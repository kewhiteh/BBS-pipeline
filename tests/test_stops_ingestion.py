"""Test suite for Phase 1 additive module: in-memory 50-stop ingestion engine.

Verifies all invariants mandated by docs/01_PLAYBOOK.md (§2.1, §2.2, §2.3,
§2.4) and docs/05_CONSTRAINTS.md.

Test categories
---------------
- Ingestion boundary isolation: all raw columns arrive as ``pl.String``.
- Header whitespace stripping.
- Null token vocabulary (``["", "NA", "null", "NULL", "*", "None"]``).
- Deferred arithmetic casting (``Stop1`` … ``Stop50`` → ``pl.Int32``).
- Scope-restricted null preservation: stops beyond ``TotalStops`` remain
  ``null`` and are never zero-filled.
- Composite key derivation (``RouteKey``).
- Negative Assertion 1: corrupt / non-zip stream → descriptive :exc:`ValueError`.
- Negative Assertion 2: missing mandatory headers → descriptive :exc:`ValueError`.
- Negative Assertion 3: unconducted stops (index > TotalStops) are NOT zero-filled.
"""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.ingestion.stops import cast_stop_columns, read_stops_data

# ---------------------------------------------------------------------------
# Test helpers / constants
# ---------------------------------------------------------------------------

_ALL_STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))

_MANDATORY_STRING_COLS: tuple[str, ...] = (
    "RouteDataID",
    "CountryNum",
    "StateNum",
    "Route",
    "RPID",
    "Year",
    "AOU",
    *_ALL_STOP_COLS,
)


# ===========================================================================
# Happy-path tests
# ===========================================================================


class TestIngestionBoundaryIsolation:
    """Verify that every column at the raw ingestion boundary is pl.String."""

    def test_all_columns_are_string_after_ingest(self, synthetic_stops_zip):
        """read_stops_data must return a LazyFrame where every raw column is String."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()

        for col in _MANDATORY_STRING_COLS:
            assert col in df.columns, f"Expected column '{col}' missing from result."
            actual_dtype = df[col].dtype
            assert actual_dtype == pl.String, (
                f"Column '{col}' must be pl.String at ingestion boundary; "
                f"got {actual_dtype}."
            )

    def test_routekey_is_string(self, synthetic_stops_zip):
        """RouteKey composite key must be pl.String."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()
        assert "RouteKey" in df.columns
        assert df["RouteKey"].dtype == pl.String

    def test_returns_lazy_frame(self, synthetic_stops_zip):
        """read_stops_data must return a pl.LazyFrame, not a DataFrame."""
        result = read_stops_data(synthetic_stops_zip)
        assert isinstance(result, pl.LazyFrame), (
            f"Expected pl.LazyFrame; got {type(result).__name__}."
        )

    def test_no_numeric_columns_at_boundary(self, synthetic_stops_zip):
        """No numeric dtype must appear in the raw output (pre-cast)."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()
        numeric_cols = [
            c
            for c in df.columns
            if df[c].dtype in (pl.Int32, pl.Int64, pl.Float32, pl.Float64)
        ]
        assert numeric_cols == [], (
            f"Numeric columns found at raw ingestion boundary (violates "
            f"infer_schema_length=0 invariant): {numeric_cols}"
        )


class TestHeaderWhitespaceStripping:
    """Verify that leading/trailing whitespace is stripped from all header names."""

    def test_whitespace_stripped_from_headers(self, minimal_stops_zip):
        """Headers with leading/trailing spaces must be stripped on ingestion."""
        lf = read_stops_data(minimal_stops_zip)
        df = lf.collect()
        # The fixture injects ' RouteDataID' and 'Stop50 ' — verify clean names.
        assert "RouteDataID" in df.columns, (
            "Leading whitespace was NOT stripped from 'RouteDataID' header."
        )
        assert "Stop50" in df.columns, (
            "Trailing whitespace was NOT stripped from 'Stop50' header."
        )
        # Confirm no column has residual whitespace.
        for col in df.columns:
            assert col == col.strip(), (
                f"Column '{col}' still has leading/trailing whitespace after ingestion."
            )


class TestNullTokenParsing:
    """Verify that all six null tokens map to native null values."""

    @pytest.mark.parametrize("null_token", ["", "NA", "null", "NULL", "*", "None"])
    def test_null_token_maps_to_null(self, synthetic_zip_builder, null_token):
        """Each null-vocabulary token must produce a null cell in the LazyFrame."""
        from tests.conftest import _STOPS_HEADER, _build_stop_row

        row = (
            "6300010251",
            "840",
            "63",
            "003",
            "101",
            "2024",
            null_token,  # AOU field — will be a null token
        )
        data_line = _build_stop_row(*row, total_stops=50)
        csv_content = _STOPS_HEADER + "\n" + data_line
        stream = synthetic_zip_builder({"fifty1.csv": csv_content})

        lf = read_stops_data(stream)
        df = lf.collect()

        assert df["AOU"][0] is None, (
            f"Null token '{null_token}' was NOT parsed as null in 'AOU' column; "
            f"got {df['AOU'][0]!r}."
        )

    def test_empty_stop_cells_parse_as_null(self, synthetic_stops_zip):
        """Stops beyond TotalStops (empty cells) must be null, not empty string."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()

        # Rows from members 6–10 have TotalStops=45; Stop46..Stop50 must be null.
        # Filter to member 6's StateNum ('06').
        subset = df.filter(pl.col("StateNum") == "06")
        assert len(subset) > 0, "No rows found for StateNum='06' (member 6)."

        for stop_idx in range(46, 51):
            col = f"Stop{stop_idx}"
            null_count = subset[col].null_count()
            assert null_count == len(subset), (
                f"Column '{col}' expected all-null for TotalStops=45 rows; "
                f"got {null_count} nulls out of {len(subset)} rows."
            )


