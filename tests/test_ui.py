"""The Streamlit profile editor, driven headlessly against the real-data fixture."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bbs_pipeline.config import RunProfile, load_profile, profile_from_data, save_profile
from bbs_pipeline.values import collect_values

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "tests" / "fixtures" / "real" / "release"
GUILDS = ROOT / "guilds" / "pwrc_species_groups.csv"
APP = ROOT / "ui" / "app.py"


def _profile(tmp_path: Path, **sections: dict) -> RunProfile:
    data = {
        "source": {
            "release": "10.5066/P144YU3S",
            "raw_dir": str(RELEASE),
            "cache_dir": str(tmp_path / "cache"),
            "offline": True,
        },
        "runs": {"start_year": 2015, "end_year": 2024},
        "output": {"path": str(tmp_path / "out" / "o.csv")},
    }
    for key, value in sections.items():
        data[key] = {**data.get(key, {}), **value}
    return profile_from_data(data)


def _app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: RunProfile
) -> tuple[AppTest, Path]:
    path = tmp_path / "project.toml"
    save_profile(profile, path)
    monkeypatch.setenv("BBS_PROFILE", str(path))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at, path


def test_collect_values_reads_the_release(tmp_path: Path) -> None:
    values = collect_values(_profile(tmp_path, guilds={"file": str(GUILDS)}))

    nc = next(s for s in values.states if s["StateNum"] == 63)
    assert (nc["abbreviation"], nc["StateName"]) == ("NC", "North Carolina")
    assert values.bcrs == [27, 28, 29]
    assert len(values.routes) == 12
    assert "Turdidae" in values.families
    assert "CarTotal" in values.join_columns["vehicle"]
    assert "is_first_year" in values.join_columns["observer"]
    assert "woodland" in values.guild_traits["breeding_habitat"]
    assert "pwrc_name" not in values.guild_traits  # free text, not a category
    assert any(s["English_Common_Name"] == "Dark-eyed Junco" for s in values.species)


def test_values_field_listing_and_unknown_field(tmp_path: Path) -> None:
    values = collect_values(_profile(tmp_path))

    assert "63\tNC\tNorth Carolina" in values.field_values("routes.states")
    assert values.field_values("routes.bcrs") == ["27", "28", "29"]
    with pytest.raises(KeyError, match="Unknown field 'joins.nope'"):
        values.field_values("joins.nope")


def test_editor_offers_pick_lists_and_saves_only_on_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path, guilds={"file": str(GUILDS)}))
    before = path.read_text(encoding="utf-8")

    # labelled pick lists from the release
    assert "NC · North Carolina" in at.multiselect(key="ui_states").options
    assert "63_024 · ARCOLA" in at.multiselect(key="ui_route_ids").options
    assert "07550 · Wood Thrush" in at.multiselect(key="ui_aous").options
    at.multiselect(key="ui_states").set_value([63])
    at.multiselect(key="ui_bcrs").set_value([29])
    at.multiselect(key="ui_families").set_value(["Turdidae"])
    at.multiselect(key="ui_guild_breeding_habitat").set_value(["woodland"])
    at.multiselect(key="ui_join_vehicle").set_value(["CarTotal"])
    at.radio(key="ui_shape").set_value("wide")
    at.run()
    assert not at.exception, at.exception

    assert path.read_text(encoding="utf-8") == before  # nothing saved yet
    assert any("Unsaved changes" in c.value for c in at.sidebar.caption)

    at.sidebar.button(key="btn_save").click().run()
    saved = load_profile(path)
    assert saved.routes.states == ["NC"]  # stored as the postal abbreviation
    assert saved.routes.bcrs == [29]
    assert saved.species.families == ["Turdidae"]
    assert saved.guilds.filters == {"breeding_habitat": ["woodland"]}
    assert saved.joins.vehicle == ["CarTotal"]
    assert saved.output.shape == "wide"


def test_route_choices_narrow_to_the_chosen_bcr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))
    assert len(at.multiselect(key="ui_route_ids").options) == 12

    at.multiselect(key="ui_bcrs").set_value([28])
    at.run()

    assert len(at.multiselect(key="ui_route_ids").options) == 4


def test_join_all_columns_checkbox_saves_a_star(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))

    at.checkbox(key="ui_join_all_routes").check().run()
    at.sidebar.button(key="btn_save").click().run()

    assert load_profile(path).joins.routes == ["*"]


def test_inspect_shows_stage_counts_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(
        tmp_path, runs={"run_types": [1], "quality_current_ids": [1], "min_continuity": 0.8}
    )
    at, _ = _app(tmp_path, monkeypatch, profile)

    at.button(key="btn_inspect").click().run()

    assert not at.exception, at.exception
    stages = at.dataframe[0].value
    counts = dict(zip(stages["stage"], stages["count"], strict=True))
    assert counts["runs after continuity"] == 79
    assert counts["routes with surviving runs"] == 9
    assert not (tmp_path / "out" / "o.csv").exists()


def test_run_writes_the_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path, species={"aous": ["07550"]}))

    at.button(key="btn_run_pipeline").click().run()

    assert not at.exception, at.exception
    assert (tmp_path / "out" / "o.csv").exists()
    assert any("Wrote" in s.value for s in at.success)


def test_invalid_edit_shows_the_error_and_disables_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))
    before = path.read_text(encoding="utf-8")

    at.text_input(key="ui_output_path").set_value("out/nc.xlsx").run()

    assert any("output.path" in e.value for e in at.sidebar.error)
    assert path.read_text(encoding="utf-8") == before


def test_without_a_release_lists_fall_back_to_text_boxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path, source={"raw_dir": str(tmp_path / "empty")})
    at, _ = _app(tmp_path, monkeypatch, profile)

    assert at.text_input(key="ui_states") is not None
    assert any("Fetch release" in b.label for b in at.button)
    assert any("Pick lists unavailable" in w.value for w in at.warning)
