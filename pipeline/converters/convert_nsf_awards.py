"""
Convert raw NSF Award Search CSV (from download_nsf_award_search.py) into a
deduplicated, loc_id-tagged parquet. Same shape/issue as the NIH RePORTER
converter: an award matching multiple keywords in the same search year
appears once per keyword, so this needs deduplication.

Input: data/countries/USA/nsf_awards/nsf_awards.csv
Output: data/countries/USA/nsf_awards/USA.parquet

Usage:
    python convert_nsf_awards.py
"""
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_FILE = PROJECT_ROOT / "data" / "Raw data" / "nsf_awards" / "nsf_awards.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "countries" / "USA" / "nsf_awards" / "USA.parquet"


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE, dtype={"awardee_state": "string"}, low_memory=False)
    print(f"Loaded {len(df):,} raw rows")

    keyword_map = (
        df.groupby("nsf_award_id")["matched_keyword"]
        .apply(lambda s: "|".join(sorted(set(s.dropna()))))
        .rename("matched_keywords")
    )

    deduped = df.drop_duplicates(subset="nsf_award_id", keep="first").drop(columns=["matched_keyword"])
    deduped = deduped.merge(keyword_map, on="nsf_award_id", how="left")

    print(f"Deduplicated to {len(deduped):,} unique awards "
          f"({len(df) - len(deduped):,} duplicate (keyword,year) matches collapsed)")

    deduped["recipient_loc_id"] = deduped["awardee_state"].apply(
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
