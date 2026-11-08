#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare model-specific ACPI overrides and an isolated unsigned UKI.
Offline commands never change the system. install/uninstall default to dry-run;
explicit --apply rebuilds/signs the reviewed primary UKI, without firmware/menu edits.
"""

import argparse
import difflib
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from asus_acpi import installer, patches, permanent, require, uki
from asus_acpi.profile import (
    TARGETS,
    check_machine,
    digest,
    load_tables,
    machine,
    save_json,
    table_header,
)

DATA = Path(__file__).resolve().parent / "data"


def output_path(path):
    path = Path(path).resolve()
    for root in ["/boot", "/efi", "/etc", "/sys", "/proc", "/dev", "/usr"]:
        require(not path.is_relative_to(root), "Use a workspace, not a live system/boot directory")
    require(
        not path.exists(), "Output must be a NEW directory; existing work is never overwritten"
    )
    return path


def run(command, log):
    result = subprocess.run([str(arg) for arg in command], capture_output=True, text=True)
    text = result.stdout + result.stderr
    Path(log).write_text(text)
    require(result.returncode == 0, f"Command failed; see {log}")
    return text


def compile_table(source, prefix, log, strict=False):
    return run(["iasl", *(["-we"] if strict else []), "-p", prefix, source], log)


def warnings(text):
    return Counter(re.findall(r"Warning\s+(\d+)", text))


def capture(args):
    info = check_machine()
    dsdt, tables = load_tables(args.tables)
    output = output_path(args.output)
    output.mkdir(parents=True)
    (output / "DSDT.aml").write_bytes(dsdt)
    for name, data in tables.items():
        (output / f"{name}.aml").write_bytes(data)
    save_json(
        output / "capture.json",
        {
            "machine": info,
            "original_sha256": {"DSDT.aml": digest(dsdt)}
            | {f"{key}.aml": digest(data) for key, data in tables.items()},
        },
    )
    print(f"Original tables captured to {output}; treat these as private firmware data.")


def prepare(args):
    info = check_machine()
    dsdt, tables = load_tables(args.tables)
    require(shutil.which("iasl"), "Install ACPICA (iasl)")
    output = output_path(args.output)
    output.mkdir(parents=True)
    for name in ["original", "sources", "baseline", "aml", "logs", "patches"]:
        (output / name).mkdir()
    dsdt_path = output / "original/DSDT.aml"
    dsdt_path.write_bytes(dsdt)
    prepared = {}
    for name, data in tables.items():
        raw = output / f"original/{name}.aml"
        raw.write_bytes(data)
        prefix = output / f"sources/{name}-original"
        command = ["iasl", "-p", prefix]
        if name != "usbguard":
            command += ["-e", dsdt_path]
        command += ["-d", raw]
        run(command, output / f"logs/disassemble-{name}.log")
        original = prefix.with_suffix(".dsl").read_text()
        baseline = patches.resolve_i2c(original) if name == "i2cguard" else original
        baseline_path = output / f"sources/{name}-baseline.dsl"
        baseline_path.write_text(baseline)
        transform = {
            "usbguard": patches.patch_usb,
            "i2cguard": patches.patch_i2c,
            "thermfix": patches.patch_thermal,
        }[name]
        patched = transform(baseline)
        patched_path = output / f"sources/{name}.dsl"
        patched_path.write_text(patched)
        (output / f"patches/{name}.patch").write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True),
                    patched.splitlines(True),
                    fromfile=f"{name}-original.dsl",
                    tofile=f"{name}.dsl",
                )
            )
        )
        before = compile_table(
            baseline_path, output / f"baseline/{name}", output / f"logs/baseline-{name}.log"
        )
        after = compile_table(
            patched_path, output / f"aml/{name}", output / f"logs/compile-{name}.log"
        )
        require(
            warnings(before) == warnings(after), f"{name}: new compiler warnings; inspect logs"
        )
        payload = (output / f"aml/{name}.aml").read_bytes()
        oem, table, old_revision, _ = TARGETS[name]
        require(
            table_header(payload) == (b"SSDT", oem, table, old_revision + 1),
            f"{name}: replacement identity/revision changed",
        )
        prepared[f"{name}.aml"] = digest(payload)
        print(f"{name}: compiled, inherited warning counts {dict(warnings(after))}")
    if not args.without_wake:
        # All input SSDTs are fingerprinted; capture/prepare reject an existing
        # FX608WAK overlay. The overlay remains separate for regression isolation.
        source = output / "sources/wake14.dsl"
        shutil.copyfile(DATA / "wake14.dsl", source)
        compile_table(
            source, output / "aml/wake14", output / "logs/compile-wake14.log", strict=True
        )
        payload = (output / "aml/wake14.aml").read_bytes()
        require(
            table_header(payload) == (b"SSDT", b"LOCAL\0", b"FX608WAK", 1),
            "Unexpected wake overlay",
        )
        prepared["wake14.aml"] = digest(payload)
    save_json(
        output / "manifest.json",
        {
            "format": 1,
            "machine": info,
            "kernel": info["kernel"],
            "aml_sha256": prepared,
            "original_sha256": {"DSDT.aml": digest(dsdt)}
            | {f"{key}.aml": digest(data) for key, data in tables.items()},
        },
    )
    print(f"Prepared {len(prepared)} tables in {output}; no live configuration changed.")


def prepared_tables(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("format") == 1
        and isinstance(manifest.get("kernel"), str)
        and bool(manifest["kernel"]),
        "Unsupported manifest",
    )
    files = manifest["aml_sha256"]
    expected = {f"{name}.aml" for name in TARGETS}
    require(
        set(files) in [expected, expected | {"wake14.aml"}], "Unexpected AML filenames/profile"
    )
    result = {}
    for filename, checksum in files.items():
        data = (directory / "aml" / filename).read_bytes()
        require(digest(data) == checksum, f"Prepared AML changed: {filename}")
        name = filename.removesuffix(".aml")
        if name in TARGETS:
            oem, table, revision, _ = TARGETS[name]
            header = (b"SSDT", oem, table, revision + 1)
        else:
            header = (b"SSDT", b"LOCAL\0", b"FX608WAK", 1)
        require(table_header(data) == header, f"Invalid AML header: {filename}")
        result[filename] = data
    return result


def build(args):
    image = uki.pe_image(Path(args.base).read_bytes())
    kernel = image["sections"][".uname"]["data"].rstrip(b"\0").decode()
    check_machine(build=True, kernel=kernel)
    require(shutil.which("objcopy"), "Install binutils (objcopy)")
    tables = prepared_tables(args.prepared)
    output = output_path(args.output)
    report = uki.build(args.base, output, tables, kernel)
    save_json(output / "build.json", report)
    print(json.dumps(report, indent=2))
    print(
        "UNSIGNED artifact only. Preserve a working entry; sign the finished UKI before testing."
    )


def export_mkinitcpio(args):
    info = check_machine(build=True)
    tables = prepared_tables(args.prepared)
    output = output_path(args.output)
    plan = permanent.export_bundle(output, tables, info)
    print(json.dumps(plan, indent=2))
    print(
        "Export only: no live files changed. Review INSTALL.txt and back up before installation."
    )


def install(args):
    info = check_machine(build=True)
    report = installer.Installer().install(
        prepared_tables(args.prepared), info, apply=args.apply, adopt=args.adopt_existing
    )
    print(json.dumps(report, indent=2))


def uninstall(args):
    print(json.dumps(installer.Installer().uninstall(apply=args.apply), indent=2))


def verify(args):
    directory = Path(args.build).resolve()
    base = (directory / "base.unsigned.efi").read_bytes()
    image = (
        Path(args.image).read_bytes()
        if args.image
        else (directory / "asus-acpi-test.unsigned.efi").read_bytes()
    )
    initramfs = (directory / "initramfs.img").read_bytes()
    uki.compare_sections(base, image, initramfs)
    old = uki.pe_image(base)["sections"][".initrd"]["data"]
    regenerated, main_hash = uki.inject_tables(old, prepared_tables(args.prepared))
    require(regenerated == initramfs, "Archive differs from validated prepared tables")
    report = json.loads((directory / "build.json").read_text())
    require(digest(base) == report["unsigned_base_sha256"], "Saved base UKI changed")
    require(main_hash == report["main_initramfs_sha256"], "Main initramfs changed")
    print("PASS: exact prepared AMLs in FIRST early CPIO; all previous early payloads preserved.")
    print("PASS: kernel/other sections and compressed main initramfs unchanged.")
    present = uki.pe_image(image)["certificate"] != (0, 0)
    print(
        f"PE certificate present: {present}. This is NOT cryptographic signature/enrollment verification."
    )
    print("Use sbctl verify separately for a signed test image.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="Print local platform/lockdown state, no changes")
    p = sub.add_parser("capture", help="Copy original tables; boot WITHOUT overrides first")
    p.add_argument("--tables", default="/sys/firmware/acpi/tables")
    p.add_argument("--output", required=True)
    p = sub.add_parser("prepare", help="Generate guarded ASL, diffs, compiled AML and manifest")
    p.add_argument("--tables", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--without-wake", action="store_true", help="Only the three ACPI error fixes")
    p = sub.add_parser("build", help="Build a NEW unsigned test UKI from a pristine UKI")
    p.add_argument("--prepared", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser(
        "export-mkinitcpio", help="Export permanent integration files; NEVER install them"
    )
    p.add_argument("--prepared", required=True)
    p.add_argument("--output", required=True)
    for command in ("install", "uninstall"):
        p = sub.add_parser(
            command, help=f"{command.title()} permanent integration (dry-run by default)"
        )
        mode = p.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply", action="store_true", help="Explicitly authorize system changes (root)"
        )
        mode.add_argument("--dry-run", action="store_true", help="Plan only; this is the default")
        if command == "install":
            p.add_argument("--prepared", required=True)
            p.add_argument(
                "--adopt-existing", action="store_true", help="Adopt ONLY byte-identical files"
            )
    p = sub.add_parser("verify", help="Read-only archive/section integrity checks")
    p.add_argument("--prepared", required=True)
    p.add_argument("--build", required=True)
    p.add_argument("--image", help="Optional signed result to inspect; does not verify signature")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            print(json.dumps(machine(), indent=2))
        else:
            globals()[args.command.replace("-", "_")](args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
