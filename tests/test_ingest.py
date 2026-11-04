"""Unit tests for bbs_pipeline.ingest (Species Normalizer, Role Resolver, Three-Tier Gate)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from bbs_pipeline.contracts import IngressMode, ProtocolConfigContract
from bbs_pipeline.ingest import (
    SPECIES_COLUMNS,
    TableRoleResolver,
    ThreeTierIngressGate,
    load_resolved_tables,
    normalize_species_list_file,
    parse_species_list_lines,
    resolve_table_roles,
)

SAMPLE_SPECIES_LIST_TXT = """\
USGS Patuxent Wildlife Research Center
North American Breeding Bird Survey Species List
======================================================================================================================================================
   Seq   AOU   English_Common_Name                               French_Common_Name                                Spanish_Common_Name                               Order                               Family                              Genus                               Species
------ ----- -------------------------------------------------- -------------------------------------------------- -------------------------------------------------- ----------------------------------- ----------------------------------- ----------------------------------- -----------------------------------
     1 01770 Black-bellied Whistling-Duck                       Dendrocygne à ventre noir                          Suirirí piquirrojo                                 Anseriformes                        Anatidae                            Dendrocygna                         autumnalis
   610 07550 Wood Thrush                                        Grive des bois                                     Zorzal maculado                                    Passeriformes                       Turdidae                            Hylocichla                          mustelina
   645 06740 Ovenbird                                           Paruline couronnée                                 Reinita coronada                                   Passeriformes                       Parulidae                           Seiurus                             aurocapilla
   681 06080 Scarlet Tanager                                    Piranga écarlate                                   Piranga escarlata                                  Passeriformes                       Cardinalidae                        Piranga                             olivacea
"""


def _make_fifty_stop_df(state_num: int, route: int, aou: int, year: int = 2022) -> pl.DataFrame:
    data: dict[str, list[Any]] = {
        "RouteDataID": [state_num * 10000 + route],
        "CountryNum": [840],
        "StateNum": [state_num],
        "Route": [route],
        "RPID": [101],
        "Year": [year],
        "AOU": [aou],
    }
    for s in range(1, 51):
        data[f"Stop{s}"] = [1 if s in (1, 10, 50) else 0]
    return pl.DataFrame(data)


def _make_ten_stop_df(state_num: int = 63, route: int = 1, aou: int = 7550) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "RouteDataID": [630001],
            "CountryNum": [840],
            "StateNum": [state_num],
            "Route": [route],
            "RPID": [101],
            "Year": [1985],
            "AOU": [aou],
            "Count10": [2],
            "Count20": [1],
            "Count30": [0],
            "Count40": [3],
            "Count50": [1],
            "StopTotal": [4],
            "SpeciesTotal": [7],
        }
    )


def _make_weather_df(state_num: int = 63, route: int = 1, year: int = 2022) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "RouteDataID": [state_num * 10000 + route],
            "CountryNum": [840],
            "StateNum": [state_num],
            "Route": [route],
            "RPID": [101],
            "Year": [year],
            "Month": [6],
            "Day": [12],
            "ObsN": [10042],
            "TotalSpp": [45],
            "StartTemp": [62],
            "EndTemp": [75],
            "StartWind": [1],
            "EndWind": [2],
            "StartSky": [0],
            "EndSky": [1],
            "StartTime": [530],
            "EndTime": [945],
            "Assistant": [0],
            "QualityCurrentID": [1],
            "RunType": [1],
        }
    )


def _make_routes_df(state_num: int = 63, route: int = 1) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "CountryNum": [840],
            "StateNum": [state_num],
            "Route": [route],
            "RouteName": ["CHAPEL HILL"],
            "Active": [1],
            "Latitude": [35.9132],
            "Longitude": [-79.0558],
            "Stratum": [11],
            "BCR": [27],
            "RouteTypeID": [1],
            "RouteTypeDetailID": [1],
        }
    )


def test_normalize_species_list_fixed_width(tmp_path: Path) -> None:
    """Verify fixed-width SpeciesList.txt parsing, header skipping, and zero-padded AOU."""
    txt_path = tmp_path / "SpeciesList.txt"
    csv_path = tmp_path / "species_list.csv"
    txt_path.write_text(SAMPLE_SPECIES_LIST_TXT, encoding="utf-8")

    df = normalize_species_list_file(txt_path, csv_path)
    assert csv_path.is_file()
    assert df.columns == SPECIES_COLUMNS
    assert df.height == 4

    aous = df["AOU"].to_list()
    assert aous == ["01770", "07550", "06740", "06080"]

    wood_thrush = df.filter(pl.col("AOU") == "07550").row(0, named=True)
    assert wood_thrush["Seq"] == 610
    assert wood_thrush["English_Common_Name"] == "Wood Thrush"
    assert wood_thrush["French_Common_Name"] == "Grive des bois"
    assert wood_thrush["Spanish_Common_Name"] == "Zorzal maculado"
    assert wood_thrush["Order"] == "Passeriformes"
    assert wood_thrush["Family"] == "Turdidae"
    assert wood_thrush["Genus"] == "Hylocichla"
    assert wood_thrush["Species"] == "mustelina"


def test_parse_species_list_fallback_without_dash_line() -> None:
    """Verify fallback multi-space parser when dashed underline spans are absent."""
    simplified = """\
