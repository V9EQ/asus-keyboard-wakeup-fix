# SPDX-License-Identifier: MIT
"""Only the configuration actually investigated and tested is supported."""

import hashlib
import json
import platform
import re
import struct
from pathlib import Path

from . import require

VENDOR = "ASUSTeK COMPUTER INC."
MODEL = "ASUS TUF Gaming F16 FX608JPR_FX608JPR"
BIOS = "FX608JPR.312"
KERNEL = "7.2.14-arch1-1"
DSDT_SHA256 = "f1b720582567c0be363f508a8ccf38cd472de468cbbaf2da39828bfd0165cba7"
# Raw firmware binaries are deliberately NOT distributed. Hashes are fail-closed
# guards against patching a different revision/layout under the same table name.
TARGETS = {
    "usbguard": (
        b"INTEL\0",
        b"xh_rplsb",
        0,
        "a8ed3b0f0df21bd24039c6962c131f6c723e34e4f0665255d5b8a3ddb911cea5",
    ),
    "i2cguard": (
        b"_ASUS_",
        b"RplSBTbt",
        0x1000,
        "44887445760e57bffec537696893775ca40321ae8f35e141c95f3e60666bb7a2",
    ),
    "thermfix": (
        b"DptfTb",
        b"DptfTabl",
        0x1000,
        "131104080dfc948a1beb709e71e03848fae2834d96dc982433ee798253e146e8",
    ),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def machine():
    root = Path("/sys/class/dmi/id")
    result = {
        key: (root / name).read_text().strip()
        for key, name in [
            ("vendor", "sys_vendor"),
            ("model", "product_name"),
            ("bios", "bios_version"),
        ]
    }
    result["kernel"] = platform.release()
    try:
        lockdown = Path("/sys/kernel/security/lockdown").read_text()
        match = re.search(r"\[([a-z]+)\]", lockdown)
        result["lockdown"] = match[1] if match else "unknown"
    except OSError:
        result["lockdown"] = "unknown"
    return result


def check_machine(build=False):
    info = machine()
    require(
        info["vendor"] == VENDOR and info["model"] == MODEL,
        "Unsupported laptop; no generic ASUS mode",
    )
    require(
        info["bios"] == BIOS, "Unsupported BIOS; re-investigate tables after a firmware update"
    )
    require(
        info["kernel"] == KERNEL, "Unsupported running kernel; this profile needs revalidation"
    )
    if build:
        require(
            info["lockdown"] == "none",
            "ACPI override blocked/unknown under lockdown; do not disable security automatically",
        )
    return info


def table_header(data):
    require(len(data) >= 36, "ACPI table shorter than header")
    require(struct.unpack_from("<I", data, 4)[0] == len(data), "ACPI length mismatch")
    require(sum(data) % 256 == 0, "Bad ACPI checksum")
    return data[:4], data[10:16], data[16:24], struct.unpack_from("<I", data, 24)[0]


def load_tables(directory):
    found = {}
    dsdt = None
    for path in sorted(Path(directory).iterdir()):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if data[:4] not in (b"DSDT", b"SSDT"):
            continue
        signature, oem, name, revision = table_header(data)
        require(
            name != b"FX608WAK",
            "Input contains our wake overlay; capture tables from an unmodified boot",
        )
        if signature == b"DSDT":
            require(dsdt is None, "Multiple DSDTs")
            dsdt = data
        for key, (
            expected_oem,
            expected_name,
            expected_revision,
            expected_hash,
        ) in TARGETS.items():
            if signature == b"SSDT" and (oem, name) == (expected_oem, expected_name):
                require(key not in found, f"Multiple input tables for {key}")
                require(
                    revision == expected_revision,
                    f"{key}: already overridden/unsupported OEM revision",
                )
                require(
                    digest(data) == expected_hash,
                    f"{key}: firmware fingerprint differs from tested BIOS",
                )
                found[key] = data
    require(
        dsdt is not None and set(found) == set(TARGETS), "Missing original DSDT or target SSDTs"
    )
    require(
        digest(dsdt) == DSDT_SHA256,
        "DSDT differs from the investigated layout; revalidate rather than bypassing the guard",
    )
    return dsdt, found


def save_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
