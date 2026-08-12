"""Tests for GET /health.

Covered twice on purpose: once against the FastAPI app directly, and once through the
Vercel entrypoint. The second is not redundant — the entrypoint strips the rewrite
prefix, and if that strip breaks, every route 404s in production while the direct test
still passes.
"""

import json

from fastapi.testclient import TestClient

from api.index import app as vercel_app
from src.api.main import app

client = TestClient(app)


def test_health_returns_200_and_status_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "v": 2}


def test_health_through_vercel_entrypoint() -> None:
    """A rewritten path reaches /health once the prefix is stripped."""
    with TestClient(vercel_app) as vercel_client:
        response = vercel_client.get("/api/index/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "v": 2}


def test_prefix_matches_vercel_config() -> None:
    """`_PREFIX` must track vercel.json's rewrite destination, or routing breaks."""
    from api.index import _PREFIX

    with open("vercel.json", encoding="utf-8") as handle:
        destination = json.load(handle)["rewrites"][0]["destination"]
    assert destination == f"{_PREFIX}/$1"
