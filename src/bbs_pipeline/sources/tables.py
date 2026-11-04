"""Read BBS release files as text and cast every column with an explicit type.

Files are found by their release names (case-insensitive), not by column signature:
``Migrants.csv`` has exactly the 50-stop columns and ``MigrantSummary.csv`` exactly the
10-stop columns, so signature matching would merge migrant non-breeders into the
breeding counts.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import polars as pl

from bbs_pipeline.sources.regions import parse_state_codes
from bbs_pipeline.sources.species_list import parse_species_list_lines

# Raw values that mean "missing" in BBS files (after stripping whitespace)
NULL_TOKENS: tuple[str, ...] = ("", "NULL")

RUN_KEYS: tuple[str, ...] = ("RouteDataID", "CountryNum", "StateNum", "Route", "RPID", "Year")
STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))
BIN_COLS: tuple[str, ...] = ("Count10", "Count20", "Count30", "Count40", "Count50")

INT, FLOAT, STR = pl.Int64, pl.Float64, pl.String
AOU = "aou"  # 5-digit, zero-padded string code
UPPER = "upper"  # string, upper-cased (e.g. TempScale "c" -> "C")

_OBS_50 = {**dict.fromkeys(RUN_KEYS, INT), "AOU": AOU, **dict.fromkeys(STOP_COLS, INT)}
_OBS_10 = {
    **dict.fromkeys(RUN_KEYS, INT),
    "AOU": AOU,
    **dict.fromkeys(BIN_COLS, INT),
    "StopTotal": INT,
    "SpeciesTotal": INT,
}

# Every column the pipeline knows, per table. Columns not listed stay String.
SCHEMAS: dict[str, dict[str, object]] = {
    "routes": {
        "CountryNum": INT,
        "StateNum": INT,
        "Route": INT,
        "RouteName": STR,
        "Active": INT,
        "Latitude": FLOAT,
        "Longitude": FLOAT,
        "Stratum": INT,
        "BCR": INT,
        "RouteTypeID": INT,
        "RouteTypeDetailID": INT,
    },
    "weather": {
        **dict.fromkeys(RUN_KEYS, INT),
        **dict.fromkeys(("Month", "Day", "ObsN", "TotalSpp", "StartTemp", "EndTemp"), INT),
        "TempScale": UPPER,
        **dict.fromkeys(("StartWind", "EndWind", "StartSky", "EndSky"), INT),
        "StartTime": STR,  # HHMM; leading zeros matter
        "EndTime": STR,
        "Assistant": INT,
        "QualityCurrentID": INT,
        "RunType": INT,
    },
    "species": {
        "Seq": INT,
        "AOU": AOU,
        **dict.fromkeys(
            (
                "English_Common_Name",
                "French_Common_Name",
                "Spanish_Common_Name",
                "Order",
                "Family",
                "Genus",
                "Species",
            ),
            STR,
        ),
    },
    "fifty_stop": _OBS_50,
    "ten_stop": _OBS_10,
    "migrants_fifty": _OBS_50,
    "migrants_ten": _OBS_10,
    "vehicle": {
        **dict.fromkeys(RUN_KEYS, INT),
        "RecordedCar": INT,
        **{f"Car{i}": INT for i in range(1, 51)},
        **{f"Noise{i}": INT for i in range(1, 51)},
    },
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "routes": ("CountryNum", "StateNum", "Route", "Latitude", "Longitude"),
    "weather": (*RUN_KEYS, "ObsN", "RunType", "QualityCurrentID"),
    "species": ("AOU", "English_Common_Name", "Order", "Family", "Genus", "Species"),
    "fifty_stop": (*RUN_KEYS, "AOU", *STOP_COLS),
    "ten_stop": (*RUN_KEYS, "AOU", *BIN_COLS, "SpeciesTotal"),
    "migrants_fifty": (*RUN_KEYS, "AOU", *STOP_COLS),
    "migrants_ten": (*RUN_KEYS, "AOU", *BIN_COLS, "SpeciesTotal"),
    "vehicle": RUN_KEYS,
}

REQUIRED_TABLES: tuple[str, ...] = ("routes", "weather", "species")


def decode_text(data: bytes) -> str:
    """Decode a release file: UTF-8 if valid, otherwise Windows-1252 (used by BBS files)."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def read_text_table(data: bytes) -> pl.DataFrame:
    """Read CSV bytes with every column as String, stripped, and NULL tokens as null."""
    text = decode_text(data)
    df = pl.read_csv(io.StringIO(text), infer_schema=False)
    return df.with_columns(
        [
            pl.when(pl.col(c).str.strip_chars().is_in(NULL_TOKENS))
            .then(None)
            .otherwise(pl.col(c).str.strip_chars())
            .alias(c)
            for c in df.columns
        ]
    )


def _cast_expr(col: str, kind: object) -> pl.Expr:
    if kind == AOU:
        return pl.col(col).str.zfill(5)
    if kind == UPPER:
        return pl.col(col).str.to_uppercase()
    return pl.col(col).cast(kind, strict=True)  # type: ignore[arg-type]


