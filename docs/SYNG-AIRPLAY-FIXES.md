# AirPlay on the Syng Cell: root causes and fixes (from reverse engineering)

Companion to `SYNG-AIRPLAY.md`. That file reports the four issues from the speakers' own journals and
D-Bus interfaces. This file reports what the binaries actually do, from static reversing of
`/usr/bin/airplay2source` (sha256 `dbcfb3cf…`, built 2026-07-15, aarch64, stripped, PIE) cross-checked
against the cell Python (`/usr/lib/python3.7/site-packages/syng/…`) and read-only observation of a live
primary (`MB9Y`, firmware 2.2.x, `libsdbus-c++.so.1.1.0`). Addresses below are file/virtual offsets in that
build of airplay2source; symbol names are the demangled `.dynsym`/weak symbols the compiler left in.

No speaker was modified or crashed to produce this. Everything was read from the stock binary and the cell's
own journals.

---

## 1. The AirPlay crash is an exception crossing the C receiver boundary — not a setChannelLayout failure

### What actually happens

The Apple AirPlay receiver core is C. `_GeneralAudioUpdateStreamFormat` (fn `0x2126b0..0x212bc0`, the function
that logs `Format Changed: …APAC_48KHz_7_1_4(68)`) runs on the `_GeneralAudioThread` and calls down into Syng's
**C++** delegate to re-initialise the format. That delegate is `AirPlay2Source::initializeFormat`
(fn `0x2546e0..0x255078`) followed by the per-stream property/badge update function (fn `0x252f80..0x253e74`).

Looking at the exception tables (`.gcc_except_table` / `.eh_frame`) of those functions:

- `initializeFormat` has exactly **one** try/catch. It wraps the single call to
  `syng::audiosource::Provider::setChannelLayout` at `0x254d3c`; the landing pad at `0x254f70`
  (`__cxa_begin_catch`) logs `"D-Bus error occurred while setChannelLayout: {}"` and calls `__cxa_end_catch`.
  That is the guard already noted in `SYNG-AIRPLAY.md`. **Every other call in the function is covered only by
  cleanup landing pads** (10 × `_Unwind_Resume`, i.e. destructor cleanup that re-raises) — no catch.
- The property/badge function `0x252f80..0x253e74` has **no `__cxa_begin_catch` at all** — only cleanup pads —
  yet it makes two unguarded `sdbus::IObject` property calls (`0x253acc`, `0x253c80`) plus async
  `setSessionProperty`/`setPlatformProperty` dispatches.
- `_GeneralAudioUpdateStreamFormat` itself (the C frame above them) has no catch and takes no lock.

So the moment any D-Bus operation on the format-change path *other than* `setChannelLayout` throws, the
exception unwinds out of the C++ delegate, back through the C receiver core (which has no handlers), and hits
`std::terminate` → `abort` (`status=6/ABRT`). The throw is `sdbus::Error` and its text — `Failed to enter/exit a
variant` with EBUSY/ENXIO — is raised inside `libsdbus-c++.so.1.1.0` (the string is in that library, not in
airplay2source), during a `Message::enterContainer`/`exitContainer`, i.e. while **reading** a D-Bus message.

### Why it throws (the trigger)

Two things line up on the live primary:

1. **One bus connection, 11 threads.** airplay2source runs 11 threads and services the system bus through a
   single shared connection (the `syng::cell::Proxy`/`Remote`/`dbus::ObjectMonitor` layer that owns the
   `std::shared_ptr<sdbus::IConnection>`). In sdbus-c++ 1.1.0 a `Message` and the read cursor of a connection
   are **not** safe to touch from two threads at once. The async reply handler
   `syng::dbus::ObjectMonitor`'s `uponReplyInvoke` deserialises a nested
   `map<string, map<string, map<string, sdbus::Variant>>>` (an ObjectManager-style payload) on the event-loop
   thread, while the `_GeneralAudioThread` is simultaneously building property updates on the same connection.
   `sd_bus_message_exit_container` returning `-EBUSY` ("variant held more than was read") and `enter` returning
   `-ENXIO` ("next item isn't the expected variant") is exactly what a corrupted/concurrent read cursor
   produces.
