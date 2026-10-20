"""MCP-readiness check for grant program rows.

schema.py checks that a CSV is structurally valid (columns present, enums
legal, no duplicate ids). This checks something different and harder: is a
program actually usable by the MCP, given the lessons this vertical learned
the expensive way.

Each rule here is a bug we hit in production, turned into a gate:

  - org_type gate. match_grant_programs filters eligible_applicant_types by
    EXACT string. "nonprofit" instead of "nonprofit_501c3" matched zero of 127
    programs silently. A token used by only this program is either a typo or a
    new applicant type that needs a matching rule (see test_org_type_coverage).

  - geography must be verifiable. A geography_scope with no backend matcher
    degrades every match to "unknown, can't verify" without saying so. A
    regional scope must have a GEOGRAPHY_STATE_MAP entry.

  - CFDA is the master key. With it, precedent scoring and Grants.gov lifecycle
    both work; without it a federal program is inert. Missing CFDA on a federal
    row is worth flagging (state/foundation rows legitimately lack one).

  - access_path and lifecycle_status must be legal values, or the phase-1
    splits that depend on them silently misfile the program.

ERROR blocks a row from being MCP-ready. WARN is a quality signal. INFO is
coverage (has a section list, has a CFDA) - not a defect.

Usage:
    python validate_program.py                 # check every row
    python validate_program.py <program_id>    # check one
    python validate_program.py --strict         # exit 1 on WARN too
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_backend  # noqa: E402
from schema import validate_programs_df, SchemaError  # noqa: E402

VALID_ACCESS_PATHS = {
    "direct", "state_routed", "invitation_only", "loi_required",
    "partnership_required", "not_an_application", "",
}
VALID_LIFECYCLE = {
    "open", "recurring", "historical_only", "one_time", "cancelled", "unknown", "",
}
# geography_scope values the US backend actually resolves. Keep in sync with the
# geography handling in us_backend (_sub_geography and GEOGRAPHY_STATE_MAP).
HANDLED_GEOGRAPHY_SCOPES = {
    "national", "international", "international_frontline_communities",
    "regional", "rural", "puerto_rico_usvi",
}


def _clean(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"nan", "none"} else text


def _known_org_types(df: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in df["eligible_applicant_types"].dropna():
        for token in str(raw).split("|"):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    return counts


def _known_sector_tags(df: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in df["sector_tags"].dropna():
        for token in str(raw).split("|"):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    return counts


def _template_program_ids() -> set[str]:
    try:
        t = pd.read_csv(us_backend.TEMPLATES_CSV, dtype=str)
    except Exception:
        return set()
    ids: set[str] = set()
    for raw in t["applies_to_program_ids"].dropna():
        for pid in str(raw).split("|"):
            pid = pid.strip()
            if pid and pid != "default":
                ids.add(pid)
    return ids


def check_row(row: dict, org_types: dict[str, int], sector_tags: dict[str, int],
              template_ids: set[str]) -> list[tuple[str, str]]:
    """Return a list of (level, message). ERROR/WARN/INFO."""
    out: list[tuple[str, str]] = []
    pid = _clean(row.get("program_id"))

    # org_type gate - the exact-match lesson.
    applicant_tokens = [t.strip() for t in _clean(row.get("eligible_applicant_types")).split("|") if t.strip()]
    if not applicant_tokens:
        out.append(("ERROR", "no eligible_applicant_types - nothing can match this program"))
    for token in applicant_tokens:
        if org_types.get(token, 0) <= 1:
            out.append((
                "WARN",
                f"applicant type '{token}' is used only by this program. If it is a typo it "
                "silently matches nothing; if it is a real new type it needs a matching rule "
                "(see test_org_type_coverage.py)",
            ))

    # geography must be verifiable.
    scope = _clean(row.get("geography_scope"))
    if not scope:
        out.append(("ERROR", "no geography_scope"))
    elif scope not in HANDLED_GEOGRAPHY_SCOPES and scope not in us_backend.GEOGRAPHY_STATE_MAP:
        out.append((
            "ERROR",
            f"geography_scope '{scope}' has no backend matcher. Add it to "
            "GEOGRAPHY_STATE_MAP (or the handled-scope set) before using it, or matches "
            "degrade to 'unknown' silently",
        ))

    # sector tags - typo protection.
    for token in [t.strip() for t in _clean(row.get("sector_tags")).split("|") if t.strip()]:
        if sector_tags.get(token, 0) <= 1:
            out.append(("WARN", f"sector tag '{token}' is used only by this program - possible typo"))

    # enums that the phase-1 splits depend on.
    access = _clean(row.get("access_path")).lower()
    if access and access not in VALID_ACCESS_PATHS:
        out.append(("ERROR", f"access_path '{access}' is not a valid value {sorted(VALID_ACCESS_PATHS - {''})}"))
    lifecycle = _clean(row.get("lifecycle_status")).lower()
    if lifecycle and lifecycle not in VALID_LIFECYCLE:
        out.append(("ERROR", f"lifecycle_status '{lifecycle}' is not a valid value {sorted(VALID_LIFECYCLE - {''})}"))

    # citation.
    if not _clean(row.get("source_url")):
        out.append(("WARN", "no source_url - requirements from this row cannot be attributed"))

    # coverage signals (not defects).
    funder_type = _clean(row.get("funder_type")).lower()
    cfda = _clean(row.get("cfda_number"))
    if not cfda and funder_type in {"federal", "defense_international"}:
        out.append((
            "INFO",
            "federal program with no cfda_number - precedent scoring and Grants.gov "
            "lifecycle both need it; map it from the award data if one exists",
        ))
    if pid not in template_ids:
        out.append(("INFO", "no writing template - falls back to the funder_type default section set"))

    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv

    df = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)

    # Structural gate first - if the CSV is malformed, stop here.
    try:
        validate_programs_df(df, "grant_funding_programs.csv")
    except SchemaError as exc:
        print(f"SCHEMA FAILURE: {exc}")
        return 1

    org_types = _known_org_types(df)
    sector_tags = _known_sector_tags(df)
    template_ids = _template_program_ids()

    rows = df.to_dict("records")
    if args:
        wanted = set(args)
        rows = [r for r in rows if _clean(r.get("program_id")) in wanted]
        if not rows:
            print(f"no program row found for: {sorted(wanted)}")
            return 1

    totals = {"ERROR": 0, "WARN": 0, "INFO": 0}
    flagged = 0
    for row in rows:
        findings = check_row(row, org_types, sector_tags, template_ids)
        real = [f for f in findings if f[0] != "INFO"]
        if not real and not args:
            continue  # in bulk mode, only print rows with something to fix
        flagged += 1
        print(f"\n{_clean(row.get('program_id'))}")
        for level, msg in findings:
            totals[level] += 1
            print(f"   [{level}] {msg}")

    print(
        f"\nchecked {len(rows)} row(s): "
        f"{totals['ERROR']} error, {totals['WARN']} warn, {totals['INFO']} info"
        + (f" across {flagged} flagged rows" if not args else "")
    )
    if totals["ERROR"]:
        return 1
    if strict and totals["WARN"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
