"""
Convert raw USAspending grant award CSVs (from download_usaspending_grants.py)
into a single queryable parquet.

Input: zips in data/Raw data/usaspending_grants/*.zip, each
containing one Assistance_PrimeAwardSummaries_*.csv (one row per award).

Output: data/countries/USA/usaspending_grants/USA.parquet
This is a standalone research dataset, not a map pack - no loc_id join, no
catalog metadata.json yet (same "pending" state LODES/CBP/SUSB started in).
Both recipient and place-of-performance county FIPS are kept as plain columns
so a map join can be added later if/when that's actually useful.

Adds two derived columns:
    project_length_days - period of performance span
    administration      - which president was in office at the award's base
                           action date (Bush/Obama/Trump/Biden/Trump2),
                           bucketed purely from date, not provided by the API

Usage:
    python convert_usaspending_grants.py
"""
import sys
import zipfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "Raw data" / "usaspending_grants"
OUTPUT_DIR = PROJECT_ROOT / "data" / "countries" / "USA" / "usaspending_grants"
OUTPUT_FILE = OUTPUT_DIR / "USA.parquet"

# Columns kept from the raw CSV, renamed for clarity. Dropped on purpose:
# COVID/IIJA supplemental amounts, highly-compensated-officer fields, legacy
# DUNS ids, treasury/federal account and object-class/program-activity
# breakdowns, foreign place-of-performance fields - all accounting/compliance
# detail not relevant to "what makes a good grant" pattern analysis.
COLUMN_RENAMES = {
    "assistance_award_unique_key": "award_key",
    "award_id_fain": "award_id_fain",
    "award_id_uri": "award_id_uri",
    "sai_number": "sai_number",
    "assistance_type_code": "award_type_code",
    "assistance_type_description": "award_type_description",
    "award_base_action_date": "award_base_action_date",
    "award_base_action_date_fiscal_year": "award_base_action_fiscal_year",
    "award_latest_action_date": "award_latest_action_date",
    "award_latest_action_date_fiscal_year": "award_latest_action_fiscal_year",
    "period_of_performance_start_date": "period_of_performance_start_date",
    "period_of_performance_current_end_date": "period_of_performance_end_date",
    "last_modified_date": "last_modified_date",
    "total_obligated_amount": "total_obligated_amount",
    "total_outlayed_amount": "total_outlayed_amount",
    "total_non_federal_funding_amount": "total_non_federal_funding_amount",
    "total_funding_amount": "total_funding_amount",
    "indirect_cost_federal_share_amount": "indirect_cost_federal_share_amount",
    "awarding_agency_code": "awarding_agency_code",
    "awarding_agency_name": "awarding_agency_name",
    "awarding_sub_agency_code": "awarding_sub_agency_code",
    "awarding_sub_agency_name": "awarding_sub_agency_name",
    "awarding_office_name": "awarding_office_name",
    "funding_agency_code": "funding_agency_code",
    "funding_agency_name": "funding_agency_name",
    "funding_sub_agency_name": "funding_sub_agency_name",
    "recipient_uei": "recipient_uei",
    "recipient_name": "recipient_name",
    "recipient_name_raw": "recipient_name_raw",
    "recipient_parent_uei": "recipient_parent_uei",
    "recipient_parent_name": "recipient_parent_name",
    "recipient_country_code": "recipient_country_code",
    "recipient_country_name": "recipient_country_name",
    "recipient_city_name": "recipient_city_name",
    "prime_award_summary_recipient_county_fips_code": "recipient_county_fips",
    "recipient_county_name": "recipient_county_name",
    "prime_award_summary_recipient_state_fips_code": "recipient_state_fips",
    "recipient_state_code": "recipient_state_code",
    "recipient_state_name": "recipient_state_name",
    "recipient_zip_code": "recipient_zip_code",
    "prime_award_summary_recipient_cd_current": "recipient_congressional_district",
    "primary_place_of_performance_scope": "place_of_performance_scope",
    "primary_place_of_performance_country_code": "place_of_performance_country_code",
    "primary_place_of_performance_city_name": "place_of_performance_city_name",
    "prime_award_summary_place_of_performance_county_fips_code": "place_of_performance_county_fips",
    "primary_place_of_performance_county_name": "place_of_performance_county_name",
    "prime_award_summary_place_of_performance_state_fips_code": "place_of_performance_state_fips",
    "primary_place_of_performance_state_name": "place_of_performance_state_name",
    "primary_place_of_performance_zip_4": "place_of_performance_zip4",
    "prime_award_summary_place_of_performance_cd_current": "place_of_performance_congressional_district",
    "cfda_numbers_and_titles": "cfda_numbers_and_titles",
    "funding_opportunity_number": "funding_opportunity_number",
    "funding_opportunity_goals_text": "funding_opportunity_goals_text",
    "prime_award_base_transaction_description": "award_description",
    "business_types_code": "recipient_business_type_code",
    "business_types_description": "recipient_business_type_description",
    "usaspending_permalink": "usaspending_permalink",
}

STR_COLUMNS = {
    "recipient_county_fips", "recipient_state_fips",
    "place_of_performance_county_fips", "place_of_performance_state_fips",
    "award_base_action_fiscal_year", "award_latest_action_fiscal_year",
}

