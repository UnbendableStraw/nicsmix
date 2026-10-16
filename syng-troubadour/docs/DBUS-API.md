# Syng Cell — `com.syng.*` control API (reverse-engineered)

> Written at b18 (2026-10-14) as the D-Bus field guide; still accurate for what it lists. Since then:
> the viz feed **is** wired (`/api/viz/stream`, `syng-led-reactive --source viz`), the sound field is
> driven through `cellbroker`'s `source_set_stem_positions` rather than `SetObjectPositions`
> (see SOUND-FIELD.md / CELL-INTERNALS.md §3), grouping is done through `PatchState` (CELL-INTERNALS
> §4–6), and the agent's REST surface has grown far past the table at the end (SETUP.md has the
> current one). `PatchState` *is* the live driver for room membership, positions, reference circle
> and listener — the "record layer only" caveat below holds for tone/volume.

The Cell's control plane is the **local system D-Bus**. Everything the app does
over the cloud ultimately lands on these objects. This is what `syng-agent`
bridges to HTTP. Capture it live on a dev/modded unit with
[`syng-dbus-dump`](../overlay/usr/bin/syng-dbus-dump), which introspects every
`com.syng.*` service and writes `/tmp/syng-api.txt`.

> Reverse-engineered by introspection + `dbus-monitor` captures of the official
> app. Signatures are as observed; treat as a field guide, not a spec.

## Tooling notes
- Use `dbus-send`/`gdbus`/`python3-dbus` locally over SSH. `busctl introspect`
  works but `busctl call` can hit a `format-table.c` assertion on this build —
  prefer `syng-dbus-dump` (python introspection) or `dbus-send`.
- Dev D-Bus-over-TCP (port 1010) is **not** enabled by this kit and isn't needed.

---

## Sources

Audio sources (`airplay2`, `spotify`, `tidal`, `hdmi`, `usbhost`, line-in, …) are
exposed under **`com.syng.AudioSourceServer`** (`ASRV`), interface
`com.syng.AudioSource` (`ASRC_IF`).

### The ownership gotcha (important)
Each source's **property object** `/com/syng/AudioSource/<id>` is **owned by that
source's own bus connection** (`:1.63`, etc.), **not** by `AudioSourceServer`.
Setting a property (tone, volume) via `AudioSourceServer` silently no-ops.

To find the real owner, read the **Runtime** object
`/com/syng/AudioSource/Runtime/<id>` (interface `com.syng.AudioSource.Runtime`):
it exposes `destination` (the owning bus name) and `path` (the property object).
Set the property on **that** destination+path. `syng-agent`'s `source_target()`
does exactly this.

### Select / activate a source
```
/com/syng/AudioSource/Runtime/<id>  com.syng.AudioSource.Runtime
  Activate()
  Deactivate()
```
(Verified working from the web UI.)

### Volume
`org.freedesktop.DBus.Properties.Set` on the source's **owner** destination+path:
```
iface   com.syng.AudioSource
prop    volume      variant double   (0.0 .. 1.0)
```

### Tone (EQ) — bass / tilt
`Properties.Set` on the source's **owner**, property `toneControl`, a variant
**string** holding JSON:
```json
{ "bass": 0.0, "tilt": 0.0, "mapping_version": "1.0.0", "version": "<UUID>" }
```
`bass`/`tilt` ~ ±1. `version` is a fresh UUID per change. Applied by
`dascoordinator` `SourceGraph::setToneControl` (low/high shelves) — it is a **live
DSP** path, distinct from the persisted state doc (see below).

---

## Listener & spatial

- **Listener position:** `/com/syng/AudioSource/ListenerOffset`, interface
  `com.syng.AudioSource.ListenerOffset`. `Properties.Set` the `position` property
  (a **string**) to JSON `{"x_m":..,"y_m":..,"version":"0"}`; also set `enable`=true.
  Room coordinates in metres. This is "where you're sitting" and reorients the mix.

