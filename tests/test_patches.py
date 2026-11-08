# SPDX-License-Identifier: MIT
import subprocess
import sys
import unittest

from asus_acpi import patches
from tests.fixtures import i2c_source, thermal_source, usb_source


class PatchTests(unittest.TestCase):
    def test_usb_guards_all_methods(self):
        result = patches.patch_usb(usb_source())
        self.assertEqual(result.count("If (!CondRefOf ("), 48)
        self.assertEqual(result.count("Method (_UPC,"), 24)
        self.assertEqual(result.count("Method (_PLD,"), 24)
        self.assertIn('"xh_rplsb", 0x00000001)', result)

    def test_usb_rejects_incomplete_layout(self):
        with self.assertRaisesRegex(ValueError, "48 USB"):
            patches.patch_usb(usb_source().replace("Method (_UPC,", "Method (BAD0,", 1))

    def test_usb_cannot_apply_twice(self):
        with self.assertRaises(ValueError):
            patches.patch_usb(patches.patch_usb(usb_source()))

    def test_i2c_only_two_optional_scopes(self):
        result = patches.patch_i2c(i2c_source())
        self.assertEqual(result.count("If (CondRefOf ("), 2)
        for number in (1, 3):
            self.assertIn(f"\\_SB.PC00.I2C{number}.TPL1", result)
        self.assertEqual(result.count("Name (FLAG, One)"), 2)
        self.assertIn('"RplSBTbt", 0x00001001)', result)

    def test_i2c_rejects_changed_scope(self):
        with self.assertRaises(ValueError):
            patches.patch_i2c(i2c_source().replace("I2C3", "I2C2"))

    def test_resolve_verified_field_not_guessed_method(self):
        guessed = "External (PSON, MethodObj)    // Warning: Unknown method, guessing 1 arguments"
        self.assertIn("External (PSON, FieldUnitObj)", patches.resolve_i2c(guessed))
        with self.assertRaises(ValueError):
            patches.resolve_i2c("External (PSON, IntObj)")

    def test_thermal_preserves_original_full_policy(self):
        source = thermal_source()
        old, new = patches.thermal_blocks(source)
        result = patches.patch_thermal(source)
        self.assertIn("Method (PSVT, 0, NotSerialized)", result)
        self.assertIn("If (CondRefOf (\\_SB.PC00.LPCB.SEN2))", result)
        self.assertIn("Return (Package (0x03)", result)
        self.assertIn("Return (Package (0x04)", result)
        self.assertIn('"DptfTabl", 0x00001001)', result)
        self.assertEqual(old.count("0x0C6E"), 3)
        self.assertEqual(new.count("0x0C6E"), 5)  # Full 3 rows + fallback 2, same limits.
        self.assertNotIn("Device (SEN2)", result)

    def test_thermal_rejects_wrong_relationship(self):
        with self.assertRaises(ValueError):
            patches.patch_thermal(thermal_source().replace("LPCB.SEN2", "LPCB.SEN4"))

    def test_thermal_cannot_apply_twice(self):
        with self.assertRaises(ValueError):
            patches.patch_thermal(patches.patch_thermal(thermal_source()))

    def test_block_parser_ignores_comments_and_strings(self):
        text = 'Scope (FOO) { Name (STR0, "}") /* } */ // }\n Name (N, One) } trailing'
        self.assertEqual(text[patches.block_end(text, 0) :], " trailing")
        with self.assertRaises(ValueError):
            patches.block_end("Scope (FOO) {", 0)

    def test_guards_remain_enabled_with_optimized_python(self):
        result = subprocess.run(
            [sys.executable, "-O", "-c", "from asus_acpi import require; require(False, 'guard')"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ValueError: guard", result.stderr)
