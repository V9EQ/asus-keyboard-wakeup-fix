# SPDX-License-Identifier: MIT
import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asus_acpi import profile

CAPABLE_CONFIG = """CONFIG_X86_64=y
CONFIG_BLK_DEV_INITRD=y
CONFIG_ACPI_TABLE_UPGRADE=y
"""


class KernelCapabilitiesTests(unittest.TestCase):
    def config(self, text, compressed=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / ("config.gz" if compressed else ".config")
        if compressed:
            with gzip.open(path, "wt") as stream:
                stream.write(text)
        else:
            path.write_text(text)
        return path

    def check(self, path, kernel="9.0-new-kernel"):
        with patch.object(profile, "kernel_config_paths", return_value=[path]):
            return profile.check_kernel(kernel)

    def test_new_version_with_required_capabilities_accepted(self):
        result = self.check(self.config(CAPABLE_CONFIG))
        self.assertEqual(result["kernel"], "9.0-new-kernel")
        self.assertFalse(result["previously_tested"])

    def test_gzipped_running_config_supported(self):
        self.assertEqual(
            self.check(self.config(CAPABLE_CONFIG, compressed=True))["kernel"],
            "9.0-new-kernel",
        )

    def test_each_required_feature_checked(self):
        for name in ("CONFIG_X86_64", "CONFIG_BLK_DEV_INITRD", "CONFIG_ACPI_TABLE_UPGRADE"):
            text = CAPABLE_CONFIG.replace(f"{name}=y", f"# {name} is not set")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                self.check(self.config(text))

    def test_incompatible_policies_rejected(self):
        for name in (
            "CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY",
            "CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY",
            "CONFIG_ACPI_TABLE_OVERRIDE_VIA_BUILTIN_INITRD",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                self.check(self.config(CAPABLE_CONFIG + f"{name}=y\n"))

    def test_no_config_is_not_assumed_compatible(self):
        with self.assertRaisesRegex(ValueError, "Cannot verify"):
            self.check(Path("/nonexistent/asus-acpi-test-config"))

    def test_empty_config_rejected(self):
        with self.assertRaisesRegex(ValueError, "Empty"):
            self.check(self.config(""))

    def test_running_config_not_used_for_other_kernel(self):
        with patch.object(profile.platform, "release", return_value="running-kernel"):
            self.assertNotIn(Path("/proc/config.gz"), profile.kernel_config_paths("candidate"))
            self.assertIn(Path("/proc/config.gz"), profile.kernel_config_paths("running-kernel"))

    def test_kernel_path_traversal_rejected(self):
        for kernel in ("../../etc", ".", "..", "", "bad/kernel", "name\0"):
            with self.subTest(kernel=kernel), self.assertRaises(ValueError):
                profile.kernel_config_paths(kernel)
