"""Baseline test fixtures and synthetic in-memory generators for USGS BBS Pipeline."""

import io
import zipfile

import pytest


@pytest.fixture
def synthetic_zip_builder():
    """Generates an in-memory ZIP archive from a dict of {filename: content_str}."""

    def _build(files: dict[str, str]) -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for filename, content in files.items():
                zf.writestr(filename, content)
        buf.seek(0)
        return buf

    return _build


@pytest.fixture
def sample_routes_csv_content() -> str:
    """Provides sample Routes.csv data strictly adhering to ROUTES_SCHEMA."""
    return (
        "CountryNum,StateNum,Route,RouteName,Active,Latitude,Longitude,Stratum,BCR,RouteTypeID,RouteTypeDetailID\n"
        "840,02,001,ST. FLORIAN,1,34.867,-87.616,02,24,1,1\n"
        "840,02,002,MOUNT HOPE,1,34.453,-87.478,02,24,1,1\n"
    )


@pytest.fixture
def corrupted_byte_stream() -> io.BytesIO:
    """Provides a corrupted in-memory byte buffer for negative/failure-path assertions."""
    return io.BytesIO(b"PK\x00\x00\xffCORRUPTED_STREAM_DATA_NOT_A_VALID_ZIP")


@pytest.fixture
def empty_byte_stream() -> io.BytesIO:
    """Provides an empty in-memory byte stream for negative/failure-path assertions."""
    return io.BytesIO(b"")


# ---------------------------------------------------------------------------
# Phase 1 (v2.0 Additive): 50-Stop Ingestion Fixtures
# ---------------------------------------------------------------------------

#: Canonical header row matching the RAW_STOPS_SCHEMA (infer_schema_length=0
#: boundary — all columns arrive as pl.String).
_STOPS_HEADER: str = "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU," + ",".join(
    f"Stop{i}" for i in range(1, 51)
)


def _build_stop_row(
    route_data_id: str,
    country: str,
    state: str,
    route: str,
    rpid: str,
    year: str,
    aou: str,
    total_stops: int,
    base_count: int = 1,
) -> str:
    """Return a single CSV data row for a 50-stop observation record.

    Stops 1 .. total_stops are filled with *base_count*; stops beyond
    total_stops are left empty (which maps to the null token ``""`` and
    therefore to ``null`` after ingestion).  This exercises the
    scope-restricted zero-filling invariant.
    """
    stop_values = [str(base_count) if i <= total_stops else "" for i in range(1, 51)]
    fields = [route_data_id, country, state, route, rpid, year, aou] + stop_values
    return ",".join(fields)


def _build_fifty_csv(
    rows: list[tuple],
    total_stops_per_row: list[int],
    *,
    ragged: bool = False,
) -> str:
    """Build a multi-row CSV string simulating a fifty*.csv archive member.

    Parameters
    ----------
    rows:
        Sequence of (RouteDataID, CountryNum, StateNum, Route, RPID, Year, AOU)
        tuples.
    total_stops_per_row:
        Corresponding TotalStops value for each row — controls how many Stop
        columns are populated vs left empty.
    ragged:
        If True, appends an extra trailing comma to the last data row to
        simulate a ragged line (exercises ``truncate_ragged_lines=True``).
    """
    lines = [_STOPS_HEADER]
    for idx, (row, ts) in enumerate(zip(rows, total_stops_per_row)):
        data_line = _build_stop_row(*row, total_stops=ts)
        if ragged and idx == len(rows) - 1:
            data_line += ","  # inject ragged trailing field
        lines.append(data_line)
    return "\n".join(lines)


@pytest.fixture
def synthetic_stops_zip(synthetic_zip_builder) -> io.BytesIO:
    """Synthetic ``50-StopData.zip`` with ten members (``fifty1.csv`` …
    ``fifty10.csv``).

    Design points
    -------------
    - Members ``fifty1.csv`` .. ``fifty5.csv`` use ``TotalStops = 50``:
      all 50 stop columns are populated.
    - Members ``fifty6.csv`` .. ``fifty10.csv`` use ``TotalStops = 45``:
      stops 46–50 are intentionally left empty (``null`` after ingestion),
      exercising the null-preservation invariant.
    - The last row in ``fifty10.csv`` is ragged (extra trailing comma) to
      exercise ``truncate_ragged_lines=True``.
    - Null tokens ``NA``, ``null``, ``NULL``, ``*``, ``None`` appear in the
      ``AOU`` column of designated rows.
    """
    files: dict[str, str] = {}

    # ---- Members 1–5: TotalStops = 50 ----------------------------------------
    for member_idx in range(1, 6):
        base_row = (
            f"63000{member_idx}0251",  # RouteDataID
            "840",  # CountryNum
            f"{member_idx:02d}",  # StateNum
            "001",  # Route
            "101",  # RPID
            "2024",  # Year
            "07550",  # AOU (valid)
        )
        # Second row with a null-token AOU to verify null_values mapping.
        null_aou_tokens = ["NA", "null", "NULL", "*", "None"]
        null_token = null_aou_tokens[(member_idx - 1) % len(null_aou_tokens)]
        null_row = (
            f"63000{member_idx}0252",
            "840",
            f"{member_idx:02d}",
            "001",
            "101",
            "2023",
            null_token,  # should parse as null
        )
        csv_content = _build_fifty_csv(
            rows=[base_row, null_row],
            total_stops_per_row=[50, 50],
        )
        files[f"fifty{member_idx}.csv"] = csv_content

    # ---- Members 6–10: TotalStops = 45 ----------------------------------------
    for member_idx in range(6, 11):
        base_row = (
            f"63000{member_idx}0251",
            "840",
            f"{member_idx:02d}",
            "001",
            "101",
            "2024",
            "07550",
        )
        is_last = member_idx == 10
        csv_content = _build_fifty_csv(
            rows=[base_row],
            total_stops_per_row=[45],
            ragged=is_last,
        )
        files[f"fifty{member_idx}.csv"] = csv_content

    return synthetic_zip_builder(files)


@pytest.fixture
def minimal_stops_zip(synthetic_zip_builder) -> io.BytesIO:
    """Single-member zip (``fifty1.csv``) with two rows and ``TotalStops = 50``.

    Used by tests that need the smallest valid input to isolate specific
    ingestion behaviours (header stripping, composite keys, etc.).
    """
    row = ("6300010251", "840", "63", "003", "101", "2024", "07550")
    csv_content = _build_fifty_csv(rows=[row], total_stops_per_row=[50])
    # Inject a header with leading/trailing whitespace to exercise stripping.
    csv_content = csv_content.replace("RouteDataID", " RouteDataID", 1).replace(
        "Stop50", "Stop50 ", 1
    )
    return synthetic_zip_builder({"fifty1.csv": csv_content})


@pytest.fixture
def missing_headers_stops_zip(synthetic_zip_builder) -> io.BytesIO:
    """Zip with a ``fifty1.csv`` member that is missing the ``AOU`` column.

    Used by the negative assertion verifying that :func:`read_stops_data`
    raises a descriptive :exc:`ValueError` when mandatory headers are absent.
    """
    # Build a header missing AOU
    bad_header = "RouteDataID,CountryNum,StateNum,Route,RPID,Year," + ",".join(
        f"Stop{i}" for i in range(1, 51)
    )
    data_row = "6300010251,840,63,003,101,2024," + ",".join(["1"] * 50)
    csv_content = bad_header + "\n" + data_row
    return synthetic_zip_builder({"fifty1.csv": csv_content})
