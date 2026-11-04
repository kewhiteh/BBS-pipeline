"""Multi-CRS Vector (GeoParquet, GPKG, FlatGeobuf, GeoJSON) & Tabular Exporter."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from bbs_pipeline.contracts import (
    ExportFormat,
    ProtocolConfigContract,
    SpatialExportConfigContract,
)
from bbs_pipeline.spatial.linear_referencing import build_spatial_geodataframe

VECTOR_FORMATS: frozenset[ExportFormat] = frozenset(
    {
        ExportFormat.GEOPARQUET,
        ExportFormat.GPKG,
        ExportFormat.FLATGEOBUF,
        ExportFormat.GEOJSON,
    }
)


def run_spatial_export(
    observations_df: pl.DataFrame,
    routes_df: pl.DataFrame | pl.LazyFrame | None,
    protocol: ProtocolConfigContract,
    spatial: SpatialExportConfigContract,
    guild_rollup_df: pl.DataFrame | None = None,
) -> list[Path]:
    """Export observations to configured tabular and multi-CRS spatial vector formats."""
    output_dir = Path(spatial.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported_paths: list[Path] = []

    requested_formats = set(spatial.export_formats)

    if ExportFormat.PARQUET in requested_formats:
        parquet_path = output_dir / "bbs_observations.parquet"
        observations_df.write_parquet(parquet_path)
        exported_paths.append(parquet_path)

    if ExportFormat.CSV in requested_formats:
        csv_path = output_dir / "bbs_observations.csv"
        observations_df.write_csv(csv_path)
        exported_paths.append(csv_path)

    if ExportFormat.DUCKDB in requested_formats:
        duckdb_path = output_dir / "bbs_observations.duckdb"
        if duckdb_path.exists():
            duckdb_path.unlink()
        with duckdb.connect(str(duckdb_path)) as con:
            con.register("_obs_view", observations_df)
            con.execute("CREATE OR REPLACE TABLE bbs_observations AS SELECT * FROM _obs_view")
            con.unregister("_obs_view")
            if guild_rollup_df is not None:
                con.register("_guild_view", guild_rollup_df)
                con.execute(
                    "CREATE OR REPLACE TABLE bbs_guild_rollup AS SELECT * FROM _guild_view"
                )
                con.unregister("_guild_view")
        exported_paths.append(duckdb_path)

    if spatial.enable_spatial_join and (requested_formats & VECTOR_FORMATS):
        base_gdf = build_spatial_geodataframe(
            observations_df=observations_df,
            routes_df=routes_df,
            protocol=protocol,
            spatial=spatial,
        )

        for crs_code in spatial.target_crs_list:
            gdf_reprojected = (
                base_gdf.to_crs(crs_code)
                if base_gdf.crs is None or base_gdf.crs.to_string().upper() != crs_code.upper()
                else base_gdf
            )
            crs_slug = crs_code.lower().replace(":", "_")

            if ExportFormat.GEOPARQUET in requested_formats:
                gpq_path = output_dir / f"bbs_observations_{crs_slug}.geoparquet"
                gdf_reprojected.to_parquet(gpq_path)
                exported_paths.append(gpq_path)

            if ExportFormat.GPKG in requested_formats:
                gpkg_path = output_dir / f"bbs_observations_{crs_slug}.gpkg"
                if gpkg_path.exists():
                    gpkg_path.unlink()
                gdf_reprojected.to_file(gpkg_path, driver="GPKG")
                exported_paths.append(gpkg_path)

            if ExportFormat.FLATGEOBUF in requested_formats:
                fgb_path = output_dir / f"bbs_observations_{crs_slug}.fgb"
                if fgb_path.exists():
                    fgb_path.unlink()
                gdf_reprojected.to_file(fgb_path, driver="FlatGeobuf")
                exported_paths.append(fgb_path)

            if ExportFormat.GEOJSON in requested_formats:
                geojson_path = output_dir / f"bbs_observations_{crs_slug}.geojson"
                if geojson_path.exists():
                    geojson_path.unlink()
                gdf_reprojected.to_file(geojson_path, driver="GeoJSON")
                exported_paths.append(geojson_path)

    return sorted(exported_paths)
