# Roadmap & status

Running list of what's done and what's still open.

## Verified on hardware (2026-10-14, a group of 5 + a dev unit)
- [x] **Custom firmware flash (uuu), SSH, USB-ethernet.**
- [x] **AirPlay-over-Ethernet** mDNS fix.
- [x] **syng-agent** as a systemd service on every cell: live dashboard, sensors,
      analytics, room field, controls. Served from any speaker.
- [x] **CPU / memory / eMMC + sparkline graphs** — `/proc` + `statvfs`, populate correctly.
- [x] **Source select** (Activate/Deactivate).
- [x] **Tone control** from the web UI — audible; writes `toneControl` to each source
      owner (via the Runtime `destination`/`path`), confirmed by readback.
- [x] **Tone → Syng app sync** — `set_tone` also mirrors into `room_state.tone_control`
      via `cloudsyncd` `PatchState`, which is what the app's EQ screen reads.
- [x] **Volume control** from the web UI.
- [x] **Reactive LED from the real audio feed, no mic** — decoded
      `VisualizationDataBroadcast` (msgpack/UDP: `{sourceId, stems:[[88-bin dB]],
      stemsRms, stemsPeak}`); the primary's agent is the single per-IP subscriber and
      relays to each cell's LED daemon. `syng-led-reactive --source viz` maps the
      spectrum to the same bass/mid/treble/overall envelope so every mode works.
- [x] **Grouped-secondary reactivity** — secondaries have no local feed; the primary
      fans the feed to every cell, so all grouped cells react in sync.
- [x] **Color sync option** — `--palette shared|free` (web UI "Group color" toggle):
      shared derives hue from the PTP-synced wall clock so the room matches; free lets
      each cell evolve its own color. Beats pop brightness locally in both.
- [x] **Music-reactive web field** — the agent serves `/api/viz/frame` (smoothed
      energy, bass, 24-bin spectrum); the canvas field draws a radial spectrum,
      bass-pulsed glow, and energy-driven ripples/sparkles. The browser interpolates
      the ~15 fps feed toward 60 fps so motion is smooth. Live on primary + secondaries.
- [x] **More settings exposed in the web UI** — Mute; Spatial (Height processing +
      Beyond-stereo duplication, `enableHeightProcessing` / `enableBeyondStereoDuplication`);
      LED reactive tuning (Beat sensitivity, Brightness floor), alongside tone, volume,
      source select, and the LED mode/palette/color/animation/editor controls.

## Known limitations
- **Tone/volume → Syng app display does not update.** The app reads EQ from Syng's
  cloud, not the local device. `cloudsyncd` exposes only `GetState`/`PatchState`/
  `ForceRefresh` (no local publish/notify), and `PatchState` updates only the local
  doc. `set_tone` mirrors into `room_state.tone_control` so the local record stays
  correct, but pushing to the app would require the cloud (MQTT) — out of scope for a
  LAN-only build. App → web still reflects instantly.
- **`flow` LED mode** previously washed to white on the full-spectrum viz feed (its
  treble sparkle used the complementary hue); fixed to a near-hue low-sat shimmer.

## Verified on hardware (2026-10-14, continued)
- [x] **Listener position** — draggable ◎ on the web field; writes
      `ListenerOffset.position` (JSON `{x_m,y_m,version}`) + `enable`. `/api/state`
      reports the live offset so the marker reflects it.
- [x] **Room correction (EQ) toggle** — `OutputGraph.enableRoomEQs/disableRoomEQs`.
- [x] **Soundcheck status** — `DasCoordinator.Client` `GetIsReadyToCalibrate` +
      `GetRoomNoiseRMS` / `GetLongTermRoomNoiseRMS`, shown in the SOUNDCHECK card.
- [x] **Tone curve graph** — matches the app's `ToneControlGraph` (bass/tilt shelves).
- [x] Pulled the app's vocabulary from `SyngSpace` (iOS bundle): rooms = "Spaces",
      calibration = "Soundcheck"; font is Graphik (commercial — not shipped).

