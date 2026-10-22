"""
Convert the raw Canada Grants & Contributions proactive disclosure CSV
(from download_canada_grants_contributions.py) into a queryable parquet.

Input: data/Raw data/canada_grants_contributions/grants.csv
       (one row per agreement/amendment, all federal departments combined)

Output: data/countries/CAN/grants_contributions/CAN.parquet
Standalone research dataset, same "no map pack, no catalog metadata.json
yet" state as USA.parquet - see convert_usaspending_grants.py.

Deliberately kept separate from the NSERC/SSHRC/CIHR parquets (see
live/private_mcp_tools/grant_analyzer/docs/Canada%20grant%20research.md) rather than merged
into one "Canada grants" table - this source's richness is program-level
prose (prog_purpose/description/expected_results), not project-level
abstracts, and forcing a shared schema would mean picking the lowest
common denominator across sources with very different shapes.

Geography: recipient_province is a real-world free-text disclosure field
filled in by hundreds of department clerks over a decade, not a
controlled vocabulary - confirmed by inspection: alongside the 13 real
province/territory codes there are partial-country leakage values (US
state codes, foreign country codes), city names typed directly into the
field ('Rome', 'Paris', 'Geneva'), full province names instead of codes,
and outright typos/garbage ('.', '-', '11'). build_loc_id() only ever
maps the unambiguous province values; everything else becomes a missing
loc_id rather than a guessed one - same "don't force a match" discipline
as the USA converter's county-FIPS handling. There is no county/CD-
equivalent field in this data at all (confirmed during research), so
loc_id stops at province (CAN-{abbr}), one level coarser than the USA
converter's county-level loc_id.

A handful (21 of 1.3M rows, confirmed during research) carry an
agreement_start_date before 1990, almost a decade before this disclosure
program existed - flagged as a likely data-entry artifact, not corrected
or dropped, same posture as the USDA loan-accounting artifact documented
on the US side.

Usage:
    python convert_canada_grants_contributions.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_FILE = RAW_ROOT / "canada_grants_contributions" / "grants.csv"
OUTPUT_DIR = DATA_ROOT / "countries" / "CAN" / "grants_contributions"
OUTPUT_FILE = OUTPUT_DIR / "CAN.parquet"

MONEY_COLUMNS = ["agreement_value", "foreign_currency_value"]
DATE_COLUMNS = ["amendment_date", "agreement_start_date", "agreement_end_date"]

# Real province/territory codes the data actually uses, plus the full-name
# variants confirmed present in the long tail of free-text entries. US
# states, foreign country codes, city names, and garbage values are
# deliberately NOT mapped - they're real values in the data, but mapping
# them would mean guessing rather than confirming.
PROVINCE_TO_ABBR = {
    "ON": "ON", "QC": "QC", "BC": "BC", "AB": "AB", "SK": "SK", "MB": "MB",
    "NS": "NS", "NB": "NB", "NL": "NL", "PE": "PE", "NT": "NT", "YT": "YT",
    "NU": "NU",
    "ONTARIO": "ON", "QUEBEC": "QC", "QUÉBEC": "QC",
    "BRITISH COLUMBIA": "BC", "ALBERTA": "AB", "SASKATCHEWAN": "SK",
    "MANITOBA": "MB", "NOVA SCOTIA": "NS", "NEW BRUNSWICK": "NB",
    "NEWFOUNDLAND AND LABRADOR": "NL", "PRINCE EDWARD ISLAND": "PE",
    "PEI": "PE", "NORTHWEST TERRITORIES": "NT", "YUKON": "YT",
    "YUKON TERRITORY": "YT", "NUNAVUT": "NU",
}


def build_loc_id(province_raw: pd.Series) -> pd.Series:
    normalized = province_raw.fillna("").str.strip().str.upper()
    abbr = normalized.map(PROVINCE_TO_ABBR)
    loc_id = pd.Series(pd.NA, index=province_raw.index, dtype="object")
    matched = abbr.notna()
    loc_id.loc[matched] = "CAN-" + abbr.loc[matched]
    return loc_id


def load_raw() -> pd.DataFrame:
    if not RAW_FILE.exists():
        raise FileNotFoundError(f"Raw file not found: {RAW_FILE} - run download_canada_grants_contributions.py first")
    print(f"Reading {RAW_FILE} ...")
    df = pd.read_csv(RAW_FILE, encoding="utf-8-sig", dtype=str, keep_default_na=False, na_values=[""])
    print(f"Loaded {len(df):,} raw rows, {len(df.columns)} columns")
    return df


def transform(df: pd.DataFrame) -> pd.DataFrame:
    for col in MONEY_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["recipient_loc_id"] = build_loc_id(df["recipient_province"])
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw()
    df = transform(raw)

    print(f"Writing {len(df):,} rows, {len(df.columns)} columns -> {OUTPUT_FILE}")
    df.to_parquet(OUTPUT_FILE, index=False)

    file_size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Done. File size: {file_size_mb:.1f} MB")

    print(f"\nrecipient_loc_id coverage: {df['recipient_loc_id'].notna().mean():.1%}")
    print("\nTop recipient_type values:")
    print(df["recipient_type"].value_counts(dropna=False).head(10).to_string())
    print("\nagreement_value: total = "
          f"${df['agreement_value'].sum():,.0f}, "
          f"median = ${df['agreement_value'].median():,.0f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
