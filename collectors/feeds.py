"""Shared runner for the grant announcement collectors.

Each collector watches a few RSS or Atom feeds. One run fetches every feed,
compares item ids with the previous snapshot, and writes two things under the
state folder:

    state/<collector>/snapshot.json         latest view: recent items, counts, errors
    state/<collector>/observed/<date>.jsonl new items, appended as they are first seen

Runs are meant to be occasional and local: by hand, from cron, or from Task
Scheduler. Nothing here assumes a server. Python standard library only.

The state folder is <repo>/state unless GRANT_STATE_DIR or --state-dir says
otherwise; the grant tools read snapshots from the same place.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

ATOM_NS = "{http://www.w3.org/2005/Atom}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "GrantTools/1.0 (announcement collector)"
)
MAX_RECENT_ITEMS = 500
# Ids remembered for new-item detection. Larger than the recent window because
# one run can fetch more items than it keeps (Canada fetches about 700).
MAX_SEEN_IDS = 10000
MAX_EXCERPT_CHARS = 500
FETCH_TIMEOUT_SECONDS = 60


REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_file_value(key: str) -> str:
    """Read one KEY=VALUE from the repo's .env, so collectors and the server agree."""
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == key and not line.strip().startswith("#"):
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def default_state_dir() -> Path:
    configured = str(os.environ.get("GRANT_STATE_DIR", "") or "").strip() or _env_file_value("GRANT_STATE_DIR")
    return Path(configured) if configured else REPO_ROOT / "state"


def _text(elem: Optional[ET.Element]) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _excerpt(raw: str) -> str:
    return raw if len(raw) <= MAX_EXCERPT_CHARS else raw[:MAX_EXCERPT_CHARS].rstrip() + "..."


def parse_rss(root: ET.Element, feed_key: str) -> List[Dict[str, Any]]:
    """RSS <item> elements; the id is <guid>, falling back to <link>."""
    items = []
    for elem in root.findall("./channel/item"):
        link = _text(elem.find("link"))
        item_id = _text(elem.find("guid")) or link
        if not item_id:
            continue
        items.append({
            "id": item_id,
            "title": _text(elem.find("title")),
            "link": link,
            "published": _text(elem.find("pubDate")),
            "source_feed": feed_key,
            "raw_excerpt": _excerpt(_text(elem.find("description"))),
        })
    return items


def parse_atom(root: ET.Element, feed_key: str) -> List[Dict[str, Any]]:
    """Atom <entry> elements; the id is <id>, falling back to the link href."""
    items = []
    for elem in root.findall(f"./{ATOM_NS}entry"):
        link_elem = elem.find(f"{ATOM_NS}link")
        link = (link_elem.get("href") or "").strip() if link_elem is not None else ""
        item_id = _text(elem.find(f"{ATOM_NS}id")) or link
        if not item_id:
            continue
        category_elem = elem.find(f"{ATOM_NS}category")
        items.append({
            "id": item_id,
            "title": _text(elem.find(f"{ATOM_NS}title")),
            "link": link,
            "published": _text(elem.find(f"{ATOM_NS}updated")),
            "category": (category_elem.get("term") or "").strip() if category_elem is not None else "",
            "source_feed": feed_key,
            "raw_excerpt": _excerpt(_text(elem.find(f"{ATOM_NS}summary"))),
        })
    return items


def parse_published(raw: str) -> Optional[datetime]:
    """Accept RFC 822 (RSS pubDate) or ISO 8601 (Atom updated)."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Collector:
    name: str
    title: str
    feeds: Dict[str, str]
    parse: Callable[[ET.Element, str], List[Dict[str, Any]]]


def fetch_feed(collector: Collector, feed_key: str, url: str) -> List[Dict[str, Any]]:
    """Fetch and parse one feed. Raises on network or XML errors."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        raw = response.read()
    return collector.parse(ET.fromstring(raw), feed_key)


