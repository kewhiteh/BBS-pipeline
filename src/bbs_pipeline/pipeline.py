"""End-to-end BBS pipeline: sources -> engine -> one output file (+ manifest)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bbs_pipeline
from bbs_pipeline.config import RunProfile
from bbs_pipeline.contracts import PipelineConfigBundle, PipelineExecutionResult
from bbs_pipeline.engine.ecology import normalize_canonical_keys, run_ecological_engine
from bbs_pipeline.sources.loader import SourceResult, load_sources
from bbs_pipeline.spatial.exporter import write_output


def _git_sha() -> str | None:
    """Commit of the installed package source, or None when it is not a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(bbs_pipeline.__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def _write_manifest(
    output_path: Path,
    result: PipelineExecutionResult,
    sources: SourceResult,
    profile: RunProfile | None,
) -> Path:
    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": {
            "path": str(output_path),
            "rows": result.funnel_metrics.final_output_rows,
            "columns": result.output_columns,
        },
        "package": {
            "name": "bbs-pipeline",
            "version": bbs_pipeline.__version__,
            "git_sha": _git_sha(),
        },
        "release": sources.manifest.get("release"),
        "cache": {"dir": str(sources.cache_dir), "built_at": sources.manifest.get("built_at")},
        "stage_counts": result.funnel_metrics.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json") if profile is not None else None,
    }
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def run_bbs_pipeline(
    bundle: PipelineConfigBundle,
    *,
    export: bool = True,
    profile: RunProfile | None = None,
) -> PipelineExecutionResult:
    """Load sources, run the engine, and (unless ``export`` is False) write the output.

    With ``export=False`` this is an inspection: stage counts and the exact output
    columns, and no files written.
    """
    sources = load_sources(bundle.protocol)

    engine_result = run_ecological_engine(
        tables=sources.tables,
        species=bundle.species,
        protocol=bundle.protocol,
        data_source=sources.source,
    )
    observations = engine_result.observations_df
    columns = observations.columns
    if bundle.output.is_vector:
        columns = [*columns, *(f"overlay_{name}" for name in bundle.output.overlays), "geometry"]

    result = PipelineExecutionResult(
        funnel_metrics=engine_result.funnel_metrics,
        output_columns=columns,
        active_joins=engine_result.active_joins,
        preview_records=observations.head(50).to_dicts(),
    )
    if not export:
        return result

    routes_lf = (
        normalize_canonical_keys(sources.tables["routes"]) if "routes" in sources.tables else None
    )
    output_path = write_output(observations, routes_lf, bundle.output)
    exported = [output_path]
    if bundle.output.manifest:
        exported.append(_write_manifest(output_path, result, sources, profile))
    return result.model_copy(update={"exported_files": exported})


def run_profile(profile: RunProfile, *, export: bool = True) -> PipelineExecutionResult:
    """Run (or inspect) a validated run profile."""
    return run_bbs_pipeline(profile.to_bundle(), export=export, profile=profile)
