import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Wi-Fi Antenna Tuner")
    app.setDesktopFileName("wifi-antenna-tuner")
    app.setOrganizationName("Local Tools")
    window = MainWindow()
    app.aboutToQuit.connect(window.monitor.stop)
    app.aboutToQuit.connect(window.beeper.stop)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    # Returning regularly to Python lets its signal handlers run.
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(250)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
