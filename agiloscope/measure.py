"""
Waveform measurements computed locally from the sample block.

Every measurement here would otherwise cost a round trip to the instrument
(:MEASure:VPP? and friends). Computed from data already on hand they are
free, which is what makes the running mean/sigma/min/max statistics in the
design affordable at all over a 5.7 kB/s link.
"""

import math
from dataclasses import dataclass, field

import numpy as np


def _clean(volts):
    """Drop the NaN holes marking samples the scope never filled in."""
    return volts[np.isfinite(volts)]


def _top_base(volts):
    """
    Estimate the logical high and low levels of a pulse train.

    Uses the histogram-peak method rather than max/min so that overshoot and
    ringing do not distort amplitude, rise time or duty cycle.
    """
    clean = _clean(volts)
    if clean.size < 8:
        return float("nan"), float("nan")

    lo, hi = float(clean.min()), float(clean.max())
    if math.isclose(lo, hi):
        return hi, lo

    counts, edges = np.histogram(clean, bins=64, range=(lo, hi))
    midpoint = (lo + hi) / 2
    centres = (edges[:-1] + edges[1:]) / 2

    upper = counts.copy()
    upper[centres < midpoint] = 0
    lower = counts.copy()
    lower[centres >= midpoint] = 0

    if upper.sum() == 0 or lower.sum() == 0:
        return hi, lo

    return float(centres[upper.argmax()]), float(centres[lower.argmax()])


def _crossings(times, volts, level, rising=True):
    """Linearly interpolated times at which the trace crosses `level`."""
    finite = np.isfinite(volts)
    if finite.sum() < 2:
        return np.array([])

    t = times[finite]
    v = volts[finite]

    above = v >= level
    if rising:
        idx = np.flatnonzero((~above[:-1]) & above[1:])
    else:
        idx = np.flatnonzero(above[:-1] & (~above[1:]))

    if idx.size == 0:
        return np.array([])

    v0, v1 = v[idx], v[idx + 1]
    t0, t1 = t[idx], t[idx + 1]
    span = v1 - v0
    # Guard against a zero-slope segment sitting exactly on the level.
    frac = np.where(span != 0, (level - v0) / np.where(span != 0, span, 1), 0.0)
    return t0 + frac * (t1 - t0)


# -- individual measurements ------------------------------------------------


def vpp(times, volts):
    clean = _clean(volts)
    return float(clean.max() - clean.min()) if clean.size else float("nan")


def vmax(times, volts):
    clean = _clean(volts)
    return float(clean.max()) if clean.size else float("nan")


def vmin(times, volts):
    clean = _clean(volts)
    return float(clean.min()) if clean.size else float("nan")


def vavg(times, volts):
    clean = _clean(volts)
    return float(clean.mean()) if clean.size else float("nan")


def vrms(times, volts):
    clean = _clean(volts)
    return float(np.sqrt(np.mean(clean ** 2))) if clean.size else float("nan")


def vamplitude(times, volts):
    top, base = _top_base(volts)
    return top - base


def period(times, volts):
    top, base = _top_base(volts)
    if not math.isfinite(top) or not math.isfinite(base):
        return float("nan")

    edges = _crossings(times, volts, (top + base) / 2, rising=True)
    if edges.size < 2:
        return float("nan")
    return float(np.mean(np.diff(edges)))


def frequency(times, volts):
    p = period(times, volts)
    return 1.0 / p if math.isfinite(p) and p > 0 else float("nan")


def _edge_time(times, volts, low_frac, high_frac, rising):
    top, base = _top_base(volts)
    if not math.isfinite(top) or not math.isfinite(base) or top == base:
        return float("nan")

    span = top - base
    low = base + span * low_frac
    high = base + span * high_frac

    low_cross = _crossings(times, volts, low, rising=rising)
    high_cross = _crossings(times, volts, high, rising=rising)
    if low_cross.size == 0 or high_cross.size == 0:
        return float("nan")

    if rising:
        first = low_cross[0]
        after = high_cross[high_cross > first]
    else:
        first = high_cross[0]
        after = low_cross[low_cross > first]

    if after.size == 0:
        return float("nan")
    return float(abs(after[0] - first))


