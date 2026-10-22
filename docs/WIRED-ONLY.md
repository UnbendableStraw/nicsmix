# Wired-only cell (Wi-Fi radio off) — root cause and fix

Goal: a Syng Cell that runs as an AirPlay receiver over **USB-Ethernet with the Wi-Fi radio fully off**,
surviving reboot with no manual steps. This completes the "Wi-Fi radio off" item left open in
`WIRED.md` and `ROADMAP.md`.

Reverse-engineered from the stock `celldirector` (2.2.2, `cbc1e00e…`, stripped aarch64 PIE) with
Ghidra + capstone. Proven on the spare Test Cell `.39` (USB-NIC = `eth0`, MAC `00:E0:4C:…` Realtek),
reboot-verified twice.

## Why the earlier hypothesis was incomplete

`WIRED.md` guessed wired-only needs only "`wifi_repair_timeout` raised + celldirector's boot repair
timer patched out." Reversing celldirector shows **celldirector's whole connectivity model is
Wi-Fi-only**, and there are three independent things to defeat, not one:

1. **celldirector force-powers the Wi-Fi radio on.** `NetManager::configureWifiTech()` (called from the
   `NetManager` ctor for the connman technology whose type==WiFi) calls
   `syng::connman::Technology::setPowered(tech, true)` — so even with connman's persisted
   `[WiFi] Enable=false`, celldirector turns the radio back on at startup. It then calls
   `NetManager::startAutoconnStateMachine()` (its only caller), which scans and reconnects the saved AP.
   This is why `connmanctl disable wifi` / `Enable=false` never "stuck": celldirector overrode it.
2. **The WiFi-repair timer.** On a NetManager "disconnected" event, `CellDirector::handleNetworkEvent`
   (CellDirector.cpp:138) arms `CellDirector::startRepairModeTimer(wifi_repair_timeout)`; the timer
   lambda sets setup phase → `wifiRepair` and starts BLE provisioning advertising. Benign for traffic
   but it drops the cell into "repair/setup" mode, which is not a working state.
3. **Role never gets claimed → airplay2source hangs.** This is the real blocker, and the part the old
   notes missed. The cell's role (`com.syng.Cell.CoreServices.role` on `/com/syng/localcell`) is
   assigned via the Wi-Fi/SyFi mesh. With Wi-Fi off it stays `"configuring"` forever. **airplay2source
   blocks in `setupLocalCellProxy` (between AirPlay2Source.cpp:389 and :626) waiting on CellServer for a
   claimed role** — observed as a thread stuck in `unix_stream_read_generic` on the D-Bus socket. So
   even with celldirector healthy and Wi-Fi off, AirPlay never starts until a role is claimed.

`manage_syfi:false` does **not** help with any of these — it governs the SyFi mesh, not the
infra-Wi-Fi power/autoconnect/repair paths or role assignment.

## The fix (two pieces + connman settings)

### 1. celldirector binary patch — 3 sites, 12 bytes

Stops celldirector from fighting a Wi-Fi-off cell. File offsets == virtual addresses for these
(single LOAD, base 0). aarch64 little-endian.

| vaddr | original | patched | effect |
|---|---|---|---|
| `0x275694` | `bl 0x3ff028` (`Technology::setPowered`) `94062665` | `nop` `1f2003d5` | don't force Wi-Fi radio on in `configureWifiTech` |
| `0x2756d0` | `bl 0x279de0` (`startAutoconnStateMachine`) `940011c4` | `nop` `1f2003d5` | don't scan/reconnect Wi-Fi |
| `0x2b486c` | `stp x29,x30,[sp,#-0x50]!` `a9bb7bfd` (entry of `startRepairModeTimer`) | `ret` `c0035fd6` | no wifiRepair timer ever armed |

The `mov w1,#1` before the first site is left in place (harmless; the call is NOP'd). Patch builder:
`_local/re/celldirector/` → `patch.py` (verifies each original opcode before writing). Patched binary:
`celldirector.patched` (sha256 `f27c58e6…`). Install replaces `/usr/bin/celldirector`; original is
saved as `/usr/bin/celldirector.troubadour-orig`.

### 2. `troubadour-pin-primary.service` — claim a role without the mesh

A oneshot unit (`_local/re/celldirector/wired-cell/`) that, after celldirector is up, pins the cell
primary via celldirector's own public D-Bus API:

```
busctl call com.syng.CellServer /com/syng/localcell com.syng.Cell.CoreServices PinPrimary
```

`role` goes `configuring` → `primary`, which immediately unblocks `setupLocalCellProxy`; airplay2source
proceeds to "AirPlay servers started". Pinning is idempotent and runs every boot. (celldirector also
rewrites `role_strategy: "primary"` into its options on pin, but that alone does **not** auto-claim at
boot without the mesh — the service is what makes it deterministic.)

### 3. connman — keep the radio off and wired preferred

Persisted, so they survive reboot:
- `/etc/connman/main.conf`: `PreferredTechnologies = ethernet`, `AlwaysConnectedTechnologies = ethernet`
- `/var/lib/connman/settings`: `[WiFi] Enable=false`
- `/var/lib/connman/<wifi-service>/settings`: `AutoConnect=false`

Leave the Wi-Fi interface (`winfra0`) **out** of `NetworkInterfaceBlacklist` — the Wi-Fi *technology*
must stay present or celldirector's `NetManager` ctor loops on "WiFi tech not available" and the service
crash-restarts. We want the tech present-but-powered-off, not absent.

## Verified on .39 (reboot-proven)

Cold boot, zero manual steps:
- `wifi_powered=False`, `winfra0` has no IP, `eth0` (USB-NIC) is the sole interface and default route.
- celldirector `active`, `NRestarts=0`, no repair / no autoconnect in the journal.
- `troubadour-pin-primary` logs `pinned primary (was "configuring")`; `role = "primary"`.
- airplay2source reaches AirPlay2Source.cpp:626 and logs "AirPlay servers started"; `_airplay`/`_raop`
  advertised; TCP `:7000` open from the LAN over Ethernet.

## Caveat for fleet rollout

Pinning **primary** is correct for a **standalone** wired cell. A multi-cell wired *room* (stereo pair,
surround set) cannot have two primaries — those need real role negotiation over the wired link, which is
the remaining `WIRED.md` work (SourceCast + PTP clock over Ethernet). Apply the wired-only recipe only to
single-cell installs for now.

## Revert (per cell)

```
cp -a /usr/bin/celldirector.troubadour-orig /usr/bin/celldirector
systemctl disable --now troubadour-pin-primary.service
cp -a /etc/connman/main.conf.troubadour-bak /etc/connman/main.conf
cp -a /var/lib/connman/settings.troubadour-bak /var/lib/connman/settings
cp -a /var/lib/connman/<wifi-service>/settings.troubadour-bak /var/lib/connman/<wifi-service>/settings
systemctl restart connman; sleep 3; connmanctl enable wifi
systemctl restart celldirector airplay2source
```
