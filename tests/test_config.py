"""Tests for the TOML run profile: loading, validation messages, overrides, round trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from bbs_pipeline.config import (
    ProfileError,
    RunProfile,
    dump_profile,
    load_profile,
    parse_override,
    profile_from_data,
)
from bbs_pipeline.contracts import StopAggregationMode, TemporalRoutingMode

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "profile_template.toml"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "p.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_template_is_valid_and_documents_the_defaults() -> None:
    assert load_profile(TEMPLATE) == RunProfile()


def test_empty_profile_uses_defaults(tmp_path: Path) -> None:
    assert load_profile(_write(tmp_path, "")) == RunProfile()


def test_unknown_and_bad_keys_are_named(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[runs]
start_yeer = 2000
min_continuity = 1.5
[output]
path = "out/x.xlsx"
[colour]
x = 1
""",
    )

    with pytest.raises(ProfileError) as excinfo:
        load_profile(path)
    message = str(excinfo.value)
    assert "runs.start_yeer: unknown key" in message
    assert "runs.min_continuity:" in message
    assert "output.path:" in message and ".xlsx" in message
    assert "colour: unknown key" in message


def test_year_range_error_names_the_section(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match=r"runs: .*start_year <= end_year.*2010-2000"):
        load_profile(_write(tmp_path, "[runs]\nstart_year = 2010\nend_year = 2000\n"))


def test_invalid_toml_and_missing_file_raise_profile_errors(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="not valid TOML"):
        load_profile(_write(tmp_path, "[runs\n"))
    with pytest.raises(ProfileError, match="not found"):
        load_profile(tmp_path / "missing.toml")


def test_overrides_parse_toml_values_and_bare_strings() -> None:
    assert parse_override("runs.start_year=2000") == (["runs", "start_year"], 2000)
    assert parse_override('routes.states=["NC", 88]') == (["routes", "states"], ["NC", 88])
    assert parse_override("source.offline=true") == (["source", "offline"], True)
    assert parse_override("output.crs=EPSG:5070") == (["output", "crs"], "EPSG:5070")
    with pytest.raises(ProfileError, match="dotted.key=value"):
        parse_override("runs.start_year")


def test_overrides_apply_on_top_of_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "[runs]\nstart_year = 1990\n")

    profile = load_profile(path, ["runs.start_year=2001", "output.crs=5070"])

    assert profile.runs.start_year == 2001
    assert profile.output.crs == "EPSG:5070"


def test_values_are_normalized() -> None:
    profile = profile_from_data(
        {
            "routes": {"states": ["63", "NC"], "route_ids": ["63_1", "6_12"], "boundary": ""},
            "species": {"aous": [7550, "6740"]},
        }
    )

    assert profile.routes.states == [63, "NC"]
    assert profile.routes.route_ids == ["63_001", "06_012"]
    assert profile.routes.boundary is None
    assert profile.species.aous == ["07550", "06740"]


def test_bad_route_id_and_aou_are_rejected() -> None:
    with pytest.raises(ProfileError, match=r"routes.route_ids: .*'63-001'"):
        profile_from_data({"routes": {"route_ids": ["63-001"]}})
    with pytest.raises(ProfileError, match=r"species.aous: .*'Wood Thrush'"):
        profile_from_data({"species": {"aous": ["Wood Thrush"]}})


def test_per_stop_output_needs_the_50_stop_source() -> None:
    with pytest.raises(ProfileError, match="only observations.source = '50_stop' has"):
        profile_from_data({"observations": {"source": "hybrid", "stops": "stops"}})


def test_profile_maps_to_internal_settings() -> None:
    bundle = profile_from_data(
        {
            "source": {"release": "10.5066/X", "rebuild_cache": True},
            "routes": {"states": ["NC"], "boundary": "b.gpkg"},
            "runs": {"start_year": 2000, "end_year": 2010, "min_continuity": 0.5},
            "observations": {"source": "hybrid", "stops": "ten_stop_bins", "zero_fill": False},
            "species": {"families": ["Parulidae"]},
            "output": {"path": "o.gpkg", "crs": "5070"},
        }
    ).to_bundle()

    p = bundle.protocol
    assert p.sciencebase_doi == "10.5066/X"
    assert p.force_resanitize is True
    assert p.states == ["NC"]
    assert p.boundary_path == Path("b.gpkg")
    assert p.year_range == (2000, 2010)
    assert p.min_route_continuity_ratio == 0.5
    assert p.temporal_routing_mode == TemporalRoutingMode.HYBRID_HARMONIZED_10_STOP
    assert p.stop_aggregation == StopAggregationMode.TEN_STOP_BINS
    assert p.enable_extirpation_zero_fill is False
    assert bundle.species.families == ["Parulidae"]
    assert bundle.output.is_vector and bundle.output.crs == "EPSG:5070"


def test_dump_round_trips(tmp_path: Path) -> None:
    profile = profile_from_data(
        {
            "routes": {"states": [63, "Nova Scotia"], "boundary": "a b/ç.gpkg"},
            "species": {"common_names": ['Bird "quoted"']},
            "output": {
                "path": "o.gpkg",
                "overlays": {"basin": "x.gpkg", "county": {"file": "c.gpkg", "column": "NAME"}},
            },
        }
    )

    path = _write(tmp_path, dump_profile(profile))

    assert load_profile(path) == profile


@pytest.mark.parametrize(
    "runs",
    [
        {"observer_tenure": "drop_first_year_on_route"},
        {"observer_min_years": 2},
        {"veteran_min_years": 5},
    ],
)
def test_old_tenure_keys_explain_the_replacement(runs: dict) -> None:
    with pytest.raises(ProfileError, match="replaced by drop_observer_first_year"):
        profile_from_data({"runs": runs})


def test_drop_observer_first_year_reaches_the_protocol() -> None:
    profile = profile_from_data({"runs": {"drop_observer_first_year": True}})

    assert profile.to_bundle().protocol.drop_observer_first_year is True


def test_overlays_accept_a_path_or_file_and_column() -> None:
    out = profile_from_data(
        {"output": {"overlays": {"a": "x.gpkg", "b": {"file": "y.gpkg", "column": "BASIN"}}}}
    ).output

    assert (out.overlays["a"].file, out.overlays["a"].column) == (Path("x.gpkg"), None)
    assert (out.overlays["b"].file, out.overlays["b"].column) == (Path("y.gpkg"), "BASIN")
    with pytest.raises(ProfileError, match="colum"):
        profile_from_data({"output": {"overlays": {"a": {"file": "x.gpkg", "colum": "N"}}}})
