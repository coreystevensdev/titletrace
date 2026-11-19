"""FastAPI application entry point for TitleTrace."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

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


_PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"


@app.get("/", include_in_schema=False)
async def demo_page():
    """The browsable demo. Served from the app rather than a CDN so the page and
    the route it calls ship together and cannot drift apart between deploys."""
    return FileResponse(_PAGE, media_type="text/html")
