def test_backoffice_has_metrics_sections(page, live_server_url):
    page.goto(f"{live_server_url}/backoffice")

    page.get_by_role("heading", name="Backoffice").wait_for()
    assert page.get_by_text("Chat requests").is_visible()
    assert page.get_by_text("Tool calls").is_visible()


def test_backoffice_has_directum_connection_settings(page, live_server_url):
    page.goto(f"{live_server_url}/backoffice")

    page.get_by_role("heading", name="Directum RX connection").wait_for()
    assert page.get_by_label("Server URL").is_visible()
    assert page.get_by_label("Login").is_visible()
    assert page.get_by_label("Password").is_visible()
    assert page.get_by_role("button", name="Проверить").is_visible()
    assert page.get_by_role("button", name="Применить").is_visible()
