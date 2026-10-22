# troubadour-ehguard — in-process exception guard at the C++/C AirPlay boundary (Fix A)

Defense-in-depth companion to `tools/busguard/` (Fix B). Where busguard removes the *race*
that makes `airplay2source` throw on a spatial-stream start, ehguard makes the throw *harmless*
if one ever happens anyway (e.g. a genuine D-Bus error during stream start): it stops any
`sdbus::Error` from unwinding out of Syng's C++ delegates into Apple's C AirPlay receiver core,
where there is no handler and the process `abort()`s.

## How
`airplay2source` can't be rebuilt (stripped, Apple SDK compiled in). So ehguard is an
`LD_PRELOAD` that, in its constructor, installs an in-process guard around the two C++ delegates
on the documented crash path:
- `syng::AirPlay2Source::initializeFormat` (file vaddr `0x2546e0`)
- the stream-property / badge updater (`0x252f80`)

It finds them from the exported anchor `AirPlayReceiverSessionUpdateBadgingType` (`0x218b80`),
overwrites each entry with a 16-byte absolute detour (`ldr x16,#8 ; br x16 ; .quad hook`) into a
small shim, and rebuilds a trampoline (the 4 stolen PC-independent prologue instructions + an
absolute jump back to `func+16`). The shim saves all argument registers (x0–x7, v0–v7, x8),
calls a C++ `try { original(...) } catch (...)` wrapper, and on a catch logs and returns a safe
value instead of letting the exception escape. On the normal path the original's real return
value and all args pass through untouched. No byte of `airplay2source` is changed on disk;
dropping the `LD_PRELOAD` reverts completely.

The hook engine is signature-agnostic for register-argument functions (verified: initializeFormat
takes `this` only, the badge updater `this`+2 — no stack args) and is unit-tested end-to-end on
aarch64 (`build.sh test`): pass-through returns the true value with args intact; a thrown
exception is caught and converted to a safe return with no abort.

## Build / test
```sh
CXX=g++ ./build.sh test      # on an aarch64 builder; runs the engine unit test too
```

## Deploy (alongside busguard)
```sh
scp libtroubadour_ehguard.so root@<cell>:/usr/lib/troubadour/
# set LD_PRELOAD to BOTH, colon-separated, in the drop-in:
#   Environment=LD_PRELOAD=/usr/lib/troubadour/libtroubadour_busguard.so:/usr/lib/troubadour/libtroubadour_ehguard.so
systemctl daemon-reload && systemctl restart airplay2source
```
Confirm: `journalctl -u airplay2source | grep ehguard` → `delegate exception guard active
(initializeFormat + badge)`. Verify the detour is live by reading the first 2 words at the
runtime address of each target (`ldr x16,#8` = `0x58000050`, `br x16` = `0xD61F0200`).

## Rollback
Remove the two preloads from the drop-in (or delete the drop-in), `daemon-reload`, restart.

## Status
Built, unit-tested, and deployed on the spare Test Cell (`.39`) with busguard: both markers
present, both detours verified in live process memory, AirPlay up, `NRestarts=0`. Recommend one
Atmos soak on the spare (no-regression check) before fleet rollout, since ehguard live-patches
the running binary in memory.

## 2026-10-20 review (§1.4)
- `troub_wrap` used to return the per-hook on-catch value (0 = noErr) after swallowing a throw, which
  tells Apple's core the delegate succeeded and lets it run on a half-initialised format/badge. The
  return is now a runtime knob: set `TROUBADOUR_EHGUARD_ONCATCH` (a signed int, e.g. `-6700`) in the
  LD_PRELOAD drop-in to have the core treat it as a failure and tear the stream down cleanly. **Unset =
  original behaviour (0)**, so nothing changes until you opt in and test on a cell.
- The guard now logs its first delegate call and every catch (with a running count), and exports
  `troub_wrap_stats(&calls,&catches)`. This settles the old doc contradiction (SYNG-AIRPLAY-FIXES.md
  ":219 both detours verified in live memory" vs ":236 in-memory detours don't execute") by counting
  hits on the running binary instead of reasoning about it. Rebuild: `CXX=aarch64-linux-gnu-g++ ./build.sh`.
