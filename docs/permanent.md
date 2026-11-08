# Permanent integration and automatic installation

Version 0.4 adds explicit installation and uninstallation. Both commands default to
**read-only dry-run**; only `--apply` authorizes privileged system changes. Export
remains available for review and never copies anything to `/`.

## Supported installation layout

The installer deliberately supports only the investigated Arch layout:

- ASUS FX608JPR / BIOS 312, lockdown disabled and a capable kernel configuration;
- one `linux.preset`, `PRESETS=('default')`, `ALL_kver="/boot/vmlinuz-linux"`, and
  `default_uki="/boot/EFI/Linux/arch-linux.efi"`;
- default mkinitcpio configuration/drop-ins (an explicit `ALL_config` or
  `default_config` disables drop-ins in mkinitcpio and is therefore rejected);
- `/boot` mounted, an existing working primary UKI, native `acpi_override` hook;
- `mkinitcpio`, `sbctl`, `flock` and `sbverify` (Arch package **sbsigntools**),
  existing sbctl signing keys and `/var/lib/sbctl/keys/db/db.pem`;
- no legacy DKMS workaround or unrelated AML in either override directory.

Dependencies/keys are not installed or enrolled automatically. Install matching
headers/config for every candidate kernel. The installer does not edit Limine,
change boot defaults, remove recovery entries, disable security or reboot. Keep an
independently bootable recovery image/media and sufficient backup/ESP disk space.
Cryptographic verification against db.pem does not establish firmware enrollment;
that must already be configured and tested.

## Usage

Prepare from preserved **original** tables, not a currently overridden boot:

```sh
uv run asus-acpi prepare --tables /path/to/original-tables --output work/prepared
uv run asus-acpi export-mkinitcpio --prepared work/prepared --output work/permanent
uv run asus-acpi install --prepared work/prepared --dry-run
```

`prepare --without-wake` omits GPE14. Review exported files and the dry-run first.
For an applied operation, use the reviewed installation of this package with root
privileges. From a trusted, reviewed checkout with its prepared Python environment:

```sh
sudo -- "$PWD/.venv/bin/asus-acpi" install --prepared "$PWD/work/prepared" --apply
```

Do not run an unreviewed checkout or dependencies as root. The installer regenerates
managed shell scripts from this package; it does not execute user-provided bundle
plans. Only the validated prepared AMLs are consumed from the workspace.

Existing destinations cause a conflict. `--adopt-existing` permits **only exact
byte-identical** files, after backing them up; there is no force-overwrite switch.
Differing manual hooks/helpers need separate review/migration. In particular, the
pre-existing manual installation on the development laptop is **not automatically
adopted**, and was not modified during development of this installer.

## Applied transaction

1. Lock project operations and acquire pacman's exclusive database lock, preventing
   concurrent package updates. Never delete another process's pacman lock.
2. Recheck prerequisites, layout, legacy module conflicts and every destination;
   reject symlink destinations/parents and unreviewed payloads.
3. Save checksummed backups of the full `/boot`, affected destinations, mkinitcpio
   config/presets/drop-ins, kernel config directory, local pacman hooks and sbctl
   file registry in **`/var/lib/asus-acpi` (0700)**. Backups may contain boot secrets;
   do not publish them. No live configuration changes precede completed backups.
4. Replace individual managed files atomically, with root ownership and appropriate
   modes. Do not rewrite global `HOOKS`; the drop-in adds `acpi_override` once.
5. Run `mkinitcpio -p linux`, `sbctl sign` for the primary image and
   `sbverify --cert .../db.pem` for cryptographic verification. Do not enroll keys
   or register/sign unrelated files using `sign-all`.
6. Validate the exact AML bytes in the **FIRST** early archive, reject extra AMLs,
   check the rebuilt image's kernel capabilities and confirm recovery/menu/config
   files and signing registry stayed unchanged. Only then mark installation complete.

The native drop-in checks model/BIOS, AML integrity, extra payloads, lockdown and
**KERNELVERSION**, not an arbitrary Linux version allowlist. A different kernel's
config is never inferred from the running kernel. Update-time software is native
Bash/coreutils/mkinitcpio/sbctl/sbsigntools; Python/uv is not required then.
The NVIDIA hook rebuilds/signs the reviewed linux preset after driver transactions.
The helper serializes against project operations. Existing normal kernel rebuild
and sbctl post-hooks must remain configured; inspect every package hook failure.

## Uninstall and failure recovery

```sh
uv run asus-acpi uninstall --dry-run
sudo -- "$PWD/.venv/bin/asus-acpi" uninstall --apply
```

Uninstall refuses changed managed files. It restores pre-install managed files
(including adopted ones) and rebuilds/signs **currently installed kernels**, rather
than restoring an obsolete UKI from the installation date. Adopted AMLs therefore
remain present: uninstall reverses this tool's changes, not the prior manual setup.
The archive is retained after uninstall; inspect/archive it privately before a new
installation. This release does not perform in-place upgrades or erase backups.

Command, write, image-validation and signature-verification failures attempt immediate
restoration from the transaction's checksummed backup. Uninstall first backs up
**current** boot images, so a failed uninstall does not resurrect an obsolete kernel.
Failure backups and incomplete state are retained; do not blindly retry an incomplete
installation or delete state without reviewing it. An uninstall can be retried after
its failure is resolved; each attempt has its own current-state backup.

**This is not a power-loss-atomic boot transaction.** Individual replacements are
atomic, but mkinitcpio and signing write the primary image in place. SIGKILL, power
loss, storage failure, or a failure during restoration may require booting recovery
media and manually restoring the private backup. Runtime update hooks likewise do
not provide full transactional boot-image backup/rollback. Do not apply during a
package transaction or without a recovery boot option. Reboot and hardware suspend,
USB, temperature and keyboard-wake regression tests remain manual.

Before BIOS/EC updates, disable these overrides, rebuild/sign and boot a pristine
image. A build-time guard cannot remove AML already embedded in an old UKI.
