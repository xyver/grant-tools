"""
Coverage test: does every eligible_applicant_types value in
grant_funding_programs.csv actually match against SOME real
recipient_business_type_description phrase in USA.parquet?

Born from a real bug (2026-06-26): org_type="higher_ed" tokenized to
{"HIGHER","ED"}, which never matched "INSTITUTION OF HIGHER EDUCATION"
text since "ED" isn't its own word - silently undercounted the recipient-
type fit for usaid_university_partnerships, the exact program built for
that scenario. That bug was found by accident, once. This test finds the
whole class of it on purpose, every time the org_type vocabulary or the
matching logic changes.

A "no match" result isn't always a bug - some org_types genuinely have no
USAspending business-type equivalent (certified_cdfi is a Treasury
certification, not a business type; agricultural_producer/cooperative
aren't tracked as a distinct category at all). The print output is a
worklist for a human to triage, not a pass/fail assertion - that's why
this is a standalone script, not wired into CI.
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_backend as ga  # this test is US-specific (org_type vocabulary
                          # vs. USAspending business-type text) - moved
                          # from grant_analyzer to us_backend 2026-06-29

# Org_types known to have no real USAspending business-type equivalent at
# all (not a matching bug - the concept just isn't tracked in this data).
# Listed explicitly so a future zero-match result for anything NOT in this
# set is treated as a real regression, not expected noise.
KNOWN_UNMATCHABLE = {
    "certified_cdfi",        # Treasury certification status, not a business type
    "agricultural_cooperative",
    "agricultural_producer", # USDA-specific concepts; USAspending has no ag-sector business type
    "utility",                # No utility business-type category in the 10yr pull
}

# Org_types that never reach _org_type_matches_entry at all, because every
# program using them has no cfda_number. Without a CFDA,
# score_and_rank_programs() leaves `precedent` None, so the recipient-type
# breakdown is None and _sub_recipient_rank/_sub_recipient_concentration
# short-circuit to status="unknown" without ever consulting the phrase
# matcher. Adding ORG_TYPE_PHRASE_OVERRIDES entries for these would be dead
# code.
#
# This set is VERIFIED below rather than trusted: the moment one of these
# org_types appears on a CFDA-backed program, its matching gap becomes real
# and reachable, and the run reports it as a regression instead of silently
# excusing it the way a static allowlist would.
# coalition, community_foundation, and local_development_district were in this
# set until 2026-07-19, when the ARC rows gained cfda_numbers and this check
# reported them as newly reachable. They now have real
# ORG_TYPE_PHRASE_OVERRIDES entries in us_backend instead.
UNSCORED_NO_CFDA = {
    "community_organization",       # adaptation_resilience_fund (private funder, no CFDA)
    "government_entity",            # bush_foundation_ci (private funder, no CFDA)
    "nonprofit_research_institute", # vipc_* (Virginia state corporation, no federal CFDA)
    "regional_planning_agency",     # caltrans_stpg (California state program, no federal CFDA)
    "transit_agency",               # caltrans_stpg (California state program, no federal CFDA)
}


def _org_types_with_cfda_backed_programs(programs):
    """org_type -> sorted program_ids that both list it and carry a cfda_number."""
    reachable = {}
    for _, row in programs.iterrows():
        cfda = row.get("cfda_number")
        if pd.isna(cfda) or not str(cfda).strip():
            continue
        for token in str(row.get("eligible_applicant_types") or "").split("|"):
            token = token.strip()
            if token:
                reachable.setdefault(token, []).append(str(row.get("program_id")))
    return {k: sorted(v) for k, v in reachable.items()}


def main():
    programs = pd.read_csv(ga.PROGRAMS_CSV, dtype=str)
    org_types = sorted({
        t.strip()
        for v in programs["eligible_applicant_types"].dropna()
        for t in v.split("|") if t.strip()
    })

    awards = pd.read_parquet(ga.AWARDS_PARQUET, columns=["recipient_business_type_description"])
    real_phrases = sorted({
        p.strip()
        for v in awards["recipient_business_type_description"].dropna()
        for p in str(v).split(";") if p.strip()
    })

    print(f"Checking {len(org_types)} org_types against {len(real_phrases)} real "
          f"recipient_business_type_description phrases...\n")

    reachable = _org_types_with_cfda_backed_programs(programs)

    regressions = []
    newly_reachable = []
    for org_type in org_types:
        matches = [p for p in real_phrases if ga._org_type_matches_entry(org_type, p)]
        status = "OK" if matches else "NO MATCH"
        print(f"[{status:8s}] {org_type}")
        for m in matches:
            print(f"             -> {m}")
        if matches:
            continue
        if org_type in KNOWN_UNMATCHABLE:
            continue
        if org_type in UNSCORED_NO_CFDA:
            # Only excused while it stays unreachable.
            if org_type in reachable:
                newly_reachable.append((org_type, reachable[org_type]))
            continue
        regressions.append(org_type)

    print()
    if newly_reachable:
        print(f"REGRESSIONS - {len(newly_reachable)} org_type(s) listed in UNSCORED_NO_CFDA now "
              f"appear on CFDA-backed programs, so their matching gap is reachable and real. "
              f"Add an ORG_TYPE_PHRASE_OVERRIDES entry and drop them from UNSCORED_NO_CFDA:")
        for org_type, program_ids in newly_reachable:
            print(f"    - {org_type} -> {', '.join(program_ids)}")
    if regressions:
        print(f"REGRESSIONS - {len(regressions)} org_type(s) with zero matches and NOT in "
              f"KNOWN_UNMATCHABLE or UNSCORED_NO_CFDA (new vocabulary added without a matching "
              f"rule, or a fix broke):")
        for r in regressions:
            print(f"    - {r}")
    if not regressions and not newly_reachable:
        print("No regressions - every org_type either matches real data, is an explicitly "
              "acknowledged gap (KNOWN_UNMATCHABLE), or is unreachable because no program "
              "using it carries a cfda_number (UNSCORED_NO_CFDA, verified above).")

    return 1 if (regressions or newly_reachable) else 0


if __name__ == "__main__":
    sys.exit(main())
