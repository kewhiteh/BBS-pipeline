"""Test suite for spatial anchoring, format serialization, and provenance stamping.

Verification gates (Phase 6, docs/04_TASKS.md §Task 6.4):
- 100% pass across all export formats (GPKG, GeoJSON, Parquet, CSV).
- Zero unhandled warnings.
- Provenance metadata fields verified across all formats.
- Explicit negative failure assertions (marked with # NEGATIVE).
- Zero-disk in-memory verification (io.BytesIO) and terminal disk write verification.
"""

from __future__ import annotations

from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import geopandas as gpd
import polars as pl
import pyarrow.parquet as pq
import pyproj
import pytest
from shapely.geometry import Point

from bbs_pipeline.export.serializer import (
    PIPELINE_VERSION,
    export_to_csv,
    export_to_geojson,
    export_to_gpkg,
    export_to_parquet,
    get_pipeline_provenance,
    serialize_dataset,
    shape_dataset,
)
from bbs_pipeline.export.spatial import (
    CRS_PRESETS,
    anchor_routes_spatial,
    reproject_geodataframe,
    resolve_crs,
)


# ---------------------------------------------------------------------------
# Synthetic Test Fixtures (Strict Schemas, Zero Inference)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_routes_df() -> pl.DataFrame:
    """Route catalog DataFrame matching ROUTES_SCHEMA."""
    return pl.DataFrame(
        {
            "CountryNum": ["840", "840"],
            "StateNum": ["02", "02"],
            "Route": ["001", "002"],
            "RouteName": ["ST. FLORIAN", "MOUNT HOPE"],
            "Active": ["1", "1"],
            "Latitude": [34.867, 34.453],
            "Longitude": [-87.616, -87.478],
            "Stratum": ["02", "02"],
            "BCR": ["24", "24"],
            "RouteTypeID": ["1", "1"],
            "RouteTypeDetailID": ["1", "1"],
            "RouteKey": ["840_02_001", "840_02_002"],
        },
        schema_overrides={
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
            "RouteKey": pl.String,
        },
    )


@pytest.fixture
def sample_observations_df() -> pl.DataFrame:
    """Observations DataFrame with 50-stop counts and RouteKey."""
    stop_data = {f"Stop{i}": [i, i * 2] for i in range(1, 51)}
    return pl.DataFrame(
        {
            "RouteDataID": ["RD001", "RD002"],
            "CountryNum": ["840", "840"],
            "StateNum": ["02", "02"],
            "Route": ["001", "002"],
            "RouteKey": ["840_02_001", "840_02_002"],
            "RPID": ["101", "101"],
            "Year": ["2015", "2018"],
            "AOU": ["04940", "04940"],
            "SpeciesTotal": [1275, 2550],
            **stop_data,
        },
        schema_overrides={
            "RouteDataID": pl.String,
            "CountryNum": pl.String,
            "StateNum": pl.String,
            "Route": pl.String,
            "RouteKey": pl.String,
            "RPID": pl.String,
            "Year": pl.String,
            "AOU": pl.String,
            "SpeciesTotal": pl.Int32,
            **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
        },
    )


# ---------------------------------------------------------------------------
# 1. Spatial Anchoring & Projections (spatial.py)
# ---------------------------------------------------------------------------


class TestResolveCrs:
    """Unit tests for CRS validation and named preset resolution."""

    def test_resolves_standard_epsg_string(self) -> None:
        crs = resolve_crs("EPSG:4326")
        assert crs.to_epsg() == 4326

    def test_resolves_integer_epsg(self) -> None:
        crs = resolve_crs(5070)
        assert crs.to_epsg() == 5070

    def test_resolves_named_presets(self) -> None:
        assert resolve_crs("nc_state_plane").to_epsg() == 32119
        assert resolve_crs("conus_albers").to_epsg() == 5070
        assert resolve_crs("wgs84").to_epsg() == 4326

    def test_preserves_existing_pyproj_crs(self) -> None:
        original = pyproj.CRS.from_epsg(32119)
        assert resolve_crs(original) == original

    def test_raises_value_error_on_invalid_crs(self) -> None:
        # NEGATIVE: Unresolvable CRS string
        with pytest.raises(ValueError, match="Invalid or unresolvable CRS"):
            resolve_crs("EPSG:INVALID_CODE_99999")

    def test_raises_type_error_on_non_string_or_int(self) -> None:
        # NEGATIVE: Invalid input type for CRS
        with pytest.raises(TypeError, match="Expected CRS to be a string"):
            resolve_crs([4326])  # type: ignore[arg-type]


