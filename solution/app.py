"""Run: python solution/app.py. No third-party runtime dependencies."""

import argparse
import hmac
import json
import logging
import os
import re
import sqlite3
import sys
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from domain import (
    COUNTRIES,
    KINDS,
    STATUSES,
    ChecklistPolicy,
    DomainError,
    SupplementValidator,
    require,
)
from repository import ApplicationRepository
from services import (
    ApplicationQueryService,
    ReviewService,
    SessionService,
    SubmissionService,
    SupplementController,
)

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("patent")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Do not log phone numbers, tokens, document contents or query strings.
        LOG.info("%s %s", self.command, self.path.split("?")[0])

    def send(
        self, status, content, media="application/json; charset=utf-8", headers=None
    ):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        elif isinstance(content, str):
            content = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(content)

    def token(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return cookies["session"].value if "session" in cookies else ""

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def read_command(self) -> dict:
        """Read a bounded JSON object from a POST request."""
        require(
            self.headers.get("Content-Type", "").split(";")[0] == "application/json",
            "Ожидается JSON.",
            415,
        )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise DomainError("Некорректный размер запроса.") from None
        require(
            0 < length <= 23 * 1024 * 1024,
            "Запрос пуст или слишком велик.",
            413,
        )
        try:
            command = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise DomainError("Некорректный JSON.") from None
        require(isinstance(command, dict), "Ожидается объект JSON.")
        return command

    def handle_request(self, post):
        try:
            expected = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            host = self.headers.get("Host", "")
            require(host in expected, "Недопустимый адрес сервера.", 403)
            if post:
                require(
                    self.headers.get("Sec-Fetch-Site") != "cross-site",
                    "Недопустимый источник запроса.",
                    403,
                )
                origin = self.headers.get("Origin")
                require(
                    not origin or origin == "http://" + host,
                    "Недопустимый источник запроса.",
                    403,
                )
            path = urlsplit(self.path).path
            if not post and path in ("/", "/app.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                media = {
                    "index.html": "text/html; charset=utf-8",
                    "app.js": "text/javascript; charset=utf-8",
                    "style.css": "text/css; charset=utf-8",
                }[name]
                return self.send(200, (ROOT / "static" / name).read_bytes(), media)
            require(path.startswith("/api/"), "Страница не найдена.", 404)
            command = self.read_command() if post else {}
            with ApplicationRepository(self.server.db_path) as store:
                auth = SessionService(store)
                if post and path == "/api/auth/code":
                    return self.send(200, auth.send_code(command))
                if post and path == "/api/auth/verify":
                    token, result = auth.authenticate(command)
                    return self.send(
                        200,
                        result,
                        headers={
                            "Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"
                        },
                    )
                user = auth.require_session(self.token())
                if post:
                    require(
                        hmac.compare_digest(
                            self.headers.get("X-CSRF-Token", ""), user["csrf"]
                        ),
                        "Обновите страницу и повторите действие.",
                        403,
                    )
                if not post and path == "/api/me":
                    return self.send(
                        200,
                        dict(
                            user={k: user[k] for k in ("id", "phone", "role")},
                            csrf=user["csrf"],
                            countries=COUNTRIES,
                            kinds={k: sorted(v) for k, v in KINDS.items()},
                            statuses=STATUSES,
                        ),
                    )
                if post and path == "/api/logout":
                    store.db.execute(
                        "DELETE FROM sessions WHERE tokenHash=?", (user["tokenHash"],)
                    )
                    return self.send(
                        200,
                        {},
                        headers={
                            "Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                        },
                    )
                if post and path == "/api/checklist":
                    SessionService.require_role(user, "APPLICANT")
                    questionnaire, _ = SupplementValidator().validate(
                        dict(questionnaire=command.get("questionnaire"))
                    )
                    return self.send(
                        200, dict(checklist=ChecklistPolicy().calculate(questionnaire))
                    )
                if path == "/api/applications":
                    if post:
                        app_id = SubmissionService(store).submit(user, command)
                        return self.send(201, dict(id=app_id))
                    return self.send(
                        200,
                        dict(
                            applications=ApplicationQueryService(
                                store
                            ).list_applications(user)
                        ),
                    )
                match = re.fullmatch(
                    r"/api/applications/([a-f0-9-]+)(?:/(supplement|start|assess|request|complete))?",
                    path,
                )
                if match:
                    app_id, action = match.groups()
                    if not post and not action:
                        return self.send(
                            200,
                            ApplicationQueryService(store).get_own_application(
                                user, app_id
                            ),
                        )
                    if post and action == "supplement":
                        command["applicationId"] = app_id
                        result = SupplementController(store).submit(
                            self.token(), command
                        )
                        return self.send(200, dict(id=result))
                    if post and action in ("start", "assess", "request", "complete"):
                        result = ReviewService(store).execute(
                            action, user, app_id, command
                        )
                        return self.send(200, dict(id=result))
                match = re.fullmatch(r"/api/documents/([a-f0-9-]+)", path)
                if match and not post:
                    doc = store.one(
                        "SELECT d.*,v.applicationId FROM documents d JOIN versions v "
                        "ON v.id=d.versionId WHERE d.id=?",
                        (match[1],),
                    )
                    require(doc, "Файл не найден.", 404)
                    store.permitted(doc["applicationId"], user)
                    return self.send(
                        200,
                        doc["content"],
                        doc["mediaType"],
                        headers={
                            "Content-Disposition": "attachment; filename*=UTF-8''"
                            + quote(doc["fileName"], safe="")
                        },
                    )
                raise DomainError("Операция не найдена.", 404)
        except DomainError as error:
            self.send(error.status, dict(error=str(error)))
        except sqlite3.Error:
            LOG.exception("Database operation failed")
            self.send(
                503,
                dict(
                    error="Не удалось подтвердить сохранение. Откройте заявку заново и проверьте версию и историю."
                ),
            )
        except Exception:
            LOG.exception("Unexpected request error")
            self.send(
                500,
                dict(
                    error="Не удалось выполнить запрос. Обновите страницу и повторите действие."
                ),
            )


def create_server(db_path, port=8000, specialist_phone="+79990000001"):
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with ApplicationRepository(db_path) as store:
        store.initialize(SessionService.phone(specialist_phone))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.db_path = db_path
    server.daemon_threads = True
    return server


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Подготовка документов для патента")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT / "data" / "patent.sqlite3"))
    parser.add_argument(
        "--specialist-phone", default=os.environ.get("SPECIALIST_PHONE", "+79990000001")
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = create_server(args.db, args.port, args.specialist_phone)
    print(
        f"Откройте http://127.0.0.1:{server.server_port}\nСпециалист: {args.specialist_phone}; SMS-код: 123456",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
