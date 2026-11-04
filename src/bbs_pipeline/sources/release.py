"""Download a BBS release from USGS ScienceBase once, with provenance in ``release.json``."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

CATALOG_URL = "https://www.sciencebase.gov/catalog"
RELEASE_JSON = "release.json"

# Release files the pipeline reads, by lower-cased name. Reports and PDFs are skipped.
WANTED_FILES: frozenset[str] = frozenset(
    {
        "routes.csv",
        "weather.csv",
        "specieslist.csv",
        "specieslist.txt",
        "50-stopdata.zip",
        "states.zip",
        "migrantnonbreeder.zip",
        "vehicledata.csv",
    }
)

DOI_RESOLVER = "https://doi.org"
_ITEM_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_ITEM_URL_RE = re.compile(r"sciencebase\.gov/catalog/item/([0-9a-f]{24})")


class ReleaseFile(BaseModel):
    """One downloaded release file and its checksums."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    size: int
    md5: str
    sha256: str


class ReleaseInfo(BaseModel):
    """Contents of ``release.json``: which ScienceBase release a raw directory holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    doi: str | None
    title: str
    publication_date: str | None
    fetched_at: str
    files: list[ReleaseFile]

    def matches(self, release: str) -> bool:
        """True if ``release`` (a DOI or ScienceBase item id) names this release."""
        wanted = _normalize_doi(release)
        return release == self.item_id or (self.doi is not None and wanted == self.doi)

    @classmethod
    def load(cls, release_dir: Path) -> ReleaseInfo:
        return cls.model_validate_json((release_dir / RELEASE_JSON).read_text(encoding="utf-8"))

    def save(self, release_dir: Path) -> None:
        (release_dir / RELEASE_JSON).write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )


def _normalize_doi(value: str) -> str:
    doi = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
    return doi.upper()


def _get_json(client: httpx.Client, url: str, retries: int, backoff: float) -> Any:
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(backoff * (2**attempt))
    raise AssertionError("unreachable")


def _item_dois(item: dict[str, Any]) -> list[str]:
    return [
        _normalize_doi(i["key"])
        for i in item.get("identifiers", []) or []
        if i.get("type") == "DOI" and i.get("key")
    ]


def resolve_release(
    client: httpx.Client, release: str, retries: int = 3, backoff: float = 1.0
) -> dict[str, Any]:
    """Return the ScienceBase item for a DOI or item id.

    A DOI is resolved through doi.org's redirect to its ScienceBase item, and the item
    must list that DOI. ScienceBase's own search does not index DOIs.
    """
    fields = "title,identifiers,files,dates"
    if _ITEM_ID_RE.match(release.strip()):
        return _get_json(
            client,
            f"{CATALOG_URL}/item/{release.strip()}?format=json&fields={fields}",
            retries,
            backoff,
        )

    doi = _normalize_doi(release)
    response = client.get(f"{DOI_RESOLVER}/{doi}", follow_redirects=False)
    location = response.headers.get("location", "")
    match = _ITEM_URL_RE.search(location)
    if response.status_code not in (301, 302, 303, 307, 308) or match is None:
        raise LookupError(
            f"DOI {doi} does not resolve to a ScienceBase item "
            f"(doi.org returned {response.status_code} {location!r})."
        )
    item = _get_json(
        client,
        f"{CATALOG_URL}/item/{match.group(1)}?format=json&fields={fields}",
        retries,
        backoff,
    )
    if doi not in _item_dois(item):
        raise LookupError(
            f"ScienceBase item {match.group(1)} does not list DOI {doi}: {_item_dois(item)}"
        )
    return item


def _checksums(path: Path) -> tuple[str, str]:
    md5, sha256 = hashlib.md5(usedforsecurity=False), hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def sha256_file(path: Path) -> str:
    return _checksums(path)[1]


def _wanted(name: str) -> bool:
    lowered = name.lower()
    return lowered in WANTED_FILES or lowered.endswith(".xml")


def fetch_release(
    release: str,
    dest: Path,
    client: httpx.Client | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    backoff: float = 1.0,
) -> ReleaseInfo:
    """Download the pipeline's release files into ``dest`` and write ``release.json``.

    Files already in ``dest`` whose MD5 matches ScienceBase are kept, not downloaded
    again. A download whose MD5 does not match raises and is not kept.
    """
    dest.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        item = resolve_release(http, release, retries=retries, backoff=backoff)
        entries = [f for f in item.get("files", []) or [] if _wanted(str(f.get("name", "")))]
        if not entries:
            raise LookupError(f"ScienceBase item {item.get('id')} lists no BBS data files.")

        recorded: list[ReleaseFile] = []
        for entry in entries:
            name = str(entry["name"])
            expected_md5 = (entry.get("checksum") or {}).get("value")
            target = dest / name
            if target.exists() and expected_md5 and _checksums(target)[0] == expected_md5:
                md5, sha = _checksums(target)
            else:
                md5, sha = _download(http, str(entry["url"]), target, retries, backoff)
                if expected_md5 and md5 != expected_md5:
                    target.unlink()
                    raise ValueError(
                        f"MD5 mismatch for {name}: ScienceBase says {expected_md5}, got {md5}."
                    )
            recorded.append(ReleaseFile(name=name, size=target.stat().st_size, md5=md5, sha256=sha))

        dois = _item_dois(item)
        publication = next(
            (
                d.get("dateString")
                for d in item.get("dates", []) or []
                if d.get("type") == "Publication"
            ),
            None,
        )
        info = ReleaseInfo(
            item_id=str(item["id"]),
            doi=dois[0] if dois else None,
            title=str(item.get("title", "")),
            publication_date=publication,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            files=recorded,
        )
        info.save(dest)
        return info
    finally:
        if owns_client:
            http.close()


def _download(
    client: httpx.Client, url: str, target: Path, retries: int, backoff: float
) -> tuple[str, str]:
    partial = target.with_name(target.name + ".part")
    for attempt in range(retries + 1):
        try:
            md5, sha256 = hashlib.md5(usedforsecurity=False), hashlib.sha256()
            with client.stream("GET", url) as response, partial.open("wb") as fh:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    fh.write(chunk)
                    md5.update(chunk)
                    sha256.update(chunk)
            partial.replace(target)
            return md5.hexdigest(), sha256.hexdigest()
        except httpx.TransportError:
            partial.unlink(missing_ok=True)
            if attempt == retries:
                raise
            time.sleep(backoff * (2**attempt))
    raise AssertionError("unreachable")
