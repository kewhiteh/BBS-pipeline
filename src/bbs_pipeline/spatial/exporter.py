"""Write the observation table to one file; the format comes from the file extension."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bbs_pipeline.contracts import ExportFormat, OutputContract
from bbs_pipeline.spatial.geometry import build_spatial_geodataframe

VECTOR_DRIVERS: dict[ExportFormat, str] = {
    ExportFormat.GPKG: "GPKG",
    ExportFormat.FLATGEOBUF: "FlatGeobuf",
    ExportFormat.GEOJSON: "GeoJSON",
}


def write_output(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    output: OutputContract,
) -> Path:
    """Write observations to ``output.path``; vector formats get route start points."""
    path = Path(output.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    fmt = output.format
    if fmt == ExportFormat.PARQUET:
        observations_df.write_parquet(path)
        return path
    if fmt == ExportFormat.CSV:
        observations_df.write_csv(path)
        return path

    gdf = build_spatial_geodataframe(
        observations_df, routes_df, overlays=output.overlays, crs=output.crs
    )
    if fmt == ExportFormat.GEOPARQUET:
        gdf.to_parquet(path)
    elif fmt == ExportFormat.GPKG:
        gdf.to_file(path, driver="GPKG", layer=output.layer_name)
    else:
        gdf.to_file(path, driver=VECTOR_DRIVERS[fmt])
    return path
