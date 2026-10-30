"""Rules of the educational document preparation service (lab01–lab05)."""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

PROCESSING_DAYS = 3


def processing_deadline(submitted_at: str) -> str:
    """Educational deadline: calendar days, without pauses or extensions."""
    return (
        datetime.fromisoformat(submitted_at) + timedelta(days=PROCESSING_DAYS)
    ).isoformat(timespec="seconds")


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def require(condition, message, status=400):
    if not condition:
        raise DomainError(message, status)


def text(value, label: str, limit: int = 4000, required: bool = True) -> str:
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
    is_highly_qualified: bool
    is_resettlement_participant: bool
    has_language_document: bool
    has_inn: bool
    inn: str

    def to_payload(self) -> dict:
        """Keep the browser and stored JSON format independent of Python names."""
        return {
            "citizenship": self.citizenship,
            "isHighlyQualified": self.is_highly_qualified,
            "isResettlementParticipant": self.is_resettlement_participant,
            "hasLanguageDocument": self.has_language_document,
            "hasInn": self.has_inn,
            "inn": self.inn,
        }


class ChecklistPolicy:
    def calculate(self, questionnaire: Questionnaire) -> list[dict]:
        require(
            questionnaire.citizenship in COUNTRIES
            and not questionnaire.is_highly_qualified
            and not questionnaire.is_resettlement_participant,
            "По учебным условиям раздела 5 услуга применима к гражданам Азербайджана, "
            "Таджикистана, Узбекистана, Молдовы или Украины без двух особых статусов.",
        )
        actions = [
            (
                "Приложить имеющийся документ о знаниях"
                if questionnaire.has_language_document
                else "Получить и приложить сертификат о знаниях"
            ),
            "Приложить цветные фотографии 3 × 4 см",
            (
                "Указать имеющийся ИНН"
                if questionnaire.has_inn
                else "Получить ИНН и сообщить номер в дополнении"
            ),
        ]
        bases = [
            "Русский язык, история и основы законодательства России; "
            + (
                "имеющийся подтверждающий документ."
                if questionnaire.has_language_document
                else "документ отсутствует."
            ),
            "Цветные фотографии для заявления на патент.",
            (
                "ИНН указан в анкете; отдельный файл не обязателен."
                if questionnaire.has_inn
                else "ИНН отсутствует. Получение в территориальном органе ФНС вне сервиса."
            ),
        ]
        return [
            dict(code=code, requiredAction=action, basis=basis)
            for code, action, basis in zip(CODES, actions, bases)
        ]


class SupplementValidator:
    MAX_FILE = 5 * 1024 * 1024
    MAX_FILES = 12
    MAX_TOTAL_SIZE = 15 * 1024 * 1024

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
            all(type(raw.get(flag)) is bool for flag in flags),
            "Заполните все признаки анкеты.",
        )
        country = text(raw.get("citizenship"), "Гражданство", 80)
        inn = text(raw.get("inn", ""), "ИНН", 12, False)
        require(
            not raw["hasInn"] or re.fullmatch(r"[0-9]{12}", inn),
            "ИНН физического лица должен содержать 12 цифр.",
        )
        require(raw["hasInn"] or not inn, "Уберите номер ИНН или отметьте его наличие.")
        questionnaire = Questionnaire(country, *(raw[flag] for flag in flags), inn)
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
            isinstance(new, list) and len(new) + len(selected) <= self.MAX_FILES,
            "В версии допускается до 12 файлов.",
        )
        documents = [dict(previous[i]) for i in selected]
        documents.extend(self.validate_document(item) for item in new)
        require(
            sum(len(d["content"]) for d in documents) <= self.MAX_TOTAL_SIZE,
            "Общий размер файлов версии — до 15 МБ.",
        )
        return questionnaire, documents

    def validate_document(self, item: dict) -> dict:
        """Validate one upload before it enters an immutable version."""
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
            not any(character in name for character in "/\\\r\n\x00"),
            "Недопустимое имя файла.",
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
        require(isinstance(mime, str) and mime in formats, "Разрешены PDF, PNG и JPEG.")
        extension, valid = formats[mime]
        require(
            name.lower().endswith(extension) and valid,
            "Содержимое файла не соответствует формату.",
        )
        return dict(
            code=code, kind=item["kind"], fileName=name, mediaType=mime, content=content
        )


@dataclass
class ChangeSet:
    application_id: str
    actor_id: str
    status: str | None = None
    comment: str = ""
    version: dict | None = None
    review: dict | None = None
    decision: dict | None = None
    applicant_id: str | None = None


