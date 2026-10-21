"""US State Department press releases.

Sources (RSS, no API key):
  https://www.state.gov/rss-feed/press-releases/feed/
  https://www.state.gov/rss-feed/western-hemisphere/feed/

New press releases and Western Hemisphere statements, including foreign
assistance and disaster-response announcements that reach no federal funding
database until weeks or months later. These are live signals, not award
precedent. Each feed exposes only its 10 most recent items, so run this at
least daily to avoid missing items. state.gov needs a browser-like
User-Agent, which the shared runner sends.

Usage:
  python state_dept_press_releases.py            poll once and write state
  python state_dept_press_releases.py --probe    fetch only, write nothing
"""
from feeds import Collector, main_for, parse_rss

COLLECTOR = Collector(
    name="state_dept_press_releases",
    title="US State Department Press Releases",
    feeds={
        "press_releases": "https://www.state.gov/rss-feed/press-releases/feed/",
        "western_hemisphere": "https://www.state.gov/rss-feed/western-hemisphere/feed/",
    },
    parse=parse_rss,
)

if __name__ == "__main__":
    main_for(COLLECTOR)
