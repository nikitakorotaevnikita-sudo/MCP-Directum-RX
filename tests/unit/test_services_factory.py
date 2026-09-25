from datetime import timedelta, timezone

import httpx

from src.services.factory import DirectumServices, build_directum_services


def test_build_directum_services_wires_one_shared_client():
    services = build_directum_services(
        "https://rx.example/Integration/odata",
        "Basic bG9naW46cGFzcw==",
        12.5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert isinstance(services, DirectumServices)
    assert services.client.base_url == "https://rx.example/Integration/odata"
    assert services.current_user.client is services.client
    assert services.assignments.client is services.client
    assert services.assignments.current_user_service is services.current_user
    assert services.action_items.client is services.client
    assert services.meetings.client is services.client
    assert services.discipline.client is services.client
    assert services.admin_access.client is services.client
    assert services.admin_access.current_user_service is services.current_user
    services.close()
    assert services.client.client.is_closed


def test_build_directum_services_passes_timezone():
    tz = timezone(timedelta(hours=4))

    services = build_directum_services(
        "https://rx.example/Integration/odata",
        "Basic bG9naW46cGFzcw==",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
        tz=tz,
    )

    assert services.assignments.tz is tz
    services.close()
