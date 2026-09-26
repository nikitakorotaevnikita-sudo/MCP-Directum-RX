from datetime import date, timedelta, timezone

import pytest

from src.models.schemas import CounterpartySummary, EmployeeSummary
from src.services.directum_client import DirectumError
from src.services.document_search import DocumentCriteria, DocumentSearchService, text_stems

STAND_TZ = timezone(timedelta(hours=4))


class FakeClient:
    def __init__(self, rows_by_set=None, fail_sets=(), rows_for=None):
        self.rows_by_set = rows_by_set or {}
        self.fail_sets = set(fail_sets)
        # rows_for(entity_set, filter_) -> rows: для сценариев ослабления условий.
        self.rows_for = rows_for
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        if entity_set in self.fail_sets:
            raise DirectumError("bad nav", 400)
        if self.rows_for is not None:
            return self.rows_for(entity_set, kwargs["filter_"])
        return self.rows_by_set.get(entity_set, [])

    def build_document_card_url(self, document_id):
        return f"https://rx.example/doc/{document_id}"


class FakeActionItems:
    def __init__(self, counterparties=(), employees=()):
        self.counterparties = list(counterparties)
        self.employees = list(employees)

    def search_counterparty(self, query, top=10):
        return self.counterparties[:top]

    def search_employee(self, query, top=10):
        return self.employees[:top]


def row(doc_id, name, subject=None, number=None, reg=None, created="2026-01-01T10:00:00+04:00", kind="Входящее письмо"):
    return {
        "Id": doc_id,
        "Name": name,
        "Subject": subject,
        "RegistrationNumber": number,
        "RegistrationDate": reg,
        "Created": created,
        "DocumentKind": {"Name": kind} if kind else None,
    }


def service(client, action_items=None):
    return DocumentSearchService(client, action_items or FakeActionItems(), tz=STAND_TZ)


def only_filter(client):
    # Первый запрос — с исходными условиями; дальше могут идти запросы с ослабленными.
    return client.calls[0][1]["filter_"]


def test_text_stems_drop_stopwords_short_tokens_and_endings():
    assert text_stems("Письмо про ремонт дорогам из Минфина, в августе") == ["ремонт", "дорог", "минфин", "август"]


def test_text_stems_deduplicate():
    assert text_stems("дороги дорогам дорог") == ["дорог"]


def test_empty_criteria_rejected():
    with pytest.raises(ValueError):
        service(FakeClient()).find(DocumentCriteria())


def test_text_builds_or_of_name_and_subject_with_both_cases():
    client = FakeClient()

    service(client).find(DocumentCriteria(text="ремонт"))

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IOfficialDocuments"
    assert kwargs["filter_"] == (
        "(contains(Name,'ремонт') or contains(Subject,'ремонт')"
        " or contains(Name,'Ремонт') or contains(Subject,'Ремонт'))"
    )
    assert kwargs["top"] == 50
    assert kwargs["orderby"] == "RegistrationDate desc"
    assert kwargs["expand"] == "DocumentKind($select=Name)"
    assert "Created" in kwargs["select"]


def test_text_escapes_quotes():
    client = FakeClient()

    service(client).find(DocumentCriteria(text="д'артаньян"))

    assert "contains(Name,'д''артаньян')" in only_filter(client)


def test_period_uses_registration_or_created_in_stand_timezone():
    client = FakeClient()

    service(client).find(DocumentCriteria(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31)))

    assert only_filter(client) == (
        "((RegistrationDate ge 2026-08-01T00:00:00+04:00 and RegistrationDate lt 2026-09-01T00:00:00+04:00)"
        " or (RegistrationDate eq null and Created ge 2026-08-01T00:00:00+04:00 and Created lt 2026-09-01T00:00:00+04:00))"
    )


def test_registration_number_contains():
    client = FakeClient()

    service(client).find(DocumentCriteria(registration_number="139"))

    assert only_filter(client) == "contains(RegistrationNumber,'139')"


