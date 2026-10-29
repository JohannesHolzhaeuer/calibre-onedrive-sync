"""Tests for the transfer meter and its formatting helpers."""

from __future__ import annotations

import threading

import pytest

from onedrive_sync.common.progress import (
    MIN_SAMPLE_INTERVAL,
    TransferMeter,
    format_duration,
    format_rate,
    format_size,
    format_transfer,
)


class Clock:
    """Manually advanced monotonic clock."""

    def __init__(self, now=0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1024, "1.0 KB"),
        (4608, "4.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (-5, "0 B"),
    ],
)
def test_format_size_scales_to_a_readable_unit(count, expected):
    assert format_size(count) == expected


def test_format_rate_appends_per_second():
    assert format_rate(10 * 1024**2) == "10.0 MB/s"
    assert format_rate(-1) == "0 B/s"


@pytest.mark.parametrize(
    ("count", "seconds", "expected"),
    [
        (5 * 1024**2, 1.0, "5.0 MB/s · 1 s"),
        (5 * 1024**2, 4.2, "1.2 MB/s · 4 s"),
        (4096, 8.0, "512 B/s · 8 s"),
        # A zero-byte file is still a file: the report stays readable.
        (0, 2.0, "0 B/s · 2 s"),
        # A response faster than the clock can measure never divides by zero.
        (1024, 0, "1000.0 KB/s · 0 s"),
        (1024, -1, "1000.0 KB/s · 0 s"),
    ],
)
def test_format_transfer_reports_one_file(count, seconds, expected):
    assert format_transfer(count, seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (44.6, "45 s"),
        (60, "1 min 0 s"),
        (492, "8 min 12 s"),
        (3600, "1 h 0 min"),
        (7320, "2 h 2 min"),
        (-10, "0 s"),
    ],
)
def test_format_duration_reads_like_a_countdown(seconds, expected):
    assert format_duration(seconds) == expected


def test_a_fresh_meter_has_no_rate_yet():
    meter = TransferMeter(total_bytes=1000, clock=Clock())

    assert meter.transferred == 0
    assert meter.rate == 0.0
    assert meter.fraction == 0.0
    assert meter.eta_seconds is None
    assert meter.label() == "measuring speed…"
    assert meter.line("Uploading 1/80") == "Uploading 1/80 · measuring speed…"


def test_a_total_size_must_be_positive_to_be_measured():
    meter = TransferMeter(total_bytes=-5)

    assert meter.total_bytes == 0
    assert meter.fraction == 0.0


def test_a_transfer_is_measured_in_bytes_per_second():
    clock = Clock()
    meter = TransferMeter(total_bytes=1000, clock=clock)

    meter.add(100)
    clock.advance(1.0)
    meter.add(100)

    assert meter.transferred == 200
    assert meter.rate == pytest.approx(200.0)
    assert meter.fraction == pytest.approx(0.2)
    assert meter.eta_seconds == pytest.approx(4.0)
    assert meter.label() == "200 B/s · 4 s left"
    assert meter.line("Uploading") == "Uploading · 200 B/s · 4 s left"


def test_the_newest_window_moves_the_rate_towards_the_real_speed():
    clock = Clock()
    meter = TransferMeter(total_bytes=10_000, clock=clock)
    meter.add(100)  # no window yet
    clock.advance(1.0)
    meter.add(100)  # first window: 200 bytes in 1 s
    clock.advance(1.0)
    meter.add(800)  # second window: 800 bytes in 1 s

    assert meter.rate == pytest.approx(200.0 + 0.35 * (800.0 - 200.0))


def test_a_spike_fades_out_as_the_transfer_continues():
    clock = Clock()
    meter = TransferMeter(total_bytes=100_000, clock=clock, alpha=0.5)
    meter.add(10)
    clock.advance(1.0)
    meter.add(10)  # first window: 20 B/s
    clock.advance(1.0)
    meter.add(980)  # 980 B/s spike -> 500
    clock.advance(1.0)
    meter.add(20)  # slow again -> 260

    assert meter.rate == pytest.approx(260.0)


def test_a_zero_byte_window_still_measures_the_bytes_it_carried():
    clock = Clock()
    meter = TransferMeter(total_bytes=100, clock=clock)
    meter.add(50)
    clock.advance(1.0)
    meter.add(0)

    assert meter.transferred == 50
    assert meter.rate == pytest.approx(50.0)


def test_a_negative_sample_never_subtracts_from_the_counter():
    meter = TransferMeter(total_bytes=100, clock=Clock())
    meter.add(30)
    meter.add(-30)

    assert meter.transferred == 30


def test_bytes_at_the_same_instant_are_carried_into_the_next_window():
    clock = Clock()
    meter = TransferMeter(total_bytes=100, clock=clock)
    meter.add(50)
    meter.add(50)  # same instant: no window, no absurd speed

    assert meter.transferred == 100
    assert meter.rate == 0.0
    assert meter.label() == "measuring speed…"

    clock.advance(MIN_SAMPLE_INTERVAL)
    meter.add(0)
    assert meter.rate == pytest.approx(100.0 / MIN_SAMPLE_INTERVAL)


def test_the_transfer_finishes_the_estimate_immediately():
    clock = Clock()
    meter = TransferMeter(total_bytes=100, clock=clock)
    meter.add(100)
    clock.advance(1.0)
    meter.add(0)

    assert meter.fraction == 1.0
    assert meter.eta_seconds == 0.0
    assert meter.label() == "100 B/s · 0 s left"


def test_a_meter_without_a_total_reports_the_speed_only():
    clock = Clock()
    meter = TransferMeter(clock=clock)
    meter.add(500)
    clock.advance(1.0)
    meter.add(500)

    assert meter.eta_seconds is None
    assert meter.fraction == 0.0
    assert meter.label() == "1000 B/s"


def test_elapsed_time_counts_from_the_meters_creation():
    clock = Clock(10.0)
    meter = TransferMeter(clock=clock)
    clock.advance(2.5)

    assert meter.elapsed == pytest.approx(2.5)


def test_time_never_runs_backwards():
    clock = Clock(5.0)
    meter = TransferMeter(clock=clock)
    clock.now = 1.0

    assert meter.elapsed == 0.0


def test_the_smoothing_weight_is_kept_in_a_useful_range():
    assert TransferMeter(alpha=99)._alpha == 1.0
    assert TransferMeter(alpha=-5)._alpha == pytest.approx(0.01)


def test_bytes_from_parallel_workers_are_all_counted():
    """Concurrent uploads share one meter; none of their bytes may get lost."""
    meter = TransferMeter(total_bytes=100 * 1000)
    workers = [
        threading.Thread(target=lambda: [meter.add(100) for _ in range(100)]) for _ in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)

    assert meter.transferred == 80_000
    assert meter.fraction == pytest.approx(0.8)
