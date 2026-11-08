# SPDX-License-Identifier: MIT
"""Userspace ACPICA tests. They never load AML into the real kernel."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from asus_acpi import patches
from asus_acpi.cli import DATA
from asus_acpi.profile import table_header
from tests import fixtures


@unittest.skipUnless(shutil.which("iasl") and shutil.which("acpiexec"), "ACPICA required")
class AMLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        sources = {
            "usb-original": fixtures.usb_source(),
            "usb-guarded": patches.patch_usb(fixtures.usb_source()),
            "usb-existing": fixtures.usb_fixture(),
            "usb-missing": fixtures.usb_fixture(missing=True),
            "i2c-original": fixtures.i2c_source(),
            "i2c-guarded": patches.patch_i2c(fixtures.i2c_source()),
            "thermal-original": fixtures.thermal_source(),
            "thermal-guarded": patches.patch_thermal(fixtures.thermal_source()),
            "wake14": (DATA / "wake14.dsl").read_text(),
            "lid": """DefinitionBlock ("", "DSDT", 2, "TEST", "LID", 1)
{
    Scope (\\_SB) {
        Device (PC00) {
            Name (_HID, "PNP0A08")
            Device (LPCB) {
                Name (_ADR, Zero)
                Device (LID0) {
                    Name (_HID, "PNP0C0D")
                    Method (_LID, 0) { Return (One) }
                }
            }
        }
    }
}
""",
        }
        for present in (False, True):
            sources[f"i2c-fixture-{int(present)}"] = fixtures.i2c_fixture(present)
            sources[f"thermal-fixture-{int(present)}"] = fixtures.thermal_fixture(present)
        for name, source in sources.items():
            path = cls.directory / f"{name}.dsl"
            path.write_text(source)
            result = subprocess.run(
                ["iasl", "-we", str(path)], capture_output=True, text=True, check=False
            )
            if result.returncode:
                raise AssertionError(f"{name}: {result.stdout}{result.stderr}")

    def emulate(self, fixture, table=None, paths=()):
        command = "; ".join(f"evaluate {path}" for path in paths) + "; quit"
        self.assertLess(len(command), 1023)
        files = [str(self.directory / f"{fixture}.aml")]
        if table:
            files.append(str(self.directory / f"{table}.aml"))
        result = subprocess.run(
            ["acpiexec", "-di", "-l", "-b", command, *files],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def clean(self, output):
        self.assertNotRegex(output, r"AE_|ACPI Error|Exception|failed")

    def evaluation(self, output, path):
        marker = f"Evaluating {path}\n"
        self.assertIn(marker, output)
        data = output.split(marker, 1)[1].split("\nEvaluating ", 1)[0]
        data = data.split("ACPI: No outstanding", 1)[0].strip()
        return re.sub(r"0x[0-9a-fA-F]{8,}", "ADDRESS", data)

    def test_usb_reproduces_duplicates_and_removes_them(self):
        self.assertIn("AE_ALREADY_EXISTS", self.emulate("usb-existing", "usb-original"))
        self.clean(self.emulate("usb-existing", "usb-guarded"))

    def test_all_48_existing_usb_methods_keep_results(self):
        paths = [
            f"\\_SB.PC00.XHCI.RHUB.{port}.{method}"
            for port in fixtures.ports()
            for method in ("_UPC", "_PLD")
        ]
        for index in range(0, len(paths), 12):
            batch = paths[index : index + 12]
            before = self.emulate("usb-existing", paths=batch)
            after = self.emulate("usb-existing", "usb-guarded", paths=batch)
            self.clean(before)
            self.clean(after)
            for path in batch:
                self.assertEqual(self.evaluation(before, path), self.evaluation(after, path))

    def test_usb_creates_individually_missing_methods(self):
        paths = (r"\_SB.PC00.XHCI.RHUB.HS02._UPC", r"\_SB.PC00.XHCI.RHUB.HS06._PLD")
        output = self.emulate("usb-missing", "usb-guarded", paths)
        self.clean(output)
        for path in paths:
            self.assertIn("returned object", self.evaluation(output, path))

    def test_absent_i2c_scopes(self):
        self.assertIn("AE_NOT_FOUND", self.emulate("i2c-fixture-0", "i2c-original"))
        self.clean(self.emulate("i2c-fixture-0", "i2c-guarded"))

    def test_present_i2c_scopes_unchanged(self):
        paths = tuple(f"\\_SB.PC00.I2C{n}.TPL1.FLAG" for n in (1, 3))
        before = self.emulate("i2c-fixture-1", "i2c-original", paths)
        after = self.emulate("i2c-fixture-1", "i2c-guarded", paths)
        self.clean(before)
        self.clean(after)
        for path in paths:
            self.assertEqual(self.evaluation(before, path), self.evaluation(after, path))

    def test_absent_sensor_uses_real_remaining_relationships(self):
        paths = (r"\_SB.IETM.PSVT",)
        self.assertIn("AE_NOT_FOUND", self.emulate("thermal-fixture-0", "thermal-original", paths))
        output = self.emulate("thermal-fixture-0", "thermal-guarded", paths)
        self.clean(output)
        value = self.evaluation(output, paths[0])
        self.assertIn("[Package] Contains 3 Elements:", value)
        self.assertIn("Name TCPU Device", value)
        self.assertIn("Name SEN3 Device", value)
        self.assertNotIn("Name SEN2 Device", value)
        self.assertIn("[Integer] = 0000000000000002", value)

    def test_present_sensor_full_policy_unchanged(self):
        paths = (r"\_SB.IETM.PSVT",)
        before = self.emulate("thermal-fixture-1", "thermal-original", paths)
        after = self.emulate("thermal-fixture-1", "thermal-guarded", paths)
        self.clean(before)
        self.clean(after)
        self.assertEqual(self.evaluation(before, paths[0]), self.evaluation(after, paths[0]))

    def test_wake_overlay_only_declares_gpe14_s0(self):
        data = (self.directory / "wake14.aml").read_bytes()
        self.assertEqual(table_header(data), (b"SSDT", b"LOCAL\0", b"FX608WAK", 1))
        path = r"\_SB.PC00.LPCB.LID0._PRW"
        output = self.emulate("lid", "wake14", (path,))
        self.clean(output)
        value = self.evaluation(output, path)
        self.assertIn("[Package] Contains 2 Elements:", value)
        self.assertIn("[Integer] = 0000000000000014", value)
        self.assertIn("[Integer] = 0000000000000000", value)
