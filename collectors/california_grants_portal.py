"""California Grants Portal opportunities.

Source: https://www.grants.ca.gov/grants/feed/ (WordPress RSS). No API key.

Newly posted California state grant and loan opportunities from the official
cross-agency portal. This is an open-opportunity layer, not historical award
precedent; the historical companion data on data.ca.gov comes from the
downloader and converter under pipeline/.

Use /grants/feed/. The site-level /feed/ is general news and stale.

Usage:
  python california_grants_portal.py            poll once and write state
  python california_grants_portal.py --probe    fetch only, write nothing
"""
from feeds import Collector, main_for, parse_rss

COLLECTOR = Collector(
    name="california_grants_portal",
    title="California Grants Portal Opportunities",
    feeds={
        "grants_archive": "https://www.grants.ca.gov/grants/feed/",
    },
    parse=parse_rss,
)

if __name__ == "__main__":
    main_for(COLLECTOR)
