"""State/province codes from the release metadata, and resolution of user state filters.

StateNum codes come only from the release's own metadata XML (the StateNum attribute
domain), never from a hand-typed table. Postal abbreviations map to *names*, which are
then looked up in that domain, so an abbreviation can never point at the wrong number.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import polars as pl

REGION_COLUMNS = ("StateNum", "StateName")

# Postal abbreviation -> region name as spelled in the BBS metadata
POSTAL_ABBREVIATIONS: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories",
    "NS": "Nova Scotia",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}


def parse_state_codes(xml_path: Path) -> pl.DataFrame:
    """Read the StateNum code list ("02,Alabama; 03,Alaska; ...") from release metadata."""
    root = ET.parse(xml_path).getroot()
    domains = {
        (attr.findtext("attrdomv/udom") or "").strip()
        for attr in root.iter("attr")
        if attr.findtext("attrlabl") == "StateNum"
    }
    domains.discard("")
    if len(domains) != 1:
        raise ValueError(
            f"Expected one StateNum code list in '{xml_path.name}', found {len(domains)}."
        )

    rows: list[tuple[int, str]] = []
    for pair in domains.pop().split(";"):
        code, _, name = pair.strip().partition(",")
        if not code.strip().isdigit() or not name.strip():
            raise ValueError(f"Malformed StateNum entry '{pair.strip()}' in '{xml_path.name}'.")
        rows.append((int(code), name.strip()))

    codes = pl.DataFrame(rows, schema={"StateNum": pl.Int64, "StateName": pl.String}, orient="row")
    for col in REGION_COLUMNS:
        dupes = codes.filter(pl.col(col).is_duplicated())[col].unique().to_list()
        if dupes:
            raise ValueError(f"Duplicate {col} values in '{xml_path.name}': {dupes}")
    return codes


def parse_code_labels(xml_path: Path, attribute: str) -> dict[int, str]:
    """Read an enumerated code list (e.g. BCR, Stratum, RunType) from release metadata.

    Returns {code: description}; empty when the metadata has no list for ``attribute``.
    """
    root = ET.parse(xml_path).getroot()
    labels: dict[int, str] = {}
    for attr in root.iter("attr"):
        if attr.findtext("attrlabl") != attribute:
            continue
        for edom in attr.iter("edom"):
            code = (edom.findtext("edomv") or "").strip()
            text = (edom.findtext("edomvd") or "").strip()
            if not code.lstrip("-").isdigit() or not text:
                raise ValueError(f"Malformed {attribute} entry '{code}' in '{xml_path.name}'.")
            if int(code) in labels and labels[int(code)] != text:
                raise ValueError(f"Conflicting {attribute} code {code} in '{xml_path.name}'.")
            labels[int(code)] = text
    return labels


def resolve_states(values: list[int | str], regions: pl.DataFrame | None) -> list[int]:
    """Turn StateNums, region names, or postal abbreviations into StateNum codes.

    Every value must exist in the release's code list; unknown values raise.
    """
    if not values:
        return []
    if regions is None:
        if all(isinstance(v, int) for v in values):
            return [int(v) for v in values]
        raise ValueError(
            "State names or abbreviations need the release metadata XML (region codes), "
            "which this release directory does not have; use StateNum codes."
        )

    by_name = {n.casefold(): c for c, n in regions.select(REGION_COLUMNS).iter_rows()}
    known_codes = set(regions["StateNum"].to_list())
    resolved: list[int] = []
    unknown: list[str] = []
    for value in values:
        if isinstance(value, int):
            code = value if value in known_codes else None
        else:
            key = value.strip()
            name = POSTAL_ABBREVIATIONS.get(key.upper(), key)
            code = by_name.get(name.casefold())
        if code is None:
            unknown.append(str(value))
        else:
            resolved.append(code)
    if unknown:
        valid = ", ".join(f"{c} {n}" for c, n in regions.sort("StateNum").iter_rows())
        raise ValueError(f"Unknown state(s) {unknown}. Valid StateNum codes: {valid}")
    return sorted(set(resolved))
