"""Per-table join columns: the exact output column contract and join cardinality."""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.config import ProfileError, profile_from_data
from bbs_pipeline.contracts import DEFAULT_JOINS, ProtocolConfigContract, SpeciesSelectionContract
from bbs_pipeline.engine import run_ecological_engine
from bbs_pipeline.stages.enrich import JoinSource, apply_joins, vehicle_source

BASE_COLUMNS = [
    "RouteDataID",
    "route_id",
    "Year",
    "route_continuity_ratio",
    "AOU",
    "route_total",
    "is_zero_filled",
]


def _tables(with_vehicle: bool = True) -> dict[str, pl.DataFrame]:
    stops = {f"Stop{i}": [0] for i in range(1, 51)}
    stops["Stop1"] = [3]
    tables = {
        "routes": pl.DataFrame(
            {
                "CountryNum": [840],
                "StateNum": [63],
                "Route": [1],
                "RouteName": ["ENO"],
                "Active": [1],
                "Latitude": [36.0],
                "Longitude": [-79.0],
                "Stratum": [11],
                "BCR": [29],
                "RouteTypeID": [1],
            }
        ),
        "weather": pl.DataFrame(
            {
                "RouteDataID": [1],
                "CountryNum": [840],
                "StateNum": [63],
                "Route": [1],
                "Year": [2021],
                "Month": [6],
                "Day": [9],
                "ObsN": [5],
                "StartTemp": [60],
                "EndTemp": [70],
                "StartWind": [1],
                "EndWind": [2],
                "StartTime": ["0450"],
                "RunType": [1],
                "QualityCurrentID": [1],
            }
        ),
        "species": pl.DataFrame(
            {
                "AOU": ["07550"],
                "English_Common_Name": ["Wood Thrush"],
                "Order": ["Passeriformes"],
                "Family": ["Turdidae"],
                "Genus": ["Hylocichla"],
                "Species": ["mustelina"],
            }
        ),
        "fifty_stop": pl.DataFrame(
            {
                "RouteDataID": [1],
                "StateNum": [63],
                "Route": [1],
                "Year": [2021],
                "AOU": ["07550"],
                **stops,
            }
        ),
    }
    if with_vehicle:
        tables["vehicle"] = pl.DataFrame(
            {
                "RouteDataID": [1],
                "RecordedCar": [1],
                **{f"Car{i}": [2] for i in range(1, 51)},
                **{f"Noise{i}": [1 if i <= 3 else 0] for i in range(1, 51)},
            }
        )
    return tables


def _columns(joins: dict[str, list[str]], tables: dict | None = None) -> list[str]:
    protocol = ProtocolConfigContract(year_range=(2021, 2021), join_columns=joins)
    result = run_ecological_engine(tables or _tables(), SpeciesSelectionContract(), protocol)
    return result.observations_df.columns


def test_no_joins_gives_only_the_observation_columns() -> None:
    assert _columns({}) == BASE_COLUMNS


def test_exact_columns_for_a_profile() -> None:
    cols = _columns(
        {
            "routes": ["RouteName", "BCR"],
            "weather": ["Month", "Day", "StartTime"],
            "observer": ["is_first_year"],
            "vehicle": ["CarTotal", "NoiseTotal"],
            "species": ["English_Common_Name"],
        }
    )

    assert cols == [
        *BASE_COLUMNS,
        "RouteName",
        "BCR",
        "Month",
        "Day",
        "StartTime",
        "is_first_year",
        "CarTotal",
        "NoiseTotal",
        "English_Common_Name",
    ]


def test_defaults_reproduce_the_previous_columns() -> None:
    cols = _columns(DEFAULT_JOINS)

    assert cols[len(BASE_COLUMNS) :] == [
        "StateNum",
        "Route",
        "RouteName",
        "Latitude",
        "Longitude",
        "Stratum",
        "ObsN",
        "RunType",
        "QualityCurrentID",
        "StartTemp",
        "EndTemp",
        "StartWind",
        "EndWind",
        "is_first_year",
        "tenure_tier",
        "English_Common_Name",
        "Order",
        "Family",
        "Genus",
        "Species",
    ]


