"""
NIH RePORTER Projects Downloader - walks backward year by year.

No API key required. NIH explicitly publishes the most conservative rate
policy found among the no-key sources researched (max 1 req/sec, prefer
off-peak/weekends for bulk jobs) - the shared rate_limited_fetcher's
default matches this exactly.

Keyword-filtered (advanced_text_search) to the same disaster/resilience
set used elsewhere, since NIH's full project history (~73K projects/year)
is overwhelmingly biomedical research unrelated to this project's focus.

Checkpointed the same way as the NSF downloader: progress.json after every
(keyword, year) unit, rows appended to the output CSV immediately.

Usage:
    python download_nih_reporter_projects.py                # last 10 years
    python download_nih_reporter_projects.py --years 20
"""
import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rate_limited_fetcher import RateLimitedFetcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "Raw data" / "nih_reporter_projects"
OUTPUT_CSV = OUTPUT_DIR / "nih_reporter_projects.csv"
CHECKPOINT_FILE = OUTPUT_DIR / "progress.json"
STATS_PATH = OUTPUT_DIR / "fetch_stats.json"

API_URL = "https://api.reporter.nih.gov/v2/projects/search"
ROWS_PER_PAGE = 500  # NIH RePORTER's documented max limit per request

DEFAULT_KEYWORDS = [
    "disaster resilience",
    "disaster preparedness",
    "emergency response",
    "climate adaptation",
    "community resilience",
    "hazard mitigation",
]


def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {"completed_units": []}


def save_checkpoint(checkpoint):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")


def fetch_year_keyword(fetcher, keyword, year, writer, csv_file):
    offset = 0
    total_for_unit = 0

    while True:
        body = {
            "criteria": {
                "fiscal_years": [year],
                "advanced_text_search": {
                    "operator": "and",
                    "search_field": "projecttitle,terms,abstracttext",
                    "search_text": keyword,
                },
            },
            "limit": ROWS_PER_PAGE,
            "offset": offset,
        }
        response = fetcher.post(API_URL, json=body)
        data = response.json()
        results = data.get("results") or []

        if not results:
            break

        for project in results:
            org = project.get("organization") or {}
            pis = project.get("principal_investigators") or []
            writer.writerow({
                "appl_id": project.get("appl_id"),
                "project_num": project.get("project_num"),
                "fiscal_year": project.get("fiscal_year"),
                "award_type": project.get("award_type"),
                "activity_code": project.get("activity_code"),
                "award_amount": project.get("award_amount"),
                "org_name": org.get("org_name"),
                "org_city": org.get("org_city"),
                "org_state": org.get("org_state"),
                "contact_pi_name": project.get("contact_pi_name"),
                "pi_names": "|".join(p.get("full_name", "") for p in pis),
                "project_title": project.get("project_title"),
                "abstract_text": (project.get("abstract_text") or "").replace("\n", " ").strip(),
                "matched_keyword": keyword,
                "search_year": year,
            })
            total_for_unit += 1
        csv_file.flush()

        total_available = (data.get("meta") or {}).get("total", 0)
        offset += ROWS_PER_PAGE
        if offset >= total_available or len(results) < ROWS_PER_PAGE:
            break

    return total_for_unit


def main():
    parser = argparse.ArgumentParser(description="Walk NIH RePORTER backward year by year")
    parser.add_argument("--years", type=int, default=10, help="How many years back from current (default: 10)")
    parser.add_argument("--keywords", type=str, default=None, help="Comma-separated keyword override")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else DEFAULT_KEYWORDS
    current_year = date.today().year
    years = list(range(current_year, current_year - args.years, -1))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint()
    completed = set(tuple(u) for u in checkpoint["completed_units"])

    print(f"Output: {OUTPUT_CSV}")
    print(f"Keywords: {keywords}")
    print(f"Years (walking backward): {years}")
    print(f"Already completed: {len(completed)} units")

    fieldnames = [
        "appl_id", "project_num", "fiscal_year", "award_type", "activity_code",
        "award_amount", "org_name", "org_city", "org_state", "contact_pi_name",
        "pi_names", "project_title", "abstract_text", "matched_keyword", "search_year",
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
                print(f"  {count} projects")

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

    raise SystemExit(run_downloader_main("download_nih_reporter_projects", main))
