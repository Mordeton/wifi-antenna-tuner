"""Cisco IW9167E RSSI guidance, separate from adjustable feedback gain.

Cisco's displayed ranges share endpoints. At an exact boundary this app uses
the stronger band; values stronger than -30 stay in the highest category.
"""

from dataclasses import dataclass
import math


CISCO_SOURCE = (
    "https://www.cisco.com/c/en/us/td/docs/IIOT/wireless/iw9167e/software/"
    "config/262x/iw9167-scg-262x/g_manage-the-workgroup-bridges-through-web-ui_wgb/"
    "t_modify-rssi-threshold.html"
)


@dataclass(frozen=True)
class QualityBand:
    minimum: float
    name: str
    color: str
    guidance: str
    caption: str


QUALITY_BANDS = (
    QualityBand(-math.inf, "Very poor", "#cf6970", "Connection may be difficult to maintain.", "below −80 dBm"),
    QualityBand(-80, "Weak", "#d58a45", "Expect possible slowdowns or interruptions.", "from −80 dBm"),
    QualityBand(-70, "Fair", "#c3a331", "Usable signal; occasional connection issues are possible.", "from −70 dBm"),
    QualityBand(-60, "Good", "#8eb345", "Strong enough for a dependable connection in typical conditions.", "from −60 dBm"),
    QualityBand(-50, "Excellent", "#37b787", "Strong signal for most uses; check latency and loss for stability.", "from −50 dBm"),
)


def signal_quality(signal):
    if signal is None or not math.isfinite(signal):
        return None
    return next(band for band in reversed(QUALITY_BANDS) if signal >= band.minimum)


def quality_position(signal):
    """Position on the fixed -100 to -30 dBm visual scale; unknown has no marker."""
    if signal_quality(signal) is None:
        return None
    return max(0, min(1, (signal + 100) / 70))
