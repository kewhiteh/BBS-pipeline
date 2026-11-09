"""Building a guild file: match pasted names to BBS species, and check a draft file.

Used by the editor's Guild File page and by ``bbs guilds match`` / ``bbs guilds check``
(docs/instructions/GUILD_WIZARD_PLAN.md, G6, G7, G9, G12). Nothing here writes files.

Matching a pasted line, in order:

1. An AOU code, the current scientific name, or the current common name (exact, ignoring
   case and extra spaces): ``exact``.
2. ``guilds/name_changes.csv``: an old name listed by hand (``renamed``, ``split``,
   ``form``, ``spelling``), or an old genus (``genus``) tried with every current genus it
   became. A genus swap keeps only species in the release; if the ending changed too
   (*formosus* -> *formosa*), the closest name in the new genus is offered: ``old``.
3. The closest common or scientific name by spelling: ``spelling``.

Only ``exact`` is used without asking; everything else is a suggestion to confirm.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from bbs_pipeline.contracts import normalize_aou
from bbs_pipeline.guilds import IDENTIFIER_COLUMNS, NOTE_COLUMNS
from bbs_pipeline.sources.tables import read_text_table
from bbs_pipeline.taxonomy import lumped_species, with_species_names

if TYPE_CHECKING:
    from bbs_pipeline.config import RunProfile

NAME_CHANGES = Path(__file__).resolve().parents[2] / "guilds" / "name_changes.csv"
SPELLING_CUTOFF = 0.85  # difflib ratio for a "did you mean" suggestion
_AOU_RE = re.compile(r"^\d{3,5}$")


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


@dataclass(frozen=True)
class NameMatch:
    """One pasted line and what it matched."""

    text: str
    kind: str  # exact | old | spelling | none
    aous: list[str] = field(default_factory=list)  # more than one for a split
    why: str = ""

    @property
    def needs_confirm(self) -> bool:
        return self.kind in ("old", "spelling")


@dataclass(frozen=True)
class SpeciesIndex:
    """Lookups over a release's species list (forms lumped under their species)."""

    species: pl.DataFrame  # AOU, English_Common_Name, scientific_name (lumped list)
    by_aou: dict[str, dict[str, str]]
    by_name: dict[str, set[str]]  # common and scientific keys -> AOUs
    by_genus: dict[str, dict[str, str]]  # genus key -> {epithet key: AOU}

    @classmethod
    def from_species(cls, species_raw: pl.DataFrame) -> SpeciesIndex:
        names = with_species_names(species_raw)
        lumped = lumped_species(species_raw).with_columns(
            (pl.col("Genus") + pl.lit(" ") + pl.col("Species")).alias("scientific_name")
        )
        by_aou = {
            r["AOU"]: r
            for r in lumped.select("AOU", "English_Common_Name", "scientific_name").to_dicts()
        }
        by_name: dict[str, set[str]] = {}
        by_genus: dict[str, dict[str, str]] = {}
        for row in names.iter_rows(named=True):
            aou = row["AOU"]
            genus, epithet = row.get("Genus") or "", row.get("Species") or ""
            by_name.setdefault(_key(row.get("English_Common_Name") or ""), set()).add(aou)
            by_name.setdefault(_key(f"{genus} {epithet}"), set()).add(aou)
            if genus and epithet and " " not in epithet.strip():
                by_genus.setdefault(_key(genus), {})[_key(epithet)] = aou
        lump = {r["AOU"] for r in lumped.iter_rows(named=True)}
        # A name that hits a form resolves to its species (lumped list), else keep the hit
        return cls(
            species=lumped.select("AOU", "English_Common_Name", "scientific_name"),
            by_aou=by_aou,
            by_name={k: ({a for a in v if a in lump} or v) for k, v in by_name.items() if k},
            by_genus=by_genus,
        )

    def label(self, aou: str) -> str:
        row = self.by_aou.get(aou)
        return f"{aou} {row['English_Common_Name']}" if row else aou


