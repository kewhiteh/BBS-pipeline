"""Route start-point geometry and STRtree polygon overlays."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from pyproj import Transformer
from shapely.geometry import Point

from bbs_pipeline.contracts import OverlaySpec
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


AUTOMATIC_OVERLAY_COLUMNS: tuple[str, ...] = (
    "name",
    "Name",
    "NAME",
    "id",
    "ID",
    "basin",
    "BASIN",
    "BCR",
    "bcr",
)


def automatic_overlay_column(columns: list[str]) -> str | None:
    """The column an overlay uses when none is chosen: a name/id/basin-like one, else the first."""
    preferred = [c for c in AUTOMATIC_OVERLAY_COLUMNS if c in columns]
    return preferred[0] if preferred else (columns[0] if columns else None)


def assign_custom_overlays(
    gdf: gpd.GeoDataFrame,
    overlays: Mapping[str, OverlaySpec | Path | str],
) -> gpd.GeoDataFrame:
    """Add ``overlay_<name>``: the label of the polygon each route's start point falls in."""
    if not overlays or gdf.empty:
        return gdf

    result_gdf = gdf.copy()
    target_geoms = np.asarray(result_gdf.geometry.values)

    for layer_name, spec in overlays.items():
        if not isinstance(spec, OverlaySpec):
            spec = OverlaySpec(file=Path(spec))
        path_obj = Path(spec.file)
        col_name = f"overlay_{layer_name}"
        if not path_obj.exists():
            raise FileNotFoundError(f"Overlay '{layer_name}' file not found: '{path_obj}'.")

        overlay_gdf = load_boundary(path_obj).to_crs(result_gdf.crs)

        non_geom_cols = [c for c in overlay_gdf.columns if c != overlay_gdf.geometry.name]
        if spec.column is not None and spec.column not in non_geom_cols:
            raise ValueError(
                f"Overlay '{layer_name}': '{path_obj.name}' has no column '{spec.column}'. "
                f"Columns: {', '.join(non_geom_cols)}"
            )
        attr_col = spec.column or automatic_overlay_column(non_geom_cols)

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


def route_geodataframe(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    overlays: Mapping[str, OverlaySpec | Path | str] | None = None,
    crs: str = WORKING_CRS,
) -> gpd.GeoDataFrame:
    """One start point per route in ``observations_df``, with overlay columns, in ``crs``."""
    points = route_start_points(observations_df, routes_df)
    route_ids = observations_df["route_id"].unique(maintain_order=True).to_list()
    routes = gpd.GeoDataFrame(
        {"route_id": route_ids}, geometry=[points.get(str(r)) for r in route_ids], crs=WORKING_CRS
    )
    if overlays:
        routes = assign_custom_overlays(routes, overlays)
    if routes.crs.to_string().upper() != crs.upper():
        routes = routes.to_crs(crs)
    return routes


def build_spatial_geodataframe(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    overlays: Mapping[str, OverlaySpec | Path | str] | None = None,
    crs: str = WORKING_CRS,
) -> gpd.GeoDataFrame:
    """Attach each route's start point (and overlay columns) to every observation row.

    Geometry, overlays, and reprojection are computed once per route and joined on
    route_id, since every row of a route shares its start point.
    """
    routes = route_geodataframe(observations_df, routes_df, overlays, crs)
    geom_col = routes.geometry.name
    merged = observations_df.to_pandas().merge(routes, on="route_id", how="left", validate="m:1")
    gdf = gpd.GeoDataFrame(merged, geometry=geom_col, crs=routes.crs)
    # Geometry last, as GeoPackage/GeoJSON readers return it, so every format agrees
    return gdf[[c for c in gdf.columns if c != geom_col] + [geom_col]]
