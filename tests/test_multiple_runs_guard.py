"""Decision D3: more than one surviving run in a route-year stops the build."""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.stages.runs import MultipleRunsError, _assert_one_run_per_route_year


def test_two_runs_in_a_route_year_raise_with_count_and_fix() -> None:
    runs = pl.LazyFrame(
        {"RouteDataID": [1, 2, 3], "route_id": ["02_141"] * 3, "Year": [2010, 2010, 2011]}
    )
    with pytest.raises(MultipleRunsError, match=r"run_types = \[1\]") as info:
        _assert_one_run_per_route_year(runs)
    assert info.value.count == 1
    assert "02_141 2010: [1, 2]" in info.value.examples
    assert isinstance(info.value, ValueError)  # CLI callers catching ValueError still do


def test_one_run_per_route_year_passes() -> None:
    runs = pl.LazyFrame({"RouteDataID": [1, 2], "route_id": ["a", "a"], "Year": [2010, 2011]})
    _assert_one_run_per_route_year(runs)
