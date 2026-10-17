#!/usr/bin/env bash
# build-troubadour.sh — stamp the SyngOS Troubadour overlay onto a stock Syng Cell image.
#
#   ./tools/fw/build-troubadour.sh <stock image.sdcard[.lzma]> [out-dir]
#
# Produces <out-dir>/image.sdcard (+ .lzma), imx-boot and flash.sh — `cd <out-dir> && ./flash.sh` (uuu). Works on Linux and
# macOS (brew install e2fsprogs xz); no root, no loop devices: the rootfs partition is edited in
# place with debugfs -w. What it does, in order:
#   1. decompress the stock image if needed
#   2. copy every file under overlay/ into partition 2 (the rootfs), creating directories, setting
#      modes (+x for bin/ and *.sh/*.py), keeping symlinks (etc/systemd/system/*.wants/*)
#   3. sshd: root login + password + pubkey; AuthorizedKeysFile also reads /etc/ssh/authorized_keys.d/<user>
#   4. root password from $ROOT_PASSWORD (default SyngCell.dev — change it on first login!)
#   5. your SSH public key(s) from $SSH_PUBKEY or ~/.ssh/id_ed25519.pub into /etc/ssh/authorized_keys.d/root
#   6. mask otamanager (no cloud OTA can revert the mods); enable syng-agent + syng-cloud-block units
#   8. troubadour-rootfs-<artifact>.img.gz: the rootfs alone, for in-place updates from the setup page (OTA=0 skips it)
#   7. connman: USB gadget interfaces blacklisted (technology order stays stock: wifi first — celldirector treats a
#      Wi-Fi service that is not the default route as "disconnected" and drops into Wi-Fi repair mode)
# Nothing else in the image is touched — see docs/FIRMWARE-2.2.2.md for what Syng changed upstream.
set -euo pipefail
SRC="${1:?usage: $0 <image.sdcard[.lzma]> [out-dir]}"
OUT="${2:-build}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"          # repo root
OVERLAY="$HERE/overlay"
ROOTFS_OFFSET="${ROOTFS_OFFSET:-301989888}"                               # partition 2 (see fdisk -l image.sdcard)
ROOT_PASSWORD="${ROOT_PASSWORD:-SyngCell.dev}"
DEBUGFS="${DEBUGFS:-$(command -v debugfs || echo /usr/local/opt/e2fsprogs/sbin/debugfs)}"
[ -x "$DEBUGFS" ] || { echo "debugfs not found (brew install e2fsprogs / apt install e2fsprogs)"; exit 1; }
mkdir -p "$OUT"
IMG="$OUT/image.sdcard"
case "$SRC" in
  *.lzma|*.xz) echo "decompressing $SRC -> $IMG"; xz -dc "$SRC" > "$IMG";;
  *) [ "$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")" = "$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")" ] || cp "$SRC" "$IMG";;
esac
FS="$IMG?offset=$ROOTFS_OFFSET"
dbg() { "$DEBUGFS" -w -R "$1" "$FS" 2>&1 | grep -v "^debugfs [0-9]" || true; }
dbgq() { "$DEBUGFS" -R "$1" "$FS" 2>/dev/null; }
echo "stock version: $(dbgq 'cat /etc/version') / $(dbgq 'cat /etc/mender/artifact_info')"

TMP="${KEEP_TMP:-$(mktemp -d)}"; [ -n "${KEEP_TMP:-}" ] && mkdir -p "$TMP" || trap 'rm -rf "$TMP"' EXIT
CMDS="$TMP/cmds"; : > "$CMDS"
HAVE=" "
mkdirp() {  # debugfs has no mkdir -p, and `mkdir` of an existing name leaves an orphaned inode behind, so only create what is missing
  local p="" part; local IFS=/
  for part in $1; do
    [ -n "$part" ] || continue; p="$p/$part"
    case "$HAVE" in *" $p "*) continue;; esac
    if dbgq "stat $p" | grep -q "^Inode:"; then HAVE="$HAVE$p "; continue; fi
    echo "mkdir $p" >> "$CMDS"; HAVE="$HAVE$p "
  done
}
put() {  # put <local file> <abs path in image> [mode]
  local mode="${3:-}"
  mkdirp "$(dirname "$2")"
  { echo "rm $2"; echo "write $1 $2"; [ -n "$mode" ] && echo "sif $2 mode $mode"; echo "sif $2 uid 0"; echo "sif $2 gid 0"; } >> "$CMDS"
}
link() { mkdirp "$(dirname "$1")"; { echo "rm $1"; echo "symlink $1 $2"; } >> "$CMDS"; }

