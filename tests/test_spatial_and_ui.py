"""Spatial geometry and exporter tests."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Point, Polygon

from bbs_pipeline.contracts import OutputContract, OverlaySpec
from bbs_pipeline.spatial.exporter import write_output
from bbs_pipeline.spatial.geometry import build_spatial_geodataframe


def _make_sample_observations() -> pl.DataFrame:
    """Create sample BBS observations with Count10..Count50 and Stop1..Stop50 columns."""
    base: dict[str, list[object]] = {
        "route_id": ["63_001", "63_002"],
        "Year": [2022, 2022],
        "AOU": ["07550", "06740"],
        "route_total": [15, 20],
        "Count10": [3, 4],
        "Count20": [3, 4],
        "Count30": [3, 4],
        "Count40": [3, 4],
        "Count50": [3, 4],
        "Latitude": [35.75, 34.90],
        "Longitude": [-78.65, -78.90],
    }
    for s in range(1, 51):
        base[f"Stop{s}"] = [1 if s <= 15 else 0, 1 if s <= 20 else 0]
    return pl.DataFrame(base)


def test_build_spatial_geodataframe_start_points() -> None:
    obs_df = _make_sample_observations()

    gdf = build_spatial_geodataframe(obs_df, None)

    assert len(gdf) == 2
    assert gdf.crs.to_string() == "EPSG:4326"
    geom_1 = gdf.loc[gdf["route_id"] == "63_002", "geometry"].iloc[0]
    assert isinstance(geom_1, Point)
    # NAD83 -> WGS84 shifts by well under a metre in North Carolina
    assert geom_1.x == pytest.approx(-78.90, abs=1e-4)
    assert geom_1.y == pytest.approx(34.90, abs=1e-4)


def test_strtree_custom_polygon_overlays(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()

    # Create synthetic NC river basin polygons
    neuse_poly = Polygon([(-79.0, 35.5), (-78.0, 35.5), (-78.0, 36.2), (-79.0, 36.2)])
    cape_fear_poly = Polygon([(-79.5, 34.5), (-78.5, 34.5), (-78.5, 35.2), (-79.5, 35.2)])
    basins_gdf = gpd.GeoDataFrame(
        {"name": ["Neuse_Basin", "Cape_Fear_Basin"]},
        geometry=[neuse_poly, cape_fear_poly],
        crs="EPSG:4326",
    )
    basins_path = tmp_path / "nc_basins.parquet"
    basins_gdf.to_parquet(basins_path)

    gdf = build_spatial_geodataframe(obs_df, None, overlays={"watershed": basins_path})
    assert "overlay_watershed" in gdf.columns
    r1_basin = gdf.loc[gdf["route_id"] == "63_001", "overlay_watershed"].iloc[0]
    r2_basin = gdf.loc[gdf["route_id"] == "63_002", "overlay_watershed"].iloc[0]
    assert r1_basin == "Neuse_Basin"
    assert r2_basin == "Cape_Fear_Basin"


def test_overlay_uses_the_chosen_column_and_rejects_a_missing_one(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()
    neuse_poly = Polygon([(-79.0, 35.5), (-78.0, 35.5), (-78.0, 36.2), (-79.0, 36.2)])
    cape_fear_poly = Polygon([(-79.5, 34.5), (-78.5, 34.5), (-78.5, 35.2), (-79.5, 35.2)])
    basins_path = tmp_path / "basins.parquet"
    gpd.GeoDataFrame(
        {"name": ["Neuse_Basin", "Cape_Fear_Basin"], "huc4": ["0302", "0303"]},
        geometry=[neuse_poly, cape_fear_poly],
        crs="EPSG:4326",
    ).to_parquet(basins_path)

    spec = OverlaySpec(file=basins_path, column="huc4")
    gdf = build_spatial_geodataframe(obs_df, None, overlays={"basin": spec})
    labels = dict(zip(gdf["route_id"], gdf["overlay_basin"], strict=True))
    assert (labels["63_001"], labels["63_002"]) == ("0302", "0303")

    with pytest.raises(ValueError, match="has no column 'HUC8'. Columns: name, huc4"):
        build_spatial_geodataframe(
            obs_df, None, overlays={"basin": OverlaySpec(file=basins_path, column="HUC8")}
        )


@pytest.mark.parametrize(
    "name", ["o.parquet", "o.csv", "o.geoparquet", "o.gpkg", "o.fgb", "o.geojson"]
)
def test_each_format_writes_one_file(tmp_path: Path, name: str) -> None:
    output = OutputContract(path=tmp_path / name, crs="EPSG:5070", layer_name="birds")

    path = write_output(_make_sample_observations(), None, output)

    assert sorted(p.name for p in tmp_path.iterdir()) == [name]
    if output.is_vector:
        gdf = (
            gpd.read_parquet(path)
            if name.endswith(".geoparquet")
            else gpd.read_file(path, layer="birds" if name.endswith(".gpkg") else None)
        )
        assert gdf.crs.to_epsg() == 5070
        assert list(gdf.columns)[-1] == "geometry"


def test_unknown_output_extension_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown output extension '.xlsx'"):
        OutputContract(path=Path("o.xlsx"))
