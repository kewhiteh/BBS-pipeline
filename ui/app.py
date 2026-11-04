"""Two-Way Streamlit Visual Workbench for BBS-Pipeline v3.

All UI controls read from and write directly back to the three frozen Pydantic v2
JSON contracts in `config/` (`guilds.json`, `protocol.json`, `spatial.json`).
Zero business logic lives in UI callbacks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import polars as pl
import streamlit as st

from bbs_pipeline.contracts import (
    Covid2020Mode,
    ExportFormat,
    GeometrySource,
    GuildProfileContract,
    GuildRegistryContract,
    IngressMode,
    JoinDimension,
    ObserverTenureMode,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpatialExportConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.pipeline import run_bbs_pipeline_from_dir


def _parse_csv_strings(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_csv_ints(raw: str) -> list[int]:
    items = _parse_csv_strings(raw)
    result: list[int] = []
    for item in items:
        if item.lstrip("-").isdigit():
            result.append(int(item))
    return result


def _parse_custom_overlays(raw: str) -> dict[str, Path]:
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): Path(str(v)) for k, v in parsed.items()}
    except Exception:
        pass
    overlays: dict[str, Path] = {}
    for pair in text.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() and v.strip():
                overlays[k.strip()] = Path(v.strip())
    return overlays


def render_workbench() -> None:
    st.set_page_config(
        page_title="BBS-Pipeline v3 Workbench",
        layout="wide",
    )
    st.title("North American Breeding Bird Survey (BBS) v3 Workbench")

    config_dir = Path(os.environ.get("BBS_CONFIG_DIR", "config"))
    guilds_path = config_dir / "guilds.json"
    protocol_path = config_dir / "protocol.json"
    spatial_path = config_dir / "spatial.json"

    if not (guilds_path.exists() and protocol_path.exists() and spatial_path.exists()):
        PipelineConfigBundle().save_to_dir(config_dir)

    bundle = PipelineConfigBundle.load_from_dir(config_dir)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "1. Data Ingress & Cache Manager",
            "2. Taxonomic & Guild Studio",
            "3. Protocol, Geography & Join Matrix",
            "4. Spatial & Sink Configurator",
            "5. Execution & Audit Preview",
        ]
    )

    # -------------------------------------------------------------------------
    # TAB 1: Data Ingress & Cache Manager
    # -------------------------------------------------------------------------
    with tab1:
        st.subheader("Three-Tier Ingress Gate & Intake Manifest")
        ingress_options = [m.value for m in IngressMode]
        current_ingress_idx = (
            ingress_options.index(bundle.protocol.ingress_mode.value)
            if bundle.protocol.ingress_mode.value in ingress_options
            else 0
        )
        ui_ingress_mode = st.selectbox(
            "Ingress Execution Mode",
            options=ingress_options,
            index=current_ingress_idx,
            key="ui_ingress_mode",
        )
        ui_sanitized_cache_dir = st.text_input(
            "Sanitized Cache Directory",
            value=str(bundle.protocol.sanitized_cache_dir),
            key="ui_sanitized_cache_dir",
        )
        ui_raw_data_dir = st.text_input(
            "Local Raw Data Directory",
            value=str(bundle.protocol.raw_data_dir),
            key="ui_raw_data_dir",
        )
        ui_sciencebase_doi = st.text_input(
            "USGS ScienceBase Release DOI",
            value=bundle.protocol.sciencebase_doi,
            key="ui_sciencebase_doi",
        )
        ui_force_resanitize = st.checkbox(
            "Force Re-Sanitize on Next Run",
            value=bundle.protocol.force_resanitize,
            key="ui_force_resanitize",
        )
        btn_force_resanitize = st.button(
            "Force Re-Sanitize",
            key="btn_force_resanitize",
        )

        manifest_file = Path(ui_sanitized_cache_dir) / "intake_manifest.json"
        if manifest_file.is_file():
            try:
                manifest_json = json.loads(manifest_file.read_text(encoding="utf-8"))
                st.success(
                    f"Cache Status: {manifest_json.get('status', 'healthy')} "
                    f"(Engine: {manifest_json.get('engine', 'unknown')})"
                )
                st.json(manifest_json)
            except Exception as exc:
                st.warning(f"Unable to parse intake_manifest.json: {exc}")
        else:
            st.info("No intake_manifest.json detected in Sanitized Cache Directory.")

    # -------------------------------------------------------------------------
    # TAB 2: Taxonomic & Guild Studio
    # -------------------------------------------------------------------------
    with tab2:
        st.subheader("Ecological Guild Profiles & Taxonomic Filters")
        existing_profiles = dict(bundle.guilds.profiles)
        profile_keys = list(existing_profiles.keys())
        default_active = bundle.guilds.active_guild or (
            profile_keys[0] if profile_keys else "custom_guild"
        )
        if default_active not in profile_keys and profile_keys:
            default_active = profile_keys[0]

        active_idx = profile_keys.index(default_active) if default_active in profile_keys else 0
        ui_active_guild = st.selectbox(
            "Active Guild Profile",
            options=profile_keys if profile_keys else ["custom_guild"],
            index=active_idx,
            key="ui_active_guild",
        )

        selected_profile = existing_profiles.get(
            ui_active_guild,
            GuildProfileContract(),
        )
        old_active_profile = existing_profiles.get(
            default_active,
            selected_profile,
        )

        ui_guild_key = st.text_input(
            "Guild Profile Key (edit to create/rename)",
            value=ui_active_guild,
            key="ui_guild_key",
        )
        ui_guild_citation = st.text_input(
            "Bibliographic Citation",
            value=selected_profile.citation,
            key="ui_guild_citation",
        )
        ui_guild_description = st.text_input(
            "Ecological Description",
            value=selected_profile.description,
            key="ui_guild_description",
        )
        ui_guild_aou_codes = st.text_input(
            "AOU Species Codes (comma-separated 5-digit codes)",
            value=", ".join(selected_profile.aou_codes),
            key="ui_guild_aou_codes",
        )
        ui_guild_orders = st.text_input(
            "Taxonomic Orders (comma-separated)",
            value=", ".join(selected_profile.orders),
            key="ui_guild_orders",
        )
        ui_guild_families = st.text_input(
            "Taxonomic Families (comma-separated)",
            value=", ".join(selected_profile.families),
            key="ui_guild_families",
        )
        ui_guild_english_names = st.text_input(
            "English Common Names (comma-separated)",
            value=", ".join(selected_profile.english_names),
            key="ui_guild_english_names",
        )
        ui_guild_lump = st.checkbox(
            "Lump Subspecies, Hybrids & Unidentified Forms",
            value=selected_profile.lump_subspecies_and_hybrids,
            key="ui_guild_lump",
        )

        col_save_g, col_del_g = st.columns(2)
        with col_save_g:
            btn_save_guild = st.button("Save Guild Profile", key="btn_save_guild")
        with col_del_g:
            btn_delete_guild = st.button("Delete Guild Profile", key="btn_delete_guild")

        target_key = ui_guild_key.strip() or ui_active_guild
        edited_profile = GuildProfileContract(
            citation=ui_guild_citation,
            description=ui_guild_description,
            aou_codes=_parse_csv_strings(ui_guild_aou_codes),
            orders=_parse_csv_strings(ui_guild_orders),
            families=_parse_csv_strings(ui_guild_families),
            english_names=_parse_csv_strings(ui_guild_english_names),
            lump_subspecies_and_hybrids=ui_guild_lump,
        )

        if btn_delete_guild and target_key in existing_profiles and len(existing_profiles) > 1:
            existing_profiles.pop(target_key, None)
            next_active = next(iter(existing_profiles.keys()))
            updated_guilds = GuildRegistryContract(
                active_guild=next_active,
                profiles=existing_profiles,
            )
            updated_guilds.save_json(guilds_path)
        else:
            # Determine if the user switched active_guild without modifying the old profile's fields
            fields_match_old = edited_profile == old_active_profile and target_key == default_active
            if ui_active_guild != default_active and fields_match_old and not btn_save_guild:
                updated_guilds = GuildRegistryContract(
                    active_guild=ui_active_guild,
                    profiles=existing_profiles,
                )
            else:
                existing_profiles[target_key] = edited_profile
                new_active = (
                    target_key
                    if (btn_save_guild or target_key != default_active)
                    else ui_active_guild
                )
                updated_guilds = GuildRegistryContract(
                    active_guild=new_active,
                    profiles=existing_profiles,
                )
            updated_guilds.save_json(guilds_path)

    # -------------------------------------------------------------------------
    # TAB 3: Protocol, Geography, COVID Gap & Join Matrix
    # -------------------------------------------------------------------------
    with tab3:
        st.subheader("Survey Protocol, Spatiotemporal Filters & Join Matrix")
        btn_golden_preset = st.button(
            "Apply Golden Protocol Preset",
            key="btn_golden_preset",
        )
        ui_golden_preset = st.checkbox(
            "Golden Protocol Preset",
            value=True if btn_golden_preset else bundle.protocol.golden_protocol_preset,
            key="ui_golden_preset",
        )

        run_type_options = sorted({0, 1, 2, 3, *bundle.protocol.run_types})
        ui_run_types = st.multiselect(
            "Permitted RunType Codes",
            options=run_type_options,
            default=[1] if (btn_golden_preset or ui_golden_preset) else bundle.protocol.run_types,
            key="ui_run_types",
        )

        quality_options = sorted({0, 1, *bundle.protocol.quality_current_ids})
        ui_quality_ids = st.multiselect(
            "Permitted QualityCurrentID Codes",
            options=quality_options,
            default=[1]
            if (btn_golden_preset or ui_golden_preset)
            else bundle.protocol.quality_current_ids,
            key="ui_quality_ids",
        )

        tenure_options = [m.value for m in ObserverTenureMode]
        tenure_idx = (
            tenure_options.index(bundle.protocol.observer_tenure_mode.value)
            if bundle.protocol.observer_tenure_mode.value in tenure_options
            else 0
        )
        ui_observer_tenure_mode = st.selectbox(
            "Observer Tenure Policy",
            options=tenure_options,
            index=tenure_idx,
            key="ui_observer_tenure_mode",
        )

        ui_year_range = st.slider(
            "Survey Year Range",
            min_value=1966,
            max_value=2026,
            value=(int(bundle.protocol.year_range[0]), int(bundle.protocol.year_range[1])),
            key="ui_year_range",
        )

        covid_options = [m.value for m in Covid2020Mode]
        covid_idx = (
            covid_options.index(bundle.protocol.covid_2020_mode.value)
            if bundle.protocol.covid_2020_mode.value in covid_options
            else 0
        )
        ui_covid_2020_mode = st.selectbox(
            "COVID-19 (2020) Cancellation Handling",
            options=covid_options,
            index=covid_idx,
            key="ui_covid_2020_mode",
        )

        ui_continuity_ratio = st.slider(
            "Minimum Route Continuity Ratio",
            min_value=0.0,
            max_value=1.0,
            value=float(bundle.protocol.min_route_continuity_ratio),
            step=0.05,
            key="ui_continuity_ratio",
        )

        ui_rolling_window = st.number_input(
            "Rolling Window Length (Years)",
            min_value=1,
            max_value=30,
            value=int(bundle.protocol.rolling_window_years or 1),
            key="ui_rolling_window",
        )

        ui_states = st.text_input(
            "State Codes (comma-separated integers, empty = all)",
            value=", ".join(str(s) for s in bundle.protocol.states),
            key="ui_states",
        )
        ui_bcrs = st.text_input(
            "Bird Conservation Regions (BCRs, comma-separated integers)",
            value=", ".join(str(b) for b in bundle.protocol.bcrs),
            key="ui_bcrs",
        )
        ui_strata = st.text_input(
            "Physiographic Strata (comma-separated integers)",
            value=", ".join(str(s) for s in bundle.protocol.strata),
            key="ui_strata",
        )
        ui_routes = st.text_input(
            "Canonical Route IDs (comma-separated SS_RRR)",
            value=", ".join(bundle.protocol.routes),
            key="ui_routes",
        )

        temporal_options = [m.value for m in TemporalRoutingMode]
        temporal_idx = (
            temporal_options.index(bundle.protocol.temporal_routing_mode.value)
            if bundle.protocol.temporal_routing_mode.value in temporal_options
            else 0
        )
        ui_temporal_routing_mode = st.selectbox(
            "Temporal Harmonization Mode",
            options=temporal_options,
            index=temporal_idx,
            key="ui_temporal_routing_mode",
        )

        stop_options = [m.value for m in StopAggregationMode]
        stop_idx = (
            stop_options.index(bundle.protocol.stop_aggregation.value)
            if bundle.protocol.stop_aggregation.value in stop_options
            else 0
        )
        ui_stop_aggregation = st.selectbox(
            "Stop Aggregation Granularity",
            options=stop_options,
            index=stop_idx,
            key="ui_stop_aggregation",
        )

        ui_zero_fill = st.checkbox(
            "Enable Extirpation Zero-Fill Matrix",
            value=bundle.protocol.enable_extirpation_zero_fill,
            key="ui_zero_fill",
        )

        st.markdown("**A-La-Carte Dimension Joins**")
        active_joins_set = set(bundle.protocol.include_joins)
        ui_join_species = st.checkbox(
            "Join Species Taxonomy",
            value=JoinDimension.SPECIES in active_joins_set,
            key="ui_join_species",
        )
        ui_join_routes = st.checkbox(
            "Join Routes Metadata",
            value=JoinDimension.ROUTES in active_joins_set,
            key="ui_join_routes",
        )
        ui_join_weather = st.checkbox(
            "Join Weather Covariates",
            value=JoinDimension.WEATHER in active_joins_set,
            key="ui_join_weather",
        )
        ui_join_bcr = st.checkbox(
            "Join Bird Conservation Regions (BCR)",
            value=JoinDimension.BCR in active_joins_set,
            key="ui_join_bcr",
        )

        st.button("Save Protocol Config", key="btn_save_protocol")

        selected_joins: list[JoinDimension] = []
        if ui_join_species:
            selected_joins.append(JoinDimension.SPECIES)
        if ui_join_routes:
            selected_joins.append(JoinDimension.ROUTES)
        if ui_join_weather:
            selected_joins.append(JoinDimension.WEATHER)
        if ui_join_bcr:
            selected_joins.append(JoinDimension.BCR)

        effective_golden = True if btn_golden_preset else bool(ui_golden_preset)
        effective_force_resanitize = True if btn_force_resanitize else bool(ui_force_resanitize)

        updated_protocol = ProtocolConfigContract(
            ingress_mode=IngressMode(ui_ingress_mode),
            sanitized_cache_dir=Path(ui_sanitized_cache_dir),
            raw_data_dir=Path(ui_raw_data_dir),
            sciencebase_doi=ui_sciencebase_doi,
            http_max_retries=bundle.protocol.http_max_retries,
            http_backoff_seconds=bundle.protocol.http_backoff_seconds,
            http_timeout_seconds=bundle.protocol.http_timeout_seconds,
            force_resanitize=effective_force_resanitize,
            golden_protocol_preset=effective_golden,
            run_types=[int(x) for x in ui_run_types] if ui_run_types else [1],
            quality_current_ids=[int(x) for x in ui_quality_ids] if ui_quality_ids else [1],
            year_range=(int(ui_year_range[0]), int(ui_year_range[1])),
            states=_parse_csv_ints(ui_states),
            bcrs=_parse_csv_ints(ui_bcrs),
            strata=_parse_csv_ints(ui_strata),
            routes=_parse_csv_strings(ui_routes),
            covid_2020_mode=Covid2020Mode(ui_covid_2020_mode),
            min_route_continuity_ratio=float(ui_continuity_ratio),
            rolling_window_years=int(ui_rolling_window),
            observer_tenure_mode=ObserverTenureMode(ui_observer_tenure_mode),
            observer_min_years=bundle.protocol.observer_min_years,
            veteran_min_years=bundle.protocol.veteran_min_years,
            temporal_routing_mode=TemporalRoutingMode(ui_temporal_routing_mode),
            stop_aggregation=StopAggregationMode(ui_stop_aggregation),
            enable_extirpation_zero_fill=bool(ui_zero_fill),
            include_joins=selected_joins,
        )
        updated_protocol.save_json(protocol_path)

    # -------------------------------------------------------------------------
    # TAB 4: Spatial & Sink Configurator
    # -------------------------------------------------------------------------
    with tab4:
        st.subheader("Spatial Join, Linear Referencing & Multi-CRS Sinks")
        ui_enable_spatial_join = st.checkbox(
            "Enable Spatial Join & Vector Export",
            value=bundle.spatial.enable_spatial_join,
            key="ui_enable_spatial_join",
        )

        geom_options = [g.value for g in GeometrySource]
        geom_idx = (
            geom_options.index(bundle.spatial.geometry_source.value)
            if bundle.spatial.geometry_source.value in geom_options
            else 0
        )
        ui_geometry_source = st.selectbox(
            "Primary Route Geometry Source",
            options=geom_options,
            index=geom_idx,
            key="ui_geometry_source",
        )

        ui_route_geometry_path = st.text_input(
            "Route Polylines Path",
            value=str(bundle.spatial.route_geometry_path),
            key="ui_route_geometry_path",
        )

        ui_linear_referencing = st.checkbox(
            "Enable Stop Linear Referencing (Sub-route bins / 50-stop interpolation)",
            value=bundle.spatial.enable_stop_linear_referencing,
            key="ui_linear_referencing",
        )

        current_crs_set = {c.upper() for c in bundle.spatial.target_crs_list}
        ui_crs_4326 = st.checkbox(
            "EPSG:4326 (WGS84 Geographic)",
            value="EPSG:4326" in current_crs_set,
            key="ui_crs_4326",
        )
        ui_crs_5070 = st.checkbox(
            "EPSG:5070 (CONUS Albers Equal Area)",
            value="EPSG:5070" in current_crs_set,
            key="ui_crs_5070",
        )
        ui_crs_32119 = st.checkbox(
            "EPSG:32119 (NAD83 / North Carolina State Plane)",
            value="EPSG:32119" in current_crs_set,
            key="ui_crs_32119",
        )

        extra_crs = [
            c
            for c in bundle.spatial.target_crs_list
            if c.upper() not in {"EPSG:4326", "EPSG:5070", "EPSG:32119"}
        ]
        ui_custom_crs = st.text_input(
            "Additional Custom EPSG Codes (comma-separated)",
            value=", ".join(extra_crs),
            key="ui_custom_crs",
        )

        format_options = [f.value for f in ExportFormat]
        default_formats = [f.value for f in bundle.spatial.export_formats]
        ui_export_formats = st.multiselect(
            "Export Sink Formats",
            options=format_options,
            default=default_formats,
            key="ui_export_formats",
        )

        overlays_serialized = json.dumps(
            {k: str(v) for k, v in bundle.spatial.custom_overlay_paths.items()}
        )
        ui_custom_overlays = st.text_input(
            "Custom Polygon Overlays (JSON or name=path pairs)",
            value=overlays_serialized if bundle.spatial.custom_overlay_paths else "",
            key="ui_custom_overlays",
        )

        ui_output_dir = st.text_input(
            "Export Output Directory",
            value=str(bundle.spatial.output_dir),
            key="ui_output_dir",
        )

        st.button("Save Spatial Config", key="btn_save_spatial")

        crs_list: list[str] = []
        if ui_crs_4326:
            crs_list.append("EPSG:4326")
        if ui_crs_5070:
            crs_list.append("EPSG:5070")
        if ui_crs_32119:
            crs_list.append("EPSG:32119")
        for custom_code in _parse_csv_strings(ui_custom_crs):
            crs_list.append(custom_code)
        if not crs_list:
            crs_list.append("EPSG:4326")

        updated_spatial = SpatialExportConfigContract(
            enable_spatial_join=bool(ui_enable_spatial_join),
            geometry_source=GeometrySource(ui_geometry_source),
            route_geometry_path=Path(ui_route_geometry_path),
            agol_fallback_url=bundle.spatial.agol_fallback_url,
            enable_stop_linear_referencing=bool(ui_linear_referencing),
            custom_overlay_paths=_parse_custom_overlays(ui_custom_overlays),
            target_crs_list=crs_list,
            export_formats=[ExportFormat(f) for f in ui_export_formats]
            if ui_export_formats
            else [ExportFormat.PARQUET],
            output_dir=Path(ui_output_dir),
        )
        updated_spatial.save_json(spatial_path)

    # -------------------------------------------------------------------------
    # TAB 5: Execution & Audit Preview
    # -------------------------------------------------------------------------
    with tab5:
        st.subheader("End-to-End Pipeline Execution & Funnel Audit")
        if st.button("Run BBS Pipeline", key="btn_run_pipeline"):
            try:
                result = run_bbs_pipeline_from_dir(config_dir)
                st.session_state["last_pipeline_result"] = result.model_dump(mode="json")
                st.success("Pipeline execution completed.")
            except Exception as exc:
                st.error(f"Pipeline execution failed: {exc}")

        if "last_pipeline_result" in st.session_state:
            res_data = st.session_state["last_pipeline_result"]
            fm = res_data.get("funnel_metrics", {})
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Raw Sampling Events", fm.get("total_sampling_events_raw", 0))
            c2.metric("Protocol Filtered Events", fm.get("protocol_filtered_events", 0))
            c3.metric("Continuity Routes", fm.get("continuity_filtered_routes", 0))
            c4.metric("Final Output Rows", fm.get("final_output_rows", 0))

            if res_data.get("preview_records"):
                st.markdown("**Observations Preview (First 50 Rows)**")
                st.dataframe(pl.DataFrame(res_data["preview_records"]))

            if res_data.get("guild_summary_records"):
                st.markdown("**Guild Rollup Summary**")
                st.dataframe(pl.DataFrame(res_data["guild_summary_records"]))

            if res_data.get("exported_files"):
                st.markdown("**Exported Artifacts**")
                st.json(res_data["exported_files"])


render_workbench()
