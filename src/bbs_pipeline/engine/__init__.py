"""Polars Ecological Engine package for BBS-Pipeline v3."""

from bbs_pipeline.engine.ecology import (
    EcologicalEngineResult,
    compute_observer_tenure,
    filter_target_species,
    harmonize_observations,
    historical_species_pool,
    normalize_canonical_keys,
    run_ecological_engine,
)

__all__ = [
    "EcologicalEngineResult",
    "compute_observer_tenure",
    "filter_target_species",
    "harmonize_observations",
    "historical_species_pool",
    "normalize_canonical_keys",
    "run_ecological_engine",
]
