# Working on Troubadour — workflow and gotchas

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

## Debugging "the grouped speakers drop out for a moment, every hour"

Captured 2026-10-17/18 (stock firmware had the same symptom for months). Signature on every **secondary** of a
group, within ~16 ms of each other, at the same minute and second every hour (here :34:12, often a second one 5–7 s
later): `jackd ... NetworkDepacketizer.cpp:210: FIFO underrun after N good pkts` with N ≈ 720 000 = one hour at
200 packets/s. A cell alone in its room never logs it (it has no network stream). Restarting cells or APs does not
move the minute; restarting the gateway does.

What the packet-level capture showed (per-packet timestamps on the primary and four secondaries, 5 Hz
`iw station dump`):
- The primary **sent steadily** on both paths (max gap ~55 ms).
- Every secondary's **home-Wi-Fi path went dark for 0.2–0.5 s**, twice, 5 s apart; the Syfi (cell-to-cell, `wsyng0`)
  path was unaffected (dualPath carried it that hour, so no underrun was logged).
- At those exact moments the primary's `tx failed` jumped by 70–80 (the AP stopped acknowledging); no retries, no
  beacon loss, steady signal on the receivers.
So the AP's 5 GHz radio briefly stops serving the BSS: an off-channel scan. Every radio of both U6 Enterprise APs
was on **Channel: Auto**, which has the APs scan periodically on a schedule the controller (in the gateway) keeps.

Ruled out on the way: WPA group rekey (no FW supplicant on this brcmfmac — `FWSUP feature check failed` — and
wpa_supplicant never logged one), cell bgscan (`bgscan=""`), DHCP / NTP / IPv6 RA / IGMP (all regular, none hourly),
the sender stalling. A separate irregular dropout (around :20 on some days, 1 s+) has the same receive-side signature.

Fixes, in order: fixed channels on the APs (5 GHz 153 / 36 as they sit, 2.4 GHz 1); then, if anything is left, a
larger SourceCast network buffer (`maxNetworkLatency_ms` 50 -> ~250) so a short AP hiccup is absorbed. Note b61:
`/api/discover` no longer sweeps the /24 on every call (it ARP-stormed the Wi-Fi every 15 s while an app was open).

Scanned access point's logs and struck some concrete info;

What causes it: UniFi’s wireless daemon (wevent) keeps a list of the other access points on your network so it can help devices roam between them. To keep that list current, every hour at :34:12 the LivingRoom access point leaves channel 153 to measure the Hallway access point on channel 36. It does this twice, about 8–10 seconds apart. For that moment it stops answering the primary speaker, the primary’s radio freezes on both Wi-Fi and SyFi, and the group runs dry.

Why :34: the timer started when the controller last re-provisioned the access point (Oct 8, 14:13), and has kept that phase since. That’s why only a full network-stack restart ever moved it.
Why only some hours of the night are bad: at :14 the same timer handles 2.4 GHz, where both access points share channel 1, so no channel hop is needed. At :54 it hops on the 6 GHz radio, which the speakers don’t use.

Fixes, all UniFi changes for you to make:

Put both access points’ 5 GHz on the same channel (for example both on 153, or both on 36). The access point skips the hop for a neighbor on its own channel, exactly as it does on 2.4 GHz, so this should end the hourly dropout. The trade-off is that both share one channel’s capacity.
Turn off BSS Transition and Roaming Assistance on your speakers’ network and watch the next :34. This might stop the neighbor checks, but I couldn’t confirm which setting drives them. The startup script forces 802.11k measurement on regardless of UniFi’s toggle.
Raise the group buffer to about 600–800 ms in the GROUP STREAM card. The longest silence I measured was about 0.6 s, so the dropout would become inaudible whatever the access point does. Audio takes slightly longer to start; music doesn’t mind.
Long term, wire the primary speaker with Ethernet. Its radio would then only carry SyFi, so an access point hiccup couldn’t freeze it.

## Debugging "AirPlay stops answering for a minute or two"

- `journalctl -u airplay2source` shows `terminate called after throwing an instance of 'sdbus::Error'` with
  `what(): [System.Error.EBUSY] Failed to exit a variant` (or `ENXIO … Failed to enter a variant`), then systemd's
  `code=dumped, status=6/ABRT` two-plus minutes later. Seen 2026-10-15 and 2026-10-18, both just after a spatial stream
  started: `Format Changed: APAC_48KHz_7_1_4`, `New channel layout: L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs`.
  Not every spatial start crashes. It predates our firmware.
- The gap is the core dump: `/usr/bin/syng-coredump` pipes a ~150 MB core into systemd-coredump, which compresses it
  until its own runtime limit stops it (leaving half-written `.#core.airplay2source.*` in /var/lib/systemd/coredump);
  AirPlay is gone throughout. With `LimitCORE=0` the dump is skipped and the process is reaped in ~0.3 s
  (Setup → AirPlay & Atmos → Quick recovery).
- The throw itself is inside airplay2source (stock code, sdbus-c++). In `AirPlay2Source::initializeFormat` the call to
  `Provider::setChannelLayout` is wrapped (it logs "D-Bus error occurred while setChannelLayout"), but the exception
  that kills the process is raised by D-Bus work elsewhere in starting a stream, where nothing catches it. A variant
  enter/exit failing with EBUSY/ENXIO is what sd-bus reports when a message is being built or read on one connection
  from two threads at once (airplay2source runs 11 threads), but enter/exit are the *read* side, so a likelier cause is
  an incoming message whose variant has an unexpected type, read in a handler without a catch. Not identified yet. Reported to
  Syng (docs/SYNG-AIRPLAY.md).
- Atmos itself works: the decoder is created once Apple sends its config, and the source produces 12 channels. Syng's
  badge (`badgingType`) says "Stereo" regardless.
- Don't write `enableHeightProcessing`: cellbroker owns it (`set_room_state` → `enable_height_processing(have_height_cells)`,
  true once the room has more than one height layer) and rewrites it on every room-state change. b63 forced it and
  the two fought. The real control is each speaker's `height_layer` (Layout card, or Setup → AirPlay & Atmos). Syng
  Studio sets it through the cloud (MQTT `HEIGHT_LAYER_UPDATE`), which an offline room never hears back from.
- The renderer is in `/usr/lib/python3.7/site-packages/syng/cellbroker/shaders.py` (`variable_71_4_shader`,
  `generic_surround_shader`): height channels sit on the floor ring at ±45°/±135° with one height layer, and go up to
  0.6 × the next layer when a speaker is marked high.

## Debugging "AirPlay goes silent at a new track, but says it's playing"

- `journalctl -u airplay2source`: a lone `_bufProcessPacket  err = -6743` (CoreUtils kSizeErr) mid-stream, with no
  `Teardown` / `Format Changed` / `ProcessPkt Flush Complete` after it, is the stuck stream. The sender carried on into
  the next track in the same buffered stream and Syng's receiver stopped taking it. A skip that logs `Flushing
  receiver complete: flushSeq 0` doesn't help; the next one, with a real `flushSeq` followed by `ProcessPkt Flush
  Complete`, does. A normal track change from a sender that tears down between tracks logs the same -6743, then
  `Teardown` and a new `Setting up stream connection` within half a second.
- b65's watchdog does the second skip's job: pause, then play, after 4 s of silence that follows an uncleared -6743
  (`AIRPLAY SILENCE` lines in `journalctl -u syng-agent`).
