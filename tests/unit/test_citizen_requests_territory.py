from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.services.citizen_request_analytics import CitizenRequestAnalyticsService
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

STAND_TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)  # на стенде 25.11.2026 14:00
VLADIMIR = {"Name": "Владимирская область"}
UDM = {"Name": "Удмуртская Республика"}


def question(topic):
    return {"Question": {"FullCode": "0001.0001.0001.0001", "Topic": {"Name": topic}}}


def request(rid, area=None, city=None, region=None, deadline="2026-12-31", answered=False, questions=(), **extra):
    row = {"Id": rid, "Deadline": deadline + "T00:00:00+04:00", "ResponseDate": "2026-11-01T00:00:00+04:00" if answered else None,
           "ExecutionState": "Executed" if answered else None, "AnswerLetter": None, "TransferredTo": None,
           "MunicipalArea": {"Id": 10, "Name": area} if area else None, "City": city, "Region": region,
           "Questions": [question(t) for t in questions]}
    row.update(extra)
    return row


ROWS = [
    request(1, area="м.р-н. Муромский", region=VLADIMIR, deadline="2026-11-01", questions=["Семья", "Семья"]),  # просрочено
    request(2, area="м.р-н. Муромский", region=VLADIMIR, answered=True, deadline="2026-10-01", questions=["Семья"]),  # отвечено
    request(3, city={"Name": "г. Александров", "MunicipalAreaGD": {"Id": 11, "Name": "м.р-н. Александровский"}, "Region": VLADIMIR},
            questions=["Конституционный строй"]),  # район по городу
    request(4, city={"Name": "г. Ижевск", "MunicipalAreaGD": None, "Region": UDM}),  # только город
    request(5),  # территория не указана
    request(6, area="м.р-н. Муромский", region=VLADIMIR, deadline="2026-11-24", TransferredTo={"Id": 1}),  # перенаправлено — не просрочка
]


class FakeClient:
    def __init__(self, rows=ROWS):
        self.rows = rows
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        last_id = int(kwargs["filter_"].split("Id gt ")[1].split(" ")[0])
        return [row for row in self.rows if row["Id"] > last_id][: kwargs["top"]]


def service(rows=ROWS):
    client = FakeClient(rows)
    return CitizenRequestAnalyticsService(client, tz=STAND_TZ, now=lambda: NOW), client


def by_name(result):
    return {area["area"]: area for area in result["areas"]}


def test_groups_by_municipal_area_with_city_fallback():
    result, _ = service()[0].by_territory(), None

    areas = by_name(result)
    assert areas["м.р-н. Муромский"]["requests"] == 3
    assert areas["м.р-н. Муромский"]["source"] == "municipal_area"
    assert areas["м.р-н. Александровский"]["source"] == "city_area"
    assert areas["м.р-н. Александровский"]["region"] == "Владимирская область"
    assert areas["г. Ижевск"]["source"] == "city"
    assert result["requests_total"] == 6
    assert result["requests_without_territory"] == 1
    assert result["requests_with_territory"] == 5


def test_counts_questions_overdue_and_top_topics():
    areas = by_name(service()[0].by_territory())
    murom = areas["м.р-н. Муромский"]

    assert murom["questions"] == 3
    assert murom["overdue"] == 1  # отвеченное и перенаправленное не считаются
    assert murom["top_topics"] == [{"name": "Семья", "count": 3}]


def test_region_filter_keeps_only_matching_region():
    result = service()[0].by_territory(region="владимирская")

    assert set(by_name(result)) == {"м.р-н. Муромский", "м.р-н. Александровский"}
    assert result["requests_total"] == 6 and result["requests_in_region"] == 4


def test_sorted_by_requests_and_has_no_personal_fields():
    result = service()[0].by_territory()

    assert [a["requests"] for a in result["areas"]] == sorted((a["requests"] for a in result["areas"]), reverse=True)
    expand = service()[1]
    svc, client = service()
    svc.by_territory()
    _, kwargs = client.calls[0]
    for marker in ("Address", "Email", "Phone", "FullName", "Correspondent"):
        assert marker not in kwargs.get("select", "") and marker not in kwargs.get("expand", "")


def test_period_and_classifier_filter_go_to_odata():
    svc, client = service()

    svc.by_territory(date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), classifier_code="0003.0008")

    flt = client.calls[0][1]["filter_"]
    assert "RegistrationDate ge 2026-01-01T00:00:00+04:00" in flt
    assert "RegistrationDate lt 2026-07-01T00:00:00+04:00" in flt
    assert "startswith(q/Question/FullCode,'0003.0008')" in flt


def test_truncated_when_more_than_max_requests():
    rows = [request(i, area="м.р-н. Муромский") for i in range(1, 8)]
    svc = CitizenRequestAnalyticsService(FakeClient(rows), tz=STAND_TZ, max_requests=5, now=lambda: NOW)

    result = svc.by_territory()

    assert result["truncated"] is True and result["requests_total"] == 5


# --- MCP-тул --------------------------------------------------------------------


def tool_services(seen):
    def by_territory(**kwargs):
        seen.update(kwargs)
        return {"areas": [], "requests_total": 0}

    return SimpleNamespace(citizen_request_analytics=SimpleNamespace(by_territory=by_territory))


def test_tool_passes_parameters():
    seen = {}

    data = payload(call_tool(build_server(FakeProvider(tool_services(seen))), "get_citizen_requests_by_territory",
                             {"region": "Владимирская", "date_from": "2026-01-01", "classifier_code": "0003", "top_topics": 2}))

    assert seen == {"region": "Владимирская", "date_from": date(2026, 1, 1), "date_to": None,
                    "classifier_code": "0003", "top_topics": 2}
    assert data["requests_total"] == 0


def test_tool_rejects_bad_classifier_code():
    seen = {}

    text = error_text(call_tool(build_server(FakeProvider(tool_services(seen))), "get_citizen_requests_by_territory",
                                {"classifier_code": "дороги"}))

    assert "Код классификатора" in text and seen == {}
