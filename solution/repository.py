"""SQLite persistence; one connection and transaction per operation."""

import json
import sqlite3
from pathlib import Path

from domain import Application, ChangeSet, now, processing_deadline, require, uid


class ApplicationRepository:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.rollback_if_active()
        self.db.close()

    def initialize(self, specialist_phone):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        )
        self.db.execute(
            "INSERT OR IGNORE INTO users VALUES (?,?,?,?)",
            (uid(), specialist_phone, "SPECIALIST", now()),
        )
        user = self.one("SELECT * FROM users WHERE phone=?", (specialist_phone,))
        require(user["role"] == "SPECIALIST", "Телефон специалиста занят заявителем.")

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def many(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def begin(self):
        self.db.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def rollback_if_active(self):
        if self.db.in_transaction:
            self.rollback()

    def permitted(self, application_id, user):
        application = self.one(
            "SELECT * FROM applications WHERE id=?", (application_id,)
        )
        require(
            application
            and (
                user["role"] == "SPECIALIST" or application["applicantId"] == user["id"]
            ),
            "Заявка не найдена.",
            404,
        )
        application["dueAt"] = processing_deadline(application["submittedAt"])
        return application

    def version(self, version_id, content=False):
        version = self.one("SELECT * FROM versions WHERE id=?", (version_id,))
        version["questionnaire"] = json.loads(version["questionnaire"])
        version["checklist"] = self.many(
            "SELECT * FROM checklist_items WHERE versionId=? ORDER BY code",
            (version_id,),
        )
        fields = (
            "d.*"
            if content
            else "d.id,d.kind,d.fileName,d.mediaType,length(d.content) AS size,d.attachedAt"
        )
        version["documents"] = self.many(
            f"SELECT {fields},i.code FROM documents d JOIN checklist_items i "
            "ON i.id=d.itemId WHERE d.versionId=? ORDER BY d.attachedAt,d.id",
            (version_id,),
        )
        version["reviews"] = self.many(
            "SELECT r.*,u.phone AS specialistPhone FROM reviews r JOIN users u "
            "ON u.id=r.specialistId WHERE versionId=? ORDER BY startedAt,id",
            (version_id,),
        )
        for review in version["reviews"]:
            review["findings"] = self.many(
                "SELECT f.*,i.code FROM findings f JOIN checklist_items i "
                "ON i.id=f.itemId WHERE reviewId=? ORDER BY i.code",
                (review["id"],),
            )
            review["request"] = self.one(
                "SELECT * FROM supplement_requests WHERE reviewId=?", (review["id"],)
            )
            review["conclusion"] = self.one(
                "SELECT * FROM conclusions WHERE reviewId=?", (review["id"],)
            )
        return version

    def view(self, application_id, user):
        application = self.permitted(application_id, user)
        application["applicantPhone"] = self.one(
            "SELECT phone FROM users WHERE id=?", (application["applicantId"],)
        )["phone"]
        application["versions"] = [
            self.version(version["id"])
            for version in self.many(
                "SELECT id FROM versions WHERE applicationId=? ORDER BY number",
                (application_id,),
            )
        ]
        application["latest"] = application["versions"][-1]
        reviews = application["latest"]["reviews"]
        application["review"] = reviews[-1] if reviews else None
        application["request"] = (
            application["review"]["request"] if application["review"] else None
        )
        application["conclusion"] = self.one(
            "SELECT * FROM conclusions WHERE applicationId=?", (application_id,)
        )
        application["history"] = self.many(
            "SELECT e.*,u.phone AS actorPhone,u.role AS actorRole FROM status_events e "
            "JOIN users u ON u.id=e.initiatorId WHERE applicationId=? ORDER BY number",
            (application_id,),
        )
        return application

    def load_supplement_context(
        self, application_id, applicant_id, source_version_id=None
    ):
        self.permitted(application_id, dict(id=applicant_id, role="APPLICANT"))
        row = self.one(
            "SELECT id FROM versions WHERE applicationId=? AND id=?",
            (application_id, source_version_id),
        )
        require(row, "Исходная версия не найдена в этой заявке.", 409)
        return self.version(row["id"], content=True)

    def begin_and_lock(self, application_id, applicant_id):
        self.begin()
        return Application(
            self.view(application_id, dict(id=applicant_id, role="APPLICANT"))
        )

    def begin_and_lock_for_review(self, application_id, specialist_id):
        self.begin()
        return Application(
            self.view(application_id, dict(id=specialist_id, role="SPECIALIST"))
        )

    def save(self, change: ChangeSet) -> None:
        """Persist a change within the caller's transaction; do not commit here."""
        if not self.db.in_transaction:
            self.begin()
        stamp = now()
        if change.applicant_id:
            self.db.execute(
                "INSERT INTO applications VALUES (?,?,?,?)",
                (change.application_id, change.applicant_id, change.status, stamp),
            )
        if change.version:
            self._save_version(change, stamp)
        if change.review:
            self._save_review(change, stamp)
        if change.decision:
            self._save_decision(change, stamp)
        if change.status:
            self._save_status(change, stamp)

    def _save_version(self, change: ChangeSet, stamp: str) -> None:
        version = change.version
        version_id = uid()
        self.db.execute(
            "INSERT INTO versions VALUES (?,?,?,?,?,?)",
            (
                version_id,
                change.application_id,
                version["requestId"],
                version["number"],
                json.dumps(version["questionnaire"].to_payload(), ensure_ascii=False),
                stamp,
            ),
        )
        items = {}
        for item in version["checklist"]:
            item_id = uid()
            items[item["code"]] = item_id
            self.db.execute(
                "INSERT INTO checklist_items VALUES (?,?,?,?,?)",
                (
                    item_id,
                    version_id,
                    item["code"],
                    item["requiredAction"],
                    item["basis"],
                ),
            )
        for document in version["documents"]:
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    version_id,
                    items[document["code"]],
                    document["kind"],
                    document["fileName"],
                    document["mediaType"],
                    document["content"],
                    stamp,
                ),
            )

    def _save_review(self, change: ChangeSet, stamp: str) -> None:
        review = change.review
        existing = self.one("SELECT id FROM reviews WHERE id=?", (review["id"],))
        if not existing:
            self.db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                (
                    review["id"],
                    review["versionId"],
                    review["specialistId"],
                    review["state"],
                    stamp,
                    None,
                    0,
                ),
            )
        else:
            self.db.execute(
                "UPDATE reviews SET state=?, assessedAt=?,revision=revision+1 WHERE id=?",
                (review["state"], stamp, review["id"]),
            )
            if not change.decision:
                self.db.execute(
                    "DELETE FROM findings WHERE reviewId=?", (review["id"],)
                )
                items = {
                    item["code"]: item["id"]
                    for item in self.many(
                        "SELECT * FROM checklist_items WHERE versionId=?",
                        (review["versionId"],),
                    )
                }
                for finding in review["findings"]:
                    self.db.execute(
                        "INSERT INTO findings VALUES (?,?,?,?,?)",
                        (
                            uid(),
                            review["id"],
                            items[finding["code"]],
                            finding["assessment"],
                            finding["comment"],
                        ),
                    )

    def _save_decision(self, change: ChangeSet, stamp: str) -> None:
        decision = change.decision
        if decision["kind"] == "request":
            self.db.execute(
                "INSERT INTO supplement_requests VALUES (?,?,?,?)",
                (uid(), change.review["id"], decision["text"], stamp),
            )
        else:
            self.db.execute(
                "INSERT INTO conclusions VALUES (?,?,?,?,?)",
                (
                    uid(),
                    change.review["id"],
                    change.application_id,
                    decision["text"],
                    stamp,
                ),
            )

    def _save_status(self, change: ChangeSet, stamp: str) -> None:
        previous = self.one(
            "SELECT newStatus FROM status_events WHERE applicationId=? "
            "ORDER BY number DESC LIMIT 1",
            (change.application_id,),
        )
        number = (
            self.one(
                "SELECT count(*) AS n FROM status_events WHERE applicationId=?",
                (change.application_id,),
            )["n"]
            + 1
        )
        self.db.execute(
            "INSERT INTO status_events VALUES (?,?,?,?,?,?,?,?)",
            (
                uid(),
                change.application_id,
                change.actor_id,
                number,
                previous["newStatus"] if previous else None,
                change.status,
                stamp,
                change.comment,
            ),
        )
        self.db.execute(
            "UPDATE applications SET status=? WHERE id=?",
            (change.status, change.application_id),
        )
