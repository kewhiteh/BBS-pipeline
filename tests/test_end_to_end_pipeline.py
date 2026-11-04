"""End-to-End Integration Verification Suite for BBS-Pipeline v3.

Proves full integration across:
- Frozen Pydantic v2 JSON Contracts (guilds.json, protocol.json, spatial.json)
- Three-Tier Ingress Gate + Fixed-Width SpeciesList.txt Normalizer + TableRoleResolver
- Predicate-First Polars Ecological Engine (cancelled-2020 continuity, observer tenure,
  start-point boundary selection, extirpation-aware zero-filling, 10/50-stop
  harmonization, dynamic a-la-carte joins)
- Route start-point geometry, STRtree Polygon Overlays & Multi-CRS Vector/Tabular Exports
- Two-Way Streamlit Visual Workbench end-to-end execution
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import polars as pl
from shapely.geometry import Polygon, box
from streamlit.testing.v1 import AppTest

from bbs_pipeline.contracts import (
    ExportFormat,
    GuildProfileContract,
    GuildRegistryContract,
    IngressMode,
    JoinDimension,
    ObserverTenureMode,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.pipeline import run_bbs_pipeline, run_bbs_pipeline_from_dir


def _build_synthetic_raw_bbs_dir(raw_dir: Path) -> None:
    """Populate raw_dir with realistic nested USGS BBS zip archives and fixed-width SpeciesList.txt."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fixed-width SpeciesList.txt
    species_txt = (
        "USGS Patuxent Wildlife Research Center - North American Breeding Bird Survey\n"
        "Seq   AOU   English_Common_Name          French_Common_Name           Spanish_Common_Name          Order                Family               Genus           Species\n"
        "----- ----- ---------------------------- ---------------------------- ---------------------------- -------------------- -------------------- --------------- --------------------\n"
        "  610 07550 Wood Thrush                  Grive des bois               Zorzal maculado              Passeriformes        Turdidae             Hylocichla      mustelina\n"
        "  620 06740 Ovenbird                     Paruline couronnee           Reinita coronada             Passeriformes        Parulidae            Seiurus         aurocapilla\n"
        "  630 05930 Northern Cardinal            Cardinal rouge               Cardenal norteño             Passeriformes        Cardinalidae         Cardinalis      cardinalis\n"
    )
    (raw_dir / "SpeciesList.txt").write_text(species_txt, encoding="utf-8")

    # 2. Routes.zip
    routes_df = pl.DataFrame(
        {
            "CountryNum": [840, 840],
            "StateNum": [63, 63],
            "Route": [1, 2],
            "RouteName": ["RALEIGH_WOODS", "DURHAM_CREEK"],
            "Active": [1, 1],
            "Latitude": [35.7796, 35.9940],
            "Longitude": [-78.6382, -78.8986],
            "Stratum": [11, 11],
            "BCR": [29, 29],
            "RouteTypeID": [1, 1],
            "RouteTypeDetailID": [1, 1],
        }
    )
    routes_csv_bytes = routes_df.write_csv().encode("utf-8")
    with zipfile.ZipFile(raw_dir / "Routes.zip", "w") as zf:
        zf.writestr("routes.csv", routes_csv_bytes)

    # 3. Weather.zip
    # Route 63_001 surveyed in 2018, 2019, 2021, 2022 by veteran observer 101 (first year in 2017)
    # Route 63_002 surveyed only in 2018 (first year for observer 202) and 2019
    weather_df = pl.DataFrame(
        {
            "RouteDataID": [1000, 1001, 1002, 1003, 1004, 2001, 2002],
            "CountryNum": [840] * 7,
            "StateNum": [63] * 7,
            "Route": [1, 1, 1, 1, 1, 2, 2],
            "RPID": [101] * 7,
            "Year": [2017, 2018, 2019, 2021, 2022, 2018, 2019],
            "Month": [6] * 7,
            "Day": [10] * 7,
            "ObsN": [101, 101, 101, 101, 101, 202, 202],
            "TotalSpp": [25, 28, 30, 27, 29, 15, 18],
            "StartTemp": [65] * 7,
            "EndTemp": [78] * 7,
            "TempScale": ["F"] * 7,
            "StartWind": [1] * 7,
            "EndWind": [2] * 7,
            "StartSky": [0] * 7,
            "EndSky": [1] * 7,
            "StartTime": [530] * 7,
            "EndTime": [945] * 7,
            "Assistant": [0] * 7,
            "QualityCurrentID": [1] * 7,
            "RunType": [1] * 7,
        }
    )
    with zipfile.ZipFile(raw_dir / "Weather.zip", "w") as zf:
        zf.writestr("weather.csv", weather_df.write_csv().encode("utf-8"))

    # 4. Nested 50-Stop archive (50-StopData/NC_fifty.zip -> nc_fifty.csv)
    # Only record Wood Thrush (07550) in 2018, 2019, 2021 on Route 1 (extirpated in 2022 -> 0 count!)
    # Ovenbird (06740) detected only in 2019 (10 birds) and 2021 (20 birds) on Route 1
    rows = []
    # Wood Thrush (07550): 2018=6, 2019=8, 2021=4, 2022=absent (extirpated!)
    for rdata_id, yr, stop1_val in [(1001, 2018, 6), (1002, 2019, 8), (1003, 2021, 4)]:
        row = {
            "RouteDataID": rdata_id,
            "CountryNum": 840,
            "StateNum": 63,
            "Route": 1,
            "RPID": 101,
            "Year": yr,
            "AOU": 7550,
        }
        for s in range(1, 51):
            row[f"Stop{s}"] = stop1_val if s == 1 else 0
        rows.append(row)

    # Ovenbird (06740): 2019=10, 2021=20
    for rdata_id, yr, stop1_val in [(1002, 2019, 10), (1003, 2021, 20)]:
        row = {
            "RouteDataID": rdata_id,
            "CountryNum": 840,
            "StateNum": 63,
            "Route": 1,
            "RPID": 101,
            "Year": yr,
            "AOU": 6740,
        }
        for s in range(1, 51):
            row[f"Stop{s}"] = stop1_val if s == 1 else 0
        rows.append(row)

    fifty_df = pl.DataFrame(rows)
    inner_zip_path = raw_dir / "nc_fifty_inner.zip"
    with zipfile.ZipFile(inner_zip_path, "w") as inner_zf:
        inner_zf.writestr("nc_fifty.csv", fifty_df.write_csv().encode("utf-8"))
    inner_bytes = inner_zip_path.read_bytes()
    inner_zip_path.unlink()

    with zipfile.ZipFile(raw_dir / "50-StopData.zip", "w") as outer_zf:
        outer_zf.writestr("nc_fifty_inner.zip", inner_bytes)


