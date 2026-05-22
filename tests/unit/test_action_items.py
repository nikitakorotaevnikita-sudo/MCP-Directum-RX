from src.models.schemas import ActionItemCreateRequest


def test_action_item_create_defaults_to_preview_mode():
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
    )

    assert request.confirm is False
    assert request.deadline is None


def test_action_item_create_requires_core_fields():
    try:
        ActionItemCreateRequest(subject="", performer_id=0, action_text="")
    except ValueError as exc:
        text = str(exc)
        assert "subject" in text or "performer_id" in text or "action_text" in text
    else:
        raise AssertionError("invalid request was accepted")
