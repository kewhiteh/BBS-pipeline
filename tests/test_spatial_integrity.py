"""Spatial output never invents coordinates or guesses a CRS."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import LineString, Polygon

from bbs_pipeline.contracts import (
    GeometrySource,
    ProtocolConfigContract,
    SpatialExportConfigContract,
)
from bbs_pipeline.spatial import assign_custom_overlays, build_spatial_geodataframe


def _observations() -> pl.DataFrame:
    return pl.DataFrame(
        {"route_id": ["63_001", "63_002"], "Year": [2021, 2021], "route_total": [3, 4]}
    )


def _routes(lat2: float | None) -> pl.DataFrame:
    return pl.DataFrame(
        {"route_id": ["63_001", "63_002"], "Latitude": [36.07, lat2], "Longitude": [-79.01, -78.94]}
    )


def test_route_without_coordinates_gets_null_geometry(tmp_path: Path) -> None:
    spatial = SpatialExportConfigContract(
        geometry_source=GeometrySource.ROUTE_START_POINTS_FALLBACK,
        route_geometry_path=tmp_path / "absent.parquet",
    )

    gdf = build_spatial_geodataframe(
        _observations(), _routes(lat2=None), ProtocolConfigContract(), spatial
    )

    by_route = dict(zip(gdf["route_id"], gdf.geometry, strict=True))
    assert by_route["63_002"] is None
    assert by_route["63_001"].x == pytest.approx(-79.01, abs=1e-4)
    assert str(gdf.crs) == "EPSG:4326"


def test_polyline_file_without_crs_raises(tmp_path: Path) -> None:
    path = tmp_path / "routes.parquet"
    gpd.GeoDataFrame(
        {"route_id": ["63_001"]}, geometry=[LineString([(-79.0, 36.0), (-78.9, 36.1)])]
    ).to_parquet(path)
    spatial = SpatialExportConfigContract(route_geometry_path=path)

    with pytest.raises(ValueError, match="has no CRS"):
        build_spatial_geodataframe(
            _observations(), _routes(lat2=35.99), ProtocolConfigContract(), spatial
        )


def test_polyline_file_without_route_id_column_raises(tmp_path: Path) -> None:
    path = tmp_path / "routes.parquet"
    gpd.GeoDataFrame(
        {"name": ["x"]}, geometry=[LineString([(-79.0, 36.0), (-78.9, 36.1)])], crs="EPSG:4326"
    ).to_parquet(path)
    spatial = SpatialExportConfigContract(route_geometry_path=path)

    with pytest.raises(KeyError, match="no route id column"):
        build_spatial_geodataframe(
            _observations(), _routes(lat2=35.99), ProtocolConfigContract(), spatial
        )


def test_missing_overlay_file_raises(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame(
        {"route_id": ["63_001"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1)]).centroid],
        crs="EPSG:4326",
    )

    with pytest.raises(FileNotFoundError, match="watersheds"):
        assign_custom_overlays(gdf, {"watersheds": tmp_path / "missing.gpkg"})
