"""Test suite for bbs_pipeline.core.zero_fill.

Verification gates (Phase 5, docs/04_TASKS.md §Task 5.3):
- 100% pass, zero unhandled warnings.
- Extirpation boundary preservation verified.
- Zero geographic species leakage verified.
- Discrete stop-count boundary guards verified (Stop_i = NULL for i > TotalStops).
- At least two explicit negative-failure assertions (marked with # NEGATIVE).
- Zero-disk in-memory mandate: all test data synthetic in RAM.
- Strict Polars type compliance (pl.String identifiers, pl.Int32 counts, no pl.Object).
"""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.core.zero_fill import (
    build_cartesian_grid,
    extract_valid_survey_years,
    get_confirmed_route_taxa,
    impute_zero_observations,
    zero_fill,
    zero_fill_route_observations,
)

# ---------------------------------------------------------------------------
# Synthetic DataFrame builders with explicit schemas (no inference)
# ---------------------------------------------------------------------------

_OBS_SCHEMA: dict[str, type[pl.DataType]] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
    "SpeciesTotal": pl.Int32,
    "RouteKey": pl.String,
}

_RUN_SCHEMA: dict[str, type[pl.DataType]] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "TotalStops": pl.Int32,
    "RouteKey": pl.String,
}


def _make_obs_row(
    route_key: str,
    year: str,
    aou: str,
    stop_counts: dict[int, int] | None = None,
    species_total: int | None = None,
    route_data_id: str = "RD001",
) -> dict[str, object]:
    """Create a single observation row adhering to _OBS_SCHEMA."""
    parts = route_key.split("_")
    country, state, route = parts[0], parts[1], parts[2]
    stops = {f"Stop{i}": 0 for i in range(1, 51)}
    if stop_counts:
        for s_idx, cnt in stop_counts.items():
            stops[f"Stop{s_idx}"] = cnt

    total = species_total if species_total is not None else sum(stops.values())
    return {
        "RouteDataID": route_data_id,
        "CountryNum": country,
        "StateNum": state,
        "Route": route,
        "RPID": "101",
        "Year": year,
        "AOU": aou.zfill(5),
        **stops,
        "SpeciesTotal": total,
        "RouteKey": route_key,
    }


def _make_run_row(
    route_key: str,
    year: str,
    total_stops: int = 50,
    route_data_id: str = "RD001",
) -> dict[str, object]:
    """Create a single survey run row adhering to _RUN_SCHEMA."""
    parts = route_key.split("_")
    return {
        "RouteDataID": route_data_id,
        "CountryNum": parts[0],
        "StateNum": parts[1],
        "Route": parts[2],
        "RPID": "101",
        "Year": year,
        "TotalStops": total_stops,
        "RouteKey": route_key,
    }


# ---------------------------------------------------------------------------
# Step 1: get_confirmed_route_taxa
# ---------------------------------------------------------------------------


