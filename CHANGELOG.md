# Changelog

Agent builds are numbered bNN; the published `agent/syng-agent.py` is the latest.

## apps — iPad (iOS) — 2026-10-18
- **A real iPad app**, not the phone view on a big screen. Rooms in a sidebar; a room is one screen with the sound
  field as large as the display allows and the controls beside it (landscape) or under it in two columns
  (portrait): now playing with big artwork, transport, the volume bar inline, Syng/Direct, Centre / Spatial / Mute,
  sources, scenes and the speakers, no sheets. Setup is two panes. Hover effects, keyboard shortcuts (Space, ⌘← ⌘→,
  ⌘0, ⌘S, ⌘M), 40k particles for the bigger field, the sidebar swipe disabled while the field is up, and the
  visualizer widget in the iPad's extra-large size. Narrow multitasking windows get the phone layout.
- The Setup sections are one list (`SetupSection`) shared by the phone index and the iPad panes.

## b58 + LED daemon — 2026-10-18
- **About a fifth of a CPU core back on every speaker.** Measured on idle cells: the agent sat at ~14 % and the LED
  daemon at ~14 %, on the same four A53 cores jackd needs (an underrun storm is what "one speaker plays late" was).
  - Agent: the viz receiver decoded every feed packet (~30/s, pure-Python msgpack plus a per-bin pass) on every
    cell, though only the primary's copy is ever read, and only while an app or widget is looking. Now it relays the
    raw packet to the LED daemon always, but decodes in full only while someone asked for viz in the last 5 s;
    otherwise one packet in eight, with the trackers' rates scaled so a one-shot `/api/viz/frame` still has real
    levels. The decoder reads the feed's int16 arrays in one `struct` call (5x faster). 14 % -> 3–4 %.
  - `syng-led-reactive`: same decoder fast path, band analysis without numpy, PWM files kept open (`pwrite`).
    15.3 % -> 2.9 % of a core in a profile on a Cell with captured packets; output identical to 2e-7.
  - Client hang-ups (an app cancelling a poll, a widget timing out) no longer log three-part tracebacks.
  - Not ours to fix: on the primary, Syng's `airplay2source` uses ~1 core while AirPlay plays (its `gcd-work`
    pool ~68 %, its audio thread ~30 %).
- **Android: Sound Field Visualizer widget**, the iOS one's twin — `FieldStill.kt` ports the CPU renderer; the glyph
  comes from the GL renderer's own drawing (`FieldRenderer.glyphBitmap`). Widget buttons and the app redraw both field
  widgets.

## apps — visualizer widget (iOS) — 2026-10-18
- **Sound Field Visualizer widget**, beside the room-plan one: the app's own field (aura, particles, glass puck,
  speaker rings) in whatever colour the speakers' LEDs are glowing, small, medium or large; medium and large add
  Centre, Classic/Spatial and width buttons. Widgets render once in the background, where iOS refuses GPU work,
  so `FieldStill.swift` is the Metal renderer ported to the CPU (the scene shader line for line, the particle
  kernel run until the cloud settles, the orbs through the same lens table), driven by the app's `FieldModel`
  from one `/api/state` + `/api/viz/frame`. A large widget draws in about a quarter of a second. The app asks for
  a redraw two seconds after any field change.

## b57 — 2026-10-18
- **Moving the sound field is heard straight away.** cellbroker applies a new position in 30–60 ms, but each
  `POST /api/soundfield` took about a second in the agent and the agent served them one at a time, so a
  two-second drag (a post every 110 ms) took 15 s to drain and the sound trailed the finger by seconds. The
  second went on D-Bus: every move read each source's `active` flag and the bass-distribution set through the
  shared bus connection, whose lock the status poll holds for most of a second. Now a move only updates the
  field and returns (~80 ms round trip from the LAN); one worker pushes the latest position, so moves that
  arrive mid-push merge into the next one and nothing can queue; `active` is read on the controls connection
  and cached 1.5 s; the bass check runs only when the snap target changes. Same drag: done 0.5 s after the
  last move, not 13 s. What remains is the stream's normal playout delay, which the Syng app has too.

