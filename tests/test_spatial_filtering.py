import polars as pl
import pytest
import shapely
from shapely.geometry import Polygon

from bbs_pipeline.spatial.filtering import (
    build_route_spatial_index,
    filter_stops_by_boundary,
)


@pytest.fixture
def sample_stops_lf():
    """Returns a LazyFrame of stops with coordinates and counts."""
    # Route 1: All stops inside
    # Route 2: Some stops inside, some outside
    # Route 3: All stops outside

    data = []

    # Route 1: points around (0, 0)
    for i in range(1, 51):
        data.append(
            {
                "RouteKey": "R1",
                "StopNumber": i,
                "StopLatitude": 0.0 + i * 0.001,
                "StopLongitude": 0.0 + i * 0.001,
                "Count": 10,
            }
        )

    # Route 2: points from (0.5, 0.5) to (1.5, 1.5)
    for i in range(1, 51):
        data.append(
            {
                "RouteKey": "R2",
                "StopNumber": i,
                "StopLatitude": 0.5 + i * 0.02,
                "StopLongitude": 0.5 + i * 0.02,
                "Count": 10,
            }
        )

    # Route 3: points around (10, 10)
    for i in range(1, 51):
        data.append(
            {
                "RouteKey": "R3",
                "StopNumber": i,
                "StopLatitude": 10.0 + i * 0.001,
                "StopLongitude": 10.0 + i * 0.001,
                "Count": 10,
            }
        )

    return pl.DataFrame(data).lazy()


def test_build_route_spatial_index(sample_stops_lf):
    """Test Tier 1 bounding box pruning via shapely.STRtree."""
    stops_df = sample_stops_lf.collect()
    tree, route_keys = build_route_spatial_index(stops_df)

    assert isinstance(tree, shapely.STRtree)
    assert len(route_keys) == 3
    assert set(route_keys) == {"R1", "R2", "R3"}

    # Query with a box that covers R1 and R2 but not R3
    poly = Polygon([(-1, -1), (2, -1), (2, 2), (-1, 2), (-1, -1)])
    candidate_indices = tree.query(poly)
    candidates = set(route_keys[candidate_indices])

    assert "R1" in candidates
    assert "R2" in candidates
    assert "R3" not in candidates


def test_filter_strict_containment(sample_stops_lf):
    """Test STRICT_CONTAINMENT mode."""
    # Polygon covers R1 completely, but only part of R2
    poly = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)])

    result_lf = filter_stops_by_boundary(sample_stops_lf, poly, "STRICT_CONTAINMENT")
    result_df = result_lf.collect()

    routes = result_df["RouteKey"].unique().to_list()
    assert "R1" in routes
    assert "R2" not in routes
    assert "R3" not in routes


def test_filter_any_stop_intersect(sample_stops_lf):
    """Test ANY_STOP_INTERSECT mode."""
    # Polygon covers R1 completely, but only part of R2
    poly = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)])

    result_lf = filter_stops_by_boundary(sample_stops_lf, poly, "ANY_STOP_INTERSECT")
    result_df = result_lf.collect()

    routes = result_df["RouteKey"].unique().to_list()
    assert "R1" in routes
    assert "R2" in routes
    assert "R3" not in routes

    # R2 should have all its original stops because entire route is kept
    r2_stops = result_df.filter(pl.col("RouteKey") == "R2")
    assert len(r2_stops) == 50


def test_filter_spatial_stop_mask(sample_stops_lf):
    """Test SPATIAL_STOP_MASK mode and recalculation logic."""
    poly = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)])

    result_lf = filter_stops_by_boundary(sample_stops_lf, poly, "SPATIAL_STOP_MASK")
    result_df = result_lf.collect()

    routes = result_df["RouteKey"].unique().to_list()
    assert "R1" in routes
    assert "R2" in routes
    assert "R3" not in routes

    # R2 should have some null counts
    r2_stops = result_df.filter(pl.col("RouteKey") == "R2")
    null_counts = r2_stops["Count"].null_count()
    assert null_counts > 0
    assert null_counts < 50

    # R1 should have no null counts
    r1_stops = result_df.filter(pl.col("RouteKey") == "R1")
    assert r1_stops["Count"].null_count() == 0


def test_negative_invalid_polygon(sample_stops_lf):
    """Negative Assertion 1: Raise ValueError on invalid/unclosed polygon geometries."""
    # A line string instead of a polygon
    invalid_poly = shapely.LineString([(0, 0), (1, 1)])

    with pytest.raises(
        ValueError, match="Invalid or unclosed polygon geometry provided."
    ):
        filter_stops_by_boundary(sample_stops_lf, invalid_poly, "STRICT_CONTAINMENT")

    # An empty polygon
    empty_poly = Polygon()
    with pytest.raises(
        ValueError, match="Invalid or unclosed polygon geometry provided."
    ):
        filter_stops_by_boundary(sample_stops_lf, empty_poly, "STRICT_CONTAINMENT")


def test_negative_invalid_mode(sample_stops_lf):
    """Negative Assertion 2: Raise ValueError on invalid spatial filter mode string."""
    poly = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)])

    with pytest.raises(
        ValueError, match="Unrecognized spatial_filter_mode: INVALID_MODE"
    ):
        filter_stops_by_boundary(sample_stops_lf, poly, "INVALID_MODE")
