"""California Grants Portal Downloader

Downloads California Grants Portal CSV resources from data.ca.gov CKAN.

This is the historical/structured companion to the live-only
california_grants_portal collector. The live collector watches
https://www.grants.ca.gov/grants/feed/ for newly posted opportunities; this
downloader captures the daily offered-grants CSV and available annual grant
award CSVs for precedent/history work.

Usage:
    python download_california_grants_portal.py

Output:
    data/Raw data/california_grants_portal/
    - one CSV per discovered resource
    - manifest.json with package/resource metadata and download results
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "Raw data" / "california_grants_portal"

CKAN_BASE = "https://data.ca.gov/api/3/action"
PACKAGE_SEARCH_URL = f"{CKAN_BASE}/package_search"
PACKAGE_SHOW_URL = f"{CKAN_BASE}/package_show"
TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300

PACKAGE_NAME_RE = re.compile(
    r"^(california-grants-portal|california-grants-portal-grant-awards-\d{4}-\d{4})$"
)


def ckan_get(url, params):
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"CKAN request failed: {payload}")
    return payload["result"]


def discover_packages():
    """Find the offered-grants package plus annual award packages."""
    result = ckan_get(PACKAGE_SEARCH_URL, {"q": "grants portal", "rows": 100})
    packages = []
    for package in result.get("results", []):
        name = str(package.get("name") or "").strip()
        if PACKAGE_NAME_RE.match(name):
            packages.append({
                "id": package.get("id"),
                "name": name,
                "title": package.get("title"),
            })
    packages.sort(key=lambda p: p["name"])
    return packages


def package_show(package_id):
    return ckan_get(PACKAGE_SHOW_URL, {"id": package_id})


def safe_filename(url, fallback):
    path_name = Path(urlparse(url).path).name
    name = path_name or fallback
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def download_resource(resource, package_name):
    url = resource.get("url")
    if not url:
        raise RuntimeError(f"Resource has no URL: {resource.get('id')}")
    filename = safe_filename(url, f"{package_name}_{resource.get('id')}.csv")
    output_path = RAW_DATA_DIR / filename
    print(f"  Downloading {filename}")
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()
        total = 0
        with open(output_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                total += len(chunk)
    return output_path, total


def main():
    print("=" * 70)
    print("California Grants Portal Downloader")
    print("=" * 70)

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {RAW_DATA_DIR}")

    packages = discover_packages()
    print(f"Discovered {len(packages)} package(s)")
    if not packages:
        raise RuntimeError("No California Grants Portal packages discovered on data.ca.gov")

    manifest = {
        "download_date": datetime.now(timezone.utc).isoformat(),
        "source": "California Grants Portal via data.ca.gov CKAN",
        "ckan_package_search_url": PACKAGE_SEARCH_URL,
        "packages": [],
        "notes": [
            "This raw download is the structured/historical companion to the live RSS collector.",
            "The offered-grants CSV is updated daily and contains open/closed opportunity metadata.",
            "Annual grant-awards CSVs provide state award history/precedent material by fiscal year.",
            "Conversion and grant-analyzer scoring integration are separate follow-up steps.",
        ],
    }

    total_bytes = 0
    failures = []
    for package_stub in packages:
        package = package_show(package_stub["id"])
        package_entry = {
            "id": package.get("id"),
            "name": package.get("name"),
            "title": package.get("title"),
            "metadata_modified": package.get("metadata_modified"),
            "resources": [],
        }
        print(f"\n{package_entry['title']} ({package_entry['name']})")
        for resource in package.get("resources", []):
            if str(resource.get("format") or "").strip().lower() != "csv":
                continue
            try:
                output_path, downloaded = download_resource(resource, package_entry["name"])
                total_bytes += downloaded
                package_entry["resources"].append({
                    "id": resource.get("id"),
                    "name": resource.get("name"),
                    "format": resource.get("format"),
                    "url": resource.get("url"),
                    "last_modified": resource.get("last_modified"),
                    "datastore_active": resource.get("datastore_active"),
                    "downloaded_bytes": downloaded,
                    "output_file": str(output_path),
                })
                print(f"    {downloaded / 1024 / 1024:.2f} MB")
            except Exception as exc:
                failure = {
                    "package": package_entry["name"],
                    "resource_id": resource.get("id"),
                    "error": str(exc),
                }
                failures.append(failure)
                print(f"    FAILED: {exc}")
        manifest["packages"].append(package_entry)

    manifest["total_downloaded_bytes"] = total_bytes
    manifest["total_downloaded_mb"] = round(total_bytes / 1024 / 1024, 2)
    manifest["failures"] = failures

    manifest_path = RAW_DATA_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    print(f"\nDownloaded {manifest['total_downloaded_mb']:.2f} MB")
    print(f"Manifest: {manifest_path}")
    return 1 if failures else 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_california_grants_portal", main))
