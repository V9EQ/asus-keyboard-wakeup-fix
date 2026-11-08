# SPDX-License-Identifier: MIT
"""Render reviewable permanent integration files (export itself never installs)."""

from pathlib import Path
from shlex import quote

from . import require
from .profile import BIOS, MODEL, TARGETS, VENDOR, digest, save_json, table_header


def render_dropin(
    sys_root="/sys",
    modules_root="/usr/lib/modules",
    boot_root="/boot",
    proc_config="/proc/config.gz",
    checksum_file="/usr/local/share/asus-acpi/aml.sha256",
    override_dir="/etc/initcpio/acpi_override",
    vendor_override_dir="/usr/lib/initcpio/acpi_override",
):
    """Parameters permit sandbox tests; the public CLI always uses real paths."""
    template = r"""# SPDX-License-Identifier: MIT
# Sourced by mkinitcpio after resolving KERNELVERSION; no Python/uv at update time.
_asus_fail() { printf 'ASUS ACPI: %s\n' "$*" >&2; exit 1; }
_asus_sys=@SYS@
_asus_modules=@MODULES@
_asus_boot=@BOOT@
[[ -r "$_asus_sys/class/dmi/id/sys_vendor" && -r "$_asus_sys/class/dmi/id/product_name" && -r "$_asus_sys/class/dmi/id/bios_version" ]] || _asus_fail 'Cannot verify platform'
[[ $(<"$_asus_sys/class/dmi/id/sys_vendor") == @VENDOR@ ]] || _asus_fail 'Unsupported manufacturer'
[[ $(<"$_asus_sys/class/dmi/id/product_name") == @MODEL@ ]] || _asus_fail 'Unsupported laptop'
[[ $(<"$_asus_sys/class/dmi/id/bios_version") == @BIOS@ ]] || _asus_fail 'BIOS changed: disable and re-investigate overrides'
sha256sum --check --status @CHECKSUM@ || _asus_fail 'AML checksum mismatch'
# The native hook collects both locations, including unreviewed extra payloads.
for _asus_aml in @OVERRIDES@/*.aml @VENDOR_OVERRIDES@/*.aml; do
    [[ -e $_asus_aml || -L $_asus_aml ]] || continue
    [[ -f $_asus_aml && ! -L $_asus_aml ]] || _asus_fail 'Invalid AML file'
    grep -Fxq -- "$(sha256sum -- "$_asus_aml")" @CHECKSUM@ || _asus_fail 'Unreviewed extra AML'
done
[[ -r "$_asus_sys/kernel/security/lockdown" ]] || _asus_fail 'Cannot verify lockdown policy'
[[ $(<"$_asus_sys/kernel/security/lockdown") == *'[none]'* ]] || _asus_fail 'Lockdown blocks ACPI override; security will not be disabled'
_asus_kernel=${KERNELVERSION:-}
[[ $_asus_kernel != . && $_asus_kernel != .. && $_asus_kernel =~ ^[A-Za-z0-9_.+-]+$ ]] || _asus_fail 'Missing/invalid mkinitcpio KERNELVERSION'
_asus_config=''
for _asus_path in "$_asus_modules/$_asus_kernel/config" "$_asus_modules/$_asus_kernel/build/.config" "$_asus_boot/config-$_asus_kernel"; do
    if [[ -r $_asus_path ]]; then
        _asus_config=$(<"$_asus_path")
        break
    fi
done
if [[ -z $_asus_config && $_asus_kernel == "$(uname -r)" && -r @PROC@ ]]; then
    _asus_config=$(gzip -cd @PROC@) || _asus_fail 'Cannot read kernel config'
fi
[[ -n $_asus_config ]] || _asus_fail 'Kernel config unavailable: install matching kernel headers'
for _asus_option in CONFIG_X86_64 CONFIG_BLK_DEV_INITRD CONFIG_ACPI_TABLE_UPGRADE; do
    grep -qx "${_asus_option}=y" <<<"$_asus_config" || _asus_fail "Kernel lacks ${_asus_option}=y"
done
for _asus_option in CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY CONFIG_ACPI_TABLE_OVERRIDE_VIA_BUILTIN_INITRD; do
    if grep -qx "${_asus_option}=y" <<<"$_asus_config"; then
        _asus_fail "Incompatible ${_asus_option}=y"
    fi
done
[[ $(declare -p HOOKS 2>/dev/null) == 'declare -a '* ]] || _asus_fail 'HOOKS must be a Bash array'
case " ${HOOKS[*]} " in
    *' acpi_override '*) ;;
    *) HOOKS+=(acpi_override) ;;
esac
unset _asus_sys _asus_modules _asus_boot _asus_kernel _asus_config _asus_path _asus_option _asus_aml
unset -f _asus_fail
"""
    for marker, value in {
        "@SYS@": sys_root,
        "@MODULES@": modules_root,
        "@BOOT@": boot_root,
        "@PROC@": proc_config,
        "@CHECKSUM@": checksum_file,
        "@OVERRIDES@": override_dir,
        "@VENDOR_OVERRIDES@": vendor_override_dir,
        "@VENDOR@": VENDOR,
        "@MODEL@": MODEL,
        "@BIOS@": BIOS,
    }.items():
        template = template.replace(marker, quote(str(value)))
    return template


