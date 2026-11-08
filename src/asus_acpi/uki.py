# SPDX-License-Identifier: MIT
"""Offline UKI/early-CPIO editing. No signing, installation, mounts or firmware IO."""

import struct
import subprocess
from pathlib import Path, PurePosixPath

from . import require
from .profile import digest, table_header


def pe_image(data):
    require(len(data) >= 64 and data[:2] == b"MZ", "Not a PE/UKI image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    require(pe + 24 <= len(data) and data[pe : pe + 4] == b"PE\0\0", "Invalid PE header")
    require(struct.unpack_from("<H", data, pe + 4)[0] == 0x8664, "Expected x86-64 PE")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    require(
        optional_size >= 152 and optional + optional_size + count * 40 <= len(data),
        "Truncated PE headers",
    )
    require(struct.unpack_from("<H", data, optional)[0] == 0x20B, "Expected PE32+")
    require(struct.unpack_from("<I", data, optional + 108)[0] >= 5, "Missing PE data directories")
    cert_entry = optional + 112 + 32
    cert = struct.unpack_from("<II", data, cert_entry)
    require(
        (cert == (0, 0)) or (cert[0] > 0 and cert[1] > 0 and sum(cert) <= len(data)),
        "Invalid certificate bounds",
    )
    sections = {}
    for index in range(count):
        entry = optional + optional_size + index * 40
        name = data[entry : entry + 8].rstrip(b"\0").decode("ascii")
        size, address, raw_size, raw_offset = struct.unpack_from("<IIII", data, entry + 8)
        require(
            name not in sections and raw_offset + raw_size <= len(data),
            "Duplicate/truncated PE section",
        )
        sections[name] = {
            "address": address,
            "size": size,
            "raw_size": raw_size,
            "offset": raw_offset,
            "entry": entry,
            "data": data[raw_offset : raw_offset + min(size, raw_size)],
        }
    require(
        ".initrd" in sections and ".linux" in sections and ".uname" in sections,
        "Expected a Linux UKI",
    )
    initrd = sections[".initrd"]
    require(initrd["size"] <= initrd["raw_size"], "Truncated embedded initramfs")
    require(
        initrd["address"] == max(s["address"] for s in sections.values()),
        "Only a final .initrd section is supported",
    )
    require(
        initrd["offset"] + initrd["raw_size"]
        == max(s["offset"] + s["raw_size"] for s in sections.values()),
        "Initrd is not last on disk",
    )
    return {
        "sections": sections,
        "certificate": cert,
        "cert_entry": cert_entry,
        "optional": optional,
        "execution": (
            struct.unpack_from("<I", data, optional + 16)[0],  # Entry point
            struct.unpack_from("<Q", data, optional + 24)[0],  # Image base
            struct.unpack_from("<II", data, optional + 32),  # Section/file alignment
            struct.unpack_from("<HH", data, optional + 68),  # Subsystem/DLL flags
        ),
    }


def unsigned_copy(data):
    info = pe_image(data)
    offset, size = info["certificate"]
    result = bytearray(data)
    if size:
        end = max(s["offset"] + s["raw_size"] for s in info["sections"].values())
        require(
            offset >= end and offset + size == len(data), "Unsupported certificate/overlay layout"
        )
        del result[offset:]
    struct.pack_into("<II", result, info["cert_entry"], 0, 0)
    struct.pack_into("<I", result, info["optional"] + 64, 0)
    return bytes(result)


def cpio_archive(data):
    offset, max_inode = 0, 0
    entries = {}
    while data[offset : offset + 6] == b"070701":
        start = offset
        require(offset + 110 <= len(data), "Truncated CPIO header")
        header = data[offset : offset + 110]
        fields = [int(header[6 + i * 8 : 14 + i * 8], 16) for i in range(13)]
        inode, size, namesize = fields[0], fields[6], fields[11]
        require(namesize > 0 and offset + 110 + namesize <= len(data), "Invalid CPIO filename")
        encoded = data[offset + 110 : offset + 110 + namesize]
        require(encoded[-1:] == b"\0", "Missing CPIO filename terminator")
        name = encoded[:-1].decode().removeprefix("./")
        require(
            not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
            "Unsafe CPIO path",
        )
        payload = (offset + 110 + namesize + 3) & ~3
        offset = (payload + size + 3) & ~3
        require(offset <= len(data), "Truncated CPIO payload")
        max_inode = max(max_inode, inode)
        if name == "TRAILER!!!":
            return entries, start, offset, max_inode
        require(name not in entries, "Duplicate early CPIO path")
        entries[name] = data[payload : payload + size]
    raise ValueError("A first uncompressed newc CPIO archive is required")


def cpio_record(name, payload, inode, mode=0o100644):
    encoded = name.encode() + b"\0"
    fields = [inode, mode, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(encoded), 0]
    require(all(0 <= value < 2**32 for value in fields), "CPIO integer overflow")
    record = b"070701" + "".join(f"{value:08x}" for value in fields).encode() + encoded
    record += b"\0" * (-len(record) % 4)
    record += payload
    return record + b"\0" * (-len(record) % 4)


def inject_tables(data, tables):
    entries, start, end, inode = cpio_archive(data)
    require(
        not any(
            name.startswith("kernel/firmware/acpi/") and value for name, value in entries.items()
        ),
        "Base already contains ACPI overrides; use a pristine UKI",
    )
    suffix = data[end:].lstrip(b"\0")
    require(
        suffix[:4] == bytes.fromhex("28b52ffd"), "Only the tested zstd main initramfs is supported"
    )
    additions, records = {}, []
    for directory in ["kernel", "kernel/firmware", "kernel/firmware/acpi"]:
        if directory not in entries and directory + "/" not in entries:
            inode += 1
            records.append(cpio_record(directory, b"", inode, 0o40755))
            additions[directory] = b""
    for filename, payload in sorted(tables.items()):
        require(
            "/" not in filename and filename.endswith(".aml") and len(filename) < 18,
            "Use short flat AML filenames",
        )
        require(table_header(payload)[0] == b"SSDT", "Only SSDT payloads are accepted")
        name = "kernel/firmware/acpi/" + filename
        inode += 1
        records.append(cpio_record(name, payload, inode))
        additions[name] = payload
    early = data[:start] + b"".join(records) + data[start:end]
    early += b"\0" * (-len(early) % 512)
    combined = early + suffix
    actual, _, actual_end, _ = cpio_archive(combined)
    require(actual == entries | additions, "CPIO contents changed unexpectedly")
    require(combined[actual_end:].lstrip(b"\0") == suffix, "Main initramfs changed")
    return combined, digest(suffix)


def resize_initrd(image, new_initrd):
    """Correct GNU objcopy's preserved PE VirtualSize on a new unsigned output."""
    result = bytearray(image)
    info = pe_image(image)
    require(info["certificate"] == (0, 0), "Never resize an already signed image")
    section = info["sections"][".initrd"]
    require(section["raw_size"] >= len(new_initrd), "objcopy did not reserve enough space")
    require(
        result[section["offset"] : section["offset"] + len(new_initrd)] == new_initrd,
        "Incorrect initrd payload",
    )
    struct.pack_into("<I", result, section["entry"] + 8, len(new_initrd))
    alignment = struct.unpack_from("<I", result, info["optional"] + 32)[0]
    require(alignment > 0, "Invalid PE section alignment")
    end = max(
        s["address"] + max(len(new_initrd) if name == ".initrd" else s["size"], s["raw_size"])
        for name, s in info["sections"].items()
    )
    struct.pack_into(
        "<I", result, info["optional"] + 56, (end + alignment - 1) // alignment * alignment
    )
    struct.pack_into("<I", result, info["optional"] + 64, 0)
    return bytes(result)


def compare_sections(original, result, initramfs):
    before, after = pe_image(original), pe_image(result)
    require(before["execution"] == after["execution"], "PE execution metadata changed")
    require(before["sections"].keys() == after["sections"].keys(), "UKI sections changed")
    for name, section in before["sections"].items():
        other = after["sections"][name]
        require(section["address"] == other["address"], f"Section relocated: {name}")
        if name != ".initrd":
            require(
                section["data"] == other["data"] and section["size"] == other["size"],
                f"Unrelated section changed: {name}",
            )
    require(after["sections"][".initrd"]["data"] == initramfs, "Embedded initramfs mismatch")


def build(base_path, directory, tables, kernel):
    data = Path(base_path).read_bytes()
    sections = pe_image(data)["sections"]
    require(
        sections[".uname"]["data"].rstrip(b"\0").decode() == kernel,
        "Base UKI kernel does not match tested/running kernel",
    )
    initramfs, main_hash = inject_tables(sections[".initrd"]["data"], tables)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    base = directory / "base.unsigned.efi"
    archive = directory / "initramfs.img"
    output = directory / "asus-acpi-test.unsigned.efi"
    base.write_bytes(unsigned_copy(data))
    archive.write_bytes(initramfs)
    subprocess.run(
        ["objcopy", "--update-section", f".initrd={archive}", str(base), str(output)], check=True
    )
    output.write_bytes(resize_initrd(output.read_bytes(), initramfs))
    compare_sections(data, output.read_bytes(), initramfs)
    return {
        "base_sha256": digest(data),
        "unsigned_base_sha256": digest(base.read_bytes()),
        "image_sha256": digest(output.read_bytes()),
        "main_initramfs_sha256": main_hash,
        "kernel": kernel,
        "unsigned_image": output.name,
    }
