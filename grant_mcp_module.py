"""
Private grant MCP module.

This is the first real module layer for the phase-aware grant MCP. It is
deliberately separate from any public MCP publishing flow:

- no registry metadata
- no server-card generation
- no public discovery/publishing assumptions

It is meant to sit behind a private HTTP MCP endpoint later, where the URL can
be shared manually with trusted users or pilot customers.

The module keeps three core promises from the contract docs:

- phase-aware routing across the pre-submission grant phases
- structured working state rather than chat-history-only state
- explicit clarification and capability-gating instead of silent guessing
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import can_backend  # noqa: E402
import grant_analyzer as ga  # noqa: E402
import us_backend  # noqa: E402
from runtime_paths import STATE_DIR  # noqa: E402


import geometry_client  # noqa: E402

PHASE_1 = "phase_1_prospect_research"
PHASE_2 = "phase_2_proposal_development"
PHASE_3 = "phase_3_submission_support"
OUT_OF_SCOPE = "out_of_scope_post_award"
AMBIGUOUS = "ambiguous"

FIELD_CONFIRMED = "confirmed"
FIELD_INFERRED = "inferred"
FIELD_MISSING = "missing"
FIELD_CONFLICTING = "conflicting"

OUTCOME_OK = "ok"
OUTCOME_CLARIFICATION = "clarification_needed"
OUTCOME_OUT_OF_SCOPE = "out_of_scope"
OUTCOME_UNSUPPORTED_COUNTRY = "unsupported_country_capability"
OUTCOME_UNSUPPORTED_CLAIM = "unsupported_claim_type"
OUTCOME_ERROR = "error"

PROJECT_PROFILE_FIELDS = (
    "project_description",
    "org_name",
    "org_type",
    "sector_tags",
    "recipient_location_text",
    "recipient_loc_id",
    "project_area_text",
    "place_of_performance_loc_id",
    "project_area_loc_ids",
    "target_country_code",
    "has_government_partner",
    "has_match_funding",
    "target_award_amount",
    "target_project_length_days",
)

_SESSIONS: dict[str, dict[str, Any]] = {}

APPALACHIA_EXPLICIT_TERMS = (
    "appalachia",
    "appalachian",
    "coal community",
    "coal communities",
    "southwest virginia",
    "southwestern virginia",
)

# ARC covers all West Virginia counties. Most other ARC states are partial-state
# geographies, so state-only loc_ids such as USA-VA should not trigger ARC live
# evidence without explicit Appalachia language or a county-level map.
APPALACHIA_FULL_STATE_CODES = {"WV"}


def reset_sessions() -> None:
    _SESSIONS.clear()


def get_session(session_id: str) -> dict[str, Any] | None:
    session = _SESSIONS.get(str(session_id or "").strip())
    return deepcopy(session) if session else None


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = dict(arguments or {})
    if name == "grant_intake_or_update_project":
        return grant_intake_or_update_project(**arguments)
    if name == "grant_analyze_current_phase":
        return grant_analyze_current_phase(**arguments)
    if name == "grant_set_phase":
        return grant_set_phase(**arguments)
    if name == "grant_select_target_program":
        return grant_select_target_program(**arguments)
    return {
        "tool_name": name,
        "outcome": OUTCOME_ERROR,
        "error": {"code": "unknown_tool", "message": f"Unknown grant MCP tool '{name}'."},
    }


def grant_intake_or_update_project(
    project_input: str,
    session_id: str | None = None,
    project_label: str | None = None,
    country_code: str | None = None,
    phase_hint: str | None = None,
    profile_overrides: dict[str, Any] | None = None,
    allow_inference: bool = True,
) -> dict[str, Any]:
    if not str(project_input or "").strip():
        return _error_result(
            "grant_intake_or_update_project",
            "invalid_project_input",
            "project_input is required.",
        )

    session = _load_or_create_session(session_id=session_id, project_label=project_label)
    text = str(project_input).strip()
    overrides = dict(profile_overrides or {})

    _merge_project_description(session, text)

    for field_name, value in overrides.items():
        if field_name not in PROJECT_PROFILE_FIELDS:
            continue
        _set_profile_field(session, field_name, value, FIELD_CONFIRMED, "profile_override")

    if country_code:
        session["country_code"] = _normalize_country_code(country_code)
    elif not session.get("country_code"):
        session["country_code"] = _infer_backend_country(session, allow_inference=allow_inference)

    _apply_location_text_overrides(session, country_code=country_code)

    if allow_inference:
        _apply_inferred_profile_fields(session, text)

    profile_notes = _apply_vocabulary_normalization(session)

    inferred_phase, phase_confidence = _infer_phase(text, phase_hint=phase_hint, existing_phase=session.get("current_phase"))
    session["current_phase"] = phase_hint or inferred_phase
    session["phase_confidence"] = phase_confidence

    if session["current_phase"] == OUT_OF_SCOPE:
        _SESSIONS[session["session_id"]] = session
        return {
            "tool_name": "grant_intake_or_update_project",
            "outcome": OUTCOME_OUT_OF_SCOPE,
            "session_id": session["session_id"],
            "current_phase": session["current_phase"],
            "notes": ["This grant MCP currently supports only pre-submission phases 1-3."],
        }

    if not _backend_exists(session.get("country_code")):
        _SESSIONS[session["session_id"]] = session
        return {
            "tool_name": "grant_intake_or_update_project",
            "outcome": OUTCOME_UNSUPPORTED_COUNTRY,
            "session_id": session["session_id"],
            "country_code": session.get("country_code"),
            "notes": [f"No grant backend exists for country '{session.get('country_code')}'."],
        }

    missing_fields = _compute_missing_fields(session["current_phase"], session)
    session["project_profile_missing_fields"] = missing_fields
    session["open_questions"] = _build_questions_for_missing_fields(session["current_phase"], missing_fields, session)

    _SESSIONS[session["session_id"]] = session

    if session["open_questions"]:
        return {
            "tool_name": "grant_intake_or_update_project",
            "outcome": OUTCOME_CLARIFICATION,
            "session_id": session["session_id"],
            "current_phase": session["current_phase"],
            "phase_confidence": session["phase_confidence"],
            "country_code": session.get("country_code"),
            "project_profile": deepcopy(session["project_profile"]),
            "project_profile_missing_fields": list(missing_fields),
            "questions": deepcopy(session["open_questions"]),
            "resume_hint": "grant_intake_or_update_project",
            "notes": list(profile_notes),
        }

    return {
        "tool_name": "grant_intake_or_update_project",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "project_label": session.get("project_label"),
        "current_phase": session["current_phase"],
        "phase_confidence": session["phase_confidence"],
        "country_code": session.get("country_code"),
        "project_profile": deepcopy(session["project_profile"]),
        "project_profile_missing_fields": [],
        "conflicting_fields": [],
        "open_questions": [],
        "next_recommended_tool": "grant_analyze_current_phase",
        "notes": list(profile_notes),
    }


def grant_analyze_current_phase(
    session_id: str,
    analysis_request: str | None = None,
    phase_override: str | None = None,
    depth: str = "standard",
    refresh_profile_from_text: bool = True,
    top_n: int = 5,
) -> dict[str, Any]:
    session = _SESSIONS.get(str(session_id or "").strip())
    if not session:
        return _error_result(
            "grant_analyze_current_phase",
            "unknown_session",
            "session_id was not found.",
        )

    request_text = str(analysis_request or "").strip()
    prior_phase = session.get("current_phase") or AMBIGUOUS

    if refresh_profile_from_text and request_text:
        _merge_request_text_into_profile(session, request_text)

    inferred_phase, phase_confidence = _infer_phase(
        request_text,
        phase_hint=phase_override,
        existing_phase=prior_phase,
    )
    active_phase = phase_override or (inferred_phase if inferred_phase != AMBIGUOUS else prior_phase)
    if active_phase == OUT_OF_SCOPE:
        session["current_phase"] = active_phase
        session["phase_confidence"] = phase_confidence
        _SESSIONS[session["session_id"]] = session
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_OUT_OF_SCOPE,
            "session_id": session["session_id"],
            "phase_run": active_phase,
            "notes": ["Post-award phases are intentionally out of scope for this grant MCP."],
        }

    missing_fields = _compute_missing_fields(active_phase, session)
    questions = _build_questions_for_missing_fields(active_phase, missing_fields, session)
    if questions:
        session["current_phase"] = active_phase
        session["phase_confidence"] = phase_confidence
        session["project_profile_missing_fields"] = missing_fields
        session["open_questions"] = questions
        _SESSIONS[session["session_id"]] = session
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_CLARIFICATION,
            "session_id": session["session_id"],
            "phase_run": active_phase,
            "questions": deepcopy(questions),
            "resume_hint": "grant_analyze_current_phase",
        }

    country_code = session.get("country_code") or "US"
    if not _backend_exists(country_code):
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_UNSUPPORTED_COUNTRY,
            "session_id": session["session_id"],
            "phase_run": active_phase,
            "country_code": country_code,
            "notes": [f"No grant backend exists for country '{country_code}'."],
        }

    phase_changed = active_phase != prior_phase
    session["current_phase"] = active_phase
    session["phase_confidence"] = phase_confidence

    if active_phase == PHASE_1:
        result = _run_phase_1(
            session,
            depth=depth,
            top_n=_bounded_phase_1_top_n(top_n),
        )
    elif active_phase == PHASE_2:
        result = _run_phase_2(session, request_text=request_text, depth=depth)
    elif active_phase == PHASE_3:
        result = _run_phase_3(session, request_text=request_text)
    else:
        result = {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_ERROR,
            "session_id": session["session_id"],
            "phase_run": active_phase,
            "error": {"code": "ambiguous_phase", "message": "Could not determine an actionable phase."},
        }

    if result.get("outcome") == OUTCOME_OK:
        result["phase_changed"] = phase_changed
        if phase_changed:
            result["prior_phase"] = prior_phase
            result["new_phase"] = active_phase

    _SESSIONS[session["session_id"]] = session
    return result


def grant_set_phase(session_id: str, phase_id: str) -> dict[str, Any]:
    session = _SESSIONS.get(str(session_id or "").strip())
    if not session:
        return _error_result("grant_set_phase", "unknown_session", "session_id was not found.")
    prior_phase = session.get("current_phase")
    session["current_phase"] = phase_id
    session["phase_confidence"] = 1.0
    _SESSIONS[session["session_id"]] = session
    return {
        "tool_name": "grant_set_phase",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "prior_phase": prior_phase,
        "new_phase": phase_id,
    }


def grant_select_target_program(session_id: str, program_id: str) -> dict[str, Any]:
    session = _SESSIONS.get(str(session_id or "").strip())
    if not session:
        return _error_result("grant_select_target_program", "unknown_session", "session_id was not found.")
    program_id = str(program_id or "").strip()
    targets = session.get("current_target_programs") or []
    match = next((item for item in targets if str(item.get("program_id") or "") == program_id), None)
    if not match:
        return _error_result(
            "grant_select_target_program",
            "program_not_in_session",
            f"Program '{program_id}' is not in the current target set.",
        )
    session["current_selected_program_id"] = program_id
    _SESSIONS[session["session_id"]] = session
    return {
        "tool_name": "grant_select_target_program",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "current_selected_program_id": program_id,
    }


def _profile_text(profile: dict[str, dict[str, Any]]) -> str:
    parts = []
    for field_name in PROJECT_PROFILE_FIELDS:
        value = _field_value(profile, field_name)
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _loc_id_state(loc_id: Any) -> str | None:
    match = re.match(r"^USA-([A-Z]{2})(?:-|$)", str(loc_id or "").strip().upper())
    return match.group(1) if match else None


def _profile_states(profile: dict[str, dict[str, Any]]) -> set[str]:
    states = set()
    for field_name in ("recipient_loc_id", "place_of_performance_loc_id"):
        state = _loc_id_state(_field_value(profile, field_name))
        if state:
            states.add(state)
    # A multi-state project is in every one of its states, not just the primary.
    for loc_id in _normalize_loc_id_list(_field_value(profile, "project_area_loc_ids")):
        state = _loc_id_state(loc_id)
        if state:
            states.add(state)
    return states


def _localization_summary(profile: dict[str, dict[str, Any]]) -> dict[str, Any]:
    applicant_loc_id = _field_value(profile, "recipient_loc_id")
    project_loc_id = _field_value(profile, "place_of_performance_loc_id")
    applicant_state = _loc_id_state(applicant_loc_id)
    project_state = _loc_id_state(project_loc_id)
    scope_loc_id = project_loc_id or applicant_loc_id
    scope_state = project_state or applicant_state
    regional_scopes = []
    if _is_california_profile(profile):
        regional_scopes.append("california")
    if _is_appalachia_profile(profile):
        regional_scopes.append("appalachia")
    if scope_state == "VA":
        regional_scopes.append("virginia")
    return {
        "applicant_home_loc_id": applicant_loc_id,
        "project_area_loc_id": project_loc_id,
        "project_area_loc_ids": _project_area_loc_ids(profile),
        "target_country_code": _field_value(profile, "target_country_code"),
        "primary_filter_loc_id": scope_loc_id,
        "primary_filter_state": scope_state,
        "regional_source_scopes": sorted(set(regional_scopes)),
        "filtering_rule": (
            "Use project_area_loc_id/place_of_performance first for regional "
            "sources; use applicant_home_loc_id for applicant eligibility and "
            "capacity signals."
        ),
    }


def _is_california_profile(profile: dict[str, dict[str, Any]]) -> bool:
    if "CA" in _profile_states(profile):
        return True
    return any(token in _profile_text(profile) for token in ("california", " ca "))


def _is_appalachia_profile(profile: dict[str, dict[str, Any]]) -> bool:
    states = _profile_states(profile)
    if states & APPALACHIA_FULL_STATE_CODES:
        return True
    text = _profile_text(profile)
    return any(term in text for term in APPALACHIA_EXPLICIT_TERMS)


def _load_local_collector_snapshot(collector_name: str) -> dict[str, Any] | None:
    path = STATE_DIR / collector_name / "snapshot.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _grantish_live_items(items: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    terms = (
        "applicant",
        "application",
        "applications",
        "apply",
        "due",
        "funding",
        "grant",
        "grants",
        "nosa",
        "solicitation",
    )
    selected = []
    for item in items:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "raw_excerpt", "link")
        ).lower()
        if not any(term in text for term in terms):
            continue
        selected.append({
            "title": item.get("title"),
            "link": item.get("link"),
            "published": item.get("published"),
            "source_feed": item.get("source_feed"),
            "id": item.get("id"),
        })
        if len(selected) >= limit:
            break
    return selected


def _arc_live_update_evidence(profile: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not _is_appalachia_profile(profile):
        return None
    snapshot = _load_local_collector_snapshot("arc_grant_announcements")
    if snapshot is None:
        return {
            "source_layer": "live_announcement",
            "source_name": "Appalachian Regional Commission news feed",
            "collector": "arc_grant_announcements",
            "found": False,
            "note": "No local ARC collector snapshot is available yet.",
        }
    payload = snapshot.get("payload_summary") or {}
    recent_items = [item for item in payload.get("recent_items") or [] if isinstance(item, dict)]
    leads = _grantish_live_items(recent_items, limit=5)
    return {
        "source_layer": "live_announcement",
        "source_name": "Appalachian Regional Commission news feed",
        "collector": "arc_grant_announcements",
        "found": bool(leads),
        "fetched_at": snapshot.get("fetched_at"),
        "upstream_issued_at": snapshot.get("upstream_issued_at"),
        "recent_grantish_items": leads,
        "observed_history_note": (
            "ARC is a live source. Its persistent history begins when our collector observes updates; "
            "it is not treated as complete historical award precedent."
        ),
    }


def _arc_curated_program_evidence(profile: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not _is_appalachia_profile(profile):
        return None
    matches = us_backend.match_grant_programs(
        org_type=_field_value(profile, "org_type"),
        sector_tags=_field_value(profile, "sector_tags") or None,
        recipient_loc_id=_field_value(profile, "recipient_loc_id"),
        place_of_performance_loc_id=_field_value(profile, "place_of_performance_loc_id"),
        has_government_partner=bool(_field_value(profile, "has_government_partner")),
        has_match_funding=bool(_field_value(profile, "has_match_funding")),
        target_country_code=_field_value(profile, "target_country_code"),
    )
    arc_matches = [
        {
            "program_id": item.get("program_id"),
            "program_name": item.get("program_name"),
            "program_status": (
                item.get("program_status")
                or (us_backend.PROGRAM_STATUS_OVERRIDES.get(str(item.get("program_id") or "")) or {}).get("status")
                or "standing"
            ),
            "directly_eligible": item.get("directly_eligible"),
            "blockers": item.get("blockers") or [],
            "pathway_note": item.get("pathway_note"),
            "source_url": item.get("source_url"),
        }
        for item in matches
        if str(item.get("program_id") or "").startswith("arc_")
    ]
    if not arc_matches:
        return None
    return {
        "source_layer": "regional_curated_programs",
        "source_name": "Appalachian Regional Commission curated eligibility rows",
        "found": True,
        "program_count": len(arc_matches),
        "programs": arc_matches,
        "note": (
            "These ARC leads are regional eligibility/pathway matches. They are "
            "reported separately from federal readiness scoring and live announcements."
        ),
    }


def _virginia_curated_program_evidence(profile: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    localization = _localization_summary(profile)
    if "virginia" not in (localization.get("regional_source_scopes") or []):
        return None
    matches = us_backend.match_grant_programs(
        org_type=_field_value(profile, "org_type"),
        sector_tags=_field_value(profile, "sector_tags") or None,
        recipient_loc_id=_field_value(profile, "recipient_loc_id"),
        place_of_performance_loc_id=_field_value(profile, "place_of_performance_loc_id"),
        has_government_partner=bool(_field_value(profile, "has_government_partner")),
        has_match_funding=bool(_field_value(profile, "has_match_funding")),
        target_country_code=_field_value(profile, "target_country_code"),
    )
    virginia_matches = [
        {
            "program_id": item.get("program_id"),
            "program_name": item.get("program_name"),
            "program_status": (
                item.get("program_status")
                or (us_backend.PROGRAM_STATUS_OVERRIDES.get(str(item.get("program_id") or "")) or {}).get("status")
                or "standing"
            ),
            "directly_eligible": item.get("directly_eligible"),
            "blockers": item.get("blockers") or [],
            "pathway_note": item.get("pathway_note"),
            "source_url": item.get("source_url"),
        }
        for item in matches
        if str(item.get("program_id") or "").startswith("vipc_")
    ]
    if not virginia_matches:
        return None
    return {
        "source_layer": "regional_curated_programs",
        "source_name": "Virginia Innovation Partnership Corporation curated eligibility rows",
        "found": True,
        "program_count": len(virginia_matches),
        "programs": virginia_matches,
        "note": (
            "These Virginia leads are local commercialization, public-safety, "
            "or innovation pathways shown separately from federal readiness scoring."
        ),
    }


def _california_curated_program_evidence(profile: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    localization = _localization_summary(profile)
    if "california" not in (localization.get("regional_source_scopes") or []):
        return None
    matches = us_backend.match_grant_programs(
        org_type=_field_value(profile, "org_type"),
        sector_tags=_field_value(profile, "sector_tags") or None,
        recipient_loc_id=_field_value(profile, "recipient_loc_id"),
        place_of_performance_loc_id=_field_value(profile, "place_of_performance_loc_id"),
        has_government_partner=bool(_field_value(profile, "has_government_partner")),
        has_match_funding=bool(_field_value(profile, "has_match_funding")),
        target_country_code=_field_value(profile, "target_country_code"),
    )
    california_prefixes = ("calfire_", "caltrans_", "cec_", "calseed", "sgc_", "lci_")
    california_matches = [
        {
            "program_id": item.get("program_id"),
            "program_name": item.get("program_name"),
            "program_status": (
                item.get("program_status")
                or (us_backend.PROGRAM_STATUS_OVERRIDES.get(str(item.get("program_id") or "")) or {}).get("status")
                or "standing"
            ),
            "directly_eligible": item.get("directly_eligible"),
            "blockers": item.get("blockers") or [],
            "pathway_note": item.get("pathway_note"),
            "source_url": item.get("source_url"),
        }
        for item in matches
        if str(item.get("program_id") or "").startswith(california_prefixes)
    ]
    if not california_matches:
        return None
    return {
        "source_layer": "regional_curated_programs",
        "source_name": "California curated eligibility rows",
        "found": True,
        "program_count": len(california_matches),
        "programs": california_matches,
        "note": (
            "These California leads are reusable state program pathways. "
            "Some are university-led R&D routes; others are best pursued "
            "with public, tribal, nonprofit, or planning-agency partners."
        ),
    }


# access_path values meaning the applicant has no way to submit at all. These
# are removed from the shortlist rather than ranked in it. loi_required and a
# blank value are absent on purpose: an LOI is a first step, and blank means
# unresearched, which must not be treated as unreachable.
UNREACHABLE_ACCESS_PATHS = frozenset(
    {"not_an_application", "invitation_only", "state_routed", "partnership_required"}
)

# lifecycle_status values meaning the program is over. These are kept and
# reported, not dropped: a one-time appropriation is useless as a target but
# genuinely useful as precedent for writing the next application. "unknown"
# and "recurring" are absent on purpose - unknown means undetermined, and a
# recurring program between rounds is still a live target for the next cycle.
HISTORICAL_LIFECYCLE_STATUSES = frozenset(
    {"one_time", "cancelled", "closed", "historical_only"}
)

# Verified against Grants.gov as having a posted opportunity right now. Worth
# calling out separately: "recurring" means it comes back, "open" means you can
# apply today, and only one of those has a deadline attached.
OPEN_LIFECYCLE_STATUS = "open"

# Single-location phase 1 keeps the historical shortlist size. When fanning out
# across several places of performance we pull a deeper slice per location
# first: geography-specific programs (ARC in Appalachia, CALFIRE in California)
# score below broad national ones, so a per-location cut of 5 truncated them
# away before the merge could ever attribute them to a location.
PHASE_1_TOP_N = 5
PHASE_1_PER_LOCATION_TOP_N = 25
PHASE_1_MERGED_TOP_N = 10
PHASE_1_MIN_TOP_N = 1
PHASE_1_MAX_TOP_N = 10


def _bounded_phase_1_top_n(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = PHASE_1_TOP_N
    return max(PHASE_1_MIN_TOP_N, min(PHASE_1_MAX_TOP_N, parsed))


# A real grant brief usually already names the funders it is aimed at - the
# Woodlands memo listed "U.S. Forest Service" and "Department of Homeland
# Security" under Prospective Funding Sources. Discovery that ignores those
# answers a question the author did not ask. Keys are regex fragments matched
# against the project narrative; values are the catalog's agency_or_funder
# spelling, which is often different ("USDA Forest Service").
DECLARED_FUNDER_PATTERNS = (
    (r"\b(u\.?s\.?|usda)?\s*forest service\b|\busfs\b", "USDA Forest Service"),
    (r"\bappalachian regional commission\b|\barc\b", "Appalachian Regional Commission"),
    (r"\bdepartment of homeland security\b|\bdhs\b", "FEMA"),
    (r"\bfema\b", "FEMA"),
    (r"\bhud\b|\bhousing and urban development\b", "HUD"),
    (r"\bdepartment of labor\b|\bdol\b", "Department of Labor"),
    (r"\busda\b(?!\s*forest)", "USDA"),
    (r"\bnrcs\b|\bnatural resources conservation service\b", "USDA NRCS"),
    (r"\bepa\b|\benvironmental protection agency\b", "EPA"),
    (r"\bdepartment of energy\b|\bdoe\b", "DOE"),
    (r"\bnsf\b|\bnational science foundation\b", "NSF"),
    (r"\bdepartment of the interior\b|\bdoi\b", "Department of the Interior"),
    (r"\bcdfi fund\b|\bcdfi\b", "CDFI Fund (Treasury)"),
    (r"\bcal ?fire\b", "CAL FIRE"),
    (r"\bcaltrans\b", "Caltrans"),
    (r"\bvipc\b|\bvirginia innovation partnership\b", "Virginia Innovation Partnership Corporation"),
    (r"\busaid\b", "USAID"),
    (r"\bdepartment of state\b", "Department of State"),
    (r"\bfhwa\b|\bfederal highway\b", "DOT (FHWA)"),
)


def _extract_declared_funders(text: Any) -> list[dict[str, Any]]:
    """Funders the project brief itself names as intended targets."""
    body = str(text or "").lower()
    if not body:
        return []
    found: dict[str, str] = {}
    for pattern, canonical in DECLARED_FUNDER_PATTERNS:
        match = re.search(pattern, body)
        if match and canonical not in found:
            found[canonical] = match.group(0).strip()
    return [
        {"funder": canonical, "matched_text": matched}
        for canonical, matched in found.items()
    ]


def _clean_cell(value: Any) -> str | None:
    """Normalize a pandas cell to a string or None without importing pandas."""
    text = str(value if value is not None else "").strip()
    return text if text and text.lower() != "nan" else None


def _programs_for_funder(funder: str) -> list[dict[str, Any]]:
    try:
        df = us_backend.load_programs()
    except Exception:
        return []
    rows = []
    for _, row in df.iterrows():
        funders = [f.strip() for f in str(row.get("agency_or_funder") or "").split("|")]
        if funder in funders:
            rows.append(
                {
                    "program_id": row.get("program_id"),
                    "program_name": row.get("program_name"),
                    "cfda_number": _clean_cell(row.get("cfda_number")),
                    "eligible_applicant_types": [
                        t.strip() for t in str(row.get("eligible_applicant_types") or "").split("|") if t.strip()
                    ],
                    "sector_tags": [
                        t.strip() for t in str(row.get("sector_tags") or "").split("|") if t.strip()
                    ],
                    "source_url": row.get("source_url"),
                }
            )
    return rows


def _match_score(item: dict[str, Any]) -> int:
    try:
        return int(item.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _score_across_locations(
    profile: dict[str, dict[str, Any]], area_loc_ids: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Score the shortlist once per place of performance, then merge.

    Program eligibility is geography-dependent, so a coalition working across
    several counties or states faces a different eligible set in each one.
    Scoring only a primary location would silently drop programs the project
    qualifies for everywhere else. Each merged entry records which locations
    produced it, so the caller can see what is and is not portable.
    """

    def score_for(loc_id: str | None, top_n: int) -> dict[str, Any]:
        return us_backend.score_and_rank_programs(
            org_type=_field_value(profile, "org_type"),
            sector_tags=_field_value(profile, "sector_tags") or None,
            recipient_loc_id=_field_value(profile, "recipient_loc_id"),
            place_of_performance_loc_id=loc_id,
            has_government_partner=bool(_field_value(profile, "has_government_partner")),
            has_match_funding=bool(_field_value(profile, "has_match_funding")),
            target_award_amount=_field_value(profile, "target_award_amount"),
            target_project_length_days=_field_value(profile, "target_project_length_days"),
            org_name=_field_value(profile, "org_name"),
            target_country_code=_field_value(profile, "target_country_code"),
            top_n=top_n,
            live_opportunity_fallback=False,
        )

    # Always pull a deeper slice than we display. Unreachable programs are
    # demoted by their access blocker, so a shallow cut buries them before the
    # caller can report why a good-fit program is not an option.
    if len(area_loc_ids) <= 1:
        return score_for(area_loc_ids[0] if area_loc_ids else None, PHASE_1_PER_LOCATION_TOP_N), []

    merged: dict[str, dict[str, Any]] = {}
    assistance: dict[str, dict[str, Any]] = {}
    breakdown: list[dict[str, Any]] = []
    for loc_id in area_loc_ids:
        scored = score_for(loc_id, PHASE_1_PER_LOCATION_TOP_N)
        matches = list(scored.get("top_matches") or [])
        breakdown.append(
            {
                "place_of_performance_loc_id": loc_id,
                "match_count": len(matches),
                "program_ids": [item.get("program_id") for item in matches],
            }
        )
        for item in matches:
            program_id = str(item.get("program_id") or "").strip()
            if not program_id:
                continue
            existing = merged.get(program_id)
            if existing is None:
                entry = deepcopy(item)
                entry["eligible_locations"] = [loc_id]
                merged[program_id] = entry
                continue
            locations = existing.get("eligible_locations") or []
            if loc_id not in locations:
                locations.append(loc_id)
            # Keep the strongest scoring instance, but carry the full location set.
            if _match_score(item) > _match_score(existing):
                replacement = deepcopy(item)
                replacement["eligible_locations"] = locations
                merged[program_id] = replacement
            else:
                existing["eligible_locations"] = locations
        for resource in scored.get("assistance_resources") or []:
            key = str(resource.get("program_id") or resource.get("program_name") or "")
            if key:
                assistance.setdefault(key, resource)

    # Truncation happens in the caller, after unreachable programs are split out.
    ranked = sorted(merged.values(), key=_match_score, reverse=True)
    return {"top_matches": ranked, "assistance_resources": list(assistance.values())}, breakdown