class TestSpatialAnchoring:
    """Unit and property tests for anchor_routes_spatial."""

    def test_anchor_route_origins_one_to_one(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(
            df=sample_observations_df,
            routes_df=sample_routes_df,
            target_crs="EPSG:4326",
        )
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 2
        assert gdf.crs.to_epsg() == 4326

        # Check exact origin point coordinates for first route (ST. FLORIAN: 34.867, -87.616)
        pt0 = gdf.iloc[0].geometry
        assert isinstance(pt0, Point)
        assert pytest.approx(pt0.x, rel=1e-5) == -87.616
        assert pytest.approx(pt0.y, rel=1e-5) == 34.867

        # Check exact origin point coordinates for second route (MOUNT HOPE: 34.453, -87.478)
        pt1 = gdf.iloc[1].geometry
        assert isinstance(pt1, Point)
        assert pytest.approx(pt1.x, rel=1e-5) == -87.478
        assert pytest.approx(pt1.y, rel=1e-5) == 34.453

    def test_reprojection_to_conus_albers(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(
            df=sample_observations_df,
            routes_df=sample_routes_df,
            target_crs="EPSG:5070",
        )
        assert gdf.crs.to_epsg() == 5070
        pt0 = gdf.iloc[0].geometry
        # EPSG:5070 coordinates for North Alabama are large planar meters
        assert pt0.x > 500_000
        assert pt0.y > 1_000_000

    def test_reprojection_to_nc_state_plane(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(
            df=sample_observations_df,
            routes_df=sample_routes_df,
            target_crs="nc_state_plane",
        )
        assert gdf.crs.to_epsg() == 32119

    def test_reproject_existing_geodataframe(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(
            df=sample_observations_df,
            routes_df=sample_routes_df,
            target_crs="EPSG:4326",
        )
        reprojected = reproject_geodataframe(gdf, target_crs="EPSG:5070")
        assert reprojected.crs.to_epsg() == 5070

    def test_auto_derives_route_key_if_missing(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        df_no_key = sample_observations_df.drop("RouteKey")
        routes_no_key = sample_routes_df.drop("RouteKey")
        gdf = anchor_routes_spatial(df=df_no_key, routes_df=routes_no_key)
        assert "RouteKey" in gdf.columns
        assert gdf.iloc[0]["RouteKey"] == "840_02_001"

    def test_raises_type_error_on_non_polars_inputs(
        self, sample_routes_df: pl.DataFrame
    ) -> None:
        # NEGATIVE: Non-Polars DataFrame input
        with pytest.raises(TypeError, match="Expected 'df' to be a polars.DataFrame"):
            anchor_routes_spatial(df={"a": [1]}, routes_df=sample_routes_df)  # type: ignore[arg-type]

    def test_raises_value_error_missing_coordinates_in_routes(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        # NEGATIVE: Missing coordinates in routes catalog
        bad_routes = sample_routes_df.drop("Latitude")
        with pytest.raises(ValueError, match="missing required coordinate columns"):
            anchor_routes_spatial(df=sample_observations_df, routes_df=bad_routes)


# ---------------------------------------------------------------------------
# 2. Tabular Output Shaping (serializer.py)
# ---------------------------------------------------------------------------


class TestShapeDataset:
    """Unit and property tests for wide vs long tabular reshaping."""

    def test_wide_shape_preserves_stop_columns(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        wide = shape_dataset(sample_observations_df, shape="wide")
        assert len(wide) == len(sample_observations_df)
        assert "Stop1" in wide.columns
        assert "Stop50" in wide.columns
        assert "StopNumber" not in wide.columns

    def test_long_shape_unpivots_stop_columns(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        long = shape_dataset(sample_observations_df, shape="long")
        # 2 original observation rows * 50 stops = 100 rows
        assert len(long) == 100
        assert "StopNumber" in long.columns
        assert "Count" in long.columns
        assert "Stop1" not in long.columns
        assert "Stop50" not in long.columns

        # Verify StopNumber is Int32 between 1 and 50
        stop_nums = long["StopNumber"].to_list()
        assert set(stop_nums) == set(range(1, 51))
        assert long.schema["StopNumber"] == pl.Int32

        # Verify counts match original wide values
        row1_stop1 = long.filter(
            (pl.col("RouteKey") == "840_02_001") & (pl.col("StopNumber") == 1)
        )["Count"][0]
        assert row1_stop1 == 1

        row2_stop50 = long.filter(
            (pl.col("RouteKey") == "840_02_002") & (pl.col("StopNumber") == 50)
        )["Count"][0]
        assert row2_stop50 == 100

    def test_raises_value_error_on_invalid_shape(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        # NEGATIVE: Unsupported shape
        with pytest.raises(ValueError, match="Unsupported shape"):
            shape_dataset(sample_observations_df, shape="matrix")

    def test_raises_value_error_on_long_shape_without_stops(self) -> None:
        # NEGATIVE: Long requested on DataFrame with no Stop columns
        df_no_stops = pl.DataFrame({"RouteKey": ["840_02_001"], "Year": ["2021"]})
        with pytest.raises(ValueError, match="no 'Stop\\*' count columns found"):
            shape_dataset(df_no_stops, shape="long")


# ---------------------------------------------------------------------------
# 3. Provenance Metadata Extraction (serializer.py)
# ---------------------------------------------------------------------------


class TestProvenanceMetadata:
    """Unit tests for pipeline provenance metadata extraction."""

    def test_provenance_contains_all_mandated_fields(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        prov = get_pipeline_provenance(sample_observations_df)
        assert "pipeline_git_hash" in prov
        assert len(prov["pipeline_git_hash"]) >= 7 or prov["pipeline_git_hash"] == "unknown"
        assert prov["dataset_min_year"] == "2015"
        assert prov["dataset_max_year"] == "2018"
        assert "extraction_timestamp" in prov
        # Verify valid ISO 8601 timestamp
        dt = datetime.fromisoformat(prov["extraction_timestamp"])
        assert dt is not None
        assert prov["pipeline_version"] == PIPELINE_VERSION

    def test_provenance_handles_custom_metadata(self) -> None:
        prov = get_pipeline_provenance(
            min_year=1970,
            max_year=2022,
            extra_metadata={"custom_filter": "true", "author": "pipeline_agent"},
        )
        assert prov["dataset_min_year"] == "1970"
        assert prov["dataset_max_year"] == "2022"
        assert prov["custom_filter"] == "true"
        assert prov["author"] == "pipeline_agent"


# ---------------------------------------------------------------------------
# 4. Format Serializers & Zero-Disk In-Memory Verification
# ---------------------------------------------------------------------------


class TestFormatSerialization:
    """Tests for export_to_parquet, export_to_csv, export_to_geojson, export_to_gpkg."""

    def test_parquet_export_in_memory_and_metadata(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        meta = {"pipeline_git_hash": "test1234", "dataset_min_year": "2015"}
        data = export_to_parquet(sample_observations_df, metadata=meta)
        assert isinstance(data, bytes)
        assert data[:4] == b"PAR1"

        # Read back table and verify schema metadata
        buf = io.BytesIO(data)
        tbl = pq.read_table(buf)
        read_meta = {k.decode("utf-8"): v.decode("utf-8") for k, v in tbl.schema.metadata.items()}
        assert read_meta["pipeline_git_hash"] == "test1234"
        assert read_meta["dataset_min_year"] == "2015"

    def test_csv_export_in_memory_and_comment_header(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        meta = {"pipeline_git_hash": "test1234", "pipeline_version": PIPELINE_VERSION}
        data = export_to_csv(sample_observations_df, metadata=meta)
        text = data.decode("utf-8")
        assert text.startswith("# pipeline_git_hash: test1234\n")
        assert "# pipeline_version: " in text

        # Parse CSV verifying comments are stripped
        read_df = pl.read_csv(io.StringIO(text), comment_prefix="#")
        assert len(read_df) == 2
        assert "RouteKey" in read_df.columns

    def test_geojson_export_in_memory_and_metadata(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(sample_observations_df, sample_routes_df)
        meta = {"pipeline_git_hash": "test1234", "dataset_max_year": "2018"}
        data = export_to_geojson(gdf, metadata=meta)
        assert isinstance(data, bytes)

        parsed = json.loads(data.decode("utf-8"))
        assert parsed["type"] == "FeatureCollection"
        assert "metadata" in parsed
        assert parsed["metadata"]["pipeline_git_hash"] == "test1234"
        assert len(parsed["features"]) == 2
        assert parsed["features"][0]["geometry"]["type"] == "Point"

    def test_gpkg_export_in_memory_and_sqlite_metadata_table(
        self, sample_observations_df: pl.DataFrame, sample_routes_df: pl.DataFrame
    ) -> None:
        gdf = anchor_routes_spatial(sample_observations_df, sample_routes_df)
        meta = {"pipeline_git_hash": "test1234", "dataset_min_year": "2015"}
        data = export_to_gpkg(gdf, metadata=meta)
        assert isinstance(data, bytes)
        assert len(data) > 0

        # Verify spatial layer read back via GeoPandas
        buf = io.BytesIO(data)
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=RuntimeWarning,
                message=".*has GPKG application_id.*",
            )
            read_gdf = gpd.read_file(buf, layer="bbs_observations")
        assert len(read_gdf) == 2
        assert read_gdf.iloc[0]["RouteKey"] == "840_02_001"

        # Verify pipeline_metadata table via SQLite
        con = sqlite3.connect(":memory:")
        con.deserialize(data)
        rows = dict(con.execute("SELECT key, value FROM pipeline_metadata").fetchall())
        con.close()
        assert rows["pipeline_git_hash"] == "test1234"
        assert rows["dataset_min_year"] == "2015"


# ---------------------------------------------------------------------------
# 5. Integrated serialize_dataset Pipeline Orchestrator
# ---------------------------------------------------------------------------


class TestSerializeDatasetOrchestrator:
    """Integrated tests exercising serialize_dataset across shapes and formats."""

    @pytest.mark.parametrize("fmt", ["parquet", "csv", "geojson", "gpkg"])
    def test_serialize_dataset_in_memory_wide(
        self,
        fmt: str,
        sample_observations_df: pl.DataFrame,
        sample_routes_df: pl.DataFrame,
    ) -> None:
        out = serialize_dataset(
            data=sample_observations_df,
            format=fmt,
            shape="wide",
            routes_df=sample_routes_df,
            target_crs="EPSG:4326",
        )
        assert isinstance(out, bytes)
        assert len(out) > 0

    @pytest.mark.parametrize("fmt", ["parquet", "csv", "geojson", "gpkg"])
    def test_serialize_dataset_in_memory_long(
        self,
        fmt: str,
        sample_observations_df: pl.DataFrame,
        sample_routes_df: pl.DataFrame,
    ) -> None:
        out = serialize_dataset(
            data=sample_observations_df,
            format=fmt,
            shape="long",
            routes_df=sample_routes_df,
            target_crs="EPSG:5070",
        )
        assert isinstance(out, bytes)
        assert len(out) > 0

    def test_serialize_dataset_disk_output_write(
        self,
        tmp_path: Path,
        sample_observations_df: pl.DataFrame,
        sample_routes_df: pl.DataFrame,
    ) -> None:
        out_file = tmp_path / "output.parquet"
        res = serialize_dataset(
            data=sample_observations_df,
            format="parquet",
            output_path=out_file,
            routes_df=sample_routes_df,
        )
        assert res == out_file
        assert out_file.exists()
        assert out_file.stat().st_size > 0

    def test_raises_value_error_on_unsupported_format(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        # NEGATIVE: Unsupported format
        with pytest.raises(ValueError, match="Unsupported format"):
            serialize_dataset(sample_observations_df, format="excel")

    def test_raises_value_error_spatial_export_without_routes_or_coords(
        self, sample_observations_df: pl.DataFrame
    ) -> None:
        # NEGATIVE: Spatial format requested without routes catalog or coordinates
        with pytest.raises(ValueError, match="requires spatial coordinates"):
            serialize_dataset(
                sample_observations_df,
                format="gpkg",
                routes_df=None,
            )
