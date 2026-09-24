"""Test suite for bbs_pipeline.core.taxonomy.

Verification gates (Phase 4, docs/04_TASKS.md §Task 4.3):
- 100% pass, zero unhandled warnings.
- At least two explicit negative-failure assertions (marked with # NEGATIVE).
- No live network calls; all data is synthetic in-memory.
- All Polars DataFrames use explicit schema_overrides (no inference).

Mathematical specification exercised:
- §2.7  Taxonomic set-union resolver:
          S_target = (⋃ S_guild ∪ ⋃ S_order ∪ ⋃ S_family ∪ S_custom)
                     ∖ S_migrant_nonbreeder
- §1.6  SpeciesList, guilds.json, and MigrantNonBreeder parsing.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from bbs_pipeline.core.taxonomy import (
    SPECIES_LIST_SCHEMA,
    filter_species_df,
    load_guilds_json,
    parse_migrant_nonbreeder,
    parse_species_list,
    resolve_target_species,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic in-memory data builders
# ---------------------------------------------------------------------------

# Minimal SpeciesList rows: 6 species across 3 orders / 3 families
_SPECIES_CSV = (
    "Seq,AOU,English_Common_Name,French_Common_Name,Order,Family,Genus,Species\n"
    "1,01770,Black-bellied Whistling-Duck,Dendrocygne a ventre noir,Anseriformes,Anatidae,Dendrocygna,autumnalis\n"
    "2,06100,American Robin,Merle americain,Passeriformes,Turdidae,Turdus,migratorius\n"
    "3,06640,Barn Swallow,Hirondelle rustique,Passeriformes,Hirundinidae,Hirundo,rustica\n"
    "4,06890,House Sparrow,Moineau domestique,Passeriformes,Passeridae,Passer,domesticus\n"
    "5,03940,Osprey,Balbuzard pecheur,Accipitriformes,Pandionidae,Pandion,haliaetus\n"
    "6,04740,American Kestrel,Crecerelle d'Amerique,Falconiformes,Falconidae,Falco,sparverius\n"
)

# Minimal guilds.json — two entries, one matching each filter type
_GUILDS_DICT = {
    "01770": {
        "common_name": "Black-bellied Whistling-Duck",
        "breeding_habitat": "wetland",
        "foraging_guild": "surface_dabbler",
        "migratory_status": "short_distance_migrant",
    },
    "06100": {
        "common_name": "American Robin",
        "breeding_habitat": "forest",
        "foraging_guild": "ground_gleaner",
        "migratory_status": "short_distance_migrant",
    },
    "06640": {
        "common_name": "Barn Swallow",
        "breeding_habitat": "open",
        "foraging_guild": "aerial_insectivore",
        "migratory_status": "long_distance_migrant",
    },
}

# AOU 06890 (House Sparrow) is declared a migrant/non-breeder
_MIGRANT_AOU = "06890"

# Migrant CSV with identical structure to Migrants.csv in the ZIP
_MIGRANT_CSV = (
    "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,"
    + ",".join(f"Stop{i}" for i in range(1, 51))
    + "\n"
    + f"9999999,840,02,001,101,2000,{_MIGRANT_AOU},"
    + ",".join(["0"] * 50)
    + "\n"
)


def _make_species_buf() -> io.BytesIO:
    """Return an in-memory buffer containing the synthetic SpeciesList CSV."""
    return io.BytesIO(_SPECIES_CSV.encode("latin-1"))


def _make_guilds_json_file(tmp_path: Path) -> Path:
    """Write a minimal guilds.json to a temp directory and return its path."""
    p = tmp_path / "guilds.json"
    p.write_text(json.dumps(_GUILDS_DICT), encoding="utf-8")
    return p


def _make_migrant_buf() -> io.BytesIO:
    """Return an in-memory buffer containing a synthetic MigrantNonBreeder.zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MigrantNonBreeder/Migrants.csv", _MIGRANT_CSV)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# parse_species_list
# ---------------------------------------------------------------------------


class TestParseSpeciesList:
    def test_returns_dataframe_with_correct_columns(self):
        buf = _make_species_buf()
        df = parse_species_list(buf)
        assert isinstance(df, pl.DataFrame)
        for col in SPECIES_LIST_SCHEMA:
            assert col in df.columns, f"Expected column '{col}' in result."

    def test_aou_column_is_string_dtype(self):
        df = parse_species_list(_make_species_buf())
        assert df["AOU"].dtype == pl.String

    def test_aou_is_zero_padded_five_digits(self):
        df = parse_species_list(_make_species_buf())
        for aou in df["AOU"].to_list():
            assert len(aou) == 5, f"AOU '{aou}' is not 5 digits."
            assert aou.isdigit(), f"AOU '{aou}' contains non-digit chars."

    def test_row_count_matches_input(self):
        df = parse_species_list(_make_species_buf())
        # _SPECIES_CSV has 6 data rows
        assert len(df) == 6

    # NEGATIVE: wrong input type raises TypeError
    def test_raises_type_error_on_non_bytesio(self):  # NEGATIVE
        with pytest.raises(TypeError, match="io.BytesIO"):
            parse_species_list("not a buffer")  # type: ignore[arg-type]

    # NEGATIVE: empty buffer raises ValueError
    def test_raises_value_error_on_empty_buffer(self):  # NEGATIVE
        with pytest.raises(ValueError, match="empty"):
            parse_species_list(io.BytesIO(b""))


