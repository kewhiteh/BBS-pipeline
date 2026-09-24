"""Two-tier spatial filtering subsystem for BBS Pipeline v2.0."""

from __future__ import annotations

import logging

import numpy as np
import polars as pl
import shapely
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)


def build_route_spatial_index(
    routes_with_stops: pl.DataFrame,
) -> tuple[shapely.STRtree, np.ndarray]:
    """Builds a Shapely STRtree over route bounding boxes for Tier 1 pruning.

    Parameters
    ----------
    routes_with_stops : pl.DataFrame
        DataFrame containing at least 'RouteKey', 'StopLatitude', and 'StopLongitude'.

    Returns
    -------
    tuple[shapely.STRtree, np.ndarray]
        The STRtree index and an array of RouteKeys corresponding to the tree geometries.
    """
    bboxes = (
        routes_with_stops.group_by("RouteKey")
        .agg(
            pl.col("StopLongitude").min().alias("min_x"),
            pl.col("StopLatitude").min().alias("min_y"),
            pl.col("StopLongitude").max().alias("max_x"),
            pl.col("StopLatitude").max().alias("max_y"),
        )
        .sort("RouteKey")
    )

    route_keys = bboxes["RouteKey"].to_numpy()
    boxes = shapely.box(
        bboxes["min_x"].to_numpy(),
        bboxes["min_y"].to_numpy(),
        bboxes["max_x"].to_numpy(),
        bboxes["max_y"].to_numpy(),
    )
    tree = shapely.STRtree(boxes)
    return tree, route_keys


def filter_stops_by_boundary(
    stops_lf: pl.LazyFrame, polygon: BaseGeometry, mode: str
) -> pl.LazyFrame:
    """Filter stops by spatial boundary using Tier 1 and Tier 2 filtering.

    Parameters
    ----------
    stops_lf : pl.LazyFrame
        LazyFrame of stops, including 'RouteKey', 'StopLatitude', 'StopLongitude', and 'Count'.
    polygon : BaseGeometry
        The query polygon geometry.
    mode : str
        Spatial filter mode: 'STRICT_CONTAINMENT', 'ANY_STOP_INTERSECT', or 'SPATIAL_STOP_MASK'.

    Returns
    -------
    pl.LazyFrame
        The filtered stops as a LazyFrame.

    Raises
    ------
    ValueError
        If the polygon is invalid/unclosed, or mode is unrecognized.
    """
    if (
        not isinstance(polygon, BaseGeometry)
        or not polygon.is_valid
        or polygon.is_empty
        or polygon.geom_type not in ("Polygon", "MultiPolygon")
    ):
        raise ValueError("Invalid or unclosed polygon geometry provided.")

    valid_modes = {"STRICT_CONTAINMENT", "ANY_STOP_INTERSECT", "SPATIAL_STOP_MASK"}
    if mode not in valid_modes:
        raise ValueError(f"Unrecognized spatial_filter_mode: {mode}")

    # Eagerly compute for spatial operations
    stops_df = stops_lf.collect()

    if stops_df.is_empty():
        return stops_df.lazy()

    # Tier 1: Route Envelope Coarse Filter
    tree, route_keys = build_route_spatial_index(stops_df)

    # Query STRtree to find candidate RouteKeys intersecting the polygon's bounding box
    candidate_indices = tree.query(polygon)
    candidate_route_keys = set(route_keys[candidate_indices])

    if not candidate_route_keys:
        return stops_df.filter(pl.lit(False)).lazy()

    # Filter to candidates
    stops_df = stops_df.filter(pl.col("RouteKey").is_in(list(candidate_route_keys)))

    # Tier 2: Stop-Level Fine Filter (Vectorized point-in-polygon containment)
    lon = stops_df["StopLongitude"].to_numpy()
    lat = stops_df["StopLatitude"].to_numpy()
    valid_mask = ~(np.isnan(lon) | np.isnan(lat))

    points = shapely.points(lon, lat)

    intersects = shapely.intersects(polygon, points)

    stops_df = stops_df.with_columns(
        pl.Series("intersects", intersects), pl.Series("valid_coord", valid_mask)
    )

    # Route-level logic
    route_stats = stops_df.group_by("RouteKey").agg(
        # all_intersect: all stops with valid coords intersect, and at least one intersects
        (
            (pl.col("intersects") | ~pl.col("valid_coord")).all()
            & pl.col("intersects").any()
        ).alias("all_intersect"),
        pl.col("intersects").any().alias("any_intersect"),
    )

    stops_df = stops_df.join(route_stats, on="RouteKey")

    if mode == "STRICT_CONTAINMENT":
        filtered = stops_df.filter(pl.col("all_intersect"))
    elif mode == "ANY_STOP_INTERSECT":
        filtered = stops_df.filter(pl.col("any_intersect"))
    elif mode == "SPATIAL_STOP_MASK":
        filtered = stops_df.filter(pl.col("any_intersect"))
        filtered = filtered.with_columns(
            pl.when(pl.col("intersects"))
            .then(pl.col("Count"))
            .otherwise(None)
            .alias("Count")
        )

    # Clean up intermediate columns
    filtered = filtered.drop(
        ["intersects", "valid_coord", "all_intersect", "any_intersect"]
    )

    return filtered.lazy()
