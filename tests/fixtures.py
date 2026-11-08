# SPDX-License-Identifier: MIT
"""Synthetic firmware for tests; no vendor dumps or real hardware IO."""

import struct

from asus_acpi.uki import cpio_record


def ports():
    return [
        f"{kind}{i:02}" for kind, count in [("HS", 14), ("SS", 10)] for i in range(1, count + 1)
    ]


def upc():
    return "Return (Package (4) { 0xFF, 3, Zero, Zero })"


def pld():
    return "Return (Package (1) { Buffer (20) { 0x82 } })"


def usb_source():
    source = 'DefinitionBlock ("", "SSDT", 2, "INTEL", "xh_rplsb", 0x00000000)\n{\n'
    for port in ports():
        source += f"    External (\\_SB.PC00.XHCI.RHUB.{port}, DeviceObj)\n"
    for port in ports():
        source += f"""    Scope (\\_SB.PC00.XHCI.RHUB.{port})
    {{
        Method (_UPC, 0, NotSerialized)
        {{
            {upc()}
        }}
        Method (_PLD, 0, NotSerialized)
        {{
            {pld()}
        }}
    }}
"""
    return source + "}\n"


def usb_fixture(missing=False):
    source = """DefinitionBlock ("", "DSDT", 2, "TEST", "USB", 1)
{
    Scope (\\_SB)
    {
        Device (PC00)
        {
            Name (_HID, "PNP0A08")
            Device (XHCI)
            {
                Name (_ADR, 0x00140000)
                Device (RHUB)
                {
                    Name (_ADR, Zero)
"""
    for index, port in enumerate(ports(), 1):
        source += f"                    Device ({port}) {{ Name (_ADR, {index})\n"
        if not (missing and port == "HS02"):
            source += "                        Method (_UPC, 0) { " + upc() + " }\n"
        if not (missing and port == "HS06"):
            source += "                        Method (_PLD, 0) { " + pld() + " }\n"
        source += "                    }\n"
    return source + "                }\n            }\n        }\n    }\n}\n"


def thermal_source():
    source = """DefinitionBlock ("", "SSDT", 2, "DptfTb", "DptfTabl", 0x00001000)
{
    External (\\_SB.IETM, DeviceObj)
    External (\\_SB.PC00.TCPU, DeviceObj)
    External (\\_SB.PC00.LPCB.SEN2, DeviceObj)
    External (\\_SB.PC00.LPCB.SEN3, DeviceObj)
    Scope (\\_SB.IETM)
    {
        Name (PSVT, Package (0x04)
        {
            0x02,
"""
    for index, sensor in enumerate(["TCPU", "LPCB.SEN2", "LPCB.SEN3"]):
        source += f"""            Package (0x0C)
            {{
                \\_SB.PC00.TCPU,
                \\_SB.PC00.{sensor},
                2, 0x012C, 0x0C6E, 9, 0x00010000, "MAX", 0x01F4, 10, 20, Zero
            }}""" + (",\n\n" if index < 2 else "\n")
    return source + "        })\n    }\n}\n"


def thermal_fixture(present):
    optional = 'Device (SEN2) { Name (_HID, "INT3403") }' if present else ""
    return f"""DefinitionBlock ("", "DSDT", 2, "TEST", "THERM", 1)
{{
    Scope (\\_SB)
    {{
        Device (PC00)
        {{
            Name (_HID, "PNP0A08")
            Device (TCPU) {{ Name (_HID, "INT3401") }}
            Device (LPCB)
            {{
                Name (_ADR, Zero)
                {optional}
                Device (SEN3) {{ Name (_HID, "INT3403") }}
            }}
        }}
        Device (IETM) {{ Name (_HID, "INT3400") }}
    }}
}}
"""


def i2c_source():
    source = 'DefinitionBlock ("", "SSDT", 2, "_ASUS_", "RplSBTbt", 0x00001000)\n{\n'
    source += "    External (PSON, FieldUnitObj)\n"
    for number in [1, 3]:
        source += f"""    External (\\_SB.PC00.I2C{number}, DeviceObj)
    External (\\_SB.PC00.I2C{number}.TPL1, DeviceObj)
    Scope (\\_SB.PC00.I2C{number})
    {{
        Scope (TPL1)
        {{
            Name (FLAG, One)
        }}
    }}
"""
    return source + "}\n"


def i2c_fixture(present):
    child = 'Device (TPL1) { Name (_HID, "TEST0001") }' if present else ""
    source = """DefinitionBlock ("", "DSDT", 2, "TEST", "I2C", 1)
{
    Scope (\\_SB)
    {
        Device (PC00)
        {
            Name (_HID, "PNP0A08")
"""
    for number in [1, 3]:
        source += f"            Device (I2C{number}) {{ Name (_ADR, {number}) {child} }}\n"
    return source + "        }\n    }\n}\n"


def acpi_table(signature=b"SSDT", oem=b"LOCAL\0", name=b"FX608WAK", revision=1):
    data = bytearray(
        struct.pack("<4sIBB6s8sI4sI", signature, 36, 2, 0, oem, name, revision, b"INTL", 0)
    )
    data[9] = -sum(data) % 256
    return bytes(data)


def initramfs():
    archive = cpio_record("kernel/", b"", 1, 0o40755)
    archive += cpio_record("kernel/x86/microcode/GenuineIntel.bin", b"private-test-microcode", 2)
    archive += cpio_record("TRAILER!!!", b"", 3, 0)
    archive += b"\0" * (-len(archive) % 512)
    return archive + bytes.fromhex("28b52ffd") + b"synthetic-untouched-compressed-stream"


def pe(initrd, signed=False):
    sections = [(".linux", b"test-kernel"), (".uname", b"7.2.14-arch1-1\0"), (".initrd", initrd)]
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, len(sections))
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 32, 4096)
    struct.pack_into("<I", data, optional + 108, 16)
    for index, (name, payload) in enumerate(sections):
        entry = optional + 240 + index * 40
        data[entry : entry + 8] = name.encode().ljust(8, b"\0")
        raw_size = (len(payload) + 511) // 512 * 512
        struct.pack_into(
            "<IIII", data, entry + 8, len(payload), (index + 1) * 4096, raw_size, len(data)
        )
        data += payload + b"\0" * (raw_size - len(payload))
    if signed:
        struct.pack_into("<II", data, optional + 112 + 32, len(data), 16)
        data += b"test-certificate"
    return bytes(data)
