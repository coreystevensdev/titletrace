import pytest
import httpx
from fastapi.testclient import TestClient


@pytest.fixture
def http_client():
    return httpx.AsyncClient()
