from typing import Any
from types import TracebackType
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

    def __enter__(self) -> "DirectumClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

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
        return data["value"]

    def get_collection(self, entity_set: str, **kwargs: Any) -> dict[str, Any]:
        url = self.build_url(entity_set)
        params = self._params(**kwargs)
        if params:
            url = f"{url}?{urlencode(params, safe='$')}"
        response = self.client.get(
            url,
            headers=self._headers(),
        )
        data = self._json_or_error(response)
        return self._collection_or_error(data, response.status_code)

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
                safe_message=f"Directum OData request failed with status {response.status_code}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DirectumError("Directum returned a non-JSON response", response.status_code) from exc

    def _collection_or_error(self, data: Any, status_code: int) -> dict[str, Any]:
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise DirectumError("Directum returned an unexpected collection response", status_code)
        return data
