import re
from collections import Counter
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

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


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _name(value: Any) -> str:
    return (value.get("Name") or "") if isinstance(value, dict) else ""


class CitizenRequestAnalyticsService:
    """Счёт обращений и вопросов по уровням общероссийского тематического классификатора (ТОТК)."""

    def __init__(self, client: Any, tz: tzinfo | None = None, max_requests: int = DEFAULT_MAX_REQUESTS):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self.max_requests = max_requests

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

    def _requests(self, date_from: date | None, date_to: date | None, prefix: str | None):
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
                select="Id",
                expand=QUESTIONS_EXPAND,
                orderby="Id asc",
                top=PAGE_SIZE,
            )
            yield from batch
            if len(batch) < PAGE_SIZE:
                return
            last_id = int(batch[-1]["Id"])

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()