## Verified on hardware (2026-10-14, continued)
- [x] **App-faithful sound-field** — reworked the room field to match the Syng app's
      spatial screen (studied live via computer-use): circular room, teal→cyan aurora
      gradient that follows the source + reacts to audio, a glowing source **puck**
      (bezel ticks, broadcast icon, glow halo, rotation **handle**), 5 **speaker
      indicator rings** at real positions, a **"you"** listener marker, and a bottom
      **tick-arc**. Drag the puck to move the sound, the handle to rotate the field.
- [x] **Control lag fixed** — the field was polling the viz feed 25×/s and starving
      control POSTs in the browser's ~6-connection pool. Polls are now single-in-flight;
      a server-side D-Bus lock serializes the shared connection. Drags apply in ~100 ms.
- [x] **Soundstage controls** — "Center the sound" reset, a **focus/width** slider, and
      **rotation** (`/api/objects` takes `x_m,y_m,focus,rot`; rotates the constellation).
- [x] **Spatial "move sound around"** — captured the app's `SetObjectPositions` schema
      (10 objects; puck = rigid translate, handle = rotate/spread; `height_layer`/`z`
      stay 0 — the "tilt" is a visual metaphor for x,y spread). See DBUS-API.md.
- [x] **Tone curve graph fixed** — it was rendering only a flat baseline: the SVG was
      built with unquoted attributes ending in `/>`, so `<line>` never self-closed and
      the `<path>`/labels nested inside it unrendered. Quoting the attributes fixed it.

## Verified on hardware (2026-10-15 — app teardown + reproduction)
- [x] **Decompiled the Syng iOS app** (stripped arm64 Mach-O, ProtoTools/Continuum
      statically linked) with Ghidra + a string/selector-xref extractor. Recovered the
      real sound-field model, now documented in **[SOUND-FIELD.md](SOUND-FIELD.md)**:
      one listener puck (`SourceInstanceNode`) with `normalizedPosition`,
      `normalizedRadius`, `rotationInDegrees` (+ snapped), `surroundWidthInDegrees`
      (spread is an **angle**, default 25°, radius/focus 0.7); rotation in degrees with
      snapping (~0.25 step, 0.01 threshold) off a `ReferenceCircleCalculator`.
- [x] **Puck motion spring** — read the app's exact `UISpringTimingParameters`
      (mass 1, stiffness **218.566**, damping **29.568** → critically damped, ζ≈1.0,
      ω₀≈14.78 rad/s) from the binary and applied it to the web field's puck, with a
      slight scale-up on grab. Confirmed (feels right).
- [x] **eMMC health → human-readable** — `GetMMCLifetime` JEDEC registers translated:
      PRE-EOL → Normal/Warning/Urgent; `DEVICE_LIFE_TIME_EST_TYP_A/B` → "0–10% used"
      bands (main vs boot area); developed bad blocks, spare blocks, avg P/E wear.
      Raw values kept in a second tile.
- [x] **LED `flow` crossfades** — hue now eases to the next color (no color→dark→color):
      a slow fade when idle (`--silent-fade`, default 3.0s) and a faster fade on the
      beat (`--beat-fade`, 0.35s), both as web-UI sliders. Group-synced `shared` palette
      stays deterministic (fade computed from the clock near each cycle boundary).

## Verified on hardware (2026-10-15 — one-puck field, live, all 6 cells)
- [x] **Spatial model decoded from live D-Bus captures** — `SetObjectPositions` is a
      room-space (metres) placement of up to 10 objects around a fixed, calibrated
      listener. Directional = a 5-object fan (centre @0dB focus0.3, ±spread/2 @0dB
      focus0.6, ±spread·0.26 @-13dB, the rest parked & muted); **surround** (spread ≥
      ~300°) = a 10-object ring. Generator reproduces a captured frame exactly.
      Documented in **[SOUND-FIELD.md](SOUND-FIELD.md)**.
