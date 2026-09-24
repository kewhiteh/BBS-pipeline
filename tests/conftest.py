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
