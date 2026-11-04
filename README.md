# BBS-Pipeline v3

Config-driven extraction of North American Breeding Bird Survey (BBS) data. One TOML
**run profile** per project describes the whole extraction: release, route and run
filters, observation handling, species, joins, and the output file. The CLI and the
Streamlit editor (`ui/app.py`) both read and write that profile.

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
BBS_PROFILE=project.toml uv run streamlit run ui/app.py
```

Pick lists for states, BCRs, strata, routes (narrowed to the chosen states and BCRs),
species, families, orders, guild trait values, and each table's join columns come from
the release. Edits stay in a draft until **Save profile**; the sidebar shows unsaved
changes and validation errors. **Inspect** shows the count after every stage, the exact
output columns, and the guild report (turn on *Live inspect* to update after each
change); **Run** writes the output. Without a fetched release, a **Fetch release** button
appears and list fields fall back to text boxes.

See `examples/profile_template.toml` for every setting and its default.

## Guild file

A CSV with one row per species (`aou`, `scientific_name`, and/or `common_name`, plus any
trait columns) can filter species by trait and add the traits to the output. See
[docs/guild_file.md](docs/guild_file.md) and `examples/guilds_example.csv`.