def test_end_to_end_pipeline_zerofill_boundary_and_multicrs(tmp_path: Path) -> None:
    """Prove end-to-end execution across Tier 2 -> Tier 1 cache, zero-fill, boundary, multi-CRS."""
    raw_dir = tmp_path / "raw"
    cache_dir = tmp_path / "cache"
    spatial_dir = tmp_path / "spatial_inputs"
    output_dir = tmp_path / "output"
    spatial_dir.mkdir(parents=True, exist_ok=True)

    _build_synthetic_raw_bbs_dir(raw_dir)

    watersheds_gdf = gpd.GeoDataFrame(
        {
            "basin_name": ["Neuse_River_Basin", "Cape_Fear_Basin"],
            "geometry": [
                Polygon([(-79.0, 35.5), (-78.0, 35.5), (-78.0, 36.2), (-79.0, 36.2)]),
                Polygon([(-80.0, 34.5), (-79.0, 34.5), (-79.0, 35.4), (-80.0, 35.4)]),
            ],
        },
        crs="EPSG:4326",
    )
    watershed_path = spatial_dir / "nc_watersheds.parquet"
    watersheds_gdf.to_parquet(watershed_path)

    guilds = GuildRegistryContract(
        active_guild="whitcomb_interior_obligates",
        profiles={
            "whitcomb_interior_obligates": GuildProfileContract(
                citation="Whitcomb et al. (1981)",
                description="Forest interior obligates",
                aou_codes=["07550", "06740"],
            )
        },
    )

    # Run 1: Tier 2 (local raw) + 100% continuity over 2018-2022 (2020 not counted)
    protocol_full = ProtocolConfigContract(
        ingress_mode=IngressMode.AUTO_THREE_TIER,
        sanitized_cache_dir=cache_dir,
        raw_data_dir=raw_dir,
        year_range=(2018, 2022),
        min_route_continuity_ratio=1.0,  # Route 63_001 has 4/4 active years (2018,2019,2021,2022) -> 1.0!
        observer_tenure_mode=ObserverTenureMode.DROP_FIRST_YEAR_ON_ROUTE,
        temporal_routing_mode=TemporalRoutingMode.FIFTY_STOP_1997_PLUS,
        stop_aggregation=StopAggregationMode.TEN_STOP_BINS,
        enable_extirpation_zero_fill=True,
        include_joins=[JoinDimension.SPECIES, JoinDimension.ROUTES],  # Weather excluded!
    )

    spatial_cfg = SpatialExportConfigContract(
        enable_spatial_join=True,
        custom_overlay_paths={"watershed": watershed_path},
        target_crs_list=["EPSG:4326", "EPSG:5070", "EPSG:32119"],
        export_formats=[
            ExportFormat.GEOPARQUET,
            ExportFormat.GPKG,
            ExportFormat.PARQUET,
            ExportFormat.CSV,
        ],
        output_dir=output_dir,
    )

    res1 = run_bbs_pipeline(
        PipelineConfigBundle(guilds=guilds, protocol=protocol_full, spatial=spatial_cfg)
    )
    assert res1.funnel_metrics.ingress_tier_used == "tier2_local_raw"
    assert (
        res1.funnel_metrics.continuity_filtered_routes == 1
    )  # Only route 63_001 has 100% continuity
    assert res1.funnel_metrics.target_species_count == 2  # 07550 & 06740
    # 1 route * 4 active years (2018, 2019, 2021, 2022) * 2 species = 8 rows!
    assert res1.funnel_metrics.zero_filled_matrix_rows == 8
    assert res1.funnel_metrics.final_output_rows == 8

    obs_df1 = pl.read_parquet(output_dir / "bbs_observations.parquet")
    assert 2020 not in obs_df1["Year"].to_list()
    # Verify extirpation zero-fill in 2022 for Wood Thrush (07550)
    wt_2022 = obs_df1.filter((pl.col("AOU") == "07550") & (pl.col("Year") == 2022))
    assert wt_2022.height == 1
    assert wt_2022["route_total"].item() == 0
    assert wt_2022["is_zero_filled"].item() is True
    # Verify Weather columns were NOT joined (since Weather was omitted from include_joins)
    assert "StartTemp" not in obs_df1.columns
    assert "English_Common_Name" in obs_df1.columns

    # One start point per output row, reprojected per CRS, with the overlay attribute
    gdf_32119 = gpd.read_parquet(output_dir / "bbs_observations_epsg_32119.geoparquet")
    assert len(gdf_32119) == 8
    assert gdf_32119.crs.to_epsg() == 32119
    assert set(gdf_32119.geometry.geom_type) == {"Point"}
    assert (gdf_32119["overlay_watershed"] == "Neuse_River_Basin").all()

    # Run 2: Tier 1 cache hit + boundary around 63_001's start point only
    boundary_path = spatial_dir / "raleigh_box.gpkg"
    gpd.GeoDataFrame(geometry=[box(-78.7, 35.7, -78.6, 35.85)], crs="EPSG:4269").to_file(
        boundary_path, driver="GPKG"
    )
    protocol_boundary = protocol_full.model_copy(
        update={"min_route_continuity_ratio": 0.0, "boundary_path": boundary_path}
    )
    res2 = run_bbs_pipeline(
        PipelineConfigBundle(guilds=guilds, protocol=protocol_boundary, spatial=spatial_cfg)
    )
    assert res2.funnel_metrics.ingress_tier_used == "tier1_cache"
    obs_df2 = pl.read_parquet(output_dir / "bbs_observations.parquet")
    assert set(obs_df2["route_id"].to_list()) == {"63_001"}

    # Without the boundary, 63_002 (outside the box) also survives at 0.0 continuity
    res3 = run_bbs_pipeline(
        PipelineConfigBundle(
            guilds=guilds,
            protocol=protocol_boundary.model_copy(update={"boundary_path": None}),
            spatial=spatial_cfg,
        )
    )
    assert res3.funnel_metrics.continuity_filtered_routes == 2


