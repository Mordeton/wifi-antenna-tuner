#!/usr/bin/env python3
"""User-local Linux installation, shortcuts, upgrades, rollback and removal."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid


APP_ID = "wifi-antenna-tuner"
MANIFEST = ".install-manifest.json"
QT_CHECK = (
    "import PySide6; from PySide6 import QtCore, QtWidgets, QtMultimedia; "
    "v=tuple(int(x) for x in PySide6.__version__.split('.')[:2]); "
    "assert (6,8)<=v<(7,0), 'PySide6 6.8 or newer is required'; "
    "print(PySide6.__version__)"
)


class InstallError(Exception):
    pass


@dataclass
class Options:
    prefix: Path
    applications_dir: Path
    desktop_dir: Path | None
    menu: bool
    desktop: bool


def path_value(value):
    path = Path(value).expanduser().absolute()
    if any(char in str(path) for char in "\n\r\x00"):
        raise InstallError("Installation paths cannot contain line breaks or NUL characters")
    return path


def data_home():
    return path_value(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def desktop_directory():
    tool = shutil.which("xdg-user-dir")
    if tool:
        result = subprocess.run([tool, "DESKTOP"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            path = path_value(result.stdout.strip())
            return path if path.resolve() != Path.home().resolve() else None
    fallback = Path.home() / "Desktop"
    return fallback if fallback.is_dir() else None


def ask(question, default):
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            reply = input(question + suffix).strip().lower()
        except EOFError as exc:
            raise InstallError("No answer received. Use --yes or explicit --menu/--no-menu and --desktop/--no-desktop flags.") from exc
        if reply in ("", "y", "yes", "n", "no"):
            return default if not reply else reply in ("y", "yes")
        print("Please enter yes or no.")


def command(arguments):
    try:
        subprocess.run([str(arg) for arg in arguments], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InstallError(f"Command failed: {arguments[0]}") from exc


def check_tools():
    missing = [name for name in ("iw", "nmcli", "ping") if not shutil.which(name)]
    if missing:
        raise InstallError(
            "Missing native tools: " + ", ".join(missing) + ". "
            "They normally come with Bazzite. On other distributions install iw, NetworkManager, and iputils-ping/iputils first."
        )


def qt_version(python):
    result = subprocess.run([str(python), "-c", QT_CHECK], capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else None


def create_runtime(prefix):
    existing = qt_version(sys.executable)
    environment = prefix / ".venv"
    arguments = [sys.executable, "-m", "venv"]
    if existing:
        print(f"Using compatible host PySide6 {existing} in a private environment.")
        arguments += ["--system-site-packages", "--without-pip"]
    arguments.append(str(environment))
    try:
        command(arguments)
    except InstallError:
        uv = shutil.which("uv")
        if not uv:
            raise InstallError("Cannot create a Python environment. Install your distribution's python3-venv support, or uv, then retry.")
        fallback = [uv, "venv", "--python", sys.executable]
        if existing:
            fallback.append("--system-site-packages")
        command(fallback + [str(environment)])
    python = environment / "bin/python"
    if not qt_version(python):
        print("Installing PySide6 into the app's environment (download required)…")
        has_pip = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode == 0
        if has_pip:
            install = [python, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:"]
        elif shutil.which("uv"):
            install = [shutil.which("uv"), "pip", "install", "--python", str(python), "--only-binary=:all:"]
        else:
            raise InstallError("The app environment has no pip. Install python3-venv or uv and retry.")
        command(install + ["-r", str(prefix / "requirements.txt")])
    version = qt_version(python)
    if not version:
        raise InstallError("The installed Qt dependency did not pass its import check")
    return version


def desktop_string(value):
    return str(value).replace("\\", "\\\\").replace("\t", "\\t")


def exec_argument(path):
    # Exec parsing has its own escaping, after desktop-entry string unescaping.
    value = str(path).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + desktop_string(value) + '"'


def render_desktop(source, prefix):
    template = (source / "assets" / f"{APP_ID}.desktop.in").read_text()
    return template.replace("@EXEC@", exec_argument(prefix / "launch.sh")).replace("@ICON@", desktop_string(prefix / "assets/antenna-tuner.svg"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_manifest(prefix):
    path = prefix / MANIFEST
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise InstallError("The existing installation manifest is unreadable") from exc
    if data.get("app_id") != APP_ID or Path(data.get("prefix", "")).resolve() != prefix.resolve():
        raise InstallError("The directory is not a recognized Wi-Fi Antenna Tuner installation")
    return data


def snapshot(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise InstallError(f"Refusing to replace a non-regular shortcut: {path}")
    return (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None


def restore(path, saved):
    if saved is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(saved[0])
        path.chmod(saved[1])


def original_shortcut(prefix, record):
    backup = record.get("backup")
    if not backup:
        return None
    # Only our generated backup basenames are accepted.
    if Path(backup).name != backup:
        raise InstallError("Invalid shortcut backup in the installation manifest")
    return snapshot(prefix / ".shortcut-backups" / backup)


def refresh_launchers(applications_dir=None):
    for tool in ("update-desktop-database", "kbuildsycoca6"):
        executable = shutil.which(tool)
        if executable:
            if tool == "update-desktop-database" and applications_dir is None:
                continue
            arguments = [executable] + ([str(applications_dir)] if tool == "update-desktop-database" else [])
            try:
                result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                print(f"Launcher cache refresh skipped: {tool}; log out and back in if the entry is not searchable yet.")
                continue
            if result.returncode != 0:
                print(f"Launcher cache refresh skipped: {tool}; log out and back in if the entry is not searchable yet.")


def install(source, options):
    prefix = options.prefix
    if prefix.is_symlink() or prefix.resolve() == source.resolve() or prefix.resolve() in source.resolve().parents:
        raise InstallError("Choose an installation directory separate from the source and its parent directories")
    old = read_manifest(prefix)
    if prefix.exists() and any(prefix.iterdir()) and old is None:
        raise InstallError(f"Refusing to replace an unrelated directory: {prefix}")
    check_tools()
    selected = []
    if options.menu:
        selected.append(("menu", options.applications_dir / f"{APP_ID}.desktop"))
    if options.desktop:
        if options.desktop_dir is None:
            raise InstallError("No desktop directory is configured. Enable it or supply --desktop-dir.")
        selected.append(("desktop", options.desktop_dir / f"{APP_ID}.desktop"))
    if len({path.resolve() for _, path in selected}) != len(selected):
        raise InstallError("Desktop and application-menu shortcuts must use different directories")
    previous = {record["path"]: record for record in (old or {}).get("shortcuts", [])}
    paths = {path for _, path in selected} | {Path(path) for path in previous}
    if any(path.resolve().is_relative_to(prefix.resolve()) for path in paths):
        raise InstallError("Shortcut directories must be outside the installation directory")
    saved = {path: snapshot(path) for path in paths}
    prior_originals = {path: original_shortcut(prefix, record) for path, record in previous.items()}
    prefix.parent.mkdir(parents=True, exist_ok=True)
    rollback = prefix.with_name(f".{prefix.name}.rollback-{uuid.uuid4().hex}")
    existed = prefix.exists()
    if existed:
        prefix.rename(rollback)
    try:
        prefix.mkdir()
        for directory in ("antenna_tuner", "assets", "packaging"):
            shutil.copytree(source / directory, prefix / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("README.md", "requirements.txt", "launch.sh", "install.sh"):
            shutil.copy2(source / name, prefix / name)
        for name in ("launch.sh", "install.sh"):
            (prefix / name).chmod(0o755)
        uninstaller = (
            "#!/usr/bin/env bash\nset -eu\n"
            'app_dir=$(dirname -- "$(readlink -f -- "$0")")\n'
            f'exec {shlex.quote(sys.executable)} "$app_dir/packaging/install.py" --uninstall --prefix "$app_dir" "$@"\n'
        )
        (prefix / "uninstall.sh").write_text(uninstaller)
        (prefix / "uninstall.sh").chmod(0o755)
        qt = create_runtime(prefix)
        entry = render_desktop(prefix, prefix).encode()
        internal = prefix / f"{APP_ID}.desktop"
        internal.write_bytes(entry)
        validator = shutil.which("desktop-file-validate")
        if validator:
            command([validator, internal])
        records = []
        for index, (kind, path) in enumerate(selected):
            before = saved[path]
            previous_record = previous.get(str(path))
            if previous_record and before and digest(before[0]) == previous_record["installed_sha256"]:
                before = prior_originals[str(path)]
            backup = None
            if before is not None:
                backup = f"{index}.desktop"
                restore(prefix / ".shortcut-backups" / backup, before)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(entry)
            path.chmod(0o755 if kind == "desktop" else 0o644)
            records.append({"kind": kind, "path": str(path), "installed_sha256": digest(entry), "backup": backup})
        wanted = {str(path) for _, path in selected}
        for path, record in previous.items():
            if path not in wanted:
                before = saved[Path(path)]
                if before and digest(before[0]) == record["installed_sha256"]:
                    restore(Path(path), prior_originals[path])
        manifest = {
            "schema": 1, "app_id": APP_ID, "prefix": str(prefix), "qt_version": qt,
            "installed_at": datetime.now(timezone.utc).isoformat(), "shortcuts": records,
        }
        (prefix / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        for path, before in saved.items():
            restore(path, before)
        if prefix.exists():
            shutil.rmtree(prefix)
        if existed:
            rollback.rename(prefix)
        raise
    if existed:
        shutil.rmtree(rollback)
    refresh_launchers(options.applications_dir if options.menu else None)
    print(f"\nInstalled Wi-Fi Antenna Tuner in {prefix}")
    for kind, path in selected:
        print(f"  {'Searchable application launcher' if kind == 'menu' else 'Desktop shortcut'}: {path}")
    if options.menu:
        print('  Search for "Wi-Fi Antenna Tuner", "wifi", or "antenna" in KDE/KRunner.')
        print('  To pin it for quick access: launch it, right-click its taskbar icon, and choose Pin to Task Manager.')
    print(f"  Launch: {prefix / 'launch.sh'}")
    print(f"  Uninstall: {prefix / 'uninstall.sh'}")
    return manifest


def uninstall(prefix):
    if prefix.is_symlink():
        raise InstallError("Refusing to remove a symlinked installation directory")
    manifest = read_manifest(prefix)
    if manifest is None:
        raise InstallError("No managed installation found at this path")
    # Validate all targets before changing any of them.
    records = [(Path(record["path"]), record, snapshot(Path(record["path"])), original_shortcut(prefix, record)) for record in manifest["shortcuts"]]
    for path, record, before, original in records:
        if before and digest(before[0]) == record["installed_sha256"]:
            restore(path, original)
        elif before:
            print(f"Keeping a shortcut edited after installation: {path}")
    shutil.rmtree(prefix)
    menu_directory = next((path.parent for path, record, _, _ in records if record["kind"] == "menu"), None)
    refresh_launchers(menu_directory)
    print("Wi-Fi Antenna Tuner was uninstalled.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Wi-Fi Antenna Tuner for the current Linux user; no sudo required.")
    parser.add_argument("--prefix", default=str(data_home() / APP_ID), help="application directory")
    parser.add_argument("--applications-dir", default=str(data_home() / "applications"), help="application-menu directory")
    parser.add_argument("--desktop-dir", help="override the XDG desktop directory")
    parser.add_argument("--menu", action=argparse.BooleanOptionalAction, default=None, help="create a searchable application-menu / quick-launch entry")
    parser.add_argument("--desktop", action=argparse.BooleanOptionalAction, default=None, help="create a desktop shortcut")
    parser.add_argument("--yes", action="store_true", help="use defaults for unanswered questions (menu yes, desktop no)")
    parser.add_argument("--uninstall", action="store_true", help="remove this app's managed installation and shortcuts")
    args = parser.parse_args(argv)
    try:
        if not sys.platform.startswith("linux") or sys.version_info < (3, 10):
            raise InstallError("This installer requires Linux and Python 3.10 or newer")
        if os.geteuid() == 0:
            raise InstallError("Run this installer as your normal desktop user, without sudo")
        prefix = path_value(args.prefix)
        if args.uninstall:
            if args.yes or ask(f"Uninstall Wi-Fi Antenna Tuner from {prefix}?", False):
                uninstall(prefix)
            return 0
        print("Wi-Fi Antenna Tuner — Linux installer\n")
        print(f"Installation directory: {prefix}\n")
        menu = args.menu if args.menu is not None else (True if args.yes else ask("Create a searchable application-menu / quick-launch icon?", True))
        desktop = args.desktop if args.desktop is not None else (False if args.yes else ask("Create a desktop shortcut?", False))
        options = Options(prefix, path_value(args.applications_dir), path_value(args.desktop_dir) if args.desktop_dir else desktop_directory(), menu, desktop)
        install(Path(__file__).resolve().parent.parent, options)
        return 0
    except (InstallError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Installation error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInstallation cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
