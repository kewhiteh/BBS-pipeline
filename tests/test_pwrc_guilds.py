"""The Patuxent species-group build script and the committed starter guild file."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.guilds import read_guild_csv
from scripts.build_pwrc_guilds import NAME_MAP, TRAITS, build

ROOT = Path(__file__).resolve().parents[1]

PAGE = """<HTML><BODY><H1>List of Species Groupings</H1>
<H2>Breeding Habitat Groups</H2>
<H3>Woodland species</H3> Wood thrush, Grt. crested flycatcher, and Rufous-sided towhee.
<H3>Urban species</H3> Rock dove, and American robin.
<H2>Migration Form Groups</H2>
<H3>Neotropical migrant species</H3> Wood thrush, and Northwestern crow.
<!--hdrfn()--></BODY></HTML>"""

SPECIES = pl.DataFrame(
    {
        "AOU": ["07550", "04520", "05870", "05880", "03131", "07610"],
        "English_Common_Name": [
            "Wood Thrush",
            "Great Crested Flycatcher",
            "Eastern Towhee",
            "Spotted Towhee",
            "Rock Pigeon",
            "American Robin",
        ],
        "Genus": ["Hylocichla", "Myiarchus", "Pipilo", "Pipilo", "Columba", "Turdus"],
        "Species": [
            "mustelina",
            "crinitus",
            "erythrophthalmus",
            "maculatus",
            "livia",
            "migratorius",
        ],
    }
)


def test_build_maps_abbreviations_renames_and_splits() -> None:
    out = build(PAGE, SPECIES)

    rows = {r["common_name"]: r for r in out.to_dicts()}
    assert rows["Wood Thrush"]["breeding_habitat"] == "woodland"
    assert rows["Wood Thrush"]["migration"] == "neotropical"
    assert rows["Great Crested Flycatcher"]["pwrc_name"] == "Grt. crested flycatcher"
    assert rows["Rock Pigeon"]["note"] == "renamed"
    # a split gives the old groups to every daughter species
    assert rows["Eastern Towhee"]["breeding_habitat"] == "woodland"
    assert rows["Spotted Towhee"]["note"] == "split"
    # dropped names (Northwestern crow) produce no row
    assert out.height == 6
    assert out.columns[:3] == ["aou", "common_name", "scientific_name"]
    assert out["forest_guild"].null_count() == out.height


def test_build_raises_on_unknown_names_and_double_groups() -> None:
    with pytest.raises(ValueError, match=r"no BBS match and no NAME_MAP entry: \['Dodo'\]"):
        build(PAGE.replace("American robin", "Dodo"), SPECIES)
    double = PAGE.replace("Rock dove, and", "Rock dove, Wood thrush, and")
    with pytest.raises(ValueError, match="Species in two groups of one kind"):
        build(double, SPECIES)


def test_name_map_never_types_aou_codes() -> None:
    for targets, _note in NAME_MAP.values():
        for name in targets or []:
            assert not name.strip().isdigit(), name


def test_committed_starter_file_is_a_valid_guild_file() -> None:
    path = ROOT / "guilds" / "pwrc_species_groups.csv"
    df = read_guild_csv(path)

    assert df.height > 400
    assert df["aou"].is_unique().all()
    assert (
        df.select(["aou", "common_name", "scientific_name"]).null_count().sum_horizontal().item()
        == 0
    )
    assert set(TRAITS) <= set(df.columns)
    habitats = set(df["breeding_habitat"].drop_nulls().to_list())
    assert habitats == {
        "grassland",
        "wetland_open_water",
        "successional_scrub",
        "woodland",
        "urban",
    }
