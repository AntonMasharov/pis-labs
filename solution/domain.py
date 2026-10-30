"""Rules of the educational document preparation service (lab01–lab05)."""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def require(condition, message, status=400):
    if not condition:
        raise DomainError(message, status)


def text(value, label, limit=4000, required=True):
    require(isinstance(value, str), f"{label}: ожидается текст.")
    value = value.strip()
    require(
        (bool(value) or not required) and len(value) <= limit,
        (
            f"{label}: укажите от 1 до {limit} символов."
            if required
            else f"{label}: слишком длинный текст."
        ),
    )
    return value


COUNTRIES = ["Азербайджан", "Таджикистан", "Узбекистан", "Молдова", "Украина"]
CODES = ("5.1", "5.2", "5.3")
STATUSES = {
    "SUBMITTED": "Подана",
    "UNDER_REVIEW": "На проверке",
    "NEEDS_SUPPLEMENT": "Требует дополнения",
    "COMPLETED": "Завершена",
}
KINDS = {
    "5.1": {"Сертификат", "Документ о знаниях"},
    "5.2": {"Фото 3 × 4 см"},
    "5.3": {"Документ ИНН"},
}


@dataclass(frozen=True)
class Questionnaire:
    citizenship: str
    isHighlyQualified: bool
    isResettlementParticipant: bool
    hasLanguageDocument: bool
    hasInn: bool
    inn: str


class ChecklistPolicy:
    def calculate(self, questionnaire):
        q = questionnaire
        require(
            q.citizenship in COUNTRIES
            and not q.isHighlyQualified
            and not q.isResettlementParticipant,
            "По учебным условиям раздела 5 услуга применима к гражданам Азербайджана, "
            "Таджикистана, Узбекистана, Молдовы или Украины без двух особых статусов.",
        )
        actions = [
            (
                "Приложить имеющийся документ о знаниях"
                if q.hasLanguageDocument
                else "Получить и приложить сертификат о знаниях"
            ),
            "Приложить цветные фотографии 3 × 4 см",
            (
                "Указать имеющийся ИНН"
                if q.hasInn
                else "Получить ИНН и сообщить номер в дополнении"
            ),
        ]
        bases = [
            "Русский язык, история и основы законодательства России; "
            + (
                "имеющийся подтверждающий документ."
                if q.hasLanguageDocument
                else "документ отсутствует."
            ),
            "Цветные фотографии для заявления на патент.",
            (
                "ИНН указан в анкете; отдельный файл не обязателен."
                if q.hasInn
                else "ИНН отсутствует. Получение в территориальном органе ФНС вне сервиса."
            ),
        ]
        return [
            dict(code=c, requiredAction=a, basis=b)
            for c, a, b in zip(CODES, actions, bases)
        ]


class SupplementValidator:
    MAX_FILE = 5 * 1024 * 1024

    def validate(self, command, context=None):
        require(isinstance(command, dict), "Ожидается объект заявления.")
        raw = command.get("questionnaire")
        require(isinstance(raw, dict), "Заполните анкету.")
        flags = (
            "isHighlyQualified",
            "isResettlementParticipant",
            "hasLanguageDocument",
            "hasInn",
        )
        require(
            all(type(raw.get(f)) is bool for f in flags),
            "Заполните все признаки анкеты.",
        )
        country = text(raw.get("citizenship"), "Гражданство", 80)
        inn = text(raw.get("inn", ""), "ИНН", 12, False)
        require(
            not raw["hasInn"] or re.fullmatch(r"[0-9]{12}", inn),
            "ИНН физического лица должен содержать 12 цифр.",
        )
        require(raw["hasInn"] or not inn, "Уберите номер ИНН или отметьте его наличие.")
        q = Questionnaire(country, *(raw[f] for f in flags), inn)
        selected = command.get("selectedDocumentIds", [])
        new = command.get("newDocuments", [])
        require(
            isinstance(selected, list) and all(isinstance(i, str) for i in selected),
            "Некорректный выбор прежних файлов.",
        )
        require(len(selected) == len(set(selected)), "Файлы выбраны повторно.")
        previous = {d["id"]: d for d in context["documents"]} if context else {}
        require(
            set(selected) <= previous.keys(),
            "Выбранный файл не принадлежит исходной версии.",
        )
        require(
            isinstance(new, list) and len(new) + len(selected) <= 12,
            "В версии допускается до 12 файлов.",
        )
        documents = [dict(previous[i]) for i in selected]
        for item in new:
            require(isinstance(item, dict), "Некорректный файл.")
            code = item.get("code")
            require(
                code in CODES
                and isinstance(item.get("kind"), str)
                and item.get("kind") in KINDS[code],
                "Проверьте пункт и тип документа.",
            )
            name = text(item.get("fileName"), "Имя файла", 160)
            require(
                not any(c in name for c in "/\\\r\n\x00"), "Недопустимое имя файла."
            )
            encoded = item.get("content")
            require(
                isinstance(encoded, str) and len(encoded) <= 7 * 1024 * 1024,
                "Файл слишком велик (максимум 5 МБ).",
            )
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise DomainError("Не удалось прочитать файл.") from None
            require(0 < len(content) <= self.MAX_FILE, "Файл пуст или больше 5 МБ.")
            formats = {
                "application/pdf": (".pdf", content.startswith(b"%PDF-")),
                "image/png": (".png", content.startswith(b"\x89PNG\r\n\x1a\n")),
                "image/jpeg": ((".jpg", ".jpeg"), content.startswith(b"\xff\xd8\xff")),
            }
            mime = item.get("mediaType")
            require(
                isinstance(mime, str) and mime in formats, "Разрешены PDF, PNG и JPEG."
            )
            extension, valid = formats[mime]
            require(
                name.lower().endswith(extension) and valid,
                "Содержимое файла не соответствует формату.",
            )
            documents.append(
                dict(
                    code=code,
                    kind=item["kind"],
                    fileName=name,
                    mediaType=mime,
                    content=content,
                )
            )
        require(
            sum(len(d["content"]) for d in documents) <= 15 * 1024 * 1024,
            "Общий размер файлов версии — до 15 МБ.",
        )
        return q, documents


