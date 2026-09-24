"""Tabular output shaping, provenance stamping, and format serialization.

Implements Tasks 6.2 and 6.3 (docs/04_TASKS.md):
- Supports 'wide' (Stop1..Stop50) and 'long' (unpivoted StopNumber, Count) shapes.
- Queries active Git hash (git rev-parse --short HEAD) with fallback.
- Discovers dataset temporal bounds (dataset_min_year, dataset_max_year).
- Generates ISO 8601 extraction timestamp and stamps pipeline version.
- Serializes to OGC GeoPackage (.gpkg), GeoJSON (.geojson), Parquet (.parquet),
  and CSV (.csv).
- Supports both physical file writes (at the terminal export boundary) and
  in-memory byte streams (io.BytesIO / bytes) adhering to the Zero-Disk Mandate.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import polars as pl
import pyarrow.parquet as pq
import pyproj

from bbs_pipeline.export.spatial import (
    DEFAULT_TARGET_CRS,
    anchor_routes_spatial,
    resolve_crs,
)

logger = logging.getLogger(__name__)

#: Approved serialization formats.
SUPPORTED_FORMATS: tuple[str, ...] = ("parquet", "csv", "geojson", "gpkg")

#: Approved output shapes.
SUPPORTED_SHAPES: tuple[str, ...] = ("wide", "long", "route")

#: Fallback pipeline version string.
PIPELINE_VERSION: str = "0.1.0"


# ---------------------------------------------------------------------------
# Tabular Output Shaping
# ---------------------------------------------------------------------------


def shape_dataset(
    df: pl.DataFrame,
    shape: str = "wide",
    route_key_col: str = "RouteKey",
    year_col: str = "Year",
    aou_col: str = "AOU",
) -> pl.DataFrame:
    """Reshape observation DataFrame into 'wide' or 'long' tabular format.

    - ``'wide'``: Retains `Stop1` through `Stop50` as discrete integer columns.
    - ``'long'``: Unpivots count columns into normalized records:
      `(RouteDataID, RouteKey, Year, AOU, StopNumber, Count)`.

    Parameters
    ----------
    df:
        Polars DataFrame containing BBS observations.
    shape:
        Target shape, either ``"wide"`` or ``"long"``. Defaults to ``"wide"``.
    route_key_col:
        Name of composite route key column. Defaults to ``"RouteKey"``.
    year_col:
        Name of survey year column. Defaults to ``"Year"``.
    aou_col:
        Name of AOU species code column. Defaults to ``"AOU"``.

    Returns
    -------
    pl.DataFrame
        Reshaped Polars DataFrame.

    Raises
    ------
    TypeError:
        If ``df`` is not a Polars DataFrame.
    ValueError:
        If ``shape`` is not in ``("wide", "long")`` or if ``long`` is requested
        on a dataset with no Stop columns.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"Expected 'df' to be a polars.DataFrame, got {type(df).__name__}"
        )

    norm_shape = shape.strip().lower()
    if norm_shape not in SUPPORTED_SHAPES:
        raise ValueError(
            f"Unsupported shape: {shape!r}. Supported shapes are: {SUPPORTED_SHAPES}"
        )

    if norm_shape == "wide":
        return df

    if norm_shape == "route":
        # Route summary shape: drop granular stop/band counts and retain run-level aggregates
        stop_band_cols = [
            c
            for c in df.columns
            if (c.startswith("Stop") and c[4:].isdigit())
            or c in ("Count10", "Count20", "Count30", "Count40", "Count50")
        ]
        collapsed = df.drop([c for c in stop_band_cols if c in df.columns])

        summary_order = [
            "RouteDataID",
            route_key_col,
            "CountryNum",
            "StateNum",
            "Route",
            "RPID",
            year_col,
            aou_col,
            "SpeciesTotal",
            "StopTotal",
        ]
        present_summary = [c for c in summary_order if c in collapsed.columns]
        other_cols = [c for c in collapsed.columns if c not in present_summary]
        ordered_cols = present_summary + other_cols

        sort_cols = [c for c in [route_key_col, year_col, aou_col] if c in ordered_cols]
        if sort_cols:
            return collapsed.select(ordered_cols).sort(sort_cols)
        return collapsed.select(ordered_cols)

    ten_stop_cols = [
        c
        for c in ("Count10", "Count20", "Count30", "Count40", "Count50")
        if c in df.columns
    ]
    is_ten_stop = len(ten_stop_cols) > 0

    if is_ten_stop:
        id_cols = [c for c in df.columns if c not in ten_stop_cols]
        if hasattr(df, "unpivot"):
            melted = df.unpivot(
                index=id_cols,
                on=ten_stop_cols,
                variable_name="StopBand",
                value_name="Count",
            )
        else:
            melted = df.melt(
                id_vars=id_cols,
                value_vars=ten_stop_cols,
                variable_name="StopBand",
                value_name="Count",
            )
    else:
        stop_cols = [c for c in df.columns if c.startswith("Stop") and c[4:].isdigit()]
        stop_cols.sort(key=lambda c: int(c[4:]))

        if not stop_cols:
            raise ValueError(
                "Cannot reshape to 'long': no 'Stop*' count columns found or 'Count10..Count50' count columns in DataFrame."
            )

        id_cols = [c for c in df.columns if c not in stop_cols]
        if hasattr(df, "unpivot"):
            melted = df.unpivot(
                index=id_cols,
                on=stop_cols,
                variable_name="Stop",
                value_name="Count",
            )
        else:
            melted = df.melt(
                id_vars=id_cols,
                value_vars=stop_cols,
                variable_name="Stop",
                value_name="Count",
            )

        melted = melted.with_columns(
            pl.col("Stop")
            .str.replace(r"^Stop", "", literal=False)
            .cast(pl.Int32)
            .alias("StopNumber")
        ).drop("Stop")

    # Defensive cast of Count if unpivoted from raw string stops
    if "Count" in melted.columns and melted["Count"].dtype in (pl.String, pl.Utf8):
        melted = melted.with_columns(
            pl.col("Count").str.strip_chars().cast(pl.Int32, strict=False).fill_null(0)
        )

    # Establish clean logical column ordering
    primary_order = [
        "RouteDataID",
        route_key_col,
        "CountryNum",
        "StateNum",
        "Route",
        "RPID",
        year_col,
        aou_col,
        "StopBand" if is_ten_stop else "StopNumber",
        "Count",
    ]
    leading_cols = [c for c in primary_order if c in melted.columns]
    trailing_cols = [c for c in melted.columns if c not in leading_cols]
    ordered_cols = leading_cols + trailing_cols

    # Sort deterministically
    sort_cols = [
        c
        for c in [
            route_key_col,
            year_col,
            aou_col,
            "StopBand" if is_ten_stop else "StopNumber",
        ]
        if c in ordered_cols
    ]
    result = melted.select(ordered_cols)
    if sort_cols:
        result = result.sort(sort_cols)

    return result


