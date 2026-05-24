from typing import Any
from types import TracebackType
import re
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
        if response.status_code == 204:
            return {"value": []}
        data = self._json_or_error(response)
        return self._collection_or_error(data, response.status_code)

    def get_one(self, entity_path: str) -> dict[str, Any]:
        response = self.client.get(self.build_url(entity_path), headers=self._headers())
        return self._json_or_error(response)

    def post(self, entity_set: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.build_url(entity_set)
        response = self.client.post(url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            error_data = self._safe_error_detail(response)
            raise DirectumError(
                safe_message=(
                    f"POST {entity_set} failed with {response.status_code}. "
                    f"Payload had {len(payload)} fields: {list(payload.keys())}. "
                    f"Response detail: {error_data}"
                ),
                status_code=response.status_code,
            )
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
            detail = self._safe_error_detail(response) if response.status_code == 400 else ""
            suffix = f": {detail}" if detail else ""
            raise DirectumError(
                safe_message=f"Directum OData request failed with status {response.status_code}{suffix}",
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

    def _safe_error_detail(self, response: httpx.Response) -> str:
        detail = ""
        try:
            data = response.json()
        except ValueError:
            detail = response.text
        else:
            detail = self._odata_error_message(data)
        detail = self._sanitize_error_detail(detail)
        return detail[:500]

    def _odata_error_message(self, data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, dict):
                value = message.get("value")
                return value if isinstance(value, str) else ""
            if isinstance(message, str):
                return message
        message = data.get("message")
        return message if isinstance(message, str) else ""

    def _sanitize_error_detail(self, detail: str) -> str:
        detail = re.sub(r"Basic\s+[A-Za-z0-9+/=_-]{4,}", "Basic [redacted]", detail)
        detail = re.sub(r"sk-or-v1-[A-Za-z0-9]+", "[redacted]", detail)
        detail = re.sub(r"\s+", " ", detail).strip()
        return detail
