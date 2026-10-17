#!/usr/bin/env python3
"""syng-agent-b39.py = b38 + the never-set-up cell path, from a real factory-fresh Troubadour 2.2.2 flash:
- cloudsyncd that never got a document from the cloud silently drops PatchState -> the agent seeds
  /data/syng/cloud_state.json with the document and restarts cloudsyncd (+ celldirector, which only listens to the
  instance it started with); verified: role "configuring" -> PRIMARY, SyFi up.
- whoami.version from /etc/mender/artifact_info (the state document's syng_os_version is whatever the cloud last knew).
- setup page: a wired-only speaker cannot take a room role (celldirector goes into Wi-Fi repair mode after 20 s
  without infra Wi-Fi) -> say so instead of showing "configuring" forever."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b38.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

rep('''def apply_view(view):
    """Apply a member view locally (PatchState) and remember it."""
    _ensure_dir(); backup_state_once()
    patch = {"cell_state": view.get("cell_state") or {}, "room_state": view.get("room_state") or {},
             "room_neighbours": view.get("room_neighbours") or []}
    ok = patch_state_strict(patch)
''', '''def seed_state(patch):
    """A factory-fresh cell: cloudsyncd has no base document (the cloud never answered, or is blocked) and quietly
    drops PatchState. It does load /data/syng/cloud_state.json at start, so write the document there and restart it;
    celldirector only follows the cloudsyncd instance it started with, so it is restarted too (harmless: it is in
    its factory/placed phase with nothing to do yet)."""
    try:
        open(CLOUD_STATE_FILE, "w").write(json.dumps(patch, indent=1))
    except Exception as e:
        log("seed: cannot write %s: %s" % (CLOUD_STATE_FILE, e)); return False
    _sh("systemctl restart cloudsyncd", 30)
    for _ in range(30):
        time.sleep(0.5)
        st = raw_state()
        if (st.get("room_state") or {}).get("room_id") == (patch.get("room_state") or {}).get("room_id"): break
    else:
        log("seed: cloudsyncd did not come back with the document"); return False
    set_placed()   # celldirector reads its phase file only at start: in "factory" it ignores the room document and waits for the app's BLE setup
    _sh("systemctl restart celldirector", 30)
    log("seed: state document seeded, cloudsyncd + celldirector restarted")
    return True

def apply_view(view):
    """Apply a member view locally (PatchState) and remember it."""
    _ensure_dir(); backup_state_once()
    patch = {"cell_state": view.get("cell_state") or {}, "room_state": view.get("room_state") or {},
             "room_neighbours": view.get("room_neighbours") or []}
    fresh = not (raw_state().get("cell_state") or {}).get("serial_number")
    ok = patch_state_strict(patch)
    if fresh or not (raw_state().get("room_state") or {}).get("room_id"):
        ok = seed_state(patch) and ok
''')
rep('''            "model": cs.get("model") or "Cell Alpha", "version": cs.get("syng_os_version"),''',
    '''            "model": cs.get("model") or "Cell Alpha", "version": fw_version() or cs.get("syng_os_version"),''')
rep('''def raw_state():
    """The cloudsyncd state document (cell_state / room_state / room_neighbours)."""''',
    '''def fw_version():
    """Troubadour-2.2.2 etc. from mender's artifact_info (what is really flashed)."""
    for l in _read("/etc/mender/artifact_info").splitlines():
        if l.startswith("artifact_name="): return l.split("=", 1)[1].strip()
    return None

def raw_state():
    """The cloudsyncd state document (cell_state / room_state / room_neighbours)."""''')
# setup page: role 'configuring' without Wi-Fi = celldirector's Wi-Fi repair mode
rep('''  if(m.wifi&&m.wifi.ssid) b.push(`<span class=badge>Wi-Fi ${m.wifi.ssid}</span>`); else b.push(`<span class="badge warn">no Wi-Fi</span>`);''',
    '''  if(m.wifi&&m.wifi.ssid) b.push(`<span class=badge>Wi-Fi ${m.wifi.ssid}</span>`); else b.push(`<span class="badge warn">no Wi-Fi${m.room_name?' — join Wi-Fi below: a wired-only speaker cannot take a room role (the firmware waits for Wi-Fi)':''}</span>`);''')

s = s.replace('"syng-agent (b38)"', '"syng-agent (b39)"').replace("syng-agent (b38) on", "syng-agent (b39) on").replace('AGENT_BUILD = "b38"', 'AGENT_BUILD = "b39"')
out = os.path.join(here, "syng-agent-b39.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True); print("wrote", out, len(s))
