"""Tests for Phase 2: High-Precision Linear Referencing Engine."""

from __future__ import annotations

import pytest
from shapely.geometry import LineString

from bbs_pipeline.processing.linear_referencing import interpolate_stops_along_route


@pytest.fixture
def valid_route_line() -> LineString:
    """A simple Eastward 25-mile line in EPSG:4326.

    1 degree of longitude at equator is ~69 miles.
    At equator, 25 miles is about 25/69 = 0.36 degrees.
    Let's just use a simple straight line along the equator for easy math.
    """
    # 0.0 to 0.4 degrees longitude at latitude 0 is ~44km (> 24.5 miles).
    return LineString([(0.0, 0.0), (0.4, 0.0)])


@pytest.fixture
def short_route_line() -> LineString:
    """A line < 24.0 miles."""
    # 0.1 degree longitude is ~11km (~6.8 miles)
    return LineString([(0.0, 0.0), (0.1, 0.0)])


@pytest.fixture
def non_monotonic_route_line() -> LineString:
    """A line that spirals tightly, breaking Euclidean distance monotonicity.

    Wait, Euclidean distance from start (0.0, 0.0) must be non-decreasing.
    We can create a line that goes out to 10 miles, comes back to 5 miles, then goes to 30 miles.
    """
    # Out to 0.2 deg, back to 0.1 deg, out to 0.4 deg.
    # Total length: 0.2 + 0.1 + 0.3 = 0.6 deg (~66km)
    return LineString([(0.0, 0.0), (0.2, 0.0), (0.1, 0.0), (0.5, 0.0)])


class TestLinearReferencing:
    def test_interpolation_fifty_stops_distance(self, valid_route_line):
        """Stop 1 at 0.0 mi, Stop 50 at 24.5 mi."""
        df = interpolate_stops_along_route(
            valid_route_line,
            origin_lat=0.0,
            origin_lon=0.0,
            crs="EPSG:4326",
        )
        assert len(df) == 50

        # Check Stop 1
        stop1 = df.row(0, named=True)
        assert stop1["StopNumber"] == 1
        assert stop1["StopDistanceMiles"] == 0.0
        assert stop1["GeometrySource"] == "route_vector_interpolated"

        # Check Stop 50
        stop50 = df.row(49, named=True)
        assert stop50["StopNumber"] == 50
        assert stop50["StopDistanceMiles"] == 24.5

    def test_monotonic_distance_validation(self, non_monotonic_route_line):
        """Assertion of monotonic distance progression raises ValueError."""
        with pytest.raises(
            ValueError, match="Non-monotonic distance progression detected"
        ):
            interpolate_stops_along_route(
                non_monotonic_route_line,
                origin_lat=0.0,
                origin_lon=0.0,
                crs="EPSG:4326",
            )

    def test_fallback_generator(self):
        """Fallback linear bearing generator when polyline is null."""
        df = interpolate_stops_along_route(
            None,
            origin_lat=40.0,
            origin_lon=-100.0,
            crs="EPSG:4269",
        )
        assert len(df) == 50
        assert df["GeometrySource"][0] == "origin_fallback"
        assert df["StopNumber"][0] == 1
        assert df["StopDistanceMiles"][49] == 24.5

        # Verify first point is origin
        stop1 = df.row(0, named=True)
        assert stop1["StopLatitude"] == 40.0
        assert stop1["StopLongitude"] == -100.0

        # Verify Stops 2-50 have null coordinates
        for i in range(1, 50):
            stop_row = df.row(i, named=True)
            assert stop_row["StopLatitude"] is None
            assert stop_row["StopLongitude"] is None


class TestNegativeAssertions:
    def test_short_polyline_length(self, short_route_line):
        """Negative Assertion 1: Raise ValueError if route polyline length is less than 24.0 miles."""
        with pytest.raises(ValueError, match="less than 24.0 miles"):
            interpolate_stops_along_route(
                short_route_line,
                origin_lat=0.0,
                origin_lon=0.0,
                crs="EPSG:4326",
            )

    def test_invalid_crs_transformation(self, valid_route_line):
        """Negative Assertion 2: Raise ValueError if coordinate transformation encounters invalid CRS."""
        with pytest.raises(ValueError, match="Coordinate transformation"):
            interpolate_stops_along_route(
                valid_route_line,
                origin_lat=0.0,
                origin_lon=0.0,
                crs="INVALID_CRS",
            )
