"""
Build a per-CFDA-program summary table from the USAspending grants parquet.

This is the "groundwork" layer for the grant-matching design in
county-map-private/live/private_mcp_tools/grant_analyzer/docs/grant_funding_programs.md: one row per CFDA
(Assistance Listing) program, aggregated from the award-level data, so a
future chat can answer "what does a winning application to program X
usually look like" without re-aggregating 1.36M award rows on every query.

Input: data/countries/USA/usaspending_grants/USA.parquet
Output: data/countries/USA/usaspending_grants/cfda_program_summary.parquet

Per program: award count, obligated-amount stats, recipient-type breakdown,
administration breakdown, project-length stats, geographic spread, and a
handful of sample award descriptions / funding opportunity goals text to
use as real precedent text (not just numbers).

Usage:
    python build_cfda_program_summary.py
"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
INPUT_FILE = DATA_ROOT / "countries" / "USA" / "usaspending_grants" / "USA.parquet"
OUTPUT_FILE = DATA_ROOT / "countries" / "USA" / "usaspending_grants" / "cfda_program_summary.parquet"

N_SAMPLE_DESCRIPTIONS = 3

# DuckDB SQL does the heavy lifting: explode the ';'-separated cfda_numbers_and_titles
# field into one row per (award, cfda program), then aggregate per program.
BUILD_SQL = f"""
WITH exploded AS (
    SELECT
        award_key,
        award_type_code,
        award_type_description,
        awarding_agency_name,
        recipient_business_type_description,
        administration,
        total_obligated_amount,
        project_length_days,
        recipient_loc_id,
        place_of_performance_loc_id,
        award_description,
        funding_opportunity_goals_text,
        award_base_action_date,
        trim(regexp_extract(entry, '^\\s*([0-9]{{2}}\\.[0-9A-Z]{{3,4}})', 1)) AS cfda_code,
        trim(regexp_extract(entry, ':\\s*(.*)$', 1)) AS cfda_title
    FROM '{INPUT_FILE.as_posix()}',
         LATERAL UNNEST(string_split(cfda_numbers_and_titles, '; ')) AS t(entry)
    WHERE cfda_numbers_and_titles IS NOT NULL
),
filtered AS (
    SELECT * FROM exploded WHERE cfda_code != ''
),
type_counts AS (
    SELECT cfda_code, recipient_business_type_description, COUNT(*) AS n
    FROM filtered
    WHERE recipient_business_type_description IS NOT NULL
    GROUP BY 1, 2
),
type_ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY n DESC) AS rnk
    FROM type_counts
),
type_breakdown AS (
    SELECT cfda_code,
        string_agg(recipient_business_type_description || ' (' || n || ')', '; ' ORDER BY rnk) AS recipient_type_breakdown
    FROM type_ranked
    WHERE rnk <= 3
    GROUP BY 1
),
admin_counts AS (
    SELECT cfda_code, administration, COUNT(*) AS n
    FROM filtered
    WHERE administration IS NOT NULL
    GROUP BY 1, 2
),
admin_ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY n DESC) AS rnk
    FROM admin_counts
),
admin_breakdown AS (
    SELECT cfda_code,
        string_agg(administration || ' (' || n || ')', '; ' ORDER BY rnk) AS administration_breakdown
    FROM admin_ranked
    GROUP BY 1
),
agency_counts AS (
    SELECT cfda_code, awarding_agency_name, COUNT(*) AS n
    FROM filtered
    GROUP BY 1, 2
),
agency_top AS (
    SELECT cfda_code, awarding_agency_name
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY n DESC) AS rnk
        FROM agency_counts
    )
    WHERE rnk = 1
),
-- CFDA programs sometimes get renamed (e.g. 97.047 was "Pre-Disaster
-- Mitigation" before becoming "BRIC" in 2020, same code reused). Pick the
-- title from the most recent award as the canonical name, and separately
-- list any older titles seen under the same code, so a rename is visible
-- rather than silently picking whichever title happened to sort first.
title_by_code_date AS (
    SELECT cfda_code, cfda_title, MAX(award_base_action_date) AS last_seen
    FROM filtered
    WHERE cfda_title IS NOT NULL AND cfda_title != ''
    GROUP BY 1, 2
),
title_ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY last_seen DESC) AS rnk
    FROM title_by_code_date
),
title_latest AS (
    SELECT cfda_code, cfda_title FROM title_ranked WHERE rnk = 1
),
title_prior AS (
    SELECT cfda_code, string_agg(cfda_title, '; ' ORDER BY last_seen DESC) AS prior_titles
    FROM title_ranked
    WHERE rnk > 1
    GROUP BY 1
),
samples AS (
    SELECT cfda_code,
        string_agg(award_description, ' ||| ' ORDER BY award_key) AS sample_award_descriptions
    FROM (
        SELECT cfda_code, award_key, award_description,
            ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY hash(award_key)) AS rnk
        FROM filtered
        WHERE award_description IS NOT NULL AND length(trim(award_description)) > 0
    )
    WHERE rnk <= {N_SAMPLE_DESCRIPTIONS}
    GROUP BY 1
),
goal_samples AS (
    SELECT cfda_code,
        string_agg(funding_opportunity_goals_text, ' ||| ' ORDER BY award_key) AS sample_funding_opportunity_goals
    FROM (
        SELECT cfda_code, award_key, funding_opportunity_goals_text,
            ROW_NUMBER() OVER (PARTITION BY cfda_code ORDER BY hash(award_key)) AS rnk
        FROM filtered
        WHERE funding_opportunity_goals_text IS NOT NULL AND length(trim(funding_opportunity_goals_text)) > 0
    )
    WHERE rnk <= {N_SAMPLE_DESCRIPTIONS}
    GROUP BY 1
)
SELECT
    f.cfda_code,
    tl.cfda_title,
    tp.prior_titles,
    a.awarding_agency_name,
    COUNT(*) AS award_count,
    COUNT(DISTINCT f.award_key) AS unique_award_count,
    SUM(f.total_obligated_amount) AS total_obligated_amount,
    AVG(f.total_obligated_amount) AS avg_obligated_amount,
    MEDIAN(f.total_obligated_amount) AS median_obligated_amount,
    AVG(f.project_length_days) AS avg_project_length_days,
    MEDIAN(f.project_length_days) AS median_project_length_days,
    COUNT(DISTINCT f.recipient_loc_id) AS distinct_recipient_loc_ids,
    COUNT(DISTINCT f.place_of_performance_loc_id) AS distinct_place_of_performance_loc_ids,
    string_agg(DISTINCT f.award_type_code, '|') AS award_type_codes_present,
    tb.recipient_type_breakdown,
    ab.administration_breakdown,
    s.sample_award_descriptions,
    gs.sample_funding_opportunity_goals
