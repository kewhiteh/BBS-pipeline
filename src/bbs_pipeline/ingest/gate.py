"""Three-Tier Ingress Gate with IntakeEngine Handshake & ScienceBase Streamer.

Implements the resilient Three-Tier Ingress pattern for BBS-Pipeline v3:
- Tier 1 (``tier1_cache``): Fast-path Polars LazyFrame scan from ``sanitized_cache_dir``
  containing ``intake_manifest.json`` and ``.parquet`` tables.
- Tier 2 (``tier2_local_raw``): Unpacks local ``.zip``/``.csv``/``.txt`` raw archives
  (including nested 50-stop state chunks and fixed-width ``SpeciesList.txt``),
  executes the ``IntakeEngine.assess()`` + ``IntakeEngine.execute_pipeline()``
  quarantine/sanitization handshake (or built-in Polars sanitization fallback),
  and populates ``sanitized_cache_dir``.
- Tier 3 (``tier3_sciencebase_stream``): Queries the USGS ScienceBase Catalog API
  by DOI with exponential backoff, streams remote archives into a staging
  directory, sanitizes into ``sanitized_cache_dir``, and gracefully falls back
  to Tier 2 or Tier 1 on network failure.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from bbs_pipeline.contracts import IngressMode, ProtocolConfigContract
from bbs_pipeline.ingest.role_resolver import (
    identify_role_from_columns,
    load_resolved_tables,
    resolve_table_roles,
)
from bbs_pipeline.ingest.species_normalizer import normalize_species_list_file

logger = logging.getLogger(__name__)


class IngressResult(BaseModel):
    """Output contract produced by ThreeTierIngressGate.execute()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tier_used: str
    cache_dir: Path
    manifest_path: Path
    manifest_data: dict[str, Any] = Field(default_factory=dict)
    resolved_paths: dict[str, Path] = Field(default_factory=dict)
    tables: dict[str, pl.LazyFrame] = Field(default_factory=dict)


