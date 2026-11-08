# SPDX-License-Identifier: MIT
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from asus_acpi import permanent, profile
from tests.fixtures import acpi_table
from tests.test_kernel import CAPABLE_CONFIG


class DropinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.sys = root / "sys"
        self.dmi = self.sys / "class/dmi/id"
        self.dmi.mkdir(parents=True)
        for name, value in (
            ("sys_vendor", profile.VENDOR),
            ("product_name", profile.MODEL),
            ("bios_version", profile.BIOS),
        ):
            (self.dmi / name).write_text(value)
        security = self.sys / "kernel/security"
        security.mkdir(parents=True)
        self.lockdown = security / "lockdown"
        self.lockdown.write_text("[none] integrity confidentiality\n")
        modules = root / "modules"
        self.config = modules / "9.0-test/config"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(CAPABLE_CONFIG)
        self.payload = root / "payload"
        self.payload.write_bytes(b"test AML")
        checksum = root / "aml.sha256"
        checksum.write_text(
            f"{hashlib.sha256(self.payload.read_bytes()).hexdigest()}  {self.payload}\n"
        )
        self.script = root / "guard.conf"
        self.script.write_text(
            permanent.render_dropin(
                sys_root=self.sys,
                modules_root=modules,
                boot_root=root / "boot",
                proc_config=root / "unavailable-proc-config.gz",
                checksum_file=checksum,
            )
        )

    def run_guard(self, twice=False):
        command = 'set -euo pipefail; KERNELVERSION=9.0-test; HOOKS=(base udev); source "$1"; '
        if twice:
            command += 'source "$1"; '
        command += "declare -p HOOKS"
        return subprocess.run(
            ["bash", "-c", command, "test", str(self.script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_new_kernel_allowed_and_hooks_preserved(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"base"', result.stdout)
        self.assertIn('"udev"', result.stdout)
        self.assertEqual(result.stdout.count('"acpi_override"'), 1)

    def test_dropin_is_idempotent(self):
        result = self.run_guard(twice=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('"acpi_override"'), 1)

    def test_bios_change_stops_before_rebuild(self):
        (self.dmi / "bios_version").write_text("FX608JPR.999")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BIOS changed", result.stderr)

    def test_different_model_rejected(self):
        (self.dmi / "product_name").write_text("Other ASUS")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported laptop", result.stderr)

    def test_tampered_payload_rejected(self):
        self.payload.write_bytes(b"modified")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)

    def test_lockdown_not_disabled(self):
        self.lockdown.write_text("none [integrity] confidentiality")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Lockdown blocks", result.stderr)
        self.assertEqual(self.lockdown.read_text(), "none [integrity] confidentiality")

    def test_missing_headers_rejected(self):
        self.config.unlink()
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("matching kernel headers", result.stderr)

    def test_missing_kernel_feature_rejected(self):
        self.config.write_text(CAPABLE_CONFIG.replace("CONFIG_ACPI_TABLE_UPGRADE=y", ""))
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_ACPI_TABLE_UPGRADE", result.stderr)

    def test_forced_lockdown_kernel_rejected(self):
        self.config.write_text(CAPABLE_CONFIG + "CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y\n")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incompatible", result.stderr)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            f"{key}.aml": acpi_table(oem=oem, name=name, revision=revision + 1)
            for key, (oem, name, revision, _) in profile.TARGETS.items()
        }
        self.info = {
            "vendor": profile.VENDOR,
            "model": profile.MODEL,
            "bios": profile.BIOS,
            "kernel": "9.0-future",
            "lockdown": "none",
        }
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "export"

    def test_export_has_reviewable_files_and_no_installer(self):
        self.tables["wake14.aml"] = acpi_table()
        plan = permanent.export_bundle(self.output, self.tables, self.info)
        self.assertTrue(plan["export_only"])
        self.assertEqual(len(plan["files"]), 8)
        saved = json.loads((self.output / "install-plan.json").read_text())
        self.assertEqual(saved, plan)
        for name, metadata in plan["files"].items():
            path = self.output / "root" / name.removeprefix("/")
            self.assertEqual(profile.digest(path.read_bytes()), metadata["sha256"])
            self.assertEqual(oct(path.stat().st_mode & 0o777), metadata["mode"])
        helper = self.output / "root/usr/local/libexec/asus-acpi-rebuild"
        self.assertNotIn("python", helper.read_text().lower())
        self.assertNotIn("uv run", helper.read_text())
        self.assertFalse((self.output / "install.sh").exists())
        self.assertIn("NOT AN AUTOMATIC INSTALLER", (self.output / "INSTALL.txt").read_text())
        for path in (helper, self.output / "root/etc/mkinitcpio.conf.d/90-asus-acpi.conf"):
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_three_payload_profile_supported(self):
        plan = permanent.export_bundle(self.output, self.tables, self.info)
        self.assertEqual(len(plan["files"]), 7)
        self.assertFalse((self.output / "root/etc/initcpio/acpi_override/wake14.aml").exists())

    def test_existing_export_not_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            permanent.export_bundle(self.output, self.tables, self.info)

    def test_unsupported_bios_no_files_written(self):
        with self.assertRaises(ValueError):
            permanent.export_bundle(self.output, self.tables, self.info | {"bios": "other"})
        self.assertFalse(self.output.exists())

    def test_invalid_payload_no_files_written(self):
        self.tables["usbguard.aml"] += b"corrupt"
        with self.assertRaises(ValueError):
            permanent.export_bundle(self.output, self.tables, self.info)
        self.assertFalse(self.output.exists())
