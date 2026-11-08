# Updates and compatibility

## AML versus UKI

AML describes firmware devices and wake events. It does not have the kernel-module
ABI dependency of the archived DKMS implementation. It is still specific to the
investigated **ASUS FX608JPR / BIOS 312 / original ACPI-table fingerprints**.

A UKI embeds a specific kernel and initramfs, possibly including NVIDIA modules.
A separate test UKI is a static snapshot, not a reference to pacman's newest kernel.
Re-signing it does not rebuild or update its contents.

Python/uv runs when preparing/building/exporting, **not at every boot**. Linux reads
AML already included in the boot image. Exported permanent update hooks are native
Bash/mkinitcpio/sbctl and do not invoke this Python project.

## No Linux release-number allowlist in 0.3

`7.2.14-arch1-1` remains the hardware-tested reference, not a mandatory release.
Preparation records its kernel as informational metadata; that does not prevent
reusing reviewed AML with another compatible kernel on the same firmware.

For UKI building the candidate's `.uname` determines which config must be checked.
The checks require `CONFIG_X86_64=y`, `CONFIG_BLK_DEV_INITRD=y` and
`CONFIG_ACPI_TABLE_UPGRADE=y`. Forced lockdown and the incompatible
`CONFIG_ACPI_TABLE_OVERRIDE_VIA_BUILTIN_INITRD=y` path are rejected.

Configuration is obtained from the candidate's installed config/headers or boot
config. `/proc/config.gz` is only used when it belongs to that exact kernel release.
If configuration cannot be read, install matching headers; do not bypass the check.
Active/unknown running lockdown policy still blocks image creation/export. The tool
never disables Secure Boot or lockdown. A valid signature does not bypass lockdown.

This removes the arbitrary version pin, **not the need to test new kernels/drivers**.
Neither capability checks nor synthetic future-version tests establish hardware
compatibility with every future release.

## Updates

| Component | Separate test UKI | Reviewed permanent integration |
| --- | --- | --- |
| Linux | Rebuild from a fresh base, sign and test; old images retain old kernels/modules. | Normal mkinitcpio rebuilds include existing AML automatically; exported guard checks the new kernel's config. |
| NVIDIA | Refresh base initramfs and derived UKI so embedded module matches userspace. | Exported NVIDIA pacman hook explicitly rebuilds/signs after driver package changes. |
| Limine | Usually no AML rebuild; verify menu path and loader signing. | Same: AML is in the UKI, not patched into Limine. |
| mkinitcpio/stub/microcode | Old test image does not inherit updates; use a fresh base. | Appropriate normal rebuilds incorporate them; inspect transaction failures. |
| BIOS/EC | Disable overrides and investigate new tables/wake wiring first. | Same. Build-time BIOS guard stops future builds, but cannot undo AML already in an old image. |
| Keys / lockdown / PCR policies | Recheck signing, override policy and TPM sealing. | Same; no automatic key enrollment/security bypass/PCR policy regeneration. |

After a Linux package update, an old image may also have no matching modules under
`/usr/lib/modules/<old-release>`. Its initramfs contains only a subset of all modules.
It might start, but mixing an old embedded NVIDIA module with newer userspace is not
supported. Re-running `build` against the same old base is **not** an update: the
builder intentionally preserves the kernel and compressed main archive byte-for-byte.

## Automatic update support versus installation

The 0.3 command:

```sh
uv run asus-acpi export-mkinitcpio --prepared work/prepared --output work/permanent
```

exports all proposed permanent files, including an idempotent guarded mkinitcpio
drop-in and NVIDIA rebuild hook. **Export does not install them.** Version 0.4 adds
`install` and `uninstall`, dry-run by default and privileged only with `--apply`.
See [permanent.md](permanent.md) for layout restrictions, signing dependencies,
conflict detection and rollback limits. An existing differing manual installation
is never silently overwritten. The native helper rebuilds only the reviewed linux
preset and uses targeted signing plus cryptographic `sbverify` verification.

Once those files have been deliberately installed and tested, standard kernel
rebuilds include the AML automatically; Python need not regenerate it for every
ordinary kernel/NVIDIA update. BIOS/table changes remain a separate investigation.

## Evidence from the inspected development system

The system's normal mkinitcpio preset produces `/boot/EFI/Linux/arch-linux.efi` and
has an sbctl post hook. Normal kernel/mkinitcpio transactions can rebuild/sign that
image. An earlier separate test image was not in the preset and did not get rebuilt.

The inspected Limine update hook copies its EFI executables, not its menu file.
That suggests ordinary Limine updates preserve an entry, but is not a guarantee
about every future hook, key, configuration or bootloader change.
