"""Government of Canada funding and grant announcements.

Source: Government of Canada News Centre Atom feeds via api.io.canada.ca. No
API key.

Watches one government-wide audience feed ("funding applicants") plus the
departments most likely to announce grant programs: the Tri-Agency research
funders (SSHRC, CIHR), ISED, Infrastructure Canada, Indigenous Services Canada,
ESDC, NRCan, Public Safety, CIRNAC, NRC, and British Columbia. Historical award
databases cannot surface something announced today; this layer does. See
docs/Canada grant research.md.

Feed URLs were copied from the literal per-feed links on
https://www.canada.ca/en/news/web-feeds.html. That page returns 403 to
automated fetches (user-agent sniffing on canada.ca HTML), but the underlying
api.io.canada.ca API does not. Each querystring (sort, order, date floor, pick,
atomtitle) is verbatim; do not edit one without re-confirming it against the
source page.

NSERC is deliberately absent: its own feed (dept=sciencesengineeringresearch)
returns zero entries even without a date filter (confirmed 2026-06-28). Its
real announcements, such as joint Tri-Agency investments, appear under SSHRC.

Usage:
  python canada_grant_announcements.py            poll once and write state
  python canada_grant_announcements.py --probe    fetch only, write nothing
"""
from feeds import Collector, main_for, parse_atom

FEEDS = {
    "funding_applicants": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?audience=fundingapplicants&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-10-25&pick=100&format=atom"
        "&atomtitle=funding%20applicants"
    ),
    "sshrc": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=socialscienceshumanitiesresearch&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Social%20Sciences%20and%20Humanities%20Research%20Council"
    ),
    "cihr": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=instituteshealthresearch&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Canadian%20Institutes%20of%20Health%20Research"
    ),
    "ised": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=departmentofindustry&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Innovation,%20Science%20and%20Economic%20Development%20Canada"
    ),
    "infrastructure": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=officeinfrastructure&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Infrastructure%20Canada"
    ),
    "indigenous_services": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=indigenousservicescanada&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Indigenous%20Services%20Canada"
    ),
    "esdc": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=departmentofemploymentandsocialdevelopment&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Employment%20and%20Social%20Development%20Canada"
    ),
    # Added 2026-06-29 after the BC wildfire-recovery test case showed a real
    # blind spot: FireSmart, Wildland Fire Resilience, DMAF, and NDMP are all
    # NRCan/Public Safety Canada programs, neither of which was being watched.
    "nrcan": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=naturalresourcescanada&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Natural%20Resources%20Canada"
    ),
    "public_safety": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=publicsafetycanada&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=Public%20Safety%20Canada"
    ),
    # Added 2026-06-29: CIRNAC to chase the ISC Emergency Management
    # Assistance Program mystery (a real, well-known First Nations
    # wildfire/disaster program that is invisible in the G&C disclosure
    # data under any searchable name - CIRNAC is a plausible alternate
    # home for it). NRC to catch AI-funding-stream announcements (IRAP/
    # AI Assist showed up as real precedent in the earthquake-AI test case).
    "cirnac": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=crownindigenousrelationsandnorthernaffairscanada&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-10-25&pick=100&format=atom"
        "&atomtitle=Crown-Indigenous%20Relations%20and%20Northern%20Affairs%20Canada"
    ),
    "nrc": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?dept=nationalresearchcouncil&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-07-23&pick=50&format=atom"
        "&atomtitle=National%20Research%20Council%20Canada"
    ),
    # Added 2026-06-29: BC specifically (not all 13 provinces/territories -
    # deliberate, see docs/Canada grant research.md) because the wildfire-
    # recovery test case was BC-specific and this catches BC-tagged
    # announcements across ANY department, not just the ones already
    # watched above. The `location` param uses the same StatCan PRUID
    # codes (ab48=48,
    # bc59=59, confirmed identical scheme by testing on35/qc24/nu62 too -
    # the other 11 province/territory codes follow the same pattern if a
    # future real case needs one, not added speculatively).
    "bc": (
        "https://api.io.canada.ca/io-server/gc/news/en/v2"
        "?location=bc59&sort=publishedDate&orderBy=desc"
        "&publishedDate%3E=2021-10-25&pick=100&format=atom"
        "&atomtitle=British%20Columbia"
    ),
}

# All 13 province/territory `location=` feeds, confirmed live 2026-06-29
# (see docs/Canada grant research.md, "Should we watch all provinces"
# section for the full scan). NOT all active by default - real content
# scan found a consistent noise source (Correctional Service of Canada
# incident reports) in most of these, so only `bc` (already in FEEDS
# above) is watched continuously. Kept here, not discarded, specifically
# so adding one for a real future case is a one-line move (copy the entry
# into FEEDS) instead of re-deriving the URL - the `location=` param uses
# StatCan PRUID codes.
PROVINCE_FEEDS = {
    "nl": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=nl10&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Newfoundland%20and%20Labrador",
    "pe": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=pe11&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Prince%20Edward%20Island",
    "ns": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=ns12&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Nova%20Scotia",
    "nb": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=nb13&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=New%20Brunswick",
    "qc": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=qc24&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Quebec",
    "on": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=on35&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Ontario",
    "mb": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=mb46&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Manitoba",
    "sk": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=sk47&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Saskatchewan",
    "ab": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=ab48&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Alberta",
    "bc": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=bc59&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=British%20Columbia",
    "yt": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=yt60&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Yukon%20Territory",
    "nt": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=nt61&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Northwest%20Territories",
    "nu": "https://api.io.canada.ca/io-server/gc/news/en/v2?location=nu62&sort=publishedDate&orderBy=desc&publishedDate%3E=2021-10-25&pick=100&format=atom&atomtitle=Nunavut",
}


COLLECTOR = Collector(
    name="canada_grant_announcements",
    title="Canada Government News - Funding/Grant Announcements",
    feeds=FEEDS,
    parse=parse_atom,
)

if __name__ == "__main__":
    main_for(COLLECTOR)