# 2. the overlay
while IFS= read -r -d '' f; do
  rel="${f#$OVERLAY}"
  case "$rel" in */.DS_Store|*/README*|*/.gitkeep) continue;; esac
  if [ -L "$f" ]; then link "$rel" "$(readlink "$f")"; continue; fi
  mode=0100644
  case "$rel" in /usr/bin/*|/usr/sbin/*|/bin/*|/sbin/*|*.sh|*.py) mode=0100755;; esac
  case "$rel" in /etc/shadow) mode=0100400;; /etc/ssh/authorized_keys.d/root) continue;; esac
  put "$f" "$rel" "$mode"
done < <(find "$OVERLAY" \( -type f -o -type l \) -print0)

# 3. sshd_config: start from the stock file so upstream changes are kept
dbgq "cat /etc/ssh/sshd_config" > "$TMP/sshd_config"
python3 - "$TMP/sshd_config" <<'EOF'
import sys, re
p = sys.argv[1]; s = open(p).read()
def setopt(s, key, val):
    pat = re.compile(r"^#?\s*%s\b.*$" % re.escape(key), re.M)
    return pat.sub("%s %s" % (key, val), s, count=1) if pat.search(s) else s + "\n%s %s\n" % (key, val)
for k, v in (("PermitRootLogin", "yes"), ("PasswordAuthentication", "yes"), ("PubkeyAuthentication", "yes"),
             ("PermitEmptyPasswords", "no"), ("AuthorizedKeysFile", ".ssh/authorized_keys /etc/ssh/authorized_keys.d/%u")):
    s = setopt(s, k, v)
open(p, "w").write(s)
EOF
put "$TMP/sshd_config" /etc/ssh/sshd_config 0100644

# 4. root password
dbgq "cat /etc/shadow" > "$TMP/shadow"
HASH="$(python3 -c 'import crypt,sys; print(crypt.crypt(sys.argv[1], crypt.mksalt(crypt.METHOD_SHA512)))' "$ROOT_PASSWORD" 2>/dev/null || openssl passwd -6 "$ROOT_PASSWORD")"
python3 - "$TMP/shadow" "$HASH" <<'EOF'
import sys
p, h = sys.argv[1], sys.argv[2]
lines = open(p).read().splitlines()
out = []
for l in lines:
    f = l.split(":")
    if f[0] == "root": f[1] = h; l = ":".join(f)
    out.append(l)
open(p, "w").write("\n".join(out) + "\n")
EOF
put "$TMP/shadow" /etc/shadow 0100400

# 5. authorized keys
KEYS="$TMP/authorized_keys"
{ echo "# SSH public keys for root (read via AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u)"
  if [ -n "${SSH_PUBKEY:-}" ]; then echo "$SSH_PUBKEY"; elif [ -f "$HOME/.ssh/id_ed25519.pub" ]; then cat "$HOME/.ssh/id_ed25519.pub"; fi
  [ -f "$OVERLAY/etc/ssh/authorized_keys.d/root" ] && grep -v '^#' "$OVERLAY/etc/ssh/authorized_keys.d/root" || true
} > "$KEYS"
put "$KEYS" /etc/ssh/authorized_keys.d/root 0100644

# 6. units: mask OTA, enable ours
link /etc/systemd/system/otamanager.service /dev/null
echo "rm /etc/systemd/system/multi-user.target.wants/otamanager.service" >> "$CMDS"
link /etc/systemd/system/multi-user.target.wants/syng-agent.service ../syng-agent.service
link /etc/systemd/system/multi-user.target.wants/syng-cloud-block.service ../syng-cloud-block.service
link /etc/systemd/system/multi-user.target.wants/syng-led-reactive.service ../syng-led-reactive.service

# 7. connman: keep upstream's order (wifi,ethernet) and blacklist the usb gadget interfaces (the overlay's main.conf already has it; belt and braces)
dbgq "cat /etc/connman/main.conf" > "$TMP/main.conf"
sed -i.bak -e 's/^PreferredTechnologies = .*/PreferredTechnologies = wifi,ethernet/' \
  -e '/^NetworkInterfaceBlacklist = /{ /,usb$/! s/$/,usb/; }' "$TMP/main.conf"