def test_streamlit_tab5_end_to_end_execution(tmp_path: Path, monkeypatch) -> None:
    """Verify clicking 'Run BBS Pipeline' inside Tab 5 of ui/app.py executes the pipeline cleanly."""
    config_dir = tmp_path / "config"
    raw_dir = tmp_path / "raw"
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "out"
    _build_synthetic_raw_bbs_dir(raw_dir)

    bundle = PipelineConfigBundle(
        guilds=GuildRegistryContract(
            active_guild="whitcomb_interior_obligates",
            profiles={
                "whitcomb_interior_obligates": GuildProfileContract(
                    citation="Whitcomb (1981)",
                    description="Interior forest birds",
                    aou_codes=["07550"],
                )
            },
        ),
        protocol=ProtocolConfigContract(
            sanitized_cache_dir=cache_dir,
            raw_data_dir=raw_dir,
            year_range=(2018, 2022),
            min_route_continuity_ratio=1.0,
        ),
        spatial=SpatialExportConfigContract(
            enable_spatial_join=False,
            export_formats=[ExportFormat.PARQUET, ExportFormat.CSV],
            output_dir=out_dir,
        ),
    )
    bundle.save_to_dir(config_dir)

    monkeypatch.setenv("BBS_CONFIG_DIR", str(config_dir))
    app_path = Path(__file__).resolve().parents[1] / "ui" / "app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)
    assert not at.exception

    at.button(key="btn_run_pipeline").click().run(timeout=15)
    assert not at.exception
    assert (out_dir / "bbs_observations.parquet").exists()
    assert (out_dir / "bbs_observations.csv").exists()

    # Also verify run_bbs_pipeline_from_dir directly
    res = run_bbs_pipeline_from_dir(config_dir)
    assert res.funnel_metrics.final_output_rows == 4
