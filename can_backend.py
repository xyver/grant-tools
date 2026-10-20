"""
Canada Grant Backend - deterministic backend functions for the Canadian
grant-matching vertical.

Built alongside us_backend.py (2026-06-29) as part of a country-adapter
refactor. Deliberately narrower than the US backend - see CAPABILITIES
below. Canada has real discovery and precedent data (the four parquets
under county-map-data/countries/CAN/, plus a hand-curated
eligibility table built the same way grant_funding_programs.csv was) but
no readiness-scoring function, no agency-verified writing templates, and
no per-program live-opportunity check (Canada has no Grants.gov
equivalent - see Canada%20grant%20research.md). Calling an unsupported
function raises NotImplementedError with a clear message rather than
returning a confident-looking empty/zero result - "not supported yet" and
"checked, found nothing" must never look the same.

No formal shared interface with us_backend.py exists yet (e.g. a
CountryGrantBackend base class) - with only one full implementation and
one partial one, designing a shared contract now would mean guessing at
a boundary instead of finding it from two real examples. Revisit once
this backend gets a real scoring function.

Schema differences from the US backend, real not cosmetic (see
Canada%20grant%20research.md for the full reasoning):
- No CFDA-style program code - programs are keyed by program_id (a
  hand-assigned slug) plus a real owner_org/program_name join back to
  program_summary.parquet for precedent.
- eligible_applicant_types uses Canada's real controlled vocabulary
  (indigenous, for_profit, government, international_ngo, nonprofit,
  other, individual_sole_proprietorship, academia) confirmed from
  open.canada.ca's live data dictionary - not the US vocabulary.
- requires_match_funding is UNKNOWN on every single row today - no
  Canadian program's actual terms/NOFO-equivalent text has been read yet.
  This is the tri-state case tristate.py exists to handle correctly: it
  must reduce confidence and emit an action item, not silently read as
  "no match funding required."
- geography_scope only has a small set of real values in use (national,
  atlantic_canada, bc, quebec) - province-level only, since Canada's grant
  disclosure data has no county/CD-equivalent location field at all
  (confirmed during research).
"""
import functools
import re
import pandas as pd

import cloud_data
from tristate import parse_tristate  # noqa: E402
from schema import validate_programs_df  # noqa: E402
from runtime_paths import DATA_ROOT, DOCS_DIR  # noqa: E402

PROGRAMS_CSV = DOCS_DIR / "canada_grant_funding_programs.csv"
PROGRAM_SUMMARY_PARQUET = DATA_ROOT / "countries" / "CAN" / "grants_contributions" / "program_summary.parquet"
BC_AWARDS_PARQUET = DATA_ROOT / "countries" / "CAN" / "bc_grant_awards" / "awards.parquet"

# Real, current capability set - deliberately narrower than the US
# backend's. Checked here, not discovered by exception, so a caller (or
# grant_analyzer.py's coordinator) can ask "what does this backend
# actually support" before calling something that doesn't exist yet.
CAPABILITIES = {
    "discovery": True,
    "precedent": True,
    "readiness_scoring": False,
    "live_opportunity_check": False,  # canada_grant_announcements is an
                                       # unstructured announcement feed,
                                       # not a per-program open/closed
                                       # lookup - genuinely not the same
                                       # capability as US's Grants.gov check.
    "writing_template": False,
}

# Real province/territory geography scopes currently in use - province-
# level only, see module docstring.
GEOGRAPHY_PROVINCE_MAP = {
    "atlantic_canada": {"NB", "NS", "PE", "NL"},
    "bc": {"BC"},
    "quebec": {"QC"},
}


@functools.lru_cache(maxsize=1)
def _load_programs_cached():
    df = pd.read_csv(PROGRAMS_CSV, dtype=str)
    validate_programs_df(df, "canada_grant_funding_programs.csv")
    return df


@functools.lru_cache(maxsize=1)
def _load_program_summary_cached():
    return cloud_data.read_parquet(PROGRAM_SUMMARY_PARQUET)


@functools.lru_cache(maxsize=1)
def _load_bc_awards_cached():
    if not cloud_data.exists(BC_AWARDS_PARQUET):
        return None
    return cloud_data.read_parquet(BC_AWARDS_PARQUET)


def clear_caches():
    """Call after canada_grant_funding_programs.csv or program_summary.parquet
    change on disk, in a long-running process. See us_backend.clear_caches()
    for the same reasoning."""
    for fn in (_load_programs_cached, _load_program_summary_cached, _load_bc_awards_cached):
        fn.cache_clear()


