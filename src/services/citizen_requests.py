from collections.abc import Callable
from datetime import date, datetime, timezone, tzinfo
from typing import Any

from src.models.schemas import CitizenRequestStatus
from src.services.citizen_request_analytics import REVIEW_RESULT_LABELS
from src.services.directum_client import DirectumError

# Только служебные реквизиты: контакты заявителя (PostalAddress, Email, Phones, PIN) не запрашиваем.
REQUEST_SELECT = (
    "Id,Name,RegistrationNumber,RegistrationDate,RequestDate,Deadline,InitialDeadline,ResponseDate,"
    "ExecutionState,IsRepeated,RequestType,ReceiptForm,QuestionsNames,FullName"
)
FULL_EXPAND = (
    "Assignee($select=Name),TransferredTo($select=Name),"
    "AnswerLetter($select=Id,Name,RegistrationNumber,RegistrationDate),"
    "ProlongationDeadline($select=Number,NewDeadline,ReasonChangeDeadline),"
    "Questions($select=ReviewResult;$expand=Question($select=Name,FullCode))"
)
# Если конфигурация стенда не поддерживает часть навигаций — сужаем запрос, а не падаем.
MINIMAL_EXPAND = "Assignee($select=Name),AnswerLetter($select=Id,Name,RegistrationNumber,RegistrationDate)"
EXPAND_FALLBACKS = (FULL_EXPAND, MINIMAL_EXPAND, None)

EXECUTION_STATES = {
    "OnReview": "на рассмотрении",
    "OnExecution": "на исполнении",
    "Executed": "исполнено",
    "WithoutExecut": "не требует исполнения",
    "Aborted": "прекращено",
    "Sending": "отправка",
    "OnControl": "на контроле",
    "ControlRemoved": "снято с контроля",
}
ANSWERED_STATES = ("Executed", "WithoutExecut")


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _name(value: Any) -> str | None:
    return value.get("Name") if isinstance(value, dict) and value.get("Name") else None


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