Header Banner Line
1  7550  Wood Thrush  Grive des bois  Zorzal maculado  Passeriformes  Turdidae  Hylocichla  mustelina
2  6740  Ovenbird     Paruline couronnee  Reinita coronada  Passeriformes  Parulidae  Seiurus  aurocapilla
"""
    df = parse_species_list_lines(simplified)
    assert df.height == 2
    assert df["AOU"].to_list() == ["07550", "06740"]
    assert df["English_Common_Name"].to_list() == ["Wood Thrush", "Ovenbird"]


def test_table_role_resolver_obfuscated_filenames(tmp_path: Path) -> None:
    """Verify TableRoleResolver binds all 5 roles from obfuscated filenames and normalizes keys."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Write 5 tables with completely obfuscated filenames and unpadded integer keys
    _make_fifty_stop_df(state_num=63, route=1, aou=7550).write_parquet(cache_dir / "alpha.parquet")
    _make_ten_stop_df(state_num=4, route=12, aou=6740).write_parquet(cache_dir / "beta.parquet")
    _make_weather_df(state_num=63, route=1).write_parquet(cache_dir / "gamma.parquet")
    _make_routes_df(state_num=63, route=1).write_parquet(cache_dir / "delta.parquet")

    # Species table using alternative 'EnglishName' column to test canonical aliasing
    species_df = pl.DataFrame(
        {
            "Seq": [610],
            "AOU": [7550],
            "EnglishName": ["Wood Thrush"],
            "Order": ["Passeriformes"],
            "Family": ["Turdidae"],
            "Genus": ["Hylocichla"],
            "Species": ["mustelina"],
        }
    )
    species_df.write_parquet(cache_dir / "epsilon.parquet")

    resolver = TableRoleResolver(cache_dir)
    assert resolver.resolve() == resolve_table_roles(cache_dir)
    resolved_paths = resolve_table_roles(cache_dir)
    assert set(resolved_paths.keys()) == {"fifty_stop", "ten_stop", "weather", "routes", "species"}
    assert resolved_paths["fifty_stop"].name == "alpha.parquet"
    assert resolved_paths["ten_stop"].name == "beta.parquet"
    assert resolved_paths["weather"].name == "gamma.parquet"
    assert resolved_paths["routes"].name == "delta.parquet"
    assert resolved_paths["species"].name == "epsilon.parquet"

    tables = load_resolved_tables(cache_dir)
    fifty_df = tables["fifty_stop"].collect()
    assert "route_id" in fifty_df.columns
    assert fifty_df["route_id"].to_list() == ["63_001"]
    assert fifty_df["AOU"].to_list() == ["07550"]

    ten_df = tables["ten_stop"].collect()
    assert ten_df["route_id"].to_list() == ["04_012"]
    assert ten_df["AOU"].to_list() == ["06740"]

    weather_df = tables["weather"].collect()
    assert weather_df["route_id"].to_list() == ["63_001"]

    routes_df = tables["routes"].collect()
    assert routes_df["route_id"].to_list() == ["63_001"]

    sp_df = tables["species"].collect()
    assert sp_df["AOU"].to_list() == ["07550"]
    assert "English_Common_Name" in sp_df.columns
    assert sp_df["English_Common_Name"].to_list() == ["Wood Thrush"]