@dataclass
class Review:
    data: dict

    def assess(self, findings):
        require(
            isinstance(findings, list) and len(findings) <= 3,
            "Оцените до трёх пунктов.",
        )
        result = []
        for finding in findings:
            require(
                isinstance(finding, dict) and finding.get("code") in CODES,
                "Неизвестный пункт оценки.",
            )
            require(
                finding.get("assessment") in ("CONFORMS", "NEEDS_CORRECTION"),
                "Выберите оценку пункта.",
            )
            comment = text(
                finding.get("comment", ""),
                "Комментарий",
                2000,
                finding["assessment"] == "NEEDS_CORRECTION",
            )
            result.append(
                dict(
                    code=finding["code"],
                    assessment=finding["assessment"],
                    comment=comment,
                )
            )
        require(
            len({finding["code"] for finding in result}) == len(result),
            "Повторяющиеся пункты оценки.",
        )
        return result

    def can_request_supplement(self):
        return len(self.data["findings"]) == 3 and any(
            finding["assessment"] == "NEEDS_CORRECTION"
            for finding in self.data["findings"]
        )

    def can_complete(self):
        return len(self.data["findings"]) == 3 and all(
            finding["assessment"] == "CONFORMS" for finding in self.data["findings"]
        )


@dataclass
class Application:
    data: dict

    @staticmethod
    def submit(applicant_id, questionnaire, checklist, documents):
        return ChangeSet(
            uid(),
            applicant_id,
            "SUBMITTED",
            "Заявка принята",
            applicant_id=applicant_id,
            version=dict(
                questionnaire=questionnaire,
                checklist=checklist,
                documents=documents,
                number=1,
                requestId=None,
            ),
        )

    def accept_supplement(
        self, applicant_id, command, questionnaire, checklist, documents
    ):
        application = self.data
        require(application["applicantId"] == applicant_id, "Заявка не найдена.", 404)
        require(
            application["status"] == "NEEDS_SUPPLEMENT"
            and command.get("sourceVersionId") == application["latest"]["id"]
            and application["request"]
            and command.get("requestId") == application["request"]["id"],
            "Заявка изменилась. Откройте её заново и проверьте принятую версию.",
            409,
        )
        return ChangeSet(
            application["id"],
            applicant_id,
            "SUBMITTED",
            "Принято дополнение",
            version=dict(
                questionnaire=questionnaire,
                checklist=checklist,
                documents=documents,
                number=application["latest"]["number"] + 1,
                requestId=application["request"]["id"],
            ),
        )

    def start_review(self, specialist_id, version_id):
        require(
            self.data["status"] == "SUBMITTED"
            and self.data["latest"]["id"] == version_id,
            "Эту версию уже проверяют или заявка изменилась. Обновите страницу.",
            409,
        )
        return ChangeSet(
            self.data["id"],
            specialist_id,
            "UNDER_REVIEW",
            "Специалист начал проверку",
            review=dict(
                id=uid(), versionId=version_id, specialistId=specialist_id, state="OPEN"
            ),
        )

    def active_review(self, specialist_id, command):
        review = self.data["review"]
        require(
            self.data["status"] == "UNDER_REVIEW"
            and review
            and review["specialistId"] == specialist_id,
            "Проверка недоступна этому специалисту.",
            409,
        )
        require(
            command.get("sourceVersionId") == self.data["latest"]["id"]
            and command.get("revision") == review["revision"],
            "Оценка изменилась. Обновите заявку перед продолжением.",
            409,
        )
        return Review(review)

    def save_assessment(self, specialist_id, command):
        review = self.active_review(specialist_id, command)
        findings = review.assess(command.get("findings"))
        return ChangeSet(
            self.data["id"],
            specialist_id,
            review=dict(review.data, findings=findings, state="ASSESSED"),
        )

    def request_supplement(self, specialist_id, command):
        review = self.active_review(specialist_id, command)
        require(
            review.can_request_supplement(),
            "Сначала сохраните оценки всех трёх пунктов с хотя бы одним недостатком.",
        )
        instructions = text(command.get("text"), "Инструкции по дополнению")
        return ChangeSet(
            self.data["id"],
            specialist_id,
            "NEEDS_SUPPLEMENT",
            instructions,
            review=dict(review.data, state="SUPPLEMENT_REQUESTED"),
            decision=dict(kind="request", text=instructions),
        )

    def complete(self, specialist_id, command):
        review = self.active_review(specialist_id, command)
        require(
            review.can_complete(), "Сначала сохраните соответствие всех трёх пунктов."
        )
        conclusion = text(command.get("text"), "Заключение")
        return ChangeSet(
            self.data["id"],
            specialist_id,
            "COMPLETED",
            "Выдано заключение о комплектности",
            review=dict(review.data, state="COMPLETED"),
            decision=dict(kind="conclusion", text=conclusion),
        )
