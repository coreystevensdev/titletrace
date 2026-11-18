"""FastAPI application entry point for TitleTrace."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from titletrace.api.http_client import close_http_client, open_http_client
from titletrace.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_http_client()
    yield
    await close_http_client()


app = FastAPI(
    title="TitleTrace",
    description="Property records intelligence agent for PA and NJ addresses.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
