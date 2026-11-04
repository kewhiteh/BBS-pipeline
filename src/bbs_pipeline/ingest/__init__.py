"""BBS-Pipeline v3 Ingest Package: Three-Tier Gate, Role Resolver, & Species Normalizer."""

from __future__ import annotations

from bbs_pipeline.ingest.gate import IngressResult, ThreeTierIngressGate
from bbs_pipeline.ingest.role_resolver import (
    LOGICAL_ROLES,
    TableRoleResolver,
    identify_role_from_columns,
    load_resolved_tables,
    normalize_lazy_table,
    resolve_table_roles,
)
from bbs_pipeline.ingest.species_normalizer import (
    SPECIES_COLUMNS,
    normalize_species_list_file,
    parse_species_list_lines,
)

__all__ = [
    "IngressResult",
    "LOGICAL_ROLES",
    "SPECIES_COLUMNS",
    "TableRoleResolver",
    "ThreeTierIngressGate",
    "identify_role_from_columns",
    "load_resolved_tables",
    "normalize_lazy_table",
    "normalize_species_list_file",
    "parse_species_list_lines",
    "resolve_table_roles",
]
