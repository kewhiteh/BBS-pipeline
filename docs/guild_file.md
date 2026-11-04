# Guild file

A guild file assigns BBS species to any traits you choose: habitat, forest guild, nest
type, and so on. It is a CSV with **one row per species**. Point a run profile at it:

```toml
[guilds]
file = "guilds/ghy5812.csv"
filters = { forest_guild = ["interior", "interior_edge"] }   # optional
unmatched = "report"                                          # or "error"

[joins]
tables = ["species", "routes", "bcr", "guilds"]   # "guilds" adds the trait columns
```

## Columns

| Column | Required | Meaning |
|---|---|---|
| `aou` | one of the three | BBS AOU code, e.g. `07550` or `7550` |
| `scientific_name` | one of the three | Genus and species as in the BBS species list, e.g. `Setophaga citrina` |
| `common_name` | one of the three | English name as in the BBS species list, e.g. `Hooded Warbler` |
| *anything else* | at least one | A trait. Every other column is a trait you can filter on or join. |

Each row needs at least one of `aou`, `scientific_name`, or `common_name`. Fill in more
than one if you like; they are cross-checked. Header names for the three identifiers
are case-insensitive. Trait names are used exactly as written, so pick names that are
not already BBS output columns (`Family`, `Order`, `RouteName`, ...).

## Starter file

[`guilds/pwrc_species_groups.csv`](../guilds/pwrc_species_groups.csv) covers 412 BBS
species with the USGS Patuxent breeding-habitat, nest-type, migration, and nest-location
groups, plus an empty `forest_guild` column to fill in. See
[guilds/README.md](../guilds/README.md) for its source and how old names were matched.

## Example

[`examples/guilds_example.csv`](../examples/guilds_example.csv). **This is a format
example, not a vetted guild classification.**

```csv
aou,common_name,scientific_name,habitat,forest_guild,notes
07550,Wood Thrush,Hylocichla mustelina,woodland,interior,all three identifiers
06740,,,woodland,interior,AOU only (Ovenbird)
,,Setophaga citrina,woodland,interior,scientific name only (Hooded Warbler)
,Scarlet Tanager,,woodland,interior,common name only
06240,Red-eyed Vireo,,woodland,interior_edge,
05930,Northern Cardinal,Cardinalis cardinalis,woodland,interior_edge,
05980,Indigo Bunting,Passerina cyanea,scrub,edge,
07610,American Robin,Turdus migratorius,urban,generalist,
```

## How rows are matched

1. `aou`, then `scientific_name`, then `common_name`, against the species list of the
   release you are using. Case and extra spaces are ignored.
2. If a row's identifiers point at **different** species, the run stops and names the
   line. Two rows for the **same** species also stop the run.
3. A row that matches nothing is **reported** by `bbs inspect` and `bbs run` and listed
   in the output manifest. With `unmatched = "error"` it stops the run instead.
4. Species recorded on the kept runs that have **no guild row** are listed too, so you
   can see what your file does not cover in your study area.

With `observations.lump_subspecies = true` (the default), every subspecies, form, and
within-species hybrid is counted under its species, using the release's scientific names
(e.g. Gray-headed and Oregon Junco under Dark-eyed Junco, `Junco hyemalis`). Give the
species one row; its plain name ("Dark-eyed Junco") or the species-level AOU works.

### Old names will not match

Names in older literature (e.g. Whitcomb et al. 1981; Freemark & Collins 1992) often
differ from the current BBS species list. *Dendroica* warblers are now *Setophaga*
(Cerulean Warbler is `Setophaga cerulea`, AOU `06580`); Kentucky Warbler is
`Geothlypis formosa` (`06770`); Louisiana Waterthrush is `Parkesia motacilla`
(`06760`). The AOU code is the most reliable identifier. Run `bbs inspect` after
editing the file; the unmatched list shows exactly which rows to fix.

## Filters

`filters` keeps species whose trait values match:

- **OR within a trait**: `forest_guild = ["interior", "interior_edge"]` keeps either.
- **AND across traits**: adding `habitat = ["woodland"]` also requires woodland.
- Values match case-insensitively. A trait column or value that does not exist in the
  file stops the run, so a typo cannot silently drop species.
- Filters combine with the `[species]` lists by **AND**: `[species] families =
  ["Parulidae"]` plus `forest_guild = ["interior"]` keeps interior warblers. With no
  `[species]` lists, the guild filters alone decide.
- Species without a guild row cannot pass a filter, so they are dropped when filters
  are set (and listed in the report).