def _analyze_declared_targets(
    profile: dict[str, dict[str, Any]], scored_ids: set[str]
) -> list[dict[str, Any]]:
    """Fit and gap analysis for the funders the brief already names.

    Answers "how do I make this stronger for the places I already picked",
    which is a different question from "what else could I apply to". A named
    funder with no eligible program is the most useful thing to say early -
    it means the plan targets a funder that cannot fund this applicant.
    """
    declared = _extract_declared_funders(_field_value(profile, "project_description"))
    if not declared:
        return []

    org_type = _field_value(profile, "org_type")
    project_tags = set(_field_value(profile, "sector_tags") or [])
    results: list[dict[str, Any]] = []
    for entry in declared:
        programs = _programs_for_funder(entry["funder"])
        eligible, ineligible = [], []
        for program in programs:
            if org_type and program["eligible_applicant_types"] and org_type not in program["eligible_applicant_types"]:
                ineligible.append(program)
            else:
                eligible.append(program)
        surfaced = [p for p in eligible if p["program_id"] in scored_ids]
        not_surfaced = [p for p in eligible if p["program_id"] not in scored_ids]

        gaps: list[str] = []
        if not programs:
            gaps.append(
                f"{entry['funder']} is named in the brief but has no program rows in the "
                "catalog, so no eligibility or precedent check was possible here."
            )
        elif not eligible:
            gaps.append(
                f"No {entry['funder']} program in the catalog lists '{org_type}' as an "
                "eligible applicant type. Applying directly likely needs a different "
                "applicant of record or a government/nonprofit co-applicant."
            )
        for program in not_surfaced:
            missing = sorted(set(program["sector_tags"]) - project_tags)
            if missing:
                gaps.append(
                    f"{program['program_id']} is open to this applicant type but did not "
                    f"surface on sector fit. It is tagged {', '.join(missing)} - framing the "
                    "proposal in those terms would strengthen the match."
                )

        results.append(
            {
                "funder": entry["funder"],
                "named_in_brief_as": entry["matched_text"],
                "catalog_programs": len(programs),
                "eligible_programs": [p["program_id"] for p in eligible],
                "ineligible_programs": [p["program_id"] for p in ineligible],
                "already_in_shortlist": [p["program_id"] for p in surfaced],
                "eligible_but_not_surfaced": [p["program_id"] for p in not_surfaced],
                "how_to_strengthen": gaps,
            }
        )
    return results


