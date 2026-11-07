"""Streamlit editor for BBS run profiles (TOML): open, edit, inspect, save, and run.

    uv run streamlit run ui/app.py                       # pick a profile in the sidebar
    BBS_PROFILE=project.toml uv run streamlit run ui/app.py

Everything the CLI does is available here: open/new/save/save-as profiles, fetch the
release, every profile setting, the resolved TOML (`bbs validate`), pick lists
(`bbs values`), inspect, and run. Edits go into a draft until a save. Pick lists come
from the release cache; without a fetched release, list fields fall back to text boxes.
"""

from __future__ import annotations

import json
import os
import tomllib
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from bbs_pipeline.cli import STAGE_LABELS
from bbs_pipeline.config import (
    OBSERVATION_SOURCES,
    SECTION_ORDER,
    STOP_LAYOUTS,
    ProfileError,
    RunProfile,
    dump_profile,
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
PROFILE_DIRS: tuple[str, ...] = (".", "examples", "profiles")
ERROR_LOG = Path("logs") / "ui_errors.log"


# --- helpers -------------------------------------------------------------------------


def _csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _input_error(message: str) -> None:
    """Record an editor input problem; it blocks Save, Inspect, and Run until fixed."""
    st.session_state.setdefault("input_errors", []).append(message)


def _int_list(raw: str, label: str) -> list[int]:
    out: list[int] = []
    for part in _csv_list(raw):
        try:
            out.append(int(part))
        except ValueError:
            _input_error(f"{label}: '{part}' is not a whole number")
    return out


def _key_values(raw: str, label: str, split_values: bool) -> dict[str, Any]:
    """'name=value' lines -> {name: value}; ``split_values`` makes value a comma list."""
    out: dict[str, Any] = {}
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        name, sep, value = line.partition("=")
        if not sep or not name.strip() or not value.strip():
            _input_error(f"{label}, line {number}: expected name=value, got '{line.strip()}'")
            continue
        out[name.strip()] = _csv_list(value) if split_values else value.strip()
    return out


def _joined(values: list[Any]) -> str:
    return ", ".join(str(v) for v in values)


def _mtime(path: str | None) -> float:
    return Path(path).stat().st_mtime if path and Path(path).exists() else 0.0


@st.cache_data(show_spinner="Reading pick lists from the release...")
def _values(profile_json: str, _stamp: tuple) -> ProfileValues:
    """Pick lists for a profile; ``_stamp`` changes when the cache or guild file changes.

    Always offline: building pick lists never downloads; only "Fetch release" does.
    """
    profile = RunProfile.model_validate_json(profile_json)
    offline = profile.model_copy(
        update={"source": profile.source.model_copy(update={"offline": True})}
    )
    return collect_values(offline)


@st.cache_data(show_spinner="Inspecting...")
def _inspect(profile_json: str, _stamp: tuple) -> dict[str, Any]:
    return run_profile(RunProfile.model_validate_json(profile_json), export=False).model_dump(
        mode="json"
    )


def _pick_list_profile(p: dict[str, Any]) -> RunProfile:
    """The draft's pick-list inputs (release, observation source, guild file) as a profile.

    Built from those fields alone, so a draft that is briefly invalid elsewhere (say,
    1995 with the 50_stop source, mid-edit) keeps its pick lists instead of every
    picker falling back to a text box and losing its selection.
    """
    obs, g = p["observations"], p["guilds"]
    return profile_from_data(
        {
            "source": p["source"],
            "observations": {
                "source": obs["source"],
                "lump_subspecies": obs["lump_subspecies"],
            },
            "guilds": {"file": g["file"], "unmatched": g["unmatched"]},
        }
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
    help: str | None = None,
) -> list[Any]:
    """Multiselect over ``options``; a text box when no pick list is available."""
    if options is None:
        raw = st.text_input(f"{label} (comma-separated)", _joined(current), key=key, help=help)
        return _int_list(raw, label) if as_int else _csv_list(raw)
    missing = [c for c in current if c not in options]
    if missing:
        st.warning(f"{label}: not in this release, dropped from the list: {missing}")
    return st.multiselect(
        label,
        options,
        [c for c in current if c in options],
        format_func=fmt,
        key=key,
        help=help,
    )


def _log_failure(action: str, exc: BaseException) -> None:
    """Append the full traceback to ERROR_LOG, so nothing has to be copied off the screen."""
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    trace = "".join(traceback.format_exception(exc))
    with ERROR_LOG.open("a", encoding="utf-8") as log:
        log.write(f"--- {stamp} {action} (profile: {st.session_state.get('profile_path')})\n")
        log.write(trace + "\n")


def _show_failure(action: str, exc: BaseException) -> None:
    """An error box with the message, the traceback on demand, and where it was logged."""
    _log_failure(action, exc)
    st.error(f"{exc}\n\nFull details saved to `{ERROR_LOG}`.", title=f"{action} failed")
    with st.expander("Technical details"):
        st.code("".join(traceback.format_exception(exc)), language=None)


def _draft() -> dict[str, Any]:
    return st.session_state["draft"]


# --- tabs ----------------------------------------------------------------------------


def _source_tab(p: dict[str, Any]) -> None:
    src = p["source"]
    st.caption(
        "Which BBS data release to use and where it lives on disk. Start here: fetch the "
        "release once, and the other tabs get pick lists with names instead of codes."
    )
    src["release"] = st.text_input(
        "Release DOI or ScienceBase item id",
        src["release"],
        key="ui_release",
        help="The USGS BBS dataset release, e.g. 10.5066/P144YU3S (the 1966–2025 release).",
    )
    src["raw_dir"] = st.text_input(
        "Release directory",
        src["raw_dir"],
        key="ui_raw_dir",
        help="Folder holding the downloaded release files (Routes.csv, zips, metadata XML).",
    )
    src["cache_dir"] = st.text_input(
        "Cache directory",
        src["cache_dir"],
        key="ui_cache_dir",
        help="Where the cleaned, typed copy of the release is kept so runs start fast.",
    )
    src["offline"] = st.checkbox(
        "Offline (never download)",
        src["offline"],
        key="ui_offline",
        help="Use only files already on disk; fail instead of reaching ScienceBase.",
    )
    src["rebuild_cache"] = st.checkbox(
        "Rebuild cache on next run",
        src["rebuild_cache"],
        key="ui_rebuild_cache",
        help="Rebuild the cache from the release files, e.g. after replacing them.",
    )
    with st.expander("Advanced: download settings"):
        src["http_timeout_seconds"] = float(
            st.number_input(
                "HTTP timeout (seconds)",
                min_value=1.0,
                value=float(src["http_timeout_seconds"]),
                key="ui_http_timeout",
            )
        )
        src["http_retries"] = int(
            st.number_input("HTTP retries", 0, 10, int(src["http_retries"]), key="ui_http_retries")
        )
        src["http_backoff_seconds"] = float(
            st.number_input(
                "Retry backoff (seconds, doubles each retry)",
                min_value=0.0,
                value=float(src["http_backoff_seconds"]),
                key="ui_http_backoff",
            )
        )
    raw_dir = Path(src["raw_dir"])
    if not (raw_dir / RELEASE_JSON).exists():
        st.info(f"No {RELEASE_JSON} in {raw_dir}: fetch the release to get pick lists.")
        if st.button("Fetch release", key="btn_fetch"):
            try:
                with st.spinner("Downloading from ScienceBase..."):
                    info = fetch_release(src["release"], raw_dir)
            except Exception as exc:  # shown and logged, not swallowed
                _show_failure("Fetch release", exc)
            else:
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
    st.caption(
        "Choose where (routes) and which surveys (runs) to keep. Each filter narrows the "
        "previous ones; leave a list empty to keep everything."
    )
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
            help="Keep routes in these states or provinces. Empty = all.",
        )
        rt["states"] = [names[c]["abbreviation"] or c for c in codes]
    else:
        rt["states"] = _pick(
            "States / provinces",
            None,
            rt["states"],
            "ui_states",
            help="Names, postal abbreviations, or StateNum codes. Empty = all.",
        )
    rt["bcrs"] = _pick(
        "Bird Conservation Regions (BCRs)",
        v.bcrs if v else None,
        rt["bcrs"],
        "ui_bcrs",
        (lambda c: v.label("BCR", c)) if v else str,
        as_int=True,
        help="NABCI ecological regions with similar bird communities and habitats. "
        "Keep routes in these BCRs. Empty = all.",
    )
    rt["strata"] = _pick(
        "BBS physiographic strata",
        v.strata if v else None,
        rt["strata"],
        "ui_strata",
        (lambda c: v.label("Stratum", c)) if v else str,
        as_int=True,
        help="The BBS's own physiographic regions (older than BCRs). Keep routes in these "
        "strata. Empty = all.",
    )

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
        help="Pick individual routes. Empty = every route that passes the filters above.",
    )
    rt["boundary"] = st.text_input(
        "Boundary polygon file (start point inside)",
        rt["boundary"] or "",
        key="ui_boundary",
        help="Optional polygon file. Keeps routes whose start point falls inside it.",
    )

    st.subheader("Runs")
    rn["start_year"], rn["end_year"] = st.slider(
        "Years",
        1966,
        2026,
        (rn["start_year"], rn["end_year"]),
        key="ui_years",
        help="Survey years to keep (inclusive).",
    )
    run_label = (lambda c: v.label("RunType", c)) if v else str
    rn["run_types"] = st.multiselect(
        "RunType",
        sorted({0, 1, *rn["run_types"]}),
        rn["run_types"],
        format_func=run_label,
        key="ui_run_types",
        help="Whether a run met all standard BBS criteria. Most trend analyses keep only "
        "1. Empty = all.",
    )
    quality_label = (lambda c: v.label("QualityCurrentID", c)) if v else str
    rn["quality_current_ids"] = st.multiselect(
        "QualityCurrentID",
        sorted({0, 1, *rn["quality_current_ids"]}),
        rn["quality_current_ids"],
        format_func=quality_label,
        key="ui_quality_ids",
        help="Whether weather, time, date, and route-completion criteria were met. Empty = all.",
    )
    rn["min_continuity"] = st.slider(
        "Minimum continuity (share of non-cancelled years surveyed)",
        0.0,
        1.0,
        float(rn["min_continuity"]),
        0.05,
        key="ui_min_continuity",
        help="Drop routes with a surviving run in less than this share of the non-cancelled "
        "years in range. 0 = keep all; 0.8 = at least 80% of years.",
    )
    tenure = [m.value for m in ObserverTenureMode]
    rn["observer_tenure"] = st.selectbox(
        "Observer tenure",
        tenure,
        tenure.index(rn["observer_tenure"]),
        key="ui_observer_tenure",
        help="include_all keeps every run. The others drop each observer's first year on a "
        "route, drop novices, or keep only veterans (thresholds below).",
    )
    c1, c2 = st.columns(2)
    rn["observer_min_years"] = int(
        c1.number_input(
            "Novice below (years)",
            1,
            60,
            rn["observer_min_years"],
            key="ui_novice",
            help="Observers with fewer years than this count as novices.",
        )
    )
    rn["veteran_min_years"] = int(
        c2.number_input(
            "Veteran from (years)",
            1,
            60,
            rn["veteran_min_years"],
            key="ui_veteran",
            help="Observers with at least this many years count as veterans.",
        )
    )


