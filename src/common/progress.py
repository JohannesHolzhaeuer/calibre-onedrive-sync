"""Transfer speed and remaining-time estimates for long syncs.

Uploading a library to OneDrive takes minutes to hours, so a bare
"Uploading 12/80" tells the user nothing about how long the wait will be.
:class:`TransferMeter` turns the byte counter of an on-going transfer into a
smoothed rate and an estimate, and keeps the formatting in one place so the
engine, the job log and the review window all say the same thing.

The meter is deliberately pure: the clock is injected, so tests advance time by
hand and never sleep.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import monotonic

from .i18n import _

#: (byte threshold, unit) from largest to smallest.
_UNITS = ((1024**4, "TB"), (1024**3, "GB"), (1024**2, "MB"), (1024, "KB"))

#: Weight of the newest sample in the smoothed rate. Graph throughput varies
#: per request, so a plain average over the whole transfer reacts too slowly.
_ALPHA = 0.35

#: A transfer window shorter than this says nothing about speed.
MIN_SAMPLE_INTERVAL = 0.05


def format_size(count: float) -> str:
    """Human readable byte count, e.g. ``"4.5 MB"``."""
    value = max(0.0, float(count))
    for threshold, unit in _UNITS:
        if value >= threshold:
            return f"{value / threshold:.1f} {unit}"
    return f"{value:.0f} B"


def format_rate(bytes_per_second: float) -> str:
    """Throughput as ``format_size`` per second, e.g. ``"4.5 MB/s"``."""
    value = max(0.0, float(bytes_per_second))
    return f"{format_size(value)}/s"


def format_transfer(count: float, seconds: float) -> str:
    """One file's report, e.g. ``"12.4 MB/s · 2.1 s"``.

    The per-file view of a transfer: the aggregate rate in the progress line says
    how the pass goes, this says what a single book cost.
    """
    elapsed = max(0.001, float(seconds))
    return f"{format_rate(max(0.0, float(count)) / elapsed)} · {format_duration(elapsed)}"


def format_duration(seconds: float) -> str:
    """A duration as ``"45 s"``, ``"8 min 12 s"`` or ``"2 h 4 min"``."""
    whole = int(round(max(0.0, float(seconds))))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{whole} s"


class TransferMeter:
    """Smoothed transfer rate plus the estimate that goes with it.

    ``total_bytes`` may stay ``0`` when the size is not known in advance; the
    rate is still reported, only the estimate is missing then.
    """

    def __init__(
        self,
        total_bytes: int = 0,
        clock: Callable[[], float] = monotonic,
        alpha: float = _ALPHA,
    ) -> None:
        self.total_bytes = max(0, int(total_bytes))
        self._clock = clock
        self._alpha = min(1.0, max(0.01, float(alpha)))
        self._transferred = 0
        self._sampled_bytes = 0
        #: Parallel uploads report into one meter.
        self._lock = threading.Lock()
        self._started = clock()
        self._sample_at = self._started
        self._rate = 0.0

    @property
    def transferred(self) -> int:
        """Bytes reported so far."""
        return self._transferred

    @property
    def elapsed(self) -> float:
        """Seconds since the meter was created."""
        return max(0.0, self._clock() - self._started)

    @property
    def rate(self) -> float:
        """Smoothed bytes per second, ``0.0`` before anything was transferred."""
        return self._rate

    @property
    def fraction(self) -> float:
        """Transferred share of ``total_bytes`` in ``0..1`` (0 without a total)."""
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self._transferred / self.total_bytes)

    def add(self, count: int) -> None:
        """Record ``count`` bytes transferred since the previous call.

        Bytes that arrive in the same instant as the previous sample are carried
        into the next measurable window: a window of ~0 s would read as an absurd
        speed, and the first chunk of a transfer always lands right at the start.
        Parallel uploads share one meter, so the counter is guarded.
        """
        with self._lock:
            self._transferred += max(0, int(count))
            now = self._clock()
            elapsed = now - self._sample_at
            if elapsed < MIN_SAMPLE_INTERVAL:
                return
            sample = (self._transferred - self._sampled_bytes) / elapsed
            if self._rate <= 0.0:
                self._rate = sample
            else:
                self._rate += self._alpha * (sample - self._rate)
            self._sampled_bytes = self._transferred
            self._sample_at = now

    @property
    def eta_seconds(self) -> float | None:
        """Estimated seconds left, ``None`` while the size or rate is unknown."""
        if self.total_bytes <= 0 or self._rate <= 0.0:
            return None
        remaining = self.total_bytes - self._transferred
        return remaining / self._rate if remaining > 0 else 0.0

    def label(self) -> str:
        """Short progress suffix, e.g. ``"4.5 MB/s · 8 min 12 s left"``."""
        if self._rate <= 0.0:
            return _("measuring speed…")
        text = format_rate(self._rate)
        eta = self.eta_seconds
        if eta is not None:
            text += f" · {format_duration(eta)} " + _("left")
        return text

    def line(self, message: str) -> str:
        """``message`` with the current speed and estimate appended."""
        return f"{message} · {self.label()}"