@pytest.mark.parametrize(
    ("kind", "entity_set"),
    [
        ("incoming_letter", "IIncomingLetters"),
        ("outgoing_letter", "IOutgoingLetters"),
        ("order", "IOrderBases"),
        ("memo", "IMemos"),
        ("contract", "IContractualDocuments"),
        ("citizen_request", "IRequests"),
    ],
)
def test_kind_selects_entity_set(kind, entity_set):
    client = FakeClient()

    service(client).find(DocumentCriteria(kind=kind, text="ремонт"))

    assert [call[0] for call in client.calls] == [entity_set]


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        service(FakeClient()).find(DocumentCriteria(kind="poem", text="x"))


def test_counterparty_for_any_kind_queries_letters_and_contracts():
    client = FakeClient()
    parties = FakeActionItems(counterparties=[CounterpartySummary(id=7, name="Минфин"), CounterpartySummary(id=8, name="Минфин РТ")])

    service(client, parties).find(DocumentCriteria(counterparty="Минфин"))

    filters = {entity_set: kwargs["filter_"] for entity_set, kwargs in client.calls}
    assert filters == {
        "IIncomingLetters": "(Correspondent/Id eq 7 or Correspondent/Id eq 8)",
        "IOutgoingLetters": "(Correspondent/Id eq 7 or Correspondent/Id eq 8)",
        "IContractualDocuments": "(Counterparty/Id eq 7 or Counterparty/Id eq 8)",
    }


def test_counterparty_for_order_rejected():
    parties = FakeActionItems(counterparties=[CounterpartySummary(id=7, name="Минфин")])

    with pytest.raises(ValueError):
        service(FakeClient(), parties).find(DocumentCriteria(kind="order", counterparty="Минфин"))


def test_counterparty_not_found_returns_message_without_document_queries():
    client = FakeClient()

    result = service(client).find(DocumentCriteria(counterparty="Нигде"))

    assert result.items == []
    assert "не найден" in result.message
    assert client.calls == []


def test_employee_filter_by_prepared_signatory_or_assignee():
    client = FakeClient()
    people = FakeActionItems(employees=[EmployeeSummary(id=63, name="Концева Надежда Ивановна")])

    service(client, people).find(DocumentCriteria(employee="Концева"))

    assert only_filter(client) == "(PreparedBy/Id eq 63 or OurSignatory/Id eq 63 or Assignee/Id eq 63)"


def test_employee_not_found_returns_message():
    result = service(FakeClient()).find(DocumentCriteria(employee="Никто"))

    assert "не найден" in result.message


def test_conditions_are_combined_with_and():
    client = FakeClient()

    service(client).find(DocumentCriteria(text="ремонт", registration_number="12"))

    assert only_filter(client).endswith(") and contains(RegistrationNumber,'12')")


def test_ranking_by_matched_words_and_reasons():
    client = FakeClient(
        rows_by_set={
            "IOfficialDocuments": [
                row(1, "Письмо о ремонте", reg="2026-08-10T00:00:00+04:00"),
                row(2, "Письмо о ремонте дорог", subject="Ремонт дорог", reg="2026-08-01T00:00:00+04:00"),
                row(3, "Отчёт", reg="2026-08-20T00:00:00+04:00"),
            ]
        }
    )

    result = service(client).find(DocumentCriteria(text="ремонт дорог"), limit=5)

    assert [item.id for item in result.items] == [2, 1, 3]
    top = result.items[0]
    assert top.score == 20
    assert "слова: ремонт, дорог" in top.match_reasons
    assert top.url == "https://rx.example/doc/2"
    assert top.kind == "Входящее письмо"
    assert result.candidates_total == 3


