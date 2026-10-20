"""
NSF Award Search Downloader - walks backward year by year.

No API key required (research.gov awardapi-service is public). Keyword-
filtered to the same disaster/resilience/green-infra set used for
Grants.gov, since NSF's full award history (500,000+ awards since 1959) is
mostly unrelated basic research - pulling all of it would be huge and
mostly noise for this project.

Checkpointed: writes progress.json after every (keyword, year) unit
completes, and appends that unit's rows to the output CSV immediately -
not buffered in memory until the end. Safe to kill and rerun; already-
completed units are skipped.

Usage:
    python download_nsf_award_search.py                # last 10 years, default keywords
    python download_nsf_award_search.py --years 20
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rate_limited_fetcher import RateLimitedFetcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "Raw data" / "nsf_awards"
OUTPUT_CSV = OUTPUT_DIR / "nsf_awards.csv"
CHECKPOINT_FILE = OUTPUT_DIR / "progress.json"
STATS_PATH = OUTPUT_DIR / "fetch_stats.json"

API_URL = "https://www.research.gov/awardapi-service/v1/awards.json"
ROWS_PER_PAGE = 25
FIELDS = "id,title,fundsObligatedAmt,awardeeName,awardeeStateCode,awardeeCity,date,startDate,expDate,piFirstName,piLastName,abstractText,fundProgramName,cfdaNumber"

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


def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {"completed_units": []}


def save_checkpoint(checkpoint):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")


def fetch_year_keyword(fetcher, keyword, year, writer, csv_file):
    """Page through one (keyword, year) unit, writing rows as they arrive."""
    offset = 1  # NSF API is 1-indexed
    total_for_unit = 0

    while True:
        params = {
            "keyword": keyword,
            "dateStart": f"01/01/{year}",
            "dateEnd": f"12/31/{year}",
            "printFields": FIELDS,
            "rpp": ROWS_PER_PAGE,
            "offset": offset,
        }
        response = fetcher.get(API_URL, params=params)
        data = response.json()
        awards = (data.get("response") or {}).get("award") or []

        if not awards:
            break

        for award in awards:
            writer.writerow({
                "nsf_award_id": award.get("id"),
                "title": award.get("title"),
                "funds_obligated": award.get("fundsObligatedAmt"),
                "awardee_name": award.get("awardeeName"),
                "awardee_state": award.get("awardeeStateCode"),
                "awardee_city": award.get("awardeeCity"),
                "award_date": award.get("date"),
                "start_date": award.get("startDate"),
                "exp_date": award.get("expDate"),
                "pi_first_name": award.get("piFirstName"),
                "pi_last_name": award.get("piLastName"),
                "fund_program_name": award.get("fundProgramName"),
                "cfda_number": award.get("cfdaNumber"),
                "abstract_text": (award.get("abstractText") or "").replace("\n", " ").strip(),
                "matched_keyword": keyword,
                "search_year": year,
            })
            total_for_unit += 1
        csv_file.flush()

        if len(awards) < ROWS_PER_PAGE:
            break
        offset += ROWS_PER_PAGE

    return total_for_unit


def main():
    parser = argparse.ArgumentParser(description="Walk NSF Award Search backward year by year")
    parser.add_argument("--years", type=int, default=10, help="How many years back from current (default: 10)")
    parser.add_argument("--keywords", type=str, default=None, help="Comma-separated keyword override")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else DEFAULT_KEYWORDS
    from datetime import date
    current_year = date.today().year
    years = list(range(current_year, current_year - args.years, -1))  # walk backward

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint()
    completed = set(tuple(u) for u in checkpoint["completed_units"])

    print(f"Output: {OUTPUT_CSV}")
    print(f"Keywords: {keywords}")
    print(f"Years (walking backward): {years}")
    print(f"Already completed: {len(completed)} units")

    fieldnames = [
        "nsf_award_id", "title", "funds_obligated", "awardee_name", "awardee_state",
        "awardee_city", "award_date", "start_date", "exp_date", "pi_first_name",
        "pi_last_name", "fund_program_name", "cfda_number", "abstract_text",
        "matched_keyword", "search_year",
    ]

    file_exists = OUTPUT_CSV.exists()
    fetcher = RateLimitedFetcher(min_interval=1.0, stats_path=STATS_PATH)

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for year in years:
            for keyword in keywords:
                unit = (keyword, year)
                if unit in completed:
                    continue

                print(f"[{keyword} / {year}] fetching...")
                count = fetch_year_keyword(fetcher, keyword, year, writer, csv_file)
                print(f"  {count} awards")

                completed.add(unit)
                checkpoint["completed_units"] = [list(u) for u in completed]
                save_checkpoint(checkpoint)

    fetcher.write_stats()
    print(f"\nDone. {len(completed)} (keyword, year) units completed.")
    return 0


try:
    from ._completion_alert import run_downloader_main
except ImportError:
    from _completion_alert import run_downloader_main


if __name__ == "__main__":

    raise SystemExit(run_downloader_main("download_nsf_award_search", main))
