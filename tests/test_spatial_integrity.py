"""Spatial output never invents coordinates or guesses a CRS; boundary selection uses start points."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Point, box

from bbs_pipeline.spatial import (
    assign_custom_overlays,
    build_spatial_geodataframe,
    route_geodataframe,
    routes_within_boundary,
)
from bbs_pipeline.spatial.files import save_gis_upload


def _observations() -> pl.DataFrame:
    return pl.DataFrame(
        {"route_id": ["63_001", "63_002"], "Year": [2021, 2021], "route_total": [3, 4]}
    )


def _routes(lat2: float | None) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "route_id": ["63_001", "63_002"],
            "Latitude": [36.07, lat2],
            "Longitude": [-79.01, -78.94],
        },
        schema_overrides={"Latitude": pl.Float64},
    )


def _write_boundary(path: Path, crs: str | None = "EPSG:4326") -> Path:
    gpd.GeoDataFrame(geometry=[box(-79.1, 36.0, -79.0, 36.1)], crs=crs).to_file(path, driver="GPKG")
    return path


def test_route_without_coordinates_gets_null_geometry() -> None:
    gdf = build_spatial_geodataframe(_observations(), _routes(lat2=None))

    by_route = dict(zip(gdf["route_id"], gdf.geometry, strict=True))
    assert by_route["63_002"] is None
    assert by_route["63_001"].x == pytest.approx(-79.01, abs=1e-4)
    assert str(gdf.crs) == "EPSG:4326"


def test_boundary_keeps_routes_whose_start_point_is_inside(tmp_path: Path) -> None:
    boundary = _write_boundary(tmp_path / "box.gpkg")

    assert routes_within_boundary(_routes(lat2=35.99), boundary) == ["63_001"]


def test_boundary_in_projected_crs_is_reprojected(tmp_path: Path) -> None:
    path = tmp_path / "box_5070.gpkg"
    gpd.GeoDataFrame(geometry=[box(-79.1, 36.0, -79.0, 36.1)], crs="EPSG:4326").to_crs(
        "EPSG:5070"
    ).to_file(path, driver="GPKG")

    assert routes_within_boundary(_routes(lat2=35.99), path) == ["63_001"]


def test_boundary_excludes_routes_without_coordinates(tmp_path: Path) -> None:
    boundary = _write_boundary(tmp_path / "box.gpkg")
    routes = _routes(lat2=None).with_columns(pl.lit(-79.05).alias("Longitude"))

    assert routes_within_boundary(routes, boundary) == ["63_001"]


def test_boundary_without_crs_raises(tmp_path: Path) -> None:
    path = tmp_path / "no_crs.parquet"
    gpd.GeoDataFrame(geometry=[box(-79.1, 36.0, -79.0, 36.1)]).to_parquet(path)

    with pytest.raises(ValueError, match="has no CRS"):
        routes_within_boundary(_routes(lat2=35.99), path)


def test_missing_boundary_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Boundary file not found"):
        routes_within_boundary(_routes(lat2=35.99), tmp_path / "missing.gpkg")


def test_missing_overlay_file_raises(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame({"route_id": ["63_001"]}, geometry=[Point(0.5, 0.5)], crs="EPSG:4326")

    with pytest.raises(FileNotFoundError, match="watersheds"):
        assign_custom_overlays(gdf, {"watersheds": tmp_path / "missing.gpkg"})


def test_geometry_is_built_once_per_route_and_joined_to_every_row() -> None:
    observations = pl.DataFrame(
        {"route_id": ["63_001", "63_001", "63_002", "63_001"], "Year": [2020, 2021, 2021, 2022]}
    )

    routes = route_geodataframe(observations, _routes(lat2=35.99), crs="EPSG:5070")
    gdf = build_spatial_geodataframe(observations, _routes(lat2=35.99), crs="EPSG:5070")

    assert routes["route_id"].tolist() == ["63_001", "63_002"]
    assert gdf["route_id"].tolist() == observations["route_id"].to_list()  # row order kept
    assert gdf.crs.to_epsg() == 5070
    first = gdf[gdf["route_id"] == "63_001"].geometry
    assert first.geom_equals(first.iloc[0]).all()


def _shapefile_zip(tmp_path: Path) -> bytes:
    import io
    import zipfile

    import geopandas as gpd
    from shapely.geometry import box

    shp = tmp_path / "shp"
    shp.mkdir()
    gpd.GeoDataFrame(
        {"NAME": ["Neuse"]}, geometry=[box(-79, 35.5, -78, 36.2)], crs="EPSG:4326"
    ).to_file(shp / "basins.shp")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for part in shp.iterdir():
            archive.write(part, part.name)
    return buffer.getvalue()


def test_a_zipped_shapefile_upload_is_unpacked_to_its_shp(tmp_path: Path) -> None:
    import geopandas as gpd

    saved = save_gis_upload("basins.zip", _shapefile_zip(tmp_path), tmp_path / "gis")

    assert saved == tmp_path / "gis" / "basins" / "basins.shp"
    assert gpd.read_file(saved)["NAME"].tolist() == ["Neuse"]


def test_an_upload_zip_cannot_write_outside_its_folder(tmp_path: Path) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.shp", b"x")

    with pytest.raises(ValueError, match="unsafe path"):
        save_gis_upload("bad.zip", buffer.getvalue(), tmp_path / "gis")
    assert not (tmp_path / "escape.shp").exists()


def test_a_plain_upload_is_written_as_is(tmp_path: Path) -> None:
    saved = save_gis_upload("x.geojson", b"{}", tmp_path / "gis")

    assert saved.read_bytes() == b"{}"
