"""
Government of Canada Proactive Disclosure - Grants and Contributions Downloader

The Canadian analog of USAspending: a single consolidated, all-departments
bulk CSV of every federal grant/contribution over $25,000, required under
the 2018 Policy on Government of Canada Publishing / Access to Information
Act proactive-disclosure rules. Confirmed live 2026-06-28 via the CKAN API
(open.canada.ca) - one real resource (`grants.csv`), not a per-department
split that needs stitching. See ../../live/private_mcp_tools/grant_analyzer/docs/
Canada grant research.md for the full verification notes.

No API key, no documented row cap (unlike USAspending's undocumented
500,000-row ceiling - untested here, flagged as unverified). Refreshed
quarterly by Treasury Board of Canada Secretariat.

Usage:
    python download_canada_grants_contributions.py

Output:
    Raw data saved to: data/Raw data/canada_grants_contributions/
    - grants.csv (~2.1GB, all departments combined, one row per
      agreement/amendment)
    - manifest.json (download info: size, resource metadata, timestamp)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DATA_DIR = RAW_ROOT / "canada_grants_contributions"

CKAN_PACKAGE_ID = "432527ab-7aac-45b5-81d6-7597107a7013"
CKAN_PACKAGE_SHOW_URL = f"https://open.canada.ca/data/api/3/action/package_show?id={CKAN_PACKAGE_ID}"
GRANTS_RESOURCE_ID = "1d15a62f-5656-49ad-8c88-f40ce689d831"

TIMEOUT = 60
DOWNLOAD_TIMEOUT = 1800  # 30 minutes - this is a ~2.1GB file


def get_resource_metadata():
    """Look up the real resource URL/metadata from CKAN rather than hardcoding it,
    so a future re-run picks up a rotated resource_id automatically."""
    print(f"Querying CKAN package metadata: {CKAN_PACKAGE_SHOW_URL}")
    resp = requests.get(CKAN_PACKAGE_SHOW_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    package = resp.json()["result"]

    for resource in package["resources"]:
        if resource["id"] == GRANTS_RESOURCE_ID:
            return package, resource

    # Resource id changed - fall back to name match, but flag it loudly.
    for resource in package["resources"]:
        if resource.get("name", "").strip().lower() == "proactive disclosure - grants and contributions":
            print("WARNING: known resource_id not found, matched by name instead - "
                  "update GRANTS_RESOURCE_ID in this script.")
            return package, resource

    raise RuntimeError("Could not find the grants.csv resource in the CKAN package - "
                        "the dataset structure may have changed, inspect package_show output manually.")


def download_grants_csv(resource, output_path):
    url = resource["url"]
    print(f"\nDownloading: {url}")

    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    downloaded = 0

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size:
                pct = downloaded / total_size * 100
                print(f"\r  Progress: {pct:5.1f}% ({downloaded / 1024 / 1024:,.0f} MB / "
                      f"{total_size / 1024 / 1024:,.0f} MB)", end="")
    print()
    return downloaded


def main():
    print("=" * 70)
    print("Canada Grants & Contributions Proactive Disclosure Downloader")
    print("=" * 70)

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {RAW_DATA_DIR}")

    package, resource = get_resource_metadata()

    output_path = RAW_DATA_DIR / "grants.csv"
    downloaded_bytes = download_grants_csv(resource, output_path)

    manifest = {
        "download_date": datetime.now(timezone.utc).isoformat(),
        "source": "Treasury Board of Canada Secretariat - Proactive Disclosure of Grants and Contributions",
        "ckan_package_url": f"https://open.canada.ca/data/en/dataset/{CKAN_PACKAGE_ID}",
        "ckan_package_show_url": CKAN_PACKAGE_SHOW_URL,
        "resource_id": resource["id"],
        "resource_name": resource.get("name"),
        "resource_format": resource.get("format"),
        "resource_last_modified": resource.get("last_modified") or resource.get("metadata_modified"),
        "update_frequency": package.get("frequency"),
        "downloaded_bytes": downloaded_bytes,
        "downloaded_mb": round(downloaded_bytes / 1024 / 1024, 1),
        "notes": [
            "Single consolidated all-department file - no per-department stitching needed.",
            "Fields confirmed (2026-06-28): ref_number, amendment_number, amendment_date, "
            "agreement_type, recipient_type, recipient_business_number, recipient_legal_name, "
            "recipient_operating_name, research_organization_name, recipient_country, "
            "recipient_province, recipient_city, recipient_postal_code, federal_riding_name_en/fr, "
            "federal_riding_number, prog_name_en/fr, prog_purpose_en/fr, agreement_title_en/fr, "
            "agreement_number, agreement_value, foreign_currency_type/value, agreement_start_date, "
            "agreement_end_date, coverage, description_en/fr, naics_identifier, "
            "expected_results_en/fr, additional_information_en/fr, owner_org, owner_org_title.",
            "Location granularity is province + city only - no county/CD-equivalent field.",
            "No CFDA-style program code - prog_name_en/fr (free text) is the closest analog.",
            "No documented row cap found - unlike USAspending's undocumented 500,000-row "
            "ceiling, this is a flat single-file download, not a paginated API; row-cap risk "
            "is believed low but untested at volume.",
        ],
    }

    manifest_path = RAW_DATA_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nSaved: {output_path} ({manifest['downloaded_mb']:,.1f} MB)")
    print(f"Manifest: {manifest_path}")
    return 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_canada_grants_contributions", main))
