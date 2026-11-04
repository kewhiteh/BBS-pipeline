"""BBS-Pipeline v3: Contract-First Polars/Shapely Spatial & Ecological ETL Engine."""

from bbs_pipeline.contracts import (
    ExportFormat,
    FunnelAuditMetrics,
    GuildProfileContract,
    GuildRegistryContract,
    JoinDimension,
    ObserverTenureMode,
    PipelineConfigBundle,
    PipelineExecutionResult,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)

__all__ = [
    "ExportFormat",
    "FunnelAuditMetrics",
    "GuildProfileContract",
    "GuildRegistryContract",
    "JoinDimension",
    "ObserverTenureMode",
    "PipelineConfigBundle",
    "PipelineExecutionResult",
    "ProtocolConfigContract",
    "SpatialExportConfigContract",
    "StopAggregationMode",
    "TemporalRoutingMode",
]

__version__ = "3.0.0"
