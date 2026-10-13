"""Parsers and timestamp-based calculations; deliberately independent of Qt."""

from collections import deque
from dataclasses import dataclass, field
import ipaddress
import math
import re
import statistics


@dataclass
class Interface:
    name: str
    ssid: str = ""
    frequency: float | None = None
    channel: int | None = None
    width: str = ""


@dataclass
class Network:
    driver: str = ""
    connected: bool = False
    gateway: str = ""


def parse_interfaces(output: str) -> dict[str, Interface]:
    interfaces = {}
    current = None
    managed = False

    def commit():
        if current and managed:
            interfaces[current.name] = current

    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Interface "):
            commit()
            name = text.removeprefix("Interface ")
            # Adapter names become command arguments. Ignore unexpected names
            # rather than letting one be interpreted as a command option.
            current = Interface(name) if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,14}", name) else None
            managed = False
        elif text.startswith("Unnamed/non-netdev interface") or text.startswith("phy#"):
            commit()
            current = None
            managed = False
        elif current:
            if text == "type managed":
                managed = True
            elif text.startswith("ssid "):
                current.ssid = text[5:]
            elif text.startswith("channel "):
                match = re.search(r"channel (\d+) \(([\d.]+) MHz\), width: ([^,]+)", text)
                if match:
                    current.channel = int(match[1])
                    current.frequency = float(match[2])
                    current.width = match[3]
    commit()
    return interfaces


def parse_networks(output: str) -> dict[str, Network]:
    networks = {}
    current = None
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key == "GENERAL.DEVICE":
            current = networks.setdefault(value, Network())
        elif current is not None:
            if key == "GENERAL.DRIVER":
                current.driver = value if value != "--" else ""
            elif key == "GENERAL.STATE":
                current.connected = value.startswith("100 ") or value == "100"
            elif key in ("IP4.GATEWAY", "IP6.GATEWAY"):
                gateway = valid_gateway(value)
                if gateway and (key == "IP4.GATEWAY" or not current.gateway):
                    current.gateway = gateway
    return networks


