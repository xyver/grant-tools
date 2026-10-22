"""Convert California Grants Portal raw CSVs into queryable parquet files.

Input:
    data/Raw data/california_grants_portal/
      - california-grants-portal-data.csv
      - grant-awards-fiscal-year-*.csv

Output:
    data/countries/USA/california_grants_portal/
      - opportunities.parquet
      - awards.parquet

This is a grant-analyzer evidence corpus, not a map pack. It keeps the
California source separate from USAspending federal awards because it answers
a different question: state-administered opportunities and state award
history, not federal CFDA precedent.
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DIR = RAW_ROOT / "california_grants_portal"
OUTPUT_DIR = DATA_ROOT / "countries" / "USA" / "california_grants_portal"
OPPORTUNITIES_FILE = OUTPUT_DIR / "opportunities.parquet"
AWARDS_FILE = OUTPUT_DIR / "awards.parquet"

OPPORTUNITIES_RAW = RAW_DIR / "california-grants-portal-data.csv"
AWARDS_GLOB = "grant-awards-fiscal-year-*.csv"

MONEY_COLUMNS = [
    "estimated_available_funds",
    "total_award_amount",
    "matching_funding_amount",
    "total_award_used",
]

OPPORTUNITY_DATE_COLUMNS = [
    "last_updated",
    "open_date",
    "application_deadline",
]

AWARD_DATE_COLUMNS = [
    "publish_date",
    "last_updated",
    "project_start_date",
    "project_end_date",
]

COLUMN_RENAMES = {
    "PortalID": "portal_id",
    "GrantID": "grant_id",
    "Status": "status",
    "LastUpdated": "last_updated",
    "ChangeNotes": "change_notes",
    "AgencyDept": "agency_dept",
    "Title": "title",
    "Type": "type",
    "LOI": "loi",
    "Categories": "categories",
    "CategorySuggestion": "category_suggestion",
    "Purpose": "purpose",
    "Description": "description",
    "ApplicantType": "applicant_type",
    "ApplicantTypeNotes": "applicant_type_notes",
    "Geography": "geography",
    "FundingSource": "funding_source",
    "FundingSourceNotes": "funding_source_notes",
    "MatchingFunds": "matching_funds",
    "MatchingFundsNotes": "matching_funds_notes",
    "EstAvailFunds": "estimated_available_funds",
    "EstAwards": "estimated_awards",
    "EstAmounts": "estimated_amounts",
    "FundingMethod": "funding_method",
    "FundingMethodNotes": "funding_method_notes",
    "OpenDate": "open_date",
    "ApplicationDeadline": "application_deadline",
    "AwardPeriod": "award_period",
    "ExpAwardDate": "expected_award_date",
    "ElecSubmission": "electronic_submission",
    "GrantURL": "grant_url",
    "AgencyURL": "agency_url",
    "AgencySubscribeURL": "agency_subscribe_url",
    "GrantEventsURL": "grant_events_url",
    "ContactInfo": "contact_info",
    "AwardStats": "award_stats",
    "PublishDate": "publish_date",
    "FiscalYear": "fiscal_year",
    "ProjectTitle": "project_title",
    "RecipientType": "recipient_type",
    "RecipientName": "recipient_name",
    "Sub-recipients": "sub_recipients",
    "TotalAwardAmount": "total_award_amount",
    "MatchingFundingAmount": "matching_funding_amount",
    "AwardAmountNotes": "award_amount_notes",
    "ProjectStartDate": "project_start_date",
    "ProjectEndDate": "project_end_date",
    "ProjectAbstract": "project_abstract",
    "GeographicLocationServed": "geographic_location_served",
    "GeographicLocationServedNotes": "geographic_location_served_notes",
    "CountiesServed": "counties_served",
    "ProjectStatus": "project_status",
    "TotalAwardUsed": "total_award_used",
    "AwardCancellingNotes": "award_cancelling_notes",
}


def _snake_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={col: COLUMN_RENAMES.get(col, _fallback_snake(col)) for col in df.columns})


def _fallback_snake(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()
    return text or "unnamed"


def _money_to_number(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False, na_values=[""])


def _normalize_shared(df: pd.DataFrame) -> pd.DataFrame:
    df = _snake_columns(df)
    for col in MONEY_COLUMNS:
        if col in df.columns:
            df[col] = _money_to_number(df[col])
    return df


def convert_opportunities() -> pd.DataFrame:
    if not OPPORTUNITIES_RAW.exists():
        raise FileNotFoundError(f"Missing raw opportunities CSV: {OPPORTUNITIES_RAW}")
    df = _normalize_shared(_read_csv(OPPORTUNITIES_RAW))
    for col in OPPORTUNITY_DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df["source_layer"] = "state_open_opportunity"
    df["source_name"] = "California Grants Portal"
    df["source_url"] = df.get("grant_url")
    df["geography_scope"] = "california"
    return df


def convert_awards() -> pd.DataFrame:
    paths = sorted(RAW_DIR.glob(AWARDS_GLOB))
    if not paths:
        raise FileNotFoundError(f"No raw award CSVs found in {RAW_DIR}")
    frames = []
    for path in paths:
        frame = _normalize_shared(_read_csv(path))
        frame = frame.dropna(axis=1, how="all")
        frame["source_file"] = path.name
        frames.append(frame)
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise ValueError("Award CSVs were found but all parsed as empty")
    df = pd.concat(frames, ignore_index=True)
    for col in AWARD_DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df["source_layer"] = "state_award_history"
    df["source_name"] = "California Grants Portal Grant Awards"
    df["geography_scope"] = "california"
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    opportunities = convert_opportunities()
    awards = convert_awards()

    print(f"Writing {len(opportunities):,} opportunities -> {OPPORTUNITIES_FILE}")
    opportunities.to_parquet(OPPORTUNITIES_FILE, index=False)
    print(f"Writing {len(awards):,} awards -> {AWARDS_FILE}")
    awards.to_parquet(AWARDS_FILE, index=False)

    print("\nOpportunity status counts:")
    print(opportunities["status"].value_counts(dropna=False).head(10).to_string())
    print("\nAward fiscal year counts:")
    print(awards["fiscal_year"].value_counts(dropna=False).sort_index().to_string())
    print("\nAward total by fiscal year:")
    totals = awards.groupby("fiscal_year", dropna=False)["total_award_amount"].sum().sort_index()
    for fiscal_year, total in totals.items():
        print(f"  {fiscal_year}: ${total:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
