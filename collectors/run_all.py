"""Run selected grant announcement collectors once.

This is the one command to schedule. Run it by hand, or from cron or Task
Scheduler as often as you like; daily is plenty for these feeds.

    python collectors/run_all.py --list
    python collectors/run_all.py --only arc,california --probe
    python collectors/run_all.py --only arc,california
    python collectors/run_all.py --state-dir D:/grant-state

Exits 1 if any collector failed outright (all of its feeds errored).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from arc_grant_announcements import COLLECTOR as ARC
from california_grants_portal import COLLECTOR as CALIFORNIA
from canada_grant_announcements import COLLECTOR as CANADA
from feeds import _env_file_value, collect, default_state_dir, format_result, probe
from state_dept_press_releases import COLLECTOR as STATE_DEPT

COLLECTORS = {"arc": ARC, "california": CALIFORNIA, "canada": CANADA, "state_dept": STATE_DEPT}


def selected_collectors(raw: str, parser: argparse.ArgumentParser):
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    if not names or names == ["all"]:
        return list(COLLECTORS.values())
    if names == ["none"]:
        return []
    invalid = sorted(set(names) - set(COLLECTORS))
    if invalid:
        parser.error(f"unknown collector(s): {', '.join(invalid)}; choose from {', '.join(COLLECTORS)}")
    if len(names) != len(set(names)):
        parser.error("each collector may be selected only once")
    return [COLLECTORS[name] for name in COLLECTORS if name in names]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run selected grant announcement collectors once")
    parser.add_argument("--state-dir", type=Path, default=None, help="Where snapshots are written (default: <repo>/state)")
    parser.add_argument("--list", action="store_true", help="List available collectors without fetching or writing")
    parser.add_argument("--only", metavar="NAMES", help="Comma-separated collector names; overrides GRANT_COLLECTORS")
    parser.add_argument("--probe", action="store_true", help="Fetch selected feeds and report counts without writing state")
    args = parser.parse_args()
    if args.list:
        for name, collector in COLLECTORS.items():
            print(f"{name}: {collector.title}")
        return
    configured = str(os.environ.get("GRANT_COLLECTORS", "") or "").strip() or _env_file_value("GRANT_COLLECTORS")
    chosen = selected_collectors(args.only if args.only is not None else configured, parser)
    if not chosen:
        print("No grant announcement collectors enabled.")
        return
    state_dir = args.state_dir or default_state_dir()
    failed = False
    for collector in chosen:
        if args.probe:
            result = probe(collector)
            print(json.dumps(result, indent=2))
            failed = failed or not any(feed.get("ok") for feed in result["feeds"].values())
            continue
        result = collect(collector, state_dir)
        print(format_result(result))
        failed = failed or not result["ok"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
