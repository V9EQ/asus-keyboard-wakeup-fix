# Tests

Run with the project's Python 3.13 environment:

```sh
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
uv build
```

No pytest dependency is needed. ACPICA (`iasl` and `acpiexec`) is required for the
AML-emulator test class; unittest explicitly reports that class as skipped if the
tools are missing. A skipped emulator class is not a full validation run.

## Coverage

- All 48 USB method guards; existing-method results and individually missing methods.
- I2C scopes absent/present, including identical scope bodies in different parents.
- Original missing-SEN2 failure, valid fallback policy and unchanged complete policy
  when the sensor exists. No fictitious sensors or changed thermal limits.
- Compilation and userspace evaluation of the actual GPE14 overlay for S0 only.
- Exact six-byte OEM identities, including NUL padding rather than assumed spaces.
- Unsupported model/BIOS/kernel/lockdown rejection, firmware fingerprints and manifests.
- Unsafe output paths/symlinks, already-patched inputs and overwritten workspaces.
- ACPI lengths/checksums, early CPIO bounds/paths/duplicates and unchanged microcode/main archive.
- PE certificate bounds, unsigned-copy isolation, initrd resize metadata and unchanged
  kernel, execution metadata and unrelated sections.
- Tampered artifacts, saved-base changes and the distinction between certificate
  presence and actual cryptographic signature verification.
- Safety checks remain active with optimized Python (`-O`).

Tests use synthetic ACPI/PE data in temporary directories. `acpiexec` runs in
userspace with initialization methods disabled; tests do not insert tables into the
kernel, change firmware, sign keys, suspend the laptop or edit its boot configuration.

## Validation performed on the development laptop

With Python 3.13.15 and ACPICA 20251212:

- **60 tests passed**, including the emulator class; no tests skipped.
- Ruff lint/format checks and source/wheel builds passed.
- A separate ignored `work/` integration run used the preserved original BIOS 312
  tables, generated and compiled all four payloads, and verified that every AML file
  was **byte-identical** to its previously hardware-tested counterpart.
- A new unsigned UKI was built from a preserved pristine base, then verified for
  early-archive contents, unchanged kernel/other sections and unchanged compressed
  main initramfs. It was **not installed or booted** by this validation run.

The firmware captures, compiled payloads, local integration outputs and logs remain
private and ignored by Git. They are not test fixtures distributed with the package.
Automated/offline success does not prove that future BIOS/kernel/driver combinations
will have correct suspend behavior; see [updates.md](updates.md).
