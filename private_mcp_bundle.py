from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


BUNDLE_DIR = Path(__file__).resolve().parent
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))

import grant_mcp_module  # noqa: E402
import cloud_data  # noqa: E402
import runtime_paths  # noqa: E402
import us_backend  # noqa: E402


def jsonrpc_result_envelope(result: Any, request_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error_envelope(request_id: Any, code: int, message: str, *, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _install_host_query_runner() -> None:
    """Share the hosted app's bounded DuckDB pool instead of opening extra
    connections inside its memory budget. Only when both sides read object
    storage: that pool has httpfs configured only in DaedalMap cloud mode, and
    the grant tools build their own s3:// URIs. Otherwise the grant tools keep
    their built-in runner."""
    if cloud_data.data_mode() != "object_store":
        return
    try:
        from mapmover.duckdb_helpers import is_cloud_mode, run_df as host_run_df
    except ModuleNotFoundError:
        return
    if is_cloud_mode():
        cloud_data.set_query_runner(lambda sql, params: host_run_df(sql, params))


_install_host_query_runner()


def prewarm() -> None:
    """Startup hook invoked by the private site server: warm query-independent
    grant state so the first request after a (re)start is fast. Safe to run in
    a background thread; the caller treats failures as non-fatal."""
    us_backend.prewarm()


PROVIDER_SLUG = "grants"
MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {
    "name": "grant-tools",
    "title": "Grant Tools MCP",
    "version": "0.1.0",
}
UNAUTHORIZED_MESSAGE = (
    "Access token required. Please provide token."
)
INSTRUCTIONS = (
    "Phase-aware grant MCP. This server supports only pre-submission "
    "grant workflow phases: prospect research, proposal development, and "
    "submission support. Start with grant_intake_or_update_project using "
    "project_input plus optional project_label, country_code, phase_hint, and "
    "profile_overrides. Users do not need to know loc_ids: send ordinary place "
    "names in profile_overrides.recipient_location_text and "
    "profile_overrides.project_area_text when that is what you have; the tool "
    "normalizes them to recipient_loc_id and place_of_performance_loc_id when "
    "possible. Then call grant_analyze_current_phase using the "
    "returned session_id. Do not send legacy intake keys like "
    "organization_name, project_title, project_summary, phase, or geography. "
    "Do not treat this server as a post-award compliance tool."
)
QUICKSTART_EXAMPLE = {
    "first_call": {
        "method": "tools/call",
        "params": {
            "name": "grant_intake_or_update_project",
            "arguments": {
                "project_input": (
                    "We are a nonprofit 501c3 working on wildfire debris removal, "
                    "wood products reuse, and disaster recovery in Appalachia."
                ),
                "profile_overrides": {
                    "recipient_location_text": "Buncombe County, North Carolina",
                    "project_area_text": "Buncombe County, North Carolina",
                    "has_government_partner": True,
                },
            },
        },
    },
    "second_call": {
        "method": "tools/call",
        "params": {
            "name": "grant_analyze_current_phase",
            "arguments": {
                "session_id": "SESSION_ID_FROM_FIRST_CALL",
            },
        },
    },
}
RESOURCE_QUICKSTART_URI = "grant-tools://quickstart"
RESOURCE_METHODS_URI = "grant-tools://methodology"


def _grant_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "grant_intake_or_update_project",
            "title": "Grant Intake Or Update Project",
            "description": "Create or update the normalized project profile and infer the current pre-submission grant phase.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_input": {"type": "string", "description": "User project description or new project facts."},
                    "session_id": {"type": "string", "description": "Optional existing grant MCP session id."},
                    "project_label": {"type": "string", "description": "Optional project label for the session."},
                    "country_code": {"type": "string", "description": "Optional explicit country code such as US or CAN."},
                    "phase_hint": {
                        "type": "string",
                        "enum": [
                            "phase_1_prospect_research",
                            "phase_2_proposal_development",
                            "phase_3_submission_support",
                        ],
                    },
                    "profile_overrides": {"type": "object", "description": "Optional structured profile fields already known."},
                    "allow_inference": {"type": "boolean", "description": "Allow inference of non-critical fields from text. Default true."},
                },
                "required": ["project_input"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "grant_analyze_current_phase",
            "title": "Grant Analyze Current Phase",
            "description": "Run the correct phase-specific grant workflow for the stored session state.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Existing grant MCP session id."},
                    "analysis_request": {"type": "string", "description": "Optional immediate follow-up request for this grant phase."},
                    "phase_override": {
                        "type": "string",
                        "enum": [
                            "phase_1_prospect_research",
                            "phase_2_proposal_development",
                            "phase_3_submission_support",
                        ],
                    },
                    "depth": {"type": "string", "enum": ["light", "standard", "full"]},
                    "refresh_profile_from_text": {"type": "boolean", "description": "Whether to merge the new request text into session profile state. Default true."},
                    "top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                        "description": "Maximum phase-one shortlist size.",
                    },
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "grant_set_phase",
            "title": "Grant Set Phase",
            "description": "Explicitly override the current pre-submission grant phase for an existing session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "phase_id": {
                        "type": "string",
                        "enum": [
                            "phase_1_prospect_research",
                            "phase_2_proposal_development",
                            "phase_3_submission_support",
                        ],
                    },
                },
                "required": ["session_id", "phase_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "grant_select_target_program",
            "title": "Grant Select Target Program",
            "description": "Set the active target program from the current shortlist for an existing session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "program_id": {"type": "string"},
                },
                "required": ["session_id", "program_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False},
        },
    ]


