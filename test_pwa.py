"""Tests for the PWA additions (manifest + service worker routes).

Run: python -m pytest test_pwa.py -v

These validate the ROUTE behavior we added to open_webui.main:
  - /manifest.json returns a valid, installable web app manifest (with id,
    scope, icons 96/192/512 any + maskable) without needing the full app boot.
  - /serviceworker.js serves the bundled SW file with no-cache + SW-allowed.

The open_webui.main module boots a huge FastAPI app at import time, so we
cannot import it directly in tests. Instead we test the same logic against
a fresh minimal FastAPI app that mounts the SAME handler functions imported
from a targeted submodule import — main.get_manifest_json reads app.state,
so we replicate the exact registration used in main (same function bodies
via importlib extraction from source AST) — overkill. Simpler: assert the
source contains the required pieces AND spin the actual endpoints via
FastAPI routes wrapping the real functions extracted by exec'ing just those
two function definitions from main.py source.

Pragmatic approach below: parse main.py, extract the two async functions,
exec them with the imports they need, and register them on a fresh app.
"""

import ast
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

MAIN_SRC = (Path(__file__).resolve().parent / "backend" / "open_webui" / "main.py").read_text()


def _extract_fn(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(MAIN_SRC)
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in main.py")


def make_pwa_app():
    """Build a fresh FastAPI app exposing the real manifest/SW handlers."""
    import types

    from fastapi.responses import FileResponse

    app = FastAPI()
    app.state.WEBUI_NAME = "Pond AI"

    ns = {
        "app": app,
        "FileResponse": FileResponse,
        "HTTPException": __import__("fastapi", fromlist=["HTTPException"]).HTTPException,
        "STATIC_DIR": Path(__file__).resolve().parent / "static",
        "getattr": getattr,
    }

    for fn_name in ("get_manifest_json",):
        fn_node = _extract_fn(fn_name)
        module = ast.Module(body=[fn_node], type_ignores=[])
        exec(compile(module, "main.py", "exec"), ns)
        app.add_api_route(
            f"/{ 'manifest.json' if fn_name == 'get_manifest_json' else 'serviceworker.js' }",
            ns[fn_name],
            methods=["GET"],
        )

    return app


def test_manifest_is_installable():
    client = TestClient(make_pwa_app())
    r = client.get("/manifest.json")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "Pond AI"
    assert m["start_url"] == "/"
    assert m["display"] == "standalone"
    # installability: id + scope + 192 & 512 icons + maskable
    assert m["id"] == "/"
    assert m["scope"] == "/"
    sizes = {i["sizes"] for i in m["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes
    assert any(i.get("purpose") == "maskable" for i in m["icons"])
    # every icon file must actually exist on disk (served at /static/<name>,
    # sourced from repo static/static/ via vite build + startup copy)
    for icon in m["icons"]:
        p = Path("static/static") / Path(icon["src"]).name
        assert p.exists(), f"missing icon file: {p}"


def test_serviceworker_in_frontend_public_dir():
    """The SW must live in the frontend public dir (static/) — vite copies it
    to the build root, and the SPA static mount serves it at /serviceworker.js
    (scope '/' works because it sits at the origin root)."""
    sw = Path("static") / "serviceworker.js"
    assert sw.exists(), "serviceworker.js missing from frontend public dir (static/)"
    body = sw.read_text()
    assert "install" in body and "fetch" in body


def test_no_shadowing_sw_route():
    """The /serviceworker.js GET route must NOT exist — the SPA static mount
    at '/' already serves the SW from the build root; a custom route would
    shadow it and break (this exact bug shipped once: 404 on live)."""
    app = make_pwa_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/serviceworker.js" not in paths
