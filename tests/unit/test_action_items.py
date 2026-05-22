from src.models.schemas import ActionItemCreateRequest, ToolCallRecord


def test_action_item_create_defaults_to_preview_mode():
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
    )

    assert request.confirm is False
    assert request.deadline is None


def test_action_item_create_rejects_empty_subject():
    try:
        ActionItemCreateRequest(
            subject=" ",
            performer_id=42,
            action_text="Prepare a short response for the incoming letter",
        )
    except ValueError as exc:
        assert "subject" in str(exc)
    else:
        raise AssertionError("request with empty subject was accepted")


def test_action_item_create_rejects_zero_performer_id():
    try:
        ActionItemCreateRequest(
            subject="Prepare response",
            performer_id=0,
            action_text="Prepare a short response for the incoming letter",
        )
    except ValueError as exc:
        assert "performer_id" in str(exc)
    else:
        raise AssertionError("request with zero performer_id was accepted")


def test_action_item_create_rejects_empty_action_text():
    try:
        ActionItemCreateRequest(
            subject="Prepare response",
            performer_id=42,
            action_text=" ",
        )
    except ValueError as exc:
        assert "action_text" in str(exc)
    else:
        raise AssertionError("request with empty action_text was accepted")


def test_tool_call_record_accepts_any_list_result():
    record = ToolCallRecord(
        name="search",
        arguments={"query": "response"},
        result=["match", 1, {"id": 2}],
    )

    assert record.result == ["match", 1, {"id": 2}]
