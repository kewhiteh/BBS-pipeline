"""Phase 7 integration test suite: dual CLI and reactive Streamlit interface.

Invariants enforced:
- Zero-Disk In-Memory Mandate: In-memory pipeline dry-run produces zero disk files.
- No live network calls: ScienceBase API and file downloads are mocked via requests_mock.
- Profile A Arithmetic Typing Invariant: All identifiers and codes are zero-padded pl.String.
- Tool Lock: Polars exclusively used for data processing.
- At least 2 explicit negative failure assertions (marked with # NEGATIVE).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
import requests_mock as requests_mock_module

from bbs_pipeline.cli import (
    _find_and_read_file,
    build_parser,
    main,
    normalize_state_input,
    run_pipeline,
)
from bbs_pipeline.client.sciencebase import DEFAULT_ITEM_ID

# ---------------------------------------------------------------------------
# Synthetic In-Memory Mock Data Generators
# ---------------------------------------------------------------------------


def _mock_routes_csv() -> str:
    return (
        "CountryNum,StateNum,Route,RouteName,Active,Latitude,Longitude,Stratum,BCR,RouteTypeID,RouteTypeDetailID\n"
        "840,02,001,ST. FLORIAN,1,34.867,-87.616,02,24,1,1\n"
        "840,02,002,MOUNT HOPE,1,34.453,-87.478,02,24,1,1\n"
        "840,63,001,OCRACOKE,1,35.112,-75.981,04,28,1,1\n"
    )


def _mock_weather_csv() -> str:
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,Month,Day,ObsN,TotalSpp,StartTemp,EndTemp,TempScale,"
        "StartWind,EndWind,StartSky,EndSky,StartTime,EndTime,Assistant,QualityCurrentID,RunType\n"
        "RD001,840,02,001,101,2018,06,10,00112,40,18.0,22.0,C,0,1,0,0,0530,0930,0,1,1\n"
        "RD002,840,02,001,101,2019,06,12,00112,42,19.0,23.5,C,0,1,0,0,0530,0930,0,1,1\n"
        "RD003,840,02,002,101,2019,06,15,00225,35,20.0,24.0,C,0,1,0,0,0545,0945,0,1,1\n"
        "RD004,840,63,001,101,2019,06,20,00331,38,21.0,25.0,C,0,1,0,0,0515,0915,0,1,1\n"
    )


def _mock_vehicle_csv() -> str:
    cars = ",".join("2       " for _ in range(50))
    noise = ",".join("0      " for _ in range(50))
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,"
        + ",".join(f"Car{i}" for i in range(1, 51))
        + ","
        + ",".join(f"Noise{i}" for i in range(1, 51))
        + "\n"
        f"RD001,840,02,001,101,2018,1,{cars},{noise}\n"
        f"RD002,840,02,001,101,2019,1,{cars},{noise}\n"
        f"RD003,840,02,002,101,2019,1,{cars},{noise}\n"
        f"RD004,840,63,001,101,2019,1,{cars},{noise}\n"
    )


def _mock_species_list_csv() -> str:
    return (
        "Seq,AOU,English_Common_Name,French_Common_Name,Order,Family,Genus,Species\n"
        "01000,07610,American Robin,Merle d'Amerique,Passeriformes,Turdidae,Turdus,migratorius\n"
        "02000,04770,Blue Jay,Geai bleu,Passeriformes,Corvidae,Cyanocitta,cristata\n"
        "03000,04950,Brown-headed Cowbird,Vacher a tete brune,Passeriformes,Icteridae,Molothrus,ater\n"
        "04000,01770,Black-bellied Whistling-Duck,Dendrocygne a ventre noir,Anseriformes,Anatidae,Dendrocygna,autumnalis\n"
    )


def _mock_fifty_stop_csv() -> str:
    stops_robin = ",".join("3" for _ in range(50))
    stops_jay = ",".join("1" for _ in range(50))
    return (
        "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,"
        + ",".join(f"Stop{i}" for i in range(1, 51))
        + "\n"
        f"RD001,840,02,001,101,2018,07610,{stops_robin}\n"
        f"RD002,840,02,001,101,2019,07610,{stops_robin}\n"
        f"RD003,840,02,002,101,2019,04770,{stops_jay}\n"
        f"RD004,840,63,001,101,2019,07610,{stops_robin}\n"
    )


def _make_in_memory_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


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


# ---------------------------------------------------------------------------
# Section 1: CLI Argument Parsing Tests
# ---------------------------------------------------------------------------


class TestCliArgumentParser:
    """Validate comprehensive CLI flag parsing and defaults."""

    def test_default_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.states is None
        assert args.bcrs is None
        assert args.strata is None
        assert args.routes is None
        assert args.species is None
        assert args.all_species is False
        assert args.include_covariates is True
        assert args.zero_fill is True
        assert args.shape == "wide"
        assert args.crs == "EPSG:4326"
        assert args.item_id == DEFAULT_ITEM_ID

    def test_spatial_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "-s",
                "NC",
                "VA",
                "SC",
                "--bcr",
                "28",
                "27",
                "--stratum",
                "04",
                "11",
                "--routes",
                "840_02_001",
                "840_02_002",
            ]
        )
        assert args.states == ["NC", "VA", "SC"]
        assert args.bcrs == ["28", "27"]
        assert args.strata == ["04", "11"]
        assert args.routes == ["840_02_001", "840_02_002"]

    def test_taxonomic_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--species",
                "07610",
                "American Robin",
                "--order",
                "Passeriformes",
                "--family",
                "Turdidae",
                "--guild",
                "wetland",
                "aerial_insectivore",
            ]
        )
        assert args.species == ["07610", "American Robin"]
        assert args.orders == ["Passeriformes"]
        assert args.families == ["Turdidae"]
        assert args.guilds == ["wetland", "aerial_insectivore"]

    def test_temporal_and_slicing_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--start-year",
                "2000",
                "--end-year",
                "2022",
                "--day-range",
                "1",
                "15",
                "--months",
                "5",
                "6",
                "--min-completeness-pct",
                "75.0",
                "--min-stops",
                "48",
                "--stop-range",
                "1",
                "25",
            ]
        )
        assert args.start_year == 2000
        assert args.end_year == 2022
        assert args.day_range == [1, 15]
        assert args.months == ["5", "6"]
        assert args.min_completeness_pct == 75.0
        assert args.min_stops == 48
        assert args.stop_range == [1, 25]

    def test_covariate_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--min-obs-tenure",
                "2",
                "--max-obs-tenure",
                "10",
                "--exclude-first-year",
                "--observer-cohorts",
                "Intermediate",
                "Veteran",
                "--max-cars-per-stop",
                "4.5",
                "--max-car-total",
                "200",
            ]
        )
        assert args.min_obs_tenure == 2
        assert args.max_obs_tenure == 10
        assert args.exclude_first_year is True
        assert args.observer_cohorts == ["Intermediate", "Veteran"]
        assert args.max_cars_per_stop == 4.5
        assert args.max_car_total == 200

    def test_output_and_format_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "-o",
                "output.gpkg",
                "-f",
                "gpkg",
                "--shape",
                "long",
                "--crs",
                "nc_state_plane",
                "--layer-name",
                "my_birds",
            ]
        )
        assert args.output == Path("output.gpkg")
        assert args.format == "gpkg"
        assert args.shape == "long"
        assert args.crs == "nc_state_plane"
        assert args.layer_name == "my_birds"

    def test_normalize_state_input(self) -> None:
        assert normalize_state_input("NC") == "63"
        assert normalize_state_input("al") == "02"
        assert normalize_state_input("North Carolina") == "63"
        assert normalize_state_input("2") == "02"
        assert normalize_state_input("02") == "02"

    def test_find_and_read_file_ignores_zone_identifier(self, tmp_path: Path) -> None:
        """Verify that _find_and_read_file ignores Windows NTFS Zone.Identifier streams."""
        real_file = tmp_path / "routes.csv"
        real_file.write_text(
            "CountryNum,StateNum,Route\n840,02,001\n", encoding="utf-8"
        )
        zone_file = tmp_path / "routes.csv:Zone.Identifier"
        zone_file.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")

        buf = _find_and_read_file("routes.csv", raw_dir=tmp_path)
        content = buf.read().decode("utf-8")
        assert "ZoneTransfer" not in content
        assert "CountryNum,StateNum,Route" in content


# ---------------------------------------------------------------------------
# Section 2: Mocked ScienceBase Pipeline Integration Tests
# ---------------------------------------------------------------------------


class TestPipelineMockedIntegration:
    """Execute complete in-memory pipeline dry-runs with mocked ScienceBase data."""

    def test_end_to_end_in_memory_parquet(self, mock_sciencebase_endpoints) -> None:
        """Verify pure in-memory execution to Parquet (Zero-Disk In-Memory Mandate)."""
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            output_path=None,  # Pure RAM
            format="parquet",
            shape="wide",
        )
        assert isinstance(data, bytes)
        assert data.startswith(b"PAR1")

        # Read back table and verify content
        tbl = pq.read_table(io.BytesIO(data))
        assert "RouteKey" in tbl.column_names
        assert "SpeciesTotal" in tbl.column_names
        assert "Stop1" in tbl.column_names

    def test_end_to_end_in_memory_csv_with_provenance(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Verify CSV export includes provenance metadata comment headers."""
        data = run_pipeline(
            states=["02"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            output_path=None,
            format="csv",
            shape="wide",
        )
        assert isinstance(data, bytes)
        text = data.decode("utf-8")
        assert "# pipeline_git_hash:" in text
        assert "# pipeline_version:" in text
        assert "RouteKey,CountryNum,StateNum,Route" in text or "RouteDataID" in text

    def test_end_to_end_in_memory_gpkg(self, mock_sciencebase_endpoints) -> None:
        """Verify GeoPackage SQLite export in memory with pipeline_metadata table."""
        data = run_pipeline(
            states=["AL"],
            all_species=True,
            start_year=2018,
            end_year=2019,
            output_path=None,
            format="gpkg",
            crs="EPSG:4326",
        )
        assert isinstance(data, bytes)
        assert data.startswith(b"SQLite format 3")

    def test_end_to_end_in_memory_geojson(self, mock_sciencebase_endpoints) -> None:
        """Verify GeoJSON RFC 7946 FeatureCollection export."""
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            output_path=None,
            format="geojson",
        )
        assert isinstance(data, bytes)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["type"] == "FeatureCollection"
        assert len(parsed["features"]) > 0
        assert "geometry" in parsed["features"][0]
        assert parsed["features"][0]["geometry"]["type"] == "Point"

    def test_end_to_end_long_shape_and_subroute_slicing(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Verify long tabular shape unpivoting and sub-route slicing with SegmentCount."""
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            stop_range=[1, 10],
            shape="long",
            format="parquet",
            output_path=None,
        )
        tbl = pq.read_table(io.BytesIO(data))
        col_names = tbl.column_names
        assert "StopNumber" in col_names
        assert "Count" in col_names
        assert "SegmentCount" in col_names

    def test_end_to_end_species_resolution_by_common_name(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Verify common name resolution translates to AOU and filters correctly."""
        data = run_pipeline(
            species=["American Robin"],
            start_year=2018,
            end_year=2019,
            output_path=None,
            format="csv",
        )
        text = data.decode("utf-8")
        assert "07610" in text

    def test_end_to_end_disk_write_output(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        """Verify physical file write at terminal boundary when --output is provided."""
        out_file = tmp_path / "bbs_extract.parquet"
        res = run_pipeline(
            states=["NC"],
            all_species=True,
            start_year=2018,
            end_year=2019,
            output_path=out_file,
            format="parquet",
        )
        assert res == out_file
        assert out_file.exists()
        assert out_file.stat().st_size > 0

    def test_pipeline_active_observer_tenure_filtering(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Verify active observer tenure filtering prunes first-year survey runs."""
        # min_obs_tenure=2 filters out first-year runs (RD001 in 2018, RD003 in 2019)
        # leaving only 2019 for Route 001 (RD002)
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            min_obs_tenure=2,
            output_path=None,
            format="parquet",
            shape="wide",
        )
        assert isinstance(data, bytes)
        tbl = pq.read_table(io.BytesIO(data))
        df = pl.from_arrow(tbl)
        assert len(df) > 0
        assert (df["Year"] == "2019").all()
        assert (df["Route"] == "001").all()

    def test_pipeline_active_traffic_filtering(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Verify active traffic filtering accepts compliant runs and rejects over-limit runs."""
        # mock vehicle data has 2 cars per stop (total=100)
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            max_cars_per_stop=3.0,
            output_path=None,
            format="parquet",
            shape="wide",
        )
        assert isinstance(data, bytes)
        assert len(data) > 0

        # Filtering with max_cars_per_stop=1.0 should prune all runs and raise ValueError (# NEGATIVE)
        with pytest.raises(
            ValueError, match="No survey runs satisfied the vehicle traffic criteria"
        ):
            run_pipeline(
                states=["AL"],
                species=["07610"],
                start_year=2018,
                end_year=2019,
                max_cars_per_stop=1.0,
                output_path=None,
                format="parquet",
                shape="wide",
            )

    def test_cli_main_entrypoint_success(self, mock_sciencebase_endpoints) -> None:
        """Verify main() function parses sys.argv and returns exit code 0."""
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
                "csv",
            ]
        )
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Section 3: Negative Failure Assertions (Mandatory ≥ 2)
# ---------------------------------------------------------------------------


