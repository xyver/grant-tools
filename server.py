"""Run the grant tools as a local MCP server (Streamable HTTP, JSON responses).

    python server.py

Settings come from the environment or `.env` (see `.env.example`). The server
listens on 127.0.0.1:7020 by default. If GRANT_MCP_ACCESS_TOKENS is set, every
request needs `Authorization: Bearer <token>`; leave it empty for local use.
"""
from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import runtime_paths  # loads .env before anything reads settings

os.environ.setdefault("GRANT_MCP_DATA_MODE", "local")

import data_bundle  # noqa: E402
import private_mcp_bundle as grants  # noqa: E402

app = FastAPI(title="Grant Tools MCP", docs_url=None, redoc_url=None)


def _unauthorized(request: Request) -> JSONResponse | None:
    if not grants._grant_mcp_tokens() or grants._grant_mcp_authorized(request):
        return None
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": grants.UNAUTHORIZED_MESSAGE}},
        status_code=401,
    )


@app.get("/health")
def health() -> dict:
    countries = runtime_paths.DATA_ROOT / "countries"
    bundles = {code: data_bundle.bundle_status(code) for code in data_bundle.COUNTRY_DIRS}
    return {
        "ok": countries.is_dir() and all(b["compatible"] is not False for b in bundles.values()),
        "data_root": str(runtime_paths.DATA_ROOT),
        "state_dir": str(runtime_paths.STATE_DIR),
        "geometry_mcp_url": runtime_paths.GEOMETRY_MCP_URL,
        "bundle_schema_supported": [data_bundle.MIN_SUPPORTED_SCHEMA, data_bundle.BUNDLE_SCHEMA_VERSION],
        "bundles": bundles,
    }


@app.get("/mcp")
async def info(request: Request) -> JSONResponse:
    return _unauthorized(request) or JSONResponse(grants.info_payload())


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    denied = _unauthorized(request)
    if denied:
        return denied
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}, status_code=400)
    return grants.handle_jsonrpc_dict(body)


def main() -> None:
    if not (runtime_paths.DATA_ROOT / "countries").is_dir():
        print(f"Warning: no countries/ folder under GRANT_DATA_ROOT ({runtime_paths.DATA_ROOT}). Analyses will lack data.")
    for code in data_bundle.COUNTRY_DIRS:
        status = data_bundle.bundle_status(code)
        if status["installed"] and status["note"]:
            print(f"Note: {status['note']}")
    host = os.environ.get("GRANT_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("GRANT_MCP_PORT", "7020"))
    print(f"Grant tools MCP: http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