# ---------------------------------------------------------------------------
# load_guilds_json
# ---------------------------------------------------------------------------


class TestLoadGuildsJson:
    def test_returns_dict_with_padded_aou_keys(self, tmp_path: Path):
        path = _make_guilds_json_file(tmp_path)
        guilds = load_guilds_json(path)
        assert isinstance(guilds, dict)
        for key in guilds:
            assert len(key) == 5 and key.isdigit(), (
                f"Key '{key}' not 5-digit zero-padded."
            )

    def test_known_entry_has_expected_fields(self, tmp_path: Path):
        guilds = load_guilds_json(_make_guilds_json_file(tmp_path))
        assert "01770" in guilds
        entry = guilds["01770"]
        assert entry["breeding_habitat"] == "wetland"
        assert entry["foraging_guild"] == "surface_dabbler"

    # NEGATIVE: missing file raises FileNotFoundError
    def test_raises_file_not_found_for_missing_path(self, tmp_path: Path):  # NEGATIVE
        with pytest.raises(FileNotFoundError):
            load_guilds_json(tmp_path / "nonexistent.json")

    # NEGATIVE: empty file raises ValueError
    def test_raises_value_error_on_empty_file(self, tmp_path: Path):  # NEGATIVE
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            load_guilds_json(p)


# ---------------------------------------------------------------------------
# parse_migrant_nonbreeder
# ---------------------------------------------------------------------------


