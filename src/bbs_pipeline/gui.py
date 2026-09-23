"""Reactive Streamlit web dashboard for USGS BBS Pipeline.

Implements Task 7.2 (docs/04_TASKS.md) and §4 of docs/project_intake_brief_usgs_breeding_bird_survey_pipeline.md:
- Searchable multi-select for States, BCRs, Strata, Routes, Guilds, and Species.
- Active covariate filters (Observer Tenure, Vehicle Traffic) and dynamic taxonomic set-union.
- Dynamic survey year dual-slider adapting to the discovered dataset temporal horizon.
- Sub-route stop slicing and route continuity dual-sliders.
- Live Map preview displaying 1:1 route starting coordinates.
- Pure in-memory streaming export via st.download_button (Zero-Disk In-Memory Mandate).
"""

from __future__ import annotations

import io
from pathlib import Path
import traceback
from typing import Any, Dict, List, Optional, Tuple

import polars as pl
import streamlit as st

from bbs_pipeline.cli import (
    STATE_ABBR_TO_NUM,
    _find_and_read_file,
    _load_csv_from_zip_or_raw,
    normalize_state_input,
    run_pipeline,
)
from bbs_pipeline.client.parser import ROUTES_SCHEMA, WEATHER_SCHEMA
from bbs_pipeline.client.sciencebase import DEFAULT_ITEM_ID, build_session
from bbs_pipeline.core.discovery import get_max_observed_year
from bbs_pipeline.core.filters import add_route_key
from bbs_pipeline.core.taxonomy import load_guilds_json, parse_species_list


