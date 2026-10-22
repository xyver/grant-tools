"""
Convert raw NSERC (Natural Sciences and Engineering Research Council)
award CSVs (from download_canada_tri_agency.py) into a queryable parquet.

Input: data/Raw data/canada_tri_agency/nserc/*.csv
       (one file per fiscal year, 1991-2024, 34 files)

Output: data/countries/CAN/tri_agency_nserc/CAN.parquet
Standalone research dataset - the Canadian analog of NSF Award Search,
kept separate from SSHRC/CIHR/grants_contributions on purpose (see
live/private_mcp_tools/grant_analyzer/docs/Canada%20grant%20research.md's "treat each one
carefully" framing) since this source's real richness is project-level
abstract text, not present at all in SSHRC and structured completely
differently in CIHR.

Real finding driving this script's design: the column names are NOT
stable across 34 years of files. Confirmed by direct inspection -
'ProgramNaneEN' (a typo, 1991 only) vs 'ProgramNameEN' (corrected,
every later year); 'Keyword' (1991-2009 era) vs 'Keywords' (2010+);
'FiscalYear-Exercice-financier' vs 'FiscalYear-Exercice financier'
(hyphen vs space); extra columns present only in some eras (Partie/
Nb_Partie in early years, FieldOfResearchListCodes/
SocioeconomicObjectiveListCodes only from a certain year forward). A
fixed rename dict keyed on exact source column names would silently drop
data in whichever years don't match exactly - this script instead
fuzzy-matches each year's real header against a small set of canonical
roles needed for cross-year analysis, and otherwise lets pandas' outer-
join concat carry every other column through unmodified (NaN in the
years that don't have it) rather than silently dropping anything.

Real finding worth keeping visible downstream, not normalized away:
'ApplicationSummary' is NOT a clean abstract for every row - spot-checked
against FY2024 (22,627 rows): 77% have ApplicationSummary genuinely
distinct from Keywords (real free-text project descriptions), the
remaining 23% have ApplicationSummary identical to Keywords (some award
types apparently never collect a real summary). Both are kept as-is;
no attempt is made here to flag/strip the keyword-duplicate rows - that's
a precedent-matching-layer decision, not a data-cleaning one.

Usage:
    python convert_canada_tri_agency_nserc.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DIR = RAW_ROOT / "canada_tri_agency" / "nserc"
OUTPUT_DIR = DATA_ROOT / "countries" / "CAN" / "tri_agency_nserc"
OUTPUT_FILE = OUTPUT_DIR / "CAN.parquet"

# Ordered (role -> normalized-substring candidates) - first match wins per
# column. Normalization strips everything but lowercase letters/digits, so
# 'FiscalYear-Exercice financier' and 'FiscalYear-Exercice-financier' both
# normalize to 'fiscalyearexercicefinancier'.
ROLE_CANDIDATES = [
    ("application_id", ["applicationid"]),
    ("pi_name", ["namenom"]),
    ("organization_id", ["organizationid"]),
    ("institution", ["institutiontablissement", "institution"]),
    ("province_en", ["provinceen"]),
    ("country_en", ["countryen"]),
    ("fiscal_year", ["fiscalyearexercicefinancier", "fiscalyear"]),
    ("competition_year", ["competitionyearanneedeconcours", "competitionyear"]),
    ("award_amount", ["awardamount"]),
    ("program_id", ["programid"]),
    ("program_name_en", ["programnameen", "programnaneen"]),  # 1991 typo variant
    ("application_title", ["applicationtitle"]),
    ("keywords", ["keywords", "keyword"]),
    ("application_summary", ["applicationsummary"]),
]

PROVINCE_TO_ABBR = {
    "ONTARIO": "ON", "QUEBEC": "QC", "QUÉBEC": "QC", "BRITISH COLUMBIA": "BC",
    "ALBERTA": "AB", "SASKATCHEWAN": "SK", "MANITOBA": "MB", "NOVA SCOTIA": "NS",
    "NEW BRUNSWICK": "NB", "NEWFOUNDLAND AND LABRADOR": "NL",
    "PRINCE EDWARD ISLAND": "PE", "NORTHWEST TERRITORIES": "NT",
    "YUKON": "YT", "YUKON TERRITORY": "YT", "NUNAVUT": "NU",
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_rename_map(columns) -> dict:
    normalized = {_normalize(c): c for c in columns}
    rename = {}
    used_targets = set()
    for role, candidates in ROLE_CANDIDATES:
        if role in used_targets:
            continue
        match = None
        for cand in candidates:
            if cand in normalized:
                match = normalized[cand]
                break
        if match is None:
            for norm_name, orig in normalized.items():
                if any(cand in norm_name for cand in candidates):
                    match = orig
                    break
        if match is not None:
            rename[match] = role
            used_targets.add(role)
    return rename


def read_csv_any_encoding(path: Path) -> pd.DataFrame:
    """Real finding: 12 of 34 NSERC files are not valid UTF-8 (older fiscal
    years) - confirmed by direct probing, not assumed. They decode cleanly
    as cp1252 (Windows-1252), the encoding NSERC's open-data exports were
    historically produced in before a later switch to UTF-8."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False, na_values=[""])
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1252", dtype=str, keep_default_na=False, na_values=[""])


def load_raw() -> pd.DataFrame:
    paths = sorted(RAW_DIR.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No raw CSVs found in {RAW_DIR} - run download_canada_tri_agency.py first")

    frames = []
    all_roles_seen = set()
    for path in paths:
        df = read_csv_any_encoding(path)
        df.columns = [c.strip() for c in df.columns]
        rename = build_rename_map(df.columns)
        df = df.rename(columns=rename)
        all_roles_seen.update(rename.values())
        df["source_file"] = path.name
        frames.append(df)

    missing_roles = {role for role, _ in ROLE_CANDIDATES} - all_roles_seen
    if missing_roles:
        print(f"WARNING: these canonical roles were never matched in any file: {sorted(missing_roles)}")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    print(f"Loaded {len(combined):,} rows from {len(paths)} files, {len(combined.columns)} total columns "
          f"(canonical + source-specific passthrough)")
    return combined


def build_loc_id(province_en: pd.Series) -> pd.Series:
    normalized = province_en.fillna("").str.strip().str.upper()
    abbr = normalized.map(PROVINCE_TO_ABBR)
    loc_id = pd.Series(pd.NA, index=province_en.index, dtype="object")
    matched = abbr.notna()
    loc_id.loc[matched] = "CAN-" + abbr.loc[matched]
    return loc_id


def transform(df: pd.DataFrame) -> pd.DataFrame:
    df["award_amount"] = pd.to_numeric(df["award_amount"], errors="coerce")
    df["loc_id"] = build_loc_id(df["province_en"])

    summary = df["application_summary"].fillna("")
    keywords = df["keywords"].fillna("")
    df["summary_is_real_abstract"] = (summary != "") & (summary != keywords)

    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw()
    df = transform(raw)

    print(f"Writing {len(df):,} rows, {len(df.columns)} columns -> {OUTPUT_FILE}")
    df.to_parquet(OUTPUT_FILE, index=False)

    file_size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Done. File size: {file_size_mb:.1f} MB")

    print(f"\nloc_id coverage: {df['loc_id'].notna().mean():.1%}")
    print(f"summary_is_real_abstract: {df['summary_is_real_abstract'].mean():.1%} of rows")
    print(f"\nFiscal years covered: {sorted(df['fiscal_year'].dropna().unique())[:3]} ... "
          f"{sorted(df['fiscal_year'].dropna().unique())[-3:]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
