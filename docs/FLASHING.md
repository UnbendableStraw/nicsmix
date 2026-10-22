# Flashing, boot layout & recovery

## Boot / partition layout (image.sdcard → eMMC)

| Part | Offset | Size | Mount | Contents |
|---|---|---|---|---|
| p1 | 32 MiB | 256 MiB | `/uboot` | U-Boot env (ext4, empty) |
| **p2** | 288 MiB | 2 GiB | `/` (**slot A**) | **rootfs — modified** |
| **p3** | ~2.3 GiB | 2 GiB | (slot B) | rootfs — **modified** (identical to A) |
| p4 | ~4.3 GiB | 1 GiB | `/data` | per-device data, created at first boot |

- U-Boot env after flash pins `mmcroot=/dev/mmcblk2p2` → always boots **slot A**.
  Both slots are modified so either is fine.
- Kernel cmdline is `rw`, **no dm-verity** → editing the rootfs boots normally.
- `imx-boot` (bootloader) and the FIT image are **left untouched**, so
  secure-boot / FIT-signature checks are unaffected.
- `/devdata` (the dev-unlock flag `devenable`) lives on a **separate SPI-NOR chip**
  (`/dev/mtdblock1`), *not* in this image — which is why SSH is baked into the
  rootfs directly instead of relying on the dev flag.

## Method A — `uuu` (recommended)

`uuu` (NXP mfgtools) is bundled in the app at `Contents/MacOS/uuu`, or
`brew install uuu`.

```sh
cd firmware
sudo ./flash.sh      # decompresses image.sdcard.lzma, then runs uuu
```

Put the Cell into **USB serial-download (SDP) mode** and connect USB-C. `uuu` waits
for the device, SDP-boots `imx-boot`, then `FB: flash -raw2sparse all` writes the
whole image to eMMC and sets the boot env. This mirrors exactly what the Syng app
does.

> Copy a stock `imx-boot` into `firmware/` first — it's required for the SDP step
> and is **not** shipped in this repo (it's Syng's/NXP's binary).

### Gotchas (observed)
- **`detach kernel driver failure (-3)`** → run with **`sudo`** (macOS needs root
  to detach the USB kernel driver).
- **Hangs right after the SDP boot step** → wrong handshake. On the i.MX8M Mini the
  ROM enumerates as **`SDP:`** (confirm with `uuu -lsusb` → `MX8MM = SDP:`). The
  working flow is the full `emmc_all`-style handshake with SDPU/SDPV and
  `write -f imx-boot -offset 0x57c00` (see `firmware/syng-cell-flash.uuu`).
- **`scp` just closes the connection** → the device has no `sftp-server`, and
  modern macOS `scp` defaults to SFTP. Use `scp -O ...` or `tar | ssh`.

The exact uuu script is [`firmware/syng-cell-flash.uuu`](../firmware/syng-cell-flash.uuu).

## Method B — the official app
Replace `Contents/Resources/Firmware/image.sdcard.lzma` in the app with your
modified one (keep `imx-boot`/`manifest.json`). **Caveat:** swapping a resource
breaks the app's code signature, and its privileged helper is installed via
SMJobBless pinned to Syng's certificate, so a re-signed/modified app will usually
fail to install the helper. Method A avoids all of that.

## SSH details
- `sshd` (OpenSSH) is already on the image, socket-activated on :22; it was only
  gated by auth + the dev `sshd_config`. The overlay sets (both slots):
  `PubkeyAuthentication yes`, `PermitEmptyPasswords no`, and (key-only builds)
  `PermitRootLogin prohibit-password` / `PasswordAuthentication no`, and
  `AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys.d/%u`.
- Root auth (§1.9): **key-only** when the build found an SSH key (password login off, root
  password locked); otherwise `ROOT_PASSWORD` if you set one, else a **random password the
  build prints once**. No shared default password is baked in any more.
- Host key is generated into `/data/ssh/` on first boot (expect a one-time
  host-key prompt).
- `otamanager.service` is masked (`→ /dev/null`) so a cloud update can't silently
  revert the mods or re-lock the device. Re-enable with
  `systemctl unmask otamanager`.

```sh
ssh root@<cell-ip>         # key-only, or the password the build printed / you set
passwd                     # change it
printf '%s\n' 'ssh-ed25519 AAAA... you' >> /etc/ssh/authorized_keys.d/root
```

> Security: this is a root-login box on your LAN. Keep it off untrusted networks,
> switch to keys, and set `PasswordAuthentication no` once your key works.

## Recovery / rollback
- Keep your **original**, unmodified `Syng Firmware Updater.app` (and its stock
  `image.sdcard.lzma`). Re-flashing stock with `uuu` fully restores the device.
- The device is flashed whole each time (`-raw2sparse all`), so a bad mod is fully
  recoverable — nothing here touches `imx-boot` or fuses.
- If a network change locks you out, re-flash stock, or use the UART console (the
  board exposes U-Boot/serial) to fix `/etc`.
