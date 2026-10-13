"""Bounded, asynchronous, read-only adapter queries and local router probes."""

import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .model import Measurements, Network, PingWindow, Sample, parse_interfaces, parse_networks, parse_ping, parse_station


class Command(QObject):
    done = Signal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LC_ALL", "C")
        self.process.setProcessEnvironment(env)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timeout)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.busy = False
        self.cancelled = False
        self.timed_out = False

    def start(self, program, arguments, timeout=1000):
        if self.busy or self.process.state() != QProcess.ProcessState.NotRunning:
            return False
        self.busy = True
        self.cancelled = False
        self.timed_out = False
        self.process.start(program, arguments)
        self.deadline.start(timeout)
        return True

    def cancel(self):
        self.cancelled = True
        self.deadline.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.busy = False

    def _timeout(self):
        self.timed_out = True
        self.process.kill()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(False, "", self.process.errorString())

    def _finished(self, code, status):
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        error = bytes(self.process.readAllStandardError()).decode("utf-8", "replace").strip()
        if self.timed_out:
            error = "Measurement timed out"
        self._complete(code == 0 and not self.timed_out and status == QProcess.ExitStatus.NormalExit, output, error)

    def _complete(self, ok, output, error):
        self.deadline.stop()
        was_busy = self.busy
        self.busy = False
        if was_busy and not self.cancelled:
            self.done.emit(ok, output, error)


