"""Which country data bundles are installed, and whether this code can read them.

Each bundle carries a manifest. Bundle schema 2 and later put it with its own
data at countries/<ISO3>/grant_data_manifest.json, so the USA and Canada
bundles can share one data folder. The 0.1.0 bundles (schema 1, no
schema_version field) put it at the data root, where unzipping a second
bundle overwrote the first; that location is still read so those installs
keep working.

Bump BUNDLE_SCHEMA_VERSION when a bundle's layout or table columns change in a
way the code depends on, and keep the oldest still-readable version in
MIN_SUPPORTED_SCHEMA. build/build_country_bundle.py stamps the current
version into every manifest it writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime_paths import DATA_ROOT

BUNDLE_SCHEMA_VERSION = 2
MIN_SUPPORTED_SCHEMA = 1
MANIFEST_NAME = "grant_data_manifest.json"
COUNTRY_DIRS = {"US": "USA", "CAN": "CAN"}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def manifest_for(country_code: str, data_root: Path | None = None) -> dict[str, Any] | None:
    """The installed bundle manifest for one country, or None."""
    root = data_root or DATA_ROOT
    iso3 = COUNTRY_DIRS.get(country_code, country_code)
    manifest = _read_json(root / "countries" / iso3 / MANIFEST_NAME)
    if manifest is not None:
        return manifest
    legacy = _read_json(root / MANIFEST_NAME)
    if legacy and str(legacy.get("country") or "").upper() == iso3:
        return legacy
    return None


def bundle_status(country_code: str, data_root: Path | None = None) -> dict[str, Any]:
    """Installed / version / compatible, plus a note when something is off."""
    root = data_root or DATA_ROOT
    iso3 = COUNTRY_DIRS.get(country_code, country_code)
    has_tables = (root / "countries" / iso3).is_dir()
    manifest = manifest_for(country_code, root)
    status: dict[str, Any] = {
        "country": iso3,
        "installed": has_tables,
        "version": (manifest or {}).get("version"),
        "schema_version": None,
        "compatible": None,
        "note": None,
    }
    if not has_tables:
        status["note"] = (
            f"No {iso3} data under GRANT_DATA_ROOT ({root}). Discovery still works from the "
            "program catalog; precedent and award evidence need the country bundle."
        )
        return status
    if manifest is None:
        status["note"] = f"{iso3} tables found but no bundle manifest; treating them as a local pipeline build."
        return status
    schema = int(manifest.get("schema_version") or 1)
    status["schema_version"] = schema
    if schema > BUNDLE_SCHEMA_VERSION:
        status["compatible"] = False
        status["note"] = (
            f"The {iso3} bundle uses schema {schema}, newer than this code reads "
            f"({BUNDLE_SCHEMA_VERSION}). Update the grant-tools code."
        )
    elif schema < MIN_SUPPORTED_SCHEMA:
        status["compatible"] = False
        status["note"] = (
            f"The {iso3} bundle uses schema {schema}, older than this code reads "
            f"({MIN_SUPPORTED_SCHEMA}+). Download the current bundle."
        )
    else:
        status["compatible"] = True
        if schema < BUNDLE_SCHEMA_VERSION:
            status["note"] = (
                f"The {iso3} bundle is an older release (schema {schema}); a newer bundle "
                "is available from the Grant Tools download page."
            )
    return status
