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
- **Halt and ask on ambiguity.** Anything marked DECISION in the plan is Kyle's call. Do not pick a value.
- Do not guess study-design values (states, years, species, guilds) for Kyle's projects.

## Repo standard
This repo follows [docs/REPO_STANDARD.md](docs/REPO_STANDARD.md); its Agent rules apply here.
- Decisions record: `docs/STATUS.md` "Decisions" plus the decision table in `docs/instructions/BBS_V3_REFACTOR_PLAN.md` § 5 (no DECISIONS.md).
- `review`: main is protected by a repository ruleset (`pull_request` rule), not classic protection.
