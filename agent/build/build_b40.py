#!/usr/bin/env python3
"""syng-agent-b40.py = b39 +
- SyFi link addresses in the room document: the primary's SourceCast streams to each neighbour's `syfi_ip`, which
  the cloud used to fill in from the cells' uploads. Without the cloud a freshly joined cell got no stream at all
  (LEDs pulsed from the viz relay, no audio). Now: whoami exposes syfi_ip/syfi_good/time_sync_good; the primary
  learns them right after admitting a cell (fast poll, up to 2 min) and in the keeper; OWN_KEYS carries them.
- volume/transport/tone/mute go over a private D-Bus connection + lock, so a control never waits behind the
  1.5 s status poll (measured: 170-500 ms -> ~40 ms); `volume all` targets the active music source(s).
- cheaper status poll: source ids cached 10 s, inactive sources only fetch base props."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b39.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# ---- private control bus ----
rep('''def set_prop(svc, path, iface, prop, variant):
    with DBUS_LOCK:
        dbus.Interface(BUS.get_object(svc, path), PROPS).Set(iface, prop, variant)
''', '''# Controls get their own bus connection: the status poll holds DBUS_LOCK for most of a second (a GetAll storm over
# every source), and a volume change must not queue behind it. D-Bus connections are independent, so a second
# connection with its own lock makes a Set ~40 ms whatever the poll is doing.
try:
    CTRL_BUS = dbus.SystemBus(private=True)
except Exception:
    CTRL_BUS = BUS
CTRL_LOCK = threading.RLock()

def set_prop(svc, path, iface, prop, variant):
    with CTRL_LOCK:
        dbus.Interface(CTRL_BUS.get_object(svc, path), PROPS).Set(iface, prop, variant)

_src_cache = {"t": 0.0, "ids": None, "targets": {}}
def source_target_cached(sid):
    """(destination, path) of a source's property object; owners don't move, so cache 10 s (the runtime's
    `active` flag is still read live by whoever needs it)."""
    now = time.time()
    if now - _src_cache["t"] > 10: _src_cache["targets"] = {}; _src_cache["t"] = now
    hit = _src_cache["targets"].get(sid)
    if hit: return hit
    dest, path, rt = source_target(sid)
    if dest and path: _src_cache["targets"][sid] = (dest, path)
    return dest, path
''')
rep('''def source_ids():
    ids = children(ASRV, "/com/syng/AudioSource/Runtime")
    return ids or ["airplay2", "spotify", "tidal", "CalibrationClient"]
''', '''_ids_cache = {"t": 0.0, "ids": None}
def source_ids():
    now = time.time()
    if _ids_cache["ids"] is not None and now - _ids_cache["t"] < 10: return _ids_cache["ids"]
    ids = children(ASRV, "/com/syng/AudioSource/Runtime") or ["airplay2", "spotify", "tidal", "CalibrationClient"]
    _ids_cache["ids"] = ids; _ids_cache["t"] = now
    return ids
''')
# volume: active music sources first, over the control bus, no GetAll storm
rep('''def set_volume(sid, vol):
    vol = max(0.0, min(1.0, float(vol)))
    targets = [x for x in source_ids() if is_music(x)] if sid in (None, "", "all") else [sid]
    n = 0
    for s in targets:
        dest, path, _ = source_target(s)
        if not dest or not path: continue
        try:
            set_prop(dest, path, ASRC_IF, "volume", dbus.Double(vol, variant_level=1)); n += 1
        except Exception: pass
    return n
''', '''def active_music_sources():
    out = []
    for x in source_ids():
        if not is_music(x): continue
        try:
            with CTRL_LOCK:
                a = dbus.Interface(CTRL_BUS.get_object(ASRV, "/com/syng/AudioSource/Runtime/" + x), PROPS).Get(RUN_IF, "active")
            if bool(a): out.append(x)
        except Exception: pass
    return out

def set_volume(sid, vol):
    vol = max(0.0, min(1.0, float(vol)))
    if sid in (None, "", "all"):
        targets = active_music_sources() or [x for x in source_ids() if is_music(x)]   # what is playing; else everything
    else: targets = [sid]
    n = 0
    for s in targets:
        dest, path = source_target_cached(s)
        if not dest or not path: continue
        try:
            set_prop(dest, path, ASRC_IF, "volume", dbus.Double(vol, variant_level=1)); n += 1
        except Exception:
            _src_cache["targets"].pop(s, None)
    return n
''')
rep('''    with DBUS_LOCK:
        I = dbus.Interface(BUS.get_object(dest, path), "com.syng.AudioSource.TransportControl")
        if m == "seek": I.seek(dbus.Double(float(position or 0)))
        else: getattr(I, m)()
    return True''', '''    with CTRL_LOCK:
        I = dbus.Interface(CTRL_BUS.get_object(dest, path), "com.syng.AudioSource.TransportControl")
        if m == "seek": I.seek(dbus.Double(float(position or 0)))
        else: getattr(I, m)()
    return True''')
# cheaper poll: inactive sources skip metadata/transport/artwork
rep('''        base = get_all(dest, path, ASRC_IF)
        meta = get_all(dest, path, "com.syng.AudioSource.Metadata")
        tc = get_all(dest, path, "com.syng.AudioSource.TransportControl")
        tone = None
        try: tone = json.loads(base.get("toneControl", "")) if base.get("toneControl") else None
        except Exception: tone = None
        art = get_all(dest, path, "com.syng.AudioSource.Artwork")''',
'''        base = get_all(dest, path, ASRC_IF)
        if rt.get("active"):
            meta = get_all(dest, path, "com.syng.AudioSource.Metadata")
            tc = get_all(dest, path, "com.syng.AudioSource.TransportControl")
            art = get_all(dest, path, "com.syng.AudioSource.Artwork")
        else: meta = {}; tc = {}; art = {}   # an idle source has nothing playing: three D-Bus round trips saved per source per poll
        tone = None
        try: tone = json.loads(base.get("toneControl", "")) if base.get("toneControl") else None
        except Exception: tone = None''')

# ---- SyFi link fields ----
rep('''            "ip": ip, "ips": iface_addrs(), "hostname": socket.gethostname(),''',
    '''            "ip": ip, "ips": iface_addrs(), "hostname": socket.gethostname(),
            "syfi_ip": (str(lp.get("syfiIP")) if lp.get("syfiIP") else None) or cs.get("syfi_ip"),
            "syfi_good": bool(lp.get("syfiGood")) if lp.get("syfiGood") is not None else cs.get("syfi_good"),
            "time_sync_good": bool(lp.get("timeSyncGood")) if lp.get("timeSyncGood") is not None else None,
            "bssid": (str(lp.get("infraBSSID")) if lp.get("infraBSSID") else None) or cs.get("bssid"),''')
rep('''OWN_KEYS = ("serial_number", "room_id", "place_id", "role", "role_pinned", "calibrated", "position", "nearby_walls",
            "is_upside_down", "height_layer", "online", "ip_address", "version")''',
    '''OWN_KEYS = ("serial_number", "room_id", "place_id", "role", "role_pinned", "calibrated", "position", "nearby_walls",
            "is_upside_down", "height_layer", "online", "ip_address", "version", "syfi_ip", "syfi_good", "bssid")''')
rep('''        "role": role, "role_pinned": False, "online": "true", "last_seen_at": now,''',
    '''        "role": role, "role_pinned": False, "online": "true", "last_seen_at": now,
        "syfi_ip": w.get("syfi_ip"), "syfi_good": w.get("syfi_good"), "bssid": w.get("bssid"),''')
# keeper: mirror link fields for every member, self included
rep('''                    if s == doc["me"]:
                        if w.get("ip") and c.get("ip_address") != w["ip"]: c["ip_address"] = w["ip"]; changed = True
                        if w.get("role") and c.get("role") != w["role"]: c["role"] = w["role"]; changed = True
                        continue''', '''                    if s == doc["me"]:
                        if w.get("ip") and c.get("ip_address") != w["ip"]: c["ip_address"] = w["ip"]; changed = True
                        if w.get("role") and c.get("role") != w["role"]: c["role"] = w["role"]; changed = True
                        if link_sync(c, w): changed = True
                        continue''')
rep('''                        if o.get("ssh_key") and o["ssh_key"] != c.get("ssh_key"): c["ssh_key"] = o["ssh_key"]; changed = True''',
    '''                        if o.get("ssh_key") and o["ssh_key"] != c.get("ssh_key"): c["ssh_key"] = o["ssh_key"]; changed = True
                        if o.get("serial") == s and link_sync(c, o): changed = True''')
rep('''def keeper_loop():
    time.sleep(20)''', '''def link_sync(c, w):
    """Copy a member's live SyFi link facts (from its whoami) into its document entry. The primary's SourceCast
    sends the audio to each neighbour's syfi_ip — the cloud used to fill it in; now we do."""
    changed = False
    for k in ("syfi_ip", "syfi_good", "bssid"):
        v = w.get(k)
        if v is not None and v != "" and c.get(k) != v: c[k] = v; changed = True
    return changed

