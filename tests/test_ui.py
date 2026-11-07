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
    assert "note" not in values.guild_traits  # a note column, not a trait to filter on
    assert any(s["English_Common_Name"] == "Dark-eyed Junco" for s in values.species)


def test_values_field_listing_and_unknown_field(tmp_path: Path) -> None:
    values = collect_values(_profile(tmp_path))

    assert "63\tNC\tNorth Carolina" in values.field_values("routes.states")
    assert values.field_values("routes.bcrs") == [
        "27\tSoutheastern Coastal Plain",
        "28\tAppalachian Mountains",
        "29\tPiedmont",
    ]
    assert values.label("Stratum", 1) == "1 · Sub-tropical"
    assert values.label("RunType", 1).startswith("1 · Meets all BBS criteria")
    with pytest.raises(KeyError, match="Unknown field 'joins.nope'"):
        values.field_values("joins.nope")


def test_code_labels_are_empty_without_metadata(tmp_path: Path) -> None:
    from bbs_pipeline.values import _code_labels

    assert _code_labels(tmp_path) == {}


def test_editor_shows_names_for_coded_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    bcrs = at.multiselect(key="ui_bcrs")
    assert bcrs.options == [
        "27 · Southeastern Coastal Plain",
        "28 · Appalachian Mountains",
        "29 · Piedmont",
    ]
    assert bcrs.help
    assert all(" · " in o for o in at.multiselect(key="ui_strata").options)


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
    at.segmented_control(key="ui_join_mode_vehicle").set_value("Some").run()
    at.multiselect(key="ui_join_vehicle").set_value(["CarTotal"])
    at.segmented_control(key="ui_shape").set_value("wide")
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

    at.segmented_control(key="ui_join_mode_routes").set_value("All").run()
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
    gpkg = {"path": str(tmp_path / "out" / "o.gpkg")}
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path, output=gpkg))
    before = path.read_text(encoding="utf-8")

    at.selectbox(key="ui_crs_choice").set_value("Other (enter an EPSG code)").run()
    at.text_input(key="ui_crs").set_value("not-a-crs").run()

    assert any("output.crs" in e.value for e in at.sidebar.error)
    assert path.read_text(encoding="utf-8") == before


def test_without_a_release_lists_fall_back_to_text_boxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path, source={"raw_dir": str(tmp_path / "empty")})
    at, _ = _app(tmp_path, monkeypatch, profile)

    assert at.text_input(key="ui_states") is not None
    assert any("Fetch release" in b.label for b in at.button)
    assert any("Fetch the release" in w.value for w in at.warning)


# --- GUI parity with the CLI ------------------------------------------------------


def test_open_another_profile_loads_its_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other.toml"
    save_profile(_profile(tmp_path, routes={"bcrs": [28]}, runs={"start_year": 2001}), other)
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    at.sidebar.text_input(key="sb_open_path").set_value(str(other)).run()
    at.sidebar.button(key="btn_open").click().run()

    assert not at.exception, at.exception
    assert any(str(other) in m.value for m in at.sidebar.markdown)
    assert at.multiselect(key="ui_bcrs").value == [28]
    assert at.slider(key="ui_years").value == (2001, 2024)


def test_open_reports_an_invalid_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[runs]\nstart_yeer = 2000\n", encoding="utf-8")
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    at.sidebar.text_input(key="sb_open_path").set_value(str(bad)).run()
    at.sidebar.button(key="btn_open").click().run()

    assert any("runs.start_yeer: unknown key" in e.value for e in at.sidebar.error)


def test_save_as_writes_a_new_file_and_refuses_to_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))
    copy = tmp_path / "copy.toml"

    at.multiselect(key="ui_bcrs").set_value([27])
    at.sidebar.text_input(key="sb_save_as").set_value(str(copy))
    at.sidebar.button(key="btn_save_as").click().run()
    assert load_profile(copy).routes.bcrs == [27]
    assert load_profile(path).routes.bcrs == []  # the original is untouched
    assert any(str(copy) in m.value for m in at.sidebar.markdown)

    at.sidebar.text_input(key="sb_save_as").set_value(str(path))
    at.sidebar.button(key="btn_save_as").click().run()
    assert any("tick 'Overwrite if it exists'" in e.value for e in at.sidebar.error)
    assert load_profile(path).routes.bcrs == []


