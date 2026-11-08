# SPDX-License-Identifier: MIT
import struct
import tempfile
import unittest
from pathlib import Path

from asus_acpi import uki
from asus_acpi.profile import digest, table_header
from tests.fixtures import acpi_table, initramfs, pe


class ArchiveTests(unittest.TestCase):
    def test_inject_first_archive_and_preserve_microcode_and_main(self):
        original = initramfs()
        before, _, end, _ = uki.cpio_archive(original)
        result, main_hash = uki.inject_tables(original, {"wake14.aml": acpi_table()})
        after, _, new_end, _ = uki.cpio_archive(result)
        for name, payload in before.items():
            self.assertEqual(after[name], payload)
        self.assertEqual(after["kernel/firmware/acpi/wake14.aml"], acpi_table())
        self.assertEqual(result[new_end:].lstrip(b"\0"), original[end:].lstrip(b"\0"))
        self.assertEqual(main_hash, digest(original[end:].lstrip(b"\0")))

    def test_cannot_layer_over_existing_acpi(self):
        patched, _ = uki.inject_tables(initramfs(), {"wake14.aml": acpi_table()})
        with self.assertRaisesRegex(ValueError, "already contains ACPI"):
            uki.inject_tables(patched, {"other.aml": acpi_table()})

    def test_filename_guards(self):
        for filename in ("../wake.aml", "sub/wake.aml", "too-long-filename.aml", "wake.txt"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                uki.inject_tables(initramfs(), {filename: acpi_table()})

    def test_bad_table_checksum(self):
        damaged = acpi_table()[:-1] + b"\x01"
        with self.assertRaisesRegex(ValueError, "checksum"):
            uki.inject_tables(initramfs(), {"wake14.aml": damaged})

    def test_bad_table_length(self):
        with self.assertRaisesRegex(ValueError, "length"):
            table_header(acpi_table() + b"x")

    def test_truncated_cpio(self):
        for data in (b"", b"070701", uki.cpio_record("test", b"abc", 1)[:-8]):
            with self.subTest(data=data[:16]), self.assertRaises(ValueError):
                uki.cpio_archive(data)

    def test_cpio_path_traversal(self):
        for name in ("../outside", "/absolute"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                uki.cpio_archive(uki.cpio_record(name, b"x", 1))

    def test_cpio_duplicate_names(self):
        data = uki.cpio_record("a", b"x", 1) + uki.cpio_record("a", b"y", 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            uki.cpio_archive(data)

    def test_cpio_integer_overflow(self):
        with self.assertRaisesRegex(ValueError, "overflow"):
            uki.cpio_record("x", b"", 2**32)

    def test_unsupported_compression(self):
        data = initramfs().replace(bytes.fromhex("28b52ffd"), b"GZIP", 1)
        with self.assertRaisesRegex(ValueError, "zstd"):
            uki.inject_tables(data, {"wake14.aml": acpi_table()})


class PETests(unittest.TestCase):
    def test_strip_certificate_only_on_copy(self):
        original = pe(initramfs(), signed=True)
        unsigned = uki.unsigned_copy(original)
        before, after = uki.pe_image(original), uki.pe_image(unsigned)
        self.assertNotEqual(before["certificate"], (0, 0))
        self.assertEqual(after["certificate"], (0, 0))
        for name, section in before["sections"].items():
            self.assertEqual(section["data"], after["sections"][name]["data"])
        self.assertTrue(original.endswith(b"test-certificate"))

    def test_invalid_certificate_bounds(self):
        data = bytearray(pe(initramfs(), signed=True))
        info = uki.pe_image(data)
        struct.pack_into("<II", data, info["cert_entry"], len(data) + 1, 16)
        with self.assertRaisesRegex(ValueError, "certificate"):
            uki.pe_image(data)

    def test_reject_certificate_overlapping_sections(self):
        data = bytearray(pe(initramfs(), signed=True))
        info = uki.pe_image(data)
        offset = info["sections"][".initrd"]["offset"]
        struct.pack_into("<II", data, info["cert_entry"], offset, len(data) - offset)
        with self.assertRaisesRegex(ValueError, "layout"):
            uki.unsigned_copy(data)

    def test_bad_pe_headers(self):
        for data in (b"", b"MZ", b"MZ" + b"\0" * 62):
            with self.subTest(data=data[:8]), self.assertRaises(ValueError):
                uki.pe_image(data)

    def test_missing_uki_sections(self):
        data = bytearray(pe(initramfs()))
        entry = uki.pe_image(data)["sections"][".linux"]["entry"]
        data[entry : entry + 8] = b".other\0\0"
        with self.assertRaisesRegex(ValueError, "Linux UKI"):
            uki.pe_image(data)

    def test_resize_fixes_objcopy_virtual_size(self):
        old = initramfs()
        new, _ = uki.inject_tables(old, {"wake14.aml": acpi_table()})
        original = pe(old)
        simulated_objcopy = bytearray(pe(new))
        section = uki.pe_image(simulated_objcopy)["sections"][".initrd"]
        struct.pack_into("<I", simulated_objcopy, section["entry"] + 8, len(old))
        resized = uki.resize_initrd(simulated_objcopy, new)
        self.assertEqual(uki.pe_image(resized)["sections"][".initrd"]["size"], len(new))
        uki.compare_sections(original, resized, new)

    def test_never_resize_signed_image(self):
        with self.assertRaisesRegex(ValueError, "signed"):
            uki.resize_initrd(pe(initramfs(), signed=True), initramfs())

    def test_detect_unrelated_kernel_change(self):
        original = pe(initramfs())
        modified = original.replace(b"test-kernel", b"evil-kernel", 1)
        with self.assertRaisesRegex(ValueError, "Unrelated section"):
            uki.compare_sections(original, modified, initramfs())

    def test_detect_entrypoint_and_image_base_change(self):
        original = pe(initramfs())
        optional = uki.pe_image(original)["optional"]
        for field, fmt, value in ((16, "<I", 0x1234), (24, "<Q", 0x400000)):
            modified = bytearray(original)
            struct.pack_into(fmt, modified, optional + field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "execution"):
                uki.compare_sections(original, modified, initramfs())

    def test_build_rejects_wrong_kernel_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.efi"
            base.write_bytes(pe(initramfs()))
            output = Path(directory) / "output"
            with self.assertRaisesRegex(ValueError, "kernel"):
                uki.build(base, output, {"wake14.aml": acpi_table()}, "wrong-kernel")
            self.assertFalse(output.exists())