- [x] **Backend `set_soundfield(aim, spread, distance)`** + `POST /api/soundfield`.
      aim/distance place the sound image relative to the listener; spread is the
      angular width (≥300° → surround ring). Sends to the active music source; live
      test on the primary returned `set:1` and engaged surround while music played.
      `/api/state` now reports `room.field {aim,spread,distance,spatial}`.
- [x] **Field rebuilt to the app's one-puck model** — ported the polished renderer
      from `field-preview.html` into the agent: one big dark room circle centred on the
      listener, a glowing **puck** (= the sound image, AirPlay glyph, app spring) you
      drag to move the sound; **lift the dot** off it to go spatial and expose **two
      focus handles**; drag a handle to **aim & widen** — swing it round the back for
      full **surround**; glittery membrane fills the arc, concentric ripples, glossy
      direction notch, dim beveled handles; real **speaker beads** at their positions,
      a centre **"you"**, and the **volume** hatch on the bottom rim → `/api/volume`.
      Fed by the live `/api/viz/frame` energy/bass/spectrum. Verified headless
      (gestures post aim/distance, spread→surround, volume) and deployed to all 6 cells.
- [ ] **Retune by ear** (noted by user) — distance→metres scaling and the surround
      voicing may need small adjustments once auditioned against the real app.

## Verified on hardware (2026-10-15 evening — decrypted app → WebGL field, build 18, all 6 cells)
- [x] **Decrypted Syng Space 2.1.0 IPA analysed** — Metal shaders reveal the real engine
      (GPU particles in a capped-torus arc pushed by an audio `pulseForce`, SDF Orb/Capsule/
      Torus shapes, audio colour-matrix). Palette sampled from the running app. Details in
      [SOUND-FIELD.md](SOUND-FIELD.md).
- [x] **Build 17** — 2D renderer retuned to the sampled palette; stereo = omni glittery disc,
      spatial = directional lobe.
- [x] **Build 18 (superseded by b19–b31; the field was rebuilt again in b19)** — WebGL field: `glFieldSetup()`
      (SDF aura shader + 9,000-particle additive cloud) on `#fieldgl` behind the 2D `#field`
      UI canvas (puck/notch/handles/rings/hatch + all pointer input). 2D fallback when no WebGL.
      Verified headless (SwiftShader) incl. all gestures. Source of truth is now
      `publish/syng-troubadour/agent/syng-agent.py` (see HACKING.md); b18 kept as `syng-agent-b18.py`.
      Backups on each cell: `/usr/bin/syng-agent.b17.bak`.

## Next up (written at b18 — check against b19+ before starting; some may be done)
- [ ] **Audio-driven hue** — wire particle/aura colour to the spectrum like the app's
      `CalculateColorBins` → `colorMatrix` (currently fixed signature cyan).
- [ ] **Resting puck centred** — backend default `SOUND_DIST=2.3` puts the puck forward;
      the app rests with the puck on the listener. Decide default distance / visual mapping.
- [ ] **Retune by ear** — distance→metres scaling and surround ring voicing.
- [ ] **Hourly WiFi/audio dropout** (tabled; user changing UniFi config) — 2026-10-15 capture:
      zero gateway loss on all 6 for 56 min incl. the :34 mark → NOT a WiFi/DHCP drop. Glitches
      are AirPlay-layer (`Concealing Missing Samples`/`Late Samples` in `journalctl -u
      airplay2source`) on the **primary only** (iPhone→primary path). Music had stopped at
      19:15 so the :34 event wasn't caught with audio flowing. Re-test: play continuously
      across a :34 boundary, then read airplay2source journal timestamps. Note: polling
      `wpa_cli status` floods the journal with EAPOL lines — don't leave watchers running.
