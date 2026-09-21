"""Authoritative reference constants and lookup registries for the USGS BBS Pipeline.

Contains:
- Bird Conservation Regions (BCRs 1-39) per NABCI.
- USGS BBS Physiographic Strata (Strata 1-99) per Robbins et al. and USGS PWRC.
- Observer experience cohorts (Novice, Intermediate, Veteran) per BBS analytical standards.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# BCR Name Reference Registry (NABCI) - BCR 1 through BCR 39
# ---------------------------------------------------------------------------

BCR_NAMES: Dict[str, str] = {
    "1": "Aleutian/Bering Sea Islands",
    "2": "Western Alaska",
    "3": "Arctic Plains and Mountains",
    "4": "Northwestern Interior Forest",
    "5": "Northern Pacific Rainforest",
    "6": "Boreal Taiga Plains",
    "7": "Taiga Shield and Hudson Plains",
    "8": "Boreal Softwood Shield",
    "9": "Great Basin",
    "10": "Northern Rockies",
    "11": "Prairie Potholes",
    "12": "Boreal Hardwood Transition",
    "13": "Lower Great Lakes/St. Lawrence Plain",
    "14": "Atlantic Northern Forest",
    "15": "Sierra Nevada",
    "16": "Southern Rockies/Colorado Plateau",
    "17": "Badlands and Prairies",
    "18": "Shortgrass Prairie",
    "19": "Central Mixed-Grass Prairie",
    "20": "Edwards Plateau",
    "21": "Oaks and Prairies",
    "22": "Eastern Tallgrass Prairie",
    "23": "Prairie Hardwood Transition",
    "24": "Central Hardwoods",
    "25": "West Gulf Coastal Plain/Ouachitas",
    "26": "Mississippi Alluvial Valley",
    "27": "Southeastern Coastal Plain",
    "28": "Appalachian Mountains",
    "29": "Piedmont",
    "30": "New England/Mid-Atlantic Coast",
    "31": "Peninsular Florida",
    "32": "Coastal California",
    "33": "Sonoran and Mojave Deserts",
    "34": "Sierra Madre Occidental",
    "35": "Chihuahuan Desert",
    "36": "Tamaulipan Brushlands",
    "37": "Gulf Coastal Prairie",
    "38": "Islas Marías",
    "39": "Sierras de Baja California",
}

# ---------------------------------------------------------------------------
# USGS BBS Physiographic Strata Registry (Robbins et al. 1986 / USGS PWRC)
# ---------------------------------------------------------------------------

STRATA_NAMES: Dict[str, str] = {
    "1": "Subtropical",
    "2": "Floridian",
    "3": "Coastal Flatwoods",
    "4": "Upper Coastal Plain",
    "5": "Mississippi Alluvial Plain",
    "6": "Coastal Prairies",
    "7": "South Texas Brushlands",
    "8": "East Texas Prairies",
    "9": "Glaciated Coastal Plain",
    "10": "Northern Piedmont",
    "11": "Southern Piedmont",
    "12": "Southern New England",
    "13": "Ridge and Valley",
    "14": "Highland Rim",
    "15": "Lexington Plain",
    "16": "Great Lakes Plain",
    "17": "Driftless Area",
    "18": "St. Lawrence River Plain",
    "19": "Ozark-Ouachita Plateau",
    "20": "Great Lakes Transition",
    "21": "Cumberland Plateau",
    "22": "Ohio Hills",
    "23": "Blue Ridge Mountains",
    "24": "Allegheny Plateau",
    "25": "Open Boreal Forest",
    "26": "Adirondack Mountains",
    "27": "Northern New England",
    "28": "Northern Spruce-Hardwoods",
    "29": "Closed Boreal Forest",
    "30": "Aspen Parklands",
    "31": "Till Plains",
    "32": "Dissected Till Plains",
    "33": "Osage Plain-Cross Timbers",
    "34": "High Plains Border",
    "35": "Rolling Red Prairies",
    "36": "High Plains",
    "37": "Drift Prairie",
    "38": "Glaciated Missouri Plateau",
    "39": "Great Plains Roughlands",
    "40": "Black Prairie",
    "53": "Edwards Plateau",
    "54": "Rolling Red Plains",
    "55": "Staked Plains",
    "56": "Chihuahuan Desert",
    "61": "Black Hills",
    "62": "Southern Rockies",
    "63": "Fraser Plateau",
    "64": "Central Rockies",
    "65": "Dissected Rockies",
    "66": "Sierra Nevada",
    "67": "Cascade Mountains",
    "68": "Northern Rockies",
    "80": "Great Basin Deserts",
    "81": "Mexican Highlands",
    "82": "Sonoran Desert",
    "83": "Mojave Desert",
    "84": "Pinyon-Juniper Woodlands",
    "85": "Pitt-Klamath Plateau",
    "86": "Wyoming Basin",
    "87": "Intermountain Grasslands",
    "88": "Basin and Range",
    "89": "Columbia Plateau",
    "90": "Southern California Grasslands",
    "91": "Central Valley",
    "92": "California Foothills",
    "93": "Southern Pacific Rainforests",
    "94": "Northern Pacific Rainforests",
    "95": "Los Angeles Ranges",
    "96": "Southern Alaska Coast",
    "98": "Willamette Lowlands",
    "99": "Tundra",
}

# ---------------------------------------------------------------------------
# Standard BBS Observer Experience Cohorts
# ---------------------------------------------------------------------------

OBSERVER_COHORTS: Tuple[str, ...] = ("Novice", "Intermediate", "Veteran")


def classify_observer_cohort(tenure: Optional[int]) -> Optional[str]:
    """Classify observer route tenure into standard BBS experience cohort.

    - Novice: 1 year
    - Intermediate: 2-5 years
    - Veteran: 6+ years

    Parameters
    ----------
    tenure:
        Observer tenure (years surveying route).

    Returns
    -------
    Optional[str]
        One of 'Novice', 'Intermediate', 'Veteran', or None if tenure is None or < 1.
    """
    if tenure is None or tenure < 1:
        return None
    if tenure == 1:
        return "Novice"
    if 2 <= tenure <= 5:
        return "Intermediate"
    return "Veteran"
