# Guild files

## `pwrc_species_groups.csv`

A starter guild file for every BBS species on the USGS Patuxent Wildlife Research Center
**"List of Species Groupings"** (`https://www.mbr-pwrc.usgs.gov/bbs15/guildlst.html`).
That site has been retired, so the copy used is the Internet Archive snapshot of
2026-03-09, saved here as `source/pwrc_guildlst_archived_2026-03-09.html`.

| Column | Values |
|---|---|
| `aou`, `common_name`, `scientific_name` | Current BBS species list (2026 release) |
| `breeding_habitat` | `grassland`, `wetland_open_water`, `successional_scrub`, `woodland`, `urban` |
| `nest_type` | `cavity`, `open_cup_passerine` |
| `migration` | `short_distance`, `permanent_resident`, `neotropical` |
| `nest_location` | `ground_low`, `midstory_canopy` |
| `forest_guild` | **Empty: fill in yourself** (e.g. `interior`, `interior_edge`, `edge`, `generalist`) |
| `pwrc_name` | The name(s) as printed on the Patuxent list |
| `note` | How an old name was matched: `spelling`, `renamed`, `split`, `subspecies form` |

A blank group cell means the source list does not place that species in any group of
that kind (the nest-type list, for example, covers only cavity nesters and open-cup
passerines).

### How old names were matched

The list uses 1990s and abbreviated names. Each was matched to the current BBS species
list by name, or through the hand-made table `NAME_MAP` in
[`scripts/build_pwrc_guilds.py`](../scripts/build_pwrc_guilds.py). AOU codes are always
looked up in the release, never typed by hand.

- **Abbreviations and spelling**: `Grt. crested flycatcher` → Great Crested Flycatcher.
- **Renames**: Rock dove → Rock Pigeon, Gray jay → Canada Jay, Common moorhen → Common
  Gallinule, Common snipe → Wilson's Snipe, and others (`note = renamed`).
- **Splits**: the old species' groups are given to **every** daughter species, e.g.
  Rufous-sided towhee → Eastern and Spotted Towhee; Solitary vireo → Blue-headed,
  Cassin's, and Plumbeous Vireo (`note = split`). Check these if a daughter species
  matters to your study.
- **Subspecies forms**: Myrtle/Audubon's warbler → Yellow-rumped Warbler; Slate-colored,
  Oregon, and Gray-headed junco → Dark-eyed Junco; the flickers → Northern Flicker. The
  pipeline counts every form under its species (see `bbs_pipeline/taxonomy.py`).
- **Dropped**: Northwestern crow (now part of American Crow, which has its own row).

### Adding forest guilds

Copy the file (e.g. to `guilds/ghy5812.csv`), fill `forest_guild` for the woodland species
from Freemark & Collins (1992) and Whitcomb et al. (1981), and point the run profile at
the copy. See [docs/guild_file.md](../docs/guild_file.md) for the format and filters.

### Rebuilding

```bash
uv run python scripts/build_pwrc_guilds.py guilds/source/pwrc_guildlst_archived_2026-03-09.html \
    data/sanitized_cache/species.parquet guilds/pwrc_species_groups.csv
```

The build stops if a name has no match, if a `NAME_MAP` target is not in the species
list, or if a species would land in two groups of one kind.