2. **Re-entrant format changes.** The journal on MB9Y shows the sender renegotiating format repeatedly, often
   twice in the *same second* — e.g. `18:16:11` logged `APAC_48KHz_7_1_4` **twice**, and most Atmos starts are
   immediately followed by a switch back to `AAC_LC_48KHz_Stereo`. The format-change handler has no lock, so two
   format changes can be in flight at once, both doing D-Bus work on the shared connection. This is the window
   where the concurrent-read crash happens; it is intermittent for exactly that reason.

(The "unexpected variant type read in a handler without a catch" theory in SYNG-AIRPLAY.md is the same bug seen
from the other side — an unguarded deserialize — so both descriptions point at the same fix.)

### Fix (two parts, both worth doing)

**A. Never let an exception cross back into the C receiver core.** Wrap the whole body of each C++ delegate the
AirPlay receiver calls — `initializeFormat`, the stream-property/badge updater, `prepareStream`,
`startStream`, `stopStream`, `finalizeStream`, and every `AsyncMethodInvoker`/`SignalSubscriber` reply handler
— in `try { … } catch (const sdbus::Error& e) { log(e); } catch (...) { log(); }`, returning a normal error
code to the C layer instead of unwinding through it. The existing single guard around `setChannelLayout` shows
the intended pattern; it just needs to cover the entire boundary, not one call. This alone turns the crash into
a logged, recoverable error.

**B. Stop the race that produces the throw.** Serialise D-Bus access so one connection is only ever touched from
one thread: either confine all `com.syng.*` calls for a source to the event-loop thread (post the property
updates to it) or hold a per-connection `std::mutex` around every `callMethod`/`emit`/reply-deserialize. Also
take a mutex (or a "format change in progress" guard) across `_GeneralAudioUpdateStreamFormat` so a second
format change can't re-enter while the first is still tearing down. With B in place, A should essentially never
fire — but keep A, because the C-boundary leak is a latent crash regardless of cause.

**Interim mitigation already in use (keep it):** `LimitCORE=0` on the airplay2source unit. The 2-minute outage
was never the crash — it was `/usr/bin/syng-coredump` piping a ~150 MB core to systemd-coredump. With
`LimitCORE=0` the dump is skipped, the process is reaped in ~0.3 s and `RestartSec=3` is all the user notices.
This does not fix the crash; it caps its cost until A/B ship.

---

## 2. The "Stereo" badge is read from the sender, not derived from the stream

In the property/badge function the badge value is produced at `0x253988` by
`CFGetInt64Ranged(dict, min=0, max=0xffffffff, default=0)` reading an integer out of the stream-property CF
dictionary, stored to the stack and then both logged (`AirPlay2Source.cpp:1469: Badging Type: {}`,
string `0x483e60`) and pushed onto the `badgingType` D-Bus property. The int→label mapping right after
(`cmp w0,#0` → "Stereo", `cmp w0,#1` → …) is only for the log line.

So the badge is simply whatever that one CF property says, defaulting to **0 = Stereo**, and it is set at every
stream start **before/independently of** the negotiated APAC format. Nothing in this path consults the 12-channel
APAC 7.1.4 layout that `initializeFormat` just set up, which is why Atmos still badges as Stereo.

**Fix:** derive `badgingType` from the negotiated format instead of (or as a fallback to) that CF property —
e.g. map APAC / ≥ 6-channel / height-channel layouts to the Atmos/spatial badge in `initializeFormat`, where
the channel count and `juce::AudioChannelSet` are already in hand (`0x254a44` builds the layout,
`0x254cac getSpeakerArrangementAsString`). This is a display-only change; it does not touch rendering.

---

## 3. Atmos height: the mark is cloud-only, and the "Beyond Stereo" flag is dead

### Height layer never applies locally

Confirmed in `cellbroker/ProxyManager.py`:

- `cell_set_height_layer(height_layer)` (line ~929) does **only** one thing: `mqtt_client.publish(
  "cell/<serial>/height_layer", {type:"HEIGHT_LAYER_UPDATE", …})`. It never updates `self.cell_positions`.
- `set_room_state` (line 217) derives `have_height_cells = get_number_of_height_layers(self.cell_positions) > 1`
  and only then calls `source_proxies[sp].enable_height_processing(have_height_cells)` (line 277).
