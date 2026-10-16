# Setting up and configuring cells without Syng — the `/setup` page (agent b26)

Every cell running SyngOS Troubadour serves `http://<cell-ip>:8080/setup`. The page is the
replacement for the Syng app's setup flow: it names rooms, groups speakers, lays out the room
map, runs Soundcheck and keeps the room document on the speakers themselves. No account, no
cloud. (The dashboard is still at `/`, "setup ›" in its header.)

## Reaching a speaker

| situation | how |
|---|---|
| fresh flash, no Wi-Fi yet | plug the cell into a laptop with a **USB-C cable** (either port). The cell is a USB-Ethernet device; the laptop gets an address and the cell is at **`http://10.86.0.1:8080/setup`** (`10.86.1.1` on the other port). Scan + join your Wi-Fi from the NETWORK card (the password goes into a connman provisioning file on the cell, never anywhere else). |
| USB-C Ethernet adapter | works too (the kernel ships r8152/asix/ax88179/cdc_ether; connman prefers wired). Find the cell's address on your router or by mDNS: `syng<serial>.local`. |
| already on Wi-Fi | `http://<ip>:8080/setup` from anything on the LAN; every cell's page lists every other cell ("open"). |

## The flow

1. **NETWORK** — Wi-Fi status, scan, connect/forget, hidden networks.
2. **ROOMS ON THIS NETWORK** — every cell found on the LAN (agent sweep of the local /24 + the
   `_syng._tcp` mDNS records the stock firmware advertises), grouped by room with roles and
   calibration state. *Join this room with this speaker* makes this cell a secondary of that
   room; *Create room with this speaker* makes it the primary of a new one (leaving its old room
   first). Cells on stock firmware show as "stock firmware (no agent)" — flash them first.
3. **ROOM LAYOUT** (when the cell is in a room) — the room map in metres, front of the room up:
   drag a speaker to move it, drag its tick to turn it, drag the dashed reference circle's
   centre / edge / ◆ to move, resize, rotate the sound-field circle, ◎ is the listener.
   *Save layout* pushes it to every member. *Auto circle & listener* recomputes the defaults
   (listener = centre of the smallest circle around the cells, circle radius = that radius ÷ 0.85 —
   the rule the cloud used). The table edits the same numbers plus ceiling (upside-down) and
   height layer, *make primary* (pins the role through celldirector), *remove* (the cell becomes a
   room of its own). *Rename* renames the room (what AirPlay shows).
4. **Soundcheck (calibrate)** — runs the firmware's own `DasCalibrator.Runner.Run()` on the primary:
   round-robin chirps on every cell, ToF/AoA, physical layout estimation, multi-channel room EQ
   and wall detection, all on the speakers (≈ 65–95 s for 6 cells). Room EQ is applied directly to
   each cell; positions and walls come back to the agent (`pycalibrator/cloudapi.py` is patched to
   POST them to `127.0.0.1:8080/api/calibration/results` before trying the cloud). On the first
   calibration the layout is anchored at the centroid and the circle/listener defaults are
   computed; on a **recalibration the new layout is rigidly aligned to the previous one**
   (2-D Kabsch on the cells both runs know) so the map, circle rotation and listener keep their
   meaning. Keep the room quiet: a run with music playing drifted ~1 m on one cell.
5. **Advanced** — block/unblock Syng's cloud on this cell (the block persists across reboots via
   `syng-cloud-block.service`), show the raw room document, restore the state this cell had before
   the agent first changed it.

## More than 5 cells

The 5-cell cap is the app's and the cloud's, not the firmware's: with the room document written
locally the primary accepts any member that celldirector can reach. Six are verified (five + the
dev unit): the primary streams SourceCast to all, time sync and SyFi good on every secondary.
The practical ceiling is the primary's SyFi access point (Wi-Fi chip client limit,
"AP-REJECTED-MAX-STA" is handled in celldirector) — if a 7th/8th cell will not associate, move the
room to wired Ethernet (`/etc/syng/SourceCast.conf` `timesyncInterface`, `celldirector
options.conf: manage_syfi false`), which is the next step on the roadmap.

