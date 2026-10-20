"""Convert BC provincial grant-award raw files into parquet.

Inputs:
  data/Raw data/bc_grant_awards/
    gaming_grants/*.xlsx
    bckdf/*.csv

Outputs:
  data/countries/CAN/bc_grant_awards/
    gaming_grants.parquet
    bckdf_awards.parquet
    awards.parquet

These are provincial award-history evidence tables for the grant analyzer.
They are not federal G&C records and should be surfaced separately from the
Canada federal grants_contributions parquet.
"""
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "Raw data" / "bc_grant_awards"
OUTPUT_DIR = PROJECT_ROOT / "data" / "countries" / "CAN" / "bc_grant_awards"

GAMING_OUT = OUTPUT_DIR / "gaming_grants.parquet"
BCKDF_OUT = OUTPUT_DIR / "bckdf_awards.parquet"
COMBINED_OUT = OUTPUT_DIR / "awards.parquet"


def _snake(value: str) -> str:
    text = str(value or "").replace("\n", " ")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "unnamed"


def _money_to_number(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _read_excel_table(path: Path, *, header: int) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=0, header=header, dtype=str, engine="openpyxl")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={col: _snake(col) for col in df.columns})


def _fiscal_year_start(fiscal_year: pd.Series) -> pd.Series:
    extracted = fiscal_year.astype("string").str.extract(r"(20\d{2}|19\d{2})", expand=False)
    return pd.to_numeric(extracted, errors="coerce").astype("Int64")


def convert_gaming_grants() -> pd.DataFrame:
    source_dir = RAW_DIR / "gaming_grants"
    historical_paths = sorted(source_dir.glob("fin-report-gaming-grant-historical*.xlsx*"))
    if not historical_paths:
        raise FileNotFoundError(f"Missing Gaming Grants historical workbook in {source_dir}")

    historical = _normalize_columns(_read_excel_table(historical_paths[0], header=13))
    historical = historical.dropna(how="all")
    historical = historical.rename(columns={
        "government_fiscal_year": "fiscal_year",
        "organization_name": "recipient_name",
        "payment_amount": "award_amount",
    })
    historical["source_file"] = historical_paths[0].name
    historical["electoral_district"] = pd.NA

    ytd_paths = sorted(source_dir.glob("year-to-date*grant*payments*.xlsx"))
    ytd_frames = []
    for path in ytd_paths:
        frame = _normalize_columns(_read_excel_table(path, header=9))
        frame = frame.dropna(how="all")
        frame = frame.rename(columns={
            "city": "city",
            "organization": "recipient_name",
            "grant_type": "grant_type",
            "grant_sector": "grant_sector",
            "payment_amount": "award_amount",
        })
        frame["fiscal_year"] = "FY 2025/26"
        frame["grant_subsector"] = pd.NA
        frame["electoral_district"] = pd.NA
        frame["source_file"] = path.name
        ytd_frames.append(frame)

    frames = [historical] + ytd_frames
    keep_cols = [
        "fiscal_year",
        "city",
        "recipient_name",
        "grant_type",
        "grant_sector",
        "grant_subsector",
        "award_amount",
        "electoral_district",
        "source_file",
    ]
    normalized = []
    for frame in frames:
        for col in keep_cols:
            if col not in frame.columns:
                frame[col] = pd.NA
        normalized.append(frame[keep_cols])
    df = pd.concat(normalized, ignore_index=True)
    fiscal_text = df["fiscal_year"].fillna("").astype(str).str.strip()
    df = df[fiscal_text.str.upper().str.startswith("FY")].copy()
    df["award_amount"] = _money_to_number(df["award_amount"])
    df["fiscal_year_start"] = _fiscal_year_start(df["fiscal_year"])
    df["recipient_loc_id"] = "CAN-BC"
    df["geography_scope"] = "british_columbia"
    df["source_layer"] = "provincial_award_history"
    df["source_name"] = "BC Gaming Grants Paid to Community Organizations"
    df["program_name"] = "Community Gaming Grants"
    return df


