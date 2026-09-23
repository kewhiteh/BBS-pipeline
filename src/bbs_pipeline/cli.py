"""Standalone CLI interface and pipeline orchestrator for USGS BBS Pipeline.

Implements Task 7.1 (docs/04_TASKS.md) and §4 of docs/project_intake_brief_usgs_breeding_bird_survey_pipeline.md:
- Exposes spatial, taxonomic, temporal, slicing, and serialization CLI arguments using argparse.
- Enforces Zero-Disk In-Memory Mandate: Network streaming and intermediate operations
  operate strictly on io.BytesIO RAM buffers.
- Enforces Tool Lock: Polars exclusively for all ETL, joins, and zero-filling.
- Enforces Arithmetic Typing Invariant: Identifiers and codes are zero-padded pl.String.
"""

from __future__ import annotations

import argparse
import io
import logging
from pathlib import Path
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Union
import zipfile

import polars as pl
import requests

from bbs_pipeline.client.parser import (
    FIFTY_STOP_SCHEMA,
    ROUTES_SCHEMA,
    SPECIES_LIST_SCHEMA,
    TEN_STOP_SCHEMA,
    VEHICLE_SCHEMA,
    WEATHER_SCHEMA,
)
from bbs_pipeline.client.sciencebase import (
    DEFAULT_ITEM_ID,
    build_session,
    fetch_file_by_name,
)
from bbs_pipeline.core.covariates import (
    compute_observer_covariates,
    compute_traffic_covariates,
)
from bbs_pipeline.core.discovery import (
    get_eligible_years,
    get_max_observed_year,
)
from bbs_pipeline.core.filters import (
    add_route_key,
    enforce_fifty_stop_temporal_guard,
    filter_by_continuity,
    filter_by_min_stops,
    filter_observer_tenure,
    filter_traffic,
    nullify_stops_beyond_total,
    slice_stop_range,
)
from bbs_pipeline.core.taxonomy import (
    load_guilds_json,
    parse_migrant_nonbreeder,
    parse_species_list,
    resolve_target_species,
)
from bbs_pipeline.core.zero_fill import zero_fill_route_observations
from bbs_pipeline.export.serializer import (
    DEFAULT_TARGET_CRS,
    SUPPORTED_FORMATS,
    SUPPORTED_SHAPES,
    serialize_dataset,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State abbreviation & name lookup table to BBS 2-digit zero-padded StateNum
# ---------------------------------------------------------------------------
STATE_ABBR_TO_NUM: Dict[str, str] = {
    # US States & Territories (USGS BBS Official StateNums)
    "AL": "02", "AK": "03", "AZ": "07", "AR": "06", "CA": "14", "CO": "17",
    "CT": "18", "DE": "21", "FL": "25", "GA": "27", "ID": "33", "IL": "34",
    "IN": "35", "IA": "36", "KS": "38", "KY": "39", "LA": "42", "ME": "44",
    "MD": "46", "MA": "45", "MI": "49", "MN": "50", "MS": "52", "MO": "51",
    "MT": "53", "NE": "54", "NV": "57", "NH": "58", "NJ": "59", "NM": "60",
    "NY": "61", "NC": "63", "ND": "64", "OH": "66", "OK": "67", "OR": "69",
    "PA": "72", "RI": "77", "SC": "80", "SD": "81", "TN": "82", "TX": "83",
    "UT": "85", "VT": "87", "VA": "88", "WA": "89", "WV": "90", "WI": "91",
    "WY": "92",
    # Canadian Provinces & Territories (USGS BBS)
    "AB": "04", "BC": "11", "MB": "47", "NB": "56", "NL": "62", "NT": "65",
    "NS": "68", "NU": "65", "ON": "68", "PE": "76", "QC": "78", "SK": "79",
    "YT": "93",
}

STATE_NAME_TO_NUM: Dict[str, str] = {
    # US States & Territories (USGS BBS Official StateNums)
    "ALABAMA": "02", "ALASKA": "03", "ARIZONA": "07", "ARKANSAS": "06",
    "CALIFORNIA": "14", "COLORADO": "17", "CONNECTICUT": "18", "DELAWARE": "21",
    "FLORIDA": "25", "GEORGIA": "27", "IDAHO": "33", "ILLINOIS": "34",
    "INDIANA": "35", "IOWA": "36", "KANSAS": "38", "KENTUCKY": "39",
    "LOUISIANA": "42", "MAINE": "44", "MARYLAND": "46", "MASSACHUSETTS": "45",
    "MICHIGAN": "49", "MINNESOTA": "50", "MISSISSIPPI": "52", "MISSOURI": "51",
    "MONTANA": "53", "NEBRASKA": "54", "NEVADA": "57", "NEW HAMPSHIRE": "58",
    "NEW JERSEY": "59", "NEW MEXICO": "60", "NEW YORK": "61", "NORTH CAROLINA": "63",
    "NORTH DAKOTA": "64", "OHIO": "66", "OKLAHOMA": "67", "OREGON": "69",
    "PENNSYLVANIA": "72", "RHODE ISLAND": "77", "SOUTH CAROLINA": "80",
    "SOUTH DAKOTA": "81", "TENNESSEE": "82", "TEXAS": "83", "UTAH": "85",
    "VERMONT": "87", "VIRGINIA": "88", "WASHINGTON": "89", "WEST VIRGINIA": "90",
    "WISCONSIN": "91", "WYOMING": "92",
    # Canadian Provinces & Territories (USGS BBS)
    "ALBERTA": "04", "BRITISH COLUMBIA": "11", "MANITOBA": "47", "NEW BRUNSWICK": "56",
    "NEWFOUNDLAND": "62", "NORTHWEST TERRITORIES": "65", "NOVA SCOTIA": "68",
    "NUNAVUT": "65", "ONTARIO": "68", "PRINCE EDWARD ISLAND": "76", "QUEBEC": "78",
    "SASKATCHEWAN": "79", "YUKON": "93",
}


def normalize_state_input(state_in: str) -> str:
    """Normalize a user-provided state input into a 2-digit zero-padded StateNum."""
    clean = state_in.strip().upper()
    if clean in STATE_ABBR_TO_NUM:
        return STATE_ABBR_TO_NUM[clean]
    if clean in STATE_NAME_TO_NUM:
        return STATE_NAME_TO_NUM[clean]
    if clean.isdigit():
        return clean.zfill(2)
    return clean


# ---------------------------------------------------------------------------
# Argument Parser Construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for the USGS BBS Pipeline."""
    parser = argparse.ArgumentParser(
        prog="extract_bbs",
        description="USGS Breeding Bird Survey (BBS) High-Performance Extraction, Zero-Filling & Spatial Serialization",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # 1. Spatial Group
    spatial_grp = parser.add_argument_group("Spatial Filtering Options")
    spatial_grp.add_argument(
        "-s",
        "--state",
        "--states",
        nargs="+",
        dest="states",
        help="State abbreviations (e.g. NC VA SC), names (e.g. 'North Carolina'), or 2-digit StateNums.",
    )
    spatial_grp.add_argument(
        "--bcr",
        "--bcrs",
        nargs="+",
        dest="bcrs",
        help="Bird Conservation Region code(s) (e.g. 28 27).",
    )
    spatial_grp.add_argument(
        "--stratum",
        "--strata",
        nargs="+",
        dest="strata",
        help="Physiographic stratum code(s) (e.g. 04 11).",
    )
    spatial_grp.add_argument(
        "--routes",
        "--route",
        nargs="+",
        dest="routes",
        help="Route identifier(s) or composite route key(s) (e.g. 840_02_001 or 001).",
    )

    # 2. Taxonomic Group
    taxa_grp = parser.add_argument_group("Taxonomic Filtering Options")
    taxa_grp.add_argument(
        "--species",
        "--aou",
        "--aous",
        nargs="+",
        dest="species",
        help="Target species AOU codes (e.g. 07610) or English common names (e.g. 'American Robin').",
    )
    taxa_grp.add_argument(
        "--guild",
        "--guilds",
        nargs="+",
        dest="guilds",
        help="Ecological guild trait names matching breeding_habitat or foraging_guild.",
    )
    taxa_grp.add_argument(
        "--breeding-habitat",
        "--breeding-habitats",
        nargs="+",
        dest="breeding_habitats",
        help="Specific breeding habitat guilds (e.g. wetland, forest_interior).",
    )
    taxa_grp.add_argument(
        "--foraging-guild",
        "--foraging-guilds",
        nargs="+",
        dest="foraging_guilds",
        help="Specific foraging guilds (e.g. aerial_insectivore, granivore).",
    )
    taxa_grp.add_argument(
        "--family",
        "--families",
        nargs="+",
        dest="families",
        help="Taxonomic family name(s) (e.g. Turdidae, Parulidae).",
    )
    taxa_grp.add_argument(
        "--order",
        "--orders",
        nargs="+",
        dest="orders",
        help="Taxonomic order name(s) (e.g. Passeriformes, Piciformes).",
    )
    taxa_grp.add_argument(
        "--all-species",
        "--community",
        action="store_true",
        dest="all_species",
        help="Include entire breeding species universe (minus migrants/non-breeders).",
    )

    # 3. Temporal & Phenological Group
    temp_grp = parser.add_argument_group("Temporal & Phenological Options")
    temp_grp.add_argument(
        "--start-year",
        type=int,
        default=None,
        dest="start_year",
        help="Inclusive lower survey year bound.",
    )
    temp_grp.add_argument(
        "--end-year",
        type=int,
        default=None,
        dest="end_year",
        help="Inclusive upper survey year bound (capped dynamically by max observed year).",
    )
    temp_grp.add_argument(
        "--day-range",
        type=int,
        nargs=2,
        metavar=("START_DAY", "END_DAY"),
        dest="day_range",
        help="Survey day-of-month window [start_day, end_day] (e.g. 1 15).",
    )
    temp_grp.add_argument(
        "--months",
        "--month",
        nargs="+",
        dest="months",
        help="Survey month(s) (e.g. 5 6 or 05 06).",
    )

    # 4. Slicing & Quality Filtering Group
    filter_grp = parser.add_argument_group("Slicing & Quality Filtering Options")
    filter_grp.add_argument(
        "--min-completeness-pct",
        "--continuity",
        type=float,
        default=None,
        dest="min_completeness_pct",
        help="Minimum proportional survey continuity percentage across eligible years (e.g. 75.0).",
    )
    filter_grp.add_argument(
        "--min-stops",
        type=int,
        default=None,
        choices=[45, 48, 50],
        dest="min_stops",
        help="Discrete stop effort guard threshold (45, 48, or 50).",
    )
    filter_grp.add_argument(
        "--enforce-quality",
        action="store_true",
        default=False,
        dest="enforce_quality",
        help="Enforce official BBS protocol: RunType == 1 and QualityCurrentID == 1.",
    )
    filter_grp.add_argument(
        "--stop-range",
        type=int,
        nargs=2,
        metavar=("START_STOP", "END_STOP"),
        dest="stop_range",
        help="Sub-route slicing stop range [start_stop, end_stop] (1-50).",
    )
    filter_grp.add_argument(
        "--start-stop",
        type=int,
        default=None,
        dest="start_stop",
        help="Sub-route slicing starting stop index (1-50).",
    )
    filter_grp.add_argument(
        "--end-stop",
        type=int,
        default=None,
        dest="end_stop",
        help="Sub-route slicing ending stop index (1-50).",
    )

    # 5. Covariates Group
    cov_grp = parser.add_argument_group("Covariate Options")
    cov_grp.add_argument(
        "--include-covariates",
        action="store_true",
        default=True,
        dest="include_covariates",
        help="Calculate observer experience metrics and vehicle traffic rates.",
    )
    cov_grp.add_argument(
        "--no-covariates",
        action="store_false",
        dest="include_covariates",
        help="Skip covariate computation.",
    )
    cov_grp.add_argument(
        "--min-obs-tenure",
        type=int,
        default=None,
        dest="min_obs_tenure",
        help="Minimum observer route tenure in years (e.g. 2 excludes first-year observers).",
    )
    cov_grp.add_argument(
        "--max-obs-tenure",
        type=int,
        default=None,
        dest="max_obs_tenure",
        help="Maximum observer route tenure in years.",
    )
    cov_grp.add_argument(
        "--exclude-first-year",
        action="store_true",
        default=False,
        dest="exclude_first_year",
        help="Exclude first-year observer survey runs on routes (Kendall first-year observer effect).",
    )
    cov_grp.add_argument(
        "--observer-cohorts",
        "--observer-cohort",
        nargs="+",
        dest="observer_cohorts",
        help="Observer experience cohorts to include: Novice, Intermediate, Veteran.",
    )
    cov_grp.add_argument(
        "--max-cars-per-stop",
        type=float,
        default=None,
        dest="max_cars_per_stop",
        help="Maximum average cars per stop threshold.",
    )
    cov_grp.add_argument(
        "--max-car-total",
        type=int,
        default=None,
        dest="max_car_total",
        help="Maximum total cars observed across all stops.",
    )

    # 6. Zero-Filling Group
    zf_grp = parser.add_argument_group("Zero-Filling Options")
    zf_grp.add_argument(
        "--zero-fill",
        action="store_true",
        default=True,
        dest="zero_fill",
        help="Perform 4-step extirpation-preserving Cartesian zero-filling.",
    )
    zf_grp.add_argument(
        "--no-zero-fill",
        action="store_false",
        dest="zero_fill",
        help="Output raw observations without Cartesian zero-filling.",
    )

    # 7. Operational & Ingestion Group
    op_grp = parser.add_argument_group("Operational & Data Source Options")
    op_grp.add_argument(
        "--resolution",
        choices=["10stop", "50stop"],
        default="50stop",
        dest="resolution",
        help="Observation resolution: '50stop' (1997–present) or '10stop' (1966–present).",
    )
    op_grp.add_argument(
        "--item-id",
        default=DEFAULT_ITEM_ID,
        dest="item_id",
        help="ScienceBase catalog item ID.",
    )
    op_grp.add_argument(
        "--raw-data-dir",
        type=Path,
        default=None,
        dest="raw_data_dir",
        help="Optional local path to pre-downloaded raw archives/CSVs (operates in RAM).",
    )
    op_grp.add_argument(
        "--guilds-path",
        type=Path,
        default=None,
        dest="guilds_path",
        help="Path to data/guilds.json trait registry.",
    )

    # 8. Output & Serialization Group
    out_grp = parser.add_argument_group("Serialization & Output Options")
    out_grp.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        dest="output",
        help="Output file path destination (e.g. output.parquet, output.gpkg, output.csv, output.geojson).",
    )
    out_grp.add_argument(
        "-f",
        "--format",
        choices=list(SUPPORTED_FORMATS),
        default=None,
        dest="format",
        help="Output serialization format (parquet, csv, geojson, gpkg). Inferred from --output if omitted.",
    )
    out_grp.add_argument(
        "--shape",
        choices=list(SUPPORTED_SHAPES),
        default="wide",
        dest="shape",
        help="Tabular shape: 'wide' (Stop1..Stop50) or 'long' (unpivoted StopNumber, Count).",
    )
    out_grp.add_argument(
        "--crs",
        default=DEFAULT_TARGET_CRS,
        dest="crs",
        help="Target coordinate projection CRS (e.g. EPSG:4326, EPSG:5070, nc_state_plane).",
    )
    out_grp.add_argument(
        "--layer-name",
        default="bbs_observations",
        dest="layer_name",
        help="Layer name when exporting to OGC GeoPackage (.gpkg).",
    )

    return parser


# ---------------------------------------------------------------------------
# In-Memory Buffer Loaders
# ---------------------------------------------------------------------------


def _find_and_read_file(
    filename: str,
    raw_dir: Optional[Path] = None,
    item_id: str = DEFAULT_ITEM_ID,
    session: Optional[requests.Session] = None,
) -> io.BytesIO:
    """Locate a file in raw_dir or stream it directly into an io.BytesIO RAM buffer.

    Enforces Zero-Disk In-Memory Mandate: remote streams are never written to disk.
    Supports extension fallbacks (.csv <-> .txt) for live ScienceBase catalog differences.
    """
    candidates = [filename]
    if filename.endswith(".csv"):
        candidates.append(filename.removesuffix(".csv") + ".txt")
    elif filename.endswith(".txt"):
        candidates.append(filename.removesuffix(".txt") + ".csv")

    if raw_dir is not None and raw_dir.exists():
        # Match case-insensitively in raw_dir across candidates
        candidate_lowers = {c.lower() for c in candidates}
        for p in raw_dir.glob("*"):
            if ":Zone.Identifier" in p.name:
                continue
            if p.name.lower() in candidate_lowers:
                return io.BytesIO(p.read_bytes())
        # Try finding as nested zip or pattern
        for cand in candidates:
            matches = [p for p in raw_dir.glob(f"*{cand}*") if ":Zone.Identifier" not in p.name]
            if matches:
                return io.BytesIO(matches[0].read_bytes())

    # Fall back to ScienceBase streaming into io.BytesIO RAM buffer across candidates
    last_exc: Optional[Exception] = None
    for cand in candidates:
        try:
            return fetch_file_by_name(cand, item_id=item_id, session=session)
        except FileNotFoundError as exc:
            last_exc = exc
            continue

    if last_exc:
        raise last_exc
    raise FileNotFoundError(f"Could not locate {filename} locally or in ScienceBase item {item_id}.")


def _load_csv_from_zip_or_raw(
    buf: io.BytesIO,
    csv_name: str,
    schema: Dict[str, Any],
) -> pl.DataFrame:
    """Read a CSV from an in-memory ZIP or raw CSV buffer using explicit schema overrides."""
    buf.seek(0)
    header = buf.read(4)
    buf.seek(0)

    if header.startswith(b"PK"):  # ZIP file
        with zipfile.ZipFile(buf) as zf:
            matching = [n for n in zf.namelist() if n.lower().endswith(csv_name.lower())]
            if not matching:
                # If exact csv_name not matched, take any CSV in zip
                matching = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not matching:
                raise KeyError(f"Could not find '{csv_name}' in zip archive members: {zf.namelist()}")
            csv_bytes = zf.read(matching[0])
            df = pl.read_csv(
                io.BytesIO(csv_bytes),
                schema_overrides=schema,
                infer_schema_length=0,
                encoding="latin1",
                null_values=["", "NA", "null", "NULL", "*", "None"],
                truncate_ragged_lines=True,
            )
            return df.rename({c: c.strip() for c in df.columns})
    else:  # Raw CSV buffer
        df = pl.read_csv(
            buf,
            schema_overrides=schema,
            infer_schema_length=0,
            encoding="latin1",
            null_values=["", "NA", "null", "NULL", "*", "None"],
            truncate_ragged_lines=True,
        )
        return df.rename({c: c.strip() for c in df.columns})


def _load_observation_data(
    buf: io.BytesIO,
    target_routes: Optional[Sequence[str]] = None,
    target_states: Optional[Sequence[str]] = None,
    resolution: str = "50stop",
) -> pl.DataFrame:
    """Load observation records from 50-StopData.zip (or CSV) entirely in RAM."""
    buf.seek(0)
    header = buf.read(4)
    buf.seek(0)

    frames: List[pl.DataFrame] = []
    active_schema = TEN_STOP_SCHEMA if resolution == "10stop" else FIFTY_STOP_SCHEMA

    if header.startswith(b"PK"):  # ZIP file
        with zipfile.ZipFile(buf) as zf:
            namelist = zf.namelist()
            inner_zips = [n for n in namelist if n.lower().endswith(".zip")]
            if resolution == "10stop" and inner_zips:
                for inner_zip_name in inner_zips:
                    inner_bytes = zf.read(inner_zip_name)
                    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner_zf:
                        csv_members = [n for n in inner_zf.namelist() if n.lower().endswith(".csv")]
                        for member in csv_members:
                            raw_bytes = inner_zf.read(member)
                            df = pl.read_csv(
                                io.BytesIO(raw_bytes),
                                schema_overrides=active_schema,
                                infer_schema_length=0,
                                encoding="latin1",
                                null_values=["", "NA", "null", "NULL", "*", "None"],
                                truncate_ragged_lines=True,
                            )
                            df = df.rename({c: c.strip() for c in df.columns})
                            if df.is_empty():
                                continue
                            if "RouteKey" not in df.columns and {"CountryNum", "StateNum", "Route"}.issubset(df.columns):
                                df = add_route_key(df)
                            if target_states and "StateNum" in df.columns:
                                df = df.filter(pl.col("StateNum").is_in(target_states))
                            if target_routes and "RouteKey" in df.columns:
                                df = df.filter(pl.col("RouteKey").is_in(target_routes))
                            if not df.is_empty():
                                frames.append(df)
            else:
                csv_members = [n for n in namelist if n.lower().endswith(".csv")]
                if not csv_members:
                    raise ValueError("No CSV files found in observation zip archive.")

                for member in csv_members:
                    raw_bytes = zf.read(member)
                    df = pl.read_csv(
                        io.BytesIO(raw_bytes),
                        schema_overrides=active_schema,
                        infer_schema_length=0,
                        encoding="latin1",
                        null_values=["", "NA", "null", "NULL", "*", "None"],
                        truncate_ragged_lines=True,
                    )
                    df = df.rename({c: c.strip() for c in df.columns})
                    if df.is_empty():
                        continue
                    if "RouteKey" not in df.columns and {"CountryNum", "StateNum", "Route"}.issubset(df.columns):
                        df = add_route_key(df)
                    if target_states and "StateNum" in df.columns:
                        df = df.filter(pl.col("StateNum").is_in(target_states))
                    if target_routes and "RouteKey" in df.columns:
                        df = df.filter(pl.col("RouteKey").is_in(target_routes))
                    if not df.is_empty():
                        frames.append(df)
    else:
        df = pl.read_csv(
            buf,
            schema_overrides=active_schema,
            infer_schema_length=0,
            encoding="latin1",
            null_values=["", "NA", "null", "NULL", "*", "None"],
            truncate_ragged_lines=True,
        )
        df = df.rename({c: c.strip() for c in df.columns})
        if "RouteKey" not in df.columns and {"CountryNum", "StateNum", "Route"}.issubset(df.columns):
            df = add_route_key(df)
        if target_states and "StateNum" in df.columns:
            df = df.filter(pl.col("StateNum").is_in(target_states))
        if target_routes and "RouteKey" in df.columns:
            df = df.filter(pl.col("RouteKey").is_in(target_routes))
        frames.append(df)

    if not frames:
        return pl.DataFrame(schema=active_schema)

    return pl.concat(frames, how="vertical_relaxed")


# ---------------------------------------------------------------------------
# Integrated Pipeline Runner
# ---------------------------------------------------------------------------


def run_pipeline(
    states: Optional[Sequence[str]] = None,
    bcrs: Optional[Sequence[str]] = None,
    strata: Optional[Sequence[str]] = None,
    routes: Optional[Sequence[str]] = None,
    species: Optional[Sequence[str]] = None,
    guilds: Optional[Sequence[str]] = None,
    breeding_habitats: Optional[Sequence[str]] = None,
    foraging_guilds: Optional[Sequence[str]] = None,
    families: Optional[Sequence[str]] = None,
    orders: Optional[Sequence[str]] = None,
    all_species: bool = False,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    day_range: Optional[Sequence[int]] = None,
    months: Optional[Sequence[str]] = None,
    min_completeness_pct: Optional[float] = None,
    min_stops: Optional[int] = None,
    enforce_quality: bool = False,
    stop_range: Optional[Sequence[int]] = None,
    start_stop: Optional[int] = None,
    end_stop: Optional[int] = None,
    include_covariates: bool = True,
    min_obs_tenure: Optional[int] = None,
    max_obs_tenure: Optional[int] = None,
    exclude_first_year: bool = False,
    observer_cohorts: Optional[Sequence[str]] = None,
    max_cars_per_stop: Optional[float] = None,
    max_car_total: Optional[int] = None,
    zero_fill: bool = True,
    resolution: str = "50stop",
    item_id: str = DEFAULT_ITEM_ID,
    raw_data_dir: Optional[Union[str, Path]] = None,
    guilds_path: Optional[Union[str, Path]] = None,
    output_path: Optional[Union[str, Path]] = None,
    format: Optional[str] = None,
    shape: str = "wide",
    crs: Union[str, int] = DEFAULT_TARGET_CRS,
    layer_name: str = "bbs_observations",
    session: Optional[requests.Session] = None,
) -> Union[bytes, Path]:
    """Execute the end-to-end BBS pipeline adhering to all architectural invariants.

    Parameters
    ----------
    states:
        State abbreviations, names, or 2-digit numeric codes.
    bcrs:
        Bird Conservation Region code(s).
    strata:
        Physiographic stratum code(s).
    routes:
        Route identifiers or composite keys (e.g. 840_02_001).
    species:
        AOU code(s) or English common name(s).
    guilds:
        Trait names matching breeding_habitat or foraging_guild.
    breeding_habitats:
        Breeding habitat guild names.
    foraging_guilds:
        Foraging guild names.
    families:
        Taxonomic families.
    orders:
        Taxonomic orders.
    all_species:
        If True, retains all valid breeding species (minus migrants/non-breeders).
    start_year:
        Inclusive lower survey year bound.
    end_year:
        Inclusive upper survey year bound.
    day_range:
        2-tuple/list [start_day, end_day].
    months:
        Survey month filter list.
    min_completeness_pct:
        Proportional route continuity threshold (e.g. 75.0).
    min_stops:
        Minimum completed stops effort guard (45, 48, or 50).
    stop_range:
        Sub-route stop slicing range [start_stop, end_stop] (1-50).
    start_stop:
        Starting stop index for slicing (1-50).
    end_stop:
        Ending stop index for slicing (1-50).
    include_covariates:
        Whether to calculate observer and vehicle covariates.
    min_obs_tenure:
        Minimum observer route tenure (years surveying route).
    max_obs_tenure:
        Maximum observer route tenure (years surveying route).
    exclude_first_year:
        Exclude first-year observer survey runs (Kendall first-year bias).
    observer_cohorts:
        Observer experience cohorts (Novice, Intermediate, Veteran).
    max_cars_per_stop:
        Maximum average cars per stop threshold.
    max_car_total:
        Maximum total cars observed across all stops.
    zero_fill:
        Whether to perform 4-step Cartesian zero-filling.
    item_id:
        ScienceBase catalog item ID.
    raw_data_dir:
        Optional local directory with pre-downloaded raw archives/CSVs.
    guilds_path:
        Path to guilds.json. Defaults to data/guilds.json.
    output_path:
        Destination file path. If None, operates in RAM and returns bytes.
    format:
        Serialization format: parquet, csv, geojson, gpkg.
    shape:
        Tabular shape: wide or long.
    crs:
        Coordinate projection CRS.
    layer_name:
        Layer name for GeoPackage export.
    session:
        Optional pre-configured requests.Session.

    Returns
    -------
    Union[bytes, Path]
        Serialized dataset bytes if output_path is None, or Path(output_path).

    Raises
    ------
    ValueError:
        On invalid parameters, boundary violations, or empty match criteria.
    """
    raw_dir = Path(raw_data_dir) if raw_data_dir is not None else None

    # Determine default guilds path
    if guilds_path is None:
        default_gp = Path("data/guilds.json")
        if default_gp.exists():
            guilds_path = default_gp
        else:
            # Look relative to package or current working dir
            candidate = Path(__file__).resolve().parent.parent.parent / "data" / "guilds.json"
            guilds_path = candidate if candidate.exists() else default_gp
    else:
        guilds_path = Path(guilds_path)

    # Validate temporal bounds
    if start_year is not None and end_year is not None:
        if start_year > end_year:
            raise ValueError(f"start_year ({start_year}) must be <= end_year ({end_year}).")

    # Validate slicing bounds
    slice_start: Optional[int] = None
    slice_end: Optional[int] = None
    if stop_range:
        if len(stop_range) != 2:
            raise ValueError(f"stop_range must contain exactly 2 integers, got {stop_range}")
        slice_start, slice_end = int(stop_range[0]), int(stop_range[1])
    else:
        if start_stop is not None and end_stop is not None:
            slice_start, slice_end = int(start_stop), int(end_stop)
        elif start_stop is not None or end_stop is not None:
            raise ValueError("Both start_stop and end_stop must be specified for sub-route slicing.")

    if slice_start is not None and slice_end is not None:
        if not (1 <= slice_start <= 50):
            raise ValueError(f"start_stop must be between 1 and 50, got {slice_start}")
        if not (1 <= slice_end <= 50):
            raise ValueError(f"end_stop must be between 1 and 50, got {slice_end}")
        if slice_start > slice_end:
            raise ValueError(f"start_stop ({slice_start}) must be <= end_stop ({slice_end}).")

    # Validate continuity percentage
    if min_completeness_pct is not None:
        if not (0 < min_completeness_pct <= 100):
            raise ValueError(
                f"min_completeness_pct must be in (0, 100], got {min_completeness_pct}"
            )

    # -----------------------------------------------------------------------
    # 10-Stop vs 50-Stop Temporal Resolution Guard
    # BBS 50-stop individual records (50-StopData.zip) only exist from 1997+.
    # Pre-1997 surveys exist only as 10-stop aggregates in States.zip.
    # The pipeline currently targets 50-stop data; enforce the epoch boundary.
    # -----------------------------------------------------------------------
    start_year, end_year = enforce_fifty_stop_temporal_guard(
        start_year=start_year,
        end_year=end_year,
        resolution=resolution,
        start_stop=slice_start,
        end_stop=slice_end,
    )

    # Determine output format
    export_format = format
    if export_format is None:
        if output_path is not None:
            suffix = Path(output_path).suffix.lstrip(".").lower()
            if suffix in SUPPORTED_FORMATS:
                export_format = suffix
            else:
                export_format = "parquet"
        else:
            export_format = "parquet"

    if export_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format: {export_format!r}. Supported: {SUPPORTED_FORMATS}")

    # Build requests session if needed
    if session is None and raw_dir is None:
        session = build_session()

    # -----------------------------------------------------------------------
    # Step 1: Ingest & Filter Route Directory (routes.csv)
    # -----------------------------------------------------------------------
    routes_buf = _find_and_read_file("routes.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    routes_df = _load_csv_from_zip_or_raw(routes_buf, "routes.csv", ROUTES_SCHEMA)
    if "RouteKey" not in routes_df.columns:
        routes_df = add_route_key(routes_df)

    # Apply spatial filters to routes_df
    target_state_nums: Optional[List[str]] = None
    if states:
        target_state_nums = [normalize_state_input(s) for s in states]
        routes_df = routes_df.filter(pl.col("StateNum").is_in(target_state_nums))

    if bcrs:
        bcr_set = {str(b).strip() for b in bcrs}
        routes_df = routes_df.filter(
            pl.col("BCR").str.strip_chars().is_in(bcr_set) | pl.col("BCR").is_in(bcr_set)
        )

    if strata:
        strata_set = {str(st).strip() for st in strata}
        routes_df = routes_df.filter(
            pl.col("Stratum").str.strip_chars().is_in(strata_set) | pl.col("Stratum").is_in(strata_set)
        )

    if routes:
        route_set = {str(r).strip() for r in routes}
        routes_df = routes_df.filter(
            pl.col("RouteKey").is_in(route_set) | pl.col("Route").is_in(route_set)
        )

    if routes_df.is_empty():
        raise ValueError("No routes matched the specified spatial filter criteria.")

    valid_route_keys = frozenset(routes_df["RouteKey"].to_list())

    # -----------------------------------------------------------------------
    # Step 2: Ingest & Filter Survey History (weather.csv)
    # -----------------------------------------------------------------------
    weather_buf = _find_and_read_file("weather.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    weather_df = _load_csv_from_zip_or_raw(weather_buf, "weather.csv", WEATHER_SCHEMA)
    if "RouteKey" not in weather_df.columns:
        weather_df = add_route_key(weather_df)

    # Filter to matching routes
    weather_df = weather_df.filter(pl.col("RouteKey").is_in(list(valid_route_keys)))
    if weather_df.is_empty():
        raise ValueError("No weather/survey run records matched the filtered routes.")

    # Dynamic temporal discovery & COVID-19 hiatus exclusion
    max_obs_year = get_max_observed_year(weather_df)
    eff_start_year = start_year if start_year is not None else 1966
    eff_end_year = end_year if end_year is not None else max_obs_year
    eligible_years = get_eligible_years(
        start_year=eff_start_year,
        end_year=eff_end_year,
        max_observed_year=max_obs_year,
    )

    # Filter weather runs to eligible years
    eligible_year_ints = list(eligible_years)
    weather_df = weather_df.filter(
        pl.col("Year").cast(pl.Int32).is_in(eligible_year_ints)
    )

    # Optional BBS protocol quality enforcement
    if enforce_quality:
        if "RunType" in weather_df.columns:
            weather_df = weather_df.filter(pl.col("RunType").str.strip_chars() == "1")
        if "QualityCurrentID" in weather_df.columns:
            weather_df = weather_df.filter(pl.col("QualityCurrentID").str.strip_chars() == "1")
        if weather_df.is_empty():
            raise ValueError("No survey runs satisfied the RunType=1 and QualityCurrentID=1 criteria.")

    # Phenological filtering: months & day-range
    if months:
        padded_months = [str(m).strip().zfill(2) for m in months]
        weather_df = weather_df.filter(pl.col("Month").is_in(padded_months))

    if day_range:
        d_start, d_end = int(day_range[0]), int(day_range[1])
        weather_df = weather_df.filter(
            (pl.col("Day").cast(pl.Int32) >= d_start) & (pl.col("Day").cast(pl.Int32) <= d_end)
        )

    # Proportional route continuity filtering
    if min_completeness_pct is not None:
        weather_df = filter_by_continuity(
            df=weather_df,
            eligible_years=eligible_years,
            min_completeness_pct=min_completeness_pct,
        )
        if weather_df.is_empty():
            raise ValueError(
                f"No routes satisfied the {min_completeness_pct}% continuity threshold."
            )
        # Update valid route keys after continuity filtering
        valid_route_keys = frozenset(weather_df["RouteKey"].to_list())
        routes_df = routes_df.filter(pl.col("RouteKey").is_in(list(valid_route_keys)))

    # Ensure TotalStops column is present for stop effort guards
    if "TotalStops" not in weather_df.columns:
        weather_df = weather_df.with_columns(pl.lit(50, dtype=pl.Int32).alias("TotalStops"))

    # Discrete stop effort guard
    if min_stops is not None:
        weather_df = filter_by_min_stops(weather_df, min_stops=min_stops)
        if weather_df.is_empty():
            raise ValueError(
                f"No survey runs satisfied the min_stops >= {min_stops} threshold."
            )

    # -----------------------------------------------------------------------
    # Step 3: Observer & Vehicle Covariates
    # -----------------------------------------------------------------------
    has_tenure_filter = (
        min_obs_tenure is not None
        or max_obs_tenure is not None
        or exclude_first_year
        or observer_cohorts is not None
    )
    has_traffic_filter = max_cars_per_stop is not None or max_car_total is not None

    if include_covariates or has_tenure_filter:
        # Longitudinal observer experience
        weather_df = compute_observer_covariates(weather_df)

    if include_covariates or has_traffic_filter:
        # Vehicle & noise covariates — MUST be joined before filter_traffic is called.
        # When the user has requested a traffic threshold (has_traffic_filter), any
        # failure here is fatal: raising prevents filter_traffic from silently
        # no-op'ing because the covariate columns are absent from weather_df.
        try:
            veh_buf = _find_and_read_file("VehicleData.csv", raw_dir=raw_dir, item_id=item_id, session=session)
            veh_df = _load_csv_from_zip_or_raw(veh_buf, "VehicleData.csv", VEHICLE_SCHEMA)
            if "TotalStops" not in veh_df.columns:
                veh_df = veh_df.with_columns(pl.lit(50, dtype=pl.Int32).alias("TotalStops"))
            veh_df = compute_traffic_covariates(veh_df)

            # Left join traffic covariates into weather runs BEFORE any filter_traffic call.
            traffic_cols = ["RouteDataID", "CarTotal", "CarsPerStop"]
            if "RouteDataID" in weather_df.columns and "RouteDataID" in veh_df.columns:
                veh_sub = veh_df.select([c for c in traffic_cols if c in veh_df.columns])
                weather_df = weather_df.join(veh_sub, on="RouteDataID", how="left", coalesce=True)
        except Exception as exc:
            if has_traffic_filter:
                # Traffic columns are required for the requested filter — do not silently skip.
                raise RuntimeError(
                    f"Traffic covariate join failed but --max-cars-per-stop / "
                    f"--max-car-total was requested. Cannot apply filter without "
                    f"CarsPerStop / CarTotal columns. Underlying error: {exc}"
                ) from exc
            logger.warning("Could not compute traffic covariates (covariates only): %s", exc)

    if has_tenure_filter:
        weather_df = filter_observer_tenure(
            weather_df,
            min_tenure=min_obs_tenure,
            max_tenure=max_obs_tenure,
            exclude_first_year=exclude_first_year,
            cohorts=observer_cohorts,
        )
        if weather_df.is_empty():
            raise ValueError("No survey runs satisfied the observer tenure criteria.")

    if has_traffic_filter:
        weather_df = filter_traffic(
            weather_df,
            max_cars_per_stop=max_cars_per_stop,
            max_car_total=max_car_total,
        )
        if weather_df.is_empty():
            raise ValueError("No survey runs satisfied the vehicle traffic criteria.")


    if has_tenure_filter or has_traffic_filter:
        valid_route_keys = frozenset(weather_df["RouteKey"].to_list())
        routes_df = routes_df.filter(pl.col("RouteKey").is_in(list(valid_route_keys)))

    # -----------------------------------------------------------------------
    # Step 4: Taxonomic Set-Union Resolution
    # -----------------------------------------------------------------------
    species_buf = _find_and_read_file("SpeciesList.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    species_df = parse_species_list(species_buf)

    guilds_dict = load_guilds_json(guilds_path)

    migrant_buf = _find_and_read_file("MigrantNonBreeder.zip", raw_dir=raw_dir, item_id=item_id, session=session)
    migrant_aous = parse_migrant_nonbreeder(migrant_buf)

    # Resolve common names to AOU codes
    custom_aous: List[str] = []
    if species:
        for sp in species:
            clean_sp = str(sp).strip()
            if clean_sp.isdigit():
                custom_aous.append(clean_sp.zfill(5))
            else:
                # Match common name case-insensitively
                matches = (
                    species_df.filter(
                        pl.col("English_Common_Name").str.to_lowercase() == clean_sp.lower()
                    )
                    .select("AOU")
                    .to_series()
                    .to_list()
                )
                if matches:
                    custom_aous.extend(matches)
                else:
                    # Try partial match
                    matches_partial = (
                        species_df.filter(
                            pl.col("English_Common_Name").str.to_lowercase().str.contains(clean_sp.lower())
                        )
                        .select("AOU")
                        .to_series()
                        .to_list()
                    )
                    custom_aous.extend(matches_partial)

    # Consolidate guild trait inputs
    combined_bh: List[str] = []
    combined_fg: List[str] = []
    if breeding_habitats:
        combined_bh.extend(breeding_habitats)
    if foraging_guilds:
        combined_fg.extend(foraging_guilds)
    if guilds:
        # Check against known traits
        known_bh = {"wetland", "forest_interior", "early_successional", "grassland", "urban", "coastal", "arid", "generalist"}
        known_fg = {
            "aerial_insectivore", "foliage_gleaner", "bark_gleaner", "ground_gleaner",
            "granivore", "frugivore", "nectarivore", "carnivore", "piscivore",
            "surface_dabbler", "diver", "shoreline_prober", "scavenger", "omnivore"
        }
        for g in guilds:
            clean_g = g.strip().lower()
            if clean_g in known_bh:
                combined_bh.append(clean_g)
            elif clean_g in known_fg:
                combined_fg.append(clean_g)
            else:
                # Add to both so traits match either
                combined_bh.append(clean_g)
                combined_fg.append(clean_g)

    has_taxa_filter = bool(
        species
        or guilds
        or breeding_habitats
        or foraging_guilds
        or families
        or orders
    )
    effective_all_species = all_species or not has_taxa_filter

    target_species = resolve_target_species(
        species_df=species_df,
        guilds=guilds_dict,
        migrant_aous=migrant_aous,
        orders=orders,
        families=families,
        guild_breeding_habitats=combined_bh if combined_bh else None,
        guild_foraging_guilds=combined_fg if combined_fg else None,
        custom_aous=custom_aous if custom_aous else None,
        all_species=effective_all_species,
    )

    if not target_species:
        raise ValueError("No species matched the specified taxonomic criteria.")

    # -----------------------------------------------------------------------
    # Step 5: Ingest Observations & Cartesian Zero-Filling
    # -----------------------------------------------------------------------
    obs_filename = "States.zip" if resolution == "10stop" else "50-StopData.zip"
    obs_buf = _find_and_read_file(obs_filename, raw_dir=raw_dir, item_id=item_id, session=session)
    raw_obs_df = _load_observation_data(
        obs_buf,
        target_routes=list(valid_route_keys),
        target_states=target_state_nums,
        resolution=resolution,
    )

    if zero_fill:
        final_obs_df = zero_fill_route_observations(
            history_df=raw_obs_df,
            survey_runs_df=weather_df,
            observations_df=raw_obs_df,
            target_species=target_species,
            eligible_years=eligible_years,
            default_total_stops=50,
        )
    else:
        # Filter raw observations directly
        final_obs_df = raw_obs_df.filter(
            pl.col("AOU").is_in(list(target_species))
            & pl.col("Year").cast(pl.Int32).is_in(eligible_year_ints)
            & pl.col("RouteKey").is_in(list(valid_route_keys))
        )
        if "TotalStops" in final_obs_df.columns:
            final_obs_df = nullify_stops_beyond_total(final_obs_df)

    if final_obs_df.is_empty():
        raise ValueError("Zero observation records resulted after filtering and zero-filling.")

    # -----------------------------------------------------------------------
    # Step 6: Sub-Route Stop Range Slicing
    # -----------------------------------------------------------------------
    if slice_start is not None and slice_end is not None:
        final_obs_df = slice_stop_range(
            df=final_obs_df,
            start_stop=slice_start,
            end_stop=slice_end,
            segment_col="SegmentCount",
        )

    # -----------------------------------------------------------------------
    # Step 7: Spatial Reprojection & Output Serialization
    # -----------------------------------------------------------------------
    provenance_extra: Dict[str, Any] = {
        "spatial_crs": str(crs),
        "target_species_count": len(target_species),
        "filtered_route_count": len(valid_route_keys),
    }

    result = serialize_dataset(
        data=final_obs_df,
        format=export_format,
        output_path=output_path,
        shape=shape,
        routes_df=routes_df,
        target_crs=crs,
        metadata=provenance_extra,
        layer_name=layer_name,
    )

    return result


# ---------------------------------------------------------------------------
# CLI Main Entry Point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the CLI interface using sys.argv or explicit arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        res = run_pipeline(
            states=args.states,
            bcrs=args.bcrs,
            strata=args.strata,
            routes=args.routes,
            species=args.species,
            guilds=args.guilds,
            breeding_habitats=args.breeding_habitats,
            foraging_guilds=args.foraging_guilds,
            families=args.families,
            orders=args.orders,
            all_species=args.all_species,
            start_year=args.start_year,
            end_year=args.end_year,
            day_range=args.day_range,
            months=args.months,
            min_completeness_pct=args.min_completeness_pct,
            min_stops=args.min_stops,
            enforce_quality=args.enforce_quality,
            stop_range=args.stop_range,
            start_stop=args.start_stop,
            end_stop=args.end_stop,
            include_covariates=args.include_covariates,
            min_obs_tenure=args.min_obs_tenure,
            max_obs_tenure=args.max_obs_tenure,
            exclude_first_year=args.exclude_first_year,
            observer_cohorts=args.observer_cohorts,
            max_cars_per_stop=args.max_cars_per_stop,
            max_car_total=args.max_car_total,
            zero_fill=args.zero_fill,
            resolution=args.resolution,
            item_id=args.item_id,
            raw_data_dir=args.raw_data_dir,
            guilds_path=args.guilds_path,
            output_path=args.output,
            format=args.format,
            shape=args.shape,
            crs=args.crs,
            layer_name=args.layer_name,
        )
        if isinstance(res, Path):
            print(f"[SUCCESS] Export serialized to: {res.resolve()}")
        else:
            print(f"[SUCCESS] In-memory pipeline finished ({len(res)} bytes generated).")
        return 0
    except Exception as exc:
        logger.error("Pipeline failure: %s", exc, exc_info=True)
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
