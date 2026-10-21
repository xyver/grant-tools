# Grant Source Matrix

Status: working inventory
Last updated: 2026-07-07

This matrix separates grant sources by evidence layer. The distinction
matters for the MCP: live announcements, observed live history, open
opportunities, and award precedent should not be presented as the same kind
of proof.

## Evidence Layers

| Layer | Meaning | Storage pattern | MCP use |
|---|---|---|---|
| Curated eligibility | Human-reviewed program rules and blockers | CSV under grant analyzer docs | Can this applicant plausibly use this program? |
| Historical awards | Source-published past awards/recipients | Raw download -> converted parquet | Has this source funded similar work before? |
| Open opportunities | Source-published current opportunities/deadlines | Snapshot CSV/parquet or live collector snapshot | What can be pursued now? |
| Live announcements | Source-published news/feed items | `BaseCollector` current-state snapshot | What new funding signal just appeared? |
| Observed live history | Our accumulated record of live announcements over time | Raw observed log -> partitioned/compacted parquet | What funding signals have we observed from this feed? |

## Current Sources

| Source | Geography | Curated eligibility | Historical awards | Open opportunities | Live announcements | Observed live history | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| US federal grant programs | US | Yes | Yes | Yes | Partial | No | Eligibility rows plus USAspending, Grants.gov, and State Dept live announcements. |
| Grants.gov | US | No | No | Yes | No | No | `grants_gov_open_opportunities/latest.csv`; open-opportunity layer only. |
| USAspending grants | US | No | Yes | No | No | No | Federal award precedent by CFDA/assistance listing. |
| State Dept press releases | US/international | No | No | No | Yes | Collector `observed/` log | Live signals for foreign assistance; not award precedent. |
| Canada federal G&C | Canada | Yes | Yes | No | Yes | Collector `observed/` log | Canada backend has federal disclosure plus federal announcement feed. |
| Canada Tri-Agency | Canada | No | Yes | No | Partial | No | Research-award precedent; separate from general G&C. |
| California Grants Portal | California | Partial | Yes | Yes | Yes | Observed JSONL landing | Live collector built; raw historical/open CSVs downloaded and converted to parquet. Curated rows now cover reusable California state pathways from SGC, CAL FIRE, Caltrans, CEC EPIC/solicitations, CalSEED, and LCI/ICARP. |
| ARC program family | Appalachia | Yes | Not confirmed | Page-based | Yes | Observed JSONL landing | Curated rows now cover POWER, INSPIRE, Area Development, ARISE, WORC, READY, Access to Capital, Broadband, Local Access Roads, and Energy Hub. Live collector watches ARC announcements separately. |
| Emerging Prairie / Grand Farm ecosystem | Upper Midwest agtech | Partial | No | Page-based | Page/news-based | No | Use as source-backed targeting context for MN/ND/SD agtech, growers, startups, field trials, community innovation, and rural upgrades; not an award-history source. |
| USDA Rural Development | Rural US / Heartland | Partial | Yes through USAspending | Program-page based | Not yet | No | Backbone source family for agriculture, rural infrastructure, energy, water, telecom, community facilities, and disaster-related rural upgrades. |
| Virginia Innovation Partnership Corporation | Virginia / GMU / startups | Yes | No | Page-based | Not yet | No | Curated rows cover university proof-of-concept, Lab-to-Launch, EIR, Launch, FFAP/SBIR-STTR support, and public-safety innovation. |
| Puerto Rico CDBG recovery portal | Puerto Rico | Not yet | Not confirmed | Not confirmed | Stale RSS | No | Use curated rows/page research first; RSS is not current enough. |
| BC provincial award datasets | British Columbia | Not yet | Yes | No | No | No | DataBC Gaming Grants and BCKDF imported to provincial award-history parquet. |
| BC UBCM CEPF/CRI | British Columbia | Not yet | Not confirmed | Page-based | No confirmed feed | No | Use curated rows/page watcher first for emergency-preparedness programs. |
| CAL FIRE grants | California | Yes | Possible | Page-based | Not confirmed | No | Curated Wildfire Prevention row imported. Best as partner/next-cycle evidence for wildfire risk mapping, evacuation plans, LHMP/CWPP, road egress/ingress, and critical infrastructure protection. |
| CAL FIRE Forest Health Research | California | Yes | Page archives | Page-based | Not confirmed | No | Curated research row imported, including graduate student and UC/CSU-facing materials. Current FY 2026-27 concept proposals are due July 30, 2026. |
| CAL FIRE Regional Wildfire and Landscape Resilience | California | Yes | Page-based | Page-based | Not confirmed | No | Curated regional partnership row imported for wildfire risk reduction, landscape resilience, community safety, and local/regional capacity. |
| Caltrans Sustainable Transportation Planning Grants | California | Yes | Award lists on pages | Page-based | Not confirmed | No | Curated STPG row imported. Partner-led route for shelter access, evacuation routing, disadvantaged-community planning, multimodal accessibility, and transportation resilience. |
| California Energy Commission EPIC/solicitations | California | Yes | Award/source pages | Page-based | Not confirmed | No | Curated EPIC, solicitation-index, and CalSEED rows imported. Best California university-led state R&D path when the project connects to energy resilience, grid impacts, community technology, forecasting, clean transportation, or commercialization. |
| Strategic Growth Council CRC/TCC | California | Yes | Previous awards/pages | Page-based | Not confirmed | No | Curated Community Resilience Centers and Transformative Climate Communities rows imported. Strong partner-led implementation sources for disadvantaged-community resilience hubs, multi-benefit climate infrastructure, planning, transportation, health, energy, and community benefits. |
| LCI/ICARP Adaptation Planning | California | Yes | Round 1 resources | Closed/canceled watchlist | Not confirmed | No | Curated APGP watchlist row imported. Round 2 is canceled after budget adjustments, so do not surface as apply-now without a reopened round. |

