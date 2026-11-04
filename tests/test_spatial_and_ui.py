"""Spatial geometry and exporter tests, and the Streamlit profile editor."""

from __future__ import annotations

import tomllib
from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Point, Polygon
from streamlit.testing.v1 import AppTest

from bbs_pipeline.config import RunProfile, load_profile, save_profile
from bbs_pipeline.contracts import ObserverTenureMode, OutputContract
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


def test_streamlit_editor_saves_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path = tmp_path / "project.toml"
    save_profile(RunProfile(), profile_path)
    monkeypatch.setenv("BBS_PROFILE", str(profile_path))

    at = AppTest.from_file(Path(__file__).resolve().parent.parent / "ui" / "app.py")
    at.run(timeout=15)
    assert not at.exception

    at.checkbox(key="ui_offline").check()
    at.text_input(key="ui_release").set_value("10.5066/CUSTOM_DOI")
    at.text_input(key="ui_states").set_value("63, NC, Virginia")
    at.text_input(key="ui_boundary").set_value("boundaries/nc_buffer.gpkg")
    at.slider(key="ui_years").set_value((2001, 2023))
    at.slider(key="ui_min_continuity").set_value(0.5)
    at.selectbox(key="ui_observer_tenure").select("veteran_only")
    at.text_input(key="ui_families").set_value("Parulidae")
    at.text_input(key="ui_aous").set_value("7550")
    at.multiselect(key="ui_joins").select("bcr")
    at.text_input(key="ui_output_path").set_value("out/nc.gpkg")
    at.text_area(key="ui_overlays").set_value("watershed=data/huc8.gpkg")
    at.run(timeout=15)
    assert not at.exception

    tomllib.loads(profile_path.read_text(encoding="utf-8"))  # valid TOML
    saved = load_profile(profile_path)
    assert saved.source.offline is True
    assert saved.source.release == "10.5066/CUSTOM_DOI"
    assert saved.routes.states == [63, "NC", "Virginia"]
    assert saved.routes.boundary == Path("boundaries/nc_buffer.gpkg")
    assert (saved.runs.start_year, saved.runs.end_year) == (2001, 2023)
    assert saved.runs.min_continuity == pytest.approx(0.5)
    assert saved.runs.observer_tenure == ObserverTenureMode.VETERAN_ONLY
    assert saved.species.families == ["Parulidae"]
    assert saved.species.aous == ["07550"]
    assert "bcr" in [j.value for j in saved.joins.tables]
    assert saved.output.path == Path("out/nc.gpkg")
    assert saved.output.overlays == {"watershed": Path("data/huc8.gpkg")}


def test_streamlit_editor_shows_validation_errors_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path = tmp_path / "project.toml"
    save_profile(RunProfile(), profile_path)
    before = profile_path.read_text(encoding="utf-8")
    monkeypatch.setenv("BBS_PROFILE", str(profile_path))

    at = AppTest.from_file(Path(__file__).resolve().parent.parent / "ui" / "app.py")
    at.run(timeout=15)
    at.text_input(key="ui_output_path").set_value("out/nc.xlsx")
    at.run(timeout=15)

    assert any("output.path" in e.value for e in at.error)
    assert profile_path.read_text(encoding="utf-8") == before
