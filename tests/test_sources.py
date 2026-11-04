"""Tests for bbs_pipeline.sources: release fetch, typed loading, regions, cache, loader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import polars as pl
import pytest

from bbs_pipeline.contracts import ProtocolConfigContract
from bbs_pipeline.sources import (
    POSTAL_ABBREVIATIONS,
    SPECIES_COLUMNS,
    build_cache,
    cache_is_current,
    fetch_release,
    load_release_tables,
    load_sources,
    parse_species_list_lines,
    parse_state_codes,
    read_text_table,
    resolve_states,
)
from bbs_pipeline.sources.cache import verify_release_files
from bbs_pipeline.sources.release import ReleaseInfo
from tests.release_fixtures import (
    METADATA_XML,
    TEST_DOI,
    fifty_frame,
    release_csv,
    routes_frame,
    species_frame,
    ten_frame,
    weather_frame,
    write_release,
    write_release_json,
)

# StateNum code list exactly as published in the 2026 release metadata (DOI 10.5066/P144YU3S)
REAL_2026_STATE_DOMAIN = (
    "02,Alabama; 03,Alaska; 04,Alberta; 06,Arizona; 07,Arkansas; 11,British Columbia; "
    "14,California; 17,Colorado; 18,Connecticut; 21,Delaware; 25,Florida; 27,Georgia; "
    "33,Idaho; 34,Illinois; 35,Indiana; 36,Iowa; 38,Kansas; 39,Kentucky; 42,Louisiana; "
    "43,Northwest Territories; 44,Maine; 45,Manitoba; 46,Maryland; 47,Massachusetts; "
    "49,Michigan; 50,Minnesota; 51,Mississippi; 52,Missouri; 53,Montana; 54,Nebraska; "
    "55,Nevada; 56,New Brunswick; 57,Newfoundland and Labrador; 58,New Hampshire; "
    "59,New Jersey; 60,New Mexico; 61,New York; 62,Nunavut; 63,North Carolina; "
    "64,North Dakota; 65,Nova Scotia; 66,Ohio; 67,Oklahoma; 68,Ontario; 69,Oregon; "
    "72,Pennsylvania; 75,Prince Edward Island; 76,Quebec; 77,Rhode Island; "
    "79,Saskatchewan; 80,South Carolina; 81,South Dakota; 82,Tennessee; 83,Texas; "
    "85,Utah; 87,Vermont; 88,Virginia; 89,Washington; 90,West Virginia; 91,Wisconsin; "
    "92,Wyoming; 93,Yukon"
)

SAMPLE_SPECIES_LIST_TXT = """\
USGS Patuxent Wildlife Research Center
North American Breeding Bird Survey Species List
   Seq   AOU   English_Common_Name                               French_Common_Name                                Spanish_Common_Name                               Order                               Family                              Genus                               Species
------ ----- -------------------------------------------------- -------------------------------------------------- -------------------------------------------------- ----------------------------------- ----------------------------------- ----------------------------------- -----------------------------------
     1 01770 Black-bellied Whistling-Duck                       Dendrocygne à ventre noir                          Suirirí piquirrojo                                 Anseriformes                        Anatidae                            Dendrocygna                         autumnalis
   610 07550 Wood Thrush                                        Grive des bois                                     Zorzal maculado                                    Passeriformes                       Turdidae                            Hylocichla                          mustelina
