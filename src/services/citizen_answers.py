"""Зарегистрированные ответы на обращения граждан — для проверки качества постфактум.

Ответ — исходящее письмо по обращению (IOutgoingRequestLetters) вида «Ответное письмо», зарегистрированное за период.
По письму: обращения и вопросы, на которые оно отвечает, с результатом рассмотрения по каждому (Requests),
кто подготовил и подписал, срок обращения и дата ответа, текст письма. Контакты заявителей не запрашиваются.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from src.services.citizen_request_analytics import REVIEW_RESULT_LABELS
from src.services.directum_client import DirectumError

LETTERS = "IOutgoingRequestLetters"
ANSWER_KIND = "Ответное письмо"
MAX_LIMIT = 20
DEFAULT_TEXT_CHARS = 6000
SELECT = "Id,Name,Subject,RegistrationNumber,RegistrationDate,Result"
EXPAND = (
    "DocumentKind($select=Name),PreparedBy($select=Id,Name),OurSignatory($select=Name),"
    "Requests($expand=Request($select=Id,RegistrationNumber,RegistrationDate,Deadline,InitialDeadline,IsRepeated),"
    "Question($select=Name,FullCode)),"
    # Запасной путь: у части писем строк Requests нет, только одиночное «Обращение» — вопросы берём из него.
    "Request($select=Id,RegistrationNumber,RegistrationDate,Deadline,InitialDeadline,IsRepeated;"
    "$expand=Questions($select=ReviewResult;$expand=Question($select=Name,FullCode)))"
)


def _name(value: Any) -> str | None:
    name = value.get("Name") if isinstance(value, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _literal(value: str) -> str:
    return value.replace("'", "''")


class CitizenAnswersService:
    def __init__(self, client: Any, text_service: Any, tz: tzinfo | None = None,
                 now: Callable[[], datetime] | None = None):
        self.client = client
        self.text_service = text_service
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list_answers(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        prepared_by_id: int | None = None,
        classifier_code: str | None = None,
        include_text: bool = True,
        max_chars: int = DEFAULT_TEXT_CHARS,
        limit: int = 5,
        skip: int = 0,
    ) -> dict[str, Any]:
        today = self._now().astimezone(self.tz).date()
        start = date_from or today.replace(day=1)
        end = date_to or today
        conditions = [
            "RegistrationState eq 'Registered'",
            f"DocumentKind/Name eq '{ANSWER_KIND}'",
            f"RegistrationDate ge {self._day_literal(start)}",
            f"RegistrationDate lt {self._day_literal(end + timedelta(days=1))}",
        ]
        if prepared_by_id is not None:
            conditions.append(f"PreparedBy/Id eq {int(prepared_by_id)}")
        if classifier_code:
            code = _literal(classifier_code)
            conditions.append(f"(Requests/any(r: startswith(r/Question/FullCode,'{code}')) or "
                              f"Request/Questions/any(q: startswith(q/Question/FullCode,'{code}')))")
        filter_ = " and ".join(conditions)
        size = max(1, min(limit, MAX_LIMIT))
        rows = self.client.query(LETTERS, filter_=filter_, select=SELECT, expand=EXPAND,
                                 orderby="RegistrationDate desc,Id desc", top=size, skip=max(0, skip))
        total = self.client.count(LETTERS, filter_=filter_)
        answers = [self._answer(row, include_text, max_chars) for row in rows]
        next_skip = skip + len(answers) if skip + len(answers) < total else None
        return {
            "date_from": start.isoformat(), "date_to": end.isoformat(), "classifier_code": classifier_code,
            "total": total, "skip": skip, "returned": len(answers), "next_skip": next_skip,
            "truncated": next_skip is not None, "answers": answers,
        }

    def _answer(self, row: dict[str, Any], include_text: bool, max_chars: int) -> dict[str, Any]:
        letter_day = self._day(row.get("RegistrationDate"))
        requests: dict[int, dict[str, Any]] = {}
        for item in row.get("Requests") or []:
            request = item.get("Request") if isinstance(item.get("Request"), dict) else None
            if not request or request.get("Id") is None:
                continue
            entry = requests.setdefault(int(request["Id"]), {
                "id": int(request["Id"]),
                "registration_number": request.get("RegistrationNumber"),
                "registration_date": self._iso(request.get("RegistrationDate")),
                "deadline": self._iso(request.get("Deadline")),
                "is_repeated": bool(request.get("IsRepeated")),
                "questions": [],
                "url": self._doc_url(int(request["Id"])),
            })
            question = item.get("Question") if isinstance(item.get("Question"), dict) else {}
            code = item.get("Result")
            entry["questions"].append({
                "question": question.get("Name") or "",
                "code": question.get("FullCode"),
                "review_result": code,
                "review_result_label": REVIEW_RESULT_LABELS.get(code, code),
            })
        single = row.get("Request") if isinstance(row.get("Request"), dict) else None
        if not requests and single and single.get("Id") is not None:
            requests[int(single["Id"])] = {
                "id": int(single["Id"]),
                "registration_number": single.get("RegistrationNumber"),
                "registration_date": self._iso(single.get("RegistrationDate")),
                "deadline": self._iso(single.get("Deadline")),
                "is_repeated": bool(single.get("IsRepeated")),
                "questions": [{
                    "question": (q.get("Question") or {}).get("Name") or "",
                    "code": (q.get("Question") or {}).get("FullCode"),
                    "review_result": q.get("ReviewResult"),
                    "review_result_label": REVIEW_RESULT_LABELS.get(q.get("ReviewResult"), q.get("ReviewResult")),
                } for q in single.get("Questions") or [] if isinstance(q, dict)],
                "url": self._doc_url(int(single["Id"])),
            }
        for entry in requests.values():
            deadline = self._day_from_iso(entry["deadline"])
            entry["days_late"] = (letter_day - deadline).days if letter_day and deadline and letter_day > deadline else 0
        result: dict[str, Any] = {
            "id": int(row["Id"]),
            "registration_number": row.get("RegistrationNumber"),
            "registration_date": letter_day.isoformat() if letter_day else None,
            "subject": row.get("Subject") or row.get("Name") or "",
            "result": row.get("Result"),
            "result_label": REVIEW_RESULT_LABELS.get(row.get("Result"), row.get("Result")),
            "prepared_by": _name(row.get("PreparedBy")),
            "signatory": _name(row.get("OurSignatory")),
            "requests": list(requests.values()),
            "url": self._doc_url(int(row["Id"])),
        }
        if include_text:
            try:
                text = self.text_service.get_text(int(row["Id"]), max_chars=max_chars)
                result.update({"text": text.text, "text_truncated": text.truncated, "text_message": text.message or None})
            except DirectumError as exc:
                result.update({"text": "", "text_truncated": False, "text_message": f"Текст не получен: {exc.safe_message}"})
        return result

    def _day(self, value: Any) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone(self.tz).date()
        except ValueError:
            return None

    def _iso(self, value: Any) -> str | None:
        day = self._day(value)
        return day.isoformat() if day else None

    @staticmethod
    def _day_from_iso(value: str | None) -> date | None:
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()

    def _doc_url(self, document_id: int) -> str | None:
        return self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None