## Design Rules

- A live announcement feed is not historical award precedent.
- Every region should support all grant categories where credible sources
  exist. Regional layers should change emphasis and pathway detail, not
  artificially narrow a user's project to one local stereotype.
- Observed live history is our record of what the feed published after we
  started watching it. It is useful, but it is not a complete source archive
  unless the upstream provides backfill.
- Store source-published historical awards separately from observed live
  history.
- If a live source later exposes an archive/API, treat that as a separate
  historical-source research pass.
- The MCP should label the evidence layer in outputs so users know whether a
  recommendation is based on eligibility rules, past awards, current open
  opportunities, or live announcements.

## Observed Live History Pattern

For sources like ARC:

1. Collect the current RSS/Atom/API window with a `live_only` collector.
2. Deduplicate items by stable id or URL.
3. Keep the current-state snapshot for Ops/current status.
4. Persist all newly observed items to an append-only raw log or dated landing
   artifact.
5. Convert/compact observed items to parquet for query access.
6. Label the result as `observed_live_history`, not source-published award
   history.

Prefer partitioned or periodically compacted parquet over appending directly
to one shared parquet file on every poll. A single parquet rewrite is fine for
manual/small local experiments, but a partitioned dataset or append-only raw
log is safer for hosted collectors and replay.

## Cadence Matrix

This grant matrix records source-specific refresh choices. Schedule the local
collectors to suit your own use.

Cadence is split into source-change expectations, lightweight polling, and
heavier processing. The collector should normally do the first cheap step; the
converter/publisher should run asynchronously on the slower process cadence.

| Source family | Source update cadence | Poll cadence | Process cadence | Processing notes |
|---|---:|---:|---:|---|
| Grant announcement RSS/Atom feeds | Irregular, often weekly/monthly | Daily | Weekly | Record newly observed items; compact observed JSONL to parquet later. |
| State/provincial open opportunities | Daily to weekly | Daily | Daily or weekly | Open/close dates can matter, so keep polling cheap and frequent. |
| Source-published awards files | Monthly to annual | Weekly/monthly metadata check | Monthly/quarterly | Check resource metadata before full download/conversion. |
| Annual fiscal-year award files | Annual | Monthly/quarterly metadata check | Quarterly or on detected change | Avoid rebuilding unless resource inventory changes. |
| Curated eligibility rows | Manual | n/a | Release-based | Human review and regression tests gate updates. |
| Page watchers without feeds | Irregular | Daily or weekly | Weekly | Use stable normalized hashes, not whole-page diffs. |

Current grant-collector defaults:

| Collector | Default poll cadence | Why |
|---|---:|---|
| `california_grants_portal` | Daily | Open opportunities can change, but the feed is not event-critical. |
| `arc_grant_announcements` | Daily | Builds observed history without pretending updates are frequent. |
| `canada_grant_announcements` | Daily | Federal grant announcements are slow-moving. |
| `state_dept_press_releases` | Daily | Grant-relevant press releases do not need 30-minute polling. |

## Regional Scope Notes

### Upper Midwest / Heartland

For current DaedalMap grant targeting, "Heartland" should not be treated as a
single national geography bucket. The immediate source-backed definition is the
Emerging Prairie / Grand Farm ecosystem: North Dakota-centered agtech and
community innovation, with an Upper Midwest agriculture-problem scope across
Minnesota, North Dakota, and South Dakota.

This points the MCP toward:

- agriculture technology, field trials, growers, startups, researchers, and
  investors;
- farm data infrastructure, AI in agriculture, input efficiency, automation,
  and climate/disaster resilience for producers;
- rural community upgrades, workforce, broadband, energy, water, food systems,
  and local economic development.

Use existing rows like `bush_foundation_ci`, `usda_reap`,
`usda_farmers_market`, `usda_rfsip`, `nsf_engines`, and relevant USDA Rural
Development infrastructure programs first. Add future scopes such as
`upper_midwest_agtech`, `delta_region`, `northern_border`, and
`southeast_crescent` only when their source families have curated rows and
tests.

### Virginia / DC Area

Northern Virginia and Washington DC projects overlap heavily with federal
research and resilience programs. Do not treat all Virginia projects as
Appalachia. George Mason University / Fairfax County should normally use the
federal higher-ed research path plus Virginia/DMV partner pathways unless the
project explicitly serves ARC counties or uses Appalachian/coal-community
language.

The first imported Virginia layer is VIPC. It is strongest for university
proof-of-concept, technology transfer, commercialization, startups, SBIR/STTR
readiness, smart-community/public-safety technology, and GMU-adjacent research
software. It is not a flood/hazard-mitigation pass-through source; those still
need VDEM/DCR/GO Virginia/DC source-specific rows.

Priority Virginia/DC source families to research next:

- Virginia flood resilience and hazard mitigation pass-through programs;
- Virginia emergency management and FEMA Hazard Mitigation Assistance pathways;
- GO Virginia and regional economic/workforce grants;
- Virginia Innovation Partnership Corporation and commercialization support;
- Metropolitan Washington Council of Governments and Northern Virginia regional
  planning/implementation opportunities;
- District of Columbia grants only when the project serves DC agencies,
  neighborhoods, or community organizations directly.