"""


def _basic_release(dest: Path, **overrides: object) -> ReleaseInfo:
    tables: dict[str, object] = {
        "routes": routes_frame(
            [(63, 1, "PEÑA CREEK", 35.9, -79.0), (63, 2, "DURHAM", 36.0, -78.9)]
        ),
        "weather": weather_frame([(1001, 63, 1, 2021, 5, 1), (1002, 63, 2, 2021, 6, 1)]),
        "species": species_frame([("7550", "Wood Thrush", "Passeriformes", "Turdidae")]),
        "fifty_stop": fifty_frame([(1001, 63, 1, 2021, "7550", {1: 3, 7: 2})]),
        "ten_stop": ten_frame([(1001, 63, 1, 2021, "7550", [5, 0, 0, 0, 0], 2)]),
        "migrants_fifty": fifty_frame([(1001, 63, 1, 2021, "4661", {2: 9})]),
        "migrants_ten": ten_frame([(1001, 63, 1, 2021, "4661", [9, 0, 0, 0, 0], 1)]),
    }
    tables.update(overrides)
    return write_release(dest, **tables)  # type: ignore[arg-type]


# --- Reading and typing -------------------------------------------------------------


def test_read_text_table_strips_padding_and_nulls() -> None:
    data = "A,B,C\n6   ,NULL,PE\xd1A\n  7,,x\n".encode("cp1252")

    df = read_text_table(data)

    assert df.schema == {"A": pl.String, "B": pl.String, "C": pl.String}
    assert df["A"].to_list() == ["6", "7"]
    assert df["B"].to_list() == [None, None]
    assert df["C"].to_list() == ["PEÑA", "x"]


def test_release_tables_are_typed_explicitly(tmp_path: Path) -> None:
    _basic_release(tmp_path)

    t = load_release_tables(tmp_path)

    assert t["routes"].schema["StateNum"] == pl.Int64
    assert t["routes"].schema["Latitude"] == pl.Float64
    assert t["routes"]["RouteName"].to_list() == ["PEÑA CREEK", "DURHAM"]
    assert t["weather"]["StartTime"].to_list() == ["0530", "0530"]  # leading zero kept
    assert t["weather"].schema["StartTemp"] == pl.Int64
    assert t["fifty_stop"]["AOU"].to_list() == ["07550"]
    assert t["species"]["French_Common_Name"].to_list() == ["Grive à dos olive"]
    assert t["regions"].height == 5


def test_null_tokens_become_null_and_tempscale_is_upper_cased(tmp_path: Path) -> None:
    weather = weather_frame([(1001, 63, 1, 2021, 5, 1)]).with_columns(
        pl.lit(None, dtype=pl.Int64).alias("StartTemp"), pl.lit("c").alias("TempScale")
    )
    _basic_release(tmp_path, weather=weather)

    row = load_release_tables(tmp_path)["weather"].row(0, named=True)

    assert row["StartTemp"] is None
    assert row["TempScale"] == "C"


def test_migrant_files_are_kept_out_of_observation_tables(tmp_path: Path) -> None:
    _basic_release(tmp_path)

    t = load_release_tables(tmp_path)

    assert t["fifty_stop"]["AOU"].to_list() == ["07550"]
    assert t["ten_stop"]["AOU"].to_list() == ["07550"]
    assert t["migrants_fifty"]["AOU"].to_list() == ["04661"]
    assert t["migrants_ten"]["AOU"].to_list() == ["04661"]


def test_nested_zip_and_fixed_width_species_list(tmp_path: Path) -> None:
    write_release(
        tmp_path,
        routes=routes_frame([(63, 1, "R", 35.9, -79.0)]),
        weather=weather_frame([(1001, 63, 1, 2021, 5, 1)]),
        species_txt=SAMPLE_SPECIES_LIST_TXT,
        fifty_stop=fifty_frame([(1001, 63, 1, 2021, "7550", {1: 3})]),
        nest_fifty_zip=True,
    )

    t = load_release_tables(tmp_path)

    assert t["fifty_stop"].height == 1
    assert t["species"]["AOU"].to_list() == ["01770", "07550"]
    assert t["species"].schema["Seq"] == pl.Int64


def test_bad_integer_value_raises_naming_the_column(tmp_path: Path) -> None:
    weather = weather_frame([(1001, 63, 1, 2021, 5, 1)]).with_columns(
        pl.lit("ten").alias("StartWind")
    )
    _basic_release(tmp_path, weather=weather)

    with pytest.raises(ValueError, match=r"weather\)\.StartWind.*\['ten'\]"):
        load_release_tables(tmp_path)


def test_missing_required_column_raises(tmp_path: Path) -> None:
    _basic_release(tmp_path, weather=weather_frame([(1001, 63, 1, 2021, 5, 1)]).drop("RunType"))

    with pytest.raises(ValueError, match=r"missing required columns \['RunType'\]"):
        load_release_tables(tmp_path)


def test_missing_required_file_raises(tmp_path: Path) -> None:
    _basic_release(tmp_path)
    (tmp_path / "Weather.csv").unlink()

    with pytest.raises(FileNotFoundError, match="weather"):
        load_release_tables(tmp_path)


def test_route_state_missing_from_metadata_raises(tmp_path: Path) -> None:
    _basic_release(tmp_path, routes=routes_frame([(99, 1, "NOWHERE", 35.9, -79.0)]))

    with pytest.raises(ValueError, match=r"missing from the metadata: \[99\]"):
        load_release_tables(tmp_path)


def test_parse_species_list_fixed_width() -> None:
    df = parse_species_list_lines(SAMPLE_SPECIES_LIST_TXT)

    assert df.columns == SPECIES_COLUMNS
    wood_thrush = df.filter(pl.col("AOU") == "07550").row(0, named=True)
    assert wood_thrush["Seq"] == 610
    assert wood_thrush["Spanish_Common_Name"] == "Zorzal maculado"
    assert wood_thrush["Species"] == "mustelina"


def test_parse_species_list_fallback_without_dash_line() -> None:
    simplified = """\