@dataclass(frozen=True)
class NameChange:
    old: str
    current: list[str]
    kind: str
    note: str


def read_name_changes(path: Path = NAME_CHANGES) -> list[NameChange]:
    """The old-name table: old_name, current_names (';'-separated), kind, note."""
    table = read_text_table(path.read_bytes())
    out = []
    for row in table.iter_rows(named=True):
        current = [c.strip() for c in (row.get("current_names") or "").split(";") if c.strip()]
        out.append(
            NameChange(
                old=(row.get("old_name") or "").strip(),
                current=current,
                kind=(row.get("kind") or "").strip(),
                note=(row.get("note") or "").strip(),
            )
        )
    return [c for c in out if c.old and c.current]


def _exact(index: SpeciesIndex, text: str) -> set[str]:
    if _AOU_RE.match(text.strip()):
        try:
            code = normalize_aou(text.strip())
        except ValueError:
            return set()
        return {code} if code in index.by_aou else set()
    return index.by_name.get(_key(text), set())


def _genus_swap(
    index: SpeciesIndex, text: str, old_genus: str, new_genera: list[str]
) -> tuple[list[str], bool]:
    """AOUs for 'Old epithet' under each new genus; bool = an ending had to be guessed."""
    parts = _key(text).split(" ")
    if len(parts) != 2 or parts[0] != _key(old_genus):
        return [], False
    epithet = parts[1]
    exact = [
        g[epithet] for ng in new_genera if (g := index.by_genus.get(_key(ng))) and epithet in g
    ]
    if exact:
        return exact, False
    close = []
    for ng in new_genera:
        g = index.by_genus.get(_key(ng), {})
        close += [g[e] for e in difflib.get_close_matches(epithet, list(g), n=1, cutoff=0.7)]
    return close, True


def match_names(
    lines: Iterable[str],
    index: SpeciesIndex,
    changes: list[NameChange] | None = None,
) -> list[NameMatch]:
    """Match each non-empty line (see the module docstring for the order)."""
    changes = read_name_changes() if changes is None else changes
    by_old = {_key(c.old): c for c in changes if c.kind != "genus"}
    genera = [c for c in changes if c.kind == "genus"]
    spell_keys = list(index.by_name)
    out: list[NameMatch] = []
    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        hits = _exact(index, text)
        if hits:
            out.append(NameMatch(text, "exact", sorted(hits)))
            continue
        change = by_old.get(_key(text))
        if change is not None:
            aous = sorted({a for c in change.current for a in _exact(index, c)})
            if aous:
                why = change.kind + (f": {change.note}" if change.note else "")
                out.append(NameMatch(text, "old", aous, why))
                continue
        found: list[str] = []
        for rule in genera:
            aous, guessed = _genus_swap(index, text, rule.old, rule.current)
            if aous:
                found = aous
                why = f"genus {rule.old} is now {' / '.join(rule.current)}"
                if guessed:
                    why += "; ending changed"
                break
        if found:
            out.append(NameMatch(text, "old", sorted(set(found)), why))
            continue
        close = difflib.get_close_matches(_key(text), spell_keys, n=1, cutoff=SPELLING_CUTOFF)
        if close:
            out.append(
                NameMatch(
                    text, "spelling", sorted(index.by_name[close[0]]), f"close to '{close[0]}'"
                )
            )
            continue
        out.append(NameMatch(text, "none"))
    return out


@dataclass(frozen=True)
class GuildChecks:
    """What the Classify & Check step shows (G9)."""

    rows: int
    matched: int
    unmatched: list[dict[str, str]]  # rows whose identifiers match no species
    duplicates: dict[str, int]  # AOU -> number of rows
    empty_trait_rows: int  # matched rows with at least one empty trait
    outside_values: dict[str, list[str]]  # trait -> values not in its allowed list
    near_duplicate_values: dict[str, list[list[str]]]  # trait -> groups like Interior/interior
    recorded_without_row: list[str] = field(default_factory=list)  # AOUs

    @property
    def blocking(self) -> bool:
        """Problems that stop a save: values outside a trait's list, duplicate rows."""
        return bool(self.outside_values or self.duplicates)


