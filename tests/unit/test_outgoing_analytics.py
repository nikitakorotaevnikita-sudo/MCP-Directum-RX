from datetime import datetime, timedelta, timezone

from src.services.outgoing_analytics import categorize_outgoing, parse_deadline_value

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def test_categorize_outgoing_by_deadline():
    items = [
        {"id": 1, "deadline": (NOW + timedelta(days=3)).isoformat()},
        {"id": 2, "deadline": (NOW + timedelta(hours=12)).isoformat()},
        {"id": 3, "deadline": (NOW - timedelta(hours=1)).isoformat()},
        {"id": 4, "deadline": None},
    ]

    groups = categorize_outgoing(items, now=NOW)

    assert [item["id"] for item in groups["work"]] == [1, 4]
    assert [item["id"] for item in groups["due_soon"]] == [2]
    assert [item["id"] for item in groups["overdue"]] == [3]


def test_parse_deadline_value_handles_z_and_naive_strings():
    assert parse_deadline_value("2026-09-25T10:00:00Z") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert parse_deadline_value("2026-09-25T10:00:00") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)


def test_parse_deadline_value_uses_fallback_for_non_iso_text():
    assert parse_deadline_value("завтра", fallback=lambda text: NOW) == NOW
    assert parse_deadline_value("завтра") is None


def test_parse_deadline_value_accepts_datetime_and_empty():
    assert parse_deadline_value(datetime(2026, 9, 25, 10)) == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert parse_deadline_value(None) is None
    assert parse_deadline_value("   ") is None