Header Banner Line
1  7550  Wood Thrush  Grive des bois  Zorzal maculado  Passeriformes  Turdidae  Hylocichla  mustelina
2  6740  Ovenbird     Paruline couronnee  Reinita coronada  Passeriformes  Parulidae  Seiurus  aurocapilla
"""
    df = parse_species_list_lines(simplified)

    assert df["AOU"].to_list() == ["07550", "06740"]


# --- Regions ------------------------------------------------------------------------


def _real_domain_xml(tmp_path: Path) -> Path:
    path = tmp_path / "meta.xml"
    path.write_text(
        f"<metadata><attr><attrlabl>StateNum</attrlabl><attrdomv><udom>{REAL_2026_STATE_DOMAIN}"
        "</udom></attrdomv></attr></metadata>",
        encoding="utf-8",
    )
    return path


def test_state_codes_come_from_release_metadata(tmp_path: Path) -> None:
    codes = parse_state_codes(_real_domain_xml(tmp_path))

    lookup = dict(codes.select("StateName", "StateNum").iter_rows())
    assert codes.height == 62
    assert codes["StateNum"].is_unique().all()
    # The v2 hand-typed table got these wrong (NS and ON both 68; NT and NU both 65)
    assert lookup["Nova Scotia"] == 65
    assert lookup["Ontario"] == 68
    assert lookup["Northwest Territories"] == 43
    assert lookup["Nunavut"] == 62


def test_postal_abbreviations_cover_exactly_the_release_regions(tmp_path: Path) -> None:
    names = set(parse_state_codes(_real_domain_xml(tmp_path))["StateName"].to_list())

    assert set(POSTAL_ABBREVIATIONS.values()) == names


def test_duplicate_state_code_raises(tmp_path: Path) -> None:
    path = tmp_path / "dup.xml"
    path.write_text(
        "<m><attr><attrlabl>StateNum</attrlabl><attrdomv><udom>65,Nova Scotia; 65,Ontario"
        "</udom></attrdomv></attr></m>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"Duplicate StateNum values.*\[65\]"):
        parse_state_codes(path)


def test_resolve_states_accepts_codes_names_and_abbreviations(tmp_path: Path) -> None:
    codes = parse_state_codes(_real_domain_xml(tmp_path))

    assert resolve_states([63, "virginia", "NS", " on "], codes) == [63, 65, 68, 88]


def test_resolve_states_rejects_unknown_values(tmp_path: Path) -> None:
    codes = parse_state_codes(_real_domain_xml(tmp_path))

    with pytest.raises(ValueError, match=r"Unknown state\(s\) \['ZZ', '99'\]"):
        resolve_states(["ZZ", 99, "NC"], codes)


def test_state_names_without_metadata_raise() -> None:
    assert resolve_states([63], None) == [63]
    with pytest.raises(ValueError, match="use StateNum codes"):
        resolve_states(["NC"], None)


# --- Fetch --------------------------------------------------------------------------


def _mock_sciencebase(files: dict[str, bytes], md5_override: dict[str, str] | None = None):
    item_id = "b" * 24
    calls: list[str] = []
    item = {
        "id": item_id,
        "title": "2099 Release - BBS",
        "identifiers": [{"type": "DOI", "key": f"doi:{TEST_DOI}"}],
        "dates": [{"type": "Publication", "dateString": "2099-05-01"}],
        "files": [
            {
                "name": name,
                "url": f"https://sb.test/file/{name}",
                "checksum": {
                    "type": "MD5",
                    "value": (md5_override or {}).get(
                        name, hashlib.md5(data, usedforsecurity=False).hexdigest()
                    ),
                },
            }
            for name, data in files.items()
        ]
        + [{"name": "RunType.pdf", "url": "https://sb.test/file/RunType.pdf"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.startswith("https://doi.org/"):
            return httpx.Response(
                302, headers={"location": f"https://www.sciencebase.gov/catalog/item/{item_id}"}
            )
        if f"/catalog/item/{item_id}" in url:
            return httpx.Response(200, json=item)
        name = unquote(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=files[name])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls, item_id


def test_fetch_release_downloads_wanted_files_and_records_provenance(tmp_path: Path) -> None:
    files = {"Routes.csv": b"a,b\n1,2\n", "States.zip": b"zipbytes", "meta.xml": b"<m/>"}
    client, calls, item_id = _mock_sciencebase(files)

    info = fetch_release(TEST_DOI, tmp_path, client=client)

    assert info.item_id == item_id
    assert info.doi == TEST_DOI
    assert info.publication_date == "2099-05-01"
    assert sorted(f.name for f in info.files) == ["Routes.csv", "States.zip", "meta.xml"]
    assert not (tmp_path / "RunType.pdf").exists()
    assert (tmp_path / "Routes.csv").read_bytes() == files["Routes.csv"]
    assert ReleaseInfo.load(tmp_path) == info
    assert info.matches(f"https://doi.org/{TEST_DOI.lower()}")
    assert info.matches(item_id)


def test_fetch_release_keeps_files_whose_md5_already_matches(tmp_path: Path) -> None:
    files = {"Routes.csv": b"a,b\n1,2\n", "Weather.csv": b"w\n"}
    (tmp_path / "Routes.csv").write_bytes(files["Routes.csv"])
    client, calls, _ = _mock_sciencebase(files)

    fetch_release(TEST_DOI, tmp_path, client=client)

    downloads = [c for c in calls if "sb.test/file/" in c]
    assert downloads == ["https://sb.test/file/Weather.csv"]


def test_fetch_release_md5_mismatch_raises_and_discards_file(tmp_path: Path) -> None:
    client, _, _ = _mock_sciencebase({"Routes.csv": b"a\n"}, md5_override={"Routes.csv": "0" * 32})

    with pytest.raises(ValueError, match="MD5 mismatch for Routes.csv"):
        fetch_release(TEST_DOI, tmp_path, client=client)
    assert not (tmp_path / "Routes.csv").exists()
    assert not (tmp_path / "release.json").exists()


def test_fetch_release_rejects_item_that_does_not_list_the_doi(tmp_path: Path) -> None:
    client, _, _ = _mock_sciencebase({"Routes.csv": b"a\n"})

    with pytest.raises(LookupError, match="does not list DOI 10.5066/OTHER"):
        fetch_release("10.5066/OTHER", tmp_path, client=client)


# --- Cache and loader -----------------------------------------------------------------


def test_build_cache_writes_typed_parquet_and_manifest(tmp_path: Path) -> None:
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    _basic_release(raw)
    cache.mkdir()
    pl.DataFrame({"x": [1]}).write_parquet(cache / "old_release_table.parquet")

    manifest = build_cache(raw, cache)

    assert not (cache / "old_release_table.parquet").exists()
    assert manifest["release"]["doi"] == TEST_DOI
    assert manifest["tables"]["routes"]["rows"] == 2
    assert pl.read_parquet(cache / "weather.parquet").schema["Year"] == pl.Int64
    assert cache_is_current(cache, raw)


def test_cache_is_stale_when_release_json_changes(tmp_path: Path) -> None:
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    _basic_release(raw)
    build_cache(raw, cache)

    write_release_json(raw, doi="10.5066/NEWER")

    assert not cache_is_current(cache, raw)


def test_changed_release_file_is_detected(tmp_path: Path) -> None:
    _basic_release(tmp_path)
    (tmp_path / "Routes.csv").write_bytes(release_csv(routes_frame([(63, 1, "EDITED", 1.0, 1.0)])))

    with pytest.raises(ValueError, match="Routes.csv' no longer matches release.json"):
        verify_release_files(tmp_path, ReleaseInfo.load(tmp_path))


def _protocol(raw: Path, cache: Path, **kw: object) -> ProtocolConfigContract:
    return ProtocolConfigContract(
        raw_data_dir=raw, sanitized_cache_dir=cache, sciencebase_doi=TEST_DOI, **kw
    )


def test_load_sources_builds_then_reuses_then_rebuilds_on_request(tmp_path: Path) -> None:
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    _basic_release(raw)

    first = load_sources(_protocol(raw, cache, offline=True))
    second = load_sources(_protocol(raw, cache, offline=True))
    forced = load_sources(_protocol(raw, cache, offline=True, force_resanitize=True))

    assert first.source == "built_from_release"
    assert second.source == "cache"
    assert forced.source == "built_from_release"
    assert set(second.tables) >= {"routes", "weather", "species", "fifty_stop", "regions"}


def test_load_sources_offline_without_release_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="offline mode is on; run `bbs fetch"):
        load_sources(_protocol(tmp_path / "raw", tmp_path / "cache", offline=True))


def test_load_sources_rejects_a_different_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _basic_release(raw)
    protocol = _protocol(raw, tmp_path / "cache").model_copy(
        update={"sciencebase_doi": "10.5066/P144YU3S"}
    )

    with pytest.raises(ValueError, match="configuration asks for '10.5066/P144YU3S'"):
        load_sources(protocol)


def test_load_sources_fetches_when_release_is_absent(tmp_path: Path) -> None:
    built = tmp_path / "built"
    _basic_release(built)
    files = {p.name: p.read_bytes() for p in built.iterdir() if p.name != "release.json"}
    client, _, _ = _mock_sciencebase(files)
    raw, cache = tmp_path / "raw", tmp_path / "cache"

    result = load_sources(_protocol(raw, cache), http_client=client)

    assert result.source == "fetched"
    assert json.loads((raw / "release.json").read_text())["doi"] == TEST_DOI
    assert result.tables["routes"].collect().height == 2


def test_metadata_xml_fixture_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "m.xml"
    path.write_text(METADATA_XML, encoding="utf-8")

    assert parse_state_codes(path).height == 5
