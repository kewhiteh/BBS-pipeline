"""Additive Phase 5 integration tests for CLI 50-stop spatial and vector export.

Invariants enforced:
- Backward compatibility: Running without additive flags matches v1.0 baseline.
- Graceful handling of missing polylines when --include-stops is passed without --shape.
- Proper delegation to export_vector_layers when --export-format is specified.
- Negative Assertion 1: Reject invalid --filter-mode strings with exit code 2.
- Negative Assertion 2: Reject non-existent file paths passed to --spatial-filter or --shape.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import pyogrio
import pytest
import requests_mock as requests_mock_module
from shapely.geometry import LineString, Polygon

from bbs_pipeline.cli import main, run_pipeline
from bbs_pipeline.client.sciencebase import DEFAULT_ITEM_ID
from tests.test_cli import (
    _make_in_memory_zip,
    _mock_fifty_stop_csv,
    _mock_routes_csv,
    _mock_species_list_csv,
    _mock_vehicle_csv,
    _mock_weather_csv,
)


@pytest.fixture
def mock_sciencebase_endpoints():
    """Sets up a complete set of mocked ScienceBase catalog and file download endpoints."""
    meta = {
        "id": DEFAULT_ITEM_ID,
        "title": "USGS Breeding Bird Survey Mocked Release",
        "files": [
            {"name": "routes.csv", "url": "https://sciencebase.gov/routes.csv"},
            {"name": "weather.csv", "url": "https://sciencebase.gov/weather.csv"},
            {
                "name": "VehicleData.csv",
                "url": "https://sciencebase.gov/VehicleData.csv",
            },
            {
                "name": "SpeciesList.csv",
                "url": "https://sciencebase.gov/SpeciesList.csv",
            },
            {
                "name": "MigrantNonBreeder.zip",
                "url": "https://sciencebase.gov/MigrantNonBreeder.zip",
            },
            {
                "name": "50-StopData.zip",
                "url": "https://sciencebase.gov/50-StopData.zip",
            },
        ],
    }

    migrant_csv = "AOU,Notes\n09999,Hypothetical Vagrant\n"
    migrant_zip_bytes = _make_in_memory_zip({"Migrants.csv": migrant_csv})
    fifty_zip_bytes = _make_in_memory_zip({"Fifty1.csv": _mock_fifty_stop_csv()})

    with requests_mock_module.Mocker() as m:
        m.get(
            f"https://www.sciencebase.gov/catalog/item/{DEFAULT_ITEM_ID}?format=json",
            json=meta,
        )
        m.get("https://sciencebase.gov/routes.csv", text=_mock_routes_csv())
        m.get("https://sciencebase.gov/weather.csv", text=_mock_weather_csv())
        m.get("https://sciencebase.gov/VehicleData.csv", text=_mock_vehicle_csv())
        m.get("https://sciencebase.gov/SpeciesList.csv", text=_mock_species_list_csv())
        m.get(
            "https://sciencebase.gov/MigrantNonBreeder.zip", content=migrant_zip_bytes
        )
        m.get("https://sciencebase.gov/50-StopData.zip", content=fifty_zip_bytes)
        yield m


@pytest.fixture
def sample_geojson_boundary(tmp_path: Path) -> Path:
    """Create a temporary GeoJSON boundary enclosing St. Florian (34.867, -87.616)

    while excluding Mount Hope (34.453, -87.478).
    """
    poly = Polygon(
        [
            (-87.8, 34.7),
            (-87.0, 34.7),
            (-87.0, 35.0),
            (-87.8, 35.0),
            (-87.8, 34.7),
        ]
    )
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    file_path = tmp_path / "boundary.geojson"
    gdf.to_file(file_path, driver="GeoJSON")
    return file_path


@pytest.fixture
def sample_route_shapefile(tmp_path: Path) -> Path:
    """Create a temporary shapefile with a 25-mile polyline for route 840_02_001."""
    # ~25 miles heading east from (-87.616, 34.867)
    line = LineString([(-87.616, 34.867), (-87.180, 34.867)])
    gdf = gpd.GeoDataFrame(
        {"RouteKey": ["840_02_001"], "Route": ["001"]},
        geometry=[line],
        crs="EPSG:4269",
    )
    file_path = tmp_path / "routes.shp"
    gdf.to_file(file_path)
    return file_path


class TestCliAdditiveFeatures:
    """Test suite verifying additive CLI arguments and execution routing."""

    def test_invocation_without_additive_flags_matches_baseline(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify backward compatibility: baseline invocation yields v1.0 schema."""
        out_file = tmp_path / "baseline_out.parquet"
        exit_code = main(
            [
                "-s",
                "AL",
                "--species",
                "07610",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "-f",
                "parquet",
                "-o",
                str(out_file),
            ]
        )
        assert exit_code == 0
        assert out_file.exists()

        tbl = pq.read_table(out_file)
        col_names = tbl.column_names
        # Baseline wide shape has Stop1..Stop50 and SpeciesTotal
        assert "Stop1" in col_names
        assert "Stop50" in col_names
        assert "SpeciesTotal" in col_names
        # Additive stop-level columns must NOT be present
        assert "StopNumber" not in col_names
        assert "StopDistanceMiles" not in col_names
        assert "GeometrySource" not in col_names

    def test_invocation_with_include_stops(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify --include-stops generates a stop-level dataset with fallback geometry."""
        out_file = tmp_path / "stops_out.parquet"
        exit_code = main(
            [
                "-s",
                "AL",
                "--species",
                "07610",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--include-stops",
                "-o",
                str(out_file),
                "-f",
                "parquet",
            ]
        )
        assert exit_code == 0
        assert out_file.exists()

        tbl = pq.read_table(out_file)
        col_names = tbl.column_names
        assert "StopNumber" in col_names
        assert "StopLatitude" in col_names
        assert "StopLongitude" in col_names
        assert "StopDistanceMiles" in col_names
        assert "GeometrySource" in col_names
        assert "Count" in col_names

        # Verify graceful fallback geometry without --shape
        sources = tbl["GeometrySource"].to_pylist()
        assert all(s == "origin_fallback" for s in sources)

    def test_graceful_handling_with_shapefile(
        self, mock_sciencebase_endpoints, sample_route_shapefile: Path, tmp_path: Path
    ) -> None:
        """Verify passing --shape with an existing shapefile interpolates from polylines."""
        out_file = tmp_path / "stops_interpolated.parquet"
        exit_code = main(
            [
                "-s",
                "AL",
                "--species",
                "07610",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--include-stops",
                "--shape",
                str(sample_route_shapefile),
                "-o",
                str(out_file),
                "-f",
                "parquet",
            ]
        )
        assert exit_code == 0
        assert out_file.exists()

        tbl = pq.read_table(out_file)
        # Route 840_02_001 should have route_vector_interpolated
        r1_rows = [
            tbl["GeometrySource"][i].as_py()
            for i in range(tbl.num_rows)
            if tbl["RouteKey"][i].as_py() == "840_02_001"
        ]
        assert len(r1_rows) > 0
        assert all(s == "route_vector_interpolated" for s in r1_rows)

    def test_invocation_with_spatial_filter_and_modes(
        self, mock_sciencebase_endpoints, sample_geojson_boundary: Path, tmp_path: Path
    ) -> None:
        """Verify --spatial-filter and --filter-mode prunes out-of-boundary routes."""
        out_file = tmp_path / "spatial_filtered.parquet"
        exit_code = main(
            [
                "-s",
                "AL",
                "--species",
                "07610",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--include-stops",
                "--spatial-filter",
                str(sample_geojson_boundary),
                "--filter-mode",
                "STRICT_CONTAINMENT",
                "-o",
                str(out_file),
                "-f",
                "parquet",
            ]
        )
        assert exit_code == 0
        assert out_file.exists()

        tbl = pq.read_table(out_file)
        retained_routes = set(tbl["RouteKey"].to_pylist())
        # Route 840_02_001 (St. Florian) is inside boundary; 840_02_002 (Mount Hope) is outside
        assert "840_02_001" in retained_routes
        assert "840_02_002" not in retained_routes

    def test_export_format_gpkg_delegation(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify --export-format gpkg delegates to export_vector_layers with dual layers."""
        out_gpkg = tmp_path / "export.gpkg"
        exit_code = main(
            [
                "-s",
                "AL",
                "--all-species",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--export-format",
                "gpkg",
                "-o",
                str(out_gpkg),
            ]
        )
        assert exit_code == 0
        assert out_gpkg.exists()

        layers = pyogrio.list_layers(out_gpkg)
        layer_names = [l[0] for l in layers]
        assert "bbs_routes" in layer_names
        assert "bbs_stops" in layer_names

    def test_export_format_fgb_delegation(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify --export-format fgb generates valid FlatGeobuf stream."""
        out_fgb = tmp_path / "export.fgb"
        exit_code = main(
            [
                "-s",
                "AL",
                "--all-species",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--export-format",
                "fgb",
                "-o",
                str(out_fgb),
            ]
        )
        assert exit_code == 0
        assert out_fgb.exists()
        raw_bytes = out_fgb.read_bytes()
        assert raw_bytes.startswith(b"fgb\x03")

    def test_export_format_parquet_delegation(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify --export-format parquet generates GeoParquet stream."""
        out_parquet = tmp_path / "export.parquet"
        exit_code = main(
            [
                "-s",
                "AL",
                "--all-species",
                "--start-year",
                "2018",
                "--end-year",
                "2019",
                "--export-format",
                "parquet",
                "-o",
                str(out_parquet),
            ]
        )
        assert exit_code == 0
        assert out_parquet.exists()
        raw_bytes = out_parquet.read_bytes()
        assert raw_bytes.startswith(b"PAR1")


class TestNegativeAssertionsCliAdditive:
    """Negative failure-path assertions for additive CLI flags."""

    def test_negative_invalid_filter_mode_exit_code_2(self) -> None:
        """# NEGATIVE: Reject invalid --filter-mode strings with exit code 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--filter-mode", "BOGUS_INVALID_MODE"])
        assert exc_info.value.code == 2

    def test_negative_nonexistent_spatial_filter_path(self) -> None:
        """# NEGATIVE: Reject non-existent file paths passed to --spatial-filter."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--spatial-filter", "nonexistent_path_to_boundary.geojson"])
        assert exc_info.value.code == 2

    def test_negative_nonexistent_shape_path(self) -> None:
        """# NEGATIVE: Reject non-existent file paths passed to --shape."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--shape", "nonexistent_path_to_routes.shp"])
        assert exc_info.value.code == 2

    def test_negative_run_pipeline_nonexistent_files_raise_value_error(self) -> None:
        """# NEGATIVE: Direct invocation of run_pipeline with bad paths raises ValueError."""
        with pytest.raises(ValueError, match="Spatial filter file does not exist"):
            run_pipeline(spatial_filter="nonexistent_filter.geojson")

        with pytest.raises(ValueError, match="Shapefile does not exist"):
            run_pipeline(shape="nonexistent_routes.shp")

        with pytest.raises(ValueError, match="Invalid filter mode"):
            run_pipeline(filter_mode="NONEXISTENT_MODE")
