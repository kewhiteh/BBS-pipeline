"""BBS-Pipeline v3: Contract-First Polars/Shapely Spatial & Ecological ETL Engine."""

from bbs_pipeline.contracts import (
    Covid2020Mode,
    ExportFormat,
    FunnelAuditMetrics,
    GeometrySource,
    GuildProfileContract,
    GuildRegistryContract,
    IngressMode,
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
    "Covid2020Mode",
    "ExportFormat",
    "FunnelAuditMetrics",
    "GeometrySource",
    "GuildProfileContract",
    "GuildRegistryContract",
    "IngressMode",
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
