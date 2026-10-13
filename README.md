# Wi-Fi Antenna Tuner

A native KDE/Wayland desktop tool for finding a better position for your PC's Wi-Fi antenna. Everything stays on your PC. The only measurement traffic is small ICMP probes to your local router, enabled by default and easily disabled.

## Install on Bazzite or Linux

Clone this private repository with an authenticated GitHub account that has access:

```sh
gh repo clone Mordeton/wifi-antenna-tuner
cd wifi-antenna-tuner
bash install.sh
```

You can also download the repository ZIP or release archive from GitHub while signed in, extract it, open a terminal in the extracted folder, and run **`bash install.sh`**.

The installer asks two separate questions:

1. **Create a searchable application-menu / quick-launch icon?** Default: yes.
2. **Create a desktop shortcut?** Default: no.

The application-menu icon is searchable in KDE's launcher and **KRunner** using “Wi-Fi Antenna Tuner”, “wifi”, or “antenna”. For a pinned quick-launch icon, start the app and right-click its taskbar icon → **Pin to Task Manager**. The installer creates the menu entry without changing your taskbar layout.

The app installs to `~/.local/share/wifi-antenna-tuner/` by default, or `$XDG_DATA_HOME/wifi-antenna-tuner/` when configured. It copies its files into that directory, so the original download can be removed afterward. It uses a private Python environment: compatible host PySide6 is reused when available; otherwise PySide6 is downloaded into that environment. No `sudo`, OS package layering, reboot, or system Python package installation is performed. This approach works with Bazzite's immutable system image. [Qt recommends virtual environments for PySide6](https://doc.qt.io/qtforpython-6/gettingstarted.html).

Requirements: **Linux, Python 3.10+, virtual-environment support, `iw`, NetworkManager's `nmcli`, and `ping`**. The native tools and Python support were verified on the target Bazzite PC. On other distributions, install missing prerequisites through your distribution's package manager before running the installer. Debian/Ubuntu may require `python3-venv`; Qt also needs the platform's normal desktop/audio libraries. If Python cannot create an environment and `uv` is available, the installer can use it instead. Downloading PySide6 requires internet access and a wheel compatible with your architecture and Python version.

For automation or custom locations:

```sh
# Defaults without questions: searchable launcher, no desktop shortcut.
bash install.sh --yes

# Both shortcuts, with no questions.
bash install.sh --menu --desktop

# Neither shortcut; run the installed launch.sh directly.
bash install.sh --no-menu --no-desktop

# Optional location overrides.
bash install.sh --prefix /path/to/app --applications-dir /path/to/applications --desktop-dir /path/to/Desktop
```

## Launch, update, or uninstall

Search for **Wi-Fi Antenna Tuner** in your application menu or KRunner, or run:

```sh
~/.local/share/wifi-antenna-tuner/launch.sh
```

To update a clone, run `git pull --ff-only` and `bash install.sh` again. For downloaded archives, download/extract the newer version and run its installer. An upgrade backs up the existing installation and restores it if installation fails. Existing same-name shortcut files are backed up; uninstall restores them. Shortcut files you edit afterward are retained during uninstall.

To uninstall, close the app and run:

```sh
~/.local/share/wifi-antenna-tuner/uninstall.sh
```

The uninstaller asks for confirmation. Use `--yes` for unattended removal. For a custom installation path, use the `uninstall.sh` in that directory. If an OS update replaces Python or Qt and launching fails, rerun the installer from a current source checkout/archive to rebuild the app's environment. There are no background services or persistent measurement databases.

## Find a good placement

1. Watch the big **signal strength** number while moving or rotating the antenna. Less-negative numbers are stronger: **−50 dBm is stronger than −70 dBm**.
2. Hold each position for at least two seconds. Compare the **two-second average** and the graph, not just a momentary peak. **Best average** keeps the strongest settled reading this session; **Reset best** starts a fresh comparison.
3. Check router latency and loss for stability. Router response time is affected by router load and power saving too, so use it alongside signal strength.
4. Expand **Stats for nerds** for radio link rates, actual traffic, driver averages, per-chain signal, retries, and latency statistics. The section stays open as readings update.
5. Adjust **Sensitivity** to match your task: **1×** shows a broad 60 dB range, **3×** (the default) shows 20 dB, and **10×** shows 6 dB for fine placement changes. Higher values magnify small changes in the bar, graph, and audio. Use **Recenter** to center the range on the current two-second average; the range stays fixed while you move the antenna. Changing sensitivity also recenters it. At 1× the overview stays fixed at −90 to −30 dBm.
6. Enable **Sound**, then choose **Faster beeps**, **Continuous pitch**, or **Pitch + beeps**. Stronger signal means quicker beeps, higher pitch, or both. The app starts muted; the volume slider affects only this app. Continuous audio uses a running oscillator with smooth transitions rather than repeatedly restarting tone files.
7. Use **Fullscreen** / **F11** when standing away from the PC; **Escape** exits fullscreen.

