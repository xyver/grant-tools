"""Place text to DaedalMap loc_id, through the geometry tools' MCP.

The grant tools' only DaedalMap dependency. Calls `convert_reference` at
GEOMETRY_MCP_URL: DaedalMap's hosted MCP by default, or a locally run copy of
the geometry tools (`http://127.0.0.1:7010/mcp/geometry`). Both speak the same
MCP, so this client does not care which one it talks to.

Every country goes through the same call. `convert_reference` needs one ISO3
scope and parses "Name, Parent" text itself ("Buncombe County, North
Carolina", "Prince George, British Columbia"), so this client adds no
per-country parsing. An ambiguous name comes back with candidates rather than
a guess; those are passed through so the user can pick one.

A lookup problem never breaks an analysis: resolve_place always returns a
result whose `loc_id` is None when nothing resolved, with `status` saying why.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from runtime_paths import GEOMETRY_MCP_URL

TIMEOUT_SECONDS = 20
MAX_CANDIDATES = 10

# Countries that have a grant backend, in the ISO3 form convert_reference wants.
SUPPORTED_ISO3 = ("USA", "CAN")
_ISO3_ALIASES = {"US": "USA", "USA": "USA", "CA": "CAN", "CAN": "CAN"}

STATUS_RESOLVED = "resolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_FOUND = "not_found"
STATUS_UNAVAILABLE = "lookup_unavailable"


def _call_tool(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return (structured output, reached_server). Output is None on failure."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).encode("utf-8")
    request = urllib.request.Request(
        GEOMETRY_MCP_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None, False
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return None, True
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured, True
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or ""), True
            except ValueError:
                return None, True
    return None, True


def _candidates(output: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (output.get("from") or {}).get("candidates") or output.get("candidates") or []
    candidates = []
    for row in raw:
        if isinstance(row, dict) and row.get("loc_id"):
            candidates.append({
                "loc_id": str(row["loc_id"]),
                "name": row.get("name"),
                "admin_level": row.get("admin_level"),
            })
    return candidates[:MAX_CANDIDATES]


def _convert(value: str, **arguments: Any) -> dict[str, Any]:
    """One convert_reference call, reduced to loc_id plus a status."""
    output, reached = _call_tool("convert_reference", {"value": value, **arguments})
    if output is None:
        return {"loc_id": None, "status": STATUS_UNAVAILABLE if not reached else STATUS_NOT_FOUND}
    origin = output.get("from") or {}
    resolved = origin.get("resolved_loc_id")
    if not resolved:
        for row in output.get("results") or []:
            if isinstance(row, dict) and row.get("value"):
                resolved = row["value"]
                break
    if resolved:
        return {"loc_id": str(resolved), "status": STATUS_RESOLVED, "match_type": origin.get("match_type")}
    candidates = _candidates(output)
    if candidates:
        return {"loc_id": None, "status": STATUS_AMBIGUOUS, "candidates": candidates}
    return {"loc_id": None, "status": STATUS_NOT_FOUND}


def _result(text: str, outcome: dict[str, Any], *, match_type: str) -> dict[str, Any]:
    return {
        "loc_id": outcome.get("loc_id"),
        "status": outcome.get("status"),
        "matched_term": text,
        "match_type": outcome.get("match_type") or match_type,
        "candidates": outcome.get("candidates") or [],
        "source": "geometry_tools",
    }


def resolve_place(text: str, country: str | None = None) -> dict[str, Any]:
    """Resolve one place string to a loc_id.

    Returns {"loc_id", "status", "matched_term", "match_type", "candidates",
    "source"}. `loc_id` is None unless status is "resolved". Without a
    country, every supported country is tried and a name found in more than
    one is reported as ambiguous.
    """
    text = str(text or "").strip()
    if not text:
        return {"loc_id": None, "status": STATUS_NOT_FOUND, "matched_term": text,
                "match_type": None, "candidates": [], "source": "geometry_tools"}
    iso3 = _ISO3_ALIASES.get(str(country or "").strip().upper())
    countries = (iso3,) if iso3 else SUPPORTED_ISO3

    zip_match = re.fullmatch(r"(\d{5})(?:-\d{4})?", text)
    if zip_match and "USA" in countries:
        outcome = _convert(zip_match.group(1), from_system="zip", iso3="USA")
        if outcome["loc_id"]:
            # A ZCTA loc_id carries its county as the first three segments,
            # e.g. USA-NC-021-000900-POSTAL-28801 -> USA-NC-021.
            outcome = {**outcome, "loc_id": "-".join(outcome["loc_id"].split("-")[:3])}
        return _result(zip_match.group(1), outcome, match_type="postal_code")

    outcomes = [_convert(text, from_system="admin_boundary", iso3=code) for code in countries]
    resolved = [o for o in outcomes if o["loc_id"]]
    if len(resolved) == 1:
        return _result(text, resolved[0], match_type="admin_name")
    if len(resolved) > 1:
        candidates = [{"loc_id": o["loc_id"], "name": text, "admin_level": None} for o in resolved]
        return _result(text, {"loc_id": None, "status": STATUS_AMBIGUOUS, "candidates": candidates},
                       match_type="admin_name")
    candidates = [c for o in outcomes for c in o.get("candidates") or []][:MAX_CANDIDATES]
    if candidates:
        return _result(text, {"loc_id": None, "status": STATUS_AMBIGUOUS, "candidates": candidates},
                       match_type="admin_name")
    status = STATUS_UNAVAILABLE if all(o["status"] == STATUS_UNAVAILABLE for o in outcomes) else STATUS_NOT_FOUND
    return _result(text, {"loc_id": None, "status": status}, match_type="admin_name")
