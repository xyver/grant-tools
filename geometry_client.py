"""Place text to DaedalMap loc_id, through the geometry tools' MCP.

The grant tools' only DaedalMap dependency. Calls `convert_reference` at
GEOMETRY_MCP_URL: DaedalMap's hosted MCP by default, or a locally run copy of
the geometry tools (`http://127.0.0.1:7010/mcp/geometry`). Both speak the same
MCP, so this client does not care which one it talks to.

Every failure returns None so the caller can fall back or ask the user for a
loc_id; a lookup problem never breaks an analysis.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from runtime_paths import GEOMETRY_MCP_URL

TIMEOUT_SECONDS = 20

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}
_STATE_CODES = set(US_STATES.values())


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
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
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or result.get("isError"):
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or "")
            except ValueError:
                return None
    return None


def _convert(value: str, **arguments: Any) -> str | None:
    """Return the resolved loc_id for one convert_reference call, or None."""
    output = _call_tool("convert_reference", {"value": value, **arguments})
    if not output:
        return None
    resolved = (output.get("from") or {}).get("resolved_loc_id")
    if resolved:
        return str(resolved)
    for row in output.get("results") or []:
        if isinstance(row, dict) and row.get("value"):
            return str(row["value"])
    return None


def _state_code(text: str) -> str | None:
    cleaned = text.strip().lower().rstrip(".")
    if cleaned.upper() in _STATE_CODES:
        return cleaned.upper()
    return US_STATES.get(cleaned)


def resolve_place(text: str, country: str | None = None) -> dict[str, Any] | None:
    """Resolve one place string to a loc_id.

    Returns {"loc_id", "matched_term", "match_type", "source"} or None.
    """
    text = str(text or "").strip()
    if not text:
        return None
    iso3 = {"US": "USA", "CA": "CAN"}.get((country or "").upper(), (country or "").upper()) or None

    zip_match = re.fullmatch(r"(\d{5})(?:-\d{4})?", text)
    if zip_match and iso3 in (None, "USA"):
        zcta = _convert(zip_match.group(1), from_system="zip", iso3="USA")
        if zcta:
            # A ZCTA loc_id carries its county as the first three segments,
            # e.g. USA-NC-021-000900-POSTAL-28801 -> USA-NC-021.
            county = "-".join(zcta.split("-")[:3])
            return {"loc_id": county, "matched_term": zip_match.group(1), "match_type": "postal_code", "source": "geometry_tools"}
        return None

    hint = {"country_hint": iso3} if iso3 else {}
    loc_id = _convert(text, from_system="admin_boundary", **hint)
    if loc_id:
        return {"loc_id": loc_id, "matched_term": text, "match_type": "admin_name", "source": "geometry_tools"}

    # "Name, State": the geometry tools do not parse a parent from the string
    # yet, so look up the bare name as a county and keep it only if it falls
    # in the named state. Ambiguous names still fail until the tools accept a
    # parent hint (tracked in the DaedalMap MCP project).
    if "," in text and iso3 in (None, "USA"):
        name, _, parent = text.rpartition(",")
        state = _state_code(parent)
        if state and name.strip():
            county = _convert(name.strip(), from_system="admin_boundary", country_hint="USA", admin_level_hint=2)
            if county and county.upper().startswith(f"USA-{state}-"):
                return {"loc_id": county, "matched_term": text, "match_type": "admin_name_with_state", "source": "geometry_tools"}
            state_loc_id = f"USA-{state}"
            return {"loc_id": state_loc_id, "matched_term": parent.strip(), "match_type": "state_only", "source": "geometry_tools"}
    return None
