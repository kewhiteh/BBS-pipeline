"""Comprehensive unit tests for the Polars Ecological Engine (`bbs_pipeline.engine`)."""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.contracts import (
    Covid2020Mode,
    GuildProfileContract,
    GuildRegistryContract,
    JoinDimension,
    ObserverTenureMode,
    ProtocolConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.engine import (
    compute_observer_tenure,
    filter_target_species,
    harmonize_observations,
    normalize_canonical_keys,
    run_ecological_engine,
)


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


def _build_two_species_guilds() -> GuildRegistryContract:
    return GuildRegistryContract(
        active_guild="whitcomb_test",
        profiles={
            "whitcomb_test": GuildProfileContract(
                citation="Whitcomb et al. (1981)",
                description="Test interior obligates",
                aou_codes=["07550", "06740"],
                lump_subspecies_and_hybrids=True,
            )
        },
    )


def test_normalize_canonical_keys() -> None:
    raw = pl.DataFrame({"StateNum": [6, 63], "Route": [7, 12], "AOU": [7550, 6740], "Year": ["2019", "2021"]})
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
        covid_2020_mode=Covid2020Mode.EXCLUDE_GAP,
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

    # Guild rollup in 2021 should have abundance=16, species_richness=1, occupancy=0.5
    rollup_2021 = res.guild_rollup_df.filter(pl.col("Year") == 2021)
    assert rollup_2021["guild_total_abundance"].item() == 16
    assert rollup_2021["species_richness"].item() == 1
    assert rollup_2021["target_species_count"].item() == 2
    assert rollup_2021["guild_occupancy_rate"].item() == pytest.approx(0.5)


def test_covid_2020_exclude_gap_and_continuity() -> None:
    """Prove exclude_gap yields 1.0 (4/4) continuity over 2018-2022 and no 2020 rows."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()
    protocol = ProtocolConfigContract(
        year_range=(2018, 2022),
        covid_2020_mode=Covid2020Mode.EXCLUDE_GAP,
        min_route_continuity_ratio=1.0,
    )

    res = run_ecological_engine(tables, guilds, protocol)
    df = res.observations_df

    # Only route 63_001 has all 4 active years (2018, 2019, 2021, 2022); 63_002 only has 2/4 (0.5)
    assert res.funnel_metrics.continuity_filtered_routes == 1
    assert set(df["route_id"].to_list()) == {"63_001"}
    assert 2020 not in df["Year"].to_list()
    assert df["route_continuity_ratio"].unique().to_list() == [pytest.approx(1.0)]
    assert all(val is False for val in df["is_covid_imputed"].to_list())


def test_covid_2020_substitute_2019() -> None:
    """Prove substitute_2019 clones 2019 rows into 2020 with is_covid_imputed == True."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()
    protocol = ProtocolConfigContract(
        year_range=(2018, 2022),
        routes=["63_001"],
        covid_2020_mode=Covid2020Mode.SUBSTITUTE_2019,
    )

    res = run_ecological_engine(tables, guilds, protocol)
    df = res.observations_df

    rows_2019 = df.filter(pl.col("Year") == 2019).sort("AOU")
    rows_2020 = df.filter(pl.col("Year") == 2020).sort("AOU")

    assert rows_2020.height == 2
    assert rows_2020["route_total"].to_list() == rows_2019["route_total"].to_list()
    assert rows_2020["is_covid_imputed"].to_list() == [True, True]


def test_covid_2020_interpolate_2019_2021() -> None:
    """Prove interpolate_2019_2021 computes arithmetic mean of 2019 and 2021 per (route_id, AOU)."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()
    protocol = ProtocolConfigContract(
        year_range=(2018, 2022),
        routes=["63_001"],
        covid_2020_mode=Covid2020Mode.INTERPOLATE_2019_2021,
    )

    res = run_ecological_engine(tables, guilds, protocol)
    df = res.observations_df

    # For Wood Thrush (07550): 2019 route_total = 10 (6+4), 2021 route_total = 16 (10+6) -> 2020 = 13.0
    woth_2020 = df.filter((pl.col("AOU") == "07550") & (pl.col("Year") == 2020))
    assert woth_2020.height == 1
    assert woth_2020["route_total"].item() == pytest.approx(13.0)
    assert woth_2020["is_covid_imputed"].item() is True
    assert woth_2020["is_zero_filled"].item() is False

    # For Ovenbird (06740): 2019 route_total = 2, 2021 route_total = 0 -> 2020 = 1.0
    oven_2020 = df.filter((pl.col("AOU") == "06740") & (pl.col("Year") == 2020))
    assert oven_2020.height == 1
    assert oven_2020["route_total"].item() == pytest.approx(1.0)
    assert oven_2020["is_covid_imputed"].item() is True


def test_observer_tenure_modes() -> None:
    """Prove observer tenure modes drop first-year runs, novices, and non-veterans accurately."""
    weather = pl.DataFrame(
        {
            "StateNum": [63] * 8,
            "Route": [1] * 8,
            "Year": [2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023],
            # Obs 10: 1 year total (novice, first year on route)
            # Obs 20: 2 years total (intermediate: 2016 is first year, 2017 is second year)
            # Obs 30: 5 years total (veteran: 2018 is first year, 2019..2023 are subsequent)
            "ObsN": [10, 20, 20, 30, 30, 30, 30, 30],
            "RunType": [1] * 8,
            "QualityCurrentID": [1] * 8,
        }
    )

    p_all = ProtocolConfigContract(
        year_range=(2015, 2023),
        observer_tenure_mode=ObserverTenureMode.INCLUDE_ALL,
        observer_min_years=2,
        veteran_min_years=5,
    )
    df_all = compute_observer_tenure(weather, p_all)
    assert df_all.height == 8

    p_no_first = ProtocolConfigContract(
        year_range=(2015, 2023),
        observer_tenure_mode=ObserverTenureMode.DROP_FIRST_YEAR_ON_ROUTE,
        observer_min_years=2,
        veteran_min_years=5,
    )
    df_no_first = compute_observer_tenure(weather, p_no_first)
    # Drops 2015 (Obs 10 first yr), 2016 (Obs 20 first yr), 2018 (Obs 30 first yr)
    assert df_no_first["Year"].to_list() == [2017, 2019, 2021, 2022, 2023]
    assert all(val is False for val in df_no_first["is_first_year"].to_list())

    p_no_novice = ProtocolConfigContract(
        year_range=(2015, 2023),
        observer_tenure_mode=ObserverTenureMode.DROP_NOVICE_OBSERVERS,
        observer_min_years=3,  # Makes Obs 20 (2 yrs) a novice!
        veteran_min_years=5,
    )
    df_no_novice = compute_observer_tenure(weather, p_no_novice)
    # Obs 10 (1 yr) and Obs 20 (2 yrs < 3) are novice; Obs 30 first year (2018) also dropped
    assert df_no_novice["Year"].to_list() == [2019, 2021, 2022, 2023]
    assert set(df_no_novice["tenure_tier"].to_list()) == {"veteran"}

    p_vet = ProtocolConfigContract(
        year_range=(2015, 2023),
        observer_tenure_mode=ObserverTenureMode.VETERAN_ONLY,
        observer_min_years=2,
        veteran_min_years=5,
    )
    df_vet = compute_observer_tenure(weather, p_vet)
    assert set(df_vet["ObsN"].to_list()) == {30}
    assert df_vet.height == 5


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


def test_a_la_carte_include_joins_and_rolling_window() -> None:
    """Prove unrequested tables in include_joins are never joined onto observations_df."""
    tables = _build_synthetic_tables()
    guilds = _build_two_species_guilds()

    # Request ONLY species join, with 2-year rolling window
    protocol_sp_only = ProtocolConfigContract(
        year_range=(2018, 2022),
        routes=["63_001"],
        rolling_window_years=2,
        include_joins=[JoinDimension.SPECIES],
    )
    res_sp = run_ecological_engine(tables, guilds, protocol_sp_only)
    cols_sp = set(res_sp.observations_df.columns)

    assert "English_Common_Name" in cols_sp
    assert "rolling_mean_abundance" in cols_sp
    # Routes, Weather, and BCR columns MUST NOT appear
    assert "RouteName" not in cols_sp
    assert "Latitude" not in cols_sp
    assert "StartTemp" not in cols_sp
    assert "ObsN" not in cols_sp
    assert "BCR" not in cols_sp
    assert res_sp.active_joins == ["species"]

    # Request ONLY BCR join
    protocol_bcr_only = ProtocolConfigContract(
        year_range=(2018, 2022),
        routes=["63_001"],
        include_joins=[JoinDimension.BCR],
    )
    res_bcr = run_ecological_engine(tables, guilds, protocol_bcr_only)
    cols_bcr = set(res_bcr.observations_df.columns)
    assert "BCR" in cols_bcr
    assert "RouteName" not in cols_bcr
    assert "English_Common_Name" not in cols_bcr
    assert "StartTemp" not in cols_bcr
    assert res_bcr.active_joins == ["bcr"]


def test_filter_target_species_fallbacks() -> None:
    """Verify English common name and Family/Order fallbacks in filter_target_species."""
    species = _build_synthetic_tables()["species"]

    by_name_guilds = GuildRegistryContract(
        active_guild="by_name",
        profiles={
            "by_name": GuildProfileContract(
                english_names=["Northern Cardinal"],
            )
        },
    )
    res_name = filter_target_species(species, by_name_guilds)
    assert res_name["AOU"].to_list() == ["05930"]

    by_family_guilds = GuildRegistryContract(
        active_guild="by_family",
        profiles={
            "by_family": GuildProfileContract(
                families=["Turdidae", "Parulidae"],
            )
        },
    )
    res_fam = filter_target_species(species, by_family_guilds)
    assert sorted(res_fam["AOU"].to_list()) == ["06740", "07550"]
