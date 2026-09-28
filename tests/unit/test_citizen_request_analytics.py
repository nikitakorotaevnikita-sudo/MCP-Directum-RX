from datetime import date, timedelta, timezone

import pytest

from src.services.citizen_request_analytics import PAGE_SIZE, CitizenRequestAnalyticsService

STAND_TZ = timezone(timedelta(hours=4))


def q(full_code, name, topic="Тематика", theme="Тема", section="Раздел", review="Explained"):
    return {
        "ReviewResult": review,
        "Question": {"FullCode": full_code, "Name": name, "Section": {"Name": section}, "Topic": {"Name": topic}, "Theme": {"Name": theme}},
    }


ROADS = q("0003.0008.0086.0567", "Дороги", topic="Хозяйственная деятельность", theme="Транспорт", section="Экономика")
LIGHT = q("0003.0008.0086.0570", "Освещение", topic="Хозяйственная деятельность", theme="Транспорт", section="Экономика", review="Supported")
CRIME = q("0004.0016.0162.1000", "Борьба с преступностью", topic="Безопасность и охрана правопорядка", theme="Безопасность общества", section="Оборона, безопасность, законность")

REQUESTS = [
    {"Id": 1, "Questions": [ROADS, LIGHT]},  # одно обращение — два вопроса одной темы
    {"Id": 2, "Questions": [ROADS]},
    {"Id": 3, "Questions": [CRIME]},
    {"Id": 4, "Questions": []},  # без классификации
]


class FakeClient:
    def __init__(self, rows=None, page_size=None):
        self.rows = REQUESTS if rows is None else rows
        self.page_size = page_size
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        last_id = int(kwargs["filter_"].split("Id gt ")[1].split(" ")[0])
        remaining = [row for row in self.rows if row["Id"] > last_id]
        return remaining[: kwargs["top"]]


def service(client, max_requests=5000):
    return CitizenRequestAnalyticsService(client, tz=STAND_TZ, max_requests=max_requests)


def test_counts_requests_and_questions_by_topic():
    result = service(FakeClient()).analytics(level="topic")

    assert result.level == "topic"
    assert result.level_label == "тематика"
    assert result.requests_total == 4
    assert result.requests_classified == 3
    assert result.requests_unclassified == 1
    assert result.question_entries_total == 4
    assert result.groups[0] == {"code": "0003.0008", "name": "Хозяйственная деятельность", "requests": 2, "questions": 3}
    assert result.groups[1] == {"code": "0004.0016", "name": "Безопасность и охрана правопорядка", "requests": 1, "questions": 1}
    assert result.review_results == [
        {"code": "Explained", "label": "Разъяснено", "count": 3},
        {"code": "Supported", "label": "Поддержано", "count": 1},
    ]
    assert result.truncated is False


@pytest.mark.parametrize(
    ("level", "first"),
    [
        ("section", {"code": "0003", "name": "Экономика", "requests": 2, "questions": 3}),
        ("theme", {"code": "0003.0008.0086", "name": "Транспорт", "requests": 2, "questions": 3}),
        ("question", {"code": "0003.0008.0086.0567", "name": "Дороги", "requests": 2, "questions": 2}),
    ],
)
def test_levels(level, first):
    assert service(FakeClient()).analytics(level=level).groups[0] == first


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        service(FakeClient()).analytics(level="rubric")


def test_query_shape_period_and_keyset_paging():
    client = FakeClient()

    service(client).analytics(level="topic", date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IRequests"
    assert kwargs["filter_"] == (
        "Id gt 0 and RegistrationDate ge 2026-01-01T00:00:00+04:00 and RegistrationDate lt 2026-04-01T00:00:00+04:00"
    )
    assert kwargs["orderby"] == "Id asc"
    assert kwargs["top"] == PAGE_SIZE
    assert "Question($select=Name,FullCode" in kwargs["expand"]
    assert "Topic($select=Name)" in kwargs["expand"]


def test_pages_until_short_batch():
    rows = [{"Id": i, "Questions": [ROADS]} for i in range(1, PAGE_SIZE + 3)]
    client = FakeClient(rows=rows)

    result = service(client).analytics(level="section")

    assert len(client.calls) == 2
    assert client.calls[1][1]["filter_"] == f"Id gt {PAGE_SIZE}"
    assert result.requests_total == PAGE_SIZE + 2


def test_classifier_prefix_filters_server_side_and_in_aggregation():
    client = FakeClient()

    result = service(client).analytics(level="question", classifier_code="0003")

    assert "Questions/any(q: startswith(q/Question/FullCode,'0003'))" in client.calls[0][1]["filter_"]
    assert {group["code"] for group in result.groups} == {"0003.0008.0086.0567", "0003.0008.0086.0570"}
    assert result.question_entries_total == 3
    assert result.classifier_code == "0003"


def test_bad_classifier_code_rejected():
    with pytest.raises(ValueError):
        service(FakeClient()).analytics(level="topic", classifier_code="0003') or (1 eq 1")


def test_limit_marks_truncation():
    rows = [{"Id": i, "Questions": [ROADS]} for i in range(1, 12)]

    result = service(FakeClient(rows=rows), max_requests=5).analytics(level="topic")

    assert result.requests_total == 5
    assert result.truncated is True


def test_top_limits_groups_but_counts_all():
    result = service(FakeClient()).analytics(level="question", top=1)

    assert len(result.groups) == 1
    assert result.groups_total == 3


class ClassifierClient:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        data = {
            "ISections": [{"Id": 976, "Name": "Экономика", "Code": "0003"}],
            "ITopics": [{"Id": 1100, "Name": "Хозяйственная деятельность"}],
            "IThemes": [{"Id": 1200, "Name": "Транспорт"}],
        }
        if entity_set == "IQuestions" and "/Id eq" in kwargs["filter_"]:
            return [{"FullCode": "0003.0008.0086.0567", "Section": {"Name": "Экономика"}, "Topic": {"Name": "Хозяйственная деятельность"}}]
        if entity_set == "IQuestions":
            return [
                {
                    "Name": "Дороги",
                    "FullCode": "0003.0008.0086.0567",
                    "Section": {"Name": "Экономика"},
                    "Topic": {"Name": "Хозяйственная деятельность"},
                    "Theme": {"Name": "Транспорт"},
                }
            ]
        return data[entity_set]


def test_search_classifier_returns_codes_for_every_level():
    client = ClassifierClient()

    matches = CitizenRequestAnalyticsService(client, tz=STAND_TZ).search_classifier("дорог")

    assert [(m.level, m.code, m.name) for m in matches] == [
        ("section", "0003", "Экономика"),
        ("topic", "0003.0008", "Хозяйственная деятельность"),
        ("theme", "0003.0008.0086", "Транспорт"),
        ("question", "0003.0008.0086.0567", "Дороги"),
    ]
    assert matches[2].path == "Экономика > Хозяйственная деятельность"
    assert matches[3].path == "Экономика > Хозяйственная деятельность > Транспорт"
    assert {call[0] for call in client.calls} == {"ISections", "ITopics", "IThemes", "IQuestions"}
    topic_sample = next(kw for es, kw in client.calls if es == "IQuestions" and "Topic/Id" in kw["filter_"])
    assert topic_sample["filter_"] == "Topic/Id eq 1100"
