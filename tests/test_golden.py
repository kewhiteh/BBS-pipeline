"""Golden tests on a real slice of the 2026 release (12 NC routes, all years).

The fixture (tests/fixtures/real/release) is cut from the real release by
scripts/build_golden_fixture.py, byte-for-byte. Each profile's output must match the
committed expected CSV exactly. After an intended change in results, regenerate with:

    BBS_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py

and review the diff of tests/fixtures/real/expected/ before committing.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.config import load_profile
from bbs_pipeline.pipeline import run_profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "real"
RELEASE = FIXTURE / "release"
GUILDS = ROOT / "guilds" / "pwrc_species_groups.csv"
UPDATE = os.environ.get("BBS_UPDATE_GOLDEN") == "1"


def _run(name: str, tmp_path: Path) -> Path:
    out = tmp_path / f"{name}.csv"
    profile = load_profile(
        FIXTURE / f"{name}.toml",
        [
            f'source.raw_dir="{RELEASE}"',
            f'source.cache_dir="{tmp_path / "cache"}"',
            "source.offline=true",
            f'guilds.file="{GUILDS}"',
            f'output.path="{out}"',
        ],
    )
    run_profile(profile)
    return out


@pytest.mark.parametrize("name", ["golden_50stop_long", "golden_10stop_wide"])
def test_output_matches_golden_file(name: str, tmp_path: Path) -> None:
    produced = _run(name, tmp_path).read_text(encoding="utf-8")
    expected_path = FIXTURE / "expected" / f"{name}.csv"
    if UPDATE:
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(produced, encoding="utf-8")
    expected = expected_path.read_text(encoding="utf-8")

    assert produced == expected, f"{name} differs from {expected_path}"


def _raw_weather() -> pl.DataFrame:
    rows = list(csv.DictReader(io.StringIO(RELEASE.joinpath("Weather.csv").read_text("cp1252"))))
    return pl.DataFrame(rows).with_columns(pl.all().str.strip_chars())


def test_golden_long_output_respects_the_filters(tmp_path: Path) -> None:
    out = pl.read_csv(_run("golden_50stop_long", tmp_path), schema_overrides={"AOU": pl.String})
    weather = _raw_weather().with_columns(pl.col("RouteDataID").cast(pl.Int64))
    runs = out.select("RouteDataID", "route_id", "Year", "route_continuity_ratio").unique()
    checked = runs.join(weather, on="RouteDataID", how="left")

    # Every kept run is RunType 1 / QualityCurrentID 1, in range, on a continuous route
    assert set(checked["RunType"].to_list()) == {"1"}
    assert set(checked["QualityCurrentID"].to_list()) == {"1"}
    assert checked["Year"].is_between(2015, 2024).all()
    assert (checked["route_continuity_ratio"] >= 0.8).all()
    # One run per route-year, and some runs in the window were really filtered out
    assert runs.select("route_id", "Year").is_unique().all()
    in_window = weather.filter(pl.col("Year").cast(pl.Int64).is_between(2015, 2024))
    assert runs.height < in_window.height


def test_golden_counts_match_the_raw_fifty_stop_file(tmp_path: Path) -> None:
    out = pl.read_csv(_run("golden_50stop_long", tmp_path), schema_overrides={"AOU": pl.String})
    with zipfile.ZipFile(RELEASE / "50-StopData.zip") as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        raw = pl.read_csv(io.BytesIO(zf.read(name)), infer_schema=False)
    raw = raw.with_columns(pl.all().str.strip_chars()).with_columns(
        pl.col("RouteDataID").cast(pl.Int64),
        pl.sum_horizontal([pl.col(f"Stop{i}").cast(pl.Int64) for i in range(1, 51)]).alias("raw"),
    )
    woth = out.filter(pl.col("AOU") == "07550").join(
        raw.filter(pl.col("AOU") == "07550").select("RouteDataID", "raw"),
        on="RouteDataID",
        how="left",
    )

    assert woth.height > 0
    detected = woth.filter(~pl.col("is_zero_filled"))
    assert (detected["route_total"] == detected["raw"]).all()
    assert woth.filter(pl.col("is_zero_filled"))["raw"].is_null().all()
