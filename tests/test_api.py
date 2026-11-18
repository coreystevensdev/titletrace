"""HTTP-layer tests.

These exist because the suite previously imported no part of titletrace.api,
so a circular import between main and routes left the app unstartable while
CI stayed green.
"""

import pytest
from fastapi.testclient import TestClient


def test_app_imports():
    from titletrace.api.main import app

    assert app.title == "TitleTrace"


def test_health_returns_ok():
    from titletrace.api.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_lifespan_opens_and_closes_the_http_client():
    from titletrace.api.http_client import get_http_client
    from titletrace.api.main import app

    with TestClient(app) as client:
        client.get("/health")
        assert get_http_client() is not None

    # Past the context manager the shutdown hook has run and the client is gone.
    with pytest.raises(RuntimeError):
        get_http_client()
