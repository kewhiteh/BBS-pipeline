"""Route-level spatial selection by start point."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import polars as pl

# routes.csv start coordinates are NAD83
ROUTE_START_CRS = "EPSG:4269"

logger = logging.getLogger(__name__)


def load_boundary(path: Path) -> gpd.GeoDataFrame:
    """Load a polygon boundary file; it must exist, be non-empty, and declare a CRS."""
    if not path.exists():
        raise FileNotFoundError(f"Boundary file not found: '{path}'.")
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        gdf = gpd.read_parquet(path)
    else:
        gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"Boundary file '{path}' has no features.")
    if gdf.crs is None:
        raise ValueError(f"Boundary file '{path}' has no CRS; set one on the file.")
    return gdf


def routes_within_boundary(routes: pl.DataFrame, boundary_path: Path) -> list[str]:
    """Return route_ids whose NAD83 start point lies inside or on the boundary.

    Routes with missing coordinates cannot be placed, so they are excluded and logged.
    """
    boundary = load_boundary(boundary_path).to_crs(ROUTE_START_CRS)
    area = boundary.geometry.union_all()

    located = routes.select(["route_id", "Longitude", "Latitude"]).unique(subset=["route_id"])
    unplaced = located.filter(pl.col("Longitude").is_null() | pl.col("Latitude").is_null())
    if unplaced.height:
        logger.warning(
            "%d route(s) have no start coordinates and are excluded by the boundary filter: %s",
            unplaced.height,
            sorted(unplaced["route_id"].to_list()),
        )
    located = located.drop_nulls()

    points = gpd.points_from_xy(
        located["Longitude"].cast(pl.Float64).to_numpy(),
        located["Latitude"].cast(pl.Float64).to_numpy(),
        crs=ROUTE_START_CRS,
    )
    inside = points.intersects(area)
    return sorted(located.filter(pl.Series(inside))["route_id"].to_list())
