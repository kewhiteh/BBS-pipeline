"""Time and memory sampling for builds (H13: a crash must leave a trail)."""

from __future__ import annotations

import time

from bbs_pipeline.usage import Usage, measure, rss_bytes, total_memory_bytes


def test_measure_reports_time_peak_and_samples() -> None:
    samples: list[tuple[float, int]] = []
    with measure(lambda t, rss: samples.append((t, rss)), every_s=0.1, poll_s=0.02) as usage:
        time.sleep(0.35)
    assert usage.elapsed_s >= 0.35
    assert usage.peak_rss_bytes >= usage.start_rss_bytes > 0
    assert len(samples) >= 2
    assert all(rss > 0 for _, rss in samples)


def test_a_failing_sampler_never_stops_the_build() -> None:
    def _boom(_t: float, _rss: int) -> None:
        raise OSError("disk full")

    with measure(_boom, every_s=0.05, poll_s=0.01):
        time.sleep(0.15)


def test_summary_reads_plainly() -> None:
    usage = Usage(elapsed_s=192.4, peak_rss_bytes=int(9.8 * 1024**3), total_bytes=16 * 1024**3)
    assert usage.summary() == "Took 3 min 12 s; peak memory 9.8 GB of 16 GB."
    assert usage.as_dict()["peak_rss_gb"] == 9.8
    assert rss_bytes() > 0 and total_memory_bytes() > 0