def test_ties_broken_by_newer_date_and_limit_applied():
    client = FakeClient(
        rows_by_set={
            "IOfficialDocuments": [
                row(1, "Ремонт", reg="2026-01-01T00:00:00+04:00"),
                row(2, "Ремонт", reg=None, created="2026-03-01T09:00:00+04:00"),
                row(3, "Ремонт", reg="2026-02-01T00:00:00+04:00"),
            ]
        }
    )

    result = service(client).find(DocumentCriteria(text="ремонт"), limit=2)

    assert [item.id for item in result.items] == [2, 3]
    assert result.candidates_total == 3


def test_exact_number_and_date_bonus():
    client = FakeClient(
        rows_by_set={
            "IOfficialDocuments": [
                row(1, "Письмо", number="139-ВХ", reg="2026-09-22T00:00:00+04:00"),
                row(2, "Письмо", number="1139-ВХ", reg="2026-07-01T00:00:00+04:00"),
            ]
        }
    )

    result = service(client).find(
        DocumentCriteria(registration_number="139-вх", date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    )

    first, second = result.items
    assert first.id == 1
    assert first.score == 7
    assert "номер: 139-ВХ" in first.match_reasons
    assert "дата в периоде" in first.match_reasons
    assert "номер содержит «139-вх»" in second.match_reasons


def test_counterparty_and_kind_reasons():
    client = FakeClient(rows_by_set={"IIncomingLetters": [row(5, "Вх. письмо")]})
    parties = FakeActionItems(counterparties=[CounterpartySummary(id=7, name="Минфин")])

    result = service(client, parties).find(DocumentCriteria(kind="incoming_letter", counterparty="Минфин"))

    assert "контрагент: Минфин" in result.items[0].match_reasons
    assert "вид: входящее письмо" in result.items[0].match_reasons


def test_duplicates_across_sets_removed_and_failing_set_skipped():
    client = FakeClient(
        rows_by_set={"IIncomingLetters": [row(5, "Письмо")], "IContractualDocuments": [row(5, "Письмо")]},
        fail_sets={"IOutgoingLetters"},
    )
    parties = FakeActionItems(counterparties=[CounterpartySummary(id=7, name="Минфин")])

    result = service(client, parties).find(DocumentCriteria(counterparty="Минфин"))

    assert [item.id for item in result.items] == [5]


def test_single_failing_set_raises():
    with pytest.raises(DirectumError):
        service(FakeClient(fail_sets={"IOfficialDocuments"})).find(DocumentCriteria(text="ремонт"))


def test_relaxes_text_then_period():
    def rows_for(entity_set, filter_):
        if "contains(Name" in filter_:
            return []
        if "2026-07-02" in filter_:  # период расширен на 30 дней назад
            return [row(9, "Уведомление", reg="2026-07-20T00:00:00+04:00")]
        return []

    client = FakeClient(rows_for=rows_for)

    result = service(client).find(
        DocumentCriteria(text="ремонт дорог", date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    )

    assert [item.id for item in result.items] == [9]
    assert result.relaxed == ["без слов из названия: «ремонт дорог»", "период расширен на 30 дней"]
    assert "дата в периоде" not in result.items[0].match_reasons


def test_relaxes_period_completely_when_nothing_found():
    calls = []

    def rows_for(entity_set, filter_):
        calls.append(filter_)
        return [row(4, "Ремонт")] if "RegistrationDate ge" not in filter_ else []

    client = FakeClient(rows_for=rows_for)

    result = service(client).find(DocumentCriteria(text="ремонт", date_from=date(2026, 8, 1), date_to=date(2026, 8, 1)))

    assert [item.id for item in result.items] == [4]
    assert result.relaxed == ["период расширен на 30 дней", "без периода"]


def test_text_only_is_not_relaxed_away():
    client = FakeClient()

    result = service(client).find(DocumentCriteria(text="ремонт"))

    assert result.items == []
    assert result.relaxed == []
    assert len(client.calls) == 1
    assert "ничего не найдено" in result.message.lower()
