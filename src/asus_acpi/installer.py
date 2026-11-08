# SPDX-License-Identifier: MIT
"""Conservative transactional installer for the reviewed single-preset Arch layout.

No user-supplied scripts/bundle plans are executed as root. Files are rendered from
this package; only validated AML bytes enter from the prepared workspace.
"""

import fcntl
import json
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import require, uki
from .permanent import export_bundle
from .profile import check_machine, digest, save_json

STATE = "/var/lib/asus-acpi"
PRIMARY = "/boot/EFI/Linux/arch-linux.efi"
CERT = "/var/lib/sbctl/keys/db/db.pem"


def safe_path(root, name):
    """Reject symlinks in every component, including dangling destination links."""
    require(name.startswith("/") and ".." not in Path(name).parts, "Unsafe system path")
    path = Path(root)
    require(path.is_absolute() and path.resolve() == path, "Unsafe system root")
    for part in Path(name).parts[1:]:
        path /= part
        require(not path.is_symlink(), f"Symlink system path: {path}")
    return path


def regular(path):
    require(path.is_file() and not path.is_symlink(), f"Expected regular file: {path}")
    require(stat.S_ISREG(path.stat().st_mode), f"Not a regular file: {path}")


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".asus-acpi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot(root, names, directory):
    """Backup all bytes before mutation; manifests are committed last."""
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    records = {}
    for index, name in enumerate(sorted(names)):
        path = safe_path(root, name)
        if path.exists():
            regular(path)
            data = path.read_bytes()
            filename = str(index)
            atomic_write(directory / filename, data, 0o600)
            records[name] = {
                "backup": filename,
                "sha256": digest(data),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        else:
            records[name] = None
    save_json(directory / "manifest.json", records)
    return records


def restore(root, directory, records):
    # Validate the entire backup before starting restoration.
    data = {}
    for name, record in records.items():
        safe_path(root, name)
        if record is not None:
            payload = (directory / record["backup"]).read_bytes()
            require(digest(payload) == record["sha256"], f"Corrupt backup: {name}")
            data[name] = payload
    for name, record in records.items():
        path = safe_path(root, name)
        if record is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, data[name], record["mode"])


def validate_image(path, tables):
    image = uki.pe_image(path.read_bytes())
    require(image["certificate"] != (0, 0), "Rebuilt UKI has no certificate")
    entries, _, _, _ = uki.cpio_archive(image["sections"][".initrd"]["data"])
    actual = {
        name.removeprefix("kernel/firmware/acpi/"): data
        for name, data in entries.items()
        if name.startswith("kernel/firmware/acpi/") and data
    }
    require(actual == tables, "FIRST early archive does not contain exactly the reviewed AMLs")
    kernel = image["sections"][".uname"]["data"].rstrip(b"\0").decode()
    check_machine(build=True, kernel=kernel)


