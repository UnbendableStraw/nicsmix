# Syng OS 2.2.2 vs 2.2.1 — what changed, and rebasing Troubadour on it

Syng's release notes for 2.2.2 (artifact `SyngOS-Troubadour-2.2.2`, built 2026-07-15; 2.2.1 was
built 2024-11-22) say almost nothing. This is the actual delta, from a file-level comparison of
the two root filesystems (`tools/fw/`: partition 2 of each `image.sdcard` dumped with `debugfs`
and hashed; 15 251 files, 2 added, 0 removed, ~100 changed excluding recompiled `.pyc`).

## The change: cloud authentication moved into one daemon

| | |
|---|---|
| **new** `/usr/bin/authtokend` + `authtokend.service` (`com.syng.authtokend`, object `/com/syng/AuthToken`, method `GetToken`) | logs in to `api.prod.syng.space/cell/login` with the device certificate from OP-TEE secure storage, caches the JWT ("Access token cached, expires in {} seconds") and hands it to every other cloud client over D-Bus |
| rebuilt: `cloudsyncd`, `mqttd`, `otamanager`, `logmanager`, `crashreporter`, `syngsoundserver` | now ask authtokend for the token ("Failed to get access token from authtokend") instead of logging in themselves; their units gained `Wants=/After=authtokend.service` (`pycalibrator.service` and `cloudbroker.service` too) |
| `syng/http/request.py` | `request(..., retries=N)`: the HTTPS *connect* is retried with exponential back-off (1, 2, 4 s); the request itself is still sent once |
| `syng/pycalibrator/cloudapi.py` | all three cloud calls use `retries=3`; the `fut_result.set_except` typo (which made the couch upload hang forever on auth failure — we worked around it) is fixed; upload exceptions now propagate |
| `syng/pycalibrator/__main__.py` | warms up the resolver once at start (`socket.getaddrinfo(api…)`), because the first lookup in a fresh process could fail with a bogus `EAI_SYSTEM` |
| `otamanager.service` | rewritten (`Type=dbus`, `CPUAffinity=0`, nice/idle IO, `Restart=always`) |
| `celldirector`, `airplay2source`, `hidserver`, `chanscan`, `rpcd`, `resetmanager`, `powermonitor`, `pd`, `sourcectl`, `syfidiagnose`, `dns-sd`, `mdnsd`, all `Syng*.so` DSP plugins, `libasound_module_pcm_syng.so`, `syngnative.so` | **rebuilt, same size, no new strings** — build-id/timestamp churn, no functional change found. `cellbroker` (the LAN control API) is byte-identical. |
| system libs | `libcrypto.so.1.1`, `libpaho-mqtt3*`, `libfdk-aac`, `libnss_mdns`, `libpd`, `libpython*`, `libperl`, `libgdbm`, nss `.chk` files, `libsystemd-shared`, `systemd-timesyncd`, busybox, kernel `Image`/`fitImage` (same 5.4.3 version string) — rebuilt |
| `etc/motd`, `etc/version`, `etc/timestamp`, `etc/build`, `etc/mender/artifact_info` | version stamps (`meta-syng` 6eeee0aa → 4d9cb1d7; `syng-embedded-*` 33f7472b → e9cc78ec) |

Nothing changed in the audio path, the spatialiser, SourceCast/SyFi, `cellbroker`, the cloud
*state* protocol (`PatchState`/`GetState` are still there) or the calibration maths — so
everything Troubadour does (agent, offline rooms, local calibration, LED, USB link) carries over.
`etc/connman/main.conf` is the same as stock 2.2.1 (`PreferredTechnologies = wifi,ethernet`) —
the earlier "ethernet first" line was ours and is gone since b39 (see FIRMWARE-MODS).

## Does it matter offline?

With the cloud blocked, `authtokend` just fails to log in and the daemons that depend on it log
"Failed to get access token" and carry on, exactly as 2.2.1 did with their own logins. The
practical gains of 2.2.2 for an offline cell are the `set_exception` fix and the retry logic in
the calibrator (less chance of a wedged `pycalibrator` after a run). Nothing in it is required.
Staying on 2.2.1 is fine; rebasing is cheap and keeps you current for whatever OTA-only fix comes
next, so the tooling does it reproducibly:

## Rebasing: `tools/fw/build-troubadour.sh`

```sh
brew install e2fsprogs xz          # macOS (debugfs); Linux: apt install e2fsprogs xz-utils
export ROOT_PASSWORD='something-else'              # optional; omit for key-only (uses SSH_PUBKEY) or an auto-generated one (§1.9)
export SSH_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"   # optional, default ~/.ssh/id_ed25519.pub
tools/fw/build-troubadour.sh "path/to/stock-2.2.2/image.sdcard.lzma" build-2.2.2
cd build-2.2.2 && ./flash.sh      # uuu, Cell in USB serial-download mode
```

The script edits partition 2 of the stock image in place with `debugfs -w` (no root, no loop
devices), applying `overlay/` plus the sshd/shadow/keys/OTA-mask/unit-enable/connman edits,
then copies `imx-boot` and the flash tooling next to the result. Verified on the 2.2.2 image:
the patched rootfs differs from stock in exactly 19 paths (13 added, 1 removed, 5 changed) and
passes `e2fsck -fn`. The 2.2.2-based `overlay/.../pycalibrator/cloudapi.py` is Syng's new file
plus the local-agent hook; the 2.2.1 variant is in git history.

Slot B (partition 3) is left as stock 2.2.2; U-Boot is pinned to slot A by the uuu script, as before.
