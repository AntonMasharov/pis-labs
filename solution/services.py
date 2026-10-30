"""Application services and persistent educational SMS authentication."""

import hashlib
import re
import secrets
import time

from domain import (
    Application,
    ChecklistPolicy,
    DomainError,
    SupplementValidator,
    now,
    processing_deadline,
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

    def send_code(self, command):
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
            raise DomainError("Неверный код. Для учебной демонстрации: 123456.", 401)
        user = self.store.one(
            "SELECT * FROM users WHERE phone=?", (challenge["phone"],)
        )
        if challenge["mode"] == "register":
            require(not user, "Этот телефон зарегистрирован. Выберите «Вход».", 409)
            user = dict(
                id=uid(), phone=challenge["phone"], role="APPLICANT", registeredAt=now()
            )
            self.store.db.execute(
                "INSERT INTO users(id,phone,role,registeredAt) VALUES (?,?,?,?)",
                (user["id"], user["phone"], user["role"], user["registeredAt"]),
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

    def require_session(self, token):
        session = self.store.one(
            "SELECT s.*,u.id,u.phone,u.role FROM sessions s JOIN users u ON u.id=s.userId "
            "WHERE tokenHash=? AND expiresAt>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        )
        require(session, "Сеанс завершён. Войдите заново.", 401)
        return session

    def require_applicant(self, session_token):
        return self.require_role(self.require_session(session_token), "APPLICANT")

    def require_specialist(self, session_token):
        return self.require_role(self.require_session(session_token), "SPECIALIST")

    @staticmethod
    def require_role(user, role):
        require(user["role"] == role, "Недостаточно прав для этой операции.", 403)
        return user


class SubmissionService:
    def __init__(self, repository):
        self.store = repository

    def submit(self, user, command):
        SessionService.require_role(user, "APPLICANT")
        questionnaire, documents = SupplementValidator().validate(command)
        checklist = ChecklistPolicy().calculate(questionnaire)
        change = Application.submit(user["id"], questionnaire, checklist, documents)
        self.store.save(change)
        self.store.commit()
        return change.application_id


class SupplementService:
    def __init__(self, repository):
        self.store = repository

    def submit(self, user, command):
        SessionService.require_role(user, "APPLICANT")
        text(command.get("sourceVersionId"), "Исходная версия", 80)
        text(command.get("requestId"), "Запрос дополнения", 80)
        context = self.store.load_supplement_context(
            command.get("applicationId"), user["id"], command.get("sourceVersionId")
        )
        questionnaire, documents = SupplementValidator().validate(command, context)
        checklist = ChecklistPolicy().calculate(questionnaire)
        application = self.store.begin_and_lock(
            command.get("applicationId"), user["id"]
        )
        change = application.accept_supplement(
            user["id"], command, questionnaire, checklist, documents
        )
        self.store.save(change)
        self.store.commit()
        return change.application_id


class SupplementController:
    def __init__(self, repository):
        self.store = repository

    def submit(self, session_token, command):
        user = SessionService(self.store).require_applicant(session_token)
        return SupplementService(self.store).submit(user, command)


class ReviewService:
    def __init__(self, repository):
        self.store = repository

    def execute(self, action, user, application_id, command):
        SessionService.require_role(user, "SPECIALIST")
        application = self.store.begin_and_lock_for_review(application_id, user["id"])
        if action == "start":
            change = application.start_review(
                user["id"], command.get("sourceVersionId")
            )
        else:
            method = {
                "assess": application.save_assessment,
                "request": application.request_supplement,
                "complete": application.complete,
            }[action]
            change = method(user["id"], command)
        self.store.save(change)
        self.store.commit()
        return application_id


class ApplicationQueryService:
    def __init__(self, repository):
        self.store = repository

    def list_applications(self, user):
        if user["role"] == "APPLICANT":
            applications = self.store.many(
                "SELECT * FROM applications WHERE applicantId=? ORDER BY submittedAt DESC,id",
                (user["id"],),
            )
        else:
            applications = self.store.many(
                "SELECT a.*,u.phone AS applicantPhone FROM applications a JOIN users u "
                "ON u.id=a.applicantId ORDER BY submittedAt DESC,a.id"
            )

        for application in applications:
            application["dueAt"] = processing_deadline(application["submittedAt"])
        return applications

    def get_own_application(self, user, application_id):
        # A read transaction keeps versions, status and history in a consistent snapshot.
        self.store.db.execute("BEGIN")
        result = self.store.view(application_id, user)
        self.store.commit()
        return result