class TestGetConfirmedRouteTaxa:
    def test_scans_history_and_finds_positive_counts(self) -> None:
        """Route taxa must only include species with at least 1 historical detection."""
        rows = [
            _make_obs_row("840_02_001", "1975", "07610", stop_counts={1: 3}),  # Robin: seen
            _make_obs_row("840_02_001", "1980", "04770", stop_counts={2: 0}, species_total=0),  # Jay: count=0
            _make_obs_row("840_02_001", "2010", "04950", stop_counts={5: 2}),  # Cowbird: seen
        ]
        history = pl.DataFrame(rows, schema=_OBS_SCHEMA)
        result = get_confirmed_route_taxa(history)

        aous = result["AOU"].to_list()
        assert "07610" in aous
        assert "04950" in aous
        assert "04770" not in aous  # Never confirmed (0 count)

    def test_target_species_restriction(self) -> None:
        """Only taxa in target_species are retained."""
        rows = [
            _make_obs_row("840_02_001", "1990", "07610", stop_counts={1: 2}),
            _make_obs_row("840_02_001", "1990", "04950", stop_counts={1: 5}),
        ]
        history = pl.DataFrame(rows, schema=_OBS_SCHEMA)
        result = get_confirmed_route_taxa(history, target_species=frozenset({"07610"}))

        aous = result["AOU"].to_list()
        assert aous == ["07610"]

    def test_min_and_max_year_bounds(self) -> None:
        """Records before min_year or after max_year are ignored."""
        rows = [
            _make_obs_row("840_02_001", "1960", "07610", stop_counts={1: 1}),  # Before BBS
            _make_obs_row("840_02_001", "1970", "04770", stop_counts={1: 1}),  # In range
            _make_obs_row("840_02_001", "2025", "04950", stop_counts={1: 1}),  # After max_year
        ]
        history = pl.DataFrame(rows, schema=_OBS_SCHEMA)
        result = get_confirmed_route_taxa(history, min_year=1966, max_year=2020)

        aous = result["AOU"].to_list()
        assert aous == ["04770"]

    def test_single_route_filter(self) -> None:
        """Route filter extracts taxa strictly for specified route."""
        rows = [
            _make_obs_row("840_02_001", "1990", "07610", stop_counts={1: 1}),
            _make_obs_row("840_02_002", "1990", "04770", stop_counts={1: 1}),
        ]
        history = pl.DataFrame(rows, schema=_OBS_SCHEMA)
        result = get_confirmed_route_taxa(history, route_key="840_02_001")

        assert result["RouteKey"].to_list() == ["840_02_001"]
        assert result["AOU"].to_list() == ["07610"]

    # --- NEGATIVE failure assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        """# NEGATIVE: non-DataFrame history raises TypeError."""
        with pytest.raises(TypeError, match="polars.DataFrame"):
            get_confirmed_route_taxa({"AOU": ["07610"]})  # type: ignore[arg-type]

    def test_raises_value_error_on_empty_dataframe(self) -> None:
        """# NEGATIVE: empty history raises ValueError."""
        empty_df = pl.DataFrame(schema=_OBS_SCHEMA)
        with pytest.raises(ValueError, match="empty"):
            get_confirmed_route_taxa(empty_df)

    def test_raises_value_error_on_missing_column(self) -> None:
        """# NEGATIVE: missing required AOU column raises ValueError."""
        df = pl.DataFrame({"Year": ["2015"], "RouteKey": ["840_02_001"]})
        with pytest.raises(ValueError, match="AOU"):
            get_confirmed_route_taxa(df)

    def test_raises_value_error_on_invalid_year_bounds(self) -> None:
        """# NEGATIVE: min_year > max_year raises ValueError."""
        df = pl.DataFrame(
            [_make_obs_row("840_02_001", "2015", "07610", stop_counts={1: 1})],
            schema=_OBS_SCHEMA,
        )
        with pytest.raises(ValueError, match="min_year"):
            get_confirmed_route_taxa(df, min_year=2020, max_year=2010)


# ---------------------------------------------------------------------------
# Step 2: extract_valid_survey_years
# ---------------------------------------------------------------------------


