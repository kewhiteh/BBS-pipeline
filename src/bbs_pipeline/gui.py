"""Reactive Streamlit web dashboard for USGS BBS Pipeline.

Implements Task 7.2 (docs/04_TASKS.md) and §4 of docs/project_intake_brief_usgs_breeding_bird_survey_pipeline.md:
- Reactive multi-select pills for States, BCRs, Strata, and candidate Routes.
- Dynamic taxonomic filters (Clade, Family, Ecological Guild) synchronizing species pills.
- Dynamic survey year dual-slider adapting to the discovered dataset temporal horizon.
- Sub-route stop slicing and route continuity dual-sliders.
- Live Map preview displaying 1:1 route starting coordinates.
- Pure in-memory streaming export via st.download_button (Zero-Disk In-Memory Mandate).
"""

from __future__ import annotations

import io
from pathlib import Path
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
    if "RouteKey" not in routes_df.columns:
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
        include_covariates = st.checkbox(
            "Calculate Observer & Traffic Covariates",
            value=True,
            help="Compute CareerSurveysCompleted, RouteTenure, IsFirstYearObserver, CarTotal, CarsPerStop.",
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
        state_labels = [f"{num_to_abbr.get(sn, sn)} ({sn})" for sn in available_statenums]
        label_to_num = dict(zip(state_labels, available_statenums))

        selected_state_labels = render_pills_or_multiselect(
            "States / Provinces (Pills)",
            options=state_labels,
            key="spatial_state_pills",
        )
        selected_states = [label_to_num[lbl] for lbl in selected_state_labels]

        # Filter routes by selected states for downstream pills
        active_routes = routes_df
        if selected_states:
            active_routes = active_routes.filter(pl.col("StateNum").is_in(selected_states))

        # BCRs and Strata
        available_bcrs = sorted(active_routes["BCR"].drop_nulls().unique().to_list())
        selected_bcrs = render_pills_or_multiselect(
            "Bird Conservation Regions (BCR)",
            options=available_bcrs,
            key="spatial_bcr_pills",
        )
        if selected_bcrs:
            active_routes = active_routes.filter(pl.col("BCR").is_in(selected_bcrs))

        available_strata = sorted(active_routes["Stratum"].drop_nulls().unique().to_list())
        selected_strata = render_pills_or_multiselect(
            "Physiographic Strata",
            options=available_strata,
            key="spatial_strata_pills",
        )
        if selected_strata:
            active_routes = active_routes.filter(pl.col("Stratum").is_in(selected_strata))

        # Candidate Routes Pills
        route_options = sorted(active_routes["RouteKey"].unique().to_list())
        # Cap display to prevent UI overload
        display_routes = route_options[:100]
        selected_routes = render_pills_or_multiselect(
            f"Candidate Routes ({len(route_options)} available, top {len(display_routes)} shown)",
            options=display_routes,
            key="spatial_route_pills",
        )
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
                active_species = active_species.filter(pl.col("Family").is_in(selected_families))

            # Guild traits filter
            avail_guilds = sorted(
                list(
                    set(v.get("breeding_habitat") for v in guilds_dict.values() if v.get("breeding_habitat"))
                    | set(v.get("foraging_guild") for v in guilds_dict.values() if v.get("foraging_guild"))
                )
            )
            selected_guilds = render_pills_or_multiselect(
                "Ecological Guilds (Breeding Habitat & Foraging)",
                options=avail_guilds,
                key="taxa_guild_pills",
            )

            # Synchronized Species Pills
            # Extract candidate species names
            species_options = (
                active_species.select(["AOU", "English_Common_Name"])
                .sort("English_Common_Name")
                .to_dicts()
            )
            sp_display_list = [f"{s['English_Common_Name']} ({s['AOU']})" for s in species_options]
            sp_name_to_aou = {f"{s['English_Common_Name']} ({s['AOU']})": s["AOU"] for s in species_options}

            selected_sp_labels = render_pills_or_multiselect(
                f"Synchronized Species ({len(sp_display_list)} available, top 80 shown)",
                options=sp_display_list[:80],
                key="taxa_species_pills",
            )
            selected_species_aous = [sp_name_to_aou[lbl] for lbl in selected_sp_labels]
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
        min_stops_val = st.selectbox(
            "Stop Effort Guard Threshold",
            options=[50, 48, 45],
            index=0,
            help="Valid runs must satisfy TotalStops >= min_stops.",
        )

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

    valid_coords_df = active_routes.filter(
        pl.col("Latitude").is_not_null() & pl.col("Longitude").is_not_null()
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
                    stop_range=list(stop_range_val) if stop_range_val != (1, 50) else None,
                    include_covariates=include_covariates,
                    zero_fill=zero_fill_toggle,
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
