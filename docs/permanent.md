# Permanent integration: current implementation status

Version 0.3 adds a reviewable **export**, not an automatic privileged installer.
The existing working system is not modified by developing or running this export.

```sh
uv run asus-acpi prepare --tables /path/to/original-tables --output work/prepared
uv run asus-acpi export-mkinitcpio --prepared work/prepared --output work/permanent
```

Use preserved original tables when the current boot already has overrides.
`prepare --without-wake` exports just the three error fixes, without GPE14.
Output must be a new non-system workspace; existing outputs are never overwritten.

## Bundle contents

- `root/etc/initcpio/acpi_override/*.aml`: exact validated payloads.
- `root/etc/mkinitcpio.conf.d/90-asus-acpi.conf`: a native Bash drop-in.
- `root/usr/local/share/asus-acpi/aml.sha256`: integrity checks for installed payloads.
- `root/etc/pacman.d/hooks/95-asus-acpi-nvidia.hook`: NVIDIA transaction rebuild.
- `root/usr/local/libexec/asus-acpi-rebuild`: mkinitcpio rebuild and sbctl signing.
- `install-plan.json`: destinations, modes and hashes, with `export_only: true`.
- `INSTALL.txt`: prerequisites, backup/review instructions and rollback design.

No firmware dumps, keys, model-specific absolute home paths or Linux version numbers
are embedded in those update scripts. Runtime software is Bash, coreutils/grep/gzip,
mkinitcpio and sbctl. Python 3.13/uv is for preparing the bundle only.
The helper currently assumes the tested primary UKI path
`/boot/EFI/Linux/arch-linux.efi`; alternative layouts need explicit review.

## Build-time safety

The drop-in is sourced while mkinitcpio is configuring the build, before it generates
a new image. It fails the build process if:

- manufacturer/model or BIOS differs from the investigated FX608JPR / BIOS 312;
- installed AML hashes differ;
- running lockdown policy is active or cannot be verified;
- the configuration for `KERNELVERSION` is unavailable or lacks x86-64,
  initrd or ACPI-table-upgrade support;
- the candidate kernel forces lockdown or selects the incompatible kernel-linked
  initramfs table-override mechanism.

Install matching headers/config for **each installed kernel preset**. A running
kernel's `/proc/config.gz` is only a fallback for that same release, never evidence
for a different newly installed kernel. Missing headers stop the build rather than
silently accepting an unverified configuration.

The drop-in preserves existing `HOOKS`, adding `acpi_override` only if absent.
It does not modify the global mkinitcpio configuration. Kernel/module ABI versions
are not an allowlist; capabilities and the candidate kernel's own configuration
are used. A passing capability check still needs boot/suspend regression testing.

**A build-time guard does not disable overrides already inside an old UKI.** Before
a BIOS/EC update, disable the overrides, rebuild/sign and boot a pristine image.

## Not implemented yet / next checkpoint

1. Privileged `install --dry-run` / explicit `--apply`, with conflict detection.
2. Atomic backup of affected files and relevant boot images before any mutation.
3. Safe adoption of a matching existing manual installation, without overwriting
   unrelated ACPI files, pacman hooks or signing configuration.
4. Rebuild/sign validation of the primary UKI before declaring installation complete.
5. Uninstall/rollback that rebuilds current kernels rather than blindly restoring
   an obsolete kernel snapshot after package updates.
6. Sandbox/failure-injection tests for every write/rollback boundary.

Do not blindly copy the exported tree over `/`. Until those steps are implemented,
installation remains an explicitly reviewed administrator procedure. In particular,
this release does not automatically remove test boot entries or change defaults.