class TestParseMigrantNonBreeder:
    def test_returns_frozenset_of_strings(self):
        buf = _make_migrant_buf()
        result = parse_migrant_nonbreeder(buf)
        assert isinstance(result, frozenset)
        for code in result:
            assert isinstance(code, str), f"Expected str, got {type(code)}"

    def test_known_migrant_aou_in_result(self):
        result = parse_migrant_nonbreeder(_make_migrant_buf())
        assert _MIGRANT_AOU in result, (
            f"Expected {_MIGRANT_AOU} in migrant set, got {result}"
        )

    def test_aou_codes_are_five_digit_zero_padded(self):
        result = parse_migrant_nonbreeder(_make_migrant_buf())
        for code in result:
            assert len(code) == 5 and code.isdigit(), (
                f"Migrant AOU '{code}' is not 5-digit zero-padded."
            )

    def test_whitespace_padded_stop_columns_in_migrants_csv(self):
        """Verify that whitespace-padded numbers like '0     ' in Stop columns do not cause ComputeError."""
        csv_content = (
            "RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,"
            + ",".join(f"Stop{i}" for i in range(1, 51))
            + "\n"
            + "9999999,840,02,001,101,2000,06890,"
            + ",".join(["0     "] * 50)
            + "\n"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("MigrantNonBreeder/Migrants.csv", csv_content)
        buf.seek(0)
        result = parse_migrant_nonbreeder(buf)
        assert "06890" in result

    # NEGATIVE: non-BytesIO raises TypeError
    def test_raises_type_error_on_wrong_type(self):  # NEGATIVE
        with pytest.raises(TypeError, match="io.BytesIO"):
            parse_migrant_nonbreeder(b"raw bytes")  # type: ignore[arg-type]

    # NEGATIVE: empty buffer raises ValueError
    def test_raises_value_error_on_empty_buffer(self):  # NEGATIVE
        with pytest.raises(ValueError, match="empty"):
            parse_migrant_nonbreeder(io.BytesIO(b""))


# ---------------------------------------------------------------------------
# resolve_target_species — §2.7 set-union resolver
# ---------------------------------------------------------------------------


def _base_species_df() -> pl.DataFrame:
    return parse_species_list(_make_species_buf())


def _base_guilds(tmp_path: Path) -> dict[str, dict[str, str]]:
    return load_guilds_json(_make_guilds_json_file(tmp_path))


def _base_migrant_aous() -> frozenset[str]:
    return parse_migrant_nonbreeder(_make_migrant_buf())


class TestResolveTargetSpecies:
    def test_order_filter_returns_correct_species(self, tmp_path: Path):
        """Anseriformes order → AOU 01770 only (from our 6 species)."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, orders=["Anseriformes"])
        assert "01770" in result
        # Passeriformes species must not be included
        assert "06100" not in result

    def test_family_filter_returns_correct_species(self, tmp_path: Path):
        """Turdidae family → AOU 06100 only."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, families=["Turdidae"])
        assert "06100" in result
        assert "01770" not in result

    def test_guild_foraging_filter(self, tmp_path: Path):
        """foraging_guild=aerial_insectivore → AOU 06640 (Barn Swallow)."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(
            df, guilds, migrants, guild_foraging_guilds=["aerial_insectivore"]
        )
        assert "06640" in result
        assert "06100" not in result

    def test_guild_breeding_habitat_filter(self, tmp_path: Path):
        """breeding_habitat=wetland → AOU 01770."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(
            df, guilds, migrants, guild_breeding_habitats=["wetland"]
        )
        assert "01770" in result

    def test_custom_aou_inclusion(self, tmp_path: Path):
        """Custom AOU 04740 included directly."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, custom_aous=["04740"])
        assert "04740" in result

    def test_migrant_exclusion_applied(self, tmp_path: Path):
        """AOU 06890 (House Sparrow, in migrant set) must be excluded from
        all-species community result."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        # Confirm House Sparrow is indeed in the migrant set
        assert _MIGRANT_AOU in migrants

        # Even when included via Passeriformes order, must be excluded
        result = resolve_target_species(df, guilds, migrants, orders=["Passeriformes"])
        assert _MIGRANT_AOU not in result, (
            "Migrant species 06890 must be excluded from S_target."
        )

    def test_all_species_flag_returns_full_universe_minus_migrants(
        self, tmp_path: Path
    ):
        """--all-species flag selects all SpeciesList AOUs minus migrants."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, all_species=True)
        # 6 species minus 1 migrant = 5
        assert len(result) == 5
        assert _MIGRANT_AOU not in result

    def test_set_union_across_multiple_filters(self, tmp_path: Path):
        """Specifying order AND family should union, not intersect."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(
            df,
            guilds,
            migrants,
            orders=["Anseriformes"],
            families=["Turdidae"],
        )
        assert "01770" in result  # from Anseriformes
        assert "06100" in result  # from Turdidae

    def test_result_is_frozenset(self, tmp_path: Path):
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, all_species=True)
        assert isinstance(result, frozenset)

    def test_result_aou_codes_are_five_digit_strings(self, tmp_path: Path):
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants, all_species=True)
        for code in result:
            assert isinstance(code, str)
            assert len(code) == 5 and code.isdigit(), (
                f"AOU '{code}' is not a 5-digit zero-padded string."
            )

    def test_no_filters_returns_empty_set(self, tmp_path: Path):
        """With no filters and all_species=False, result should be empty."""
        df = _base_species_df()
        guilds = _base_guilds(tmp_path)
        migrants = _base_migrant_aous()

        result = resolve_target_species(df, guilds, migrants)
        assert result == frozenset()

    # NEGATIVE: non-DataFrame raises TypeError
    def test_raises_type_error_on_non_dataframe(self, tmp_path: Path):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            resolve_target_species(
                "not_a_df",  # type: ignore[arg-type]
                {},
                frozenset(),
                all_species=True,
            )

    # NEGATIVE: empty DataFrame raises ValueError
    def test_raises_value_error_on_empty_dataframe(self, tmp_path: Path):  # NEGATIVE
        empty_df = pl.DataFrame(schema=SPECIES_LIST_SCHEMA)
        with pytest.raises(ValueError, match="empty"):
            resolve_target_species(empty_df, {}, frozenset(), all_species=True)


# ---------------------------------------------------------------------------
# filter_species_df
# ---------------------------------------------------------------------------


class TestFilterSpeciesDf:
    def test_filters_to_target_aous(self):
        df = _base_species_df()
        target = frozenset(["01770", "06100"])
        filtered = filter_species_df(df, target)
        assert set(filtered["AOU"].to_list()) == target

    def test_row_count_matches_target(self):
        df = _base_species_df()
        target = frozenset(["03940"])
        filtered = filter_species_df(df, target)
        assert len(filtered) == 1

    # NEGATIVE: non-DataFrame raises TypeError
    def test_raises_type_error_on_wrong_input(self):  # NEGATIVE
        with pytest.raises(TypeError, match="polars.DataFrame"):
            filter_species_df([], frozenset(["01770"]))  # type: ignore[arg-type]

    # NEGATIVE: empty target_aous raises ValueError
    def test_raises_value_error_on_empty_target(self):  # NEGATIVE
        df = _base_species_df()
        with pytest.raises(ValueError, match="non-empty"):
            filter_species_df(df, frozenset())


def test_resolve_target_species_custom_aou_bypasses_migrant_exclusion():
    # Setup minimal species DataFrame
    df = pl.DataFrame(
        {
            "AOU": ["07550", "01234"],
            "Order": ["Passeriformes", "Passeriformes"],
            "Family": ["Turdidae", "Other"],
        }
    )
    guilds = {}
    migrant_aous = frozenset({"07550"})  # Wood Thrush flagged as migrant

    # Explicit custom selection must survive migrant subtraction
    result = resolve_target_species(
        species_df=df,
        guilds=guilds,
        migrant_aous=migrant_aous,
        custom_aous=["07550"],
        all_species=False,
    )
    assert result == frozenset({"07550"})
