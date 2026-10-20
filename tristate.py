"""
Shared TRUE/FALSE/UNKNOWN parsing for grant program CSV fields.

Real bug this exists to prevent: both backends' CSVs store
requires_match_funding/requires_government_intermediary as one of
TRUE/FALSE/UNKNOWN (Canada's table uses UNKNOWN on every row today, since
no Canadian program's actual terms/NOFO-equivalent text has been read
yet). A naive `str(value).upper() == "TRUE"` check (the US backend's
original logic, before this fix) treats UNKNOWN identically to FALSE -
silently coercing "we don't know if this is required" into "no blocker,"
which is a stronger, false claim. UNKNOWN must reduce confidence and emit
an action item, never collapse into "ok."
"""


def parse_tristate(value):
    """Returns True, False, or None (meaning unknown/unset) - never coerces
    an unrecognized value to False."""
    text = str(value).strip().upper()
    if text == "TRUE":
        return True
    if text == "FALSE":
        return False
    return None
