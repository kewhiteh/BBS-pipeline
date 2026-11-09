"""The Guild File page's working copy: traits with allowed values, and species rows.

Plain data and functions (no Streamlit), so the page stays thin and this is testable
(docs/instructions/GUILD_WIZARD_PLAN.md G3, G4, G8, G11). A draft is a dict that
survives Streamlit reruns in session state:

    {"traits": {"forest_guild": ["interior", "edge"], ...},   # name -> allowed values
     "notes": ["notes", "pwrc_name"],                          # free-text columns kept
     "rows": [{"aou": "07550", "common_name": ..., "scientific_name": ...,
               "forest_guild": "interior", "notes": "Whitcomb 1981"}, ...],
     "added": ["07550"]}                                       # AOUs added this session
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl

from bbs_pipeline.guild_builder import SpeciesIndex, row_aou
from bbs_pipeline.guilds import IDENTIFIER_COLUMNS, NOTE_COLUMNS, read_guild_csv

STARTER = Path(__file__).resolve().parents[2] / "guilds" / "pwrc_species_groups.csv"
FREE_TEXT_LIMIT = 50  # a column with more distinct values is free text, not a trait
NOTES = "notes"


def clean_value(value: str) -> str:
    """Allowed values are lower case with underscores: 'Interior Edge' -> 'interior_edge'."""
    return re.sub(r"\s+", "_", value.strip().lower())


def blank_draft() -> dict[str, Any]:
    return {"traits": {}, "notes": [NOTES], "rows": [], "added": []}


def draft_from_csv(path: Path, index: SpeciesIndex) -> dict[str, Any]:
    """A draft from a guild CSV; each row gets its AOU and current names from the release.

    Rows that match no species are kept as they are (the checks list them).
    """
    raw = read_guild_csv(path)
    ids = [c for c in IDENTIFIER_COLUMNS if c in raw.columns]
    others = [c for c in raw.columns if c not in ids]
    notes = [
        c
        for c in others
        if c.lower() in NOTE_COLUMNS or raw[c].drop_nulls().n_unique() > FREE_TEXT_LIMIT
    ]
    traits = {
        c: sorted({v for v in raw[c].drop_nulls().to_list() if v.strip()})
        for c in others
        if c not in notes
    }
    rows = []
    for row in raw.iter_rows(named=True):
        aou = row_aou(index, row)
        known = index.by_aou.get(aou) if aou else None
        rows.append(
            {
                "aou": aou or row.get("aou") or "",
                "common_name": known["English_Common_Name"]
                if known
                else row.get("common_name") or "",
                "scientific_name": known["scientific_name"]
                if known
                else row.get("scientific_name") or "",
                **{c: row[c] or "" for c in others},
            }
        )
    return {"traits": traits, "notes": notes or [NOTES], "rows": rows, "added": []}


def rename_trait(draft: dict[str, Any], old: str, new: str) -> None:
    traits = draft["traits"]
    draft["traits"] = {(new if k == old else k): v for k, v in traits.items()}
    for row in draft["rows"]:
        row[new] = row.pop(old, "")


def remove_trait(draft: dict[str, Any], name: str) -> None:
    draft["traits"].pop(name, None)
    for row in draft["rows"]:
        row.pop(name, None)


def has_row(draft: dict[str, Any], aou: str) -> bool:
    return any(r["aou"] == aou for r in draft["rows"])


def add_species(
    draft: dict[str, Any],
    aous: list[str],
    index: SpeciesIndex,
    assign: dict[str, str] | None = None,
    note: str = "",
) -> list[tuple[str, str, str, str]]:
    """Add each AOU once; set ``assign`` values and append ``note``.

    Returns the (AOU, trait, old, new) values that were overwritten, so the page can
    ask first (G8): call with ``assign`` only after the user confirmed those.
    """
    overwritten: list[tuple[str, str, str, str]] = []
    note_col = draft["notes"][0] if draft["notes"] else NOTES
    for aou in aous:
        row = next((r for r in draft["rows"] if r["aou"] == aou), None)
        if row is None:
            known = index.by_aou[aou]
            row = {
                "aou": aou,
                "common_name": known["English_Common_Name"],
                "scientific_name": known["scientific_name"],
                **dict.fromkeys(draft["traits"], ""),
                **dict.fromkeys(draft["notes"], ""),
            }
            draft["rows"].append(row)
            if aou not in draft["added"]:
                draft["added"].append(aou)
        for trait, value in (assign or {}).items():
            old = row.get(trait) or ""
            if old and old != value:
                overwritten.append((aou, trait, old, value))
            row[trait] = value
        if note:
            current = row.get(note_col) or ""
            if note not in current:
                row[note_col] = f"{current}; {note}" if current else note
    return overwritten


def would_overwrite(
    draft: dict[str, Any], aous: list[str], assign: dict[str, str]
) -> list[tuple[str, str, str, str]]:
    """The values add_species would replace, without changing anything."""
    out = []
    for aou in aous:
        row = next((r for r in draft["rows"] if r["aou"] == aou), None)
        for trait, value in assign.items():
            old = (row or {}).get(trait) or ""
            if old and old != value:
                out.append((aou, trait, old, value))
    return out


def draft_table(draft: dict[str, Any]) -> pl.DataFrame:
    """The draft as the guild CSV's columns, all text: ids, traits, then notes."""
    columns = ["aou", "common_name", "scientific_name", *draft["traits"], *draft["notes"]]
    return pl.DataFrame(
        [{c: (r.get(c) or None) for c in columns} for r in draft["rows"]],
        schema=dict.fromkeys(columns, pl.String),
    )


def write_draft(draft: dict[str, Any], path: Path) -> Path:
    """Write the guild CSV (AOU stays text, so leading zeros survive)."""
    if path.suffix.lower() != ".csv":
        raise ValueError(f"A guild file name must end in .csv, got '{path.name}'")
    if path.resolve() == STARTER.resolve():
        raise ValueError("Save under a new name: the starter file is never overwritten.")
    path.parent.mkdir(parents=True, exist_ok=True)
    draft_table(draft).sort("aou").write_csv(path)
    return path