- **Object positions (spatial mix) — `SetObjectPositions(s)`** on each source's
  Runtime object `/com/syng/AudioSource/Runtime/<id>` (interface
  `com.syng.AudioSource.Runtime`). The string arg is JSON:
  ```json
  {
    "listener_position": {"x_m": 2.01, "y_m": 1.22, "version": "0"},
    "objects": {
      "0": {"snapToHorn": false, "distanceAttenuation": false, "volumeDecibels": 0.0,
            "position": {"scope": "room", "focus": 0.8, "height_layer": 0,
                         "location": {"x": 2.06, "y": 3.16, "z": 0}}},
      "1": { ... }, …, "9": { ... }
    }
  }
  ```
  **10 objects** (keys `"0"`–`"9"`), each with `volumeDecibels` (−100 = muted),
  `focus` (0–1, image tightness), and a room-space `location`. For stereo content,
  objects 0–2 carry the audible image (0/0/−3 dB, focus 0.8–0.9, clustered ~1.9 m in
  front of centre) and 3–9 are parked and muted (−100 dB). The app's "move sound
  around" **rigidly translates the whole constellation** — the per-object offsets
  from the soundstage centre stay fixed while the centre moves. `syng-agent` stores
  that constellation (captured from the app) and re-centres it via `/api/objects`.
  The constellation is content-specific (this one is a stereo upmix); other content
  would produce a different natural layout.

---

## Rooms & grouping

- A **room == a group**: all cells in a room share a `room_id`.
- `GetState` returns `room_neighbours` — the full fleet topology (every cell, its
  role, IP, room, calibration), which is how any one agent can render the whole
  group and fan out operations.
- Grouping is coordinated by `celldirector` (`CellServer`, SyFi Primary/Secondary
  over the 2.4 GHz mesh `wsyng0`). Over Ethernet, pair with the SourceCast-over-eth
  settings in `/etc/syng/SourceCast.conf` (`timesync: ptp`, `timesyncInterface:
  eth0`, `dualPath: false`). Capturing the exact group/ungroup calls is a roadmap
  item.

---

## State document: `cloudsyncd` StateFetcher

```
GetState()            -> full JSON state (now-playing, group, room, volume,
                         room_neighbours, calibration, …)
PatchState(s json)    -> merge a patch into the state doc
```

**Caveat (learned the hard way):** the state doc is a **persistence / record**
layer, **not** the live DSP driver. `PatchState` updates the stored value (and the
app will show it) but does **not** apply tone/volume to the audio engine. To
actually change the sound you must call the **live** methods above (source-owner
`Properties.Set`). Use `GetState` for read/topology; use the live D-Bus methods
for control.

---

## LED

Service + interface `com.syng.Led`, object `/com/syng/ledmanager`. See
**[LED.md](LED.md)** for the full hardware model and animation format.
```
SetColor(i state, a d rgbw)   # state 5 = custom; rgbw MUST be length 4 [R,G,B,W], 0..1
PlayAnimation(s path)
PauseAnimation()
ResumeAnimation()
ClearAnimationQueue()
```

---

## Sensors

Service `com.syng.Sensor`, object `/com/syng/sensormanager`:
```
GetTemperatureReadings()  -> { amp/soc/… : °C }
GetSpkFaultCount()        -> { driver fault counters }
GetAccelerationVectors()  -> { x,y,z g }   (orientation / upside-down detect)
GetMMCLifetime()          -> { eMMC life estimate }
```

---

## Audio-reactive feed (not yet wired)

`com.syng.AudioSource.VisualizationDataBroadcast` — `subscribe` streams the audio
energy/spectrum the app uses for its orb sparkles + spectrum visualizer. Wiring
this into `syng-agent` (SSE to the browser) and into `syng-led-reactive` (instead
of the JACK/mic tap) is the cleanest way to get true, in-sync music reactivity on
every cell. Tracked in **[ROADMAP.md](ROADMAP.md)**.

---

## `syng-agent` REST surface

The agent exposes these (all JSON; served from any cell on `:8080`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | dashboard state: core, sensors, sources, room/fleet, `system` |
| GET | `/api/stats` | extra analytics / stat providers |
| GET | `/api/led` | LED status: reactive mode, modes, visions |
| GET | `/api/vision?name=` | read a vision JSON (for the editor) |
| POST | `/api/source` | `{id, on}` → Activate/Deactivate |
| POST | `/api/volume` | `{source, volume}` |
| POST | `/api/tone` | `{bass, tilt}` |
| POST | `/api/led/color` | `{r,g,b,w, scope}` |
| POST | `/api/led/reactive` | `{mode, scope}` |
| POST | `/api/led/anim` | `{action, name, scope}` (play/pause/resume/clear) |
| POST | `/api/vision` | `{name, content}` → save a vision |

`scope: "group"` fans the command out to peer agents; `scope: "this"` is local.
Control writes are logged to `/tmp/agent.log` for debugging (`grep TONE /tmp/agent.log`).
