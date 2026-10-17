# Cell-to-cell over Ethernet ("Wired mode")

What it takes to run a room's inter-cell link — audio distribution *and* the shared clock — over wired Ethernet
instead of SyFi. Findings from the firmware (2.2.1/2.2.2 binaries) and a live two-cell experiment (2026-10-17),
and the design the agent implements.

## How the stock link works

Two things travel between the primary and its secondaries:

1. **Audio** — SourceCast, `pakUDP`, from `syngsoundserver` on the primary to the ALSA plugin (`libasound_module_pcm_syng.so`,
   jackd's "device") on every secondary. It is already **dual path**: *main* packets go to each neighbour's
   `syfi_ip` (an IPv6 link-local on `wsyng0`, the SyFi interface), *dual-path* copies go to the neighbour's
   `ip_address` (the infra IPv4). The receiver uses whichever arrives (`scast_rx_dualPathSaves_pkts`). Both
   addresses come from the room document (`room_neighbours[].syfi_ip / ip_address`), which Troubadour's agent owns.

2. **The clock** — this is the real reason SyFi exists. The Broadcom Wi-Fi chip's "**cync**" feature
   (`wl cync_config`, set up in `/usr/sbin/syfi_ap_setup.sh` / `syfi_sta_setup.sh`) shares the SyFi AP's beacon
   timer (TSF) with every associated STA and toggles a GPIO on it; a Syng kernel driver (`syngtimer.ko`, i.MX8
   GPT input capture, `/dev/syngtimer`) time-stamps those edges against the system clock
   (`get_device_system_crosststamp`). The ALSA plugin's `CyncClockConverter` turns that into a room-wide
   hardware clock (published as `/dev/shm/syficlock`, `syng::SharedClockState`), disciplines the DAC to it and
   reports `timeSyncGood`. That gives microsecond-level sample alignment across cells. There is no equivalent
   signal on Ethernet.

   But the plugin ships **three** clock converters, selected by `"timesync"` in `/etc/syng/SourceCast.conf`:

   | value | converter | shared clock |
   |---|---|---|
   | `cync` (default) | `CyncClockConverter` | SyFi TSF via GPIO capture |
   | `ntp` | `NTPClockConverter` | **`CLOCK_REALTIME`** — the system wall clock, counted at 48 kHz (verified: logged "SyFi time" / 48000 = the wall-clock time to the millisecond) |
   | `freerunning` | `FreeRunningClockConverter` | none (each cell drifts) |

   So Syng left a software path in: with `ntp`, every cell plays against its own wall clock, and how well the
   room stays in sync is exactly how well the cells' wall clocks agree. Stock, that is `systemd-timesyncd`
   against Google NTP with `PollIntervalMinSec=16` (ms-level, wandering). The image also ships **linuxptp**
   (`ptp4l`, `phc2sys`, `pmc`) — on a wired LAN, `ptp4l` in software-timestamp mode holds a few tens of µs
   between hosts, which is the lever that makes `ntp` mode good enough.

## What we measured (Garage cell + spare cell, Wi-Fi only, SyFi off)

- `timesync: ntp` + `manage_syfi: false` on both: jackd's clock tracker locks within 2 s on both and
  `timeSyncGood` is true (it is tracking the wall clock, not each other — see above). The primary streams
  main packets to the (now dead) `syfi_ip` and dual-path packets to the infra IP; audio would ride the dual path.
- `ptp4l -S` master on the primary, slave on the secondary, **over Wi-Fi**: works mechanically (multicast PTP
  passes, the slave steps then slews `CLOCK_REALTIME`), offsets ±2–17 ms with ~4 ms path delay — Wi-Fi, as
  expected, useless for this; the wired numbers will be two to three orders better. jackd takes the slewing in
  stride ("switched to new clock train, remote jumped 15–50 usecs").
- **celldirector restart bug (stock):** 20 s after celldirector starts it enters *WiFi repair mode* unless a
  Wi-Fi "connected" event arrives in between. On a cold boot Wi-Fi connects after celldirector starts, so the
  event comes. On a *restart* with Wi-Fi already up, nothing changes and the timer fires — phase `wifiRepair`
  (BLE setup advertising, Wi-Fi scanning; roles and SyFi keep working, the phase is persisted to
  `celldirector_state.json`). Independent of SyFi/manage_syfi — it bit both with stock config too. Cure: a
  Wi-Fi state change (reconnect) or a reboot; the agent now bounces the Wi-Fi service after any celldirector
  restart it performs if the phase goes to `wifiRepair`. This is also what happened to the wired-only fresh
  cell in b39.
- `manage_syfi: false` therefore is **not** needed and not wanted: SyFi can stay up as celldirector's glue
  (it costs nothing idle) while audio and clock go over the wire. Wi-Fi radio off remains a later, separate
  step (needs `wifi_repair_timeout` raised and the boot-timer call in celldirector patched out).

## The design (agent, "Wired" link mode per room)

Prerequisite: every member of the room has a wired IPv4 (USB-Ethernet dongle; connman brings it up, our
overlay preloads the common dongle drivers). The agent refuses to switch a room with a member that has none.

Switching a room to *Wired* (primary fans this out to every member):

1. `/etc/syng/SourceCast.conf`: `"timesync": "ntp"`, `"timesyncInterface": "<wired iface>"` (backup kept in
   `/data/syng-agent/`); restart `syngsoundserver` and `jackd` (a few seconds of silence, once).
2. **PTP** on the wired interface: `ptp4l -f /data/syng-agent/ptp4l.conf -i <iface>` (software time stamping,
   E2E, `step_threshold 0` = step once then only slew). Primary: `priority1 10`; secondaries: `slaveOnly 1`.
   `systemd-timesyncd` stopped on secondaries (would fight ptp4l), kept on the primary (its wall clock stays
   NTP-true and the others follow it). The keeper re-writes the config and restarts ptp4l on role changes.
3. Room document: every member's `syfi_ip` ← its wired IPv4 (so *main* packets go over the wire); `ip_address`
   stays the infra IP, so the dual path still runs over Wi-Fi as redundancy (or over the wire too, once Wi-Fi
   is eventually turned off).
4. celldirector untouched: `manage_syfi` stays true, SyFi forms as usual and simply carries nothing.

Switching back to *SyFi* restores the config files, stops ptp4l, starts timesyncd, restores `syfi_ip` from
`CellServer.syfiIP`, restarts the audio stack.

Status surfaces: `/api/link` reports the mode, each member's wired address, ptp4l state and the current
offset/path delay (parsed from `pmc GET CURRENT_DATA_SET` / ptp4l summaries), and `timeSyncGood`.

## What to expect / verify when the cables are in

- `ptp4l` slave log: `master offset` settling to within ±50 µs, `path delay` a few hundred µs on USB dongles.
  Anything in the ms means a bad cable/switch path (or a dongle without software timestamps — `ethtool -T`).
- jackd: `timeSyncGood: true`, no stream of "remote jumped" lines once settled.
- `syngsoundserver` on the primary: `Streaming main packets to (<iface>) <wired ip>:<port>`.
- Listening test in Direct mode (every speaker plays the same mix): walk between two cells — any flanging /
  comb-filter "swirl" in the overlap zone means they are more than ~0.3 ms apart.
