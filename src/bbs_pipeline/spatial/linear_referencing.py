"""Spatial Join, Stop Linear Referencing, and STRtree Polygon Overlays."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, substring

from bbs_pipeline.contracts import (
    GeometrySource,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
)

TEN_STOP_BIN_COLS: tuple[str, ...] = ("Count10", "Count20", "Count30", "Count40", "Count50")
TEN_STOP_BIN_LABELS: dict[str, str] = {
    "Count10": "Stops 1-10",
    "Count20": "Stops 11-20",
    "Count30": "Stops 21-30",
    "Count40": "Stops 31-40",
    "Count50": "Stops 41-50",
}
TEN_STOP_BIN_INDICES: dict[str, int] = {
    "Count10": 0,
    "Count20": 1,
    "Count30": 2,
    "Count40": 3,
    "Count50": 4,
    "Stops 1-10": 0,
    "Stops 11-20": 1,
    "Stops 21-30": 2,
    "Stops 31-40": 3,
    "Stops 41-50": 4,
}
RAW_50_STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))


def _load_geodataframe(path: Path) -> gpd.GeoDataFrame:
    """Load a GeoDataFrame from GeoParquet or standard OGR vector formats."""
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".geoparquet"}:
        gdf = gpd.read_parquet(path)
    else:
        gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_string() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _ensure_linestring(geom: shapely.Geometry | None, lon: float = 0.0, lat: float = 0.0) -> LineString:
    """Ensure a geometry is a single LineString suitable for normalized linear referencing."""
    if geom is None or geom.is_empty:
        return LineString([(lon, lat), (lon + 0.36, lat + 0.36)])
    if isinstance(geom, LineString):
        return geom
    if isinstance(geom, MultiLineString):
        merged = linemerge(geom)
        if isinstance(merged, LineString):
            return merged
        coords: list[tuple[float, ...]] = []
        for part in geom.geoms:
            coords.extend(part.coords)
        if len(coords) >= 2:
            return LineString(coords)
    if isinstance(geom, Point):
        return LineString([(geom.x, geom.y), (geom.x + 0.36, geom.y + 0.36)])
    centroid = geom.centroid
    return LineString([(centroid.x, centroid.y), (centroid.x + 0.36, centroid.y + 0.36)])


def _extract_route_coordinates(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
) -> dict[str, tuple[float, float]]:
    """Extract (lon, lat) start coordinates per route_id from routes_df or observations_df."""
    coords_map: dict[str, tuple[float, float]] = {}

    sources: list[pl.DataFrame] = []
    if routes_df is not None:
        r_df = routes_df.collect() if isinstance(routes_df, pl.LazyFrame) else routes_df
        sources.append(r_df)
    sources.append(observations_df)

    for df in sources:
        if "route_id" not in df.columns:
            continue
        lat_col = next((c for c in ("Latitude", "latitude", "lat") if c in df.columns), None)
        lon_col = next((c for c in ("Longitude", "longitude", "lon") if c in df.columns), None)
        if lat_col and lon_col:
            dedup = (
                df.select(["route_id", lon_col, lat_col])
                .drop_nulls(subset=["route_id"])
                .unique(subset=["route_id"], keep="first")
            )
            for row in dedup.iter_rows(named=True):
                rid = str(row["route_id"])
                if rid not in coords_map:
                    lon_val = float(row[lon_col]) if row[lon_col] is not None else 0.0
                    lat_val = float(row[lat_col]) if row[lat_col] is not None else 0.0
                    coords_map[rid] = (lon_val, lat_val)
    return coords_map


def _resolve_route_geometries(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    spatial: SpatialExportConfigContract,
) -> dict[str, shapely.Geometry]:
    """Build a mapping from route_id to base geometry (LineString or Point) in EPSG:4326."""
    coords_map = _extract_route_coordinates(observations_df, routes_df)
    geom_map: dict[str, shapely.Geometry] = {}

    if (
        spatial.geometry_source == GeometrySource.ROUTE_POLYLINES
        and spatial.route_geometry_path.exists()
    ):
        route_gdf = _load_geodataframe(spatial.route_geometry_path)
        id_col = next(
            (c for c in ("route_id", "RouteID", "ROUTE_ID", "rteno") if c in route_gdf.columns),
            None,
        )
        if id_col is not None:
            for _idx, row in route_gdf.iterrows():
                rid = str(row[id_col])
                geom = row.geometry
                if geom is not None and not geom.is_empty:
                    if spatial.enable_stop_linear_referencing:
                        lon, lat = coords_map.get(rid, (0.0, 0.0))
                        geom_map[rid] = _ensure_linestring(geom, lon, lat)
                    else:
                        geom_map[rid] = geom

    unique_routes: list[str] = []
    if "route_id" in observations_df.columns:
        unique_routes = [
            str(r) for r in observations_df["route_id"].drop_nulls().unique().to_list()
        ]

    for rid in unique_routes:
        if rid not in geom_map:
            lon, lat = coords_map.get(rid, (0.0, 0.0))
            if spatial.enable_stop_linear_referencing:
                geom_map[rid] = LineString([(lon, lat), (lon + 0.36, lat + 0.36)])
            else:
                geom_map[rid] = Point(lon, lat)

    return geom_map


def _apply_stop_linear_referencing(
    df: pl.DataFrame,
    geom_map: dict[str, shapely.Geometry],
    stop_mode: StopAggregationMode,
) -> tuple[pl.DataFrame, list[shapely.Geometry]]:
    """Unpivot stop bins/stops if needed and compute linear-referenced sub-geometries."""
    if stop_mode == StopAggregationMode.TEN_STOP_BINS:
        present_bins = [c for c in TEN_STOP_BIN_COLS if c in df.columns]
        if len(present_bins) == 5:
            index_cols = [c for c in df.columns if c not in present_bins]
            unpivoted = df.unpivot(
                on=present_bins,
                index=index_cols,
                variable_name="_raw_bin",
                value_name="bin_abundance",
            ).with_columns(
                pl.col("_raw_bin").replace(TEN_STOP_BIN_LABELS).alias("stop_bin"),
                pl.col("_raw_bin")
                .replace_strict(TEN_STOP_BIN_INDICES, return_dtype=pl.Int64)
                .alias("bin_index"),
            ).drop("_raw_bin")
            df = unpivoted
        elif "stop_bin" in df.columns and "bin_index" not in df.columns:
            df = df.with_columns(
                pl.col("stop_bin")
                .replace_strict(TEN_STOP_BIN_INDICES, default=0, return_dtype=pl.Int64)
                .alias("bin_index")
            )

        if "bin_index" in df.columns:
            route_ids = df["route_id"].to_list() if "route_id" in df.columns else [""] * df.height
            bin_indices = df["bin_index"].to_list()
            geoms: list[shapely.Geometry] = []
            for rid, b_idx in zip(route_ids, bin_indices, strict=False):
                line = _ensure_linestring(geom_map.get(str(rid)))
                idx_clamped = max(0, min(4, int(b_idx if b_idx is not None else 0)))
                start_frac = idx_clamped * 0.2
                end_frac = (idx_clamped + 1) * 0.2
                geoms.append(substring(line, start_frac, end_frac, normalized=True))
            return df, geoms

    elif stop_mode == StopAggregationMode.RAW_50_STOPS:
        present_stops = [c for c in RAW_50_STOP_COLS if c in df.columns]
        if len(present_stops) == 50:
            index_cols = [c for c in df.columns if c not in present_stops]
            unpivoted = df.unpivot(
                on=present_stops,
                index=index_cols,
                variable_name="_raw_stop",
                value_name="stop_abundance",
            ).with_columns(
                pl.col("_raw_stop").str.strip_prefix("Stop").cast(pl.Int64).alias("stop_number")
            ).drop("_raw_stop")
            df = unpivoted

        if "stop_number" in df.columns:
            route_ids = df["route_id"].to_list() if "route_id" in df.columns else [""] * df.height
            stop_nums = df["stop_number"].to_list()
            geoms = []
            for rid, s_num in zip(route_ids, stop_nums, strict=False):
                line = _ensure_linestring(geom_map.get(str(rid)))
                if isinstance(s_num, str) and s_num.startswith("Stop"):
                    num_int = int(s_num.removeprefix("Stop"))
                else:
                    num_int = int(s_num if s_num is not None else 1)
                num_clamped = max(1, min(50, num_int))
                frac = (num_clamped - 1) / 49.0
                geoms.append(shapely.line_interpolate_point(line, frac, normalized=True))
            return df, geoms

    route_ids = df["route_id"].to_list() if "route_id" in df.columns else [""] * df.height
    geoms = [geom_map.get(str(rid), Point(0.0, 0.0)) for rid in route_ids]
    return df, geoms


def assign_custom_overlays(
    gdf: gpd.GeoDataFrame,
    custom_overlay_paths: dict[str, Path],
) -> gpd.GeoDataFrame:
    """Assign custom polygon overlay attributes using vectorized shapely.STRtree queries."""
    if not custom_overlay_paths or gdf.empty:
        return gdf

    result_gdf = gdf.copy()
    target_geoms = np.asarray(result_gdf.geometry.values)

    for layer_name, layer_path in custom_overlay_paths.items():
        path_obj = Path(layer_path)
        col_name = f"overlay_{layer_name}"
        if not path_obj.exists():
            result_gdf[col_name] = None
            continue

        overlay_gdf = _load_geodataframe(path_obj)
        if overlay_gdf.empty:
            result_gdf[col_name] = None
            continue

        non_geom_cols = [c for c in overlay_gdf.columns if c != overlay_gdf.geometry.name]
        preferred_cols = [
            c
            for c in ("name", "Name", "NAME", "id", "ID", "basin", "BASIN", "BCR", "bcr")
            if c in non_geom_cols
        ]
        attr_col = preferred_cols[0] if preferred_cols else (non_geom_cols[0] if non_geom_cols else None)

        if attr_col is not None:
            attr_values = overlay_gdf[attr_col].astype(str).to_numpy()
        else:
            attr_values = np.array([str(i) for i in range(len(overlay_gdf))], dtype=object)

        tree = shapely.STRtree(overlay_gdf.geometry.values)
        pairs = tree.query(target_geoms, predicate="intersects")

        assigned: list[str | None] = [None] * len(result_gdf)
        if pairs.shape[1] > 0:
            for target_idx, overlay_idx in zip(pairs[0], pairs[1], strict=False):
                t_i = int(target_idx)
                if assigned[t_i] is None:
                    assigned[t_i] = str(attr_values[int(overlay_idx)])

        result_gdf[col_name] = assigned

    return result_gdf


def build_spatial_geodataframe(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    protocol: ProtocolConfigContract,
    spatial: SpatialExportConfigContract,
) -> gpd.GeoDataFrame:
    """Attach route/stop geometries, perform linear referencing, and run STRtree overlays."""
    geom_map = _resolve_route_geometries(observations_df, routes_df, spatial)

    if spatial.enable_stop_linear_referencing:
        working_df, geometries = _apply_stop_linear_referencing(
            observations_df, geom_map, protocol.stop_aggregation
        )
    else:
        working_df = observations_df
        route_ids = (
            working_df["route_id"].to_list()
            if "route_id" in working_df.columns
            else [""] * working_df.height
        )
        geometries = [geom_map.get(str(rid), Point(0.0, 0.0)) for rid in route_ids]

    pdf = working_df.to_pandas()
    gdf = gpd.GeoDataFrame(pdf, geometry=geometries, crs="EPSG:4326")

    if spatial.custom_overlay_paths:
        gdf = assign_custom_overlays(gdf, spatial.custom_overlay_paths)

    return gdf
