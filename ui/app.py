"""Streamlit editor for BBS run profiles (TOML): open, edit, inspect, save, and run.

    uv run streamlit run ui/app.py                       # open profiles from the header
    BBS_PROFILE=project.toml uv run streamlit run ui/app.py

Two pages: Build (the dataset settings, inspect and run) and Settings (the release,
folders, and download settings: the profile's [source] section, set about once a year).
The header on both pages shows the open profile, Save, and a Profile menu (open, new,
example, save as, TOML). Everything the CLI does is available here. Edits go into a
draft until a save. Pick lists come from the release cache; without a fetched release,
list fields fall back to text boxes. Layout plan: docs/instructions/UI_REDESIGN_PLAN.md.
"""

from __future__ import annotations

import json
import os
import tomllib
import traceback
import zipfile  # for BadZipFile from uploads
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import polars as pl
import pyogrio
import pyproj
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
from bbs_pipeline.contracts import JOIN_TABLES
from bbs_pipeline.pipeline import run_profile
from bbs_pipeline.sources.cache import CACHE_MANIFEST
from bbs_pipeline.sources.regions import resolve_states
from bbs_pipeline.sources.release import RELEASE_JSON, fetch_release
from bbs_pipeline.spatial.files import save_gis_upload
from bbs_pipeline.spatial.geometry import automatic_overlay_column
from bbs_pipeline.values import ProfileValues, collect_values

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


# --- GIS files -----------------------------------------------------------------------

GIS_EXTENSIONS = frozenset({".gpkg", ".shp", ".geojson", ".fgb", ".geoparquet"})
UPLOAD_TYPES = ["gpkg", "geojson", "fgb", "geoparquet", "zip"]  # zip = zipped shapefile
GIS_UPLOAD_DIR = Path("data") / "gis"  # data/ is git-ignored
# Not searched for GIS files: tooling, caches, the raw release, outputs, test fixtures
SKIP_DIRS = frozenset(
    {".git", ".venv", "__pycache__", "node_modules", ".claude", ".agents", "output", "tests"}
)
SKIP_PATHS = frozenset({Path("data") / "raw", Path("data") / "sanitized_cache"})
MAX_GIS_FILES = 300
TYPE_A_PATH = "Type a path…"


def _gis_files() -> list[str]:
    """GIS files under the project, for pick lists (at most MAX_GIS_FILES)."""
    found: list[str] = []
    for root, dirs, files in os.walk("."):
        here = Path(root)
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP_DIRS and (here / d).relative_to(".") not in SKIP_PATHS
        )
        for name in sorted(files):
            if Path(name).suffix.lower() in GIS_EXTENSIONS:
                found.append(str((here / name).relative_to(".")))
                if len(found) >= MAX_GIS_FILES:
                    return found
    return found


@st.cache_data(show_spinner=False, max_entries=64)
def _gis_columns(path: str, _mtime: float) -> tuple[list[str], int]:
    """(attribute columns, feature count) of a GIS file, without reading its geometry."""
    if Path(path).suffix.lower() == ".geoparquet":
        gdf = gpd.read_parquet(path)
        return [c for c in gdf.columns if c != gdf.geometry.name], len(gdf)
    info = pyogrio.read_info(path)
    return [str(f) for f in info["fields"]], int(info["features"])


@st.cache_data(show_spinner=False, max_entries=64)
def _gis_sample(path: str, column: str, _mtime: float) -> list[str]:
    """A few distinct values of ``column``, to show what the labels will look like."""
    if Path(path).suffix.lower() == ".geoparquet":
        values = gpd.read_parquet(path, columns=[column])[column]
    else:
        values = pyogrio.read_dataframe(path, columns=[column], read_geometry=False)[column]
    return [str(v) for v in values.dropna().unique()[:5]]


def _uploaded(key: str) -> None:
    """on_change for a GIS upload: save it and select it in the matching file list."""
    uploaded = st.session_state.get(f"{key}_upload")
    if uploaded is None:
        return
    try:
        saved = save_gis_upload(uploaded.name, uploaded.getvalue(), GIS_UPLOAD_DIR)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        st.session_state[f"{key}_upload_error"] = str(exc)
        _log_failure("Upload", exc)
        return
    st.session_state.pop(f"{key}_upload_error", None)
    st.session_state[key] = str(saved)


def _gis_file_picker(label: str, current: str, key: str, help: str) -> str:
    """Pick a GIS file found in the project, upload one, or type a path ('' = none)."""
    found = _gis_files()
    options = ["", *found]
    if current and current not in options:
        options.append(current)  # keep a saved path that is outside the searched folders
    if st.session_state.get(key) not in (*options, TYPE_A_PATH, None):
        options.append(st.session_state[key])  # just uploaded
    options.append(TYPE_A_PATH)
    choice = st.selectbox(
        label,
        options,
        options.index(current) if current in options else 0,
        format_func=lambda o: "(none)" if o == "" else o,
        key=key,
        help=help,
    )
    if choice == TYPE_A_PATH:
        choice = st.text_input("File path", current, key=f"{key}_typed").strip()
    with st.popover("Upload a file", icon=":material/upload:"):
        st.file_uploader(
            f"GeoPackage, GeoJSON, FlatGeobuf, GeoParquet, or a zipped shapefile. Saved "
            f"to `{GIS_UPLOAD_DIR}/` (not committed).",
            type=UPLOAD_TYPES,
            key=f"{key}_upload",
            on_change=_uploaded,
            args=(key,),
        )
    if f"{key}_upload_error" in st.session_state:
        st.error(st.session_state[f"{key}_upload_error"], title="Upload failed")
    if choice and not Path(choice).exists():
        st.warning(f"`{choice}` doesn't exist.")
    return choice