- `self.cell_positions` (and each cell's `height_layer`) is populated from the room document in `set_cell_state`
  / room-state apply — which only arrives **from the cloud**.

So a wall/ceiling speaker's height layer cannot change without a cloud round-trip, and an offline room never
hears back. This matches SYNG-AIRPLAY.md §3 exactly, now pinned to the code.

**Fix:** make `cell_set_height_layer` apply locally as well as publish — update the cell's entry in
`self.cell_positions['…']['height_layer']`, persist it, then re-run the room-state/shader application
(`set_room_state`/`enable_height_processing` + a shader recompute) so `have_height_cells` and the
`variable_71_4_shader` height placement update immediately. Expose the control per speaker in Syng Space (next to
position), not only in Studio. Keep the MQTT publish so the cloud document stays in sync when it is reachable.

### enableBeyondStereoDuplication is never set by anyone

airplay2source exposes `DefaultAudioSourceProvider::enableBeyondStereoDuplication(bool)` →
`syng::graph::SourceGraph::enableBeyondStereoDuplication(bool)` as a writable D-Bus property, and
`syngsoundserver` reads the `enableBeyondStereoDuplication` property. But **no component ever writes it**: a
full-tree grep of the shipped cell Python (cellbroker, ProxyManager, Router, AudioSourceServer, Space, Studio
glue) finds no setter — the only "beyond stereo" code is `shaders.is_beyond_stereo_fallback`, which is the
unrelated puck-geometry fallback (`panner_v2` with `radius == 0`), not this flag.

So "Beyond Stereo duplication" (duplicating a stereo source across cells in the source graph) is a dormant
feature: the sink reads the flag, the renderer honours it, nothing turns it on. **Question for Syng:** is this
meant to be driven by the room's "fill"/stereo-spread control, and was the writer dropped? If it should exist, it
needs a setter in the room/source-state path (likely alongside the stereo shader selection); if not, it's dead
surface area on every source.

---

## 4. The -6743 silent-track-change is an AirPlay-SDK buffer reject with no resync

`_bufProcessPacket err = -6743` is `kSizeErr` returned by the RTP/jitter buffer inside the Apple AirPlay
receiver code that is compiled into airplay2source (the `_GeneralAudioThread` /
`AirPlayReceiverSession*` family; the `-6743` constant appears at many RTP sites, e.g. `0x201258`, `0x205e08`,
`0x214378`). On a continued **buffered** stream, the first packet after a track boundary can fail the node
size/slot check (a larger packet after a bitrate/format change), the packet is dropped, and — because the sender
did **not** tear the stream down — nothing upstream flushes or resyncs. The buffer simply stops being fed while
the source still reports "playing", which is the stuck-silent state. A skip that carries a real `flushSeq` →
`ProcessPkt Flush Complete` is what clears it, which is why a second skip (or pause→play) recovers.

This code is the licensed Apple AirPlay receiver SDK, so the clean fix is in Syng's integration of it rather
than a one-line patch in stock code: on an **uncleared** `kSizeErr` mid-stream (no `Teardown`/`Flush Complete`
following within a short window), force a receiver resync to the next good packet — effectively do the second
skip's flush automatically — and log every rejected packet, not just the first, so the size that fails is
visible. If the size check itself is too strict for a post-boundary packet, growing the node/slot high-water for
the buffered path would stop the reject at source.

**Interim mitigation already in use (keep it):** the b65 agent watchdog does the pause→play after ~4 s of
silence following an uncleared -6743. That restores audio without user action; the resync-on-reject fix above
would make it inaudible.

---

## Minor: "Missing Magic Cookie" at Atmos start is harmless

`Unable to create an APAC decoder. Missing Magic Cookie.` (string `0x476b8a`) is logged before the sender's
format config arrives; the decoder is created a moment later (`APACDecoder_Initialize … numChannels = 12`). It
is a first-attempt warning, not a failure — it should be demoted to debug or suppressed on the first try to stop
it reading like an error.

---

## How this was verified

- Static: exception-table / landing-pad analysis of `initializeFormat`, the stream-property/badge function and
  `_GeneralAudioUpdateStreamFormat` in airplay2source `dbcfb3cf…`; string→xref resolution of the log lines;
  symbol inspection of the sdbus proxy/ObjectMonitor layer.
- Source: full grep of the shipped cell Python for the height-layer and beyond-stereo paths.
- Live (read-only, primary MB9Y): 11 threads; `libsdbus-c++.so.1.1.0`; journal showing re-entrant/flip-flopping
  `Format Changed` events and the `terminate … 6/ABRT` signature with no leftover cores (LimitCORE=0 active).

---

## What we shipped ourselves (Troubadour side, 2026-10-18)

The deep causes of #1, #2 and #4 are inside Syng's stripped `airplay2source` with Apple's licensed AirPlay-2
SDK compiled in; we can't rebuild that binary, so those stay with Syng (patches above). What we can do from
our side is deployed to the ballroom group (b66 + a cellbroker patch; each cell keeps a `.prev.bak`):

- **#2 badge — fixed as far as we can without the binary.** The agent (b66) raises the published
  `badgingType` to match the negotiated layout (Dolby Atmos 7 when the layout has height channels, Surround 2
  for other multichannel), correcting only the plainly-wrong Unknown/Stereo/Undefined values and re-applying
  after each format change. The real fix is still Syng deriving the badge in `initializeFormat`.
- **#1 crash — now fixed on our side (serialization), not just cost-capped.** The crash is a
  data race on the single shared `sd_bus` connection (§1 trigger B). We implemented upstream's
  "Fix B" without rebuilding the binary: `airplay2source` reaches `sd_bus` only through
  `libsdbus-c++.so.1.1.0`, which imports `sd_bus_process`/`call`/`call_async`/`send`/`flush`
  (`@LIBSYSTEMD_221`) from `libsystemd`. `tools/busguard/` is an `LD_PRELOAD` shim that
  interposes those five and runs each under one process-wide recursive mutex, so two threads can
  never pump the connection at once — the read cursor can't corrupt, so the `sdbus::Error` is
  never thrown and the C-boundary `abort` never happens. Message-local `enter`/`exit_container`
  reads are left unlocked (no hot-path cost). Deployed + verified on the spare Test Cell (`.39`):
  loads with no loader/version errors, interposes cleanly, `AirPlay servers started`, survives
  restarts, `NRestarts=0`, byte-for-byte stock on rollback. Definitive confirmation needs a soak
  with real Dolby Atmos content to the Test Cell. Keep `LimitCORE=0` (quick-recover) as belt-and-suspenders. We also implemented upstream's
  **"Fix A"** (wrap the C++ delegate boundary in a catch) on our side as `tools/ehguard/`: an
  `LD_PRELOAD` that live-installs an in-process guard around `initializeFormat` (`0x2546e0`) and
  the badge/property updater (`0x252f80`) — a 16-byte absolute entry detour into a shim that runs
  the original under `try{…}catch(...)`, so a stray `sdbus::Error` is logged and turned into a safe
  return instead of unwinding into Apple's C core and aborting. Signature-agnostic (register args,
  no stack args — verified), unit-tested on aarch64, no on-disk change to the binary. Deployed on
  the spare with busguard (both detours verified in live memory); soak + fleet pending. With Fix B
  removing the race, Fix A should essentially never fire — but it closes the latent C-boundary leak
  for any residual throw, exactly as the notes recommend keeping both.
