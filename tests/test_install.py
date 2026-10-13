import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tuner_installer", SOURCE / "packaging/install.py")
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.options = installer.Options(root / "installed app", root / "applications", root / "Desktop", True, True)
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.patches = [
            patch.object(installer, "check_tools"),
            patch.object(installer, "create_runtime", return_value="6.11.2"),
            patch.object(installer, "refresh_launchers"),
        ]
        for patched in self.patches:
            patched.start()
            self.addCleanup(patched.stop)

    def shortcut(self, kind="menu"):
        directory = self.options.applications_dir if kind == "menu" else self.options.desktop_dir
        return directory / "wifi-antenna-tuner.desktop"

    def test_prompts_support_defaults_and_independent_choices(self):
        with patch("builtins.input", side_effect=["maybe", "no", ""]):
            self.assertFalse(installer.ask("Menu?", True))
            self.assertFalse(installer.ask("Desktop?", False))
        with patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(installer.InstallError):
                installer.ask("Menu?", True)

    def test_new_install_has_portable_searchable_launchers_and_uninstaller(self):
        manifest = installer.install(SOURCE, self.options)
        self.assertEqual(len(manifest["shortcuts"]), 2)
        menu = self.shortcut().read_text()
        self.assertIn(str(self.options.prefix), menu)
        self.assertNotIn("/home/Mordeton", menu)
        self.assertIn("Keywords=wifi;", menu)
        self.assertEqual(self.shortcut().stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.shortcut("desktop").stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.options.prefix / "uninstall.sh").exists())
        self.assertFalse((self.options.prefix / "tests").exists())

    def test_declining_both_shortcuts_creates_neither_directory(self):
        self.options.menu = self.options.desktop = False
        manifest = installer.install(SOURCE, self.options)
        self.assertEqual(manifest["shortcuts"], [])
        self.assertFalse(self.options.applications_dir.exists())
        self.assertFalse(self.options.desktop_dir.exists())

    def test_upgrade_and_uninstall_restore_preexisting_shortcut(self):
        original = b"[Desktop Entry]\nName=Previous personal shortcut\n"
        self.shortcut().parent.mkdir()
        self.shortcut().write_bytes(original)
        self.shortcut().chmod(0o640)
        installer.install(SOURCE, self.options)
        installer.install(SOURCE, self.options)
        installer.uninstall(self.options.prefix)
        self.assertEqual(self.shortcut().read_bytes(), original)
        self.assertEqual(self.shortcut().stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.shortcut("desktop").exists())
        self.assertFalse(self.options.prefix.exists())

    def test_user_edited_shortcut_is_preserved_on_uninstall(self):
        installer.install(SOURCE, self.options)
        self.shortcut().write_text("A later personal edit\n")
        installer.uninstall(self.options.prefix)
        self.assertEqual(self.shortcut().read_text(), "A later personal edit\n")

    def test_failed_upgrade_rolls_back_old_app_and_shortcuts(self):
        installer.install(SOURCE, self.options)
        before = self.shortcut().read_bytes()
        manifest_before = (self.options.prefix / installer.MANIFEST).read_bytes()
        (self.options.prefix / "rollback-marker").write_text("old app")
        with patch.object(installer, "create_runtime", side_effect=installer.InstallError("simulated dependency failure")):
            with self.assertRaises(installer.InstallError):
                installer.install(SOURCE, self.options)
        self.assertEqual(self.shortcut().read_bytes(), before)
        self.assertEqual((self.options.prefix / installer.MANIFEST).read_bytes(), manifest_before)
        self.assertEqual((self.options.prefix / "rollback-marker").read_text(), "old app")
        self.assertFalse(list(self.options.prefix.parent.glob("*.rollback-*")))

    def test_rollback_restores_old_app_even_if_one_shortcut_cannot_be_restored(self):
        installer.install(SOURCE, self.options)
        (self.options.prefix / "rollback-marker").write_text("old app")
        real_restore = installer.restore
        def fail_one_restore(path, saved):
            if path == self.shortcut():
                raise OSError("simulated rollback shortcut failure")
            return real_restore(path, saved)
        with patch.object(installer, "create_runtime", side_effect=installer.InstallError("simulated dependency failure")), patch.object(installer, "restore", fail_one_restore):
            with self.assertRaisesRegex(installer.InstallError, "rollback was incomplete"):
                installer.install(SOURCE, self.options)
        self.assertEqual((self.options.prefix / "rollback-marker").read_text(), "old app")
        self.assertFalse(list(self.options.prefix.parent.glob("*.rollback-*")))

    def test_shortcut_write_failure_rolls_back_changes_already_made(self):
        self.shortcut().parent.mkdir()
        self.shortcut().write_text("original\n")
        self.options.desktop_dir.mkdir()
        real_restore = installer.restore
        def fail_desktop(path, saved):
            if path == self.shortcut("desktop") and saved is not None:
                raise OSError("simulated desktop write failure")
            return real_restore(path, saved)
        with patch.object(installer, "restore", fail_desktop):
            with self.assertRaises(OSError):
                installer.install(SOURCE, self.options)
        self.assertEqual(self.shortcut().read_text(), "original\n")
        self.assertFalse(self.options.prefix.exists())

    def test_existing_hardlinked_shortcut_does_not_change_the_other_file(self):
        unrelated = self.options.prefix.parent / "unrelated.txt"
        unrelated.write_text("Keep this data\n")
        self.shortcut().parent.mkdir()
        os.link(unrelated, self.shortcut())
        installer.install(SOURCE, self.options)
        self.assertEqual(unrelated.read_text(), "Keep this data\n")
        self.assertNotEqual(self.shortcut().stat().st_ino, unrelated.stat().st_ino)
        installer.uninstall(self.options.prefix)
        self.assertEqual(unrelated.read_text(), "Keep this data\n")
        self.assertEqual(self.shortcut().read_text(), "Keep this data\n")

    def test_damaged_manifest_or_missing_backup_cannot_change_installation(self):
        self.shortcut().parent.mkdir()
        self.shortcut().write_text("Original launcher\n")
        installer.install(SOURCE, self.options)
        manifest_path = self.options.prefix / installer.MANIFEST
        original = manifest_path.read_bytes()
        shortcut = self.shortcut().read_bytes()
        for change in (
            lambda data: data.update(shortcuts="bad"),
            lambda data: data["shortcuts"][0].update(path=str(self.options.prefix / "other.desktop")),
            lambda data: data["shortcuts"][0].update(backup="../launch.sh"),
        ):
            with self.subTest(change=change):
                data = json.loads(original)
                change(data)
                manifest_path.write_text(json.dumps(data))
                with self.assertRaises(installer.InstallError):
                    installer.uninstall(self.options.prefix)
                self.assertEqual(self.shortcut().read_bytes(), shortcut)
                self.assertTrue(self.options.prefix.exists())
        manifest_path.write_bytes(original)
        (self.options.prefix / ".shortcut-backups/0.desktop").unlink()
        with self.assertRaises(installer.InstallError):
            installer.uninstall(self.options.prefix)
        self.assertEqual(self.shortcut().read_bytes(), shortcut)
        self.assertTrue(self.options.prefix.exists())

    def test_symlinked_manifest_and_parent_path_traversal_are_rejected(self):
        self.options.prefix.mkdir()
        other = self.options.prefix.parent / "other-manifest.json"
        other.write_text("{}")
        (self.options.prefix / installer.MANIFEST).symlink_to(other)
        with self.assertRaises(installer.InstallError):
            installer.install(SOURCE, self.options)
        self.assertEqual(other.read_text(), "{}")
        with self.assertRaises(installer.InstallError):
            installer.path_value(str(self.options.prefix / ".." / "unrelated"))

    def test_source_overlap_and_source_symlinks_are_rejected_before_copy(self):
        source = Path(self.temporary.name) / "download"
        source.mkdir()
        nested = source / "nested-install"
        self.options.prefix = nested
        with patch.object(installer, "check_source", side_effect=AssertionError("overlap was not rejected")):
            with self.assertRaises(installer.InstallError):
                installer.install(source, self.options)
        self.assertFalse(nested.exists())

        for name in installer.SOURCE_DIRECTORIES:
            (source / name).mkdir(parents=True)
        for name in installer.SOURCE_FILES:
            (source / name).write_text("test\n")
        (source / "assets" / "outside").symlink_to(SOURCE / "README.md")
        self.options.prefix = Path(self.temporary.name) / "separate-install"
        with self.assertRaises(installer.InstallError):
            installer.install(source, self.options)
        self.assertFalse(self.options.prefix.exists())

    def test_shortcut_removed_on_upgrade_when_user_declines_it(self):
        installer.install(SOURCE, self.options)
        self.options.desktop = False
        installer.install(SOURCE, self.options)
        self.assertFalse(self.shortcut("desktop").exists())
        self.assertTrue(self.shortcut().exists())

    def test_unrelated_directory_or_symlink_is_rejected(self):
        self.options.prefix.mkdir()
        (self.options.prefix / "important.txt").write_text("keep")
        with self.assertRaises(installer.InstallError):
            installer.install(SOURCE, self.options)
        self.assertEqual((self.options.prefix / "important.txt").read_text(), "keep")
        self.options.prefix = self.options.prefix.parent / "symlink"
        self.options.prefix.symlink_to(self.options.applications_dir)
        with self.assertRaises(installer.InstallError):
            installer.install(SOURCE, self.options)

    def test_paths_with_spaces_and_exec_special_characters_validate(self):
        if not installer.shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate is not installed")
        special = self.options.prefix.parent / 'space dollar$ quote" slash\\ percent%'
        self.options.prefix = special
        installer.install(SOURCE, self.options)
        subprocess.run(["desktop-file-validate", str(self.shortcut())], check=True)


class RuntimeTests(unittest.TestCase):
    def test_available_host_qt_does_not_download_packages(self):
        with patch.object(installer, "qt_version", return_value="6.11.2"), patch.object(installer, "command") as run:
            installer.create_runtime(Path("/tmp/test-runtime"))
        arguments = run.call_args[0][0]
        self.assertIn("--without-pip", arguments)
        self.assertIn("--system-site-packages", arguments)
        self.assertEqual(run.call_count, 1)

    def test_missing_qt_is_installed_only_into_the_app_environment(self):
        with patch.object(installer, "qt_version", side_effect=[None, None, "6.11.2"]), patch.object(installer, "command") as run, patch.object(installer.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            installer.create_runtime(Path("/tmp/test-runtime"))
        arguments = run.call_args[0][0]
        self.assertEqual(arguments[0], Path("/tmp/test-runtime/.venv/bin/python"))
        self.assertEqual(arguments[1:4], ["-m", "pip", "install"])


if __name__ == "__main__":
    unittest.main()
