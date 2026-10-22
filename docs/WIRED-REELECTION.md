# Troubadour — Wired Primary, SyFi Mesh, Reelection & Ops Playbook

_Reverse-engineering notes + operational playbook from the 2026-10-19/20 session. This is the authoritative
record of how a Syng Cell room forms its audio group over SyFi, how to make a cabled cell the primary, how
primary **reelection** works, and how to recover the fleet when a cell goes silent. Read with
`docs/CELL-INTERNALS.md` (celldirector/SyFi overview) and `docs/WIRED.md`._

---

## 0. Current fleet state (as left at end of session)

- **Room "Ball Room", 5 live cells.** One cell (serial `6JXFU…`, the one holding the USB-Ethernet adapter)
  is the **wired primary** on `eth0`; the other four (`6LGT35…`/.226, `6K3LB…HGZP`/.44,
  `6K3LB…HH3Q`/.138, `6KS77…`/.30) are Wi-Fi secondaries. All 5 audible.
- **SyFi mesh AP is pinned to 2.4 GHz channel 11** via a `syfi_channel=11` celldirector env override
  (systemd drop-in). This override is **staged on all 5 cells** and persists across reboot.
- **Auto-elect hook (`troubadour-autoprimary`) is DISABLED fleet-wide** — it needs the hardened logic in §6
  before it's safe to re-enable.
- **Syng cloud is currently UNBLOCKED** (the `/data/syng-cloud-blocked` marker is off). It should be
  re-blocked (`syng-cloud-block on`) for clean offline operation — see §4. It's operationally moot (the
  cloud auth path is dead) but leaving it open causes background auth-retry churn.
- `.169` is untouched stock, no backup — leave it alone.

Fleet IPs are **not stable** across this kind of work: the USB-Ethernet adapter's DHCP lease is `.174`
regardless of which cell holds it, so "`.174`" means "whichever cell is currently cabled," and its SSH host
key changes. Always SSH with `-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no`.

---

## 1. Flashing a new speaker — the confirmed flow

Your 4-step understanding is **correct**:

1. **Bring your own stock 2.2.2 image** (`image.sdcard[.lzma]` + `imx-boot`, from the Syng Firmware
   Updater.app package — see `docs/FIRMWARE-2.2.2.md`).