def _analyze_alternative_framings(
    profile: dict[str, dict[str, Any]], area_loc_ids: list[str], shortlist_ids: set[str]
) -> list[dict[str, Any]]:
    """The same work, pitched differently, reaches different funders.

    Sector matching is an OR across tags, so scoring every tag at once returns
    one blended ranking dominated by whichever tag has the most programs. A
    debris-removal project is simultaneously wildfire mitigation, disaster
    recovery, wood products, and workforce development - each is a different
    application. Scoring one tag at a time shows what each pitch unlocks.
    """
    tags = list(_field_value(profile, "sector_tags") or [])
    if len(tags) < 2:
        return []
    primary_loc = area_loc_ids[0] if area_loc_ids else None

    framings: list[dict[str, Any]] = []
    for tag in tags:
        scored = us_backend.score_and_rank_programs(
            org_type=_field_value(profile, "org_type"),
            sector_tags=[tag],
            recipient_loc_id=_field_value(profile, "recipient_loc_id"),
            place_of_performance_loc_id=primary_loc,
            has_government_partner=bool(_field_value(profile, "has_government_partner")),
            has_match_funding=bool(_field_value(profile, "has_match_funding")),
            target_award_amount=_field_value(profile, "target_award_amount"),
            target_project_length_days=_field_value(profile, "target_project_length_days"),
            org_name=_field_value(profile, "org_name"),
            target_country_code=_field_value(profile, "target_country_code"),
            top_n=PHASE_1_PER_LOCATION_TOP_N,
            live_opportunity_fallback=False,
        )
        matches = list(scored.get("top_matches") or [])
        if not matches:
            continue
        new_ids = [m["program_id"] for m in matches if m["program_id"] not in shortlist_ids]
        framings.append(
            {
                "framing": tag,
                "match_count": len(matches),
                "top_programs": [
                    {
                        "program_id": m.get("program_id"),
                        "program_name": m.get("program_name"),
                        "score": m.get("score"),
                        "agency_or_funder": m.get("agency_or_funder"),
                    }
                    for m in matches[:3]
                ],
                "not_in_main_shortlist": new_ids[:5],
            }
        )
    # Framings that reach programs the blended shortlist missed are the useful ones.
    framings.sort(key=lambda f: (len(f["not_in_main_shortlist"]), f["match_count"]), reverse=True)
    return framings


