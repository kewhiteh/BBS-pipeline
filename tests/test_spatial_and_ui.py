"""Unit and Headless Streamlit AppTest Suite for Spatial Exporter, Pipeline, CLI & UI."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import LineString, Point, Polygon
from streamlit.testing.v1 import AppTest

from bbs_pipeline.cli import main as cli_main
from bbs_pipeline.contracts import (
    Covid2020Mode,
    ExportFormat,
    GeometrySource,
    IngressMode,
    ObserverTenureMode,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
)
from bbs_pipeline.pipeline import run_bbs_pipeline_from_dir
from bbs_pipeline.spatial.exporter import run_spatial_export
from bbs_pipeline.spatial.linear_referencing import build_spatial_geodataframe


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


def test_build_spatial_geodataframe_polylines_and_fallback(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()

    # 1. Create route polylines GeoParquet for 63_001 (leave 63_002 to test fallback)
    routes_gdf = gpd.GeoDataFrame(
        {"route_id": ["63_001"]},
        geometry=[LineString([(-78.80, 35.70), (-78.50, 35.85)])],
        crs="EPSG:4326",
    )
    route_parquet = tmp_path / "bbs_routes.parquet"
    routes_gdf.to_parquet(route_parquet)

    protocol = ProtocolConfigContract(stop_aggregation=StopAggregationMode.ROUTE_TOTAL)
    spatial = SpatialExportConfigContract(
        enable_spatial_join=True,
        geometry_source=GeometrySource.ROUTE_POLYLINES,
        route_geometry_path=route_parquet,
        enable_stop_linear_referencing=False,
    )

    gdf = build_spatial_geodataframe(obs_df, None, protocol, spatial)
    assert len(gdf) == 2
    assert gdf.crs.to_string() == "EPSG:4326"
    geom_0 = gdf.loc[gdf["route_id"] == "63_001", "geometry"].iloc[0]
    geom_1 = gdf.loc[gdf["route_id"] == "63_002", "geometry"].iloc[0]
    assert isinstance(geom_0, LineString)
    assert isinstance(geom_1, Point)
    assert geom_1.x == pytest.approx(-78.90)
    assert geom_1.y == pytest.approx(34.90)


def test_stop_linear_referencing_ten_stop_bins_and_raw_50_stops(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()

    routes_gdf = gpd.GeoDataFrame(
        {"route_id": ["63_001", "63_002"]},
        geometry=[
            LineString([(-79.0, 35.0), (-78.0, 36.0)]),
            LineString([(-79.5, 34.5), (-78.5, 35.5)]),
        ],
        crs="EPSG:4326",
    )
    route_parquet = tmp_path / "routes.parquet"
    routes_gdf.to_parquet(route_parquet)

    # 1. TEN_STOP_BINS linear referencing -> 5 sub-route LineStrings per observation row
    protocol_bins = ProtocolConfigContract(stop_aggregation=StopAggregationMode.TEN_STOP_BINS)
    spatial_lr = SpatialExportConfigContract(
        enable_spatial_join=True,
        geometry_source=GeometrySource.ROUTE_POLYLINES,
        route_geometry_path=route_parquet,
        enable_stop_linear_referencing=True,
    )
    gdf_bins = build_spatial_geodataframe(obs_df, None, protocol_bins, spatial_lr)
    assert len(gdf_bins) == 2 * 5  # 2 route-species rows * 5 bins
    assert set(gdf_bins["stop_bin"]) == {
        "Stops 1-10",
        "Stops 11-20",
        "Stops 21-30",
        "Stops 31-40",
        "Stops 41-50",
    }
    assert all(isinstance(g, LineString) and g.length > 0 for g in gdf_bins.geometry)

    # Verify bin 0 starts at (-79.0, 35.0) and ends at (-78.8, 35.2) for route 63_001
    r1_bin0 = gdf_bins[(gdf_bins["route_id"] == "63_001") & (gdf_bins["bin_index"] == 0)].iloc[0]
    coords = list(r1_bin0.geometry.coords)
    assert coords[0][0] == pytest.approx(-79.0)
    assert coords[-1][0] == pytest.approx(-78.8)

    # 2. RAW_50_STOPS linear referencing -> 50 interpolated Points per observation row
    protocol_stops = ProtocolConfigContract(stop_aggregation=StopAggregationMode.RAW_50_STOPS)
    gdf_stops = build_spatial_geodataframe(obs_df, None, protocol_stops, spatial_lr)
    assert len(gdf_stops) == 2 * 50  # 2 route-species rows * 50 stops
    assert set(gdf_stops["stop_number"]) == set(range(1, 51))
    assert all(isinstance(g, Point) for g in gdf_stops.geometry)

    # Stop 1 is at start of line (-79.0, 35.0); Stop 50 is at end (-78.0, 36.0)
    r1_stop1 = gdf_stops[
        (gdf_stops["route_id"] == "63_001") & (gdf_stops["stop_number"] == 1)
    ].iloc[0]
    r1_stop50 = gdf_stops[
        (gdf_stops["route_id"] == "63_001") & (gdf_stops["stop_number"] == 50)
    ].iloc[0]
    assert r1_stop1.geometry.x == pytest.approx(-79.0)
    assert r1_stop1.geometry.y == pytest.approx(35.0)
    assert r1_stop50.geometry.x == pytest.approx(-78.0)
    assert r1_stop50.geometry.y == pytest.approx(36.0)


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

    protocol = ProtocolConfigContract(stop_aggregation=StopAggregationMode.ROUTE_TOTAL)
    spatial = SpatialExportConfigContract(
        enable_spatial_join=True,
        geometry_source=GeometrySource.ROUTE_START_POINTS_FALLBACK,
        custom_overlay_paths={"watershed": basins_path},
    )

    gdf = build_spatial_geodataframe(obs_df, None, protocol, spatial)
    assert "overlay_watershed" in gdf.columns
    r1_basin = gdf.loc[gdf["route_id"] == "63_001", "overlay_watershed"].iloc[0]
    r2_basin = gdf.loc[gdf["route_id"] == "63_002", "overlay_watershed"].iloc[0]
    assert r1_basin == "Neuse_Basin"
    assert r2_basin == "Cape_Fear_Basin"


def test_multi_crs_and_all_seven_export_formats(tmp_path: Path) -> None:
    obs_df = _make_sample_observations()
    guild_df = pl.DataFrame(
        {
            "route_id": ["63_001", "63_002"],
            "Year": [2022, 2022],
            "guild_abundance": [15, 20],
            "species_richness": [1, 1],
            "guild_occupancy_rate": [0.5, 0.5],
        }
    )

    out_dir = tmp_path / "exports"
    protocol = ProtocolConfigContract(stop_aggregation=StopAggregationMode.ROUTE_TOTAL)
    spatial = SpatialExportConfigContract(
        enable_spatial_join=True,
        geometry_source=GeometrySource.ROUTE_START_POINTS_FALLBACK,
        target_crs_list=["EPSG:4326", "EPSG:5070", "EPSG:32119"],
        export_formats=[
            ExportFormat.GEOPARQUET,
            ExportFormat.GPKG,
            ExportFormat.FLATGEOBUF,
            ExportFormat.GEOJSON,
            ExportFormat.PARQUET,
            ExportFormat.CSV,
            ExportFormat.DUCKDB,
        ],
        output_dir=out_dir,
    )

    exported = run_spatial_export(
        observations_df=obs_df,
        routes_df=None,
        protocol=protocol,
        spatial=spatial,
        guild_rollup_df=guild_df,
    )

    # 3 tabular + (3 CRS * 4 vector formats) = 15 files
    assert len(exported) == 15
    for p in exported:
        assert p.exists() and p.stat().st_size > 0

    # Verify CRS reprojection on EPSG:5070 and EPSG:32119 GeoParquet
    gdf_5070 = gpd.read_parquet(out_dir / "bbs_observations_epsg_5070.geoparquet")
    assert gdf_5070.crs.to_epsg() == 5070

    gdf_32119 = gpd.read_parquet(out_dir / "bbs_observations_epsg_32119.geoparquet")
    assert gdf_32119.crs.to_epsg() == 32119

    # Verify DuckDB tables
    with duckdb.connect(str(out_dir / "bbs_observations.duckdb"), read_only=True) as con:
        obs_cnt = con.execute("SELECT COUNT(*) FROM bbs_observations").fetchone()[0]
        guild_cnt = con.execute("SELECT COUNT(*) FROM bbs_guild_rollup").fetchone()[0]
        assert obs_cnt == 2
        assert guild_cnt == 2


def test_pipeline_orchestrator_and_cli(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)

    # Populate minimal Tier 1 sanitized cache
    pl.DataFrame(
        {
            "StateNum": [63],
            "Route": [1],
            "RouteName": ["RALEIGH"],
            "Latitude": [35.75],
            "Longitude": [-78.65],
            "Stratum": [11],
            "BCR": [29],
        }
    ).write_parquet(cache_dir / "routes.parquet")

    pl.DataFrame(
        {
            "RouteDataID": [1001],
            "StateNum": [63],
            "Route": [1],
            "Year": [2022],
            "ObsN": [501],
            "RunType": [1],
            "QualityCurrentID": [1],
            "StartTemp": [65],
            "EndTemp": [78],
            "StartWind": [1],
            "EndWind": [2],
        }
    ).write_parquet(cache_dir / "weather.parquet")

    pl.DataFrame(
        {
            "AOU": ["07550"],
            "English_Common_Name": ["Wood Thrush"],
            "Order": ["Passeriformes"],
            "Family": ["Turdidae"],
            "Genus": ["Hylocichla"],
            "Species": ["mustelina"],
        }
    ).write_parquet(cache_dir / "species_list.parquet")

    fifty_df = pl.DataFrame(
        {
            "RouteDataID": [1001],
            "StateNum": [63],
            "Route": [1],
            "Year": [2022],
            "AOU": ["07550"],
            **{f"Stop{i}": [1 if i <= 5 else 0] for i in range(1, 51)},
        }
    )
    fifty_df.write_parquet(cache_dir / "fifty_stop.parquet")
    (cache_dir / "intake_manifest.json").write_text(
        json.dumps({"status": "healthy", "engine": "intake_engine", "tables": []}),
        encoding="utf-8",
    )

    cfg_dir = tmp_path / "config"
    bundle = PipelineConfigBundle(
        protocol=ProtocolConfigContract(
            ingress_mode=IngressMode.CACHE_ONLY,
            sanitized_cache_dir=cache_dir,
            year_range=(2021, 2023),
        ),
        spatial=SpatialExportConfigContract(
            geometry_source=GeometrySource.ROUTE_START_POINTS_FALLBACK,
            target_crs_list=["EPSG:4326"],
            export_formats=[ExportFormat.GEOPARQUET, ExportFormat.PARQUET],
            output_dir=tmp_path / "out",
        ),
    )
    bundle.save_to_dir(cfg_dir)

    # Validate-only CLI check
    rc_val = cli_main(["--config-dir", str(cfg_dir), "--validate-only"])
    assert rc_val == 0

    # End-to-end execution
    result = run_bbs_pipeline_from_dir(cfg_dir)
    assert result.funnel_metrics.final_output_rows == 1
    assert len(result.exported_files) == 2
    assert len(result.preview_records) == 1
    assert len(result.guild_summary_records) == 1


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
    at.selectbox(key="ui_ingress_mode").select("local_raw_via_intake")
    at.text_input(key="ui_sciencebase_doi").set_value("10.5066/CUSTOM_DOI")
    at.checkbox(key="ui_force_resanitize").check()

    at.text_input(key="ui_guild_citation").set_value("Updated Citation (2026)")
    at.text_input(key="ui_guild_aou_codes").set_value("07550, 06740, 06080")

    at.slider(key="ui_year_range").set_value((2001, 2023))
    at.selectbox(key="ui_covid_2020_mode").select("interpolate_2019_2021")
    at.slider(key="ui_continuity_ratio").set_value(0.5)
    at.selectbox(key="ui_observer_tenure_mode").select("veteran_only")
    at.text_input(key="ui_states").set_value("63, 88")
    at.checkbox(key="ui_join_bcr").check()

    at.checkbox(key="ui_linear_referencing").check()
    at.checkbox(key="ui_crs_5070").uncheck()
    at.text_input(key="ui_custom_crs").set_value("EPSG:26917")

    at.run(timeout=15)
    assert not at.exception

    # Reload from disk and verify two-way JSON contract synchronization
    reloaded = PipelineConfigBundle.load_from_dir(cfg_dir)

    assert reloaded.protocol.ingress_mode == IngressMode.LOCAL_RAW_VIA_INTAKE
    assert reloaded.protocol.sciencebase_doi == "10.5066/CUSTOM_DOI"
    assert reloaded.protocol.force_resanitize is True

    active_key = reloaded.guilds.active_guild
    assert active_key is not None
    assert reloaded.guilds.profiles[active_key].citation == "Updated Citation (2026)"
    assert reloaded.guilds.profiles[active_key].aou_codes == ["07550", "06740", "06080"]

    assert reloaded.protocol.year_range == (2001, 2023)
    assert reloaded.protocol.covid_2020_mode == Covid2020Mode.INTERPOLATE_2019_2021
    assert reloaded.protocol.min_route_continuity_ratio == pytest.approx(0.5)
    assert reloaded.protocol.observer_tenure_mode == ObserverTenureMode.VETERAN_ONLY
    assert reloaded.protocol.states == [63, 88]
    assert "bcr" in [j.value for j in reloaded.protocol.include_joins]

    assert reloaded.spatial.enable_stop_linear_referencing is True
    assert "EPSG:5070" not in reloaded.spatial.target_crs_list
    assert "EPSG:26917" in reloaded.spatial.target_crs_list