def _species_tab(p: dict[str, Any], v: ProfileValues | None) -> None:
    obs, sp, g = p["observations"], p["species"], p["guilds"]
    st.caption(
        "How bird counts are read, and which species to keep. Species filters combine "
        "with OR; guild filters then narrow the result."
    )
    st.subheader("Observations")
    sources, layouts = list(OBSERVATION_SOURCES), list(STOP_LAYOUTS)
    c1, c2 = st.columns(2)
    obs["source"] = c1.selectbox(
        "Source",
        sources,
        sources.index(obs["source"]),
        key="ui_obs_source",
        help="50_stop: per-stop counts, 1997 on. 10_stop: 10-stop summaries, 1966 on. "
        "hybrid: both, harmonised to 10-stop bins.",
    )
    obs["stops"] = c2.selectbox(
        "Stop layout",
        layouts,
        layouts.index(obs["stops"]),
        key="ui_stops",
        help="route_total: one count per run. ten_stop_bins: five counts per run "
        "(stops 1–10, 11–20, ...). stops: all 50 stops (needs the 50_stop source).",
    )
    obs["zero_fill"] = st.checkbox(
        "Zero-fill every species ever recorded on the route",
        obs["zero_fill"],
        key="ui_zero_fill",
        help="Add a 0 count for each species seen on the route in any year but not on "
        "this run. Needed for most abundance models.",
    )
    obs["lump_subspecies"] = st.checkbox(
        "Count subspecies and forms under their species",
        obs["lump_subspecies"],
        key="ui_lump",
        help="E.g. fold Dark-eyed Junco forms into Dark-eyed Junco.",
    )
    obs["rolling_years"] = int(
        st.number_input(
            "Rolling mean over N calendar years (0 = off)",
            0,
            60,
            obs["rolling_years"],
            key="ui_rolling",
            help="Smooth each route's counts with a moving average over this many years.",
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
        "Guild file (CSV; see docs/guild_file.md)",
        g["file"] or "",
        key="ui_guild_file",
        help="Optional CSV that tags species with traits (habitat, diet, ...). Lets you "
        "filter by trait and adds the traits as output columns.",
    )
    units = ["report", "error"]
    g["unmatched"] = st.selectbox(
        "Guild rows that match no BBS species",
        units,
        units.index(g["unmatched"]),
        key="ui_guild_unmatched",
    )
    if not g["file"]:
        g["filters"] = {}
        return
    st.caption("Filters: OR within a trait, AND across traits, AND with the species lists.")
    traits = v.guild_traits if v else {}
    filters: dict[str, list[str]] = {}
    for trait, values in traits.items():
        if not values:
            st.caption(f"`{trait}`: no values in the file yet")
            continue
        chosen = _pick(trait, values, g["filters"].get(trait, []), f"ui_guild_{trait}")
        if chosen:
            filters[trait] = chosen
    other = {t: vals for t, vals in g["filters"].items() if t not in traits}
    filters.update(
        _key_values(
            st.text_area(
                "Other trait filters (trait=value1, value2 per line; for traits without a "
                "pick list, such as free-text columns)",
                "\n".join(f"{t}={_joined(vals)}" for t, vals in other.items()),
                key="ui_guild_other_filters",
            ),
            "Other trait filters",
            split_values=True,
        )
    )
    g["filters"] = filters


def _joins_output_tab(p: dict[str, Any], v: ProfileValues | None) -> None:
    st.caption("Extra columns to attach to each output row, and the file to write.")
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
    out["path"] = st.text_input(
        f"Output file ({FORMATS})",
        out["path"],
        key="ui_output_path",
        help="The file extension picks the format. Vector formats include route geometry.",
    )
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
    out["crs"] = st.text_input(
        "CRS (vector formats)",
        out["crs"],
        key="ui_crs",
        help="Coordinate system to write geometry in, e.g. EPSG:4326 or EPSG:5070.",
    )
    out["layer_name"] = st.text_input("GeoPackage layer", out["layer_name"], key="ui_layer_name")
    overlays_raw = st.text_area(
        "Overlays (name=path per line; vector formats)",
        "\n".join(f"{k}={val}" for k, val in out["overlays"].items()),
        key="ui_overlays",
    )
    out["overlays"] = _key_values(overlays_raw, "Overlays", split_values=False)
    out["manifest"] = st.checkbox(
        "Write manifest",
        out["manifest"],
        key="ui_manifest",
        help="Write a manifest file beside the output describing how it was made.",
    )


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
    st.caption(
        "Inspect shows what the profile would produce (row counts at each filter and a "
        "preview) without writing a file. Run writes the output file."
    )
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
    except Exception as exc:  # shown and logged, not swallowed
        _show_failure("Run" if run_clicked else "Inspect", exc)
        return
    if "last_result" in st.session_state:
        _show_result(st.session_state["last_result"])


# --- profile files -------------------------------------------------------------------


def _looks_like_profile(path: Path) -> bool:
    """A TOML file whose top-level sections are all run-profile sections (not pyproject)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return set(data) <= set(SECTION_ORDER)


def _found_profiles() -> list[str]:
    found = {str(p) for d in PROFILE_DIRS for p in Path(d).glob("*.toml") if _looks_like_profile(p)}
    return sorted(found)


def _switch_to(path: Path | None, draft: dict[str, Any]) -> None:
    """Make ``path`` (None = a new, unsaved profile) current and reset every field."""
    for key in [k for k in st.session_state if k.startswith(("ui_", "sb_"))]:
        del st.session_state[key]
    st.session_state.pop("last_result", None)
    st.session_state["profile_path"] = str(path) if path is not None else None
    st.session_state["draft"] = draft
    st.rerun()


def _profile_manager(
    path: Path | None, profile: RunProfile | None, saved: RunProfile | None
) -> None:
    """Sidebar: current profile, Open, New, Save, Save as, and the resolved TOML."""
    st.markdown(f"**Profile:** `{path}`" if path else "**Profile:** new, not saved yet")
    if profile is not None:
        dirty = saved is None or profile != saved
        st.caption("Unsaved changes" if dirty else "Saved")
        if path is not None and st.button(
            "Save profile", key="btn_save", disabled=not dirty, type="primary"
        ):
            save_profile(profile, path)
            st.toast(f"Saved {path}")
            st.rerun()

    with st.expander("Save as", expanded=path is None):
        target = st.text_input(
            "Save as file", value=str(path or "profiles/new_profile.toml"), key="sb_save_as"
        )
        overwrite = st.checkbox("Overwrite if it exists", key="sb_overwrite")
        if st.button("Save as", key="btn_save_as", disabled=profile is None):
            new_path = Path(target.strip())
            if new_path.suffix.lower() != ".toml":
                st.error("A profile file name must end in .toml")
            elif new_path.exists() and not overwrite and new_path != path:
                st.error(f"{new_path} exists; tick 'Overwrite if it exists' to replace it.")
            else:
                assert profile is not None
                save_profile(profile, new_path)
                st.session_state["profile_path"] = str(new_path)
                st.toast(f"Saved {new_path}")
                st.rerun()

    with st.expander("Open / new"):
        found = _found_profiles()
        choice = st.selectbox("Profiles in this project", ["", *found], key="sb_open_choice")
        typed = st.text_input("…or a file path", key="sb_open_path")
        if st.button("Open", key="btn_open", disabled=not (typed.strip() or choice)):
            target_path = Path(typed.strip() or choice)
            try:
                opened = load_profile(target_path)
            except ProfileError as exc:
                st.error(str(exc))
            else:
                _switch_to(target_path, opened.model_dump(mode="json"))
        if st.button("New profile (defaults)", key="btn_new"):
            _switch_to(None, RunProfile().model_dump(mode="json"))

    if profile is not None:
        with st.expander("Profile TOML (what `bbs validate` prints)"):
            text = dump_profile(profile)
            st.code(text, language="toml")
            st.download_button(
                "Download .toml",
                text,
                file_name=(path.name if path else "profile.toml"),
                mime="application/toml",
                key="btn_download_toml",
            )


# --- page ----------------------------------------------------------------------------


def render_editor() -> None:
    st.set_page_config(page_title="BBS Run Profile", layout="wide")
    if "profile_path" not in st.session_state:
        st.session_state["profile_path"] = os.environ.get("BBS_PROFILE", "profile.toml")
    path = Path(st.session_state["profile_path"]) if st.session_state["profile_path"] else None
    saved = load_profile(path) if path is not None and path.exists() else None
    if "draft" not in st.session_state:
        st.session_state["draft"] = (saved or RunProfile()).model_dump(mode="json")
    p = _draft()
    st.session_state["input_errors"] = []

    st.title("BBS Run Profile")
    alerts = st.container()  # filled after the tabs, once the draft has been validated
    values: ProfileValues | None = None
    values_error = None
    try:
        values = _values(*_value_source(_pick_list_profile(p)))
    except Exception as exc:  # no release yet, or an unreadable guild file
        values_error = str(exc)
        if st.session_state.get("logged_values_error") != values_error:  # once, not per rerun
            st.session_state["logged_values_error"] = values_error
            _log_failure("Reading pick lists", exc)
    if values_error:
        alerts.warning(
            f"Fetch the release or fix the release directory on the Release & Cache tab: "
            f"{values_error}",
            title="Pick lists unavailable, using text boxes",
        )

    tabs = st.tabs(
        ["Release & Cache", "Routes & Runs", "Species & Guilds", "Joins & Output", "Inspect & Run"]
    )
    with tabs[0]:
        _source_tab(p)
    with tabs[1]:
        _routes_runs_tab(p, values)
    with tabs[2]:
        _species_tab(p, values)
    with tabs[3]:
        _joins_output_tab(p, values)

    profile: RunProfile | None = None
    with st.sidebar:
        problems = st.session_state["input_errors"]
        if problems:
            st.error("Fix these entries:\n\n" + "\n".join(f"- {m}" for m in problems))
            alerts.error("\n".join(f"- {m}" for m in problems), title="Fix these entries")
        else:
            try:
                profile = profile_from_data(p, origin="from the editor")
            except ProfileError as exc:
                st.error(str(exc))
                alerts.error(str(exc), title="This profile can't be inspected or run yet")
        _profile_manager(path, profile, saved)
    with tabs[4]:
        stamp = _value_source(profile)[1] if profile is not None else ()
        _inspect_run_tab(profile, stamp)


try:
    render_editor()
except Exception as exc:  # st.rerun/st.stop are BaseException, so they pass through
    _log_failure("Unexpected error", exc)
    raise  # Streamlit still shows it on the page
