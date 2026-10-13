"""Quiet, optional synthesized feedback; never changes system volume."""

import math
import struct

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio


from .feedback import FeedbackScale, beep_interval, pitch_hz


MODES = ("beeps", "pitch", "both")


class ToneSynth:
    """Phase-continuous PCM synthesis with short, click-free amplitude ramps."""

    def __init__(self, sample_rate, channels, sample_format):
        self.sample_rate = sample_rate
        self.channels = channels
        formats = {
            QAudioFormat.SampleFormat.UInt8: ("B", 127, 128),
            QAudioFormat.SampleFormat.Int16: ("h", 32767, 0),
            QAudioFormat.SampleFormat.Int32: ("i", 2147483647, 0),
            QAudioFormat.SampleFormat.Float: ("f", 1, 0),
        }
        self.code, self.scale, self.offset = formats[sample_format]
        self.phase = 0.0
        self.beat_phase = 0.0
        self.frequency = 880.0
        self.amplitude = 0.0
        self.frames = 0
        self.mode = "beeps"
        self.frequency_step = 1 - math.exp(-1 / (sample_rate * 0.035))
        self.amplitude_step = 1 - math.exp(-1 / (sample_rate * 0.003))

    def render(self, frames, strength, mode):
        if mode not in MODES:
            raise ValueError("Unknown audio mode")
        if self.mode != mode:
            self.beat_phase = 0
            self.mode = mode
        target_frequency = 880 if mode == "beeps" else pitch_hz(strength)
        interval = beep_interval(strength)
        samples = []
        for _ in range(frames):
            self.frequency += (target_frequency - self.frequency) * self.frequency_step
            self.beat_phase %= interval
            target_amplitude = 0.20 if mode == "pitch" or self.beat_phase < 0.065 else 0.0
            self.amplitude += (target_amplitude - self.amplitude) * self.amplitude_step
            value = self.amplitude * math.sin(self.phase) * self.scale + self.offset
            samples.extend([value if self.code == "f" else round(value)] * self.channels)
            self.phase = (self.phase + 2 * math.pi * self.frequency / self.sample_rate) % (2 * math.pi)
            self.beat_phase += 1 / self.sample_rate
        self.frames += frames
        return struct.pack("=" + self.code * len(samples), *samples)


class DeviceSignals(QObject):
    ready = Signal(object)
    failed = Signal(str)


class DeviceLookup(QRunnable):
    """Initial PulseAudio/PipeWire enumeration can take over a second."""

    def __init__(self, signals):
        super().__init__()
        self.signals = signals

    def run(self):
        try:
            device = QMediaDevices.defaultAudioOutput()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.ready.emit(device)


class Beeper(QObject):
    unavailable = Signal(str)

    def __init__(self, measurements, parent=None, feedback=None):
        super().__init__(parent)
        self.feedback = feedback or FeedbackScale(measurements)
        self.mode = "beeps"
        self.output = None
        self.synth = None
        self.format = None
        self.pending = b""
        self.measurements = measurements
        self.enabled = False
        self.volume = 0.15
        self.sink = None
        self.device = None
        self.lookup_pending = False
        self.pool = QThreadPool(self)
        # The runnable retains these signals even if the window closes early.
        self.device_signals = DeviceSignals()
        self.device_signals.ready.connect(self._device_ready)
        self.device_signals.failed.connect(self._device_failed)
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.tick)

    def set_enabled(self, enabled):
        self.enabled = enabled
        if enabled:
            if self.sink is None:
                if self.device is not None:
                    self._device_ready(self.device)
                elif not self.lookup_pending:
                    self.lookup_pending = True
                    self.pool.start(DeviceLookup(self.device_signals))
            else:
                self.timer.start()
        else:
            self.timer.stop()
            self._stop_stream()

    @Slot(object)
    def _device_ready(self, device):
        self.lookup_pending = False
        self.device = device
        if not self.enabled:
            return
        if device.isNull():
            self._device_failed("No audio output available")
            return
        fmt = device.preferredFormat()
        try:
            self.synth = ToneSynth(fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat())
        except (KeyError, ValueError):
            self._device_failed("Audio output format is unsupported")
            return
        self.format = fmt
        self.sink = QAudioSink(device, fmt, self)
        self.sink.setBufferSize(fmt.bytesForDuration(80000))
        self.timer.start()

    @Slot(str)
    def _device_failed(self, message):
        self.lookup_pending = False
        self.device = None
        self.enabled = False
        self.unavailable.emit(message)

    def set_volume(self, percent):
        self.volume = percent / 100
        if self.sink:
            self.sink.setVolume(self.volume)

    def _state_changed(self, state):
        # Some PySide builds expose signal arguments through the old QAudio
        # enum and getters through QtAudio. Compare their native values.
        if self.enabled and state.value == QtAudio.State.StoppedState.value and self.sink.error().value not in (QtAudio.Error.NoError.value, QtAudio.Error.UnderrunError.value):
            self.set_enabled(False)
            self.unavailable.emit("Audio output unavailable; visual readings remain active")

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("Unknown audio mode")
        self.mode = mode

    def _stop_stream(self):
        if self.sink:
            # Drop buffered tone immediately on mute, stale data or disconnect.
            self.sink.reset()
        self.output = None
        self.pending = b""

    def tick(self):
        # Use getters instead of the broken legacy enum signal converter.
        if self.sink and self.enabled:
            self._state_changed(self.sink.state())
        strength = self.feedback.strength()
        if not self.enabled or not self.sink or strength is None:
            if self.output is not None:
                self._stop_stream()
            return
        if self.output is None:
            self.synth = ToneSynth(self.format.sampleRate(), self.format.channelCount(), self.format.sampleFormat())
            self.sink.setVolume(self.volume)
            self.output = self.sink.start()
            if self.output is None:
                self.set_enabled(False)
                self.unavailable.emit("Cannot start audio output")
                return
        # Keep only ~60 ms queued, even if the backend enlarges its buffer.
        # The stream stays open between beeps and across mode changes.
        queued = self.sink.bufferSize() - self.sink.bytesFree()
        budget = min(self.sink.bytesFree(), max(0, self.format.bytesForDuration(60000) - queued))
        frame_bytes = self.format.bytesPerFrame()
        for _ in range(4):
            frames = min(budget // frame_bytes, self.format.sampleRate() // 50)
            if frames <= 0:
                break
            data = self.pending or self.synth.render(frames, strength, self.mode)
            written = self.output.write(data)
            if written < 0:
                self.set_enabled(False)
                self.unavailable.emit("Cannot write audio output")
                return
            self.pending = data[written:]
            budget -= written
            if self.pending:
                break

    def stop(self):
        self.set_enabled(False)
        # Finish device discovery before Qt destroys its multimedia backend.
        # This wait occurs only while closing, never while monitoring or muting.
        self.pool.waitForDone()
