"""End-to-end HTTP tests with an isolated real SQLite database."""

import base64
import concurrent.futures
from contextlib import closing
from datetime import datetime, timedelta
import http.cookiejar
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_server


class Client:
    def __init__(self, url):
        self.url = url
        self.csrf = ""
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def call(self, path, body=None, csrf=True, headers=None):
        request_headers = {"Content-Type": "application/json"}
        if csrf:
            request_headers["X-CSRF-Token"] = self.csrf
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.url + "/api" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=request_headers,
        )
        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            response = error
        data = response.read()
        if response.headers.get_content_type() == "application/json":
            data = json.loads(data)
        return response.status, data

    def auth(self, phone, mode="register"):
        status, data = self.call("/auth/code", dict(phone=phone, mode=mode))
        assert status == 200, data
        status, data = self.call(
            "/auth/verify", dict(challengeId=data["challengeId"], code="123456")
        )
        assert status == 200, data
        self.csrf = data["csrf"]
        return data["user"]


def questionnaire(**changes):
    return {
        **dict(
            citizenship="Узбекистан",
            isHighlyQualified=False,
            isResettlementParticipant=False,
            hasLanguageDocument=False,
            hasInn=False,
            inn="",
        ),
        **changes,
    }


def document(code="5.2", content=b"\x89PNG\r\n\x1a\nexample"):
    return dict(
        code=code,
        kind="Фото 3 × 4 см",
        fileName="photo.png",
        mediaType="image/png",
        content=base64.b64encode(content).decode(),
    )


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[1] / ".artifacts"
        artifacts.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=artifacts)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.server = create_server(self.path, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.applicant = Client(self.url)
        self.applicant.auth("+79991111111")
        self.specialist = Client(self.url)
        self.specialist.auth("+79990000001", "login")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def submit(self, files=None):
        status, result = self.applicant.call(
            "/applications",
            dict(questionnaire=questionnaire(), newDocuments=files or []),
        )
        self.assertEqual(status, 201, result)
        return result["id"]

    def view(self, app_id, client=None):
        status, data = (client or self.applicant).call("/applications/" + app_id)
        self.assertEqual(status, 200, data)
        return data

    def test_processing_deadline(self):
        app_id = self.submit()
        application = self.view(app_id)
        expected = (
            datetime.fromisoformat(application["submittedAt"]) + timedelta(days=3)
        ).isoformat(timespec="seconds")
        self.assertEqual(application["dueAt"], expected)
        self.assertEqual(self.view(app_id, self.specialist)["dueAt"], expected)
        for client in (self.applicant, self.specialist):
            status, data = client.call("/applications")
            self.assertEqual(status, 200)
            self.assertEqual(data["applications"][0]["dueAt"], expected)
        application = self.request_supplement(app_id)
        status, result = self.applicant.call(
            f"/applications/{app_id}/supplement", self.supplement_command(application)
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(self.view(app_id)["dueAt"], expected)

    def review_command(self, a):
        return dict(
            sourceVersionId=a["latest"]["id"],
            revision=a["review"]["revision"] if a["review"] else None,
        )

    def start(self, app_id):
        a = self.view(app_id)
        status, result = self.specialist.call(
            f"/applications/{app_id}/start", self.review_command(a)
        )
        self.assertEqual(status, 200, result)
        return self.view(app_id)

    def assess(self, app_id, correction=False):
        a = self.view(app_id)
        findings = [
            dict(
                code=c,
                assessment=(
                    "NEEDS_CORRECTION" if correction and c == "5.1" else "CONFORMS"
                ),
                comment=(
                    "Нужен сертификат" if correction and c == "5.1" else "Проверено"
                ),
            )
            for c in ("5.1", "5.2", "5.3")
        ]
        status, result = self.specialist.call(
            f"/applications/{app_id}/assess",
            dict(**self.review_command(a), findings=findings),
        )
        self.assertEqual(status, 200, result)
        return self.view(app_id)

    def request_supplement(self, app_id):
        self.start(app_id)
        a = self.assess(app_id, True)
        status, result = self.specialist.call(
            f"/applications/{app_id}/request",
            dict(**self.review_command(a), text="Приложите сертификат и укажите ИНН."),
        )
        self.assertEqual(status, 200, result)
        return self.view(app_id)

    def supplement_command(self, a):
        return dict(
            sourceVersionId=a["latest"]["id"],
            requestId=a["request"]["id"],
            questionnaire=questionnaire(hasInn=True, inn="123456789012"),
            selectedDocumentIds=[d["id"] for d in a["latest"]["documents"]],
            newDocuments=[],
        )

    def test_full_workflow_and_persistence(self):
        app_id = self.submit([document()])
        a = self.request_supplement(app_id)
        first_version = a["latest"]["id"]
        old_doc = a["latest"]["documents"][0]["id"]
        cmd = self.supplement_command(a)
        status, data = self.applicant.call(f"/applications/{app_id}/supplement", cmd)
        self.assertEqual(status, 200, data)
        status, _ = self.applicant.call(f"/applications/{app_id}/supplement", cmd)
        self.assertEqual(status, 409)
        a = self.start(app_id)
        # Completion before assessment is forbidden.
        status, _ = self.specialist.call(
            f"/applications/{app_id}/complete",
            dict(**self.review_command(a), text="Комплект соответствует."),
        )
        self.assertEqual(status, 400)
        a = self.assess(app_id)
        status, data = self.specialist.call(
            f"/applications/{app_id}/complete",
            dict(
                **self.review_command(a),
                text="Комплект документов соответствует перечню 5.1–5.3.",
            ),
        )
        self.assertEqual(status, 200, data)
        a = self.view(app_id)
        self.assertEqual(a["status"], "COMPLETED")
        self.assertEqual(len(a["history"]), 6)
        self.assertEqual(len(a["versions"]), 2)
        self.assertEqual(a["versions"][0]["id"], first_version)
        self.assertFalse(a["versions"][0]["questionnaire"]["hasInn"])
        self.assertTrue(a["versions"][1]["questionnaire"]["hasInn"])
        new_doc = a["latest"]["documents"][0]["id"]
        self.assertNotEqual(old_doc, new_doc)
        for doc_id in (old_doc, new_doc):
            status, content = self.applicant.call("/documents/" + doc_id)
            self.assertEqual((status, content), (200, b"\x89PNG\r\n\x1a\nexample"))
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server = create_server(self.path, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Persisted session, service, versions and result survive server restart.
        self.applicant.url = f"http://127.0.0.1:{self.server.server_port}"
        a = self.view(app_id)
        self.assertEqual(a["status"], "COMPLETED")
        self.assertIsNotNone(a["conclusion"])
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_access_roles_csrf_and_authentication(self):
        app_id = self.submit([document()])
        doc_id = self.view(app_id)["latest"]["documents"][0]["id"]
        stranger = Client(self.url)
        self.assertEqual(stranger.call("/applications")[0], 401)
        stranger.auth("+79992222222")
        self.assertEqual(stranger.call("/applications/" + app_id)[0], 404)
        self.assertEqual(stranger.call("/documents/" + doc_id)[0], 404)
        self.assertEqual(stranger.call(f"/applications/{app_id}/start", {})[0], 403)
        self.assertEqual(
            self.specialist.call("/applications", dict(questionnaire=questionnaire()))[
                0
            ],
            403,
        )
        self.assertEqual(self.applicant.call("/applications", {}, csrf=False)[0], 403)
        self.assertEqual(
            self.applicant.call(
                "/logout", {}, headers={"Origin": "https://evil.example"}
            )[0],
            403,
        )
        self.assertEqual(self.applicant.call("/me")[1]["user"]["role"], "APPLICANT")
        _, data = stranger.call(
            "/auth/code", dict(phone="+79993333333", mode="register")
        )
        challenge = data["challengeId"]
        for _ in range(5):
            self.assertEqual(
                stranger.call(
                    "/auth/verify", dict(challengeId=challenge, code="000000")
                )[0],
                401,
            )
        self.assertEqual(
            stranger.call("/auth/verify", dict(challengeId=challenge, code="123456"))[
                0
            ],
            401,
        )
        self.applicant.call("/logout", {})
        self.assertEqual(self.applicant.call("/me")[0], 401)

    def test_validation_and_applicability_leave_no_rows(self):
        for change in [
            dict(citizenship="Другое"),
            dict(isHighlyQualified=True),
            dict(isResettlementParticipant=True),
            dict(hasInn=True, inn="abc"),
            dict(hasInn="true"),
            dict(inn="123456789012"),
        ]:
            status, result = self.applicant.call(
                "/applications", dict(questionnaire=questionnaire(**change))
            )
            self.assertEqual(status, 400, result)
        for file in [
            document(content=b"<script>bad</script>"),
            {**document(), "content": "!"},
            {**document(), "fileName": "../photo.png"},
        ]:
            status, result = self.applicant.call(
                "/applications",
                dict(questionnaire=questionnaire(), newDocuments=[file]),
            )
            self.assertEqual(status, 400, result)
        self.assertEqual(self.applicant.call("/applications")[1]["applications"], [])
        status, result = self.applicant.call(
            "/checklist",
            dict(
                questionnaire=questionnaire(
                    hasLanguageDocument=True, hasInn=True, inn="123456789012"
                )
            ),
        )
        self.assertEqual(status, 200)
        self.assertIn("имеющийся", result["checklist"][0]["requiredAction"])

    def test_review_conflicts_and_invalid_decisions(self):
        app_id = self.submit()
        a = self.start(app_id)
        old_command = self.review_command(a)
        a = self.assess(app_id, True)
        self.assertEqual(a["status"], "UNDER_REVIEW")
        self.assertEqual(len(a["history"]), 2)
        self.assertEqual(
            self.specialist.call(
                f"/applications/{app_id}/request", dict(**old_command, text="Исправить")
            )[0],
            409,
        )
        self.assertEqual(
            self.specialist.call(
                f"/applications/{app_id}/complete",
                dict(**self.review_command(a), text="Готово"),
            )[0],
            400,
        )
        duplicate = [dict(code="5.1", assessment="CONFORMS", comment="")] * 2
        self.assertEqual(
            self.specialist.call(
                f"/applications/{app_id}/assess",
                dict(**self.review_command(a), findings=duplicate),
            )[0],
            400,
        )
        self.assertEqual(
            self.specialist.call(f"/applications/{app_id}/start", old_command)[0], 409
        )

    def test_atomic_rollback_and_competing_supplements(self):
        app_id = self.submit([document()])
        a = self.request_supplement(app_id)
        cmd = self.supplement_command(a)
        # Fail the last write after version/checklist/file insertion.
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute(
                "CREATE TRIGGER fail_event BEFORE INSERT ON status_events BEGIN SELECT RAISE(ABORT,'test'); END"
            )
        with self.assertLogs("patent", level="ERROR"):
            self.assertEqual(
                self.applicant.call(f"/applications/{app_id}/supplement", cmd)[0], 503
            )
        a = self.view(app_id)
        self.assertEqual(
            (a["status"], len(a["versions"]), len(a["history"])),
            ("NEEDS_SUPPLEMENT", 1, 3),
        )
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM documents").fetchone()[0], 1
            )
            db.execute("DROP TRIGGER fail_event")
        # Two requests from separate clients with the same session and source version.
        second = Client(self.url)
        second.csrf = self.applicant.csrf
        for cookie in self.applicant.cookies:
            second.cookies.set_cookie(cookie)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda c: c.call(f"/applications/{app_id}/supplement", cmd)[0],
                    [self.applicant, second],
                )
            )
        self.assertEqual(sorted(outcomes), [200, 409])
        a = self.view(app_id)
        self.assertEqual(len(a["versions"]), 2)
        self.assertEqual(len(a["history"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
