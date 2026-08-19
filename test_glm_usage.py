"""Tests for the GLM usage widget backend router.

Run: cd backend && python -m pytest test_glm_usage.py -v
(or from repo root: python -m pytest backend/test_glm_usage.py)

No network, no real z.ai call: aiohttp session is monkeypatched.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret-key-not-for-production")

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import glm_usage


# ---------- helpers ----------

def make_app(monkeypatch, api_key: str = "test-key", upstream=None, upstream_status=200):
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")

    monkeypatch.setenv("GLM_USAGE_API_KEY", api_key)

    # auth: bypass login by resolving any user
    monkeypatch.setattr(
        "open_webui.routers.glm_usage.get_current_user",
        lambda: {"id": "u1", "email": "t@t"},
    )
    # the router imported get_current_user by name; patch the module's reference
    monkeypatch.setattr(glm_usage, "get_current_user", lambda: {"id": "u1"})

    if upstream is not None:
        # Replace the whole aiohttp call path with a stub
        async def fake_fetch(request, refresh):
            return upstream

        monkeypatch.setattr(glm_usage, "_fetch_upstream", fake_fetch, raising=False)
    return app


def patch_route_dep(app, monkeypatch):
    # FastAPI Depends(get_current_user) resolves at include time via the module attr.
    from fastapi import dependencies

    app.dependency_overrides[glm_usage.get_current_user] = lambda: {"id": "u1"}


def fake_response_json(body, status=200):
    class _Ctx:
        def __init__(self, body, status):
            self._body, self._status = body, status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return self._body

        @property
        def status(self):
            return self._status

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            return _Ctx(body, status)

    class _Timeout:
        def __init__(self, *a, **k):
            pass

    return _Session, _Timeout


SAMPLE_UPSTREAM = {
    "code": 200,
    "success": True,
    "data": {
        "level": "lite",
        "limits": [
            {
                "type": "CREDIT_LIMIT",
                "unit": 3,
                "number": 5,
                "usage": 2000,
                "currentValue": 49,
                "remaining": 1950,
                "percentage": 2,
                "nextResetTime": 1787169305587,
            },
            {
                "type": "CREDIT_LIMIT",
                "unit": 6,
                "number": 1,
                "usage": 10000,
                "currentValue": 49,
                "remaining": 9950,
                "percentage": 1,
                "nextResetTime": 1787755822998,
            },
        ],
    },
}


# ---------- tests ----------

def test_normalize_maps_windows():
    out = glm_usage._normalize(SAMPLE_UPSTREAM)
    assert out.plan == "lite"
    labels = [w["label"] for w in out.windows]
    assert labels == ["5h", "monthly"]
    w5 = out.windows[0]
    assert w5["percentage"] == 2
    assert w5["used"] == 49
    assert w5["total"] == 2000
    assert w5["resets_at"] == pytest.approx(1787169305.587)


def test_normalize_empty_payload():
    out = glm_usage._normalize({})
    assert out.plan is None
    assert out.windows == []


def test_normalize_unknown_unit():
    raw = {"data": {"level": "pro", "limits": [{"unit": 99, "percentage": 5}]}}
    out = glm_usage._normalize(raw)
    assert out.windows[0]["label"] == "unit99"


def test_route_requires_auth(monkeypatch):
    # no dependency override => anonymous call must 401/403 via real auth chain
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")
    monkeypatch.setenv("GLM_USAGE_API_KEY", "k")
    client = TestClient(app)
    r = client.get("/api/v1/glm/usage")
    assert r.status_code in (401, 403)


def test_route_503_without_key(monkeypatch):
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")
    monkeypatch.delenv("GLM_USAGE_API_KEY", raising=False)
    app.dependency_overrides[glm_usage.get_current_user] = lambda: {"id": "u1"}
    client = TestClient(app)
    r = client.get("/api/v1/glm/usage")
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_route_serves_cached_upstream(monkeypatch):
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")
    monkeypatch.setenv("GLM_USAGE_API_KEY", "k")
    app.dependency_overrides[glm_usage.get_current_user] = lambda: {"id": "u1"}

    sess_cls, timeout_cls = fake_response_json(SAMPLE_UPSTREAM)
    monkeypatch.setattr(glm_usage.aiohttp, "ClientSession", sess_cls)
    monkeypatch.setattr(glm_usage.aiohttp, "ClientTimeout", timeout_cls)

    glm_usage._cache.update({"payload": None, "fetched_at": 0.0})
    client = TestClient(app)
    r = client.get("/api/v1/glm/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "lite"
    assert [w["label"] for w in body["windows"]] == ["5h", "monthly"]

    # second call within TTL must hit the cache: same fetched_at
    r2 = client.get("/api/v1/glm/usage")
    assert r2.json()["fetched_at"] == body["fetched_at"]


def test_route_refresh_bypasses_cache(monkeypatch):
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")
    monkeypatch.setenv("GLM_USAGE_API_KEY", "k")
    app.dependency_overrides[glm_usage.get_current_user] = lambda: {"id": "u1"}

    calls = {"n": 0}

    sess_cls, timeout_cls = fake_response_json(SAMPLE_UPSTREAM)
    orig_get = sess_cls.get

    class CountingSession(sess_cls):
        def get(self, url, **k):
            calls["n"] += 1
            return orig_get(self, url, **k)

    monkeypatch.setattr(glm_usage.aiohttp, "ClientSession", CountingSession)
    monkeypatch.setattr(glm_usage.aiohttp, "ClientTimeout", timeout_cls)

    glm_usage._cache.update({"payload": None, "fetched_at": 0.0})
    client = TestClient(app)
    client.get("/api/v1/glm/usage")
    client.get("/api/v1/glm/usage")
    assert calls["n"] == 1  # second call cached
    client.get("/api/v1/glm/usage?refresh=true")
    assert calls["n"] == 2  # refresh forced upstream


def test_route_upstream_error(monkeypatch):
    app = FastAPI()
    app.include_router(glm_usage.router, prefix="/api/v1/glm")
    monkeypatch.setenv("GLM_USAGE_API_KEY", "k")
    app.dependency_overrides[glm_usage.get_current_user] = lambda: {"id": "u1"}

    sess_cls, timeout_cls = fake_response_json({"code": 401, "success": False}, status=401)
    monkeypatch.setattr(glm_usage.aiohttp, "ClientSession", sess_cls)
    monkeypatch.setattr(glm_usage.aiohttp, "ClientTimeout", timeout_cls)

    glm_usage._cache.update({"payload": None, "fetched_at": 0.0})
    client = TestClient(app)
    r = client.get("/api/v1/glm/usage")
    assert r.status_code == 502
    assert "401" in r.json()["detail"]
