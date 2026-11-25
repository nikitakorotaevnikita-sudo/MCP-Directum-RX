import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from src.models.schemas import CitizenRequestAnalytics, ClassifierMatch

# Уровни ТОТК: сколько частей полного кода вопроса (0001.0001.0001.0001) задаёт группу.
# Внимание к названиям RX: Topic — это «тематика» (2-й уровень), Theme — «тема» (3-й уровень).
LEVELS: dict[str, tuple[int, str | None, str]] = {
    "section": (1, "Section", "раздел"),
    "topic": (2, "Topic", "тематика"),
    "theme": (3, "Theme", "тема"),
    "question": (4, None, "вопрос"),
}
# Подписи результатов рассмотрения вопроса — из ресурсов решения (GD.CitizenRequests, Request@Questions).
REVIEW_RESULT_LABELS = {
    "Draft": "Не зарегистрировано",
    "Active": "На рассмотрении",
    "InWorkExtended": "Рассмотрение продлено",
    "Supported": "Поддержано",
    "NotSupported": "Не поддержано",
    "Explained": "Разъяснено",
    "ActionsTaken": "Меры приняты",
    "Transferred": "Направлено по компетенции",
    "Denied": "Отказано",
    "Obsolete": "Оставлено без ответа",
    "Outdated": "Недействующий",
}
PAGE_SIZE = 500
DEFAULT_MAX_REQUESTS = 5000
CLASSIFIER_CODE = re.compile(r"^\d{4}(\.\d{4}){0,4}$")
QUESTIONS_EXPAND = (
    "Questions($select=ReviewResult;$expand=Question($select=Name,FullCode;"
    "$expand=Section($select=Name),Topic($select=Name),Theme($select=Name)))"
)
MATCHES_PER_LEVEL = 5
# Территория обращения: район из карточки, иначе район города (справочник городов госрешения).
# Только названия территорий — ни адресов, ни заявителей (ПДн) не запрашиваем.
TERRITORY_SELECT = "Id,Deadline,ResponseDate,ExecutionState"
TERRITORY_EXPAND = (
    "MunicipalArea($select=Id,Name),Region($select=Name),"
    "City($select=Id,Name;$expand=MunicipalAreaGD($select=Id,Name),Region($select=Name)),"
    "AnswerLetter($select=Id),TransferredTo($select=Id),"
    "Questions($select=Id;$expand=Question($select=FullCode;$expand=Topic($select=Name)))"
)
CLOSED_STATES = {"Executed", "WithoutExecut", "Aborted"}


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _name(value: Any) -> str:
    return (value.get("Name") or "") if isinstance(value, dict) else ""


