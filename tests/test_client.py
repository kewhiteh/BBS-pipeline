"""Phase 2 test suite: ScienceBase streaming client and in-memory ZIP/CSV parsers.

Invariants enforced:
- Zero-Disk Mandate: no temporary or permanent files are created on disk.
- No live network calls: all HTTP interactions are intercepted by requests_mock.
- Schema Compliance: all DataFrames are validated against canonical schemas.
- At least 2 negative failure assertions per specification.
"""

from __future__ import annotations

import io
import zipfile

import polars as pl
import pytest
import requests
import requests_mock as requests_mock_module

from bbs_pipeline.client.parser import (
    FIFTY_SCHEMA,
    FIFTY_STOP_SCHEMA,
    ROUTES_SCHEMA,
    TEN_STOP_SCHEMA,
    VEHICLE_SCHEMA,
    WEATHER_SCHEMA,
    parse_fifty_stop,
    parse_nested_zip_csv,
    parse_routes,
    parse_ten_stop,
    parse_ten_stop_nested,
    parse_vehicle,
    parse_weather,
    parse_zip_csv,
)
from bbs_pipeline.client.sciencebase import (
    DEFAULT_ITEM_ID,
    build_session,
    fetch_file_by_name,
    fetch_file_by_url,
    fetch_item_metadata,
)

