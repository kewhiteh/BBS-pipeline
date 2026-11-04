"""Write small release directories shaped like real BBS releases, for tests.

Values are space-padded, missing values are the literal ``NULL``, text is Windows-1252,
observation tables sit inside zip folders, and ``release.json`` records checksums.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import polars as pl

from bbs_pipeline.sources.release import ReleaseFile, ReleaseInfo

TEST_DOI = "10.5066/TESTREL1"
TEST_ITEM_ID = "a" * 24

METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<metadata><eainfo>
  <detailed><enttyp><enttypl>Routes.csv</enttypl></enttyp>
    <attr><attrlabl>StateNum</attrlabl>
      <attrdomv><udom>02,Alabama; 63,North Carolina; 65,Nova Scotia; 68,Ontario; 88,Virginia</udom></attrdomv>
    </attr>
  </detailed>
  <detailed><enttyp><enttypl>Weather.csv</enttypl></enttyp>
    <attr><attrlabl>StateNum</attrlabl>
      <attrdomv><udom>02,Alabama; 63,North Carolina; 65,Nova Scotia; 68,Ontario; 88,Virginia</udom></attrdomv>
    </attr>
  </detailed>
</eainfo></metadata>
"""


def release_csv(df: pl.DataFrame) -> bytes:
    """Render a frame the way BBS files look: padded values, NULL for missing, cp1252."""
    padded = df.with_columns(
        [(pl.col(c).cast(pl.String) + "  ").fill_null("NULL").alias(c) for c in df.columns]
    )
    return padded.write_csv().encode("cp1252")