def _run_phase_1(session: dict[str, Any], *, depth: str, top_n: int) -> dict[str, Any]:
    country_code = session["country_code"]
    profile = session["project_profile"]
    selected_steps: list[str] = []
    top_matches: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    key_blockers: list[str] = []
    precedent_summary: list[dict[str, Any]] = []
    open_now_summary: list[dict[str, Any]] = []
    regional_evidence: list[dict[str, Any]] = []
    limitations: list[str] = []
    location_breakdown: list[dict[str, Any]] = []
    no_application_path: list[dict[str, Any]] = []
    historical_precedent: list[dict[str, Any]] = []
    open_now: list[dict[str, Any]] = []
    declared_targets: list[dict[str, Any]] = []
    alternative_framings: list[dict[str, Any]] = []
    localization = _localization_summary(profile)
    selected_steps.append("localize_project_area")

    if country_code == "US":
        area_loc_ids = _project_area_loc_ids(profile)
        scored, location_breakdown = _score_across_locations(profile, area_loc_ids)
        selected_steps.extend(["match_grant_programs", "score_and_rank_programs"])
        if location_breakdown:
            selected_steps.append("score_each_place_of_performance")
            limitations.append(
                "Eligibility was scored separately for each place of performance. "
                "A program listed for one location is not automatically available in "
                "the others - check eligible_locations on each match."
            )
        ranked_matches = list(scored.get("top_matches") or [])
        session["current_scored_results"] = scored

        # A program with no application path is not a shortlist option, however
        # well it scores on fit. Split before truncating: the access blocker
        # already demotes these, so a shallow cut would bury them before we
        # could report why a good-fit program is not actually available.
        unreachable = [m for m in ranked_matches if m.get("access_path") in UNREACHABLE_ACCESS_PATHS]
        reachable = [m for m in ranked_matches if m.get("access_path") not in UNREACHABLE_ACCESS_PATHS]

        # Programs that are over are separated rather than dropped. They cannot
        # be applied to, but they are the best available evidence of what this
        # funder actually funded, which is what phase 2 writing work needs.
        historical = [
            m for m in reachable if m.get("lifecycle_status") in HISTORICAL_LIFECYCLE_STATUSES
        ]
        current = [
            m for m in reachable if m.get("lifecycle_status") not in HISTORICAL_LIFECYCLE_STATUSES
        ]

        top_matches = current[:top_n]
        session["current_target_programs"] = deepcopy(top_matches)

        if historical:
            historical_precedent = [
                {
                    "program_id": m.get("program_id"),
                    "program_name": m.get("program_name"),
                    "score": m.get("score"),
                    "lifecycle_status": m.get("lifecycle_status"),
                    "why_not_a_target": m.get("lifecycle_notes"),
                    "precedent_confidence": m.get("precedent_confidence"),
                    "use_for": (
                        "Comparison and writing reference - what this funder actually "
                        "funded. Not an application target."
                    ),
                    "source_url": m.get("source_url"),
                }
                for m in historical[:PHASE_1_MERGED_TOP_N]
            ]
            selected_steps.append("separate_historical_programs")
            limitations.append(
                f"{len(historical)} program(s) match this project but are closed, cancelled, "
                "or one-time appropriations - see historical_precedent. Use them as writing "
                "reference, not as targets."
            )

        # "recurring" means it comes back; "open" means there is something to
        # apply to today. Only the latter carries a live deadline.
        open_now = [
            {
                "program_id": m.get("program_id"),
                "program_name": m.get("program_name"),
                "score": m.get("score"),
                "why": m.get("lifecycle_notes"),
                "source_url": m.get("source_url"),
            }
            for m in top_matches
            if m.get("lifecycle_status") == OPEN_LIFECYCLE_STATUS
        ]
        if open_now:
            selected_steps.append("flag_open_opportunities")

        if unreachable:
            no_application_path = [
                {
                    "program_id": m.get("program_id"),
                    "program_name": m.get("program_name"),
                    "score": m.get("score"),
                    "access_path": m.get("access_path"),
                    "why": m.get("access_notes") or (m.get("blockers") or [None])[-1],
                    "source_url": m.get("source_url"),
                }
                for m in unreachable[:PHASE_1_MERGED_TOP_N]
            ]
            selected_steps.append("separate_unreachable_programs")
            limitations.append(
                f"{len(unreachable)} program(s) fit this project but have no application path "
                "for this applicant - see no_application_path for the reason on each. They are "
                "excluded from the shortlist rather than ranked in it."
            )

        blocked = [item for item in top_matches if item.get("blockers")]
        key_blockers = _unique_strings(
            blocker
            for item in top_matches
            for blocker in (item.get("blockers") or [])
        )
        precedent_summary = [
            {
                "program_id": item.get("program_id"),
                "program_name": item.get("program_name"),
                "precedent_confidence": item.get("precedent_confidence"),
            }
            for item in top_matches
        ]
        open_now_summary = [
            {
                "program_id": item.get("program_id"),
                "is_currently_open": ((item.get("open_opportunity") or {}).get("is_currently_open")),
                "funding_mechanism": _first_opportunity_mechanism(item.get("open_opportunity") or {}),
            }
            for item in top_matches
            if item.get("open_opportunity") is not None
        ]
        shortlist_ids = {str(m.get("program_id")) for m in top_matches}
        declared_targets = _analyze_declared_targets(profile, shortlist_ids)
        if declared_targets:
            selected_steps.append("analyze_declared_targets")
        if depth == "full":
            alternative_framings = _analyze_alternative_framings(
                profile, area_loc_ids, shortlist_ids
            )
            if alternative_framings:
                selected_steps.append("analyze_alternative_framings")

        sector_tags = _field_value(profile, "sector_tags") or None
        if _is_california_profile(profile):
            selected_steps.append("match_california_state_evidence")
            california_open = us_backend.find_california_open_opportunities(
                sector_tags=sector_tags,
                max_results=5,
            )
            california_awards = us_backend.get_california_award_precedents(
                sector_tags=sector_tags,
                max_examples=3,
            )
            regional_evidence.extend([california_open, california_awards])
            limitations.append(
                "California state evidence is reported separately from federal readiness scoring."
            )
        california_program_evidence = _california_curated_program_evidence(profile)
        if california_program_evidence is not None:
            selected_steps.append("match_california_curated_programs")
            regional_evidence.append(california_program_evidence)
            limitations.append(
                "California curated program leads are state pathways shown separately from federal scoring."
            )
        arc_program_evidence = _arc_curated_program_evidence(profile)
        if arc_program_evidence is not None:
            selected_steps.append("match_arc_curated_programs")
            regional_evidence.append(arc_program_evidence)
            limitations.append(
                "ARC curated program leads are regional pathways shown separately from federal scoring."
            )
        virginia_program_evidence = _virginia_curated_program_evidence(profile)
        if virginia_program_evidence is not None:
            selected_steps.append("match_virginia_curated_programs")
            regional_evidence.append(virginia_program_evidence)
            limitations.append(
                "Virginia curated program leads are local pathways shown separately from federal scoring."
            )
        arc_evidence = _arc_live_update_evidence(profile)
        if arc_evidence is not None:
            selected_steps.append("read_arc_live_announcements")
            regional_evidence.append(arc_evidence)
            limitations.append(
                "ARC live announcements are current leads; historical coverage starts from observed collector runs."
            )
    elif country_code == "CAN":
        province = _province_from_loc_id(_field_value(profile, "recipient_loc_id"))
        matches = can_backend.match_can_grant_programs(
            org_type=_field_value(profile, "org_type"),
            sector_tags=_field_value(profile, "sector_tags") or None,
            province=province,
            has_government_partner=bool(_field_value(profile, "has_government_partner")),
            has_match_funding=bool(_field_value(profile, "has_match_funding")),
        )
        selected_steps.append("match_can_grant_programs")
        top_matches = list(matches[:top_n])
        session["current_target_programs"] = deepcopy(top_matches)
        open_now_summary = _canada_program_page_checks(top_matches)
        if open_now_summary:
            selected_steps.append("build_canada_program_page_checks")
        blocked = [item for item in top_matches if item.get("blockers")]
        key_blockers = _unique_strings(
            blocker
            for item in top_matches
            for blocker in (item.get("blockers") or [])
        )
        if depth == "full":
            selected_steps.append("get_can_precedent")
            for item in top_matches[:3]:
                precedent = can_backend.get_can_precedent(str(item.get("program_id") or ""))
                precedent_summary.append(
                    {
                        "program_id": item.get("program_id"),
                        "program_name": item.get("program_name"),
                        "found": precedent.get("found"),
                        "award_count": precedent.get("award_count"),
                    }
                )
        if province == "BC":
            selected_steps.append("match_bc_provincial_award_evidence")
            regional_evidence.append(can_backend.get_bc_award_precedents(
                sector_tags=_field_value(profile, "sector_tags") or None,
                max_examples=3,
            ))
            limitations.append(
                "BC provincial award evidence is reported separately from federal Canada G&C precedent."
            )
        limitations.append(
            "Canada open-option links are official program pages to check; the Canada backend does not yet verify per-program open/closed status like Grants.gov."
        )
    else:
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_UNSUPPORTED_COUNTRY,
            "session_id": session["session_id"],
            "phase_run": PHASE_1,
            "country_code": country_code,
        }

    return {
        "tool_name": "grant_analyze_current_phase",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "phase_run": PHASE_1,
        "selected_workflow_steps": selected_steps,
        "top_matches": top_matches,
        "blocked_but_workable_matches": blocked,
        "key_blockers": key_blockers,
        "precedent_summary": precedent_summary,
        "open_now_summary": open_now_summary,
        "localization_summary": localization,
        "location_breakdown": location_breakdown,
        "no_application_path": no_application_path,
        "historical_precedent": historical_precedent,
        "open_now": open_now,
        "declared_targets": declared_targets,
        "alternative_framings": alternative_framings,
        "regional_evidence": regional_evidence,
        "recommended_next_step": {
            "type": "move_to_phase_2_or_select_target",
            "message": "Select one target program before memo and budget work.",
        },
        "limitations": limitations,
    }


