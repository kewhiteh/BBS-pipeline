"""Run-level integrity: filters, zero-fill pool, and joins are keyed on survey runs."""

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
from bbs_pipeline.stages.observations import harmonize_observations

NO_GUILD = SpeciesSelectionContract()


def _stops(**values: list[int | None]) -> dict[str, list[int | None]]:
    """Build Stop1..Stop50 columns of zeros, overriding the given stops."""
    n = len(next(iter(values.values())))
    cols: dict[str, list[int | None]] = {f"Stop{i}": [0] * n for i in range(1, 51)}
    cols.update(values)
    return cols


def _tables(
    weather_rows: list[dict[str, int]],
    fifty_rows: list[dict[str, int]],
    stop1: list[int | None],
    ten_stop: pl.DataFrame | None = None,
) -> dict[str, pl.DataFrame]:
    routes = pl.DataFrame(
        {
            "CountryNum": [840],
            "StateNum": [63],
            "Route": [1],
            "RouteName": ["ENO RIVER"],
            "Latitude": [36.07],
            "Longitude": [-79.01],
            "Stratum": [11],
            "BCR": [29],
        }
    )
    weather = pl.DataFrame(
        [
            {
                "CountryNum": 840,
                "StateNum": 63,
                "Route": 1,
                "QualityCurrentID": 1,
                # default weather join columns
                "StartTemp": 60,
                "EndTemp": 70,
                "StartWind": 1,
                "EndWind": 2,
                **r,
            }
            for r in weather_rows
        ]
    )
    species = pl.DataFrame(
        {
            "AOU": ["07550", "06740", "05930"],
            "English_Common_Name": ["Wood Thrush", "Ovenbird", "Northern Cardinal"],
            "Order": ["Passeriformes"] * 3,
            "Family": ["Turdidae", "Parulidae", "Cardinalidae"],
            "Genus": ["Hylocichla", "Seiurus", "Cardinalis"],
            "Species": ["mustelina", "aurocapilla", "cardinalis"],
        }
    )
    fifty = pl.DataFrame(fifty_rows).with_columns(
        pl.DataFrame(_stops(Stop1=stop1), schema_overrides={"Stop1": pl.Int64})
    )
    tables = {"routes": routes, "weather": weather, "species": species, "fifty_stop": fifty}
    if ten_stop is not None:
        tables["ten_stop"] = ten_stop
    return tables


