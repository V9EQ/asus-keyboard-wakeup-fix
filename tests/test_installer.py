# SPDX-License-Identifier: MIT
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asus_acpi import installer, permanent, profile, uki
from tests.fixtures import acpi_table, initramfs, pe


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.info = dict(
            vendor=profile.VENDOR,
            model=profile.MODEL,
            bios=profile.BIOS,
            kernel=profile.TESTED_KERNEL,
            lockdown="none",
        )
        self.tables = {
            f"{key}.aml": acpi_table(oem=oem, name=name, revision=revision + 1)
            for key, (oem, name, revision, _) in profile.TARGETS.items()
        }
        self.calls = []
        self.tool = installer.Installer(self.root, self.run_command)
        self.addCleanup(patch.stopall)
        patch.object(installer, "check_machine", return_value=self.info).start()
        patch.object(installer.os, "geteuid", return_value=0).start()
        for name in (
            "/usr/bin/mkinitcpio",
            "/usr/bin/sbctl",
            "/usr/bin/sbverify",
            "/usr/bin/flock",
            installer.CERT,
            "/boot/vmlinuz-linux",
            "/etc/mkinitcpio.conf",
            "/usr/lib/initcpio/install/acpi_override",
            "/boot/recovery.efi",
            "/boot/EFI/BOOT/limine.conf",
            "/var/lib/sbctl/files.json",
        ):
            self.put(name, b"original")
        self.put(
            "/etc/mkinitcpio.d/linux.preset",
            (
                "ALL_kver=\"/boot/vmlinuz-linux\"\nPRESETS=('default')\n"
                f'default_uki="{installer.PRIMARY}"\n'
            ).encode(),
        )
        self.put(installer.PRIMARY, pe(initramfs(), signed=True))
        (self.root / "run").mkdir()
        (self.root / "var/lib/pacman").mkdir()

    def put(self, name, data):
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_command(self, command):
        self.calls.append(command)
        if command[0].endswith("mkinitcpio"):
            tables = {
                p.name: p.read_bytes()
                for p in self.tool.path("/etc/initcpio/acpi_override").glob("*.aml")
            }
            archive = uki.inject_tables(initramfs(), tables)[0] if tables else initramfs()
            self.put(installer.PRIMARY, pe(archive, signed=True))

    def tree(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def test_dry_run_is_read_only_even_for_non_root(self):
        before = self.tree()
        with patch.object(installer.os, "geteuid", return_value=1000):
            report = self.tool.install(self.tables, self.info)
        self.assertFalse(report["applied"])
        self.assertEqual(before, self.tree())
        self.assertFalse(self.calls)

    def test_apply_requires_root_before_changes(self):
        before = self.tree()
        with patch.object(installer.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "requires root"):
                self.tool.install(self.tables, self.info, apply=True)
        self.assertEqual(before, self.tree())

    def test_install_and_uninstall_rebuild_current_kernel(self):
        self.assertTrue(self.tool.install(self.tables, self.info, apply=True)["applied"])
        self.assertEqual(len(self.calls), 3)
        self.assertIn("sbverify", self.calls[-1][0])
        self.assertEqual(self.tool.state.stat().st_mode & 0o777, 0o700)
        installed = self.tree()
        self.assertFalse(self.tool.uninstall()["applied"])
        self.assertEqual(installed, self.tree())
        self.put(installer.PRIMARY, b"newer kernel image, not the install-time image")
        self.assertTrue(self.tool.uninstall(apply=True)["applied"])
        self.assertEqual(len(self.calls), 6)
        self.assertFalse(self.tool.path("/etc/initcpio/acpi_override/usbguard.aml").exists())
        self.assertEqual(self.tool.path("/boot/recovery.efi").read_bytes(), b"original")
        state = json.loads((self.tool.state / "transaction.json").read_text())
        self.assertEqual(state["status"], "uninstalled")

    def test_conflict_never_overwritten_even_with_adopt(self):
        path = self.put("/etc/initcpio/acpi_override/usbguard.aml", b"manual")
        for adopt in (False, True):
            with self.assertRaisesRegex(ValueError, "Conflict"):
                self.tool.install(self.tables, self.info, apply=True, adopt=adopt)
        self.assertEqual(path.read_bytes(), b"manual")
        self.assertFalse(self.tool.state.exists())

    def test_identical_adoption_is_explicit_and_preserved_on_uninstall(self):
        path = self.put("/etc/initcpio/acpi_override/usbguard.aml", self.tables["usbguard.aml"])
        with self.assertRaisesRegex(ValueError, "Conflict"):
            self.tool.install(self.tables, self.info)
        report = self.tool.install(self.tables, self.info, apply=True, adopt=True)
        self.assertIn("/etc/initcpio/acpi_override/usbguard.aml", report["adopted"])
        self.tool.uninstall(apply=True)
        self.assertEqual(path.read_bytes(), self.tables["usbguard.aml"])

    def test_unrelated_aml_and_vendor_payload_rejected(self):
        for name in (
            "/etc/initcpio/acpi_override/other.aml",
            "/usr/lib/initcpio/acpi_override/vendor.aml",
        ):
            path = self.put(name, b"other")
            with self.assertRaisesRegex(ValueError, "ACPI"):
                self.tool.install(self.tables, self.info)
            path.unlink()

    def test_custom_config_preset_rejected_without_execution(self):
        path = self.tool.path("/etc/mkinitcpio.d/linux.preset")
        path.write_text(path.read_text() + 'ALL_config="/etc/mkinitcpio.conf"\n')
        with self.assertRaisesRegex(ValueError, "preset"):
            self.tool.install(self.tables, self.info)
        self.assertFalse(self.calls)

    def test_symlink_destination_and_parent_rejected(self):
        for parent in (False, True):
            with self.subTest(parent=parent):
                path = self.tool.path("/etc/mkinitcpio.conf.d")
                if parent:
                    path.symlink_to(self.root / "elsewhere", target_is_directory=True)
                else:
                    path.mkdir()
                    (path / "90-asus-acpi.conf").symlink_to(self.root / "missing")
                with self.assertRaisesRegex(ValueError, "Symlink"):
                    self.tool.install(self.tables, self.info)
                if parent:
                    path.unlink()
                else:
                    (path / "90-asus-acpi.conf").unlink()
                    path.rmdir()

    def test_each_external_command_failure_restores_files_and_boot(self):
        for fail_at in (1, 2, 3):
            with self.subTest(fail_at=fail_at):
                before = self.tree()
                count = 0

                def fail(command, fail_at=fail_at):
                    nonlocal count
                    count += 1
                    self.run_command(command)
                    if count == fail_at:
                        raise subprocess.CalledProcessError(1, command)

                self.tool.runner = fail
                with self.assertRaises(subprocess.CalledProcessError):
                    self.tool.install(self.tables, self.info, apply=True)
                for name, payload in before.items():
                    self.assertEqual((self.root / name).read_bytes(), payload)
                self.assertFalse(
                    self.tool.path("/etc/mkinitcpio.conf.d/90-asus-acpi.conf").exists()
                )
                self.assertTrue((self.tool.state / "failed").exists())
                # Independent failure scenario; production never deletes backups.
                import shutil

                shutil.rmtree(self.tool.state)

    def test_payload_verification_failure_rolls_back(self):
        before = self.tool.path(installer.PRIMARY).read_bytes()
        self.tool.runner = lambda command: None  # success codes but stale artifact
        with self.assertRaisesRegex(ValueError, "FIRST early"):
            self.tool.install(self.tables, self.info, apply=True)
        self.assertEqual(self.tool.path(installer.PRIMARY).read_bytes(), before)

    def test_uninstall_failure_restores_current_not_stale_boot(self):
        self.tool.install(self.tables, self.info, apply=True)
        self.put(installer.PRIMARY, b"current kernel snapshot")
        before = self.tree()

        def fail(command):
            self.put(installer.PRIMARY, b"partially rebuilt")
            raise subprocess.CalledProcessError(1, command)

        self.tool.runner = fail
        with self.assertRaises(subprocess.CalledProcessError):
            self.tool.uninstall(apply=True)
        for name, payload in before.items():
            self.assertEqual((self.root / name).read_bytes(), payload)

    def test_failed_uninstall_can_be_retried_after_fixing_failure(self):
        self.tool.install(self.tables, self.info, apply=True)

        def fail(command):
            raise subprocess.CalledProcessError(1, command)

        self.tool.runner = fail
        with self.assertRaises(subprocess.CalledProcessError):
            self.tool.uninstall(apply=True)
        self.tool.runner = self.run_command
        self.assertTrue(self.tool.uninstall(apply=True)["applied"])

    def test_unrelated_boot_modification_is_detected_and_restored(self):
        def unexpected(command):
            self.run_command(command)
            self.put("/boot/recovery.efi", b"unexpected modification")

        self.tool.runner = unexpected
        with self.assertRaisesRegex(ValueError, "unrelated file"):
            self.tool.install(self.tables, self.info, apply=True)
        self.assertEqual(self.tool.path("/boot/recovery.efi").read_bytes(), b"original")

    def test_changed_managed_file_blocks_uninstall(self):
        self.tool.install(self.tables, self.info, apply=True)
        self.put("/etc/initcpio/acpi_override/usbguard.aml", b"user modification")
        with self.assertRaisesRegex(ValueError, "Managed file changed"):
            self.tool.uninstall(apply=True)

    def test_uninstall_refuses_new_unreviewed_aml(self):
        self.tool.install(self.tables, self.info, apply=True)
        self.put("/etc/initcpio/acpi_override/new.aml", b"unreviewed")
        with self.assertRaisesRegex(ValueError, "Unrelated ACPI payload"):
            self.tool.uninstall(apply=True)

    def test_existing_state_blocks_reinstall(self):
        self.tool.install(self.tables, self.info, apply=True)
        with self.assertRaisesRegex(ValueError, "Existing installer state"):
            self.tool.install(self.tables, self.info, apply=True)

    def test_corrupt_backup_rejected_before_restoration(self):
        directory = self.root / "backup"
        records = installer.snapshot(self.root, {installer.PRIMARY}, directory)
        (directory / records[installer.PRIMARY]["backup"]).write_bytes(b"corrupt")
        self.put(installer.PRIMARY, b"current")
        with self.assertRaisesRegex(ValueError, "Corrupt backup"):
            installer.restore(self.root, directory, records)
        self.assertEqual(self.tool.path(installer.PRIMARY).read_bytes(), b"current")

    def test_package_lock_is_not_stolen(self):
        lock = self.put("/var/lib/pacman/db.lck", b"pacman owns this")
        with self.assertRaisesRegex(ValueError, "Package transaction"):
            self.tool.install(self.tables, self.info)
        with self.assertRaises(FileExistsError):
            self.tool.install(self.tables, self.info, apply=True)
        self.assertEqual(lock.read_bytes(), b"pacman owns this")

    def test_partial_write_failure_restores_previous_payloads(self):
        original = installer.atomic_write
        target = self.tool.path("/etc/mkinitcpio.conf.d/90-asus-acpi.conf")
        primary = self.tool.path(installer.PRIMARY).read_bytes()

        def fail(path, data, mode):
            if path == target:
                raise OSError("Injected write failure")
            original(path, data, mode)

        with patch.object(installer, "atomic_write", side_effect=fail):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.tool.install(self.tables, self.info, apply=True)
        self.assertFalse(self.tool.path("/etc/initcpio/acpi_override/usbguard.aml").exists())
        self.assertEqual(self.tool.path(installer.PRIMARY).read_bytes(), primary)
        self.assertFalse(self.tool.path("/var/lib/pacman/db.lck").exists())

    def test_no_unreviewed_plan_is_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "export"
            permanent.export_bundle(bundle, self.tables, self.info)
            self.assertFalse((bundle / "install.sh").exists())