def cast_table(df: pl.DataFrame, table: str, source: str) -> pl.DataFrame:
    """Check required columns and cast known ones; raise naming the column on bad values."""
    missing = [c for c in REQUIRED_COLUMNS[table] if c not in df.columns]
    if missing:
        raise ValueError(f"{source} ({table}) is missing required columns {missing}.")

    if (
        table == "species"
        and "English_Common_Name" not in df.columns
        and "EnglishName" in df.columns
    ):
        df = df.rename({"EnglishName": "English_Common_Name"})

    schema = SCHEMAS[table]
    for col in df.columns:
        if col not in schema:
            continue
        kind = schema[col]
        if kind == AOU:
            bad = df.filter(pl.col(col).is_not_null() & ~pl.col(col).str.contains(r"^\d{1,5}$"))
            if bad.height:
                raise ValueError(
                    f"{source} ({table}).{col}: not an AOU code: {bad[col].head(5).to_list()}"
                )
        try:
            df = df.with_columns(_cast_expr(col, kind))
        except pl.exceptions.InvalidOperationError as exc:
            sample = (
                df.filter(
                    pl.col(col).cast(kind, strict=False).is_null() & pl.col(col).is_not_null()
                )[  # type: ignore[arg-type]
                    col
                ]
                .head(5)
                .to_list()
            )
            raise ValueError(f"{source} ({table}).{col}: values are not {kind}: {sample}") from exc
    return df


def _csv_members(data: bytes, archive: str) -> Iterator[tuple[str, bytes]]:
    """Yield (name, bytes) for every CSV in a zip, descending into nested zips."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            lowered = name.lower()
            if lowered.endswith(".csv"):
                yield f"{archive}/{name}", zf.read(name)
            elif lowered.endswith(".zip"):
                yield from _csv_members(zf.read(name), f"{archive}/{name}")


def _find(release_dir: Path, *names: str) -> Path | None:
    wanted = {n.lower() for n in names}
    hits = [p for p in release_dir.iterdir() if p.is_file() and p.name.lower() in wanted]
    if len(hits) > 1:
        raise ValueError(f"Ambiguous release files in '{release_dir}': {[p.name for p in hits]}")
    return hits[0] if hits else None


def _concat(frames: list[pl.DataFrame]) -> pl.DataFrame:
    return pl.concat(frames, how="diagonal")


def load_release_tables(release_dir: Path) -> dict[str, pl.DataFrame]:
    """Load every pipeline table from a release directory as typed DataFrames.

    Tables: routes, weather, species (required); fifty_stop, ten_stop (at least one);
    migrants_fifty, migrants_ten, vehicle, regions (when present in the release).
    """
    tables: dict[str, pl.DataFrame] = {}

    for table, names in (
        ("routes", ("routes.csv",)),
        ("weather", ("weather.csv",)),
        ("vehicle", ("vehicledata.csv",)),
    ):
        path = _find(release_dir, *names)
        if path is not None:
            tables[table] = cast_table(read_text_table(path.read_bytes()), table, path.name)

    species_path = _find(release_dir, "specieslist.csv", "specieslist.txt")
    if species_path is not None:
        if species_path.suffix.lower() == ".txt":
            parsed = parse_species_list_lines(decode_text(species_path.read_bytes()))
            raw_species = parsed.with_columns(pl.all().cast(pl.String))
        else:
            raw_species = read_text_table(species_path.read_bytes())
        tables["species"] = cast_table(raw_species, "species", species_path.name)

    for table, archive_name in (("fifty_stop", "50-stopdata.zip"), ("ten_stop", "states.zip")):
        path = _find(release_dir, archive_name)
        if path is not None:
            frames = [
                cast_table(read_text_table(data), table, member)
                for member, data in _csv_members(path.read_bytes(), path.name)
            ]
            if not frames:
                raise ValueError(f"'{path.name}' contains no CSV files.")
            tables[table] = _concat(frames)

    migrants_path = _find(release_dir, "migrantnonbreeder.zip")
    if migrants_path is not None:
        for member, data in _csv_members(migrants_path.read_bytes(), migrants_path.name):
            base = Path(member).name.lower()
            table = {"migrants.csv": "migrants_fifty", "migrantsummary.csv": "migrants_ten"}.get(
                base
            )
            if table is None:
                raise ValueError(f"Unexpected file '{member}' in '{migrants_path.name}'.")
            tables[table] = cast_table(read_text_table(data), table, member)

    xml_files = [p for p in release_dir.iterdir() if p.suffix.lower() == ".xml"]
    if len(xml_files) > 1:
        raise ValueError(f"More than one metadata XML in '{release_dir}': {xml_files}")
    if xml_files:
        tables["regions"] = parse_state_codes(xml_files[0])

    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        raise FileNotFoundError(f"Release directory '{release_dir}' has no {missing} file(s).")
    if "fifty_stop" not in tables and "ten_stop" not in tables:
        raise FileNotFoundError(
            f"Release directory '{release_dir}' has neither 50-StopData.zip nor States.zip."
        )

    if "regions" in tables:
        _check_route_states(tables["routes"], tables["regions"])
    return tables


def _check_route_states(routes: pl.DataFrame, regions: pl.DataFrame) -> None:
    unknown = sorted(set(routes["StateNum"].to_list()) - set(regions["StateNum"].to_list()))
    if unknown:
        raise ValueError(f"routes.csv uses StateNum codes missing from the metadata: {unknown}")
