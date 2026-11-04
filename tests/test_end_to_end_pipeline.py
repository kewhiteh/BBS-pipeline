"""End-to-End Integration Verification Suite for BBS-Pipeline v3.

Proves full integration across:
- Frozen Pydantic v2 JSON Contracts (guilds.json, protocol.json, spatial.json)
- Release sources: typed loading of release-shaped files (padded values, NULL tokens,
  cp1252, nested zips, fixed-width SpeciesList.txt) and the release-tied parquet cache
- Predicate-First Polars Ecological Engine (cancelled-2020 continuity, observer tenure,
  start-point boundary selection, extirpation-aware zero-filling, 10/50-stop
  harmonization, dynamic a-la-carte joins)
- Route start-point geometry, STRtree Polygon Overlays & Multi-CRS Vector/Tabular Exports
- Two-Way Streamlit Visual Workbench end-to-end execution
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
from shapely.geometry import Polygon, box
from streamlit.testing.v1 import AppTest

from bbs_pipeline.contracts import (
    ExportFormat,
    GuildProfileContract,
    GuildRegistryContract,
    JoinDimension,
    ObserverTenureMode,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.pipeline import run_bbs_pipeline, run_bbs_pipeline_from_dir
from tests.release_fixtures import (
    TEST_DOI,
    fifty_frame,
    routes_frame,
    weather_frame,
    write_release,
)

SPECIES_TXT = (
    "USGS Patuxent Wildlife Research Center - North American Breeding Bird Survey\n"
    "Seq   AOU   English_Common_Name          French_Common_Name           Spanish_Common_Name          Order                Family               Genus           Species\n"
    "----- ----- ---------------------------- ---------------------------- ---------------------------- -------------------- -------------------- --------------- --------------------\n"
    "  610 07550 Wood Thrush                  Grive des bois               Zorzal maculado              Passeriformes        Turdidae             Hylocichla      mustelina\n"
    "  620 06740 Ovenbird                     Paruline couronnee           Reinita coronada             Passeriformes        Parulidae            Seiurus         aurocapilla\n"
    "  630 05930 Northern Cardinal            Cardinal rouge               Cardenal norteño             Passeriformes        Cardinalidae         Cardinalis      cardinalis\n"
)


def _build_synthetic_raw_bbs_dir(raw_dir: Path) -> None:
    """Write a release-shaped directory (with release.json) for two NC routes."""
    # Route 63_001 surveyed in 2017-2022 (no 2020) by observer 101 (first year 2017);
    # route 63_002 surveyed in 2018 (observer 202's first year) and 2019.
    weather = weather_frame(
        [
            (1000, 63, 1, 2017, 101, 1),
            (1001, 63, 1, 2018, 101, 1),
            (1002, 63, 1, 2019, 101, 1),
            (1003, 63, 1, 2021, 101, 1),
            (1004, 63, 1, 2022, 101, 1),
            (2001, 63, 2, 2018, 202, 1),
            (2002, 63, 2, 2019, 202, 1),
        ]
    )
    # Wood Thrush (07550): 2018=6, 2019=8, 2021=4, absent in 2022 (zero-filled).
    # Ovenbird (06740): 2019=10, 2021=20.
    fifty = fifty_frame(
        [
            (1001, 63, 1, 2018, "7550", {1: 6}),
            (1002, 63, 1, 2019, "7550", {1: 8}),
            (1003, 63, 1, 2021, "7550", {1: 4}),
            (1002, 63, 1, 2019, "6740", {1: 10}),
            (1003, 63, 1, 2021, "6740", {1: 20}),
        ]
    )
    write_release(
        raw_dir,
        routes=routes_frame(
            [
                (63, 1, "RALEIGH_WOODS", 35.7796, -78.6382),
                (63, 2, "DURHAM_CREEK", 35.9940, -78.8986),
            ]
        ),
        weather=weather,
        species_txt=SPECIES_TXT,
        fifty_stop=fifty,
        nest_fifty_zip=True,
    )


def test_end_to_end_pipeline_zerofill_boundary_and_multicrs(tmp_path: Path) -> None:
    """Prove end-to-end execution: release -> cache -> cache hit, zero-fill, boundary, multi-CRS."""
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

    # Run 1: cache built from the release + 100% continuity over 2018-2022 (2020 not counted)
    protocol_full = ProtocolConfigContract(
        sanitized_cache_dir=cache_dir,
        sciencebase_doi=TEST_DOI,
        offline=True,
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
    assert res1.funnel_metrics.ingress_tier_used == "built_from_release"
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

    # Run 2: cache hit + boundary around 63_001's start point only
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
    assert res2.funnel_metrics.ingress_tier_used == "cache"
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
            sciencebase_doi=TEST_DOI,
            offline=True,
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
