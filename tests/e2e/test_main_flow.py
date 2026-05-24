from playwright.sync_api import expect


def test_main_page_has_directum_panel_and_chat(page, live_server_url):
    page.goto(live_server_url)

    page.get_by_role("heading", name="Directum RX Assistant").wait_for()
    assert page.locator("[data-action='my']").is_visible()
    assert page.locator("#chat-input").is_visible()


def test_chat_sends_prior_messages_as_history(page, live_server_url):
    requests = []

    def handle_chat(route):
        post_data_json = route.request.post_data_json
        requests.append(post_data_json() if callable(post_data_json) else post_data_json)
        route.fulfill(status=200, body="ok", content_type="text/plain")

    page.route("**/api/chat", handle_chat)
    page.goto(live_server_url)

    page.locator("#chat-input").fill("first")
    page.locator("#chat-form").evaluate("form => form.requestSubmit()")
    page.locator(".message.assistant", has_text="ok").first.wait_for()
    page.locator("#chat-input").fill("second")
    page.locator("#chat-form").evaluate("form => form.requestSubmit()")

    expect(page.locator(".message.assistant", has_text="ok")).to_have_count(2)
    assert requests[0]["history"] == []
    assert requests[1]["history"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
    ]


def test_chat_action_item_preview_requires_confirm_button(page, live_server_url):
    confirm_requests = []
    marker = (
        '[[DIRECTUM_ACTION_ITEM_PREVIEW:{"type":"action_item",'
        '"payload":{"subject":"Docs","performer_id":42,"action_text":"Check docs",'
        '"deadline":"2026-05-26T23:59:00+00:00"},'
        '"display":{"performer_name":"Ardo"}}]]'
    )

    def handle_chat(route):
        route.fulfill(status=200, body=f"Preview is ready.\n{marker}", content_type="text/plain")

    def handle_confirm(route):
        post_data_json = route.request.post_data_json
        confirm_requests.append(post_data_json() if callable(post_data_json) else post_data_json)
        route.fulfill(
            status=200,
            json={
                "mode": "created",
                "success": True,
                "directum_id": 777,
                "url": "https://rx.example/action-item/777",
                "message": "Created",
            },
        )

    page.route("**/api/chat", handle_chat)
    page.route("**/api/directum/action-items", handle_confirm)
    page.goto(live_server_url)

    page.locator("#chat-input").fill("create it")
    page.locator("#chat-form").evaluate("form => form.requestSubmit()")

    expect(page.locator(".message.assistant", has_text="Preview is ready.")).to_be_visible()
    expect(page.locator(".message.assistant", has_text="DIRECTUM_ACTION_ITEM_PREVIEW")).to_have_count(0)
    page.locator("#messages").get_by_role("button", name="Создать поручение").click()

    expect(page.locator(".preview-status", has_text="777")).to_be_visible()
    expect(page.get_by_role("link", name="Открыть поручение")).to_have_attribute(
        "href",
        "https://rx.example/action-item/777",
    )
    assert confirm_requests == [
        {
            "subject": "Docs",
            "performer_id": 42,
            "action_text": "Check docs",
            "deadline": "2026-05-26T23:59:00+00:00",
            "confirm": True,
        }
    ]
