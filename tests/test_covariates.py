"""Test suite for bbs_pipeline.core.covariates.

Verification gates (Phase 4, docs/04_TASKS.md §Task 4.3):
- 100% pass, zero unhandled warnings.
- At least two explicit negative-failure assertions (marked with # NEGATIVE).
- No live network calls; all data is synthetic in-memory.
- All Polars DataFrames use explicit schema_overrides (no inference).

Mathematical specifications exercised:
- §2.6  CareerSurveysCompleted, RouteTenure, IsFirstYearObserver (Kendall bias).
- §2.5  CarTotal and CarsPerStop traffic rate metrics.
"""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.core.covariates import (
    compute_observer_covariates,
    compute_traffic_covariates,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic DataFrame builders (no schema inference)
# ---------------------------------------------------------------------------

_WEATHER_SCHEMA: dict[str, type[pl.DataType]] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "Month": pl.String,
    "Day": pl.String,
    "ObsN": pl.String,
    "TotalSpp": pl.Int32,
    "StartTemp": pl.Float64,
    "EndTemp": pl.Float64,
    "TempScale": pl.String,
    "StartWind": pl.String,
    "EndWind": pl.String,
    "StartSky": pl.String,
    "EndSky": pl.String,
    "StartTime": pl.String,
    "EndTime": pl.String,
    "Assistant": pl.String,
    "QualityCurrentID": pl.String,
    "RunType": pl.String,
    # RouteKey (added by filters.add_route_key)
    "RouteKey": pl.String,
}

_VEHICLE_SCHEMA: dict[str, type[pl.DataType]] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "RecordedCar": pl.String,
    **{f"Car{i}": pl.Int32 for i in range(1, 51)},
    **{f"Noise{i}": pl.UInt8 for i in range(1, 51)},
    "TotalStops": pl.Int32,
}


def _make_weather_row(
    route_key: str,
    obs_n: str,
    year: str,
    route_data_id: str = "0001",
) -> dict[str, object]:
    """Return a dict of all WEATHER_SCHEMA fields for one survey run."""
    return {
        "RouteDataID": route_data_id,
        "CountryNum": "840",
        "StateNum": "02",
        "Route": "001",
        "RPID": "101",
        "Year": year,
        "Month": "06",
        "Day": "15",
        "ObsN": obs_n,
        "TotalSpp": 10,
        "StartTemp": 65.0,
        "EndTemp": 70.0,
        "TempScale": "F",
        "StartWind": "0",
        "EndWind": "1",
        "StartSky": "0",
        "EndSky": "0",
        "StartTime": "0500",
        "EndTime": "0830",
        "Assistant": "0",
        "QualityCurrentID": "1",
        "RunType": "1",
        "RouteKey": route_key,
    }


def _make_weather_df(rows: list[dict]) -> pl.DataFrame:
    """Build a weather DataFrame with explicit schema."""
    # Collect column-by-column to avoid inference
    if not rows:
        return pl.DataFrame(schema=_WEATHER_SCHEMA)
    cols: dict[str, list] = {k: [] for k in _WEATHER_SCHEMA}
    for row in rows:
        for k in _WEATHER_SCHEMA:
            cols[k].append(row.get(k))
    return pl.DataFrame(cols, schema=_WEATHER_SCHEMA)


def _make_vehicle_row(
    total_stops: int,
    car_counts: list[int] | None = None,
    route_data_id: str = "0001",
    year: str = "2000",
) -> dict[str, object]:
    """Return a dict of VEHICLE_SCHEMA fields for one survey run."""
    cars = car_counts if car_counts is not None else [0] * 50
    # Pad / truncate to 50 entries
    cars = (cars + [0] * 50)[:50]
    row: dict[str, object] = {
        "RouteDataID": route_data_id,
        "CountryNum": "840",
        "StateNum": "02",
        "Route": "001",
        "RPID": "101",
        "Year": year,
        "RecordedCar": "1",
        "TotalStops": total_stops,
    }
    for i, val in enumerate(cars, start=1):
        row[f"Car{i}"] = val
    for i in range(1, 51):
        row[f"Noise{i}"] = 0
    return row


def _make_vehicle_df(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=_VEHICLE_SCHEMA)
    cols: dict[str, list] = {k: [] for k in _VEHICLE_SCHEMA}
    for row in rows:
        for k in _VEHICLE_SCHEMA:
            cols[k].append(row.get(k, 0))
    return pl.DataFrame(cols, schema=_VEHICLE_SCHEMA)


# ---------------------------------------------------------------------------
# compute_observer_covariates — §2.6
# ---------------------------------------------------------------------------