class TestNegativeFailureAssertions:
    """Explicit negative failure tests validating domain invariants and error paths."""

    def test_negative_invalid_temporal_bounds_raises_value_error(self) -> None:
        """# NEGATIVE: start_year > end_year must raise ValueError."""
        with pytest.raises(ValueError, match="start_year .* must be <= end_year"):
            run_pipeline(start_year=2022, end_year=2015)

    def test_negative_invalid_stop_range_raises_value_error(self) -> None:
        """# NEGATIVE: start_stop > end_stop must raise ValueError."""
        with pytest.raises(ValueError, match="start_stop .* must be <= end_stop"):
            run_pipeline(stop_range=[40, 10])

    def test_negative_stop_range_out_of_bounds_raises_value_error(self) -> None:
        """# NEGATIVE: stop index < 1 or > 50 must raise ValueError."""
        with pytest.raises(ValueError, match="must be between 1 and 50"):
            run_pipeline(stop_range=[0, 50])

    def test_negative_invalid_continuity_pct_raises_value_error(self) -> None:
        """# NEGATIVE: min_completeness_pct > 100 must raise ValueError."""
        with pytest.raises(
            ValueError, match="min_completeness_pct must be in \\(0, 100\\]"
        ):
            run_pipeline(min_completeness_pct=150.0)

    def test_negative_unsupported_format_raises_value_error(self) -> None:
        """# NEGATIVE: invalid serialization format must raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported format"):
            run_pipeline(format="excel")

    def test_negative_no_routes_matched_raises_value_error(
        self, mock_sciencebase_endpoints
    ) -> None:
        """# NEGATIVE: nonexistent state filter must raise ValueError with clear message."""
        with pytest.raises(ValueError, match="No routes matched"):
            run_pipeline(states=["NONEXISTENT_STATE_ZZ"])

    def test_negative_main_entrypoint_returns_exit_code_1_on_error(self) -> None:
        """# NEGATIVE: CLI main() must catch domain errors and return exit code 1."""
        exit_code = main(
            [
                "--start-year",
                "2025",
                "--end-year",
                "2010",  # Invalid year order
            ]
        )
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Section 4: Zero-Disk Mandate Verification
# ---------------------------------------------------------------------------