FROM filtered f
LEFT JOIN title_latest tl ON tl.cfda_code = f.cfda_code
LEFT JOIN title_prior tp ON tp.cfda_code = f.cfda_code
LEFT JOIN agency_top a ON a.cfda_code = f.cfda_code
LEFT JOIN type_breakdown tb ON tb.cfda_code = f.cfda_code
LEFT JOIN admin_breakdown ab ON ab.cfda_code = f.cfda_code
LEFT JOIN samples s ON s.cfda_code = f.cfda_code
LEFT JOIN goal_samples gs ON gs.cfda_code = f.cfda_code
GROUP BY f.cfda_code, tl.cfda_title, tp.prior_titles, a.awarding_agency_name,
         tb.recipient_type_breakdown, ab.administration_breakdown,
         s.sample_award_descriptions, gs.sample_funding_opportunity_goals
ORDER BY award_count DESC
"""


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input parquet not found: {INPUT_FILE}")

    con = duckdb.connect()
    print(f"Aggregating {INPUT_FILE} by CFDA program...")
    df = con.execute(BUILD_SQL).fetchdf()
    print(f"Built {len(df):,} CFDA program rows")

    df.to_parquet(OUTPUT_FILE, index=False)
    size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Wrote {OUTPUT_FILE} ({size_mb:.1f} MB)")

    print("\nTop 10 programs by award count:")
    print(df[["cfda_code", "cfda_title", "awarding_agency_name", "award_count", "total_obligated_amount"]].head(10).to_string())

    renamed = df[df["prior_titles"].notna()]
    if len(renamed):
        print(f"\n{len(renamed)} CFDA code(s) with a prior title detected (program renamed, code reused):")
        print(renamed[["cfda_code", "cfda_title", "prior_titles"]].to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
