"""Appalachian Regional Commission grant announcements.

Source: https://www.arc.gov/feed/ (RSS). No API key.

New ARC announcements: funding opportunities, application windows,
capacity-building offers, and regional program updates. This is an
announcement layer, not a complete ARC program eligibility table. The grant
tools read its snapshot when a project is in Appalachia.

Usage:
  python arc_grant_announcements.py            poll once and write state
  python arc_grant_announcements.py --probe    fetch only, write nothing
"""
from feeds import Collector, main_for, parse_rss

COLLECTOR = Collector(
    name="arc_grant_announcements",
    title="ARC Grant Announcements",
    feeds={
        "arc_news": "https://www.arc.gov/feed/",
    },
    parse=parse_rss,
)

if __name__ == "__main__":
    main_for(COLLECTOR)
