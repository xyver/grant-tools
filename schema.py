"""
Shared schema validation for grant program CSVs, run at load time in both
backends. Fails fast and loud on a malformed CSV (unknown enum value,
missing required column, duplicate program_id) instead of letting bad
data surface later as a confusing downstream bug - e.g. a duplicate
program_id would silently make match_grant_programs() return two
different rows for what looks like one program, with no obvious cause.

Both backends share identical semantics for the tri-state fields and the
funds_what enum (see tristate.py and grant_funding_programs.md/
Canada%20grant%20research.md) even though their CSVs differ in other
columns (cfda_number is US-only, owner_org is Canada-only) - this module
validates the shared core, not the whole schema, on purpose.
"""

REQUIRED_COLUMNS = {
    "program_id", "program_name", "funder_type", "agency_or_funder",
    "funds_what", "eligible_applicant_types", "requires_government_intermediary",
    "requires_match_funding", "geography_scope", "sector_tags",
    "typical_award_size", "notes", "source_url",
}

VALID_TRISTATE = {"TRUE", "FALSE", "UNKNOWN"}
VALID_FUNDS_WHAT = {"direct_project", "application_assistance", "both"}


class SchemaError(ValueError):
    """A grant program CSV failed validation - see the message for which
    rule and which row(s)."""


def validate_programs_df(df, source_label):
    """Raises SchemaError on the first category of problem found, with
    every offending row/value listed (not just the first one) so a human
    fixing the CSV doesn't have to re-run this once per error."""
    errors = []

    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        raise SchemaError(f"{source_label}: missing required column(s): {sorted(missing_cols)}")

    dupes = df["program_id"][df["program_id"].duplicated()].tolist()
    if dupes:
        errors.append(f"duplicate program_id value(s): {sorted(set(dupes))}")

    null_required = {
        col: df[df[col].isna() | (df[col].astype(str).str.strip() == "")]["program_id"].tolist()
        for col in ("program_id", "program_name", "funder_type", "agency_or_funder", "funds_what")
    }
    for col, bad_ids in null_required.items():
        if bad_ids:
            errors.append(f"missing/blank required value in column '{col}' for program_id(s): {bad_ids}")

    for col in ("requires_government_intermediary", "requires_match_funding"):
        bad = df[~df[col].astype(str).str.strip().str.upper().isin(VALID_TRISTATE)]
        if not bad.empty:
            errors.append(
                f"column '{col}' has value(s) outside {{TRUE, FALSE, UNKNOWN}} for program_id(s): "
                f"{bad['program_id'].tolist()} (values: {bad[col].tolist()})"
            )

    bad_funds_what = df[~df["funds_what"].isin(VALID_FUNDS_WHAT)]
    if not bad_funds_what.empty:
        errors.append(
            f"column 'funds_what' has value(s) outside {sorted(VALID_FUNDS_WHAT)} for program_id(s): "
            f"{bad_funds_what['program_id'].tolist()} (values: {bad_funds_what['funds_what'].tolist()})"
        )

    if errors:
        raise SchemaError(f"{source_label}: " + "; ".join(errors))
