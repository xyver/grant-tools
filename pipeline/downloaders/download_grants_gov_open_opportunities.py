"""
Grants.gov Open Opportunities Downloader

Pulls currently-open funding opportunities (NOFOs) from Grants.gov's public
search2 endpoint. This is deliberately different from the USAspending pull:
USAspending only has awards that already happened, this has what's open to
apply for *right now*, with deadlines - the "apply here" layer Step 1 of
the grant-matching design needs.

No API key/account required - search2 is the public, unauthenticated
endpoint. No published rate limit was found for it (the *keyed*
Simpler.Grants.gov platform documents 60 req/min, 10k/day, but that's a
different, authenticated API). Conservative policy adopted here: max
1 request/second, exponential backoff on 429/5xx - the most conservative
published policy found across the no-key sources researched (NIH RePORTER
publishes that exact limit), applied as a blanket default since none of
these endpoints publish anything looser.

Usage:
    python download_grants_gov_open_opportunities.py
    python download_grants_gov_open_opportunities.py --keywords "resilience,hazard mitigation"

Output:
    data/countries/USA/grants_gov_open_opportunities/snapshot_{date}.csv
    data/countries/USA/grants_gov_open_opportunities/latest.csv
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rate_limited_fetcher import RateLimitedFetcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "countries" / "USA" / "grants_gov_open_opportunities"
STATS_PATH = OUTPUT_DIR / "fetch_stats.json"

API_URL = "https://api.grants.gov/v1/api/search2"
ROWS_PER_PAGE = 100

# Keywords covering the disaster recovery / community resilience / green
# infrastructure / prevention space researched in USA grant research.md.
DEFAULT_KEYWORDS = [
    "resilience",
    "hazard mitigation",
    "disaster recovery",
    "climate adaptation",
    "community development",
    "brownfield",
    "weatherization",
    "food system infrastructure",
]


def search_opportunities(fetcher, keyword, start_record=0):
    """One page of search2 results for a keyword. Pacing/retries handled by fetcher."""
    body = {
        "keyword": keyword,
        "rows": ROWS_PER_PAGE,
        "startRecordNum": start_record,
        "oppStatuses": "posted",
    }
    response = fetcher.post(API_URL, json=body)
    return response.json()


def fetch_all_for_keyword(fetcher, keyword):
    """Page through all results for one keyword, respecting the request interval."""
    print(f"Searching: '{keyword}'")
    all_hits = []
    start_record = 0

    while True:
        data = search_opportunities(fetcher, keyword, start_record)

        payload = data.get("data", {})
        hits = payload.get("oppHits", [])
        hit_count = payload.get("hitCount", 0)

        all_hits.extend(hits)
        print(f"  {len(all_hits)}/{hit_count} fetched")

        start_record += ROWS_PER_PAGE
        if start_record >= hit_count or not hits:
            break

    return all_hits


def main():
    parser = argparse.ArgumentParser(description="Download currently-open Grants.gov opportunities")
    parser.add_argument(
        "--keywords", type=str, default=None,
        help="Comma-separated keywords (default: resilience/disaster/recovery set)"
    )
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else DEFAULT_KEYWORDS

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Keywords: {keywords}")

    fetcher = RateLimitedFetcher(min_interval=1.0, stats_path=STATS_PATH)

    seen_ids = set()
    rows = []
    for keyword in keywords:
        hits = fetch_all_for_keyword(fetcher, keyword)
        for hit in hits:
            if hit["id"] in seen_ids:
                continue
            seen_ids.add(hit["id"])
            rows.append({
                "id": hit.get("id"),
                "opportunity_number": hit.get("number"),
                "title": hit.get("title"),
                "agency_code": hit.get("agencyCode"),
                "agency": hit.get("agency"),
                "open_date": hit.get("openDate"),
                "close_date": hit.get("closeDate"),
                "opp_status": hit.get("oppStatus"),
                "doc_type": hit.get("docType"),
                "cfda_numbers": "|".join(hit.get("cfdaList") or []),
                "matched_keyword": keyword,
            })

    print(f"\nTotal unique open opportunities: {len(rows)}")
    fetcher.write_stats()

    today = date.today().isoformat()
    snapshot_file = OUTPUT_DIR / f"snapshot_{today}.csv"
    latest_file = OUTPUT_DIR / "latest.csv"

    fieldnames = list(rows[0].keys()) if rows else [
        "id", "opportunity_number", "title", "agency_code", "agency",
        "open_date", "close_date", "opp_status", "doc_type", "cfda_numbers", "matched_keyword",
    ]

    for output_file in (snapshot_file, latest_file):
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote: {output_file}")

    return 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_grants_gov_open_opportunities", main))
