"""Multi-format vector export engine (Track B Phase 4)."""

import io
import os
import tempfile

import geopandas as gpd
import polars as pl
import pyogrio
import shapely


def _to_geodataframe(df: pl.DataFrame, layer_type: str) -> gpd.GeoDataFrame:
    """Transform Polars DataFrame into GeoDataFrame strictly at the serialization boundary."""
    pdf = df.to_pandas()

    if layer_type == "stops":
        if "StopLatitude" not in pdf.columns or "StopLongitude" not in pdf.columns:
            raise ValueError(
                "Required coordinate columns missing or malformed in stops_df."
            )

        # Check for malformed data
        if pdf["StopLatitude"].isna().any() or pdf["StopLongitude"].isna().any():
            raise ValueError(
                "Required coordinate columns missing or malformed in stops_df."
            )

        geometry = shapely.points(pdf["StopLongitude"], pdf["StopLatitude"])
    elif layer_type == "routes":
        if "Latitude" not in pdf.columns or "Longitude" not in pdf.columns:
            raise ValueError(
                "Required coordinate columns missing or malformed in routes_df."
            )

        # Check for malformed data
        if pdf["Latitude"].isna().any() or pdf["Longitude"].isna().any():
            raise ValueError(
                "Required coordinate columns missing or malformed in routes_df."
            )

        geometry = shapely.points(pdf["Longitude"], pdf["Latitude"])
    else:
        raise ValueError(f"Unknown layer type: {layer_type}")

    return gpd.GeoDataFrame(pdf, geometry=geometry, crs="EPSG:4326")


def export_vector_layers(
    routes_df: pl.DataFrame, stops_df: pl.DataFrame, format_name: str = "parquet"
) -> io.BytesIO:
    """Export routes and stops to the requested format.

    Supported formats: GeoPackage (.gpkg), FlatGeobuf (.fgb), GeoParquet (.parquet).

    Parameters
    ----------
    routes_df : pl.DataFrame
        Routes data.
    stops_df : pl.DataFrame
        Stops data.
    format_name : str
        Format to export ('gpkg', 'fgb', 'parquet').

    Returns
    -------
    io.BytesIO
        In-memory bytes buffer containing the exported data.
    """
    valid_formats = {"gpkg", "fgb", "parquet"}
    if format_name.lower() not in valid_formats:
        raise ValueError(f"Unsupported export format extension: {format_name}")

    routes_gdf = _to_geodataframe(routes_df, "routes")
    stops_gdf = _to_geodataframe(stops_df, "stops")

    buf = io.BytesIO()

    if format_name.lower() == "gpkg":
        # GeoPackage requires two layers.
        # Using a temporary file securely since pyogrio cannot append to an in-memory BytesIO directly.
        # But wait! I will use pyogrio to write directly to BytesIO for routes, and just return it if we can't append,
        # but the spec says "dual layers: routes and stops". So we use tempfile and read bytes to avoid disk leaks.
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # write first layer
            pyogrio.write_dataframe(
                routes_gdf, tmp_path, layer="bbs_routes", driver="GPKG"
            )
            # append second layer
            pyogrio.write_dataframe(
                stops_gdf, tmp_path, layer="bbs_stops", driver="GPKG", append=True
            )

            with open(tmp_path, "rb") as f:
                buf.write(f.read())
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        buf.seek(0)
        return buf

    elif format_name.lower() == "fgb":
        # FlatGeobuf does not support multiple layers. We export stops.
        pyogrio.write_dataframe(stops_gdf, buf, driver="FlatGeobuf")
        buf.seek(0)
        return buf

    elif format_name.lower() == "parquet":
        # GeoParquet columnar export
        stops_gdf.to_parquet(buf)
        buf.seek(0)
        return buf
