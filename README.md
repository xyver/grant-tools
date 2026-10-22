# Grant Tools

A community-built grant research assistant adapted from DaedalMap's Research
MCP. Install it from this repository, connect an MCP client, and describe a
project. Grant Tools works through the
pre-submission phases: finding programs that fit, checking eligibility and
blockers, pulling precedent from past awards, shortlisting, and helping with
memos, budgets, and submission checklists. It never estimates odds of winning
and does not handle post-award reporting.

Coverage: every country runs the same workflow, and each country's backend
declares what it can do. **US** supports everything: discovery, precedent,
readiness scoring, memos, budget frameworks, writing templates, and live
open-opportunity checks. **Canada** supports discovery, precedent for every
shortlisted program, and program-page links for open status (labelled
unverified). A request a country cannot serve comes back as a named
`capability_gaps` entry, never as an empty result.

## What it is built for

Grant Tools was built for **disaster recovery, hazard mitigation, community
resilience, and climate adaptation** work, and its curated program catalogs
show it. These catalogs are what the shortlist ranks: a program has to be in
one to be matched, checked for eligibility and blockers, and scored.

| Catalog | Programs | Disaster, resilience, or climate | Other categories it carries |
|---|---:|---:|---|
| US (`docs/grant_funding_programs.csv`) | 127 (105 federal, 16 state, 5 foundation, 1 other) | 87 | economic development 26, humanitarian 21, infrastructure 21, international development 18, research 15, housing 9, research software 8, energy 7, transportation 7, commercialization 7, capacity building 7, workforce 6, food security 5, entrepreneurship 5, broadband 2, health 2, and smaller groups |
| Canada (`docs/canada_grant_funding_programs.csv`) | 48 (44 federal, 3 BC, 1 nonprofit) | 41 | research 12, data infrastructure 7, AI and data science 6, public safety 5, policy research 5, research software 5, geospatial 5, Indigenous research 4, Indigenous programs 3, and smaller groups |

Counts are programs carrying each tag (a program can carry several), as of
October 2026.

The data underneath is broader than the catalogs. USAspending, NSF, NIH, and
Grants.gov (every posted and forecasted opportunity) cover all US federal
programs, and Canada's Grants and Contributions disclosure and the
Tri-Agency award tables cover all Canadian federal programs. A project outside
the focus areas still runs, but expect a short shortlist: the funders are in
the data, the programs are not yet in a catalog. Adding them is the
[program import pipeline](docs/program_import_pipeline.md), and
`validate_program.py` checks a new row.

Place handling uses DaedalMap `loc_id`s (stable identifiers for counties,
states, provinces, and other places). Grant Tools calls DaedalMap's hosted
geometry MCP to turn place text into `loc_id`s by default. You can supply a
`loc_id` directly, or point `GEOMETRY_MCP_URL` at a local geometry server.

## Quickstart

1. **Get the code and install.** Python 3.11 or newer. From the repository
   folder:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```

2. **Get the data for your country.** Open the
   [Grant Tools download page](https://www.daedalmap.com/projects/grant-tools)
   and choose the USA or Canada grant data bundle. Review its size, coverage,
   sources, and checksum before confirming the download. Unzip the bundle into
   this repository's `data/` folder so the result contains `data/countries/`.
   You may unzip both into the same `data/` folder. Each ZIP also includes
   source details and checksums in `countries/<USA|CAN>/grant_data_manifest.json`.
   `/health` on the running server shows which bundles are installed and
   whether this code version can read them. You can build tables from
   upstream sources with the pipeline below.

3. **Configure only if needed.** `data/` is the default. If you unzipped the
   bundle elsewhere, copy `.env.example` to `.env` and set `GRANT_DATA_ROOT` to
   the folder containing `countries/`.

4. **Start the server.**

   ```powershell
   .\.venv\Scripts\python server.py
   ```

5. **Connect your MCP client** to `http://127.0.0.1:7020/mcp`. For Claude Code:

   ```powershell
   claude mcp add --transport http grants http://127.0.0.1:7020/mcp
   ```

   Then describe your project. The first tool, `grant_intake_or_update_project`,
   takes it from there.

## Configuration

Settings come from environment variables or a `.env` file next to the code.

| Variable | Default | Meaning |
|---|---|---|
| `GRANT_DATA_ROOT` | `./data` | Folder containing `countries/` (the downloaded bundle or your own build) |
| `GRANT_RAW_ROOT` | `<GRANT_DATA_ROOT>/Raw data` | Where the pipeline's downloaders save raw upstream files |
| `GRANT_STATE_DIR` | `./state` | Where collectors write snapshots and where the tools read them |
| `GRANT_COLLECTORS` | `all` | Announcement collectors enabled for `collectors/run_all.py`: `arc`, `california`, `canada`, `state_dept`, `all`, or `none` |
| `GEOMETRY_MCP_URL` | `https://app.daedalmap.com/mcp` | DaedalMap geometry tools used for place to `loc_id` lookups |
| `GRANT_MCP_HOST` / `GRANT_MCP_PORT` | `127.0.0.1` / `7020` | Where the server listens |
| `GRANT_MCP_ACCESS_TOKENS` | empty | Comma-separated bearer tokens. Leave empty for local use; set before exposing the server beyond your machine |
| `GRANT_DUCKDB_MEMORY_LIMIT` | `1GB` | Query memory cap. Country-wide USA queries need more than 256MB |

## Place lookups

The grant tools need one thing from DaedalMap: turning place text (a county,
state, ZIP code, province, or municipality) into a `loc_id`. They call the
geometry tool `convert_reference` at `GEOMETRY_MCP_URL`, DaedalMap's hosted MCP
by default. No setup or account is needed; a grant session makes only a few
lookups, well inside the free anonymous allowance. You can also pass a `loc_id`
directly in place of place text.