def link_watch(serial, ip, seconds=150):
    """Right after admitting a cell: its SyFi association takes a few seconds to a minute; poll its whoami until
    the link address shows up and fan it out at once, instead of waiting for the next keeper pass (up to 90 s
    of silence on the new speaker)."""
    def run():
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(4)
            try:
                o = agent_get(ip, "/api/whoami", timeout=3)
                if o.get("serial") != serial or not o.get("syfi_ip"): continue
                with LC_LOCK:
                    doc = room_doc(); c = doc["cells"].get(serial)
                    if not c: return
                    if link_sync(c, o):
                        bump(doc["room_state"]); fan_out(doc); log("link: %s syfi_ip %s -> fanned out" % (serial[-5:], o.get("syfi_ip")))
                    return
            except Exception: pass
        log("link: %s never reported a syfi_ip" % serial[-5:])
    threading.Thread(target=run, daemon=True).start()

def keeper_loop():
    time.sleep(20)''')
# start the watch when a cell is admitted
a = s.index("def op_admit(w):"); b = s.index("\ndef ", a + 10)
blk = s[a:b]
assert "fan_out(doc" in blk, "admit body changed"
import re
m = re.search(r"\n(\s+)res = fan_out\(doc[^\n]*\n", blk)
assert m, blk[:400]
ind = m.group(1)
blk2 = blk.replace(m.group(0), m.group(0) + "%slink_watch(w[\"serial\"], w.get(\"ip\"))\n" % ind, 1)
s = s[:a] + blk2 + s[b:]

# dashboard slider: a little snappier (the server is no longer the bottleneck)
rep("el.addEventListener('input',()=>{$('#'+label).textContent=el.value;throttle(id,()=>onsend(+el.value),120);});",
    "el.addEventListener('input',()=>{$('#'+label).textContent=el.value;throttle(id,()=>onsend(+el.value),70);});")


# ---- share one status computation between all pollers (browsers + secondaries' nowplaying): collect() is ~25 D-Bus
# round trips, and the primary was running it 5-6 times per 1.5 s on an already busy CPU ----
rep("""            if self.path.startswith("/api/state"): self._json(200, collect())""",
    """            if self.path.startswith("/api/state"): self._json(200, cached("state", 0.9, collect))""")
rep("""    if path.startswith("/api/nowplaying"): return nowplaying()""",
    """    if path.startswith("/api/nowplaying"): return cached("nowplaying", 0.9, nowplaying)""")
rep("""def collect():
    core = get_all("com.syng.CellServer", "/com/syng/localcell", "com.syng.Cell.CoreServices")""",
    """_cache = {}; _cache_lock = threading.Lock(); _cache_locks = {}
def cached(key, ttl, fn):
    \"\"\"Return fn()'s result, at most once per ttl seconds; concurrent callers wait for the one computation.\"\"\"
    with _cache_lock:
        ent = _cache.get(key)
        if ent and time.time() - ent[0] < ttl: return ent[1]
        lk = _cache_locks.setdefault(key, threading.Lock())
    with lk:
        with _cache_lock:
            ent = _cache.get(key)
            if ent and time.time() - ent[0] < ttl: return ent[1]
        val = fn()
        with _cache_lock: _cache[key] = (time.time(), val)
        return val

def collect():
    core = get_all("com.syng.CellServer", "/com/syng/localcell", "com.syng.Cell.CoreServices")""")

s = s.replace('"syng-agent (b39)"', '"syng-agent (b40)"').replace("syng-agent (b39) on", "syng-agent (b40) on").replace('AGENT_BUILD = "b39"', 'AGENT_BUILD = "b40"')
out = os.path.join(here, "syng-agent-b40.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True); print("wrote", out, len(s))
