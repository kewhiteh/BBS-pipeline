"""Spatial anchoring and coordinate reprojection engine for USGS BBS Pipeline.

Implements Task 6.1 (docs/04_TASKS.md) and spatial constraints (docs/05_CONSTRAINTS.md):
- Anchors route records strictly 1:1 to starting origin coordinates from
  routes.csv (NAD83 / EPSG:4269).
- Prohibits interpolated intermediate stop coordinates.
- Reprojects to target CRS: EPSG:4326 (WGS 84), EPSG:5070 (CONUS Albers),
  EPSG:32119 (NC State Plane), or arbitrary user-supplied EPSG/CRS codes.
- Adheres strictly to the Tool Lock: pandas and geopandas are permitted solely
  in spatial.py for terminal vector layer creation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

import geopandas as gpd
import polars as pl
import pyproj
from shapely.geometry import Point

from bbs_pipeline.core.filters import add_route_key

logger = logging.getLogger(__name__)

#: Named CRS aliases and geographic coordinate system presets.
CRS_PRESETS: Dict[str, str] = {
    "nc_state_plane": "EPSG:32119",
    "nc_sp": "EPSG:32119",
    "conus_albers": "EPSG:5070",
    "albers": "EPSG:5070",
    "wgs84": "EPSG:4326",
    "nad83": "EPSG:4269",
    "web_mercator": "EPSG:3857",
}

#: Default source CRS for USGS BBS route starting coordinates (NAD83).
DEFAULT_SOURCE_CRS: str = "EPSG:4269"

#: Default target projection (WGS 84 geographic 2D).
DEFAULT_TARGET_CRS: str = "EPSG:4326"


def resolve_crs(crs: Union[str, int, pyproj.CRS]) -> pyproj.CRS:
    """Resolve and validate a CRS identifier or named alias into a pyproj.CRS.

    Supports:
    - Standard EPSG strings (e.g. ``"EPSG:4326"``, ``"EPSG:5070"``, ``"EPSG:32119"``)
    - Integer EPSG codes (e.g. ``4326``, ``5070``, ``32119``)
    - Named preset strings (e.g. ``"nc_state_plane"``, ``"conus_albers"``, ``"wgs84"``)
    - Existing ``pyproj.CRS`` instances

    Parameters
    ----------
    crs:
        CRS specification string, integer, or pyproj.CRS instance.

    Returns
    -------
    pyproj.CRS
        Validated pyproj Coordinate Reference System object.

    Raises
    ------
    TypeError:
        If ``crs`` is neither a string, integer, nor pyproj.CRS.
    ValueError:
        If the CRS string or code cannot be resolved to a valid projection.
    """
    if isinstance(crs, pyproj.CRS):
        return crs

    if not isinstance(crs, (str, int)):
        raise TypeError(
            f"Expected CRS to be a string, integer, or pyproj.CRS, got {type(crs).__name__}"
        )

    if isinstance(crs, str):
        normalized = crs.strip().lower()
        if normalized in CRS_PRESETS:
            crs_input: Union[str, int] = CRS_PRESETS[normalized]
        else:
            crs_input = crs
    else:
        crs_input = crs

    try:
        return pyproj.CRS.from_user_input(crs_input)
    except Exception as exc:
        raise ValueError(
            f"Invalid or unresolvable CRS specification: {crs!r}. "
            f"Ensure a valid EPSG code (e.g. 'EPSG:4326', 'EPSG:5070', 'EPSG:32119') "
            f"or named preset {list(CRS_PRESETS.keys())} is supplied."
        ) from exc


def anchor_routes_spatial(
    df: pl.DataFrame,
    routes_df: pl.DataFrame,
    target_crs: Union[str, int, pyproj.CRS] = DEFAULT_TARGET_CRS,
    source_crs: Union[str, int, pyproj.CRS] = DEFAULT_SOURCE_CRS,
    route_key_col: str = "RouteKey",
    drop_null_coordinates: bool = False,
) -> gpd.GeoDataFrame:
    """Join route origin coordinates 1:1 to records and reproject to target CRS.

    Implements strict 1:1 route origin anchoring:
    - Route features anchor strictly to starting coordinates (`Latitude`, `Longitude`
      from `routes.csv` in NAD83 / EPSG:4269).
    - Interpolating intermediate stop coordinates is prohibited.
    - Geometry type is strictly Point(x=Longitude, y=Latitude).
    - Reprojection to target CRS is performed via PyProj / GeoPandas.

    Parameters
    ----------
    df:
        Polars DataFrame containing BBS observations or route records.
    routes_df:
        Polars DataFrame containing route catalog records (from `routes.csv`)
        with ``Latitude`` and ``Longitude`` columns.
    target_crs:
        Target projection CRS string, code, or pyproj.CRS. Defaults to ``"EPSG:4326"``.
    source_crs:
        Source projection CRS of origin coordinates in ``routes_df``. Defaults to
        ``"EPSG:4269"`` (NAD83).
    route_key_col:
        Name of composite route key column. Defaults to ``"RouteKey"``.
    drop_null_coordinates:
        If True, drops records without valid coordinates. If False, keeps records
        with null geometry. Defaults to False.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame anchored 1:1 to route starting coordinates and reprojected
        to ``target_crs``.

    Raises
    ------
    TypeError:
        If ``df`` or ``routes_df`` is not a Polars DataFrame.
    ValueError:
        If ``Latitude`` or ``Longitude`` is missing from ``routes_df``, or if
        ``route_key_col`` cannot be established on both DataFrames.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"Expected 'df' to be a polars.DataFrame, got {type(df).__name__}"
        )
    if not isinstance(routes_df, pl.DataFrame):
        raise TypeError(
            f"Expected 'routes_df' to be a polars.DataFrame, got {type(routes_df).__name__}"
        )

    # Validate coordinate columns in routes catalog
    required_coord_cols = {"Latitude", "Longitude"}
    missing_coords = required_coord_cols - set(routes_df.columns)
    if missing_coords:
        raise ValueError(
            f"'routes_df' is missing required coordinate columns: {missing_coords}. "
            "Must contain 'Latitude' and 'Longitude' from routes.csv."
        )

    # Resolve and validate CRS
    src_proj = resolve_crs(source_crs)
    tgt_proj = resolve_crs(target_crs)

    # Ensure RouteKey exists on df
    working_df = df
    if route_key_col not in working_df.columns:
        if {"CountryNum", "StateNum", "Route"}.issubset(working_df.columns):
            working_df = add_route_key(working_df)
        else:
            raise ValueError(
                f"Column '{route_key_col}' not found in 'df', and composite route parts "
                "('CountryNum', 'StateNum', 'Route') are not present to construct it."
            )

    # Ensure RouteKey exists on routes_df
    working_routes = routes_df
    if route_key_col not in working_routes.columns:
        if {"CountryNum", "StateNum", "Route"}.issubset(working_routes.columns):
            working_routes = add_route_key(working_routes)
        else:
            raise ValueError(
                f"Column '{route_key_col}' not found in 'routes_df', and composite route parts "
                "('CountryNum', 'StateNum', 'Route') are not present to construct it."
            )

    # Extract distinct route coordinate dictionary to ensure strict 1:1 origin anchoring
    # and avoid Cartesian duplication if routes_df contains duplicates
    route_coords = (
        working_routes.select([route_key_col, "Latitude", "Longitude"])
        .unique(subset=[route_key_col], keep="first")
    )

    # Drop existing Latitude/Longitude if already in working_df to avoid duplicate suffix collision
    cols_to_select = [
        c for c in working_df.columns if c not in ("Latitude", "Longitude")
    ]
    cleaned_df = working_df.select(cols_to_select)

    # 1:1 Left join route origin coordinates
    joined_pl = cleaned_df.join(
        route_coords, on=route_key_col, how="left", coalesce=True
    )

    if drop_null_coordinates:
        joined_pl = joined_pl.filter(
            pl.col("Latitude").is_not_null() & pl.col("Longitude").is_not_null()
        )

    # Boundary transition to Pandas/GeoPandas strictly for vector layer creation
    pdf = joined_pl.to_pandas()

    # Construct Point geometries: Point(x=Longitude, y=Latitude)
    # Handle rows where coordinates may be null
    valid_coords_mask = pdf["Longitude"].notna() & pdf["Latitude"].notna()
    geometries = [
        Point(xy) if valid else None
        for valid, xy in zip(
            valid_coords_mask,
            zip(pdf["Longitude"], pdf["Latitude"]),
        )
    ]

    gdf = gpd.GeoDataFrame(pdf, geometry=geometries, crs=src_proj)

    # Reproject to target CRS if different from source CRS
    if src_proj != tgt_proj:
        gdf = gdf.to_crs(tgt_proj)

    return gdf


def reproject_geodataframe(
    gdf: gpd.GeoDataFrame,
    target_crs: Union[str, int, pyproj.CRS],
) -> gpd.GeoDataFrame:
    """Reproject an existing GeoDataFrame to a target CRS.

    Parameters
    ----------
    gdf:
        GeoDataFrame with an existing CRS defined.
    target_crs:
        Target projection CRS string, integer code, or pyproj.CRS.

    Returns
    -------
    gpd.GeoDataFrame
        Reprojected GeoDataFrame.

    Raises
    ------
    TypeError:
        If ``gdf`` is not a GeoDataFrame.
    ValueError:
        If ``gdf.crs`` is None or if ``target_crs`` cannot be resolved.
    """
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError(
            f"Expected 'gdf' to be a geopandas.GeoDataFrame, got {type(gdf).__name__}"
        )
    if gdf.crs is None:
        raise ValueError(
            "GeoDataFrame has no CRS defined; cannot reproject without source CRS."
        )

    resolved_target = resolve_crs(target_crs)
    if gdf.crs == resolved_target:
        return gdf

    return gdf.to_crs(resolved_target)
