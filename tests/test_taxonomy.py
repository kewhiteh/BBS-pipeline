"""Subspecies lumping derived from the release's scientific names."""

from __future__ import annotations

import polars as pl
import pytest

from bbs_pipeline.contracts import ProtocolConfigContract, SpeciesSelectionContract
from bbs_pipeline.engine import run_ecological_engine
from bbs_pipeline.taxonomy import lump_map, lumped_species, with_species_names

# Entries shaped like the 2026 release's species list
SPECIES = pl.DataFrame(
    [
        ("05670", "(Slate-colored Junco) Dark-eyed Junco", "Junco", "hyemalis hyemalis"),
        ("05690", "(Gray-headed Junco) Dark-eyed Junco", "Junco", "hyemalis caniceps"),
        ("05677", "(unid. race) Dark-eyed Junco", "Junco", "hyemalis"),
        ("04120", "(Yellow-shafted Flicker) Northern Flicker", "Colaptes", "auratus auratus"),
        (
            "04125",
            "hybrid Northern Flicker (Red x Yellow-shafted)",
            "Colaptes",
            "auratus auratus x auratus cafer",
        ),
        ("04123", "(unid. Red / Yellow Shafted) Northern Flicker", "Colaptes", "auratus"),
        ("05280", "(Common Redpoll) Redpoll", "Acanthis", "flammea flammea"),
        ("05270", "(Hoary Redpoll) Redpoll", "Acanthis", "flammea hornemanni"),
        ("05275", "(unid. Common / Hoary) Redpoll", "Acanthis", "flammea flammea / hornemanni"),
        ("01489", "unid. Greater Scaup / Lesser Scaup", "Aythya", "marila / affinis"),
        ("01326", "hybrid Mallard x Black Duck", "Anas", "platyrhynchos x rubripes"),
        ("01320", "Mallard", "Anas", "platyrhynchos"),
        ("07550", "Wood Thrush", "Hylocichla", "mustelina"),
    ],
    schema=["AOU", "English_Common_Name", "Genus", "Species"],
    orient="row",
)


def test_forms_lump_into_their_species_level_entry() -> None:
    assert lump_map(SPECIES) == {
        "05670": "05677",
        "05690": "05677",
        "04120": "04123",
        "04125": "04123",  # hybrid between two forms of one species
        "05280": "05275",  # no plain binomial: the within-species unid. entry
        "05270": "05275",
    }


def test_between_species_entries_are_left_alone() -> None:
    mapping = lump_map(SPECIES)

    assert "01489" not in mapping  # unid. Greater / Lesser Scaup
    assert "01326" not in mapping  # Mallard x Black Duck
    assert "01320" not in mapping


def test_lumped_species_are_named_without_the_form_prefix() -> None:
    names = dict(lumped_species(SPECIES).select("AOU", "English_Common_Name").iter_rows())

    assert names["05677"] == "Dark-eyed Junco"
    assert names["04123"] == "Northern Flicker"
    assert names["05275"] == "Redpoll"
    assert "05690" not in names
    assert names["07550"] == "Wood Thrush"


def test_species_names_are_available_for_lookups() -> None:
    lookup = with_species_names(SPECIES)

    junco = lookup.filter(pl.col("AOU") == "05677")["English_Common_Name"].to_list()
    assert sorted(junco) == ["(unid. race) Dark-eyed Junco", "Dark-eyed Junco"]


def test_group_without_a_species_level_entry_raises() -> None:
    forms_only = SPECIES.filter(pl.col("AOU") != "05677")

    with pytest.raises(ValueError, match=r"Junco hyemalis: 0 species-level entries"):
        lump_map(forms_only)


def _tables() -> dict[str, pl.DataFrame]:
    stops = {f"Stop{i}": [0, 0] for i in range(1, 51)}
    stops["Stop1"] = [3, 2]
    return {
        "routes": pl.DataFrame(
            {"StateNum": [63], "Route": [1], "Latitude": [36.0], "Longitude": [-79.0]}
        ),
        "weather": pl.DataFrame(
            {
                "RouteDataID": [1],
                "StateNum": [63],
                "Route": [1],
                "Year": [2021],
                "ObsN": [5],
                "RunType": [1],
                "QualityCurrentID": [1],
            }
        ),
        "species": SPECIES.with_columns(
            pl.lit("Passeriformes").alias("Order"), pl.lit("F").alias("Family")
        ),
        "fifty_stop": pl.DataFrame(
            {
                "RouteDataID": [1, 1],
                "StateNum": [63, 63],
                "Route": [1, 1],
                "Year": [2021, 2021],
                "AOU": ["05670", "05690"],  # Slate-colored and Gray-headed on one run
                **stops,
            }
        ),
    }


def test_engine_counts_all_forms_under_the_species() -> None:
    df = run_ecological_engine(
        _tables(), SpeciesSelectionContract(), ProtocolConfigContract(year_range=(2021, 2021))
    ).observations_df

    junco = df.filter(pl.col("AOU") == "05677").row(0, named=True)
    assert junco["route_total"] == 5
    assert junco["English_Common_Name"] == "Dark-eyed Junco"
    assert "05690" not in df["AOU"].to_list()


def test_selecting_a_form_selects_its_species() -> None:
    df = run_ecological_engine(
        _tables(),
        SpeciesSelectionContract(aous=["05690"]),
        ProtocolConfigContract(year_range=(2021, 2021)),
    ).observations_df

    assert df["AOU"].to_list() == ["05677"]
    assert df["route_total"].to_list() == [5]


def test_without_lumping_forms_stay_separate() -> None:
    df = run_ecological_engine(
        _tables(),
        SpeciesSelectionContract(),
        ProtocolConfigContract(year_range=(2021, 2021), lump_subspecies_and_hybrids=False),
    ).observations_df

    assert sorted(df["AOU"].to_list()) == ["05670", "05690"]
