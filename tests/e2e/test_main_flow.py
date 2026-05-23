def test_main_page_has_directum_panel_and_chat(page, live_server_url):
    page.goto(live_server_url)

    page.get_by_role("heading", name="Directum RX Assistant").wait_for()
    assert page.get_by_text("Мои задания").is_visible()
    assert page.get_by_label("Спросите про поручения").is_visible()
