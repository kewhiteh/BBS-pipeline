"""Lump subspecies, forms, and within-species hybrids into their species.

Derived from the release's own scientific names, never a hand-typed table:

- ``Junco hyemalis caniceps`` (a form) belongs to ``Junco hyemalis``.
- ``Colaptes auratus auratus x auratus cafer`` (a hybrid of two forms) belongs to
  ``Colaptes auratus``.
- ``Acanthis flammea flammea / hornemanni`` (unidentified between forms) belongs to
  ``Acanthis flammea``.
- ``Aythya marila / affinis`` and ``Anas platyrhynchos x rubripes`` involve two species
  and are left alone.

Each group is counted under its species-level entry: the plain binomial entry (e.g.
``(unid. race) Dark-eyed Junco``, ``Junco hyemalis``), or, when the release has none, the
within-species "unid." entry (Redpoll). That entry is renamed without its
parenthetical prefix ("Dark-eyed Junco").
"""

from __future__ import annotations

import re

import polars as pl

_PREFIX_RE = re.compile(r"^\([^)]*\)\s*")


def _species_of(genus: str | None, species: str | None) -> tuple[str | None, bool]:
    """Return (binomial, is_species_level) for one species-list entry.

    The binomial is None when the entry spans more than one species.
    """
    genus, epithets = (genus or "").strip(), (species or "").strip()
    if not genus or not epithets or "/" in genus:
        return None, False
    if " x " in epithets:
        sides = [side.split()[0] for side in epithets.split(" x ") if side.split()]
        if len(set(sides)) != 1 or "/" in epithets:
            return None, False
        return f"{genus} {sides[0]}", False
    if "/" in epithets:
        first = epithets.split("/")[0].split()
        # "flammea flammea / hornemanni": alternatives are forms of one species
        if len(first) == 2:
            return f"{genus} {first[0]}", True
        return None, False
    tokens = epithets.split()
    return f"{genus} {tokens[0]}", len(tokens) == 1


def species_groups(species: pl.DataFrame) -> pl.DataFrame:
    """One row per entry in a multi-entry group: AOU, parent_AOU, binomial."""
    rows = []
    for aou, genus, epithets in species.select("AOU", "Genus", "Species").iter_rows():
        binomial, level = _species_of(genus, epithets)
        if binomial is not None:
            rows.append((aou, binomial, level))
    entries = pl.DataFrame(rows, schema=["AOU", "binomial", "species_level"], orient="row")
    groups = entries.filter(pl.len().over("binomial") > 1)

    parents: dict[str, str] = {}
    problems: list[str] = []
    for binomial, members in groups.group_by("binomial"):
        name = binomial[0]
        level = members.filter(pl.col("species_level"))
        if level.height != 1:
            problems.append(
                f"{name}: {level.height} species-level entries {level['AOU'].to_list()}"
            )
            continue
        parents[name] = level["AOU"].item()
    if problems:
        raise ValueError("Cannot lump subspecies:\n  " + "\n  ".join(problems))
    return groups.select(
        "AOU", pl.col("binomial").replace_strict(parents).alias("parent_AOU"), "binomial"
    ).sort("AOU")


def lump_map(species: pl.DataFrame) -> dict[str, str]:
    """Form AOU -> species-level AOU, for every entry that is not its own parent."""
    groups = species_groups(species)
    return {
        aou: parent
        for aou, parent in groups.select("AOU", "parent_AOU").iter_rows()
        if aou != parent
    }


def with_species_names(species: pl.DataFrame) -> pl.DataFrame:
    """The species list plus each lumped species under its plain name, for name lookups.

    "(unid. race) Dark-eyed Junco" stays, and "Dark-eyed Junco" is added with the same
    AOU, so a guild file can name the species either way.
    """
    lumped = lumped_species(species)
    renamed = lumped.join(species, on=["AOU", "English_Common_Name"], how="anti")
    return pl.concat([species, renamed.select(species.columns)], how="vertical")


def lumped_species(species: pl.DataFrame) -> pl.DataFrame:
    """The species list with forms removed and parent entries renamed to the species name."""
    groups = species_groups(species)
    forms = set(lump_map(species))
    parents = set(groups["parent_AOU"].to_list())
    return species.filter(~pl.col("AOU").is_in(sorted(forms))).with_columns(
        pl.when(pl.col("AOU").is_in(sorted(parents)))
        .then(pl.col("English_Common_Name").str.replace(_PREFIX_RE.pattern, ""))
        .otherwise(pl.col("English_Common_Name"))
        .alias("English_Common_Name")
    )