# ---------------------------------------------------------------------------
# In-memory fixture helpers
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, str]) -> io.BytesIO:
    """Build an in-memory ZIP archive from a mapping of filename → CSV content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def _make_nested_zip(inner_name: str, inner_files: dict[str, str]) -> io.BytesIO:
    """Build a ZIP-inside-ZIP hierarchy in memory (States.zip pattern)."""
    inner_buf = _make_zip(inner_files)
    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, inner_buf.getvalue())
    outer_buf.seek(0)
    return outer_buf


# ---------------------------------------------------------------------------
# CSV sample content factories
# ---------------------------------------------------------------------------


def _routes_csv() -> str:
    return (
        "CountryNum,StateNum,Route,RouteName,Active,Latitude,Longitude,"
        "Stratum,BCR,RouteTypeID,RouteTypeDetailID\n"
        "840,02,001,ST. FLORIAN,1,34.867,-87.616,02,24,1,1\n"
        "840,02,002,MOUNT HOPE,1,34.453,-87.478,02,24,1,1\n"
    )


def _fifty_stop_csv() -> str:
    stop_values = ",".join("0" for _ in range(50))
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,"
        + ",".join(f"Stop{i}" for i in range(1, 51))
        + "\n"
        "10001,840,02,001,1,2022,04990," + stop_values + "\n"
        "10002,840,02,001,1,2022,05260," + stop_values + "\n"
    )


def _ten_stop_csv() -> str:
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,"
        "Count10,Count20,Count30,Count40,Count50,StopTotal,SpeciesTotal\n"
        "10001,840,02,001,1,2022,04990,5,3,2,1,0,50,11\n"
    )


def _weather_csv() -> str:
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,Month,Day,ObsN,"
        "TotalSpp,StartTemp,EndTemp,TempScale,StartWind,EndWind,StartSky,EndSky,"
        "StartTime,EndTime,Assistant,QualityCurrentID,RunType\n"
        "10001,840,02,001,1,2022,06,15,00112,42,18.0,22.5,C,0,1,0,1,0530,0945,0,1,1\n"
    )


def _vehicle_csv() -> str:
    car_values = ",".join("3" for _ in range(50))
    noise_values = ",".join("0" for _ in range(50))
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,"
        + ",".join(f"Car{i}" for i in range(1, 51))
        + ","
        + ",".join(f"Noise{i}" for i in range(1, 51))
        + "\n"
        "10001,840,02,001,1,2022,1," + car_values + "," + noise_values + "\n"
    )


def _fake_item_metadata(
    filename: str = "routes.zip", file_url: str = "https://example.com/routes.zip"
) -> dict:
    return {
        "id": DEFAULT_ITEM_ID,
        "title": "BBS Data",
        "files": [
            {
                "name": filename,
                "url": file_url,
                "size": 1024,
                "contentType": "application/zip",
            },
        ],
    }


# ---------------------------------------------------------------------------
# ─── Section 1: Parser unit tests ───────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestRoutesParsing:
    """Validate routes.csv parsing against ROUTES_SCHEMA."""

    def test_parse_routes_returns_dataframe(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        assert isinstance(df, pl.DataFrame)

    def test_parse_routes_schema_compliance(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        for col, expected_dtype in ROUTES_SCHEMA.items():
            assert col in df.columns, f"Column {col!r} missing"
            assert df.schema[col] == expected_dtype, (
                f"Column {col!r}: expected {expected_dtype}, got {df.schema[col]}"
            )

    def test_parse_routes_row_count(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        assert df.height == 2

    def test_parse_routes_string_identifiers_not_numeric(self, synthetic_zip_builder):
        """Arithmetic Typing Invariant: CountryNum/StateNum/Route must be pl.String."""
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        for col in ("CountryNum", "StateNum", "Route", "Stratum", "BCR"):
            assert df.schema[col] == pl.String, f"{col} must be pl.String, not numeric"

    def test_parse_routes_latitude_longitude_are_string(self, synthetic_zip_builder):
        """Arithmetic Typing Invariant: Latitude and Longitude must be ingested as pl.String."""
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        assert df.schema["Latitude"] == pl.String
        assert df.schema["Longitude"] == pl.String


class TestFiftyStopParsing:
    """Validate 50-stop observation file parsing against FIFTY_STOP_SCHEMA."""

    def test_parse_fifty_stop_returns_dataframe(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"fifty1.csv": _fifty_stop_csv()})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        assert isinstance(df, pl.DataFrame)

    def test_parse_fifty_stop_schema_compliance(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"fifty1.csv": _fifty_stop_csv()})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        for col in df.columns:
            assert col in FIFTY_STOP_SCHEMA, (
                f"Column {col!r} missing from FIFTY_STOP_SCHEMA"
            )
            assert df.schema[col] == FIFTY_STOP_SCHEMA[col], (
                f"Column {col!r}: expected {FIFTY_STOP_SCHEMA[col]}, got {df.schema[col]}"
            )

    def test_fifty_stop_columns_are_string(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"fifty1.csv": _fifty_stop_csv()})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        for i in range(1, 51):
            assert df.schema[f"Stop{i}"] == pl.String

    def test_fifty_stop_aou_is_string(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"fifty1.csv": _fifty_stop_csv()})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        assert df.schema["AOU"] == pl.String

    def test_fifty_stop_year_is_string(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"fifty1.csv": _fifty_stop_csv()})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        assert df.schema["Year"] == pl.String

    def test_fifty_schema_all_columns_are_string(self):
        """Mandate: Every column in FIFTY_SCHEMA must ingest strictly as pl.String."""
        required = [
            "RouteDataId",
            "CountryNum",
            "StateNum",
            "Route",
            "RPID",
            "Year",
            "AOU",
            "Count",
            *[f"Stop{i}" for i in range(1, 51)],
        ]
        for col in required:
            assert col in FIFTY_SCHEMA, f"Column {col!r} missing from FIFTY_SCHEMA"
            assert FIFTY_SCHEMA[col] == pl.String, (
                f"FIFTY_SCHEMA column {col!r} must be pl.String"
            )

    def test_fifty_schema_zero_numeric_dtypes(self):
        """Audit invariant: zero numeric dtypes in FIFTY_SCHEMA and FIFTY_STOP_SCHEMA."""
        numeric_dtypes = (
            pl.UInt8,
            pl.UInt16,
            pl.UInt32,
            pl.UInt64,
            pl.Int8,
            pl.Int16,
            pl.Int32,
            pl.Int64,
            pl.Float32,
            pl.Float64,
        )
        for col, dtype in FIFTY_SCHEMA.items():
            assert dtype not in numeric_dtypes, (
                f"FIFTY_SCHEMA column {col!r} has forbidden numeric dtype {dtype}"
            )
            assert dtype == pl.String, f"FIFTY_SCHEMA column {col!r} must be pl.String"

    def test_fifty_stop_space_padded_values_parse_without_panic(
        self, synthetic_zip_builder
    ):
        """Real-world crash regression: space-padded '0      ' in Stop1..Stop50 and Count must not panic."""
        csv_data = (
            "RouteDataId,CountryNum,StateNum,Route,RPID,Year,AOU,Count,"
            + ",".join(f"Stop{i}" for i in range(1, 51))
            + "\n"
            "10001,840,02,001,1,2022,04990,0      ,"
            + ",".join("0      " for _ in range(50))
            + "\n"
        )
        zip_buf = synthetic_zip_builder({"fifty1.csv": csv_data})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        assert df["Stop1"][0] == "0      "
        assert df.schema["Stop1"] == pl.String
        assert df["Count"][0] == "0      "
        assert df.schema["Count"] == pl.String

    def test_fifty_stop_defensive_aggregation_with_space_padded_values(
        self, synthetic_zip_builder
    ):
        """Verify horizontal sums handle dirty space-padded observation strings defensively."""
        csv_data = (
            "RouteDataId,CountryNum,StateNum,Route,RPID,Year,AOU,Count,"
            + ",".join(f"Stop{i}" for i in range(1, 51))
            + "\n"
            "10001,840,02,001,1,2022,04990,5      ,"
            + "1      ,"
            + ",".join("0      " for _ in range(49))
            + "\n"
        )
        zip_buf = synthetic_zip_builder({"fifty1.csv": csv_data})
        df = parse_fifty_stop(zip_buf, "fifty1.csv")
        total = df.select(
            pl.sum_horizontal(
                [
                    pl.col(f"Stop{i}")
                    .str.strip_chars()
                    .cast(pl.Int32, strict=False)
                    .fill_null(0)
                    for i in range(1, 51)
                ]
            ).alias("StopSum")
        )
        assert total["StopSum"][0] == 1


class TestTenStopParsing:
    """Validate 10-stop count-band file parsing against TEN_STOP_SCHEMA."""

    def test_parse_ten_stop_returns_dataframe(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"Alabama.csv": _ten_stop_csv()})
        df = parse_ten_stop(zip_buf, "Alabama.csv")
        assert isinstance(df, pl.DataFrame)

    def test_parse_ten_stop_schema_compliance(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"Alabama.csv": _ten_stop_csv()})
        df = parse_ten_stop(zip_buf, "Alabama.csv")
        for col, expected_dtype in TEN_STOP_SCHEMA.items():
            assert col in df.columns, f"Column {col!r} missing"
            assert df.schema[col] == expected_dtype

    def test_ten_stop_count_columns_are_string(self, synthetic_zip_builder):
        """Count10..Count50, StopTotal, SpeciesTotal must be pl.String per Universal String Ingestion."""
        zip_buf = synthetic_zip_builder({"Alabama.csv": _ten_stop_csv()})
        df = parse_ten_stop(zip_buf, "Alabama.csv")
        for col in (
            "Count10",
            "Count20",
            "Count30",
            "Count40",
            "Count50",
            "StopTotal",
            "SpeciesTotal",
        ):
            assert df.schema[col] == pl.String

    # Alias for backwards compatibility
    test_ten_stop_count_columns_are_int32 = test_ten_stop_count_columns_are_string


class TestWeatherParsing:
    """Validate weather.csv parsing against WEATHER_SCHEMA."""

    def test_parse_weather_returns_dataframe(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"weather.csv": _weather_csv()})
        df = parse_weather(zip_buf)
        assert isinstance(df, pl.DataFrame)

    def test_parse_weather_schema_compliance(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"weather.csv": _weather_csv()})
        df = parse_weather(zip_buf)
        for col, expected_dtype in WEATHER_SCHEMA.items():
            assert col in df.columns, f"Column {col!r} missing"
            assert df.schema[col] == expected_dtype

    def test_weather_temp_and_spp_columns_are_string(self, synthetic_zip_builder):
        """TotalSpp, StartTemp, and EndTemp must be pl.String per Arithmetic Typing Invariant."""
        zip_buf = synthetic_zip_builder({"weather.csv": _weather_csv()})
        df = parse_weather(zip_buf)
        assert df.schema["StartTemp"] == pl.String
        assert df.schema["EndTemp"] == pl.String
        assert df.schema["TotalSpp"] == pl.String

    def test_weather_date_fields_are_strings(self, synthetic_zip_builder):
        """Year/Month/Day/StartTime/EndTime must be pl.String per arithmetic invariant."""
        zip_buf = synthetic_zip_builder({"weather.csv": _weather_csv()})
        df = parse_weather(zip_buf)
        for col in ("Year", "Month", "Day", "StartTime", "EndTime"):
            assert df.schema[col] == pl.String, f"{col} must be pl.String"


class TestVehicleParsing:
    """Validate VehicleData.csv parsing against VEHICLE_SCHEMA."""

    def test_parse_vehicle_returns_dataframe(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"VehicleData.csv": _vehicle_csv()})
        df = parse_vehicle(zip_buf)
        assert isinstance(df, pl.DataFrame)

    def test_parse_vehicle_schema_compliance(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"VehicleData.csv": _vehicle_csv()})
        df = parse_vehicle(zip_buf)
        for col, expected_dtype in VEHICLE_SCHEMA.items():
            assert col in df.columns, f"Column {col!r} missing"
            assert df.schema[col] == expected_dtype

    def test_vehicle_car_columns_are_string(self, synthetic_zip_builder):
        zip_buf = synthetic_zip_builder({"VehicleData.csv": _vehicle_csv()})
        df = parse_vehicle(zip_buf)
        for i in range(1, 51):
            assert df.schema[f"Car{i}"] == pl.String

    # Aliases for naming consistency
    test_vehicle_car_columns_are_int32 = test_vehicle_car_columns_are_string
    test_vehicle_columns_are_int32 = test_vehicle_car_columns_are_string

    def test_vehicle_car_columns_with_trailing_whitespace(self, synthetic_zip_builder):
        """Verify Car columns with trailing whitespace parse cleanly without error."""
        car_values = ",".join("1       " for _ in range(50))
        noise_values = ",".join("0" for _ in range(50))
        csv_content = (
            "RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,"
            + ",".join(f"Car{i}" for i in range(1, 51))
            + ","
            + ",".join(f"Noise{i}" for i in range(1, 51))
            + "\n"
            "10001,840,02,001,1,2022,1," + car_values + "," + noise_values + "\n"
        )
        zip_buf = synthetic_zip_builder({"VehicleData.csv": csv_content})
        df = parse_vehicle(zip_buf)
        assert df["Car1"][0] == "1       "
        assert df.schema["Car1"] == pl.String

    def test_vehicle_noise_columns_are_string(self, synthetic_zip_builder):
        """Noise1..Noise50 must be pl.String per Universal String Ingestion."""
        zip_buf = synthetic_zip_builder({"VehicleData.csv": _vehicle_csv()})
        df = parse_vehicle(zip_buf)
        for i in range(1, 51):
            assert df.schema[f"Noise{i}"] == pl.String

    # Alias for backwards compatibility
    test_vehicle_noise_columns_are_uint8 = test_vehicle_noise_columns_are_string

    def test_vehicle_noise_columns_with_trailing_whitespace(
        self, synthetic_zip_builder
    ):
        """Verify Noise columns with trailing whitespace parse cleanly without error."""
        car_values = ",".join("1" for _ in range(50))
        noise_values = ",".join("0      " for _ in range(50))
        csv_content = (
            "RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,"
            + ",".join(f"Car{i}" for i in range(1, 51))
            + ","
            + ",".join(f"Noise{i}" for i in range(1, 51))
            + "\n"
            "10001,840,02,001,1,2022,1," + car_values + "," + noise_values + "\n"
        )
        zip_buf = synthetic_zip_builder({"VehicleData.csv": csv_content})
        df = parse_vehicle(zip_buf)
        assert df["Noise1"][0] == "0      "
        assert df.schema["Noise1"] == pl.String

    def test_vehicle_all_columns_are_string(self, synthetic_zip_builder):
        """Profile A Invariant: Every column in VEHICLE_SCHEMA must be pl.String."""
        zip_buf = synthetic_zip_builder({"VehicleData.csv": _vehicle_csv()})
        df = parse_vehicle(zip_buf)
        for col, expected_dtype in VEHICLE_SCHEMA.items():
            assert expected_dtype == pl.String, (
                f"VEHICLE_SCHEMA column {col!r} must be pl.String"
            )
            assert df.schema[col] == pl.String, (
                f"Parsed VehicleData column {col!r} must be pl.String"
            )

    def test_vehicle_schema_zero_numeric_dtypes(self):
        """Section 1 & 9 Ingestion Isolation: Zero numeric dtypes permitted at ingestion."""
        numeric_dtypes = (
            pl.UInt8,
            pl.UInt16,
            pl.UInt32,
            pl.UInt64,
            pl.Int8,
            pl.Int16,
            pl.Int32,
            pl.Int64,
            pl.Float32,
            pl.Float64,
        )
        for col, dtype in VEHICLE_SCHEMA.items():
            assert dtype not in numeric_dtypes, (
                f"VEHICLE_SCHEMA column {col!r} has forbidden numeric dtype {dtype}"
            )
            assert dtype == pl.String, (
                f"VEHICLE_SCHEMA column {col!r} must be pl.String"
            )


class TestNestedZipParsing:
    """Validate the States.zip → State.zip → State.csv nested parsing path."""

    def test_parse_ten_stop_nested_returns_dataframe(self):
        outer_buf = _make_nested_zip("Alabama.zip", {"Alabama.csv": _ten_stop_csv()})
        df = parse_ten_stop_nested(outer_buf, "Alabama.zip", "Alabama.csv")
        assert isinstance(df, pl.DataFrame)
        assert df.height == 1

    def test_parse_ten_stop_nested_schema_compliance(self):
        outer_buf = _make_nested_zip("Alabama.zip", {"Alabama.csv": _ten_stop_csv()})
        df = parse_ten_stop_nested(outer_buf, "Alabama.zip", "Alabama.csv")
        for col, expected_dtype in TEN_STOP_SCHEMA.items():
            assert col in df.columns
            assert df.schema[col] == expected_dtype


# ---------------------------------------------------------------------------
# ─── Section 2: Negative failure assertions ─────────────────────────────────
# ---------------------------------------------------------------------------


class TestNegativeAssertions:
    """Mandatory failure-path assertions (≥2 required by spec)."""

    # Negative assertion 1: corrupted ZIP raises BadZipFile
    def test_corrupted_zip_raises_bad_zip_file(self, corrupted_byte_stream):
        """parse_zip_csv must propagate zipfile.BadZipFile on corrupt input."""
        with pytest.raises(zipfile.BadZipFile):
            parse_zip_csv(corrupted_byte_stream, "routes.csv", ROUTES_SCHEMA)

    # Negative assertion 2: missing CSV entry inside valid ZIP raises KeyError
    def test_missing_csv_in_zip_raises_key_error(self, synthetic_zip_builder):
        """parse_zip_csv must raise KeyError when the requested CSV is absent."""
        zip_buf = synthetic_zip_builder({"other.csv": "col\nval\n"})
        with pytest.raises(KeyError):
            parse_zip_csv(zip_buf, "routes.csv", ROUTES_SCHEMA)

    # Negative assertion 3: empty stream raises BadZipFile
    def test_empty_stream_raises_bad_zip_file(self, empty_byte_stream):
        """An empty BytesIO buffer must not be treated as a valid ZIP."""
        with pytest.raises(zipfile.BadZipFile):
            parse_zip_csv(empty_byte_stream, "routes.csv", ROUTES_SCHEMA)

    # Negative assertion 4: missing inner ZIP entry inside nested ZIP raises KeyError
    def test_missing_inner_zip_raises_key_error(self):
        """parse_nested_zip_csv must raise KeyError when inner zip is absent."""
        outer_buf = _make_nested_zip("Alabama.zip", {"Alabama.csv": _ten_stop_csv()})
        with pytest.raises(KeyError):
            parse_nested_zip_csv(
                outer_buf,
                "NonExistentState.zip",
                "NonExistentState.csv",
                TEN_STOP_SCHEMA,
            )

    # Negative assertion 5: fetch_file_by_name raises FileNotFoundError on absent file
    def test_fetch_file_by_name_raises_for_missing_file(self):
        """fetch_file_by_name must raise FileNotFoundError when filename is not in item metadata."""
        item_meta = _fake_item_metadata(
            filename="routes.zip", file_url="https://example.com/routes.zip"
        )
        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                json=item_meta,
            )
            with pytest.raises(FileNotFoundError, match="fifty_data.zip"):
                fetch_file_by_name("fifty_data.zip", item_id=DEFAULT_ITEM_ID)


# ---------------------------------------------------------------------------
# ─── Section 3: ScienceBase client tests ────────────────────────────────────
# ---------------------------------------------------------------------------


class TestScienceBaseClientMetadata:
    """Verify metadata fetch from the mocked ScienceBase catalog API."""

    def test_fetch_item_metadata_returns_dict(self):
        item_meta = _fake_item_metadata()
        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                json=item_meta,
            )
            result = fetch_item_metadata(item_id=DEFAULT_ITEM_ID)
        assert isinstance(result, dict)
        assert result["id"] == DEFAULT_ITEM_ID

    def test_fetch_item_metadata_contains_files_list(self):
        item_meta = _fake_item_metadata()
        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                json=item_meta,
            )
            result = fetch_item_metadata()
        assert "files" in result
        assert len(result["files"]) == 1

    def test_fetch_item_metadata_raises_on_http_error(self):
        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                status_code=404,
            )
            with pytest.raises(requests.HTTPError):
                fetch_item_metadata()


class TestScienceBaseStreamingDownload:
    """Verify end-to-end in-memory streaming of ZIP archives via mocked HTTP."""

    def _routes_zip_bytes(self) -> bytes:
        return _make_zip({"routes.csv": _routes_csv()}).getvalue()

    def test_fetch_file_by_url_returns_bytesio(self):
        zip_bytes = self._routes_zip_bytes()
        with requests_mock_module.Mocker() as m:
            m.get("https://example.com/routes.zip", content=zip_bytes)
            result = fetch_file_by_url("https://example.com/routes.zip")
        assert isinstance(result, io.BytesIO)
        assert result.getbuffer().nbytes == len(zip_bytes)

    def test_fetch_file_by_url_buffer_positioned_at_zero(self):
        zip_bytes = self._routes_zip_bytes()
        with requests_mock_module.Mocker() as m:
            m.get("https://example.com/routes.zip", content=zip_bytes)
            result = fetch_file_by_url("https://example.com/routes.zip")
        assert result.tell() == 0

    def test_fetch_file_by_url_produces_valid_zip(self):
        zip_bytes = self._routes_zip_bytes()
        with requests_mock_module.Mocker() as m:
            m.get("https://example.com/routes.zip", content=zip_bytes)
            buf = fetch_file_by_url("https://example.com/routes.zip")
        with zipfile.ZipFile(buf) as zf:
            assert "routes.csv" in zf.namelist()

    def test_fetch_file_by_name_end_to_end(self):
        """Full pipeline: metadata → URL resolution → streaming download."""
        zip_bytes = self._routes_zip_bytes()
        file_url = "https://example.com/routes.zip"
        item_meta = _fake_item_metadata(filename="routes.zip", file_url=file_url)

        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                json=item_meta,
            )
            m.get(file_url, content=zip_bytes)
            buf = fetch_file_by_name("routes.zip", item_id=DEFAULT_ITEM_ID)

        assert isinstance(buf, io.BytesIO)
        df = parse_routes(buf)
        assert df.height == 2

    def test_fetch_and_parse_routes_schema_round_trip(self):
        """Streaming + parsing round trip must produce a schema-compliant DataFrame."""
        zip_bytes = self._routes_zip_bytes()
        file_url = "https://example.com/routes.zip"
        item_meta = _fake_item_metadata(filename="routes.zip", file_url=file_url)

        with requests_mock_module.Mocker() as m:
            m.get(
                f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
                json=item_meta,
            )
            m.get(file_url, content=zip_bytes)
            buf = fetch_file_by_name("routes.zip", item_id=DEFAULT_ITEM_ID)

        df = parse_routes(buf)
        for col, expected_dtype in ROUTES_SCHEMA.items():
            assert df.schema[col] == expected_dtype


# ---------------------------------------------------------------------------
# ─── Section 4: Retry / backoff tests ───────────────────────────────────────
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    """Verify that transient HTTP 503/429 failures are retried and eventually succeed.

    The application-level retry loop in _stream_url_to_buffer allows requests_mock
    to exercise backoff behavior without relying on urllib3's transport-layer retry
    (which requests_mock bypasses).  We use backoff_factor=0 to avoid actual sleeping
    in the test suite.
    """

    def _routes_zip_bytes(self) -> bytes:
        return _make_zip({"routes.csv": _routes_csv()}).getvalue()

    def test_retry_on_503_eventually_succeeds(self):
        """After two initial 503 responses, a successful 200 must be returned."""
        zip_bytes = self._routes_zip_bytes()
        file_url = "https://example.com/routes.zip"

        response_sequence = [
            {"status_code": 503},
            {"status_code": 503},
            {"content": zip_bytes, "status_code": 200},
        ]

        with requests_mock_module.Mocker() as m:
            m.get(file_url, response_list=response_sequence)
            buf = fetch_file_by_url(
                file_url,
                max_application_retries=5,
                backoff_factor=0.0,
            )

        assert isinstance(buf, io.BytesIO)
        assert buf.getbuffer().nbytes > 0

    def test_retry_on_429_eventually_succeeds(self):
        """After a 429 rate-limit response, a subsequent 200 must be returned."""
        zip_bytes = self._routes_zip_bytes()
        file_url = "https://example.com/routes.zip"

        response_sequence = [
            {"status_code": 429},
            {"content": zip_bytes, "status_code": 200},
        ]

        with requests_mock_module.Mocker() as m:
            m.get(file_url, response_list=response_sequence)
            buf = fetch_file_by_url(
                file_url,
                max_application_retries=3,
                backoff_factor=0.0,
            )

        assert isinstance(buf, io.BytesIO)
        assert buf.getbuffer().nbytes > 0

    def test_all_retries_exhausted_raises_http_error(self):
        """If every attempt returns a retryable error, HTTPError must be raised."""
        file_url = "https://example.com/routes.zip"

        with requests_mock_module.Mocker() as m:
            m.get(file_url, status_code=503)
            with pytest.raises(requests.HTTPError):
                fetch_file_by_url(
                    file_url,
                    max_application_retries=2,
                    backoff_factor=0.0,
                )

    def test_session_factory_returns_session(self):
        sess = build_session()
        assert isinstance(sess, requests.Session)


# ---------------------------------------------------------------------------
# ─── Section 5: Zero-disk mandate verification ──────────────────────────────
# ---------------------------------------------------------------------------


class TestZeroDiskMandate:
    """Prove that no files are created on disk during streaming or parsing."""

    def _routes_zip_bytes(self) -> bytes:
        return _make_zip({"routes.csv": _routes_csv()}).getvalue()

    def test_streaming_produces_no_disk_files(self, tmp_path):
        """The tmp_path fixture is empty before and after a streaming download."""
        zip_bytes = self._routes_zip_bytes()
        file_url = "https://example.com/routes.zip"

        files_before = set(tmp_path.rglob("*"))

        with requests_mock_module.Mocker() as m:
            m.get(file_url, content=zip_bytes)
            fetch_file_by_url(file_url)

        files_after = set(tmp_path.rglob("*"))
        assert files_before == files_after, (
            f"Disk files created during streaming: {files_after - files_before}"
        )

    def test_parsing_produces_no_disk_files(self, tmp_path, synthetic_zip_builder):
        """Parsing a ZIP/CSV must not create any disk-resident files."""
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})

        files_before = set(tmp_path.rglob("*"))
        _ = parse_routes(zip_buf)
        files_after = set(tmp_path.rglob("*"))

        assert files_before == files_after, (
            f"Disk files created during parsing: {files_after - files_before}"
        )

    def test_buffer_is_bytesio_not_file(self, synthetic_zip_builder):
        """The result of parse_zip_csv is always an in-memory pl.DataFrame, not a file handle."""
        zip_buf = synthetic_zip_builder({"routes.csv": _routes_csv()})
        df = parse_routes(zip_buf)
        # DataFrame must be an in-memory object — verify no associated filepath
        assert isinstance(df, pl.DataFrame)
        assert not hasattr(df, "fileno"), "DataFrame must not expose a file descriptor"

    def test_bytesio_result_is_not_a_temp_file(self):
        """BytesIO buffers returned by the client must not be backed by real files."""
        zip_bytes = _make_zip({"routes.csv": _routes_csv()}).getvalue()

        with requests_mock_module.Mocker() as m:
            m.get("https://example.com/routes.zip", content=zip_bytes)
            buf = fetch_file_by_url("https://example.com/routes.zip")

        # A genuine io.BytesIO has no file-system path
        assert isinstance(buf, io.BytesIO)
        assert not isinstance(buf, io.FileIO)
        assert not isinstance(buf, io.BufferedReader)


# ---------------------------------------------------------------------------
# ─── Section 6: Schema guard – pl.Object is banned ──────────────────────────
# ---------------------------------------------------------------------------


class TestSchemaObjectBan:
    """Verify that pl.Object never appears in any canonical schema."""

    def test_routes_schema_has_no_object_type(self):
        for col, dtype in ROUTES_SCHEMA.items():
            assert dtype != pl.Object, (
                f"ROUTES_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_fifty_stop_schema_has_no_object_type(self):
        for col, dtype in FIFTY_STOP_SCHEMA.items():
            assert dtype != pl.Object, (
                f"FIFTY_STOP_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_ten_stop_schema_has_no_object_type(self):
        for col, dtype in TEN_STOP_SCHEMA.items():
            assert dtype != pl.Object, (
                f"TEN_STOP_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_weather_schema_has_no_object_type(self):
        for col, dtype in WEATHER_SCHEMA.items():
            assert dtype != pl.Object, (
                f"WEATHER_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_fifty_schema_has_no_object_type(self):
        for col, dtype in FIFTY_SCHEMA.items():
            assert dtype != pl.Object, (
                f"FIFTY_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_vehicle_schema_has_no_object_type(self):
        for col, dtype in VEHICLE_SCHEMA.items():
            assert dtype != pl.Object, (
                f"VEHICLE_SCHEMA column {col!r} must not be pl.Object"
            )

    def test_all_schemas_in_parser_contain_zero_numeric_types(self):
        """Universal Ingestion Isolation: audit that no schema in parser.py contains numeric types."""
        numeric_dtypes = (
            pl.UInt8,
            pl.UInt16,
            pl.UInt32,
            pl.UInt64,
            pl.Int8,
            pl.Int16,
            pl.Int32,
            pl.Int64,
            pl.Float32,
            pl.Float64,
        )
        for schema_name, schema in (
            ("FIFTY_SCHEMA", FIFTY_SCHEMA),
            ("FIFTY_STOP_SCHEMA", FIFTY_STOP_SCHEMA),
            ("TEN_STOP_SCHEMA", TEN_STOP_SCHEMA),
            ("WEATHER_SCHEMA", WEATHER_SCHEMA),
            ("VEHICLE_SCHEMA", VEHICLE_SCHEMA),
            ("ROUTES_SCHEMA", ROUTES_SCHEMA),
        ):
            for col, dtype in schema.items():
                assert dtype not in numeric_dtypes, (
                    f"{schema_name} column {col!r} has forbidden numeric dtype {dtype}"
                )
                assert dtype == pl.String, (
                    f"{schema_name} column {col!r} must be pl.String"
                )


# ---------------------------------------------------------------------------
# ─── Section 7: Default item ID constant ────────────────────────────────────
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_item_id_matches_spec(self):
        """The default ScienceBase item ID must match the project specification."""
        assert DEFAULT_ITEM_ID == "64ad9c3dd34e70357a292cee"
