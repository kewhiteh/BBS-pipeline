"""End-to-End BBS-Pipeline v3 Orchestrator."""

from __future__ import annotations

from pathlib import Path

from bbs_pipeline.contracts import PipelineConfigBundle, PipelineExecutionResult
from bbs_pipeline.engine.ecology import normalize_canonical_keys, run_ecological_engine
from bbs_pipeline.sources.loader import load_sources
from bbs_pipeline.spatial.exporter import run_spatial_export


def run_bbs_pipeline(bundle: PipelineConfigBundle) -> PipelineExecutionResult:
    """Execute the full BBS-Pipeline v3 workflow from an immutable PipelineConfigBundle."""
    sources = load_sources(bundle.protocol)

    engine_result = run_ecological_engine(
        tables=sources.tables,
        guilds=bundle.guilds,
        protocol=bundle.protocol,
        ingress_tier_used=sources.source,
    )

    routes_lf = (
        normalize_canonical_keys(sources.tables["routes"]) if "routes" in sources.tables else None
    )

    exported_files = run_spatial_export(
        observations_df=engine_result.observations_df,
        routes_df=routes_lf,
        spatial=bundle.spatial,
    )

    return PipelineExecutionResult(
        funnel_metrics=engine_result.funnel_metrics,
        exported_files=exported_files,
        active_joins=engine_result.active_joins,
        preview_records=engine_result.observations_df.head(50).to_dicts(),
    )


def run_bbs_pipeline_from_dir(
    config_dir: Path | str = Path("config"),
) -> PipelineExecutionResult:
    """Load the three JSON contracts from config_dir and execute the full BBS pipeline."""
    bundle = PipelineConfigBundle.load_from_dir(config_dir)
    return run_bbs_pipeline(bundle)