# ---------------------------------------------------------------------------
# Caching Data Loaders
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Loading route catalog and temporal metadata...")
def load_metadata_cache(
    raw_dir_str: Optional[str] = None,
    item_id: str = DEFAULT_ITEM_ID,
) -> Tuple[pl.DataFrame, int, pl.DataFrame, Dict[str, Dict[str, str]]]:
    """Load routes catalog, species authority, guilds traits, and max observed year."""
    raw_dir = Path(raw_dir_str) if raw_dir_str else None
    session = build_session()

    # 1. Routes
    routes_buf = _find_and_read_file("routes.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    routes_df = _load_csv_from_zip_or_raw(routes_buf, "routes.csv", ROUTES_SCHEMA)
    routes_df = routes_df.with_columns([
        pl.col("CountryNum").str.strip_chars().str.zfill(3),
        pl.col("StateNum").str.strip_chars().str.zfill(2),
        pl.col("Route").str.strip_chars().str.zfill(3),
        pl.col("RouteName").str.strip_chars().fill_null(""),
    ])
    if "RouteKey" in routes_df.columns:
        routes_df = routes_df.drop("RouteKey")
    routes_df = add_route_key(routes_df)

    # 2. Weather & max year
    weather_buf = _find_and_read_file("weather.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    weather_df = _load_csv_from_zip_or_raw(weather_buf, "weather.csv", WEATHER_SCHEMA)
    max_year = get_max_observed_year(weather_df)

    # 3. Species List
    species_buf = _find_and_read_file("SpeciesList.csv", raw_dir=raw_dir, item_id=item_id, session=session)
    species_df = parse_species_list(species_buf)

    # 4. Guilds
    gp = Path("data/guilds.json")
    if not gp.exists():
        gp = Path(__file__).resolve().parent.parent.parent / "data" / "guilds.json"
    guilds_dict = load_guilds_json(gp) if gp.exists() else {}

    return routes_df, max_year, species_df, guilds_dict



from bbs_pipeline.core.constants import BCR_NAMES, STRATA_NAMES, OBSERVER_COHORTS


# ---------------------------------------------------------------------------
# Helper UI components
# ---------------------------------------------------------------------------


def render_pills_or_multiselect(
    label: str,
    options: List[str],
    default: Optional[List[str]] = None,
    key: Optional[str] = None,
) -> List[str]:
    """Render st.pills with multi selection mode, falling back to st.multiselect if needed."""
    if hasattr(st, "pills"):
        res = st.pills(
            label,
            options=options,
            default=default or [],
            selection_mode="multi",
            key=key,
        )
        return list(res) if res is not None else []
    else:
        return st.multiselect(
            label,
            options=options,
            default=default or [],
            key=key,
        )


# ---------------------------------------------------------------------------
# Streamlit Dashboard Main Application
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="USGS BBS Pipeline Dashboard",
        page_icon="🦅",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("🦅 USGS Breeding Bird Survey (BBS) Pipeline")
    st.markdown(
        "High-performance in-memory extraction, taxonomic set-union resolution, "
        "discrete zero-filling, and spatial serialization dashboard."
    )

    # Sidebar: Operational Settings
    with st.sidebar:
        st.header("⚙️ Operational Settings")
        raw_data_dir = st.text_input(
            "Local Raw Data Directory (Optional)",
            value="data/raw" if Path("data/raw").exists() else "",
            help="Directory with pre-downloaded raw archives/CSVs to operate offline.",
        )
        item_id = st.text_input(
            "ScienceBase Catalog Item ID",
            value=DEFAULT_ITEM_ID,
            help="USGS ScienceBase release catalog identifier.",
        )
        resolution_label = st.radio(
            "Temporal Resolution & Data Source",
            options=["50-Stop Detailed (1997–Present)", "10-Stop Summary (1966–Present)"],
            index=0,
            help="'50-Stop Detailed' pulls 50-StopData.zip; '10-Stop Summary' pulls States.zip back to 1966.",
        )
        resolution = "10stop" if "10-Stop" in resolution_label else "50stop"

        st.divider()
        st.header("📦 Output Configuration")
        export_format = st.selectbox(
            "Serialization Format",
            options=["parquet", "csv", "gpkg", "geojson"],
            index=0,
            help="Target serialization format.",
        )
        shape = st.radio(
            "Tabular Output Shape",
            options=["wide", "long"],
            index=0,
            horizontal=True,
            help="'wide' preserves Stop1..Stop50; 'long' unpivots into normalized records.",
        )
        crs = st.selectbox(
            "Target Projection (CRS)",
            options=["EPSG:4326", "EPSG:5070", "nc_state_plane", "EPSG:3857"],
            index=0,
            help="Coordinate reference system for spatial anchoring.",
        )
        with st.expander("🚗 Covariate Filters & Covariates", expanded=False):
            include_covariates = st.checkbox(
                "Calculate Observer & Traffic Covariates",
                value=True,
                help="Compute CareerSurveysCompleted, RouteTenure, IsFirstYearObserver, CarTotal, CarsPerStop.",
            )
            st.markdown("**Observer Experience Filter**")
            exclude_first_year = st.checkbox(
                "Exclude First-Year Observer Runs (First-Year Effect)",
                value=True,
                help="Exclude first-year observer runs (RouteTenure == 1) to control for Kendall first-year observer effect.",
            )
            cohort_display_options = [
                "Novice (1 yr)",
                "Intermediate (2-5 yrs)",
                "Veteran (6+ yrs)",
            ]
            selected_cohort_display = st.multiselect(
                "Observer Experience Cohorts",
                options=cohort_display_options,
                default=[],
                help="Filter survey runs by observer experience cohorts on the route.",
            )
            cohort_map = {
                "Novice (1 yr)": "Novice",
                "Intermediate (2-5 yrs)": "Intermediate",
                "Veteran (6+ yrs)": "Veteran",
            }
            selected_cohorts = [cohort_map[c] for c in selected_cohort_display if c in cohort_map]

            st.markdown("**Vehicle Traffic Filter**")
            enable_traffic_filter = st.checkbox(
                "Filter by Vehicle Traffic",
                value=False,
                help="Prune high-traffic survey runs exceeding thresholds.",
            )
            max_cars_per_stop: Optional[float] = None
            max_car_total: Optional[int] = None
            if enable_traffic_filter:
                traffic_mode = st.radio(
                    "Traffic Threshold Metric",
                    options=["Cars Per Stop", "Total Cars"],
                    horizontal=True,
                )
                if traffic_mode == "Cars Per Stop":
                    max_cars_per_stop = st.slider(
                        "Max Average Cars Per Stop",
                        min_value=0.0,
                        max_value=100.0,
                        value=10.0,
                        step=0.5,
                        help="Exclude runs where CarsPerStop exceeds this value.",
                    )
                else:
                    max_car_total = st.slider(
                        "Max Total Cars Observed",
                        min_value=0,
                        max_value=1500,
                        value=250,
                        step=10,
                        help="Exclude runs where CarTotal exceeds this value.",
                    )

        enforce_quality_toggle = st.checkbox(
            "Enforce BBS Protocol Quality (RunType=1, Quality=1)",
            value=False,
            help="Filter to runs satisfying official USGS BBS protocol standards. Uncheck for raw unfiltered survey runs.",
        )
        zero_fill_toggle = st.checkbox(
            "Extirpation-Preserving Zero-Filling",
            value=True,
            help="Perform 4-step Cartesian zero-filling with zero geographic leakage.",
        )

    # Load Metadata
    try:
        routes_df, max_year, species_df, guilds_dict = load_metadata_cache(
            raw_dir_str=raw_data_dir if raw_data_dir else None,
            item_id=item_id,
        )
    except Exception as exc:
        traceback.print_exc()
        st.error(f"Failed to load dataset metadata: {exc}")
        st.stop()

    # Layout: Two columns for filters
    col_spatial, col_taxa = st.columns(2)

    # -----------------------------------------------------------------------
    # Column 1: Spatial Filters
    # -----------------------------------------------------------------------
    with col_spatial:
        st.subheader("📍 Spatial Filtering")

        # Distinct States
        num_to_abbr = {v: k for k, v in STATE_ABBR_TO_NUM.items() if len(k) == 2}
        available_statenums = sorted(routes_df["StateNum"].unique().to_list())
        state_options = [f"{num_to_abbr.get(sn, sn)} ({sn})" for sn in available_statenums]
        label_to_num = dict(zip(state_options, available_statenums))

        selected_state_labels = st.multiselect(
            "States / Provinces",
            options=state_options,
            default=[],
            placeholder="Search or select states...",
        )
        selected_states = [label_to_num[lbl] for lbl in selected_state_labels]
        if selected_state_labels:
            with st.expander(f"📋 Selected States ({len(selected_state_labels)})", expanded=False):
                for lbl in selected_state_labels:
                    st.caption(f"• {lbl}")

        # Filter routes by selected states for downstream widgets
        active_routes = routes_df
        if selected_states:
            active_routes = active_routes.filter(pl.col("StateNum").is_in(selected_states))

        # BCRs and Strata
        raw_bcrs = active_routes["BCR"].drop_nulls().unique().to_list()
        available_bcrs = sorted(
            list({str(b).strip() for b in raw_bcrs if str(b).strip()}),
            key=lambda x: int(x) if x.isdigit() else x,
        )
        bcr_labels = [
            f"{bcr} - {BCR_NAMES[bcr.lstrip('0')]}"
            if bcr.lstrip("0") in BCR_NAMES
            else (f"{bcr} - {BCR_NAMES[bcr]}" if bcr in BCR_NAMES else f"{bcr}")
            for bcr in available_bcrs
        ]
        label_to_bcr = dict(zip(bcr_labels, available_bcrs))

        selected_bcr_labels = st.multiselect(
            "Bird Conservation Regions (BCR)",
            options=bcr_labels,
            default=[],
            placeholder="Search BCRs...",
        )
        selected_bcrs = [label_to_bcr[lbl] for lbl in selected_bcr_labels]
        if selected_bcr_labels:
            with st.expander(f"📋 Selected BCRs ({len(selected_bcr_labels)})", expanded=False):
                for lbl in selected_bcr_labels:
                    st.caption(f"• {lbl}")
        if selected_bcrs:
            active_routes = active_routes.filter(
                pl.col("BCR").str.strip_chars().is_in(selected_bcrs) | pl.col("BCR").is_in(selected_bcrs)
            )

        raw_strata = active_routes["Stratum"].drop_nulls().unique().to_list()
        available_strata = sorted(
            list({str(st).strip() for st in raw_strata if str(st).strip()}),
            key=lambda x: int(x) if x.isdigit() else x,
        )
        strata_labels = [
            f"{st_val} - {STRATA_NAMES[st_val.lstrip('0')]}"
            if st_val.lstrip("0") in STRATA_NAMES
            else (f"{st_val} - {STRATA_NAMES[st_val]}" if st_val in STRATA_NAMES else f"{st_val}")
            for st_val in available_strata
        ]
        label_to_stratum = dict(zip(strata_labels, available_strata))

        selected_strata_labels = st.multiselect(
            "Physiographic Strata",
            options=strata_labels,
            default=[],
            placeholder="Search strata...",
        )
        selected_strata = [label_to_stratum[lbl] for lbl in selected_strata_labels]
        if selected_strata_labels:
            with st.expander(f"📋 Selected Strata ({len(selected_strata_labels)})", expanded=False):
                for lbl in selected_strata_labels:
                    st.caption(f"• {lbl}")
        if selected_strata:
            active_routes = active_routes.filter(
                pl.col("Stratum").str.strip_chars().is_in(selected_strata) | pl.col("Stratum").is_in(selected_strata)
            )

        # Candidate Routes
        route_records = (
            active_routes.select(["RouteKey", "RouteName"])
            .unique(subset=["RouteKey"])
            .sort("RouteKey")
            .to_dicts()
        )
        route_labels = [
            f"{r['RouteKey']} - {r['RouteName']}" if r.get("RouteName") and str(r["RouteName"]).strip() else str(r["RouteKey"])
            for r in route_records
        ]
        label_to_route_key = dict(zip(route_labels, [r["RouteKey"] for r in route_records]))

        selected_route_labels = st.multiselect(
            f"Candidate Routes ({len(route_records)} available)",
            options=route_labels,
            default=[],
            placeholder="Search routes by ID/name...",
        )
        selected_routes = [label_to_route_key[lbl] for lbl in selected_route_labels]
        if selected_route_labels:
            with st.expander(f"📋 Selected Routes ({len(selected_route_labels)})", expanded=True):
                for lbl in selected_route_labels:
                    st.caption(f"• {lbl}")
        if selected_routes:
            active_routes = active_routes.filter(pl.col("RouteKey").is_in(selected_routes))

    # -----------------------------------------------------------------------
    # Column 2: Taxonomic Filters
    # -----------------------------------------------------------------------
    with col_taxa:
        st.subheader("🦉 Taxonomic Set-Union Resolver")

        all_species_comm = st.toggle(
            "All Species / Community Survey Mode",
            value=False,
            help="Select the complete breeding species universe (minus non-breeding migrants).",
        )

        selected_orders: List[str] = []
        selected_families: List[str] = []
        selected_guilds: List[str] = []
        selected_species_aous: List[str] = []

        if not all_species_comm:
            # Order filter
            avail_orders = sorted(species_df["Order"].drop_nulls().unique().to_list())
            selected_orders = st.multiselect(
                "Taxonomic Orders",
                options=avail_orders,
                key="taxa_orders",
            )
            if selected_orders:
                with st.expander(f"📋 Selected Orders ({len(selected_orders)})", expanded=False):
                    for o in selected_orders:
                        st.caption(f"• {o}")

            # Family filter
            active_species = species_df
            if selected_orders:
                active_species = active_species.filter(pl.col("Order").is_in(selected_orders))

            avail_families = sorted(active_species["Family"].drop_nulls().unique().to_list())
            selected_families = st.multiselect(
                "Taxonomic Families",
                options=avail_families,
                key="taxa_families",
            )
            if selected_families:
                with st.expander(f"📋 Selected Families ({len(selected_families)})", expanded=False):
                    for f in selected_families:
                        st.caption(f"• {f}")
                active_species = active_species.filter(pl.col("Family").is_in(selected_families))

            # Guild traits filter
            avail_guilds = sorted(
                list(
                    set(v.get("breeding_habitat") for v in guilds_dict.values() if v.get("breeding_habitat"))
                    | set(v.get("foraging_guild") for v in guilds_dict.values() if v.get("foraging_guild"))
                )
            )
            selected_guilds = st.multiselect(
                "Ecological Guilds (Breeding Habitat & Foraging)",
                options=avail_guilds,
                default=[],
                placeholder="Search or select ecological guilds...",
                key="taxa_guild_multiselect",
            )
            if selected_guilds:
                with st.expander(f"📋 Selected Guilds ({len(selected_guilds)})", expanded=False):
                    for g in selected_guilds:
                        st.caption(f"• {g}")

            # Synchronized Species Multi-Select
            # Extract candidate species names
            species_options = (
                active_species.select(["AOU", "English_Common_Name"])
                .sort("English_Common_Name")
                .to_dicts()
            )
            sp_display_list = [f"{s['English_Common_Name']} ({s['AOU']})" for s in species_options]
            sp_name_to_aou = {f"{s['English_Common_Name']} ({s['AOU']})": s["AOU"] for s in species_options}

            selected_sp_labels = st.multiselect(
                f"Synchronized Species ({len(sp_display_list)} available)",
                options=sp_display_list,
                default=[],
                placeholder="Search species by name or AOU...",
                key="taxa_species_multiselect",
            )
            selected_species_aous = [sp_name_to_aou[lbl] for lbl in selected_sp_labels]
            if selected_sp_labels:
                with st.expander(f"📋 Selected Species ({len(selected_sp_labels)})", expanded=True):
                    for sp in selected_sp_labels:
                        st.caption(f"• {sp}")
        else:
            st.info("Community mode enabled: extracting full breeding avifauna matrix.")

    st.divider()

    # -----------------------------------------------------------------------
    # Temporal & Slicing Controls
    # -----------------------------------------------------------------------
    st.subheader("⏱️ Temporal Horizon & Stop Effort Slicing")
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

    with ctrl_col1:
        year_range = st.slider(
            "Survey Year Range (COVID-19 hiatus 2020 auto-excluded)",
            min_value=1966,
            max_value=max_year,
            value=(max(1966, max_year - 15), max_year),
            step=1,
            help="Dynamic temporal horizon discovering latest survey release.",
        )

    with ctrl_col2:
        continuity_pct = st.slider(
            "Proportional Route Continuity Threshold (%)",
            min_value=0.0,
            max_value=100.0,
            value=60.0,
            step=5.0,
            help="Required valid survey runs = ceil(|eligible_years| * pct / 100).",
        )
        stop_options = ["50 (BBS Standard)", "48", "45", "None (Raw / All Stops)"]
        selected_stop_option = st.selectbox(
            "Stop Effort Guard Threshold",
            options=stop_options,
            index=0,
            help="Filter runs by minimum completed stops. Select 'None' to keep all runs regardless of stops completed.",
        )
        stop_map = {
            "50 (BBS Standard)": 50,
            "48": 48,
            "45": 45,
            "None (Raw / All Stops)": None,
        }
        min_stops_val = stop_map[selected_stop_option]

    with ctrl_col3:
        stop_range_val = st.slider(
            "Sub-Route Stop Slicing Window",
            min_value=1,
            max_value=50,
            value=(1, 50),
            step=1,
            help="Sums stop counts across [start_stop, end_stop] as SegmentCount.",
        )

    st.divider()

    # -----------------------------------------------------------------------
    # Live Map Preview
    # -----------------------------------------------------------------------
    st.subheader(f"🗺️ Route Origins Map Preview ({active_routes.height} routes selected)")

    valid_coords_df = (
        active_routes.with_columns(
            pl.col("Latitude").cast(pl.Float64, strict=False),
            pl.col("Longitude").cast(pl.Float64, strict=False),
        )
        .filter(
            pl.col("Latitude").is_not_null() & pl.col("Longitude").is_not_null()
        )
    )

    if not valid_coords_df.is_empty():
        # Convert to pandas for st.map
        pdf_coords = valid_coords_df.select(["Latitude", "Longitude", "RouteName", "RouteKey"]).to_pandas()
        st.map(pdf_coords, latitude="Latitude", longitude="Longitude", zoom=3)
    else:
        st.warning("No geographic coordinates found for current spatial criteria.")

    st.divider()

    # -----------------------------------------------------------------------
    # Execution & Pure Streaming Export
    # -----------------------------------------------------------------------
    st.subheader("🚀 Pipeline Execution & Streaming Export")

    exec_col1, exec_col2 = st.columns([1, 2])

    with exec_col1:
        run_btn = st.button("Run Pipeline (In-Memory)", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner("Executing in-memory pipeline: Ingestion → Zero-Filling → Spatial Anchoring..."):
            try:
                # Prepare arguments
                out_bytes = run_pipeline(
                    states=selected_states if selected_states else None,
                    bcrs=selected_bcrs if selected_bcrs else None,
                    strata=selected_strata if selected_strata else None,
                    routes=selected_routes if selected_routes else None,
                    species=selected_species_aous if selected_species_aous else None,
                    guilds=selected_guilds if selected_guilds else None,
                    families=selected_families if selected_families else None,
                    orders=selected_orders if selected_orders else None,
                    all_species=all_species_comm,
                    start_year=year_range[0],
                    end_year=year_range[1],
                    min_completeness_pct=continuity_pct if continuity_pct > 0 else None,
                    min_stops=min_stops_val,
                    enforce_quality=enforce_quality_toggle,
                    stop_range=list(stop_range_val) if stop_range_val != (1, 50) else None,
                    include_covariates=include_covariates,
                    min_obs_tenure=None,
                    max_obs_tenure=None,
                    exclude_first_year=exclude_first_year,
                    observer_cohorts=selected_cohorts if selected_cohorts else None,
                    max_cars_per_stop=max_cars_per_stop,
                    max_car_total=max_car_total,
                    zero_fill=zero_fill_toggle,
                    resolution=resolution,
                    item_id=item_id,
                    raw_data_dir=raw_data_dir if raw_data_dir else None,
                    output_path=None,  # Zero-Disk Mandate: return bytes
                    format=export_format,
                    shape=shape,
                    crs=crs,
                )

                mime_types = {
                    "parquet": "application/octet-stream",
                    "csv": "text/csv",
                    "geojson": "application/geo+json",
                    "gpkg": "application/x-sqlite3",
                }
                mime = mime_types.get(export_format, "application/octet-stream")
                filename = f"bbs_extract_{shape}_{year_range[0]}_{year_range[1]}.{export_format}"

                st.session_state["pipeline_export"] = {
                    "bytes": out_bytes,
                    "filename": filename,
                    "mime": mime,
                    "size_kb": len(out_bytes) / 1024,
                }
                st.success(f"Pipeline executed successfully! ({len(out_bytes) / 1024:.1f} KiB buffered in RAM)")
            except Exception as err:
                st.error(f"Pipeline Execution Failed: {err}")

    # Render streaming download button if export exists in session_state
    if "pipeline_export" in st.session_state:
        exp = st.session_state["pipeline_export"]
        st.download_button(
            label=f"⬇️ Download {exp['filename']} ({exp['size_kb']:.1f} KiB)",
            data=exp["bytes"],
            file_name=exp["filename"],
            mime=exp["mime"],
            type="primary",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