def test_tier1_cache_hit_and_cache_only_errors(tmp_path: Path) -> None:
    """Verify Tier 1 fast-path cache hit and CACHE_ONLY error enforcement."""
    cache_dir = tmp_path / "sanitized_cache"
    raw_dir = tmp_path / "raw_empty"
    cache_dir.mkdir()

    _make_routes_df(63, 1).write_parquet(cache_dir / "routes.parquet")
    _make_weather_df(63, 1).write_parquet(cache_dir / "weather.parquet")
    manifest = {
        "engine": "intake_engine",
        "status": "healthy",
        "quarantine_enabled": True,
        "tables": [
            {"filename": "routes.parquet", "row_count": 1},
            {"filename": "weather.parquet", "row_count": 1},
        ],
    }
    (cache_dir / "intake_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    protocol = ProtocolConfigContract(
        ingress_mode=IngressMode.AUTO_THREE_TIER,
        sanitized_cache_dir=cache_dir,
        raw_data_dir=raw_dir,
    )
    gate = ThreeTierIngressGate(protocol)
    result = gate.execute()

    assert result.tier_used == "tier1_cache"
    assert result.cache_dir == cache_dir
    assert "routes" in result.tables
    assert "weather" in result.tables
    assert result.tables["routes"].collect()["route_id"].to_list() == ["63_001"]

    # CACHE_ONLY on missing directory raises FileNotFoundError
    missing_protocol = ProtocolConfigContract(
        ingress_mode=IngressMode.CACHE_ONLY,
        sanitized_cache_dir=tmp_path / "nonexistent_cache",
        raw_data_dir=raw_dir,
    )
    with pytest.raises(FileNotFoundError):
        ThreeTierIngressGate(missing_protocol).execute()


