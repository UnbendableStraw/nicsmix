# The firmware mod — hardware, partitions, SSH, USB-Ethernet, LED, flashing

Reverse-engineered from **Syng Firmware Updater.app 2.2.1** (and verified against 2.2.2, see
FIRMWARE-2.2.2.md). The base mod makes three changes to the stock root filesystem (the agent,
cloud block and calibration hook were added later and are stamped in by the same build script):

1. **USB‑to‑Ethernet adapter support** + wired made the *preferred* network (bypass Wi‑Fi).
2. **SSH enabled** (root login, password + public‑key).
3. **LED customization** — a documented control path (`syng-led`), the decoded LED model, and example animations.

`tools/fw/build-troubadour.sh` bakes all of it into a copy of the stock `image.sdcard.lzma`; flash with `uuu` (see **Flashing**).

---

## 1. What the hardware/OS actually is

| | |
|---|---|
| SoC | NXP **i.MX8M Mini** (board family `C01`, production DTB `imx8mm-syng-c01mp1-ff.dtb`) |
| OS | Yocto **zeus**, "NXP i.MX Release Distro 5.4‑zeus", `ID=syng-cell-alpha` |
| Kernel | Linux **5.4.3**, booted from a FIT image (`/boot/fitImage`) via U‑Boot |
| Init | **systemd** |
| Storage | eMMC `mmcblk2`; image is a 5.67 GB SD/eMMC image |
| Network mgr | **ConnMan** (`connmand`) for Wi‑Fi + Ethernet; `wpa_supplicant`; systemd‑networkd only for gadget/containers |
| Bootloader | `imx-boot`, embedded in the image at the 33 KB offset (IVT header `d1 00 20 41`) — **left untouched** |

### Partition layout (image.sdcard → eMMC)
| Part | Offset | Size | Mount | Contents |
|---|---|---|---|---|
| p1 | 32 MiB | 256 MiB | `/uboot` | U‑Boot env (ext4, empty) |
| **p2** | 288 MiB | 2 GiB | `/` (**slot A**) | **rootfs — modified** |
| **p3** | ~2.3 GiB | 2 GiB | (slot B) | rootfs — **modified** (identical to A) |
| p4 | ~4.3 GiB | 1 GiB | `/data` | per‑device data, created at first boot |

`/devdata` (the dev‑unlock flag `devenable`) lives on a **separate SPI‑NOR chip** (`/dev/mtdblock1`), *not* in this image — which is why the SSH changes are baked directly into the rootfs instead of relying on the dev flag.

U‑Boot env after flash pins `mmcroot=/dev/mmcblk2p2` → it always boots **slot A (p2)**. Both slots were modified so either is fine. Kernel cmdline is `rw` and there is **no dm‑verity**, so editing the rootfs is safe and boots normally. The FIT image and `imx-boot` were not touched, so secure‑boot/FIT‑signature checks are not affected.

---

## 2. The three modifications (exact changes)

### A. USB‑to‑Ethernet (bypass Wi‑Fi)
The kernel already ships every common dongle driver as a module and udev autoloads them by USB modalias. Both USB controllers are `dr_mode = otg` (host role works — the stock USB‑audio input uses host mode), so a dongle on the OTG port enumerates. Changes:

- **`/etc/modules-load.d/usb-ethernet.conf`** — pre‑loads `usbnet asix ax88179_178a cdc_ether cdc_ncm r8152 smsc95xx` (covers ASIX AX887xx/AX88179, Realtek RTL8152/8153, SMSC95xx, CDC‑ECM/NCM). A hot‑plugged adapter is recognized immediately.
- **`/etc/connman/main.conf`** — technology order stays **stock (`wifi,ethernet`)**; only `usb` is added to the interface blacklist (the agent owns the USB gadget link). Earlier builds flipped it to `ethernet,wifi`; that was a mistake: celldirector decides "Wi-Fi connected" from the Wi-Fi *service* being connman's online (default-route) service, so with a wired adapter owning the route it logged `Entering WiFi repair mode after being disconnected for at least 20 s`, set its phase to `wifiRepair` and kept scanning (observed on a fresh 2.2.2 cell, b39). A wired adapter still gets DHCP and the agent answers on it; it just isn't the default route while Wi-Fi is up. A **wired-only** cell (no Wi-Fi at all) ends up in the same repair mode — rooms need Wi-Fi until the wired-room work on the roadmap.
- **`/etc/systemd/network/80-wired.network`** — overrides the stock file to mark `en*/eth*` **`Unmanaged=yes`** in systemd‑networkd, so ConnMan is the single owner of the wired link (no DHCP race).

