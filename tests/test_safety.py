# SPDX-License-Identifier: MIT
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asus_acpi import cli, profile
from tests.fixtures import acpi_table


class PlatformTests(unittest.TestCase):
    def supported(self):
        return {
            "vendor": profile.VENDOR,
            "model": profile.MODEL,
            "bios": profile.BIOS,
            "kernel": profile.TESTED_KERNEL,
            "lockdown": "none",
        }

    def test_supported_machine(self):
        info = self.supported()
        with (
            patch.object(profile, "machine", return_value=info),
            patch.object(profile, "check_kernel") as capabilities,
        ):
            self.assertEqual(profile.check_machine(build=True), info)
        capabilities.assert_called_once_with(profile.TESTED_KERNEL)

    def test_reject_untested_platform(self):
        for key in ("vendor", "model", "bios"):
            info = self.supported() | {key: "different"}
            with (
                self.subTest(key=key),
                patch.object(profile, "machine", return_value=info),
                self.assertRaises(ValueError),
            ):
                profile.check_machine()

    def test_new_kernel_is_checked_by_capabilities_not_version(self):
        info = self.supported() | {"kernel": "9.0-future"}
        with (
            patch.object(profile, "machine", return_value=info),
            patch.object(profile, "check_kernel") as capabilities,
        ):
            self.assertEqual(profile.check_machine(build=True), info)
        capabilities.assert_called_once_with("9.0-future")

    def test_candidate_kernel_is_checked_instead_of_running_kernel(self):
        info = self.supported()
        with (
            patch.object(profile, "machine", return_value=info),
            patch.object(profile, "check_kernel") as capabilities,
        ):
            profile.check_machine(build=True, kernel="9.0-candidate")
        capabilities.assert_called_once_with("9.0-candidate")

    def test_lockdown_not_silently_disabled(self):
        for mode in ("integrity", "confidentiality", "unknown"):
            info = self.supported() | {"lockdown": mode}
            with self.subTest(mode=mode), patch.object(profile, "machine", return_value=info):
                self.assertEqual(profile.check_machine(), info)  # Analysis is still allowed.
                with self.assertRaisesRegex(ValueError, "lockdown"):
                    profile.check_machine(build=True)

    def test_live_output_directories_forbidden(self):
        for path in ("/boot/new", "/efi/new", "/etc/new", "/sys/new", "/usr/new"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "live system"):
                cli.output_path(path)

    def test_existing_workspace_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "NEW directory"):
                cli.output_path(directory)
            output = Path(directory) / "new"
            self.assertEqual(cli.output_path(output), output)
            self.assertFalse(output.exists())

    def test_original_table_fingerprints_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "DSDT").write_bytes(acpi_table(signature=b"DSDT", name=b"TESTDSDT"))
            for key, (oem, name, revision, _) in profile.TARGETS.items():
                (path / key).write_bytes(acpi_table(oem=oem, name=name, revision=revision))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                profile.load_tables(path)

    def test_existing_wake_overlay_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "wake").write_bytes(acpi_table())
            with self.assertRaisesRegex(ValueError, "wake overlay"):
                profile.load_tables(directory)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / "aml").mkdir()
        self.manifest = {"format": 1, "kernel": profile.TESTED_KERNEL, "aml_sha256": {}}
        for key, (oem, name, revision, _) in profile.TARGETS.items():
            filename = f"{key}.aml"
            data = acpi_table(oem=oem, name=name, revision=revision + 1)
            (self.directory / "aml" / filename).write_bytes(data)
            self.manifest["aml_sha256"][filename] = profile.digest(data)
        self.save()

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def test_three_table_profile(self):
        self.assertEqual(len(cli.prepared_tables(self.directory)), 3)

    def test_four_table_profile(self):
        data = acpi_table()
        (self.directory / "aml/wake14.aml").write_bytes(data)
        self.manifest["aml_sha256"]["wake14.aml"] = profile.digest(data)
        self.save()
        self.assertEqual(len(cli.prepared_tables(self.directory)), 4)

    def test_manifest_path_traversal_rejected(self):
        self.manifest["aml_sha256"]["../../outside.aml"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "filenames"):
            cli.prepared_tables(self.directory)

    def test_modified_payload_rejected(self):
        path = self.directory / "aml/usbguard.aml"
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            cli.prepared_tables(self.directory)

    def test_header_checked_even_if_hash_was_updated(self):
        data = acpi_table()
        (self.directory / "aml/usbguard.aml").write_bytes(data)
        self.manifest["aml_sha256"]["usbguard.aml"] = profile.digest(data)
        self.save()
        with self.assertRaisesRegex(ValueError, "header"):
            cli.prepared_tables(self.directory)

    def test_preparation_kernel_is_informational_not_an_allowlist(self):
        self.manifest["kernel"] = "future-linux"
        self.save()
        self.assertEqual(len(cli.prepared_tables(self.directory)), 3)

    def test_missing_kernel_metadata_rejected(self):
        self.manifest["kernel"] = ""
        self.save()
        with self.assertRaisesRegex(ValueError, "manifest"):
            cli.prepared_tables(self.directory)