## What the agent does underneath (so you can script it)

All JSON over HTTP on port 8080; the setup page uses nothing else.

| endpoint | |
|---|---|
| `GET /api/whoami` | serial, IPs, SSH keys, room, role (live, from celldirector), calibration, Wi-Fi, cloud-block |
| `GET /api/discover` | cells on the LAN grouped by room |
| `GET /api/room` · `GET /api/room/doc` | the room view / the raw cloudsyncd document |
| `POST /api/room/create {name}` | this cell → primary of a new room |
| `POST /api/room/join {ip}` | this cell joins the room of the cell at `ip` (asks that room's primary to admit it) |
| `POST /api/room/admit {cell}` · `/remove {serial}` · `/rename {name}` · `/pin {serial}` | primary-only |
| `POST /api/room/layout {cells:{serial:{x,y,z,rotation,is_upside_down,height_layer}}, reference_circle, listener_position, auto_circle}` | primary-only |
| `POST /api/room/leave` | this cell → room of its own |
| `POST /api/room/apply {cell_state, room_state, room_neighbours}` | internal: what the primary fans out to each member (`cloudsyncd.PatchState`) |
| `POST /api/calibration/run` · `GET /api/calibration/status` | Soundcheck (primary) |
| `POST /api/calibration/results [..]` | where the patched calibrator posts |
| `GET /api/wifi` · `/api/wifi/scan` · `POST /api/wifi/connect {ssid,psk}` · `/api/wifi/forget` | connman |
| `POST /api/cloudblock {on}` · `GET /api/cloudblock` | `syng-cloud-block` |
| `POST /api/relay {ip, path, method, body}` | run a request against another cell through this one (how the page reaches the primary over the USB link) |
| `GET /api/room/backup` · `POST /api/room/restore {state}` | the pre-agent state document of this cell / write any document |
| `POST /api/ping {serial?, all?, vol?}` | the identify sound + LED on this cell, a named cell (relayed) or every member |
| `POST /api/transport {source, action}` | play · pause · stop · next · previous · seek (`position`) · shuffle_on/off · repeat_off/context/track |
| `GET /api/artwork?source=` | the current track's artwork, fetched from the source on the cell |
| `POST /api/sleep {minutes}` · `GET /api/sleep` | sleep timer (0 = off) |
| `GET/POST /api/settings {volume_limit}` | per-cell agent settings (`/data/syng-agent/settings.json`) |
| `GET/POST /api/ring {steps, clockwise_up, ui_sound_pct}` | control-ring and UI-sound options (hidserver / celldirector options.conf) |
| `GET /api/fleet/live` | SyFi / time sync / Wi-Fi RSSI of every member, asked live |
| `GET /api/housekeeping` · `POST {action: vacuum_journal | clear_coredumps | restart_service, name}` | disk/RAM hygiene |
| `GET /api/cloudblock/all` · `POST /api/cloudblock {on, scope:"group"}` | cloud block status / toggle for the room |

The primary's agent also runs a **keeper** every 90 s: it refreshes each member's `ip_address`
(a DHCP change is picked up and re-fanned-out — seen live when the old primary came back as
<new-ip>), mirrors the roles celldirector elected into the document, and re-arms the USB link.

## Reverting

* Per cell: *Advanced → Restore pre-agent state* (`/data/syng-agent/state-before-agent.json`,
  written the first time the agent touched the document), or `PatchState` any of the
  `_re/cell/state-backup/<ip>.json` files from before the 6-cell experiment.
* Back to Syng's cloud: `syng-cloud-block off` on each cell (or the Advanced toggle) — the cloud
  then overwrites the local document with whatever it last knew (the 5-cell room).
* The calibrator patch: `/usr/lib/python3.7/site-packages/syng/pycalibrator/cloudapi.py.orig`.
* Agent: `/usr/bin/syng-agent.prev.bak` (b25).
* connman: `/etc/connman/main.conf.orig` (the only change is `usb` in `NetworkInterfaceBlacklist`,
  so connman never grabs the USB link and reroutes the cell's default route to a laptop).