## apps — touch fixes — 2026-10-17
- **No more accidental "back to Rooms" while dragging the field (iOS).** Recent iOS added swipe-back from
  anywhere in the content, not just the screen edge, and only the edge swipe was being blocked. On the Room
  tab every pan recogniser the navigation controller owns is now switched off (and restored exactly on the
  way out), and the system back button is replaced by our own "‹ Rooms", which stops iOS starting a
  back-swipe at all. The other tabs keep the stock behaviour. Android: the field excludes itself from the
  system back gesture along both edges, and no parent can intercept a drag that starts on it.
- **Volume never jumps.** The rim at the bottom of the field can be grabbed anywhere along the arc; the volume
  then moves by how far the finger travels from where it already is, and a tap changes nothing (a small dead
  zone before the drag engages). Same for the volume bar in the Sound sheet on iOS, which was a stock slider
  that jumped to the tapped point. Both platforms.

## b56 — 2026-10-17
- **The real cause of "a speaker plays half a second behind" — the agent itself.** The keeper ran
  `ip addr replace` on both USB setup ports of every cell every 90 s. `replace` re-installs an address that is
  already there, the kernel announces it (RTM_NEWADDR), mDNSResponder answers by withdrawing and re-announcing
  the whole cell, so every cell saw the whole room DISAPPEAR and APPEAR every 90 s (~150 flaps an hour), and
  celldirector rebuilt the group's audio links each time. Now and then a cell came back exactly one 32768-frame
  DAC buffer late (640 ms) and stayed there, jackd logging a constant "Playback underrun" storm (50–80/s; a
  healthy cell logs none). The primary went from 22:44, .138 from 01:30 — each on the minute of an address
  event on usb0/usb1 followed by the flap. `usbnet_up()` now only adds what is missing: 0 address events and
  0 flaps in the minutes after the deploy, where there had been one every 90 s.
- b55's microphone check was blind to this: it compared the primary with .138 only, both were one buffer
  late, so they agreed — and each check attached capture clients to the live audio engine. It no longer runs
  by itself; by hand (`GET /api/synccheck`) it now compares every member with every other.
- **Self-healing that touches nothing:** each cell reads its own jackd underrun rate from the journal every
  30 s (`GET /api/storm`); over 10/s on two checks in a row it restarts jackd (at most every 10 min) and logs
  it. After restarting jackd on .226 and .138: 0 underruns/s on all five Ball Room cells.

## b55 — 2026-10-17
- **Direct now plays through all three drivers.** It was built on cellbroker's fixed-focus override, and *any*
  override value (0.0, 0.5, 0.8, 1.0 — metered with all three driver outputs in the same window) switches every
  cell to a single driver: `system_out_2/3` at exactly −∞, back the moment the override is cleared. So Direct
  never touches focus now: it is the normal renderer with the field pinned to the classic puck at the centre (no
  steering), room correction off, listener-offset compensation off, bass distribution on. Measured on the
  primary: all three drivers live; each cell's own mic hears its speaker within 1.2 dB of the others. The
  field you set is kept and returns in Syng.
- **The primary playing ~half a second behind the group**, measured with the cells' own microphones (two
  cells record the room at the same NTP second; the log-RMS envelopes are cross-correlated): 640 ms late,
  r = 0.82 — exactly one 32768-frame DAC buffer. Restarting jackd brought it to 0 ms (r = 0.87); restarting
  syngsoundserver did not. Switching listener-offset compensation or room EQ live did *not* reproduce it
  (0 / −30 / −40 ms), so the trigger is still unknown — the agent measures instead of guessing:
  (superseded by b56, which found the cause) `GET /api/synccheck` (the two-mic measurement), `GET /api/synccheck/last`,
  `POST /api/capture/envelope {at, seconds}`, `POST /api/resync` (restart jackd, wait for the source, re-apply
  the mode). The check runs by itself 25 s after every sound-mode switch and every 30 min while music plays;
  an offset over 150 ms with a clear correlation restarts jackd (at most every 10 min) and re-measures.
  Only an 8 s envelope (100 numbers/s) leaves a cell; the recording is deleted at once. `sync_check: false`
  in `/api/settings` turns the automatic checks off. After the change: Syng −10 ms, Direct −30 ms, both r ≥ 0.97.
