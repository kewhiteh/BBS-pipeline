"""High-Precision 50-Stop Linear Referencing Engine.

Implements Phase 2 of the Track B Additive Upgrade.
Provides sub-mile linear interpolation along route polylines.
"""

from __future__ import annotations

import logging
import math

import polars as pl
from pyproj import Geod, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import LineString
from shapely.ops import transform

logger = logging.getLogger(__name__)

# Constants
_METERS_PER_MILE = 1609.344
_STOP_INTERVAL_MILES = 0.5
_STOP_INTERVAL_METERS = _STOP_INTERVAL_MILES * _METERS_PER_MILE
_TOTAL_STOPS = 50
_MIN_ROUTE_LENGTH_MILES = 24.0
_TARGET_CRS = "EPSG:4326"
_INTERPOLATION_CRS = "EPSG:5070"
_FALLBACK_BEARING = 90.0  # Due East for fallback


def interpolate_stops_along_route(
    route_line: LineString | None,
    origin_lat: float,
    origin_lon: float,
    crs: str = "EPSG:4269",
) -> pl.DataFrame:
    """Interpolate 50 discrete stops along a route polyline.

    Transforms the line to EPSG:5070 for Euclidean interpolation,
    calculates points at 0.5-mile intervals, and transforms back to EPSG:4326.
    If the line is missing, empty, or corrupt (e.g. invalid CRS), uses a
    geodesic fallback generator anchoring from (origin_lat, origin_lon).

    Parameters
    ----------
    route_line : LineString | None
        The continuous route polyline in the source CRS.
    origin_lat : float
        Latitude anchor from routes.csv.
    origin_lon : float
        Longitude anchor from routes.csv.
    crs : str, default "EPSG:4269"
        The Coordinate Reference System of the input line.

    Returns
    -------
    pl.DataFrame
        DataFrame with StopNumber, StopLatitude, StopLongitude,
        StopDistanceMiles, and GeometrySource.

    Raises
    ------
    ValueError
        If the polyline is present but < 24.0 miles, or if CRS transforms fail
        with unprojectable coordinates.
    """
    stops_data = []

    use_fallback = False
    if route_line is None or route_line.is_empty:
        use_fallback = True

    if not use_fallback:
        try:
            transformer_to_5070 = Transformer.from_crs(
                crs, _INTERPOLATION_CRS, always_xy=True
            )
            line_5070 = transform(transformer_to_5070.transform, route_line)
        except (CRSError, ProjError, Exception) as exc:
            raise ValueError(
                f"Coordinate transformation to EPSG:5070 failed: {exc}"
            ) from exc

        length_miles = line_5070.length / _METERS_PER_MILE
        if length_miles < _MIN_ROUTE_LENGTH_MILES:
            raise ValueError(
                f"Route polyline length is less than 24.0 miles (insufficient transect length): {length_miles:.2f} mi"
            )

        try:
            transformer_to_4326 = Transformer.from_crs(
                _INTERPOLATION_CRS, _TARGET_CRS, always_xy=True
            )
        except (CRSError, ProjError, Exception) as exc:
            raise ValueError(
                f"Coordinate transformation to EPSG:4326 failed: {exc}"
            ) from exc

        # Check monotonicity via Euclidean distance from first point
        # The equation: distance(P_1, P_i) <= distance(P_1, P_{i+1})
        # Note: P_1 is the 0-mile point (Stop 1)
        prev_dist_from_start = -1.0
        p1_5070 = None

        for i in range(1, _TOTAL_STOPS + 1):
            dist_miles = (i - 1) * _STOP_INTERVAL_MILES
            dist_meters = dist_miles * _METERS_PER_MILE

            pt_5070 = line_5070.interpolate(dist_meters)

            if i == 1:
                p1_5070 = pt_5070

            dist_from_start = p1_5070.distance(pt_5070)
            if dist_from_start < prev_dist_from_start - 1e-5:
                # Monotonicity failed
                # The rule states we must assert monotonic distance progression.
                raise ValueError(
                    f"Non-monotonic distance progression detected at stop {i}"
                )
            prev_dist_from_start = dist_from_start

            pt_4326 = transform(transformer_to_4326.transform, pt_5070)
            if math.isinf(pt_4326.x) or math.isnan(pt_4326.x):
                raise ValueError(
                    "Unprojectable coordinates generated during interpolation."
                )

            stops_data.append(
                {
                    "StopNumber": i,
                    "StopLatitude": pt_4326.y,
                    "StopLongitude": pt_4326.x,
                    "StopDistanceMiles": dist_miles,
                    "GeometrySource": "route_vector_interpolated",
                }
            )
    else:
        # Fallback mechanism
        logger.info("Using origin_linear_fallback for missing route geometry.")
        geod = Geod(ellps="WGS84")
        for i in range(1, _TOTAL_STOPS + 1):
            dist_miles = (i - 1) * _STOP_INTERVAL_MILES
            dist_meters = dist_miles * _METERS_PER_MILE

            if dist_meters == 0:
                lon, lat = origin_lon, origin_lat
            else:
                lon, lat, _ = geod.fwd(
                    origin_lon, origin_lat, _FALLBACK_BEARING, dist_meters
                )

            stops_data.append(
                {
                    "StopNumber": i,
                    "StopLatitude": lat,
                    "StopLongitude": lon,
                    "StopDistanceMiles": dist_miles,
                    "GeometrySource": "origin_linear_fallback",
                }
            )

    return pl.DataFrame(
        stops_data,
        schema={
            "StopNumber": pl.Int32,
            "StopLatitude": pl.Float64,
            "StopLongitude": pl.Float64,
            "StopDistanceMiles": pl.Float64,
            "GeometrySource": pl.String,
        },
    )
