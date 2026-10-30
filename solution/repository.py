"""SQLite persistence; one connection and transaction per operation."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from domain import Application, now, require, uid


class ApplicationRepository:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.rollbackIfActive()
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
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def begin(self):
        self.db.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def rollbackIfActive(self):
        if self.db.in_transaction:
            self.rollback()

    def permitted(self, applicationId, user):
        a = self.one("SELECT * FROM applications WHERE id=?", (applicationId,))
        require(
            a and (user["role"] == "SPECIALIST" or a["applicantId"] == user["id"]),
            "Заявка не найдена.",
            404,
        )
        return a

    def version(self, versionId, content=False):
        v = self.one("SELECT * FROM versions WHERE id=?", (versionId,))
        v["questionnaire"] = json.loads(v["questionnaire"])
        v["checklist"] = self.many(
            "SELECT * FROM checklist_items WHERE versionId=? ORDER BY code",
            (versionId,),
        )
        fields = (
            "d.*"
            if content
            else "d.id,d.kind,d.fileName,d.mediaType,length(d.content) AS size,d.attachedAt"
        )
        v["documents"] = self.many(
            f"SELECT {fields},i.code FROM documents d JOIN checklist_items i "
            "ON i.id=d.itemId WHERE d.versionId=? ORDER BY d.attachedAt,d.id",
            (versionId,),
        )
        v["reviews"] = self.many(
            "SELECT r.*,u.phone AS specialistPhone FROM reviews r JOIN users u "
            "ON u.id=r.specialistId WHERE versionId=? ORDER BY startedAt,id",
            (versionId,),
        )
        for r in v["reviews"]:
            r["findings"] = self.many(
                "SELECT f.*,i.code FROM findings f JOIN checklist_items i "
                "ON i.id=f.itemId WHERE reviewId=? ORDER BY i.code",
                (r["id"],),
            )
            r["request"] = self.one(
                "SELECT * FROM supplement_requests WHERE reviewId=?", (r["id"],)
            )
            r["conclusion"] = self.one(
                "SELECT * FROM conclusions WHERE reviewId=?", (r["id"],)
            )
        return v

    def view(self, applicationId, user):
        a = self.permitted(applicationId, user)
        a["applicantPhone"] = self.one(
            "SELECT phone FROM users WHERE id=?", (a["applicantId"],)
        )["phone"]
        a["versions"] = [
            self.version(v["id"])
            for v in self.many(
                "SELECT id FROM versions WHERE applicationId=? ORDER BY number",
                (applicationId,),
            )
        ]
        a["latest"] = a["versions"][-1]
        reviews = a["latest"]["reviews"]
        a["review"] = reviews[-1] if reviews else None
        a["request"] = a["review"]["request"] if a["review"] else None
        a["conclusion"] = self.one(
            "SELECT * FROM conclusions WHERE applicationId=?", (applicationId,)
        )
        a["history"] = self.many(
            "SELECT e.*,u.phone AS actorPhone,u.role AS actorRole FROM status_events e "
            "JOIN users u ON u.id=e.initiatorId WHERE applicationId=? ORDER BY number",
            (applicationId,),
        )
        return a

    def loadSupplementContext(self, applicationId, applicantId, sourceVersionId=None):
        self.permitted(applicationId, dict(id=applicantId, role="APPLICANT"))
        row = self.one(
            "SELECT id FROM versions WHERE applicationId=? AND id=?",
            (applicationId, sourceVersionId),
        )
        require(row, "Исходная версия не найдена в этой заявке.", 409)
        return self.version(row["id"], content=True)

    def beginAndLock(self, applicationId, applicantId):
        self.begin()
        return Application(
            self.view(applicationId, dict(id=applicantId, role="APPLICANT"))
        )

    def beginAndLockForReview(self, applicationId, specialistId):
        self.begin()
        return Application(
            self.view(applicationId, dict(id=specialistId, role="SPECIALIST"))
        )

    def save(self, changeSet):
        c = changeSet
        if not self.db.in_transaction:
            self.begin()
        stamp = now()
        if c.applicantId:
            self.db.execute(
                "INSERT INTO applications VALUES (?,?,?,?)",
                (c.applicationId, c.applicantId, c.status, stamp),
            )
        if c.version:
            v = c.version
            version_id = uid()
            self.db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?)",
                (
                    version_id,
                    c.applicationId,
                    v["requestId"],
                    v["number"],
                    json.dumps(asdict(v["questionnaire"]), ensure_ascii=False),
                    stamp,
                ),
            )
            items = {}
            for item in v["checklist"]:
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
            for d in v["documents"]:
                self.db.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                    (
                        uid(),
                        version_id,
                        items[d["code"]],
                        d["kind"],
                        d["fileName"],
                        d["mediaType"],
                        d["content"],
                        stamp,
                    ),
                )
        if c.review:
            r = c.review
            existing = self.one("SELECT id FROM reviews WHERE id=?", (r["id"],))
            if not existing:
                self.db.execute(
                    "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                    (
                        r["id"],
                        r["versionId"],
                        r["specialistId"],
                        r["state"],
                        stamp,
                        None,
                        0,
                    ),
                )
            else:
                self.db.execute(
                    "UPDATE reviews SET state=?, assessedAt=?,revision=revision+1 WHERE id=?",
                    (r["state"], stamp, r["id"]),
                )
                if not c.decision:
                    self.db.execute("DELETE FROM findings WHERE reviewId=?", (r["id"],))
                    items = {
                        i["code"]: i["id"]
                        for i in self.many(
                            "SELECT * FROM checklist_items WHERE versionId=?",
                            (r["versionId"],),
                        )
                    }
                    for f in r["findings"]:
                        self.db.execute(
                            "INSERT INTO findings VALUES (?,?,?,?,?)",
                            (
                                uid(),
                                r["id"],
                                items[f["code"]],
                                f["assessment"],
                                f["comment"],
                            ),
                        )
        if c.decision:
            d = c.decision
            if d["kind"] == "request":
                self.db.execute(
                    "INSERT INTO supplement_requests VALUES (?,?,?,?)",
                    (uid(), c.review["id"], d["text"], stamp),
                )
            else:
                self.db.execute(
                    "INSERT INTO conclusions VALUES (?,?,?,?,?)",
                    (uid(), c.review["id"], c.applicationId, d["text"], stamp),
                )
        if c.status:
            previous = self.one(
                "SELECT newStatus FROM status_events WHERE applicationId=? "
                "ORDER BY number DESC LIMIT 1",
                (c.applicationId,),
            )
            number = (
                self.one(
                    "SELECT count(*) AS n FROM status_events WHERE applicationId=?",
                    (c.applicationId,),
                )["n"]
                + 1
            )
            self.db.execute(
                "INSERT INTO status_events VALUES (?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    c.applicationId,
                    c.actorId,
                    number,
                    previous["newStatus"] if previous else None,
                    c.status,
                    stamp,
                    c.comment,
                ),
            )
            self.db.execute(
                "UPDATE applications SET status=? WHERE id=?",
                (c.status, c.applicationId),
            )