- Not the cause, for the record: jackd's "Playback underrun" storm on the primary (its idle SourceCast receiver
  stream; secondaries show none), CPU (the audio chain is pinned to cores 1–3, the agent and the LED daemon to
  core 0 — stopping both changed nothing), and the CPU governor (left at stock `powersave`).

## b53 / b54 — 2026-10-17
- **The primary silenced itself again — this time through snap-to-cell, and now it cannot.** Same symptom as b50
  (no audio from the primary, LEDs still reacting, secondaries fine), different mechanism. cellbroker excludes any
  cell the sound is *snapped* to from bass distribution (`ProxyManager.source_set_stem_positions` →
  `snapped_cells.add(...)` → `bass_distribution_proxy.set_disabled_cells(...)`), and that set is **only added to**:
  the one place it is cleared is `set_cell_state()`, i.e. when Syng's cloud pushes the room document back — which
  we deliberately block. So once the puck has snapped onto a cell, that cell stays out of bass distribution for
  good; and with bass distribution off for the *primary*, its own render is exactly zero (the b50 finding). The
  puck snaps to the nearest speaker ring and the primary sits where the listener marker is, so it is easy to hit.
  Found on the cell: `airplay2:out0` at −53 dBFS, `syngsoundserver:system_out_*` at −∞, and
  `disabledCells = ["…0HH3Q", "…GMB9Y"]` with GMB9Y being the primary itself.
- **b53**: the agent owns `disabledCells`. `bass_sync()` writes the set that matches the live field — the snapped
  cell, never this cell itself — after every handle push, after a sound-mode change and on each keeper pass, and
  logs it when it finds the cell muting itself. **b54**: a 5 s watchdog, because the keeper only comes round every
  90 s and this is the worst failure the firmware has.
- Verified by reproducing it: snapping the sound onto the primary used to leave it in `disabledCells` for ever;
  it now stays empty and the primary keeps rendering.

## b52 — 2026-10-17
- **The sound field glows what the speakers glow.** `syng-led-reactive` publishes its live colour to
  `/dev/shm/syng-led-rgbw` (~20 Hz, every mode goes through one PWM writer); the agent reads it and puts it on
  the viz feed as `led: [r,g,b,w]` (`/api/viz/frame` and the SSE stream), and `/api/led` reports it as `color`.
  A hand-picked colour (`/api/led/color`) publishes to the same place. The field — the dashboard's, and the
  native one in the iOS app — derives its whole palette from that colour: hue and some of the saturation from
  the light, the value/saturation structure kept from the app's own palettes so the field keeps its depth
  instead of flattening into a wash. White light or LEDs off falls back to the source's palette
  (`cfg.followLed`, toggle on the dashboard: "Colours follow the LEDs").
- With `--palette free` each speaker evolves its own colour, so the field follows the primary's; set the LED
  palette to **shared** for one colour across the room.

## b51 — 2026-10-17
- **Soundcheck quality.** Three Oct-16 runs had put the same speakers metres apart from run to run, while the layout Syng's cloud held (square + centre) matched the room. Same solver on both sides (pycalibrator's gradient-descent MDS on a ToF matrix, initialised from AoA) — ours ran against music, with a speaker from another room in the measurement, and one bad pair skews the unweighted fit. Now: playback is **paused** on every active source for the measurement and resumed after; the patched `pycalibrator/runner.py` (overlay) keeps each run's raw **ToF/AoA matrices** in `/data/syng-agent/calib-matrices.jsonl`; **Soundcheck ×3** (setup page, `POST /api/calibration/run {runs: 3}`) runs three passes and solves once on the per-pair median ToF / circular-median AoA with the firmware's own solver tried from every reference cell (lowest residual wins), reports residual and the pairs whose spread or reciprocity is off. Synthetic check: 3 cm max pairwise error vs 52 cm for a single run with two late arrivals.
- Ball Room layout restored to the cloud-calibrated one from the pre-agent backup (`_re/cell/state-backup/138.json`): the five original positions/rotations, circle (2.02, 1.21) r 2.74 m, rotation 2.4°, listener on the centre cell; the previous layout saved beside it.

