"""Tests for GET /health."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_returns_200_and_status_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
