# BBS-Pipeline v3

Config-driven extraction of North American Breeding Bird Survey (BBS) data. One TOML
**run profile** per project describes the whole extraction: release, route and run
filters, observation handling, species, joins, and the output file. The CLI and the
Streamlit editor (`ui/app.py`) both read and write that profile.

What is verified, known limitations, and open items: [docs/STATUS.md](docs/STATUS.md).
What has been checked against the real release (and what has **not**, so use with
caution): [docs/VERIFICATION.md](docs/VERIFICATION.md). Rerun the data checks with
`uv run python scripts/validate_outputs.py`.

## Quick start

```bash
uv sync --extra dev
cp examples/profile_template.toml project.toml     # edit: states, years, species, output
uv run bbs fetch    --config project.toml           # once: download the release
uv run bbs inspect  --config project.toml --offline # counts per stage + exact columns
uv run bbs run      --config project.toml --offline # writes the output file + manifest
```

- `--set dotted.key=value` overrides any profile value for one command, e.g.
  `--set runs.start_year=2000 --set 'routes.states=["NC","VA"]'`.
- `bbs validate --config project.toml` checks a profile and prints it with every default.
- Unknown keys are errors, and every error names the key.
- `bbs fetch` keeps files already in `source.raw_dir` whose MD5 matches ScienceBase and
  writes `release.json`. The first run builds a typed Parquet cache in `source.cache_dir`;
  later runs reuse it until `release.json` changes.
- `bbs run` writes `output.path` (format from the extension: `.parquet`, `.csv`,
  `.geoparquet`, `.gpkg`, `.fgb`, `.geojson`) and `<path>.manifest.json` with the resolved
  profile, release, package version and git commit, and the counts after each stage.
- `bbs values --config project.toml routes.states` (or `routes.bcrs`, `routes.route_ids`,
  `species.families`, `joins.weather`, `guilds.<trait>`, ...) lists the valid values for a
  field from the release.

## Editor

```bash
uv run streamlit run ui/app.py                       # open a profile from the header
BBS_PROFILE=project.toml uv run streamlit run ui/app.py
```

Everything the CLI does can be done in the editor:

| CLI | Editor |
|---|---|
| `bbs fetch` | **Fetch release** button (**Settings** page) |
| `--config FILE` | Header **Profile** menu: profiles found in the project, or any path; **New profile**; **Start from an example** |
| editing the TOML | Every setting: the dataset on the **Build** page; release, folders and download settings on the **Settings** page |
| `bbs validate` | Validation messages at the top of the page and the **Profile TOML** view in the Profile menu (with download) |
| `bbs values` | Pick lists for states, BCRs, strata (with names from the release metadata), routes (narrowed to the chosen states and BCRs), species, families, orders, guild traits, and each table's join columns |
| `--set key=value` | Change the field and Inspect/Run without saving |
| `bbs inspect` | **Inspect**: stage counts, exact output columns, guild report, preview; marked out of date after any edit |
| `bbs run` | **Run**: writes the file, then offers it as a download |

Failures in the editor (fetch, inspect, run, unexpected errors) are written with their
full traceback to `logs/ui_errors.log`; the Settings page shows, downloads and clears it. Everything else that happens in the editor (actions with the profile used, edits, messages shown, results) goes to `logs/ui_activity.log`, so a problem can be traced without screenshots. After pulling or switching branches, restart
Streamlit; a running server keeps the old library code loaded.

Edits stay in a draft until **Save profile** or **Save as** (which will not overwrite an
existing file unless you tick *Overwrite*). Entries the editor cannot read (a malformed
overlay or filter line, a non-number in a number list) are listed at the top of the page and
block saving and running until fixed. Guild traits without a pick list (free-text
columns) can be filtered in the *Other trait filters* box.

## Guild file

A CSV with one row per species (`aou`, `scientific_name`, and/or `common_name`, plus any
trait columns) can filter species by trait and add the traits to the output. See
[docs/guild_file.md](docs/guild_file.md) and `examples/guilds_example.csv`.
