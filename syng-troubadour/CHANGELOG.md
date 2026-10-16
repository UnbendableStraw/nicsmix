# Changelog

Agent builds are numbered bNN; the published `agent/syng-agent.py` is the latest.

## repo — 2026-10-16
- Restored into the published set (they were on the cells but missing from the overlay): `syng-led-reactive` daemon + unit + `/etc/default` args, `syng-dbus-dump`, `/etc/default/syng-agent`; `build-troubadour.sh` now enables `syng-led-reactive.service`. Docs: `LED.md`, `DBUS-API.md`, `FLASHING.md`, `HACKING.md`; `tools/fw/apply-overlay-linux.sh` (loop-mount alternative).

## b28–b31 — 2026-10-16 (overnight)
- **Ping** (b28): the app's "ta ta ta" (`SAPlayer.PlayOneShot /usr/lib/syng/sounds/multi-cell-orientation.wav` at the UI sound level) + `CellServer.IdentifyCell` LED — this cell, any cell (relayed, even in another room), or the whole room.
- **Now playing + transport** (b29): title/artist/album, artwork (proxied from the source's `artworkUrl`), progress, previous/play-pause/next honouring `supportedControls`, mix badge (`badgingType` / `mixType` / `channelLayout`).
- **Sleep timer** (b29): 15/30/60/90 min, 30 s fade then the source is deactivated and the level restored.
- **Volume limit** (b29): a per-cell guard (50/70/85 %) that clamps any music source above it; the slider's range follows it.
- **Control ring & sounds** (b29, setup → Advanced): ring volume steps, clockwise direction (restarts hidserver only), UI sound level.
- **Live fleet status** (b29): SyFi / time-sync / Wi-Fi RSSI per cell under each fleet row, from each agent.
- **Snap-to-cell** (b30): the puck magnetises onto a speaker ring (`cfg.snapFrac` 0.13 R) and the handle carries `snap_to_cell` / `snapped_rotation`, so the spatialiser plays from that cell like the app. Toggle in Field tweaks.
- **Hot-plugged sources** (b31): USB-C / HDMI / Pure Data sources are selectable when the sound server registers them; `is_music()` replaces the fixed list.
- **Field persistence** (b31): the handle is saved to `/data/syng-agent/field.json` and re-applied 1.5 s after any source becomes active (a new AirPlay session used to fall back to the cloud default).

## b27 — 2026-10-16
- Dashboard: Syng-cloud card (status of every cell in the room, block/unblock this cell or all), theme switch (auto/dark/light), fleet rows link to each cell.
- Sound field: canvas no longer stays stretched after a page load (re-sizes with the wrapper, puck keeps its place); tone curve labels no longer stretched.
- Setup page: Housekeeping (journal size, core dumps, /data usage, biggest processes; vacuum journal, delete core dumps, restart a service).
- Firmware: `tools/fw/build-troubadour.sh` stamps the overlay onto a stock image (2.2.1/2.2.2); `docs/FIRMWARE-2.2.2.md`; overlay `cloudapi.py` rebased on 2.2.2.

## b26 — 2026-10-16
- The agent is the room authority ("local cloud"): discovery (LAN sweep + `_syng._tcp` mDNS), create/join/leave/remove/rename/pin, member views fanned out through `cloudsyncd.PatchState`, keeper for IP/role drift.
- `/setup` page: Wi-Fi provisioning, rooms on the LAN, room map (positions, rotation, reference circle, listener), Soundcheck, Advanced (cloud block, document, restore).
- On-device calibration with results captured locally (`pycalibrator/cloudapi.py` patch), reference-circle/listener rule reproduced, recalibration aligned to the previous layout.
- USB-C setup link (ECM gadget + udhcpd at 10.86.0.1 / 10.86.1.1), connman keeps its hands off the gadget interfaces.
- `syng-cloud-block` persists across reboots (`syng-cloud-block.service`).
- Rooms with more than 5 cells verified (six).

## b23–b25
- Pulse texture fixed (beat-reactive glitter), app-like puck pulse, 30 Hz CPU particle sim + GPU interpolation, real spatialisation through `cellbroker` (`panner_v2` handles), field tweaks panel, speaker rings + listener marker.

## b19–b22
- SyngField WebGL renderer reproduced from the app's Metal shaders and asset catalog; `/api/soundfield`; viz feed v2 over SSE (level/flux/beat).

## b18 and earlier
- Dashboard, sensors, tone/volume/source control, LED control (`syng-led`, music-reactive from the audio feed), fleet view. Firmware mod: SSH, USB-Ethernet, OTA mask (see docs/FIRMWARE-MODS.md).
