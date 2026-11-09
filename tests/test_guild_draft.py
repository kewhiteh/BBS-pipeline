"""The Guild File page's working copy (docs/instructions/GUILD_WIZARD_PLAN.md G3, G4, G8, G11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bbs_pipeline.config import profile_from_data
from bbs_pipeline.guild_builder import SpeciesIndex
from bbs_pipeline.guild_draft import (
    STARTER,
    add_species,
    blank_draft,
    clean_value,
    draft_from_csv,
    remove_trait,
    rename_trait,
    would_overwrite,
    write_draft,
)
from bbs_pipeline.guilds import read_guild_csv, resolve_guilds
from bbs_pipeline.sources.loader import load_sources

RELEASE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real" / "release"


@pytest.fixture
def species(tmp_path: Path):
    profile = profile_from_data(
        {"source": {"raw_dir": str(RELEASE), "cache_dir": str(tmp_path / "c"), "offline": True}}
    )
    return load_sources(profile.to_bundle().protocol).tables["species"].collect()


@pytest.fixture
def index(species) -> SpeciesIndex:
    return SpeciesIndex.from_species(species)


def test_starter_opens_with_its_traits_and_free_text_kept(index: SpeciesIndex) -> None:
    draft = draft_from_csv(STARTER, index)
    assert list(draft["traits"]) == [
        "breeding_habitat",
        "nest_type",
        "migration",
        "nest_location",
        "forest_guild",
    ]
    assert draft["traits"]["nest_type"] == ["cavity", "open_cup_passerine"]
    assert draft["traits"]["forest_guild"] == []
    assert draft["notes"] == ["pwrc_name", "note"]  # free text, not traits
    wood = next(r for r in draft["rows"] if r["aou"] == "07550")
    assert (wood["common_name"], wood["breeding_habitat"]) == ("Wood Thrush", "woodland")


def test_values_are_normalised() -> None:
    assert clean_value("  Interior Edge ") == "interior_edge"


def test_add_assign_and_overwrite_reporting(index: SpeciesIndex) -> None:
    draft = blank_draft()
    draft["traits"]["forest_guild"] = ["interior", "edge"]
    add_species(draft, ["07550", "06740"], index, {"forest_guild": "interior"}, "Whitcomb 1981")
    add_species(draft, ["07550"], index)  # never twice
    assert [r["aou"] for r in draft["rows"]] == ["07550", "06740"]
    assert draft["rows"][0]["notes"] == "Whitcomb 1981"
    assert would_overwrite(draft, ["07550"], {"forest_guild": "edge"}) == [
        ("07550", "forest_guild", "interior", "edge")
    ]
    replaced = add_species(draft, ["07550"], index, {"forest_guild": "edge"}, "Freemark 1992")
    assert replaced == [("07550", "forest_guild", "interior", "edge")]
    assert draft["rows"][0]["notes"] == "Whitcomb 1981; Freemark 1992"


def test_rename_and_remove_trait(index: SpeciesIndex) -> None:
    draft = blank_draft()
    draft["traits"]["guild"] = ["interior"]
    add_species(draft, ["07550"], index, {"guild": "interior"})
    rename_trait(draft, "guild", "forest_guild")
    assert draft["rows"][0]["forest_guild"] == "interior" and "guild" not in draft["rows"][0]
    remove_trait(draft, "forest_guild")
    assert "forest_guild" not in draft["traits"] and "forest_guild" not in draft["rows"][0]


def test_saved_file_is_a_valid_guild_file(index: SpeciesIndex, species, tmp_path: Path) -> None:
    draft = blank_draft()
    draft["traits"]["forest_guild"] = ["interior"]
    add_species(draft, ["07550", "00060"], index, {"forest_guild": "interior"})
    path = write_draft(draft, tmp_path / "g.csv")
    assert "00060" in path.read_text(encoding="utf-8")  # leading zeros kept
    table = resolve_guilds(read_guild_csv(path), species, path)
    assert table.table["AOU"].to_list() == ["00060", "07550"]
    assert "forest_guild" in table.traits


def test_save_refuses_the_starter_and_non_csv(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="never overwritten"):
        write_draft(blank_draft(), STARTER)
    with pytest.raises(ValueError, match=r"end in \.csv"):
        write_draft(blank_draft(), tmp_path / "g.txt")
