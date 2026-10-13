import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from antenna_tuner.audio import Beeper, ToneSynth
from antenna_tuner.backend import Command, Monitor
from antenna_tuner.model import Sample, parse_interfaces, parse_station
from antenna_tuner.ui import MainWindow
from test_model import INTERFACES, STATION


APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=2000):
    started = time.monotonic()
    while not predicate() and time.monotonic() - started < timeout / 1000:
        QTest.qWait(10)
    return predicate()


class CommandTests(unittest.TestCase):
    def test_async_command_and_no_overlap(self):
        command = Command()
        results = []
        command.done.connect(lambda *args: results.append(args))
        self.assertTrue(command.start(sys.executable, ["-c", "import time; time.sleep(.05); print('ok')"]))
        self.assertFalse(command.start(sys.executable, ["-c", "print('wrong')"]))
        self.assertTrue(wait_until(lambda: bool(results)))
        self.assertEqual(results, [(True, "ok\n", "")])

    def test_timeout_and_failed_start(self):
        command = Command()
        results = []
        command.done.connect(lambda *args: results.append(args))
        command.start(sys.executable, ["-c", "import time; time.sleep(5)"], 50)
        self.assertTrue(wait_until(lambda: bool(results)))
        self.assertFalse(results[0][0])
        self.assertEqual(results[0][2], "Measurement timed out")
        self.assertEqual(command.process.state(), QProcess.ProcessState.NotRunning)
        command.start("/nonexistent/antenna-tuner-command", [])
        self.assertTrue(wait_until(lambda: len(results) == 2))
        self.assertFalse(results[1][0])

    def test_cancel_emits_no_result(self):
        command = Command()
        results = []
        command.done.connect(lambda *args: results.append(args))
        command.start(sys.executable, ["-c", "import time; time.sleep(5)"])
        QTest.qWait(30)
        command.cancel()
        self.assertTrue(wait_until(lambda: command.process.state() == QProcess.ProcessState.NotRunning))
        self.assertEqual(results, [])


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor()
        self.monitor.interfaces = parse_interfaces(INTERFACES)
        self.monitor.select("wlo1")
        self.window = MainWindow(self.monitor, start=False)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.monitor.deleteLater()
        APP.processEvents()

    def add_reading(self):
        self.monitor.measurements.add(Sample(time.monotonic(), ("wlo1", "02:00:00:00:00:01", 5220), parse_station(STATION, "wlo1")))
        self.window.render()

    def test_live_readings_and_persistent_expansion(self):
        self.assertFalse(self.window.sound.isChecked())
        self.assertFalse(self.window.beeper.enabled)
        self.assertTrue(self.window.probe_toggle.isChecked())
        self.assertFalse(self.window.details.isVisible())
        self.add_reading()
        self.assertEqual(self.window.signal_value.text(), "-60")
        self.window.details_toggle.click()
        self.assertTrue(self.window.details.isVisible())
        self.add_reading()
        self.assertTrue(self.window.details.isVisible())
        self.assertEqual(self.window.detail_values["chains"].text(), "0: -60 dBm · 1: -61 dBm")
        self.assertIn("VHT", self.window.detail_values["tx_link"].text())

    def test_sensitivity_and_audio_modes_leave_measurements_unchanged(self):
        self.add_reading()
        original = self.monitor.measurements.current.station.signal
        self.window.sensitivity.setValue(10)
        self.assertEqual(self.window.feedback.bounds, (-63, -57))
        self.assertIs(self.window.feedback, self.window.beeper.feedback)
        self.assertEqual(self.window.signal_value.text(), "-60")
        self.assertEqual(self.monitor.measurements.current.station.signal, original)
        self.window.audio_mode.setCurrentIndex(1)
        self.assertEqual(self.window.beeper.mode, "pitch")
        self.window.audio_mode.setCurrentIndex(2)
        self.assertEqual(self.window.beeper.mode, "both")
        self.assertFalse(self.window.sound.isChecked())
        self.window.sensitivity.setValue(1)
        self.assertEqual(self.window.feedback.bounds, (-90, -30))
        self.assertFalse(self.window.recenter.isEnabled())

    def test_stale_reading_is_hidden(self):
        self.add_reading()
        self.monitor.measurements.current.time -= 2
        self.window.render()
        self.assertEqual(self.window.signal_value.text(), "—")
        self.assertEqual(self.window.detail_values["chains"].text(), "Unavailable")
        self.assertEqual(self.window.quality_rating.text(), "No live reading")
        self.assertIsNone(self.window.quality_meter.value)

    def test_quality_meter_is_live_and_independent_of_sensitivity(self):
        self.add_reading()
        self.assertEqual(self.window.quality_rating.text(), "Good · -60 dBm")
        self.window.sensitivity.setValue(10)
        self.window.recenter.click()
        self.assertEqual(self.window.quality_rating.text(), "Good · -60 dBm")
        self.assertEqual(self.window.quality_meter.value, -60)
        self.monitor.measurements.current.station.signal = -49
        self.window.render()
        self.assertEqual(self.window.quality_rating.text(), "Excellent · -49 dBm")
        self.assertEqual(self.window.quality_meter.value, -49)
        self.monitor.measurements.invalidate(time.monotonic())
        self.window.render()
        self.assertEqual(self.window.quality_rating.text(), "No live reading")
        self.assertIsNone(self.window.quality_meter.value)

    def test_fullscreen_and_probe_toggle(self):
        self.window.toggle_fullscreen()
        self.assertTrue(self.window.isFullScreen())
        self.window.exit_fullscreen()
        self.assertFalse(self.window.isFullScreen())
        self.window.probe_toggle.click()
        self.assertFalse(self.monitor.probing)
        self.assertEqual(self.window.latency_note.text(), "Router probes paused")

    def test_device_change_resets_and_disconnect_recovers(self):
        self.add_reading()
        self.monitor.select("wlan1")
        self.assertIsNone(self.monitor.measurements.current)
        self.monitor.select("wlo1")
        self.monitor.sample_context = (self.monitor.generation, "wlo1", 5220)
        self.monitor._station_done(True, "", "")
        self.assertIn("disconnected", self.monitor.status)
        self.monitor.sample_context = (self.monitor.generation, "wlo1", 5220)
        self.monitor._station_done(True, STATION, "")
        self.assertEqual(self.window.signal_value.text(), "-60")

    def test_old_request_does_not_cross_connection_change(self):
        old_generation = self.monitor.generation
        self.monitor.select("wlan1")
        self.monitor.sample_context = (old_generation, "wlo1", 5220)
        self.monitor._station_done(True, STATION, "")
        self.assertIsNone(self.monitor.measurements.current)

    def test_audio_toggle_controls_without_playing(self):
        self.window.sound.click()
        self.assertTrue(self.window.volume.isEnabled())
        # No measurement is present, so enabling sound cannot play a tone.
        self.window.beeper.tick()
        self.window.volume.setValue(9)
        self.assertEqual(self.window.beeper.volume, 0.09)
        self.window.sound.click()
        self.assertFalse(self.window.beeper.enabled)
        self.assertFalse(self.window.volume.isEnabled())

    def test_audio_discovery_does_not_block_ui_or_play_after_mute(self):
        from unittest.mock import patch
        from PySide6.QtMultimedia import QAudioDevice

        def slow_lookup():
            time.sleep(0.15)
            return QAudioDevice()

        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        with patch("antenna_tuner.audio.QMediaDevices.defaultAudioOutput", slow_lookup):
            started = time.monotonic()
            self.window.sound.click()
            self.assertLess(time.monotonic() - started, 0.1)
            self.window.sound.click()
            self.assertTrue(wait_until(lambda: not self.window.beeper.lookup_pending))
        timer.stop()
        self.assertGreaterEqual(len(ticks), 5)
        self.assertFalse(self.window.beeper.enabled)
        self.assertIsNone(self.window.beeper.sink)