def _run_phase_2(session: dict[str, Any], *, request_text: str, depth: str) -> dict[str, Any]:
    country_code = session["country_code"]
    if country_code != "US":
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_UNSUPPORTED_COUNTRY,
            "session_id": session["session_id"],
            "phase_run": PHASE_2,
            "country_code": country_code,
            "notes": [f"Phase 2 tools are not yet supported for country '{country_code}'."],
        }

    selected_program_id = session.get("current_selected_program_id")
    if not selected_program_id:
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_CLARIFICATION,
            "session_id": session["session_id"],
            "phase_run": PHASE_2,
            "questions": [
                {
                    "question_id": "select_target_program",
                    "prompt": "Which program from the current shortlist are you actively developing the proposal for?",
                    "fields_unblocked": ["current_selected_program_id"],
                }
            ],
            "resume_hint": "grant_analyze_current_phase",
        }

    request_lower = request_text.lower()
    selected_steps: list[str] = []
    memo_support = None
    budget_framework = None
    draft_structure_review = None
    precedent_text_support = None
    profile = session["project_profile"]

    if (
        session.get("current_scored_results") is None
        and _field_value(profile, "org_type")
    ):
        phase1_result = _run_phase_1(session, depth="standard")
        if phase1_result.get("outcome") != OUTCOME_OK:
            return phase1_result

    wants_budget = any(token in request_lower for token in ("budget", "sf-424", "sf424", "cost"))
    wants_draft = "draft" in request_lower or "broader impacts" in request_lower
    wants_precedent_text = "precedent text" in request_lower or "similar abstract" in request_lower

    if wants_budget:
        target_award_amount = _field_value(profile, "target_award_amount")
        if target_award_amount in (None, ""):
            return {
                "tool_name": "grant_analyze_current_phase",
                "outcome": OUTCOME_CLARIFICATION,
                "session_id": session["session_id"],
                "phase_run": PHASE_2,
                "questions": [
                    {
                        "question_id": "target_award_amount",
                        "prompt": "What target award amount are you planning around for the budget framework?",
                        "fields_unblocked": ["target_award_amount"],
                    }
                ],
                "resume_hint": "grant_analyze_current_phase",
            }
        selected_steps.append("build_budget_framework")
        budget_framework = us_backend.build_budget_framework(
            target_award_amount=target_award_amount,
            program_id=selected_program_id,
            target_project_length_days=_field_value(profile, "target_project_length_days"),
            has_negotiated_indirect_rate=False,
        )

    if wants_draft:
        selected_steps.append("score_proposal_structure")
        draft_text = request_text
        draft_structure_review = us_backend.score_proposal_structure(draft_text=draft_text, program_id=selected_program_id)

    if wants_precedent_text:
        selected_steps.append("find_similar_precedent_text")
        precedent_text_support = us_backend.find_similar_precedent_text(
            draft_text=request_text or (_field_value(profile, "project_description") or ""),
            source="nsf",
            keyword=None,
            top_n=3,
        )

    if not selected_steps or (not wants_budget and not wants_draft and not wants_precedent_text):
        selected_steps.append("build_memo_talking_points")
        memo_support = us_backend.build_memo_talking_points(session.get("current_scored_results") or {}, top_n=5)

    return {
        "tool_name": "grant_analyze_current_phase",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "phase_run": PHASE_2,
        "selected_program_id": selected_program_id,
        "selected_workflow_steps": selected_steps,
        "memo_support": memo_support,
        "budget_framework": budget_framework,
        "draft_structure_review": draft_structure_review,
        "precedent_text_support": precedent_text_support,
        "recommended_next_step": {
            "type": "confirm_submission_target",
            "message": "If this is the chosen target, the next step is submission-support confirmation.",
        },
        "limitations": [],
    }