def row_aou(index: SpeciesIndex, row: dict[str, str | None]) -> str | None:
    for col in IDENTIFIER_COLUMNS:
        value = row.get(col)
        if value:
            hits = _exact(index, value)
            if len(hits) == 1:
                return next(iter(hits))
    return None


def check_guild_rows(
    table: pl.DataFrame,
    index: SpeciesIndex,
    allowed: dict[str, list[str]] | None = None,
    recorded: Iterable[str] | None = None,
) -> GuildChecks:
    """Check a guild table (the CSV's columns, as text) against the release."""
    ids = [c for c in IDENTIFIER_COLUMNS if c in table.columns]
    traits = [c for c in table.columns if c not in ids and c.lower() not in NOTE_COLUMNS]
    matched: list[str] = []
    unmatched: list[dict[str, str]] = []
    empty = 0
    for row in table.iter_rows(named=True):
        aou = row_aou(index, row)
        if aou is None:
            unmatched.append({c: row[c] for c in ids if row[c]})
            continue
        matched.append(aou)
        if any(not (row[t] or "").strip() for t in traits):
            empty += 1
    counts: dict[str, int] = {}
    for aou in matched:
        counts[aou] = counts.get(aou, 0) + 1
    outside: dict[str, list[str]] = {}
    near: dict[str, list[list[str]]] = {}
    for t in traits:
        values = sorted({v for v in table[t].drop_nulls().to_list() if v.strip()})
        if allowed is not None and t in allowed:
            bad = [v for v in values if v not in allowed[t]]
            if bad:
                outside[t] = bad
        groups: dict[str, list[str]] = {}
        for v in values:
            groups.setdefault(_key(v).replace(" ", "_"), []).append(v)
        variants = [g for g in groups.values() if len(g) > 1]
        if variants:
            near[t] = variants
    have = set(matched)
    return GuildChecks(
        rows=table.height,
        matched=len(matched),
        unmatched=unmatched,
        duplicates={a: n for a, n in counts.items() if n > 1},
        empty_trait_rows=empty,
        outside_values=outside,
        near_duplicate_values=near,
        recorded_without_row=sorted(set(recorded or ()) - have),
    )


def recorded_species(profile: RunProfile) -> pl.DataFrame:
    """Species with a count on the profile's kept runs: AOU, English_Common_Name, runs.

    Uses the profile's routes, years, run filters and lumping (G10), ignores its species,
    guild and join settings, and never zero-fills, so every row is a real record.
    """
    from bbs_pipeline.config import profile_from_data  # noqa: PLC0415 (avoid an import cycle)
    from bbs_pipeline.engine.ecology import run_ecological_engine  # noqa: PLC0415
    from bbs_pipeline.sources.loader import load_sources  # noqa: PLC0415

    data = profile.model_dump(mode="json")
    data["observations"]["zero_fill"] = False
    data["observations"]["rolling_years"] = 0
    for section in ("species", "guilds"):
        data.pop(section, None)
    data["joins"] = {table: [] for table in data["joins"]}
    bundle = profile_from_data(data).to_bundle()
    sources = load_sources(bundle.protocol)
    result = run_ecological_engine(
        tables=sources.tables,
        species=bundle.species,
        protocol=bundle.protocol,
        data_source=sources.source,
    )
    index = SpeciesIndex.from_species(sources.tables["species"].collect())
    counts = (
        result.observations_df.filter(pl.col("route_total") > 0)
        .group_by("AOU")
        .agg(pl.col("RouteDataID").n_unique().alias("runs"))
    )
    return (
        counts.join(index.species.select("AOU", "English_Common_Name"), on="AOU", how="left")
        .select("AOU", "English_Common_Name", "runs")
        .sort("runs", descending=True)
    )
