"""Native Qt widgets, with the detailed readings kept out of the main view."""

from pathlib import Path
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QToolButton, QVBoxLayout, QWidget,
)

from .audio import Beeper
from .backend import Monitor
from .feedback import FeedbackScale
from .model import Network
from .quality import CISCO_SOURCE, QUALITY_BANDS, quality_position, signal_quality


def number(value, unit="", precision=1):
    return "Unavailable" if value is None else f"{value:.{precision}f}{unit}"


def signal_color(signal):
    band = signal_quality(signal)
    return QColor(band.color if band else "#8794a3")


def label(text="", size=None, bold=False):
    widget = QLabel(text)
    if size:
        font = widget.font()
        font.setPointSize(size)
        font.setBold(bold)
        widget.setFont(font)
    return widget


class SignalBar(QWidget):
    def __init__(self, feedback, parent=None):
        super().__init__(parent)
        self.feedback = feedback
        self.value = None
        self.setMinimumHeight(18)
        self.setMaximumHeight(18)
        self.setAccessibleName("Signal strength bar within the selected sensitivity range")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 3, self.width(), 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.Mid))
        painter.drawRoundedRect(rect, 6, 6)
        if self.value is not None:
            rect.setWidth(self.feedback.normalize(self.value) * self.width())
            painter.setBrush(signal_color(self.value))
            painter.drawRoundedRect(rect, 6, 6)


