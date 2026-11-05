#!/usr/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
dest=/usr/src/asus-gpe-wake-1.0
unit=/etc/systemd/system/asus-keyboard-wake.service
kernel=$(uname -r)

if [[ $EUID != 0 ]]; then
    echo "Run with: pkexec /usr/bin/bash \"$src/install.sh\"" >&2
    exit 1
fi
for command in dkms make gcc install systemctl modprobe; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
if [[ $(</sys/class/dmi/id/sys_vendor) != 'ASUSTeK COMPUTER INC.' ||
      $(</sys/class/dmi/id/product_name) != 'ASUS TUF Gaming F16 FX608JPR_FX608JPR' ]]; then
    echo 'Unsupported model; refusing installation.' >&2
    exit 1
fi
if [[ ! -d /usr/lib/modules/$kernel/build ]]; then
    echo "Install the headers matching the running kernel ($kernel) first." >&2
    exit 1
fi
for path in "$dest" "$unit" /var/lib/dkms/asus-gpe-wake/1.0 \
            /etc/systemd/system/multi-user.target.wants/asus-keyboard-wake.service \
            /usr/lib/systemd/system/asus-keyboard-wake.service \
            /sys/module/asus_gpe_wake /sys/module/gpe14_wake_test; do
    if [[ -e $path || -L $path ]]; then
        echo "Existing installation or test module: $path" >&2
        echo 'Remove the existing installation/test module before reinstalling.' >&2
        exit 1
    fi
done
for file in asus_gpe_wake.c Makefile dkms.conf asus-keyboard-wake.service; do
    [[ -f $src/$file ]] || { echo "Missing source file: $file" >&2; exit 1; }
done

source_created=0
unit_created=0
rollback() {
    local result=$?
    trap - EXIT
    if (( result != 0 )); then
        echo 'Installation failed; rolling back this installation.' >&2
        if (( unit_created )); then
            if ! systemctl disable --now asus-keyboard-wake.service; then
                echo 'Could not stop the service; retaining files for manual recovery.' >&2
                exit "$result"
            fi
            rm -f -- "$unit"
            systemctl daemon-reload || true
        fi
        if [[ -d /var/lib/dkms/asus-gpe-wake/1.0 ]]; then
            if ! dkms remove -m asus-gpe-wake -v 1.0 --all; then
                echo 'DKMS cleanup failed; retaining source files for recovery.' >&2
                exit "$result"
            fi
        fi
        if (( source_created )); then rm -rf -- "$dest"; fi
    fi
    exit "$result"
}
trap rollback EXIT

install -d -m 755 "$dest"
source_created=1
install -m 644 "$src/asus_gpe_wake.c" "$src/Makefile" "$src/dkms.conf" "$dest/"
dkms add -m asus-gpe-wake -v 1.0
dkms build -m asus-gpe-wake -v 1.0 -k "$kernel"
dkms install -m asus-gpe-wake -v 1.0 -k "$kernel"
unit_created=1
install -m 644 "$src/asus-keyboard-wake.service" "$unit"
systemctl daemon-reload
systemctl enable --now asus-keyboard-wake.service
echo 'Installed and enabled. Disable with:'
echo '  pkexec /usr/bin/systemctl disable --now asus-keyboard-wake.service'