# ---------------------------------------------------------------------------
# Provenance Metadata Extraction
# ---------------------------------------------------------------------------


def get_pipeline_provenance(
    df: pl.DataFrame | gpd.GeoDataFrame | None = None,
    min_year: int | str | None = None,
    max_year: int | str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Extract pipeline execution provenance metadata.

    Captures:
    - ``pipeline_git_hash``: Commit hash from ``git rev-parse --short HEAD``
      (fallback to ``BBS_PIPELINE_GIT_HASH`` or ``"unknown"``).
    - ``dataset_min_year``: Lower bound of survey years in dataset.
    - ``dataset_max_year``: Upper bound of survey years in dataset.
    - ``extraction_timestamp``: ISO 8601 UTC extraction timestamp.
    - ``pipeline_version``: Semantic version of bbs-pipeline package.

    Parameters
    ----------
    df:
        Optional DataFrame used to discover temporal year bounds.
    min_year:
        Explicit lower year bound. If omitted, discovered from ``df``.
    max_year:
        Explicit upper year bound. If omitted, discovered from ``df``.
    extra_metadata:
        Optional additional key-value metadata to include.

    Returns
    -------
    Dict[str, str]
        Dictionary of provenance attributes.
    """
    # 1. Active Git hash
    git_hash: str = "unknown"
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            git_hash = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Could not query git rev-parse: %s", exc)

    if git_hash == "unknown":
        git_hash = os.environ.get("BBS_PIPELINE_GIT_HASH", "unknown")

    # 2. Year bounds discovery
    discovered_min: str | None = str(min_year) if min_year is not None else None
    discovered_max: str | None = str(max_year) if max_year is not None else None

    if (discovered_min is None or discovered_max is None) and df is not None:
        try:
            if isinstance(df, pl.DataFrame) and "Year" in df.columns:
                valid_years = (
                    df.select(pl.col("Year").cast(pl.Int32, strict=False))
                    .filter(pl.col("Year").is_not_null())
                    .get_column("Year")
                )
                if len(valid_years) > 0:
                    if discovered_min is None:
                        discovered_min = str(valid_years.min())
                    if discovered_max is None:
                        discovered_max = str(valid_years.max())
            elif isinstance(df, gpd.GeoDataFrame) and "Year" in df.columns:
                valid_years = df["Year"].dropna().astype(int)
                if len(valid_years) > 0:
                    if discovered_min is None:
                        discovered_min = str(valid_years.min())
                    if discovered_max is None:
                        discovered_max = str(valid_years.max())
        except (
            pl.exceptions.PolarsError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
        ) as exc:
            logger.debug("Failed to discover year bounds: %s", exc)

    provenance: dict[str, str] = {
        "pipeline_git_hash": git_hash,
        "dataset_min_year": discovered_min if discovered_min is not None else "unknown",
        "dataset_max_year": discovered_max if discovered_max is not None else "unknown",
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
    }

    if extra_metadata:
        for k, v in extra_metadata.items():
            provenance[str(k)] = str(v)

    return provenance


# ---------------------------------------------------------------------------
# Individual Format Exporters
# ---------------------------------------------------------------------------


def export_to_parquet(
    df: pl.DataFrame | gpd.GeoDataFrame,
    output_path: str | Path | None = None,
    metadata: dict[str, str] | None = None,
) -> bytes:
    """Export tabular dataset to Apache Parquet with embedded schema metadata.

    Parameters
    ----------
    df:
        Polars DataFrame or GeoPandas GeoDataFrame.
    output_path:
        Optional file path destination. If None, operates purely in RAM.
    metadata:
        Optional provenance metadata dictionary to embed into Parquet schema.

    Returns
    -------
    bytes
        Serialized Parquet bytes.
    """
    if isinstance(df, gpd.GeoDataFrame):
        import pandas as pd

        pdf = pd.DataFrame(df)
        if "geometry" in pdf.columns:
            pdf["geometry"] = pdf["geometry"].apply(
                lambda g: g.wkt if g is not None else None
            )
        pl_df = pl.from_pandas(pdf)
        table = pl_df.to_arrow()
    elif isinstance(df, pl.DataFrame):
        table = df.to_arrow()
    else:
        raise TypeError(
            f"Expected Polars DataFrame or GeoPandas GeoDataFrame, got {type(df).__name__}"
        )

    # Embed schema metadata
    if metadata:
        existing_meta = table.schema.metadata or {}
        encoded_meta = {
            **existing_meta,
            **{k.encode("utf-8"): str(v).encode("utf-8") for k, v in metadata.items()},
        }
        table = table.replace_schema_metadata(encoded_meta)

    buf = io.BytesIO()
    pq.write_table(table, buf)
    data = buf.getvalue()

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    return data


def export_to_csv(
    df: pl.DataFrame | gpd.GeoDataFrame,
    output_path: str | Path | None = None,
    metadata: dict[str, str] | None = None,
) -> bytes:
    """Export tabular dataset to CSV with prepended metadata comment header.

    Parameters
    ----------
    df:
        Polars DataFrame or GeoPandas GeoDataFrame.
    output_path:
        Optional destination file path. If None, operates purely in RAM.
    metadata:
        Optional provenance metadata dictionary prepended as '# key: value'.

    Returns
    -------
    bytes
        Serialized CSV bytes (UTF-8).
    """
    if isinstance(df, gpd.GeoDataFrame):
        import pandas as pd

        pdf = pd.DataFrame(df)
        if "geometry" in pdf.columns:
            pdf["geometry"] = pdf["geometry"].apply(
                lambda g: g.wkt if g is not None else None
            )
        pl_df = pl.from_pandas(pdf)
    elif isinstance(df, pl.DataFrame):
        pl_df = df
    else:
        raise TypeError(
            f"Expected Polars DataFrame or GeoPandas GeoDataFrame, got {type(df).__name__}"
        )

    csv_body = pl_df.write_csv()

    if metadata:
        meta_lines = [f"# {k}: {v}" for k, v in metadata.items()]
        csv_text = "\n".join(meta_lines) + "\n" + csv_body
    else:
        csv_text = csv_body

    data = csv_text.encode("utf-8")

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    return data


def export_to_geojson(
    gdf: gpd.GeoDataFrame,
    output_path: str | Path | None = None,
    metadata: dict[str, str] | None = None,
) -> bytes:
    """Export spatial dataset to RFC 7946 GeoJSON with top-level metadata object.

    Parameters
    ----------
    gdf:
        GeoPandas GeoDataFrame with valid geometries and CRS.
    output_path:
        Optional destination file path. If None, operates purely in RAM.
    metadata:
        Optional provenance metadata dictionary embedded at root of FeatureCollection.

    Returns
    -------
    bytes
        Serialized GeoJSON bytes (UTF-8).
    """
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError(
            f"Expected 'gdf' to be a geopandas.GeoDataFrame, got {type(gdf).__name__}"
        )

    # GeoJSON specification requires WGS 84 (EPSG:4326)
    export_gdf = gdf
    if export_gdf.crs is not None and export_gdf.crs != resolve_crs("EPSG:4326"):
        export_gdf = export_gdf.to_crs("EPSG:4326")

    raw_geojson = export_gdf.to_json()
    geojson_dict = json.loads(raw_geojson)

    if metadata:
        geojson_dict["metadata"] = metadata

    formatted = json.dumps(geojson_dict, indent=2)
    data = formatted.encode("utf-8")

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    return data


def export_to_gpkg(
    gdf: gpd.GeoDataFrame,
    output_path: str | Path | None = None,
    metadata: dict[str, str] | None = None,
    layer_name: str = "bbs_observations",
) -> bytes:
    """Export spatial dataset to OGC GeoPackage (.gpkg) with metadata table.

    Embeds provenance attributes in a dedicated SQLite table: ``pipeline_metadata``
    with columns ``(key TEXT PRIMARY KEY, value TEXT)``.

    Parameters
    ----------
    gdf:
        GeoPandas GeoDataFrame.
    output_path:
        Optional destination file path. If None, operates purely in RAM.
    metadata:
        Optional provenance metadata dictionary.
    layer_name:
        Layer name within the GeoPackage. Defaults to ``"bbs_observations"``.

    Returns
    -------
    bytes
        Serialized GeoPackage SQLite bytes.
    """
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError(
            f"Expected 'gdf' to be a geopandas.GeoDataFrame, got {type(gdf).__name__}"
        )

    # Write layer to in-memory buffer via GeoPandas / Pyogrio
    buf = io.BytesIO()
    gdf.to_file(buf, driver="GPKG", layer=layer_name)
    raw_gpkg_bytes = buf.getvalue()

    # Connect to in-memory SQLite to inject pipeline_metadata table
    con = sqlite3.connect(":memory:")
    con.deserialize(raw_gpkg_bytes)

    if metadata:
        with con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_metadata ("
                "key TEXT PRIMARY KEY, "
                "value TEXT"
                ")"
            )
            con.executemany(
                "INSERT OR REPLACE INTO pipeline_metadata (key, value) VALUES (?, ?)",
                [(str(k), str(v)) for k, v in metadata.items()],
            )

    serialized = con.serialize()
    con.close()

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(serialized)

    return serialized


# ---------------------------------------------------------------------------
# Integrated Multi-Format Export Orchestrator
# ---------------------------------------------------------------------------


def append_community_metrics(df: pl.DataFrame) -> pl.DataFrame:
    """Calculate and append run-level community diversity metrics (Richness, Shannon H', Evenness).

    Metrics are computed per (RouteKey, Year, RPID) grouping over species with SpeciesTotal > 0.
    """
    if (
        "SpeciesTotal" not in df.columns
        or "RouteKey" not in df.columns
        or "Year" not in df.columns
    ):
        return df

    group_keys = ["RouteKey", "Year"]
    if "RPID" in df.columns:
        group_keys.append("RPID")

    # Filter to detected species for richness and diversity index calculations
    st_numeric = (
        pl.col("SpeciesTotal")
        .str.strip_chars()
        .cast(pl.Int32, strict=False)
        .fill_null(0)
        if df["SpeciesTotal"].dtype in (pl.String, pl.Utf8)
        else pl.col("SpeciesTotal").cast(pl.Int32, strict=False).fill_null(0)
    )
    detected = df.filter(st_numeric > 0)
    if detected.is_empty():
        return df.with_columns(
            [
                pl.lit(0, dtype=pl.Int32).alias("CommunityRichness"),
                pl.lit(0, dtype=pl.Int32).alias("CommunityTotalIndividuals"),
                pl.lit(0.0, dtype=pl.Float64).alias("ShannonDiversity"),
                pl.lit(0.0, dtype=pl.Float64).alias("ShannonEvenness"),
            ]
        )

    run_totals = detected.group_by(group_keys).agg(
        st_numeric.sum().alias("CommunityTotalIndividuals"),
        pl.col("AOU").n_unique().alias("CommunityRichness"),
    )

    metrics_df = (
        detected.join(run_totals, on=group_keys, how="inner")
        .with_columns(
            (
                st_numeric.cast(pl.Float64)
                / pl.col("CommunityTotalIndividuals").cast(pl.Float64)
            ).alias("_p_i")
        )
        .with_columns((-pl.col("_p_i") * pl.col("_p_i").log()).alias("_h_term"))
        .group_by(group_keys)
        .agg(
            pl.first("CommunityTotalIndividuals"),
            pl.first("CommunityRichness"),
            pl.col("_h_term").sum().alias("ShannonDiversity"),
        )
        .with_columns(
            pl.when(pl.col("CommunityRichness") > 1)
            .then(pl.col("ShannonDiversity") / pl.col("CommunityRichness").log())
            .otherwise(pl.lit(0.0, dtype=pl.Float64))
            .alias("ShannonEvenness")
        )
    )

    return df.join(metrics_df, on=group_keys, how="left", coalesce=True).with_columns(
        [
            pl.col("CommunityRichness").fill_null(0).cast(pl.Int32),
            pl.col("CommunityTotalIndividuals").fill_null(0).cast(pl.Int32),
            pl.col("ShannonDiversity").fill_null(0.0).cast(pl.Float64),
            pl.col("ShannonEvenness").fill_null(0.0).cast(pl.Float64),
        ]
    )


def serialize_dataset(
    data: pl.DataFrame | gpd.GeoDataFrame,
    format: str,
    output_path: str | Path | None = None,
    shape: str = "wide",
    routes_df: pl.DataFrame | None = None,
    target_crs: str | int | pyproj.CRS = DEFAULT_TARGET_CRS,
    metadata: dict[str, Any] | None = None,
    layer_name: str = "bbs_observations",
    community_metrics: bool = False,
) -> bytes | Path:
    """Serialize BBS dataset with shaping, spatial anchoring, and provenance.

    Parameters
    ----------
    data:
        Observation dataset (Polars DataFrame or GeoPandas GeoDataFrame).
    format:
        Target format: ``"gpkg"``, ``"geojson"``, ``"parquet"``, or ``"csv"``.
    output_path:
        Optional output destination path. If provided, writes file to disk and
        returns ``Path(output_path)``. If None, operates strictly in-memory and
        returns ``bytes``.
    shape:
        Tabular shape: ``"wide"`` (default) or ``"long"``.
    routes_df:
        Optional route geographic directory DataFrame (from routes.csv).
        Required when exporting to spatial formats (``gpkg``, ``geojson``) from
        an un-anchored Polars DataFrame.
    target_crs:
        Target projection CRS when spatial anchoring is performed. Defaults to
        ``"EPSG:4326"``.
    metadata:
        Optional extra metadata attributes to embed.
    layer_name:
        Layer name for GeoPackage export. Defaults to ``"bbs_observations"``.

    Returns
    -------
    Union[bytes, Path]
        Bytes buffer if ``output_path`` is None, or ``Path(output_path)`` if
        written to disk.

    Raises
    ------
    ValueError:
        If ``format`` is not supported, if ``shape`` is invalid, or if spatial
        export is requested on data without coordinates/geometry.
    TypeError:
        If ``data`` is not a supported DataFrame type.
    """
    norm_format = format.strip().lower().lstrip(".")
    if norm_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported format: {format!r}. Supported formats are: {SUPPORTED_FORMATS}"
        )

    # 1. Apply tabular shaping if input is Polars DataFrame
    working_data = data
    if isinstance(working_data, pl.DataFrame):
        if community_metrics:
            working_data = append_community_metrics(working_data)
        working_data = shape_dataset(working_data, shape=shape)

    # 2. Extract provenance metadata
    provenance = get_pipeline_provenance(df=working_data, extra_metadata=metadata)

    # 3. Handle spatial formats: gpkg, geojson
    if norm_format in ("gpkg", "geojson"):
        if isinstance(working_data, gpd.GeoDataFrame):
            spatial_gdf = working_data
            if spatial_gdf.crs != resolve_crs(target_crs):
                spatial_gdf = spatial_gdf.to_crs(resolve_crs(target_crs))
        elif isinstance(working_data, pl.DataFrame):
            if routes_df is not None:
                spatial_gdf = anchor_routes_spatial(
                    df=working_data,
                    routes_df=routes_df,
                    target_crs=target_crs,
                )
            elif (
                "Latitude" in working_data.columns
                and "Longitude" in working_data.columns
            ):
                spatial_gdf = anchor_routes_spatial(
                    df=working_data,
                    routes_df=working_data,
                    target_crs=target_crs,
                )
            else:
                raise ValueError(
                    f"Format '{norm_format}' requires spatial coordinates. Supply "
                    "'routes_df' to anchor starting coordinates 1:1, or pass a "
                    "GeoPandas GeoDataFrame with geometry."
                )
        else:
            raise TypeError(
                f"Expected Polars DataFrame or GeoPandas GeoDataFrame, got {type(working_data).__name__}"
            )

        if norm_format == "geojson":
            output_bytes = export_to_geojson(
                gdf=spatial_gdf,
                output_path=output_path,
                metadata=provenance,
            )
        else:
            output_bytes = export_to_gpkg(
                gdf=spatial_gdf,
                output_path=output_path,
                metadata=provenance,
                layer_name=layer_name,
            )
    elif norm_format == "parquet":
        output_bytes = export_to_parquet(
            df=working_data,
            output_path=output_path,
            metadata=provenance,
        )
    elif norm_format == "csv":
        output_bytes = export_to_csv(
            df=working_data,
            output_path=output_path,
            metadata=provenance,
        )
    else:
        raise ValueError(f"Unhandled format: {norm_format}")

    if output_path is not None:
        return Path(output_path)
    return output_bytes


#: Pipeline convenience alias
export_dataset = serialize_dataset