class TestExtractValidSurveyYears:
    def test_extracts_years_and_preserves_metadata(self) -> None:
        """Valid runs retain TotalStops and run identifiers."""
        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2018", total_stops=50),
                _make_run_row("840_02_001", "2019", total_stops=48),
            ],
            schema=_RUN_SCHEMA,
        )
        result = extract_valid_survey_years(runs)
        assert len(result) == 2
        assert result["Year"].to_list() == ["2018", "2019"]
        assert result["TotalStops"].to_list() == [50, 48]

    def test_eligible_years_filter(self) -> None:
        """Excludes survey years not in eligible set (e.g. 2020 COVID hiatus)."""
        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2019", total_stops=50),
                _make_run_row("840_02_001", "2020", total_stops=50),
                _make_run_row("840_02_001", "2021", total_stops=50),
            ],
            schema=_RUN_SCHEMA,
        )
        result = extract_valid_survey_years(
            runs, eligible_years=frozenset({2019, 2021})
        )
        assert result["Year"].to_list() == ["2019", "2021"]

    def test_default_total_stops_fallback(self) -> None:
        """If TotalStops is absent, default_total_stops fills the column."""
        runs = pl.DataFrame(
            {"RouteKey": ["840_02_001"], "Year": ["2018"]},
            schema={"RouteKey": pl.String, "Year": pl.String},
        )
        result = extract_valid_survey_years(runs, default_total_stops=50)
        assert result["TotalStops"].to_list() == [50]

    # --- NEGATIVE failure assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        """# NEGATIVE: non-DataFrame input raises TypeError."""
        with pytest.raises(TypeError, match="polars.DataFrame"):
            extract_valid_survey_years(["2018"])  # type: ignore[arg-type]

    def test_raises_value_error_missing_total_stops(self) -> None:
        """# NEGATIVE: missing TotalStops and no default raises ValueError."""
        runs = pl.DataFrame(
            {"RouteKey": ["840_02_001"], "Year": ["2018"]},
            schema={"RouteKey": pl.String, "Year": pl.String},
        )
        with pytest.raises(ValueError, match="TotalStops"):
            extract_valid_survey_years(runs)

    def test_raises_value_error_non_positive_total_stops(self) -> None:
        """# NEGATIVE: non-positive TotalStops raises ValueError."""
        runs = pl.DataFrame(
            {"RouteKey": ["840_02_001"], "Year": ["2018"], "TotalStops": [0]},
            schema={"RouteKey": pl.String, "Year": pl.String, "TotalStops": pl.Int32},
        )
        with pytest.raises(ValueError, match="positive"):
            extract_valid_survey_years(runs)


# ---------------------------------------------------------------------------
# Step 3: build_cartesian_grid (G_r = Y_r × S_r)
# ---------------------------------------------------------------------------


class TestBuildCartesianGrid:
    def test_cartesian_product_cardinality(self) -> None:
        """Grid cardinality must strictly equal |Y_r| × |S_r| for route r."""
        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2018"),
                _make_run_row("840_02_001", "2019"),
                _make_run_row("840_02_001", "2021"),
            ],
            schema=_RUN_SCHEMA,
        )  # 3 years
        taxa = pl.DataFrame(
            {
                "RouteKey": ["840_02_001", "840_02_001"],
                "AOU": ["07610", "04950"],
            },
            schema={"RouteKey": pl.String, "AOU": pl.String},
        )  # 2 species

        grid = build_cartesian_grid(runs, taxa)
        assert len(grid) == 3 * 2  # 6 rows

    def test_zero_geographic_leakage_in_grid(self) -> None:
        """Taxa confirmed on Route A must never appear in Route B's grid."""
        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2019"),
                _make_run_row("840_02_002", "2019"),
            ],
            schema=_RUN_SCHEMA,
        )
        taxa = pl.DataFrame(
            {
                "RouteKey": ["840_02_001", "840_02_002"],
                "AOU": ["07610", "04770"],  # 07610 only on 001, 04770 only on 002
            },
            schema={"RouteKey": pl.String, "AOU": pl.String},
        )

        grid = build_cartesian_grid(runs, taxa)
        assert len(grid) == 2

        # Route 001 must have ONLY 07610
        r1_aous = grid.filter(pl.col("RouteKey") == "840_02_001")["AOU"].to_list()
        assert r1_aous == ["07610"]
        assert "04770" not in r1_aous

        # Route 002 must have ONLY 04770
        r2_aous = grid.filter(pl.col("RouteKey") == "840_02_002")["AOU"].to_list()
        assert r2_aous == ["04770"]
        assert "07610" not in r2_aous

    # --- NEGATIVE failure assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        """# NEGATIVE: non-DataFrame input raises TypeError."""
        df = pl.DataFrame({"RouteKey": ["840_02_001"]})
        with pytest.raises(TypeError, match="polars.DataFrame"):
            build_cartesian_grid(df, {"RouteKey": ["840_02_001"]})  # type: ignore[arg-type]

    def test_raises_value_error_missing_route_key(self) -> None:
        """# NEGATIVE: missing RouteKey in taxa raises ValueError."""
        runs = pl.DataFrame(
            {"RouteKey": ["840_02_001"], "Year": ["2019"]},
            schema={"RouteKey": pl.String, "Year": pl.String},
        )
        taxa = pl.DataFrame({"AOU": ["07610"]}, schema={"AOU": pl.String})
        with pytest.raises(ValueError, match="RouteKey"):
            build_cartesian_grid(runs, taxa)


