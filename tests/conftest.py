import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def two_lamps(client):
    client.post("/lamps", json={
        "name": "Spotlight A", "channel": "ch-1",
        "min_brightness": 0, "max_brightness": 100, "default_brightness": 10,
    })
    client.post("/lamps", json={
        "name": "Wall Washer B", "channel": "ch-2",
        "min_brightness": 20, "max_brightness": 80, "default_brightness": 20,
    })
