from datetime import date, datetime, timezone
from src.models.schemas import ActionItemDetail, MeetingSummary


def test_meeting_summary_fields():
    m = MeetingSummary(
        id=1,
        subject="Планёрка",
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        end_date=None,
        place="Каб. 305",
        agenda_summary="Итоги квартала",
        client_card_url="https://rx.example/Client/#/card/abc/1",
    )
    assert m.id == 1
    assert m.place == "Каб. 305"
    assert m.agenda_summary == "Итоги квартала"


def test_meeting_summary_optional_fields_default_none():
    m = MeetingSummary(
        id=2,
        subject="Без места",
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        client_card_url="",
    )
    assert m.end_date is None
    assert m.place is None
    assert m.agenda_summary is None


def test_action_item_detail_fields():
    d = ActionItemDetail(
        id=42,
        subject="Подготовить записку",
        text="Подготовить аналитическую записку",
        performer="Иванова М.П.",
        author="Петров А.С.",
        deadline=date(2026, 5, 30),
        status="InProcess",
        created_date=date(2026, 5, 20),
        client_card_url="https://rx.example/Client/#/card/abc/42",
        narrative="",
    )
    assert d.id == 42
    assert d.narrative == ""
