# Announcement collectors

Collectors check public announcement feeds for recent grant leads. They run
independently of the historical award pipeline and write snapshots under
`GRANT_STATE_DIR` (`state/` by default).

```powershell
python collectors/run_all.py --list
python collectors/run_all.py --only arc,california --probe
python collectors/run_all.py --only arc,california
```

Available names are `arc`, `california`, `canada`, and `state_dept`. The State
Department feeds expose only their 10 most recent items, so run that one at
least daily. Use `--probe` to check
feeds without writing snapshots. Set `GRANT_COLLECTORS=arc,california` in
`.env` to keep a selection; use `none` to disable scheduled polls. Run the
command once per check, or schedule it daily with Task Scheduler or cron.

Each snapshot keeps the newest 100 items per feed, so a collector reading many
feeds cannot let its busiest feeds push the others out. Every new item is also
appended to `observed/<date>.jsonl`.

Each collector also has its own entry point in `collectors/`. Add a source by
following the existing collector structure, recording its source URL and
terms, and writing output to `GRANT_STATE_DIR`. To have the tools read it, add
a row to `LIVE_ANNOUNCEMENT_SOURCES` in `grant_mcp_module.py` with its country,
when it applies to a project, and its `scope`: `regional` for a feed already
limited to one region (sector words may appear anywhere in an item), or
`national` (sector words must appear in the title).