def _overlay_card(row: int, name: str, spec: dict[str, Any]) -> tuple[str, dict[str, Any], bool]:
    """One overlay: name, file, label column. Returns (name, spec, remove clicked)."""
    with st.container(border=True):
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        name = c1.text_input(
            "Name",
            name,
            key=f"ui_ov_{row}_name",
            help="The output column is overlay_<name>, e.g. overlay_basin.",
        ).strip()
        remove = c2.button("Remove", key=f"btn_ov_{row}_remove", icon=":material/delete:")
        file = _gis_file_picker(
            "Polygon file",
            str(spec.get("file") or ""),
            f"ui_ov_{row}_file",
            "Each route is tagged with the polygon its start point falls in.",
        )
        column = spec.get("column")
        if file and Path(file).exists():
            try:
                columns, features = _gis_columns(file, Path(file).stat().st_mtime)
            except (OSError, ValueError, pyogrio.errors.DataSourceError) as exc:
                st.error(f"Can't read `{file}`: {exc}")
                return name, {"file": file, "column": column}, remove
            auto = automatic_overlay_column(columns)
            options = [None, *columns]
            column = st.selectbox(
                "Label column",
                options,
                options.index(column) if column in options else 0,
                format_func=lambda c: f"Automatic ({auto or 'row number'})" if c is None else c,
                key=f"ui_ov_{row}_column",
                help="Which of the polygon's attributes goes in the output column.",
            )
            shown = column or auto
            if shown:
                sample = _gis_sample(file, shown, Path(file).stat().st_mtime)
                st.caption(f"{features} polygons. `{shown}` values: {', '.join(sample)}…")
        return name, {"file": file, "column": column}, remove


def _overlays_editor(out: dict[str, Any]) -> None:
    """Overlay cards plus Add; rows have stable ids so removing one keeps the others."""
    if "ui_overlay_rows" not in st.session_state:
        items = list(out["overlays"].items())
        st.session_state["ui_overlay_rows"] = list(range(len(items)))
        st.session_state["ui_overlay_init"] = dict(enumerate(items))
    rows: list[int] = st.session_state["ui_overlay_rows"]
    init: dict[int, tuple[str, dict[str, Any]]] = st.session_state["ui_overlay_init"]

    st.markdown("**Tag routes with polygon layers (overlays)**")
    st.caption(
        "Each overlay adds a column overlay_<name> holding the polygon each route's start "
        "point falls in, e.g. its river basin or county."
    )
    overlays: dict[str, dict[str, Any]] = {}
    for row in list(rows):
        name, spec = init.get(row, ("", {}))
        name, spec, remove = _overlay_card(row, name, spec)
        if remove:
            rows.remove(row)
            st.rerun()
        if not name:
            _input_error("Overlays: every overlay needs a name")
        elif name in overlays:
            _input_error(f"Overlays: the name '{name}' is used twice")
        elif not spec["file"]:
            _input_error(f"Overlays: '{name}' needs a polygon file")
        else:
            overlays[name] = spec if spec["column"] else {"file": spec["file"]}
    if st.button("Add overlay", key="btn_ov_add", icon=":material/add:"):
        new = max(rows, default=-1) + 1
        rows.append(new)
        init[new] = (f"overlay{len(rows)}", {})
        st.rerun()
    out["overlays"] = overlays


# --- pages and tabs ----------------------------------------------------------------------------


def _release_status(p: dict[str, Any]) -> tuple[bool, str]:
    """(fetched, one-line status) for the release named in the draft."""
    src = p["source"]
    fetched = (Path(src["raw_dir"]) / RELEASE_JSON).exists()
    manifest = Path(src["cache_dir"]) / CACHE_MANIFEST
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        title = data.get("release", {}).get("title") or src["release"]
        return fetched, f"{title} · cache built {data.get('built_at')}"
    if fetched:
        return True, f"{src['release']} · fetched, cache builds on the first run"
    return False, f"{src['release']} · not fetched yet"


