"""Comprehensive unit tests for the Polars Ecological Engine (`bbs_pipeline.engine`)."""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.contracts import (
    ProtocolConfigContract,
    SpeciesSelectionContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.engine import run_ecological_engine
from bbs_pipeline.stages.common import normalize_canonical_keys
from bbs_pipeline.stages.observations import harmonize_observations
from bbs_pipeline.stages.runs import flag_observer_first_year
from bbs_pipeline.stages.species import filter_target_species


def _build_synthetic_tables() -> dict[str, pl.DataFrame]:
    """Construct synthetic routes, weather, species, fifty_stop, and ten_stop tables."""
    routes = pl.DataFrame(
        {
            "CountryNum": [840, 840],
            "StateNum": [63, 63],
            "Route": [1, 2],
            "RouteName": ["ENO RIVER", "DUKE FOREST"],
            "Latitude": [36.07, 35.99],
            "Longitude": [-79.01, -78.94],
            "Stratum": [11, 11],
            "BCR": [29, 29],
        }
    )

    weather = pl.DataFrame(
        {
            "RouteDataID": [1001, 1002, 1003, 1004, 2001, 2002],
            "CountryNum": [840, 840, 840, 840, 840, 840],
            "StateNum": [63, 63, 63, 63, 63, 63],
            "Route": [1, 1, 1, 1, 2, 2],
            "Year": [2018, 2019, 2021, 2022, 2018, 2019],
            "ObsN": [101, 101, 101, 101, 202, 202],
            "RunType": [1, 1, 1, 1, 1, 1],
            "QualityCurrentID": [1, 1, 1, 1, 1, 1],
            "StartTemp": [65, 66, 64, 67, 68, 70],
            "EndTemp": [78, 80, 77, 81, 82, 84],
            "StartWind": [1, 0, 1, 2, 1, 1],
            "EndWind": [2, 1, 2, 2, 2, 3],
        }
    )

    species = pl.DataFrame(
        {
            "AOU": [7550, 6740, 5930],
            "English_Common_Name": ["Wood Thrush", "Ovenbird", "Northern Cardinal"],
            "Order": ["Passeriformes", "Passeriformes", "Passeriformes"],
            "Family": ["Turdidae", "Parulidae", "Cardinalidae"],
            "Genus": ["Hylocichla", "Seiurus", "Cardinalis"],
            "Species": ["mustelina", "aurocapilla", "cardinalis"],
        }
    )

    # Route 63_001 has Wood Thrush (07550) in 2018, 2019, 2021, 2022
    # Route 63_001 has Ovenbird (06740) ONLY in 2018 and 2019 (extirpated in 2021, 2022!)
    stop_data: dict[str, list[int]] = {
        "RouteDataID": [1001, 1002, 1003, 1004, 1001, 1002],
        "CountryNum": [840, 840, 840, 840, 840, 840],
        "StateNum": [63, 63, 63, 63, 63, 63],
        "Route": [1, 1, 1, 1, 1, 1],
        "Year": [2018, 2019, 2021, 2022, 2018, 2019],
        "AOU": [7550, 7550, 7550, 7550, 6740, 6740],
    }
    for i in range(1, 51):
        if i == 1:
            stop_data[f"Stop{i}"] = [4, 6, 10, 8, 3, 2]
        elif i == 15:
            stop_data[f"Stop{i}"] = [2, 4, 6, 4, 1, 0]
        else:
            stop_data[f"Stop{i}"] = [0, 0, 0, 0, 0, 0]

    fifty_stop = pl.DataFrame(stop_data)

    ten_stop = pl.DataFrame(
        {
            "RouteDataID": [9001, 9002],
            "CountryNum": [840, 840],
            "StateNum": [63, 63],
            "Route": [1, 1],
            "Year": [1990, 1995],
            "AOU": [7550, 7550],
            "Count10": [3, 5],
            "Count20": [2, 1],
            "Count30": [1, 2],
            "Count40": [0, 1],
            "Count50": [1, 0],
            "StopTotal": [7, 9],
        }
    )

    return {
        "routes": routes,
        "weather": weather,
        "species": species,
        "fifty_stop": fifty_stop,
        "ten_stop": ten_stop,
    }


def _build_two_species_guilds() -> SpeciesSelectionContract:
    return SpeciesSelectionContract(aous=["07550", "06740"])


def test_normalize_canonical_keys() -> None:
    raw = pl.DataFrame(
        {"StateNum": [6, 63], "Route": [7, 12], "AOU": [7550, 6740], "Year": ["2019", "2021"]}
    )
    norm = normalize_canonical_keys(raw)
    assert norm["route_id"].to_list() == ["06_007", "63_012"]
    assert norm["AOU"].to_list() == ["07550", "06740"]
    assert norm["Year"].dtype == pl.Int64


def test_extirpation_aware_zero_filling() -> None:
    """Prove a route surveyed in a valid year with 0 observations of a target AOU produces an explicit 0 row."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()
    protocol = ProtocolConfigContract(
        year_range=(2018, 2022),
        routes=["63_001"],
        enable_extirpation_zero_fill=True,
    )

    res = run_ecological_engine(tables, guilds, protocol)
    df = res.observations_df

    # 4 active years (2018, 2019, 2021, 2022) x 2 target species = 8 rows
    assert df.height == 8
    assert res.funnel_metrics.zero_filled_matrix_rows == 8

    # Ovenbird (06740) was NOT observed in 2021 or 2022 on route 63_001 -> must be zero-filled
    ovenbird_2021 = df.filter((pl.col("AOU") == "06740") & (pl.col("Year") == 2021))
    assert ovenbird_2021.height == 1
    assert ovenbird_2021["route_total"].item() == 0
    assert ovenbird_2021["is_zero_filled"].item() is True

    # Wood Thrush (07550) in 2021 had Stop1=10 + Stop15=6 = 16 -> not zero-filled
    woth_2021 = df.filter((pl.col("AOU") == "07550") & (pl.col("Year") == 2021))
    assert woth_2021["route_total"].item() == 16
    assert woth_2021["is_zero_filled"].item() is False


def test_cancelled_2020_survey_does_not_count_against_continuity() -> None:
    """Prove 2020 is left out of the continuity denominator: 4/4 years over 2018-2022 = 1.0."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()
    protocol = ProtocolConfigContract(
        year_range=(2018, 2022),
        min_route_continuity_ratio=1.0,
    )

    res = run_ecological_engine(tables, guilds, protocol)
    df = res.observations_df

    # Only route 63_001 has all 4 active years (2018, 2019, 2021, 2022); 63_002 only has 2/4 (0.5)
    assert res.funnel_metrics.continuity_filtered_routes == 1
    assert set(df["route_id"].to_list()) == {"63_001"}
    assert 2020 not in df["Year"].to_list()
    assert df["route_continuity_ratio"].unique().to_list() == [pytest.approx(1.0)]


def test_observer_first_year_is_per_route() -> None:
    """is_first_year marks an observer's earliest year on *that route*; dropping removes it."""
    weather = pl.DataFrame(
        {
            "StateNum": [63] * 8,
            "Route": [1, 1, 1, 1, 1, 2, 2, 2],
            "Year": [2015, 2016, 2017, 2018, 2019, 2016, 2017, 2019],
            # Obs 10: route 1 in 2015-2016, then route 2 from 2016: first year on route 2
            #         is 2016 even though they started in 2015 on route 1
            # Obs 30: route 1 from 2017; Obs 40: route 2 from 2019
            "ObsN": [10, 10, 30, 30, 30, 10, 10, 40],
            "RunType": [1] * 8,
            "QualityCurrentID": [1] * 8,
        }
    )

    flagged = flag_observer_first_year(
        weather, ProtocolConfigContract(year_range=(2015, 2019))
    ).sort(["Route", "Year"])
    assert flagged.height == 8
    first = flagged.filter(pl.col("is_first_year")).select("Route", "ObsN", "Year").rows()
    assert sorted(first) == [(1, 10, 2015), (1, 30, 2017), (2, 10, 2016), (2, 40, 2019)]

    dropped = flag_observer_first_year(
        weather, ProtocolConfigContract(year_range=(2015, 2019), drop_observer_first_year=True)
    )
    assert sorted(dropped.select("Route", "Year").rows()) == [
        (1, 2016),
        (1, 2018),
        (1, 2019),
        (2, 2017),
    ]
    assert not dropped["is_first_year"].any()


def test_hybrid_harmonized_10_stop() -> None:
    """Prove 50-stop data (1997+) is summed into 5 10-stop bins and concatenated with 10-stop data (<1997)."""
    tables = _build_synthetic_tables()
    protocol = ProtocolConfigContract(
        year_range=(1990, 2022),
        temporal_routing_mode=TemporalRoutingMode.HYBRID_HARMONIZED_10_STOP,
        stop_aggregation=StopAggregationMode.TEN_STOP_BINS,
    )

    harm_df = harmonize_observations(tables, protocol).collect().sort(["Year", "AOU"])
    expected_cols = [
        "RouteDataID",
        "route_id",
        "Year",
        "AOU",
        "Count10",
        "Count20",
        "Count30",
        "Count40",
        "Count50",
        "route_total",
    ]
    assert harm_df.columns == expected_cols

    # Check 1990 10-stop row
    row_1990 = harm_df.filter((pl.col("Year") == 1990) & (pl.col("AOU") == "07550"))
    assert row_1990["Count10"].item() == 3
    assert row_1990["route_total"].item() == 7

    # Check 2018 50-stop collapsed row (Stop1=4 -> Count10=4, Stop15=2 -> Count20=2, route_total=6)
    row_2018 = harm_df.filter((pl.col("Year") == 2018) & (pl.col("AOU") == "07550"))
    assert row_2018["Count10"].item() == 4
    assert row_2018["Count20"].item() == 2
    assert row_2018["Count30"].item() == 0
    assert row_2018["route_total"].item() == 6


def test_species_selectors_combine_as_union() -> None:
    """A species matching ANY selector is kept; names and families match case-insensitively."""
    species = _build_synthetic_tables()["species"]

    by_name = filter_target_species(
        species, SpeciesSelectionContract(common_names=["northern cardinal"])
    )
    assert by_name["AOU"].to_list() == ["05930"]

    union = filter_target_species(
        species, SpeciesSelectionContract(families=["Parulidae"], aous=["07550"])
    )
    assert sorted(union["AOU"].to_list()) == ["06740", "07550"]

    everything = filter_target_species(species, SpeciesSelectionContract())
    assert everything.height == 3


def test_species_selector_that_matches_nothing_raises() -> None:
    species = _build_synthetic_tables()["species"]

    with pytest.raises(ValueError, match=r"families='Parulidea'"):
        filter_target_species(
            species, SpeciesSelectionContract(families=["Parulidea"], aous=["07550"])
        )
