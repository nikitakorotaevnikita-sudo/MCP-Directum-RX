from types import SimpleNamespace

import anyio
from mcp import Client

from src.mcp_server.app import build_server
from src.mcp_server.odata_meta import MetadataCache, is_denied, parse_metadata
from src.mcp_server.resources import load_domain_guides
from tests.unit.mcp_fakes import METADATA_XML, FakeODataClient, FakeProvider, call_tool, error_text, payload


def write_skill(root, name, description, body="# Справочник\nНаборы: IRequests"):
    folder = root / f"rxapi-{name}"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: rxapi-{name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


def make_server(tmp_path, client=None):
    services = SimpleNamespace(client=client or FakeODataClient())
    return build_server(FakeProvider(services), skills_dir=tmp_path), services.client


def test_parse_metadata_resolves_inheritance_and_navigation():
    entities = parse_metadata(METADATA_XML)

    request = entities["IRequests"]
    assert set(request.properties) == {"Id", "Subject", "RegistrationDate"}
    assert set(request.navigation) == {"Author"}


def test_deny_list_blocks_sensitive_sets():
    assert is_denied("ILogins")
    assert is_denied("IUsers")
    assert not is_denied("IRequests")


def test_metadata_cache_fetches_once():
    client = FakeODataClient()
    cache = MetadataCache()

    cache.get(client)
    cache.get(client)

    assert client.metadata_calls == 1


def test_load_domain_guides_reads_frontmatter(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные: отпуска, командировки")

    guides = load_domain_guides(tmp_path)

    assert guides["hr"].description == "HR-данные: отпуска, командировки"
    assert "IRequests" in guides["hr"].body


def test_domain_guides_exposed_as_resources(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные")
    server, _ = make_server(tmp_path)

    async def main():
        async with Client(server) as client:
            uris = [str(resource.uri) for resource in (await client.list_resources()).resources]
            content = await client.read_resource("drx://domains/hr")
            return uris, content.contents[0].text

    uris, text = anyio.run(main)

    assert "drx://domains/hr" in uris
    assert "Справочник" in text


def test_odata_list_domains(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные")
    server, _ = make_server(tmp_path)

    data = payload(call_tool(server, "odata_list_domains"))

    assert data["domains"] == [{"name": "hr", "description": "HR-данные", "resource": "drx://domains/hr"}]


def test_odata_describe_entity(tmp_path):
    server, _ = make_server(tmp_path)

    data = payload(call_tool(server, "odata_describe_entity", {"entity_set": "IRequests"}))

    assert {"name": "Subject", "type": "Edm.String"} in data["properties"]
    assert data["navigation"] == [{"name": "Author", "type": "Demo.IEmployeeDto"}]


def test_odata_query_requires_filter(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "  "}))

    assert "Нужен фильтр" in text


def test_odata_query_blocks_denied_and_unknown_sets(tmp_path):
    server, _ = make_server(tmp_path)

    assert "недоступен" in error_text(call_tool(server, "odata_query", {"entity_set": "ILogins", "filter": "Id gt 0"}))
    assert "недоступен" in error_text(call_tool(server, "odata_query", {"entity_set": "INope", "filter": "Id gt 0"}))


def test_odata_query_rejects_parameter_smuggling(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0&$top=999"}))

    assert "&" in text


def test_odata_query_validates_fields(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(
        call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0", "select": "Subject,Nope"})
    )
    assert "Nope" in text and "Subject" in text

    text = error_text(
        call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0", "expand": "Author($select=Name)"})
    )
    assert "expand" in text


def test_odata_query_passes_validated_params(tmp_path):
    server, client = make_server(tmp_path)

    data = payload(
        call_tool(
            server,
            "odata_query",
            {
                "entity_set": "IRequests",
                "filter": "RegistrationDate ge 2026-09-01T00:00:00+04:00",
                "select": "Id, Subject",
                "expand": "Author",
                "orderby": "RegistrationDate desc",
                "top": 2,
            },
        )
    )

    assert client.queries[-1] == {
        "entity_set": "IRequests",
        "filter_": "RegistrationDate ge 2026-09-01T00:00:00+04:00",
        "select": "Id,Subject",
        "expand": "Author",
        "orderby": "RegistrationDate desc",
        "top": 3,
    }
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_odata_count_and_get(tmp_path):
    server, client = make_server(tmp_path)

    count = payload(call_tool(server, "odata_count", {"entity_set": "IRequests", "filter": "Id gt 0"}))
    record = payload(call_tool(server, "odata_get", {"entity_set": "IRequests", "record_id": 5, "expand": "Author"}))

    assert count == {"entity_set": "IRequests", "filter": "Id gt 0", "count": 1761}
    assert record["Id"] == 5
    assert client.paths[-1] == "IRequests(5)?$expand=Author"
