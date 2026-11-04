"""Cut the golden-test release fixture out of a real BBS release directory.

Keeps every record for 12 North Carolina routes (the Wood Thrush story-map routes, 4 per
BCR 27/28/29), all years and all runs, so filters, continuity, and the historical species
pool are exercised. Lines are copied byte-for-byte, so the fixture keeps the release's
padding, NULL markers, and Windows-1252 text. Usage:

    uv run python scripts/build_golden_fixture.py data/raw tests/fixtures/real/release
"""

from __future__ import annotations

import csv
import io
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROUTES: frozenset[tuple[str, str]] = frozenset(
    ("63", f"{r:03d}") for r in (101, 207, 224, 311, 31, 228, 906, 910, 12, 24, 27, 225)
)
SOURCE_DOI = "10.5066/P144YU3S"


def _key(row: dict[str, str]) -> tuple[str, str] | None:
    state, route = row.get("StateNum"), row.get("Route")
    if state is None or route is None:
        return None
    return state.strip().zfill(2), route.strip().zfill(3)


def _filter(data: bytes, keep) -> bytes:
    """Keep the header and the lines whose parsed row passes ``keep``, unchanged."""
    lines = data.splitlines(keepends=True)
    header = lines[0]
    names = next(csv.reader([header.decode("cp1252")]))
    out = [header]
    for line in lines[1:]:
        values = next(csv.reader([line.decode("cp1252")]), None)
        if values and keep(dict(zip(names, values, strict=False))):
            out.append(line)
    return b"".join(out)


def _filter_zip(src: Path, dest: Path, keep) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if not name.lower().endswith(".csv"):
                continue
            kept = _filter(zin.read(name), keep)
            if kept.count(b"\n") > 1:  # header plus at least one row
                zout.writestr(name, kept)


def build(release: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    def on_route(row: dict[str, str]) -> bool:
        return _key(row) in ROUTES and row.get("CountryNum", "").strip() == "840"

    (dest / "Routes.csv").write_bytes(_filter((release / "Routes.csv").read_bytes(), on_route))
    weather = _filter((release / "Weather.csv").read_bytes(), on_route)
    (dest / "Weather.csv").write_bytes(weather)
    run_ids = {
        r["RouteDataID"].strip()
        for r in csv.DictReader(io.StringIO(weather.decode("cp1252")))
    }

    def on_run(row: dict[str, str]) -> bool:
        return row.get("RouteDataID", "").strip() in run_ids

    (dest / "VehicleData.csv").write_bytes(
        _filter((release / "VehicleData.csv").read_bytes(), on_run)
    )
    for archive in ("50-StopData.zip", "States.zip", "MigrantNonBreeder.zip"):
        _filter_zip(release / archive, dest / archive, on_run)
    shutil.copy2(release / "SpeciesList.csv", dest / "SpeciesList.csv")
    for xml in release.glob("*.xml"):
        shutil.copy2(xml, dest / xml.name)

    # release.json for the subset (checksums of the fixture files, not the full release)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from release_fixtures import write_release_json  # noqa: PLC0415

    info = write_release_json(dest, doi=SOURCE_DOI, item_id="6a0b0b0ab66b0188da36aedd")
    info = info.model_copy(
        update={
            "title": "2026 Release (1966-2025) - golden-test subset: 12 NC routes",
            "publication_date": "2026-05-28",
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    info.save(dest)


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")