class AudioTests(unittest.TestCase):
    def test_successful_stop_is_not_an_error_across_qt_enum_aliases(self):
        from types import SimpleNamespace
        from PySide6.QtMultimedia import QAudio, QtAudio
        from antenna_tuner.model import Measurements
        beeper = Beeper(Measurements())
        beeper.enabled = True
        errors = []
        beeper.unavailable.connect(errors.append)
        beeper.sink = SimpleNamespace(error=lambda: QtAudio.Error.NoError, reset=lambda: None)
        beeper._state_changed(QAudio.State.StoppedState)
        self.assertTrue(beeper.enabled)
        self.assertEqual(errors, [])
        beeper.sink.error = lambda: QtAudio.Error.IOError
        beeper._state_changed(QAudio.State.StoppedState)
        self.assertFalse(beeper.enabled)
        self.assertEqual(len(errors), 1)
        beeper.stop()

    def test_audio_chunks_preserve_phase_without_clicks(self):
        first = ToneSynth(48000, 2, QAudioFormat.SampleFormat.Int16)
        second = ToneSynth(48000, 2, QAudioFormat.SampleFormat.Int16)
        whole = first.render(4800, .7, "pitch")
        chunks = b"".join(second.render(960, .7, "pitch") for _ in range(5))
        self.assertEqual(whole, chunks)
        import struct
        values = struct.unpack("=" + "h" * (len(whole) // 2), whole)
        self.assertEqual(values[:2], (0, 0))
        self.assertLessEqual(max(abs(value) for value in values), 6554)

    def test_continuous_pitch_tracks_strength(self):
        synth = ToneSynth(16000, 1, QAudioFormat.SampleFormat.Float)
        synth.render(8000, 0, "pitch")
        self.assertAlmostEqual(synth.frequency, 440, delta=.1)
        synth.render(8000, 1, "pitch")
        self.assertAlmostEqual(synth.frequency, 1760, delta=.1)
        synth.render(8000, 1, "beeps")
        self.assertAlmostEqual(synth.frequency, 880, delta=.1)

    def test_faster_beep_mode_produces_more_pulses(self):
        import struct
        def pulses(strength):
            synth = ToneSynth(16000, 1, QAudioFormat.SampleFormat.Float)
            data = synth.render(32000, strength, "beeps")
            values = struct.unpack("=" + "f" * 32000, data)
            loud = [max(abs(v) for v in values[i:i + 160]) > .1 for i in range(0, len(values), 160)]
            return sum(on and (i == 0 or not loud[i - 1]) for i, on in enumerate(loud))
        self.assertEqual(pulses(0), 2)
        self.assertGreaterEqual(pulses(1), 15)

    def test_stale_signal_flushes_continuous_audio(self):
        from types import SimpleNamespace
        from PySide6.QtMultimedia import QtAudio
        from antenna_tuner.model import Measurements, Station
        model = Measurements()
        model.add(Sample(time.monotonic() - 2, ("wlo1", "ap", 5220), Station("ap", signal=-60)))
        beeper = Beeper(model)
        resets = []
        beeper.sink = SimpleNamespace(state=lambda: QtAudio.State.ActiveState, error=lambda: QtAudio.Error.NoError, reset=lambda: resets.append(True))
        beeper.enabled = True
        beeper.output = object()
        beeper.set_mode("pitch")
        beeper.tick()
        self.assertEqual(resets, [True])
        self.assertIsNone(beeper.output)
        beeper.stop()


if __name__ == "__main__":
    unittest.main()