def _run_phase_3(session: dict[str, Any], *, request_text: str) -> dict[str, Any]:
    country_code = session["country_code"]
    if country_code != "US":
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_UNSUPPORTED_COUNTRY,
            "session_id": session["session_id"],
            "phase_run": PHASE_3,
            "country_code": country_code,
            "notes": [f"Phase 3 tools are not yet supported for country '{country_code}'."],
        }

    selected_program_id = session.get("current_selected_program_id")
    if not selected_program_id:
        return {
            "tool_name": "grant_analyze_current_phase",
            "outcome": OUTCOME_CLARIFICATION,
            "session_id": session["session_id"],
            "phase_run": PHASE_3,
            "questions": [
                {
                    "question_id": "select_target_program",
                    "prompt": "Which program are you asking submission-support questions about?",
                    "fields_unblocked": ["current_selected_program_id"],
                }
            ],
            "resume_hint": "grant_analyze_current_phase",
        }

    cfda_code = _program_cfda_code_us(selected_program_id)
    if not cfda_code:
        return _error_result(
            "grant_analyze_current_phase",
            "missing_cfda_code",
            f"Could not resolve a CFDA code for program '{selected_program_id}'.",
            session_id=session["session_id"],
            phase_run=PHASE_3,
        )

    selected_steps = ["check_open_opportunity"]
    opportunity_status = us_backend.check_open_opportunity(cfda_code, live_fallback=False)
    funding_mechanism_summary = {
        "funding_mechanism": _first_opportunity_mechanism(opportunity_status),
        "is_currently_open": opportunity_status.get("is_currently_open"),
        "checked_live": opportunity_status.get("checked_live"),
    }

    return {
        "tool_name": "grant_analyze_current_phase",
        "outcome": OUTCOME_OK,
        "session_id": session["session_id"],
        "phase_run": PHASE_3,
        "selected_program_id": selected_program_id,
        "selected_workflow_steps": selected_steps,
        "opportunity_status": opportunity_status,
        "funding_mechanism_summary": funding_mechanism_summary,
        "submission_support_notes": [
            "Confirm the exact funding notice, deadline posture, and final submission requirements outside the tool."
        ],
        "review_process_limitations": [
            "The tool cannot observe internal agency panel or merit review outcomes."
        ],
        "recommended_next_step": {
            "type": "submission_confirmation",
            "message": "Confirm the exact funding notice, deadline posture, and final application requirements outside the tool.",
        },
    }


def _load_or_create_session(*, session_id: str | None, project_label: str | None) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id and normalized_session_id in _SESSIONS:
        session = deepcopy(_SESSIONS[normalized_session_id])
    else:
        normalized_session_id = normalized_session_id or f"gs_{uuid.uuid4().hex[:12]}"
        session = {
            "session_id": normalized_session_id,
            "project_label": project_label or "Untitled Grant Project",
            "current_phase": PHASE_1,
            "phase_confidence": 0.5,
            "country_code": None,
            "project_profile": _empty_profile(),
            "project_profile_missing_fields": [],
            "current_target_programs": [],
            "current_selected_program_id": None,
            "current_scored_results": None,
            "current_memo_context": None,
            "current_budget_context": None,
            "current_draft_context": None,
            "open_questions": [],
            "last_tool_outputs": [],
        }
    if project_label:
        session["project_label"] = project_label
    return session


def _empty_profile() -> dict[str, dict[str, Any]]:
    return {
        field_name: {"value": None, "status": FIELD_MISSING, "source": None}
        for field_name in PROJECT_PROFILE_FIELDS
    }


def _set_profile_field(session: dict[str, Any], field_name: str, value: Any, status: str, source: str) -> None:
    if field_name not in session["project_profile"]:
        return
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    session["project_profile"][field_name] = {
        "value": value,
        "status": status,
        "source": source,
    }


def _field_value(profile: dict[str, dict[str, Any]], field_name: str) -> Any:
    return (profile.get(field_name) or {}).get("value")


# match_grant_programs() gates on an exact string match against the CSV's
# eligible_applicant_types vocabulary, so a near-miss like "nonprofit" instead
# of "nonprofit_501c3" silently excludes all 127 programs rather than raising.
# Text inference already emits canonical values; explicit profile_overrides did
# not, which is the path real callers hit most. Keys are slugified user input.
US_ORG_TYPE_SYNONYMS = {
    "nonprofit": "nonprofit_501c3",
    "non_profit": "nonprofit_501c3",
    "nonprofit_organization": "nonprofit_501c3",
    "non_profit_organization": "nonprofit_501c3",
    "not_for_profit": "nonprofit_501c3",
    "charity": "nonprofit_501c3",
    "foundation": "nonprofit_501c3",
    "ngo": "nonprofit_501c3",
    "501c3": "nonprofit_501c3",
    "501_c_3": "nonprofit_501c3",
    "501c_3": "nonprofit_501c3",
    "university": "higher_ed",
    "college": "higher_ed",
    "academia": "higher_ed",
    "academic": "higher_ed",
    "higher_education": "higher_ed",
    "institution_of_higher_education": "higher_ed",
    "city": "local_government",
    "county": "local_government",
    "municipality": "local_government",
    "municipal": "local_government",
    "municipal_government": "local_government",
    "state_government": "state",
    "state_agency": "state",
    "tribe": "tribal_government",
    "tribal": "tribal_government",
    "business": "for_profit_business",
    "company": "for_profit_business",
    "for_profit": "for_profit_business",
    "startup": "small_business",
}

_ORG_TYPE_VOCABULARY_CACHE: set[str] | None = None


def _known_us_org_types() -> set[str]:
    global _ORG_TYPE_VOCABULARY_CACHE
    if _ORG_TYPE_VOCABULARY_CACHE is not None:
        return _ORG_TYPE_VOCABULARY_CACHE
    values: set[str] = set()
    try:
        df = us_backend.load_programs()
        for raw in df["eligible_applicant_types"].dropna():
            for token in str(raw).split("|"):
                token = token.strip()
                if token:
                    values.add(token)
    except Exception:
        values = set()
    _ORG_TYPE_VOCABULARY_CACHE = values
    return values


