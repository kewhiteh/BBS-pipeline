"""Guild file: reading, matching to BBS species, trait filters, and pipeline integration."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.config import ProfileError, profile_from_data
from bbs_pipeline.guilds import guild_filter_aous, read_guild_csv, resolve_guilds
from bbs_pipeline.pipeline import run_profile
from tests.release_fixtures import (
    TEST_DOI,
    fifty_frame,
    routes_frame,
    species_frame,
    weather_frame,
    write_release,
)

ROOT = Path(__file__).resolve().parents[1]

SPECIES = pl.DataFrame(
    {
        "AOU": ["07550", "06740", "06840", "05930", "05980"],
        "English_Common_Name": [
            "Wood Thrush",
            "Ovenbird",
            "Hooded Warbler",
            "Northern Cardinal",
            "Indigo Bunting",
        ],
        "Genus": ["Hylocichla", "Seiurus", "Setophaga", "Cardinalis", "Passerina"],
        "Species": ["mustelina", "aurocapilla", "citrina", "cardinalis", "cyanea"],
    }
)


def _csv(tmp_path: Path, text: str, name: str = "g.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _resolve(tmp_path: Path, text: str):
    path = _csv(tmp_path, text)
    return resolve_guilds(read_guild_csv(path), SPECIES, path)


def test_rows_match_by_aou_scientific_or_common_name(tmp_path: Path) -> None:
    g = _resolve(
        tmp_path,
        "AOU,Common_Name,Scientific_Name,forest_guild\n"
        "7550,,,interior\n"
        ",,  setophaga   CITRINA ,interior\n"
        ",northern cardinal,,interior_edge\n"
        "06740,Ovenbird,Seiurus aurocapilla,interior\n",
    )

    assert g.table.to_dicts() == [
        {"AOU": "05930", "forest_guild": "interior_edge"},
        {"AOU": "06740", "forest_guild": "interior"},
        {"AOU": "06840", "forest_guild": "interior"},
        {"AOU": "07550", "forest_guild": "interior"},
    ]
    assert g.traits == ["forest_guild"]
    assert g.unmatched_rows == []


def test_unmatched_rows_are_reported_with_line_numbers(tmp_path: Path) -> None:
    g = _resolve(
        tmp_path,
        "aou,scientific_name,forest_guild\n07550,,interior\n,Dendroica cerulea,interior\n\n",
    )

    assert g.unmatched_rows == [{"line": 3, "scientific_name": "Dendroica cerulea"}]
    assert g.report()["matched_rows"] == 1


def test_conflicting_identifiers_and_duplicate_species_raise(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"line 2: identifiers point at different species"):
        _resolve(tmp_path, "aou,common_name,g\n07550,Ovenbird,x\n")
    with pytest.raises(ValueError, match=r"lines \[2, 3\]: more than one row for AOU 07550"):
        _resolve(tmp_path, "aou,common_name,g\n07550,,x\n,Wood Thrush,y\n")
    with pytest.raises(ValueError, match=r"line 2: aou 'WOTH' is not an AOU code"):
        _resolve(tmp_path, "aou,g\nWOTH,x\n")


def test_file_must_have_an_identifier_and_a_trait(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs at least one of the columns"):
        read_guild_csv(_csv(tmp_path, "bird,guild\nx,y\n"))
    with pytest.raises(ValueError, match="no trait columns"):
        read_guild_csv(_csv(tmp_path, "aou,common_name\n07550,Wood Thrush\n"))
    with pytest.raises(ValueError, match="must be a .csv"):
        read_guild_csv(_csv(tmp_path, "aou,g\n07550,x\n", name="g.json"))
    with pytest.raises(FileNotFoundError):
        read_guild_csv(tmp_path / "missing.csv")


def test_filters_or_within_and_across_traits(tmp_path: Path) -> None:
    g = _resolve(
        tmp_path,
        "aou,habitat,forest_guild\n"
        "07550,woodland,interior\n"
        "06740,woodland,Interior\n"
        "05930,woodland,interior_edge\n"
        "05980,scrub,edge\n",
    )

    assert guild_filter_aous(g, {"forest_guild": ["interior", "edge"]}) == {
        "07550",
        "06740",
        "05980",
    }
    assert guild_filter_aous(
        g, {"forest_guild": ["interior", "edge"], "habitat": ["woodland"]}
    ) == {
        "07550",
        "06740",
    }
    with pytest.raises(KeyError, match="no trait column 'guild'"):
        guild_filter_aous(g, {"guild": ["interior"]})
    with pytest.raises(ValueError, match=r"forest_guild: no row has \['intrior'\]"):
        guild_filter_aous(g, {"forest_guild": ["intrior"]})


def test_example_file_and_docs_agree() -> None:
    example = (ROOT / "examples" / "guilds_example.csv").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "guild_file.md").read_text(encoding="utf-8")

    assert example.strip() in docs
    df = read_guild_csv(ROOT / "examples" / "guilds_example.csv")
    assert {"aou", "common_name", "scientific_name"} <= set(df.columns)


# --- Pipeline -------------------------------------------------------------------------


def _profile(tmp_path: Path, guild_csv: str, **sections: dict) -> object:
    raw = tmp_path / "raw"
    write_release(
        raw,
        routes=routes_frame([(63, 1, "R", 35.9, -79.0)]),
        weather=weather_frame([(1001, 63, 1, 2021, 5, 1), (1002, 63, 1, 2022, 5, 1)]),
        species=species_frame(
            [
                ("7550", "Wood Thrush", "Passeriformes", "Turdidae"),
                ("6740", "Ovenbird", "Passeriformes", "Parulidae"),
                ("5930", "Northern Cardinal", "Passeriformes", "Cardinalidae"),
            ]
        ),
        fifty_stop=fifty_frame(
            [
                (1001, 63, 1, 2021, "7550", {1: 3}),
                (1001, 63, 1, 2021, "6740", {1: 2}),
                (1002, 63, 1, 2022, "5930", {1: 4}),
            ]
        ),
    )
    data = {
        "source": {"release": TEST_DOI, "raw_dir": str(raw), "cache_dir": str(tmp_path / "c")},
        "runs": {"start_year": 2021, "end_year": 2022},
        "observations": {"source": "50_stop"},  # the fixture has 50-stop data only
        "guilds": {"file": str(_csv(tmp_path, guild_csv))},
        "joins": {"routes": [], "weather": [], "observer": [], "guilds": ["*"]},
        "output": {"path": str(tmp_path / "out" / "o.csv")},
    }
    for key, value in sections.items():
        data[key] = {**data.get(key, {}), **value}
    return profile_from_data(data)


GUILD_CSV = "aou,forest_guild\n07550,interior\n06740,interior\n"


def test_guild_filter_limits_species_and_joins_traits(tmp_path: Path) -> None:
    profile = _profile(tmp_path, GUILD_CSV, guilds={"filters": {"forest_guild": ["interior"]}})

    result = run_profile(profile)

    out = pl.read_csv(tmp_path / "out" / "o.csv", schema_overrides={"AOU": pl.String})
    assert sorted(out["AOU"].unique().to_list()) == ["06740", "07550"]
    assert set(out["forest_guild"].to_list()) == {"interior"}
    assert result.guild_report["species_without_guild"] == [
        {"AOU": "05930", "English_Common_Name": "Northern Cardinal"}
    ]


def test_guild_filter_combines_with_species_selection_by_and(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        GUILD_CSV,
        guilds={"filters": {"forest_guild": ["interior"]}},
        species={"families": ["Parulidae"]},
    )

    result = run_profile(profile, export=False)

    assert result.funnel_metrics.target_species_count == 1
    assert {r["AOU"] for r in result.preview_records} == {"06740"}


def test_without_filters_every_species_is_kept_with_null_traits(tmp_path: Path) -> None:
    result = run_profile(_profile(tmp_path, GUILD_CSV), export=False)

    traits = {r["AOU"]: r["forest_guild"] for r in result.preview_records}
    assert traits == {"07550": "interior", "06740": "interior", "05930": None}


def test_unmatched_error_mode_raises(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        "aou,scientific_name,forest_guild\n07550,,interior\n,Dendroica cerulea,interior\n",
        guilds={"unmatched": "error"},
    )

    with pytest.raises(ValueError, match=r"1 guild row\(s\) match no BBS species"):
        run_profile(profile, export=False)


def test_wide_output_keeps_trait_columns(tmp_path: Path) -> None:
    profile = _profile(tmp_path, GUILD_CSV, output={"shape": "wide"})

    result = run_profile(profile)

    wide = pl.read_csv(tmp_path / "out" / "o.csv")
    assert "forest_guild" in wide.columns
    assert wide.columns == result.output_columns


def test_guild_settings_need_a_file() -> None:
    with pytest.raises(ProfileError, match="need guilds.file"):
        profile_from_data({"joins": {"guilds": ["forest_guild"]}})
    with pytest.raises(ProfileError, match="need guilds.file"):
        profile_from_data({"guilds": {"filters": {"forest_guild": ["interior"]}}})


def test_trait_column_clashing_with_output_column_raises(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "aou,Family\n07550,x\n")

    with pytest.raises(
        ValueError, match="'Family' is requested from both joins.species and joins.guilds"
    ):
        run_profile(profile, export=False)


def test_identifier_header_aliases(tmp_path) -> None:
    """A 'species' header (Kyle's first file, 2026-11-11) reads as common_name."""
    from bbs_pipeline.guilds import read_guild_csv

    path = tmp_path / "g.csv"
    path.write_text("Species,Scientific Name,guild\nWood Thrush,Hylocichla mustelina,interior\n")
    assert read_guild_csv(path).columns == ["common_name", "scientific_name", "guild"]

    both = tmp_path / "b.csv"  # the standard name wins; the alias stays a trait
    both.write_text("common_name,species,guild\nWood Thrush,mustelina,interior\n")
    assert read_guild_csv(both).columns == ["common_name", "species", "guild"]