def _settings_page_body(p: dict[str, Any]) -> None:
    """The profile's [source] section and machine-level lists: set about once a year."""
    src = p["source"]
    st.caption(
        "The BBS release and where it lives on disk. USGS publishes about one release a "
        "year, so these rarely change. They are saved in the open profile's [source] "
        "section, like every other setting."
    )
    st.subheader("BBS Release")
    fetched, status = _release_status(p)
    (st.success if fetched else st.info)(status)
    src["release"] = st.text_input(
        "Release DOI or ScienceBase item id",
        src["release"],
        key="ui_release",
        help="The USGS BBS dataset release, e.g. 10.5066/P144YU3S (the 1966–2025 release).",
    )
    raw_dir = Path(src["raw_dir"])
    if not (raw_dir / RELEASE_JSON).exists():
        st.caption(f"No {RELEASE_JSON} in {raw_dir}: fetch the release to get pick lists.")
        if st.button("Fetch release", key="btn_fetch", type="primary"):
            try:
                with st.spinner("Downloading from ScienceBase..."):
                    info = fetch_release(src["release"], raw_dir)
            except Exception as exc:  # shown and logged, not swallowed
                _show_failure("Fetch release", exc)
            else:
                st.success(f"Fetched {info.title}")
                st.rerun()
    src["rebuild_cache"] = st.checkbox(
        "Rebuild cache on next run",
        src["rebuild_cache"],
        key="ui_rebuild_cache",
        help="Rebuild the cache from the release files, e.g. after replacing them.",
    )

    st.subheader("Data Folders")
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

    st.subheader("Download Settings")
    src["offline"] = st.checkbox(
        "Offline (never download)",
        src["offline"],
        key="ui_offline",
        help="Use only files already on disk; fail instead of reaching ScienceBase.",
    )
    c1, c2, c3 = st.columns(3)
    src["http_timeout_seconds"] = float(
        c1.number_input(
            "HTTP timeout (seconds)",
            min_value=1.0,
            value=float(src["http_timeout_seconds"]),
            key="ui_http_timeout",
        )
    )
    src["http_retries"] = int(
        c2.number_input("HTTP retries", 0, 10, int(src["http_retries"]), key="ui_http_retries")
    )
    src["http_backoff_seconds"] = float(
        c3.number_input(
            "Retry backoff (seconds, doubles each retry)",
            min_value=0.0,
            value=float(src["http_backoff_seconds"]),
            key="ui_http_backoff",
        )
    )

    _projections_manager()

    st.subheader("Logs")
    st.caption(f"Editor errors are written to `{ERROR_LOG}` with the full traceback.")


def _study_area_card(p: dict[str, Any], v: ProfileValues | None) -> None:
    """Where: states, BCRs and a boundary; strata and single routes under More."""
    rt = p["routes"]
    st.subheader("Study Area")
    st.caption("Each filter narrows the previous ones; empty keeps every route.")
    codes: list[int] = []
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
    rt["boundary"] = _gis_file_picker(
        "Boundary polygon file (start point inside)",
        rt["boundary"] or "",
        "ui_boundary",
        "Optional. Keeps only routes whose start point falls inside the polygon(s).",
    )
    with st.expander("More: strata and individual routes", key="ui_more_area"):
        rt["strata"] = _pick(
            "BBS physiographic strata",
            v.strata if v else None,
            rt["strata"],
            "ui_strata",
            (lambda c: v.label("Stratum", c)) if v else str,
            as_int=True,
            help="The BBS's own physiographic regions (older than BCRs). Keep routes in "
            "these strata. Empty = all.",
        )
        route_options = None
        route_info: dict[str, Any] = {}
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


FIFTY_STOP_FIRST_YEAR = 1997
SOURCE_LABELS = {"10_stop": "10-stop (1966+)", "hybrid": "Hybrid", "50_stop": "50-stop (1997+)"}
LAYOUT_LABELS = {
    "route_total": "Route total",
    "ten_stop_bins": "10-stop bins",
    "stops": "All 50 stops",
}
# The radios list these in this order; they must name exactly the profile's choices
assert set(SOURCE_LABELS) == set(OBSERVATION_SOURCES)
assert set(LAYOUT_LABELS) == set(STOP_LAYOUTS)


def _switch_choice(section: dict[str, Any], field: str, key: str, value: str) -> None:
    """Move a choice widget to a still-valid value when an earlier choice removed its option."""
    section[field] = value
    if key in st.session_state:
        st.session_state[key] = value


def _code_toggle(label: str, current: list[int], key: str, help: str) -> list[int]:
    """A 0/1 code filter as a toggle: on = [1] only, off = no filter.

    A profile holding another mix (e.g. [0], only non-standard runs) gets a multiselect
    instead, so opening it never rewrites the filter.
    """
    if sorted(current) not in ([], [1], [0, 1]):
        return st.multiselect(f"{label} (codes)", [0, 1], current, key=f"{key}_codes", help=help)
    on = st.toggle(label, current == [1], key=key, help=help)
    if on:
        return [1]
    return current if sorted(current) == [0, 1] else []  # [0, 1] also means no filter


