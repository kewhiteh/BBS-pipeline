"""In-memory ZIP/CSV parser producing typed Polars DataFrames.

Zero-Disk In-Memory Mandate: All decompression and CSV parsing operate
exclusively on io.BytesIO buffers.  No data is written to disk.

Schema Invariant: Every CSV read supplies an explicit ``schema_overrides``
dictionary.  Polars schema inference is strictly prohibited.
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Dict

import polars as pl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical schema definitions (from docs/03_SCHEMAS.md)
# ---------------------------------------------------------------------------

#: Schema for 50-stop individual stop-count observation files.
FIFTY_STOP_SCHEMA: Dict[str, type] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
}

#: Schema for 10-stop count-band summary files.
TEN_STOP_SCHEMA: Dict[str, type] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    "Count10": pl.Int32,
    "Count20": pl.Int32,
    "Count30": pl.Int32,
    "Count40": pl.Int32,
    "Count50": pl.Int32,
    "StopTotal": pl.Int32,
    "SpeciesTotal": pl.Int32,
}

#: Schema for weather and operational history records.
WEATHER_SCHEMA: Dict[str, type] = {
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
}

#: Schema for vehicle and noise records.
VEHICLE_SCHEMA: Dict[str, type] = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "RecordedCar": pl.String,
    **{f"Car{i}": pl.Int32 for i in range(1, 51)},
    **{f"Noise{i}": pl.UInt8 for i in range(1, 51)},
}

#: Schema for route geographic directory.
ROUTES_SCHEMA: Dict[str, type] = {
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RouteName": pl.String,
    "Active": pl.String,
    "Latitude": pl.Float64,
    "Longitude": pl.Float64,
    "Stratum": pl.String,
    "BCR": pl.String,
    "RouteTypeID": pl.String,
    "RouteTypeDetailID": pl.String,
}

#: Schema for taxonomic species list.
SPECIES_LIST_SCHEMA: Dict[str, type] = {
    "Seq": pl.String,
    "AOU": pl.String,
    "English_Common_Name": pl.String,
    "French_Common_Name": pl.String,
    "Spanish_Common_Name": pl.String,
    "ORDER": pl.String,
    "Family": pl.String,
    "Genus": pl.String,
    "Species": pl.String,
}


# ---------------------------------------------------------------------------
# CSV parsing helpers
# ---------------------------------------------------------------------------


def _read_csv_from_bytes(
    data: bytes,
    schema_overrides: Dict[str, type],
    has_header: bool = True,
) -> pl.DataFrame:
    """Parse a CSV byte string into a typed Polars DataFrame.

    The operation is performed entirely in RAM via an :class:`io.BytesIO` buffer.
    Schema inference is **never** used; ``schema_overrides`` is mandatory.

    Parameters
    ----------
    data:
        Raw CSV bytes.
    schema_overrides:
        Explicit column → Polars type mapping.  Must cover all expected columns.
    has_header:
        Whether the first row is a header row.

    Returns
    -------
    pl.DataFrame
    """
    buf = io.BytesIO(data)
    return pl.read_csv(
        buf,
        schema_overrides=schema_overrides,
        has_header=has_header,
        null_values=["", "NA", "null", "NULL", "*", "None"],
        truncate_ragged_lines=True,
        try_parse_dates=False,
    )


# ---------------------------------------------------------------------------
# ZIP-level parsers
# ---------------------------------------------------------------------------


def parse_zip_csv(
    zip_buffer: io.BytesIO,
    csv_filename: str,
    schema_overrides: Dict[str, type],
) -> pl.DataFrame:
    """Extract a single CSV from an in-memory ZIP and parse it into a Polars DataFrame.

    Parameters
    ----------
    zip_buffer:
        In-memory ZIP archive positioned at any offset (will be seeked internally).
    csv_filename:
        Name of the CSV entry inside the archive (e.g. ``"routes.csv"``).
    schema_overrides:
        Explicit Polars schema overrides; inference is prohibited.

    Returns
    -------
    pl.DataFrame

    Raises
    ------
    zipfile.BadZipFile
        If ``zip_buffer`` does not contain a valid ZIP structure.
    KeyError
        If ``csv_filename`` is absent from the archive.
    """
    zip_buffer.seek(0)
    with zipfile.ZipFile(zip_buffer) as zf:
        logger.debug("ZIP members: %s", zf.namelist())
        raw_bytes = zf.read(csv_filename)
    return _read_csv_from_bytes(raw_bytes, schema_overrides=schema_overrides)


def parse_nested_zip_csv(
    outer_zip_buffer: io.BytesIO,
    inner_zip_name: str,
    csv_filename: str,
    schema_overrides: Dict[str, type],
) -> pl.DataFrame:
    """Parse a CSV from a ZIP nested inside another ZIP — entirely in RAM.

    This handles the BBS ``States.zip → {State}.zip → {State}.csv`` pattern.

    Parameters
    ----------
    outer_zip_buffer:
        In-memory buffer containing the outer ZIP archive.
    inner_zip_name:
        Name of the inner ZIP entry within the outer archive.
    csv_filename:
        Name of the CSV entry inside the inner archive.
    schema_overrides:
        Explicit Polars schema overrides.

    Returns
    -------
    pl.DataFrame

    Raises
    ------
    zipfile.BadZipFile
        If either ZIP layer is corrupt.
    KeyError
        If an expected entry is missing from either layer.
    """
    outer_zip_buffer.seek(0)
    with zipfile.ZipFile(outer_zip_buffer) as outer_zf:
        logger.debug("Outer ZIP members: %s", outer_zf.namelist())
        inner_bytes = outer_zf.read(inner_zip_name)

    inner_buf = io.BytesIO(inner_bytes)
    with zipfile.ZipFile(inner_buf) as inner_zf:
        logger.debug("Inner ZIP members: %s", inner_zf.namelist())
        raw_bytes = inner_zf.read(csv_filename)

    return _read_csv_from_bytes(raw_bytes, schema_overrides=schema_overrides)


# ---------------------------------------------------------------------------
# Domain-specific convenience parsers
# ---------------------------------------------------------------------------


def parse_fifty_stop(zip_buffer: io.BytesIO, csv_filename: str) -> pl.DataFrame:
    """Parse a 50-stop observation CSV from an in-memory ZIP archive.

    Parameters
    ----------
    zip_buffer:
        In-memory buffer of the 50-stop ZIP file.
    csv_filename:
        Name of the CSV entry (e.g. ``"fifty1.csv"``).

    Returns
    -------
    pl.DataFrame typed with :data:`FIFTY_STOP_SCHEMA`.
    """
    return parse_zip_csv(zip_buffer, csv_filename, FIFTY_STOP_SCHEMA)


def parse_ten_stop(zip_buffer: io.BytesIO, csv_filename: str) -> pl.DataFrame:
    """Parse a 10-stop count-band CSV from an in-memory ZIP archive.

    Parameters
    ----------
    zip_buffer:
        In-memory buffer of the state-level ZIP file.
    csv_filename:
        Name of the CSV entry (e.g. ``"Alabama.csv"``).

    Returns
    -------
    pl.DataFrame typed with :data:`TEN_STOP_SCHEMA`.
    """
    return parse_zip_csv(zip_buffer, csv_filename, TEN_STOP_SCHEMA)


def parse_weather(zip_buffer: io.BytesIO) -> pl.DataFrame:
    """Parse the ``weather.csv`` file from an in-memory ZIP archive.

    Parameters
    ----------
    zip_buffer:
        In-memory buffer containing ``weather.csv``.

    Returns
    -------
    pl.DataFrame typed with :data:`WEATHER_SCHEMA`.
    """
    return parse_zip_csv(zip_buffer, "weather.csv", WEATHER_SCHEMA)


def parse_vehicle(zip_buffer: io.BytesIO) -> pl.DataFrame:
    """Parse the ``VehicleData.csv`` file from an in-memory ZIP archive.

    Parameters
    ----------
    zip_buffer:
        In-memory buffer containing ``VehicleData.csv``.

    Returns
    -------
    pl.DataFrame typed with :data:`VEHICLE_SCHEMA`.
    """
    return parse_zip_csv(zip_buffer, "VehicleData.csv", VEHICLE_SCHEMA)


def parse_routes(zip_buffer: io.BytesIO) -> pl.DataFrame:
    """Parse the ``routes.csv`` file from an in-memory ZIP archive.

    Parameters
    ----------
    zip_buffer:
        In-memory buffer containing ``routes.csv``.

    Returns
    -------
    pl.DataFrame typed with :data:`ROUTES_SCHEMA`.
    """
    return parse_zip_csv(zip_buffer, "routes.csv", ROUTES_SCHEMA)


def parse_ten_stop_nested(
    states_zip_buffer: io.BytesIO, state_zip_name: str, state_csv_name: str
) -> pl.DataFrame:
    """Parse a state-level 10-stop CSV from the nested ``States.zip`` hierarchy.

    Parameters
    ----------
    states_zip_buffer:
        In-memory buffer of the outer ``States.zip``.
    state_zip_name:
        Name of the inner state ZIP entry (e.g. ``"Alabama.zip"``).
    state_csv_name:
        Name of the CSV entry within the inner ZIP (e.g. ``"Alabama.csv"``).

    Returns
    -------
    pl.DataFrame typed with :data:`TEN_STOP_SCHEMA`.
    """
    return parse_nested_zip_csv(
        states_zip_buffer, state_zip_name, state_csv_name, TEN_STOP_SCHEMA
    )
