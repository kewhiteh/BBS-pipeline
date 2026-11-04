"""Build guilds/pwrc_species_groups.csv from the USGS Patuxent BBS species-group list.

Source: "List of Species Groupings", USGS Patuxent Wildlife Research Center,
https://www.mbr-pwrc.usgs.gov/bbs15/guildlst.html (site retired; archived copy:
http://web.archive.org/web/20260309093105/https://www.mbr-pwrc.usgs.gov/bbs15/guildlst.html).

The list uses 1990s and abbreviated names. Each name is matched to the current BBS
species list exactly (ignoring case), or through NAME_MAP below, which records every
rename, split, and subspecies form by hand. Usage:

    uv run python scripts/build_pwrc_guilds.py guildlst.html data/sanitized_cache/species.parquet \\
        guilds/pwrc_species_groups.csv
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import polars as pl

from bbs_pipeline.taxonomy import lumped_species

SOURCE_URL = "https://www.mbr-pwrc.usgs.gov/bbs15/guildlst.html"

# Page section heading -> (output column, value)
GROUPS: dict[str, tuple[str, str]] = {
    "Grassland species": ("breeding_habitat", "grassland"),
    "Wetland-open water species": ("breeding_habitat", "wetland_open_water"),
    "Successional-scrub species": ("breeding_habitat", "successional_scrub"),
    "Woodland species": ("breeding_habitat", "woodland"),
    "Urban species": ("breeding_habitat", "urban"),
    "Cavity nesting species": ("nest_type", "cavity"),
    "Open-cup nesting passerine species": ("nest_type", "open_cup_passerine"),
    "Short distance migrant species": ("migration", "short_distance"),
    "Permanent resident species": ("migration", "permanent_resident"),
    "Neotropical migrant species": ("migration", "neotropical"),
    "Ground-low nesting species": ("nest_location", "ground_low"),
    "Mid-story- canopy nesting species": ("nest_location", "midstory_canopy"),
}
TRAITS: tuple[str, ...] = ("breeding_habitat", "nest_type", "migration", "nest_location")

# Page name -> (current BBS English names, note). AOU codes are always looked up in the
# release's species list, never typed here. A split maps to every daughter species (each
# gets the parent's groups); a subspecies form maps to its species (the pipeline counts
# every form under the species, see bbs_pipeline.taxonomy); None drops the name.
NAME_MAP: dict[str, tuple[list[str] | None, str]] = {
    "Am. Swallow-tailed kite": (["Swallow-tailed Kite"], ""),
    "Black-&-white warbler": (["Black-and-white Warbler"], ""),
    "Black-bell. whist.-Duck": (["Black-bellied Whistling-Duck"], ""),
    "Black-chin. hummingbird": (["Black-chinned Hummingbird"], ""),
    "Black-crn. night-heron": (["Black-crowned Night Heron"], ""),
    "Black-tail gnatcatcher": (["Black-tailed Gnatcatcher"], ""),
    "Black-th. green warbler": (["Black-throated Green Warbler"], ""),
    "Black-thr. blue warbler": (["Black-throated Blue Warbler"], ""),
    "Black-thr. gray warbler": (["Black-throated Gray Warbler"], ""),
    "Broad-tail. hummingbird": (["Broad-tailed Hummingbird"], ""),
    "Brown-crest. flycatcher": (["Brown-crested Flycatcher"], ""),
    "Chestnut-bkd chickadee": (["Chestnut-backed Chickadee"], ""),
    "Chestnut-col. longspur": (["Chestnut-collared Longspur"], ""),
    "Double-crest. Cormorant": (["Double-crested Cormorant"], ""),
    "Golden-fronted woodpkr": (["Golden-fronted Woodpecker"], ""),
    "Grt. crested flycatcher": (["Great Crested Flycatcher"], ""),
    "Ladder-back. woodpecker": (["Ladder-backed Woodpecker"], ""),
    "N. Rough-winged swallow": (["Northern Rough-winged Swallow"], ""),
    "Ruby-thr. hummingbird": (["Ruby-throated Hummingbird"], ""),
    "Scissor-tail flycatcher": (["Scissor-tailed Flycatcher"], ""),
    "Yellow-bell. flycatcher": (["Yellow-bellied Flycatcher"], ""),
    "Yellow-bell. sapsucker": (["Yellow-bellied Sapsucker"], ""),
    "Yellow-crn. night-heron": (["Yellow-crowned Night Heron"], ""),
    "Yellow-head. blackbird": (["Yellow-headed Blackbird"], ""),
    "Harris' hawk": (["Harris's Hawk"], "spelling"),
    "Lewis' woodpecker": (["Lewis's Woodpecker"], "spelling"),
    "Le Conte's thrasher": (["LeConte's Thrasher"], "spelling"),
    "Le conte's sparrow": (["LeConte's Sparrow"], "spelling"),
    "Cattle egret": (["Western Cattle-Egret"], "renamed"),
    "Common barn-owl": (["American Barn Owl"], "renamed"),
    "Common moorhen": (["Common Gallinule"], "renamed"),
    "Common snipe": (["Wilson's Snipe"], "renamed"),
    "Gray jay": (["Canada Jay"], "renamed"),
    "McCown's longspur": (["Thick-billed Longspur"], "renamed"),
    "Northern goshawk": (["American Goshawk"], "renamed"),
    "Rock dove": (["Rock Pigeon"], "renamed"),
    "Three-toed woodpecker": (["American Three-toed Woodpecker"], "renamed"),
    "House wren": (["Northern House Wren"], "renamed"),
    "Blue grouse": (["Dusky Grouse", "Sooty Grouse"], "split"),
    "Brown towhee": (["Canyon Towhee", "California Towhee"], "split"),
    "Plain titmouse": (["Oak Titmouse", "Juniper Titmouse"], "split"),
    "Rufous-sided towhee": (["Eastern Towhee", "Spotted Towhee"], "split"),
    "Sage grouse": (["Greater Sage-Grouse", "Gunnison Sage-Grouse"], "split"),
    "Sage sparrow": (["Sagebrush Sparrow", "Bell's Sparrow"], "split"),
    "Scrub jay": (
        ["Florida Scrub-Jay", "Island Scrub-Jay", "California Scrub-Jay", "Woodhouse's Scrub-Jay"],
        "split",
    ),
    "Sharp-tailed sparrow": (["Nelson's Sparrow", "Saltmarsh Sparrow"], "split"),
    "Solitary vireo": (["Blue-headed Vireo", "Cassin's Vireo", "Plumbeous Vireo"], "split"),
    "Whip-poor-will": (["Eastern Whip-poor-will", "Mexican Whip-poor-will"], "split"),
    "Western flycatcher": (["Western Flycatcher"], ""),
    "Myrtle warbler": (["Yellow-rumped Warbler"], "subspecies form"),
    "Audubon's warbler": (["Yellow-rumped Warbler"], "subspecies form"),
    "Slate-colored junco": (["Dark-eyed Junco"], "subspecies form"),
    "Oregon junco": (["Dark-eyed Junco"], "subspecies form"),
    "Gray-headed junco": (["Dark-eyed Junco"], "subspecies form"),
    "Yellow-shafted flicker": (["Northern Flicker"], "subspecies form"),
    "Red-shafted flicker": (["Northern Flicker"], "subspecies form"),
    "Northwestern crow": (None, "lumped into American Crow, which has its own groups"),
}


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def parse_groups(page: str) -> pl.DataFrame:
    """(column, value, pwrc_name) for every name in every group on the page."""
    body = page[page.index("<H2>") :]
    tokens = re.split(r"(<H3[^>]*>)", body, flags=re.I)
    rows: list[tuple[str, str, str]] = []
    for i in range(1, len(tokens), 2):
        text = html.unescape(re.sub(r"<[^>]+>", " ", tokens[i + 1]))
        text = re.sub(r"\s+", " ", text.split("hdrfn")[0]).strip()
        heading = next((h for h in GROUPS if text.lower().startswith(h.lower())), None)
        if heading is None:
            raise ValueError(f"Unknown group heading near: {text[:60]!r}")
        column, value = GROUPS[heading]
        listed = text[len(heading) :]
        listed = listed.split(" Nest Type Groups")[0].split(" Migration Form Groups")[0]
        listed = listed.split(" Nest Location Groups")[0]
        for name in re.split(r",\s*", listed):
            name = re.sub(r"^and\s+", "", name.strip()).rstrip(". ").strip()
            if name:
                rows.append((column, value, name))
    return pl.DataFrame(rows, schema=["column", "value", "pwrc_name"], orient="row")


def build(page: str, species: pl.DataFrame) -> pl.DataFrame:
    species = lumped_species(species)
    groups = parse_groups(page)
    by_name = {_key(n): a for a, n in species.select("AOU", "English_Common_Name").iter_rows()}
    mapped: list[dict[str, str]] = []
    unknown: list[str] = []
    for column, value, name in groups.iter_rows():
        if name in NAME_MAP:
            current, note = NAME_MAP[name]
            if current is None:
                continue
            missing = [c for c in current if _key(c) not in by_name]
            if missing:
                raise ValueError(f"NAME_MAP for {name!r}: not in the BBS species list: {missing}")
            aous = [by_name[_key(c)] for c in current]
        elif _key(name) in by_name:
            aous, note = [by_name[_key(name)]], ""
        else:
            unknown.append(name)
            continue
        for aou in aous:
            mapped.append(
                {"aou": aou, "column": column, "value": value, "pwrc_name": name, "note": note}
            )
    if unknown:
        raise ValueError(f"Names with no BBS match and no NAME_MAP entry: {sorted(set(unknown))}")

    long = pl.DataFrame(mapped)
    conflicts = (
        long.group_by("aou", "column")
        .agg(pl.col("value").unique().sort())
        .filter(pl.col("value").list.len() > 1)
    )
    if conflicts.height:
        raise ValueError(f"Species in two groups of one kind: {conflicts.to_dicts()}")

    wide = long.pivot(on="column", index="aou", values="value", aggregate_function="first")
    names = long.group_by("aou").agg(
        pl.col("pwrc_name").unique().sort().str.join("; ").alias("pwrc_name"),
        pl.col("note").filter(pl.col("note") != "").unique().sort().str.join("; ").alias("note"),
    )
    current = species.select(
        pl.col("AOU").alias("aou"),
        pl.col("English_Common_Name").alias("common_name"),
        (pl.col("Genus") + " " + pl.col("Species")).alias("scientific_name"),
    )
    out = current.join(wide, on="aou", how="inner").join(names, on="aou", how="left")
    for trait in TRAITS:
        if trait not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.String).alias(trait))
    return out.select(
        "aou",
        "common_name",
        "scientific_name",
        *TRAITS,
        pl.lit(None, dtype=pl.String).alias("forest_guild"),
        "pwrc_name",
        "note",
    ).sort("aou")


def main(argv: list[str]) -> int:
    page_path, species_path, out_path = (Path(a) for a in argv)
    species = pl.read_parquet(species_path)
    table = build(page_path.read_text(encoding="latin-1"), species)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(out_path)
    print(f"wrote {out_path}: {table.height} species from {SOURCE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
