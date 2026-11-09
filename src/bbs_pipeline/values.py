"""Valid values for profile fields, read from the release cache (for `bbs values` and the UI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from bbs_pipeline.config import RunProfile
from bbs_pipeline.contracts import JOIN_TABLES
from bbs_pipeline.guilds import NOTE_COLUMNS, read_guild_csv, resolve_guilds
from bbs_pipeline.sources.loader import load_sources
from bbs_pipeline.sources.regions import POSTAL_ABBREVIATIONS, parse_code_labels
from bbs_pipeline.stages.common import normalize_canonical_keys, to_lazy
from bbs_pipeline.stages.enrich import join_sources
from bbs_pipeline.stages.runs import flag_observer_first_year
from bbs_pipeline.taxonomy import lumped_species

ABBREVIATION_BY_NAME: dict[str, str] = {name: abbr for abbr, name in POSTAL_ABBREVIATIONS.items()}

# Coded profile fields whose descriptions come from the release metadata XML
LABELED_CODES = ("BCR", "Stratum", "RunType", "QualityCurrentID")

# Traits with more distinct values than this are free text (names), not categories
MAX_TRAIT_VALUES = 50


@dataclass(frozen=True)
class ProfileValues:
    """Everything a profile field can be set to, for one release (and guild file)."""

    states: list[dict[str, object]]  # {"StateNum", "StateName", "abbreviation"}
    bcrs: list[int]
    strata: list[int]
    routes: list[dict[str, object]]  # {"route_id", "RouteName", "StateNum", "BCR", "Stratum"}
    species: list[dict[str, str]]  # {"AOU", "English_Common_Name", "Family", "Order"}
    families: list[str]
    orders: list[str]
    join_columns: dict[str, list[str]]
    guild_traits: dict[str, list[str]] = field(default_factory=dict)
    # {"BCR": {27: "Southeastern Coastal Plain", ...}}; empty without the metadata XML
    code_labels: dict[str, dict[int, str]] = field(default_factory=dict)
    years: tuple[int, int] | None = None  # first and last survey year in the release

    def label(self, attribute: str, code: int) -> str:
        """'27 · Southeastern Coastal Plain', or just '27' when the release has no name."""
        name = self.code_labels.get(attribute, {}).get(code)
        return f"{code} · {name}" if name else str(code)

    def field_values(self, name: str) -> list[str]:
        """Plain-text values for `bbs values FIELD` (e.g. routes.states, guilds.habitat)."""
        simple: dict[str, list[str]] = {
            "routes.states": [
                f"{s['StateNum']}\t{s['abbreviation'] or ''}\t{s['StateName']}" for s in self.states
            ],
            "routes.bcrs": [self.label("BCR", b).replace(" · ", "\t") for b in self.bcrs],
            "routes.strata": [self.label("Stratum", s).replace(" · ", "\t") for s in self.strata],
            "routes.route_ids": [f"{r['route_id']}\t{r['RouteName']}" for r in self.routes],
            "species.aous": [f"{s['AOU']}\t{s['English_Common_Name']}" for s in self.species],
            "species.common_names": [s["English_Common_Name"] for s in self.species],
            "species.families": self.families,
            "species.orders": self.orders,
            "guilds": list(self.guild_traits),
        }
        if name in simple:
            return simple[name]
        table_or_trait = name.split(".", 1)[1] if "." in name else ""
        if name.startswith("joins.") and table_or_trait in self.join_columns:
            return self.join_columns[table_or_trait]
        if name.startswith("guilds.") and table_or_trait in self.guild_traits:
            return self.guild_traits[table_or_trait]
        valid = [
            *simple,
            *(f"joins.{t}" for t in JOIN_TABLES),
            *(f"guilds.{t}" for t in self.guild_traits),
        ]
        raise KeyError(f"Unknown field '{name}'. Fields: {', '.join(valid)}")


def _sorted_unique(df: pl.DataFrame, column: str) -> list:
    if column not in df.columns:
        return []
    return sorted(df[column].drop_nulls().unique().to_list())


def _code_labels(raw_dir: Path) -> dict[str, dict[int, str]]:
    xml_files = sorted(raw_dir.glob("*.xml")) if raw_dir.is_dir() else []
    if len(xml_files) != 1:
        return {}  # no metadata (or ambiguous): show bare codes rather than guess names
    return {a: parse_code_labels(xml_files[0], a) for a in LABELED_CODES}


AREA_FIELDS = ("StateNum", "BCR", "Stratum", "route_id")


def area_options(
    routes: list[dict[str, object]], chosen: dict[str, list[object]]
) -> dict[str, list[object]]:
    """For each area field, the values that still match a route given the OTHER picks.

    ``routes`` are ``ProfileValues.routes``; ``chosen`` maps each of AREA_FIELDS to the
    picked values (empty = no filter). Every filter respects every other one, so a
    state, BCR, stratum or route that is offered always leaves at least one route:
    pick BCR 29 and only states with BCR 29 routes are offered, and the other way round.
    """

    def fits(route: dict[str, object], skip: str) -> bool:
        return all(not chosen.get(f) or route.get(f) in chosen[f] for f in AREA_FIELDS if f != skip)

    return {
        f: sorted({r[f] for r in routes if r.get(f) is not None and fits(r, f)})  # type: ignore[type-var]
        for f in AREA_FIELDS
    }


def collect_values(profile: RunProfile) -> ProfileValues:
    """Read the release (building the cache if needed) and list every valid value."""
    bundle = profile.to_bundle()
    tables = load_sources(bundle.protocol).tables

    routes = normalize_canonical_keys(to_lazy(tables["routes"])).collect()
    species_raw = normalize_canonical_keys(to_lazy(tables["species"])).collect()
    species = (
        lumped_species(species_raw) if bundle.protocol.lump_subspecies_and_hybrids else species_raw
    )

    if "regions" in tables:
        regions = to_lazy(tables["regions"]).collect().sort("StateNum")
        states = [
            {"StateNum": code, "StateName": name, "abbreviation": ABBREVIATION_BY_NAME.get(name)}
            for code, name in regions.select("StateNum", "StateName").iter_rows()
        ]
    else:
        states = [
            {"StateNum": code, "StateName": "", "abbreviation": None}
            for code in _sorted_unique(routes, "StateNum")
        ]

    guild_df, traits, trait_values = None, [], {}
    if bundle.guilds.file is not None and Path(bundle.guilds.file).exists():
        guilds = resolve_guilds(
            read_guild_csv(Path(bundle.guilds.file)), species_raw, Path(bundle.guilds.file)
        )
        guild_df, traits = guilds.table, guilds.traits
        for trait in traits:
            if trait.lower() in NOTE_COLUMNS:
                continue
            values = _sorted_unique(guilds.table, trait)
            if len(values) <= MAX_TRAIT_VALUES:
                trait_values[trait] = values

    weather = flag_observer_first_year(
        normalize_canonical_keys(to_lazy(tables["weather"])), bundle.protocol
    )
    sources = join_sources(
        normalize_canonical_keys(to_lazy(tables["routes"])),
        weather,
        species.lazy(),
        tables,
        guild_df,
        traits,
    )
    join_columns = {t: (s.columns if s is not None else []) for t, s in sources.items()}

    route_cols = [
        c for c in ("route_id", "RouteName", "StateNum", "BCR", "Stratum") if c in routes.columns
    ]
    span = weather.select(pl.col("Year").min().alias("lo"), pl.col("Year").max().alias("hi"))
    lo, hi = span.collect().row(0)
    return ProfileValues(
        states=states,
        bcrs=_sorted_unique(routes, "BCR"),
        strata=_sorted_unique(routes, "Stratum"),
        routes=routes.select(route_cols).sort("route_id").to_dicts(),
        species=species.select(
            [c for c in ("AOU", "English_Common_Name", "Family", "Order") if c in species.columns]
        )
        .sort("AOU")
        .to_dicts(),
        families=_sorted_unique(species, "Family"),
        orders=_sorted_unique(species, "Order"),
        join_columns=join_columns,
        guild_traits=trait_values,
        code_labels=_code_labels(Path(profile.source.raw_dir)),
        years=(int(lo), int(hi)) if lo is not None else None,
    )
