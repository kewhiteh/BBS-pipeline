"""Species: target selection (union), subspecies lumping, and guild filters."""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from bbs_pipeline.contracts import SpeciesSelectionContract
from bbs_pipeline.guilds import GuildTable, guild_filter_aous
from bbs_pipeline.stages.common import match_return_type, normalize_canonical_keys, to_lazy
from bbs_pipeline.taxonomy import lump_map, lumped_species

SPECIES_SELECTORS: tuple[tuple[str, str], ...] = (
    ("aous", "AOU"),
    ("common_names", "English_Common_Name"),
    ("families", "Family"),
    ("orders", "Order"),
)


def filter_target_species[FrameT: (pl.DataFrame, pl.LazyFrame)](
    species: FrameT,
    selection: SpeciesSelectionContract,
) -> FrameT:
    """Keep species matching ANY selector (union); every species when all are empty.

    Names, families, and orders match case-insensitively. A selector value that matches
    no species raises, so a typo cannot silently shrink the species list.
    """
    lf = to_lazy(normalize_canonical_keys(species))
    if selection.is_empty:
        return match_return_type(lf, species)

    schema_names = set(lf.collect_schema().names())
    predicates: list[pl.Expr] = []
    unmatched: list[str] = []
    for selector, column in SPECIES_SELECTORS:
        wanted = getattr(selection, selector)
        if not wanted:
            continue
        if column not in schema_names:
            raise KeyError(f"species.{selector} needs column '{column}' in the species list.")
        if column == "AOU":
            expr = pl.col(column)
            keys = list(wanted)
        else:
            expr = pl.col(column).str.strip_chars().str.to_lowercase()
            keys = [w.strip().lower() for w in wanted]
        present = set(lf.select(expr.alias("k")).collect()["k"].to_list())
        unmatched += [
            f"{selector}={w!r}" for w, k in zip(wanted, keys, strict=True) if k not in present
        ]
        predicates.append(expr.is_in(keys))
    if unmatched:
        raise ValueError(f"Species selector value(s) match no species: {', '.join(unmatched)}")

    combined = predicates[0]
    for expr in predicates[1:]:
        combined = combined | expr
    return match_return_type(lf.filter(combined), species)


def subspecies_lump_map(
    tables: dict[str, pl.LazyFrame | pl.DataFrame], lump: bool
) -> dict[str, str]:
    """Form AOU -> species AOU, derived from the release's species list (empty when off)."""
    if not lump:
        return {}
    if "species" not in tables:
        raise KeyError("Lumping subspecies needs the 'species' table (scientific names).")
    return lump_map(normalize_canonical_keys(to_lazy(tables["species"])).collect())


def resolve_target_species(
    species_raw_lf: pl.LazyFrame,
    selection: SpeciesSelectionContract,
    mapping: dict[str, str],
) -> tuple[pl.LazyFrame, pl.DataFrame]:
    """Return (species rows for the targets, unique target AOUs).

    With lumping, selecting a form selects its species, and species rows carry the
    species name.
    """
    target_species_lf = filter_target_species(species_raw_lf, selection)
    if mapping:
        target_aous_lf = target_species_lf.select(pl.col("AOU").replace(mapping)).unique()
        target_species_lf = (
            lumped_species(species_raw_lf.collect())
            .lazy()
            .join(target_aous_lf, on="AOU", how="semi")
        )
    target_aous_df = (
        target_species_lf.select(["AOU"]).unique(subset=["AOU"], keep="first").collect()
    )
    return target_species_lf, target_aous_df


@dataclass(frozen=True)
class GuildStage:
    """Target AOUs after guild filters, the guild table to join, and the coverage report."""

    target_aous_df: pl.DataFrame
    guild_join_df: pl.DataFrame | None = None
    species_without_guild: list[dict[str, str]] = field(default_factory=list)


def apply_guilds(
    guilds: GuildTable | None,
    guild_filters: dict[str, list[str]] | None,
    mapping: dict[str, str],
    target_species_lf: pl.LazyFrame,
    target_aous_df: pl.DataFrame,
    runs_lf: pl.LazyFrame,
    run_obs_lf: pl.LazyFrame,
) -> GuildStage:
    """Report recorded species without a guild row, then apply guild filters (AND)."""
    if guilds is None:
        return GuildStage(target_aous_df=target_aous_df)
    guild_join_df = guilds.table
    if mapping:
        guild_join_df = guild_join_df.with_columns(pl.col("AOU").replace(mapping))
        collapsed = guild_join_df.filter(pl.col("AOU").is_duplicated())["AOU"].unique()
        if collapsed.len():
            raise ValueError(
                f"Guild rows for a species and its subspecies collapse to the same AOU "
                f"when lumping: {sorted(collapsed.to_list())}"
            )
    recorded = (
        runs_lf.select("RouteDataID")
        .join(run_obs_lf.filter(pl.col("route_total") > 0), on="RouteDataID")
        .select("AOU")
        .unique()
        .join(target_aous_df.lazy(), on="AOU", how="inner")
        .join(guild_join_df.lazy().select("AOU"), on="AOU", how="anti")
        .join(
            target_species_lf.select(["AOU", "English_Common_Name"]).unique(subset=["AOU"]),
            on="AOU",
            how="left",
        )
        .sort("AOU")
        .collect()
    )
    if guild_filters:
        allowed = guild_filter_aous(
            GuildTable(
                source=guilds.source,
                table=guild_join_df,
                traits=guilds.traits,
                rows_total=guilds.rows_total,
            ),
            guild_filters,
        )
        target_aous_df = target_aous_df.filter(pl.col("AOU").is_in(sorted(allowed)))
    return GuildStage(
        target_aous_df=target_aous_df,
        guild_join_df=guild_join_df,
        species_without_guild=recorded.to_dicts(),
    )
