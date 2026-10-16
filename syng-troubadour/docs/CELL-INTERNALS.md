# Syng Cell internals — what runs on the speaker and how the app drives it

Recovered 2026-10-16 from the cells themselves (Troubadour 2.2.1): D-Bus introspection,
the Python half of the firmware (`/usr/lib/python3.7/site-packages/syng`, copied to
`_re/cell/syng-py.tgz`), binary strings, and live experiments. This is the basis for the
offline setup/grouping/calibration work.

## 1. Process map

| service | what it is |
|---|---|
| `celldirector` (C++) | room/role manager: discovers cells (`Browser`, mDNS), elects **primary/secondary**, manages **SyFi** (a hidden 802.11 AP the primary runs, `syfi-<roomid>` / `syngspace-<suffix>`, interface `wsyng0`, used for time sync + SourceCast audio between cells), SSH key exchange between cells (`sshkeymanager`), the **SetupManager** (first boot, BLE bonding with the app, "ready-to-setup" sound), exports `com.syng.CellServer` |
| `cloudsyncd` (C++) | holds the **cloud state document** (`GET https://api.prod.syng.space/cell/state`, login via device cert at `/cell/login`, MQTT push via `mqttd`). D-Bus `com.syng.cloudsyncd /com/syng/StateFetcher`: `GetState`, `PatchState(json)`, `ForceRefresh`, property `state`. **`PatchState` emits `PropertiesChanged(state)`** → every on-cell observer (`syngnative.CloudStateObserver`) re-reads it. This is the offline injection point. |
| `cellbroker` (Python) | **the local control API the app uses on the LAN**: JSON-RPC 2.0 over `wss://<cell>:8765` (TLS with the device cert, **no auth**). `ProxyManager.py` = the methods, `shaders.py` = the spatialisation ("panner_v2" handle → 10 object positions). Persists changes to the cloud over MQTT (`cell/<serial>/sources`, …). |
| `cloudbroker` (Python) | cloud→cell request relay |
| `syngsoundserver` (C++) | audio engine; `com.syng.AudioSourceServer` (sources, `Runtime/<id>.SetObjectPositions`, `ListenerOffset`, `BassDistribution`, `CellLayoutOverride.cellLayoutOverrideJSON`), `com.syng.OutputGraph` (room EQ), `VisualizationDataBroadcast` |
| `dascoordinator` (C++) + `pycalibrator` (Python, `com.syng.DasCalibrator`) | **Soundcheck**: `DasCalibrator/Runner.Run()` runs the whole calibration on-device (round-robin chirps, ToF/AoA, physical layout, room EQ, wall detection) and then `PUT /v2/cell/calibration` with `[{serial_number, nearby_walls, position{x,y,z,rotation}}]`. The cloud stores positions + computes the reference circle and pushes state back. |
| `airplay2source`, `spotifysource`, `tidalsource`, `hdmisource`, `usbhostsource`, `saplayer` | sources |
| `ledmanager`, `hidserver` (control ring), `sensormanager`, `resetmanager`, `otamanager`, `rpcd` | misc |

Cells SSH into each other as `root` (keys synced through the cloud state `ssh_key`/`ssh_host`
fields) — calibration uses that to drive remote `CalibrationClientProxy`/`CalibrationAnalysisProxy`.

## 2. The cloud state document (`/data/syng/cloud_state.json`)