**Using it:** plug a supported USB‑Ethernet dongle into the Cell's USB‑C port (use an OTG/host adapter if needed). It should get DHCP and become the default route within a few seconds. Check over SSH:
```sh
connmanctl technologies     # 'Ethernet' present + connected
connmanctl services         # ethernet_* should be 'ready'/'online'
ip route                    # default route via the dongle
```
If the port doesn't auto‑switch to host, force it (replace the controller addr if different):
```sh
echo host > /sys/bus/platform/devices/32e40000.usb/role   # or 32e50000.usb
```

### B. SSH
`sshd` (OpenSSH) is already on the image and `sshd.socket` is socket‑activated on port 22; it was only gated by auth + the dev `sshd_config`. Changes (applied to both rootfs slots):

- **`/etc/ssh/sshd_config`** — replaced with Syng's own `devel` config and set: `PermitRootLogin yes`, `PasswordAuthentication yes`, `PubkeyAuthentication yes`, `PermitEmptyPasswords no`, and `AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys.d/%u`.
- **`/etc/shadow`** — root password set to **`SyngCell.dev`** (SHA‑512). *Change it on first login.*
- **`/etc/ssh/authorized_keys.d/root`** — drop your public key here (before flashing, in `overlay/`, or after login) for key‑based access.
- **OTA masked** — `otamanager.service → /dev/null` so a cloud update can't silently revert these mods or re‑lock the device. Re‑enable with `rm /etc/systemd/system/otamanager.service` (or `systemctl unmask otamanager`).

**First login:**
```sh
ssh root@<cell-ip>        # password: SyngCell.dev
passwd                    # change it
# add your key:
mkdir -p ~/.ssh && printf '%s\n' 'ssh-ed25519 AAAA... you' >> /etc/ssh/authorized_keys.d/root
```
The host key is generated into `/data/ssh/` on first boot (expect a one‑time host‑key prompt). PAM has no `securetty` gate on sshd, so root logs in fine.

