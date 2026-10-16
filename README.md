# SyngOS Troubadour — an open mod kit for Syng Cell speakers

Keep Syng Cell Alpha speakers useful without Syng's cloud: a flashable firmware mod, a
self-hosted web UI that runs **on the speakers**, offline room setup/grouping/calibration with
**no 5-cell limit**, a faithful WebGL port of the app's sound-field control, LED control, and the
reverse-engineering notes that explain how all of it works.

> Not affiliated with Syng. You are reflashing a speaker's eMMC: read the recovery notes first.
> Everything here was recovered from the stock firmware image and from the speakers themselves.

## What you get

| | |
|---|---|
| **Firmware mod** (`overlay/`, `tools/fw/`) | SSH (root, password + keys), USB-Ethernet drivers + wired-first networking, OTA masked so Syng can't revert you, the agent + cloud-block services baked in. `build-troubadour.sh` stamps it onto any stock image (2.2.1 and 2.2.2 verified). |
| **syng-agent** (`agent/`) | a single-file Python 3.7 service on every cell, port 8080: live dashboard (sources, volume, tone, spatial toggles, room EQ, temperatures, faults, accelerometer, eMMC health, CPU/RAM), the **sound-field control** (puck / spatial / focus dots / volume rim, music-reactive, driven through the cell's own spatialiser), LED modes (music-reactive from the real audio feed, no mic), fleet view, theme. |
| **`/setup`** — the Syng app replacement | Wi-Fi, discover cells on the LAN, create/join/leave rooms, **more than 5 cells per room**, room map (drag speakers, rotation, reference circle, listener), **Soundcheck** run on-device with the results kept locally, housekeeping, cloud block toggles, restore. Works over a plain USB-C cable (the cell is a USB-Ethernet device at `http://10.86.0.1:8080/setup`). |
| **Cloud block** (`syng-cloud-block`) | DNS + firewall block of `syng.space` per cell, persistent across reboots, toggled from the dashboard. Off = Syng's cloud takes over again. |
| **Docs** (`docs/`) | [HACKING](docs/HACKING.md) (workflow, gotchas) · [FIRMWARE-MODS](docs/FIRMWARE-MODS.md) (hardware, partitions, SSH/LED/USB mods, flashing, recovery) · [CELL-INTERNALS](docs/CELL-INTERNALS.md) (every daemon, the cloud state document, the LAN control API, grouping/SyFi, calibration, USB gadget) · [SETUP](docs/SETUP.md) (the offline setup flow + the agent's HTTP API) · [SOUND-FIELD](docs/SOUND-FIELD.md) (the app's field reproduced from its Metal shaders and asset catalog) · [FIRMWARE-2.2.2](docs/FIRMWARE-2.2.2.md) (what Syng changed, rebasing) · [DBUS-API](docs/DBUS-API.md) (detailed `com.syng.*` calls and JSON schemas) · [LED](docs/LED.md) (LED daemons, reactive modes) · [ROADMAP](docs/ROADMAP.md) |

## Quick start

**You need:** a stock firmware package (`image.sdcard.lzma`, `imx-boot`, `manifest.json` — from
*Syng Firmware Updater.app/Contents/Resources/Firmware/*; not redistributed here), `uuu`
(`brew install uuu`), `e2fsprogs` + `xz` (`brew install e2fsprogs xz`), and a USB-C cable.

```sh
git clone … syng-troubadour && cd syng-troubadour
export ROOT_PASSWORD='pick-one'                      # root password baked into the image
export SSH_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"     # your key, for passwordless root
tools/fw/build-troubadour.sh ~/stock-firmware/image.sdcard.lzma build
cd build && ./flash.sh        # put the Cell in USB serial-download mode when asked
```

Then, with the cell on your network: `http://<cell-ip>:8080/` is the dashboard,
`http://<cell-ip>:8080/setup` sets it up (or plug it straight into a laptop over USB-C and open
`http://10.86.0.1:8080/setup`). The first thing to do there is **Block cloud** — after that the
room document on the speakers is authoritative and Syng's servers can't overwrite it.

Already running Troubadour? Update just the agent over SSH, no reflash:
`agent/deploy.sh agent/syng-agent.py 192.168.1.10 192.168.1.11 …`

## Repository layout

```
agent/syng-agent.py        the agent (b27) — deploy as /usr/bin/syng-agent
agent/deploy.sh            push a build to running cells over SSH
agent/build/               how the agent is assembled: localcloud.py (room authority, discovery,
                           join/leave, layout, calibration, USB link, Wi-Fi), housekeeping.py,
                           setup.html (the /setup page), build_b26/27.py, parts/, history/
field/                     SyngField WebGL renderer (field.js), demo.html (synthetic music + tweak
                           sliders), textures.js (gloss maps / lens table / glyph recovered from the app)
overlay/                   every file the mod adds to the stock rootfs (what build-troubadour.sh stamps in):
                           syng-agent, syng-led (LED CLI), syng-led-reactive (music-reactive LED daemon),
                           syng-cloud-block, syng-dbus-dump, units, connman/usb-ethernet/wired configs
tools/fw/                  build-troubadour.sh (stock image → Troubadour image), sha512crypt.py
tools/carx.py              Assets.car extractor (BOM + CSI + chunked lzfse)
tools/split_metallib.py    metallib → LLVM bitcode (then llvm-dis) for the shaders
firmware/                  flash.sh + syng-cell-flash.uuu (uuu flow) + stock manifests; no images
docs/                      see above
attic/                     earlier field builds, kept for the record
```

## How it works, in one paragraph each

**Grouping without the cloud.** Each cell keeps Syng's *cloud state document*
(`cell_state` / `room_state` / `room_neighbours`) in `cloudsyncd`, and every on-cell consumer —
celldirector (rooms, roles, the SyFi link), the spatialiser, the calibrator — reacts to
`cloudsyncd.PatchState`, the same entry point the cloud uses. The agent on the room's primary
is the writer: it builds each member's view and pushes it over HTTP to that member's agent,
which patches it in. The 5-cell cap lived in the app and the cloud only.

**Calibration.** Soundcheck is entirely on-device (`DasCalibrator.Runner.Run()`: round-robin
chirps, ToF/AoA, layout estimation, room EQ, wall detection). Only the final positions went to the
cloud; a 40-line patch to `pycalibrator/cloudapi.py` posts them to the local agent, which
updates the room document, computes the reference circle (the cloud's rule: smallest enclosing
circle of the cells, radius ÷ 0.85) and fans it out. Recalibrations are aligned to the previous
layout so the map keeps its orientation.

**The sound field.** `cellbroker` (JSON-RPC over `wss://cell:8765`, no auth) is what the app
drives on the LAN; `source_set_stem_positions` with a `panner_v2` handle (puck in room-circle
units, rotation, width, radius 0 = classic / 0.7 = spatial) is the whole sound-field API. The
renderer reproduces the app's look from its own Metal shaders (particle kernel, capped-torus SDF,
pulse-texture displacement) and Assets.car (Orb gloss maps, lens table, palettes).

## Safety and recovery

* Flashing writes the whole eMMC image; re-flashing the stock package with the same `uuu` flow
  restores a stock speaker. `imx-boot` and fuses are never touched.
* The mod turns a speaker into a root-login box on your LAN. Change the password, use keys,
  keep it off untrusted networks.
* Everything the agent writes to a cell's state is reversible: the pre-agent document is saved
  on first touch (`/data/syng-agent/state-before-agent.json`, *Advanced → Restore*), and
  unblocking the cloud lets Syng's servers re-impose whatever they last knew.
* Soundcheck plays chirps at a moderate fixed level; the agent never changes your volume.

## Status

Verified on six Cell Alphas (Troubadour 2.2.1 base): six in one room with SourceCast + SyFi +
time sync, leave/join through the UI, three on-device Soundchecks, DHCP address changes picked up
automatically. Open items are in [docs/ROADMAP.md](docs/ROADMAP.md) (never-set-up cells, wired
rooms beyond the SyFi station limit, calibration averaging).

## Legal

Code in this repository is MIT (see `LICENSE`). It interoperates with proprietary firmware and an
app that are not included: you bring your own stock firmware package. `field/textures.js` and the
palettes in `docs/SOUND-FIELD.md` were recovered from the Syng Space app's asset catalog for
interoperability (to reproduce the control the speakers were sold with); if you'd rather not
redistribute them, delete `field/textures.js` and rebuild the agent — the field renders with flat
fallbacks. Syng and Cell are trademarks of their owner.