# Same FIPS -> abbreviation table used by convert_fema_disasters.py, so loc_id
# values line up with the rest of the project's USA county packs.
STATE_FIPS = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA',
    '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL',
    '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN',
    '19': 'IA', '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME',
    '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS',
    '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND',
    '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
    '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT',
    '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI',
    '56': 'WY', '60': 'AS', '64': 'FM', '66': 'GU', '68': 'MH',
    '69': 'MP', '70': 'PW', '72': 'PR', '78': 'VI',
}

DATE_COLUMNS = [
    "award_base_action_date", "award_latest_action_date",
    "period_of_performance_start_date", "period_of_performance_end_date",
    "last_modified_date",
]

# Administration boundaries (inauguration dates), used only to bucket the
# award's base action date - USAspending has no "administration" field.
ADMINISTRATIONS = [
    ("2001-01-20", "2009-01-20", "Bush 43"),
    ("2009-01-20", "2017-01-20", "Obama"),
    ("2017-01-20", "2021-01-20", "Trump 1"),
    ("2021-01-20", "2025-01-20", "Biden"),
    ("2025-01-20", "2029-01-20", "Trump 2"),
]


def load_raw_csvs() -> pd.DataFrame:
    zip_paths = sorted(RAW_DATA_DIR.glob("*.zip"))
    if not zip_paths:
        raise FileNotFoundError(f"No raw zips found in {RAW_DATA_DIR}")

    frames = []
    for zip_path in zip_paths:
        with zipfile.ZipFile(zip_path) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            for csv_name in csv_names:
                print(f"Reading {zip_path.name} -> {csv_name}")
                with zf.open(csv_name) as f:
                    df = pd.read_csv(f, dtype=str, keep_default_na=False, na_values=[""])
                    frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(combined):,} raw rows from {len(zip_paths)} zip(s)")
    return combined


def assign_administration(action_date: pd.Series) -> pd.Series:
    result = pd.Series(pd.NA, index=action_date.index, dtype="object")
    for start, end, label in ADMINISTRATIONS:
        mask = (action_date >= start) & (action_date < end)
        result.loc[mask] = label
    return result


def build_loc_id(state_fips: pd.Series, county_fips: pd.Series) -> pd.Series:
    """USA-{state_abbr}-{county3} to match the rest of the project's loc_id family.

    Falls back to USA-{state_abbr} when there's no county (e.g. statewide
    formula grants with no specific place of performance).
    """
    state_abbr = state_fips.map(STATE_FIPS)
    county3 = county_fips.str[-3:]

    loc_id = pd.Series(pd.NA, index=state_fips.index, dtype="object")

    has_county = state_abbr.notna() & county3.notna()
    loc_id.loc[has_county] = "USA-" + state_abbr.loc[has_county] + "-" + county3.loc[has_county]

    has_state_only = state_abbr.notna() & ~has_county
    loc_id.loc[has_state_only] = "USA-" + state_abbr.loc[has_state_only]

    return loc_id


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    missing = set(COLUMN_RENAMES) - set(raw.columns)
    if missing:
        raise ValueError(f"Raw CSV missing expected columns: {sorted(missing)}")

    df = raw[list(COLUMN_RENAMES)].rename(columns=COLUMN_RENAMES)

    before = len(df)
    df = df.drop_duplicates(subset="award_key")
    if len(df) != before:
        print(f"Dropped {before - len(df):,} duplicate award_key rows (chunk overlap)")

    money_cols = [
        "total_obligated_amount", "total_outlayed_amount",
        "total_non_federal_funding_amount", "total_funding_amount",
        "indirect_cost_federal_share_amount",
    ]
    for col in money_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["project_length_days"] = (
        df["period_of_performance_end_date"] - df["period_of_performance_start_date"]
    ).dt.days

    df["administration"] = assign_administration(
        df["award_base_action_date"].dt.strftime("%Y-%m-%d")
    )

    for col in STR_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("string")

    # Two loc_ids on purpose: recipient (who got the money, e.g. a DC-based
    # nonprofit's address) and place of performance (where the work actually
    # happens, e.g. a rural county). They're often different and both are
    # useful research questions.
    df["recipient_loc_id"] = build_loc_id(df["recipient_state_fips"], df["recipient_county_fips"])
    df["place_of_performance_loc_id"] = build_loc_id(
        df["place_of_performance_state_fips"], df["place_of_performance_county_fips"]
    )

    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw_csvs()
    df = transform(raw)

    print(f"Writing {len(df):,} rows, {len(df.columns)} columns -> {OUTPUT_FILE}")
    df.to_parquet(OUTPUT_FILE, index=False)

    file_size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Done. File size: {file_size_mb:.1f} MB")

    print("\nRows by award_type_description:")
    print(df["award_type_description"].value_counts().to_string())
    print("\nRows by administration:")
    print(df["administration"].value_counts(dropna=False).to_string())
    print(
        f"\nrecipient_loc_id coverage: {df['recipient_loc_id'].notna().mean():.1%}  "
        f"place_of_performance_loc_id coverage: {df['place_of_performance_loc_id'].notna().mean():.1%}"
    )
    mismatched = (
        df["recipient_loc_id"].notna()
        & df["place_of_performance_loc_id"].notna()
        & (df["recipient_loc_id"] != df["place_of_performance_loc_id"])
    )
    print(f"Awards where recipient_loc_id != place_of_performance_loc_id: {mismatched.sum():,} ({mismatched.mean():.1%})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