put "$TMP/main.conf" /etc/connman/main.conf 0100644

echo "applying $(grep -c '^write' "$CMDS") files / $(grep -c '^symlink' "$CMDS") links to the rootfs…"
"$DEBUGFS" -w -f "$CMDS" "$FS" 2>&1 | grep -v "^debugfs [0-9]\|^debugfs: \(mkdir\|rm\|sif\|write\|symlink\)\|File not found by ext2_lookup\|rm: File not found\|already exists" || true
"$DEBUGFS" -R "stat /usr/bin/syng-agent" "$FS" 2>/dev/null | grep -q "Type: regular" && echo "ok: /usr/bin/syng-agent present" || { echo "FAILED: agent not in image"; exit 1; }
"$DEBUGFS" -R "stat /etc/systemd/system/otamanager.service" "$FS" 2>/dev/null | grep -q "Fast link dest: \"/dev/null\"" && echo "ok: otamanager masked"
# bootloader + flash tooling next to the image so `cd <out-dir> && ./flash.sh` works
SRCDIR="$(cd "$(dirname "$SRC")" && pwd)"; OUTDIR="$(cd "$OUT" && pwd)"
if [ "$SRCDIR" != "$OUTDIR" ]; then
  [ -f "$SRCDIR/imx-boot" ] && cp -f "$SRCDIR/imx-boot" "$OUT/" && echo "copied imx-boot from $SRCDIR"
  [ -f "$SRCDIR/manifest.json" ] && cp -f "$SRCDIR/manifest.json" "$OUT/"
fi
[ -f "$OUT/imx-boot" ] || echo "note: no imx-boot next to the image — copy it from the stock package before flashing"
cp -f "$HERE/firmware/flash.sh" "$HERE/firmware/syng-cell-flash.uuu" "$OUT/"
echo "image ready: $IMG"
# 8. an in-place update image for cells already running Troubadour: the rootfs partition alone, gzipped, plus its
#    sha256. Setup page → FIRMWARE → Upload image, then Install (this speaker) or Send + Install on all speakers.
if [ "${OTA:-1}" = 1 ]; then
  ART="$(dbgq 'cat /etc/mender/artifact_info' | sed 's/^artifact_name=//' | tr -d '[:space:]')"
  OTAIMG="$OUT/troubadour-rootfs-${ART:-unknown}.img.gz"
  echo "writing $OTAIMG (rootfs partition, 2 GiB → gzip)…"
  dd if="$IMG" bs=1048576 skip=$((ROOTFS_OFFSET/1048576)) count=2048 2>/dev/null | gzip -1 > "$OTAIMG"
  (cd "$OUT" && { shasum -a 256 "$(basename "$OTAIMG")" 2>/dev/null || sha256sum "$(basename "$OTAIMG")"; } > "$(basename "$OTAIMG").sha256")
  ls -la "$OTAIMG"
fi
if [ "${COMPRESS:-1}" = 1 ]; then echo "compressing (xz -T0 -3)…"; xz -T0 -3 -kf "$IMG"; mv -f "$IMG.xz" "$IMG.lzma"; ls -la "$IMG.lzma"; fi
echo "flash with:  cd $OUT && ./flash.sh   (uuu, Cell in USB serial-download mode)"
