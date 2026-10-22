# Syng Cell — LED subsystem (reverse-engineered)

Decoded from `/usr/bin/ledmanager` (stripped aarch64 D-Bus service) plus hardware
poking. This is everything you need to drive the ring yourself.

## Hardware model
- **4× PWM, RGBW.** Channel → PWM chip mapping (from `setColor`):
  | Color | PWM chip |
  |---|---|
  | White | `pwmchip0` |
  | Green | `pwmchip1` |
  | Red   | `pwmchip2` |
  | Blue  | `pwmchip3` |
  (This is why stock `init-pwm-led-white.service` drives `pwmchip0`.)
- **Duty is linear:** `duty_cycle = period × value`, `value ∈ [0,1]`.
  Period is **22767** (~43.9 kHz), so `1.0 → 22767`, `0.5 → 11384`.
- The `translationLUT` (gamma) path is a **stub** (`return -1`, unused);
  `translationRatio` is live. In practice: treat it as linear.

## Animations ("visions")
JSON in `/usr/lib/syng/visions/`:
```json
{ "rgbw_frames": [[r,g,b,w], ...], "metadata": { "oneshot": true } }
```
- Each value 0.0–1.0, order `[R, G, B, W]`.
- Played at **30 FPS** (frame interval decoded as `0x3fa1111111111111` = 1/30 s).
- `oneshot: true` plays once; otherwise it loops.

Examples in this kit (`overlay/usr/lib/syng/visions/`):
- `NicsFixBreathe.json` — cyan breathe (loops)
- `PulseRed.json` — one-shot red pulse (alert)
- `WarmIdle.json` — static half-white idle

Make your own: write `/usr/lib/syng/visions/MyThing.json` (30 fps frames) then
`syng-led play MyThing`, or edit it live in the `syng-agent` web UI.

## D-Bus interface
Service/interface `com.syng.Led`, object `/com/syng/ledmanager`:
- `SetColor(i state, a d rgbw)` — `state 5` = custom color, array **must be length
  4** `[R,G,B,W]`; states 0–4 are predefined colors, state 6 is the animation
  state (`isColor` accepts 0–5, `isAnimation` accepts 6).
- `PlayAnimation(s path)`, `PauseAnimation()`, `ResumeAnimation()`,
  `ClearAnimationQueue()`.

## `syng-led` helper
```sh
syng-led color R G B W        # via ledmanager D-Bus (0.0-1.0). e.g. color 1 0 0 0 = red
syng-led play NAME            # play /usr/lib/syng/visions/NAME.json
syng-led pause | resume | clear
syng-led raw R G B W          # DIRECT sysfs (run `systemctl stop ledmanager` first)
syng-led off                  # all channels off (direct sysfs)
```

Raw PWM without the daemon:
```sh
systemctl stop ledmanager
for c in 0 1 2 3; do
  echo 0 > /sys/class/pwm/pwmchip$c/export 2>/dev/null
  echo 22767 > /sys/class/pwm/pwmchip$c/pwm0/period
  echo 1 > /sys/class/pwm/pwmchip$c/pwm0/enable
done
echo 22767 > /sys/class/pwm/pwmchip2/pwm0/duty_cycle   # full red (chip2 = Red)
```

## Sound-reactive daemon (`syng-led-reactive`)

Modes: `vu`, `spectrum`, `beat`, `rainbow`, `pulse`, `party`, `flow` (evolving colour, brightness from bass, idle at `--floor`) and
`groove` (flow's colour engine driven by three layers: a slow mood level with its own 12 s AGC, a contrast-expanded transient
layer, and a tempo tracker that locks a phase-synced breath to the beat; treble cools the tint, white only on hits),
`bloom` (brightness on the beat, colour from the treble: each kick blooms and falls away — the fall timed to the
tempo once it locks, a soft swell into the next predicted beat — over a resting level that follows the passage; the
colour slides along a range around the slowly evolving base hue, warm when the top end is quiet, cooler and paler
with hats and cymbals) and `shimmer` (brightness from the treble, colour on the beat: sparks on each hi-hat or
cymbal hit over a glow from the top end's level, a little bass body underneath; the colour holds and steps a little
on each kick, more on an accent — the downbeat once the tempo locks, or a much harder hit).

