# Syng Cell — Offline / LAN-only control: architecture & plan

Goal: custom firmware that sets up and runs the Cells **entirely on the LAN** (no Syng
cloud, no internet), keeping stock features — AirPlay + local inputs, spatial audio, EQ,
room placement/calibration, grouping — controlled from a **web UI served by any speaker**
(and a Mac app later; iOS/Android after that).

## 1. Feasibility verdict (from RE)

The control plane is already **local D-Bus**; the cloud is a convenience layer on top.

| Function | Where it runs | Cloud needed? |
|---|---|---|
| AirPlay2 receiver | `airplay2source` + mDNS | No (working over Ethernet) |
| HDMI / USB / line in | `hdmisource` / `usbhostsource` / `saplayer` | No |
| **Spotify / Tidal** | `spotifysource` / `tidalsource` | **Yes — internet + account tokens** |
| Spatial audio / mix | `syngsoundserver` `MixEngine` | No (on-device DSP) |
| EQ / tone | `syngsoundserver` (`setBassDistNormParam`, mix params) | No |
| Room placement / calibration | `pycalibrator` + mic `CalibrationCaptureClient` | No |
| Grouping | `celldirector` (SyFi Primary/Secondary) + SourceCast audio | No |
| Discovery | Bonjour `_syng._tcp` (celldirector browser/advertiser) | No |
| LED | `ledmanager` (`com.syng.Led`) | No |
| Remote (off-LAN) control | `mqttd` → `*.syng.space:8883` | Yes — we drop this |
| Account / claim | `cloudbroker` / `cloudsyncd` | Yes — we replace with local claim |
| OTA / analytics | `otamanager` / Datadog | Yes — already masked |

**Conclusion:** buildable on top of the existing engines. The only stock feature that
cannot be made offline is paid streaming services (they need internet + Syng-brokered
tokens). AirPlay is the offline input of record.

## 2. Target architecture

```
        ┌─────────────────── each Cell (custom fw) ───────────────────┐
browser │  syng-agent (NEW)                                           │
  ──────┼─▶ HTTP + WebSocket  ──▶ local system D-Bus ──▶ celldirector  │
 (hit   │   serves web UI         (dbus_next, py3.7)     syngsoundserver│
 any    │   REST/WS API                                  pycalibrator   │
 speaker│   mDNS browse _syng._tcp                        ledmanager     │
 IP)    │        │  agent-to-agent HTTP (fleet ops)       NetManager     │
        └────────┼─────────────────────────────────────────────────────┘
                 ▼
        other Cells' syng-agents (same binary) on the LAN
```

- **`syng-agent`** — a small service we add to the firmware. Written in **on-device Python 3.7**
  (already present) using `dbus_next` (present) for async D-Bus and `http.server`/a tiny WS lib.
  No cross-compiling, no new native deps.
- Each Cell runs its own agent and drives **its own** local D-Bus (no raw D-Bus exposed on the
  network — cleaner/safer than dev-mode D-Bus-over-TCP:1010).
- Any Cell's agent serves the **web UI**; it discovers peers via the existing Bonjour
  `_syng._tcp` records (or by asking `celldirector`'s `/com/syng/remotecells`) and fans fleet
  operations out to peer agents over HTTP.
- **Local claim/setup** replaces the cloud claim: set device name, room, and group membership
  by writing the same on-device stores the engines already read (`/data`, `/devdata`), so the
  Cell is usable with no account.

## 3. Feature → local control mapping (what the agent calls)

- **Grouping / rooms:** `celldirector` `CellServer` — `claimRole`, remote-cell add/remove,
  SyFi Primary/Secondary. Over Ethernet, pair with the SourceCast-over-eth work (separate note).
- **Spatial / EQ / tone:** `syngsoundserver` MixEngine params + `/etc/syng/SourceCast.conf`.
- **Calibration / placement:** `pycalibrator` (trigger + store room geometry; it already uses the
  far-field mics and a convex-hull speaker layout).
- **Sources:** select/activate `airplay2source`, `hdmisource`, `usbhostsource`, `saplayer`.
- **LED:** `com.syng.Led` (`SetColor`, `PlayAnimation`) + our `syng-led-reactive`.
- **Network:** `NetManager` (ethernet/wifi, the Ethernet-first work).

Exact method signatures are the main open item — see §5.

## 4. Phased roadmap

- **Phase 0 — API map (next):** introspect the live D-Bus surface on a dev unit and write the
  canonical list of services/objects/methods/signals for every function above. Turns §3 from
  names into a real API.
- **Phase 1 — read-only agent + web UI:** `syng-agent` serves a page that lists all Cells
  (discovery), shows state (now-playing, group, room, volume), live. Proves discovery + D-Bus
  bridge end-to-end. No writes yet.
- **Phase 2 — control:** volume, source select, group create/join/leave, EQ/tone, LED. Fleet ops
  via agent-to-agent.
- **Phase 3 — setup/claim offline:** first-boot local claim (name/room/group) with no cloud; fold
  into the firmware image so a freshly flashed Cell is usable from the web UI with zero internet.
- **Phase 4 — calibration/placement UI:** drive `pycalibrator`, store/restore room layouts.
- **Phase 5 — Mac app:** thin native wrapper over the same agent API (then iOS/Android).

## 5. Open RE items (resolve in Phase 0)

1. **D-Bus API map** — `busctl introspect` each `com.syng.*` service on a running unit.
2. **Claim gating** — confirm what (if anything) hard-fails with the cloud fully blocked
   (test on an isolated VLAN). No blatant "disabled until claimed" string was found, which is
   encouraging, but verify by test.
3. **Calibration data format** — where `pycalibrator` stores room geometry, so the UI can
   read/write it.
4. **Source control methods** — how a source is selected/started locally (vs cloud/MQTT today).

## 6. Immediate next step

On a dev-enabled Cell over SSH:
```sh
busctl list | grep syng
for s in $(busctl list | awk '/com.syng/{print $1}'); do
  echo "==== $s ===="; busctl introspect "$s" / 2>/dev/null | head -40
done
```
Paste that and Phase 0 becomes concrete — I'll turn it into the agent's API client and we start
on the read-only web UI.