# ---------------------------------------------------------------------------
# Step 4: impute_zero_observations & Stop Boundary Guards
# ---------------------------------------------------------------------------


class TestImputeZeroObservations:
    def test_observed_row_preserves_counts(self) -> None:
        """Observed counts are retained for matched species."""
        grid = pl.DataFrame(
            [
                {
                    "RouteKey": "840_02_001",
                    "Year": "2019",
                    "AOU": "07610",
                    "TotalStops": 50,
                }
            ],
            schema={
                "RouteKey": pl.String,
                "Year": pl.String,
                "AOU": pl.String,
                "TotalStops": pl.Int32,
            },
        )
        obs = pl.DataFrame(
            [
                _make_obs_row(
                    "840_02_001",
                    "2019",
                    "07610",
                    stop_counts={1: 3, 2: 4},
                    species_total=7,
                )
            ],
            schema=_OBS_SCHEMA,
        )
        result = impute_zero_observations(grid, obs)
        assert len(result) == 1
        assert result["SpeciesTotal"][0] == 7
        assert result["Stop1"][0] == 3
        assert result["Stop2"][0] == 4
        assert result["Stop3"][0] == 0

    def test_unobserved_row_imputes_zeros(self) -> None:
        """Unobserved species receives zero for SpeciesTotal and Stop1..Stop50."""
        grid = pl.DataFrame(
            [
                {
                    "RouteKey": "840_02_001",
                    "Year": "2019",
                    "AOU": "07610",
                    "TotalStops": 50,
                }
            ],
            schema={
                "RouteKey": pl.String,
                "Year": pl.String,
                "AOU": pl.String,
                "TotalStops": pl.Int32,
            },
        )
        obs = pl.DataFrame(schema=_OBS_SCHEMA)  # No detections
        result = impute_zero_observations(grid, obs)
        assert len(result) == 1
        assert result["SpeciesTotal"][0] == 0
        for i in range(1, 51):
            assert result[f"Stop{i}"][0] == 0

    def test_48_stop_route_nullifies_stops_49_and_50(self) -> None:
        """For TotalStops=48: Stop1..Stop48 are 0, Stop49 and Stop50 are strictly NULL."""
        grid = pl.DataFrame(
            [
                {
                    "RouteKey": "840_02_001",
                    "Year": "2019",
                    "AOU": "07610",
                    "TotalStops": 48,
                }
            ],
            schema={
                "RouteKey": pl.String,
                "Year": pl.String,
                "AOU": pl.String,
                "TotalStops": pl.Int32,
            },
        )
        obs = pl.DataFrame(schema=_OBS_SCHEMA)  # Unobserved
        result = impute_zero_observations(grid, obs)

        assert result["SpeciesTotal"][0] == 0
        for i in range(1, 49):
            assert result[f"Stop{i}"][0] == 0, f"Stop{i} should be 0"
        assert result["Stop49"][0] is None, "Stop49 must strictly be NULL for 48-stop route"
        assert result["Stop50"][0] is None, "Stop50 must strictly be NULL for 48-stop route"

    def test_45_stop_route_nullifies_stops_46_to_50(self) -> None:
        """For TotalStops=45: Stop1..Stop45 are 0, Stop46..Stop50 are strictly NULL."""
        grid = pl.DataFrame(
            [
                {
                    "RouteKey": "840_02_001",
                    "Year": "2019",
                    "AOU": "07610",
                    "TotalStops": 45,
                }
            ],
            schema={
                "RouteKey": pl.String,
                "Year": pl.String,
                "AOU": pl.String,
                "TotalStops": pl.Int32,
            },
        )
        obs = pl.DataFrame(schema=_OBS_SCHEMA)
        result = impute_zero_observations(grid, obs)

        for i in range(1, 46):
            assert result[f"Stop{i}"][0] == 0
        for i in range(46, 51):
            assert result[f"Stop{i}"][0] is None, f"Stop{i} must be NULL"

    def test_observed_row_beyond_total_stops_nullified(self) -> None:
        """Even if observed row had values in Stop49 on a 48-stop run, Stop49 is NULL."""
        grid = pl.DataFrame(
            [
                {
                    "RouteKey": "840_02_001",
                    "Year": "2019",
                    "AOU": "07610",
                    "TotalStops": 48,
                }
            ],
            schema={
                "RouteKey": pl.String,
                "Year": pl.String,
                "AOU": pl.String,
                "TotalStops": pl.Int32,
            },
        )
        obs = pl.DataFrame(
            [
                _make_obs_row(
                    "840_02_001",
                    "2019",
                    "07610",
                    stop_counts={1: 2, 49: 99},  # Stop 49 erroneously has count
                )
            ],
            schema=_OBS_SCHEMA,
        )
        result = impute_zero_observations(grid, obs)

        assert result["Stop1"][0] == 2
        assert result["Stop48"][0] == 0
        assert result["Stop49"][0] is None  # Guarded: NULL, not 99
        assert result["Stop50"][0] is None

    # --- NEGATIVE failure assertions ---

    def test_raises_type_error_on_non_dataframe(self) -> None:
        """# NEGATIVE: non-DataFrame input raises TypeError."""
        with pytest.raises(TypeError, match="polars.DataFrame"):
            impute_zero_observations({"TotalStops": [50]}, pl.DataFrame())  # type: ignore[arg-type]

    def test_raises_value_error_missing_total_stops(self) -> None:
        """# NEGATIVE: missing TotalStops column without default raises ValueError."""
        grid = pl.DataFrame(
            {"RouteKey": ["840_02_001"], "Year": ["2019"], "AOU": ["07610"]},
            schema={"RouteKey": pl.String, "Year": pl.String, "AOU": pl.String},
        )
        obs = pl.DataFrame(schema=_OBS_SCHEMA)
        with pytest.raises(ValueError, match="TotalStops"):
            impute_zero_observations(grid, obs)


