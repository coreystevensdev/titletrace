"""FastAPI application entry point for TitleTrace."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from titletrace.api.routes import router

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient()
    yield
    await _http_client.aclose()
    _http_client = None


app = FastAPI(
    title="TitleTrace",
    description="Property records intelligence agent for PA and NJ addresses.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
