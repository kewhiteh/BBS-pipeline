"""Streamlit editor for one BBS run profile (TOML): pick lists, inspect, save, and run.

    BBS_PROFILE=project.toml uv run streamlit run ui/app.py

Edits go into a draft; "Save profile" writes the TOML file. Pick lists (states, BCRs,
routes, species, guild traits, join columns) come from the release cache, the same values
`bbs values` prints. Without a fetched release the list fields fall back to text boxes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from bbs_pipeline.cli import STAGE_LABELS
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
from bbs_pipeline.sources.regions import resolve_states
from bbs_pipeline.sources.release import RELEASE_JSON, fetch_release
from bbs_pipeline.values import ProfileValues, collect_values

FORMATS = ".parquet .csv .geoparquet .gpkg .fgb .geojson"


# --- helpers -------------------------------------------------------------------------


def _csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _int_list(raw: str) -> list[int]:
    return [int(part) for part in _csv_list(raw)]


def _joined(values: list[Any]) -> str:
    return ", ".join(str(v) for v in values)


def _mtime(path: str | None) -> float:
    return Path(path).stat().st_mtime if path and Path(path).exists() else 0.0


@st.cache_data(show_spinner="Reading pick lists from the release...")
def _values(profile_json: str, _stamp: tuple) -> ProfileValues:
    """Pick lists for a profile; ``_stamp`` changes when the cache or guild file changes."""
    return collect_values(RunProfile.model_validate_json(profile_json))


@st.cache_data(show_spinner="Inspecting...")
def _inspect(profile_json: str, _stamp: tuple) -> dict[str, Any]:
    return run_profile(RunProfile.model_validate_json(profile_json), export=False).model_dump(
        mode="json"
    )


def _value_source(profile: RunProfile) -> tuple[str, tuple]:
    """Only the fields that change the pick lists, plus file stamps, as the cache key."""
    keyed = profile.model_copy(
        update={
            "routes": type(profile.routes)(),
            "runs": type(profile.runs)(),
            "species": type(profile.species)(),
            "joins": type(profile.joins)(),
            "output": type(profile.output)(),
        }
    )
    manifest = Path(profile.source.cache_dir) / CACHE_MANIFEST
    guild = str(profile.guilds.file) if profile.guilds.file else None
    return keyed.model_dump_json(), (_mtime(str(manifest)), _mtime(guild))


def _pick(
    label: str,
    options: list[Any] | None,
    current: list[Any],
    key: str,
    fmt: Any = str,
    as_int: bool = False,
) -> list[Any]:
    """Multiselect over ``options``; a text box when no pick list is available."""
    if options is None:
        raw = st.text_input(f"{label} (comma-separated)", _joined(current), key=key)
        return _int_list(raw) if as_int else _csv_list(raw)
    missing = [c for c in current if c not in options]
    if missing:
        st.warning(f"{label}: not in this release, dropped from the list: {missing}")
    return st.multiselect(
        label, options, [c for c in current if c in options], format_func=fmt, key=key
    )


def _draft() -> dict[str, Any]:
    return st.session_state["draft"]


# --- tabs ----------------------------------------------------------------------------


def _source_tab(p: dict[str, Any]) -> None:
    src = p["source"]
    src["release"] = st.text_input(
        "Release DOI or ScienceBase item id", src["release"], key="ui_release"
    )
    src["raw_dir"] = st.text_input("Release directory", src["raw_dir"], key="ui_raw_dir")
    src["cache_dir"] = st.text_input("Cache directory", src["cache_dir"], key="ui_cache_dir")
    src["offline"] = st.checkbox("Offline (never download)", src["offline"], key="ui_offline")
    src["rebuild_cache"] = st.checkbox(
        "Rebuild cache on next run", src["rebuild_cache"], key="ui_rebuild_cache"
    )
    raw_dir = Path(src["raw_dir"])
    if not (raw_dir / RELEASE_JSON).exists():
        st.info(f"No {RELEASE_JSON} in {raw_dir}: fetch the release to get pick lists.")
        if st.button("Fetch release", key="btn_fetch"):
            with st.spinner("Downloading from ScienceBase..."):
                info = fetch_release(src["release"], raw_dir)
            st.success(f"Fetched {info.title}")
            st.rerun()
    manifest = Path(src["cache_dir"]) / CACHE_MANIFEST
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        st.caption(
            f"Cache built {data.get('built_at')} from {data.get('release', {}).get('title')}"
        )


def _routes_runs_tab(p: dict[str, Any], v: ProfileValues | None) -> None:
    rt, rn = p["routes"], p["runs"]
    st.subheader("Routes")
    if v is not None:
        names = {s["StateNum"]: s for s in v.states}
        regions = pl.DataFrame(
            {"StateNum": list(names), "StateName": [s["StateName"] for s in v.states]}
        )
        try:
            current = resolve_states(rt["states"], regions) if rt["states"] else []
        except ValueError as exc:
            st.warning(str(exc))
            current = []
        codes = _pick(
            "States / provinces",
            list(names),
            current,
            "ui_states",
            lambda c: f"{names[c]['abbreviation'] or c} · {names[c]['StateName']}",
        )
        rt["states"] = [names[c]["abbreviation"] or c for c in codes]
    else:
        rt["states"] = _pick("States / provinces", None, rt["states"], "ui_states")
    rt["bcrs"] = _pick("BCRs", v.bcrs if v else None, rt["bcrs"], "ui_bcrs", as_int=True)
    rt["strata"] = _pick("Strata", v.strata if v else None, rt["strata"], "ui_strata", as_int=True)

    route_options = None
    if v is not None:
        chosen_states = set(codes) if codes else None
        route_info = {
            r["route_id"]: r
            for r in v.routes
            if (chosen_states is None or r["StateNum"] in chosen_states)
            and (not rt["bcrs"] or r.get("BCR") in rt["bcrs"])
            or r["route_id"] in rt["route_ids"]
        }
        route_options = list(route_info)
    rt["route_ids"] = _pick(
        "Route ids (narrowed to the chosen states and BCRs)",
        route_options,
        rt["route_ids"],
        "ui_route_ids",
        (lambda r: f"{r} · {route_info[r]['RouteName']}") if v else str,
    )
    rt["boundary"] = st.text_input(
        "Boundary polygon file (start point inside)", rt["boundary"] or "", key="ui_boundary"
    )

    st.subheader("Runs")
    rn["start_year"], rn["end_year"] = st.slider(
        "Years", 1966, 2026, (rn["start_year"], rn["end_year"]), key="ui_years"
    )
    rn["run_types"] = st.multiselect(
        "RunType (empty = all; 1 = meets BBS criteria)",
        sorted({0, 1, *rn["run_types"]}),
        rn["run_types"],
        key="ui_run_types",
    )
    rn["quality_current_ids"] = st.multiselect(
        "QualityCurrentID (empty = all; 1 includes >= 45 of 50 stops)",
        sorted({0, 1, *rn["quality_current_ids"]}),
        rn["quality_current_ids"],
        key="ui_quality_ids",
    )
    rn["min_continuity"] = st.slider(
        "Minimum continuity (share of non-cancelled years surveyed)",
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
    c1, c2 = st.columns(2)
    rn["observer_min_years"] = int(
        c1.number_input("Novice below (years)", 1, 60, rn["observer_min_years"], key="ui_novice")
    )
    rn["veteran_min_years"] = int(
        c2.number_input("Veteran from (years)", 1, 60, rn["veteran_min_years"], key="ui_veteran")
    )


def _species_tab(p: dict[str, Any], v: ProfileValues | None) -> None:
    obs, sp, g = p["observations"], p["species"], p["guilds"]
    st.subheader("Observations")
    sources, layouts = list(OBSERVATION_SOURCES), list(STOP_LAYOUTS)
    c1, c2 = st.columns(2)
    obs["source"] = c1.selectbox(
        "Source", sources, sources.index(obs["source"]), key="ui_obs_source"
    )
    obs["stops"] = c2.selectbox("Stop layout", layouts, layouts.index(obs["stops"]), key="ui_stops")
    obs["zero_fill"] = st.checkbox(
        "Zero-fill every species ever recorded on the route", obs["zero_fill"], key="ui_zero_fill"
    )
    obs["lump_subspecies"] = st.checkbox(
        "Count subspecies and forms under their species", obs["lump_subspecies"], key="ui_lump"
    )
    obs["rolling_years"] = int(
        st.number_input(
            "Rolling mean over N calendar years (0 = off)",
            0,
            60,
            obs["rolling_years"],
            key="ui_rolling",
        )
    )

    st.subheader("Species (a species is kept if it matches any list; all empty = every species)")
    names = {s["AOU"]: s["English_Common_Name"] for s in v.species} if v else {}
    sp["aous"] = _pick(
        "Species (AOU)",
        list(names) if v else None,
        sp["aous"],
        "ui_aous",
        lambda a: f"{a} · {names[a]}",
    )
    sp["common_names"] = _pick(
        "Common names",
        sorted(set(names.values())) if v else None,
        sp["common_names"],
        "ui_common_names",
    )
    sp["families"] = _pick("Families", v.families if v else None, sp["families"], "ui_families")
    sp["orders"] = _pick("Orders", v.orders if v else None, sp["orders"], "ui_orders")

    st.subheader("Guilds")
    g["file"] = st.text_input(
        "Guild file (CSV; see docs/guild_file.md)", g["file"] or "", key="ui_guild_file"
    )
    units = ["report", "error"]
    g["unmatched"] = st.selectbox(
        "Guild rows that match no BBS species",
        units,
        units.index(g["unmatched"]),
        key="ui_guild_unmatched",
    )
    traits = v.guild_traits if v else {}
    if g["file"] and traits:
        st.caption("Filters: OR within a trait, AND across traits, AND with the species lists.")
        filters: dict[str, list[str]] = {}
        for trait, values in traits.items():
            if not values:
                st.caption(f"`{trait}`: no values in the file yet")
                continue
            chosen = _pick(trait, values, g["filters"].get(trait, []), f"ui_guild_{trait}")
            if chosen:
                filters[trait] = chosen
        g["filters"] = filters
    elif g["file"]:
        st.caption("Guild trait filters appear once the release and guild file can be read.")


def _joins_output_tab(p: dict[str, Any], v: ProfileValues | None) -> None:
    st.subheader("Join columns")
    for table in JOIN_TABLES:
        current = p["joins"][table]
        options = v.join_columns.get(table, []) if v else None
        if options is not None and not options:
            st.caption(f"`{table}`: nothing to join in this release/profile")
            p["joins"][table] = []
            continue
        everything = st.checkbox(
            f"{table}: all columns", current == ["*"], key=f"ui_join_all_{table}"
        )
        if everything:
            p["joins"][table] = ["*"]
        else:
            p["joins"][table] = _pick(
                table, options, [c for c in current if c != "*"], f"ui_join_{table}"
            )

    st.subheader("Output")
    out = p["output"]
    out["path"] = st.text_input(f"Output file ({FORMATS})", out["path"], key="ui_output_path")
    shapes = ["long", "wide"]
    out["shape"] = st.radio(
        "Shape (long: one row per run and species; wide: one row per route and species)",
        shapes,
        shapes.index(out["shape"]),
        horizontal=True,
        key="ui_shape",
    )
    if out["shape"] == "wide":
        wide_values = ["route_total", "rolling_mean"]
        out["wide_value"] = st.selectbox(
            "Value per year", wide_values, wide_values.index(out["wide_value"]), key="ui_wide_value"
        )
    out["crs"] = st.text_input("CRS (vector formats)", out["crs"], key="ui_crs")
    out["layer_name"] = st.text_input("GeoPackage layer", out["layer_name"], key="ui_layer_name")
    overlays_raw = st.text_area(
        "Overlays (name=path per line; vector formats)",
        "\n".join(f"{k}={val}" for k, val in out["overlays"].items()),
        key="ui_overlays",
    )
    out["overlays"] = {
        name.strip(): path.strip()
        for name, sep, path in (line.partition("=") for line in overlays_raw.splitlines())
        if sep and name.strip()
    }
    out["manifest"] = st.checkbox("Write manifest", out["manifest"], key="ui_manifest")


def _show_result(res: dict[str, Any]) -> None:
    metrics = res["funnel_metrics"]
    st.caption(f"Data source: {metrics['data_source']}")
    stages = pl.DataFrame(
        {
            "stage": [label for _, label in STAGE_LABELS] + ["rows in output file"],
            "count": [metrics[k] for k, _ in STAGE_LABELS] + [res["output_rows"]],
        }
    )
    st.dataframe(stages, hide_index=True)
    st.markdown(f"**Output columns ({len(res['output_columns'])})**")
    st.code(", ".join(res["output_columns"]), language=None)
    report = res.get("guild_report")
    if report:
        st.markdown(f"**Guild file**: {report['matched_rows']} of {report['rows']} rows matched")
        if report["unmatched_rows"]:
            st.warning(f"{len(report['unmatched_rows'])} guild rows match no BBS species")
            st.dataframe(pl.DataFrame(report["unmatched_rows"]), hide_index=True)
        if report["species_without_guild"]:
            st.info(f"{len(report['species_without_guild'])} recorded species have no guild row")
            st.dataframe(pl.DataFrame(report["species_without_guild"]), hide_index=True)
    if res.get("preview_records"):
        st.markdown("**Preview (first 50 rows)**")
        st.dataframe(pl.DataFrame(res["preview_records"]), hide_index=True)
    for path in res.get("exported_files", []):
        st.success(f"Wrote {path}")


def _inspect_run_tab(profile: RunProfile | None, stamp: tuple) -> None:
    if profile is None:
        st.info("Fix the profile errors in the sidebar to inspect or run.")
        return
    live = st.toggle("Live inspect (re-inspect after every change)", key="ui_live")
    c1, c2 = st.columns(2)
    inspect_clicked = c1.button("Inspect", key="btn_inspect")
    run_clicked = c2.button("Run", key="btn_run_pipeline", type="primary")
    try:
        if run_clicked:
            with st.spinner("Running..."):
                st.session_state["last_result"] = run_profile(profile).model_dump(mode="json")
        elif inspect_clicked or live:
            st.session_state["last_result"] = _inspect(profile.model_dump_json(), stamp)
    except Exception as exc:  # shown to the user, not swallowed
        st.error(f"{'Run' if run_clicked else 'Inspect'} failed: {exc}")
        return
    if "last_result" in st.session_state:
        _show_result(st.session_state["last_result"])


# --- page ----------------------------------------------------------------------------


def render_editor() -> None:
    st.set_page_config(page_title="BBS Run Profile", layout="wide")
    profile_path = Path(os.environ.get("BBS_PROFILE", "profile.toml"))
    saved = load_profile(profile_path) if profile_path.exists() else RunProfile()
    if st.session_state.get("draft_path") != str(profile_path):
        st.session_state["draft"] = saved.model_dump(mode="json")
        st.session_state["draft_path"] = str(profile_path)
    p = _draft()

    st.title("BBS Run Profile")
    values: ProfileValues | None = None
    values_error = None
    try:
        values = _values(*_value_source(profile_from_data(p)))
    except Exception as exc:  # no release yet, or an unreadable guild file
        values_error = str(exc)

    tabs = st.tabs(
        ["Release & Cache", "Routes & Runs", "Species & Guilds", "Joins & Output", "Inspect & Run"]
    )
    with tabs[0]:
        _source_tab(p)
        if values_error:
            st.warning(f"Pick lists unavailable, using text boxes: {values_error}")
    with tabs[1]:
        _routes_runs_tab(p, values)
    with tabs[2]:
        _species_tab(p, values)
    with tabs[3]:
        _joins_output_tab(p, values)

    profile: RunProfile | None = None
    with st.sidebar:
        st.markdown(f"**Profile:** `{profile_path}`")
        try:
            profile = profile_from_data(p, origin="from the editor")
        except ProfileError as exc:
            st.error(str(exc))
        if profile is not None:
            dirty = profile != saved
            st.caption("Unsaved changes" if dirty else "Saved")
            if st.button("Save profile", key="btn_save", disabled=not dirty, type="primary"):
                save_profile(profile, profile_path)
                st.toast(f"Saved {profile_path}")
                st.rerun()
    with tabs[4]:
        stamp = _value_source(profile)[1] if profile is not None else ()
        _inspect_run_tab(profile, stamp)


render_editor()