def _normalize_us_org_type(value: Any) -> tuple[str | None, str | None]:
    """Return (canonical_org_type, warning). Warning is set when nothing matches."""
    raw = str(value or "").strip()
    if not raw:
        return None, None
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    known = _known_us_org_types()
    if slug in known:
        return slug, None
    canonical = US_ORG_TYPE_SYNONYMS.get(slug)
    if canonical and (not known or canonical in known):
        return canonical, None
    if known:
        return slug, (
            f"org_type '{raw}' is not in the US eligibility vocabulary, so no program can "
            f"match it. Supported values include: {', '.join(sorted(known)[:10])}."
        )
    return slug, None


def _apply_vocabulary_normalization(session: dict[str, Any]) -> list[str]:
    """Snap free-text profile values onto the backend's exact-match vocabulary."""
    if str(session.get("country_code") or "").upper() != "US":
        return []
    profile = session["project_profile"]
    current = _field_value(profile, "org_type")
    if not current:
        return []
    canonical, warning = _normalize_us_org_type(current)
    if canonical and canonical != current:
        entry = profile.get("org_type") or {}
        _set_profile_field(
            session, "org_type", canonical, entry.get("status") or FIELD_INFERRED, "vocabulary_normalized"
        )
    return [warning] if warning else []


def _merge_project_description(session: dict[str, Any], text: str) -> None:
    """Accumulate the project narrative across intake calls.

    A follow-up call usually adds or corrects a few facts rather than restating
    the whole project, so overwriting would silently discard the original brief
    and leave later phases reasoning about a one-line correction note.
    """
    existing = str(_field_value(session["project_profile"], "project_description") or "").strip()
    new_text = str(text or "").strip()
    if not existing or existing in new_text:
        merged = new_text
    elif new_text in existing:
        merged = existing
    else:
        merged = f"{existing}\n\n{new_text}"
    _set_profile_field(session, "project_description", merged, FIELD_CONFIRMED, "project_input")


def _infer_backend_country(session: dict[str, Any], *, allow_inference: bool) -> str:
    recipient_loc_id = _field_value(session["project_profile"], "recipient_loc_id")
    if isinstance(recipient_loc_id, str) and recipient_loc_id.upper().startswith("USA"):
        return "US"
    if isinstance(recipient_loc_id, str) and recipient_loc_id.upper().startswith("CAN"):
        return "CAN"
    if not allow_inference:
        return "US"
    description = str(_field_value(session["project_profile"], "project_description") or "").lower()
    if any(token in description for token in (" british columbia", " alberta", " ontario", " canada", " canadian ")):
        return "CAN"
    return "US"


def _normalize_country_code(country_code: str) -> str:
    raw = str(country_code or "").strip().upper()
    if raw in {"USA", "US"}:
        return "US"
    if raw in {"CAN", "CA"}:
        return "CAN"
    return raw


def _looks_like_loc_id(value: Any) -> bool:
    return bool(re.match(r"^[A-Z]{3}(?:-[A-Z0-9]+)*$", str(value or "").strip().upper()))


def _normalize_location_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s,-]", " ", text)
    return " ".join(text.split())


def _grant_domain_alias(value: Any) -> dict[str, Any] | None:
    """Grant-specific place aliases the geometry tools cannot know about."""
    text = _normalize_location_text(value)
    if re.search(r"(gmu|george mason university)", text):
        return {
            "loc_id": "USA-VA-059",
            "matched_term": "George Mason University / Fairfax County",
            "match_type": "grant_domain_alias",
            "source": "grant_location_alias",
        }
    return None


def _resolve_location_text_to_loc_id(value: Any, *, country_hint: str | None = None) -> dict[str, Any] | None:
    """Place text to loc_id: a provided loc_id, a grant alias, then the
    DaedalMap geometry tools at GEOMETRY_MCP_URL (see geometry_client.py)."""
    text = str(value or "").strip()
    if not text:
        return None
    if _looks_like_loc_id(text):
        return {
            "loc_id": text.upper(),
            "matched_term": text,
            "match_type": "provided_loc_id",
            "source": "profile_override",
        }
    alias = _grant_domain_alias(text)
    if alias:
        return alias
    hint = _normalize_country_code(country_hint or "")
    return geometry_client.resolve_place(text, country=hint or None)


