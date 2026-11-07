"""BBS-Pipeline v3: config-driven extraction of North American Breeding Bird Survey data."""

from bbs_pipeline.config import ProfileError, RunProfile, load_profile
from bbs_pipeline.contracts import (
    ExportFormat,
    FunnelAuditMetrics,
    OutputContract,
    PipelineConfigBundle,
    PipelineExecutionResult,
    ProtocolConfigContract,
    SpeciesSelectionContract,
    StopAggregationMode,
    TemporalRoutingMode,
)

__all__ = [
    "ExportFormat",
    "FunnelAuditMetrics",
    "OutputContract",
    "PipelineConfigBundle",
    "PipelineExecutionResult",
    "ProfileError",
    "ProtocolConfigContract",
    "RunProfile",
    "SpeciesSelectionContract",
    "StopAggregationMode",
    "TemporalRoutingMode",
    "load_profile",
]

__version__ = "3.0.0"
