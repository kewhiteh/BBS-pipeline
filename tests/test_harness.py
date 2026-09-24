"""Baseline testing harness verification tests with mandatory failure-path assertions."""

import io
import zipfile

import pytest

import bbs_pipeline


def test_package_scaffolding_import():
    """Verify bbs_pipeline package is discoverable and imports cleanly."""
    assert bbs_pipeline is not None


def test_synthetic_zip_fixture_in_memory(
    synthetic_zip_builder, sample_routes_csv_content
):
    """Verify baseline in-memory zip fixture functions entirely in RAM."""
    zip_buf = synthetic_zip_builder({"routes.csv": sample_routes_csv_content})
    assert isinstance(zip_buf, io.BytesIO)
    with zipfile.ZipFile(zip_buf) as zf:
        namelist = zf.namelist()
        assert "routes.csv" in namelist
        content = zf.read("routes.csv").decode("utf-8")
        assert "ST. FLORIAN" in content


def test_corrupted_stream_failure_assertion(corrupted_byte_stream):
    """Failure-path assertion 1: Unpacking corrupted stream raises BadZipFile."""
    with (
        pytest.raises(zipfile.BadZipFile),
        zipfile.ZipFile(corrupted_byte_stream) as zf,
    ):
        zf.namelist()


def test_empty_stream_failure_assertion(empty_byte_stream):
    """Failure-path assertion 2: Unpacking empty stream raises BadZipFile."""
    with (
        pytest.raises(zipfile.BadZipFile),
        zipfile.ZipFile(empty_byte_stream) as zf,
    ):
        zf.namelist()


def test_missing_archive_entry_failure_assertion(synthetic_zip_builder):
    """Failure-path assertion 3: Querying missing file within valid stream raises KeyError."""
    zip_buf = synthetic_zip_builder({"valid.txt": "sample data"})
    with zipfile.ZipFile(zip_buf) as zf, pytest.raises(KeyError):
        zf.read("non_existent_file.csv")