def _grant_prompt_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "grant_get_started",
            "title": "Get Started With Grant Matching",
            "description": "Starter intake prompt for a new grant project.",
            "arguments": [],
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Start by calling grant_intake_or_update_project. "
                            "Put the user's project description into project_input. "
                            "If the user already knows structured facts, include them in "
                            "profile_overrides, especially org_type, recipient_loc_id, "
                            "place_of_performance_loc_id, has_government_partner, "
                            "has_match_funding, target_award_amount, and "
                            "target_project_length_days. After intake returns a session_id, "
                            "call grant_analyze_current_phase."
                        ),
                    },
                }
            ],
        },
        {
            "name": "grant_phase_1_example",
            "title": "Example Prospect Research Flow",
            "description": "Concrete two-call example for a first-time grant search.",
            "arguments": [],
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Example call 1: grant_intake_or_update_project with "
                            "project_input='We are a nonprofit 501c3 working on wildfire debris "
                            "removal, wood products reuse, and disaster recovery in Appalachia.' "
                            "and profile_overrides recipient_loc_id='USA-NC', "
                            "place_of_performance_loc_id='USA-NC', has_government_partner=true. "
                            "Example call 2: grant_analyze_current_phase with the returned session_id."
                        ),
                    },
                }
            ],
        },
        {
            "name": "grant_choose_shortlist_target",
            "title": "Choose A Shortlist Target",
            "description": "How to move from a shortlist into phase 2 memo or budget work.",
            "arguments": [],
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "After phase 1 returns top_matches, pick one program_id from the shortlist "
                            "with grant_select_target_program, then call grant_analyze_current_phase "
                            "again with an analysis_request like 'Build memo talking points' or "
                            "'Help me build the budget framework.'"
                        ),
                    },
                }
            ],
        },
    ]


def _grant_resource_definitions() -> list[dict[str, Any]]:
    return [
        {
            "uri": RESOURCE_QUICKSTART_URI,
            "name": "Grant MCP Quickstart",
            "title": "Grant MCP Quickstart",
            "description": "Short onboarding guide for first-time grant MCP users and clients.",
            "mimeType": "text/markdown",
        },
        {
            "uri": RESOURCE_METHODS_URI,
            "name": "Grant MCP Methodology",
            "title": "Grant MCP Methodology",
            "description": "High-level explanation of scope, workflow phases, and honesty limits.",
            "mimeType": "text/markdown",
        },
    ]


def _resource_text_result(uri: str, text: str, *, mime_type: str = "text/markdown") -> dict[str, Any]:
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": mime_type,
                "text": text,
            }
        ]
    }


def _read_bundle_text(relative_path: str) -> str:
    return (BUNDLE_DIR / relative_path).read_text(encoding="utf-8")


def _grant_read_resource(uri: str) -> dict[str, Any] | None:
    if uri == RESOURCE_QUICKSTART_URI:
        return _resource_text_result(
            uri,
            _read_bundle_text("docs/grant_mcp_quickstart.md"),
        )
    if uri == RESOURCE_METHODS_URI:
        return _resource_text_result(
            uri,
            _read_bundle_text("docs/grant_mcp_mode.md"),
        )
    return None


def _grant_render_prompt(name: str) -> dict[str, Any] | None:
    prompt = next((item for item in _grant_prompt_definitions() if str(item.get("name") or "") == name), None)
    if not prompt:
        return None
    return {
        "description": prompt.get("description"),
        "messages": prompt.get("messages") or [],
    }


