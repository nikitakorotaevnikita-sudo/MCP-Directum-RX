import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from src.models.schemas import DocumentCandidate, DocumentSearchResult
from src.services.directum_client import DirectumError

# Вид документа → (набор OData, навигация на контрагента или None, подпись для пользователя).
DOCUMENT_KINDS: dict[str, tuple[str, str | None, str]] = {
    "any": ("IOfficialDocuments", None, "любой"),
    "incoming_letter": ("IIncomingLetters", "Correspondent", "входящее письмо"),
    "outgoing_letter": ("IOutgoingLetters", "Correspondent", "исходящее письмо"),
    "order": ("IOrderBases", None, "приказ / распоряжение"),
    "memo": ("IMemos", None, "служебная записка"),
    "contract": ("IContractualDocuments", "Counterparty", "договорной документ"),
    "citizen_request": ("IRequests", None, "обращение гражданина"),
}
# Контрагент без указания вида: он есть только у писем и договорных документов.
COUNTERPARTY_TARGETS_FOR_ANY: tuple[tuple[str, str], ...] = (
    ("IIncomingLetters", "Correspondent"),
    ("IOutgoingLetters", "Correspondent"),
    ("IContractualDocuments", "Counterparty"),
)
EMPLOYEE_NAVIGATIONS = ("PreparedBy", "OurSignatory", "Assignee")

POOL_SIZE = 50
MAX_MATCHES = 3
RELAX_PERIOD_DAYS = 30
WORD_SCORE = 10
EXACT_NUMBER_SCORE = 5
DATE_SCORE = 2
DOCUMENT_SELECT = "Id,Name,Subject,RegistrationNumber,RegistrationDate,Created"

MIN_TOKEN_LENGTH = 3
MIN_STEM_LENGTH = 4
TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
# Слова, которые описывают запрос, а не документ.
STOPWORDS = {
    "про", "для", "или", "что", "где", "это", "как", "его", "она", "они", "был", "была", "было",
    "письмо", "письма", "письме", "письмом", "документ", "документа", "документы", "документов",
    "найди", "найти", "нужен", "нужна", "нужно", "который", "которая", "которое", "года", "год",
}
# Окончания для грубого стемминга, от длинных к коротким.
ENDINGS = (
    "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ах", "ях", "ов", "ев", "ей", "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ам", "ям", "ом", "ем",
    "ы", "и", "а", "я", "о", "е", "у", "ю", "ь", "й",
)


def _stem(word: str) -> str:
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM_LENGTH:
            return word[: -len(ending)]
    return word


