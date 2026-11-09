"""Long/wide output, the calendar-year rolling mean, and the 50-stop start-year guard."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.config import ProfileError, load_profile, profile_from_data
from bbs_pipeline.contracts import ProtocolConfigContract, SpeciesSelectionContract
from bbs_pipeline.engine import run_ecological_engine
from bbs_pipeline.pipeline import run_profile
from bbs_pipeline.shape import to_wide
from bbs_pipeline.sources.loader import load_sources
from bbs_pipeline.stages.observations import add_rolling_mean
from tests.release_fixtures import (
    TEST_DOI,
    fifty_frame,
    routes_frame,
    species_frame,
    ten_frame,
    weather_frame,
    write_release,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


INDEX = ["route_id", "RouteName", "route_continuity_ratio", "AOU", "English_Common_Name"]


def _long() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "RouteDataID": [1, 2, 3, 1, 2, 3],
            "route_id": ["63_001", "63_001", "63_002", "63_001", "63_001", "63_002"],
            "Year": [2019, 2021, 2021, 2019, 2021, 2021],
            "AOU": ["07550", "07550", "07550", "06740", "06740", "06740"],
            "route_total": [3, 0, 5, 1, 2, 0],
            "is_zero_filled": [False, True, False, False, False, True],
            "RouteName": ["A", "A", "B", "A", "A", "B"],
            "English_Common_Name": ["Wood Thrush"] * 3 + ["Ovenbird"] * 3,
            "ObsN": [9, 9, 8, 9, 9, 8],
        }
    )


def test_wide_has_one_row_per_route_and_species_and_a_column_per_year() -> None:
    wide = to_wide(_long(), "route_total", index=INDEX)

    assert wide.columns == [
        "route_id",
        "RouteName",
        "AOU",
        "English_Common_Name",
        "route_total_2019",
        "route_total_2021",
    ]
    row = wide.filter((pl.col("route_id") == "63_002") & (pl.col("AOU") == "07550")).row(
        0, named=True
    )
    assert row["route_total_2019"] is None  # not surveyed
    assert row["route_total_2021"] == 5
    assert wide.height == 4


def test_wide_value_must_be_an_output_column() -> None:
    with pytest.raises(KeyError, match="rolling_mean"):
        to_wide(_long(), "rolling_mean", index=INDEX)


def test_rolling_mean_uses_calendar_years_not_rows() -> None:
    lf = pl.LazyFrame(
        {
            "route_id": ["a"] * 4,
            "AOU": ["07550"] * 4,
            "Year": [2010, 2011, 2014, 2016],
            "route_total": [2, 4, 6, 8],
        }
    )

    out = add_rolling_mean(lf, 5).collect().sort("Year")

    assert out["rolling_mean"].to_list() == [2.0, 3.0, 4.0, 7.0]
    assert out["rolling_n_years"].to_list() == [1, 2, 3, 2]


def _release(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    write_release(
        raw,
        routes=routes_frame([(63, 1, "R", 35.9, -79.0)]),
        weather=weather_frame(
            [(900, 63, 1, 1995, 5, 1), (1001, 63, 1, 2019, 5, 1), (1002, 63, 1, 2021, 5, 1)]
        ),
        species=species_frame([("7550", "Wood Thrush", "Passeriformes", "Turdidae")]),
        fifty_stop=fifty_frame(
            [(1001, 63, 1, 2019, "7550", {1: 4}), (1002, 63, 1, 2021, "7550", {1: 8})]
        ),
        ten_stop=ten_frame(
            [
                (900, 63, 1, 1995, "7550", [2, 0, 0, 0, 0], 1),
                (1001, 63, 1, 2019, "7550", [4, 0, 0, 0, 0], 1),
                (1002, 63, 1, 2021, "7550", [8, 0, 0, 0, 0], 1),
            ]
        ),
    )
    return raw


def test_fifty_stop_before_1997_is_rejected_instead_of_zero_filled(tmp_path: Path) -> None:
    raw = _release(tmp_path)
    protocol = ProtocolConfigContract(
        raw_data_dir=raw, sanitized_cache_dir=tmp_path / "c", year_range=(1995, 2021)
    )
    tables = load_sources(protocol.model_copy(update={"sciencebase_doi": TEST_DOI})).tables

    with pytest.raises(ValueError, match="50-stop data starts in 1997"):
        run_ecological_engine(tables, SpeciesSelectionContract(), protocol)
    with pytest.raises(ProfileError, match="starts in 1997 but runs.start_year is 1995"):
        profile_from_data({"runs": {"start_year": 1995}, "observations": {"source": "50_stop"}})


def test_profile_runs_wide_with_rolling_mean(tmp_path: Path) -> None:
    raw = _release(tmp_path)
    out = tmp_path / "out" / "w.csv"
    profile = profile_from_data(
        {
            "source": {"release": TEST_DOI, "raw_dir": str(raw), "cache_dir": str(tmp_path / "c")},
            "runs": {"start_year": 1995, "end_year": 2021},
            "observations": {"source": "10_stop", "rolling_years": 3},
            "joins": {"routes": [], "weather": [], "observer": [], "species": []},
            "output": {"path": str(out), "shape": "wide", "wide_value": "rolling_mean"},
        }
    )

    result = run_profile(profile)

    wide = pl.read_csv(out)
    assert wide.columns == result.output_columns
    assert result.output_rows == 1
    row = wide.row(0, named=True)
    # 2021 window = 2019-2021: surveyed 2019 (4) and 2021 (8)
    assert row["rolling_mean_2021"] == pytest.approx(6.0)
    assert row["rolling_mean_1995"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"observations": {"rolling_years": 1}}, "0 \\(off\\) or at least 2"),
        (
            {"observations": {"rolling_years": 5, "zero_fill": False}},
            "rolling_years needs observations.zero_fill",
        ),
        ({"output": {"wide_value": "rolling_mean"}}, "needs observations.rolling_years"),
        ({"output": {"shape": "tall"}}, "output.shape"),
    ],
)
def test_rolling_and_shape_settings_are_validated(data: dict, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        profile_from_data(data)


@pytest.mark.parametrize("name", ["wood_thrush_story_map.toml", "ghy5812_guilds.toml"])
def test_project_profiles_are_valid(name: str) -> None:
    load_profile(EXAMPLES / name)
