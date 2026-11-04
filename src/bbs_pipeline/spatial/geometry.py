"""Route start-point geometry and STRtree polygon overlays."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from pyproj import Transformer
from shapely.geometry import Point

from bbs_pipeline.contracts import SpatialExportConfigContract
from bbs_pipeline.spatial.selection import ROUTE_START_CRS, load_boundary

# Geometries are assembled in WGS84, then reprojected per export CRS
WORKING_CRS = "EPSG:4326"

logger = logging.getLogger(__name__)


def _extract_route_coordinates(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
) -> dict[str, tuple[float, float]]:
    """Extract NAD83 (lon, lat) start coordinates per route_id from routes_df or observations_df.

    Routes with missing coordinates are left out, never placed at (0, 0).
    """
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
                .drop_nulls()
                .unique(subset=["route_id"], keep="first")
            )
            for row in dedup.iter_rows(named=True):
                rid = str(row["route_id"])
                if rid not in coords_map:
                    coords_map[rid] = (float(row[lon_col]), float(row[lat_col]))
    return coords_map


def route_start_points(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
) -> dict[str, Point]:
    """Map each route_id in observations_df to its start point in EPSG:4326.

    Routes without start coordinates are absent from the mapping and logged, so they end
    up with NULL geometry.
    """
    nad83_coords = _extract_route_coordinates(observations_df, routes_df)
    to_working = Transformer.from_crs(ROUTE_START_CRS, WORKING_CRS, always_xy=True)

    unique_routes: list[str] = []
    if "route_id" in observations_df.columns:
        unique_routes = [
            str(r) for r in observations_df["route_id"].drop_nulls().unique().to_list()
        ]

    points: dict[str, Point] = {}
    missing: list[str] = []
    for rid in unique_routes:
        if rid not in nad83_coords:
            missing.append(rid)
            continue
        points[rid] = Point(*to_working.transform(*nad83_coords[rid]))

    if missing:
        logger.warning(
            "%d route(s) have no start coordinates and get NULL geometry: %s",
            len(missing),
            sorted(missing),
        )
    return points


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
            raise FileNotFoundError(f"Overlay '{layer_name}' file not found: '{path_obj}'.")

        overlay_gdf = load_boundary(path_obj).to_crs(result_gdf.crs)

        non_geom_cols = [c for c in overlay_gdf.columns if c != overlay_gdf.geometry.name]
        preferred_cols = [
            c
            for c in ("name", "Name", "NAME", "id", "ID", "basin", "BASIN", "BCR", "bcr")
            if c in non_geom_cols
        ]
        attr_col = (
            preferred_cols[0] if preferred_cols else (non_geom_cols[0] if non_geom_cols else None)
        )

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
    spatial: SpatialExportConfigContract,
) -> gpd.GeoDataFrame:
    """Attach route start-point geometry (EPSG:4326) and run STRtree overlays."""
    points = route_start_points(observations_df, routes_df)
    route_ids = (
        observations_df["route_id"].to_list()
        if "route_id" in observations_df.columns
        else [""] * observations_df.height
    )
    geometries = [points.get(str(rid)) for rid in route_ids]

    gdf = gpd.GeoDataFrame(observations_df.to_pandas(), geometry=geometries, crs=WORKING_CRS)

    if spatial.custom_overlay_paths:
        gdf = assign_custom_overlays(gdf, spatial.custom_overlay_paths)

    return gdf
