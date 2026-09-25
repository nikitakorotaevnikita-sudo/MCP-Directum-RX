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
