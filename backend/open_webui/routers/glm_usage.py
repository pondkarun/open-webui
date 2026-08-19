"""GLM (z.ai) plan-usage widget backend.

Server-side proxy for https://api.z.ai/api/monitor/usage/quota/limit so the
browser never sees the GLM API key. Single-file, self-contained router.

Env:
    GLM_USAGE_API_KEY   — Bearer token for api.z.ai (required for the route to work)
    GLM_USAGE_URL       — override endpoint (default: the quota/limit URL)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.utils.auth import get_current_user

router = APIRouter()

log = logging.getLogger(__name__)

GLM_USAGE_URL = os.environ.get("GLM_USAGE_URL", "https://api.z.ai/api/monitor/usage/quota/limit")

# Simple in-process cache: (payload, fetched_at). TTL seconds.
_CACHE_TTL = 120
_cache: dict[str, Any] = {"payload": None, "fetched_at": 0.0}

# unit codes from z.ai: 3 => 5-hour window, 6 => monthly window
_WINDOW_LABELS = {3: "5h", 6: "monthly"}


class GlmUsage(BaseModel):
    plan: Optional[str] = None
    windows: list[dict[str, Any]] = []
    fetched_at: float


def _normalize(raw: dict[str, Any]) -> GlmUsage:
    data = raw.get("data") or {}
    windows = []
    for lim in data.get("limits") or []:
        unit = lim.get("unit")
        label = _WINDOW_LABELS.get(unit, f"unit{unit}")
        reset_ms = lim.get("nextResetTime")
        windows.append(
            {
                "label": label,  # "5h" | "monthly"
                "unit": unit,
                "percentage": lim.get("percentage"),
                "used": lim.get("currentValue"),
                "total": lim.get("usage"),
                "remaining": lim.get("remaining"),
                "resets_at": (reset_ms / 1000) if isinstance(reset_ms, (int, float)) else None,
            }
        )
    return GlmUsage(plan=data.get("level"), windows=windows, fetched_at=time.time())


def _api_key(request: Request) -> Optional[str]:
    # App-level config wins, process env as fallback.
    cfg = getattr(request.app.state.config, "GLM_USAGE_API_KEY", None) if hasattr(request.app.state, "config") else None
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip()
    return os.environ.get("GLM_USAGE_API_KEY")


@router.get("/usage", response_model=GlmUsage)
async def get_glm_usage(
    request: Request,
    refresh: bool = False,
    user=Depends(get_current_user),
):
    """Proxy the z.ai quota endpoint; 120s server-side cache, ?refresh=true bypass."""
    api_key = _api_key(request)
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="GLM usage widget not configured: set GLM_USAGE_API_KEY",
        )

    now = time.time()
    if not refresh and _cache["payload"] is not None and now - _cache["fetched_at"] < _CACHE_TTL:
        return _cache["payload"]

    timeout = aiohttp.ClientTimeout(total=15)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Language": "en-US,en",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                GLM_USAGE_URL,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200 or not body.get("success", True):
                    log.warning("glm usage upstream error status=%s body=%s", resp.status, str(body)[:300])
                    raise HTTPException(status_code=502, detail=f"z.ai returned {resp.status}")
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"z.ai unreachable: {e}")

    payload = _normalize(body)
    _cache["payload"] = payload
    _cache["fetched_at"] = now
    return payload


@router.get("/health", include_in_schema=False)
async def glm_usage_health():
    return {
        "ok": bool(os.environ.get("GLM_USAGE_API_KEY")),
        "cached": _cache["payload"] is not None,
    }
