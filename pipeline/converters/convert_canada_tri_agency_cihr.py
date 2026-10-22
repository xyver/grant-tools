"""
Convert raw CIHR (Canadian Institutes of Health Research) investment
XLSX files (from download_canada_tri_agency.py) into a queryable parquet.

Input: data/Raw data/canada_tri_agency/cihr/*.xlsx
       (one file per fiscal year, 2000-01 through 2025-26, 26 files, each
       with 5 sheets: G&A_S&B, Research_Team, Research_Categories,
       Partners, Institutions)

Output: data/countries/CAN/tri_agency_cihr/CAN.parquet
Standalone research dataset, kept separate from NSERC/SSHRC/
grants_contributions on purpose (see live/private_mcp_tools/grant_analyzer/docs/
Canada%20grant%20research.md). This is the richest of the four sources -
`ApplicationAbstract_ResumeDemande` carries genuine full-paragraph
project abstracts (confirmed by inspection: real multi-sentence
descriptions of study aims/methods/population, not boilerplate),
comparable in depth to NIH RePORTER - richer than NSERC's
ApplicationSummary and far richer than SSHRC, which has no abstract field
at all.

Only the `G&A_S&B` sheet (one row per award/grant-and-award/
salary-and-bursary payment) is converted here - same "main award record
only" scoping as NSERC/SSHRC excluding their Co-Applicants/Partners
sub-files. The other 4 sheets (Research_Team, Research_Categories,
Partners, Institutions) are real, confirmed-present, and NOT yet
converted - a later enrichment pass could join Research_Categories in
for taxonomy tags the way SSHRC's discipline columns work, but that's
deferred until a real use case asks for it.

Schema confirmed stable across the full 26-year span by direct
inspection of the 2000-01 and 2024-25 headers (byte-identical column
list) - unlike NSERC/SSHRC, no fuzzy column-matching was needed here.

No province/location field exists anywhere in this sheet (confirmed
during research) - institution names occasionally embed a city/province
in parentheses as free text (e.g. "St. Michael's Hospital (Toronto,
Ontario)"), but it's not a reliable, consistently-formatted field, so no
loc_id is built here rather than inventing a parser for inconsistent
free text. If location join is needed later, ResearchInstitutionCode_
CodeEtablissementRecherche could potentially join against the
Institutions sheet at that point.

Usage:
    python convert_canada_tri_agency_cihr.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DIR = RAW_ROOT / "canada_tri_agency" / "cihr"
OUTPUT_DIR = DATA_ROOT / "countries" / "CAN" / "tri_agency_cihr"
OUTPUT_FILE = OUTPUT_DIR / "CAN.parquet"

SHEET_NAME = "G&A_S&B"

MONEY_COLUMNS = [
    "AmountPaidFY_MontantPayeAF",
    "TotalAmountPaid_MontantTotalPaye",
    "TotalAmountAwarded_MontantTotalAccorde",
]
DATE_COLUMNS = [
    "CompetitionDate_DateConcours",
    "FundingStartDate_DatePremierVersement",
    "FundingEndDate_DateDernierVersement",
]


def load_raw() -> pd.DataFrame:
    paths = sorted(RAW_DIR.glob("*.xlsx"))
    if not paths:
        raise FileNotFoundError(f"No raw XLSX files found in {RAW_DIR} - run download_canada_tri_agency.py first")

    frames = []
    expected_columns = None
    for path in paths:
        df = pd.read_excel(path, sheet_name=SHEET_NAME, dtype=str)
        if expected_columns is None:
            expected_columns = list(df.columns)
        elif list(df.columns) != expected_columns:
            print(f"WARNING: {path.name} has a different column set than expected - "
                  "schema drift not seen during research, check this file manually.")
        df["source_file"] = path.name
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True, sort=False)
    print(f"Loaded {len(combined):,} rows from {len(paths)} files, {len(combined.columns)} columns")
    return combined


def transform(df: pd.DataFrame) -> pd.DataFrame:
    for col in MONEY_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    abstract = df["ApplicationAbstract_ResumeDemande"].fillna("")
    keywords = df["ApplicationKeywords_MotsClesDemande"].fillna("")
    df["has_real_abstract"] = (abstract.str.len() > 0) & (abstract != keywords)

    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw()
    df = transform(raw)

    print(f"Writing {len(df):,} rows, {len(df.columns)} columns -> {OUTPUT_FILE}")
    df.to_parquet(OUTPUT_FILE, index=False)

    file_size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Done. File size: {file_size_mb:.1f} MB")

    print(f"\nhas_real_abstract: {df['has_real_abstract'].mean():.1%} of rows")
    print("\nTop ProgramTypeEN values:")
    print(df["ProgramTypeEN_TypeProgrammeAN"].value_counts(dropna=False).head(8).to_string())
    print(f"\nFiscal years covered: {sorted(df['FiscalYear_AnneeFinanciere'].dropna().unique())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