class CitizenRequestService:
    """Статус обращения гражданина по номеру, id или ФИО заявителя — для сотрудника, без контактов заявителя."""

    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def find(
        self,
        request_id: int | None = None,
        registration_number: str | None = None,
        applicant: str | None = None,
        limit: int = 5,
    ) -> list[CitizenRequestStatus]:
        number = (registration_number or "").strip()
        person = (applicant or "").strip()
        if request_id is None and not number and not person:
            raise ValueError("Request id, registration number or applicant is required")
        conditions = []
        if request_id is not None:
            conditions.append(f"Id eq {int(request_id)}")
        if person:
            conditions.append(f"contains(FullName,'{_literal(person)}')")
        if number:
            # Сначала точный номер, затем по вхождению («13-ОГ» найдёт и «113-ОГ», поэтому только как запасной путь).
            filters = [
                " and ".join(conditions + [f"RegistrationNumber eq '{_literal(number)}'"]),
                " and ".join(conditions + [f"contains(RegistrationNumber,'{_literal(number)}')"]),
            ]
        else:
            filters = [" and ".join(conditions)]
        rows: list[dict[str, Any]] = []
        for filter_ in filters:
            rows = self._query(filter_, limit)
            if rows:
                break
        return [self._status(row) for row in rows]

    def _query(self, filter_: str, limit: int) -> list[dict[str, Any]]:
        last_error: DirectumError | None = None
        for expand in EXPAND_FALLBACKS:
            try:
                return self.client.query(
                    "IRequests",
                    filter_=filter_,
                    select=REQUEST_SELECT,
                    expand=expand,
                    orderby="RegistrationDate desc",
                    top=limit,
                )
            except DirectumError as exc:
                if exc.status_code != 400:
                    raise
                last_error = exc
        assert last_error is not None
        raise last_error

    def _day(self, value: Any) -> date | None:
        moment = _parse(value)
        return moment.astimezone(self.tz).date() if moment else None

    def _iso_day(self, value: Any) -> str | None:
        day = self._day(value)
        return day.isoformat() if day else None

    def _status(self, row: dict[str, Any]) -> CitizenRequestStatus:
        state = row.get("ExecutionState")
        deadline = self._day(row.get("Deadline"))
        initial = self._day(row.get("InitialDeadline"))
        response_date = self._day(row.get("ResponseDate"))
        letter = row.get("AnswerLetter") if isinstance(row.get("AnswerLetter"), dict) else None
        transferred_to = _name(row.get("TransferredTo"))
        prolongations = [
            {
                "number": item.get("Number"),
                "new_deadline": self._iso_day(item.get("NewDeadline")),
                "reason": item.get("ReasonChangeDeadline"),
            }
            for item in sorted(row.get("ProlongationDeadline") or [], key=lambda item: item.get("Number") or 0)
        ]
        answered = bool(response_date or letter or state in ANSWERED_STATES)
        aborted = state == "Aborted"
        in_work = not (answered or aborted or transferred_to)
        today = self._now().astimezone(self.tz).date()
        days_left = (deadline - today).days if (in_work and deadline) else None
        questions = [
            {
                "question": _name(item.get("Question")) or "",
                "code": (item.get("Question") or {}).get("FullCode"),
                "review_result": item.get("ReviewResult"),
                "review_result_label": REVIEW_RESULT_LABELS.get(item.get("ReviewResult"), item.get("ReviewResult")),
            }
            for item in row.get("Questions") or []
        ]
        if not questions and row.get("QuestionsNames"):
            questions = [{"question": row["QuestionsNames"], "code": None, "review_result": None, "review_result_label": None}]

        status = CitizenRequestStatus(
            id=int(row["Id"]),
            name=row.get("Name") or "",
            registration_number=row.get("RegistrationNumber"),
            registration_date=self._day(row.get("RegistrationDate")),
            request_date=self._day(row.get("RequestDate")),
            applicant=row.get("FullName"),
            request_type=row.get("RequestType"),
            receipt_form=row.get("ReceiptForm"),
            is_repeated=bool(row.get("IsRepeated")),
            questions=questions,
            assignee=_name(row.get("Assignee")),
            execution_state=EXECUTION_STATES.get(state, state) if state else None,
            deadline=deadline,
            initial_deadline=initial,
            days_left=days_left,
            overdue=days_left is not None and days_left < 0,
            prolonged=bool(prolongations) or bool(deadline and initial and deadline != initial),
            prolongations=prolongations,
            answered=answered,
            response_date=response_date,
            answer_letter=self._letter(letter),
            transferred_to=transferred_to,
            url=self._url(int(row["Id"])),
        )
        status.status_text = self._text(status, aborted)
        return status

    def _letter(self, letter: dict[str, Any] | None) -> dict[str, Any] | None:
        if not letter:
            return None
        return {
            "id": letter.get("Id"),
            "name": letter.get("Name"),
            "registration_number": letter.get("RegistrationNumber"),
            "registration_date": self._iso_day(letter.get("RegistrationDate")),
            "url": self._url(int(letter["Id"])) if letter.get("Id") is not None else None,
        }

    def _url(self, document_id: int) -> str | None:
        if hasattr(self.client, "build_document_card_url"):
            return self.client.build_document_card_url(document_id)
        return None

    @staticmethod
    def _text(status: CitizenRequestStatus, aborted: bool) -> str:
        state = f" ({status.execution_state})" if status.execution_state else ""
        if aborted:
            return "Рассмотрение прекращено."
        if status.transferred_to:
            return f"Перенаправлено: {status.transferred_to}{state}."
        if status.answered:
            if status.response_date:
                return f"Рассмотрено{state}, ответ от {_fmt(status.response_date)}."
            if status.answer_letter and status.answer_letter.get("registration_number"):
                return f"Рассмотрено{state}, ответ — письмо № {status.answer_letter['registration_number']}."
            return f"Рассмотрено{state}."
        if status.deadline is None or status.days_left is None:
            return f"На рассмотрении{state}, срок не указан."
        if status.days_left < 0:
            text = f"На рассмотрении{state}, срок {_fmt(status.deadline)} просрочен на {-status.days_left} дн."
        elif status.days_left == 0:
            text = f"На рассмотрении{state}, срок сегодня ({_fmt(status.deadline)})."
        else:
            text = f"На рассмотрении{state}, до срока {_fmt(status.deadline)} осталось {status.days_left} дн."
        if status.prolonged:
            text = f"{text} Срок продлён."
        return text