def _zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def write_release_json(dest: Path, doi: str = TEST_DOI, item_id: str = TEST_ITEM_ID) -> ReleaseInfo:
    """Record every file in ``dest`` in release.json, as `bbs fetch` would."""
    files = []
    for path in sorted(p for p in dest.iterdir() if p.is_file() and p.name != "release.json"):
        data = path.read_bytes()
        files.append(
            ReleaseFile(
                name=path.name,
                size=len(data),
                md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    info = ReleaseInfo(
        item_id=item_id,
        doi=doi,
        title="Test Release - BBS fixture",
        publication_date="2026-01-01",
        fetched_at="2026-01-01T00:00:00+00:00",
        files=files,
    )
    info.save(dest)
    return info


def write_release(
    dest: Path,
    *,
    routes: pl.DataFrame,
    weather: pl.DataFrame,
    species: pl.DataFrame | None = None,
    species_txt: str | None = None,
    fifty_stop: pl.DataFrame | None = None,
    ten_stop: pl.DataFrame | None = None,
    migrants_fifty: pl.DataFrame | None = None,
    migrants_ten: pl.DataFrame | None = None,
    metadata_xml: str | None = METADATA_XML,
    nest_fifty_zip: bool = False,
    doi: str = TEST_DOI,
) -> ReleaseInfo:
    """Write a release directory with the given tables and its release.json."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "Routes.csv").write_bytes(release_csv(routes))
    (dest / "Weather.csv").write_bytes(release_csv(weather))
    if species is not None:
        (dest / "SpeciesList.csv").write_bytes(release_csv(species))
    if species_txt is not None:
        (dest / "SpeciesList.txt").write_bytes(species_txt.encode("cp1252"))
    if fifty_stop is not None:
        member = release_csv(fifty_stop)
        if nest_fifty_zip:
            inner = dest / "_inner.zip"
            _zip(inner, {"Fifty1.csv": member})
            _zip(dest / "50-StopData.zip", {"50-StopData/Fifty1.zip": inner.read_bytes()})
            inner.unlink()
        else:
            _zip(dest / "50-StopData.zip", {"50-StopData/Fifty1.csv": member})
    if ten_stop is not None:
        _zip(dest / "States.zip", {"States/NCarolin.csv": release_csv(ten_stop)})
    migrant_members: dict[str, bytes] = {}
    if migrants_fifty is not None:
        migrant_members["MigrantNonBreeder/Migrants.csv"] = release_csv(migrants_fifty)
    if migrants_ten is not None:
        migrant_members["MigrantNonBreeder/MigrantSummary.csv"] = release_csv(migrants_ten)
    if migrant_members:
        _zip(dest / "MigrantNonBreeder.zip", migrant_members)
    if metadata_xml is not None:
        (dest / "BBS Dataset (test).xml").write_text(metadata_xml, encoding="utf-8")
    return write_release_json(dest, doi=doi)


def routes_frame(rows: list[tuple[int, int, str, float | None, float | None]]) -> pl.DataFrame:
    """(StateNum, Route, RouteName, Latitude, Longitude) rows -> a routes.csv frame."""
    return pl.DataFrame(
        {
            "CountryNum": [840] * len(rows),
            "StateNum": [r[0] for r in rows],
            "Route": [r[1] for r in rows],
            "RouteName": [r[2] for r in rows],
            "Active": [1] * len(rows),
            "Latitude": [r[3] for r in rows],
            "Longitude": [r[4] for r in rows],
            "Stratum": [11] * len(rows),
            "BCR": [29] * len(rows),
            "RouteTypeID": [1] * len(rows),
            "RouteTypeDetailID": [1] * len(rows),
        },
        schema_overrides={"Latitude": pl.Float64, "Longitude": pl.Float64},
    )


def weather_frame(rows: list[tuple[int, int, int, int, int, int]]) -> pl.DataFrame:
    """(RouteDataID, StateNum, Route, Year, ObsN, RunType) rows -> a weather.csv frame."""
    n = len(rows)
    return pl.DataFrame(
        {
            "RouteDataID": [r[0] for r in rows],
            "CountryNum": [840] * n,
            "StateNum": [r[1] for r in rows],
            "Route": [r[2] for r in rows],
            "RPID": [101] * n,
            "Year": [r[3] for r in rows],
            "Month": [6] * n,
            "Day": [10] * n,
            "ObsN": [r[4] for r in rows],
            "TotalSpp": [40] * n,
            "StartTemp": [62] * n,
            "EndTemp": [75] * n,
            "TempScale": ["F"] * n,
            "StartWind": [1] * n,
            "EndWind": [2] * n,
            "StartSky": [0] * n,
            "EndSky": [1] * n,
            "StartTime": ["0530"] * n,
            "EndTime": ["0945"] * n,
            "Assistant": [0] * n,
            "QualityCurrentID": [1] * n,
            "RunType": [r[5] for r in rows],
        }
    )


def species_frame(rows: list[tuple[str, str, str, str]]) -> pl.DataFrame:
    """(AOU, English name, Order, Family) rows -> a SpeciesList.csv frame."""
    n = len(rows)
    return pl.DataFrame(
        {
            "Seq": list(range(1, n + 1)),
            "AOU": [r[0] for r in rows],
            "English_Common_Name": [r[1] for r in rows],
            "French_Common_Name": ["Grive à dos olive"] * n,
            "Order": [r[2] for r in rows],
            "Family": [r[3] for r in rows],
            "Genus": ["Genus"] * n,
            "Species": ["species"] * n,
        }
    )


def fifty_frame(rows: list[tuple[int, int, int, int, str, dict[int, int]]]) -> pl.DataFrame:
    """(RouteDataID, StateNum, Route, Year, AOU, {stop: count}) rows -> a 50-stop frame."""
    n = len(rows)
    data: dict[str, list[object]] = {
        "RouteDataID": [r[0] for r in rows],
        "CountryNum": [840] * n,
        "StateNum": [r[1] for r in rows],
        "Route": [r[2] for r in rows],
        "RPID": [101] * n,
        "Year": [r[3] for r in rows],
        "AOU": [r[4] for r in rows],
    }
    for i in range(1, 51):
        data[f"Stop{i}"] = [r[5].get(i, 0) for r in rows]
    return pl.DataFrame(data)


def ten_frame(rows: list[tuple[int, int, int, int, str, list[int], int]]) -> pl.DataFrame:
    """(RouteDataID, StateNum, Route, Year, AOU, [5 bin counts], StopTotal) -> 10-stop frame."""
    n = len(rows)
    data: dict[str, list[object]] = {
        "RouteDataID": [r[0] for r in rows],
        "CountryNum": [840] * n,
        "StateNum": [r[1] for r in rows],
        "Route": [r[2] for r in rows],
        "RPID": [101] * n,
        "Year": [r[3] for r in rows],
        "AOU": [r[4] for r in rows],
    }
    for b in range(5):
        data[f"Count{(b + 1) * 10}"] = [r[5][b] for r in rows]
    data["StopTotal"] = [r[6] for r in rows]
    data["SpeciesTotal"] = [sum(r[5]) for r in rows]
    return pl.DataFrame(data)
