"""Application services and persistent educational SMS authentication."""

import hashlib
import re
import secrets
import time

from domain import (
    Application,
    ChecklistPolicy,
    SupplementValidator,
    now,
    require,
    text,
    uid,
)


class SessionService:
    def __init__(self, repository):
        self.store = repository

    @staticmethod
    def phone(value):
        require(isinstance(value, str), "Укажите телефон.")
        value = re.sub(r"[ ()-]", "", value)
        require(
            re.fullmatch(r"\+[1-9][0-9]{9,14}", value),
            "Укажите телефон с кодом страны, например +79991234567.",
        )
        return value

    def sendCode(self, command):
        phone = self.phone(command.get("phone"))
        mode = command.get("mode")
        require(mode in ("register", "login"), "Выберите вход или регистрацию.")
        self.store.begin()
        last = self.store.one(
            "SELECT createdAt FROM challenges WHERE phone=? ORDER BY createdAt DESC LIMIT 1",
            (phone,),
        )
        require(
            not last or time.time() - last["createdAt"] >= 2,
            "Подождите две секунды перед повторным запросом.",
            429,
        )
        challenge = uid()
        self.store.db.execute(
            "INSERT INTO challenges(id,phone,mode,createdAt,expiresAt) VALUES (?,?,?,?,?)",
            (challenge, phone, mode, time.time(), time.time() + 300),
        )
        self.store.commit()
        return dict(
            challengeId=challenge, message="Учебный SMS-код: 123456. Действует 5 минут."
        )

    def authenticate(self, command):
        text(command.get("challengeId"), "Запрос SMS", 80)
        self.store.begin()
        challenge = self.store.one(
            "SELECT * FROM challenges WHERE id=?", (command.get("challengeId"),)
        )
        require(
            challenge
            and not challenge["consumed"]
            and challenge["expiresAt"] > time.time()
            and challenge["attempts"] < 5,
            "Запросите новый SMS-код.",
            401,
        )
        if command.get("code") != "123456":
            self.store.db.execute(
                "UPDATE challenges SET attempts=attempts+1 WHERE id=?",
                (challenge["id"],),
            )
            self.store.commit()
            require(False, "Неверный код. Для учебной демонстрации: 123456.", 401)
        user = self.store.one(
            "SELECT * FROM users WHERE phone=?", (challenge["phone"],)
        )
        if challenge["mode"] == "register":
            require(not user, "Этот телефон зарегистрирован. Выберите «Вход».", 409)
            user = dict(
                id=uid(), phone=challenge["phone"], role="APPLICANT", registeredAt=now()
            )
            self.store.db.execute(
                "INSERT INTO users VALUES (?,?,?,?)", tuple(user.values())
            )
        else:
            require(user, "Телефон не зарегистрирован. Выберите «Регистрация».", 401)
        self.store.db.execute(
            "UPDATE challenges SET consumed=1 WHERE id=?", (challenge["id"],)
        )
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        self.store.db.execute("DELETE FROM sessions WHERE expiresAt<?", (time.time(),))
        self.store.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user["id"],
                csrf,
                time.time() + 86400,
            ),
        )
        self.store.commit()
        return token, dict(user=user, csrf=csrf)

    def requireSession(self, token):
        session = self.store.one(
            "SELECT s.*,u.id,u.phone,u.role FROM sessions s JOIN users u ON u.id=s.userId "
            "WHERE tokenHash=? AND expiresAt>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        )
        require(session, "Сеанс завершён. Войдите заново.", 401)
        return session

    def requireApplicant(self, sessionToken):
        return self.requireRole(self.requireSession(sessionToken), "APPLICANT")

    def requireSpecialist(self, sessionToken):
        return self.requireRole(self.requireSession(sessionToken), "SPECIALIST")

    @staticmethod
    def requireRole(user, role):
        require(user["role"] == role, "Недостаточно прав для этой операции.", 403)
        return user


class SubmissionService:
    def __init__(self, repository):
        self.store = repository

    def submit(self, user, command):
        SessionService.requireRole(user, "APPLICANT")
        q, documents = SupplementValidator().validate(command)
        checklist = ChecklistPolicy().calculate(q)
        change = Application.submit(user["id"], q, checklist, documents)
        self.store.save(change)
        self.store.commit()
        return change.applicationId


class SupplementService:
    def __init__(self, repository):
        self.store = repository

    def submit(self, user, command):
        SessionService.requireRole(user, "APPLICANT")
        text(command.get("sourceVersionId"), "Исходная версия", 80)
        text(command.get("requestId"), "Запрос дополнения", 80)
        context = self.store.loadSupplementContext(
            command.get("applicationId"), user["id"], command.get("sourceVersionId")
        )
        q, documents = SupplementValidator().validate(command, context)
        checklist = ChecklistPolicy().calculate(q)
        application = self.store.beginAndLock(command.get("applicationId"), user["id"])
        change = application.acceptSupplement(
            user["id"], command, q, checklist, documents
        )
        self.store.save(change)
        self.store.commit()
        return change.applicationId


class SupplementController:
    def __init__(self, repository):
        self.store = repository

    def submit(self, sessionToken, command):
        user = SessionService(self.store).requireApplicant(sessionToken)
        return SupplementService(self.store).submit(user, command)


class ReviewService:
    def __init__(self, repository):
        self.store = repository

    def execute(self, action, user, applicationId, command):
        SessionService.requireRole(user, "SPECIALIST")
        application = self.store.beginAndLockForReview(applicationId, user["id"])
        if action == "start":
            change = application.startReview(user["id"], command.get("sourceVersionId"))
        else:
            method = {
                "assess": application.saveAssessment,
                "request": application.requestSupplement,
                "complete": application.complete,
            }[action]
            change = method(user["id"], command)
        self.store.save(change)
        self.store.commit()
        return applicationId


class ApplicationQueryService:
    def __init__(self, repository):
        self.store = repository

    def listApplications(self, user):
        if user["role"] == "APPLICANT":
            return self.store.many(
                "SELECT * FROM applications WHERE applicantId=? ORDER BY submittedAt DESC,id",
                (user["id"],),
            )
        return self.store.many(
            "SELECT a.*,u.phone AS applicantPhone FROM applications a JOIN users u "
            "ON u.id=a.applicantId ORDER BY submittedAt DESC,a.id"
        )

    def getOwnApplication(self, user, applicationId):
        # A read transaction keeps versions, status and history in a consistent snapshot.
        self.store.db.execute("BEGIN")
        result = self.store.view(applicationId, user)
        self.store.commit()
        return result
