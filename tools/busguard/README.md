# troubadour-busguard — the real fix for the AirPlay spatial-stream crash (issue #1)

`airplay2source` services **one** shared `sd_bus` connection from **11 threads**. `sd_bus`
is not thread-safe. When the event-loop thread is inside `sd_bus_process()` deserializing an
incoming reply/signal while another thread drives the same connection via
`sd_bus_call()`/`sd_bus_send()`, the connection read cursor is corrupted. `libsdbus-c++`
1.1.0 then throws `sdbus::Error` *"Failed to enter/exit a variant"* (ENXIO/EBUSY) from a
handler with no `catch`; the exception unwinds through Apple's **C** AirPlay-receiver core
(which has no handlers) and hits `std::terminate` → `abort` (status=6/ABRT). See
`docs/SYNG-AIRPLAY-FIXES.md` §1 for the full reverse-engineering (addresses, exception
tables, call graph) — all re-verified against the shipped binary
(sha256 `dbcfb3cf…`).

We can't rebuild `airplay2source` (stripped, Apple AirPlay-2 SDK compiled in). But it reaches
`sd_bus` **only** through `libsdbus-c++.so.1.1.0`, which imports the C symbols
`sd_bus_process`, `sd_bus_call`, `sd_bus_call_async`, `sd_bus_send`, `sd_bus_flush`
(all at version `LIBSYSTEMD_221`) from `libsystemd.so.0`. So we fix it **without touching the
binary**: an `LD_PRELOAD` shim interposes those five calls and runs each under one
process-wide **recursive** mutex. Two threads can no longer pump the connection at once, the
read cursor is never corrupted, and the throw never happens. Message-local reads
(`enter`/`exit_container` on a singly-owned message) are left untouched — no extra locking on
the hot deserialize path. Dropping the `LD_PRELOAD` restores byte-for-byte stock behavior.

This is the implementation of "Fix B" (serialize the connection) from the upstream notes, done
on our side. It supersedes the earlier interim mitigation for #1 (which only *capped the cost*
of each crash via `LimitCORE=0`); keep `LimitCORE=0` as belt-and-suspenders.

## Build
```sh
CC=gcc ./build.sh          # on an aarch64 builder (the cell's arch)
```
Produces `libtroubadour_busguard.so`. The source `.symver`-pins libc/pthread/dl to
`GLIBC_2.17` (aarch64 baseline), so the `.so` requires only `GLIBC_2.17` and loads on the
cell's glibc 2.30 whatever the builder's glibc is. `build.sh` fails if that invariant breaks.

## Deploy
```sh
scp libtroubadour_busguard.so root@<cell>:/usr/lib/troubadour/
cat >/etc/systemd/system/airplay2source.service.d/troubadour-busguard.conf <<CONF
[Service]
Environment=LD_PRELOAD=/usr/lib/troubadour/libtroubadour_busguard.so
CONF
systemctl daemon-reload && systemctl restart airplay2source
```
Confirm: `journalctl -u airplay2source | grep busguard` → `sd-bus connection serialization
active`.

## Rollback
```sh
rm /etc/systemd/system/airplay2source.service.d/troubadour-busguard.conf
systemctl daemon-reload && systemctl restart airplay2source
```

## Status
Deployed and verified on the spare Test Cell (`.39`): loads with no loader/version errors,
interposes all five symbols, `AirPlay servers started`, survives restart cycles, `NRestarts=0`,
clean rollback. Definitive confirmation (no `6/ABRT` on an Atmos stream) needs a soak with real
Dolby Atmos content to the Test Cell.

## 2026-10-20 review (§1.3) — treat busguard as UNVALIDATED
The documented premise (a connection-level read-cursor race pumped from two threads) does not match the
errors: ENXIO-on-enter / EBUSY-on-exit are *message*-local, so serialising the connection cannot by
itself prevent them. The likelier mechanism is sdbus-c++ 1.x `Variant` copies sharing one
`sd_bus_message` read from two threads at a format change. busguard also holds one recursive mutex across
the blocking `sd_bus_call` (25 s default), which can stall the event-loop thread's `sd_bus_process`.

Before trusting it, run the diagnostic shim `busguard_trace.c` (build:
`aarch64-linux-gnu-gcc -O2 -fPIC -shared -fno-stack-protector -static-libgcc busguard_trace.c -o libtroubadour_busguard_trace.so -ldl`)
**instead of** busguard, reproduce a format change, and check the journal: the same `msg=` pointer under
two different `tid=` values (around an ENXIO/EBUSY) confirms the Variant-copy theory — in which case the
fix belongs around sdbus-c++'s Variant, not the connection.
