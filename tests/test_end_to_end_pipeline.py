"""End-to-End Integration Verification Suite for BBS-Pipeline v3.

Proves full integration across:
- TOML run profiles with --set overrides
- Release sources: typed loading of release-shaped files (padded values, NULL tokens,
  cp1252, nested zips, fixed-width SpeciesList.txt) and the release-tied parquet cache
- Predicate-First Polars Ecological Engine (cancelled-2020 continuity, observer tenure,
  start-point boundary selection, extirpation-aware zero-filling, 10/50-stop
  harmonization, dynamic a-la-carte joins)
- Route start-point geometry, STRtree Polygon Overlays & Multi-CRS Vector/Tabular Exports
- The Streamlit profile editor's Run button
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Polygon, box
from streamlit.testing.v1 import AppTest

from bbs_pipeline.cli import main as cli_main
from bbs_pipeline.config import load_profile
from bbs_pipeline.pipeline import run_profile
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


def _write_profile(path: Path, raw_dir: Path, cache_dir: Path, output: Path) -> Path:
    path.write_text(
        f"""
[source]
release = "{TEST_DOI}"
raw_dir = "{raw_dir}"
cache_dir = "{cache_dir}"
offline = true

[runs]
start_year = 2018
end_year = 2022
min_continuity = 1.0   # 63_001 has 4/4 active years (2018, 2019, 2021, 2022)
observer_tenure = "drop_first_year_on_route"

[observations]
stops = "ten_stop_bins"

[species]
aous = ["07550", "06740"]

[joins]
weather = []    # weather excluded
observer = []

[output]
path = "{output}"
""",
        encoding="utf-8",
    )
    return path


def test_end_to_end_pipeline_zerofill_boundary_and_multicrs(tmp_path: Path) -> None:
    """Profile -> release -> cache -> outputs; zero-fill, boundary, vector CRS, overlays."""
    raw_dir, cache_dir, out_dir = tmp_path / "raw", tmp_path / "cache", tmp_path / "out"
    spatial_dir = tmp_path / "spatial_inputs"
    spatial_dir.mkdir(parents=True, exist_ok=True)
    _build_synthetic_raw_bbs_dir(raw_dir)
    profile_path = _write_profile(
        tmp_path / "project.toml", raw_dir, cache_dir, out_dir / "obs.parquet"
    )

    watershed_path = spatial_dir / "nc_watersheds.parquet"
    gpd.GeoDataFrame(
        {"basin_name": ["Neuse_River_Basin", "Cape_Fear_Basin"]},
        geometry=[
            Polygon([(-79.0, 35.5), (-78.0, 35.5), (-78.0, 36.2), (-79.0, 36.2)]),
            Polygon([(-80.0, 34.5), (-79.0, 34.5), (-79.0, 35.4), (-80.0, 35.4)]),
        ],
        crs="EPSG:4326",
    ).to_parquet(watershed_path)

    # Run 1: cache built from the release
    res1 = run_profile(load_profile(profile_path))
    assert res1.funnel_metrics.data_source == "built_from_release"
    assert res1.funnel_metrics.continuity_filtered_routes == 1
    assert res1.funnel_metrics.target_species_count == 2
    # 1 route * 4 active years * 2 species = 8 rows
    assert res1.funnel_metrics.final_output_rows == 8

    obs = pl.read_parquet(out_dir / "obs.parquet")
    assert obs.columns == res1.output_columns
    assert 2020 not in obs["Year"].to_list()
    wt_2022 = obs.filter((pl.col("AOU") == "07550") & (pl.col("Year") == 2022))
    assert wt_2022["route_total"].item() == 0
    assert wt_2022["is_zero_filled"].item() is True
    assert "StartTemp" not in obs.columns
    assert "English_Common_Name" in obs.columns

    manifest = json.loads((out_dir / "obs.parquet.manifest.json").read_text())
    assert manifest["release"]["doi"] == TEST_DOI
    assert manifest["output"]["rows"] == 8
    assert manifest["profile"]["species"]["aous"] == ["07550", "06740"]
    assert manifest["stage_counts"]["routes_selected"] == 2

    # Same profile as a projected GeoParquet with an overlay, via --set
    res_vec = run_profile(
        load_profile(
            profile_path,
            [
                f'output.path="{out_dir / "obs.geoparquet"}"',
                "output.crs=EPSG:32119",
                f'output.overlays={{ watershed = "{watershed_path}" }}',
            ],
        )
    )
    gdf = gpd.read_parquet(out_dir / "obs.geoparquet")
    assert res_vec.funnel_metrics.data_source == "cache"
    assert list(gdf.columns) == res_vec.output_columns
    assert gdf.crs.to_epsg() == 32119
    assert set(gdf.geometry.geom_type) == {"Point"}
    assert (gdf["overlay_watershed"] == "Neuse_River_Basin").all()

    # Boundary around 63_001's start point only
    boundary_path = spatial_dir / "raleigh_box.gpkg"
    gpd.GeoDataFrame(geometry=[box(-78.7, 35.7, -78.6, 35.85)], crs="EPSG:4269").to_file(
        boundary_path, driver="GPKG"
    )
    base = ["runs.min_continuity=0.0"]
    res2 = run_profile(load_profile(profile_path, [*base, f'routes.boundary="{boundary_path}"']))
    assert res2.funnel_metrics.routes_selected == 1
    assert set(pl.read_parquet(out_dir / "obs.parquet")["route_id"].to_list()) == {"63_001"}

    # Without the boundary, 63_002 (outside the box) also survives at 0.0 continuity
    res3 = run_profile(load_profile(profile_path, base))
    assert res3.funnel_metrics.continuity_filtered_routes == 2


def test_cli_inspect_writes_nothing_and_run_writes_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_dir, cache_dir, out = tmp_path / "raw", tmp_path / "cache", tmp_path / "out" / "o.csv"
    _build_synthetic_raw_bbs_dir(raw_dir)
    profile = str(_write_profile(tmp_path / "p.toml", raw_dir, cache_dir, out))

    assert cli_main(["inspect", "--config", profile]) == 0
    printed = capsys.readouterr().out
    assert "routes after route filters" in printed
    assert "English_Common_Name" in printed
    assert not out.exists()

    assert cli_main(["run", "--config", profile, "--set", 'species.aous=["07550"]']) == 0
    assert pl.read_csv(out)["AOU"].unique().to_list() == [7550]

    assert cli_main(["validate", "--config", profile, "--set", "runs.start_yeer=2000"]) == 2
    assert "runs.start_yeer: unknown key" in capsys.readouterr().err


def test_streamlit_run_button_executes_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, cache_dir, out = tmp_path / "raw", tmp_path / "cache", tmp_path / "out" / "o.parquet"
    _build_synthetic_raw_bbs_dir(raw_dir)
    profile_path = _write_profile(tmp_path / "p.toml", raw_dir, cache_dir, out)
    monkeypatch.setenv("BBS_PROFILE", str(profile_path))

    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "ui" / "app.py"))
    at.run(timeout=15)
    assert not at.exception

    at.button(key="btn_run_pipeline").click().run(timeout=15)
    assert not at.exception
    assert pl.read_parquet(out).height == 8