## b50 — 2026-10-17
- **Direct mode silenced the primary (fixed).** Direct used to switch *bass distribution* off on every member; with it off, the primary's own output is exactly zero (its secondaries keep playing — the symptom was one silent speaker, whichever one is primary, since the re-election after the firmware rollout). Direct is now focus 0.0 + room EQ off + listener-offset compensation off (equal level everywhere is the point; the compensation was costing the cell next to the listener marker ~6 dB), bass distribution left on. Found by metering syngsoundserver's jack outputs on the cell: AirPlay in at −40 dBFS, `system_out_*` at −∞ until bass distribution was re-enabled.

## b49 — 2026-10-17
- **Wired cell-to-cell link** (setup page → NETWORK → Cell-to-cell link; `GET/POST /api/link`): the room's audio distribution and shared clock over Ethernet instead of SyFi. Findings and design in `docs/WIRED.md`: SyFi matters for the *clock* (the Wi-Fi chip's "cync" beacon-timer GPIO captured by `/dev/syngtimer`), but the ALSA plugin has an `ntp` time-sync mode that plays against the system wall clock, so PTP over the wire (linuxptp is in the image) makes Ethernet viable. Per member the agent sets `SourceCast.conf` `timesync: ntp` on the wired interface, runs `syng-ptp` (primary = grandmaster, secondaries clients, timesyncd off on clients), points the room document's `syfi_ip` at the wired address (main audio path over the wire, dual path stays on Wi-Fi) and restarts the audio stack once. Refuses unless every member has a wired address. Re-asserted after reboots / firmware updates; the keeper follows role changes.
- **`syng-ptp4l` / `syng-pmc`**: our static linuxptp 4.4 build (`tools/ptp/build.sh`, zig cross-compile, one patch) — the cells' RTL8153 USB NICs take software TX time stamps but the 5.4 kernel's r8152 doesn't advertise them, so stock `ptp4l` refuses the interface; ours warns and goes ahead (verified on a cell). Carried across firmware updates.
- **celldirector restart guard**: restarted while Wi-Fi is already up, stock celldirector never gets a "connected" event and drops into *WiFi repair mode* 20 s later (phase persisted; roles keep working). The agent bounces the Wi-Fi service once when it sees that after a restart it performed. This is what the b39 fresh cell ran into.
- `whoami` gains `wired_if`, `wired_ip`, `link_mode`, `ptp`; document entries carry `wired_ip`.

## apps — Android catches up — 2026-10-17
- **The sound field is native on Android too**, in OpenGL ES 3.1 (`apps/android/.../field/`): the same four passes
  as the Metal renderer, the ProtoTools simulation in a **compute shader** stepped at the app's fixed 30 Hz with
  the frame interpolating between steps, the particles read back as vertex attributes (ES 3.1 does not promise
  storage blocks in the vertex stage), and the glass orbs refracted through the app's lens table. Touches go
  straight to the model on the GL thread; pinch sets the width, two-finger twist aims, double-tap centres.
  A device without ES 3.1 compute falls back to the cell's WebGL field.
- **Now Playing and the field are one screen** on Android as well, and **Setup is native**: Room, Layout &
  Soundcheck, LEDs, Sound & controls, Scenes, Network, Firmware (upload a `.img.gz` from the phone) and System.
