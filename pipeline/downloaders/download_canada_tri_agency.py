"""
Canada Tri-Agency Research Funding Downloader (NSERC / SSHRC / CIHR)

The Canadian analog of NIH RePORTER / NSF Award Search for the researcher/
university use case: per-agency bulk award history for the three federal
research-granting agencies. Confirmed live 2026-06-28 via CKAN
package_show on open.canada.ca - real per-fiscal-year resource lists, no
combined Tri-Agency dataset exists (ruled out, see Canada grant research.md).

Deliberately pulls only the main award/payment/investment file per year -
NOT the Co-Applicants/Partners sub-files - same "one row per award" scope
as the USAspending pull. Per-year files are small (NSERC: 12-57MB/year
observed; SSHRC/CIHR comparable), so full history is pulled for all three
rather than windowing to ~20 years - there's no size reason to trim, unlike
USAspending where the row cap forced chunking.

Sources (all confirmed live 2026-06-28):
  - NSERC: 1991-2024, resource name pattern "{year} Awards"
  - SSHRC: 1998-2024, resource name pattern "{year} Payments" (case varies
    by year in the source data - matched case-insensitively)
  - CIHR: FY2000-01 to FY2025-26, resource name pattern
    "CIHR Investments {YYYY-YY}" (XLSX, not CSV - the only one of the
    three not hosted as CSV)

Usage:
    python download_canada_tri_agency.py

Output:
    Raw data saved to: data/Raw data/canada_tri_agency/
    - nserc/NSERC_FY{year}_Expenditures.csv (or whatever the real filename is)
    - sshrc/SSHRC_FYR{year}_AWARD.csv
    - cihr/cihr_investments_investissements_irsc_{yyyyyy}.xlsx
    - manifest.json (per-file download results, sizes, source resource ids)
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "Raw data" / "canada_tri_agency"

TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300

AGENCIES = {
    "nserc": {
        "label": "NSERC",
        "package_id": "c1b0f627-8c29-427c-ab73-33968ad9176e",
        "name_pattern": re.compile(r"^(\d{4})\s+Awards$", re.IGNORECASE),
    },
    "sshrc": {
        "label": "SSHRC",
        "package_id": "b4e2b302-9bc6-4b33-b880-6496f8cef0f1",
        "name_pattern": re.compile(r"^(\d{4})\s+Payments$", re.IGNORECASE),
    },
    "cihr": {
        "label": "CIHR",
        "package_id": "49edb1d7-5cb4-4fa7-897c-515d1aad5da3",
        "name_pattern": re.compile(r"^CIHR Investments\s+(\d{4}-\d{2})$", re.IGNORECASE),
    },
}


def package_show(package_id):
    url = f"https://open.canada.ca/data/api/3/action/package_show?id={package_id}"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["result"]


def matching_resources(package, name_pattern):
    matches = []
    for resource in package["resources"]:
        name = (resource.get("name") or "").strip()
        m = name_pattern.match(name)
        if m:
            matches.append((m.group(1), resource))
    matches.sort(key=lambda pair: pair[0])
    return matches


def download_file(url, output_path):
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()
    total = 0
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            f.write(chunk)
            total += len(chunk)
    return total


def download_agency(key, config, manifest):
    label = config["label"]
    out_dir = RAW_DATA_DIR / key
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n--- {label} ---")
    package = package_show(config["package_id"])
    matches = matching_resources(package, config["name_pattern"])
    print(f"  Found {len(matches)} matching year resources "
          f"({matches[0][0]}-{matches[-1][0]})" if matches else "  No matches found")

    agency_manifest = {
        "package_id": config["package_id"],
        "package_url": f"https://open.canada.ca/data/en/dataset/{config['package_id']}",
        "update_frequency": package.get("frequency"),
        "years_downloaded": [],
        "total_bytes": 0,
        "failures": [],
    }

    for year_label, resource in matches:
        url = resource["url"]
        filename = url.rsplit("/", 1)[-1]
        output_path = out_dir / filename
        try:
            size = download_file(url, output_path)
            agency_manifest["years_downloaded"].append({
                "year": year_label,
                "filename": filename,
                "resource_id": resource["id"],
                "bytes": size,
            })
            agency_manifest["total_bytes"] += size
            print(f"  {year_label}: {filename} ({size / 1024 / 1024:.1f} MB)")
        except Exception as e:
            agency_manifest["failures"].append({"year": year_label, "url": url, "error": str(e)})
            print(f"  {year_label}: FAILED - {e}")

    manifest[key] = agency_manifest
    print(f"  {label} total: {agency_manifest['total_bytes'] / 1024 / 1024:.1f} MB, "
          f"{len(agency_manifest['years_downloaded'])} years, "
          f"{len(agency_manifest['failures'])} failures")


def main():
    print("=" * 70)
    print("Canada Tri-Agency Research Funding Downloader (NSERC / SSHRC / CIHR)")
    print("=" * 70)

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "download_date": datetime.now(timezone.utc).isoformat(),
        "source": "NSERC / SSHRC / CIHR open data via open.canada.ca CKAN API",
        "notes": [
            "No combined Tri-Agency dataset exists - confirmed 2026-06-28, three "
            "separate per-agency pulls is the correct approach.",
            "Co-Applicants/Partners sub-files deliberately excluded - main award/"
            "payment/investment file only, matching the USAspending 'one row per "
            "award' scope.",
            "CIHR files are XLSX, not CSV - the converter will need openpyxl/pandas "
            "read_excel rather than a plain CSV reader.",
        ],
        "agencies": {},
    }

    for key, config in AGENCIES.items():
        download_agency(key, config, manifest["agencies"])

    manifest_path = RAW_DATA_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest: {manifest_path}")

    total_bytes = sum(a["total_bytes"] for a in manifest["agencies"].values())
    total_failures = sum(len(a["failures"]) for a in manifest["agencies"].values())
    print(f"\nGrand total: {total_bytes / 1024 / 1024:.1f} MB across all three agencies, "
          f"{total_failures} failures")
    return 1 if total_failures else 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_canada_tri_agency", main))