def test_new_profile_starts_from_defaults_and_needs_save_as(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the defaults point at ./data/raw, which does not exist here
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path, routes={"bcrs": [29]}))

    at.sidebar.button(key="btn_new").click().run()

    assert not at.exception, at.exception
    assert any("new, not saved yet" in m.value for m in at.sidebar.markdown)
    assert at.slider(key="ui_years").value == (1997, 2024)
    assert not any(b.key == "btn_save" for b in at.sidebar.button)
    # No release there: the editor offers Fetch and never downloads on its own
    assert any("Fetch release" in b.label for b in at.button)
    assert not (tmp_path / "data").exists()


def test_download_settings_are_editable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))

    at.number_input(key="ui_http_timeout").set_value(90.0)
    at.number_input(key="ui_http_retries").set_value(5)
    at.number_input(key="ui_http_backoff").set_value(2.5).run()
    at.sidebar.button(key="btn_save").click().run()

    source = load_profile(path).source
    assert (source.http_timeout_seconds, source.http_retries, source.http_backoff_seconds) == (
        90.0,
        5,
        2.5,
    )


def test_resolved_toml_is_shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    shown = "\n".join(c.value for c in at.sidebar.code)

    assert "[runs]" in shown and "start_year = 2015" in shown
    assert "http_retries = 3" in shown  # every default filled in, like `bbs validate`


def test_free_text_guild_filter_for_traits_without_a_pick_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path, guilds={"file": str(GUILDS)}))

    at.text_area(key="ui_guild_other_filters").set_value("pwrc_name=Wood thrush").run()
    at.sidebar.button(key="btn_save").click().run()

    assert load_profile(path).guilds.filters == {"pwrc_name": ["Wood thrush"]}


@pytest.mark.parametrize(
    ("widget", "value", "message"),
    [
        ("ui_overlays", "watershed data/huc8.gpkg", "Overlays, line 1: expected name=value"),
        ("ui_guild_other_filters", "habitat", "Other trait filters, line 1: expected name=value"),
    ],
)
def test_malformed_lines_block_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, widget: str, value: str, message: str
) -> None:
    profile = _profile(
        tmp_path, guilds={"file": str(GUILDS)}, output={"path": str(tmp_path / "out" / "o.gpkg")}
    )
    at, path = _app(tmp_path, monkeypatch, profile)
    before = path.read_text(encoding="utf-8")

    at.text_area(key=widget).set_value(value).run()

    assert any(message in e.value for e in at.sidebar.error)
    assert not any(b.key == "btn_save" for b in at.sidebar.button)
    assert path.read_text(encoding="utf-8") == before


def test_non_numeric_bcr_in_text_box_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path, source={"raw_dir": str(tmp_path / "empty")})
    at, _ = _app(tmp_path, monkeypatch, profile)

    at.text_input(key="ui_bcrs").set_value("27, twenty-nine").run()

    assert not at.exception, at.exception
    assert any("'twenty-nine' is not a whole number" in e.value for e in at.sidebar.error)


def test_profile_list_skips_non_profile_toml_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "broken.toml").write_text("[runs\n", encoding="utf-8")
    (tmp_path / "typo.toml").write_text("[runs]\nstart_yeer = 2000\n", encoding="utf-8")
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    offered = at.sidebar.selectbox(key="sb_open_choice").options

    assert "project.toml" in offered
    assert "typo.toml" in offered  # a profile with a mistake can still be opened and fixed
    assert "pyproject.toml" not in offered
    assert "broken.toml" not in offered