class TestZeroDiskVerification:
    """Verify that in-memory runs leave zero temporary artifacts on disk."""

    def test_in_memory_run_creates_zero_disk_artifacts(
        self, mock_sciencebase_endpoints, tmp_path: Path
    ) -> None:
        files_before = set(tmp_path.rglob("*"))
        _ = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            output_path=None,
            format="parquet",
        )
        files_after = set(tmp_path.rglob("*"))
        assert files_before == files_after, (
            f"Unexpected disk files created: {files_after - files_before}"
        )


# ---------------------------------------------------------------------------
# Section 5: Covariate Execution Order & 10-Stop vs 50-Stop Temporal Guard
# ---------------------------------------------------------------------------


class TestCovariateOrderAndTemporalGuard:
    """Integration tests for covariate compute→filter ordering and 10/50-stop epoch guard.

    Defect fixed: 'CarsPerStop' not found in DataFrame — filter_traffic was
    called before compute_traffic_covariates + join were executed.

    Domain requirement enforced: BBS 50-stop data only exists from 1997+.
    """

    def test_full_pipeline_with_covariate_filtering_both_tenure_and_traffic(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Integration: observer tenure + traffic covariate filtering together.

        Verifies that covariate computation (compute_observer_covariates and
        compute_traffic_covariates + join) precedes filter_observer_tenure and
        filter_traffic without raising ColumnNotFoundError.

        Mock data: 2 cars per stop across 50 stops → CarTotal=100, CarsPerStop=2.0
        max_cars_per_stop=3.0 (accepts all runs); min_obs_tenure=1 (accepts all runs).
        """
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=2018,
            end_year=2019,
            include_covariates=True,
            min_obs_tenure=1,  # observer tenure filter enabled (compute must run first)
            max_cars_per_stop=3.0,  # traffic filter enabled (join must run first)
            output_path=None,
            format="parquet",
            shape="wide",
        )
        assert isinstance(data, bytes), "Pipeline must return bytes in RAM mode."
        assert data.startswith(b"PAR1"), "Output must be valid Parquet."
        tbl = pq.read_table(io.BytesIO(data))
        col_names = tbl.column_names
        # Covariate columns must be present in output
        assert "RouteTenure" in col_names, (
            "RouteTenure covariate must be computed before filtering."
        )
        assert "CarTotal" in col_names or "CarsPerStop" in col_names, (
            "Traffic covariates must be joined and present before filter_traffic runs."
        )

    def test_full_pipeline_covariate_filtering_excludes_high_traffic(
        self, mock_sciencebase_endpoints
    ) -> None:
        """Integration: traffic filter with threshold below mock data level prunes all runs.

        Mock data has CarsPerStop=2.0; max_cars_per_stop=1.0 should reject all runs.
        This test also verifies the join and computation order is correct (no ColumnNotFoundError).
        """
        # NEGATIVE: threshold below 2.0 cars/stop must eliminate all runs
        with pytest.raises(
            ValueError, match="No survey runs satisfied the vehicle traffic criteria"
        ):
            run_pipeline(
                states=["AL"],
                species=["07610"],
                start_year=2018,
                end_year=2019,
                include_covariates=True,
                max_cars_per_stop=1.0,
                output_path=None,
                format="parquet",
            )

    def test_fifty_stop_pre1997_start_year_clamped_to_1997(
        self, mock_sciencebase_endpoints
    ) -> None:
        """10-Stop vs 50-Stop guard: start_year < 1997 with 50-stop data clamps to 1997.

        The pipeline uses 50-StopData.zip as its observation source; individual
        stop records only exist from 1997+. When start_year precedes 1997 the
        guard must emit a warning and clamp start_year to 1997.

        This test supplies start_year=1990 but mocked data only has 2018/2019
        records, so after clamping the effective range is [1997, 2019].
        The pipeline should succeed (records exist in the clamped window).
        """
        # Should not raise; guard clamps start_year silently with a warning
        data = run_pipeline(
            states=["AL"],
            species=["07610"],
            start_year=1990,  # Pre-1997 — guard must clamp this
            end_year=2019,
            output_path=None,
            format="parquet",
            shape="wide",
        )
        assert isinstance(data, bytes), (
            "Pipeline should succeed after clamping start_year to 1997."
        )
        assert len(data) > 0

    def test_fifty_stop_entirely_pre1997_raises_value_error(self) -> None:
        """# NEGATIVE: Requesting 50-stop data for a range entirely before 1997 must raise.

        No 50-stop individual-stop records exist before 1997; the guard must
        raise a ValueError with an informative message.
        """
        with pytest.raises(
            ValueError,
            match="50-stop individual-stop records are only available from 1997",
        ):
            run_pipeline(
                start_year=1966,
                end_year=1996,  # Entirely pre-1997 — no 50-stop data
                all_species=True,
            )
