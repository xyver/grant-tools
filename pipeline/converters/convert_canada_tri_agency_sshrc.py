"""
Convert raw SSHRC (Social Sciences and Humanities Research Council)
award CSVs (from download_canada_tri_agency.py) into a queryable parquet.

Input: data/Raw data/canada_tri_agency/sshrc/*.csv
       (one file per fiscal year, 1998-2024, 27 files)

Output: data/countries/CAN/tri_agency_sshrc/CAN.parquet
Standalone research dataset, kept separate from NSERC/CIHR/
grants_contributions on purpose (see live/private_mcp_tools/grant_analyzer/docs/
Canada%20grant%20research.md). Unlike NSERC and CIHR, SSHRC's real,
confirmed limitation is that it has NO abstract/summary field at all,
in any year - only a Title and a semicolon-delimited Keywords tag list.
This is preserved here as a real gap, not papered over with a fabricated
summary field or silently dropped from the project because it's "the
weak one" - SSHRC's discipline/area-of-research taxonomy is still real,
useful precedent signal, just not full-text.

Same column-drift problem as NSERC, confirmed by direct inspection across
1998/2015/2024 headers: 'Province_EN' vs 'ProvinceEN'; 'AwardAmount' vs
' AwardAmount ' (literal leading/trailing spaces in the 2015-16 file) vs
'Amount-Montant'; 'Title' vs 'Title-Titre'; 'DisciplineEN'/'MainDisciplineEN'
(older years) vs 'SSHRC_Discipline_EN'/'SSHRC_Main_Discipline' (newer years).
Same fuzzy-role-matching approach as the NSERC converter - canonical roles
get a stable name across all 27 years, everything else passes through
unmodified via outer-join concat.

Usage:
    python convert_canada_tri_agency_sshrc.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DIR = RAW_ROOT / "canada_tri_agency" / "sshrc"
OUTPUT_DIR = DATA_ROOT / "countries" / "CAN" / "tri_agency_sshrc"
OUTPUT_FILE = OUTPUT_DIR / "CAN.parquet"

ROLE_CANDIDATES = [
    ("record_key", ["cle"]),
    ("pi_name", ["namenom"]),
    ("institution", ["institutionetablissement", "institution"]),
    ("province_en", ["provinceen"]),
    ("fiscal_year", ["fiscalyearexercicefinancier", "fiscalyear"]),
    ("competition_year", ["competitionyear"]),
    ("award_amount", ["awardamount", "amountmontant"]),
    # "ProgramNaneEN" is an upstream typo (2014); from 2016 the column is just "Program".
    ("program_name_en", ["programnameen", "programnaneen", "program"]),
    ("title", ["titletitre", "title"]),
    ("keywords", ["keywordsmotscles", "keywords"]),
    ("discipline_en", ["sshrcdisciplineen", "disciplineen"]),
    ("main_discipline_en", ["sshrcmaindiscipline", "maindisciplineen"]),
    ("area_of_research_en", ["sshrcareaofresearch", "areaofresearchen"]),
]

PROVINCE_TO_ABBR = {
    "ONTARIO": "ON", "QUEBEC": "QC", "QUÉBEC": "QC", "BRITISH COLUMBIA": "BC",
    "ALBERTA": "AB", "SASKATCHEWAN": "SK", "MANITOBA": "MB", "NOVA SCOTIA": "NS",
    "NEW BRUNSWICK": "NB", "NEWFOUNDLAND AND LABRADOR": "NL",
    "PRINCE EDWARD ISLAND": "PE", "NORTHWEST TERRITORIES": "NT",
    "YUKON": "YT", "YUKON TERRITORY": "YT", "NUNAVUT": "NU",
    # Confirmed real value, deliberately NOT mapped to a province - it
    # means "no Canadian province applies," not "unknown."
    "OUTSIDE OF CANADA": pd.NA,
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
    """Same real encoding split found in the NSERC files - older fiscal
    years are not valid UTF-8, confirmed by direct probing, and decode
    cleanly as cp1252 (Windows-1252) instead."""
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
    # The 2017-18 file ends in thousands of empty CSV lines; they carry no key.
    blank = df["record_key"].isna() & df["pi_name"].isna() & df["award_amount"].isna()
    df = df.loc[~blank].copy()
    if "award_amount" in df.columns:
        # 2015-2020 files write amounts as " $302,000.00 ". Cast to string
        # first: text may arrive as object or as pandas' string dtype.
        cleaned = df["award_amount"].astype("string").str.replace(r"[\s$,]", "", regex=True)
        df["award_amount"] = pd.to_numeric(cleaned, errors="coerce")
    df["loc_id"] = build_loc_id(df["province_en"])
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw()
    df = transform(raw)

    print(f"Writing {len(df):,} rows, {len(df.columns)} columns -> {OUTPUT_FILE}")
    df.to_parquet(OUTPUT_FILE, index=False)

    file_size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Done. File size: {file_size_mb:.1f} MB")

    print(f"\nloc_id coverage: {df['loc_id'].notna().mean():.1%} "
          "(remainder includes real 'Outside of Canada' rows plus unmatched values)")
    print("\nNo abstract/summary field exists in this source - confirmed during "
          "research, not a converter gap. 'keywords' and the discipline/area-of-"
          "research columns are the only content-richness fields available.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