def text_stems(text: str | None) -> list[str]:
    stems: list[str] = []
    for token in TOKEN_PATTERN.findall((text or "").lower()):
        token = token.strip("'-")
        if len(token) < MIN_TOKEN_LENGTH or token in STOPWORDS:
            continue
        stem = _stem(token)
        if stem not in stems:
            stems.append(stem)
    return stems


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class DocumentCriteria:
    text: str | None = None
    kind: str = "any"
    counterparty: str | None = None
    employee: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    registration_number: str | None = None

    @property
    def has_period(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def has_any(self) -> bool:
        return bool(
            text_stems(self.text)
            or (self.counterparty or "").strip()
            or (self.employee or "").strip()
            or self.has_period
            or (self.registration_number or "").strip()
        )


def counterparty_supported(kind: str) -> bool:
    return kind == "any" or DOCUMENT_KINDS.get(kind, ("", None, ""))[1] is not None


@dataclass
class _Resolved:
    counterparty_ids: list[int]
    counterparty_names: list[str]
    employee_ids: list[int]
    employee_names: list[str]


class DocumentSearchService:
    """Поиск документа по признакам, которые помнит пользователь: несколько вариантов с причинами совпадения."""

    def __init__(self, client: Any, action_items: Any, tz: tzinfo | None = None):
        self.client = client
        self.action_items = action_items
        self.tz = tz or datetime.now().astimezone().tzinfo

    def find(self, criteria: DocumentCriteria, limit: int = 5) -> DocumentSearchResult:
        if criteria.kind not in DOCUMENT_KINDS:
            raise ValueError(f"Unknown document kind: {criteria.kind}")
        if not criteria.has_any():
            raise ValueError("At least one document criterion is required")
        if (criteria.counterparty or "").strip() and not counterparty_supported(criteria.kind):
            raise ValueError(f"Counterparty is not applicable to kind {criteria.kind}")

        resolved = _Resolved([], [], [], [])
        if (criteria.counterparty or "").strip():
            parties = self.action_items.search_counterparty(criteria.counterparty, top=MAX_MATCHES)
            if not parties:
                return DocumentSearchResult(message=f"Контрагент «{criteria.counterparty}» не найден. Уточните название.")
            resolved.counterparty_ids = [party.id for party in parties]
            resolved.counterparty_names = [party.name for party in parties]
        if (criteria.employee or "").strip():
            people = self.action_items.search_employee(criteria.employee, top=MAX_MATCHES)
            if not people:
                return DocumentSearchResult(message=f"Сотрудник «{criteria.employee}» не найден. Уточните ФИО.")
            resolved.employee_ids = [person.id for person in people]
            resolved.employee_names = [person.name for person in people]

        for attempt, relaxed in self._attempts(criteria):
            pool = self._pool(attempt, resolved)
            if pool:
                ranked = sorted(
                    (self._candidate(row, criteria, resolved) for row in pool),
                    key=lambda item: (item.score, self._sort_date(item)),
                    reverse=True,
                )
                return DocumentSearchResult(items=ranked[:limit], candidates_total=len(pool), relaxed=relaxed)
        return DocumentSearchResult(
            message="Ничего не найдено по заданным признакам. Попробуйте другие слова или уберите часть условий."
        )

    def _attempts(self, criteria: DocumentCriteria):
        """Исходные условия, затем ослабленные: без слов → шире период → без периода."""
        yield criteria, []
        has_text = bool(text_stems(criteria.text))
        widened = replace(
            criteria,
            date_from=criteria.date_from - timedelta(days=RELAX_PERIOD_DAYS) if criteria.date_from else None,
            date_to=criteria.date_to + timedelta(days=RELAX_PERIOD_DAYS) if criteria.date_to else None,
        )
        without_text_label = f"без слов из названия: «{criteria.text}»"
        widened_label = f"период расширен на {RELAX_PERIOD_DAYS} дней"
        if has_text:
            no_text = replace(criteria, text=None)
            if no_text.has_any():
                yield no_text, [without_text_label]
                if criteria.has_period:
                    yield replace(widened, text=None), [without_text_label, widened_label]
        if criteria.has_period:
            yield widened, [widened_label]
            no_period = replace(criteria, date_from=None, date_to=None)
            if no_period.has_any():
                yield no_period, [widened_label, "без периода"]

    def _pool(self, criteria: DocumentCriteria, resolved: _Resolved) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        failures = 0
        targets = self._targets(criteria)
        last_error: DirectumError | None = None
        for entity_set, nav in targets:
            try:
                batch = self.client.query(
                    entity_set,
                    filter_=self._filter(criteria, resolved, nav),
                    select=DOCUMENT_SELECT,
                    expand="DocumentKind($select=Name)",
                    orderby="RegistrationDate desc",
                    top=POOL_SIZE,
                )
            except DirectumError as exc:
                # Набор может не поддерживать навигацию в этой конфигурации — пропускаем его.
                failures += 1
                last_error = exc
                continue
            for row in batch:
                document_id = int(row["Id"])
                if document_id not in seen:
                    seen.add(document_id)
                    rows.append(row)
        if failures == len(targets) and last_error is not None:
            raise last_error
        return rows

    def _targets(self, criteria: DocumentCriteria) -> list[tuple[str, str | None]]:
        if criteria.kind == "any" and (criteria.counterparty or "").strip():
            return list(COUNTERPARTY_TARGETS_FOR_ANY)
        entity_set, nav, _ = DOCUMENT_KINDS[criteria.kind]
        return [(entity_set, nav)]

    def _filter(self, criteria: DocumentCriteria, resolved: _Resolved, counterparty_nav: str | None) -> str:
        conditions: list[str] = []
        stems = text_stems(criteria.text)
        if stems:
            parts = []
            for stem in stems:
                for variant in dict.fromkeys((stem, stem.capitalize())):
                    parts.append(f"contains(Name,'{_literal(variant)}') or contains(Subject,'{_literal(variant)}')")
            conditions.append("(" + " or ".join(parts) + ")")
        if criteria.has_period:
            conditions.append(self._period_condition(criteria.date_from, criteria.date_to))
        number = (criteria.registration_number or "").strip()
        if number:
            conditions.append(f"contains(RegistrationNumber,'{_literal(number)}')")
        if resolved.counterparty_ids and counterparty_nav:
            conditions.append("(" + " or ".join(f"{counterparty_nav}/Id eq {i}" for i in resolved.counterparty_ids) + ")")
        if resolved.employee_ids:
            conditions.append(
                "("
                + " or ".join(f"{nav}/Id eq {i}" for i in resolved.employee_ids for nav in EMPLOYEE_NAVIGATIONS)
                + ")"
            )
        return " and ".join(conditions)

    def _period_condition(self, date_from: date | None, date_to: date | None) -> str:
        def bounds(field: str) -> str:
            parts = []
            if date_from:
                parts.append(f"{field} ge {self._day_literal(date_from)}")
            if date_to:
                parts.append(f"{field} lt {self._day_literal(date_to + timedelta(days=1))}")
            return " and ".join(parts)

        return f"(({bounds('RegistrationDate')}) or (RegistrationDate eq null and {bounds('Created')}))"

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()

    def _candidate(self, row: dict[str, Any], criteria: DocumentCriteria, resolved: _Resolved) -> DocumentCandidate:
        name = row.get("Name") or ""
        haystack = f"{name} {row.get('Subject') or ''}".lower()
        score = 0
        reasons: list[str] = []

        matched = [stem for stem in text_stems(criteria.text) if stem in haystack]
        if matched:
            score += WORD_SCORE * len(matched)
            reasons.append("слова: " + ", ".join(matched))

        number = (criteria.registration_number or "").strip()
        actual_number = row.get("RegistrationNumber") or ""
        if number and actual_number:
            if actual_number.casefold() == number.casefold():
                score += EXACT_NUMBER_SCORE
                reasons.append(f"номер: {actual_number}")
            elif number.casefold() in actual_number.casefold():
                reasons.append(f"номер содержит «{number}»")

        registration_date = _parse_datetime(row.get("RegistrationDate"))
        created = _parse_datetime(row.get("Created"))
        moment = registration_date or created
        if criteria.has_period and moment is not None:
            day = moment.astimezone(self.tz).date()
            if (criteria.date_from is None or day >= criteria.date_from) and (criteria.date_to is None or day <= criteria.date_to):
                score += DATE_SCORE
                reasons.append("дата в периоде")

        if resolved.counterparty_names:
            reasons.append("контрагент: " + " / ".join(resolved.counterparty_names))
        if resolved.employee_names:
            reasons.append("сотрудник: " + " / ".join(resolved.employee_names))
        if criteria.kind != "any":
            reasons.append(f"вид: {DOCUMENT_KINDS[criteria.kind][2]}")

        kind = row.get("DocumentKind")
        document_id = int(row["Id"])
        return DocumentCandidate(
            id=document_id,
            name=name,
            kind=kind.get("Name") if isinstance(kind, dict) else None,
            registration_number=actual_number or None,
            registration_date=registration_date,
            created=created,
            url=self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None,
            score=score,
            match_reasons=reasons,
        )

    @staticmethod
    def _sort_date(item: DocumentCandidate) -> float:
        moment = item.registration_date or item.created
        return moment.timestamp() if moment is not None else float("-inf")
