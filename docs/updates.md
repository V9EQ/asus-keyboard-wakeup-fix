# Updates and version compatibility

## Two separate lifetimes

1. **AML fixes** describe the firmware/ACPI namespace. They are not Linux kernel
   modules and do not have the DKMS/kernel-module ABI dependency of `old/`.
   Their main dependency is the exact laptop, BIOS tables and observed wake path.
   A new kernel can still change ACPICA/driver behavior, so compatibility must be tested.
2. **A UKI** embeds a particular kernel and initramfs. The initramfs can contain
   NVIDIA and other kernel modules. Our separate test UKI is a frozen artifact,
   not a pointer to whatever kernel pacman has most recently installed.

The Python project runs during preparation/build/verification, **not on every boot**.
At boot, Linux reads AML already embedded in the UKI. Python/uv need not be running.

## Current implementation is intentionally pinned

`src/asus_acpi/profile.py` currently accepts:

- ASUS TUF Gaming F16 FX608JPR_FX608JPR;
- BIOS FX608JPR.312;
- exact fingerprints of the investigated original DSDT/three SSDTs;
- running and embedded kernel `7.2.14-arch1-1`;
- `lockdown=none` for building an image intended for initrd overrides.

The kernel pin is a conservative **tested-profile guard**, not proof that the AML
intrinsically works only with 7.2.14. Do not simply remove it to make an update pass.
Review compatibility, test on the new version, then deliberately update the profile.
There is no automatic firmware flashing, security disabling or generic force switch.

NVIDIA and Limine versions are not hard-coded as platform guards. That does not make
an old embedded driver compatible with every future userspace driver/bootloader.

## What to do after updates

| Update | What happens / required action |
| --- | --- |
| `linux` | The separate test UKI keeps its old kernel/initramfs. Revalidate on the new kernel and rebuild from a **fresh pristine UKI** for that kernel, then sign and test the new result. The current profile intentionally rejects a new kernel until reviewed. |
| `nvidia-open`, `nvidia-utils`, NVIDIA DKMS packages | Rebuild the base initramfs/UKI when the NVIDIA module is embedded, then rebuild/sign the ACPI test UKI. An old embedded kernel module can mismatch updated NVIDIA userspace even if the Linux release has not changed. |
| `limine` | Usually no AML rebuild is needed. Keep the menu entry pointing at the signed UKI and ensure the updated Limine EFI binary is signed/trusted. Loader regressions/configuration changes still need checking. |
| `mkinitcpio`, systemd-stub, microcode, early firmware | Rebuild the base UKI when appropriate; the frozen custom test image does not inherit those updates. Rebuild/sign the derived image to incorporate them. |
| BIOS / EC firmware | Stop using old overrides until the new tables and wake path are investigated. New OEM revisions, device names, GPE routing or vendor fixes may invalidate or make the overrides unnecessary. |
| Secure Boot keys or lockdown policy | Recheck image signing and override policy. A valid UKI signature does not bypass Linux lockdown restrictions on ACPI overrides. |

Booting the old UKI after a Linux update may also leave no matching modules in
`/usr/lib/modules/<old-version>`: a new Arch kernel package normally replaces that
module tree. The initramfs contains only a subset of all modules. An old image can
still start but is not a supported way to keep the whole installation on an old kernel.

**Re-running `build` against the same old base is not an update.** This project
preserves the base kernel and compressed main initramfs byte-for-byte. It does not
rebuild drivers, run mkinitcpio or fetch current modules itself.

## Current laptop's hooks versus the custom test

The inspected system has a default mkinitcpio preset producing
`/boot/EFI/Linux/arch-linux.efi`. Its separate
`/boot/EFI/Linux/arch-linux-acpi-final-test.efi` is not in that preset and is not
registered for automatic regeneration by this project.

The installed pacman hooks can rebuild normal initcpios and run `sbctl sign-all -g`.
**Signing is not rebuilding**: re-signing an old test UKI does not update its kernel,
NVIDIA module, initramfs, or AML payloads. Do not assume an update rebuilt a particular
image without checking the transaction log and output.

The inspected custom Limine update hook copies its EFI executables, not its menu
configuration. Thus a normal Limine package update should not itself remove the
custom menu entry. That is a statement about those inspected hooks, not a guarantee
about every future package/hook change or Secure Boot key configuration.

## Future permanent integration (not installed by this project)

After validation, a less manual approach is to use mkinitcpio's existing
`acpi_override` hook:

1. Preserve an independent known-good boot/recovery path and backups.
2. Put the reviewed AML payloads in `/etc/initcpio/acpi_override/`.
3. Add `acpi_override` to the intended mkinitcpio configuration.
4. Rebuild and sign the normal UKI; test it before making it the default.
5. Ensure relevant Linux/NVIDIA transactions really rebuild that image and sign it.

Then ordinary kernel/initramfs rebuilds include the existing AML files automatically;
Python does not need to regenerate firmware patches at each boot or ordinary update.
Kernel updates still require regression testing, and BIOS/table changes still require
re-investigation. This integration has **not** been enabled: the tool currently only
builds isolated unsigned artifacts in a workspace.

Keep TPM/PCR sealing policies in mind as well: changing a UKI changes measurements.
This builder does not regenerate TPM sealing policies or promise compatibility with
arbitrary pre-existing PCR-signed UKIs.
