"""H13 memory cuts must not change the output: same rows, same order, same values."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

from bbs_pipeline.config import profile_from_data
from bbs_pipeline.engine import ecology
from bbs_pipeline.pipeline import run_profile

RELEASE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real" / "release"


def _run(tmp_path: Path, route_ids: list[str] | None = None) -> pl.DataFrame:
    profile = profile_from_data(
        {
            "source": {
                "release": "10.5066/P144YU3S",
                "raw_dir": str(RELEASE),
                "cache_dir": str(tmp_path / "cache"),
                "offline": True,
            },
            "routes": {"route_ids": route_ids or []},
            "runs": {"start_year": 2015, "end_year": 2024},
            "observations": {"source": "50_stop", "zero_fill": True},
            "joins": {t: ["*"] for t in ("routes", "weather", "observer", "vehicle", "species")},
            "output": {"path": str(tmp_path / "o.parquet")},
        }
    )
    run_profile(profile)
    return pl.read_parquet(tmp_path / "o.parquet")


def test_output_is_sorted_without_a_final_sort(tmp_path: Path) -> None:
    out = _run(tmp_path)
    assert out.height > 0
    assert_frame_equal(out, out.sort(["route_id", "Year", "AOU"], maintain_order=True))


def test_route_prefilter_matches_the_unfiltered_tables(tmp_path: Path, monkeypatch) -> None:
    filtered = _run(tmp_path / "a", route_ids=["63_024", "63_101"])
    monkeypatch.setattr(ecology, "_observations_for_routes", lambda tables, _runs: tables)
    unfiltered = _run(tmp_path / "b", route_ids=["63_024", "63_101"])
    assert filtered.height > 0
    assert_frame_equal(filtered, unfiltered)