def _read_snapshot(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def collect(collector: Collector, state_dir: Path) -> Dict[str, Any]:
    """Run one poll and write state. Returns a small summary for printing."""
    out_dir = state_dir / collector.name
    snapshot_path = out_dir / "snapshot.json"
    previous = _read_snapshot(snapshot_path)
    previous_summary = previous.get("payload_summary") or {}
    previous_recent = [r for r in previous_summary.get("recent_items") or [] if isinstance(r, dict)]
    previous_seen = [str(i) for i in previous.get("seen_ids") or []]
    seen = set(previous_seen) | {str(r.get("id")) for r in previous_recent if r.get("id")}

    feed_counts: Dict[str, int] = {}
    fetch_errors: Dict[str, str] = {}
    fetched: List[Dict[str, Any]] = []
    for feed_key, url in collector.feeds.items():
        try:
            items = fetch_feed(collector, feed_key, url)
        except (URLError, OSError, ET.ParseError) as exc:
            fetch_errors[feed_key] = str(exc)
            continue
        feed_counts[feed_key] = len(items)
        fetched.extend(items)

    if fetch_errors and len(fetch_errors) == len(collector.feeds):
        # Keep the previous snapshot; a failed run should not erase what we know.
        return {"collector": collector.name, "ok": False, "new_items": 0, "fetch_errors": fetch_errors}

    fetched_at = datetime.now(timezone.utc).isoformat()
    new_items = []
    for item in fetched:
        if item["id"] not in seen:
            seen.add(item["id"])
            new_items.append({**item, "fetched_at": fetched_at})

    observed_file = None
    if new_items:
        observed_dir = out_dir / "observed"
        observed_dir.mkdir(parents=True, exist_ok=True)
        observed_path = observed_dir / f"{fetched_at[:10]}.jsonl"
        with observed_path.open("a", encoding="utf-8") as handle:
            for item in new_items:
                handle.write(json.dumps({**item, "collector": collector.name}, ensure_ascii=False, sort_keys=True) + "\n")
        observed_file = observed_path.relative_to(out_dir).as_posix()

    dates = [d for d in (parse_published(i.get("published", "")) for i in fetched) if d is not None]
    snapshot = {
        "collector": collector.name,
        "title": collector.title,
        "fetched_at": fetched_at,
        "upstream_issued_at": max(dates).strftime("%Y-%m-%d %H:%M:%S") if dates else None,
        "seen_ids": ([i["id"] for i in new_items] + previous_seen)[:MAX_SEEN_IDS],
        "payload_summary": {
            "feeds": feed_counts,
            "fetch_errors": fetch_errors,
            "new_items_this_poll": new_items,
            "recent_items": (new_items + previous_recent)[:MAX_RECENT_ITEMS],
            "observed_history_file": observed_file,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_path = snapshot_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(snapshot_path)
    return {"collector": collector.name, "ok": True, "new_items": len(new_items), "fetch_errors": fetch_errors}


def probe(collector: Collector) -> Dict[str, Any]:
    """Fetch every feed and report counts without writing anything."""
    report: Dict[str, Any] = {}
    for feed_key, url in collector.feeds.items():
        try:
            report[feed_key] = {"ok": True, "items": len(fetch_feed(collector, feed_key, url))}
        except (URLError, OSError, ET.ParseError) as exc:
            report[feed_key] = {"ok": False, "error": str(exc)}
    return {"collector": collector.name, "feeds": report}


def format_result(result: Dict[str, Any]) -> str:
    if not result["ok"]:
        return f"{result['collector']}: FAILED, all feeds errored: {', '.join(result['fetch_errors'])}"
    line = f"{result['collector']}: {result['new_items']} new item(s)"
    if result["fetch_errors"]:
        line += f"; feeds with errors: {', '.join(result['fetch_errors'])}"
    return line


def main_for(collector: Collector) -> None:
    """Command-line entry point shared by the individual collector files."""
    parser = argparse.ArgumentParser(description=f"{collector.title} collector")
    parser.add_argument("--state-dir", type=Path, default=None, help="Where snapshots are written (default: <repo>/state)")
    parser.add_argument("--probe", action="store_true", help="Fetch feeds and print counts without writing state")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(collector), indent=2))
        return
    result = collect(collector, args.state_dir or default_state_dir())
    print(format_result(result))
    sys.exit(0 if result["ok"] else 1)
