"""Shared visual/audio sensitivity, independent of the measured dBm values."""

import statistics
import time


class FeedbackScale:
    def __init__(self, measurements, sensitivity=3):
        self.measurements = measurements
        self.sensitivity = sensitivity
        self.reference = None
        self.identity = None

    @property
    def span(self):
        return 60 / self.sensitivity

    @property
    def smoothing_seconds(self):
        return 2 / self.sensitivity

    @property
    def bounds(self):
        if self.sensitivity == 1:
            return -90.0, -30.0
        center = self.reference if self.reference is not None else -60
        center = max(-127 + self.span / 2, min(-self.span / 2, center))
        return center - self.span / 2, center + self.span / 2

    def set_sensitivity(self, value):
        self.sensitivity = max(1, min(10, int(value)))
        self.recenter()

    def recenter(self):
        sample = self.measurements.current
        if sample and sample.station.signal is not None:
            self.reference = round(self.measurements.average if self.measurements.average is not None else sample.station.signal, 1)
            self.identity = sample.identity
        else:
            self.reference = None

    def sync(self):
        if self.identity != self.measurements.identity:
            self.identity = self.measurements.identity
            self.reference = None
        if self.reference is None:
            self.recenter()

    def normalize(self, signal):
        lower, upper = self.bounds
        return max(0, min(1, (signal - lower) / (upper - lower)))

    def strength(self, now=None):
        self.sync()
        sample = self.measurements.current
        now = time.monotonic() if now is None else now
        if not sample or sample.station.signal is None or now - sample.time > 1:
            return None
        values = [value for stamp, value in self.measurements.window if stamp > sample.time - self.smoothing_seconds]
        signal = statistics.mean(values) if values else sample.station.signal
        return self.normalize(signal)


def pitch_hz(strength):
    """Two octaves, with equal perceptual steps as the signal improves."""
    return 440 * 4 ** max(0, min(1, strength))


def beep_interval(strength):
    return 1.5 - 1.38 * max(0, min(1, strength))
