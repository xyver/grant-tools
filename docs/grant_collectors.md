# Announcement collectors

Collectors check public announcement feeds for recent grant leads. They run
independently of the historical award pipeline and write snapshots under
`GRANT_STATE_DIR` (`state/` by default).

```powershell
python collectors/run_all.py --list
python collectors/run_all.py --only arc,california --probe
python collectors/run_all.py --only arc,california
```

Available names are `arc`, `california`, and `canada`. Use `--probe` to check
feeds without writing snapshots. Set `GRANT_COLLECTORS=arc,california` in
`.env` to keep a selection; use `none` to disable scheduled polls. Run the
command once per check, or schedule it daily with Task Scheduler or cron.

Each collector also has its own entry point in `collectors/`. Add a source by
following the existing collector structure, recording its source URL and
terms, and writing output to `GRANT_STATE_DIR`.