def _populate_raw_nested_archives(raw_dir: Path) -> None:
    """Populate raw_dir with nested 50-stop state zips, 10-stop, routes, weather, and SpeciesList.txt."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "SpeciesList.txt").write_text(SAMPLE_SPECIES_LIST_TXT, encoding="utf-8")

    # Create nested 50-stop zip containing two state zip archives
    inner_nc_bytes = io.BytesIO()
    with zipfile.ZipFile(inner_nc_bytes, "w") as zf_nc:
        zf_nc.writestr(
            "NC_63.csv",
            _make_fifty_stop_df(state_num=63, route=1, aou=7550).write_csv(),
        )

    inner_va_bytes = io.BytesIO()
    with zipfile.ZipFile(inner_va_bytes, "w") as zf_va:
        zf_va.writestr(
            "VA_88.csv",
            _make_fifty_stop_df(state_num=88, route=5, aou=6740).write_csv(),
        )

    with zipfile.ZipFile(raw_dir / "50-StopData.zip", "w") as outer_zf:
        outer_zf.writestr("state_nc.zip", inner_nc_bytes.getvalue())
        outer_zf.writestr("state_va.zip", inner_va_bytes.getvalue())

    with zipfile.ZipFile(raw_dir / "Routes.zip", "w") as zf_routes:
        routes_combined = pl.concat([_make_routes_df(63, 1), _make_routes_df(88, 5)])
        zf_routes.writestr("routes.csv", routes_combined.write_csv())

    with zipfile.ZipFile(raw_dir / "Weather.zip", "w") as zf_weather:
        weather_combined = pl.concat([_make_weather_df(63, 1), _make_weather_df(88, 5)])
        zf_weather.writestr("weather.csv", weather_combined.write_csv())

    _make_ten_stop_df(63, 1, 7550).write_csv(raw_dir / "10StopData.csv")


def test_tier2_local_raw_nested_zips_and_cache_rebuild(tmp_path: Path) -> None:
    """Verify Tier 2 unpacks nested zips, concatenates state chunks, and rewrites the cache."""
    raw_dir = tmp_path / "raw"
    cache_dir = tmp_path / "sanitized_cache"
    _populate_raw_nested_archives(raw_dir)

    # A valid cache from an older build: a forced rebuild must replace it, not reuse it
    cache_dir.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(cache_dir / "old_release_table.parquet")
    (cache_dir / "intake_manifest.json").write_text('{"tables": []}', encoding="utf-8")

    protocol = ProtocolConfigContract(
        ingress_mode=IngressMode.AUTO_THREE_TIER,
        sanitized_cache_dir=cache_dir,
        raw_data_dir=raw_dir,
        force_resanitize=True,
    )
    gate = ThreeTierIngressGate(protocol)
    result = gate.execute()

    assert result.tier_used == "tier2_local_raw"
    assert not (cache_dir / "old_release_table.parquet").exists()
    manifest = json.loads((cache_dir / "intake_manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == "polars_builtin"

    # Verify all 5 roles resolved and partitioned 50-stop chunks were concatenated
    assert set(result.tables.keys()) == {"fifty_stop", "ten_stop", "weather", "routes", "species"}
    fifty_df = result.tables["fifty_stop"].collect().sort("StateNum")
    assert fifty_df.height == 2
    assert fifty_df["route_id"].to_list() == ["63_001", "88_005"]
    assert fifty_df["AOU"].to_list() == ["07550", "06740"]

    species_df = result.tables["species"].collect()
    assert species_df.height == 4
    assert "07550" in species_df["AOU"].to_list()


def test_tier3_sciencebase_stream_and_failure_raises(tmp_path: Path) -> None:
    """Verify the Tier 3 ScienceBase streamer, and that a download failure raises."""
    cache_dir = tmp_path / "sb_cache"
    raw_dir = tmp_path / "empty_raw"

    # Build in-memory zip payload for Routes.zip
    routes_zip_buf = io.BytesIO()
    with zipfile.ZipFile(routes_zip_buf, "w") as zf:
        zf.writestr("routes.csv", _make_routes_df(63, 1).write_csv())

    weather_zip_buf = io.BytesIO()
    with zipfile.ZipFile(weather_zip_buf, "w") as zf:
        zf.writestr("weather.csv", _make_weather_df(63, 1).write_csv())

    fifty_zip_buf = io.BytesIO()
    with zipfile.ZipFile(fifty_zip_buf, "w") as zf:
        zf.writestr("fifty.csv", _make_fifty_stop_df(63, 1, 7550).write_csv())

    catalog_payload = {
        "items": [
            {
                "id": "sb-item-1",
                "files": [
                    {"name": "Routes.zip", "url": "https://sb.test/Routes.zip"},
                    {"name": "Weather.zip", "url": "https://sb.test/Weather.zip"},
                    {"name": "50-StopData.zip", "url": "https://sb.test/50-StopData.zip"},
                    {"name": "SpeciesList.txt", "url": "https://sb.test/SpeciesList.txt"},
                ],
            }
        ]
    }

    class FakeResponse:
        def __init__(self, json_data: Any = None, content: bytes = b"") -> None:
            self._json = json_data
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self._json

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str) -> FakeResponse:
            self.calls.append(url)
            if "catalog/items" in url:
                return FakeResponse(json_data=catalog_payload)
            if url.endswith("Routes.zip"):
                return FakeResponse(content=routes_zip_buf.getvalue())
            if url.endswith("Weather.zip"):
                return FakeResponse(content=weather_zip_buf.getvalue())
            if url.endswith("50-StopData.zip"):
                return FakeResponse(content=fifty_zip_buf.getvalue())
            if url.endswith("SpeciesList.txt"):
                return FakeResponse(content=SAMPLE_SPECIES_LIST_TXT.encode("utf-8"))
            raise RuntimeError(f"Unexpected URL: {url}")

    fake_client = FakeHttpClient()
    protocol = ProtocolConfigContract(
        ingress_mode=IngressMode.SCIENCEBASE_STREAM,
        sanitized_cache_dir=cache_dir,
        raw_data_dir=raw_dir,
        http_max_retries=1,
        http_backoff_seconds=0.0,
    )
    gate = ThreeTierIngressGate(protocol, http_client=fake_client)
    res = gate.execute()

    assert res.tier_used == "tier3_sciencebase_stream"
    assert len(fake_client.calls) == 5
    assert {"fifty_stop", "weather", "routes", "species"}.issubset(set(res.tables.keys()))

    # A network failure in SCIENCEBASE_STREAM mode raises; it never falls back to the
    # cache written above, which may be from an older release
    class FailingHttpClient:
        def get(self, url: str) -> Any:
            raise ConnectionError(f"Simulated network outage for {url}")

    failing_gate = ThreeTierIngressGate(protocol, http_client=FailingHttpClient())
    with pytest.raises(ConnectionError, match="Simulated network outage"):
        failing_gate.execute()


def test_malformed_cache_manifest_raises(tmp_path: Path) -> None:
    (tmp_path / "intake_manifest.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed cache manifest"):
        resolve_table_roles(tmp_path)