def test_pick_lists_survive_a_briefly_invalid_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1995 with the 50_stop source is invalid until the source changes; keep the pickers."""
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))
    at.multiselect(key="ui_run_types").set_value([1]).run()

    at.slider(key="ui_years").set_value((1995, 2024)).run()
    assert any(m.key == "ui_aous" for m in at.multiselect)
    assert at.multiselect(key="ui_run_types").value == [1]
    assert any("50_stop" in e.value for e in at.error)  # the reason is shown

    at.selectbox(key="ui_obs_source").set_value("10_stop").run()
    assert not at.error
    assert at.multiselect(key="ui_run_types").value == [1]


def test_failures_are_logged_with_their_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path, joins={"weather": ["ObsN"], "observer": ["ObsN"]})
    at, _ = _app(tmp_path, monkeypatch, profile)
    monkeypatch.chdir(tmp_path)

    at.button(key="btn_inspect").click().run()

    assert any("logs/ui_errors.log" in e.value for e in at.error)
    log = (tmp_path / "logs" / "ui_errors.log").read_text(encoding="utf-8")
    assert "Inspect" in log and "Traceback" in log and "requested from both" in log


def test_output_file_is_folder_name_and_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))
    assert not any(s.key == "ui_crs_choice" for s in at.selectbox)  # CSV: no projection

    at.text_input(key="ui_output_name").set_value("wood_thrush").run()
    at.selectbox(key="ui_output_format").set_value(".gpkg").run()
    at.selectbox(key="ui_crs_choice").set_value("EPSG:5070").run()
    at.sidebar.button(key="btn_save").click().run()

    saved = load_profile(path).output
    assert saved.path == tmp_path / "out" / "wood_thrush.gpkg"
    assert saved.crs == "EPSG:5070"


def test_join_modes_none_some_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))

    at.segmented_control(key="ui_join_mode_weather").set_value("None").run()
    at.segmented_control(key="ui_join_mode_vehicle").set_value("Some").run()
    at.multiselect(key="ui_join_vehicle").set_value(["CarTotal"]).run()
    at.segmented_control(key="ui_join_mode_species").set_value("All").run()
    at.sidebar.button(key="btn_save").click().run()

    joins = load_profile(path).joins
    assert (joins.weather, joins.vehicle, joins.species) == ([], ["CarTotal"], ["*"])


def test_observer_first_year_is_one_checkbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path))
    assert not any(n.key in ("ui_novice", "ui_veteran") for n in at.number_input)

    at.checkbox(key="ui_drop_first_year").check().run()
    at.sidebar.button(key="btn_save").click().run()

    assert load_profile(path).runs.drop_observer_first_year is True


def test_other_projection_can_be_added_to_the_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gpkg = {"path": str(tmp_path / "out" / "o.gpkg")}
    at, path = _app(tmp_path, monkeypatch, _profile(tmp_path, output=gpkg))
    monkeypatch.chdir(tmp_path)

    at.selectbox(key="ui_crs_choice").set_value("Other (enter an EPSG code)").run()
    at.text_input(key="ui_crs").set_value("26917").run()
    assert any("NAD83 / UTM zone 17N" in c.value for c in at.caption)
    at.button(key="btn_add_crs").click().run()

    saved = (tmp_path / "projections.toml").read_text(encoding="utf-8")
    assert '"EPSG:26917" = "NAD83 / UTM zone 17N, metre"' in saved
    choice = at.selectbox(key="ui_crs_choice")
    assert choice.value == "EPSG:26917"
    assert "NAD83 / UTM zone 17N, metre · EPSG:26917" in choice.options
    at.sidebar.button(key="btn_save").click().run()
    assert load_profile(path).output.crs == "EPSG:26917"


def test_unknown_projection_code_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gpkg = {"path": str(tmp_path / "out" / "o.gpkg")}
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path, output=gpkg))

    at.selectbox(key="ui_crs_choice").set_value("Other (enter an EPSG code)").run()
    at.text_input(key="ui_crs").set_value("EPSG:999999").run()

    assert any("not a known EPSG code" in c.value for c in at.caption)
    assert not any(b.key == "btn_add_crs" for b in at.button)


def test_opening_a_saved_profile_shows_no_unsaved_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default joins.guilds is ["*"]; with no guild file the editor must not clear it."""
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    assert [c.value for c in at.sidebar.caption if "aved" in c.value] == ["Saved"]


# --- getting started ----------------------------------------------------------------


def test_start_from_an_example_makes_an_unsaved_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = ROOT / "examples" / "wood_thrush_story_map.toml"
    before = example.read_text(encoding="utf-8")
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))

    picker = at.selectbox(key="guide_example_choice")
    assert any("wood_thrush_story_map: Wood Thrush story map" in o for o in picker.options)
    picker.set_value("examples/wood_thrush_story_map.toml").run()
    at.button(key="btn_guide_example").click().run()

    assert at.session_state["profile_path"] is None  # new, unsaved: Save as to keep it
    route_ids = at.session_state["draft"]["routes"]["route_ids"]
    assert route_ids == load_profile(example).routes.route_ids
    assert example.read_text(encoding="utf-8") == before


def test_getting_started_ticks_steps_as_they_are_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, _ = _app(tmp_path, monkeypatch, _profile(tmp_path))
    label = next(e.label for e in at.expander if e.label.startswith("Getting started"))
    # open + fetched + surveys + output are done; where, species, inspect, run are not
    assert "(5 of 9 done)" in label  # the profile is also saved

    at.multiselect(key="ui_states").set_value([63]).run()
    at.button(key="btn_inspect").click().run()

    label = next(e.label for e in at.expander if e.label.startswith("Getting started"))
    assert "(6 of 9 done)" in label  # where and inspect done, but now unsaved
