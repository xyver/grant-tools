# Grant Tools

A community-built grant research assistant adapted from DaedalMap's Research
MCP. Install it from this repository, connect an MCP client, and describe a
project. Grant Tools works through the
pre-submission phases: finding programs that fit, checking eligibility and
blockers, pulling precedent from past awards, shortlisting, and helping with
memos, budgets, and submission checklists. It never estimates odds of winning
and does not handle post-award reporting.

Coverage: **US** is full (discovery, precedent, readiness scoring, writing
templates, live opportunities). **Canada** has discovery and precedent only,
and says so rather than returning empty results.

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

2. **Get the data for your country.** Download the
   [USA bundle](https://downloads.daedalmap.com/downloadable/grants/usa/grant-usa-0.1.0.zip)
   (749 MB) or the
   [Canada bundle](https://downloads.daedalmap.com/downloadable/grants/can/grant-can-0.1.0.zip)
   (2.4 MB). Unzip either into this repository's `data/` folder so the result
   contains `data/countries/`. You may unzip both into the same `data/` folder.
   Each ZIP includes source details and checksums in `grant_data_manifest.json`.
   You can also build tables from upstream sources with the pipeline below.

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
| `GRANT_STATE_DIR` | `./state` | Where collectors write snapshots and where the tools read them |
| `GRANT_COLLECTORS` | `all` | Announcement collectors enabled for `collectors/run_all.py`: `arc`, `california`, `canada`, `all`, or `none` |
| `GEOMETRY_MCP_URL` | `https://app.daedalmap.com/mcp` | DaedalMap geometry tools used for place to `loc_id` lookups |
| `GRANT_MCP_HOST` / `GRANT_MCP_PORT` | `127.0.0.1` / `7020` | Where the server listens |
| `GRANT_MCP_ACCESS_TOKENS` | empty | Comma-separated bearer tokens. Leave empty for local use; set before exposing the server beyond your machine |

## Place lookups

The grant tools need one thing from DaedalMap: turning place text (a county,
state, ZIP code, or province) into a `loc_id`. They call the geometry tool
`convert_reference` at `GEOMETRY_MCP_URL`, DaedalMap's hosted MCP by default.
No setup or account is needed; a grant session makes only a few lookups, well
inside the free anonymous allowance. You can also pass a `loc_id` directly in
place of place text.

## Data

The tools read processed tables from `GRANT_DATA_ROOT`. Downloaded ZIPs provide
ready-to-use snapshots. The included pipeline can build your own tables under
`data/` from official upstream sources. Collector output goes to `state/` and
adds recent announcement leads; it does not replace historical award tables.

| Layer | What | How often | Where |
|---|---|---|---|
| **Country bundles** | Processed award, program, and opportunity tables, ready to use | Download once | Separate USA and Canada downloads above |
| **Historical pipeline** | Downloaders fetch raw upstream files; converters turn them into processed tables in `data/` | Occasionally (monthly, or when a source publishes) | `pipeline/downloaders/`, `pipeline/converters/` |
| **Collectors** | Watch live announcement feeds (ARC, California, Canada) for new opportunities | As often as you like; daily is plenty | `collectors/` |

For example, build California opportunities and awards yourself:

```powershell
.\.venv\Scripts\python pipeline\downloaders\download_california_grants_portal.py
.\.venv\Scripts\python pipeline\converters\convert_california_grants_portal.py
```

Each source has its own downloader and converter. Some upstream files are large
and take time to fetch. Run only the sources you need; their outputs use the
same `data/countries/` layout as the downloadable bundles.

## Announcement collectors

The included ARC, California, and Canada collectors can run independently of
the historical pipeline. List them, probe feeds without writing, and then run
the ones you want:

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

### Sources

| Source | Layer | Licence |
|---|---|---|
| USAspending federal grant awards | Historical | Public domain (US government work) |
| NSF awards | Historical | Public domain (US government work) |
| NIH RePORTER projects | Historical | Public domain (US government work) |
| Grants.gov open opportunities | Historical (current snapshot) | US federal |
| Canada Grants and Contributions | Historical | Open Government Licence - Canada |
| Tri-Agency awards (SSHRC, CIHR, NSERC) | Historical | Open Government Licence - Canada |
| BC grant awards | Historical | Open Government Licence - BC |
| California Grants Portal opportunities and awards | Historical | data.ca.gov |
| ARC announcements | Collector | US federal-state commission |
| California Grants Portal feed | Collector | data.ca.gov |
| Canada government news | Collector | canada.ca terms |

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
