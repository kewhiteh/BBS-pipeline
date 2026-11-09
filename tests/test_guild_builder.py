"""Guild File wizard core: name matching and checks (docs/instructions/GUILD_WIZARD_PLAN.md)."""

from __future__ import annotations

import io
from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.cli import main as cli_main
from bbs_pipeline.config import profile_from_data, save_profile
from bbs_pipeline.guild_builder import (
    SpeciesIndex,
    check_guild_rows,
    match_names,
    read_name_changes,
    recorded_species,
)
from bbs_pipeline.sources.loader import load_sources

RELEASE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real" / "release"


@pytest.fixture
def profile(tmp_path: Path):
    return profile_from_data(
        {
            "source": {
                "release": "10.5066/P144YU3S",
                "raw_dir": str(RELEASE),
                "cache_dir": str(tmp_path / "cache"),
                "offline": True,
            },
            "runs": {"start_year": 2015, "end_year": 2024},
            "observations": {"source": "50_stop"},
            "output": {"path": str(tmp_path / "o.csv")},
        }
    )


@pytest.fixture
def index(profile) -> SpeciesIndex:
    tables = load_sources(profile.to_bundle().protocol).tables
    return SpeciesIndex.from_species(tables["species"].collect())


def _kinds(index: SpeciesIndex, lines: list[str]) -> dict[str, tuple[str, list[str]]]:
    return {m.text: (m.kind, m.aous) for m in match_names(lines, index)}


def test_exact_names_codes_and_forms(index: SpeciesIndex) -> None:
    got = _kinds(
        index, ["Wood Thrush", "hylocichla  MUSTELINA", "07550", "7550", "Dark-eyed Junco", ""]
    )
    assert all(v == ("exact", ["07550"]) for k, v in got.items() if k != "Dark-eyed Junco")
    assert got["Dark-eyed Junco"] == ("exact", ["05677"])
    assert "" not in got  # blank lines are skipped


def test_old_genera_and_changed_endings_are_suggestions(index: SpeciesIndex) -> None:
    got = {
        m.text: m
        for m in match_names(
            ["Dendroica cerulea", "Oporornis formosus", "Wilsonia pusilla", "Seiurus aurocapillus"],
            index,
        )
    }
    assert got["Dendroica cerulea"].aous == ["06580"]
    assert got["Oporornis formosus"].aous == ["06770"]
    assert "ending changed" in got["Oporornis formosus"].why
    assert got["Wilsonia pusilla"].aous == ["06850"]  # Cardellina, not Setophaga
    assert got["Seiurus aurocapillus"].aous == ["06740"]
    assert all(m.kind == "old" and m.needs_confirm for m in got.values())


def test_splits_forms_spelling_and_no_match(index: SpeciesIndex) -> None:
    got = _kinds(
        index,
        ["Rufous-sided towhee", "Slate-colored junco", "Wood Thursh", "forest thrushes (unid.)"],
    )
    assert got["Rufous-sided towhee"] == ("old", ["05870", "05880"])
    assert got["Slate-colored junco"] == ("old", ["05677"])
    assert got["Wood Thursh"] == ("spelling", ["07550"])
    assert got["forest thrushes (unid.)"] == ("none", [])


def test_name_change_targets_exist_in_the_release(index: SpeciesIndex) -> None:
    """Every hand-listed (non-genus) old name resolves to species in the release."""
    for change in read_name_changes():
        if change.kind == "genus":
            continue
        result = match_names([change.old], index)[0]
        assert result.aous, f"{change.old} -> {change.current} matches nothing"


def test_checks_find_every_problem(index: SpeciesIndex) -> None:
    table = pl.DataFrame(
        {
            "aou": ["07550", "06740", None, "07550", None],
            "common_name": [None, None, "Scarlet Tanager", None, "No Such Bird"],
            "forest_guild": ["interior", "Interior", "interior", "edge", "edge"],
            "nest": ["cup", None, "cup", "cup", "cup"],
            "notes": ["Whitcomb 1981", None, None, None, None],
        }
    )
    allowed = {"forest_guild": ["interior", "edge"], "nest": ["cup"]}
    checks = check_guild_rows(table, index, allowed=allowed, recorded=["07550", "05930"])
    assert (checks.rows, checks.matched) == (5, 4)
    assert checks.unmatched == [{"common_name": "No Such Bird"}]
    assert checks.duplicates == {"07550": 2}
    assert checks.empty_trait_rows == 1  # notes are not a trait
    assert checks.outside_values == {"forest_guild": ["Interior"]}
    assert checks.near_duplicate_values == {"forest_guild": [["Interior", "interior"]]}
    assert checks.recorded_without_row == ["05930"]
    assert checks.blocking


def test_recorded_species_are_real_records(profile) -> None:
    rec = recorded_species(profile)
    assert rec.height > 0 and rec["runs"].min() >= 1
    assert "07550" in rec["AOU"].to_list()  # Wood Thrush is on the fixture routes


def test_cli_match_and_check(profile, tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "p.toml"
    save_profile(profile, cfg)
    monkeypatch.setattr("sys.stdin", io.StringIO("Dendroica cerulea\nWood Thrush\n"))
    assert cli_main(["guilds", "match", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "06580 Cerulean Warbler" in out and "CONFIRM" in out

    csv = tmp_path / "g.csv"
    csv.write_text("aou,forest_guild\n07550,interior\n07550,edge\n", encoding="utf-8")
    assert cli_main(["guilds", "check", str(csv), "--config", str(cfg)]) == 1
    assert "2 rows for 07550 Wood Thrush" in capsys.readouterr().out