class TestDeferredCasting:
    """Verify deferred casting of Stop columns via cast_stop_columns."""

    def test_cast_stop_columns_produces_int32(self, synthetic_stops_zip):
        """cast_stop_columns must convert Stop1..Stop50 to pl.Int32."""
        lf = read_stops_data(synthetic_stops_zip)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        for col in _ALL_STOP_COLS:
            actual = df[col].dtype
            assert actual == pl.Int32, (
                f"Column '{col}' must be pl.Int32 after cast; got {actual}."
            )

    def test_cast_preserves_identifier_dtypes(self, synthetic_stops_zip):
        """Identifier columns must remain pl.String after casting stop columns."""
        id_cols = (
            "RouteDataID",
            "CountryNum",
            "StateNum",
            "Route",
            "RPID",
            "Year",
            "AOU",
        )
        lf = read_stops_data(synthetic_stops_zip)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        for col in id_cols:
            actual = df[col].dtype
            assert actual == pl.String, (
                f"Identifier column '{col}' must remain pl.String after cast; "
                f"got {actual}."
            )

    def test_cast_non_parseable_strings_become_null(self, synthetic_zip_builder):
        """Non-integer stop values must cast to null with strict=False."""
        from tests.conftest import _STOPS_HEADER

        # Craft a row where Stop1 has a non-numeric value.
        stop_fields = ["bad_value"] + ["1"] * 49
        data_line = "6300010251,840,63,003,101,2024,07550," + ",".join(stop_fields)
        csv_content = _STOPS_HEADER + "\n" + data_line
        stream = synthetic_zip_builder({"fifty1.csv": csv_content})

        lf = read_stops_data(stream)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        assert df["Stop1"][0] is None, (
            "Non-parseable stop value 'bad_value' must cast to null with strict=False; "
            f"got {df['Stop1'][0]!r}."
        )
        # Stop2 should be 1.
        assert df["Stop2"][0] == 1

    def test_raw_schema_before_cast_is_string(self, synthetic_stops_zip):
        """Baseline: stop columns must be pl.String before cast_stop_columns is called."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()
        for col in _ALL_STOP_COLS:
            assert df[col].dtype == pl.String, (
                f"Pre-cast: '{col}' must be pl.String. Got {df[col].dtype}."
            )


class TestNullPreservation:
    """Verify scope-restricted null preservation for stops beyond TotalStops."""

    def test_stops_beyond_total_stops_are_null_after_cast(self, synthetic_stops_zip):
        """Stops k > TotalStops must remain null even after Int32 casting."""
        lf = read_stops_data(synthetic_stops_zip)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        # Rows from member 6 (StateNum='06') have TotalStops=45.
        subset = df.filter(pl.col("StateNum") == "06")
        assert len(subset) > 0

        for stop_idx in range(46, 51):
            col = f"Stop{stop_idx}"
            null_count = subset[col].null_count()
            assert null_count == len(subset), (
                f"After Int32 cast: '{col}' must be null for TotalStops=45 rows; "
                f"got {null_count}/{len(subset)} nulls."
            )

    def test_conducted_stops_are_non_null_after_cast(self, synthetic_stops_zip):
        """Stops 1..TotalStops must not be null for non-null-AOU rows."""
        lf = read_stops_data(synthetic_stops_zip)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        # Rows from member 1 (StateNum='01') with valid AOU='07550' TotalStops=50.
        subset = df.filter((pl.col("StateNum") == "01") & (pl.col("AOU") == "07550"))
        assert len(subset) > 0

        for stop_idx in range(1, 51):
            col = f"Stop{stop_idx}"
            non_null = subset[col].drop_nulls()
            assert len(non_null) == len(subset), (
                f"Stop '{col}' should be non-null for TotalStops=50 row; "
                f"got {len(non_null)}/{len(subset)} non-null values."
            )


class TestCompositeKeys:
    """Verify RouteKey composite key construction."""

    def test_route_key_format(self, minimal_stops_zip):
        """RouteKey must be CountryNum(3) + '_' + StateNum(2) + '_' + Route(3)."""
        lf = read_stops_data(minimal_stops_zip)
        df = lf.collect()

        # The minimal fixture has CountryNum='840', StateNum='63', Route='003'.
        expected_route_key = "840_63_003"
        actual = df["RouteKey"][0]
        assert actual == expected_route_key, (
            f"RouteKey mismatch: expected '{expected_route_key}', got '{actual}'."
        )

    def test_route_key_zero_padding(self, synthetic_zip_builder):
        """RouteKey components must be zero-padded to 3-3-3 digits."""
        from tests.conftest import _STOPS_HEADER, _build_stop_row

        # Use single-digit StateNum and Route to exercise zero-padding.
        row = ("9000010251", "124", "1", "1", "101", "2024", "07550")
        data_line = _build_stop_row(*row, total_stops=50)
        csv_content = _STOPS_HEADER + "\n" + data_line
        stream = synthetic_zip_builder({"fifty1.csv": csv_content})

        lf = read_stops_data(stream)
        df = lf.collect()

        route_key = df["RouteKey"][0]
        assert route_key == "124_01_001", (
            f"Zero-padded RouteKey expected '124_01_001', got '{route_key}'."
        )

    def test_route_key_dtype_is_string(self, minimal_stops_zip):
        """RouteKey must have dtype pl.String."""
        lf = read_stops_data(minimal_stops_zip)
        df = lf.collect()
        assert df["RouteKey"].dtype == pl.String

    def test_all_members_concatenated(self, synthetic_stops_zip):
        """All 10 zip members must be concatenated into a single LazyFrame."""
        lf = read_stops_data(synthetic_stops_zip)
        df = lf.collect()
        # 5 members × 2 rows + 5 members × 1 row = 15 rows total.
        assert len(df) == 15, f"Expected 15 rows from 10 members; got {len(df)}."


# ===========================================================================
# Negative assertions (failure-path tests)
# ===========================================================================


class TestNegativeAssertions:
    """Mandatory negative failure-path tests (Track B §4.1 minimum two required)."""

    # --- Negative Assertion 1: corrupt/non-zip stream -----------------------

    def test_corrupt_stream_raises_value_error(self, corrupted_byte_stream):
        """Negative 1a: Corrupt binary data (not a zip) must raise ValueError."""
        with pytest.raises(ValueError, match="not a valid zip archive"):
            read_stops_data(corrupted_byte_stream)

    def test_empty_stream_raises_value_error(self, empty_byte_stream):
        """Negative 1b: Completely empty byte stream must raise ValueError."""
        with pytest.raises(ValueError, match="not a valid zip archive"):
            read_stops_data(empty_byte_stream)

    def test_corrupt_stream_error_message_is_descriptive(self, corrupted_byte_stream):
        """Negative 1c: Error message from corrupt stream must be descriptive."""
        with pytest.raises(ValueError) as exc_info:
            read_stops_data(corrupted_byte_stream)
        error_msg = str(exc_info.value)
        assert "read_stops_data" in error_msg, (
            "Error message must name the failing function for diagnostics."
        )
        assert "zip" in error_msg.lower(), (
            "Error message must mention 'zip' to aid operator diagnosis."
        )

    # --- Negative Assertion 2: missing mandatory headers --------------------

    def test_missing_mandatory_header_raises_value_error(
        self, missing_headers_stops_zip
    ):
        """Negative 2a: Archive member lacking 'AOU' column must raise ValueError."""
        with pytest.raises(ValueError, match="missing mandatory headers"):
            read_stops_data(missing_headers_stops_zip)

    def test_missing_header_error_names_column(self, missing_headers_stops_zip):
        """Negative 2b: The ValueError must name the missing column."""
        with pytest.raises(ValueError) as exc_info:
            read_stops_data(missing_headers_stops_zip)
        error_msg = str(exc_info.value)
        assert "AOU" in error_msg, (
            f"Error must name the missing column 'AOU'; got: '{error_msg}'."
        )

    def test_missing_header_error_names_member(self, missing_headers_stops_zip):
        """Negative 2c: The ValueError must identify the offending archive member."""
        with pytest.raises(ValueError) as exc_info:
            read_stops_data(missing_headers_stops_zip)
        error_msg = str(exc_info.value)
        assert "fifty1.csv" in error_msg, (
            f"Error must name offending member 'fifty1.csv'; got: '{error_msg}'."
        )

    # --- Negative Assertion 3: unconducted stops beyond TotalStops NOT zero-filled

    def test_stops_beyond_total_stops_not_zero_filled(self, synthetic_stops_zip):
        """Negative 3: Stops k > TotalStops must be null, never 0 (zero-filled)."""
        lf = read_stops_data(synthetic_stops_zip)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        # TotalStops=45 rows come from members 6–10 (StateNum in ['06'..'10']).
        subset = df.filter(pl.col("StateNum").is_in(["06", "07", "08", "09", "10"]))
        assert len(subset) > 0, "Fixture must contain TotalStops=45 rows."

        for stop_idx in range(46, 51):
            col = f"Stop{stop_idx}"
            zero_count = (subset[col] == 0).sum()
            assert zero_count == 0, (
                f"Negative 3 FAIL: column '{col}' has {zero_count} zero-filled "
                f"values for rows with TotalStops=45. Unconducted stops must be "
                f"null, never 0."
            )

    def test_total_stops_45_rows_have_null_beyond_45(self, synthetic_zip_builder):
        """Negative 3b: Explicit single-row fixture confirming null, not 0, beyond TotalStops."""
        from tests.conftest import _STOPS_HEADER, _build_stop_row

        # Row with exactly 45 conducted stops (stops 46–50 empty → null).
        row = ("6300060251", "840", "06", "003", "101", "2024", "07550")
        data_line = _build_stop_row(*row, total_stops=45, base_count=2)
        csv_content = _STOPS_HEADER + "\n" + data_line
        stream = synthetic_zip_builder({"fifty1.csv": csv_content})

        lf = read_stops_data(stream)
        lf_cast = cast_stop_columns(lf)
        df = lf_cast.collect()

        # Conducted stops (1..45) must be non-null.
        for stop_idx in range(1, 46):
            val = df[f"Stop{stop_idx}"][0]
            assert val is not None, f"Stop{stop_idx} should be 2 (conducted), got None."
            assert val == 2, f"Stop{stop_idx} should be 2, got {val}."

        # Unconducted stops (46..50) must be null, not 0.
        for stop_idx in range(46, 51):
            val = df[f"Stop{stop_idx}"][0]
            assert val is None, (
                f"Negative 3b FAIL: Stop{stop_idx} is {val!r} — must be null "
                f"(unconducted stop beyond TotalStops=45). Zero-filling here "
                f"violates the scope-restricted invariant."
            )
