# ASUS FX608JPR ACPI fixes

Model-specific Linux ACPI workarounds for **ASUS TUF Gaming F16 FX608JPR, BIOS 312**.
This is an experimental local workaround, not an upstream firmware fix.

## Project versions

- **`main`**: Python 3.13 tooling for the ACPI table overrides tested on this laptop.
- **`legacy-dkms` branch**: the archived DKMS/kernel-module implementation and its history;
  it is not included in `main`.

The legacy installer is **not** part of the new implementation. Do not run it alongside
these overrides. Offline commands do not change the installed system; privileged
`install/uninstall --apply` are the explicit exception.

## Current implementation

- USB `_UPC`/`_PLD` duplicate-object guards.
- Guards for absent I²C `TPL1` scopes.
- A conditional PSVT policy that does not reference absent `SEN2` and preserves the
  original thermal limits and relationships for present devices.
- Optional GPE14 wake declaration for the active lid/shared EC event, enabling
  built-in keyboard wake from `s2idle` on the tested laptop.
- Offline construction of a **separate unsigned UKI**, preserving the kernel,
  other UKI sections and compressed main initramfs.
- Kernel **capability checks**, not a Linux release-number allowlist.
- Export of permanent mkinitcpio drop-in, AML checksums and NVIDIA update hook.
- Explicit automatic installation/uninstallation for the reviewed Arch layout,
  dry-run by default, with conflict checks, private backups and failure rollback.

The implementation rejects other models, BIOS/table fingerprints, incompatible kernels,
already-patched input tables and ambiguous source layouts. It does not flash BIOS,
change Secure Boot keys, install a custom kernel module or modify Limine.
Only explicit privileged installation/uninstallation rebuilds and signs the primary UKI.

## Development environment

Initialized with:

```sh
uv init --python 3.13
```

Python is pinned in `.python-version`; Ruff targets `py313` with a 99-column line length.
The package uses a `src/` layout and the `uv_build` backend.

```sh
uv sync
uv run asus-acpi --help
uv run asus-acpi inspect
```

ACPICA (`iasl`, `acpiexec`) and binutils (`objcopy`) are system tools, not Python dependencies.

## Intended workflow

Capture tables from an **unmodified boot**, then prepare and build in new workspace directories:

```sh
# Reading firmware tables normally requires administrator permissions.
# Use a preserved original capture if the current boot already has overrides.
uv run asus-acpi prepare --tables /path/to/original-tables --output work/prepared
uv run asus-acpi build --prepared work/prepared --base /path/to/pristine.efi --output work/uki
uv run asus-acpi verify --prepared work/prepared --build work/uki
```

`prepare --without-wake` excludes GPE14 for regression isolation.
Generated dumps, diffs, AMLs, UKIs and signing keys must not be committed.
The output UKI is **unsigned**; testing it requires a separately reviewed signing,
backup and non-default boot-entry procedure. Never overwrite the only working boot image.

## Permanent integration export

```sh
uv run asus-acpi export-mkinitcpio --prepared work/prepared --output work/permanent
```

The bundle mirrors proposed system files under `root/`, with a checksummed plan and
`INSTALL.txt`. It does not copy them to `/`, edit the boot menu or touch an existing
manual installation. Its build-time guard checks model/BIOS, AML integrity, lockdown
and the **kernel being built**, and adds `acpi_override` only once. Python/uv is not
needed by the exported update hooks.

Read [installation requirements and recovery limits](docs/permanent.md) before use.

```sh
uv run asus-acpi install --prepared work/prepared --dry-run
# Explicit application from a trusted reviewed checkout/environment:
sudo -- "$PWD/.venv/bin/asus-acpi" install --prepared "$PWD/work/prepared" --apply
uv run asus-acpi uninstall --dry-run
```

Requires the reviewed single-linux-preset layout, existing sbctl keys, and
`sbsigntools` (`sbverify`). Existing differing manual files are never overwritten;
`--adopt-existing` accepts only byte-identical destinations. No boot-menu changes
or reboot are performed.

## Safety and known limits

The table approach was tested on one laptop with Arch Linux, kernel `7.2.14-arch1-1`,
mkinitcpio 42.2, Limine 12.9.6 and Secure Boot enabled with kernel lockdown disabled.
The Python 3.13 project passes **106 automated tests**, including userspace ACPICA
emulator tests. An offline integration run reproduced all four hardware-tested AML
payloads byte-for-byte and built/verified a separate unsigned UKI. That newly packaged
UKI was not installed or booted by the test run. See [test coverage](docs/testing.md).

GPE14 is shared with EC/lid events: it may cause spurious wakeups or sleep regressions.
Test on a desk, not in a bag. A separate custom lid-suspend service and USB mouse wake
rule used on the original system are **not** included here. GNOME's external-monitor
lid behavior is not changed by this project alone.

NVIDIA ACPI notifier errors, `spd5118` resume errors and incorrect lid-state reports
were observed separately; the table patches do not claim to fix them.
After BIOS/kernel updates, re-investigate rather than bypassing the safety checks.

## Tests

```sh
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
uv build
```

## Updates

**Do not treat a test UKI as automatically up-to-date.** It freezes the base kernel
and initramfs, including any embedded NVIDIA module. Linux/NVIDIA updates require a
fresh base and a rebuilt, signed test image. Kernel release numbers are no longer
hard-pinned: a readable matching kernel configuration and required capabilities are
checked instead. This is not a guarantee against future driver regressions. Limine
updates normally do not require regenerating AML. BIOS changes need re-investigation.

The project is not run on each boot. Explicit permanent installation configures
inclusion in normal mkinitcpio rebuilds and an NVIDIA update hook. The development
machine's prior manual setup was not changed by this work. See the detailed
[update and compatibility guide](docs/updates.md).

## License and references

New tooling and AML source: [MIT](LICENSE). The archived module retains its original
MIT/GPL alternative and notices in the `legacy-dkms` branch.

- [Kernel: ACPI table upgrades via initrd](https://docs.kernel.org/admin-guide/acpi/initrd_table_override.html)
- [Kernel: SSDT overlays](https://docs.kernel.org/admin-guide/acpi/ssdt-overlays.html)
- [ArchWiki: DSDT](https://wiki.archlinux.org/title/DSDT)
