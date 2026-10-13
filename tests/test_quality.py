import unittest

from antenna_tuner.quality import quality_position, signal_quality


class QualityTests(unittest.TestCase):
    def test_cisco_bands_and_exact_boundaries(self):
        cases = {
            -100: "Very poor", -80.001: "Very poor", -80: "Weak",
            -70.001: "Weak", -70: "Fair", -60.001: "Fair",
            -60: "Good", -50.001: "Good", -50: "Excellent",
            -49: "Excellent", -30: "Excellent", -20: "Excellent",
        }
        for signal, rating in cases.items():
            with self.subTest(signal=signal):
                self.assertEqual(signal_quality(signal).name, rating)

    def test_missing_and_invalid_readings_are_not_bad_signal(self):
        for signal in (None, float("nan"), float("inf"), -float("inf")):
            with self.subTest(signal=signal):
                self.assertIsNone(signal_quality(signal))
                self.assertIsNone(quality_position(signal))

    def test_marker_tracks_real_dbm_and_clamps_only_at_visual_edges(self):
        self.assertEqual(quality_position(-127), 0)
        self.assertEqual(quality_position(-100), 0)
        self.assertEqual(quality_position(-65), .5)
        self.assertAlmostEqual(quality_position(-49), 51 / 70)
        self.assertEqual(quality_position(-30), 1)
        self.assertEqual(quality_position(-20), 1)


if __name__ == "__main__":
    unittest.main()
