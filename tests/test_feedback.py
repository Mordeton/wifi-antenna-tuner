import unittest

from antenna_tuner.feedback import FeedbackScale, beep_interval, pitch_hz
from antenna_tuner.model import Measurements, Sample, Station


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.model = Measurements()
        self.scale = FeedbackScale(self.model)
        self.add(0, -60)

    def add(self, timestamp, signal, identity=("wlo1", "ap", 5220)):
        self.model.add(Sample(timestamp, identity, Station(identity[1], signal=signal)))

    def test_sensitivity_magnifies_one_db_change(self):
        self.scale.set_sensitivity(1)
        broad = self.scale.normalize(-59) - self.scale.normalize(-60)
        self.scale.set_sensitivity(10)
        fine = self.scale.normalize(-59) - self.scale.normalize(-60)
        self.assertAlmostEqual(fine / broad, 10)
        self.assertEqual(self.model.current.station.signal, -60)

    def test_reference_stays_fixed_until_recenter(self):
        self.scale.sync()
        self.assertEqual(self.scale.reference, -60)
        self.add(3, -40)
        self.scale.sync()
        self.assertEqual(self.scale.reference, -60)
        self.assertEqual(self.scale.strength(now=3), 1)
        self.scale.recenter()
        self.assertEqual(self.scale.reference, -40)
        self.assertEqual(self.scale.strength(now=3), .5)

    def test_new_connection_recenters(self):
        self.scale.sync()
        self.add(.25, -75, ("wlan1", "ap2", 2412))
        self.scale.sync()
        self.assertEqual(self.scale.reference, -75)

    def test_stale_and_disconnected_measurements_are_silent(self):
        self.assertIsNone(self.scale.strength(now=2))
        self.model.invalidate(2)
        self.assertIsNone(self.scale.strength(now=2))

    def test_fine_sensitivity_responds_to_latest_sample(self):
        for i in range(1, 9):
            self.add(i / 4, -60)
        self.scale.set_sensitivity(10)
        self.add(2.25, -58)
        self.assertAlmostEqual(self.scale.strength(now=2.25), 5 / 6)
        self.assertEqual(self.model.best, -60 + 2 / 9)
        self.scale.set_sensitivity(1)
        self.assertAlmostEqual(self.scale.strength(now=2.25), (-59.75 + 90) / 60)

    def test_audio_limits_are_monotonic(self):
        strengths = [i / 10 for i in range(11)]
        self.assertEqual([pitch_hz(s) for s in strengths], sorted(pitch_hz(s) for s in strengths))
        self.assertEqual([beep_interval(s) for s in strengths], sorted((beep_interval(s) for s in strengths), reverse=True))
        self.assertEqual(pitch_hz(0), 440)
        self.assertEqual(pitch_hz(1), 1760)


if __name__ == "__main__":
    unittest.main()
