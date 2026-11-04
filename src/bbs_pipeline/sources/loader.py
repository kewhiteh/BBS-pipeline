"""Decide where the pipeline's tables come from: the cache, the release files, or a fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from bbs_pipeline.contracts import ProtocolConfigContract
from bbs_pipeline.sources.cache import build_cache, cache_is_current, load_cache, read_manifest
from bbs_pipeline.sources.release import RELEASE_JSON, ReleaseInfo, fetch_release


class SourceResult(BaseModel):
    """Tables for one pipeline run, and where they came from."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str  # "cache" | "built_from_release" | "fetched"
    cache_dir: Path
    manifest: dict[str, Any] = Field(default_factory=dict)
    tables: dict[str, pl.LazyFrame] = Field(default_factory=dict)


def load_sources(
    protocol: ProtocolConfigContract,
    http_client: httpx.Client | None = None,
) -> SourceResult:
    """Return the release tables, using the network only when nothing local exists.

    1. ``raw_data_dir`` has no ``release.json``: fetch the configured release (an error
       when ``offline``), then build the cache.
    2. ``release.json`` names a different release than configured: raise.
    3. The cache was built from this ``release.json`` (and no rebuild was forced): load it.
    4. Otherwise rebuild the cache from the release files.
    """
    raw_dir = Path(protocol.raw_data_dir)
    cache_dir = Path(protocol.sanitized_cache_dir)
    source = "built_from_release"

    if not (raw_dir / RELEASE_JSON).exists():
        if protocol.offline:
            raise FileNotFoundError(
                f"No {RELEASE_JSON} in '{raw_dir}' and offline mode is on; run "
                f"`bbs fetch --release {protocol.sciencebase_doi} --dest {raw_dir}` first."
            )
        fetch_release(
            protocol.sciencebase_doi,
            raw_dir,
            client=http_client,
            timeout=protocol.http_timeout_seconds,
            retries=protocol.http_max_retries,
            backoff=protocol.http_backoff_seconds,
        )
        source = "fetched"

    info = ReleaseInfo.load(raw_dir)
    if not info.matches(protocol.sciencebase_doi):
        raise ValueError(
            f"'{raw_dir}' holds {info.title} (DOI {info.doi}, item {info.item_id}), but the "
            f"configuration asks for '{protocol.sciencebase_doi}'. Point raw_data_dir at a "
            "directory for that release, or change sciencebase_doi."
        )

    if (
        source != "fetched"
        and not protocol.force_resanitize
        and cache_is_current(cache_dir, raw_dir)
    ):
        return SourceResult(
            source="cache",
            cache_dir=cache_dir,
            manifest=read_manifest(cache_dir) or {},
            tables=load_cache(cache_dir),
        )

    manifest = build_cache(raw_dir, cache_dir)
    return SourceResult(
        source=source, cache_dir=cache_dir, manifest=manifest, tables=load_cache(cache_dir)
    )