def test_star_takes_every_column_of_a_table() -> None:
    cols = _columns({"routes": ["*"]})

    assert cols[len(BASE_COLUMNS) :] == [
        "CountryNum",
        "StateNum",
        "Route",
        "RouteName",
        "Active",
        "Latitude",
        "Longitude",
        "Stratum",
        "BCR",
        "RouteTypeID",
    ]


def test_vehicle_totals_are_summed_per_run() -> None:
    protocol = ProtocolConfigContract(
        year_range=(2021, 2021), join_columns={"vehicle": ["CarTotal", "NoiseTotal", "Car1"]}
    )
    row = run_ecological_engine(
        _tables(), SpeciesSelectionContract(), protocol
    ).observations_df.row(0, named=True)

    assert (row["CarTotal"], row["NoiseTotal"], row["Car1"]) == (100, 3, 2)


@pytest.mark.parametrize(
    ("joins", "message"),
    [
        (
            {"routes": ["RouteNmae"]},
            r"joins.routes: no column\(s\) \['RouteNmae'\]. Valid columns: .*RouteName",
        ),
        ({"routes": ["*", "BCR"]}, r'use \["\*"\] alone'),
        (
            {"weather": ["ObsN"], "observer": ["ObsN"]},
            "requested from both joins.weather and joins.observer",
        ),
        ({"weather": ["route_total"]}, r"no column\(s\) \['route_total'\]"),
    ],
)
def test_bad_join_columns_raise(joins: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _columns(joins)


def test_vehicle_join_without_vehicle_table_raises() -> None:
    with pytest.raises(ValueError, match="no vehicle table"):
        _columns({"vehicle": ["CarTotal"]}, _tables(with_vehicle=False))


def test_duplicate_keys_on_the_right_side_raise_instead_of_multiplying_rows() -> None:
    left = pl.LazyFrame({"route_id": ["a", "b"], "x": [1, 2]})
    right = pl.LazyFrame({"route_id": ["a", "a", "b"], "RouteName": ["A1", "A2", "B"]})

    with pytest.raises(ValueError, match="duplicate route_id value"):
        apply_joins(
            left,
            {"routes": JoinSource(right, "route_id", ["RouteName"])},
            {"routes": ["RouteName"]},
        )


def test_each_join_keeps_the_row_count() -> None:
    left = pl.LazyFrame({"route_id": ["a", "b", "c"], "x": [1, 2, 3]})
    right = pl.LazyFrame({"route_id": ["a", "b"], "RouteName": ["A", "B"]})

    out, joined = apply_joins(
        left, {"routes": JoinSource(right, "route_id", ["RouteName"])}, {"routes": ["RouteName"]}
    )

    assert out.collect().height == 3
    assert joined == {"routes": ["RouteName"]}


def test_vehicle_source_offers_totals_first() -> None:
    source = vehicle_source(_tables()["vehicle"].lazy())

    assert source.columns[:3] == ["RecordedCar", "CarTotal", "NoiseTotal"]
    assert "Car50" in source.columns and "Noise50" in source.columns


def test_profile_rejects_the_old_tables_key_with_a_hint() -> None:
    with pytest.raises(
        ProfileError, match="joins.tables was replaced by one column list per table"
    ):
        profile_from_data({"joins": {"tables": ["species"]}})


def test_profile_guild_column_names_need_a_guild_file() -> None:
    with pytest.raises(ProfileError, match="joins.guilds column names need guilds.file"):
        profile_from_data({"joins": {"guilds": ["forest_guild"]}})
    # the default ["*"] is fine without a file (no guild columns are joined)
    profile_from_data({"joins": {"guilds": ["*"]}})
