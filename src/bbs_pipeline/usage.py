"""Time and memory use of a build, sampled while it runs.

Kyle, 2026-11-09 (H13): a full-extent Inspect ran WSL out of memory and left no record
of how far it got. The sampler reports memory every few seconds, so a crash still leaves
a trail in the log, and the summary gives elapsed time and peak memory.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

GB = 1024**3


def rss_bytes() -> int:
    """This process's resident memory now (Linux /proc; 0 where unavailable)."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def total_memory_bytes() -> int:
    """Physical memory visible to this machine (under WSL, the VM's limit)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


@dataclass
class Usage:
    elapsed_s: float = 0.0
    peak_rss_bytes: int = 0
    start_rss_bytes: int = 0
    total_bytes: int = field(default_factory=total_memory_bytes)

    def summary(self) -> str:
        mins, secs = divmod(round(self.elapsed_s), 60)
        took = f"{mins} min {secs} s" if mins else f"{self.elapsed_s:.1f} s"
        peak = f"peak memory {self.peak_rss_bytes / GB:.1f} GB"
        if self.total_bytes:
            peak += f" of {self.total_bytes / GB:.0f} GB"
        return f"Took {took}; {peak}."

    def as_dict(self) -> dict[str, float]:
        return {
            "elapsed_s": round(self.elapsed_s, 1),
            "peak_rss_gb": round(self.peak_rss_bytes / GB, 2),
            "start_rss_gb": round(self.start_rss_bytes / GB, 2),
            "total_memory_gb": round(self.total_bytes / GB, 1),
        }


@contextmanager
def measure(
    on_sample: Callable[[float, int], None] | None = None,
    every_s: float = 5.0,
    poll_s: float = 0.25,
) -> Iterator[Usage]:
    """Track elapsed time and peak memory; call on_sample(elapsed_s, rss) every every_s.

    on_sample runs on a background thread, so it must not touch Streamlit state.
    """
    usage = Usage(start_rss_bytes=rss_bytes())
    usage.peak_rss_bytes = usage.start_rss_bytes
    start = time.perf_counter()
    stop = threading.Event()

    def _sample() -> None:
        next_report = every_s
        while not stop.wait(poll_s):
            rss = rss_bytes()
            usage.peak_rss_bytes = max(usage.peak_rss_bytes, rss)
            elapsed = time.perf_counter() - start
            if on_sample is not None and elapsed >= next_report:
                next_report += every_s
                try:
                    on_sample(elapsed, rss)
                except Exception:  # a failed log line must never stop the build
                    pass

    thread = threading.Thread(target=_sample, name="bbs-usage", daemon=True)
    thread.start()
    try:
        yield usage
    finally:
        stop.set()
        thread.join(timeout=2)
        usage.peak_rss_bytes = max(usage.peak_rss_bytes, rss_bytes())
        usage.elapsed_s = time.perf_counter() - start


def available_memory_bytes() -> int:
    """Memory the OS can still give out now (Linux MemAvailable; 0 where unknown)."""
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


# Peak while building the output ~= memory in use + PEAK_TABLE_COPIES x final table.
# Calibrated 2026-11-09 on H13 (151 columns, ~1.2 KB/row): 10 states grew 1.5x the
# table, 20 states 1.76x (13.25 GB peak of 15.4). The extra copy is the final sort.
PEAK_TABLE_COPIES = 1.8
# Stop when the estimate needs more than this share of what is free plus in use.
MEMORY_HEADROOM = 0.95


class MemoryEstimateError(RuntimeError):
    """The build would need more memory than this machine has free (checked before it)."""

    def __init__(self, rows: int, columns: int, table_bytes: float, peak: float, limit: float):
        self.rows, self.columns = rows, columns
        self.table_gb, self.peak_gb, self.limit_gb = table_bytes / GB, peak / GB, limit / GB
        super().__init__(
            f"This build would make {rows:,} rows x {columns} columns (about "
            f"{self.table_gb:.1f} GB) and need about {self.peak_gb:.1f} GB of memory at its "
            f"peak; about {self.limit_gb:.1f} GB is available. Stopped before running out. "
            "Choose fewer states or years, fewer species, or fewer join columns."
        )


def check_memory(rows: int, columns: int, bytes_per_row: float, in_use: int | None = None) -> None:
    """Raise MemoryEstimateError if building the output would likely run out of memory.

    in_use: this process's memory before sizing the sample (the sample's own scratch
    memory is reused by the build, so counting it again overstates the peak).
    """
    available = available_memory_bytes()
    if not available:  # unknown platform: no basis to stop
        return
    table = rows * bytes_per_row
    now = rss_bytes()
    in_use = now if in_use is None else min(in_use, now)
    available += now - in_use  # memory the sample holds is free for the build too
    peak = in_use + PEAK_TABLE_COPIES * table
    limit = (in_use + available) * MEMORY_HEADROOM
    if peak > limit:
        raise MemoryEstimateError(rows, columns, table, peak, limit)