@dataclass
class ChangeSet:
    applicationId: str
    actorId: str
    status: str | None = None
    comment: str = ""
    version: dict | None = None
    review: dict | None = None
    decision: dict | None = None
    applicantId: str | None = None


@dataclass
class Review:
    data: dict

    def assess(self, findings):
        require(
            isinstance(findings, list) and len(findings) <= 3,
            "Оцените до трёх пунктов.",
        )
        result = []
        for f in findings:
            require(
                isinstance(f, dict) and f.get("code") in CODES,
                "Неизвестный пункт оценки.",
            )
            require(
                f.get("assessment") in ("CONFORMS", "NEEDS_CORRECTION"),
                "Выберите оценку пункта.",
            )
            comment = text(
                f.get("comment", ""),
                "Комментарий",
                2000,
                f["assessment"] == "NEEDS_CORRECTION",
            )
            result.append(
                dict(code=f["code"], assessment=f["assessment"], comment=comment)
            )
        require(
            len({f["code"] for f in result}) == len(result),
            "Повторяющиеся пункты оценки.",
        )
        return result

    def canRequestSupplement(self):
        return len(self.data["findings"]) == 3 and any(
            f["assessment"] == "NEEDS_CORRECTION" for f in self.data["findings"]
        )

    def canComplete(self):
        return len(self.data["findings"]) == 3 and all(
            f["assessment"] == "CONFORMS" for f in self.data["findings"]
        )


@dataclass
class Application:
    data: dict

    @staticmethod
    def submit(applicantId, questionnaire, checklist, documents):
        return ChangeSet(
            uid(),
            applicantId,
            "SUBMITTED",
            "Заявка принята",
            applicantId=applicantId,
            version=dict(
                questionnaire=questionnaire,
                checklist=checklist,
                documents=documents,
                number=1,
                requestId=None,
            ),
        )

    def acceptSupplement(
        self, applicantId, command, questionnaire, checklist, documents
    ):
        a = self.data
        require(a["applicantId"] == applicantId, "Заявка не найдена.", 404)
        require(
            a["status"] == "NEEDS_SUPPLEMENT"
            and command.get("sourceVersionId") == a["latest"]["id"]
            and a["request"]
            and command.get("requestId") == a["request"]["id"],
            "Заявка изменилась. Откройте её заново и проверьте принятую версию.",
            409,
        )
        return ChangeSet(
            a["id"],
            applicantId,
            "SUBMITTED",
            "Принято дополнение",
            version=dict(
                questionnaire=questionnaire,
                checklist=checklist,
                documents=documents,
                number=a["latest"]["number"] + 1,
                requestId=a["request"]["id"],
            ),
        )

    def startReview(self, specialistId, versionId):
        require(
            self.data["status"] == "SUBMITTED"
            and self.data["latest"]["id"] == versionId,
            "Эту версию уже проверяют или заявка изменилась. Обновите страницу.",
            409,
        )
        return ChangeSet(
            self.data["id"],
            specialistId,
            "UNDER_REVIEW",
            "Специалист начал проверку",
            review=dict(
                id=uid(), versionId=versionId, specialistId=specialistId, state="OPEN"
            ),
        )

    def activeReview(self, specialistId, command):
        r = self.data["review"]
        require(
            self.data["status"] == "UNDER_REVIEW"
            and r
            and r["specialistId"] == specialistId,
            "Проверка недоступна этому специалисту.",
            409,
        )
        require(
            command.get("sourceVersionId") == self.data["latest"]["id"]
            and command.get("revision") == r["revision"],
            "Оценка изменилась. Обновите заявку перед продолжением.",
            409,
        )
        return Review(r)

    def saveAssessment(self, specialistId, command):
        review = self.activeReview(specialistId, command)
        findings = review.assess(command.get("findings"))
        return ChangeSet(
            self.data["id"],
            specialistId,
            review=dict(review.data, findings=findings, state="ASSESSED"),
        )

    def requestSupplement(self, specialistId, command):
        review = self.activeReview(specialistId, command)
        require(
            review.canRequestSupplement(),
            "Сначала сохраните оценки всех трёх пунктов с хотя бы одним недостатком.",
        )
        instructions = text(command.get("text"), "Инструкции по дополнению")
        return ChangeSet(
            self.data["id"],
            specialistId,
            "NEEDS_SUPPLEMENT",
            instructions,
            review=dict(review.data, state="SUPPLEMENT_REQUESTED"),
            decision=dict(kind="request", text=instructions),
        )

    def complete(self, specialistId, command):
        review = self.activeReview(specialistId, command)
        require(
            review.canComplete(), "Сначала сохраните соответствие всех трёх пунктов."
        )
        conclusion = text(command.get("text"), "Заключение")
        return ChangeSet(
            self.data["id"],
            specialistId,
            "COMPLETED",
            "Выдано заключение о комплектности",
            review=dict(review.data, state="COMPLETED"),
            decision=dict(kind="conclusion", text=conclusion),
        )
