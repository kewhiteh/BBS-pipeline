"""Fixed-Width USGS SpeciesList.txt Normalizer.

Converts raw USGS Breeding Bird Survey fixed-width ``SpeciesList.txt`` files
(including banner headers and dashed underline formatting rows) into clean
tabular Polars DataFrames and standard CSV files with canonical 5-digit AOU codes.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

SPECIES_COLUMNS: list[str] = [
    "Seq",
    "AOU",
    "English_Common_Name",
    "French_Common_Name",
    "Spanish_Common_Name",
    "Order",
    "Family",
    "Genus",
    "Species",
]

_DASH_LINE_RE = re.compile(r"^[\s\-]+$")
_DASH_SPAN_RE = re.compile(r"-+")
_LEADING_SEQ_AOU_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(.*)$")


def _extract_dash_spans(lines: list[str]) -> list[tuple[int, int | None]] | None:
    """Locate the dashed column underline row and return (start, end) character spans."""
    for line in lines:
        stripped = line.strip()
        if not stripped or "-" not in stripped:
            continue
        if _DASH_LINE_RE.match(line):
            matches = list(_DASH_SPAN_RE.finditer(line))
            if len(matches) == len(SPECIES_COLUMNS):
                spans: list[tuple[int, int | None]] = []
                for idx, m in enumerate(matches):
                    # Allow the final column (Species) to extend to the end of the line
                    end: int | None = None if idx == len(matches) - 1 else m.end()
                    spans.append((m.start(), end))
                return spans
    return None


def _parse_line_with_spans(
    line: str,
    spans: list[tuple[int, int | None]],
) -> dict[str, int | str] | None:
    """Attempt to extract the 9 species columns using fixed-width character spans."""
    raw_fields: list[str] = []
    for start, end in spans:
        # Reject fixed-width slicing if a span boundary cuts directly through a word
        if 0 < start < len(line) and not line[start - 1].isspace() and not line[start].isspace():
            return None
        if (
            end is not None
            and 0 < end < len(line)
            and not line[end - 1].isspace()
            and not line[end].isspace()
        ):
            return None

        if start >= len(line):
            raw_fields.append("")
        elif end is None:
            raw_fields.append(line[start:].strip())
        else:
            raw_fields.append(line[start:end].strip())

    if len(raw_fields) != 9:
        return None
    seq_str, aou_str = raw_fields[0], raw_fields[1]
    if not (seq_str.isdigit() and aou_str.isdigit()):
        return None
    if not raw_fields[2] or not raw_fields[7] or not raw_fields[8]:
        return None

    return {
        "Seq": int(seq_str),
        "AOU": aou_str.zfill(5),
        "English_Common_Name": raw_fields[2],
        "French_Common_Name": raw_fields[3],
        "Spanish_Common_Name": raw_fields[4],
        "Order": raw_fields[5],
        "Family": raw_fields[6],
        "Genus": raw_fields[7],
        "Species": raw_fields[8],
    }


def _parse_line_fallback(line: str) -> dict[str, int | str] | None:
    """Fallback parser using multi-space delimiters and taxonomic tail tokens."""
    match = _LEADING_SEQ_AOU_RE.match(line)
    if not match:
        return None

    seq_str, aou_str, remainder = match.groups()
    remainder = remainder.strip()
    if not remainder:
        return None

    parts = [p.strip() for p in re.split(r"\s{2,}", remainder) if p.strip()]
    if len(parts) == 7:
        eng, fre, spa, order, family, genus, species = parts
    elif len(parts) > 7:
        eng, fre, spa = parts[0], parts[1], parts[2]
        order, family, genus = parts[-4], parts[-3], parts[-2]
        species = parts[-1]
    else:
        # Fallback: extract last 4 whitespace-separated tokens as Order, Family, Genus, Species
        tokens = remainder.split()
        if len(tokens) < 5:
            return None
        order, family, genus, species = tokens[-4], tokens[-3], tokens[-2], tokens[-1]
        names_blob = " ".join(tokens[:-4]).strip()
        name_parts = [p.strip() for p in re.split(r"\s{2,}", names_blob) if p.strip()]
        if len(name_parts) >= 3:
            eng, fre, spa = name_parts[0], name_parts[1], name_parts[2]
        elif len(name_parts) == 2:
            eng, fre, spa = name_parts[0], name_parts[1], ""
        else:
            eng, fre, spa = names_blob, "", ""

    return {
        "Seq": int(seq_str),
        "AOU": aou_str.strip().zfill(5),
        "English_Common_Name": eng,
        "French_Common_Name": fre,
        "Spanish_Common_Name": spa,
        "Order": order,
        "Family": family,
        "Genus": genus,
        "Species": species,
    }


def parse_species_list_lines(raw_text: str) -> pl.DataFrame:
    """Parse raw USGS fixed-width ``SpeciesList.txt`` text into a normalized DataFrame."""
    lines = raw_text.splitlines()
    spans = _extract_dash_spans(lines)
    records: list[dict[str, int | str]] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _DASH_LINE_RE.match(line):
            continue
        # Only lines starting with a numeric sequence number are data rows
        if not _LEADING_SEQ_AOU_RE.match(line):
            continue

        parsed: dict[str, int | str] | None = None
        if spans is not None:
            parsed = _parse_line_with_spans(line, spans)
        if parsed is None:
            parsed = _parse_line_fallback(line)
        if parsed is not None:
            records.append(parsed)

    schema = {
        "Seq": pl.Int64,
        "AOU": pl.String,
        "English_Common_Name": pl.String,
        "French_Common_Name": pl.String,
        "Spanish_Common_Name": pl.String,
        "Order": pl.String,
        "Family": pl.String,
        "Genus": pl.String,
        "Species": pl.String,
    }
    if not records:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(records, schema=schema)


def normalize_species_list_file(txt_path: Path | str, output_csv_path: Path | str) -> pl.DataFrame:
    """Read a fixed-width ``SpeciesList.txt``, write normalized CSV, and return DataFrame."""
    source = Path(txt_path)
    target = Path(output_csv_path)
    try:
        raw_text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = source.read_text(encoding="latin-1")

    df = parse_species_list_lines(raw_text)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(target)
    return df
