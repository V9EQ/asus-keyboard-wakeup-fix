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
- Unsupported model/BIOS/lockdown rejection, firmware fingerprints and manifests.
- Candidate-kernel configuration checks instead of version allowlists, including
  synthetic future releases, missing headers, compressed configs and incompatible policies.
- Export-only permanent bundles, permissions/hashes, native Bash guard behavior,
  BIOS changes, tampered/extra payloads and idempotent preservation of HOOKS.
- Installer dry-run/root authorization, strict preset layout, exact-match adoption,
  conflicts, symlink rejection and pacman locking in temporary sandbox roots.
- Failure injection for writes, rebuild, signing and signature verification;
  private checksummed backups, exact first-archive validation, unrelated-file
  preservation, changed-managed-file refusal, and current-kernel uninstall/rollback.
- Unsafe output paths/symlinks, already-patched inputs and overwritten workspaces.
- ACPI lengths/checksums, early CPIO bounds/paths/duplicates and unchanged microcode/main archive.
- PE certificate bounds, unsigned-copy isolation, initrd resize metadata and unchanged
  kernel, execution metadata and unrelated sections.
- Tampered artifacts, saved-base changes and the distinction between certificate
  presence and actual cryptographic signature verification.
- Safety checks remain active with optimized Python (`-O`).

Tests use synthetic ACPI/PE data in temporary directories. `acpiexec` runs in
userspace with initialization methods disabled; tests do not insert tables into the
kernel, change firmware, sign real boot images, suspend the laptop or edit its boot
configuration. Installer commands are mocked against temporary sandbox roots;
privileged live installation/signing is not established by these tests.

## Validation performed on the development laptop

With Python 3.13.15 and ACPICA 20251212:

- **106 tests passed**, including emulator, native Bash drop-in and sandboxed
  installer/failure cases; no tests skipped.
- Ruff lint/format checks and source/wheel builds passed.
- A separate ignored `work/` integration run used the preserved original BIOS 312
  tables, generated and compiled all four payloads, and verified that every AML file
  was **byte-identical** to its previously hardware-tested counterpart.
- The 0.4 audit repeated preparation using the original keyboard-test capture in
  `Downloads`, and all four payloads matched the later manual permanent installation
  byte-for-byte. A new export and unsigned UKI were generated in ignored `work/`.
- A new unsigned UKI was built from a preserved pristine base, then verified for
  early-archive contents, unchanged kernel/other sections and unchanged compressed
  main initramfs. It was **not installed or booted** by this validation run.
- Live installer dry-run stopped safely at missing `sbverify` (`sbsigntools`). The
  existing differing manual installation was left untouched; no root apply,
  real signing, reboot or hardware suspend cycle was performed.

The firmware captures, compiled payloads, local integration outputs and logs remain
private and ignored by Git. They are not test fixtures distributed with the package.
Automated/offline success does not prove that future BIOS/kernel/driver combinations
will have correct suspend behavior; see [updates.md](updates.md).
