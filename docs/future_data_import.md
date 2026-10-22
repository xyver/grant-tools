# Future data import

Candidate grant data sources, and the state of each. This is the queue; the
procedures live elsewhere:

- [program_import_pipeline.md](program_import_pipeline.md) - adding programs
  to the curated catalogs (`validate_program.py` checks a row)
- [grant_collectors.md](grant_collectors.md) - adding an announcement
  collector
- [grant_source_matrix.md](grant_source_matrix.md) - sources already in use,
  by evidence layer

A new table that ships to users also goes in `build/bundle_sources.json` with
its licence and vintage, and in the next bundle release (see the release
notes in `build/build_country_bundle.py`).

## Layers

| Layer | What it adds | Where it lands |
|---|---|---|
| Award history | Who has won a program before: precedent counts, amounts, recipients | `pipeline/downloaders`, `pipeline/converters`, `data/countries/<ISO3>/` |
| Catalog | Programs the shortlist can rank, with eligibility and status | `docs/*_grant_funding_programs.csv` |
| Announcement leads | New calls and funding news, read live | `collectors/` and `LIVE_ANNOUNCEMENT_SOURCES` |

## Candidates

Status: `candidate` (found, not checked in detail), `researching`, `blocked`,
`done`. Checked 2026-10-21 unless noted.

| Source | Layer | Country | Licence | Status | Why it matters | Next step |
|---|---|---|---|---|---|---|
| CRA charity returns, T3010 with the T1236 qualified-donee gifts schedule | Award history | Canada | Open Government Licence - Canada | candidate | Which Canadian foundations gave to which charities: foundation precedent, the main gap for nonprofit applicants | Find the current T1236 resource on open.canada.ca and its years; CRA lags several months behind filings |
| Alberta grant payments disclosure | Award history | Canada | Open Government Licence - Alberta | candidate | Provincial grant payments by fiscal year, 2014-15 to 2024-25, as CSV: a second province beside BC | Pull one year, map program and recipient columns, compare with the BC converter |
| Ontario Trillium Foundation open data | Award history | Canada | Open Government Licence - Ontario | candidate | Largest Ontario granting foundation; says it publishes all approved grants | Locate the grant files on otf.ca/open and check fields |
| IRS Form 990-PF e-file data (private foundations) | Award history | US | US federal | candidate | Itemized grants paid by private foundations | Size the AWS e-file data; a 2025 comment letter to the IRS reports billions in grants missing from the XML |
| SAM.gov Assistance Listings API | Catalog enrichment | US | US federal | candidate | Official description and eligibility text for every federal program (CFDA) | Needs a SAM.gov key; 10 requests a day without a registered role, 10 results a page |
| More Canadian provincial programs (beyond BC) | Catalog | Canada | per program | candidate | The Canada catalog has 3 provincial rows, all BC; nonprofits mostly reach wildfire mitigation money through provincial and local programs | Start with Alberta and Ontario community and emergency-preparedness programs |
| FEMA narrative press releases | Announcement leads | US | US federal | blocked | Funding mobilizations and policy announcements that OpenFEMA's declaration tables do not carry | The newsroom returns 403 even with a browser User-Agent; look for an RSS or API route |
| ReliefWeb API v2 | Announcement leads | Global | Verify per source | blocked | Curates reports from more than 4,000 sources, including funding appeals | v1 is decommissioned and v2 returned 403 on first try; DaedalMap tracks it separately for disaster data (`reliefweb_api` in its import tracker) |
| Tri-Agency abstracts for Canadian precedent text | Award history (enhancement) | Canada | Open Government Licence - Canada | candidate | The full NSERC and CIHR tables carry abstracts; they would give Canada the "similar past awards" search the US has (`precedent_text` capability) | The bundle ships trimmed tables; decide whether to ship an abstracts table or build it on demand |
| Tri-Agency award records (NSERC, SSHRC, CIHR) | Award history | Canada | Open Government Licence - Canada | done 2026-10-21 | Council precedent for research programs; in the Canada 0.2.0 bundle (trimmed to lookup, institution, and province columns) | - |
| Grants.gov posted and forecasted opportunities, complete | Open status | US | US federal | done 2026-10-21 | Every posted and forecasted opportunity, not an 8-keyword sample; in the USA 0.2.0 bundle | - |

Not pursued: SOUTHCOM and DOD public affairs (no feed; relevant items reach
the State Department feed), USAID newsroom (defunct since the 2025
dissolution), ProPublica foundation financials (kept private under ProPublica
API terms).

## Known gaps that are not data imports

- Live-lead place matching knows province and state names but not city names,
  so a national item about "Edmonton" can reach an Ontario project. A
  city-to-region lookup through the geometry tools would close it.
- The USAspending award table is most of the USA bundle (596 MB). Trimming it
  to the columns the tools read would shrink the download, as was done for
  Tri-Agency; it needs a check of every column the US backend uses.

## Sources

- [CRA T3010 charity data (McMaster guide)](https://seal.mcmaster.ca/cra-t3010-canadian-charity-returns-data)
- [Alberta grant payments disclosure](https://alberta.ca/grant-payments-disclosure)
- [Alberta grant disclosure open data](https://open.alberta.ca/dataset/grant-disclosure)
- [Ontario Trillium Foundation open data](https://otf.ca/open)
- [IRS 990 comment letter, 2025 (Aspen Institute)](https://www.aspeninstitute.org/wp-content/uploads/2025/11/IRS-Comments-2025-Form-990-Series.pdf)
- [SAM.gov Assistance Listings API](https://open.gsa.gov/api/assistance-listings-api/)
- [ReliefWeb API](https://reliefweb.int/help/api)
