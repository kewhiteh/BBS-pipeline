# Status & known limitations

What is known, unknown, and decided about BBS-Pipeline. Update this file in the same
change that discovers or fixes any of it. The phase plan and its decisions live in
[instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md).

## Verified

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q` pass
  (155 tests, 2026-11-06).
- Code names for BCR (37 codes), Stratum (71), RunType (2), and QualityCurrentID (2) are
  read from the release metadata XML (`<attr>/<edom>` lists), checked against the
  2026 release (1966–2025). The editor and `bbs values` show them as `27 · Southeastern
  Coastal Plain`; tests in `tests/test_ui.py` cover this.

## Decisions

- **Code names come only from the release XML, never a typed-in table** (same rule as
  StateNum, see `sources/regions.py`). If the raw release directory has no metadata XML,
  or more than one, the editor shows bare codes instead of guessing names.
- **Editor help text** (tooltips and the intro line on each tab) describes what each
  setting does in plain terms. It was written from `config.py`/`contracts.py`; wording
  about BBS meaning (e.g. RunType, strata) follows the release metadata.

## Known limitations / workarounds

- Code names are read from `source.raw_dir`, not the cache. A setup that has only a
  cache (raw files deleted) shows bare codes. Workaround: keep the release files, or run
  **Fetch release** again.
- RunType and QualityCurrentID pick lists offer only 0 and 1 (plus any value already in
  the profile), not the codes found in the data.
- Observer tenure tooltips do not say whether "years" means years on that route or in
  the whole survey. Check `stages/runs.py` before relying on the wording.

## Environment notes

- Moving the project folder breaks `.venv`: its scripts keep the old absolute Python
  path, so `uv run streamlit ...` fails with "Failed to spawn ... No such file or
  directory". Fix: `rm -rf .venv && uv sync --extra dev`. Plain `uv sync` leaves out
  ruff and pytest.

## Open items

- Help text is unreviewed by Kyle. Check the wording, especially the observation source,
  stop layout, and tenure descriptions.
- The UI has no in-app walkthrough or example profile picker for first-time users beyond
  tooltips and tab captions.

## How to reproduce key checks

```bash
uv sync --extra dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
uv run bbs values --config project.toml routes.bcrs   # codes with names
uv run streamlit run ui/app.py                        # Routes & Runs tab: named BCRs/strata
```
