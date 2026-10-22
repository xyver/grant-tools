"""
Convert raw NIH RePORTER project CSV (from download_nih_reporter_projects.py)
into a deduplicated, loc_id-tagged parquet.

Input: data/countries/USA/nih_reporter_projects/nih_reporter_projects.csv
       (one row per (keyword, year) match - a project matching multiple
       keywords in the same fiscal year appears once per keyword, so this
       needs deduplication, same issue as the USAspending year-chunk pull)

Output: data/countries/USA/nih_reporter_projects/USA.parquet
        one row per unique appl_id, matched_keywords collapsed into a
        single '|'-separated column, recipient_loc_id derived from
        org_state (NIH gives state-level only, no county FIPS).

Usage:
    python convert_nih_reporter_projects.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
INPUT_FILE = RAW_ROOT / "nih_reporter_projects" / "nih_reporter_projects.csv"
OUTPUT_FILE = DATA_ROOT / "countries" / "USA" / "nih_reporter_projects" / "USA.parquet"


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE, dtype={"org_state": "string"})
    print(f"Loaded {len(df):,} raw rows")

    # Collapse the (keyword, year) duplication: same appl_id can match
    # multiple keywords in the same fiscal year. Keep one row per appl_id,
    # collect every keyword it matched under.
    keyword_map = (
        df.groupby("appl_id")["matched_keyword"]
        .apply(lambda s: "|".join(sorted(set(s))))
        .rename("matched_keywords")
    )

    deduped = df.drop_duplicates(subset="appl_id", keep="first").drop(columns=["matched_keyword"])
    deduped = deduped.merge(keyword_map, on="appl_id", how="left")

    print(f"Deduplicated to {len(deduped):,} unique projects "
          f"({len(df) - len(deduped):,} duplicate (keyword,year) matches collapsed)")

    deduped["recipient_loc_id"] = deduped["org_state"].apply(
        lambda s: f"USA-{s}" if pd.notna(s) and s.strip() else pd.NA
    )

    deduped.to_parquet(OUTPUT_FILE, index=False)
    size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Wrote {len(deduped):,} rows -> {OUTPUT_FILE} ({size_mb:.1f} MB)")

    print(f"\nrecipient_loc_id coverage: {deduped['recipient_loc_id'].notna().mean():.1%}")
    print("\nRows by search_year:")
    print(deduped["search_year"].value_counts().sort_index(ascending=False).to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