- iOS gained the same Sound & controls and Scenes screens (tone, volume ceiling, control ring; save / apply /
  schedule a scene, with the phone's time zone sent to the primary so a schedule fires locally) and cloud block.

## apps — native sound field (iOS) — 2026-10-17
- **Setup is native too** on iOS: Room (rename, members, make primary, remove, add a nearby speaker), **Layout &
  Soundcheck** (drag the speakers on a plan, tap one to turn it, fit a circle, hear it, Soundcheck ×1/×3 with the
  pass counter, residual and per-pair spread/reciprocity), LEDs (reactive modes, palette, colour, room-wide or
  one speaker), Network (Wi-Fi scan/join/forget, radios, the wired cell-to-cell link with live PTP offsets per
  member), Firmware (what is flashed, install, roll back, room-wide, upload a `.img.gz` from Files) and System
  (live fleet, stats, sleep timer, housekeeping). The cell's own page is still one tap away for the rest.
- Now Playing and the sound field are **one page**: the field fills the screen with a compact player under it.
- Fixes from the first run on a device: the puck's shadow is a contact shadow again (the app's 4.5-radius one
  read as a grey donut over this renderer's brighter aura); the volume rim only takes a drag that starts where
  the volume already sits (a tap elsewhere on the arc used to jump it); and the navigation edge-swipe is off
  while the field is on screen, so dragging near the left edge no longer pops back to Rooms.
- **The sound field is native on iOS** (`apps/ios/Troubadour/Sources/Field/`), no WebView: the web renderer ported
  to Metal in four passes — scene (room disc, capped-torus aura per-source palette, flurry blobs, PulseField halo)
  at 0.62 resolution, blit, particles, overlay. The **particle simulation moved into a compute kernel** (same
  ProtoTools physics, same fixed 30 Hz step), and frames interpolate between steps, so it holds the display's rate
  (120 Hz on ProMotion) where the WebView was spending its time in a CPU loop. The glass puck / notch / handles /
  lift dot are drawn analytically, refracting the scene through the app's own 64-entry lens table; the puck wears
  the source's glyph; speaker rings and the volume rim are in the same pass.
- **Touch, not pointer events**: UIKit touches straight to the model (no recognizer delay) — drag the puck, pull
  the dot above it out to go spatial, drag the notch to aim, a dot to widen, and the rim for volume; plus two
  gestures the page could not do: **pinch** for width and **two-finger twist** to aim, with **double-tap** to
  centre. Haptics when the puck snaps onto a speaker, when spatial engages or collapses, and every 5 % of volume.
- The field now takes its audio from `/api/viz/stream` (SSE) directly instead of the page's polling, and posts
  `/api/soundfield` at the same 110 ms throttle as the page. While nothing is being touched the agent stays the
  source of truth (2 s poll).

## apps — widgets and discovery — 2026-10-17
- **Home-screen widgets** on both platforms: *Sound Field* (the room from above — reference circle, speakers, listener, the puck or the two spatial dots — with buttons to centre, switch classic/spatial, widen/narrow and nudge) and *LEDs* (current mode, a button per reactive mode and off, room-wide). They act on the room last opened in the app; iOS buttons need iOS 17 (WidgetKit interactive intents, App Group `group.space.troubadour`), Android uses Glance.
- Discovery fixes after the first device test: Bonjour/NSD resolution pinned to IPv4 (iOS handed back the cells' IPv6 link-local and the app dropped it), a 2 s probe before the 25 s discovery call, a subnet sweep for :8080 as the fallback, Local Network denial surfaced in the message.
- **iOS and Android companion apps** (`apps/`). Thin native shells around the agent: Bonjour / NSD discovery of `_syng._tcp` (celldirector advertises it on every cell) then `/api/discover` on the first agent found; a native Now Playing (artwork, transport, sources, volume slider with haptic ticks sent at ~12 Hz while dragging, mute, Sound mode, Scenes); speaker list with Ping and long-press naming; the cells' own sound-field and `/setup` pages embedded in a web view, with the file picker wired through so a firmware image can be installed from the phone. iOS adds Siri / Shortcuts intents (room volume, scene, play/pause); Android adds a Quick Settings play/pause tile and launcher shortcuts (pause, mute). No accounts, no cloud, no third-party dependencies beyond OkHttp/Coil on Android. Android build compiles (`assembleDebug`); the iOS project is an XcodeGen spec and has not been built here (no Xcode) — expect at most small fixes on first build.

## b45–b48 — 2026-10-17
- **Reboots kept an old room (fixed).** cloudsyncd holds the room document in memory and writes `/data/syng/cloud_state.json` only on a cloud fetch / at start / now and then — never on `PatchState`, not on shutdown. A cell rebooted hours after a room change came back with the stale file (the fresh cell woke up as the primary of its old test room; the Ball Room cells' files still showed four neighbours). The agent now persists the live document after every patch, on every keeper pass and right before a firmware reboot (b45).
- **Speaker names** (b45): `display_name` in the cell's entry of the room document — travels with the room, survives reboots, shown in the hero, the speaker list (double-click to name), the room map, discovery and the layout table ("name" button). `POST /api/cell/name {serial, name}`.
- Room install skips members already on the image's artifact (b46); the slot write completes short writes (b47); **the slot being written is never mounted meanwhile** (b48) — the status call used to mount the idle slot read-only to read its version, the page polls status every 2 s during an install, and a mount that landed mid-write made the read-back verification fail (safely: the slot was never switched). Slot info is cached and skipped while busy, a lock serialises mounting and writing, the target is unmounted and flushed before the write, the page cache is dropped before the read-back.
- Rolled out: six Ball Room cells (five 2.2.1 → 2.2.2 in place) plus the fresh one, all committed; the room re-formed on its own (celldirector elected a new primary), SyFi and time sync good on every member.

## b44 — 2026-10-17
- **In-place firmware updates** (setup page → FIRMWARE): the cells' stock mender A/B slots, driven by the agent. Upload `troubadour-rootfs-<artifact>.img.gz` (now produced by `build-troubadour.sh`), install on this speaker or send to and install on every speaker in the room (secondaries first, primary last). Idle slot written and read back, this cell's Troubadour files and root password carried over, boot slot flipped with `upgrade_available=1`, commit after the stack is up, U-Boot rollback if not, "Boot the other slot" for manual rollback. Verified on a cell over Wi-Fi: ~6 minutes upload to commit. `docs/FIRMWARE-UPDATES.md`.
- **Network settings** (setup page → NETWORK → Network settings): Wi-Fi / Ethernet power (the agent refuses to switch off the only link it is reached on), every connman service with DHCP or static IPv4 (address, netmask, gateway, DNS), interfaces with MAC addresses. `/api/net`, `/api/net/power`, `/api/net/config`.

## b43 — 2026-10-17
- **The Syng app's look** on the dashboard and the setup page, as one system: pure black pages, borderless grey cards, white as the only accent (white capsule = primary action, outlined = secondary, selected = filled), the app's status green and destructive red, small grey uppercase section labels, black pucks with three amber dots for the cells (hero orb and room map). Dark is the default (the app is dark-only); **Light** is the same system inverted; Auto follows the OS. Taken from the 2.1.0 app (screenshots + bundle).
- **Typeface**: the app uses Graphik. The pages load it from `/fonts/` when the agent finds `Graphik-{Regular,Medium,Semibold,Bold}-App.ttf` in `/data/syng-agent/fonts/` (copy them from the app bundle's `syng-ui_SyngUI.bundle/`; they are Commercial Type's, not shipped here) and fall back to the system grotesk otherwise.

## b42 — 2026-10-17
- **Room map: commits carry only what the page changed.** A page left open with an older layout used to send its whole stale model on the first gesture and overwrite positions changed elsewhere (that is how a freshly placed cell went back to 0,0, on top of another dot). Now the circle, the dragged speakers and the listener are sent individually; the page refreshes from the room every 5 s while idle.
- **Zoom can't run away**: trackpad momentum once took the circle from 3.6 m to the 0.4 m floor in one flick, leaving every speaker off-screen. Per-tick zoom is capped at 6 %, zooming in stops where a speaker would leave the view, and the settle + save happen 300 ms after the last tick.
- **Discovery shows one row per cell** — a cell answers on every address it has (Wi-Fi, wired, both USB gadget links); a wired+Wi-Fi cell showed as four speakers, two of them "stock firmware" on 10.86.x.

## b41 — 2026-10-17
- **Sound mode: Syng / Direct** (dashboard, room-wide, persistent). *Direct* overrides the spatialiser's object focus to 0.0 through cellbroker's own `cell_set_objects_to_fixed_focus` — the hook Syng left in to "simulate omni/traditional speakers"; in the shaders focus 0 is what the LFE gets: "all cells playing towards outside" — so every speaker plays every object through all three drivers at the same level, plus room correction off and bass distribution off on every member. The primary re-applies it on handle pushes and the keeper re-applies it after restarts. *Syng* restores the stock steering (focus −1 = no override), EQ and bass distribution.
- A newly admitted uncalibrated speaker is placed on the reference circle in the widest gap between the others, facing in, instead of at (0, 0) on top of another dot.
- `/api/soundmode {mode}` (relayed from secondaries).

## b40 — 2026-10-17
- **A newly joined cell was silent** (LEDs pulsed — that's the viz relay — but no audio): the primary's SourceCast streams to each neighbour's `syfi_ip`, and only the cloud used to fill that in from the cells' uploads. `whoami` now exposes `syfi_ip` / `syfi_good` / `time_sync_good` / `bssid` (from `CellServer`), a new member's entry is created with them, the primary polls a just-admitted cell until its SyFi address appears and fans it out at once (`link_watch`), and the keeper keeps them current. Leave + re-join measured: audio on the new cell 22 s after the click.
- **Volume latency**: controls (volume, transport, tone, mute) use a private D-Bus connection and lock, so they never queue behind the status poll; `volume all` targets what is playing; the status computation is shared between all pollers (browsers and secondaries) at most once per 0.9 s; idle sources skip three D-Bus reads per poll; source ids cached. `POST /api/volume` on the busy primary went from 170–500 ms to 70–140 ms (a raw D-Bus Set is 2 ms; the rest is Python on a CPU that airplay2source + syngsoundserver + jackd keep at load 4). Slider throttle 120 → 70 ms.

## b39 — 2026-10-17
- **Never-set-up cell path, verified on a factory-fresh Troubadour 2.2.2 flash** (built with `tools/fw/build-troubadour.sh`, flashed with `flash.sh`): a cloudsyncd that never got a document from the cloud silently drops `PatchState`; the agent now seeds `/data/syng/cloud_state.json`, restarts cloudsyncd, sets celldirector's phase to `placed` and restarts it, then waits for the document to come back. Create room → PRIMARY with SyFi in ~30 s; join → secondary with time sync and audio in ~40 s. Details in SETUP.md.
- **connman order back to stock (`wifi,ethernet`)** in the overlay and the build script: with a wired adapter holding the default route, celldirector treated Wi-Fi as disconnected and went into permanent Wi-Fi repair mode (scanning every 12 s). Wired adapters still work for the setup link; a wired-only cell can't take a room role (roadmap).
- **`syng-cloud-block on`** now learns the cloud's current addresses before killing DNS (so the firewall half has something to reject) and restarts `authtokend` / `logmanager` / `crashreporter` too, dropping the cached JWT and any open sessions.
- `whoami.version` reads `/etc/mender/artifact_info` (really flashed) instead of the state document's `syng_os_version`; the setup page says why a wired-only speaker stays "configuring".
- `build-troubadour.sh`: no `debugfs mkdir` of existing directories (left an orphan inode behind — fsck now clean), no self-copy of `imx-boot` when the out-dir is the source dir.

## b38 — 2026-10-16
- **Field v38 — particles stop piling up at the rim.** Under continuous music every onset extended a particle's float window, so the home pull never ran and repeated impulses random-walked the reactive particles out to the reach cap, leaving the middle of the field empty. Now each particle remembers its resting radius (tracked slowly so the puck can still move it), the outward push fades to nothing once it is `puffReach` (0.2) beyond that radius, and a gentle home pull acts even while floating. 60 s of nonstop beats: mean radius flat at ~0.35 instead of climbing past 0.46.

## b37 — 2026-10-16
- **Room map, the app's way**: the reference circle is the fixed frame and you move the *group* — drag to pan, scroll / trackpad pinch to grow or shrink about the cursor, ⌥-drag / ⇧-scroll / two-finger twist to rotate, ↺↻ 90° buttons, arrow keys. Same solve as the app's `RoomMappingGestureRecognizer` (the physical point under each pointer stays put; two pointers give centre, radius and rotation), real two-finger support on touch screens and Safari trackpads. Speakers drag with centre / wall snapping (⇧ to skip), ticks turn them.
- **Live preview + auto-save**: while you drag, the primary applies the circle through cellbroker's `reference_circle` override (`/api/room/preview`, what the app's `ReferenceCircleUpdater` sends) so you hear it; the layout commits when you let go. Undo (⌘Z), *Identify in turn* (round-robin orientation sound with a pulsing dot), metre rings and a scale bar.

## b36 — 2026-10-16
- **Smooth puff outline** (field v36): a particle's onset impulse is interpolated between its two nearest spectrum bands (plus a quarter of the next pair), so neighbouring particles no longer get step-different pushes — the reactive ring's perimeter bulges in smooth curves instead of 24 jagged wedges.
- **Secondaries show what the room is playing**: a secondary's dashboard reads now-playing/transport/sleep/settings from the primary (`/api/nowplaying`, `viaPrimary` in `/api/state`), proxies the primary's artwork, and relays transport, volume, mute, tone, sound-field, spatial, source, sleep, objects and listener controls to it (`relayed` in the reply). Every cell's page is now a full remote for the room.

## b34–b35 — 2026-10-16
- **Puff physics** (field v34): music now pushes particles in the simulation instead of displacing them on the GPU — an outward velocity impulse per onset in the particle's own spectrum band plus a beat kick, capped speed/reach, a float window where the field barely pulls, then a gentle drift back to the radius they had. No more out-and-snap-back; the GPU pulse displacement is reduced to a shimmer. Sliders: Puff strength / on the beat / max speed / float time.
- **Per-source puck glyphs** from the app's `SourceGlyphs_*.pdf` (AirPlay, Spotify, Tidal, HDMI, Pure Data, USB-C, system audio, Syng stream), switched with the palette.
- Dashboard: identical `innerHTML` writes are skipped page-wide, so the 1.5 s refresh no longer re-renders unchanged cards (periodic jank).
- **Scenes** (b35): save the room's volume, tone, sound field, LED mode/tune/palette, height/beyond and volume limit under a name; tap to recall room-wide (LED on every cell); per-scene daily schedule with weekdays, run by the primary in the browser's time zone (cells are on UTC). Stored in `/data/syng-agent/settings.json`.

## b32–b33 + LED — 2026-10-16
- **LED `groove` mode** (syng-led-reactive): three layers — a 1.5 s "mood" level with a slow 12 s AGC (quiet passages stay dim), an 80 ms contrast-expanded transient layer, and a tempo tracker (bass onsets; when 4+ gaps agree within 0.28–1.25 s a phase-locked breath rides the beat between hits). Treble cools/whitens the tint, bass deepens saturation, white channel only on hits.
- **`flow` idle level = the floor**: the mode had a hard-coded 0.22 base, so the "dimmest" setting was still bright; now floor 0 is dark when silent.
- **Field v32**: the glow/flurry background renders at half resolution and is upscaled (the field is fragment-bound; 5× faster in the software renderer, the particles and orbs stay full-res), DPR capped at 1.5, and two particle populations — `reactiveFrac` (default 0.5) take the full music push, the rest mostly idle, like the app. Both are sliders in Field tweaks.
- **Field v33**: the field's colours follow the active source like the app's `Auras_*` palettes (AirPlay cyan, Spotify green, Tidal teal, HDMI indigo, Pure Data orange, USB grey), crossfading over ~1 s; `Colours follow the source` toggle.

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
