"""Shared HTTP client for the API layer.

Lives apart from main so routes can reach the client without importing the
module that imports routes.
"""

from __future__ import annotations

import httpx

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client


def open_http_client() -> None:
    global _http_client
    _http_client = httpx.AsyncClient()


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
