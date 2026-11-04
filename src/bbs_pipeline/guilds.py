"""Pluggable guild file: one CSV row per species, any number of trait columns.

Identifier columns (any of them, at least one filled per row; header case does not
matter): ``aou``, ``common_name``, ``scientific_name``. Every other column is a trait.
A row is matched to the BBS species list by AOU, then scientific name (Genus species),
then common name, ignoring case and extra whitespace. Identifiers in one row that point
at different species raise, as do two rows for the same species. Rows that match no
species are reported (or raise, with ``unmatched = "error"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from bbs_pipeline.contracts import normalize_aou
from bbs_pipeline.sources.tables import read_text_table

IDENTIFIER_COLUMNS: tuple[str, ...] = ("aou", "scientific_name", "common_name")

# Free-text columns: kept and joinable by name, but never offered as filters
NOTE_COLUMNS: frozenset[str] = frozenset(
    {"note", "notes", "comment", "comments", "source", "citation", "reference"}
)


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


@dataclass(frozen=True)
class GuildTable:
    """Guild rows resolved to BBS AOU codes."""

    source: Path
    table: pl.DataFrame  # AOU + trait columns (String)
    traits: list[str]
    rows_total: int
    unmatched_rows: list[dict[str, Any]] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "file": str(self.source),
            "rows": self.rows_total,
            "matched_rows": self.table.height,
            "traits": self.traits,
            "unmatched_rows": self.unmatched_rows,
        }


def read_guild_csv(path: Path) -> pl.DataFrame:
    """Read a guild CSV as text; identifier headers are lower-cased, blank rows dropped."""
    if not path.exists():
        raise FileNotFoundError(f"Guild file not found: '{path}'")
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Guild file must be a .csv, got '{path.name}'")
    df = read_text_table(path.read_bytes())
    df = df.rename(
        {c: c.strip().lower() for c in df.columns if c.strip().lower() in IDENTIFIER_COLUMNS}
    )
    ids = [c for c in IDENTIFIER_COLUMNS if c in df.columns]
    if not ids:
        raise ValueError(
            f"Guild file '{path.name}' needs at least one of the columns {list(IDENTIFIER_COLUMNS)}; "
            f"found {df.columns}"
        )
    if len(df.columns) == len(ids):
        raise ValueError(f"Guild file '{path.name}' has no trait columns besides {ids}.")
    return df.filter(~pl.all_horizontal(pl.all().is_null()))


def _lookup(species: pl.DataFrame) -> dict[str, dict[str, set[str]]]:
    maps: dict[str, dict[str, set[str]]] = {"scientific_name": {}, "common_name": {}}
    for row in species.iter_rows(named=True):
        aou = row["AOU"]
        sci = f"{row.get('Genus') or ''} {row.get('Species') or ''}"
        maps["scientific_name"].setdefault(_key(sci), set()).add(aou)
        maps["common_name"].setdefault(_key(row.get("English_Common_Name") or ""), set()).add(aou)
    return maps


def resolve_guilds(raw: pl.DataFrame, species: pl.DataFrame, source: Path) -> GuildTable:
    """Match guild rows to BBS species (AOU, then scientific, then common name)."""
    ids = [c for c in IDENTIFIER_COLUMNS if c in raw.columns]
    traits = [c for c in raw.columns if c not in ids]
    known_aous = set(species["AOU"].to_list())
    maps = _lookup(species)

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    problems: list[str] = []
    for line, row in enumerate(raw.iter_rows(named=True), start=2):  # line 1 is the header
        given = {c: row[c] for c in ids if row[c] is not None}
        if not given:
            problems.append(f"line {line}: no aou, scientific_name, or common_name")
            continue
        found: dict[str, str] = {}
        for column, value in given.items():
            if column == "aou":
                try:
                    code = normalize_aou(value)
                except ValueError:
                    problems.append(f"line {line}: aou '{value}' is not an AOU code")
                    continue
                if code in known_aous:
                    found[column] = code
            else:
                hits = maps[column].get(_key(value), set())
                if len(hits) > 1:
                    problems.append(f"line {line}: {column} '{value}' matches AOUs {sorted(hits)}")
                elif hits:
                    found[column] = next(iter(hits))
        if not found:
            unmatched.append({"line": line, **given})
            continue
        if len(set(found.values())) > 1:
            pairs = ", ".join(f"{c}={given[c]!r} -> {a}" for c, a in found.items())
            problems.append(f"line {line}: identifiers point at different species ({pairs})")
            continue
        matched.append(
            {"AOU": next(iter(found.values())), "_line": line, **{t: row[t] for t in traits}}
        )

    table = pl.DataFrame(
        matched, schema={"AOU": pl.String, "_line": pl.Int64, **dict.fromkeys(traits, pl.String)}
    )
    for aou, lines in (
        table.group_by("AOU")
        .agg(pl.col("_line").sort())
        .filter(pl.col("_line").list.len() > 1)
        .iter_rows()
    ):
        problems.append(f"lines {lines}: more than one row for AOU {aou}")
    if problems:
        raise ValueError(f"Guild file '{source.name}':\n  " + "\n  ".join(problems))
    return GuildTable(
        source=source,
        table=table.drop("_line").sort("AOU"),
        traits=traits,
        rows_total=raw.height,
        unmatched_rows=unmatched,
    )


def guild_filter_aous(guilds: GuildTable, filters: dict[str, list[str]]) -> set[str]:
    """AOUs passing every trait filter: OR within a trait, AND across traits.

    Values match case-insensitively. An unknown trait or a value that no row has raises,
    so a typo cannot silently empty the species list.
    """
    table = guilds.table
    keep = pl.lit(True)
    for trait, values in filters.items():
        if trait not in guilds.traits:
            raise KeyError(f"guilds.filters: no trait column '{trait}'; traits are {guilds.traits}")
        present = {_key(v) for v in table[trait].drop_nulls().to_list()}
        missing = [v for v in values if _key(v) not in present]
        if missing:
            raise ValueError(
                f"guilds.filters.{trait}: no row has {missing}; values are {sorted(present)}"
            )
        wanted = [_key(v) for v in values]
        normalized = pl.col(trait).str.replace_all(r"\s+", " ").str.strip_chars().str.to_lowercase()
        keep = keep & normalized.is_in(wanted)
    return set(table.filter(keep)["AOU"].to_list())