# ---------------------------------------------------------------------------
# Integrated Pipeline: Extirpation Preservation & Zero Geographic Leakage
# ---------------------------------------------------------------------------


class TestZeroFillPipelineIntegration:
    def test_extirpation_boundary_preservation(self) -> None:
        """Species detected in 1970 but absent in 2018–2019 receives zeros in 2018–2019.

        Verifies historical extirpation tracking: extirpated species remain
        in the route's longitudinal matrix with confirmed absence (0).
        """
        history = pl.DataFrame(
            [
                # Robin observed in 1970
                _make_obs_row("840_02_001", "1970", "07610", stop_counts={1: 4}),
                # Jay observed in 2018 and 2019
                _make_obs_row("840_02_001", "2018", "04770", stop_counts={2: 2}),
                _make_obs_row("840_02_001", "2019", "04770", stop_counts={2: 3}),
            ],
            schema=_OBS_SCHEMA,
        )

        target_runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2018", total_stops=50),
                _make_run_row("840_02_001", "2019", total_stops=50),
            ],
            schema=_RUN_SCHEMA,
        )

        result = zero_fill_route_observations(
            history_df=history,
            survey_runs_df=target_runs,
            observations_df=history,
        )

        # Both Robin (07610) and Jay (04770) must appear in 2018 and 2019
        assert len(result) == 4  # 2 years × 2 species

        # Robin was extirpated/absent in 2018 & 2019 -> count = 0
        robin_2018 = result.filter(
            (pl.col("AOU") == "07610") & (pl.col("Year") == "2018")
        )
        assert len(robin_2018) == 1
        assert robin_2018["SpeciesTotal"][0] == 0
        assert robin_2018["Stop1"][0] == 0

        robin_2019 = result.filter(
            (pl.col("AOU") == "07610") & (pl.col("Year") == "2019")
        )
        assert len(robin_2019) == 1
        assert robin_2019["SpeciesTotal"][0] == 0

        # Jay was observed in 2018 & 2019 -> retains observed counts
        jay_2018 = result.filter(
            (pl.col("AOU") == "04770") & (pl.col("Year") == "2018")
        )
        assert jay_2018["SpeciesTotal"][0] == 2
        assert jay_2018["Stop2"][0] == 2

    def test_zero_geographic_species_leakage(self) -> None:
        """Species never observed on Route B must NEVER leak into Route B's zero-fill.

        Scenario:
        - Route 001 (Alabama): Robin (07610) historically observed.
        - Route 002 (Alabama): Blue Jay (04770) historically observed.
        - Target species: Both {07610, 04770}.

        Assertion:
        - Route 001 matrix must NEVER contain Blue Jay (04770).
        - Route 002 matrix must NEVER contain Robin (07610).
        """
        history = pl.DataFrame(
            [
                _make_obs_row("840_02_001", "1995", "07610", stop_counts={1: 1}),
                _make_obs_row("840_02_002", "1995", "04770", stop_counts={1: 1}),
            ],
            schema=_OBS_SCHEMA,
        )

        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2019", total_stops=50),
                _make_run_row("840_02_002", "2019", total_stops=50),
            ],
            schema=_RUN_SCHEMA,
        )

        result = zero_fill(
            history_df=history,
            survey_runs_df=runs,
            target_species=frozenset({"07610", "04770"}),
        )

        r1_aous = result.filter(pl.col("RouteKey") == "840_02_001")["AOU"].to_list()
        r2_aous = result.filter(pl.col("RouteKey") == "840_02_002")["AOU"].to_list()

        assert "07610" in r1_aous
        assert "04770" not in r1_aous, "Blue Jay leaked into Route 001 where it was never seen!"

        assert "04770" in r2_aous
        assert "07610" not in r2_aous, "Robin leaked into Route 002 where it was never seen!"

    def test_discrete_stop_effort_with_mixed_routes(self) -> None:
        """Route with 48 stops nullifies 49-50 while 50-stop route retains 0s."""
        history = pl.DataFrame(
            [
                _make_obs_row("840_02_001", "1990", "07610", stop_counts={1: 2}),
                _make_obs_row("840_02_002", "1990", "07610", stop_counts={1: 3}),
            ],
            schema=_OBS_SCHEMA,
        )

        runs = pl.DataFrame(
            [
                _make_run_row("840_02_001", "2019", total_stops=50),
                _make_run_row("840_02_002", "2019", total_stops=48),
            ],
            schema=_RUN_SCHEMA,
        )

        result = zero_fill(history, runs)

        r1_row = result.filter(pl.col("RouteKey") == "840_02_001")
        assert r1_row["Stop48"][0] == 0
        assert r1_row["Stop49"][0] == 0
        assert r1_row["Stop50"][0] == 0

        r2_row = result.filter(pl.col("RouteKey") == "840_02_002")
        assert r2_row["Stop48"][0] == 0
        assert r2_row["Stop49"][0] is None
        assert r2_row["Stop50"][0] is None

    def test_arithmetic_typing_invariants(self) -> None:
        """Confirm all schema dtypes adhere strictly to Profile A invariants."""
        history = pl.DataFrame(
            [_make_obs_row("840_02_001", "2015", "07610", stop_counts={1: 1})],
            schema=_OBS_SCHEMA,
        )
        runs = pl.DataFrame(
            [_make_run_row("840_02_001", "2015", total_stops=50)],
            schema=_RUN_SCHEMA,
        )
        result = zero_fill(history, runs)

        # Identifiers must be pl.String
        assert result.schema["RouteKey"] == pl.String
        assert result.schema["Year"] == pl.String
        assert result.schema["AOU"] == pl.String

        # Counts must be pl.Int32
        assert result.schema["TotalStops"] == pl.Int32
        assert result.schema["SpeciesTotal"] == pl.Int32
        for i in range(1, 51):
            assert result.schema[f"Stop{i}"] == pl.Int32

        # pl.Object is banned
        assert pl.Object not in result.schema.values()
