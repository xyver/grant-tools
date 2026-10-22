"""
Build a per-program summary table from the Canada Grants & Contributions
disclosure parquet - the Canadian analog of build_cfda_program_summary.py.

Why this exists: the wildfire/flood/forest test case
(live/private_mcp_tools/grant_analyzer/docs/Canada%20grant%20research.md) was answered by
scanning 1.3M raw award rows for keyword text matches - that does not
scale to a second test case, a third, etc. This script aggregates once,
so future questions filter ~thousands of program rows instead of
re-scanning the full award-level table every time, the same shortcut
build_cfda_program_summary.py provides on the US side.

Real structural difference from the US side: Canada has no CFDA-style
numeric program code. The closest stable identifier is the
(owner_org, prog_name_en) pair - confirmed against the data: program
names are not globally unique across departments, but are effectively
unique within a department.

Input: data/countries/CAN/grants_contributions/CAN.parquet
Output: data/countries/CAN/grants_contributions/program_summary.parquet

Usage:
    python build_can_program_summary.py
"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
INPUT_FILE = DATA_ROOT / "countries" / "CAN" / "grants_contributions" / "CAN.parquet"
OUTPUT_FILE = DATA_ROOT / "countries" / "CAN" / "grants_contributions" / "program_summary.parquet"

N_SAMPLE_DESCRIPTIONS = 3

# Real legend confirmed 2026-06-29 from open.canada.ca's grants data
# dictionary (recombinant-published-dictionary/grants), not guessed -
# recipient_type is a controlled list with exactly these 8 codes.
RECIPIENT_TYPE_LABELS = """
    CASE recipient_type
        WHEN 'A' THEN 'Indigenous recipients'
        WHEN 'F' THEN 'For-profit organizations'
        WHEN 'G' THEN 'Government'
        WHEN 'I' THEN 'International (non-government)'
        WHEN 'N' THEN 'Not-for-profit organizations and charities'
        WHEN 'O' THEN 'Other'
        WHEN 'P' THEN 'Individual or sole proprietorships'
        WHEN 'S' THEN 'Academia'
        ELSE NULL
    END
"""

BUILD_SQL = f"""
WITH base AS (
    SELECT
        owner_org,
        owner_org_title,
        prog_name_en,
        recipient_type,
        {RECIPIENT_TYPE_LABELS} AS recipient_type_label,
        recipient_loc_id,
        agreement_value,
        agreement_start_date,
        description_en,
        prog_purpose_en
    FROM '{INPUT_FILE.as_posix()}'
    WHERE prog_name_en IS NOT NULL AND prog_name_en != ''
),
type_counts AS (
    SELECT owner_org, prog_name_en, recipient_type_label, COUNT(*) AS n
    FROM base
    WHERE recipient_type_label IS NOT NULL
    GROUP BY 1, 2, 3
),
type_ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY owner_org, prog_name_en ORDER BY n DESC) AS rnk
    FROM type_counts
),
type_breakdown AS (
    SELECT owner_org, prog_name_en,
        string_agg(recipient_type_label || ' (' || n || ')', '; ' ORDER BY rnk) AS recipient_type_breakdown
    FROM type_ranked
    GROUP BY 1, 2
),
samples AS (
    SELECT owner_org, prog_name_en,
        string_agg(description_en, ' ||| ' ORDER BY description_en) AS sample_descriptions
    FROM (
        SELECT owner_org, prog_name_en, description_en,
            ROW_NUMBER() OVER (PARTITION BY owner_org, prog_name_en ORDER BY hash(description_en)) AS rnk
        FROM base
        WHERE description_en IS NOT NULL AND length(trim(description_en)) > 0
    )
    WHERE rnk <= {N_SAMPLE_DESCRIPTIONS}
    GROUP BY 1, 2
),
purpose AS (
    -- GROUP BY (not DISTINCT) - real data has minor text variants of the
    -- same purpose under one program key (whitespace/amendment drift), and
    -- a DISTINCT-then-join fans out the join, silently multiplying every
    -- downstream count (caught this directly: it produced a 4.6M-award
    -- "Discovery Grants Program" row against a 1.3M-row source table).
    SELECT owner_org, prog_name_en, ANY_VALUE(prog_purpose_en) AS prog_purpose_en
    FROM base
    WHERE prog_purpose_en IS NOT NULL AND prog_purpose_en != ''
    GROUP BY 1, 2
)
SELECT
    b.owner_org,
    b.owner_org_title,
    b.prog_name_en,
    ANY_VALUE(p.prog_purpose_en) AS prog_purpose_en,
    COUNT(*) AS award_count,
    COUNT(DISTINCT b.recipient_loc_id) AS distinct_recipient_loc_ids,
    SUM(b.agreement_value) AS total_agreement_value,
    MEDIAN(b.agreement_value) AS median_agreement_value,
    MIN(b.agreement_start_date) AS earliest_agreement_start,
    MAX(b.agreement_start_date) AS latest_agreement_start,
    ANY_VALUE(tb.recipient_type_breakdown) AS recipient_type_breakdown,
    ANY_VALUE(s.sample_descriptions) AS sample_descriptions
FROM base b
LEFT JOIN type_breakdown tb ON tb.owner_org = b.owner_org AND tb.prog_name_en = b.prog_name_en
LEFT JOIN samples s ON s.owner_org = b.owner_org AND s.prog_name_en = b.prog_name_en
LEFT JOIN purpose p ON p.owner_org = b.owner_org AND p.prog_name_en = b.prog_name_en
GROUP BY b.owner_org, b.owner_org_title, b.prog_name_en
ORDER BY award_count DESC
"""


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input parquet not found: {INPUT_FILE}")

    con = duckdb.connect()
    print(f"Aggregating {INPUT_FILE} by (owner_org, prog_name_en)...")
    df = con.execute(BUILD_SQL).fetchdf()
    print(f"Built {len(df):,} program rows from the raw award-level data")

    df.to_parquet(OUTPUT_FILE, index=False)
    size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Wrote {OUTPUT_FILE} ({size_mb:.1f} MB)")

    print("\nTop 10 programs by award count:")
    print(df[["owner_org_title", "prog_name_en", "award_count", "total_agreement_value"]].head(10).to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