- Deploy notes: `device_commit_files` caches by basename → always use a fresh filename and
  check md5 on the Mac before scp; scp drops the exec bit → `chmod 755` after; never
  `pkill -f <pattern>` that also appears in the launching ssh command (kills the shell).

## Open build items (no hardware needed)
- [ ] **Full "Run Soundcheck"** — orchestrate `DasCoordinator` Prepare →
      `PlayStimulusAndRecordResponse` / `RecordOtherCellAndIdentifyLocation` →
      `DasCalibrator` EstimateTOFAndAOA/PhysicalLayout/Rir → EvaluateRoomEQ →
      `OutputGraph.loadRoomEQs`. Replicate the app's sequence exactly before firing
      (a wrong run can degrade sound). Status + room-noise are already exposed.
- [ ] **LED manual color / animations / editor** — confirm visually (Manual mode).
- [ ] **Group / ungroup controls** — capture the app's group/ungroup D-Bus calls.
- [ ] **Ethernet-only group sync** — SourceCast over eth; confirm sync.
- [ ] **Offline claim / setup** (Phase 3) — first-boot local claim, no cloud.
- [ ] **Agent auth** — the agent + REST are open on the LAN. Add a token.
- [ ] **Bake `syng-agent.service` enabled into the image** (unit ships in the overlay).
- [ ] **Exact color match** — the app's palette can be pulled from `Assets.car` if an
      exact visual match is wanted; the current orb/periwinkle theme is close in spirit.

## Build 19 (2026-10-16) — app-faithful sound field, from the decrypted IPA
- [x] **Metal shaders disassembled** (`split_metallib.py` + `llvm-dis`): exact particle
      kernel, capped-torus SDF, pulse-texture displacement, aura colour matrix. `_re/shaders/`.
- [x] **Assets.car parser** (`tools/carx.py`, BOM + CSI + chunked lzfse): Orb gloss maps,
      lens table (half floats), noise, AirPlay glyph, `Auras_*` palettes/flurry settings.
- [x] **Geometry fitted to the tutorial masks** (`NewFeatures20/*.m4v`): weight 0.8
      (ra 0.3 / rb 0.2), handles on the torus centre line at 0.6 R, field radius R − d,
      arc = dot angle, caps per the shader. Omni = weight 2 (sphere).
- [x] **`field/field.js`** — WebGL renderer: scene FBO (room disc, palette aura, 24 flurry
      blobs, grain) + 10k-particle CPU port of `updateParticle` rendered additively with the
      pulse-texture push; puck/handles/notch as refracting glass orbs (lens table + gloss
      maps) with the puck shadow ring; grab halos; critically-damped springs; volume rim.
      Standalone `field/demo.html` with synthetic music + Tweaks sliders.
- [x] **`syng-agent-b19.py`** — field embedded; `/api/soundfield` takes `rot` + `spatial`;
      `room.field` reports them; default distance 0. "Field tweaks" panel in the ROOM card
      (particles, glow, pulse, sparkle size, grain, flurry, weight, handle radius, metres per
      radius; persisted in localStorage) + Reset particles / Center / Classic buttons.
      Headless-verified (lift → spatial, dots widen/narrow, notch spins, puck moves, volume).
      **Not yet deployed** — `./deploy-b19.sh` (keeps `/usr/bin/syng-agent.b18.bak`).
- [ ] Audition the fan mapping (`SOUND_FAN_R`, rot vs aim) by ear; capture the app's
      *classic* (non-spatial) `SetObjectPositions` to pin what "sound all around" sends.
- [ ] Match the app screenshot's lobe offset (see SOUND-FIELD.md open point) — needs a
      screen recording of the app settling after a handle drag.
- [ ] Own twists: colour themes (other `Auras_*` palettes are decoded), spectrum→angle
      mapping options, speaker rings toggle (`cfg.showRings`), particle count presets.