> Security: this is a root‑login box on your LAN. Keep it off untrusted networks, switch to keys, and consider `PasswordAuthentication no` once your key works. DBus‑over‑TCP (port 1010, Syng's other dev feature) is **not** enabled — you don't need it; use `dbus-send`/`busctl` locally over SSH.

### C. LED customization
Fully reverse‑engineered from `/usr/bin/ledmanager` (see §3). Added:

- **`/usr/bin/syng-led`** — a helper to set color / play animations via the LED D‑Bus service, or poke PWM directly.
- **Example animations** in `/usr/lib/syng/visions/`: `NicsFixBreathe.json` (cyan breathe), `PulseRed.json` (one‑shot alert), `WarmIdle.json` (half‑white idle). Stock animations are untouched.

```sh
syng-led color 1 0 0 0        # solid red      (R G B W, 0.0–1.0)
syng-led color 0 0 0 0.5      # half white
syng-led play NicsFixBreathe  # loop a vision in /usr/lib/syng/visions
syng-led pause ; syng-led resume ; syng-led clear
syng-led raw 0 0 1 0          # DIRECT sysfs (run `systemctl stop ledmanager` first)
```

---

## 3. Reverse‑engineering notes: the LED subsystem

`ledmanager` is a stripped aarch64 D‑Bus service. Decoded facts:

- **4× PWM, RGBW.** Physical mapping (from `setColor`):
  `pwmchip0 = White`, `pwmchip1 = Green`, `pwmchip2 = Red`, `pwmchip3 = Blue`.
  (This is why `init-pwm-led-white.service` drives *pwmchip0*.)
- **Duty is linear:** `duty_cycle = period × value`, `value ∈ [0,1]`. Period is `22767` (~43.9 kHz), so `1.0 → 22767`, `0.5 → 11384`. The `translationLUT` (gamma) path is a **stub** (`return -1`, unused); `translationRatio` is live.
- **Animations** are JSON in `/usr/lib/syng/visions/`:
  ```json
  { "rgbw_frames": [[r,g,b,w], ...], "metadata": { "oneshot": true|false } }
  ```
  Each value 0.0–1.0. Played at **30 FPS** (frame interval decoded as `0x3fa1111111111111` = 1/30 s). `oneshot:true` plays once; otherwise it loops.
- **D‑Bus:** service + interface `com.syng.Led`, object `/com/syng/ledmanager`:
  - `SetColor(i state, a d rgbw)` — `state 5` = custom color, the array **must be length 4** `[R,G,B,W]`; states 0–4 are predefined colors, state 6 is the animation state (`isColor` accepts 0–5, `isAnimation` accepts 6).
  - `PlayAnimation(s path)`, `PauseAnimation()`, `ResumeAnimation()`, `ClearAnimationQueue()`.

Raw example without the daemon:
```sh
systemctl stop ledmanager
for c in 0 1 2 3; do echo 0 > /sys/class/pwm/pwmchip$c/export 2>/dev/null; echo 22767 > /sys/class/pwm/pwmchip$c/pwm0/period; echo 1 > /sys/class/pwm/pwmchip$c/pwm0/enable; done
echo 22767 > /sys/class/pwm/pwmchip2/pwm0/duty_cycle   # full red
```

Make a new animation: write `/usr/lib/syng/visions/MyThing.json` (30 fps frames), then `syng-led play MyThing`.

---

## 4. Flashing

> The step-by-step with observed gotchas (sudo, SDP mode, recovery) is in [FLASHING.md](FLASHING.md).

**Method A — `uuu` (recommended, no code‑signing hassle).** `uuu` is bundled in the app
at `Syng Firmware Updater.app/Contents/MacOS/uuu`, or `brew install uuu`.

```sh
tools/fw/build-troubadour.sh ~/stock/image.sdcard.lzma build
cd build && ./flash.sh      # decompresses if needed, then runs uuu
```
Put the Cell into **USB serial‑download (SDP) mode** and connect USB‑C — `uuu` waits for the
device, SDP‑boots `imx-boot`, then `FB: flash -raw2sparse all` writes the whole image to eMMC
and sets the boot env. This mirrors exactly what the Syng app does. Use the same physical
connect/mode procedure the Syng updater walks you through.

**Method B — the official app.** Replace `Contents/Resources/Firmware/image.sdcard.lzma`
in the app with the one here (keep `imx-boot`/`manifest.json`). Note: swapping a resource
**breaks the app's code signature**, and the privileged helper is installed via SMJobBless
with a requirement pinned to Syng's certificate, so a re‑signed/modified app will usually
fail to install the helper. Method A avoids all of that.

---

## 5. Recovery / rollback
- Keep your **original** `Syng Firmware Updater.app` (unmodified `image.sdcard.lzma`). Re‑flashing it with `uuu` restores stock.
- The device is flashed whole each time (`-raw2sparse all`), so a bad mod is fully recoverable by re‑flashing — nothing here touches `imx-boot` or fuses.
- If a dongle/network change locks you out, re‑flash stock, or use a UART console (the board exposes U‑Boot/serial) to fix `/etc`.

## 6. File manifest
```
build/image.sdcard(.lzma)      the Troubadour image, produced by tools/fw/build-troubadour.sh (not in git)
build/imx-boot                 stock bootloader, copied from your stock package (unchanged, for the SDP step)
firmware/syng-cell-flash.uuu   uuu script (mirrors the app's flow)
firmware/flash.sh              decompress + run uuu (copied into build/)
firmware/manifest-2.2.1.json, firmware/2.2.2/manifest.json   stock manifests, for reference
overlay/                       the individual files the mod adds (what the build script stamps in)
```

**Default credential:** `root` / `SyngCell.dev` — change it immediately.