@pytest.mark.parametrize("zero_fill", [True, False])
def test_excluded_run_never_contributes_counts(zero_fill: bool) -> None:
    """A RunType 0 run in the same route-year must not add to the kept run's counts."""
    tables = _tables(
        weather_rows=[
            {"RouteDataID": 1, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1},
            {"RouteDataID": 2, "RPID": 102, "Year": 2021, "ObsN": 6, "RunType": 0},
        ],
        fifty_rows=[
            {"RouteDataID": 1, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"},
            {"RouteDataID": 2, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"},
        ],
        stop1=[3, 100],
    )
    protocol = ProtocolConfigContract(
        year_range=(2021, 2021), enable_extirpation_zero_fill=zero_fill
    )

    df = run_ecological_engine(tables, NO_GUILD, protocol).observations_df

    assert df.height == 1
    row = df.row(0, named=True)
    assert row["route_total"] == 3
    assert row["RouteDataID"] == 1
    # Weather columns come from the same run, not an arbitrary one in the route-year
    assert row["ObsN"] == 5
    assert row["RunType"] == 1


def test_zero_fill_uses_route_historical_pool() -> None:
    """Species seen on the route in any year get zeros; never-seen species get no rows."""
    ten_stop = pl.DataFrame(
        {
            "RouteDataID": [900],
            "StateNum": [63],
            "Route": [1],
            "Year": [1990],
            "AOU": ["05930"],  # Cardinal: only ever seen in 1990, before the window
            "Count10": [2],
            "Count20": [0],
            "Count30": [0],
            "Count40": [0],
            "Count50": [0],
            "StopTotal": [2],
        }
    )
    tables = _tables(
        weather_rows=[
            {"RouteDataID": 10, "RPID": 101, "Year": 2010, "ObsN": 5, "RunType": 1},
            {"RouteDataID": 20, "RPID": 101, "Year": 2020, "ObsN": 5, "RunType": 1},
            {"RouteDataID": 21, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1},
        ],
        fifty_rows=[
            # Ovenbird seen only in 2010, outside the 2021 window
            {"RouteDataID": 10, "StateNum": 63, "Route": 1, "Year": 2010, "AOU": "06740"},
            {"RouteDataID": 21, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"},
        ],
        stop1=[4, 7],
        ten_stop=ten_stop,
    )
    protocol = ProtocolConfigContract(year_range=(2021, 2021))

    df = run_ecological_engine(tables, NO_GUILD, protocol).observations_df.sort("AOU")

    assert df["AOU"].to_list() == ["05930", "06740", "07550"]
    assert df["route_total"].to_list() == [0, 0, 7]
    assert df["is_zero_filled"].to_list() == [True, True, False]


def test_species_never_recorded_on_route_get_no_rows() -> None:
    tables = _tables(
        weather_rows=[{"RouteDataID": 1, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1}],
        fifty_rows=[{"RouteDataID": 1, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"}],
        stop1=[3],
    )
    df = run_ecological_engine(
        tables, NO_GUILD, ProtocolConfigContract(year_range=(2021, 2021))
    ).observations_df

    assert df["AOU"].to_list() == ["07550"]


def test_more_than_one_surviving_run_per_route_year_raises() -> None:
    tables = _tables(
        weather_rows=[
            {"RouteDataID": 1, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1},
            {"RouteDataID": 2, "RPID": 102, "Year": 2021, "ObsN": 6, "RunType": 1},
        ],
        fifty_rows=[{"RouteDataID": 1, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"}],
        stop1=[3],
    )

    with pytest.raises(ValueError, match=r"63_001 2021: \[1, 2\]"):
        run_ecological_engine(tables, NO_GUILD, ProtocolConfigContract(year_range=(2021, 2021)))


def test_missing_stop_values_stay_null() -> None:
    tables = _tables(
        weather_rows=[{"RouteDataID": 1, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1}],
        fifty_rows=[{"RouteDataID": 1, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"}],
        stop1=[None],
    )
    tables["fifty_stop"] = tables["fifty_stop"].with_columns(pl.lit(2).alias("Stop2"))
    protocol = ProtocolConfigContract(
        year_range=(2021, 2021), stop_aggregation=StopAggregationMode.RAW_50_STOPS
    )

    row = harmonize_observations(tables, protocol).collect().row(0, named=True)

    assert row["Stop1"] is None
    assert row["Stop2"] == 2
    assert row["route_total"] == 2


def test_raw_stops_from_ten_stop_source_raises() -> None:
    protocol = ProtocolConfigContract(
        temporal_routing_mode=TemporalRoutingMode.TEN_STOP_1966_PLUS,
        stop_aggregation=StopAggregationMode.RAW_50_STOPS,
    )

    with pytest.raises(ValueError, match="only the 50-stop data has"):
        harmonize_observations({}, protocol)


def test_user_run_filters_are_not_overridden() -> None:
    protocol = ProtocolConfigContract(run_types=[0, 1], quality_current_ids=[])

    assert protocol.run_types == [0, 1]
    assert protocol.quality_current_ids == []


def test_ten_stop_abundance_is_species_total_not_stop_total() -> None:
    """StopTotal counts stops with a detection; SpeciesTotal counts birds."""
    ten_stop = pl.DataFrame(
        {
            "RouteDataID": [900],
            "StateNum": [63],
            "Route": [1],
            "Year": [1990],
            "AOU": ["07550"],
            "Count10": [9],
            "Count20": [3],
            "Count30": [0],
            "Count40": [0],
            "Count50": [0],
            "StopTotal": [2],
            "SpeciesTotal": [12],
        }
    )
    protocol = ProtocolConfigContract(
        temporal_routing_mode=TemporalRoutingMode.TEN_STOP_1966_PLUS,
        stop_aggregation=StopAggregationMode.TEN_STOP_BINS,
    )

    row = (
        harmonize_observations({"ten_stop": ten_stop}, protocol, lump_subspecies_and_hybrids=False)
        .collect()
        .row(0, named=True)
    )

    assert row["route_total"] == 12


def test_state_filter_accepts_abbreviations_via_release_regions() -> None:
    tables = _tables(
        weather_rows=[{"RouteDataID": 1, "RPID": 101, "Year": 2021, "ObsN": 5, "RunType": 1}],
        fifty_rows=[{"RouteDataID": 1, "StateNum": 63, "Route": 1, "Year": 2021, "AOU": "07550"}],
        stop1=[3],
    )
    tables["regions"] = pl.DataFrame(
        {"StateNum": [63, 88], "StateName": ["North Carolina", "Virginia"]}
    )

    nc = run_ecological_engine(
        tables, NO_GUILD, ProtocolConfigContract(year_range=(2021, 2021), states=["NC"])
    )
    va = run_ecological_engine(
        tables, NO_GUILD, ProtocolConfigContract(year_range=(2021, 2021), states=["Virginia"])
    )

    assert nc.observations_df.height == 1
    assert va.observations_df.height == 0