class Monitor(QObject):
    changed = Signal()
    interfaces_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.interfaces = {}
        self.networks = {}
        self.selected = ""
        self.manual_selection = False
        self.measurements = Measurements()
        self.pings = PingWindow()
        self.probing = True
        self.gateway = ""
        self.status = "Looking for a connected Wi-Fi adapter…"
        self.ping_error = ""
        self.stopped = False
        self.generation = 0
        self.sample_context = None
        self.ping_jobs = set()
        self.station = Command(self)
        self.station.done.connect(self._station_done)
        self.discovery = Command(self)
        self.discovery.done.connect(self._discovery_done)
        self.network = Command(self)
        self.network.done.connect(self._network_done)
        self.metadata_timer = QTimer(self)
        self.metadata_timer.setInterval(2000)
        self.metadata_timer.timeout.connect(self.refresh_metadata)
        self.sample_timer = QTimer(self)
        self.sample_timer.setInterval(250)
        self.sample_timer.timeout.connect(self.poll)

    def start(self):
        self.refresh_metadata()
        self.metadata_timer.start()
        self.sample_timer.start()

    def refresh_metadata(self):
        if self.stopped:
            return
        self.discovery.start("iw", ["dev"])
        self.network.start("nmcli", ["-t", "-e", "no", "-f", "GENERAL.DEVICE,GENERAL.DRIVER,GENERAL.STATE,IP4.GATEWAY,IP6.GATEWAY", "device", "show"], 1500)

    def _discovery_done(self, ok, output, error):
        if not ok:
            self._unavailable("Cannot read Wi-Fi adapters: " + (error or "iw failed"))
            return
        previous = self.interfaces.get(self.selected)
        self.interfaces = parse_interfaces(output)
        if self.selected not in self.interfaces:
            candidates = list(self.interfaces.values())
            choice = next((i.name for i in candidates if i.ssid or self.networks.get(i.name, Network()).connected), candidates[0].name if candidates else "")
            self.select(choice, manual=False)
        elif previous and previous.frequency != self.interfaces[self.selected].frequency:
            self._reset_connection()
        # If started offline, prefer an adapter that subsequently connects.
        if not self.manual_selection and self.selected and not self.interfaces[self.selected].ssid:
            connected = next((i.name for i in self.interfaces.values() if i.ssid), None)
            if connected and connected != self.selected:
                self.select(connected, manual=False)
        if not self.interfaces:
            self._unavailable("No Wi-Fi adapter found. Connect or enable Wi-Fi to begin.")
        self.interfaces_changed.emit()
        self.changed.emit()

    def _network_done(self, ok, output, error):
        self.networks = parse_networks(output) if ok else {}
        network = self.networks.get(self.selected, Network())
        gateway = network.gateway if network.connected else ""
        if gateway != self.gateway:
            self.gateway = gateway
            self._reset_pings()
        self.changed.emit()

    def select(self, name, manual=True):
        if manual:
            self.manual_selection = True
        if name == self.selected:
            return
        self.selected = name
        self.station.cancel()
        self._reset_connection()
        network = self.networks.get(name, Network())
        self.gateway = network.gateway if network.connected else ""
        self.status = "Waiting for Wi-Fi measurements…" if name else "No Wi-Fi adapter found."
        self.changed.emit()

    def _reset_connection(self):
        self.generation += 1
        self.measurements.reset()
        self._reset_pings()

    def _reset_pings(self):
        for job in tuple(self.ping_jobs):
            job.cancel()
            # Delete after the process exits, not while it is running.
            if job.process.state() == QProcess.ProcessState.NotRunning:
                job.deleteLater()
            else:
                job.process.finished.connect(job.deleteLater)
        self.ping_jobs.clear()
        self.pings = PingWindow()
        self.ping_error = ""

    def set_probing(self, enabled):
        self.probing = enabled
        self._reset_pings()
        self.changed.emit()

    def _unavailable(self, status):
        self.measurements.invalidate(time.monotonic())
        self.status = status
        self.changed.emit()

    def poll(self):
        if self.stopped:
            return
        now = time.monotonic()
        current = self.measurements.current
        if current and now - current.time > 1:
            self._unavailable("Measurements are stale — waiting for the adapter…")
        if self.selected and self.selected in self.interfaces and not self.station.busy:
            interface = self.interfaces[self.selected]
            context = (self.generation, self.selected, interface.frequency)
            if self.station.start("iw", ["dev", self.selected, "station", "dump"]):
                self.sample_context = context
        if self.probing and self.gateway and self.measurements.current and len(self.ping_jobs) < 6:
            self._probe()

    def _station_done(self, ok, output, error):
        if not self.sample_context or self.sample_context[0] != self.generation:
            return
        if not ok:
            self._unavailable("Cannot read adapter: " + (error or "iw failed"))
            return
        try:
            station = parse_station(output, self.selected)
        except ValueError as exc:
            self._unavailable(str(exc))
            return
        if station is None:
            if self.measurements.identity is not None:
                self._reset_connection()
            self._unavailable("Wi-Fi disconnected — reconnect to continue.")
            return
        now = time.monotonic()
        identity = (self.selected, station.bssid, self.sample_context[2])
        if self.measurements.identity is not None and self.measurements.identity != identity:
            self._reset_pings()
        self.measurements.add(Sample(now, identity, station))
        self.status = "Live · 4 readings / second" if station.signal is not None else "Adapter does not report signal strength"
        self.changed.emit()

    def _probe(self):
        job = Command(self)
        self.ping_jobs.add(job)
        window = self.pings
        probe_id = window.start(time.monotonic())

        def finished(ok, output, error):
            self.ping_jobs.discard(job)
            if self.pings is window:
                # Failed commands and timeouts never count as valid replies.
                window.finish(probe_id, parse_ping(output) if ok else None)
                self.ping_error = error if error and error != "Measurement timed out" else ""
                self.changed.emit()
            job.deleteLater()

        job.done.connect(finished)
        job.start("ping", ["-n", "-I", self.selected, "-c", "1", "-W", "1", self.gateway], 1400)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.metadata_timer.stop()
        self.sample_timer.stop()
        commands = self.findChildren(Command)
        for command in commands:
            command.cancel()
        # Only shutdown may wait; kill was already issued to every process.
        for command in commands:
            if command.process.state() != QProcess.ProcessState.NotRunning:
                command.process.waitForFinished(200)
        self.ping_jobs.clear()
