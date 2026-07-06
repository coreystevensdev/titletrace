"""Shared HTTP client base with retry/timeout for all TitleTrace data sources.

All external API calls use a 30-second timeout and exponential backoff on
HTTP 429 and 503 only. Non-transient errors (4xx except 429, 5xx except 503)
are not retried -- they indicate a data or auth problem, not a transient one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 503}
_MAX_RETRIES = 3
_TIMEOUT = httpx.Timeout(30.0)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """GET a JSON endpoint with retry/backoff. Raises httpx.HTTPStatusError on
    final failure after exhausting retries."""
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                logger.warning(
                    "retryable_status url=%s status=%d attempt=%d delay=%.1f",
                    url,
                    resp.status_code,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.warning("timeout url=%s attempt=%d delay=%.1f", url, attempt, delay)
                await asyncio.sleep(delay)
                delay *= 2
            continue
    raise last_exc or httpx.RequestError(f"exhausted retries for {url}")