def valid_gateway(value: str) -> str:
    """Only send router probes to a literal, unicast IP gateway."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return ""
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        return ""
    if isinstance(address, ipaddress.IPv4Address) and int(address) == 0xffffffff:
        return ""
    # -I chooses the selected adapter; discard any scope supplied in metadata.
    return str(address).split("%", 1)[0]


@dataclass
class Station:
    bssid: str
    signal: float | None = None
    chains: tuple[float, ...] = ()
    average: float | None = None
    average_chains: tuple[float, ...] = ()
    beacon_average: float | None = None
    rx_link: str = ""
    tx_link: str = ""
    counters: dict[str, int] = field(default_factory=dict)
    inactive_ms: int | None = None


COUNTERS = ("rx bytes", "tx bytes", "rx packets", "tx packets", "tx retries", "tx failed", "beacon loss", "rx drop misc")


def parse_signal(value: str) -> tuple[float, tuple[float, ...]]:
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:\[([^]]*)\])?\s*dBm", value.strip())
    if not match:
        raise ValueError("Malformed signal reading")
    signal = float(match[1])
    chains = tuple(float(item.strip()) for item in match[2].split(",")) if match[2] else ()
    if any(not math.isfinite(v) or not -127 <= v <= 0 for v in (signal, *chains)):
        raise ValueError("Signal reading outside the dBm range")
    return signal, chains


def parse_station(output: str, interface: str) -> Station | None:
    if not output.strip() or output.strip() == "Not connected.":
        return None
    lines = output.splitlines()
    header = re.fullmatch(r"Station ((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}) \(on ([^)]+)\)", lines[0])
    if not header or header[2] != interface or any(line.startswith("Station ") for line in lines[1:]):
        raise ValueError("Unexpected station identity")
    station = Station(header[1].lower())
    for line in lines[1:]:
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        value = value.strip()
        if key == "signal":
            station.signal, station.chains = parse_signal(value)
        elif key == "signal avg":
            station.average, station.average_chains = parse_signal(value)
        elif key == "beacon signal avg":
            station.beacon_average, _ = parse_signal(value)
        elif key in COUNTERS:
            if not re.fullmatch(r"\d+", value):
                raise ValueError(f"Malformed {key} counter")
            station.counters[key] = int(value)
        elif key == "rx bitrate":
            station.rx_link = value
        elif key == "tx bitrate":
            station.tx_link = value
        elif key == "inactive time":
            match = re.fullmatch(r"(\d+) ms", value)
            station.inactive_ms = int(match[1]) if match else None
    return station


def parse_ping(output: str) -> float | None:
    # An RTT appears only in an echo reply, not in the final summary.
    match = re.search(r"\bicmp_seq=\d+.*?\btime([=<])([\d.]+)\s*ms", output)
    if not match:
        return None
    value = float(match[2])
    return value if math.isfinite(value) and value >= 0 else None


@dataclass
class Sample:
    time: float
    identity: tuple[str, str, float | None]
    station: Station


class Measurements:
    def __init__(self):
        self.identity = None
        self.history = deque(maxlen=500)
        self.window = deque(maxlen=20)
        self.current: Sample | None = None
        self.average = None
        self.best = None
        self.deltas = {}
        self.rates = {}
        self.contiguous_since = None

    def reset(self, identity=None):
        self.__init__()
        self.identity = identity

    def reset_best(self):
        self.best = None
        self.window.clear()
        self.average = None
        self.contiguous_since = None

    def invalidate(self, now: float):
        self.current = None
        self.window.clear()
        self.average = None
        self.deltas = {}
        self.rates = {}
        self.contiguous_since = None
        if self.history and self.history[-1][1] is not None:
            self.history.append((now, None, None))

    def add(self, sample: Sample):
        if self.identity != sample.identity:
            self.reset(sample.identity)
        previous = self.current
        if previous and sample.time - previous.time > 0.75:
            self.invalidate(sample.time)
            previous = None
        self.deltas = {}
        self.rates = {}
        if previous and sample.time > previous.time:
            elapsed = sample.time - previous.time
            for key, value in sample.station.counters.items():
                before = previous.station.counters.get(key)
                if before is not None and value >= before:
                    self.deltas[key] = value - before
                    self.rates[key] = (value - before) / elapsed
        self.current = sample
        signal = sample.station.signal
        if signal is not None:
            if self.contiguous_since is None:
                self.contiguous_since = sample.time
            self.window.append((sample.time, signal))
            while self.window and self.window[0][0] < sample.time - 2:
                self.window.popleft()
            self.average = statistics.mean(value for _, value in self.window)
            if sample.time - self.contiguous_since >= 2 and len(self.window) >= 6:
                self.best = max(self.best, self.average) if self.best is not None else self.average
        else:
            self.window.clear()
            self.average = None
            self.contiguous_since = None
        self.history.append((sample.time, signal, self.average))
        while self.history and self.history[0][0] < sample.time - 60:
            self.history.popleft()


@dataclass
class Probe:
    started: float
    finished: bool = False
    rtt: float | None = None


class PingWindow:
    def __init__(self):
        self.probes: dict[int, Probe] = {}
        self.next_id = 0

    def start(self, now: float) -> int:
        self.next_id += 1
        self.probes[self.next_id] = Probe(now)
        self.trim(now)
        return self.next_id

    def finish(self, probe_id: int, rtt: float | None):
        probe = self.probes.get(probe_id)
        if probe and not probe.finished:
            probe.finished = True
            probe.rtt = rtt

    def trim(self, now: float):
        self.probes = {key: probe for key, probe in self.probes.items() if probe.started >= now - 30}

    def stats(self, now: float) -> dict:
        self.trim(now)
        complete = [p for p in self.probes.values() if p.finished]
        replies = [p.rtt for p in complete if p.rtt is not None]
        latest = max(complete, key=lambda p: p.started) if complete else None
        return {
            "sent": len(self.probes), "completed": len(complete),
            "pending": len(self.probes) - len(complete), "received": len(replies),
            "loss": 100 * (len(complete) - len(replies)) / len(complete) if complete else None,
            "latest": latest.rtt if latest else None,
            "latest_time": latest.started if latest else None,
            "mean": statistics.mean(replies) if replies else None,
            "minimum": min(replies) if replies else None,
            "maximum": max(replies) if replies else None,
            "variation": statistics.pstdev(replies) if len(replies) >= 2 else None,
        }