class TestComputeObserverCovariates:
    def test_single_run_career_one_tenure_one(self):
        """A single survey run produces career=1, tenure=1, firstyear=1."""
        rows = [_make_weather_row("840_02_001", "9999", "2000")]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)

        assert "CareerSurveysCompleted" in result.columns
        assert "RouteTenure" in result.columns
        assert "IsFirstYearObserver" in result.columns

        row = result.row(0, named=True)
        assert row["CareerSurveysCompleted"] == 1
        assert row["RouteTenure"] == 1
        assert row["IsFirstYearObserver"] == 1

    def test_multiple_years_same_observer_same_route(self):
        """Observer returns to same route in years 2000, 2001, 2002.
        By 2002: career=3, tenure=3, firstyear=0."""
        obs = "9999"
        rk = "840_02_001"
        rows = [
            _make_weather_row(rk, obs, "2000", "R001"),
            _make_weather_row(rk, obs, "2001", "R002"),
            _make_weather_row(rk, obs, "2002", "R003"),
        ]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)

        # Sort by year for predictable row indexing
        result = result.sort("Year")
        rows_out = result.to_dicts()

        assert rows_out[0]["CareerSurveysCompleted"] == 1  # only 2000 ≤ 2000
        assert rows_out[0]["RouteTenure"] == 1
        assert rows_out[0]["IsFirstYearObserver"] == 1

        assert rows_out[1]["CareerSurveysCompleted"] == 2  # 2000, 2001 ≤ 2001
        assert rows_out[1]["RouteTenure"] == 2
        assert rows_out[1]["IsFirstYearObserver"] == 0

        assert rows_out[2]["CareerSurveysCompleted"] == 3
        assert rows_out[2]["RouteTenure"] == 3
        assert rows_out[2]["IsFirstYearObserver"] == 0

    def test_observer_on_two_routes_career_counts_all(self):
        """Observer surveys Route A in 2000 and Route B in 2001.
        For Route B in 2001: career=2, tenure=1, firstyear=1."""
        obs = "7777"
        rows = [
            _make_weather_row("840_02_001", obs, "2000", "R001"),
            _make_weather_row("840_02_002", obs, "2001", "R002"),
        ]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)
        result = result.sort("Year")
        r = result.to_dicts()

        # Year 2001, Route B: career includes 2000 and 2001, tenure=1 (new route)
        assert r[1]["CareerSurveysCompleted"] == 2
        assert r[1]["RouteTenure"] == 1
        assert r[1]["IsFirstYearObserver"] == 1

    def test_two_different_observers_isolated(self):
        """Two observers on the same route in the same year have independent
        tenure and career counts."""
        rk = "840_02_001"
        rows = [
            _make_weather_row(rk, "OBS_A", "2000", "R001"),
            _make_weather_row(rk, "OBS_B", "2000", "R002"),
        ]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)
        result = result.sort("ObsN")
        r = result.to_dicts()

        # Each observer sees career=1, tenure=1
        for row in r:
            assert row["CareerSurveysCompleted"] == 1
            assert row["RouteTenure"] == 1
            assert row["IsFirstYearObserver"] == 1

    def test_output_columns_are_int32(self):
        rows = [_make_weather_row("840_02_001", "9999", "2000")]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)
        assert result["CareerSurveysCompleted"].dtype == pl.Int32
        assert result["RouteTenure"].dtype == pl.Int32
        assert result["IsFirstYearObserver"].dtype == pl.Int32

    def test_is_first_year_binary_only_zero_or_one(self):
        obs = "9999"
        rk = "840_02_001"
        rows = [
            _make_weather_row(rk, obs, "2000", "R001"),
            _make_weather_row(rk, obs, "2001", "R002"),
        ]
        df = _make_weather_df(rows)
        result = compute_observer_covariates(df)
        values = set(result["IsFirstYearObserver"].to_list())
        assert values.issubset({0, 1}), (
            f"IsFirstYearObserver must be binary, got {values}"
        )

    # NEGATIVE: non-DataFrame raises TypeError
    def test_raises_type_error_on_non_dataframe(self):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            compute_observer_covariates(
                {"ObsN": ["9999"], "Year": ["2000"], "RouteKey": ["840_02_001"]}
            )  # type: ignore[arg-type]

    # NEGATIVE: missing required column raises ValueError
    def test_raises_value_error_on_missing_column(self):  # NEGATIVE
        # Drop RouteKey to trigger missing-column error
        rows = [_make_weather_row("840_02_001", "9999", "2000")]
        df = _make_weather_df(rows).drop("RouteKey")
        with pytest.raises(ValueError, match="RouteKey"):
            compute_observer_covariates(df)

    def test_empty_dataframe_returns_null_covariates(self):
        df = _make_weather_df([])
        result = compute_observer_covariates(df)
        assert "CareerSurveysCompleted" in result.columns
        assert "RouteTenure" in result.columns
        assert "IsFirstYearObserver" in result.columns
        assert len(result) == 0


