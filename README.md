# BBS-Pipeline v3

Clean-room v3 North American Breeding Bird Survey (BBS) Spatial & Ecological ETL Pipeline.
Decouples a Two-Way Streamlit Visual Workbench (`ui/app.py`) from a headless Polars/Shapely spatial ETL backend using frozen Pydantic v2 JSON contracts (`config/guilds.json`, `config/protocol.json`, `config/spatial.json`).

## Quick start

```bash
uv sync --extra dev
uv run bbs fetch --dest data/raw          # once: download the 2026 release (DOI 10.5066/P144YU3S)
uv run bbs run --config-dir config --offline
```

`bbs fetch` keeps files already in `--dest` whose MD5 matches ScienceBase, and writes
`release.json` (item id, DOI, checksums). The first `bbs run` builds a typed Parquet cache
in `data/sanitized_cache`; later runs reuse it until `release.json` changes. Pass a
different release with `bbs fetch --release <DOI or ScienceBase item id>`.