Sensitivity changes feedback, not the radio measurements. The large dBm number and the two-second average/best stay accurate and unchanged. For sound, the averaging window shortens from two seconds at 1× to 0.2 seconds at 10× (effectively the latest available 250 ms reading). High sensitivity makes small variations more apparent; it cannot extract precision the Wi-Fi driver does not report. If the meter reaches an edge, use **Recenter** or reduce sensitivity. Sound stops when readings become stale or Wi-Fi disconnects.

## Cisco signal-quality meter

The separate **Cisco signal quality** meter puts the current live reading on a fixed five-level scale from [Cisco's IW9167E RSSI guidance](https://www.cisco.com/c/en/us/td/docs/IIOT/wireless/iw9167e/software/config/262x/iw9167-scg-262x/g_manage-the-workgroup-bridges-through-web-ui_wgb/t_modify-rssi-threshold.html). It shows a moving pointer, the current rating, and a short explanation. Sensitivity and Recenter do not affect its rating.

| Rating | App's range |
| --- | --- |
| Excellent | −50 dBm or stronger |
| Good | −60 dBm up to, but below, −50 dBm |
| Fair | −70 dBm up to, but below, −60 dBm |
| Weak | −80 dBm up to, but below, −70 dBm |
| Very poor | Below −80 dBm |

Cisco's published ranges share endpoints. The app assigns an exact boundary to the stronger band and keeps readings stronger than −30 dBm in Excellent. Its pointer clamps to the visual endpoints (−100 to −30 dBm); the displayed number is never clipped. Missing or stale readings remove the pointer and show **No live reading**. This is signal-strength guidance for this Cisco reference, not a measurement of interference or a guarantee of connection speed; use latency and packet loss alongside it.

## What the numbers mean

- Signal is polled every 250 ms. Polling faster does not guarantee the Wi-Fi driver has a newer radio sample; the driver's RX inactivity is shown in details. Repeated values are normal.
- The graph covers the last 60 seconds. Its vertical scale follows the selected sensitivity range, whose bounds are shown beneath the meter. The large numerical reading is never clipped to that range. The faint line is live signal and the bold line is a trailing two-second arithmetic average of dBm readings.
- Best signal is recorded after two seconds of continuous valid readings. Moving to another adapter, access point, or frequency, or reconnecting, starts a new comparison. A sampling interruption breaks the averaging window.
- Per-chain values are driver identifiers and are not a reliable mapping to particular external antenna elements. Unreported measurements are marked unavailable.
- Link rates describe the last reported radio transmission/reception rates, **not internet speed**. Actual traffic is calculated from byte-counter differences. Traffic is not a controlled throughput benchmark.
- Retry/failure/packet changes are counts since the preceding sample, not percentages. Counter decreases reset the affected baseline rather than producing negative traffic.
- Router probes run four times per second, bound to the selected Wi-Fi interface, with a one-second reply deadline. No reply can also mean a router filters ICMP; it does not prove Wi-Fi failed.
- Loss uses completed probes in the trailing 30-second window; pending probes are shown separately and excluded. RTT variation is the population standard deviation of replies in that window. A delayed result never overwrites a newer probe's result.
- Network settings are never changed. The app does not scan for networks, tune transmit power, reconnect Wi-Fi, or send internet speed-test traffic. Histories stay in memory and disappear when the app exits.

## Development and verification

```sh
cd wifi-antenna-tuner
QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m unittest discover -s tests -v
```

The application separates parsing/calculations (`model.py`), feedback sensitivity (`feedback.py`), asynchronous commands and probe lifecycle (`backend.py`), streaming audio (`audio.py`), and Qt widgets (`ui.py`). Tests use synthetic fixtures and processes, without changing Wi-Fi settings. Audio tests verify pitch direction, beep cadence, continuity across chunks, sensitivity scaling, and stale-signal muting. Installer tests verify shortcut choices, portable paths, upgrades, rollback, and preservation of existing/user-edited shortcuts. GitHub Actions runs the suite on Linux with Python 3.12 and 3.14.

For development, run `./launch.sh` from the source checkout with PySide6 installed, or use the installed runtime. Installation is optional for development.