Include the parent region for names that repeat: "Washington County, Oregon",
"Prince George, British Columbia". When a name is ambiguous or not found,
intake says so in `notes` and `unresolved_places`, with candidate `loc_id`s
where the geometry tools returned them.

## Data

The tools read processed tables from `GRANT_DATA_ROOT`. Downloaded ZIPs provide
ready-to-use snapshots. The included pipeline can build your own tables under
`data/` from official upstream sources. Collector output goes to `state/` and
adds recent announcement leads; it does not replace historical award tables.

| Layer | What | How often | Where |
|---|---|---|---|
| **Country bundles** | Processed award, program, and opportunity tables, ready to use | Download once | Separate USA and Canada downloads above |
| **Historical pipeline** | Downloaders fetch raw upstream files; converters turn them into processed tables in `data/` | Occasionally (monthly, or when a source publishes) | `pipeline/downloaders/`, `pipeline/converters/` |
| **Collectors** | Watch live announcement feeds (ARC, California, Canada, US State Department) for new opportunities | As often as you like; daily is plenty | `collectors/` |

For example, build California opportunities and awards yourself:

```powershell
.\.venv\Scripts\python pipeline\downloaders\download_california_grants_portal.py
.\.venv\Scripts\python pipeline\converters\convert_california_grants_portal.py
```

Each source has its own downloader and converter. Some upstream files are large
and take time to fetch. Run only the sources you need; their outputs use the
same `data/countries/` layout as the downloadable bundles.

## Announcement collectors

The included ARC, California, Canada, and US State Department collectors can
run independently of the historical pipeline. List them, probe feeds without
writing, and then run the ones you want:

```powershell
.\.venv\Scripts\python collectors\run_all.py --list
.\.venv\Scripts\python collectors\run_all.py --only arc,california --probe
.\.venv\Scripts\python collectors\run_all.py --only arc,california
```

To keep that selection, set `GRANT_COLLECTORS=arc,california` in `.env` and run
`collectors/run_all.py` without `--only`. The command runs once. Schedule it
daily with Task Scheduler or cron for ongoing checks. Use
`GRANT_COLLECTORS=none` to disable scheduled polls. Each collector also runs on
its own (`python collectors\arc_grant_announcements.py`) and supports `--probe`.
The collectors need no API keys. Their snapshots go to `GRANT_STATE_DIR`.

Phase 1 reads the snapshots that apply to the project (ARC for Appalachia,
California for California, Canada for Canadian projects, State Department for
international work) and returns only leads about the project's sectors and
place in `regional_evidence`. When a feed has funding news but none of it fits,
the result says so (`relevance_note`) rather than listing unrelated items.

"Is this program open right now?" asks Grants.gov live for US federal
programs. The shortlist's open status comes from the bundle's opportunity
snapshot and is labelled `checked_snapshot`; programs without a Grants.gov code
(state programs, Canada) return their official program page to check.

### Sources

| Source | Layer | Licence |
|---|---|---|
| USAspending federal grant awards | Historical | Public domain (US government work) |
| NSF awards | Historical | Public domain (US government work) |
| NIH RePORTER projects | Historical | Public domain (US government work) |
| Grants.gov open opportunities | Historical (current snapshot) | US federal |
| Canada Grants and Contributions | Historical | Open Government Licence - Canada |
| Tri-Agency awards (SSHRC, CIHR, NSERC) | Historical (pipeline only; not in the Canada bundle yet) | Open Government Licence - Canada |
| BC grant awards | Historical | Open Government Licence - BC |
| California Grants Portal opportunities and awards | Historical | data.ca.gov |
| ARC announcements | Collector | US federal-state commission |
| California Grants Portal feed | Collector | data.ca.gov |
| Canada government news | Collector | canada.ca terms |
| US State Department press releases | Collector | Public domain (US government work) |

Cite the original sources when you use their data.

The code is available under the [MIT License](LICENSE). Country bundles and
upstream source records retain their respective source terms; see each bundle's
`grant_data_manifest.json` for provenance.

## Tools

| Tool | Purpose |
|---|---|
| `grant_intake_or_update_project` | Start or update a project profile from plain-language input; returns a `session_id` |
| `grant_analyze_current_phase` | Run the analysis for the session's current phase |
| `grant_set_phase` | Move the session to a different phase |
| `grant_select_target_program` | Pick the program to develop a proposal for |

## Layout

| Path | Contents |
|---|---|
| repo root | Grant analysis modules (`grant_mcp_module.py`, `us_backend.py`, `can_backend.py`, ...) and tests |
| `collectors/` | Live announcement collectors and `run_all.py` |
| `pipeline/downloaders/`, `pipeline/converters/` | Historical data pipeline |
| `build/` | Packaging scripts |
| `docs/` | Method and source documentation; the curated program catalog CSVs |
| `state/` | Collector output (not committed) |

## Docs

- [docs/grant_mcp_quickstart.md](docs/grant_mcp_quickstart.md) - the tool
  workflow once connected
- [docs/grant_mcp_mode.md](docs/grant_mcp_mode.md) - product shape and method
- [docs/program_import_pipeline.md](docs/program_import_pipeline.md) - how to
  add a program so the tools use it correctly; `validate_program.py` checks it
- [docs/proposal_section_model.md](docs/proposal_section_model.md) - the
  proposal section vocabulary and how per-funder requirements resolve
- [docs/grant_collectors.md](docs/grant_collectors.md) - how to add sources
  and collectors
- [docs/grant_source_matrix.md](docs/grant_source_matrix.md) - source
  inventory by evidence layer