2. **Run one script to modify it:** `tools/fw/build-troubadour.sh <stock image> [out-dir]`. It stamps
   `overlay/` into the rootfs (partition 2) with `debugfs -w` (no root, no loop mounts; works on macOS with
   `brew install e2fsprogs xz`), sets up ssh + root password + your pubkey, masks `otamanager` (so cloud OTA
   can't revert the mods), enables our units, and writes `image.sdcard(.lzma)`, `imx-boot`, `flash.sh`,
   `syng-cell-flash.uuu`, and an OTA rootfs image into the out-dir.
3. **Flash it:** `cd <out-dir> && ./flash.sh` — puts the cell in USB serial-download mode and drives NXP
   `uuu` (`brew install uuu`, or the `uuu` bundled in the Updater app). Full-image write to eMMC, pins boot
   to slot A + the production dtb.
4. **Cell reboots into setup mode.** Then either set it up normally with the Syng app (confirmed), or never
   set it up and run **offline** — with or without AirPlay over Wi-Fi (all confirmed).

**One build script, as you wanted.** The variants (stock / snapfix / L150 / L50 / L0 airplay2source) are
handled by `troubadour-fw` on the cell (see §9) — the build bakes in a default and the switch tool lets you
A/B on the running cell without reflashing.

### 1a. Gaps to make a flashed cell match the running fleet (see §10 for the fix)
The builder as-committed stamps the overlay but does **not** yet: patch `airplay2source` (the seamless/L0
audio fix), add the `syfi_channel` override, or enable `troubadour-autoprimary`. §10 lists exactly what to
add so a fresh flash == the live fleet.

---

## 2. SyFi mesh internals (this is where tonight's pain came from)

**SyFi** is Syng's private cell-to-cell mesh. The **primary runs a hidden Wi-Fi AP on `wsyng0`**
(SSID `syfi-<room_id>`, hidden), and every **secondary joins it as a station on its own `wsyng0`**. The
primary streams the rendered PCM ("SourceCast", `pakUDP`, ~3–4 Mbps/cell) to each secondary's SyFi
link-local address; timesync ("cync") also rides SyFi.

Key facts established by RE this session:

- **`wsyng0` (SyFi) and `winfra0` (home Wi-Fi) are SEPARATE radios** (same `phy`? no — confirmed different
  channels simultaneously: `winfra0` on 5 GHz ch153 while `wsyng0` on 2.4 ch11). So home-Wi-Fi band does
  **not** physically constrain the SyFi channel. (Earlier single-radio theories were wrong.)
- The SyFi AP's hostapd config is **generated** at `/run/syfi_controller/syfi_hostapd.conf` by
  celldirector's `SyFiManager` (not by `/usr/sbin/syfi_ap_setup.sh`, which is a legacy path). `ps`:
  `hostapd -B … /run/syfi_controller/syfi_hostapd.conf`.
- **Channel selection (the crux):** celldirector logs
  `managing SyFi - (infra channel: {} use band: {})` → it derives the SyFi **band from the primary's infra
  (uplink) channel**. A **wired primary has no Wi-Fi infra channel → "infra channel: 0" → defaults to band 1
  (5 GHz)** → the AP lands on ch44/48/153. In a mixed room that strands cells and causes the "only N of 5
  play" symptom.
- **The override:** celldirector reads env var **`syfi_channel`** (`getCachedSyFiChannel` → "Cached Channel
  for SyFi: N - check for override" → "Selected Channel for SyFi: M"). Setting
  `Environment=syfi_channel=11` makes it emit `channel=11 / hw_mode=g` (it derives the correct 2.4 hw_mode
  from the channel). **This is the fix for wired-primary mesh reachability** — pin SyFi to a 2.4 channel all
  cells hold. Applied via a `celldirector.service.d` drop-in; **takes effect only after celldirector
  restarts** (env is read at process start).

Detecting who's actually on the mesh / receiving audio:
- Role ground truth: `busctl --system get-property com.syng.CellServer /com/syng/localcell
  com.syng.Cell.CoreServices role` (and `rolePinned`). **The cloud `GetState` document lags** — it kept
  showing 2 primaries when busctl said 1. Trust busctl + the radio associations over GetState.
- Which AP a secondary joined: `iw dev wsyng0 link` → "Connected to <BSSID>". The BSSID is the primary's
  `wsyng0` MAC (= its `syfi_ip` interface id).
- Receiving PCM: `wsyng0` RX byte-rate (~3–4 Mbps = getting the stream; near 0 = off the mesh). Primary's
  `wsyng0` TX ≈ N×3.8 Mbps.

---

## 3. celldirector roles, pinning, the cloud document

D-Bus: bus `com.syng.CellServer`, object `/com/syng/localcell`, interface `com.syng.Cell.CoreServices`.
Methods used: `PinPrimary`, `PinSecondary`, `UnpinRole`, `MigratePrimary(s)`, `KickInfraInterface`,
`KickSyFiInterface`. Properties: `role` (s), `rolePinned` (b).

- **`PinPrimary(X)` + `PinSecondary(oldPrimary)` is how you force a clean handover.** A plain
  `UnpinRole(old)` does **not** make the old primary yield — it stays primary and you get split-brain.
  `PinSecondary` on the old primary makes it tear down its AP so everyone migrates.
- **"Force removing cell {} due to local IP change"** (celldirector string): a **dual-homed** cell (both
  `eth0` and `winfra0` carrying an IP) makes celldirector think its address keeps changing → it drops &
  re-elects → primary bounces between cells. **Single-home the cell** (drop `winfra0` so it's eth0-only, or
  vice-versa) to stop the churn.
- `KickInfraInterface`/`KickSyFiInterface` did **not** reliably re-register a hot-plugged uplink this
  session; a `celldirector` restart (or reboot) is what actually re-reads interfaces.

---

## 4. Syng cloud block (offline mode) — `syng-cloud-block`

`/usr/bin/syng-cloud-block on|off|apply|status`. Block = unbound `local_zone syng.space always_nxdomain`
+ `iptables -I OUTPUT -d <learned cloud IP> -j REJECT`; marker `/data/syng-cloud-blocked`;
`syng-cloud-block.service` re-applies at boot via `apply`. Both `on` and `off` **restart mqttd cloudsyncd
authtokend logmanager crashreporter**.

- **Syng's cloud is NOT dead.** `api.prod.syng.space` resolves and the API responds (`{"message":"Not
  Found"}` at the root = a normal 404). BUT the **auth path is dead** — `authtokend` can't obtain a token
  (`Failed to get access token from authtokend`), so `cloudsyncd` can't actually sync a room document. So
  "turn the cloud back on to pull the authoritative config" does **not** work here; the room doc on the
  cells is authoritative (that's the whole point of offline mode).
- **Keep it blocked.** Leaving it unblocked makes syngsoundserver/authtokend spin on auth retries/timeouts
  in the background. (Left unblocked at end of session — re-block with `syng-cloud-block on` when you can
  tolerate the one-time service restart.)

---

## 5. Making a cell the WIRED primary (proven recipe)

Goal: AirPlay comes into one cabled cell over Ethernet (low jitter → L=0 is safe), cell-to-cell stays SyFi
over Wi-Fi.

1. **Pin SyFi to 2.4 ch11** so the wired primary's mesh AP is reachable by all:
   `printf '[Service]\nEnvironment=syfi_channel=11\n' > /etc/systemd/system/celldirector.service.d/troubadour-syfi.conf`
   `systemctl daemon-reload` (effective on next celldirector (re)start).
2. **Bring up eth0.** ~~Then single-home the cell by dropping `winfra0` via connman~~ — **this does not
   persist**: Syng's own "WPA supplicant for Infrastructure Connection" service re-joins home Wi-Fi from
   `/etc/wpa_supplicant.conf` at every boot regardless of connman's `AutoConnect=false` (seen 2026-10-20:
   autoconnect=false, yet `winfra0` up since boot). The primary therefore runs **dual-homed** (eth0 + winfra0
   on the same subnet) in practice — which is fine and is the intended design (Ethernet to the sender, SyFi on
   `wsyng0` to the secondaries) **provided `/etc/sysctl.d/90-troubadour-multihome.conf` is installed** (§8).
   14 h dual-homed on 2026-10-20 showed no celldirector "local IP change" churn.
3. **`PinPrimary`** the cabled cell; **`PinSecondary`** the old primary so it yields.
4. **Sequenced render restart** on the secondaries (see §7).

Caveat: with the single radio for home Wi-Fi being dropped, the cell's SyFi AP channel is now set purely by
the `syfi_channel` override (good — deterministic). Do **not** rely on the AP "following" the infra band.

---

## 6. Primary REELECTION (moving Ethernet to a different cell)

This is what broke repeatedly this session until the recipe was nailed. When the adapter moves to a
secondary and you want it to take over:

**Working sequence (done by hand this session; must be automated in the hook):**
1. Ensure the `syfi_channel=11` override is present **and celldirector on the target has been restarted
   with it in its env** (so when it becomes primary its AP is ch11, not 5 GHz).
2. Bring up `eth0` on the target (dual-homed for a moment).
3. **Single-home** the target to eth0 (drop its `winfra0`).
4. **`PinPrimary(target)` + `PinSecondary(oldPrimary)`.** The old primary tears down its ch11 AP; all other
   secondaries re-scan and join the target's ch11 AP (same channel → fast). Verify via `iw dev wsyng0 link`
   BSSIDs all == target's `wsyng0` MAC.
5. **Sequenced `systemctl restart jackd syngsoundserver` on each secondary** (see §7) — the transport
   pause/play is NOT enough after a handover.

**Pitfalls proven this session:**
- **Never `systemctl restart celldirector` on all cells at once** → two cells race to primary → split-brain
  (two ch11 APs, cells divided). Sequence it, or pin-flip instead of restarting.
- A dual-homed primary bounces (see §3). Single-home first.
- `GetState` will show 2 primaries during/after a pin-flip for a while (cloud-doc lag). Trust busctl.

**Hardened `troubadour-autoprimary` (TODO — the auto version):** on `eth0` carrier-up: ensure override env
(restart own celldirector once if needed) → single-home → `PinPrimary` self → `PinSecondary` the current
primary → wait for mesh convergence → sequenced render-restart on all secondaries. On carrier-down: reverse
(reconnect winfra0, `UnpinRole`, let the cabled cell take over). Debounce carrier ≥2 polls. The old hook
only did `PinPrimary` + a connman infra flip and is NOT sufficient — it's disabled for that reason.

---

## 7. The render-stuck problem & the fix (most important ops lesson)

**Symptom:** some cells are on the mesh and **receiving PCM** (wsyng0 RX ~3.5 Mbps) and their **LEDs react to
the music**, but they produce **no audible sound**. The LED reacts because the LED visualization is a
separate broadcast from the primary — it does **not** mean the cell is rendering audio locally.

**What it is NOT:** not the mesh (they're on it), not delivery (PCM arrives), not mute/volume (ALSA/amixer
identical across cells), not jack routing (identical). It's the Syng **render** stage on those cells sitting
idle after churn/handover.

**What does NOT reliably fix it:** AirPlay app pause/play (that only controls the source stream from the
Apple TV). The **Syng app** pause/play (which hits Syng's internal `airplay2` transport) fixes it
_sometimes_ — it worked when `.226` was primary, but after a primary handover 3 cells stayed stuck.

**What DOES fix it (reliable):** a **sequenced restart of the audio stack on the stuck cells**:
```
for IP in <silent cells>; do
  ssh root@$IP 'systemctl restart jackd syngsoundserver'
  sleep 11          # one at a time, so you don't knock out the cells that are working
done
```
This forces each cell to re-establish SourceCast-receive → render → ALSA → amp. This is the step that
finally got all 5 audible after the reelection. **Bake this into the auto-elect hook** (and it's a good
manual recovery any time a cell goes silent-but-LED-reactive).

The agent can trigger the Syng-style transport kick itself: `transport("airplay2","pause")` /
`...,"play")` (interface `com.syng.AudioSource.TransportControl`, lowercase methods `pause`/`play`). Resolve
the source owner via the agent's `source_target()` — do **not** brute-force introspect every D-Bus name
(that hangs).

---

## 8. Split-brain — cause & avoidance

Two primaries, each running a ch11 SyFi AP, cells divided between them. Caused this session by:
(a) restarting all 5 celldirectors simultaneously (race), and (b) pinning a new primary without forcing the
old one to secondary. Avoid by: never bulk-restart celldirector; always `PinSecondary` the outgoing primary;
verify with busctl `role` + `iw dev wsyng0 link` BSSIDs (all secondaries should share ONE BSSID).

---

## 9. Operational playbook (quick recovery)

- **A cell is silent but its LED reacts →** §7 sequenced `systemctl restart jackd syngsoundserver` on it.
- **Group bouncing / two primaries →** `busctl … role` on each; `PinPrimary` the one you want,
  `PinSecondary` the other(s); verify BSSIDs converge.
- **Cell stuck "configuring"/off mesh →** `systemctl restart celldirector` on that cell (re-scans + rejoins
  the primary's AP). Do it one cell at a time.
- **Mesh AP on the wrong (5 GHz) channel after wiring a primary →** confirm the `syfi_channel=11` drop-in is
  present, then restart that cell's celldirector.
- **Switch airplay2source variant (A/B L=0 vs L=50 vs stock) →** `troubadour-fw use <variant>` on the cell
  (`troubadour-fw list|status`); store at `/usr/lib/troubadour-fw/`. Fleet-wide:
  `_local/re/airplay-silence-fix/troubadour-fleet.sh`.
- **Deploy a new agent build →** `agent/deploy.sh agent/syng-agent.py <ips…>` (keeps `.prev.bak`; restarts
  only `syng-agent`, never touches audio). The b67 agent (unstick setting removed + AirPlay crash/recovery
  counter) is staged in the repo, NOT yet deployed fleet-wide.
- **SSH:** key at `_local/keys/syng_ed25519`; always `-o UserKnownHostsFile=/dev/null -o
  StrictHostKeyChecking=no` (host keys change as the adapter/lease moves).

---

## 10. Build-script TODO — make a fresh flash == the live fleet

Add to `overlay/` (so `build-troubadour.sh` stamps them):
1. **`overlay/etc/systemd/system/celldirector.service.d/troubadour-syfi.conf`** = `[Service]\nEnvironment=syfi_channel=11\n`
   (pins SyFi to 2.4 ch11 — essential for a wired primary).
2. **Patched `airplay2source`** — the seamless/L0 fix is NOT in the build. Either (a) ship a pre-patched
   binary + the `troubadour-fw` switch + variant store in the overlay, or (b) have `build-troubadour.sh`
   extract `/usr/bin/airplay2source` from the image, run `patch_snap2.py` then `patch_snap_L.py` (currently
   in `_local/re/airplay-silence-fix/` — move into `tools/fw/`), and write it back. Option (b) keeps
   "bring-your-own-firmware" honest. Base sha the patches assert: stock `airplay2source` dbcfb3cf.
3. **Sync `overlay/usr/bin/syng-agent`** to the latest `agent/syng-agent.py` (b67).
4. **Hardened `overlay/usr/bin/troubadour-autoprimary`** (§6) + decide whether to enable it in the build
   (`build-troubadour.sh` currently enables syng-agent, syng-cloud-block, syng-led-reactive — add the
   autoprimary `.wants` symlink only once the hardened hook is validated).
5. Keep `syng-cloud-block` enabled (offline by default) — already done by the build.


---

## 8. Dual-homed primary: ARP flux → AirPlay PTP "offset is invalid" → connects but never plays (2026-10-20)

**Symptom.** An Apple TV connected to the group (control channel up, metadata shown) but no audio; a phone
a few minutes later played fine. airplay2source never restarted.

**Log signature (primary, `journalctl -u airplay2source`):**
```
[Ptp@error] ERROR: ptpClockCtxSyncComplete: offset is invalid for gm c0956da90e590008
SetRateAndAnchorTime timeline mismatch after #10 tries
SetRateAndAnchor err = -71976          (50× in two minutes, then teardown)
```
The sender anchors playback on its PTP grandmaster's timeline; the primary can't lock to that clock, refuses
every anchor, renders nothing. A phone uses the *speaker* as grandmaster, so it sidesteps the failure.
Only the dual-homed primary showed it; the four single-homed secondaries had zero PTP errors.

**Cause.** eth0 (.174) and winfra0 (.192) on one subnet with stock `arp_ignore=0`: Linux answers ARP for either
address on either interface ("ARP flux") — verified live: another cell had the primary's Wi-Fi IP cached at
its **wired** MAC. Timing packets meant for the wired address can then arrive over Wi-Fi (jitter) and strict
`rp_filter=1` drops some (`IPReversePathFilter` counter), which breaks PTP's offset measurement.

**Fix (installed on all cells, in the overlay):** `/etc/sysctl.d/90-troubadour-multihome.conf` —
`arp_ignore=1`, `arp_announce=2`, `rp_filter=2` (loose). Each interface now only answers for its own address;
cross-interface arrivals aren't dropped. Non-disruptive (no link or session drops); a gratuitous ARP from each
interface refreshes the LAN's caches immediately. Harmless on single-homed cells, and needed for the
"Ethernet to every cell" option. **Watch for:** if `offset is invalid for gm` + `-71976` recur on the primary
with this in place, the next suspect is USB-NIC timestamp jitter on eth0 (WIRED.md: 100–500 µs).
