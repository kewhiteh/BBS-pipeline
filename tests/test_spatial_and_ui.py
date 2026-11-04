"""Unit and Headless Streamlit AppTest Suite for Spatial Exporter, Pipeline, CLI & UI."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Point, Polygon
from streamlit.testing.v1 import AppTest

from bbs_pipeline.cli import main as cli_main
from bbs_pipeline.contracts import (
    ExportFormat,
    ObserverTenureMode,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpatialExportConfigContract,
)
from bbs_pipeline.pipeline import run_bbs_pipeline_from_dir
from bbs_pipeline.spatial.exporter import run_spatial_export
from bbs_pipeline.spatial.geometry import build_spatial_geodataframe
from tests.release_fixtures import (
    TEST_DOI,
    fifty_frame,
    routes_frame,
    species_frame,
    weather_frame,
    write_release,
)


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
    spatial = SpatialExportConfigContract(enable_spatial_join=True)

    gdf = build_spatial_geodataframe(obs_df, None, spatial)

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

    spatial = SpatialExportConfigContract(
        enable_spatial_join=True,
        custom_overlay_paths={"watershed": basins_path},
    )

    gdf = build_spatial_geodataframe(obs_df, None, spatial)
    assert "overlay_watershed" in gdf.columns
    r1_basin = gdf.loc[gdf["route_id"] == "63_001", "overlay_watershed"].iloc[0]
    r2_basin = gdf.loc[gdf["route_id"] == "63_002", "overlay_watershed"].iloc[0]
    assert r1_basin == "Neuse_Basin"
    assert r2_basin == "Cape_Fear_Basin"


def test_multi_crs_and_all_six_export_formats(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()

    out_dir = tmp_path / "exports"
    spatial = SpatialExportConfigContract(
        enable_spatial_join=True,
        target_crs_list=["EPSG:4326", "EPSG:5070", "EPSG:32119"],
        export_formats=[
            ExportFormat.GEOPARQUET,
            ExportFormat.GPKG,
            ExportFormat.FLATGEOBUF,
            ExportFormat.GEOJSON,
            ExportFormat.PARQUET,
            ExportFormat.CSV,
        ],
        output_dir=out_dir,
    )

    exported = run_spatial_export(
        observations_df=obs_df,
        routes_df=None,
        spatial=spatial,
    )

    # 2 tabular + (3 CRS * 4 vector formats) = 14 files
    assert len(exported) == 14
    for p in exported:
        assert p.exists() and p.stat().st_size > 0

    # Verify CRS reprojection on EPSG:5070 and EPSG:32119 GeoParquet
    gdf_5070 = gpd.read_parquet(out_dir / "bbs_observations_epsg_5070.geoparquet")
    assert gdf_5070.crs.to_epsg() == 5070

    gdf_32119 = gpd.read_parquet(out_dir / "bbs_observations_epsg_32119.geoparquet")
    assert gdf_32119.crs.to_epsg() == 32119


def test_pipeline_orchestrator_and_cli(tmp_path: Path) -> None:
    raw_dir, cache_dir = tmp_path / "raw", tmp_path / "cache"
    write_release(
        raw_dir,
        routes=routes_frame([(63, 1, "RALEIGH", 35.75, -78.65)]),
        weather=weather_frame([(1001, 63, 1, 2022, 501, 1)]),
        species=species_frame([("07550", "Wood Thrush", "Passeriformes", "Turdidae")]),
        fifty_stop=fifty_frame([(1001, 63, 1, 2022, "7550", dict.fromkeys(range(1, 6), 1))]),
    )

    cfg_dir = tmp_path / "config"
    bundle = PipelineConfigBundle(
        protocol=ProtocolConfigContract(
            raw_data_dir=raw_dir,
            sanitized_cache_dir=cache_dir,
            sciencebase_doi=TEST_DOI,
            year_range=(2021, 2023),
        ),
        spatial=SpatialExportConfigContract(
            target_crs_list=["EPSG:4326"],
            export_formats=[ExportFormat.GEOPARQUET, ExportFormat.PARQUET],
            output_dir=tmp_path / "out",
        ),
    )
    bundle.save_to_dir(cfg_dir)

    assert cli_main(["run", "--config-dir", str(cfg_dir), "--validate-only"]) == 0
    assert cli_main(["run", "--config-dir", str(cfg_dir), "--offline"]) == 0

    result = run_bbs_pipeline_from_dir(cfg_dir)
    assert result.funnel_metrics.ingress_tier_used == "cache"
    assert result.funnel_metrics.final_output_rows == 1
    assert len(result.exported_files) == 2
    assert len(result.preview_records) == 1


def test_streamlit_workbench_two_way_contract_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_dir = tmp_path / "config"
    default_bundle = PipelineConfigBundle()
    default_bundle.save_to_dir(cfg_dir)

    monkeypatch.setenv("BBS_CONFIG_DIR", str(cfg_dir))

    app_path = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    at = AppTest.from_file(app_path)
    at.run(timeout=15)
    assert not at.exception

    # Mutate Tab 1 (Ingress), Tab 2 (Guilds), Tab 3 (Protocol), Tab 4 (Spatial)
    at.checkbox(key="ui_offline").check()
    at.text_input(key="ui_sciencebase_doi").set_value("10.5066/CUSTOM_DOI")
    at.checkbox(key="ui_force_resanitize").check()

    at.text_input(key="ui_guild_citation").set_value("Updated Citation (2026)")
    at.text_input(key="ui_guild_aou_codes").set_value("07550, 06740, 06080")

    at.slider(key="ui_year_range").set_value((2001, 2023))
    at.text_input(key="ui_boundary_path").set_value("boundaries/nc_buffer.gpkg")
    at.slider(key="ui_continuity_ratio").set_value(0.5)
    at.selectbox(key="ui_observer_tenure_mode").select("veteran_only")
    at.text_input(key="ui_states").set_value("63, NC, Virginia")
    at.checkbox(key="ui_join_bcr").check()

    at.checkbox(key="ui_crs_5070").uncheck()
    at.text_input(key="ui_custom_crs").set_value("EPSG:26917")

    at.run(timeout=15)
    assert not at.exception

    # Reload from disk and verify two-way JSON contract synchronization
    reloaded = PipelineConfigBundle.load_from_dir(cfg_dir)

    assert reloaded.protocol.offline is True
    assert reloaded.protocol.sciencebase_doi == "10.5066/CUSTOM_DOI"
    assert reloaded.protocol.force_resanitize is True

    active_key = reloaded.guilds.active_guild
    assert active_key is not None
    assert reloaded.guilds.profiles[active_key].citation == "Updated Citation (2026)"
    assert reloaded.guilds.profiles[active_key].aou_codes == ["07550", "06740", "06080"]

    assert reloaded.protocol.year_range == (2001, 2023)
    assert reloaded.protocol.boundary_path == Path("boundaries/nc_buffer.gpkg")
    assert reloaded.protocol.min_route_continuity_ratio == pytest.approx(0.5)
    assert reloaded.protocol.observer_tenure_mode == ObserverTenureMode.VETERAN_ONLY
    assert reloaded.protocol.states == [63, "NC", "Virginia"]
    assert "bcr" in [j.value for j in reloaded.protocol.include_joins]

    assert "EPSG:5070" not in reloaded.spatial.target_crs_list
    assert "EPSG:26917" in reloaded.spatial.target_crs_list
