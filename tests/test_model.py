import unittest

from antenna_tuner.model import (
    Measurements, PingWindow, Sample, Station,
    parse_interfaces, parse_networks, parse_ping, parse_station,
)


STATION = """Station 02:00:00:00:00:01 (on wlo1)
    inactive time: 11 ms
    rx bytes: 12773063082
    rx packets: 9117221
    tx bytes: 921618559
    tx packets: 2743523
    tx retries: 69938
    tx failed: 0
    beacon loss: 0
    rx drop misc: 200
    signal: -60 [-60, -61] dBm
    signal avg: -60 [-59, -61] dBm
    beacon signal avg: -58 dBm
    tx bitrate: 650.0 MBit/s VHT-MCS 7 80MHz short GI VHT-NSS 2
    rx bitrate: 650.0 MBit/s VHT-MCS 7 80MHz short GI VHT-NSS 2
"""

INTERFACES = """phy#0
    Unnamed/non-netdev interface
        type P2P-device
    Interface wlo1
        ssid A network: with spaces
        type managed
        channel 44 (5220 MHz), width: 80 MHz, center1: 5210 MHz
phy#1
    Interface wlan1
        type managed
"""


class ParserTests(unittest.TestCase):
    def test_current_adapter_output(self):
        value = parse_station(STATION, "wlo1")
        self.assertEqual(value.signal, -60)
        self.assertEqual(value.chains, (-60, -61))
        self.assertEqual(value.average_chains, (-59, -61))
        self.assertEqual(value.beacon_average, -58)
        self.assertEqual(value.counters["rx bytes"], 12773063082)
        self.assertEqual(value.inactive_ms, 11)
        self.assertIn("650.0 MBit/s", value.tx_link)

    def test_chain_count_changes_and_optional_fields(self):
        one = parse_station("Station 02:00:00:00:00:01 (on wlo1)\n signal: -63 [-63] dBm", "wlo1")
        self.assertEqual(one.chains, (-63,))
        none = parse_station("Station 02:00:00:00:00:01 (on wlo1)\n signal: -63 dBm", "wlo1")
        self.assertEqual(none.chains, ())
        self.assertIsNone(none.average)
        self.assertEqual(none.counters, {})

    def test_missing_signal_is_not_zero(self):
        value = parse_station("Station 02:00:00:00:00:01 (on wlo1)\n tx bytes: 100", "wlo1")
        self.assertIsNone(value.signal)

    def test_disconnected(self):
        self.assertIsNone(parse_station("", "wlo1"))
        self.assertIsNone(parse_station("Not connected.\n", "wlo1"))

    def test_malformed_and_mismatched_evidence(self):
        for output in ("garbage", STATION.replace("-60 [-60, -61] dBm", "NaN dBm"), STATION.replace("tx retries: 69938", "tx retries: -1"), STATION + STATION):
            with self.subTest(output=output[:70]):
                with self.assertRaises(ValueError):
                    parse_station(output, "wlo1")
        with self.assertRaises(ValueError):
            parse_station(STATION, "wlan2")

    def test_interface_discovery(self):
        interfaces = parse_interfaces(INTERFACES)
        self.assertEqual(list(interfaces), ["wlo1", "wlan1"])
        self.assertEqual(interfaces["wlo1"].ssid, "A network: with spaces")
        self.assertEqual(interfaces["wlo1"].frequency, 5220)
        self.assertEqual(interfaces["wlo1"].width, "80 MHz")
        self.assertIsNone(interfaces["wlan1"].frequency)

    def test_networks_ipv4_preferred_ipv6_preserved(self):
        output = "GENERAL.DEVICE:wlo1\nGENERAL.DRIVER:iwlwifi\nGENERAL.STATE:100 (connected)\nIP4.GATEWAY:192.168.0.1\nIP6.GATEWAY:fe80::1\nGENERAL.DEVICE:wlan1\nGENERAL.STATE:100 (connected)\nIP4.GATEWAY:--\nIP6.GATEWAY:fe80::2"
        networks = parse_networks(output)
        self.assertEqual(networks["wlo1"].gateway, "192.168.0.1")
        self.assertEqual(networks["wlan1"].gateway, "fe80::2")
        self.assertTrue(networks["wlo1"].connected)

    def test_ping_reply_and_loss(self):
        self.assertEqual(parse_ping("64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time=2.26 ms"), 2.26)
        self.assertIsNone(parse_ping("1 packets transmitted, 0 received, 100% packet loss"))
        self.assertIsNone(parse_ping("From 192.168.0.120 icmp_seq=1 Destination Host Unreachable"))


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.model = Measurements()

    def sample(self, time, signal=-60, rx=1000, retries=10, identity=("wlo1", "aa:bb:cc:dd:ee:ff", 5220)):
        value = Sample(time, identity, Station(identity[1], signal=signal, counters={"rx bytes": rx, "tx retries": retries}))
        self.model.add(value)
        return value

    def test_best_requires_two_seconds(self):
        for i in range(8):
            self.sample(i * 0.25)
        self.assertIsNone(self.model.best)
        self.sample(2)
        self.assertEqual(self.model.best, -60)
        for i in range(1, 10):
            self.sample(2 + i * 0.25, -70)
        self.assertEqual(self.model.average, -70)
        self.assertEqual(self.model.best, -60)

    def test_timestamp_based_traffic_and_counter_reset(self):
        self.sample(0)
        self.sample(0.5, rx=126000, retries=12)
        self.assertEqual(self.model.rates["rx bytes"], 250000)
        self.assertEqual(self.model.deltas["tx retries"], 2)
        self.sample(0.75, rx=10, retries=0)
        self.assertNotIn("rx bytes", self.model.rates)
        self.assertNotIn("tx retries", self.model.deltas)
        self.sample(1, rx=35, retries=2)
        self.assertEqual(self.model.rates["rx bytes"], 100)

    def test_identity_change_clears_comparison(self):
        for i in range(9):
            self.sample(i * 0.25)
        self.sample(2.25, identity=("wlo1", "aa:bb:cc:dd:ee:ff", 2412))
        self.assertIsNone(self.model.best)
        self.assertEqual(len(self.model.history), 1)
        self.assertEqual(self.model.deltas, {})

    def test_stale_gap_resets_average_but_retains_best(self):
        for i in range(9):
            self.sample(i * 0.25)
        self.sample(5, -80)
        self.assertEqual(self.model.average, -80)
        self.assertEqual(self.model.best, -60)
        self.assertEqual(self.model.deltas, {})
        self.assertIn((5, None, None), self.model.history)

    def test_invalid_reading_never_appears_live(self):
        self.sample(0)
        self.model.invalidate(0.25)
        self.assertIsNone(self.model.current)
        self.assertIsNone(self.model.average)

    def test_missing_signal_breaks_average(self):
        self.sample(0)
        self.sample(0.25, signal=None)
        self.assertIsNone(self.model.average)
        self.assertEqual(len(self.model.window), 0)

    def test_reset_best_starts_fresh_window(self):
        for i in range(9):
            self.sample(i * 0.25)
        self.model.reset_best()
        self.assertIsNone(self.model.best)
        self.sample(2.25, -75)
        self.assertIsNone(self.model.best)
        self.assertEqual(self.model.average, -75)

    def test_history_is_bounded(self):
        for i in range(600):
            self.sample(i * 0.25)
        self.assertLessEqual(len(self.model.history), 241)


class PingTests(unittest.TestCase):
    def test_loss_excludes_pending_and_late_completion_cannot_replace_latest(self):
        window = PingWindow()
        older = window.start(0)
        newer = window.start(0.25)
        window.start(0.5)
        window.finish(newer, 2)
        self.assertEqual(window.stats(0.6)["loss"], 0)
        window.finish(older, None)
        stats = window.stats(1)
        self.assertEqual(stats["loss"], 50)
        self.assertEqual(stats["latest"], 2)
        self.assertEqual(stats["pending"], 1)
        window.finish(older, 5)
        self.assertEqual(window.stats(1)["loss"], 50)

    def test_variation_and_expiry(self):
        window = PingWindow()
        for now, rtt in ((0, 1), (0.25, 3)):
            window.finish(window.start(now), rtt)
        self.assertEqual(window.stats(1)["variation"], 1)
        self.assertEqual(window.stats(1)["mean"], 2)
        self.assertIsNone(window.stats(31)["loss"])
        self.assertEqual(window.stats(31)["sent"], 0)


if __name__ == "__main__":
    unittest.main()