REBUILD = """#!/usr/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
exec 9>/run/asus-acpi.lock
/usr/bin/flock -x 9
/usr/bin/mkinitcpio -p linux
/usr/bin/sbctl sign /boot/EFI/Linux/arch-linux.efi
/usr/bin/sbverify --cert /var/lib/sbctl/keys/db/db.pem /boot/EFI/Linux/arch-linux.efi
"""

NVIDIA_HOOK = """[Trigger]
Operation = Install
Operation = Upgrade
Operation = Remove
Type = Package
Target = nvidia
Target = nvidia-dkms
Target = nvidia-open
Target = nvidia-open-dkms
Target = nvidia-utils

[Action]
Description = Rebuilding and signing UKIs with reviewed ASUS ACPI overrides...
When = PostTransaction
Depends = mkinitcpio
Depends = sbctl
Depends = sbsigntools
Exec = /usr/local/libexec/asus-acpi-rebuild
"""

INSTRUCTIONS = """PERMANENT INTEGRATION EXPORT — NOT AN AUTOMATIC INSTALLER

No live files have been changed. Inspect install-plan.json and root/ first.
Only ASUS FX608JPR BIOS 312, mkinitcpio drop-ins and sbctl are covered.
No Limine/menu edits, key enrollment, security disabling or firmware writes.

Before installation:
- Back up /boot, mkinitcpio config/drop-ins, existing target paths and signing registry.
- Keep a separately bootable known-good/recovery image; do NOT remove it yet.
- Review conflicting existing files rather than overwriting a manual installation.
- Install matching headers/config for every kernel preset; the guard fails closed
  when a future installed kernel's capabilities cannot be verified.
- Ensure the primary UKI is registered with sbctl and signing keys are available.

The proposed files mirror absolute destinations underneath root/:
- etc/initcpio/acpi_override/*.aml: the reviewed payloads.
- etc/mkinitcpio.conf.d/90-asus-acpi.conf: model/BIOS, checksums, lockdown and kernel
  capability guard; adds acpi_override once without rewriting global HOOKS.
- usr/local/share/asus-acpi/aml.sha256: installed AML integrity checks.
- usr/local/libexec/asus-acpi-rebuild: rebuild the linux preset and sign/verify.
- etc/pacman.d/hooks/95-asus-acpi-nvidia.hook: explicit driver-update rebuild.

Copy only reviewed files with root ownership (helper 0755; others 0644), then
rebuild the reviewed linux preset, sign with sbctl sign and verify using
sbverify --cert /var/lib/sbctl/keys/db/db.pem /boot/EFI/Linux/arch-linux.efi.
Validate all payloads in the FIRST early archive before changing boot defaults.
The existing standard Linux/mkinitcpio hooks rebuild the normal UKI on updates;
the NVIDIA hook covers embedded driver updates too. Python/uv is not used there.

A kernel version number is not an allowlist. New kernels still need regression
checks. Signed UKIs/PCR policies may require additional, system-specific handling.
Before a BIOS/EC update, disable these overrides, rebuild/sign and boot a pristine
image. The build-time BIOS guard cannot undo AML already inside an old UKI.

Rollback design: restore backed-up/only-managed files, remove the added drop-in,
AMLs, checksum file and NVIDIA helper/hook, rebuild/sign CURRENT installed kernels.
Never restore a stale kernel image after package updates without checking modules.
Do not blindly copy this tree over /. The separate asus-acpi install command
renders these files itself, defaults to dry-run and requires explicit --apply.
See docs/permanent.md for its restricted layout, conflict checks and rollback limits.
"""


def export_bundle(output, tables, info):
    require(
        (info["vendor"], info["model"], info["bios"]) == (VENDOR, MODEL, BIOS),
        "Unsupported export platform",
    )
    expected = {f"{name}.aml" for name in TARGETS}
    require(set(tables) in (expected, expected | {"wake14.aml"}), "Unexpected payload profile")
    files = {}
    for filename, data in tables.items():
        if filename == "wake14.aml":
            header = (b"SSDT", b"LOCAL\0", b"FX608WAK", 1)
        else:
            oem, name, revision, _ = TARGETS[filename.removesuffix(".aml")]
            header = (b"SSDT", oem, name, revision + 1)
        require(table_header(data) == header, f"Invalid payload {filename}")
        files[f"etc/initcpio/acpi_override/{filename}"] = data
    checksums = "".join(
        f"{digest(data)}  /etc/initcpio/acpi_override/{name}\n"
        for name, data in sorted(tables.items())
    )
    files.update(
        {
            "etc/mkinitcpio.conf.d/90-asus-acpi.conf": render_dropin().encode(),
            "etc/pacman.d/hooks/95-asus-acpi-nvidia.hook": NVIDIA_HOOK.encode(),
            "usr/local/libexec/asus-acpi-rebuild": REBUILD.encode(),
            "usr/local/share/asus-acpi/aml.sha256": checksums.encode(),
        }
    )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for name, data in sorted(files.items()):
        path = output / "root" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        mode = 0o755 if name == "usr/local/libexec/asus-acpi-rebuild" else 0o644
        path.chmod(mode)
        manifest["/" + name] = {"sha256": digest(data), "mode": oct(mode)}
    plan = {"format": 1, "export_only": True, "machine": info, "files": manifest}
    save_json(output / "install-plan.json", plan)
    (output / "INSTALL.txt").write_text(INSTRUCTIONS)
    return plan