class CitizenRequestAnalyticsService:
    """Счёт обращений и вопросов по уровням общероссийского тематического классификатора (ТОТК)."""

    def __init__(
        self,
        client: Any,
        tz: tzinfo | None = None,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        now: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self.max_requests = max_requests
        self._now = now or (lambda: datetime.now(timezone.utc))

    def analytics(
        self,
        level: str = "topic",
        date_from: date | None = None,
        date_to: date | None = None,
        classifier_code: str | None = None,
        top: int = 10,
    ) -> CitizenRequestAnalytics:
        if level not in LEVELS:
            raise ValueError(f"Unknown classifier level: {level}")
        prefix = (classifier_code or "").strip() or None
        if prefix is not None and not CLASSIFIER_CODE.match(prefix):
            raise ValueError("Classifier code must look like 0003 or 0003.0008.0086")
        depth, nav, label = LEVELS[level]

        groups: dict[str, dict[str, Any]] = {}
        reviews: Counter[str] = Counter()
        requests_total = classified = entries = 0
        truncated = False
        for request in self._requests(date_from, date_to, prefix):
            if requests_total >= self.max_requests:
                truncated = True
                break
            requests_total += 1
            seen: set[str] = set()
            for item in request.get("Questions") or []:
                question = item.get("Question") or {}
                full_code = question.get("FullCode") or ""
                if not full_code or (prefix and not full_code.startswith(prefix)):
                    continue
                entries += 1
                if item.get("ReviewResult"):
                    reviews[item["ReviewResult"]] += 1
                key = ".".join(full_code.split(".")[:depth])
                group = groups.setdefault(
                    key, {"code": key, "name": _name(question.get(nav)) if nav else question.get("Name") or "", "requests": 0, "questions": 0}
                )
                group["questions"] += 1
                if key not in seen:
                    seen.add(key)
                    group["requests"] += 1
            if seen:
                classified += 1

        ranked = sorted(groups.values(), key=lambda group: (group["requests"], group["questions"]), reverse=True)
        return CitizenRequestAnalytics(
            level=level,
            level_label=label,
            date_from=date_from,
            date_to=date_to,
            classifier_code=prefix,
            requests_total=requests_total,
            requests_classified=classified,
            requests_unclassified=requests_total - classified,
            question_entries_total=entries,
            groups=ranked[:top],
            groups_total=len(ranked),
            review_results=[
                {"code": code, "label": REVIEW_RESULT_LABELS.get(code, code), "count": count}
                for code, count in reviews.most_common()
            ],
            truncated=truncated,
        )

    def search_classifier(self, query: str) -> list[ClassifierMatch]:
        text = _literal(query.strip())
        matches: list[ClassifierMatch] = []
        for row in self.client.query("ISections", filter_=f"contains(Name,'{text}')", select="Id,Name,Code", top=MATCHES_PER_LEVEL):
            matches.append(ClassifierMatch(level="section", level_label="раздел", code=row.get("Code") or "", name=row.get("Name") or ""))
        for level, entity_set in (("topic", "ITopics"), ("theme", "IThemes")):
            depth, nav, label = LEVELS[level]
            for row in self.client.query(entity_set, filter_=f"contains(Name,'{text}')", select="Id,Name", top=MATCHES_PER_LEVEL):
                sample = self.client.query(
                    "IQuestions",
                    filter_=f"{nav}/Id eq {int(row['Id'])}",
                    select="FullCode",
                    expand="Section($select=Name),Topic($select=Name)",
                    top=1,
                )
                if not sample or not sample[0].get("FullCode"):
                    continue
                code = ".".join(sample[0]["FullCode"].split(".")[:depth])
                path = [_name(sample[0].get("Section"))] + ([_name(sample[0].get("Topic"))] if level == "theme" else [])
                matches.append(ClassifierMatch(level=level, level_label=label, code=code, name=row.get("Name") or "", path=" > ".join(p for p in path if p)))
        for row in self.client.query(
            "IQuestions",
            filter_=f"contains(Name,'{text}')",
            select="Name,FullCode",
            expand="Section($select=Name),Topic($select=Name),Theme($select=Name)",
            top=MATCHES_PER_LEVEL,
        ):
            path = [_name(row.get("Section")), _name(row.get("Topic")), _name(row.get("Theme"))]
            matches.append(
                ClassifierMatch(level="question", level_label="вопрос", code=row.get("FullCode") or "", name=row.get("Name") or "", path=" > ".join(p for p in path if p))
            )
        return matches

    def by_territory(
        self,
        region: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        classifier_code: str | None = None,
        top_topics: int = 3,
    ) -> dict[str, Any]:
        """Обращения по муниципальным районам: сколько обращений, вопросов, просрочено, частые тематики.

        Район — из карточки (MunicipalArea); если не указан — район города (City.MunicipalAreaGD);
        если и его нет — сам город (source=city). Без территории — в requests_without_territory.
        """
        today = self._now().astimezone(self.tz).date()
        region_query = (region or "").strip().lower().replace("ё", "е") or None
        areas: dict[str, dict[str, Any]] = {}
        total = in_region = without = 0
        truncated = False
        for row in self._requests(date_from, date_to, classifier_code, select=TERRITORY_SELECT, expand=TERRITORY_EXPAND):
            if total >= self.max_requests:
                truncated = True
                break
            total += 1
            city = row.get("City") or {}
            region_name = _name(row.get("Region")) or _name(city.get("Region"))
            if region_query and region_query not in region_name.lower().replace("ё", "е"):
                continue
            in_region += 1
            area, source = _name(row.get("MunicipalArea")), "municipal_area"
            if not area:
                area, source = _name(city.get("MunicipalAreaGD")), "city_area"
            if not area:
                area, source = _name(city), "city"
            if not area:
                without += 1
                continue
            item = areas.setdefault(area, {"area": area, "region": region_name or None, "source": source,
                                           "requests": 0, "questions": 0, "overdue": 0, "_topics": Counter()})
            item["requests"] += 1
            questions = row.get("Questions") or []
            item["questions"] += len(questions)
            for question in questions:
                topic = _name((question.get("Question") or {}).get("Topic"))
                if topic:
                    item["_topics"][topic] += 1
            if self._overdue(row, today):
                item["overdue"] += 1
        result_areas = []
        for item in sorted(areas.values(), key=lambda a: (-a["requests"], a["area"])):
            topics = item.pop("_topics")
            item["top_topics"] = [{"name": n, "count": c} for n, c in topics.most_common(max(0, int(top_topics)))]
            result_areas.append(item)
        return {
            "region_filter": region,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "classifier_code": classifier_code,
            "requests_total": total,
            "requests_in_region": in_region,
            "requests_with_territory": in_region - without,
            "requests_without_territory": without,
            "areas_total": len(result_areas),
            "areas": result_areas,
            "truncated": truncated,
        }

    def _overdue(self, row: dict[str, Any], today: date) -> bool:
        """Просрочено: срок прошёл, ответа нет, не перенаправлено и не закрыто (как list_citizen_requests_due)."""
        if row.get("ResponseDate") or row.get("AnswerLetter") or row.get("TransferredTo"):
            return False
        if row.get("ExecutionState") in CLOSED_STATES:
            return False
        deadline = str(row.get("Deadline") or "")[:10]
        try:
            return bool(deadline) and date.fromisoformat(deadline) < today
        except ValueError:
            return False

    def _requests(
        self,
        date_from: date | None,
        date_to: date | None,
        prefix: str | None,
        select: str = "Id",
        expand: str = QUESTIONS_EXPAND,
    ):
        """Постранично по возрастанию Id (keyset): «Id gt последний» вместо $skip."""
        last_id = 0
        while True:
            conditions = [f"Id gt {last_id}"]
            if date_from:
                conditions.append(f"RegistrationDate ge {self._day_literal(date_from)}")
            if date_to:
                conditions.append(f"RegistrationDate lt {self._day_literal(date_to + timedelta(days=1))}")
            if prefix:
                conditions.append(f"Questions/any(q: startswith(q/Question/FullCode,'{prefix}'))")
            batch = self.client.query(
                "IRequests",
                filter_=" and ".join(conditions),
                select=select,
                expand=expand,
                orderby="Id asc",
                top=PAGE_SIZE,
            )
            yield from batch
            if len(batch) < PAGE_SIZE:
                return
            last_id = int(batch[-1]["Id"])

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()