# ---------------------------------------------------------------------------
# compute_traffic_covariates — §2.5
# ---------------------------------------------------------------------------


class TestComputeTrafficCovariates:
    def test_car_total_sums_within_total_stops(self):
        """With TotalStops=3 and Car1=2, Car2=3, Car3=5, Car4=99:
        CarTotal must be 2+3+5=10 (Car4 ignored since stop 4 > TotalStops=3)."""
        cars = [2, 3, 5, 99] + [0] * 46
        row = _make_vehicle_row(total_stops=3, car_counts=cars)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)

        assert result["CarTotal"][0] == 10

    def test_cars_per_stop_formula(self):
        """CarsPerStop = CarTotal / TotalStops."""
        cars = [4, 6] + [0] * 48  # Sum = 10, TotalStops = 2 → 5.0
        row = _make_vehicle_row(total_stops=2, car_counts=cars)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)

        assert result["CarTotal"][0] == 10
        assert result["CarsPerStop"][0] == pytest.approx(5.0)

    def test_full_50_stop_route(self):
        """All 50 stops active, each with 1 car → CarTotal=50, CarsPerStop=1.0."""
        cars = [1] * 50
        row = _make_vehicle_row(total_stops=50, car_counts=cars)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)

        assert result["CarTotal"][0] == 50
        assert result["CarsPerStop"][0] == pytest.approx(1.0)

    def test_zero_cars_gives_zero_total(self):
        cars = [0] * 50
        row = _make_vehicle_row(total_stops=50, car_counts=cars)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)

        assert result["CarTotal"][0] == 0
        assert result["CarsPerStop"][0] == pytest.approx(0.0)

    def test_multiple_rows_computed_independently(self):
        """Two rows with different stop counts produce independent covariates."""
        row1 = _make_vehicle_row(total_stops=2, car_counts=[3, 7] + [0] * 48)
        row2 = _make_vehicle_row(total_stops=4, car_counts=[1, 1, 1, 1] + [0] * 46)
        df = _make_vehicle_df([row1, row2])
        result = compute_traffic_covariates(df)

        totals = result["CarTotal"].to_list()
        assert totals[0] == 10   # 3+7
        assert totals[1] == 4    # 1+1+1+1

        per_stop = result["CarsPerStop"].to_list()
        assert per_stop[0] == pytest.approx(5.0)  # 10/2
        assert per_stop[1] == pytest.approx(1.0)  # 4/4

    def test_output_dtype_car_total_is_int32(self):
        row = _make_vehicle_row(total_stops=50)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)
        assert result["CarTotal"].dtype == pl.Int32

    def test_output_dtype_cars_per_stop_is_float64(self):
        row = _make_vehicle_row(total_stops=50)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)
        assert result["CarsPerStop"].dtype == pl.Float64

    def test_cars_beyond_total_stops_are_excluded(self):
        """Verify boundary: Car4 must NOT contribute when TotalStops=3."""
        cars = [0] * 50
        cars[3] = 100  # Car4 — should be excluded when TotalStops=3
        row = _make_vehicle_row(total_stops=3, car_counts=cars)
        df = _make_vehicle_df([row])
        result = compute_traffic_covariates(df)

        assert result["CarTotal"][0] == 0, (
            "Car4 must not contribute to CarTotal when TotalStops=3."
        )

    # NEGATIVE: non-DataFrame raises TypeError
    def test_raises_type_error_on_non_dataframe(self):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            compute_traffic_covariates(
                [{"TotalStops": 50}]  # type: ignore[arg-type]
            )

    # NEGATIVE: missing TotalStops column raises ValueError
    def test_raises_value_error_on_missing_total_stops(self):  # NEGATIVE
        row = _make_vehicle_row(total_stops=50)
        df = _make_vehicle_df([row]).drop("TotalStops")
        with pytest.raises(ValueError, match="TotalStops"):
            compute_traffic_covariates(df)

    def test_empty_dataframe_returns_null_covariates(self):
        df = _make_vehicle_df([])
        result = compute_traffic_covariates(df)
        assert "CarTotal" in result.columns
        assert "CarsPerStop" in result.columns
        assert len(result) == 0
