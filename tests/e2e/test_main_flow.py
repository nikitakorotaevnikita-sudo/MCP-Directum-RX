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