def _split_location_texts(value: Any) -> list[str]:
    """Split supplied place-of-performance text into individual place names.

    Accepts a real list, or one string separated by ';' or newlines. Commas are
    deliberately not separators: "Buncombe County, North Carolina" is one place,
    not two.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[;\n]+", str(value))
    return [part.strip() for part in raw if str(part).strip()]


def _normalize_loc_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else re.split(r"[;,\n]+", str(value))
    normalized: list[str] = []
    for item in items:
        loc_id = str(item or "").strip().upper()
        if loc_id and loc_id not in normalized:
            normalized.append(loc_id)
    return normalized


def _apply_location_text_overrides(session: dict[str, Any], *, country_code: str | None) -> None:
    """Resolve explicitly supplied location fields to loc_ids.

    Only ever reads the dedicated location fields, never the project narrative:
    a university or partner named in a project description is a collaborator,
    not a place of performance. Place of performance is multi-valued because a
    coalition working across several counties or states faces a different
    eligible program set in each one.
    """
    profile = session["project_profile"]
    hint = country_code or session.get("country_code")

    if not _field_value(profile, "recipient_loc_id"):
        resolved = _resolve_location_text_to_loc_id(
            _field_value(profile, "recipient_location_text"), country_hint=hint
        )
        if resolved:
            _set_profile_field(session, "recipient_loc_id", resolved["loc_id"], FIELD_INFERRED, resolved["source"])

    explicit_loc_ids = _normalize_loc_id_list(_field_value(profile, "project_area_loc_ids"))
    area_loc_ids = list(explicit_loc_ids)
    resolution_source = "profile_override"
    if not area_loc_ids:
        resolution_source = "location_text_resolution"
        for text in _split_location_texts(_field_value(profile, "project_area_text")):
            resolved = _resolve_location_text_to_loc_id(text, country_hint=hint)
            if resolved and resolved["loc_id"] not in area_loc_ids:
                area_loc_ids.append(resolved["loc_id"])
    if not area_loc_ids:
        primary = str(_field_value(profile, "place_of_performance_loc_id") or "").strip().upper()
        if primary:
            resolution_source = "profile_override"
            area_loc_ids = [primary]

    if not area_loc_ids:
        return

    _set_profile_field(
        session,
        "project_area_loc_ids",
        area_loc_ids,
        FIELD_CONFIRMED if explicit_loc_ids else FIELD_INFERRED,
        resolution_source,
    )
    if not _field_value(profile, "place_of_performance_loc_id"):
        _set_profile_field(
            session, "place_of_performance_loc_id", area_loc_ids[0], FIELD_INFERRED, resolution_source
        )


def _project_area_loc_ids(profile: dict[str, dict[str, Any]]) -> list[str]:
    """Every place of performance to evaluate, primary first."""
    loc_ids = _normalize_loc_id_list(_field_value(profile, "project_area_loc_ids"))
    primary = str(_field_value(profile, "place_of_performance_loc_id") or "").strip().upper()
    if primary and primary not in loc_ids:
        loc_ids.insert(0, primary)
    return loc_ids


def _backend_exists(country_code: str | None) -> bool:
    try:
        ga.get_backend(str(country_code or ""))
        return True
    except Exception:
        return False


def _apply_inferred_profile_fields(session: dict[str, Any], text: str) -> None:
    text_lower = text.lower()
    if _field_value(session["project_profile"], "org_type") in (None, ""):
        inferred_org_type = _infer_org_type(text_lower, country_code=session.get("country_code"))
        if inferred_org_type:
            _set_profile_field(session, "org_type", inferred_org_type, FIELD_INFERRED, "text_inference")

    if _field_value(session["project_profile"], "sector_tags") in (None, ""):
        sector_tags = _infer_sector_tags(text_lower)
        if sector_tags:
            _set_profile_field(session, "sector_tags", sector_tags, FIELD_INFERRED, "text_inference")

    if _field_value(session["project_profile"], "has_government_partner") in (None, ""):
        if "government partner" in text_lower or "state partner" in text_lower or "city partner" in text_lower:
            _set_profile_field(session, "has_government_partner", True, FIELD_INFERRED, "text_inference")

    if _field_value(session["project_profile"], "has_match_funding") in (None, ""):
        if "match funding" in text_lower and any(token in text_lower for token in ("have", "secured", "confirmed")):
            _set_profile_field(session, "has_match_funding", True, FIELD_INFERRED, "text_inference")


def _infer_org_type(text_lower: str, country_code: str | None = None) -> str | None:
    country = str(country_code or "").strip().upper()
    if country == "CAN":
        mapping = (
            ("university", "academia"),
            ("college", "academia"),
            ("research hospital", "academia"),
            ("academic", "academia"),
            ("nonprofit", "nonprofit"),
            ("not-for-profit", "nonprofit"),
            ("first nation", "indigenous"),
            ("indigenous", "indigenous"),
            ("municipality", "government"),
            ("municipal", "government"),
            ("local government", "government"),
            ("city ", "government"),
            ("small business", "for_profit"),
            ("company", "for_profit"),
        )
    else:
        mapping = (
        ("501c3", "nonprofit_501c3"),
        ("nonprofit", "nonprofit_501c3"),
        ("university", "higher_ed"),
        ("college", "higher_ed"),
        ("research institute", "nonprofit_research_institute"),
        ("tribe", "tribal_government"),
        ("city ", "local_government"),
        ("county ", "local_government"),
        ("local government", "local_government"),
        ("state agency", "state"),
        ("small business", "small_business"),
        )
    for token, org_type in mapping:
        if token in text_lower:
            return org_type
    return None


def _infer_sector_tags(text_lower: str) -> list[str]:
    mapping = {
        "disaster_recovery": ("disaster recovery", "recovery", "reconstruction"),
        "hazard_mitigation": ("hazard mitigation", "mitigation"),
        "community_resilience": ("resilience",),
        "climate_adaptation": ("climate", "adaptation"),
        "green_infrastructure": ("green infrastructure",),
        "wildfire": ("wildfire", "fuel reduction"),
        "wildfire_management": ("wildfire", "fuel reduction"),
        "flood_mitigation": ("flood", "flooding"),
        "forestry": ("forestry", "forest", "wood products", "biochar"),
        "food_security": ("food system", "food security", "agriculture", "agtech", "farm", "grower"),
        "rural_economic_development": ("rural economic", "rural community", "agtech", "farm", "grower"),
        "innovation_ecosystem": ("innovation ecosystem", "startup", "startups", "agtech", "field trial"),
        "economic_development": ("economic development", "workforce", "entrepreneur", "startup"),
        "workforce": ("workforce", "job training", "worker training", "employment training"),
        "broadband": ("broadband", "internet access", "digital equity", "connectivity"),
        "transportation": ("transportation", "road", "roads", "access road", "mobility"),
        "access_to_capital": ("access to capital", "revolving loan", "credit gap", "capital gap"),
        "entrepreneurship": ("entrepreneurship", "entrepreneur", "small business"),
        "energy": ("energy", "grid", "hydrogen", "natural gas", "renewable"),
        "regional_collaboration": ("multi-state", "regional collaboration", "cross-state"),
        "commercialization": ("commercialization", "commercialize", "proof of concept", "prototype"),
        "sbir_sttr": ("sbir", "sttr"),
        "public_safety": ("public safety", "first responder", "first responders"),
        "emergency_management": ("emergency management", "emergency responder", "emergency responders"),
        "international_development": ("international development", "usaid", "foreign assistance"),
        "humanitarian": ("humanitarian", "refugee"),
        "research": ("research",),
        "research_software": ("research software", "software platform", "software"),
        "data_infrastructure": ("data infrastructure", "data platform", "data science", "datasets"),
        "geospatial_analytics": ("geospatial", "mapping", "map", "remote sensing"),
        "ai_data_science": ("ai", "machine learning", "data science"),
        "capacity_building": ("capacity building",),
    }
    tags = [tag for tag, tokens in mapping.items() if any(token in text_lower for token in tokens)]
    return sorted(set(tags))


def _infer_phase(text: str, *, phase_hint: str | None, existing_phase: str | None) -> tuple[str, float]:
    if phase_hint:
        return phase_hint, 1.0
    text_lower = str(text or "").lower()
    if any(token in text_lower for token in ("post-award", "compliance", "closeout", "reporting", "reimbursement", "audit")):
        return OUT_OF_SCOPE, 0.95
    if any(token in text_lower for token in ("memo", "budget", "draft", "proposal structure", "broader impacts")):
        return PHASE_2, 0.9
    if any(token in text_lower for token in ("deadline", "is it open", "nofo", "aps", "baa", "submit", "submission")):
        return PHASE_3, 0.9
    if text_lower.strip():
        return PHASE_1, 0.75
    return existing_phase or AMBIGUOUS, 0.5


def _compute_missing_fields(phase_id: str, session: dict[str, Any]) -> list[str]:
    profile = session["project_profile"]
    missing: list[str] = []

    if phase_id == PHASE_1:
        if not _field_value(profile, "org_type"):
            missing.append("org_type")
        if not _field_value(profile, "recipient_loc_id") and not _field_value(profile, "target_country_code"):
            missing.append("recipient_loc_id")
        if not _project_area_loc_ids(profile):
            missing.append("place_of_performance_loc_id")
    elif phase_id == PHASE_2:
        if not session.get("current_selected_program_id"):
            missing.append("current_selected_program_id")
    elif phase_id == PHASE_3:
        if not session.get("current_selected_program_id"):
            missing.append("current_selected_program_id")
    return missing


def _build_questions_for_missing_fields(phase_id: str, missing_fields: list[str], session: dict[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    missing = set(missing_fields)
    if phase_id == PHASE_1 and "place_of_performance_loc_id" in missing:
        questions.append(
            {
                "question_id": "place_of_performance",
                "prompt": (
                    "Where will the funded work actually be carried out? List every county, "
                    "state, or territory where activity will happen, not the location of "
                    "partners or universities named in the description. Each place is scored "
                    "separately because eligibility differs by location. Answer with "
                    "profile_overrides.project_area_text (semicolon-separated for several "
                    "places) or profile_overrides.project_area_loc_ids."
                ),
                "fields_unblocked": ["place_of_performance_loc_id", "project_area_loc_ids"],
            }
        )
    if phase_id == PHASE_1 and "recipient_loc_id" in missing:
        questions.append(
            {
                "question_id": "recipient_location",
                "prompt": (
                    "Where is the applying organization itself based? This can differ from "
                    "where the work happens and is used for applicant eligibility."
                ),
                "fields_unblocked": ["recipient_loc_id"],
            }
        )
    if "org_type" in missing:
        questions.append(
            {
                "question_id": "org_type",
                "prompt": "What kind of applicant is this organization, for example nonprofit, higher ed, tribal government, or local government?",
                "fields_unblocked": ["org_type"],
            }
        )
    if "current_selected_program_id" in missing:
        targets = session.get("current_target_programs") or []
        target_names = [str(item.get("program_name") or item.get("program_id") or "") for item in targets[:5]]
        questions.append(
            {
                "question_id": "select_target_program",
                "prompt": "Which program from the current shortlist are you actively working on now?"
                + (f" Current shortlist: {', '.join(target_names)}." if target_names else ""),
                "fields_unblocked": ["current_selected_program_id"],
            }
        )
    return questions


def _merge_request_text_into_profile(session: dict[str, Any], request_text: str) -> None:
    if not request_text:
        return
    current_description = str(_field_value(session["project_profile"], "project_description") or "").strip()
    merged = request_text if not current_description else f"{current_description}\n\nFollow-up: {request_text}"
    _set_profile_field(session, "project_description", merged, FIELD_CONFIRMED, "session_merge")


def _province_from_loc_id(loc_id: str | None) -> str | None:
    raw = str(loc_id or "").strip().upper()
    if raw.startswith("CAN-"):
        parts = raw.split("-")
        if len(parts) >= 2:
            return parts[1]
    return None


def _first_opportunity_mechanism(open_opportunity: dict[str, Any]) -> str | None:
    opportunities = open_opportunity.get("opportunities") or []
    if opportunities and isinstance(opportunities[0], dict):
        return opportunities[0].get("funding_mechanism")
    return None


def _canada_program_page_checks(top_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    for item in top_matches:
        source_url = str(item.get("source_url") or "").strip()
        if not source_url:
            continue
        checks.append({
            "program_id": item.get("program_id"),
            "program_name": item.get("program_name"),
            "source_url": source_url,
            "opportunity_status": "program_page_check_required",
            "is_currently_open": None,
            "directly_eligible": item.get("directly_eligible"),
            "matched_sector_tags": item.get("matched_sector_tags") or [],
            "blockers": item.get("blockers") or [],
            "check_next": (
                "Open the official program page and confirm the current competition window, "
                "internal university deadline, partner requirements, and application instructions."
            ),
        })
    return checks


def _program_cfda_code_us(program_id: str) -> str | None:
    df = us_backend.load_programs()
    match = df[df["program_id"] == program_id]
    if match.empty:
        return None
    cfda_value = str(match.iloc[0]["cfda_number"] or "").strip()
    if not cfda_value:
        return None
    return cfda_value.split("|")[0].strip()


def _unique_strings(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _error_result(tool_name: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "tool_name": tool_name,
        "outcome": OUTCOME_ERROR,
        "error": {"code": code, "message": message},
    }
    payload.update(extra)
    return payload
