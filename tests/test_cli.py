# SPDX-License-Identifier: MIT
import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asus_acpi import cli, profile, uki
from tests.fixtures import acpi_table, initramfs, pe


class CLITests(unittest.TestCase):
    def test_help_has_no_side_effects(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["asus-acpi", "--help"]),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as result,
        ):
            cli.main()
        self.assertEqual(result.exception.code, 0)
        self.assertIn("prepare", output.getvalue())
        self.assertIn("verify", output.getvalue())

    def test_inspect_only_reports_machine(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["asus-acpi", "inspect"]),
            patch.object(cli, "machine", return_value={"kernel": "test"}),
            contextlib.redirect_stdout(output),
        ):
            cli.main()
        self.assertEqual(json.loads(output.getvalue()), {"kernel": "test"})

    def test_failed_tool_keeps_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "tool.log"
            result = subprocess.CompletedProcess(["tool"], 1, "stdout", "stderr")
            with (
                patch.object(cli.subprocess, "run", return_value=result),
                self.assertRaisesRegex(ValueError, "Command failed"),
            ):
                cli.run(["tool"], log)
            self.assertEqual(log.read_text(), "stdoutstderr")

    def test_output_symlink_cannot_bypass_boot_directory_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "boot-link"
            link.symlink_to("/boot", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "live system"):
                cli.output_path(link / "new")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.prepared = root / "prepared"
        self.build = root / "build"
        (self.prepared / "aml").mkdir(parents=True)
        self.build.mkdir()
        tables = {}
        for name, (oem, table, revision, _) in profile.TARGETS.items():
            tables[f"{name}.aml"] = acpi_table(oem=oem, name=table, revision=revision + 1)
        for name, data in tables.items():
            (self.prepared / "aml" / name).write_bytes(data)
        manifest = {
            "format": 1,
            "kernel": profile.TESTED_KERNEL,
            "aml_sha256": {name: profile.digest(data) for name, data in tables.items()},
        }
        (self.prepared / "manifest.json").write_text(json.dumps(manifest))
        self.archive, main_hash = uki.inject_tables(initramfs(), tables)
        self.base = pe(initramfs())
        (self.build / "base.unsigned.efi").write_bytes(self.base)
        (self.build / "asus-acpi-test.unsigned.efi").write_bytes(pe(self.archive))
        (self.build / "initramfs.img").write_bytes(self.archive)
        (self.build / "build.json").write_text(
            json.dumps(
                {
                    "unsigned_base_sha256": profile.digest(self.base),
                    "main_initramfs_sha256": main_hash,
                }
            )
        )
        self.args = argparse.Namespace(prepared=self.prepared, build=self.build, image=None)

    def test_verify_valid_artifacts(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.verify(self.args)
        self.assertIn("PASS:", output.getvalue())
        self.assertIn("certificate present: False", output.getvalue())

    def test_archive_tampering_rejected(self):
        (self.build / "initramfs.img").write_bytes(self.archive + b"tamper")
        with self.assertRaises(ValueError):
            cli.verify(self.args)

    def test_mutating_saved_base_and_candidate_does_not_hide_change(self):
        for name in ("base.unsigned.efi", "asus-acpi-test.unsigned.efi"):
            path = self.build / name
            path.write_bytes(path.read_bytes().replace(b"test-kernel", b"evil-kernel", 1))
        with self.assertRaisesRegex(ValueError, "Saved base"):
            cli.verify(self.args)

    def test_certificate_presence_is_not_claimed_as_signature_verification(self):
        image = self.build / "signed.efi"
        image.write_bytes(pe(self.archive, signed=True))
        self.args.image = image
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.verify(self.args)
        self.assertIn("certificate present: True", output.getvalue())
        self.assertIn("NOT cryptographic", output.getvalue())