def _jsonrpc_response(result: dict[str, Any], request_id: Any) -> JSONResponse:
    response = JSONResponse(jsonrpc_result_envelope(result, request_id))
    response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
    response.headers["Cache-Control"] = "no-store"
    return response


def _jsonrpc_error(request_id: Any, code: int, message: str, *, data: dict[str, Any] | None = None, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(
        jsonrpc_error_envelope(request_id, code, message, data=data),
        status_code=status_code,
    )
    response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
    response.headers["Cache-Control"] = "no-store"
    return response


def _json_safe(obj: Any) -> Any:
    """Replace non-JSON-compliant float values (NaN, Infinity) with None.

    Starlette's JSONResponse serializes with allow_nan=False, so a NaN/Inf
    anywhere in a tool payload raises "Out of range float values are not JSON
    compliant" and the whole call fails with a generic 500. These values arise
    from empty-slice medians or divide-by-zero score factors and semantically
    mean "no value" - null conveys exactly that. Recurses through dicts/lists;
    numpy float scalars are handled since they subclass float. Applied at the
    tool-result boundary so both the text block and structuredContent are safe."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    return obj


def _tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    payload = _json_safe(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, (dict, list)) else str(payload)
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload if isinstance(payload, (dict, list)) else {"value": payload},
    }
    if is_error:
        result["isError"] = True
    return result


def _grant_runtime_debug_snapshot() -> dict[str, Any]:
    docs_dir = runtime_paths.DOCS_DIR
    data_root = runtime_paths.DATA_ROOT
    cfda_summary = data_root / "countries" / "USA" / "usaspending_grants" / "cfda_program_summary.parquet"
    awards = data_root / "countries" / "USA" / "usaspending_grants" / "USA.parquet"
    open_opps = data_root / "countries" / "USA" / "grants_gov_open_opportunities" / "latest.csv"
    checks = {
        "docs_dir": str(docs_dir),
        "docs_dir_exists": docs_dir.exists(),
        "data_root": str(data_root),
        "data_root_exists": data_root.exists(),
        "grant_funding_programs_csv": str(docs_dir / "grant_funding_programs.csv"),
        "grant_funding_programs_csv_exists": (docs_dir / "grant_funding_programs.csv").exists(),
        "grant_writing_templates_csv": str(docs_dir / "grant_writing_templates.csv"),
        "grant_writing_templates_csv_exists": (docs_dir / "grant_writing_templates.csv").exists(),
        "us_cfda_summary_parquet": str(cfda_summary),
        "us_cfda_summary_parquet_exists": cloud_data.exists(cfda_summary),
        "us_awards_parquet": str(awards),
        "us_awards_parquet_exists": cloud_data.exists(awards),
        "us_open_opportunities_csv": str(open_opps),
        "us_open_opportunities_csv_exists": cloud_data.exists(open_opps),
    }
    return {
        "module_dir": str(BUNDLE_DIR),
        "cwd": str(Path.cwd()),
        "project_root": str(runtime_paths.PROJECT_ROOT),
        "grant_data_mode": cloud_data.data_mode(),
        "checks": checks,
    }


def _canonical_site_headers(source_headers: dict[str, str] | None = None) -> dict[str, str]:
    source_headers = {str(key): str(value) for key, value in (source_headers or {}).items()}
    lookup = {key.lower(): value for key, value in source_headers.items()}
    headers = {
        "MCP-Protocol-Version": lookup.get("mcp-protocol-version", MCP_PROTOCOL_VERSION),
        "Cache-Control": lookup.get("cache-control", "no-store"),
        "Content-Type": "application/json",
    }
    if "retry-after" in lookup:
        headers["Retry-After"] = lookup["retry-after"]
    if "www-authenticate" in lookup:
        headers["WWW-Authenticate"] = lookup["www-authenticate"]
    return headers


def _parse_env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _grant_mcp_token_records() -> list[dict[str, str | None]]:
    raw = str(os.getenv("GRANT_MCP_ACCESS_TOKENS", "")).strip()
    if not raw:
        return []
    records: list[dict[str, str | None]] = []
    for entry in raw.split(","):
        token_entry = entry.strip()
        if not token_entry:
            continue
        if "=" in token_entry:
            label, token = token_entry.split("=", 1)
            label = label.strip()
            token = token.strip()
            if label and token:
                records.append({"label": label, "token": token})
                continue
        records.append({"label": None, "token": token_entry})
    return records


def _grant_mcp_tokens() -> set[str]:
    return {
        str(record.get("token") or "").strip()
        for record in _grant_mcp_token_records()
        if str(record.get("token") or "").strip()
    }


def _grant_mcp_token_metadata_from_header(header: str) -> dict[str, Any] | None:
    header = str(header or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    for record in _grant_mcp_token_records():
        candidate = str(record.get("token") or "").strip()
        if candidate != token:
            continue
        metadata: dict[str, Any] = {
            "private_mcp_token_id": hashlib.sha256(token.encode("utf-8")).hexdigest()[:8],
        }
        label = str(record.get("label") or "").strip()
        if label:
            metadata["private_mcp_token_label"] = label
        return metadata
    return None


def _grant_mcp_authorized(request: Request) -> bool:
    header = str(request.headers.get("Authorization") or "").strip()
    return _grant_mcp_authorized_header(header)


def _grant_mcp_authorized_header(header: str) -> bool:
    header = str(header or "").strip()
    if not header.lower().startswith("bearer "):
        return False
    token = header.split(" ", 1)[1].strip()
    return token in _grant_mcp_tokens()


def build_analytics_api_usage_event(
    *,
    authorization_header: str,
    body_bytes: bytes,
    status_code: int,
    response_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    token_metadata = _grant_mcp_token_metadata_from_header(authorization_header)
    if token_metadata is None:
        return None
    # DaedalMap hosted analytics only; standalone servers never call this.
    from mcp_runtime_shared import build_private_mcp_api_usage_event

    return build_private_mcp_api_usage_event(
        provider_slug=PROVIDER_SLUG,
        body_bytes=body_bytes,
        status_code=status_code,
        response_payload=response_payload,
        token_metadata=token_metadata,
    )


def _rate_limit_response(request_id: Any, retry_after: int) -> JSONResponse:
    response = _jsonrpc_error(
        request_id,
        -32000,
        "Grant MCP rate limit exceeded",
        data={"retry_after": retry_after},
        status_code=429,
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


def _apply_rate_limit(request: Request, request_id: Any) -> JSONResponse | None:
    from mapmover.security import get_client_ip, rate_limiter

    limit = _parse_env_int("GRANT_MCP_RATE_LIMIT", 30)
    window_seconds = _parse_env_int("GRANT_MCP_RATE_WINDOW_SECONDS", 60)
    caller = get_client_ip(request) or "unknown"
    allowed, retry_after = rate_limiter.check(
        f"grant-mcp:{caller}",
        limit=limit,
        window_seconds=window_seconds,
    )
    if allowed:
        return None
    return _rate_limit_response(request_id, retry_after)


async def handle_get_info(request: Request) -> JSONResponse:
    response = JSONResponse(info_payload())
    response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
    response.headers["Cache-Control"] = "no-store"
    return response


def info_payload() -> dict[str, Any]:
    return {
        "serverInfo": SERVER_INFO,
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "transport": "streamable-http",
        "auth": "bearer",
        "summary": (
            "Phase-aware grant workflow MCP for pre-submission grant work. "
            "Use it to intake a project, analyze fit and blockers, shortlist target "
            "programs, and then move into memo, budget, and submission-support steps."
        ),
        "howToStart": [
            "Send a bearer token only if the server has GRANT_MCP_ACCESS_TOKENS set.",
            "Call grant_intake_or_update_project with project_input.",
            "Use the returned session_id in grant_analyze_current_phase.",
            "Call prompts/list for starter instructions and example flows.",
        ],
        "recommendedFirstPrompt": (
            "Describe your project in plain language, including who the applicant is, "
            "where the work will happen, and what kind of funding support you need."
        ),
        "quickstartExample": QUICKSTART_EXAMPLE,
        "tools": [tool["name"] for tool in _grant_tool_definitions()],
        "prompts": [prompt["name"] for prompt in _grant_prompt_definitions()],
        "resources": [resource["uri"] for resource in _grant_resource_definitions()],
    }


async def handle_post_request(request: Request) -> JSONResponse:
    if not _grant_mcp_authorized(request):
        return JSONResponse(
            {"error": UNAUTHORIZED_MESSAGE},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error", status_code=400)

    if not isinstance(body, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request", status_code=400)

    request_id = body.get("id")
    rate_limit_response = _apply_rate_limit(request, request_id)
    if rate_limit_response:
        return rate_limit_response

    return handle_jsonrpc_dict(body)


def handle_jsonrpc_dict(body: dict[str, Any]) -> JSONResponse:
    request_id = body.get("id")
    method = str(body.get("method") or "").strip()
    params = body.get("params") or {}
    if params and not isinstance(params, dict):
        return _jsonrpc_error(request_id, -32602, "Invalid params")

    if method == "initialize":
        return _jsonrpc_response(
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
            request_id,
        )

    if method in {"notifications/initialized", "notifications/cancelled"}:
        response = JSONResponse({}, status_code=202)
        response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        response.headers["Cache-Control"] = "no-store"
        return response

    if method == "ping":
        return _jsonrpc_response({}, request_id)

    if method == "tools/list":
        return _jsonrpc_response({"tools": _grant_tool_definitions()}, request_id)

    if method == "resources/list":
        return _jsonrpc_response({"resources": _grant_resource_definitions()}, request_id)

    if method == "resources/read":
        uri = str(params.get("uri") or "").strip()
        if not uri:
            return _jsonrpc_error(request_id, -32602, "Resource uri is required")
        payload = _grant_read_resource(uri)
        if not payload:
            return _jsonrpc_error(request_id, -32602, f"Resource '{uri}' not found")
        return _jsonrpc_response(payload, request_id)

    if method == "prompts/list":
        return _jsonrpc_response({"prompts": _grant_prompt_definitions()}, request_id)

    if method == "prompts/get":
        prompt_name = str(params.get("name") or "").strip()
        if not prompt_name:
            return _jsonrpc_error(request_id, -32602, "Prompt name is required")
        payload = _grant_render_prompt(prompt_name)
        if not payload:
            return _jsonrpc_error(request_id, -32602, f"Prompt '{prompt_name}' not found")
        return _jsonrpc_response(payload, request_id)

    if method != "tools/call":
        return _jsonrpc_error(request_id, -32601, f"Method '{method}' not found")

    tool_name = str(params.get("name") or "").strip()
    arguments = params.get("arguments") or {}
    if not tool_name:
        return _jsonrpc_error(request_id, -32602, "Tool name is required")
    if arguments and not isinstance(arguments, dict):
        return _jsonrpc_error(request_id, -32602, "Tool arguments must be an object")

    if tool_name not in {tool["name"] for tool in _grant_tool_definitions()}:
        return _jsonrpc_error(request_id, -32601, f"Tool '{tool_name}' not found")

    try:
        result = grant_mcp_module.call_tool(tool_name, arguments)
    except Exception as exc:
        debug_snapshot = _grant_runtime_debug_snapshot()
        print(
            f"[grant_mcp_bundle] tool error tool={tool_name} exc={type(exc).__name__}: {exc}\n"
            f"debug={json.dumps(debug_snapshot, ensure_ascii=False)}\n"
            f"{traceback.format_exc()}"
        )
        return _jsonrpc_error(
            request_id,
            -32603,
            "Internal grant MCP tool error",
            data={
                "tool_name": tool_name,
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "debug": debug_snapshot,
            },
            status_code=500,
        )
    is_error = result.get("outcome") in {
        grant_mcp_module.OUTCOME_ERROR,
        grant_mcp_module.OUTCOME_OUT_OF_SCOPE,
        grant_mcp_module.OUTCOME_UNSUPPORTED_COUNTRY,
        grant_mcp_module.OUTCOME_UNSUPPORTED_CLAIM,
    }
    return _jsonrpc_response(_tool_result(result, is_error=is_error), request_id)


def handle_internal_site_get(headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any], dict[str, str]]:
    headers = _canonical_site_headers(headers)
    return 200, info_payload(), headers


def handle_internal_site_post(
    *,
    authorization_header: str,
    body_bytes: bytes,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    if not _grant_mcp_authorized_header(authorization_header):
        return 401, {"error": UNAUTHORIZED_MESSAGE}, _canonical_site_headers(
            {"WWW-Authenticate": "Bearer"}
        )

    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        response = _jsonrpc_error(None, -32700, "Parse error", status_code=400)
        response_headers = _canonical_site_headers(dict(response.headers))
        return response.status_code, json.loads(response.body.decode("utf-8")), response_headers
    if not isinstance(body, dict):
        response = _jsonrpc_error(None, -32600, "Invalid Request", status_code=400)
        response_headers = _canonical_site_headers(dict(response.headers))
        return response.status_code, json.loads(response.body.decode("utf-8")), response_headers

    response = handle_jsonrpc_dict(body)
    response_headers = _canonical_site_headers(dict(response.headers))
    return response.status_code, json.loads(response.body.decode("utf-8")), response_headers
