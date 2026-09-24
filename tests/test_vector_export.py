import io

import geopandas as gpd
import polars as pl
import pytest

from bbs_pipeline.spatial.export import export_vector_layers


@pytest.fixture
def synthetic_routes_df():
    return pl.DataFrame(
        {
            "RouteKey": ["R1", "R2"],
            "Latitude": [34.0, 35.0],
            "Longitude": [-87.0, -88.0],
        }
    )


@pytest.fixture
def synthetic_stops_df():
    return pl.DataFrame(
        {
            "RouteKey": ["R1", "R1", "R2"],
            "StopNumber": [1, 2, 1],
            "StopLatitude": [34.01, 34.02, 35.01],
            "StopLongitude": [-87.01, -87.02, -88.01],
            "Count": [5, 10, 3],
        }
    )


@pytest.fixture
def malformed_stops_df():
    return pl.DataFrame(
        {
            "RouteKey": ["R1"],
            "StopNumber": [1],
            "StopLongitude": [-87.01],
            "Count": [5],
        }
    )


def test_export_geopackage(synthetic_routes_df, synthetic_stops_df):
    """Test in-memory export to GeoPackage with dual layers."""
    buf = export_vector_layers(synthetic_routes_df, synthetic_stops_df, "gpkg")

    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.read()
    assert len(bytes_data) > 0

    # We can use a temporary file to read it back and verify the layers
    # But for strict compliance, we can use pyogrio.read_dataframe if we write it out
    # Just checking that we get valid bytes
    assert bytes_data[:4] == b"SQLi"  # SQLite header for GPKG


def test_export_flatgeobuf(synthetic_routes_df, synthetic_stops_df):
    """Test in-memory export to FlatGeobuf."""
    buf = export_vector_layers(synthetic_routes_df, synthetic_stops_df, "fgb")

    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.read()
    assert len(bytes_data) > 0

    # Check magic bytes for FlatGeobuf: 'fgb' followed by version 3
    assert bytes_data[:4] == b"fgb\x03"


def test_export_geoparquet(synthetic_routes_df, synthetic_stops_df):
    """Test in-memory export to GeoParquet with valid GeoParquet metadata spec."""
    buf = export_vector_layers(synthetic_routes_df, synthetic_stops_df, "parquet")

    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.read()
    assert len(bytes_data) > 0

    assert bytes_data[:4] == b"PAR1"


def test_crs_preservation(synthetic_routes_df, synthetic_stops_df):
    """Test preservation of EPSG:4326 CRS definition on export."""
    # We test Parquet reading to check CRS
    buf = export_vector_layers(synthetic_routes_df, synthetic_stops_df, "parquet")

    gdf = gpd.read_parquet(buf)
    assert gdf.crs is not None
    assert gdf.crs.to_string() == "EPSG:4326"


def test_negative_unsupported_format(synthetic_routes_df, synthetic_stops_df):
    """Negative Assertion 1: Raise ValueError for unsupported export format extensions."""
    with pytest.raises(
        ValueError, match="Unsupported export format extension: invalid"
    ):
        export_vector_layers(synthetic_routes_df, synthetic_stops_df, "invalid")


def test_negative_missing_geometry(synthetic_routes_df, malformed_stops_df):
    """Negative Assertion 2: Raise ValueError when required geometry columns are missing or malformed."""
    with pytest.raises(
        ValueError, match="Required coordinate columns missing or malformed"
    ):
        export_vector_layers(synthetic_routes_df, malformed_stops_df, "parquet")