def load_programs():
    return _load_programs_cached()


def _split_pipe(value):
    if pd.isna(value) or not str(value).strip():
        return []
    return [v.strip() for v in str(value).split("|") if v.strip()]


def _normalize_program_name(name):
    """Order/punctuation-tolerant normalization for matching a CSV
    program_name against program_summary.parquet's prog_name_en.

    Real problem this solves: the disclosure data spells the same program
    multiple ways - "(DFAA) Disaster Financial Assistance Arrangements"
    vs. "Disaster Financial Assistance Arrangements (DFAA)" vs.
    "DFAA - Disaster Financial Assistance Arrangements" all need to match
    each other and the CSV's canonical name. Confirmed live against real
    data (2026-06-29): stripping a leading "ABBR - " prefix and any
    parenthetical content, then comparing case-insensitive substrings
    either direction, correctly recovers all 4 DFAA variants (72 awards,
    $1.80B) and all 6 NDMP variants (91 awards, $189.7M) as one program
    each - the same totals computed by hand during the eligibility-table
    triage. A naive exact-string or fixed-substring match misses these.
    """
    text = re.sub(r"^[A-Za-z]{2,6}\s*-\s*", "", str(name))
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _geography_matches(geography_scope, province):
    if geography_scope == "national":
        return True, None
    allowed = GEOGRAPHY_PROVINCE_MAP.get(geography_scope)
    if allowed is None:
        return None, f"Unknown geography_scope '{geography_scope}' - not in GEOGRAPHY_PROVINCE_MAP, can't verify"
    if not province:
        return None, "No province given - can't verify geography match"
    return (province.upper() in allowed), None


BC_SECTOR_KEYWORDS = {
    "wildfire_management": ["wildfire", "fire", "public safety", "emergency", "resilience"],
    "flood_mitigation": ["flood", "emergency", "public safety", "infrastructure", "resilience"],
    "community_resilience": ["community", "public safety", "human and social services", "resilience"],
    "arts_culture": ["arts", "culture", "festival", "music", "performance"],
    "sports_recreation": ["sport", "sports", "recreation", "youth"],
    "research": ["research", "infrastructure", "science", "technology", "innovation"],
    "infrastructure": ["infrastructure", "capital", "facility", "project"],
    "economic_development": ["economic", "community", "development", "innovation"],
}


def _bc_keywords(sector_tags=None, keyword=None):
    terms = []
    for tag in sector_tags or []:
        tag_text = str(tag or "").strip()
        if not tag_text:
            continue
        terms.extend(BC_SECTOR_KEYWORDS.get(tag_text, []))
        terms.append(tag_text.replace("_", " "))
    if keyword:
        terms.append(str(keyword))
    seen = []
    for term in terms:
        cleaned = str(term or "").strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def _frame_text_mask(df, columns, terms):
    if not terms:
        return pd.Series(True, index=df.index)
    text = pd.Series("", index=df.index, dtype="object")
    for col in columns:
        if col in df.columns:
            text = text + " " + df[col].fillna("").astype(str)
    text = text.str.lower()
    mask = pd.Series(False, index=df.index)
    for term in terms:
        mask = mask | text.str.contains(re.escape(term), na=False)
    return mask


