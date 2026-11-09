"""Deterministic checks that pipeline outputs match the raw BBS release and each other.

    uv run python scripts/validate_outputs.py                    # NC, 1990-2024
    uv run python scripts/validate_outputs.py --states NC VA --start 1985 --end 2024

Runs the pipeline several ways on one scope (states x years), then checks:

  V1  raw spot-check   every 50-stop route total equals the sum of Stop1..Stop50 for that
                       run and species in the raw 50-StopData.zip, and every raw record of a
                       kept run is in the output (none dropped)
  V2  stop layouts     route_total = Count10+..+Count50 = Stop1+..+Stop50, same rows
  V3  1997 seam        10-stop and 50-stop route totals agree for every run from 1997 on;
                       hybrid equals 10-stop before 1997 and 50-stop after; the raw States.zip
                       SpeciesTotal equals the raw 50-stop sum for the same runs
  V4  zero-fill        zero rows are exactly the zero-filled rows; every run of a route lists
                       the same species; zeros only in runs that recorded something
  V5  long vs wide     every wide year cell equals the long route_total; empty where no run
  V6  CLI vs API       `bbs run` and run_profile() write byte-identical CSVs; a rerun too
  V7  formats          CSV, Parquet and GeoPackage hold the same rows and values; GeoPackage
                       points sit at the route's Longitude/Latitude (EPSG:4326)

The raw files are read here with plain polars from data/raw (the zips and CSVs), never
through the pipeline's loader or cache, so a loader bug cannot hide itself. Exit code 0
means every check passed. Results go in docs/VERIFICATION.md by hand, with the date and
the command used.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import polars as pl

from bbs_pipeline.config import profile_from_data, save_profile
from bbs_pipeline.pipeline import run_profile

RAW = Path("data/raw")
KEY = ["RouteDataID", "AOU"]
STOPS = [f"Stop{i}" for i in range(1, 51)]
BINS = ["Count10", "Count20", "Count30", "Count40", "Count50"]
FIRST_50_STOP_YEAR = 1997


# --- raw release, read independently ---------------------------------------------------


def _int(column: str) -> pl.Expr:
    """Raw counts are text padded with spaces ("0     "); strip, then read as integers."""
    return pl.col(column).str.strip_chars().cast(pl.Int64)


def _read_zip_csvs(path: Path, keep_states: set[int]) -> pl.DataFrame:
    frames = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            df = pl.read_csv(io.BytesIO(zf.read(name)), infer_schema_length=0)
            df = df.with_columns(_int("StateNum"))
            frames.append(df.filter(pl.col("StateNum").is_in(keep_states)))
    return pl.concat(frames, how="diagonal_relaxed")


def raw_fifty(states: set[int]) -> pl.DataFrame:
    """RouteDataID, AOU, Year, raw_total (Stop1..Stop50 summed) from 50-StopData.zip."""
    df = _read_zip_csvs(RAW / "50-StopData.zip", states)
    return df.select(
        _int("RouteDataID"),
        _int("AOU"),
        _int("Year"),
        pl.sum_horizontal([_int(c) for c in STOPS]).alias("raw_total"),
    )


def raw_ten(states: set[int]) -> pl.DataFrame:
    """RouteDataID, AOU, Year, raw_total (SpeciesTotal) from States.zip.

    SpeciesTotal is the birds counted; StopTotal is the number of stops (of 50) where the
    species was detected, not a count.
    """
    df = _read_zip_csvs(RAW / "States.zip", states)
    return df.select(
        _int("RouteDataID"),
        _int("AOU"),
        _int("Year"),
        _int("SpeciesTotal").alias("raw_total"),
    )


# --- pipeline runs ---------------------------------------------------------------------


class Runner:
    def __init__(self, workdir: Path, states: list[str], start: int, end: int) -> None:
        self.workdir, self.states, self.start, self.end = workdir, states, start, end

    def data(self, name: str, suffix: str = ".csv", **sections: dict) -> dict:
        start = sections.pop("start", self.start)
        data: dict = {
            "source": {"offline": True},
            "routes": {"states": self.states},
            "runs": {"start_year": start, "end_year": self.end},
            "observations": {"lump_subspecies": False},
            "output": {"path": str(self.workdir / f"{name}{suffix}"), "manifest": False},
        }
        for key, value in sections.items():
            data[key] = {**data.get(key, {}), **value}
        return data

    def run(self, name: str, suffix: str = ".csv", **sections: dict) -> Path:
        data = self.data(name, suffix, **sections)
        run_profile(profile_from_data(data))
        return Path(data["output"]["path"])


def read_csv(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, infer_schema_length=0)
    ints = ["RouteDataID", "AOU", "Year", "route_total", "StateNum", *BINS, *STOPS]
    casts = [pl.col(c).cast(pl.Int64) for c in ints if c in df.columns]
    if "is_zero_filled" in df.columns:
        casts.append(pl.col("is_zero_filled").str.to_lowercase().eq("true"))
    return df.with_columns(casts)


# --- checks ----------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, code: str, title: str, problems: list[str], detail: str) -> None:
        mark = "PASS" if not problems else "FAIL"
        print(f"[{mark}] {code} {title}: {detail}")
        for problem in problems[:10]:
            print(f"       - {problem}")
        if problems:
            self.failed.append(code)


def _diff(label: str, frame: pl.DataFrame) -> list[str]:
    return [f"{label}: {row}" for row in frame.head(10).iter_rows(named=True)]


def v1_raw(r: Runner, rep: Report, fifty_raw: pl.DataFrame) -> pl.DataFrame:
    start = max(r.start, FIRST_50_STOP_YEAR)
    out = read_csv(r.run("v1_fifty", observations={"source": "50_stop"}, start=start))
    seen = out.filter(~pl.col("is_zero_filled"))
    joined = seen.join(fifty_raw, on=KEY, how="left")
    wrong = joined.filter(
        pl.col("raw_total").is_null() | (pl.col("raw_total") != pl.col("route_total"))
    )
    kept_runs = out.select("RouteDataID").unique()
    raw_kept = fifty_raw.join(kept_runs, on="RouteDataID").filter(pl.col("raw_total") > 0)
    missing = raw_kept.join(seen, on=KEY, how="anti")
    problems = _diff("total differs from raw", wrong) + _diff("raw record missing", missing)
    rep.check(
        "V1",
        "raw spot-check",
        problems,
        f"{seen.height} observed rows vs raw 50-stop sums; {raw_kept.height} raw records of "
        f"{kept_runs.height} kept runs all present",
    )
    return out


def v2_layouts(r: Runner, rep: Report) -> None:
    start = max(r.start, FIRST_50_STOP_YEAR)
    obs = {"source": "50_stop"}
    total = read_csv(r.run("v2_total", observations={**obs, "stops": "route_total"}, start=start))
    bins = read_csv(r.run("v2_bins", observations={**obs, "stops": "ten_stop_bins"}, start=start))
    stops = read_csv(r.run("v2_stops", observations={**obs, "stops": "stops"}, start=start))
    t = total.select(*KEY, "route_total")
    b = bins.select(*KEY, pl.sum_horizontal(BINS).alias("bins_sum"), "route_total")
    s = stops.select(*KEY, pl.sum_horizontal(STOPS).alias("stops_sum"))
    j = t.join(b.drop("route_total"), on=KEY, how="full", coalesce=True).join(
        s, on=KEY, how="full", coalesce=True
    )
    bad = j.filter(
        (pl.col("route_total") != pl.col("bins_sum"))
        | (pl.col("route_total") != pl.col("stops_sum"))
        | pl.any_horizontal(pl.col("route_total", "bins_sum", "stops_sum").is_null())
    )
    inner = b.filter(pl.col("bins_sum") != pl.col("route_total"))
    rep.check(
        "V2",
        "stop layouts agree",
        _diff("layouts disagree", bad) + _diff("bins != own route_total", inner),
        f"{j.height} run x species rows identical across route_total, 10-stop bins, 50 stops",
    )


def v3_seam(r: Runner, rep: Report, fifty_raw: pl.DataFrame, ten_raw: pl.DataFrame) -> None:
    # Continuity is measured over each run's own year range, and the 50-stop run starts
    # later, so it would keep different routes; 0 makes the three runs comparable.
    runs = {"min_continuity": 0.0}
    ten = read_csv(r.run("v3_ten", observations={"source": "10_stop"}, runs=runs))
    hyb = read_csv(r.run("v3_hybrid", observations={"source": "hybrid"}, runs=runs))
    start = max(r.start, FIRST_50_STOP_YEAR)
    fifty = read_csv(r.run("v3_fifty", observations={"source": "50_stop"}, runs=runs, start=start))
    cols = [*KEY, "Year", "route_total"]
    ten_late = ten.filter(pl.col("Year") >= FIRST_50_STOP_YEAR).select(cols)
    vs = ten_late.join(fifty.select(cols), on=[*KEY, "Year"], how="full", coalesce=True)
    bad_sources = vs.filter(
        pl.col("route_total").is_null()
        | pl.col("route_total_right").is_null()
        | (pl.col("route_total") != pl.col("route_total_right"))
    )
    early = hyb.filter(pl.col("Year") < FIRST_50_STOP_YEAR).select(cols)
    late = hyb.filter(pl.col("Year") >= FIRST_50_STOP_YEAR).select(cols)
    ten_early = ten.filter(pl.col("Year") < FIRST_50_STOP_YEAR).select(cols)
    bad_early = early.join(ten_early, on=[*KEY, "Year"], how="full", coalesce=True).filter(
        pl.col("route_total").ne_missing(pl.col("route_total_right"))
    )
    bad_late = late.join(fifty.select(cols), on=[*KEY, "Year"], how="full", coalesce=True).filter(
        pl.col("route_total").ne_missing(pl.col("route_total_right"))
    )
    late_years = pl.col("Year") >= FIRST_50_STOP_YEAR  # the 50-stop file has a few older runs
    raw = ten_raw.filter(late_years).join(
        fifty_raw.filter(late_years), on=KEY, how="full", coalesce=True
    )
    bad_raw = raw.filter(pl.col("raw_total").ne_missing(pl.col("raw_total_right")))
    rep.check(
        "V3",
        "1997 seam",
        _diff("10-stop vs 50-stop (pipeline)", bad_sources)
        + _diff("hybrid pre-1997 vs 10-stop", bad_early)
        + _diff("hybrid 1997+ vs 50-stop", bad_late)
        + _diff("raw States.zip vs raw 50-stop", bad_raw),
        f"{vs.height} rows 1997+ agree between sources; hybrid matches 10-stop on "
        f"{early.height} pre-1997 rows and 50-stop on {late.height}; raw files agree on "
        f"{raw.height} records",
    )


def v4_zero_fill(rep: Report, out: pl.DataFrame) -> None:
    zeros = out.filter(pl.col("route_total") == 0)
    not_flagged = zeros.filter(~pl.col("is_zero_filled"))
    flagged_nonzero = out.filter(pl.col("is_zero_filled") & (pl.col("route_total") != 0))
    per_run = out.group_by("route_id", "RouteDataID").agg(pl.col("AOU").sort().alias("aous"))
    uneven = per_run.group_by("route_id").agg(pl.col("aous").n_unique().alias("species_sets"))
    uneven = uneven.filter(pl.col("species_sets") > 1)
    observed_runs = out.filter(~pl.col("is_zero_filled")).select("RouteDataID").unique()
    orphan = zeros.join(observed_runs, on="RouteDataID", how="anti")
    rep.check(
        "V4",
        "zero-fill",
        _diff("zero not flagged", not_flagged)
        + _diff("flagged but not zero", flagged_nonzero)
        + _diff("runs of one route list different species", uneven)
        + _diff("zero in a run with no records", orphan),
        f"{zeros.height} zero rows, all flagged; {per_run.height} runs on "
        f"{out['route_id'].n_unique()} routes each list their route's full species set",
    )


def v5_long_wide(r: Runner, rep: Report) -> None:
    start = max(r.start, FIRST_50_STOP_YEAR)
    obs = {"source": "50_stop"}
    long = read_csv(r.run("v5_long", observations=obs, start=start))
    wide = pl.read_csv(
        r.run("v5_wide", observations=obs, output={"shape": "wide"}, start=start),
        infer_schema_length=0,
    )
    year_cols = [c for c in wide.columns if c.startswith("route_total_")]
    melted = (
        wide.unpivot(index=["route_id", "AOU"], on=year_cols, variable_name="col", value_name="v")
        .with_columns(
            pl.col("col").str.replace("route_total_", "").cast(pl.Int64).alias("Year"),
            pl.col("AOU").cast(pl.Int64),
            pl.col("v").cast(pl.Float64),
        )
        .drop("col")
    )
    long_cells = long.group_by("route_id", "AOU", "Year").agg(
        pl.col("route_total").sum().cast(pl.Float64).alias("lv")
    )
    j = melted.join(long_cells, on=["route_id", "AOU", "Year"], how="left")
    bad = j.filter(pl.col("v").ne_missing(pl.col("lv")))
    lost = long_cells.join(melted.drop_nulls("v"), on=["route_id", "AOU", "Year"], how="anti")
    rep.check(
        "V5",
        "long vs wide",
        _diff("wide cell differs from long", bad) + _diff("long row missing in wide", lost),
        f"{melted.drop_nulls('v').height} filled wide cells equal the long totals; "
        f"{melted['v'].null_count()} empty cells have no long row",
    )


def v6_cli(r: Runner, rep: Report) -> None:
    obs = {"observations": {"source": "50_stop"}, "start": max(r.start, FIRST_50_STOP_YEAR)}
    api_path = r.run("v6_api", **obs)
    again = r.run("v6_again", **obs)
    data = r.data("v6_cli", **obs)
    toml = r.workdir / "v6_cli.toml"
    save_profile(profile_from_data(data), toml)
    proc = subprocess.run(
        ["uv", "run", "bbs", "run", "--config", str(toml)], capture_output=True, text=True
    )
    cli_path = Path(data["output"]["path"])
    problems = []
    if proc.returncode != 0:
        problems.append(f"bbs run failed: {proc.stderr.strip()[-300:]}")
    elif cli_path.read_bytes() != api_path.read_bytes():
        problems.append("CLI and API files differ")
    if again.read_bytes() != api_path.read_bytes():
        problems.append("a rerun of the same profile wrote a different file")
    rep.check(
        "V6",
        "CLI vs API",
        problems,
        f"`bbs run` and run_profile() wrote identical {api_path.stat().st_size:,}-byte CSVs; "
        "a rerun matched too",
    )


def v7_formats(r: Runner, rep: Report) -> None:
    import geopandas as gpd

    obs = {"observations": {"source": "50_stop"}, "start": max(r.start, FIRST_50_STOP_YEAR)}
    csv = pl.read_csv(r.run("v7", ".csv", **obs), infer_schema_length=0)
    parquet = pl.read_parquet(r.run("v7", ".parquet", **obs))
    gpkg_path = r.run("v7", ".gpkg", **obs)
    gdf = gpd.read_file(gpkg_path)
    gpkg = pl.from_pandas(gdf.drop(columns="geometry"))

    def as_text(df: pl.DataFrame) -> pl.DataFrame:
        """Every value as text, numbers normalised (1 == 1.0), so formats compare fairly."""
        out = []
        for c in df.columns:
            col = pl.col(c).cast(pl.String)
            as_num = col.cast(pl.Float64, strict=False)
            out.append(
                pl.when(as_num.is_not_null())
                .then(as_num.round(6).cast(pl.String))
                .otherwise(col)
                .str.to_lowercase()
                .alias(c)
            )
        return df.select(out)

    def same(name: str, other: pl.DataFrame) -> list[str]:
        missing = [c for c in csv.columns if c not in other.columns]
        cols = [c for c in csv.columns if c in other.columns]
        problems = [f"{name} lacks columns {missing}"] if missing else []
        a, b = as_text(csv.select(cols)), as_text(other.select(cols))
        if a.height != b.height:
            problems.append(f"{name}: {b.height} rows vs CSV {a.height}")
        else:
            problems += _diff(
                f"{name} row not in CSV", b.join(a, on=cols, how="anti", nulls_equal=True)
            )
        return problems

    xy = gdf.geometry.get_coordinates()
    off = (
        (xy["x"] - gdf["Longitude"].astype(float)).abs().max(),
        (xy["y"] - gdf["Latitude"].astype(float)).abs().max(),
    )
    problems = same("Parquet", parquet) + same("GeoPackage", gpkg)
    if str(gdf.crs).upper() != "EPSG:4326":
        problems.append(f"GeoPackage CRS is {gdf.crs}, expected EPSG:4326")
    if max(off) > 1e-9:
        problems.append(f"points are off Longitude/Latitude by up to {max(off)}")
    rep.check(
        "V7",
        "formats agree",
        problems,
        f"{csv.height} rows x {len(csv.columns)} columns identical in CSV, Parquet and "
        f"GeoPackage; points at Longitude/Latitude in {gdf.crs}",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--states", nargs="+", default=["NC"], help="postal codes, e.g. NC VA")
    ap.add_argument(
        "--start", type=int, default=1990, help="first year (before 1997 tests the seam)"
    )
    ap.add_argument("--end", type=int, default=2024)
    ap.add_argument("--keep", type=Path, help="write the outputs here instead of a temp folder")
    args = ap.parse_args(argv)
    if not (RAW / "release.json").exists():
        print(f"No release in {RAW}; fetch it first (Settings page, or `bbs fetch`).")
        return 2

    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = args.keep or Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        r = Runner(workdir, args.states, args.start, args.end)
        print(f"Scope: {' '.join(args.states)}, {args.start}-{args.end}; outputs in {workdir}")
        # StateNum codes from a pipeline run's own output, so the raw read covers the same scope
        probe = read_csv(r.run("probe", observations={"source": "10_stop"}))
        state_nums = set(probe["StateNum"].unique().to_list())
        fifty_raw, ten_raw = raw_fifty(state_nums), raw_ten(state_nums)
        rep = Report()
        steps: list[Callable[[], object]] = [
            lambda: v4_zero_fill(rep, v1_raw(r, rep, fifty_raw)),
            lambda: v2_layouts(r, rep),
            lambda: v3_seam(r, rep, fifty_raw, ten_raw),
            lambda: v5_long_wide(r, rep),
            lambda: v6_cli(r, rep),
            lambda: v7_formats(r, rep),
        ]
        for step in steps:
            step()
    took = time.time() - started
    if rep.failed:
        print(f"\n{len(rep.failed)} check(s) FAILED: {', '.join(rep.failed)} ({took:.0f} s)")
        return 1
    print(f"\nAll checks passed ({took:.0f} s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
