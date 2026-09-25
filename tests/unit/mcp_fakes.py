"""Тестовые дублёры mcpOGV: без HTTP и без Directum."""

import json
from contextlib import contextmanager

import anyio
from mcp import Client

from src.mcp_server.context import Credentials

FAKE_CREDENTIALS = Credentials(auth_token="Basic fake", fingerprint="f" * 64)


class FakeProvider:
    def __init__(self, services):
        self.services = services
        self.opened_with = []

    @contextmanager
    def open(self, headers):
        self.opened_with.append(headers)
        yield FAKE_CREDENTIALS, self.services


def run_async(func, *args):
    return anyio.run(func, *args)


def call_tool(server, name, arguments=None):
    async def main():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(main)


def list_tools(server):
    async def main():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(main)


def payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def error_text(result):
    assert result.is_error, "expected a tool error"
    return result.content[0].text


METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="Demo" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="IEntityBase"><Key><PropertyRef Name="Id"/></Key><Property Name="Id" Type="Edm.Int64"/></EntityType>
      <EntityType Name="IRequestDto" BaseType="Demo.IEntityBase">
        <Property Name="Subject" Type="Edm.String"/>
        <Property Name="RegistrationDate" Type="Edm.DateTimeOffset"/>
        <NavigationProperty Name="Author" Type="Demo.IEmployeeDto"/>
        <NavigationProperty Name="Performer" Type="Demo.IUserDto"/>
      </EntityType>
      <EntityType Name="IEmployeeDto" BaseType="Demo.IEntityBase">
        <Property Name="Name" Type="Edm.String"/>
        <NavigationProperty Name="Login" Type="Demo.ILoginDto"/>
      </EntityType>
      <EntityType Name="ILoginDto" BaseType="Demo.IEntityBase"><Property Name="LoginName" Type="Edm.String"/></EntityType>
      <EntityType Name="IUserDto" BaseType="Demo.IEntityBase"><Property Name="Name" Type="Edm.String"/></EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="IRequests" EntityType="Demo.IRequestDto"/>
        <EntitySet Name="IEmployees" EntityType="Demo.IEmployeeDto"/>
        <EntitySet Name="ILogins" EntityType="Demo.ILoginDto"/>
        <EntitySet Name="IUsers" EntityType="Demo.IUserDto"/>
        <EntitySet Name="ICitizenRequestSettings" EntityType="Demo.IEmployeeDto"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


class FakeODataClient:
    def __init__(self):
        self.metadata_calls = 0
        self.queries = []
        self.counts = []
        self.paths = []

    def get_metadata_xml(self):
        self.metadata_calls += 1
        return METADATA_XML

    def query(self, entity_set, *, filter_=None, select=None, expand=None, orderby=None, top=None, count=False):
        self.queries.append(
            {"entity_set": entity_set, "filter_": filter_, "select": select, "expand": expand, "orderby": orderby, "top": top}
        )
        return [{"Id": i, "Subject": f"Обращение {i}"} for i in range(top or 1)]

    def count(self, entity_set, filter_=None):
        self.counts.append((entity_set, filter_))
        return 1761

    def get_one(self, entity_path):
        self.paths.append(entity_path)
        return {"Id": 5, "Subject": "Обращение 5"}