def get_bc_award_precedents(sector_tags=None, keyword=None, max_examples=5):
    """Summarize BC provincial award history from DataBC grant-award sources.

    This is provincial award precedent, separate from federal Canada G&C
    disclosure precedent.
    """
    df = _load_bc_awards_cached()
    if df is None:
        return {
            "source_layer": "provincial_award_history",
            "source_name": "BC provincial grant awards",
            "found": False,
            "note": f"BC awards parquet not found at {BC_AWARDS_PARQUET}.",
            "examples": [],
        }
    terms = _bc_keywords(sector_tags=sector_tags, keyword=keyword)
    filtered = df[_frame_text_mask(
        df,
        ["program_name", "grant_type", "grant_sector", "grant_subsector", "project_title", "recipient_name", "city"],
        terms,
    )].copy()
    if filtered.empty:
        return {
            "source_layer": "provincial_award_history",
            "source_name": "BC provincial grant awards",
            "found": False,
            "matched_terms": terms,
            "award_count": 0,
            "examples": [],
        }
    amount = pd.to_numeric(filtered.get("award_amount"), errors="coerce")
    examples_df = filtered.assign(_amount=amount).sort_values("_amount", ascending=False, na_position="last")
    examples = []
    for _, row in examples_df.head(max(int(max_examples or 1), 1)).iterrows():
        examples.append({
            "source_layer": "provincial_award_history",
            "source_name": row.get("source_name"),
            "program_name": row.get("program_name"),
            "fiscal_year": row.get("fiscal_year"),
            "recipient_name": row.get("recipient_name"),
            "city": row.get("city"),
            "grant_type": row.get("grant_type"),
            "grant_sector": row.get("grant_sector"),
            "project_title": row.get("project_title"),
            "award_amount": row.get("award_amount"),
        })
    program_totals = (
        filtered.assign(_amount=amount)
        .groupby("program_name", dropna=False)["_amount"]
        .agg(["count", "sum"])
        .sort_values("sum", ascending=False)
        .head(5)
    )
    top_programs = [
        {"program_name": str(idx), "award_count": int(row["count"]), "total_award_amount": float(row["sum"] or 0)}
        for idx, row in program_totals.iterrows()
    ]
    return {
        "source_layer": "provincial_award_history",
        "source_name": "BC provincial grant awards",
        "found": True,
        "matched_terms": terms,
        "award_count": int(len(filtered)),
        "total_award_amount": float(amount.sum(skipna=True)),
        "median_award_amount": float(amount.median(skipna=True)) if amount.notna().any() else None,
        "top_programs": top_programs,
        "examples": examples,
        "limitation": "These are BC provincial award records, not federal Canada G&C precedent or approval odds.",
    }


def match_can_grant_programs(org_type, sector_tags=None, province=None,
                              has_government_partner=False, has_match_funding=False):
    """Step 1: filter canada_grant_funding_programs.csv for a project's
    profile - the Canadian counterpart to us_backend.match_grant_programs().

    org_type: a single value from Canada's real recipient_type vocabulary
        (indigenous, for_profit, government, international_ngo, nonprofit,
        other, individual_sole_proprietorship, academia).
    province: 2-letter province/territory abbreviation (e.g. "BC", "QC").
        Geography here is province-level only - no county/CD-equivalent
        exists in Canadian grant disclosure data.

    Same tri-state handling as the US backend: requires_government_
    intermediary/requires_match_funding being UNKNOWN never becomes a
    blocker (we haven't confirmed a requirement exists) but also never
    silently reads as "no blocker" - see tristate.py. In practice every
    row's requires_match_funding is UNKNOWN today, so every match will
    carry an unknown_requirements entry for it - that's accurate, not a
    bug, given no Canadian program's actual terms have been read yet.
    """
    df = load_programs()
    results = []

    for _, row in df.iterrows():
        eligible_types = _split_pipe(row["eligible_applicant_types"])
        if org_type not in eligible_types:
            continue

        if sector_tags:
            program_tags = set(_split_pipe(row["sector_tags"]))
            matched_sector_tags = sorted(set(sector_tags) & program_tags)
            if not matched_sector_tags:
                continue
        else:
            program_tags = set(_split_pipe(row["sector_tags"]))
            matched_sector_tags = []

        geo_match, geo_note = _geography_matches(row["geography_scope"], province)
        if geo_match is False:
            continue

        requires_intermediary = parse_tristate(row["requires_government_intermediary"])
        requires_match = parse_tristate(row["requires_match_funding"])

        blockers = []
        unknown_requirements = []
        if requires_intermediary is True and not has_government_partner:
            blockers.append(
                "Requires a government co-applicant - you cannot apply directly without one."
            )
        elif requires_intermediary is None:
            unknown_requirements.append(
                "Whether this program requires a government co-applicant is unconfirmed - "
                "verify before relying on direct eligibility."
            )
        if requires_match is True and not has_match_funding:
            blockers.append("Requires match funding, which isn't confirmed available yet.")
        elif requires_match is None:
            unknown_requirements.append(
                "Whether this program requires match funding is unconfirmed - no Canadian "
                "program's actual terms/NOFO-equivalent text has been read yet, see "
                "Canada grant research.md."
            )

        pathway_note = None
        if requires_intermediary is True and has_government_partner:
            pathway_note = "Eligible via your government partner as co-applicant/sub-applicant."
        elif requires_intermediary is True:
            pathway_note = "Cannot apply directly. Find a government partner willing to apply on your behalf."

        results.append({
            "program_id": row["program_id"],
            "program_name": row["program_name"],
            "funder_type": row["funder_type"],
            "agency_or_funder": row["agency_or_funder"],
            "owner_org": row["owner_org"],
            "funds_what": row["funds_what"],
            "sector_tags": row["sector_tags"],
            "matched_sector_tags": matched_sector_tags,
            "sector_overlap_count": len(matched_sector_tags),
            "geography_note": geo_note,
            "blockers": blockers,
            "unknown_requirements": unknown_requirements,
            "eligibility_confidence": "uncertain" if unknown_requirements else "confirmed",
            "directly_eligible": len(blockers) == 0,
            "pathway_note": pathway_note,
            "typical_award_size": row["typical_award_size"],
            "notes": row["notes"],
            "source_url": row["source_url"],
        })

    results.sort(key=lambda r: (not r["directly_eligible"], -int(r.get("sector_overlap_count") or 0), len(r["blockers"])))
    return results


