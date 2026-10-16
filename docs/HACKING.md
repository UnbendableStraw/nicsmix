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