def rise_time(times, volts):
    return _edge_time(times, volts, 0.10, 0.90, rising=True)


def fall_time(times, volts):
    return _edge_time(times, volts, 0.10, 0.90, rising=False)


def duty_cycle(times, volts):
    top, base = _top_base(volts)
    if not math.isfinite(top) or not math.isfinite(base):
        return float("nan")

    mid = (top + base) / 2
    rising = _crossings(times, volts, mid, rising=True)
    falling = _crossings(times, volts, mid, rising=False)
    if rising.size < 2 or falling.size == 0:
        return float("nan")

    cycle = rising[1] - rising[0]
    high = falling[falling > rising[0]]
    if cycle <= 0 or high.size == 0:
        return float("nan")

    return float((high[0] - rising[0]) / cycle * 100.0)


# name -> (function, unit)
MEASUREMENTS = {
    "Vpp (Peak-Peak)": (vpp, "V"),
    "V-Amplitude": (vamplitude, "V"),
    "Vmax": (vmax, "V"),
    "Vmin": (vmin, "V"),
    "V-Average": (vavg, "V"),
    "V-RMS": (vrms, "V"),
    "Frequency": (frequency, "Hz"),
    "Period": (period, "s"),
    "Rise Time": (rise_time, "s"),
    "Fall Time": (fall_time, "s"),
    "Duty Cycle": (duty_cycle, "%"),
}


# -- running statistics -----------------------------------------------------


@dataclass
class Statistic:
    """
    Running mean/sigma/min/max over successive acquisitions.

    Welford's algorithm keeps this numerically stable over long runs without
    retaining every sample.
    """

    name: str
    source: str
    unit: str
    current: float = float("nan")
    count: int = 0
    _mean: float = 0.0
    _m2: float = 0.0
    minimum: float = field(default=float("inf"))
    maximum: float = field(default=float("-inf"))

    def add(self, value):
        self.current = value
        if not math.isfinite(value):
            return

        self.count += 1
        delta = value - self._mean
        self._mean += delta / self.count
        self._m2 += delta * (value - self._mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    @property
    def mean(self):
        return self._mean if self.count else float("nan")

    @property
    def std_dev(self):
        return math.sqrt(self._m2 / (self.count - 1)) if self.count > 1 else float("nan")

    def reset(self):
        self.count = 0
        self._mean = 0.0
        self._m2 = 0.0
        self.minimum = float("inf")
        self.maximum = float("-inf")
        self.current = float("nan")


def gate(times, volts, start=None, end=None):
    """Restrict a trace to the gating region shown in the design."""
    if start is None and end is None:
        return times, volts

    mask = np.ones(len(times), dtype=bool)
    if start is not None:
        mask &= times >= start
    if end is not None:
        mask &= times <= end

    if mask.sum() < 2:
        return times, volts
    return times[mask], volts[mask]


def spectrum(times, volts, window="hann"):
    """
    Single-sided amplitude spectrum in dBV, for the MATH: FFT panel.

    Runs locally on data already transferred, so it costs no link bandwidth.
    """
    finite = np.isfinite(volts)
    v = volts[finite]
    t = times[finite]
    if v.size < 8:
        return np.array([]), np.array([])

    dt = float(np.mean(np.diff(t)))
    if dt <= 0:
        return np.array([]), np.array([])

    if window == "hann":
        w = np.hanning(v.size)
    elif window == "hamming":
        w = np.hamming(v.size)
    elif window == "blackman":
        w = np.blackman(v.size)
    else:
        w = np.ones(v.size)

    coherent_gain = w.mean()
    spec = np.fft.rfft((v - v.mean()) * w)
    freqs = np.fft.rfftfreq(v.size, dt)

    amplitude = np.abs(spec) * 2.0 / (v.size * max(coherent_gain, 1e-12))
    with np.errstate(divide="ignore"):
        db = 20 * np.log10(np.maximum(amplitude, 1e-12))

    return freqs, db