def get_can_precedent(program_id):
    """Step 2: precedent lookup against program_summary.parquet - the
    Canadian counterpart to us_backend.get_grant_precedents().

    Real structural difference from the US version: there's no CFDA code
    to look up directly. Instead this joins the CSV's (owner_org,
    program_name) back to program_summary.parquet's real
    (owner_org, prog_name_en) rows via order/punctuation-tolerant name
    matching (see _normalize_program_name) and AGGREGATES every matching
    row - several CSV rows represent multiple disclosure-data name
    variants of the same real program (DFAA, NDMP, PDCP, HUSAR, the Red
    Cross flood/fire program, IAFF, EM PACP - see
    Canada%20grant%20research.md's Batch 2/3 notes). Returns combined
    award_count/total/median plus a real recipient_type_breakdown summed
    across every matched variant, not just whichever single row's text
    happened to get sampled by hand during the CSV-building research.
    """
    programs = load_programs()
    match = programs[programs["program_id"] == program_id]
    if match.empty:
        return {"found": False, "note": f"No program with program_id '{program_id}' in canada_grant_funding_programs.csv."}

    row = match.iloc[0]
    summary = _load_program_summary_cached()
    same_owner = summary[summary["owner_org"] == row["owner_org"]].copy()
    if same_owner.empty:
        return {
            "found": False,
            "program_id": program_id,
            "note": f"No program_summary.parquet rows found for owner_org '{row['owner_org']}' - "
                    f"check the CSV's owner_org against real data (it's a hand-verified code, not "
                    f"derived automatically).",
        }

    target_norm = _normalize_program_name(row["program_name"])
    same_owner["_norm"] = same_owner["prog_name_en"].apply(_normalize_program_name)
    matched = same_owner[same_owner["_norm"].apply(lambda n: n in target_norm or target_norm in n)]

    if matched.empty:
        return {
            "found": False,
            "program_id": program_id,
            "note": f"No program_summary.parquet rows matched program_name '{row['program_name']}' "
                    f"under owner_org '{row['owner_org']}' - the disclosure data's program name may "
                    f"have drifted further than this normalizer handles. Verify by hand before "
                    f"concluding there's no precedent.",
        }

    total_awards = int(matched["award_count"].sum())
    total_value = float(matched["total_agreement_value"].sum())
    # Median can't be re-derived from pre-aggregated medians across variants
    # without the raw rows - report the matched variant with the most
    # awards' median as a representative figure, flagged as such rather
    # than silently averaging medians (which would be a real statistical
    # error - the median of medians is not the median of the pooled data).
    representative = matched.sort_values("award_count", ascending=False).iloc[0]

    # Real recipient-type counts, summed across every matched variant -
    # more rigorous than the by-hand merge used when the CSV rows were
    # first written, which only sampled one variant's breakdown text.
    type_totals = {}
    for breakdown in matched["recipient_type_breakdown"].dropna():
        for entry in str(breakdown).split(";"):
            entry = entry.strip()
            m = re.match(r"^(.*?)\s*\((\d+)\)$", entry)
            if m:
                type_name, count = m.group(1).strip(), int(m.group(2))
                type_totals[type_name] = type_totals.get(type_name, 0) + count
    recipient_type_breakdown = "; ".join(
        f"{t} ({c})" for t, c in sorted(type_totals.items(), key=lambda kv: -kv[1])
    ) or None

    return {
        "found": True,
        "program_id": program_id,
        "program_name": row["program_name"],
        "owner_org": row["owner_org"],
        "matched_variant_count": len(matched),
        "matched_variant_names": matched["prog_name_en"].tolist(),
        "award_count": total_awards,
        "total_agreement_value": total_value,
        "median_agreement_value": float(representative["median_agreement_value"]) if pd.notna(representative["median_agreement_value"]) else None,
        "median_agreement_value_note": f"Median is from the highest-award-count single variant "
                                        f"('{representative['prog_name_en']}', {int(representative['award_count'])} awards) - "
                                        f"medians cannot be validly averaged across variants.",
        "recipient_type_breakdown": recipient_type_breakdown,
        "limitation": "These are descriptive statistics about past award recipients, not a "
                      "prediction of whether any specific application will be funded - same "
                      "honesty constraint as the US backend.",
    }


