# Hacking on Troubadour — workflow and gotchas

## Where things are
| Path | |
|---|---|
| `agent/syng-agent.py` | the agent, one file (Python 3.7 stdlib + python3-dbus; the page and `/setup` are embedded strings) |
| `agent/build/build_bNN.py` | each build is a patch script over the previous build's output; `history/` has the b19–b25 integrators |
| `overlay/` | what `tools/fw/build-troubadour.sh` stamps into a stock image (`usr/bin/syng-agent` must equal `agent/syng-agent.py`) |
| `field/field.js` | the sound-field renderer; `build_bNN.py` scripts patch the embedded copy and this file together |

## Edit → build → deploy
1. Copy the latest `agent/build/build_bNN.py`, bump the numbers, add `rep(old, new)` patches (each asserts the anchor text exists exactly once).
2. `python3 build_bNN.py` → `syng-agent-bNN.py`; it `py_compile`s the result. Render the page against fixtures with Playwright if you touched the UI (headless WebGL: `--use-gl=swiftshader --enable-webgl --ignore-gpu-blocklist`).
3. `agent/deploy.sh syng-agent-bNN.py <ip…>` — cat-over-ssh, keeps `/usr/bin/syng-agent.prev.bak`, restarts the unit, prints md5 and `is-active`.
4. Check `curl http://<ip>:8080/api/whoami` (`agent` shows the build) and `journalctl -u syng-agent -n 20` on the cell.
5. Copy the build to `agent/syng-agent.py` and `overlay/usr/bin/syng-agent`, add a CHANGELOG entry.

Roll back on a cell: `cp -f /usr/bin/syng-agent.prev.bak /usr/bin/syng-agent && systemctl restart syng-agent`.

## Things that bit us
- **Never read a DSP node's `State` / `GetAll` over D-Bus** (`ProcessorGraphRegistrar` graphs): it stalls the audio thread, the watchdog restarts jackd + syngsoundserver (they recover in ~20 s). Single `Parameter` reads are untested.
- `PatchState` merges top-level `cell_state`/`room_state` keys and **replaces** `room_neighbours`; only send a member the room-owned keys of its own `cell_state` (see `OWN_KEYS` in `localcloud.py`) or you wipe its sources/Wi-Fi fields.
- Restarting connman re-DHCPs the Wi-Fi: the cell may come back on a new IP and celldirector may re-elect the primary while it's away. The keeper fixes the room document; `deploy.sh` needs the new IPs.
- `pkill -f <pattern>` kills your own shell when the pattern is in your command line. `scp` from some sandboxes drops the connection; cat-over-ssh doesn't.
- Polling `wpa_cli status` every second floods the journal (`EAPOL: EAP Session-Id not available`).
- `busctl call` can hit a `format-table.c` assertion on this build; use `python3-dbus` / `dbus-send`, or `overlay/usr/bin/syng-dbus-dump` to introspect every `com.syng.*` service.
- udhcpd on the USB gadget interfaces: one instance per interface; the agent re-arms them every 90 s because a connman restart drops the addresses.
- Soundcheck results vary ~0.3 m between quiet runs and ~1 m with music playing; recalibrations are aligned to the previous layout, so run it quiet.
- Nothing here raises the volume above what the user set or resets a cell; keep it that way (`set_volume` is clamped 0–1, the volume-limit guard only lowers).

## Theme and typeface

The pages carry the Syng app's look (b43): colours are CSS variables on `:root` in both page templates
(`--bg --surface --surface2 --line --text --muted --accent --accent-ink --live --warn --bad`), dark by
default, with a light set under `:root[data-theme=light]` and `prefers-color-scheme`. The app's typeface
is Graphik; put `Graphik-Regular-App.ttf`, `-Medium-`, `-Semibold-`, `-Bold-` into `/data/syng-agent/fonts/`
on a cell (they're in the app bundle under `syng-ui_SyngUI.bundle/`) and the agent serves them at
`/fonts/<name>`; without them the system font is used. The font files are licensed and not part of this
repository.

## Debugging "one speaker is silent"

On the cell, `jack_lsp -c` shows the graph and `jack_meter -n -f 4 <port>` the level: `airplay2:out0` (or the source's
ports) is what comes in, `syngsoundserver:system_out_1..3` is what the spatialiser renders for *this* cell's three
drivers (secondaries receive audio inside jackd's ALSA plugin instead, so their `system_out` ports read −∞ even while
playing). In at −40 dBFS and out at −∞ means the render for this cell is zero: check the Direct/Syng state (focus
override, room EQ, **bass distribution — off zeroes the primary's own output**, listener-offset compensation) before
suspecting the DAC; `POST /api/ping {serial}` through `system_out` proves the output path. A listener marker sitting
on top of a speaker also pulls that speaker down several dB through the offset compensation.

The other way the primary goes quiet is **snap-to-cell**: cellbroker adds every cell the sound is snapped to
to `BassDistribution.disabledCells` and only ever clears that set when the *cloud* pushes the room document
back (`ProxyManager.set_cell_state`), which never happens with the cloud blocked. A primary left in that set
renders zero. Check it with

```sh
python3 -c "import dbus;b=dbus.SystemBus();p=dbus.Interface(b.get_object('com.syng.AudioSourceServer','/com/syng/AudioSource/BassDistribution'),'org.freedesktop.DBus.Properties');print(p.Get('com.syng.AudioSource.BassDistribution','disabledCells'))"
```

**A speaker playing ~0.6 s behind** is one DAC buffer (32768 frames) out, and it shows in that cell's jackd
log as a constant `Playback underrun` storm (50–80 a second, mostly hidden behind journald's "Suppressed N
messages from jackd"). In step, a cell logs none. `systemctl restart jackd` on that cell cures it. What put
cells there was a network flap: anything that re-adds an address (here: the agent's own `ip addr replace` on
usb0/usb1 every 90 s, fixed in b56) makes mDNSResponder re-announce the cell and every peer sees the room
vanish and reappear (`CELL DISAPPEARED` in celldirector's log). Since b56 the agent watches the storm itself
(`GET /api/storm`). Compare **all** cells when you measure: two cells late by the same buffer agree with each other.

**Measuring whether a speaker is in step.** Nothing inside the cell reports the room's acoustic alignment, so
measure it: record the room on two cells at the same NTP second (`jack_capture -d 10 -c 1 -p system:capture_1`
started by `while [ $(date +%s) -lt $T ]`), take the log-RMS envelope of each in 10 ms frames and
cross-correlate. Each mic is dominated by its own speaker, so the peak lag is how late one plays against the
other (±50 ms of start-time slack; a stuck primary showed up as +640 ms, r 0.82). The agent does exactly this
(`GET /api/synccheck`). A stuck primary is cured by restarting **jackd** — the extra latency lives in its
32768-frame DAC ring — not syngsoundserver.

**Fixed focus is single-driver.** `cell_set_objects_to_fixed_focus` with any value ≥ 0 makes every cell play
through one driver only (`system_out_2/3` go to −∞; meter all three ports in the same window, music varies too
much between sequential reads). `-1.0` clears it.

Since b53/b54 the agent keeps that property honest (`bass_sync()`, a 5 s watchdog) and logs
`BASS disabledCells [...] -> [...] (this cell was muting itself)` when it corrects it.