def _survey_card(p: dict[str, Any]) -> None:
    """When and how counted: years, source, stop layout, run filters; the rest under More."""
    rn, obs = p["runs"], p["observations"]
    st.subheader("Survey Period & Protocol")
    rn["start_year"], rn["end_year"] = st.slider(
        "Years",
        1966,
        2026,
        (rn["start_year"], rn["end_year"]),
        key="ui_years",
        help="Survey years to keep (inclusive).",
    )
    sources, layouts = list(SOURCE_LABELS), list(LAYOUT_LABELS)
    start_year = rn["start_year"]
    if start_year < FIFTY_STOP_FIRST_YEAR:  # 50-stop counts only exist from 1997
        sources.remove("50_stop")
        if obs["source"] == "50_stop":
            _switch_choice(obs, "source", "ui_obs_source", "10_stop")
            st.info(
                f"Source switched to 10-stop: the years start in {start_year}, and "
                f"50-stop data only exist from {FIFTY_STOP_FIRST_YEAR}.",
                icon=":material/sync_alt:",
            )
    obs["source"] = st.radio(
        "Source",
        sources,
        sources.index(obs["source"]),
        format_func=SOURCE_LABELS.get,
        horizontal=True,
        key="ui_obs_source",
        help="10-stop: 10-stop summaries, 1966 on (the widest coverage). Hybrid: both, "
        "harmonised to 10-stop bins. 50-stop: per-stop counts, 1997 on only; offered "
        "when the years start in 1997 or later.",
    )
    if obs["source"] != "50_stop":  # per-stop layout needs per-stop counts
        layouts.remove("stops")
        if obs["stops"] == "stops":
            _switch_choice(obs, "stops", "ui_stops", "ten_stop_bins")
            st.info(
                "Stop layout switched to 10-stop bins: all 50 stops need the 50-stop source.",
                icon=":material/sync_alt:",
            )
    obs["stops"] = st.radio(
        "Stop layout",
        layouts,
        layouts.index(obs["stops"]),
        format_func=LAYOUT_LABELS.get,
        horizontal=True,
        key="ui_stops",
        help="Route total: one count per run. 10-stop bins: five counts per run "
        "(stops 1–10, 11–20, ...). All 50 stops: needs the 50-stop source.",
    )
    rn["run_types"] = _code_toggle(
        "Standard runs only (RunType 1)",
        rn["run_types"],
        "ui_run_types",
        "Keep only runs that met all standard BBS criteria, as most trend analyses do. "
        "Off = every run.",
    )
    rn["quality_current_ids"] = _code_toggle(
        "Passed quality check only (QualityCurrentID 1)",
        rn["quality_current_ids"],
        "ui_quality_ids",
        "Keep only runs that met the weather, time, date, and route-completion criteria. "
        "Off = every run.",
    )
    shown = st.session_state.get("ui_min_continuity", rn["min_continuity"])
    st.caption(f"Routes need a kept run in at least {shown:.0%} of years (change under More).")
    with st.expander(
        "More: continuity, observer first year, zero-fill, rolling mean", key="ui_more_survey"
    ):
        rn["min_continuity"] = st.slider(
            "Minimum continuity (share of non-cancelled years surveyed)",
            0.0,
            1.0,
            float(rn["min_continuity"]),
            0.05,
            key="ui_min_continuity",
            help="Drop routes with a surviving run in less than this share of the "
            "non-cancelled years in range. 0 = keep all; default 0.8 = at least 80%.",
        )
        rn["drop_observer_first_year"] = st.checkbox(
            "Drop each observer's first year on a route",
            rn["drop_observer_first_year"],
            key="ui_drop_first_year",
            help="Counts are lower in an observer's first year on a route (the start-up "
            "effect USGS trend analyses account for). Tick to leave those runs out, or "
            "keep them and join the observer table's is_first_year column to model it.",
        )
        obs["zero_fill"] = st.checkbox(
            "Zero-fill every species ever recorded on the route",
            obs["zero_fill"],
            key="ui_zero_fill",
            help="Add a 0 count for each species seen on the route in any year but not on "
            "this run. Needed for most abundance models.",
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


def _species_card(p: dict[str, Any], v: ProfileValues | None) -> None:
    """Which birds: species and the guild file; names, taxonomy and guild options under More."""
    obs, sp, g = p["observations"], p["species"], p["guilds"]
    st.subheader("Species & Guilds")
    st.caption("Empty keeps every species. Species lists combine with OR; guilds then narrow.")
    names = {s["AOU"]: s["English_Common_Name"] for s in v.species} if v else {}
    sp["aous"] = _pick(
        "Species (AOU)",
        list(names) if v else None,
        sp["aous"],
        "ui_aous",
        lambda a: f"{a} · {names[a]}",
    )
    g["file"] = st.text_input(
        "Guild file (CSV; see docs/guild_file.md)",
        g["file"] or "",
        key="ui_guild_file",
        help="Optional CSV that tags species with traits (habitat, diet, ...). Lets you "
        "filter by trait and adds the traits as output columns.",
    )
    traits = v.guild_traits if (v and g["file"]) else {}
    filters: dict[str, list[str]] = {}
    if g["file"]:
        st.caption("Guild filters: OR within a trait, AND across traits and with species.")
        for trait, values in traits.items():
            if not values:
                st.caption(f"`{trait}`: no values in the file yet")
                continue
            chosen = _pick(trait, values, g["filters"].get(trait, []), f"ui_guild_{trait}")
            if chosen:
                filters[trait] = chosen
    with st.expander(
        "More: common names, families, orders, subspecies, guild options", key="ui_more_species"
    ):
        sp["common_names"] = _pick(
            "Common names",
            sorted(set(names.values())) if v else None,
            sp["common_names"],
            "ui_common_names",
        )
        sp["families"] = _pick("Families", v.families if v else None, sp["families"], "ui_families")
        sp["orders"] = _pick("Orders", v.orders if v else None, sp["orders"], "ui_orders")
        obs["lump_subspecies"] = st.checkbox(
            "Count subspecies and forms under their species",
            obs["lump_subspecies"],
            key="ui_lump",
            help="E.g. fold Dark-eyed Junco forms into Dark-eyed Junco.",
        )
        units = ["report", "error"]
        g["unmatched"] = st.selectbox(
            "Guild rows that match no BBS species",
            units,
            units.index(g["unmatched"]),
            key="ui_guild_unmatched",
        )
        if g["file"]:
            other = {t: vals for t, vals in g["filters"].items() if t not in traits}
            filters.update(
                _key_values(
                    st.text_area(
                        "Other trait filters (trait=value1, value2 per line; for traits "
                        "without a pick list, such as free-text columns)",
                        "\n".join(f"{t}={_joined(vals)}" for t, vals in other.items()),
                        key="ui_guild_other_filters",
                    ),
                    "Other trait filters",
                    split_values=True,
                )
            )
    g["filters"] = filters if g["file"] else {}


JOIN_TABLE_HELP: dict[str, str] = {
    "routes": "Route details: name, start coordinates, stratum, BCR.",
    "weather": "Conditions on each run: temperature, wind, sky, times, RunType, "
    "QualityCurrentID, observer id.",
    "observer": "Observer id (ObsN) and is_first_year: the observer's first year on the route.",
    "vehicle": "Cars and noise counted during each run.",
    "species": "Taxonomy: common and scientific names, family, order.",
    "guilds": "Traits from your guild file.",
}
JOIN_MODES = ("None", "Some", "All")

# label, extension, what it is for
OUTPUT_FORMATS: tuple[tuple[str, str, str], ...] = (
    ("CSV", ".csv", "opens in Excel, R, or any spreadsheet"),
    ("Parquet", ".parquet", "compact and fast, for R or Python"),
    ("GeoPackage", ".gpkg", "GIS layer of route start points (ArcGIS, QGIS)"),
    ("GeoParquet", ".geoparquet", "Parquet with route start points"),
    ("FlatGeobuf", ".fgb", "fast GIS layer of route start points"),
    ("GeoJSON", ".geojson", "web-map layer of route start points"),
)
VECTOR_EXTENSIONS = frozenset({".gpkg", ".geoparquet", ".fgb", ".geojson"})

# Common output coordinate systems; anything else via "Other"
CRS_CHOICES: dict[str, str] = {
    "EPSG:4326": "Latitude/longitude, WGS 84 (GPS and web maps)",
    "EPSG:4269": "Latitude/longitude, NAD83 (what BBS route coordinates use)",
    "EPSG:5070": "Albers equal-area, lower 48 US (meters; for areas and distances)",
    "EPSG:32119": "North Carolina State Plane, NAD83 (meters)",
    "EPSG:3857": "Web Mercator (web tile maps; distorts area)",
}
OTHER_CRS = "Other (enter an EPSG code)"
# Projections added with "Add to my list"; shown in every profile after the built-ins
PROJECTIONS_FILE = Path("projections.toml")


JOIN_TABLE_TITLES: dict[str, str] = {
    "routes": "Routes",
    "weather": "Weather",
    "observer": "Observer",
    "vehicle": "Vehicle",
    "species": "Species",
    "guilds": "Guild Traits",
}


def _join_picker(p: dict[str, Any], table: str, options: list[str] | None) -> None:
    """None / Some / All for one table (stacked radios); "Some" opens a column picker."""
    current = p["joins"][table]
    mode_now = "All" if current == ["*"] else ("Some" if current else "None")
    with st.container(border=True, height="stretch"):
        mode = st.radio(
            JOIN_TABLE_TITLES.get(table, table),
            JOIN_MODES,
            JOIN_MODES.index(mode_now),
            key=f"ui_join_mode_{table}",
            help=JOIN_TABLE_HELP.get(table),
        )
        if mode == "All":
            p["joins"][table] = ["*"]
        elif mode == "Some":
            p["joins"][table] = _pick(
                f"{JOIN_TABLE_TITLES.get(table, table)} columns",
                options,
                [c for c in current if c != "*"],
                f"ui_join_{table}",
            )
        else:
            p["joins"][table] = []


def _output_path_picker(out: dict[str, Any]) -> str:
    """Folder + file name + format list; returns the chosen extension ('' if none)."""
    path = Path(out["path"])
    extensions = [ext for _, ext, _ in OUTPUT_FORMATS]
    suffix = path.suffix.lower()
    if suffix not in extensions:
        st.warning(f"'{path.name}' has no supported format; choose one below.")
    c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
    folder = c1.text_input(
        "Folder", str(path.parent), key="ui_output_folder", help="Created if it doesn't exist."
    )
    stem = c2.text_input(
        "File name",
        path.stem if suffix in extensions else path.name,
        key="ui_output_name",
        help="Without the extension; the format adds it.",
    )
    labels = {ext: f"{name} ({ext}): {use}" for name, ext, use in OUTPUT_FORMATS}
    ext = c3.selectbox(
        "Format",
        extensions,
        extensions.index(suffix) if suffix in extensions else None,
        format_func=labels.get,
        key="ui_output_format",
        help="GIS formats include each route's start point; CSV and Parquet are plain tables.",
    )
    if not stem.strip():
        _input_error("Output: enter a file name")
    if ext is None:
        _input_error("Output: choose a format")
        return ""
    out["path"] = str(Path(folder.strip() or ".") / f"{stem.strip()}{ext}")
    st.caption(f"Writes `{out['path']}`")
    return ext


def _saved_projections() -> dict[str, str]:
    """{code: label} from PROJECTIONS_FILE's [projections] table; {} when there is none."""
    if not PROJECTIONS_FILE.is_file():
        return {}
    try:
        data = tomllib.loads(PROJECTIONS_FILE.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        st.error(f"{PROJECTIONS_FILE} is not valid TOML, so saved projections are not shown: {exc}")
        return {}
    table = data.get("projections", {})
    return {str(code): str(label) for code, label in table.items()}


def _write_projections(saved: dict[str, str]) -> None:
    lines = [
        '# Projections saved in the editor (Settings page, or "Add to my list").',
        "[projections]",
        *(f"{json.dumps(c)} = {json.dumps(text, ensure_ascii=False)}" for c, text in saved.items()),
    ]
    PROJECTIONS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save_projection(code: str, label: str) -> None:
    _write_projections({**_saved_projections(), code: label})


def _epsg_code(raw: str) -> str:
    """'26917', 'epsg:26917' -> 'EPSG:26917'."""
    code = raw.strip().upper()
    return f"EPSG:{code}" if code.isdigit() else code


def _rename_projection(code: str) -> None:
    """on_change for a saved projection's name box: write the new name at once."""
    label = st.session_state[f"ui_proj_name_{code}"].strip()
    saved = _saved_projections()
    if label and code in saved:
        saved[code] = label
        _write_projections(saved)
        st.toast(f"Renamed {code}")


def _projections_manager() -> None:
    """Settings page: list, rename, remove and add the projections offered in every profile."""
    st.subheader("Saved Projections")
    st.caption(
        "Your own projections, offered in the Build page's projection list for every "
        "profile, after the built-in ones. Changes are saved at once (to "
        f"`{PROJECTIONS_FILE}`); no profile Save needed."
    )
    saved = _saved_projections()
    if not saved:
        st.caption("None saved yet.")
    for code, label in saved.items():
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.text_input(
                f"Name for {code}",
                label,
                key=f"ui_proj_name_{code}",
                on_change=_rename_projection,
                args=(code,),
                help="Press Enter to save the new name.",
            )
            if st.button("Remove", key=f"btn_proj_remove_{code}", icon=":material/delete:"):
                _write_projections({c: t for c, t in saved.items() if c != code})
                st.toast(f"Removed {code}")
                st.rerun()
    with st.container(horizontal=True, vertical_alignment="bottom"):
        raw = st.text_input(
            "Add an EPSG code",
            key="ui_proj_new_code",
            placeholder="e.g. 26917",
            help="Look one up at epsg.io, e.g. 26917 (UTM zone 17N).",
        )
        code = _epsg_code(raw) if raw.strip() else ""
        name = _crs_name(code) if code else None
        label = st.text_input(
            "Name in my list", name or "", key=f"ui_proj_new_name_{code}", disabled=not name
        )
        add = st.button(
            "Add", key="btn_proj_add", icon=":material/add:", disabled=not name or code in saved
        )
    if code and name is None:
        st.caption(f"`{raw.strip()}` is not a known EPSG code.")
    elif code in saved:
        st.caption(f"{code} is already in your list.")
    elif code in CRS_CHOICES:
        st.caption(f"{code} is already a built-in choice; adding it gives it your own name.")
    elif name:
        st.caption(f"{code}: {name}")
    if add and name:
        _save_projection(code, label.strip() or name)
        st.toast(f"Added {code}")
        st.session_state.pop("ui_proj_new_code", None)
        st.rerun()


def _crs_name(code: str) -> str | None:
    """The official name of an EPSG code (e.g. 'NAD83 / UTM zone 17N, metre'), or None."""
    try:
        crs = pyproj.CRS.from_user_input(code)
    except pyproj.exceptions.CRSError:
        return None
    unit = crs.axis_info[0].unit_name if crs.axis_info else ""
    return f"{crs.name}, {unit}" if unit else crs.name


def _crs_picker(out: dict[str, Any]) -> None:
    current = out["crs"]
    choices = {**CRS_CHOICES, **_saved_projections()}
    options = [*choices, OTHER_CRS]
    if "pending_crs" in st.session_state:  # just added: select it before the list is drawn
        st.session_state["ui_crs_choice"] = st.session_state.pop("pending_crs")
    choice = st.selectbox(
        "Map coordinate system (projection)",
        options,
        options.index(current) if current in choices else len(options) - 1,
        format_func=lambda c: c if c == OTHER_CRS else f"{choices[c]} · {c}",
        key="ui_crs_choice",
        help="How route locations are stored in the GIS file. Pick latitude/longitude "
        "unless your analysis needs meters (e.g. Albers for areas and distances). "
        'Codes you add with "Add to my list" appear here in every profile.',
    )
    if choice != OTHER_CRS:
        out["crs"] = choice
        return
    out["crs"] = st.text_input(
        "EPSG code",
        current,
        key="ui_crs",
        help="Look one up at epsg.io, e.g. EPSG:26917 (UTM zone 17N).",
    )
    code = out["crs"].strip().upper()
    if code.isdigit():
        code = f"EPSG:{code}"
    name = _crs_name(code)
    if name is None:
        st.caption(f"`{out['crs']}` is not a known EPSG code.")
        return
    st.caption(f"{code}: {name}")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        label = st.text_input(
            "Name in my list",
            name,
            key=f"ui_crs_label_{code}",  # per code, so a new code gets its own name
            help="How it will appear in the list.",
        )
        if st.button("Add to my list", key="btn_add_crs", icon=":material/bookmark_add:"):
            _save_projection(code, label.strip() or name)
            st.toast(f"Added {code} to {PROJECTIONS_FILE}")
            st.session_state["pending_crs"] = code
            st.rerun()


def _output_card(p: dict[str, Any], v: ProfileValues | None) -> None:
    """The file to write, its shape, the joined columns, and GIS options."""
    out = p["output"]
    st.subheader("Output")
    shape_col, file_col = st.columns([1, 3])
    with shape_col:
        shapes = ["long", "wide"]
        out["shape"] = st.radio(
            "Shape",
            shapes,
            shapes.index(out["shape"]),
            format_func={
                "long": "Long (one row per count)",
                "wide": "Wide (a column per year)",
            }.get,
            key="ui_shape",
            help="Long: one row per run and species; suits most statistics. Wide: one row "
            "per route and species, a column per year; suits spreadsheets and maps.",
        )
        if out["shape"] == "wide":
            wide_values = ["route_total", "rolling_mean"]
            out["wide_value"] = st.selectbox(
                "Value in each year column",
                wide_values,
                wide_values.index(out["wide_value"]),
                format_func={
                    "route_total": "That year's count",
                    "rolling_mean": "Rolling mean (N under Survey Period & Protocol)",
                }.get,
                key="ui_wide_value",
            )
    with file_col:
        ext = _output_path_picker(out)
        out["manifest"] = st.checkbox(
            "Write manifest",
            out["manifest"],
            key="ui_manifest",
            help="Write <file>.manifest.json beside the output: the profile, release, code "
            "version, and the counts after each filter, so the file can be traced and "
            "rebuilt.",
        )

    st.markdown("#### Joined Columns")
    st.caption("Extra columns added to every row. Inspect shows the exact columns you'll get.")
    shown: list[tuple[str, list[str] | None]] = []
    for table in JOIN_TABLES:
        options = v.join_columns.get(table, []) if v else None
        if options is not None and not options:
            # Leave the saved value alone (e.g. guilds = ["*"] without a guild file):
            # clearing it marked every such profile "Unsaved changes" as soon as it opened
            st.caption(f"{JOIN_TABLE_TITLES.get(table, table)}: nothing to add in this profile")
            continue
        shown.append((table, options))
    if shown:
        for column, (table, options) in zip(st.columns(len(shown)), shown, strict=True):
            with column:
                _join_picker(p, table, options)

    if ext in VECTOR_EXTENSIONS:
        with st.expander(f"Projection, layer name and overlays · {out['crs']}", key="ui_more_gis"):
            _crs_picker(out)
            if ext == ".gpkg":
                out["layer_name"] = st.text_input(
                    "Layer name",
                    out["layer_name"],
                    key="ui_layer_name",
                    help="The layer's name inside the GeoPackage, as GIS software lists it.",
                )
            _overlays_editor(out)


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
        st.info("Fix the problems shown at the top of the page to inspect or run.")
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
            st.session_state["ran"] = True
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


EXAMPLES_DIR = Path("examples")


def _example_profiles() -> dict[str, str]:
    """{path: description} for example profiles; the description is the first comment line."""
    examples: dict[str, str] = {}
    for path in sorted(EXAMPLES_DIR.glob("*.toml")):
        if not _looks_like_profile(path):
            continue
        first = next(
            (ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("#")),
            "",
        )
        examples[str(path)] = first.lstrip("# ").strip() or path.stem
    return examples


def _example_picker(key: str) -> None:
    """Start a new, unsaved draft from an example (the example file is never overwritten)."""
    examples = _example_profiles()
    if not examples:
        st.caption(f"No example profiles in `{EXAMPLES_DIR}/`.")
        return
    with st.container(horizontal=True, vertical_alignment="bottom"):
        choice = st.selectbox(
            "Start from an example",
            list(examples),
            format_func=lambda e: f"{Path(e).stem}: {examples[e]}",
            key=f"{key}_choice",
            help="Copies the example into a new, unsaved profile. Use Save as to keep it.",
        )
        if st.button("Use example", key=f"btn_{key}", icon=":material/content_copy:"):
            _switch_to(None, load_profile(Path(choice)).model_dump(mode="json"))


def _switch_to(path: Path | None, draft: dict[str, Any]) -> None:
    """Make ``path`` (None = a new, unsaved profile) current and reset every field."""
    for key in [k for k in st.session_state if k.startswith(("ui_", "sb_"))]:
        del st.session_state[key]
    st.session_state.pop("last_result", None)
    st.session_state.pop("ran", None)
    st.session_state["profile_path"] = str(path) if path is not None else None
    st.session_state["draft"] = draft
    st.rerun()


def _profile_menu(path: Path | None, profile: RunProfile | None) -> None:
    """The header's Profile menu: Open, New, examples, Save as, and the resolved TOML."""
    with st.expander("What is a profile?", icon=":material/help:"):
        st.markdown(
            "A profile is one `.toml` file holding every setting for one extraction: "
            "the release, which routes and runs, species, extra columns, and the output "
            "file. Keep one per project, e.g. `examples/wood_thrush_story_map.toml`.\n\n"
            "- The Build and Settings pages edit the open profile.\n"
            "- Your edits are a **draft**. Inspect and Run use the draft right away; "
            "**Save profile** writes it to the file.\n"
            "- The command line runs the same file: `uv run bbs run --config "
            "profile.toml`.\n\n"
            "The old pipeline took these settings as options every time you ran it; a "
            "profile keeps them in a file you can save, reopen, share, and rerun."
        )
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
        _example_picker("sb_example")

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


PAGES: dict[str, Any] = {}  # "build" / "settings" -> st.Page, set in render_editor


def _header(
    path: Path | None, profile: RunProfile | None, saved: RunProfile | None, page: str
) -> None:
    """Title, release status, the open profile, Save, the Profile menu, and page switch."""
    p = _draft()
    with st.container(horizontal=True, vertical_alignment="center"):
        st.title("BBS Dataset Builder", width="stretch")
        if page == "build":
            if st.button("Settings", key="btn_to_settings", icon=":material/settings:"):
                st.switch_page(PAGES["settings"])
        elif st.button("Back to builder", key="btn_to_build", icon=":material/arrow_back:"):
            st.switch_page(PAGES["build"])
    fetched, status = _release_status(p)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge(
            f"Release {status}" if fetched else "No release fetched",
            icon=":material/check:" if fetched else ":material/warning:",
            color="green" if fetched else "orange",
        )
        st.markdown(f"**Profile:** `{path}`" if path else "**Profile:** new, not saved yet")
        if profile is not None:
            dirty = saved is None or profile != saved
            st.badge("Unsaved changes" if dirty else "Saved", color="orange" if dirty else "gray")
            if path is not None and st.button(
                "Save profile", key="btn_save", disabled=not dirty, type="primary"
            ):
                save_profile(profile, path)
                st.toast(f"Saved {path}")
                st.rerun()
        with st.popover("Profile", icon=":material/folder_open:"):
            _profile_menu(path, profile)


def _build_page() -> None:
    p = _draft()
    alerts = st.container()  # filled after the tabs, once the draft has been validated
    if not _release_status(p)[0]:
        with st.container(border=True):
            st.subheader("Fetch the BBS Release")
            st.caption(
                "Pick lists and runs need the USGS release on disk. Fetch it once per "
                "release (about once a year) on the Settings page."
            )
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
            f"Fetch the release or fix the release directory on the Settings page: {values_error}",
            title="Pick lists unavailable, using text boxes",
        )

    area, survey, birds = st.columns(3, border=True)
    with area:
        _study_area_card(p, values)
    with survey:
        _survey_card(p)
    with birds:
        _species_card(p, values)
    with st.container(border=True):
        _output_card(p, values)
    profile = _validated(alerts)
    with st.container(border=True):
        st.subheader("Inspect & Run")
        stamp = _value_source(profile)[1] if profile is not None else ()
        _inspect_run_tab(profile, stamp)


def _settings_page() -> None:
    alerts = st.container()
    _settings_page_body(_draft())
    _validated(alerts)


def _validated(alerts: Any) -> RunProfile | None:
    """The draft as a profile, or None with the reasons shown in ``alerts``."""
    problems = st.session_state["input_errors"]
    if problems:
        alerts.error("\n".join(f"- {m}" for m in problems), title="Fix these entries")
        return None
    try:
        return profile_from_data(_draft(), origin="from the editor")
    except ProfileError as exc:
        alerts.error(str(exc), title="This profile can't be inspected or run yet")
        return None


def render_editor() -> None:
    st.set_page_config(
        page_title="BBS Dataset Builder", page_icon=":material/travel_explore:", layout="wide"
    )
    if "profile_path" not in st.session_state:
        st.session_state["profile_path"] = os.environ.get("BBS_PROFILE", "profile.toml")
    path = Path(st.session_state["profile_path"]) if st.session_state["profile_path"] else None
    saved = load_profile(path) if path is not None and path.exists() else None
    if "draft" not in st.session_state:
        st.session_state["draft"] = (saved or RunProfile()).model_dump(mode="json")
    st.session_state["input_errors"] = []

    PAGES["build"] = st.Page(_build_page, title="Build", url_path="build", default=True)
    PAGES["settings"] = st.Page(_settings_page, title="Settings", url_path="settings")
    page = st.navigation(list(PAGES.values()), position="hidden")
    header = st.container()  # filled after the page, once the draft has been validated
    page.run()
    profile: RunProfile | None = None
    if not st.session_state["input_errors"]:
        try:
            profile = profile_from_data(_draft(), origin="from the editor")
        except ProfileError:
            profile = None  # the page already shows why
    with header:
        _header(path, profile, saved, "build" if page.url_path in ("", "build") else "settings")


try:
    render_editor()
except Exception as exc:  # st.rerun/st.stop are BaseException, so they pass through
    _log_failure("Unexpected error", exc)
    raise  # Streamlit still shows it on the page