```
cell_state:      serial_number, place_id (home), room_id, role, role_pinned, ip_address, syfi_ip,
                 ssh_key, ssh_host, model, syng_os_version, online, calibrated, is_upside_down,
                 height_layer, position {x,y,z,rotation}, nearby_walls[{angle_deg,distance_m,likelihood,close_to_wall}],
                 sources[{id, active, channelLayout, positions{"0": panner_v2 handle}, shader_name, volume, ...}]
room_state:      place_id, room_id, room_name, listener_position{x_m,y_m,version},
                 reference_circle{x_m,y_m,radius_m,rotation_deg,version}, tone_control{bass,tilt,version,mapping_version}
room_neighbours: [cell_state of every other cell in the room]
```
`celldirector` takes `room_id`/`place_id`/`role` from it ("Ignoring cell X as we are not assigned
to a room", "Cell X @ ip not known yet from cloud or different Room"), `ProxyManager` takes cell
positions, walls, reference circle, listener, tone and per-source handles from it.

## 3. The sound-field control path (what the app really does)

```
app  ──wss 8765──►  cellbroker.source_set_stem_positions(source_id, handles)
                        └─ shaders.default_stereo_shader(handles, reference_circle, cell_positions, walls, listener)
                              └─ AudioSource.Runtime/<id>.SetObjectPositions(json)   (10 objects, room metres)
                        └─ MQTT cell/<serial>/sources STEM_POSITION_UPDATE  (cloud persists, pushes to the room)
```
The handle (`type: panner_v2`):

| field | meaning |
|---|---|
| `x`, `y` | puck position in **room-circle units**: the reference circle is radius 1, centre 0, **y up**, rotated by `reference_circle.rotation_deg` |
| `rotation` | field direction, degrees **CCW, 0 = +x, 90 = top of the room view** |
| `surround_width` | the dots' half-angle in degrees (app default 120; the dots are drawn at ±width) |
| `radius` | 0 → **classic** (`legacy_default_stereo_shader`, the 10-object surround ring); 0.7 → spatial 3-puck upmix (L/R/C at ±width/0 on a circle of `radius` around the puck, surrounds at 1.3·radius·±width/2). > 0.8 fades to omni "playing outwards". **Lifting the dot = radius 0→0.7; dragging the bar into the puck = radius 0.** |
| `snap_to_cell`, `snapped_rotation` | snapping the sound to one cell (serial) |

Build 25 of `syng-agent` drives this exact path (`CellRPC` → `source_set_stem_positions` with
`skip_persist: true`, so nothing goes to the cloud) instead of its own object fan.

Other RPC methods (dict params): `room_set_listener_position({x_m,y_m,version})`,
`room_set_tone_control({bass,tilt,version,mapping_version})`, `room_calibrate()`,
`source_activate/deactivate/mute/unmute/set_volume`, `source_get_object_positions` (debug),
`cell_set_height_layer`, `dev_only_room_set_reference_circle` (cloud only). Legacy router
(positional params): `get_sources`, `subscribe_viz(ip, port)`, `check_ota`, `reboot_cell`,
`factory_reset`, raw D-Bus `call/get_value/set_value`.

## 4. Grouping, roles, SyFi

* A room = cells sharing `room_id` (and `place_id`) **in the cloud state**. celldirector then
  finds them on the LAN, the primary brings up the SyFi AP, secondaries join it, roles are
  elected by `roleAcqTime` (pin with `CellServer.PinPrimary/PinSecondary/MigratePrimary`).
* The SyFi hostapd config has no `max_num_sta`; the 5-cell ceiling is the app/cloud (and the
  Wi-Fi chip's AP client limit — "AP-REJECTED-MAX-STA" is handled). SourceCast: `/etc/syng/SourceCast.conf`
  (`timesyncInterface: wsyng0`, `dualPath: true`, `protocol: pakUDP`). Going past 5 means moving
  time sync + SourceCast onto the wired LAN and keeping SyFi off (`celldirector options.conf: manage_syfi`).
* Setup: `/data/syng/celldirector_state.json {"phase": "placed"}`; first boot → "ready-to-setup"
  sound + BLE bonding tokens (`BLEBondingTokenDeviceAuthRequest`), Wi-Fi via connman.

## 5. Offline architecture (plan)

1. **Local cloud = the agent.** The agent on every cell keeps a room document; the elected
   coordinator (primary) is the writer. Any change (membership, positions, reference circle,
   listener, tone, handles) → `PatchState` on **every** member's `cloudsyncd` → all on-cell
   consumers react exactly as with Syng's cloud. The real cloud is unreachable offline, so the
   doc never gets overwritten (and `otamanager` is already masked).
2. **Calibration**: trigger `DasCalibrator/Runner.Run()`; patch `pycalibrator/cloudapi.put_calibration_results`
   to POST the results to the local agent (`/api/calibration/results`) which writes positions,
   walls, `calibrated: true`, computes the reference circle (circumscribed circle of the cells,
   rotation 0 until the user rotates it) and fans the state out.
3. **Setup page** (`/setup`): fresh cell on Ethernet → name it, create a room or join one found on
   the LAN (agents discover each other over mDNS/SSDP-style UDP beacon), pick upside-down /
   height layer, run Soundcheck, drag/rotate the room map, done. Reposition/recalibrate = the same
   page on an existing room.
4. **>5 cells**: wired SourceCast (`timesyncInterface` → the Ethernet interface), SyFi off.

## 6. What b26 found and fixed (2026-10-16, verified on the six cells)

* **SSH keys**: `cell_state.ssh_key` is `/data/home/root/.ssh/id_ed25519.pub` (made by
  `sshdgenkeys` on first boot); `ssh_host` is `"<ip> <contents of /data/ssh/ssh_host_ed25519_key.pub>"`.
  `sshkeymanager` turns neighbours' `ssh_key`/`ssh_host` into `authorized_keys`/`known_hosts`.
* **mDNS**: celldirector advertises `_syng._tcp` as `syng<serial>` port 22 with TXT
  `homeID`, `roomID`, `role`. The agent's `discover` asks for that record (own tiny mDNS client)
  and sweeps the local /24 for other agents.
* **Serial** when there is no state document yet: `CellServer /com/syng/localcell`
  property `uniqueID` (also `roomID`, `roomName`, `role`, `syfiGood`, `timeSyncGood`, `infraIP`,
  `syfiSSID`/`syfiPass`, `cellIsUpsideDown`, `cellPosition`, `listenerPosition`).
* **Reference circle / listener rule** (fitted on Ball Room to 1e-6): listener default = centre
  of the smallest enclosing circle of the cell positions; `reference_circle.radius_m` = that
  circle's radius / 0.85 (the cells sit at 85 % of the room circle); rotation 0 until rotated.
* **Calibration**: `com.syng.DasCalibrator /com/syng/DasCalibrator/Runner`, interface
  **`com.syng.DasCalibrator`** (not `.Runner` as the source constant suggests), method `Run()`.
  Round robin ≈ 6 s per cell, ToF/AoA 7 s, PLE 22 s, MC room EQ + walls, then
  `cloudapi.put_calibration_results([{serial_number, nearby_walls, position{x,y,z,rotation}}])`
  — 66–95 s for six cells. Positions come out in the calibrator's own frame (translation +
  rotation differ run to run; the inter-cell distances agree with the cloud's to ~0.3 m in a quiet
  room, ~1 m with music playing). The patched `cloudapi.py` POSTs the list to the local agent first
  (`CLOUD_BLOCKED` when `/data/syng-cloud-blocked` exists skips the cloud and the couch upload).
  pycalibrator restarts itself after every run (`Restarting pycalibrator`).
* **USB**: both USB-C ports are chipidea dual-role (`ci_hdrc.0/1`, typec tcpci); `usbgadgetsource`
  builds a configfs gadget `g0`/`g1` = **ECM Ethernet (`usb0`/`usb1`) + UAC2 audio** ("Syng, Inc. Cell
  Alpha", 0x3397:0x1010). Stock only brings the interfaces up with `/devdata/devenable`
  (`usbethernetup.service`). The agent gives them 10.86.0.1/24 and 10.86.1.1/24 and runs `udhcpd`
  (`/tmp/udhcpd-usbN.conf`) so a laptop on a cable lands on the setup page. `usb` is added to
  connman's `NetworkInterfaceBlacklist` (restarting connman re-DHCPs the Wi-Fi: the old primary
  came back as <new-ip> and celldirector re-elected another cell as primary while it was away).
* **PatchState semantics**: top-level keys of `cell_state` merge, `room_state` merges,
  `room_neighbours` is replaced whole. The agent only sends the room-owned keys of a member's own
  `cell_state` (`serial_number, room_id, place_id, role, role_pinned, calibrated, position,
  nearby_walls, is_upside_down, height_layer, online, ip_address, version`) so a cell's own
  sources/Wi-Fi/OTA fields are never clobbered.
* **ProxyManager** builds the cell layout from `[cell] + neighbours` filtered by `online` — an
  uncalibrated member at (0,0) is in the spatialiser until Soundcheck places it.
* **Leave/join live**: leaving a room = `PatchState` of a fresh `room_state` with role primary →
  celldirector claims PRIMARY, starts its own SyFi AP and ignores the old members; joining =
  the target primary adds the entry and fans out → "CELL APPEARED … role configuring → SECONDARY",
  SyFi association, time sync good, SourceCast streaming, ~30 s end to end.
* **Cloud block persistence**: `syng-cloud-block apply` + `syng-cloud-block.service`
  (`ConditionPathExists=/data/syng-cloud-blocked`, before cloudsyncd/mqttd) — until b26 the block
  was lost on reboot.

## 7. More surfaces (found while adding b28–b31)

* **Ping** = `com.syng.SAPlayer /com/syng/SAPlayer PlayOneShot(path, level)` with
  `/usr/lib/syng/sounds/multi-cell-orientation.wav` (the three-note "ta ta ta"; `Play(path, loop, level)`,
  `Load/Unload/Stop` exist too — any WAV on the cell can be played through the room this way) plus
  `CellServer.IdentifyCell()` for the LED. `ui_sound_vol_pct` in celldirector's options.conf is the level.
* **Per-source D-Bus** (owner = the source's bus name, path from `AudioSource/Runtime/<id>`): interfaces
  `.TransportControl` (`play pause stop next previous seek enableShuffle disableShuffle disableRepeat
  enableRepeatContext enableRepeatTrack`; props `playing position repeatMode shuffling supportedControls`),
  `.Metadata` (`title artist album duration`), `.Artwork` (`artworkUrl`), `.Auth` on Spotify
  (`Authenticate(token)`, `AuthenticatePassword(user, pass)`, `authenticated`, `username`), and the base
  interface (`volume mute toneControl enableHeightProcessing enableBeyondStereoDuplication mixType
  mediaType badgingType channelLayout outputChannelLayout state displayName`).
* **DSP graphs** — `/usr/lib/syng/graphs/output/*.ag` and `source/*.ag` are JUCE AudioProcessorGraph
  JSON: the output graph is the speaker's voicing (ManualTuningEQ, RoomEQ1/2 written by Soundcheck,
  per-driver delay/EQ/FIR/gain for woofer, tweeter and mid beam, limiters, `ReportAdditionalLatency`).
  At runtime `com.syng.ProcessorGraphRegistrar` lists every graph (`_com_syng_outputgraph`,
  `_com_syng_sourcegraph_<source>`) with `Destination`/`Path`; under each graph every node is an object
  (`com.syng.AudioProcessor.Node`: `name`, `State`) and every parameter a child
  (`com.syng.AudioProcessor.Parameter`: `Name Min Max Value NormalizedValue`, read-write). **Careful:**
  reading a node's `State` (or `GetAll` on a node) stalls the audio thread — on the dev unit the
  `AudioThreadWatchDogAlert` fired after 12 s and jackd + syngsoundserver were restarted by systemd
  (recovered by themselves in ~20 s). A parametric-EQ page is possible through the `Parameter`
  objects, but needs testing with `Get` on single parameters only, never `State`/`GetAll` on nodes.
* **Sources that register on hot-plug**: the USB-C ports (UAC2 gadget) and the USB-host input appear
  as their own AudioSource ids; the agent now treats any non-calibration, non-`file@` source as
  selectable.
* The app's field: `snap_to_cell` in the `panner_v2` handle (serial) + `snapped_rotation`;
  `shaders.py` `SNAP_TO_CELL_DISTANCE_THRESHOLD = 0.09` is the legacy distance rule, the app sends the
  serial explicitly (the "SnappingEllipse"/"PointSnappingDragSession" classes in the binary).
