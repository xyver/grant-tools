"""
USAspending.gov Grants Downloader

Pulls federal grant award data (block grants, formula grants, project grants,
cooperative agreements) from the USAspending.gov bulk download API, scoped to
a rolling window (default: last 5 years). Deliberately broad - no agency or
recipient-type filter - since the goal is a wide bucket of award data to look
for patterns later (recipient type, industry/program, project length, which
administration was in office, etc).

Grant award type codes (DAIMS Assistance Type):
    02 = Block Grant
    03 = Formula Grant
    04 = Project Grant
    05 = Cooperative Agreement
Excluded on purpose: 06/10 (direct payments), 07/08 (loans), 09/11 (insurance,
other) - those are not "grants" and dominate transaction count without being
relevant to this analysis.

IMPORTANT - row cap: /api/v2/download/search/ silently caps at 500,000 rows
per request with no error or warning - it just returns 500,000 rows and
stops, with no indication anything was dropped. A 5-year, all-types pull
hit this directly (1,361,427 real grants, only 500,000 came back). To stay
under the cap we split the work into one request per (award_type_code,
calendar year) - the biggest single type/year bucket observed is still well
under 500K. Before each download we also check the real count via
spending_by_award_count and print a warning if a chunk is still too big, so
a future wider pull (more years, or a type that grows) doesn't silently
clip again.

No "columns" list is sent in the request, so the API returns its full default
column set (recipient info, business categories, CFDA/assistance listing,
period of performance, award description, place of performance, amounts).
That avoids guessing at exact column names and getting a 400 back.

Usage:
    python download_usaspending_grants.py                  # last 5 years
    python download_usaspending_grants.py --years 10        # last 10 years
    python download_usaspending_grants.py --start 2015-01-01 --end 2020-01-01

Output:
    Raw data saved to: data/Raw data/usaspending_grants/
    One zip per (award_type_code, calendar year) chunk, plus a combined
    manifest.json summarizing every chunk and its row count.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "Raw data" / "usaspending_grants"

API_BASE = "https://api.usaspending.gov/api/v2"
FILES_BASE = "https://files.usaspending.gov"

GRANT_AWARD_TYPE_CODES = ["02", "03", "04", "05"]
AWARD_TYPE_LABELS = {
    "02": "block_grant",
    "03": "formula_grant",
    "04": "project_grant",
    "05": "cooperative_agreement",
}

# Observed cap is 500,000 rows per request, silently truncated with no error.
SAFE_ROW_LIMIT = 450_000

TIMEOUT = 120
POLL_INTERVAL = 10  # seconds
POLL_TIMEOUT = 60 * 60  # 1 hour ceiling on waiting for a single zip to build


def setup_dirs():
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {RAW_DATA_DIR}")


def year_chunks(start_date, end_date):
    """Split [start_date, end_date] into <=1-year (start, end) string pairs."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=365), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def get_award_count(award_type_code, start_date, end_date):
    """Real count for a single award type + window, used to sanity-check the cap."""
    url = f"{API_BASE}/search/spending_by_award_count/"
    body = {
        "filters": {
            "award_type_codes": [award_type_code],
            "time_period": [{"start_date": start_date, "end_date": end_date}],
        }
    }
    response = requests.post(url, json=body, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()["results"]["grants"]


def request_download(award_type_code, start_date, end_date):
    """Kick off an async award-data export job, return the response JSON."""
    url = f"{API_BASE}/download/search/"
    body = {
        "filters": {
            "award_type_codes": [award_type_code],
            "time_period": [{"start_date": start_date, "end_date": end_date}],
        },
        "spending_level": ["awards"],
        "file_format": "csv",
    }

    response = requests.post(url, json=body, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    print(f"  Job queued: {data['file_name']}")
    return data


def poll_status(file_name):
    """Poll the status endpoint until the export finishes (or fails/times out)."""
    url = f"{API_BASE}/download/status"
    elapsed = 0

    while elapsed < POLL_TIMEOUT:
        response = requests.get(url, params={"file_name": file_name}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        status = data["status"]

        if status == "finished":
            size_mb = (data["total_size"] or 0) / 1024
            print(
                f"  Finished: {data['total_rows']:,} rows, "
                f"{data['total_columns']} columns, {size_mb:.1f} MB"
            )
            return data
        if status == "failed":
            raise RuntimeError(f"Export failed: {data['message']}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"Export did not finish within {POLL_TIMEOUT}s")


def download_file(file_url, file_name):
    """Stream the finished zip to disk."""
    if file_url.startswith("http"):
        url = file_url
    else:
        url = f"{FILES_BASE}{file_url}"

    output_file = RAW_DATA_DIR / file_name
    print(f"  Downloading -> {output_file.name}")

    with requests.get(url, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        with open(output_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    file_size = output_file.stat().st_size / 1024 / 1024
    print(f"  Downloaded {file_size:.1f} MB")
    return output_file


def download_chunk(award_type_code, start_date, end_date):
    """Download one (award_type_code, <=1-year window) chunk. Returns a manifest entry."""
    label = AWARD_TYPE_LABELS[award_type_code]
    print(f"\n[{label} {start_date} to {end_date}]")

    real_count = get_award_count(award_type_code, start_date, end_date)
    print(f"  Real count: {real_count:,} awards")
    if real_count > SAFE_ROW_LIMIT:
        print(
            f"  WARNING: chunk exceeds the safe limit ({SAFE_ROW_LIMIT:,}) - "
            f"the export will likely be truncated at 500,000. Narrow this window further."
        )

    job = request_download(award_type_code, start_date, end_date)
    status = poll_status(job["file_name"])
    output_file = download_file(status["file_url"], job["file_name"])

    truncated = status["total_rows"] is not None and status["total_rows"] < real_count
    if truncated:
        print(
            f"  WARNING: downloaded {status['total_rows']:,} rows but real count was "
            f"{real_count:,} - this chunk was truncated by the API cap."
        )

    return {
        "award_type_code": award_type_code,
        "award_type_label": label,
        "start_date": start_date,
        "end_date": end_date,
        "real_count": real_count,
        "downloaded_rows": status["total_rows"],
        "truncated": truncated,
        "total_columns": status["total_columns"],
        "total_size_kb": status["total_size"],
        "output_file": str(output_file),
    }


MAX_RETRIES = 3
RETRY_WAIT = 30  # seconds


def download_chunk_with_retries(award_type_code, start_date, end_date):
    """Retry a chunk a few times on transient server-side failures before giving up."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return download_chunk(award_type_code, start_date, end_date)
        except Exception as e:
            last_error = e
            print(f"  Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                print(f"  Retrying in {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)

    return {
        "award_type_code": award_type_code,
        "award_type_label": AWARD_TYPE_LABELS[award_type_code],
        "start_date": start_date,
        "end_date": end_date,
        "real_count": None,
        "downloaded_rows": None,
        "truncated": False,
        "error": str(last_error),
    }


def load_existing_manifest():
    manifest_file = RAW_DATA_DIR / "manifest.json"
    if not manifest_file.exists():
        return []
    with open(manifest_file, encoding="utf-8") as f:
        existing = json.load(f)
    return existing.get("chunks", [])


def write_manifest(manifest_entries):
    manifest_file = RAW_DATA_DIR / "manifest.json"
    dated = [e for e in manifest_entries if e.get("start_date") and e.get("end_date")]
    manifest = {
        "download_date": datetime.now().isoformat(),
        "start_date": min(e["start_date"] for e in dated) if dated else None,
        "end_date": max(e["end_date"] for e in dated) if dated else None,
        "award_type_codes": GRANT_AWARD_TYPE_CODES,
        "total_real_count": sum(e["real_count"] or 0 for e in manifest_entries),
        "total_downloaded_rows": sum(e["downloaded_rows"] or 0 for e in manifest_entries),
        "any_truncated": any(e["truncated"] for e in manifest_entries),
        "any_failed": any(e.get("error") for e in manifest_entries),
        "chunks": manifest_entries,
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def download_grants(start_date, end_date):
    chunks = year_chunks(start_date, end_date)
    print(f"Plan: {len(GRANT_AWARD_TYPE_CODES)} award types x {len(chunks)} year-window(s) "
          f"= {len(GRANT_AWARD_TYPE_CODES) * len(chunks)} downloads")

    manifest_entries = load_existing_manifest()
    if manifest_entries:
        print(f"Found {len(manifest_entries)} existing chunk(s) in manifest.json - appending, not replacing")

    new_entries = []
    for award_type_code in GRANT_AWARD_TYPE_CODES:
        for chunk_start, chunk_end in chunks:
            entry = download_chunk_with_retries(award_type_code, chunk_start, chunk_end)
            new_entries.append(entry)
            # Persist after every chunk so one failure doesn't lose prior progress.
            write_manifest(manifest_entries + new_entries)

    manifest = write_manifest(manifest_entries + new_entries)
    print(f"\nManifest saved to: {RAW_DATA_DIR / 'manifest.json'}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Download USAspending grant award data")
    parser.add_argument(
        "--years", type=int, default=5, help="Number of years back from today (default: 5)"
    )
    parser.add_argument("--start", type=str, help="Explicit start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="Explicit end date (YYYY-MM-DD), default: today")
    args = parser.parse_args()

    if args.start:
        start_date = args.start
        end_date = args.end or date.today().isoformat()
    else:
        end = date.today()
        start = end - timedelta(days=365 * args.years)
        start_date = start.isoformat()
        end_date = end.isoformat()

    print("=" * 70)
    print("USASPENDING GRANTS DOWNLOADER")
    print("=" * 70)
    print(f"Range: {start_date} to {end_date}")
    print(f"Award types: {GRANT_AWARD_TYPE_CODES} (block/formula/project grants, cooperative agreements)")

    setup_dirs()

    try:
        manifest = download_grants(start_date, end_date)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    print("\n" + "=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)
    print(f"Total real awards: {manifest['total_real_count']:,}")
    print(f"Total downloaded rows: {manifest['total_downloaded_rows']:,}")
    if manifest["any_truncated"]:
        print("WARNING: at least one chunk was truncated - check manifest.json")
    if manifest["any_failed"]:
        print("WARNING: at least one chunk failed after retries - check manifest.json")
    return 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_usaspending_grants", main))