- [x] **b20/b21** (2026-10-16, from the live app recording): classic = smooth blue disc (sparkles optional), speaker rings, PulseField halo around the puck, hued flurry, weight 0.88, raw/jittered pulse rows for the ripple texture, 18k finer sparkles, darker spatial base; Field tweaks panel with toggles. `deploy-b21.sh`.

- 2026-10-16 deploy notes: the **agent listens on :8080** (5050 is RepairDesk on the Mac, not the cells). Rollback copies on every cell: `/usr/bin/syng-agent.b18.bak`. SSH key for all six (incl. dev unit, whose root home is `/data/home/root`): `keys/syng_ed25519` (also baked into `overlay/etc/ssh/authorized_keys.d/root`). The Cowork sandbox must have LAN network access enabled for pushes; `curl --noproxy "*"` from it.

- [x] **b22** (2026-10-16): viz feed v2 — per-bin level (30 dB window vs decaying per-bin peak), per-bin **flux/onsets**, energy, bass, **beat**; pushed over **SSE `/api/viz/stream`** at the feed rate (30 fps) with polling fallback. Pulse texture rows are written per feed frame from the onsets (head interpolated between frames), so beats ripple the glitter outward; puck halo breathes with per-angle flux + beat. Deployed to all 6 (md5 89c1d616…). Generic `./deploy.sh syng-agent-bNN.py` (uses keys/, `cat`-over-ssh since scp drops the connection from the sandbox). Old builds in `_old/`.

- [x] **b23–b25** (2026-10-16): pulse texture power-of-two (the "particles don't move with the beat" root cause), app-like puck pulse (`pulseGain/Jitter/Emitter`), 30 Hz CPU sim + GPU interpolation for a smooth 60 fps, real spatialisation routed through the cell's own `cellbroker` shader (`CellRPC` → `source_set_stem_positions`, `panner_v2` handles in room-circle units, `skip_persist`), field tweaks panel, reference-circle rings + listener marker. `docs/CELL-INTERNALS.md` written from the firmware.
- [x] **Cloud blocked on all six** (approved): `syng-cloud-block` (unbound `always_nxdomain syng.space` + iptables REJECT); state backups in `_re/cell/state-backup/`.
- [x] **6 cells in one room** — the 5-cell cap was the app/cloud's. Verified: primary streams SourceCast to the dev unit (SyFi + dual path), time sync good.
- [x] **b26 — local cloud + `/setup`** (2026-10-16): the agent is the room authority. Discovery (LAN sweep + `_syng._tcp` mDNS), create/join/leave/remove/rename/pin, room map (drag cells/rotation/reference circle/listener), **Soundcheck on-device with results captured locally** (patched `pycalibrator/cloudapi.py`), recalibration aligned to the previous layout, keeper (IP/role drift), **USB-C setup link** (ECM gadget + udhcpd at 10.86.0.1), Wi-Fi provisioning via connman config files, cloud-block toggle + persistence unit, pre-agent state backup/restore. Verified live: leave → own room → re-join (~30 s), three Soundcheck runs (66–95 s), IP change of the old primary (.23 → .30) picked up and fanned out. Docs: `docs/SETUP.md`. Deployed to all six (md5 1d29b568…), overlay updated (`usr/bin/syng-agent`, `usr/bin/syng-cloud-block`, `etc/systemd/system/syng-cloud-block.service` + wants, `etc/connman/main.conf`, `usr/lib/python3.7/site-packages/syng/pycalibrator/cloudapi.py`).
- [x] **Fresh-cell path** — done in b39 on a factory-fresh Troubadour 2.2.2 flash (see SETUP.md "A speaker that was never set up"): cloudsyncd with no base document drops `PatchState`, so the agent seeds `/data/syng/cloud_state.json` and restarts cloudsyncd + celldirector with the phase already `placed`; create room → PRIMARY + SyFi in ~30 s, join → secondary with time sync and SourceCast audio in ~40 s. Rooms need Wi-Fi (wired-only → celldirector Wi-Fi repair mode).
- [~] **Wired rooms** (b49, `docs/WIRED.md`): link mode implemented (ntp timesync + PTP + syfi_ip over the wire); SyFi stays up as glue. **To verify with cables in**: PTP offsets on the USB NICs, audio alignment in Direct mode, a 6-cell room end to end. Then: Wi-Fi radio off — **done for standalone cells** (`docs/WIRED-ONLY.md`): celldirector patched (no force-power / autoconnect / repair timer) + `troubadour-pin-primary.service`. The real blocker was the cell's role stuck at `configuring` (no mesh) hanging airplay2source in `setupLocalCellProxy`, not just the repair timer. Multi-cell wired rooms still need wired role negotiation, >6 cells (SyFi `max_num_sta=64` in the hostapd config; the 5-cell cap was the app's).
- [ ] Roles after a primary hiccup: celldirector re-elects (seen .138 take over); the keeper mirrors it, but an explicit "make primary" on the page uses `MigratePrimary`/`PinPrimary` — verify on hardware.
- [ ] Calibration frame: the cloud evidently kept the very first frame (one cell at the origin); b26 reproduces that by aligning recalibrations. Variance ≈ 0.3 m quiet / 1 m with music; consider averaging two runs.
- [ ] Field polish still open: lobe offset vs the app screenshot, glitter grain, handle bubble look; bake chosen slider values as defaults.