def convert_bckdf() -> pd.DataFrame:
    paths = sorted((RAW_DIR / "bckdf").glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"Missing BCKDF CSV in {RAW_DIR / 'bckdf'}")
    df = pd.read_csv(paths[0], encoding="utf-8-sig", dtype=str)
    df = _normalize_columns(df)
    df = df.rename(columns={
        "project": "project_number",
        "bc_institution": "recipient_name",
        "project_title": "project_title",
        "bckdf_project_type": "grant_type",
        "date_awarded": "award_date",
        "award_fiscal_year": "fiscal_year",
        "bckdf_contribution": "award_amount",
        "total_project_cost": "total_project_cost",
    })
    if "award_amount" in df.columns:
        df["award_amount"] = _money_to_number(df["award_amount"])
    if "total_project_cost" in df.columns:
        df["total_project_cost"] = _money_to_number(df["total_project_cost"])
    if "award_date" in df.columns:
        df["award_date"] = pd.to_datetime(df["award_date"], format="%d-%b-%y", errors="coerce")
    df["fiscal_year_start"] = _fiscal_year_start(df.get("fiscal_year", pd.Series(index=df.index, dtype="object")))
    df["recipient_loc_id"] = "CAN-BC"
    df["geography_scope"] = "british_columbia"
    df["source_layer"] = "provincial_award_history"
    df["source_name"] = "BC Knowledge Development Fund"
    df["program_name"] = "BC Knowledge Development Fund"
    df["source_file"] = paths[0].name
    keep_cols = [
        "project_number",
        "recipient_name",
        "researcher_last_name",
        "researcher_first_name",
        "project_title",
        "grant_type",
        "award_date",
        "fiscal_year",
        "fiscal_year_start",
        "award_amount",
        "total_project_cost",
        "recipient_loc_id",
        "geography_scope",
        "source_layer",
        "source_name",
        "program_name",
        "source_file",
    ]
    for col in keep_cols:
        if col not in df.columns:
            df[col] = pd.NA
    recipient_text = df["recipient_name"].fillna("").astype(str).str.strip()
    df = df[(recipient_text != "") | df["award_amount"].notna()].copy()
    return df[keep_cols]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gaming = convert_gaming_grants()
    bckdf = convert_bckdf()

    print(f"Writing {len(gaming):,} BC Gaming Grants rows -> {GAMING_OUT}")
    gaming.to_parquet(GAMING_OUT, index=False)
    print(f"Writing {len(bckdf):,} BCKDF rows -> {BCKDF_OUT}")
    bckdf.to_parquet(BCKDF_OUT, index=False)

    combined_columns = [
        "program_name",
        "source_name",
        "source_layer",
        "fiscal_year",
        "fiscal_year_start",
        "award_date",
        "city",
        "recipient_name",
        "recipient_loc_id",
        "grant_type",
        "grant_sector",
        "grant_subsector",
        "project_title",
        "award_amount",
        "total_project_cost",
        "geography_scope",
        "source_file",
    ]
    parts = []
    for frame in (gaming, bckdf):
        part = frame.reindex(columns=combined_columns)
        parts.append(part.dropna(axis=1, how="all"))
    combined = pd.concat(parts, ignore_index=True, sort=False).reindex(columns=combined_columns)
    print(f"Writing {len(combined):,} combined BC award rows -> {COMBINED_OUT}")
    combined.to_parquet(COMBINED_OUT, index=False)

    print("\nBC Gaming Grants fiscal year counts:")
    print(gaming["fiscal_year"].value_counts(dropna=False).sort_index().tail(10).to_string())
    print("\nBCKDF fiscal year counts:")
    print(bckdf["fiscal_year"].value_counts(dropna=False).sort_index().tail(10).to_string())
    print("\nAward totals:")
    print(f"  Gaming Grants: ${gaming['award_amount'].sum(skipna=True):,.0f}")
    print(f"  BCKDF: ${bckdf['award_amount'].sum(skipna=True):,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