**Onsets (bloom, shimmer).** The band envelope's own "flux" (the rise of the smoothed, AGC'd bass level) sees a
kick as a small nudge and misses most of them — 37 a minute on a test track with ~120 kicks. `VizBands` also runs a
spectral onset detector on the raw 88-bin spectrum: per-bin dB rise summed over the bass bins (kicks) and the top
bins (hats), against a running mean + k running deviations (k = 1 + 12 x beat sensitivity, so the dashboard's
slider still means "lower = touchier"), fired once at the peak, at most every 0.12 s. Treble is also tracked on its
own scale (8 s AGC); the shared AGC leaves it far below the kick on most music.


The dashboard's LED card (modes, group colour, beat sensitivity, floor, fades, vision editor) drives this
daemon through `/api/led/*` on every cell (`scope: group`). It is enabled in the image by
`tools/fw/build-troubadour.sh`; the service args live in `/etc/default/syng-led-reactive`.

Whatever the mode, every colour goes through one PWM writer, which publishes it to
`/dev/shm/syng-led-rgbw` (`r g b w t`, ~20 Hz). The agent reads that file: `/api/led` reports it as
`color`, and it rides the viz feed as `led: [r,g,b,w]`, which is how the sound field (the dashboard's
and the app's) ends up glowing the same colour as the speaker — see `paletteFromColor()` in
`field/field.js` and `FieldPalette.from()` in the iOS app. A manual colour set through
`/api/led/color` publishes to the same file.

**Cost.** With `--source viz` the loop runs at 60 fps on every cell, all day, so it is kept lean (measured on a Cell,
2026-10-18): the msgpack decoder reads the feed's int16 arrays in one `struct` call, the 88-bin band analysis is plain
`math` (numpy's per-call overhead on arrays that small cost more than the maths), and the four PWM `duty_cycle` files
stay open and are written with `pwrite` at offset 0 instead of open/write/close per change. 2.55 ms -> 0.48 ms a
frame: 15 % of a core -> 3 %, identical output. Profile it with the captured-packet harness in the changelog entry.

Taps the JACK playback mix (what the speaker is playing), does a small numpy FFT,
and drives the 4 PWM channels directly. `ledmanager` is stopped while it runs and
restarted on exit (so `systemctl stop syng-led-reactive` restores normal behavior).

```
syng-led-reactive [--mode vu|spectrum|beat|rainbow|pulse]
                  [--source playback|mic] [--ports "p1,p2"]
                  [--cycle SEC]       # palette evolves every SEC seconds
                  [--beat-sens F]     # bass-boom color-shift sensitivity
                  [--floor F]         # brightness floor
                  [--fps 60] [--gain 1.0] [--rate 48000] [--period 22767]
                  [--debug] [--no-manage]
```
Service args live in `/etc/default/syng-led-reactive`. Default:
```
ARGS=--mode pulse --cycle 25 --beat-sens 0.07 --floor 0.08
```

**Grouped speakers:** on grouped playback, secondaries receive audio via SourceCast
(→ DAC) and bypass the local JACK mix, so `--source playback` only reacts on the
primary. Use `--source mic` to get all grouped cells reacting (works, but less
tight than a direct tap). The clean fix — reading the
`VisualizationDataBroadcast` feed — is on the [roadmap](ROADMAP.md).

## `room` mode — react to the room through the mics (2026-10-20)

`--mode room --source mic` (the agent sets the source automatically when you pick **Room** in the apps or the
dashboard; every other mode goes back to the viz feed). It listens to the Cell's three mics, so it reacts to anything in
the room — voices, claps, other speakers, its own music — and in a quiet room it never goes dark: a soft glow that
breathes on a 9 s cycle and keeps cross-fading to a new colour (golden-angle hops every `--cycle` s, 6 s fades).

* Talking lifts the brightness without changing colour; a real hit (clap, kick drum, door) pops to full and kicks
  the colour.
* The mics have very low gain (quiet room ≈ −88 dBFS, the Cell's identify chime ≈ −63 dBFS), so levels are measured
  in dB above a tracked noise floor (power smoothed over 0.12 s; floor follows quiet stretches down, creeps up
  0.15 dB/s, the overall floor is capped at −55 dBFS so continuous loud sound can't become "quiet").
* Tunables: `--idle` (quiet glow brightness, default 0.3; also `/api/led/tune {"idle": …}`), `--mic-gate` (dB above
  the floor ignored as hiss, default 7), `--mic-range` (dB above the floor that reads as full, default 30), plus the
  usual `--cycle`, `--silent-fade`, `--beat-sens`.