- [x] **b27** (2026-10-16): cloud block toggles on the dashboard (this cell / whole room, live status), theme switch, field canvas no longer stretched after load (ResizeObserver + per-frame check, puck rescaled), tone-curve labels fixed, Housekeeping panel (journal/core dumps/RSS — found a 174 MB `airplay2source` core on one cell and a 203 MB RSS `airplay2source` after 75 days on another), fleet links.
- [x] **2.2.2 analysed** (`docs/FIRMWARE-2.2.2.md`): cloud-auth daemon + retries only; audio stack unchanged. `tools/fw/build-troubadour.sh` rebuilds Troubadour on any stock image (verified on 2.2.2: 19-path delta, fsck clean).
- [x] **Published layout** (README, CHANGELOG, LICENSE, .gitignore): keys, firmware images, per-installation captures excluded.
- [ ] Flash a 2.2.2-based build on one cell and re-run the b26 checks (join, Soundcheck) before moving the room.

- [x] **b28–b31** (overnight 2026-10-16): Ping, now-playing/transport/artwork, sleep timer, volume limit, ring settings, live fleet link status, snap-to-cell, hot-plug sources, persistent + re-applied field. See CHANGELOG.
- [ ] Parametric EQ / DSP page through `ProcessorGraphRegistrar` parameters — only after verifying single-parameter `Get`/`Set` doesn't trip the audio watchdog (reading a node's `State` does).
- [ ] Spotify login without the app: `.Auth AuthenticatePassword` exists on the Spotify source — test whether Spotify still accepts it (password auth was retired for third-party clients); `Authenticate(token)` wants a token the app got from Syng's cloud.
- [ ] Any WAV through the room via `SAPlayer.Play` — a "doorbell / announce" endpoint is a few lines.

- [x] **Companion apps** (2026-10-17, `apps/`): iOS (SwiftUI, XcodeGen) and Android (Compose) — NSD/Bonjour discovery, native Now Playing with haptic volume, speaker naming, Shortcuts / Quick Settings tile, agent pages embedded (field, setup incl. firmware upload from the phone). Android compiled; iOS not yet built (needs Xcode).
- [ ] Apps, next: native firmware upload with a progress bar (the embedded page already works); a Now Playing media notification / lock-screen controls (iOS `MPNowPlayingInfoCenter`, Android `MediaSession`); widgets (iOS WidgetKit volume/scene, Android home-screen tile); Wear/Watch volume knob; Dynamic Island / Live Activity for Soundcheck progress.
