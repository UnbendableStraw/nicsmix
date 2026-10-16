#!/usr/bin/env bash
# apply-overlay-linux.sh — the loop-mount way (Linux, root) to apply overlay/ onto both rootfs slots.
# build-troubadour.sh (debugfs, no root, macOS too) is the maintained path; this one also writes slot B (p3).
#
#   sudo scripts/apply-overlay.sh <image.sdcard> [overlay_dir]
#
# <image.sdcard> is a *decompressed* stock image (xz -dc image.sdcard.lzma > image.sdcard).
# This loop-mounts partition 2 (slot A) and partition 3 (slot B), copies the
# overlay into each, sets the root password + sshd config, masks otamanager, then
# unmounts. Re-compress the result and flash it (see README).
#
# Linux only (needs loop mounts). On macOS, apply the overlay live over SSH to an
# already-modded Cell instead (see README "Applying the overlay live").
#
# Partition offsets (from the known layout): p2 @ 288 MiB, p3 @ ~2.3 GiB. We read
# them from the partition table rather than hard-coding, via `sfdisk`/`partx`.
set -euo pipefail

IMG="${1:?usage: apply-overlay.sh <image.sdcard> [overlay_dir]}"
OVL="${2:-overlay}"
ROOT_PW="${ROOT_PW:-SyngCell.dev}"

[ -f "$IMG" ] || { echo "image not found: $IMG" >&2; exit 1; }
[ -d "$OVL" ] || { echo "overlay dir not found: $OVL" >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root (loop mounts)" >&2; exit 1; }
command -v partx >/dev/null || { echo "need util-linux 'partx'" >&2; exit 1; }

LOOP="$(losetup --find --show --partscan "$IMG")"
echo "loop: $LOOP"
cleanup(){ set +e; for m in "$MNT2" "$MNT3"; do [ -n "${m:-}" ] && umount "$m" 2>/dev/null; done
           losetup -d "$LOOP" 2>/dev/null; rmdir "${MNT2:-}" "${MNT3:-}" 2>/dev/null; }
trap cleanup EXIT
sleep 1   # let partscan settle

MNT2="$(mktemp -d)"; MNT3="$(mktemp -d)"
apply_to(){ # $1 = mountpoint
  local m="$1"
  echo ">> applying overlay to $m"
  cp -a "$OVL"/. "$m"/
  # root password (SHA-512)
  local hash; hash="$(openssl passwd -6 "$ROOT_PW")"
  if [ -f "$m/etc/shadow" ]; then
    sed -i "s|^root:[^:]*:|root:${hash//|/\\|}:|" "$m/etc/shadow"
  fi
  # sshd: allow root + password + the authorized_keys.d path
  local cfg="$m/etc/ssh/sshd_config"
  if [ -f "$cfg" ]; then
    sed -i -E 's|^#?\s*PermitRootLogin.*|PermitRootLogin yes|; s|^#?\s*PasswordAuthentication.*|PasswordAuthentication yes|; s|^#?\s*PubkeyAuthentication.*|PubkeyAuthentication yes|' "$cfg"
    grep -q 'authorized_keys.d' "$cfg" || echo 'AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys.d/%u' >> "$cfg"
  fi
  # mask OTA so a cloud update can't revert the mods
  ln -sf /dev/null "$m/etc/systemd/system/otamanager.service"
  # enable our services
  mkdir -p "$m/etc/systemd/system/multi-user.target.wants"
  for u in syng-agent.service syng-led-reactive.service; do
    [ -f "$m/etc/systemd/system/$u" ] && ln -sf "/etc/systemd/system/$u" "$m/etc/systemd/system/multi-user.target.wants/$u"
  done
  sync
}

# mount p2 and p3 (the two rootfs slots)
mount "${LOOP}p2" "$MNT2"; apply_to "$MNT2"; umount "$MNT2"
mount "${LOOP}p3" "$MNT3"; apply_to "$MNT3"; umount "$MNT3"

echo
echo "done. Recompress and flash:"
echo "  xz -z -c '$IMG' > firmware/image.sdcard.lzma"
echo "  (cd firmware && sudo ./flash.sh)"
