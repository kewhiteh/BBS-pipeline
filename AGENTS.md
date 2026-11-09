# Agent rules for BBS-Pipeline

Kyle is the author and makes the decisions. The active plan is
`docs/instructions/BBS_V3_REFACTOR_PLAN.md`. Work phase by phase, one branch and one PR per phase.

## Data rules
- **Raw columns are read as strings.** Cast to explicit dtypes in one place, after loading. Never rely on schema inference.
- **Runs are the unit of sampling.** Observations join to surviving runs on `RouteDataID`. A run removed by any filter must not contribute rows or counts, with zero-fill on or off.
- **Zero-fill uses each route's historical species pool**: every species ever recorded on that route in the full BBS record. Each of those species gets a count or a zero for every surviving run. Species never recorded on a route get no rows there. Stops beyond `TotalStops` stay NULL, not 0.
- **No fabricated values.** No (0, 0) coordinates, no placeholder geometry, no invented counts. Missing data is NULL and gets reported.
- **No silent `except`.** Catch only specific exceptions, and only when the fallback is something the user explicitly chose. Otherwise raise.
- routes.csv coordinates are NAD83 (EPSG:4269). Label them as such before reprojecting.
- Joins must not change the row count. Assert it.

## Process
- Before every PR, run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`.
- **After every change to the code (`src/`, `ui/`, `scripts/`), also run
  `uv run python scripts/validate_outputs.py`** and require all checks to pass (Kyle,
  2026-11-09). If a change makes a check out of date (new output columns, a new source or
  layout, a changed default), update the script in the same PR, and update
  `docs/VERIFICATION.md` with the date and result. Docs-only PRs are exempt.
- **Halt and ask on ambiguity.** Anything marked DECISION in the plan is Kyle's call. Do not pick a value.
- Do not guess study-design values (states, years, species, guilds) for Kyle's projects.

## Troubleshooting the editor
- When Kyle says something happened in the editor, **read `logs/ui_activity.log` first**
  (newest lines last) before asking for screenshots. One JSON line per event: `action`
  (with the full `profile_toml` at that moment), `edit` (field: from -> to), `info` /
  `warning` / `error` (every message box shown, when it appeared), `result` (Inspect/Run
  counts), plus `session`, `page` and `profile`. Errors also carry the traceback, and
  `logs/ui_errors.log` holds errors and warnings only. Rerun the logged `profile_toml`
  to reproduce. (Kyle, 2026-11-09: no screenshots or copy-paste needed.)

## Repo standard
This repo follows [docs/REPO_STANDARD.md](docs/REPO_STANDARD.md); its Agent rules apply here.
- Decisions record: `docs/STATUS.md` "Decisions" plus the decision table in `docs/instructions/BBS_V3_REFACTOR_PLAN.md` § 5 (no DECISIONS.md).
- `review`: main is protected by a repository ruleset (`pull_request` rule), not classic protection.