class ThreeTierIngressGate:
    """Orchestrates Tier 1 (Cache), Tier 2 (Local Raw), and Tier 3 (ScienceBase)."""

    def __init__(
        self,
        protocol: ProtocolConfigContract,
        http_client: Any | None = None,
    ) -> None:
        self.protocol = protocol
        self.http_client = http_client

    @staticmethod
    def is_cache_valid(cache_dir: Path | str) -> bool:
        """Return True if cache_dir has intake_manifest.json and >=1 .parquet file."""
        target = Path(cache_dir)
        if not target.is_dir():
            return False
        manifest_path = target / "intake_manifest.json"
        if not manifest_path.is_file():
            return False
        parquet_files = list(target.glob("*.parquet"))
        return len(parquet_files) > 0

    @staticmethod
    def _has_raw_files(raw_dir: Path | str) -> bool:
        """Return True if raw_dir exists and contains any .zip, .csv, or .txt files."""
        target = Path(raw_dir)
        if not target.is_dir():
            return False
        for ext in ("*.zip", "*.csv", "*.txt"):
            if any(target.rglob(ext)):
                return True
        return False

    def execute(self) -> IngressResult:
        """Execute the Three-Tier Ingress Gate according to protocol.ingress_mode."""
        mode = self.protocol.ingress_mode
        cache_dir = self.protocol.sanitized_cache_dir
        raw_dir = self.protocol.raw_data_dir

        if mode == IngressMode.CACHE_ONLY:
            if self.protocol.force_resanitize or not self.is_cache_valid(cache_dir):
                raise FileNotFoundError(
                    f"IngressMode.CACHE_ONLY requires a valid cache in '{cache_dir}' "
                    f"(force_resanitize={self.protocol.force_resanitize})."
                )
            return self._execute_tier1_cache()

        if mode == IngressMode.LOCAL_RAW_VIA_INTAKE:
            if not self._has_raw_files(raw_dir):
                raise FileNotFoundError(
                    f"IngressMode.LOCAL_RAW_VIA_INTAKE requires raw files in '{raw_dir}'."
                )
            return self._execute_tier2_local_raw(raw_dir, tier_label="tier2_local_raw")

        if mode == IngressMode.SCIENCEBASE_STREAM:
            return self._execute_tier3_sciencebase()

        # Default: IngressMode.AUTO_THREE_TIER
        if not self.protocol.force_resanitize and self.is_cache_valid(cache_dir):
            return self._execute_tier1_cache()

        if self._has_raw_files(raw_dir):
            return self._execute_tier2_local_raw(raw_dir, tier_label="tier2_local_raw")

        return self._execute_tier3_sciencebase()

    def _execute_tier1_cache(self) -> IngressResult:
        """Tier 1: Fast-path read from sanitized Parquet cache."""
        cache_dir = self.protocol.sanitized_cache_dir
        manifest_path = cache_dir / "intake_manifest.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        resolved_paths = resolve_table_roles(cache_dir)
        tables = load_resolved_tables(cache_dir)
        return IngressResult(
            tier_used="tier1_cache",
            cache_dir=cache_dir,
            manifest_path=manifest_path,
            manifest_data=manifest_data,
            resolved_paths=resolved_paths,
            tables=tables,
        )

    def _execute_tier2_local_raw(
        self,
        source_raw_dir: Path,
        tier_label: str = "tier2_local_raw",
    ) -> IngressResult:
        """Tier 2: Unpack raw archives, normalize species/chunks, run IntakeEngine handshake."""
        cache_dir = self.protocol.sanitized_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="bbs_ingest_") as tmp_root_str:
            tmp_root = Path(tmp_root_str)
            unpacked_dir = tmp_root / "unpacked"
            staged_dir = tmp_root / "staged"
            unpacked_dir.mkdir(parents=True, exist_ok=True)
            staged_dir.mkdir(parents=True, exist_ok=True)

            self._unpack_raw_directory(source_raw_dir, unpacked_dir)
            self._stage_and_consolidate_files(unpacked_dir, staged_dir)
            self._run_intake_or_polars_sanitization(staged_dir, cache_dir)

        manifest_path = cache_dir / "intake_manifest.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        resolved_paths = resolve_table_roles(cache_dir)
        tables = load_resolved_tables(cache_dir)
        return IngressResult(
            tier_used=tier_label,
            cache_dir=cache_dir,
            manifest_path=manifest_path,
            manifest_data=manifest_data,
            resolved_paths=resolved_paths,
            tables=tables,
        )

    def _execute_tier3_sciencebase(self) -> IngressResult:
        """Tier 3: Stream raw BBS release files from USGS ScienceBase API with fallback."""
        try:
            with tempfile.TemporaryDirectory(prefix="bbs_sb_stream_") as tmp_sb_str:
                tmp_sb_dir = Path(tmp_sb_str)
                self._download_sciencebase_release(tmp_sb_dir)
                return self._execute_tier2_local_raw(
                    tmp_sb_dir,
                    tier_label="tier3_sciencebase_stream",
                )
        except Exception as exc:
            logger.warning(
                "ScienceBase Tier 3 streaming failed (%s); attempting fallback to Tier 2 / Tier 1.",
                exc,
            )
            if self._has_raw_files(self.protocol.raw_data_dir):
                return self._execute_tier2_local_raw(
                    self.protocol.raw_data_dir,
                    tier_label="tier2_local_raw",
                )
            if self.is_cache_valid(self.protocol.sanitized_cache_dir):
                return self._execute_tier1_cache()
            raise

    @classmethod
    def _unpack_raw_directory(cls, source_dir: Path, unpacked_dir: Path) -> None:
        """Recursively copy raw files and unpack all nested .zip archives."""
        counter = 0
        for item in sorted(source_dir.rglob("*")):
            if not item.is_file():
                continue
            suffix = item.suffix.lower()
            if suffix == ".zip":
                extract_subdir = unpacked_dir / f"zip_{counter}_{item.stem}"
                extract_subdir.mkdir(parents=True, exist_ok=True)
                counter += 1
                cls._extract_zip_recursively(item, extract_subdir)
            elif suffix in (".csv", ".txt"):
                dest = unpacked_dir / f"raw_{counter}_{item.name}"
                counter += 1
                shutil.copy2(item, dest)

    @classmethod
    def _extract_zip_recursively(cls, zip_path: Path, target_dir: Path) -> None:
        """Extract a zip archive and recursively unpack any nested zip archives inside it."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)

        nested_zips = sorted(target_dir.rglob("*.zip"))
        for idx, nested_zip in enumerate(nested_zips):
            nested_target = nested_zip.parent / f"nested_{idx}_{nested_zip.stem}"
            nested_target.mkdir(parents=True, exist_ok=True)
            cls._extract_zip_recursively(nested_zip, nested_target)
            nested_zip.unlink(missing_ok=True)

    @staticmethod
    def _stage_and_consolidate_files(unpacked_dir: Path, staged_dir: Path) -> None:
        """Normalize SpeciesList.txt and consolidate partitioned CSV chunks by role."""
        # 1. Process any fixed-width SpeciesList.txt files
        txt_files = sorted(unpacked_dir.rglob("*.txt"))
        for txt_file in txt_files:
            df = normalize_species_list_file(txt_file, staged_dir / "species_list.csv")
            if df.height > 0:
                break

        # 2. Group CSV files by detected logical role
        role_Buckets: dict[str, list[Path]] = {
            "fifty_stop": [],
            "ten_stop": [],
            "weather": [],
            "routes": [],
            "species": [],
        }
        unclassified_csvs: list[Path] = []

        for csv_file in sorted(unpacked_dir.rglob("*.csv")):
            try:
                cols = list(pl.scan_csv(csv_file, infer_schema_length=100).collect_schema().names())
            except Exception:
                continue
            role = identify_role_from_columns(cols)
            if role in role_Buckets:
                role_Buckets[role].append(csv_file)
            else:
                unclassified_csvs.append(csv_file)

        role_output_filenames = {
            "fifty_stop": "fifty_stop.csv",
            "ten_stop": "ten_stop.csv",
            "weather": "weather.csv",
            "routes": "routes.csv",
            "species": "species_list.csv",
        }

        for role, files in role_Buckets.items():
            if not files:
                continue
            out_path = staged_dir / role_output_filenames[role]
            if len(files) == 1 and not out_path.exists():
                shutil.copy2(files[0], out_path)
            else:
                dfs = [pl.read_csv(f, infer_schema_length=10000) for f in files]
                combined = pl.concat(dfs, how="diagonal_relaxed")
                combined.write_csv(out_path)

        for idx, extra_csv in enumerate(unclassified_csvs):
            dest = staged_dir / f"extra_{idx}_{extra_csv.name}"
            shutil.copy2(extra_csv, dest)

    def _run_intake_or_polars_sanitization(self, staged_dir: Path, cache_dir: Path) -> None:
        """Invoke IntakeEngine handshake if available, with built-in Polars sanitization."""
        engine_used = "polars_builtin_fallback"
        try:
            from intake_engine import IntakeEngine  # type: ignore[import-not-found]

            IntakeEngine.assess(staged_dir, cache_dir, target_format="parquet")
            IntakeEngine.execute_pipeline(
                staged_dir,
                cache_dir,
                target_format="parquet",
                quarantine=True,
            )
            engine_used = "intake_engine"
        except ImportError:
            logger.info("intake_engine package not installed; using built-in Polars sanitizer.")
        except Exception as exc:
            logger.warning(
                "IntakeEngine execution raised %s; falling back to Polars sanitizer.", exc
            )

        # Ensure Parquet files and intake_manifest.json exist (handles both fallback & mocked IntakeEngine)
        if not self.is_cache_valid(cache_dir):
            self._builtin_polars_sanitize(staged_dir, cache_dir, engine_name=engine_used)

    @staticmethod
    def _builtin_polars_sanitize(
        staged_dir: Path,
        cache_dir: Path,
        engine_name: str = "polars_builtin_fallback",
    ) -> None:
        """Convert staged CSVs to Parquet and write intake_manifest.json."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        tables_manifest: list[dict[str, Any]] = []

        for csv_path in sorted(staged_dir.glob("*.csv")):
            df = pl.read_csv(csv_path, infer_schema_length=10000)
            # Drop completely null rows as quarantine hygiene
            df_clean = df.filter(~pl.all_horizontal(pl.all().is_null()))
            parquet_name = f"{csv_path.stem}.parquet"
            parquet_path = cache_dir / parquet_name
            df_clean.write_parquet(parquet_path)

            role = identify_role_from_columns(df_clean.columns)
            tables_manifest.append(
                {
                    "filename": parquet_name,
                    "role": role,
                    "row_count": df_clean.height,
                    "quarantined_rows": df.height - df_clean.height,
                    "columns": list(df_clean.columns),
                }
            )

        manifest = {
            "engine": engine_name,
            "status": "healthy",
            "quarantine_enabled": True,
            "tables": tables_manifest,
        }
        manifest_path = cache_dir / "intake_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def _request_with_retries(self, client: Any, url: str) -> Any:
        """Perform an HTTP GET request with exponential backoff."""
        max_retries = self.protocol.http_max_retries
        backoff = self.protocol.http_backoff_seconds
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = client.get(url)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                return response
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries and backoff > 0:
                    time.sleep(backoff * (2**attempt))

        assert last_exc is not None
        raise last_exc

    def _download_sciencebase_release(self, target_dir: Path) -> None:
        """Query USGS ScienceBase Catalog for protocol.sciencebase_doi and stream files."""
        doi = self.protocol.sciencebase_doi
        catalog_url = f"https://www.sciencebase.gov/catalog/items?q={doi}&format=json"

        owns_client = self.http_client is None
        client = (
            httpx.Client(timeout=self.protocol.http_timeout_seconds, follow_redirects=True)
            if owns_client
            else self.http_client
        )

        try:
            resp = self._request_with_retries(client, catalog_url)
            payload = (
                resp.json() if callable(getattr(resp, "json", None)) else json.loads(resp.text)
            )
            items = payload.get("items", [])
            if not items:
                raise RuntimeError(f"No ScienceBase catalog items returned for DOI '{doi}'.")

            file_entries: list[dict[str, Any]] = []
            for item in items:
                if isinstance(item, dict):
                    file_entries.extend(item.get("files", []) or [])
                    for facet in item.get("facets", []) or []:
                        if isinstance(facet, dict):
                            file_entries.extend(facet.get("files", []) or [])

            if not file_entries:
                raise RuntimeError(
                    f"No downloadable files listed in ScienceBase item for DOI '{doi}'."
                )

            downloaded = 0
            for idx, f_meta in enumerate(file_entries):
                if not isinstance(f_meta, dict):
                    continue
                fname = f_meta.get("name") or f"sb_file_{idx}.dat"
                f_url = f_meta.get("url") or f_meta.get("downloadUri")
                if not f_url:
                    continue
                file_resp = self._request_with_retries(client, str(f_url))
                dest_path = target_dir / str(fname)
                content = getattr(file_resp, "content", None)
                if isinstance(content, bytes):
                    dest_path.write_bytes(content)
                else:
                    text_data = getattr(file_resp, "text", "")
                    dest_path.write_text(str(text_data), encoding="utf-8")
                downloaded += 1

            if downloaded == 0:
                raise RuntimeError(
                    f"Failed to download any files from ScienceBase for DOI '{doi}'."
                )
        finally:
            if owns_client and hasattr(client, "close"):
                client.close()
