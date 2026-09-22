"""Test suite for bbs_pipeline.core.discovery and bbs_pipeline.core.filters.

Verification gates (Phase 3, docs/04_TASKS.md §Task 3.4):
- 100 % pass, zero unhandled warnings.
- At least two explicit negative-failure assertions.
- No live network calls (all data is synthetic in-memory).
- All Polars DataFrames carry explicit schema_overrides (no inference).

Mathematical specifications exercised:
- §2.1  Composite RouteKey construction.
- §2.2  Dynamic max_observed_year extraction and eligible year set.
- §2.3  Proportional continuity threshold with ceiling arithmetic.
- §2.4  Stop effort guard (TotalStops ≥ min_stops) and NULL imputation.
- §2.5  Sub-route stop range slicing.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from bbs_pipeline.core.discovery import (
    COVID_HIATUS_YEAR,
    get_eligible_years,
    get_max_observed_year,
)
from bbs_pipeline.core.filters import (
    FIFTY_STOP_MIN_YEAR,
    add_route_key,
    enforce_fifty_stop_temporal_guard,
    filter_by_continuity,
    filter_by_min_stops,
    filter_by_observer_cohort,
    filter_observer_tenure,
    filter_traffic,
    nullify_stops_beyond_total,
    slice_stop_range,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_weather(years: list[str]) -> pl.DataFrame:
    """Build a minimal weather DataFrame using explicit schema — no inference."""
    n = len(years)
    return pl.DataFrame(
        {
            "RouteDataID": [f"RD{i:04d}" for i in range(n)],
            "CountryNum": ["840"] * n,
            "StateNum": ["02"] * n,
            "Route": ["001"] * n,
            "RPID": ["1"] * n,
            "Year": years,
            "Month": ["06"] * n,
            "Day": ["15"] * n,
            "ObsN": ["9999"] * n,
            "TotalSpp": [10] * n,
            "StartTemp": [65.0] * n,
            "EndTemp": [70.0] * n,
            "TempScale": ["F"] * n,
            "StartWind": ["0"] * n,
            "EndWind": ["1"] * n,
            "StartSky": ["0"] * n,
            "EndSky": ["1"] * n,
            "StartTime": ["0500"] * n,
            "EndTime": ["0900"] * n,
            "Assistant": ["0"] * n,
            "QualityCurrentID": ["1"] * n,
            "RunType": ["1"] * n,
        },
        schema={
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
        },
    )


def _make_obs_df(
    routes: list[str],
    years: list[str],
    country: str = "840",
    state: str = "02",
    total_stops: int = 50,
) -> pl.DataFrame:
    """Build a minimal observation-like DataFrame for filter tests."""
    n = len(routes)
    assert len(years) == n, "routes and years must be same length"
    stop_data = {f"Stop{i}": [5] * n for i in range(1, 51)}
    base: dict[str, list] = {
        "CountryNum": [country] * n,
        "StateNum": [state] * n,
        "Route": routes,
        "Year": years,
        "TotalStops": [total_stops] * n,
    }
    base.update(stop_data)  # type: ignore[arg-type]
    schema: dict[str, type] = {
        "CountryNum": pl.String,
        "StateNum": pl.String,
        "Route": pl.String,
        "Year": pl.String,
        "TotalStops": pl.Int32,
        **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
    }
    return pl.DataFrame(base, schema=schema)


# ---------------------------------------------------------------------------
# §2.2 — get_max_observed_year
# ---------------------------------------------------------------------------


class TestGetMaxObservedYear:
    def test_returns_integer_maximum(self) -> None:
        """Should parse Year strings and return the integer maximum."""
        df = _make_weather(["2018", "2021", "2019"])
        assert get_max_observed_year(df) == 2021

    def test_single_year(self) -> None:
        """Single-row DataFrame should return that year."""
        df = _make_weather(["2015"])
        assert get_max_observed_year(df) == 2015

    def test_year_2020_present_returns_2020_if_max(self) -> None:
        """2020 is not excluded by this function; discovery is year-set aware."""
        df = _make_weather(["2018", "2020"])
        assert get_max_observed_year(df) == 2020

    def test_return_type_is_int(self) -> None:
        df = _make_weather(["2022"])
        result = get_max_observed_year(df)
        assert isinstance(result, int)

    # --- Negative / failure-path assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        """Passing a non-DataFrame must raise TypeError."""
        with pytest.raises(TypeError, match="polars.DataFrame"):
            get_max_observed_year({"Year": ["2020"]})  # type: ignore[arg-type]

    def test_raises_value_error_on_missing_year_column(self) -> None:
        """DataFrame without a 'Year' column must raise ValueError."""
        df = pl.DataFrame({"X": ["a"]}, schema={"X": pl.String})
        with pytest.raises(ValueError, match="Year"):
            get_max_observed_year(df)

    def test_raises_value_error_on_empty_dataframe(self) -> None:
        """Empty DataFrame must raise ValueError."""
        df = _make_weather([]).slice(0, 0)
        with pytest.raises(ValueError, match="empty"):
            get_max_observed_year(df)


# ---------------------------------------------------------------------------
# §2.2 — get_eligible_years
# ---------------------------------------------------------------------------


class TestGetEligibleYears:
    def test_excludes_2020_covid_hiatus(self) -> None:
        """2020 must never appear in the eligible set."""
        years = get_eligible_years(2018, 2022, 2022)
        assert COVID_HIATUS_YEAR not in years

    def test_excludes_2020_at_boundary(self) -> None:
        """Even when 2020 is the only year in range, result is empty."""
        years = get_eligible_years(2020, 2020, 2020)
        assert len(years) == 0

    def test_caps_at_max_observed_year(self) -> None:
        """Years beyond max_observed_year must not appear."""
        years = get_eligible_years(2015, 2025, 2022)
        assert max(years) == 2022
        assert 2023 not in years
        assert 2025 not in years

    def test_returns_frozenset(self) -> None:
        years = get_eligible_years(2015, 2018, 2020)
        assert isinstance(years, frozenset)

    def test_full_window_without_2020(self) -> None:
        """2019 and 2021 both present; 2020 absent."""
        years = get_eligible_years(2019, 2021, 2021)
        assert 2019 in years
        assert 2021 in years
        assert 2020 not in years
        assert len(years) == 2

    def test_end_year_beyond_max_observed(self) -> None:
        """end_year=9999 but max_observed=2023 caps the range."""
        years = get_eligible_years(2022, 9999, 2023)
        assert set(years) == {2022, 2023}

    def test_single_year_not_2020(self) -> None:
        years = get_eligible_years(2021, 2021, 2021)
        assert years == frozenset({2021})

    # --- Negative / failure-path assertions ---

    def test_raises_value_error_start_after_end(self) -> None:
        """start_year > end_year must raise ValueError."""
        with pytest.raises(ValueError, match="start_year"):
            get_eligible_years(2022, 2019, 2023)

    def test_raises_value_error_non_positive_year(self) -> None:
        """Negative or zero year arguments must raise ValueError."""
        with pytest.raises(ValueError, match="positive integer"):
            get_eligible_years(-1, 2022, 2022)

    def test_raises_value_error_zero_max_observed(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            get_eligible_years(2015, 2022, 0)


# ---------------------------------------------------------------------------
# §2.1 — add_route_key
# ---------------------------------------------------------------------------


class TestAddRouteKey:
    def test_key_format(self) -> None:
        """RouteKey must be 'CountryNum_StateNum_Route'."""
        df = pl.DataFrame(
            {"CountryNum": ["840"], "StateNum": ["02"], "Route": ["001"]},
            schema={"CountryNum": pl.String, "StateNum": pl.String, "Route": pl.String},
        )
        result = add_route_key(df)
        assert result["RouteKey"][0] == "840_02_001"

    def test_key_dtype_is_string(self) -> None:
        df = pl.DataFrame(
            {"CountryNum": ["124"], "StateNum": ["03"], "Route": ["007"]},
            schema={"CountryNum": pl.String, "StateNum": pl.String, "Route": pl.String},
        )
        result = add_route_key(df)
        assert result.schema["RouteKey"] == pl.String

    def test_key_preserved_zero_padding(self) -> None:
        """Zero-padded strings must be preserved exactly."""
        df = pl.DataFrame(
            {"CountryNum": ["124"], "StateNum": ["09"], "Route": ["003"]},
            schema={"CountryNum": pl.String, "StateNum": pl.String, "Route": pl.String},
        )
        result = add_route_key(df)
        assert result["RouteKey"][0] == "124_09_003"

    def test_multiple_rows(self) -> None:
        df = pl.DataFrame(
            {
                "CountryNum": ["840", "840"],
                "StateNum": ["01", "02"],
                "Route": ["001", "002"],
            },
            schema={"CountryNum": pl.String, "StateNum": pl.String, "Route": pl.String},
        )
        result = add_route_key(df)
        assert result["RouteKey"].to_list() == ["840_01_001", "840_02_002"]

    # --- Negative / failure-path assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        with pytest.raises(TypeError, match="polars.DataFrame"):
            add_route_key([{"CountryNum": "840"}])  # type: ignore[arg-type]

    def test_raises_value_error_on_missing_column(self) -> None:
        df = pl.DataFrame(
            {"CountryNum": ["840"], "StateNum": ["02"]},
            schema={"CountryNum": pl.String, "StateNum": pl.String},
        )
        with pytest.raises(ValueError, match="Route"):
            add_route_key(df)


# ---------------------------------------------------------------------------
# §2.3 — filter_by_continuity
# ---------------------------------------------------------------------------


class TestFilterByContinuity:
    def _make_multi_route_df(self) -> pl.DataFrame:
        """
        Route 001: 5 eligible runs  (2016–2019, 2021)
        Route 002: 2 eligible runs  (2016, 2017)
        Route 003: 0 eligible runs  (only 2020 which is excluded)
        Eligible years: {2016,2017,2018,2019,2021} → |Y|=5
        """
        routes = (
            ["001"] * 5 + ["002"] * 2 + ["003"] * 1
        )
        years = (
            ["2016", "2017", "2018", "2019", "2021"]
            + ["2016", "2017"]
            + ["2020"]
        )
        df = _make_obs_df(routes, years)
        return add_route_key(df)

    def test_100_pct_keeps_only_complete_routes(self) -> None:
        """At 100 % completeness all 5 runs required; only route 001 qualifies."""
        df = self._make_multi_route_df()
        eligible = frozenset({2016, 2017, 2018, 2019, 2021})
        result = filter_by_continuity(df, eligible, 100.0)
        route_keys = result["RouteKey"].unique().to_list()
        assert all(k.endswith("001") for k in route_keys)

    def test_40_pct_threshold_math(self) -> None:
        """40 % of 5 = 2.0 → ceil = 2; routes 001 and 002 both qualify."""
        df = self._make_multi_route_df()
        eligible = frozenset({2016, 2017, 2018, 2019, 2021})
        result = filter_by_continuity(df, eligible, 40.0)
        keys = sorted(set(k.split("_")[2] for k in result["RouteKey"].unique().to_list()))
        assert "001" in keys
        assert "002" in keys

    def test_2020_rows_not_counted_as_valid_runs(self) -> None:
        """Route 003 with only a 2020 run should be excluded at any threshold."""
        df = self._make_multi_route_df()
        eligible = frozenset({2016, 2017, 2018, 2019, 2021})
        result = filter_by_continuity(df, eligible, 1.0)
        route_nums = [k.split("_")[2] for k in result["RouteKey"].unique().to_list()]
        assert "003" not in route_nums

    def test_ceiling_arithmetic(self) -> None:
        """Verify ceiling: ceil(5 × 75 / 100) = ceil(3.75) = 4."""
        assert math.ceil(5 * 75 / 100) == 4

    def test_exact_required_runs_passes(self) -> None:
        """Route with exactly required_runs should be retained, not dropped."""
        # 5 eligible years, 40 % → required = ceil(2) = 2; route 002 has 2 runs.
        df = self._make_multi_route_df()
        eligible = frozenset({2016, 2017, 2018, 2019, 2021})
        result = filter_by_continuity(df, eligible, 40.0)
        route_nums = [k.split("_")[2] for k in result["RouteKey"].unique().to_list()]
        assert "002" in route_nums

    # --- Negative / failure-path assertions ---

    def test_raises_value_error_empty_eligible_years(self) -> None:
        df = self._make_multi_route_df()
        with pytest.raises(ValueError, match="eligible_years"):
            filter_by_continuity(df, frozenset(), 75.0)

    def test_raises_value_error_invalid_pct_zero(self) -> None:
        df = self._make_multi_route_df()
        with pytest.raises(ValueError, match="min_completeness_pct"):
            filter_by_continuity(df, frozenset({2021}), 0.0)

    def test_raises_value_error_pct_above_100(self) -> None:
        df = self._make_multi_route_df()
        with pytest.raises(ValueError, match="min_completeness_pct"):
            filter_by_continuity(df, frozenset({2021}), 101.0)

    def test_raises_value_error_missing_route_key_column(self) -> None:
        df = pl.DataFrame(
            {"Year": ["2021"]},
            schema={"Year": pl.String},
        )
        with pytest.raises(ValueError, match="RouteKey"):
            filter_by_continuity(df, frozenset({2021}), 75.0)


# ---------------------------------------------------------------------------
# §2.4 — filter_by_min_stops
# ---------------------------------------------------------------------------


class TestFilterByMinStops:
    def _make_stops_df(self, total_stops_values: list[int]) -> pl.DataFrame:
        n = len(total_stops_values)
        return pl.DataFrame(
            {
                "RouteKey": [f"840_02_00{i}" for i in range(n)],
                "TotalStops": total_stops_values,
            },
            schema={"RouteKey": pl.String, "TotalStops": pl.Int32},
        )

    def test_keeps_rows_at_min_boundary(self) -> None:
        df = self._make_stops_df([45, 50, 48])
        result = filter_by_min_stops(df, 45)
        assert len(result) == 3

    def test_excludes_rows_below_threshold(self) -> None:
        df = self._make_stops_df([44, 45, 50])
        result = filter_by_min_stops(df, 45)
        assert len(result) == 2
        assert 44 not in result["TotalStops"].to_list()

    def test_standard_50_stop_threshold(self) -> None:
        df = self._make_stops_df([49, 50, 50, 48])
        result = filter_by_min_stops(df, 50)
        assert result["TotalStops"].to_list() == [50, 50]

    # --- Negative / failure-path assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        with pytest.raises(TypeError, match="polars.DataFrame"):
            filter_by_min_stops({"TotalStops": [50]}, 45)  # type: ignore[arg-type]

    def test_raises_value_error_missing_column(self) -> None:
        df = pl.DataFrame({"X": [1]}, schema={"X": pl.Int32})
        with pytest.raises(ValueError, match="TotalStops"):
            filter_by_min_stops(df, 45)

    def test_raises_value_error_zero_min_stops(self) -> None:
        df = self._make_stops_df([50])
        with pytest.raises(ValueError, match="min_stops"):
            filter_by_min_stops(df, 0)

    def test_raises_value_error_negative_min_stops(self) -> None:
        df = self._make_stops_df([50])
        with pytest.raises(ValueError, match="min_stops"):
            filter_by_min_stops(df, -1)


# ---------------------------------------------------------------------------
# §2.4 — nullify_stops_beyond_total
# ---------------------------------------------------------------------------


class TestNullifyStopsBeyondTotal:
    def _make_stop_df(
        self,
        total_stops: list[int],
        n_stop_cols: int = 5,
        stop_value: int = 3,
    ) -> pl.DataFrame:
        """Create a DataFrame with Stop1…Stop{n_stop_cols} and a TotalStops column."""
        n = len(total_stops)
        data: dict = {
            "TotalStops": total_stops,
            **{f"Stop{i}": [stop_value] * n for i in range(1, n_stop_cols + 1)},
        }
        schema = {
            "TotalStops": pl.Int32,
            **{f"Stop{i}": pl.Int32 for i in range(1, n_stop_cols + 1)},
        }
        return pl.DataFrame(data, schema=schema)

    def test_stops_within_total_are_preserved(self) -> None:
        """Stops ≤ TotalStops must retain their original value."""
        df = self._make_stop_df([3], n_stop_cols=5)
        result = nullify_stops_beyond_total(df)
        assert result["Stop1"][0] == 3
        assert result["Stop2"][0] == 3
        assert result["Stop3"][0] == 3

    def test_stops_beyond_total_are_null(self) -> None:
        """Stops 4 and 5 must become NULL when TotalStops = 3."""
        df = self._make_stop_df([3], n_stop_cols=5)
        result = nullify_stops_beyond_total(df)
        assert result["Stop4"][0] is None
        assert result["Stop5"][0] is None

    def test_full_50_stop_route_nothing_nullified(self) -> None:
        """A 50-stop route must have zero nulls among Stop1…Stop50."""
        df = _make_obs_df(["001"], ["2021"], total_stops=50)
        result = nullify_stops_beyond_total(df)
        for i in range(1, 51):
            assert result[f"Stop{i}"][0] == 5  # original value

    def test_48_stop_route_stops_49_50_are_null(self) -> None:
        """Classic 48-stop route: Stop49 and Stop50 must be NULL."""
        df = _make_obs_df(["001"], ["2021"], total_stops=48)
        result = nullify_stops_beyond_total(df)
        assert result["Stop49"][0] is None
        assert result["Stop50"][0] is None
        assert result["Stop48"][0] == 5  # retained

    def test_boundary_exactly_at_total(self) -> None:
        """Stop at index == TotalStops must NOT be nullified."""
        df = self._make_stop_df([5], n_stop_cols=5)
        result = nullify_stops_beyond_total(df)
        assert result["Stop5"][0] == 3

    def test_null_not_zero_beyond_total(self) -> None:
        """Crucial invariant: out-of-range stops must be null, never 0."""
        df = self._make_stop_df([2], n_stop_cols=4)
        result = nullify_stops_beyond_total(df)
        for col in ["Stop3", "Stop4"]:
            val = result[col][0]
            assert val is None, f"{col} should be None, got {val!r}"

    def test_multiple_rows_mixed_totals(self) -> None:
        """Each row is independently nullified per its own TotalStops."""
        df = self._make_stop_df([3, 5], n_stop_cols=5)
        result = nullify_stops_beyond_total(df)
        # Row 0: TotalStops=3 → Stop4, Stop5 null
        assert result["Stop4"][0] is None
        assert result["Stop5"][0] is None
        # Row 1: TotalStops=5 → all stops kept
        assert result["Stop4"][1] == 3
        assert result["Stop5"][1] == 3

    # --- Negative / failure-path assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        with pytest.raises(TypeError, match="polars.DataFrame"):
            nullify_stops_beyond_total({"TotalStops": [50]})  # type: ignore[arg-type]

    def test_raises_value_error_missing_total_stops_column(self) -> None:
        df = pl.DataFrame({"Stop1": [5]}, schema={"Stop1": pl.Int32})
        with pytest.raises(ValueError, match="TotalStops"):
            nullify_stops_beyond_total(df)


# ---------------------------------------------------------------------------
# §2.5 — slice_stop_range
# ---------------------------------------------------------------------------


class TestSliceStopRange:
    def _make_stop_df_with_values(self, stop_values: dict[int, int]) -> pl.DataFrame:
        """Build a single-row DataFrame with specified stop values."""
        schema = {f"Stop{i}": pl.Int32 for i in stop_values}
        data = {f"Stop{i}": [v] for i, v in stop_values.items()}
        return pl.DataFrame(data, schema=schema)

    def test_sum_single_stop(self) -> None:
        """Slicing a single stop should return its value."""
        df = self._make_stop_df_with_values({1: 7, 2: 3, 3: 5})
        result = slice_stop_range(df, 2, 2)
        assert result["SegmentCount"][0] == 3

    def test_sum_range(self) -> None:
        """Sum over a contiguous range should match arithmetic expectation."""
        df = self._make_stop_df_with_values({1: 2, 2: 4, 3: 6, 4: 8, 5: 10})
        result = slice_stop_range(df, 2, 4)
        assert result["SegmentCount"][0] == 4 + 6 + 8

    def test_full_50_stop_sum(self) -> None:
        """Sum of all 50 stops each = 5 → SegmentCount = 250."""
        df = _make_obs_df(["001"], ["2021"], total_stops=50)
        result = slice_stop_range(df, 1, 50)
        assert result["SegmentCount"][0] == 250

    def test_custom_segment_col_name(self) -> None:
        df = self._make_stop_df_with_values({1: 1, 2: 2, 3: 3})
        result = slice_stop_range(df, 1, 3, segment_col="MySum")
        assert "MySum" in result.columns
        assert result["MySum"][0] == 6

    # --- Negative / failure-path assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        with pytest.raises(TypeError, match="polars.DataFrame"):
            slice_stop_range({"Stop1": [1]}, 1, 5)  # type: ignore[arg-type]

    def test_raises_value_error_start_below_1(self) -> None:
        df = _make_obs_df(["001"], ["2021"])
        with pytest.raises(ValueError, match="start_stop"):
            slice_stop_range(df, 0, 10)

    def test_raises_value_error_end_above_50(self) -> None:
        df = _make_obs_df(["001"], ["2021"])
        with pytest.raises(ValueError, match="end_stop"):
            slice_stop_range(df, 1, 51)

    def test_raises_value_error_start_after_end(self) -> None:
        df = _make_obs_df(["001"], ["2021"])
        with pytest.raises(ValueError, match="start_stop"):
            slice_stop_range(df, 10, 5)

    def test_raises_value_error_no_matching_columns(self) -> None:
        """If the DataFrame has no Stop columns in range, raise ValueError."""
        df = pl.DataFrame({"RouteKey": ["840_02_001"]}, schema={"RouteKey": pl.String})
        with pytest.raises(ValueError, match="No Stop columns"):
            slice_stop_range(df, 1, 10)


# ---------------------------------------------------------------------------
# Integration: discovery → filters pipeline
# ---------------------------------------------------------------------------


class TestIntegrationDiscoveryToFilters:
    """End-to-end smoke test linking temporal discovery to route filtering."""

    def test_full_pipeline(self) -> None:
        """
        Given weather data spanning 2016–2022 (no 2020 row),
        eligible years = {2016, 2017, 2018, 2019, 2021, 2022} (size 6).

        Route A has 6 eligible-year runs → passes 100 % threshold.
        Route B has 3 eligible-year runs → passes 50 % (ceil(6 × 50/100)=3).
        Route C has 2 eligible-year runs → fails 50 % threshold.
        """
        all_years = ["2016", "2017", "2018", "2019", "2021", "2022"]
        weather = _make_weather(all_years)
        max_yr = get_max_observed_year(weather)
        assert max_yr == 2022

        eligible = get_eligible_years(2016, 2025, max_yr)
        assert 2020 not in eligible
        assert len(eligible) == 6

        routes = (
            ["001"] * 6   # Route A — all 6 eligible years
            + ["002"] * 3  # Route B — 3 of 6 (2016, 2017, 2018)
            + ["003"] * 2  # Route C — 2 of 6 (2016, 2017)
        )
        years = (
            ["2016", "2017", "2018", "2019", "2021", "2022"]
            + ["2016", "2017", "2018"]
            + ["2016", "2017"]
        )
        obs = _make_obs_df(routes, years)
        obs = add_route_key(obs)

        # 50 % threshold → required = ceil(6 × 50/100) = 3
        filtered = filter_by_continuity(obs, eligible, 50.0)
        route_nums = sorted(
            set(k.split("_")[2] for k in filtered["RouteKey"].unique().to_list())
        )
        assert "001" in route_nums
        assert "002" in route_nums
        assert "003" not in route_nums

        # Apply stop guards then nullify out-of-bound stops
        guarded = filter_by_min_stops(filtered, 50)
        nullified = nullify_stops_beyond_total(guarded)
        # All rows have TotalStops=50, so no nulls should appear
        assert nullified["Stop50"].null_count() == 0


# ---------------------------------------------------------------------------
# §2.6 / §2.5 — Covariate Filtering Unit Tests
# ---------------------------------------------------------------------------


class TestFilterObserverTenure:
    """Unit tests for filter_observer_tenure."""

    @pytest.fixture
    def sample_df(self) -> pl.DataFrame:
        schema = {
            "RouteKey": pl.String,
            "Year": pl.String,
            "ObsN": pl.String,
            "RouteTenure": pl.Int32,
        }
        return pl.DataFrame(
            {
                "RouteKey": ["840_02_001", "840_02_001", "840_02_001", "840_02_002"],
                "Year": ["2015", "2016", "2017", "2018"],
                "ObsN": ["001", "001", "001", "002"],
                "RouteTenure": [1, 2, 5, None],
            },
            schema=schema,
        )

    def test_unbounded_returns_all_rows_including_nulls(self, sample_df: pl.DataFrame):
        res = filter_observer_tenure(sample_df, min_tenure=None, max_tenure=None)
        assert len(res) == len(sample_df)
        assert res["RouteTenure"].null_count() == 1

    def test_min_tenure_excludes_first_year_and_nulls(self, sample_df: pl.DataFrame):
        # min_tenure=2 excludes first-year (tenure=1) and null tenure
        res = filter_observer_tenure(sample_df, min_tenure=2)
        assert len(res) == 2
        assert sorted(res["RouteTenure"].to_list()) == [2, 5]

    def test_max_tenure_excludes_experienced_and_nulls(self, sample_df: pl.DataFrame):
        res = filter_observer_tenure(sample_df, max_tenure=2)
        assert len(res) == 2
        assert sorted(res["RouteTenure"].to_list()) == [1, 2]

    def test_min_and_max_tenure_range(self, sample_df: pl.DataFrame):
        res = filter_observer_tenure(sample_df, min_tenure=2, max_tenure=3)
        assert len(res) == 1
        assert res["RouteTenure"].to_list() == [2]

    def test_observer_tenure_column_name_fallback(self):
        schema = {"RouteKey": pl.String, "ObserverTenure": pl.Int32}
        df = pl.DataFrame(
            {"RouteKey": ["840_02_001", "840_02_002"], "ObserverTenure": [1, 4]},
            schema=schema,
        )
        res = filter_observer_tenure(df, min_tenure=2)
        assert len(res) == 1
        assert res["ObserverTenure"].to_list() == [4]

    def test_exclude_first_year(self, sample_df: pl.DataFrame):
        # exclude_first_year=True drops tenure=1 (first-year) and null tenure
        res = filter_observer_tenure(sample_df, exclude_first_year=True)
        assert len(res) == 2
        assert sorted(res["RouteTenure"].to_list()) == [2, 5]

    def test_cohort_filtering(self):
        schema = {"RouteKey": pl.String, "RouteTenure": pl.Int32}
        df = pl.DataFrame(
            {
                "RouteKey": ["R1", "R2", "R3", "R4", "R5"],
                "RouteTenure": [1, 2, 4, 6, None],
            },
            schema=schema,
        )
        # Novice only
        res_nov = filter_observer_tenure(df, cohorts=["Novice"])
        assert len(res_nov) == 1
        assert res_nov["RouteTenure"].to_list() == [1]

        # Intermediate only
        res_int = filter_observer_tenure(df, cohorts=["Intermediate"])
        assert len(res_int) == 2
        assert sorted(res_int["RouteTenure"].to_list()) == [2, 4]

        # Veteran only
        res_vet = filter_observer_tenure(df, cohorts=["Veteran"])
        assert len(res_vet) == 1
        assert res_vet["RouteTenure"].to_list() == [6]

        # Intermediate + Veteran
        res_combo = filter_observer_tenure(df, cohorts=["Intermediate", "Veteran"])
        assert len(res_combo) == 3
        assert sorted(res_combo["RouteTenure"].to_list()) == [2, 4, 6]

        # UI display option format (e.g. 'Intermediate (2-5 yrs)')
        res_ui = filter_observer_tenure(df, cohorts=["Intermediate (2-5 yrs)"])
        assert len(res_ui) == 2
        assert sorted(res_ui["RouteTenure"].to_list()) == [2, 4]

    def test_filter_by_observer_cohort_convenience(self):
        schema = {"RouteKey": pl.String, "RouteTenure": pl.Int32}
        df = pl.DataFrame(
            {"RouteKey": ["R1", "R2", "R3"], "RouteTenure": [1, 3, 7]},
            schema=schema,
        )
        res = filter_by_observer_cohort(df, cohorts=["Intermediate", "Veteran"])
        assert len(res) == 2
        assert sorted(res["RouteTenure"].to_list()) == [3, 7]

    def test_raises_type_error_on_non_dataframe(self):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            filter_observer_tenure([{"RouteTenure": 2}], min_tenure=1)  # type: ignore[arg-type]

    def test_raises_value_error_on_invalid_bounds(self, sample_df: pl.DataFrame):  # NEGATIVE
        with pytest.raises(ValueError, match="min_tenure"):
            filter_observer_tenure(sample_df, min_tenure=-1)

        with pytest.raises(ValueError, match="max_tenure"):
            filter_observer_tenure(sample_df, max_tenure=-5)

        with pytest.raises(ValueError, match="must be <= max_tenure"):
            filter_observer_tenure(sample_df, min_tenure=5, max_tenure=2)

    def test_raises_value_error_missing_column_when_bounded(self):  # NEGATIVE
        df = pl.DataFrame({"RouteKey": ["840_02_001"]}, schema={"RouteKey": pl.String})
        with pytest.raises(ValueError, match="Column 'RouteTenure' not found"):
            filter_observer_tenure(df, min_tenure=2)

    def test_raises_value_error_on_invalid_cohort(self, sample_df: pl.DataFrame):  # NEGATIVE
        with pytest.raises(ValueError, match="Invalid cohort 'Expert'"):
            filter_observer_tenure(sample_df, cohorts=["Expert"])

    def test_raises_value_error_on_empty_cohorts(self, sample_df: pl.DataFrame):  # NEGATIVE
        with pytest.raises(ValueError, match="cohorts must be a non-empty sequence"):
            filter_observer_tenure(sample_df, cohorts=[])


class TestFilterTraffic:
    """Unit tests for filter_traffic."""

    @pytest.fixture
    def sample_traffic_df(self) -> pl.DataFrame:
        schema = {
            "RouteDataID": pl.String,
            "CarTotal": pl.Int32,
            "CarsPerStop": pl.Float64,
        }
        return pl.DataFrame(
            {
                "RouteDataID": ["RD1", "RD2", "RD3", "RD4"],
                "CarTotal": [20, 100, 250, None],
                "CarsPerStop": [0.4, 2.0, 5.0, None],
            },
            schema=schema,
        )

    def test_unbounded_preserves_all_records_including_nulls(self, sample_traffic_df: pl.DataFrame):
        res = filter_traffic(sample_traffic_df)
        assert len(res) == len(sample_traffic_df)
        assert res["CarsPerStop"].null_count() == 1

    def test_max_cars_per_stop_filter(self, sample_traffic_df: pl.DataFrame):
        res = filter_traffic(sample_traffic_df, max_cars_per_stop=2.0)
        assert len(res) == 2
        assert res["RouteDataID"].to_list() == ["RD1", "RD2"]

    def test_max_car_total_filter(self, sample_traffic_df: pl.DataFrame):
        res = filter_traffic(sample_traffic_df, max_car_total=100)
        assert len(res) == 2
        assert res["RouteDataID"].to_list() == ["RD1", "RD2"]

    def test_combined_traffic_bounds(self, sample_traffic_df: pl.DataFrame):
        res = filter_traffic(sample_traffic_df, max_cars_per_stop=3.0, max_car_total=50)
        assert len(res) == 1
        assert res["RouteDataID"].to_list() == ["RD1"]

    def test_traffic_filter_does_not_drop_unbounded_nulls(self, sample_traffic_df: pl.DataFrame):
        df = pl.DataFrame(
            {
                "RouteDataID": ["RD1", "RD2"],
                "CarTotal": [50, 50],
                "CarsPerStop": [1.0, None],
            },
            schema={"RouteDataID": pl.String, "CarTotal": pl.Int32, "CarsPerStop": pl.Float64},
        )
        res = filter_traffic(df, max_car_total=100)
        assert len(res) == 2

    def test_raises_type_error_on_non_dataframe(self):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            filter_traffic([{"CarTotal": 10}], max_car_total=20)  # type: ignore[arg-type]

    def test_raises_value_error_on_negative_bounds(self, sample_traffic_df: pl.DataFrame):  # NEGATIVE
        with pytest.raises(ValueError, match="max_cars_per_stop"):
            filter_traffic(sample_traffic_df, max_cars_per_stop=-1.0)

        with pytest.raises(ValueError, match="max_car_total"):
            filter_traffic(sample_traffic_df, max_car_total=-10)

    def test_missing_column_logs_warning_and_returns_df_unchanged(self):  # NEGATIVE → graceful skip
        """Defensive guard: missing traffic columns emit a warning and skip the threshold.

        When CarsPerStop or CarTotal is not present (e.g. vehicle join hasn't
        happened yet), filter_traffic must log a warning and return the DataFrame
        unchanged rather than raising an unhandled ColumnNotFoundError.
        """
        df = pl.DataFrame({"RouteDataID": ["RD1", "RD2"]}, schema={"RouteDataID": pl.String})

        # CarsPerStop column absent — should warn and skip, returning df unchanged
        res_cars_per_stop = filter_traffic(df, max_cars_per_stop=5.0)
        assert len(res_cars_per_stop) == len(df), (
            "filter_traffic must return df unchanged when CarsPerStop column is missing."
        )
        assert res_cars_per_stop.columns == df.columns

        # CarTotal column absent — should warn and skip, returning df unchanged
        res_car_total = filter_traffic(df, max_car_total=100)
        assert len(res_car_total) == len(df), (
            "filter_traffic must return df unchanged when CarTotal column is missing."
        )
        assert res_car_total.columns == df.columns


class TestFiftyStopTemporalGuard:
    """Unit tests for enforce_fifty_stop_temporal_guard (§2.7 — 10-Stop vs 50-Stop boundary)."""

    def test_valid_start_year_returns_unchanged(self):
        """start_year >= 1997 must return bounds unchanged (no-op path)."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1997, end_year=2022, resolution="50stop"
        )
        assert start == 1997
        assert end == 2022

    def test_valid_start_year_none_uses_1966_but_still_below_1997_clamps(self):
        """start_year=None implies 1966, which is below 1997; must clamp to 1997."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=None, end_year=2022, resolution="50stop"
        )
        assert start == FIFTY_STOP_MIN_YEAR, (
            "start_year=None (1966) must be clamped to FIFTY_STOP_MIN_YEAR."
        )
        assert end == 2022

    def test_pre1997_start_year_with_valid_end_year_clamps_start(self):
        """start_year < 1997 with end_year >= 1997 clamps start_year to 1997."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1980, end_year=2010, resolution="50stop"
        )
        assert start == FIFTY_STOP_MIN_YEAR
        assert end == 2010

    def test_entirely_pre1997_range_raises_value_error(self):  # NEGATIVE
        """# NEGATIVE: start and end both < 1997 must raise ValueError."""
        with pytest.raises(ValueError, match="50-stop individual-stop records are only available from 1997"):
            enforce_fifty_stop_temporal_guard(
                start_year=1966, end_year=1996, resolution="50stop"
            )

    def test_ten_stop_resolution_is_noop(self):
        """resolution='10stop' must pass through any year range unchanged."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1966, end_year=1990, resolution="10stop"
        )
        assert start == 1966
        assert end == 1990

    def test_stop_range_beyond_10_triggers_fifty_stop_enforcement(self):
        """stop range with end_stop > 10 implicitly requires 50-stop data; guard must clamp."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1985,
            end_year=2010,
            resolution="10stop",    # overridden by stop range
            start_stop=1,
            end_stop=25,            # > 10 → 50-stop required
        )
        assert start == FIFTY_STOP_MIN_YEAR, (
            "stop range ending > stop 10 must trigger 50-stop temporal enforcement."
        )
        assert end == 2010

    def test_stop_range_within_10_stops_with_10stop_resolution_is_noop(self):
        """stop range [1, 10] with 10stop resolution must not clamp."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1966,
            end_year=1995,
            resolution="10stop",
            start_stop=1,
            end_stop=10,  # ≤ 10 → no enforcement
        )
        assert start == 1966
        assert end == 1995

    def test_open_ended_year_range_none_end_year_clamps_start_only(self):
        """open-ended end_year=None should clamp start_year but keep end_year=None."""
        start, end = enforce_fifty_stop_temporal_guard(
            start_year=1970, end_year=None, resolution="50stop"
        )
        assert start == FIFTY_STOP_MIN_YEAR
        assert end is None