def get_grant_writing_template(program_id):
    """Not supported yet - no Canadian program's actual application
    guidelines/terms have been independently researched (unlike the US
    backend's NSF/FEMA-verified templates). Raises rather than returning
    a fabricated generic template, since a confident-looking-but-wrong
    template is worse than an explicit capability gap."""
    raise NotImplementedError(
        "can_backend has no writing-template capability yet (CAPABILITIES['writing_template'] "
        "is False) - no Canadian program's actual guidelines have been researched. Check "
        "CAPABILITIES before calling this."
    )


def check_open_opportunity(program_id):
    """Not supported yet - Canada has no Grants.gov equivalent (a
    centralized, program-keyed open/closed NOFO catalog). The closest
    real thing, canada_grant_announcements.py, is an unstructured
    announcement feed, not a per-program lookup - a genuinely different
    capability, not the same one with thinner data. See
    Canada%20grant%20research.md."""
    raise NotImplementedError(
        "can_backend has no live-opportunity-check capability yet (CAPABILITIES"
        "['live_opportunity_check'] is False) - Canada has no Grants.gov equivalent. "
        "Check CAPABILITIES before calling this."
    )


def score_and_rank_programs(*args, **kwargs):
    """Not supported yet - no Canadian Grant Readiness Score has been
    built (CAPABILITIES['readiness_scoring'] is False). match_can_grant_
    programs() + get_can_precedent() are the two real capabilities today;
    a scoring function would combine them the way us_backend.
    score_and_rank_programs() does, but that combination hasn't been
    built. Raises rather than silently returning a partial/zero score."""
    raise NotImplementedError(
        "can_backend has no readiness-scoring capability yet (CAPABILITIES"
        "['readiness_scoring'] is False) - use match_can_grant_programs() and "
        "get_can_precedent() directly. Check CAPABILITIES before calling this."
    )


if __name__ == "__main__":
    print("=" * 70)
    print("VALIDATION: BC wildfire/flood/forest test case (real-world, 2026-06-29)")
    print("=" * 70)
    print(f"\nCapabilities: {CAPABILITIES}\n")

    matches = match_can_grant_programs(
        org_type="nonprofit",
        sector_tags=["wildfire_management", "flood_mitigation", "community_resilience"],
        province="BC",
    )
    print(f"{len(matches)} matching program(s):\n")
    for m in matches:
        status = "DIRECTLY ELIGIBLE" if m["directly_eligible"] else "BLOCKED"
        print(f"[{status}] {m['program_name']} ({m['program_id']})")
        if m["blockers"]:
            for b in m["blockers"]:
                print(f"    - {b}")
        if m["unknown_requirements"]:
            for u in m["unknown_requirements"]:
                print(f"    ? {u}")
        print()

    print("=" * 70)
    print("PRECEDENT CHECK: Community Resilience Fund (best direct-nonprofit pathway found)")
    print("=" * 70)
    precedent = get_can_precedent("can_psc_community_resilience_fund")
    for k, v in precedent.items():
        print(f"{k}: {v}")

    print("\n" + "=" * 70)
    print("PRECEDENT CHECK: DFAA (confirms 4-variant merge matches the by-hand total)")
    print("=" * 70)
    dfaa = get_can_precedent("can_ps_dfaa")
    for k, v in dfaa.items():
        print(f"{k}: {v}")

    print("\n" + "=" * 70)
    print("CAPABILITY GAP DEMONSTRATION: score_and_rank_programs raises, not silently empty")
    print("=" * 70)
    try:
        score_and_rank_programs(org_type="nonprofit")
    except NotImplementedError as e:
        print(f"NotImplementedError (expected): {e}")