- **#3 height — genuinely fixed our side, and patched for stock.** The native firmware already honours a
  locally-set top-level `height_layer` (verified live), so our agent's layout path gives offline per-speaker
  height today. We also patched `cellbroker/ProxyManager.cell_set_height_layer` to apply locally (not just
  publish to MQTT), so the **stock** Space-app control works offline too — this is the upstream fix Syng
  should take (`tools/patch_proxymanager.py`).
- **#4 silent track changes — root diagnosed (Apple SDK, not reachable) + fast watchdog shipped (2026-10-19).**
  Live logs at reproduced silences show it is entirely on the **primary** receiver (secondaries log none): at each
  track change a `flushAudio → SetRateAndAnchorTime(new anchor) → "Stopping stream" → flush` churn, and the new
  track's first packets log `Render Anomaly: Packet in the Future … Decoded format: Unused_Invalid Format` — the
  decoder has no format yet (the "re-init the magic cookie after a decoder reset but before feeding packets" case)
  and the packets land ~190 ms ahead of the render window, so nothing renders until a later flush+anchor aligns.
  **Wired `.39` reproduced the identical pattern** (same `-6743` + future-packet, stream id=1 vs the group's id=5),
  proving it is track-change *logic*, not the network/PTP. The decode/anchor path is Apple's licensed receiver SDK
  statically linked into airplay2source; the only hook that works in this binary is `LD_PRELOAD` of *imported*
  symbols (how busguard fixes #1), and those SDK functions are not imported (in-memory detours don't execute in
  this binary — proven with ehguard/trace), so we cannot rewrite Apple's buffer/decoder logic. What we fixed is
  **our own watchdog** (`syng-agent` `airplay_silence_loop`): the recovery (a pause→play that forces a clean
  re-anchor) already worked in ~0.4 s, but *detection* waited 4 s **and** a 45 s rate-limit bug let silence run up
  to ~43 s when tracks changed within 45 s of a prior fix. Now: detect ~1 s, 45 s rate-limit removed (replaced by a
  3 s cooldown), poll 0.4 s, pause/play dwell 0.8 s, attempts cap 3 → track-change silence drops from 4–43 s to
  ~1 s. Deployed live to the ballroom group + `.39`; `overlay/usr/bin/syng-agent` updated; patcher
  `tools/airplay-silence/sil_patch.py` (each cell keeps a `.troubadour-presil.bak`). The in-SDK resync remains
  Syng's to fix properly.

---

## UPDATE 2026-10-19 (afternoon): #4 silent-track-change — ROOT FIXED in the receiver (flushfix), watchdog retired

The earlier §4 conclusion ("Apple SDK, not reachable; keep the watchdog") was **wrong** and is superseded.
Live trace-level capture on .39 nailed the real mechanism and it IS patchable. Full writeup:
`_local/re/airplay-silence-fix/TWO-CLOCK-ROOTCAUSE.md` + `flushfix/FLUSHFIX.md`.

### Real root cause
At a track transition the sender sends `AirPlayReceiverSessionFlushAudio` (sets flush-pending
`session+0x888`=1, `flushTS +0x8a8`, `flushSeq +0x8a0`) then re-anchors via
`AirPlayReceiverSessionSetRateAndAnchorTime`. **SetRateAndAnchorTime re-anchors WITHOUT clearing the
flush-pending state.** AirPlay-2 buffered RTP timestamps are re-based per segment, so the new track's
base often lands *below* the stale `flushTS`; the render-side check `if (0x888 && pktTS < 0x8a8) drop`
and the ProcessPkt seq compare (flushSeq 0 never completes for a lower base) then discard **every** new
packet → the buffer never fills → `_GeneralAudioRender` conceals (zero-fills) forever. It's intermittent
because it depends on whether the new base lands above (ok) or below (wedge) the leftover flushTS. A
pause/play kick works only because it issues a flush with `flushSeq=current` that completes immediately
and clears 0x888. The two-clock bridge (SRC + skew) is NOT involved — skew stayed 0-1 samples through wedges.

### The fix — "flushfix" (deployed fleet-wide 2026-10-19)
Patch `SetRateAndAnchorTime` to clear flush-pending on every re-anchor, i.e. add `strb wzr,[x20,#0x888]`
to its state-store block (code-cave detour: hijack `str xzr,[x20,#0x620]` @0x3190a8 → cave @0x2f0744).
Stock `dbcfb3cf…` → flushfix `215b5ee9…`. Builder: `flushfix/patch_flushfix.py` (verifies both shas; sets 0755).
Deployed to all 6 (.174 test + group of 5: .226 primary, .138/.44/.192/.30 secondaries). Backup per cell:
`/usr/bin/airplay2source.troubadour-presilence` (stock). **Verified: the multi-minute silent wedge is gone.**
Gotcha fixed: the first group rollout installed the binary 0644 (python wrote it non-exec, `cp -a` preserved it)
→ 203/EXEC crash-loop; `chmod 755` fixed it; builder now chmods 0755.

The b65 `syng-agent` watchdog (airplay_silence_loop pause/play) is **retired** — the root fix replaces it.

### Residual (in progress): ~1 s rebuffer blip at flush+reanchor transitions
With the wedge fixed, those transitions now recover but play a brief gap: the new track is anchored
~0.2-1.0 s ahead (MORJA KAIJU VIP: 46,491 samp ≈ 969 ms; ZEET NOISE: 47,717 samp ≈ 994 ms) and the render
conceals while its clock catches up at 1x (rebuilding the jitter buffer). Seamless transitions (continuous
stream, no flush) have no gap. Plan: a **tuned render snap** — on a large future gap, re-anchor the render
offset to a small lead (~150 ms) instead of waiting out the full ~1 s — keeping most of the dropout buffer.
It's RT-render-path surgery, so: develop on .174, prove stable, then group. Validating the target lead against
a reverse of the HomePod 18.3 firmware's own AirPlay receiver (`18.3.ipsw`, in progress).

## Review note (2026-10-20, §1.4) — resolving the ehguard "do the detours run?" contradiction
Two statements above read as contradictory: Fix A "both detours verified in live memory" vs the #4
note "in-memory detours don't execute in this binary". They are about different targets:
- **ehguard's absolute-jump detours hook airplay2source's OWN C++ delegate methods**
  (`syng::AirPlay2Source::initializeFormat` @0x2546e0, the badge/property updater @0x252f80). Those are
  real functions in the binary and the 16-byte entry detour runs — that is Fix A.
- What does **not** work is LD_PRELOAD *symbol interposition* of Apple's licensed receiver SDK
  (decode/anchor) functions, because they are statically linked and **not imported**, so there is no
  dynamic symbol to interpose. That is the #4 limitation.
So both can be true. To remove all doubt about Fix A firing, ehguard now counts hits: it logs the first
delegate call and every catch, and exports `troub_wrap_stats()`. Read the journal on a running cell —
a non-zero call count proves the detours execute. (And `TROUBADOUR_EHGUARD_ONCATCH` lets you make the
caught-throw return a real error so the core tears the stream down instead of running half-initialised.)

## 2026-10-20 late: review §1.1 / §1.3 / §1.4 / §1.5 acted on (Ball Room fleet)
- **§1.1** The five Ball Room cells left `l0` (snap on any gap, 0 ms lead). They now run **`flushgate`** =
  `snapfix` (150 ms lead / 300 ms trigger, normal path) + the §1.5 gate below. Garaga (.169) stays pure stock.
- **§1.5 flushgate** (`_local/re/airplay-silence-fix/flushgate/patch_flushgate.py`, base snapfix 2959dd10 →
  7ce99be8). The sender's track-change flush arrives as `Flushing until ts N seq 0` (journal shows one at every
  transition), and stock `FlushAudio` (ELF 0x217870) set flush-pending `+0x888` for it; with the 24-bit seq
  compare and sessions at seq ~10.8M (> 0x800000) that flush never completes → every new-track packet dropped
  (the silent wedge). flushfix cleared `+0x888` on every re-anchor, which also kills legitimately pending
  `seq≠0` flushes. flushgate instead leaves `+0x888` unset when `flushUntilSeq == 0` (12-byte cave at
  0x1f0744 — the old flushfix cave, reused: `cbz w28; strb w0,[x19,#0x888]; b back`; the flushfix hijack at
  0x2190a8 is restored to stock). The synchronous purge in FlushAudio still runs. Verified by disassembly;
  5 words differ from snapfix.
- **§1.4** `airplay2source` restarted on all five, so the rebuilt ehguard (call/catch counters, `ONCATCH` knob)
  is finally the one loaded — the fleet had been running the pre-review copy since boot. Watch for
  `first delegate call went through the guard` after the next format change.
- **§1.3** On the primary only, `troubadour-busguard.conf` now preloads **`libtroubadour_busguard_trace.so`
  instead of busguard** (backup `/data/troubadour-busguard.conf.bak`). Read the journal after some format
  changes: the same `msg=` under two `tid=` values confirms the sdbus-c++ Variant-copy theory. Restore busguard
  (or drop it) once decided. The four secondaries still preload busguard.