class Installer:
    """root/runner injection is internal, for sandbox tests; CLI always uses '/'."""

    def __init__(self, root="/", runner=None):
        self.root = Path(root)
        self.runner = runner or self.run
        self.state = safe_path(self.root, STATE)
        self.package_locked = False

    def path(self, name):
        return safe_path(self.root, name)

    @staticmethod
    def run(command):
        subprocess.run(command, check=True, env={"PATH": "/usr/bin:/usr/sbin", "LANG": "C"})

    def preflight(self):
        check_machine(build=True)
        require(
            self.root != Path("/") or self.path("/boot").is_mount(),
            "/boot must be mounted before installation",
        )
        require(
            self.package_locked or not self.path("/var/lib/pacman/db.lck").exists(),
            "Package transaction in progress; retry after pacman finishes",
        )
        for command in ("mkinitcpio", "sbctl", "sbverify", "flock"):
            path = self.path(f"/usr/bin/{command}")
            package = {"sbverify": "sbsigntools", "flock": "util-linux"}.get(command, command)
            require(path.exists(), f"Missing {command}; install the {package} system package")
            regular(path)
        for name in (
            PRIMARY,
            CERT,
            "/boot/vmlinuz-linux",
            "/etc/mkinitcpio.conf",
            "/etc/mkinitcpio.d/linux.preset",
            "/usr/lib/initcpio/install/acpi_override",
        ):
            regular(self.path(name))
        # Deliberately narrow: custom configs bypass mkinitcpio drop-ins. Do not
        # execute presets during planning (they are arbitrary shell programs).
        lines = [
            line.strip()
            for line in self.path("/etc/mkinitcpio.d/linux.preset").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        required = {
            'ALL_kver="/boot/vmlinuz-linux"',
            "PRESETS=('default')",
            f'default_uki="{PRIMARY}"',
        }
        allowed = required | {
            'default_options="--splash /usr/share/systemd/bootctl/splash-arch.bmp"',
            'fallback_uki="/boot/EFI/Linux/arch-linux-fallback.efi"',
        }
        require(
            required <= set(lines) <= allowed and len(lines) == len(set(lines)),
            "Unsupported linux.preset: requires the reviewed default UKI/drop-in layout",
        )
        require(
            sorted(p.name for p in self.path("/etc/mkinitcpio.d").glob("*.preset"))
            == ["linux.preset"],
            "Only the reviewed single linux preset is supported",
        )
        for name in (
            "/sys/module/asus_gpe_wake",
            "/sys/module/gpe14_wake_test",
            "/etc/systemd/system/asus-keyboard-wake.service",
            "/usr/lib/systemd/system/asus-keyboard-wake.service",
            "/var/lib/dkms/asus-gpe-wake",
        ):
            require(not self.path(name).exists(), f"Remove legacy DKMS installation first: {name}")
        # hook collects BOTH directories; never silently include unrelated AMLs.
        require(
            not list(self.path("/usr/lib/initcpio/acpi_override").glob("*.aml")),
            "Conflicting vendor ACPI overrides",
        )

    def plan(self, tables, info, adopt=False):
        self.preflight()
        require(not self.state.exists(), f"Existing installer state: {self.state}")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            plan = export_bundle(bundle, tables, info)
            files = {
                name: ((bundle / "root" / name.lstrip("/")).read_bytes(), int(meta["mode"], 8))
                for name, meta in plan["files"].items()
            }
        expected = {Path(name).name for name in files if name.endswith(".aml")}
        existing = list(self.path("/etc/initcpio/acpi_override").glob("*.aml"))
        require(all(p.name in expected for p in existing), "Unrelated/stale ACPI payloads present")
        adopted = []
        for name, (data, _) in files.items():
            path = self.path(name)
            if path.exists():
                regular(path)
                require(
                    adopt and path.read_bytes() == data,
                    f"Conflict: {name}; --adopt-existing permits ONLY byte-identical files",
                )
                adopted.append(name)
        return files, {
            "action": "install",
            "files": sorted(files),
            "adopted": adopted,
            "primary": PRIMARY,
            "backup": STATE,
            "applied": False,
        }

    @contextmanager
    def locked(self):
        require(os.geteuid() == 0, "--apply requires root; review dry-run first")
        lock = self.path("/run/asus-acpi.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Do not unlink a locked inode: waiting installers must share it.
            # Pacman honors this O_EXCL lock; prevent kernel updates during backup
            # and immediate rollback. Never remove a lock created by another process.
            package_lock = self.path("/var/lib/pacman/db.lck")
            package_fd = os.open(package_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(package_fd)
            self.package_locked = True
            try:
                yield
            finally:
                self.package_locked = False
                package_lock.unlink()

    def boot_files(self):
        boot = self.path("/boot")
        names = set()
        for path in boot.rglob("*"):
            require(not path.is_symlink(), f"Symlink in boot backup: {path}")
            if path.is_file():
                names.add("/" + str(path.relative_to(self.root)))
        return names

    def backup_names(self, managed):
        names = set(managed) | self.boot_files() | {"/var/lib/sbctl/files.json"}
        for name in (
            "/etc/mkinitcpio.conf",
            "/etc/mkinitcpio.d",
            "/etc/mkinitcpio.conf.d",
            "/etc/kernel",
            "/etc/pacman.d/hooks",
        ):
            path = self.path(name)
            if path.is_file():
                names.add(name)
            elif path.is_dir():
                for child in path.rglob("*"):
                    require(not child.is_symlink(), f"Symlink in config backup: {child}")
                    if child.is_file():
                        names.add("/" + str(child.relative_to(self.root)))
        return names

    def rebuild(self):
        self.runner(["/usr/bin/mkinitcpio", "-p", "linux"])
        self.runner(["/usr/bin/sbctl", "sign", PRIMARY])
        self.runner(["/usr/bin/sbverify", "--cert", CERT, PRIMARY])

    def install(self, tables, info, apply=False, adopt=False):
        if not apply:
            return self.plan(tables, info, adopt)[1]
        with self.locked():
            files, report = self.plan(tables, info, adopt)
            self.state.mkdir(mode=0o700, parents=True, exist_ok=False)
            # Full /boot includes cmdline secrets. State must never be public.
            names = self.backup_names(files)
            records = snapshot(self.root, names, self.state / "before")
            installed = {name: digest(data) for name, (data, _) in files.items()}
            atomic_write(
                self.state / "transaction.json",
                json.dumps(
                    {
                        "format": 1,
                        "status": "installing",
                        "installed": installed,
                    }
                ).encode(),
                0o600,
            )
            try:
                for name, (data, mode) in files.items():
                    atomic_write(self.path(name), data, mode)
                self.rebuild()
                validate_image(self.path(PRIMARY), tables)
                # Rebuild must not touch recovery images, menu, or signing registry.
                self.check_preserved(records, {PRIMARY} | set(files))
                atomic_write(
                    self.state / "transaction.json",
                    json.dumps(
                        {
                            "format": 1,
                            "status": "installed",
                            "installed": installed,
                        }
                    ).encode(),
                    0o600,
                )
            except BaseException:
                restore(self.root, self.state / "before", records)
                atomic_write(
                    self.state / "failed", b"Installation rolled back; backup retained.\n", 0o600
                )
                raise
            return report | {"applied": True}

    def check_preserved(self, records, mutable):
        for name, record in records.items():
            if name in mutable:
                continue
            path = self.path(name)
            require(
                (record is None and not path.exists())
                or (
                    record is not None
                    and path.is_file()
                    and digest(path.read_bytes()) == record["sha256"]
                ),
                f"Rebuild changed unrelated file: {name}",
            )

    def uninstall(self, apply=False):
        if apply:
            with self.locked():
                return self._uninstall(True)
        return self._uninstall(False)

    def _uninstall(self, apply):
        self.preflight()
        transaction = json.loads((self.state / "transaction.json").read_text())
        require(
            transaction.get("format") == 1 and transaction.get("status") == "installed",
            "Incomplete transaction: inspect private backup; do not retry blindly",
        )
        installed = transaction["installed"]
        expected_aml = {Path(name).name for name in installed if name.endswith(".aml")}
        require(
            all(
                path.name in expected_aml
                for path in self.path("/etc/initcpio/acpi_override").glob("*.aml")
            ),
            "Unrelated ACPI payload added since installation; review before uninstall",
        )
        before = json.loads((self.state / "before/manifest.json").read_text())
        for name, checksum in installed.items():
            require(
                digest(self.path(name).read_bytes()) == checksum, f"Managed file changed: {name}"
            )
        report = {
            "action": "uninstall",
            "files": sorted(installed),
            "applied": False,
            "note": "Adopted files are restored, not deleted; current kernels are rebuilt.",
        }
        if not apply:
            return report
        # CURRENT boot images, never stale images from the installation date.
        names = self.backup_names(installed)
        current_dir = Path(tempfile.mkdtemp(prefix="uninstall-", dir=self.state)) / "before"
        current = snapshot(self.root, names, current_dir)
        try:
            restore(self.root, self.state / "before", {name: before[name] for name in installed})
            self.rebuild()
            tables = {}
            for base in ("/etc/initcpio/acpi_override", "/usr/lib/initcpio/acpi_override"):
                for path in self.path(base).glob("*.aml"):
                    tables[path.name] = path.read_bytes()
            validate_image(self.path(PRIMARY), tables)
            self.check_preserved(current, {PRIMARY} | set(installed))
            atomic_write(
                self.state / "transaction.json",
                json.dumps(transaction | {"status": "uninstalled"}).encode(),
                0o600,
            )
        except BaseException:
            restore(self.root, current_dir, current)
            raise
        return report | {"applied": True}