class HistoryGraph(QWidget):
    def __init__(self, measurements, feedback, parent=None):
        super().__init__(parent)
        self.measurements = measurements
        self.feedback = feedback
        self.setMinimumHeight(155)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Signal history for the last sixty seconds")
        self.setToolTip("Thin line: current signal. Thick line: trailing two-second average. Less-negative dBm is stronger.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(43, 12, max(1, self.width() - 56), max(1, self.height() - 38))
        text_color = self.palette().color(QPalette.ColorRole.Text)
        grid_color = QColor(text_color)
        grid_color.setAlpha(30)
        font = self.font()
        font.setPointSize(9)
        painter.setFont(font)
        lower, upper = self.feedback.bounds
        for index in range(5):
            dbm = upper - index * (upper - lower) / 4
            y = rect.top() + index / 4 * rect.height()
            painter.setPen(QPen(grid_color, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(text_color)
            painter.drawText(QRectF(0, y - 9, 39, 18), Qt.AlignmentFlag.AlignRight, f"{dbm:.1f}".removesuffix(".0"))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 8, 130, 20), "60 seconds ago")
        painter.drawText(QRectF(rect.right() - 45, rect.bottom() + 8, 45, 20), Qt.AlignmentFlag.AlignRight, "Now")
        now = time.monotonic()
        history = self.measurements.history
        painter.save()
        painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
        for index, alpha, width in ((1, 100, 1.3), (2, 255, 2.4)):
            color = self.palette().color(QPalette.ColorRole.Highlight)
            color.setAlpha(alpha)
            painter.setPen(QPen(color, width))
            path = QPainterPath()
            active = False
            previous_time = None
            for point in history:
                timestamp, value = point[0], point[index]
                if timestamp < now - 60 or value is None:
                    active = False
                    continue
                if previous_time is not None and timestamp - previous_time > 0.75:
                    active = False
                x = rect.right() - (now - timestamp) / 60 * rect.width()
                y = rect.top() + (upper - max(lower, min(upper, value))) / (upper - lower) * rect.height()
                if active:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                active = True
                previous_time = timestamp
            painter.drawPath(path)
        painter.restore()
        if not history:
            painter.setPen(text_color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Signal history will appear here")


class QualityMeter(QWidget):
    """Absolute RSSI scale with five labeled bands and a live position marker."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = None
        self.setFixedHeight(83)
        self.setAccessibleName("Cisco signal quality scale")
        self.setToolTip("Cisco IW9167E RSSI guidance. Fixed scale: sensitivity and Recenter do not affect this rating. The marker uses the current live dBm reading; signal strength alone does not measure interference or guarantee speed.")

    def set_signal(self, signal):
        self.value = signal
        band = signal_quality(signal)
        self.setAccessibleDescription(f"{band.name}, {signal:g} dBm. {band.guidance}" if band else "No live reading")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().color(QPalette.ColorRole.Text)
        bounds = (-100, -80, -70, -60, -50, -30)
        left, width = 9, max(1, self.width() - 18)
        active = signal_quality(self.value)
        for index, band in enumerate(QUALITY_BANDS):
            x = left + (bounds[index] + 100) / 70 * width
            segment_width = (bounds[index + 1] - bounds[index]) / 70 * width
            color = QColor(band.color)
            color.setAlpha(255 if band == active else 115)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x + 2, 18, segment_width - 4, 15), 4, 4)
            painter.setPen(text_color)
            font = self.font()
            font.setBold(band == active)
            painter.setFont(font)
            painter.drawText(QRectF(x, 37, segment_width, 21), Qt.AlignmentFlag.AlignCenter, band.name)
            font.setBold(False)
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(QRectF(x, 58, segment_width, 19), Qt.AlignmentFlag.AlignCenter, band.caption)
        position = quality_position(self.value)
        if position is not None:
            x = left + position * width
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Base), 5))
            painter.drawLine(QPointF(x, 14), QPointF(x, 34))
            painter.setPen(QPen(text_color, 2))
            painter.drawLine(QPointF(x, 14), QPointF(x, 34))
            pointer = QPainterPath()
            pointer.moveTo(x - 5, 4)
            pointer.lineTo(x + 5, 4)
            pointer.lineTo(x, 12)
            pointer.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(text_color)
            painter.drawPath(pointer)


def card():
    frame = QFrame()
    frame.setObjectName("card")
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(self, monitor=None, start=True):
        super().__init__()
        self.monitor = monitor or Monitor(self)
        self.feedback = FeedbackScale(self.monitor.measurements)
        self.beeper = Beeper(self.monitor.measurements, self, feedback=self.feedback)
        self.setWindowTitle("Wi-Fi Antenna Tuner")
        icon = Path(__file__).resolve().parent.parent / "assets" / "antenna-tuner.svg"
        self.setWindowIcon(QIcon(str(icon)))
        self.resize(1080, 1000)
        self.setMinimumSize(780, 630)
        self.setStyleSheet("QFrame#card { border: 1px solid palette(mid); border-radius: 12px; } QToolButton { padding: 6px; } QComboBox { min-width: 90px; }")
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(16)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Wi-Fi Antenna Tuner", 23, True))
        titles.addWidget(label("Move the antenna. Watch the signal. Let it settle for two seconds."))
        header.addLayout(titles, 1)
        self.adapter = QComboBox()
        self.adapter.setAccessibleName("Wi-Fi adapter")
        self.adapter.setToolTip("Wi-Fi adapter being measured")
        self.adapter.currentIndexChanged.connect(self._select_adapter)
        header.addWidget(self.adapter)
        self.fullscreen = QPushButton("Fullscreen")
        self.fullscreen.setToolTip("Toggle fullscreen (F11); Escape exits fullscreen")
        self.fullscreen.clicked.connect(self.toggle_fullscreen)
        header.addWidget(self.fullscreen)
        root.addLayout(header)
        self.connection = label("Finding your Wi-Fi connection…")
        self.connection.setTextFormat(Qt.TextFormat.PlainText)
        self.connection.setWordWrap(True)
        root.addWidget(self.connection)

        meters = QHBoxLayout()
        signal_card, signal_layout = card()
        signal_layout.addWidget(label("SIGNAL STRENGTH", 10, True))
        value_row = QHBoxLayout()
        self.signal_value = label("—", 52, True)
        self.signal_value.setAccessibleName("Current Wi-Fi signal strength")
        value_row.addWidget(self.signal_value)
        value_row.addWidget(label("dBm", 16))
        value_row.addStretch()
        signal_layout.addLayout(value_row)
        self.signal_bar = SignalBar(self.feedback)
        signal_layout.addWidget(self.signal_bar)
        self.range_label = label("Less negative = stronger  ·  −50 is stronger than −70")
        self.range_label.setWordWrap(True)
        self.range_label.setToolTip("The sensitivity slider zooms this range. The large dBm reading is always the original measurement.")
        signal_layout.addWidget(self.range_label)
        summary = QHBoxLayout()
        self.average_value = label("2-second average: —")
        self.best_value = label("Best average: —")
        summary.addWidget(self.average_value)
        summary.addStretch()
        summary.addWidget(self.best_value)
        self.reset = QPushButton("Reset best")
        self.reset.clicked.connect(self._reset_best)
        summary.addWidget(self.reset)
        signal_layout.addLayout(summary)
        meters.addWidget(signal_card, 2)

        latency_card, latency_layout = card()
        latency_layout.addWidget(label("ROUTER LATENCY", 10, True))
        self.latency_value = label("—", 38, True)
        self.latency_value.setAccessibleName("Latest router response time")
        latency_layout.addWidget(self.latency_value)
        latency_layout.addWidget(label("milliseconds  ·  lower is better"))
        self.latency_note = label("Waiting for the router…")
        self.latency_note.setWordWrap(True)
        latency_layout.addWidget(self.latency_note)
        latency_layout.addStretch()
        self.probe_toggle = QCheckBox("Measure router latency")
        self.probe_toggle.setChecked(True)
        self.probe_toggle.setToolTip("Four small local pings per second through the selected Wi-Fi adapter. No internet test.")
        self.probe_toggle.toggled.connect(self.monitor.set_probing)
        latency_layout.addWidget(self.probe_toggle)
        meters.addWidget(latency_card, 1)
        root.addLayout(meters)

        quality_card, quality_layout = card()
        quality_header = QHBoxLayout()
        quality_header.addWidget(label("CISCO SIGNAL QUALITY", 10, True))
        self.quality_rating = label("No live reading", 14, True)
        quality_header.addWidget(self.quality_rating)
        quality_header.addStretch()
        source = QLabel(f'<a href="{CISCO_SOURCE}">Cisco guide ↗</a>')
        source.setOpenExternalLinks(True)
        source.setToolTip("Open the Cisco IW9167E RSSI guidance used for these bands")
        quality_header.addWidget(source)
        quality_layout.addLayout(quality_header)
        self.quality_meter = QualityMeter()
        quality_layout.addWidget(self.quality_meter)
        self.quality_guidance = label("Waiting for a fresh signal reading.")
        self.quality_guidance.setWordWrap(True)
        quality_layout.addWidget(self.quality_guidance)
        root.addWidget(quality_card)

        history_card, history_layout = card()
        history_title = QHBoxLayout()
        history_title.addWidget(label("SIGNAL HISTORY", 10, True))
        history_title.addStretch()
        history_title.addWidget(label("Thin: live   ·   Bold: 2-second average"))
        history_layout.addLayout(history_title)
        self.graph = HistoryGraph(self.monitor.measurements, self.feedback)
        history_layout.addWidget(self.graph)
        root.addWidget(history_card)

        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(label("Sensitivity", bold=True))
        sensitivity_row.addWidget(label("Broad"))
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(1, 10)
        self.sensitivity.setValue(self.feedback.sensitivity)
        self.sensitivity.setMinimumWidth(150)
        self.sensitivity.setAccessibleName("Feedback sensitivity, 1 for broad changes to 10 for fine adjustments")
        self.sensitivity.setToolTip("Higher sensitivity zooms the meter and graph and makes audio respond to smaller, faster changes. It does not change the raw dBm or the two-second best measurement.")
        self.sensitivity.valueChanged.connect(self._sensitivity_changed)
        sensitivity_row.addWidget(self.sensitivity, 1)
        sensitivity_row.addWidget(label("Fine"))
        self.sensitivity_value = label("3× · 20 dB view")
        self.sensitivity_value.setMinimumWidth(130)
        sensitivity_row.addWidget(self.sensitivity_value)
        self.recenter = QPushButton("Recenter")
        self.recenter.setToolTip("Center the visual and audio range on your current two-second average. The range stays fixed until you change sensitivity, recenter, or change connection.")
        self.recenter.clicked.connect(self._recenter)
        sensitivity_row.addWidget(self.recenter)
        root.addLayout(sensitivity_row)

        controls = QHBoxLayout()
        self.sound = QCheckBox("Sound")
        self.sound.setToolTip("Enable audio feedback. Always muted at startup.")
        self.sound.toggled.connect(self._sound_changed)
        controls.addWidget(self.sound)
        self.audio_mode = QComboBox()
        self.audio_mode.setAccessibleName("Audio feedback mode")
        self.audio_mode.addItem("Faster beeps", "beeps")
        self.audio_mode.addItem("Continuous pitch", "pitch")
        self.audio_mode.addItem("Pitch + beeps", "both")
        self.audio_mode.setToolTip("Stronger signal makes the pitch higher, the beeps faster, or both. Modes follow the sensitivity range shown above.")
        self.audio_mode.currentIndexChanged.connect(lambda: self.beeper.set_mode(self.audio_mode.currentData()))
        controls.addWidget(self.audio_mode)
        controls.addWidget(label("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(15)
        self.volume.setFixedWidth(105)
        self.volume.setEnabled(False)
        self.volume.setAccessibleName("Feedback volume, without changing system volume")
        self.volume.valueChanged.connect(self.beeper.set_volume)
        controls.addWidget(self.volume)
        controls.addStretch()
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Stats for nerds")
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self._details_changed)
        controls.addWidget(self.details_toggle)
        root.addLayout(controls)

        self.details, details_layout = card()
        note = label("Link rates are negotiated radio rates, not internet speed. Chain numbers are driver identifiers, not physical antenna labels.")
        note.setWordWrap(True)
        details_layout.addWidget(note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(9)
        self.detail_values = {}
        fields = [
            ("interface", "Interface / driver"), ("ap", "Access point"),
            ("radio", "Frequency / channel / width"), ("age", "Sample age / RX inactivity"),
            ("chains", "Signal by chain"), ("driver_avg", "Driver signal average"),
            ("chain_avg", "Driver average by chain"), ("beacon", "Beacon signal average"),
            ("rx_link", "Receive link rate"), ("tx_link", "Transmit link rate"),
            ("rx_rate", "Received traffic"), ("tx_rate", "Sent traffic"),
            ("retries", "TX retries · since last sample"), ("failures", "TX failures · since last sample"),
            ("drops", "RX drops / beacon losses · Δ"), ("packets", "RX / TX packets · Δ"),
            ("gateway", "Router probe target"), ("loss", "Packet loss · last 30 s"),
            ("rtt", "RTT min / mean / max · 30 s"), ("variation", "RTT variation · std dev · 30 s"),
            ("probes", "Probe replies / complete / pending"), ("totals", "Adapter RX / TX byte counters"),
        ]
        for index, (key, title) in enumerate(fields):
            row, column = index // 2, (index % 2) * 2
            title_label = label(title)
            title_label.setWordWrap(True)
            grid.addWidget(title_label, row, column)
            value = label("Unavailable")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            grid.addWidget(value, row, column + 1)
            self.detail_values[key] = value
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        details_layout.addLayout(grid)
        self.details.setVisible(False)
        root.addWidget(self.details)

        self.status = label()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch()
        self.beeper.unavailable.connect(self._audio_unavailable)
        self.monitor.interfaces_changed.connect(self._interfaces_changed)
        self.monitor.changed.connect(self.render)
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(100)
        self.render_timer.timeout.connect(self.render)
        self.render_timer.start()
        self.shortcuts = [QShortcut(QKeySequence("F11"), self), QShortcut(QKeySequence("Escape"), self)]
        self.shortcuts[0].activated.connect(self.toggle_fullscreen)
        self.shortcuts[1].activated.connect(self.exit_fullscreen)
        self._interfaces_changed()
        self.render()
        if start:
            self.monitor.start()

    def _interfaces_changed(self):
        self.adapter.blockSignals(True)
        self.adapter.clear()
        for name in self.monitor.interfaces:
            self.adapter.addItem(name, name)
        index = self.adapter.findData(self.monitor.selected)
        self.adapter.setCurrentIndex(index)
        self.adapter.setEnabled(self.adapter.count() > 1)
        self.adapter.blockSignals(False)

    def _select_adapter(self, index):
        name = self.adapter.itemData(index)
        if name:
            self.monitor.select(name)

    def _reset_best(self):
        self.monitor.measurements.reset_best()
        self.render()

    def _sensitivity_changed(self, value):
        self.feedback.set_sensitivity(value)
        self.render()

    def _recenter(self):
        self.feedback.recenter()
        self.render()

    def _details_changed(self, expanded):
        self.details.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        if expanded:
            # Reveal the expanded readings after Qt has updated the layout.
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.details))

    def _sound_changed(self, enabled):
        self.volume.setEnabled(enabled)
        self.beeper.set_enabled(enabled)

    def _audio_unavailable(self, message):
        self.sound.setChecked(False)
        self.statusBar().showMessage(message, 10000)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.showFullScreen()
            self.fullscreen.setText("Exit fullscreen")

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen.setText("Fullscreen")

    def render(self):
        now = time.monotonic()
        monitor = self.monitor
        model = monitor.measurements
        sample = model.current
        fresh = sample is not None and now - sample.time <= 1
        station = sample.station if fresh else None
        signal = station.signal if station else None
        band = signal_quality(signal)
        self.quality_meter.set_signal(signal)
        self.quality_rating.setText(f"{band.name} · {signal:g} dBm" if band else "No live reading")
        self.quality_guidance.setText(band.guidance if band else "Waiting for a fresh signal reading.")
        self.feedback.sync()
        lower, upper = self.feedback.bounds
        self.sensitivity_value.setText(f"{self.feedback.sensitivity}× · {round(self.feedback.span, 1):g} dB view")
        self.sensitivity_value.setToolTip(f"Audio smoothing: {self.feedback.smoothing_seconds:.2f} seconds. Raw numbers and the best two-second average are unchanged.")
        self.recenter.setEnabled(signal is not None and self.feedback.sensitivity > 1)
        suffix = "outside view — use Recenter" if signal is not None and not lower <= signal <= upper else "less negative = stronger"
        self.range_label.setText(f"View: {round(lower, 1):g} to {round(upper, 1):g} dBm · {suffix}")
        self.signal_value.setText(f"{signal:.0f}" if signal is not None else "—")
        self.signal_bar.value = signal
        self.signal_bar.update()
        self.average_value.setText("2-second average: " + (number(model.average, " dBm") if fresh else "—"))
        self.best_value.setText("Best average: " + (number(model.best, " dBm") if model.best is not None else "collecting…"))
        interface = monitor.interfaces.get(monitor.selected)
        network = monitor.networks.get(monitor.selected, Network())
        if interface:
            frequency = f"{interface.frequency / 1000:g} GHz" if interface.frequency else ""
            self.connection.setText("  ·  ".join(part for part in (interface.ssid or "No network name available", interface.name, frequency) if part))
        else:
            self.connection.setText("No Wi-Fi connection")
        stats = monitor.pings.stats(now)
        self.latency_value.setText("—")
        if not monitor.probing:
            self.latency_note.setText("Router probes paused")
        elif not fresh:
            self.latency_note.setText("Waiting for Wi-Fi measurements…")
        elif not monitor.gateway:
            self.latency_note.setText("No router gateway available")
        elif monitor.ping_error:
            self.latency_note.setText(monitor.ping_error)
        elif stats["latest_time"] is not None and now - stats["latest_time"] <= 2:
            if stats["latest"] is not None:
                self.latency_value.setText(f"{stats['latest']:.1f}")
                self.latency_note.setText(f"{stats['loss']:.0f}% packet loss · last 30 s")
            else:
                self.latency_note.setText("No reply — the router may block or delay pings")
        else:
            self.latency_note.setText("Waiting for a router reply…")

        def chains(values):
            return " · ".join(f"{i}: {value:g} dBm" for i, value in enumerate(values)) or "Unavailable"

        def delta(key):
            return str(model.deltas[key]) if fresh and key in model.deltas else "—"

        values = {
            "interface": f"{monitor.selected or '—'} / {network.driver or '—'}",
            "ap": station.bssid if station else "Unavailable",
            "radio": f"{interface.frequency:g} MHz / {interface.channel} / {interface.width}" if interface and interface.frequency else "Unavailable",
            "age": f"{now - sample.time:.2f} s / {number(station.inactive_ms, ' ms', 0)}" if station else "Unavailable",
            "chains": chains(station.chains) if station else "Unavailable",
            "driver_avg": number(station.average, " dBm") if station else "Unavailable",
            "chain_avg": chains(station.average_chains) if station else "Unavailable",
            "beacon": number(station.beacon_average, " dBm") if station else "Unavailable",
            "rx_link": station.rx_link or "Unavailable" if station else "Unavailable",
            "tx_link": station.tx_link or "Unavailable" if station else "Unavailable",
            "rx_rate": number(model.rates.get("rx bytes", 0) * 8 / 1e6, " Mbit/s", 3) if fresh and "rx bytes" in model.rates else "Unavailable",
            "tx_rate": number(model.rates.get("tx bytes", 0) * 8 / 1e6, " Mbit/s", 3) if fresh and "tx bytes" in model.rates else "Unavailable",
            "retries": delta("tx retries"), "failures": delta("tx failed"),
            "drops": f"{delta('rx drop misc')} / {delta('beacon loss')}",
            "packets": f"{delta('rx packets')} / {delta('tx packets')}",
            "gateway": f"{monitor.gateway or 'Unavailable'} via {monitor.selected or '—'}",
            "loss": number(stats["loss"], "%"),
            "rtt": " / ".join(number(stats[key], " ms") for key in ("minimum", "mean", "maximum")),
            "variation": number(stats["variation"], " ms", 2),
            "probes": f"{stats['received']} / {stats['completed']} / {stats['pending']}",
            "totals": f"{station.counters.get('rx bytes', '—')} / {station.counters.get('tx bytes', '—')}" if station else "Unavailable",
        }
        for key, value in values.items():
            self.detail_values[key].setText(value)
        self.status.setText(monitor.status + "   ·   Driver-reported readings; freshness depends on the adapter.")
        self.graph.update()

    def closeEvent(self, event):
        self.render_timer.stop()
        self.beeper.stop()
        self.monitor.stop()
        event.accept()
