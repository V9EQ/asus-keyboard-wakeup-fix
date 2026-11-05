# ASUS TUF F16 keyboard wake workaround

A small, model-specific Linux kernel module that enables wakeup through **ACPI GPE `0x14`** on the **ASUS TUF Gaming F16 FX608JPR**.

On the tested laptop, the built-in keyboard could not wake the machine from suspend even though its `power/wakeup` setting was `enabled`. A USB mouse and the touchpad could wake it. Enabling the GPE14 wake mask restored keyboard wakeup.

**This is a local workaround, not an upstream fix or a general solution for ASUS laptops.** Do not change the model guard or GPE number without inspecting the target machine's ACPI tables.

## Tested configuration

| Component | Tested value |
| --- | --- |
| Manufacturer | ASUSTeK COMPUTER INC. |
| DMI product name | ASUS TUF Gaming F16 FX608JPR_FX608JPR |
| BIOS | FX608JPR.312 |
| Distribution | Arch Linux |
| Kernel | 7.2.14-arch1-1, x86_64 |
| Suspend mode | `s2idle` |
| Built-in keyboard | i8042 / serio0, IRQ 1 |

Validation was performed on one machine. Other BIOS versions, kernels, suspend modes, and long-term battery behavior have not been validated. Keyboard wakeup was tested successfully; power-button and lid wakeup are not claimed to be fixed.

## What it does

The inspected DSDT routes an embedded-controller event path through `_GPE._L14` to `ECLV()`. The active lid and keyboard devices do not declare `_PRW`. Tracing showed that i8042 requests IRQ wake configuration, but internal input events were handled only after another device initiated resume.

These observations alone do not establish the full firmware-level cause. The decisive test was enabling the GPE14 wake mask: keyboard wakeup immediately started working. The original GPE status reported wake disabled (`0x19`); after enabling it, the status was `0x1b`.

The module:

1. Checks the exact manufacturer and product name.
2. Saves the existing GPE14 wake-mask state.
3. Marks GPE14 wake-capable and enables its wake mask using exported ACPI APIs.
4. Restores the original wake-mask state when unloaded.

It does **not** modify the BIOS, DSDT/SSDT, keyboard driver, IRQ handlers, runtime GPE enable state, kernel command line, or initramfs. A systemd service loads the module at boot. DKMS rebuilds it for kernel updates when matching headers are available.

## Risks and limitations

- GPE14 carries more than keyboard events. Other EC events may cause unwanted wakeups and increased battery drain. Test before leaving the laptop suspended in a bag.
- This is an out-of-tree kernel module running with kernel privileges. It has no stability guarantee. Review the source before installing.
- Screen-lock and authentication settings are not changed.
- Unloading restores the wake mask, but the ACPI wake-capability flag remains in memory until reboot. The out-of-tree kernel taint flag also remains until reboot. Disable the service and reboot for a fully clean kernel state.
- The saved mask is a load-time snapshot. Avoid other software independently changing the same GPE wake mask while the module is loaded.
- A BIOS update may change the event routing or fix the issue. Disable this workaround and retest after updating firmware.
- Secure Boot/module-signature enforcement may reject the module. Use your distribution's trusted module-signing procedure; disabling Secure Boot is not required or recommended by this project.
- DKMS rebuild failures or missing headers can leave the workaround unavailable after a kernel update. Inspect package-manager output and `dkms status`.

## Requirements

Arch Linux, systemd, DKMS, a compiler/build toolchain, and headers matching the **running** kernel. Polkit (`pkexec`) provides the graphical authorization prompt.

For the standard Arch kernel, the packages are `base-devel`, `dkms`, `linux-headers`, and `polkit`. Other kernels need their corresponding header packages. After a kernel upgrade, reboot into the matching kernel before installing.

## Install

From the repository directory:

```sh
pkexec /usr/bin/bash "$(pwd)/install.sh"
```

The installer checks the model, builds and installs the DKMS module, then enables `asus-keyboard-wake.service`. It refuses to overwrite an existing installation or replace a loaded test module. On installation errors it attempts to roll back the files it created; review any rollback errors.

Installed files:

- `/usr/src/asus-gpe-wake-1.0/` — module source, Makefile, DKMS configuration
- `/var/lib/dkms/asus-gpe-wake/1.0/` — DKMS registration/build state
- `/usr/lib/modules/<kernel>/updates/dkms/asus_gpe_wake.ko*` — built module
- `/etc/systemd/system/asus-keyboard-wake.service` — boot-time loading and unloading

## Disable immediately and on future boots

```sh
pkexec /usr/bin/systemctl disable --now asus-keyboard-wake.service
```

This stops the service, unloads the module, and restores the original wake mask. Check the command's exit status; if unloading fails, inspect the journal. Reboot after disabling for a fully clean kernel state.

To enable it again:

```sh
pkexec /usr/bin/systemctl enable --now asus-keyboard-wake.service
```

## Verify

```sh
systemctl status asus-keyboard-wake.service
lsmod | grep asus_gpe_wake
dkms status -m asus-gpe-wake
journalctl -b -k -g asus_gpe_wake
```

Save your work, suspend normally, wait for the laptop to settle, then press Shift. Check that suspend lasts as expected without touching any input devices; successful keyboard wakeup alone does not rule out unwanted wakeups.

## Uninstall

Run these commands **in order**, stopping if a command fails:

```sh
pkexec /usr/bin/systemctl disable --now asus-keyboard-wake.service
pkexec /usr/bin/dkms remove -m asus-gpe-wake -v 1.0 --all
pkexec /usr/bin/rm -- /etc/systemd/system/asus-keyboard-wake.service
pkexec /usr/bin/rm -r -- /usr/src/asus-gpe-wake-1.0
pkexec /usr/bin/systemctl daemon-reload
```

Then reboot. Do not remove source files belonging to a different package/version. Reinstalling this version requires removing the previous installation first; the installer does not perform in-place upgrades.

## Files

- `asus_gpe_wake.c` — model guard and ACPI wake-mask handling
- `Makefile`, `dkms.conf` — kernel/DKMS build configuration
- `asus-keyboard-wake.service` — reversible service-based loading
- `install.sh` — location-independent installer

No firmware dumps, keypress data, or diagnostic logs are included.

## References

- [Linux device power management](https://docs.kernel.org/driver-api/pm/devices.html)
- [ArchWiki: Wakeup triggers](https://wiki.archlinux.org/title/Power_management/Wakeup_triggers)
- [Related ASUS FX507 report](https://discuss.cachyos.org/t/asus-tuf-gaming-f15-fx507zi4-s2idle-wake-sources-fixed-by-dsdt-override/35463) — a different model using GPE `0x02`; **do not copy that GPE number onto this machine**.

## License

GPL-2.0-only. See [LICENSE](LICENSE).
