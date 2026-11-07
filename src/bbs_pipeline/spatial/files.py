"""Saving GIS files the user hands the editor (an upload) where the pipeline can read them."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def save_gis_upload(name: str, data: bytes, folder: Path) -> Path:
    """Write ``data`` as ``folder/name``; a .zip is unpacked and its one shapefile returned."""
    folder.mkdir(parents=True, exist_ok=True)
    file_name = Path(name).name
    if Path(file_name).suffix.lower() != ".zip":
        target = folder / file_name
        target.write_bytes(data)
        return target
    target_dir = folder / Path(file_name).stem
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.namelist():  # refuse paths that would land outside target_dir
            if not (target_dir / member).resolve().is_relative_to(target_dir.resolve()):
                raise ValueError(f"'{name}' contains an unsafe path: '{member}'.")
        archive.extractall(target_dir)
    shapefiles = sorted(target_dir.rglob("*.shp"))
    if len(shapefiles) != 1:
        raise ValueError(f"'{name}' should hold exactly one shapefile; found {len(shapefiles)}.")
    return shapefiles[0]
