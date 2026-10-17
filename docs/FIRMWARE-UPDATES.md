# Firmware updates without reflashing — and keeping the mods across Syng releases

The cells are stock **mender A/B**: two 2 GiB root filesystem slots (`/dev/mmcblk2p2` = A, `p3` = B), a
shared data partition (`p4`, mounted on `/data`: rooms, Wi-Fi, calibration, the agent's settings, your
fonts), and U-Boot's mender integration (`mender_boot_part` 2|3, `upgrade_available`, `bootcount`,
`bootlimit=1`). Syng's own OTA used exactly this. Troubadour's agent uses it too (b44), so a cell is
updated from the setup page without a cable:

```
FIRMWARE card (setup page)
  Upload image…            troubadour-rootfs-<artifact>.img.gz from build-troubadour.sh → /data/firmware
  Install on this speaker  write idle slot → read back + sha256 → carry Troubadour files → flip boot slot → reboot
  Send to all speakers     every member pulls the image from the primary (cell to cell, ~30 s each)
  Install on all speakers  secondaries one by one (each comes back on the new slot before the next starts), primary last
  Boot the other slot      rollback (or "try the other one")
```

## What exactly happens on install

1. `gzip` stream → the **idle** slot's block device (the running one is never touched), 4 MiB chunks, `fsync`.
2. The slot is read back and its sha256 compared with what was written. A mismatch stops here.
3. The new slot is mounted and this cell's Troubadour bits are copied over it: `/usr/bin/syng-agent`
   (the one that is running — so an older image still comes up with the current agent),
   `syng-led-reactive`, `syng-led`, `syng-cloud-block`, `syng-dbus-dump`, `syng-ptp4l`, `syng-pmc`, the `syng-ptp` unit, `/etc/default/syng-*`,
   `/etc/ssh/authorized_keys.d/root`, and the `root:` line of `/etc/shadow` (your password stays).
4. `fw_setenv`: `mender_boot_part=<idle>`, `mender_boot_part_hex`, `upgrade_available=1`, `bootcount=0`;
   `/data/firmware/last-install.json` records it; reboot.
5. On the new slot the agent waits for `celldirector`, `syngsoundserver` and `cloudsyncd` to be active,
   then **commits** (`upgrade_available=0`). If the slot never gets that far, U-Boot's `altbootcmd`
   flips back to the previous slot on the next boot (`bootlimit=1`) — the stock safety net.

Measured on a Cell Alpha over Wi-Fi: upload 278 MB ≈ 75 s, write + verify ≈ 160 s, reboot to agent
≈ 60 s, commit ≈ 45 s later. Rooms, Wi-Fi, the cloud block, calibration, scenes, fonts all survive
(they live on `/data`). The cell rejoins its room on its own; the primary re-learns its SyFi address.

## Keeping up with Syng

When Syng publishes a new stock package (`image.sdcard.lzma`, `imx-boot`, `manifest.json`):

1. `tools/fw/build-troubadour.sh stock/image.sdcard.lzma build-<ver>` — stamps the overlay onto the new
   rootfs (SSH, keys, password, OTA mask, units, connman) and writes **both** `image.sdcard` (for `uuu`,
   new or bricked cells) and `troubadour-rootfs-<artifact>.img.gz` (for in-place updates).
2. Compare first: `docs/FIRMWARE-2.2.2.md` shows how (dump both root filesystems, hash, diff). Look at
   what changed in `cellbroker`, `celldirector`, `syngsoundserver`, `pycalibrator` and the units; our
   patched `pycalibrator/cloudapi.py` must be re-based if theirs changed.
3. Upload the `.img.gz` to the room's primary, **Send to all speakers**, **Install on all speakers**.
4. If something is wrong on the new slot: **Boot the other slot** — the old system is still there intact.

What this does *not* update: `imx-boot` (SPL/U-Boot, written by `uuu` only — Syng's OTA doesn't touch it
either) and the partition layout. If a future release changes either, that one needs the cable.

## Lessons from the first rollout (five cells, b44–b48)

* **The read-back check earns its keep.** Three of the first five installs failed verification — not
  the eMMC: the status endpoint mounted the idle slot read-only to read its version while the
  install was writing it (the page polls status every 2 s), and the read-back then went through a
  stale page cache. Nothing was switched; b48 locks the slot during the write, skips slot info while
  busy, unmounts/flushes before writing and drops caches before reading back. Reinstall = one click.
* **Persist the room document before rebooting.** cloudsyncd does not write its document on shutdown;
  a cell rebooted after room changes used to come back with whatever `cloud_state.json` held. The
  agent persists it after every patch, every 90 s and before a firmware reboot (b45).
* **The primary moves.** Rebooting the primary makes celldirector elect another member; the room
  re-forms by itself (the whole document, SyFi addresses included, is on every member). Pin it back
  with *make primary* if you care which one it is.
* **Block the cloud first on 2.2.2.** Cells that got `401` from Syng's API on 2.2.1 log in fine on
  2.2.2 (`authtokend`): within a minute of the new slot booting, `cloudsyncd` pulls the serial's last
  cloud record — a smaller, older room. The agent persists the local document, but the cloud's patch
  lands in memory; block the cloud on every member before updating a room that only exists locally.

## Under the hood / scripting

| | |
|---|---|
| `GET /api/firmware` | running {artifact, version, part, agent}, other slot {artifact, version, agent}, env, image {name,size,sha256,artifact}, last install, progress, log, room status |
| `POST /api/firmware/upload` (raw body, `X-Filename`) | store an image in `/data/firmware/` (streamed, sha256 computed) |
| `GET /api/firmware/image` | the stored image (what members pull) |
| `POST /api/firmware/fetch {ip}` | pull the image from another cell |
| `POST /api/firmware/install {reboot:true, carry:true}` | the sequence above, in the background; watch `/api/firmware` |
| `POST /api/firmware/rollback` | boot the other slot |
| `POST /api/firmware/room {action: distribute\|install}` | primary: fan out / orchestrate |

Manual equivalent of the install, for the record:
```sh
gunzip -c troubadour-rootfs-X.img.gz | dd of=/dev/mmcblk2p3 bs=4M conv=fsync   # if running from p2
fw_setenv mender_boot_part 3; fw_setenv mender_boot_part_hex 3; fw_setenv upgrade_available 1; fw_setenv bootcount 0; reboot
# after a good boot:
fw_setenv upgrade_available 0
```
