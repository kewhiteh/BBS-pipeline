"""Typed parquet cache built from one release directory, tied to its ``release.json``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from bbs_pipeline.sources.release import RELEASE_JSON, ReleaseInfo, sha256_file
from bbs_pipeline.sources.tables import load_release_tables

CACHE_MANIFEST = "cache_manifest.json"
# Bump when the cached table layout or types change, so old caches are rebuilt
CACHE_SCHEMA_VERSION = 1


def verify_release_files(release_dir: Path, info: ReleaseInfo) -> None:
    """Raise if any file recorded in release.json is missing or has changed."""
    for f in info.files:
        path = release_dir / f.name
        if not path.exists():
            raise FileNotFoundError(f"'{f.name}' listed in {RELEASE_JSON} is missing.")
        if sha256_file(path) != f.sha256:
            raise ValueError(
                f"'{f.name}' no longer matches {RELEASE_JSON} (sha256 changed); "
                "re-run `bbs fetch` for this directory."
            )


def read_manifest(cache_dir: Path) -> dict[str, Any] | None:
    path = cache_dir / CACHE_MANIFEST
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cache_is_current(cache_dir: Path, release_dir: Path) -> bool:
    """True if the cache was built by this schema version from this exact release.json."""
    manifest = read_manifest(cache_dir)
    if manifest is None:
        return False
    return (
        manifest.get("schema_version") == CACHE_SCHEMA_VERSION
        and manifest.get("release_json_sha256") == sha256_file(release_dir / RELEASE_JSON)
        and all((cache_dir / t["file"]).exists() for t in manifest.get("tables", {}).values())
    )


def build_cache(release_dir: Path, cache_dir: Path) -> dict[str, Any]:
    """Load the release, write one parquet per table, and write the cache manifest.

    Parquet files from an earlier build are deleted first.
    """
    info = ReleaseInfo.load(release_dir)
    verify_release_files(release_dir, info)
    tables = load_release_tables(release_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / CACHE_MANIFEST).unlink(missing_ok=True)
    for stale in cache_dir.glob("*.parquet"):
        stale.unlink()

    entries: dict[str, dict[str, Any]] = {}
    for name, df in sorted(tables.items()):
        file_name = f"{name}.parquet"
        df.write_parquet(cache_dir / file_name)
        entries[name] = {"file": file_name, "rows": df.height, "columns": df.columns}

    manifest = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_dir": str(release_dir.resolve()),
        "release_json_sha256": sha256_file(release_dir / RELEASE_JSON),
        "release": info.model_dump(mode="json"),
        "tables": entries,
    }
    (cache_dir / CACHE_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_cache(cache_dir: Path) -> dict[str, pl.LazyFrame]:
    """Scan every cached table listed in the manifest."""
    manifest = read_manifest(cache_dir)
    if manifest is None:
        raise FileNotFoundError(f"No {CACHE_MANIFEST} in '{cache_dir}'.")
    return {
        name: pl.scan_parquet(cache_dir / entry["file"])
        for name, entry in manifest["tables"].items()
    }
