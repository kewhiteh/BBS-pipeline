"""ScienceBase streaming HTTP client with in-memory byte buffering and exponential backoff.

Zero-Disk In-Memory Mandate: All network streams are consumed into io.BytesIO RAM
buffers. No temporary files, disk caches, or intermediate writes are permitted.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default ScienceBase item ID hosting the BBS release archives.
DEFAULT_ITEM_ID: str = "64ad9c3dd34e70357a292cee"

#: ScienceBase Catalog REST endpoint.
_CATALOG_BASE: str = "https://www.sciencebase.gov/catalog/item"

#: HTTP status codes that trigger a retry with exponential backoff.
_RETRY_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504)

#: Maximum number of retry attempts (after initial attempt).
_MAX_RETRIES: int = 5

#: Base back-off factor (seconds).  urllib3 uses factor * (2 ** (attempt - 1)).
_BACKOFF_FACTOR: float = 1.0

#: Download chunk size (bytes) used when streaming response bodies.
_CHUNK_SIZE: int = 1 << 17  # 128 KiB


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_session(
    max_retries: int = _MAX_RETRIES,
    backoff_factor: float = _BACKOFF_FACTOR,
    status_forcelist: tuple[int, ...] = _RETRY_STATUS_CODES,
) -> requests.Session:
    """Build a :class:`requests.Session` with a mounted :class:`~urllib3.util.retry.Retry`
    adapter that performs exponential back-off on the specified HTTP error codes.

    Parameters
    ----------
    max_retries:
        Maximum number of retry attempts (not counting the initial request).
    backoff_factor:
        Multiplier for the exponential sleep interval between retries.
    status_forcelist:
        HTTP response codes that should trigger an automatic retry.

    Returns
    -------
    requests.Session
        A session with retry semantics pre-configured on both ``http://`` and
        ``https://`` scheme prefixes.
    """
    retry_policy = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=list(status_forcelist),
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_policy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _stream_url_to_buffer(
    url: str,
    session: requests.Session,
    chunk_size: int = _CHUNK_SIZE,
    timeout: int = 60,
    max_application_retries: int = _MAX_RETRIES,
    backoff_factor: float = _BACKOFF_FACTOR,
) -> io.BytesIO:
    """Stream *url* into a fresh :class:`io.BytesIO` buffer entirely in RAM.

    Implements two complementary retry layers:

    1. **urllib3 transport-layer retry** – configured via the :class:`HTTPAdapter`
       mounted on the session (effective for real network connections).
    2. **Application-layer retry loop** – an explicit loop with exponential back-off
       that handles both transport failures and HTTP error status codes.  This
       second layer is necessary to support ``requests_mock`` in tests, which
       intercepts requests before the urllib3 adapter retry policy executes.

    Parameters
    ----------
    url:
        Fully-qualified URL to fetch.
    session:
        An active :class:`requests.Session` (may carry retry/auth configuration).
    chunk_size:
        Number of bytes per read chunk during streaming.
    timeout:
        Request connect/read timeout in seconds.
    max_application_retries:
        Maximum number of application-level retry attempts after the initial
        request.  The total number of attempts is ``max_application_retries + 1``.
    backoff_factor:
        Multiplier for the exponential sleep interval: sleep seconds equal
        ``backoff_factor * (2 ** attempt)`` where *attempt* counts from 0.

    Returns
    -------
    io.BytesIO
        A seekable in-memory buffer positioned at offset 0 containing the full
        response body.

    Raises
    ------
    requests.HTTPError
        If the server returns a non-2xx response after all retry attempts are
        exhausted.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_application_retries + 1):
        if attempt > 0:
            sleep_seconds = backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "Retry attempt %d/%d for %s — sleeping %.2fs",
                attempt,
                max_application_retries,
                url,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

        try:
            buffer = io.BytesIO()
            logger.debug("Streaming URL → RAM (attempt %d): %s", attempt + 1, url)
            with session.get(url, stream=True, timeout=timeout) as response:
                if response.status_code in _RETRY_STATUS_CODES:
                    last_exc = requests.HTTPError(
                        f"HTTP {response.status_code} on attempt {attempt + 1}",
                        response=response,
                    )
                    logger.warning(
                        "Received HTTP %d for %s; will retry if attempts remain.",
                        response.status_code,
                        url,
                    )
                    continue
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        buffer.write(chunk)
            buffer.seek(0)
            logger.debug("Buffered %d bytes from %s", buffer.getbuffer().nbytes, url)
            return buffer
        except requests.HTTPError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning("Request error on attempt %d for %s: %s", attempt + 1, url, exc)

    # All retries exhausted – re-raise the last captured exception.
    if last_exc is not None:
        raise last_exc
    raise requests.HTTPError(f"All {max_application_retries + 1} attempts failed for {url}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_item_metadata(
    item_id: str = DEFAULT_ITEM_ID,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> dict:
    """Retrieve ScienceBase item metadata JSON for *item_id*.

    Parameters
    ----------
    item_id:
        ScienceBase catalog item identifier.
    session:
        Optional pre-built session; a new one is created if *None*.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    dict
        Parsed JSON payload from the ScienceBase Catalog API.

    Raises
    ------
    requests.HTTPError
        On non-2xx response after retries.
    """
    if session is None:
        session = _build_session()

    url = f"{_CATALOG_BASE}/{item_id}?format=json"
    logger.info("Fetching ScienceBase item metadata: %s", url)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_file_by_name(
    filename: str,
    item_id: str = DEFAULT_ITEM_ID,
    session: Optional[requests.Session] = None,
    timeout: int = 120,
    chunk_size: int = _CHUNK_SIZE,
) -> io.BytesIO:
    """Download a specific file attachment from a ScienceBase item into RAM.

    The method first queries the item metadata to discover the download URL for
    *filename*, then streams the file body into an :class:`io.BytesIO` buffer.
    No bytes are written to disk at any point.

    Parameters
    ----------
    filename:
        Exact filename to locate in the item's ``files`` list
        (e.g. ``"50-StopData.zip"``).
    item_id:
        ScienceBase catalog item identifier.
    session:
        Optional pre-built session.
    timeout:
        HTTP timeout in seconds for the download request.
    chunk_size:
        Streaming chunk size in bytes.

    Returns
    -------
    io.BytesIO
        In-memory buffer positioned at offset 0 containing the file contents.

    Raises
    ------
    FileNotFoundError
        If *filename* is not listed in the item's file attachments.
    requests.HTTPError
        On non-2xx response after retries.
    """
    if session is None:
        session = _build_session()

    metadata = fetch_item_metadata(item_id=item_id, session=session)

    files: list[dict] = metadata.get("files", [])
    match = next((f for f in files if f.get("name") == filename), None)
    if match is None:
        available = [f.get("name") for f in files]
        raise FileNotFoundError(
            f"File {filename!r} not found in ScienceBase item {item_id!r}. "
            f"Available files: {available}"
        )

    download_url: str = match["url"]
    logger.info("Downloading %r (%s)", filename, download_url)
    return _stream_url_to_buffer(download_url, session, chunk_size=chunk_size, timeout=timeout)


def fetch_file_by_url(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: int = 120,
    chunk_size: int = _CHUNK_SIZE,
    max_application_retries: int = _MAX_RETRIES,
    backoff_factor: float = _BACKOFF_FACTOR,
) -> io.BytesIO:
    """Download an arbitrary URL directly into an in-memory buffer.

    This is a low-level escape hatch for callers that already have a resolved
    download URL (e.g. from a previously cached metadata lookup).

    Parameters
    ----------
    url:
        Fully-qualified download URL.
    session:
        Optional pre-built session.
    timeout:
        HTTP timeout in seconds.
    chunk_size:
        Streaming chunk size in bytes.
    max_application_retries:
        Maximum number of application-level retries on transient HTTP errors.
    backoff_factor:
        Exponential back-off multiplier for application-level retries.

    Returns
    -------
    io.BytesIO
        In-memory buffer at offset 0.
    """
    if session is None:
        session = _build_session()
    return _stream_url_to_buffer(
        url,
        session,
        chunk_size=chunk_size,
        timeout=timeout,
        max_application_retries=max_application_retries,
        backoff_factor=backoff_factor,
    )


def build_session(
    max_retries: int = _MAX_RETRIES,
    backoff_factor: float = _BACKOFF_FACTOR,
) -> requests.Session:
    """Public factory for a retry-enabled :class:`requests.Session`.

    Useful for callers that want to share a single session across multiple
    :func:`fetch_file_by_name` / :func:`fetch_file_by_url` calls.

    Parameters
    ----------
    max_retries:
        Maximum retry count.
    backoff_factor:
        Exponential back-off multiplier.

    Returns
    -------
    requests.Session
    """
    return _build_session(max_retries=max_retries, backoff_factor=backoff_factor)
