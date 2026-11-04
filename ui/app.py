"""Streamlit editor for one BBS run profile (TOML), with inspect and run.

The profile path comes from the BBS_PROFILE environment variable (default: profile.toml).
Every change is validated and, when valid, saved back to that file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from bbs_pipeline.config import (
    OBSERVATION_SOURCES,
    STOP_LAYOUTS,
    ProfileError,
    RunProfile,
    load_profile,
    profile_from_data,
    save_profile,
)
from bbs_pipeline.contracts import JOIN_TABLES, ObserverTenureMode
from bbs_pipeline.pipeline import run_profile
from bbs_pipeline.sources.cache import CACHE_MANIFEST


def _csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _int_list(raw: str) -> list[int]:
    return [int(part) for part in _csv_list(raw)]


def _joined(values: list[Any]) -> str:
    return ", ".join(str(v) for v in values)


def _overlays(raw: str) -> dict[str, str]:
    """'name=path' per line -> {name: path}."""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if line.strip():
            name, sep, path = line.partition("=")
            if not sep:
                raise ProfileError(f"Overlay line must be name=path, got '{line.strip()}'")
            out[name.strip()] = path.strip()
    return out


def _filters(raw: str) -> dict[str, list[str]]:
    """'trait=value1, value2' per line -> {trait: [values]}."""
    out: dict[str, list[str]] = {}
    for line in raw.splitlines():
        if line.strip():
            trait, sep, values = line.partition("=")
            if not sep:
                raise ProfileError(f"Guild filter line must be trait=values, got '{line.strip()}'")
            out[trait.strip()] = _csv_list(values)
    return out


def render_editor() -> None:
    st.set_page_config(page_title="BBS Run Profile", layout="wide")
    profile_path = Path(os.environ.get("BBS_PROFILE", "profile.toml"))
    st.title("BBS Run Profile")
    st.caption(f"Editing `{profile_path}`")

    profile = load_profile(profile_path) if profile_path.exists() else RunProfile()
    p = profile.model_dump(mode="json")

    tab_source, tab_filters, tab_species, tab_output, tab_run = st.tabs(
        [
            "Release & Cache",
            "Routes & Runs",
            "Observations & Species",
            "Joins & Output",
            "Inspect & Run",
        ]
    )

    with tab_source:
        src = p["source"]
        src["release"] = st.text_input(
            "Release DOI or ScienceBase item id", src["release"], key="ui_release"
        )
        src["raw_dir"] = st.text_input(
            "Release directory (from `bbs fetch`)", src["raw_dir"], key="ui_raw_dir"
        )
        src["cache_dir"] = st.text_input("Cache directory", src["cache_dir"], key="ui_cache_dir")
        src["offline"] = st.checkbox("Offline (never download)", src["offline"], key="ui_offline")
        src["rebuild_cache"] = st.checkbox(
            "Rebuild cache on next run", src["rebuild_cache"], key="ui_rebuild_cache"
        )
        manifest_file = Path(src["cache_dir"]) / CACHE_MANIFEST
        if manifest_file.is_file():
            st.json(manifest_file.read_text(encoding="utf-8"), expanded=False)
        else:
            st.info(f"No {CACHE_MANIFEST} in the cache directory yet.")

    with tab_filters:
        rt, rn = p["routes"], p["runs"]
        rt["states"] = _csv_list(
            st.text_input(
                "States (StateNum, name, or abbreviation)", _joined(rt["states"]), key="ui_states"
            )
        )
        rt["bcrs"] = _int_list(st.text_input("BCRs", _joined(rt["bcrs"]), key="ui_bcrs"))
        rt["strata"] = _int_list(st.text_input("Strata", _joined(rt["strata"]), key="ui_strata"))
        rt["route_ids"] = _csv_list(
            st.text_input("Route ids (63_001)", _joined(rt["route_ids"]), key="ui_route_ids")
        )
        rt["boundary"] = st.text_input(
            "Boundary polygon file", rt["boundary"] or "", key="ui_boundary"
        )
        rn["start_year"], rn["end_year"] = st.slider(
            "Years", 1966, 2026, (rn["start_year"], rn["end_year"]), key="ui_years"
        )
        rn["run_types"] = st.multiselect(
            "RunType (empty = all)",
            sorted({0, 1, 2, 3, *rn["run_types"]}),
            rn["run_types"],
            key="ui_run_types",
        )
        rn["quality_current_ids"] = st.multiselect(
            "QualityCurrentID (empty = all)",
            sorted({0, 1, *rn["quality_current_ids"]}),
            rn["quality_current_ids"],
            key="ui_quality_ids",
        )
        rn["min_continuity"] = st.slider(
            "Minimum continuity",
            0.0,
            1.0,
            float(rn["min_continuity"]),
            0.05,
            key="ui_min_continuity",
        )
        tenure = [m.value for m in ObserverTenureMode]
        rn["observer_tenure"] = st.selectbox(
            "Observer tenure", tenure, tenure.index(rn["observer_tenure"]), key="ui_observer_tenure"
        )

    with tab_species:
        obs, sp = p["observations"], p["species"]
        sources, layouts = list(OBSERVATION_SOURCES), list(STOP_LAYOUTS)
        obs["source"] = st.selectbox(
            "Observation source", sources, sources.index(obs["source"]), key="ui_obs_source"
        )
        obs["stops"] = st.selectbox(
            "Stop layout", layouts, layouts.index(obs["stops"]), key="ui_stops"
        )
        obs["zero_fill"] = st.checkbox(
            "Zero-fill (route's historical species)", obs["zero_fill"], key="ui_zero_fill"
        )
        obs["lump_subspecies"] = st.checkbox(
            "Lump subspecies", obs["lump_subspecies"], key="ui_lump"
        )
        st.markdown("A species is kept if it matches **any** list (all empty = every species).")
        sp["aous"] = _csv_list(st.text_input("AOU codes", _joined(sp["aous"]), key="ui_aous"))
        sp["common_names"] = _csv_list(
            st.text_input("Common names", _joined(sp["common_names"]), key="ui_common_names")
        )
        sp["families"] = _csv_list(
            st.text_input("Families", _joined(sp["families"]), key="ui_families")
        )
        sp["orders"] = _csv_list(st.text_input("Orders", _joined(sp["orders"]), key="ui_orders"))
        g = p["guilds"]
        g["file"] = st.text_input(
            "Guild file (CSV; see docs/guild_file.md)", g["file"] or "", key="ui_guild_file"
        )
        g["filters"] = _filters(
            st.text_area(
                "Guild filters (trait=value1, value2 per line)",
                "\n".join(f"{k}={', '.join(v)}" for k, v in g["filters"].items()),
                key="ui_guild_filters",
            )
        )
        units = ["report", "error"]
        g["unmatched"] = st.selectbox(
            "Unmatched guild rows", units, units.index(g["unmatched"]), key="ui_guild_unmatched"
        )

    with tab_output:
        out = p["output"]
        st.markdown("Join columns per table: comma-separated names, `*` for all, empty for none.")
        for table in JOIN_TABLES:
            p["joins"][table] = _csv_list(
                st.text_input(f"joins.{table}", _joined(p["joins"][table]), key=f"ui_join_{table}")
            )
        out["path"] = st.text_input(
            "Output file (.parquet .csv .geoparquet .gpkg .fgb .geojson)",
            out["path"],
            key="ui_output_path",
        )
        out["crs"] = st.text_input("CRS (vector formats)", out["crs"], key="ui_crs")
        out["layer_name"] = st.text_input(
            "GeoPackage layer", out["layer_name"], key="ui_layer_name"
        )
        out["overlays"] = _overlays(
            st.text_area(
                "Overlays (name=path per line)",
                "\n".join(f"{k}={v}" for k, v in out["overlays"].items()),
                key="ui_overlays",
            )
        )
        out["manifest"] = st.checkbox("Write manifest", out["manifest"], key="ui_manifest")

    try:
        edited = profile_from_data(p, origin="from the editor")
    except ProfileError as exc:
        st.error(str(exc))
        return
    if edited != profile:
        save_profile(edited, profile_path)
        st.toast(f"Saved {profile_path}")

    with tab_run:
        col_inspect, col_run = st.columns(2)
        action = None
        if col_inspect.button("Inspect", key="btn_inspect"):
            action = False
        if col_run.button("Run", key="btn_run_pipeline"):
            action = True
        if action is not None:
            try:
                result = run_profile(edited, export=action)
                st.session_state["last_result"] = result.model_dump(mode="json")
            except Exception as exc:  # shown to the user, not swallowed
                st.error(f"{'Run' if action else 'Inspect'} failed: {exc}")

        if "last_result" in st.session_state:
            res = st.session_state["last_result"]
            st.dataframe(pl.DataFrame([res["funnel_metrics"]]))
            st.markdown(
                f"**Output columns ({len(res['output_columns'])})**: "
                + ", ".join(res["output_columns"])
            )
            if res.get("preview_records"):
                st.dataframe(pl.DataFrame(res["preview_records"]))
            if res.get("exported_files"):
                st.json(res["exported_files"])


render_editor()
