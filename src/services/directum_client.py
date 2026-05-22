from typing import Any
from urllib.parse import urlencode

import httpx


class DirectumError(RuntimeError):
    def __init__(self, safe_message: str, status_code: int | None = None):
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.status_code = status_code


class DirectumClient:
    def __init__(
        self,
        base_url: str,
        auth_token: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def build_url(self, entity_set: str) -> str:
        return f"{self.base_url}/{entity_set.lstrip('/')}"

    def query(
        self,
        entity_set: str,
        *,
        filter_: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        count: bool = False,
    ) -> list[dict[str, Any]]:
        data = self.get_collection(
            entity_set,
            filter_=filter_,
            select=select,
            expand=expand,
            orderby=orderby,
            top=top,
            count=count,
        )
        return data.get("value", [])

    def get_collection(self, entity_set: str, **kwargs: Any) -> dict[str, Any]:
        url = self.build_url(entity_set)
        params = self._params(**kwargs)
        if params:
            url = f"{url}?{urlencode(params, safe='$')}"
        response = self.client.get(
            url,
            headers=self._headers(),
        )
        return self._json_or_error(response)

    def get_one(self, entity_path: str) -> dict[str, Any]:
        response = self.client.get(self.build_url(entity_path), headers=self._headers())
        return self._json_or_error(response)

    def post(self, entity_set: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(self.build_url(entity_set), headers=self._headers(), json=payload)
        return self._json_or_error(response)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.auth_token, "Accept": "application/json"}

    def _params(
        self,
        *,
        filter_: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        count: bool = False,
    ) -> dict[str, str | int | bool]:
        params: dict[str, str | int | bool] = {}
        if filter_:
            params["$filter"] = filter_
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = top
        if count:
            params["$count"] = "true"
        return params

    def _json_or_error(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise DirectumError(
                safe_message=f"Directum OData request failed: {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DirectumError("Directum returned a non-JSON response", response.status_code) from exc
