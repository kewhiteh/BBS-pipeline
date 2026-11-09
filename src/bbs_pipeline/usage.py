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
