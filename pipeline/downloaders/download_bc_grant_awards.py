"""Download British Columbia provincial grant-award datasets from DataBC.

Sources:
  - Gaming Grants Paid to Community Organizations
  - BC Knowledge Development Fund (BCKDF)

Output:
  data/Raw data/bc_grant_awards/

This downloader records source package/resource metadata in a manifest so a
future scheduled job can do cheap CKAN metadata checks before redownloading.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_paths import DATA_ROOT, RAW_ROOT  # noqa: E402
RAW_DATA_DIR = RAW_ROOT / "bc_grant_awards"

CKAN_PACKAGE_SHOW_URL = "https://catalogue.data.gov.bc.ca/api/3/action/package_show"
TIMEOUT = 60
DOWNLOAD_TIMEOUT = 600

PACKAGES = {
    "gaming_grants": {
        "id": "f878ebde-cbee-4bf2-bd5d-4e70c930a143",
        "title": "Gaming Grants Paid to Community Organizations",
        "formats": {"xlsx", "xls"},
        "include_names": (
            "fin-report-gaming-grant-historical",
            "year-to-date_grant_report_by_community",
            "Gaming Grants Paid to Community Organizations - 2021/22",
            "Gaming Grants Paid to Community Organizations - 2022/23",
        ),
    },
    "bckdf": {
        "id": "bf1cd755-f2ce-4b86-af07-73dc22b36a8b",
        "title": "BC Knowledge Development Fund",
        "formats": {"csv"},
        "include_names": (
            "BCKDF Open Data table 1998-2024",
        ),
    },
}


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return text.lower() or "resource"


def _resource_filename(resource: dict) -> str:
    fmt = str(resource.get("format") or "dat").strip().lower() or "dat"
    name = _safe_name(resource.get("name"))
    suffix = f".{fmt}"
    if name.lower().endswith(suffix):
        return name
    return f"{name}{suffix}"


def _package_metadata(package_id: str) -> dict:
    resp = requests.get(CKAN_PACKAGE_SHOW_URL, params={"id": package_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"CKAN package_show failed for {package_id}: {payload}")
    return payload["result"]


def _resource_matches(resource: dict, spec: dict) -> bool:
    fmt = str(resource.get("format") or "").strip().lower()
    if fmt not in spec["formats"]:
        return False
    name = str(resource.get("name") or "")
    url = str(resource.get("url") or "")
    haystack = f"{name} {url}"
    return any(token in haystack for token in spec["include_names"])


def _download(url: str, out_path: Path) -> int:
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()
    downloaded = 0
    with out_path.open("wb") as handle:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
    return downloaded


def main() -> int:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "download_date": datetime.now(timezone.utc).isoformat(),
        "source": "DataBC grant-award datasets",
        "packages": [],
        "resources": [],
    }

    for source_key, spec in PACKAGES.items():
        package = _package_metadata(spec["id"])
        print(f"Package: {package.get('title') or spec['title']}")
        manifest["packages"].append({
            "source_key": source_key,
            "package_id": package.get("id"),
            "package_name": package.get("name"),
            "package_title": package.get("title"),
            "record_last_modified": package.get("record_last_modified"),
            "metadata_modified": package.get("metadata_modified"),
            "license_title": package.get("license_title"),
            "license_url": package.get("license_url"),
        })

        matches = [r for r in package.get("resources", []) if _resource_matches(r, spec)]
        if not matches:
            raise RuntimeError(f"No matching resources found for {source_key}")

        source_dir = RAW_DATA_DIR / source_key
        source_dir.mkdir(parents=True, exist_ok=True)
        for resource in matches:
            filename = _resource_filename(resource)
            out_path = source_dir / filename
            print(f"  Downloading {resource.get('name')} -> {out_path.name}")
            downloaded = _download(resource["url"], out_path)
            manifest["resources"].append({
                "source_key": source_key,
                "resource_id": resource.get("id"),
                "resource_name": resource.get("name"),
                "format": resource.get("format"),
                "resource_update_cycle": resource.get("resource_update_cycle"),
                "last_modified": resource.get("last_modified"),
                "metadata_modified": resource.get("metadata_modified"),
                "declared_size": resource.get("size"),
                "downloaded_bytes": downloaded,
                "url": resource.get("url"),
                "local_path": str(out_path.relative_to(RAW_DATA_DIR)),
            })

    manifest_path = RAW_DATA_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")
    return 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_bc_grant_awards", main))
