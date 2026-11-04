"""Signature-Column TableRoleResolver and Canonical Key Normalizer.

Deterministically binds logical BBS table roles (``fifty_stop``, ``ten_stop``,
``weather``, ``routes``, ``species``) by inspecting ``intake_manifest.json``
metadata and/or Parquet/CSV column schemas rather than relying on fragile
file naming conventions.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import polars as pl

LOGICAL_ROLES: tuple[str, ...] = (
    "fifty_stop",
    "ten_stop",
    "weather",
    "routes",
    "species",
)


def identify_role_from_columns(columns: Iterable[str]) -> str | None:
    """Identify the logical BBS table role from a collection of column names."""
    col_set = set(columns)

    # 1. fifty_stop: contains AOU, Stop1, and Stop50
    if "AOU" in col_set and "Stop1" in col_set and "Stop50" in col_set:
        return "fifty_stop"

    # 2. ten_stop: contains AOU and (StopTotal or Count10..Count50) and NOT Stop50
    ten_stop_indicators = {"StopTotal", "Count10", "Count20", "Count30", "Count40", "Count50"}
    if "AOU" in col_set and "Stop50" not in col_set and (col_set & ten_stop_indicators):
        return "ten_stop"

    # 3. weather: contains RouteDataID, ObsN, RunType, QualityCurrentID
    if {"RouteDataID", "ObsN", "RunType", "QualityCurrentID"}.issubset(col_set):
        return "weather"

    # 4. routes: contains StateNum, Route, BCR, Latitude, Longitude
    if {"StateNum", "Route", "BCR", "Latitude", "Longitude"}.issubset(col_set):
        return "routes"

    # 5. species: contains AOU, (English_Common_Name or EnglishName), Order, Family
    if (
        "AOU" in col_set
        and {"Order", "Family"}.issubset(col_set)
        and ("English_Common_Name" in col_set or "EnglishName" in col_set)
    ):
        return "species"

    return None


def normalize_lazy_table(lf: pl.LazyFrame, role: str | None = None) -> pl.LazyFrame:
    """Apply canonical route_id ('SS_RRR'), 5-digit AOU, and species name normalization."""
    schema_names = set(lf.collect_schema().names())
    exprs: list[pl.Expr] = []

    if "StateNum" in schema_names and "Route" in schema_names:
        route_id_expr = (
            pl.col("StateNum").cast(pl.Int64).cast(pl.String).str.zfill(2)
            + pl.lit("_")
            + pl.col("Route").cast(pl.Int64).cast(pl.String).str.zfill(3)
        ).alias("route_id")
        exprs.append(route_id_expr)

    if "AOU" in schema_names:
        aou_expr = pl.col("AOU").cast(pl.String).str.strip_chars().str.zfill(5).alias("AOU")
        exprs.append(aou_expr)

    if (
        (role == "species" or "Order" in schema_names)
        and "EnglishName" in schema_names
        and "English_Common_Name" not in schema_names
    ):
        exprs.append(pl.col("EnglishName").cast(pl.String).alias("English_Common_Name"))

    if exprs:
        lf = lf.with_columns(exprs)
    return lf


class TableRoleResolver:
    """Resolves logical BBS table roles from a sanitized cache directory."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    @staticmethod
    def _read_file_columns(file_path: Path) -> list[str]:
        suffix = file_path.suffix.lower()
        if suffix == ".parquet":
            schema = pl.read_parquet_schema(file_path)
            return list(schema.keys())
        if suffix == ".csv":
            lf = pl.scan_csv(file_path, infer_schema_length=100)
            return list(lf.collect_schema().names())
        return []

    def resolve(self, cache_dir: Path | str | None = None) -> dict[str, Path]:
        """Return a mapping of logical role name to physical file Path in ``cache_dir``."""
        target_dir = Path(cache_dir) if cache_dir is not None else self.cache_dir
        if target_dir is None:
            raise ValueError("cache_dir must be provided to TableRoleResolver.resolve()")
        if not target_dir.exists():
            return {}

        resolved: dict[str, Path] = {}

        # Step 1: Inspect intake_manifest.json if present
        manifest_path = target_dir / "intake_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed cache manifest '{manifest_path}': {exc}") from exc
            tables_meta = manifest.get("tables", [])
            if isinstance(tables_meta, list):
                for entry in tables_meta:
                    if not isinstance(entry, dict):
                        continue
                    fname = entry.get("filename") or entry.get("path")
                    cols = entry.get("columns")
                    if not fname:
                        continue
                    candidate_path = target_dir / str(fname)
                    if not candidate_path.is_file():
                        continue
                    if isinstance(cols, list) and cols:
                        role = identify_role_from_columns(cols)
                    else:
                        role = identify_role_from_columns(self._read_file_columns(candidate_path))
                    if role and role not in resolved:
                        resolved[role] = candidate_path

        # Step 2: Inspect all .parquet files (preferred) and .csv files (fallback)
        candidates = sorted(target_dir.glob("*.parquet")) + sorted(target_dir.glob("*.csv"))
        for file_path in candidates:
            if len(resolved) == len(LOGICAL_ROLES):
                break
            if file_path in resolved.values():
                continue
            try:
                cols = self._read_file_columns(file_path)
            except pl.exceptions.PolarsError as exc:
                raise ValueError(f"Cannot read the columns of '{file_path}': {exc}") from exc
            role = identify_role_from_columns(cols)
            if role and role not in resolved:
                resolved[role] = file_path

        return resolved

    def load_tables(self, cache_dir: Path | str | None = None) -> dict[str, pl.LazyFrame]:
        """Resolve table roles and return normalized Polars LazyFrames."""
        resolved_paths = self.resolve(cache_dir)
        tables: dict[str, pl.LazyFrame] = {}
        for role, path in resolved_paths.items():
            if path.suffix.lower() == ".parquet":
                lf = pl.scan_parquet(path)
            else:
                lf = pl.scan_csv(path, infer_schema_length=10000)
            tables[role] = normalize_lazy_table(lf, role=role)
        return tables


def resolve_table_roles(cache_dir: Path | str) -> dict[str, Path]:
    """Resolve logical BBS table roles to physical file paths in ``cache_dir``."""
    return TableRoleResolver(cache_dir).resolve()


def load_resolved_tables(cache_dir: Path | str) -> dict[str, pl.LazyFrame]:
    """Resolve logical BBS table roles and load normalized Polars LazyFrames."""
    return TableRoleResolver(cache_dir).load_tables()
