#!/usr/bin/env python3
# syng-agent (Phase 2) — live dashboard + controls for a Syng Cell group, served
# from any speaker. Bridges local D-Bus to HTTP. Controls (tone/volume/source) call
# the exact methods the Syng app uses. Python 3.7 stdlib + python3-dbus. Offline.
# Listen: 0.0.0.0:8080
import json, sys, uuid, os, time, shlex, subprocess, traceback
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import dbus

BUS = dbus.SystemBus()
PROPS = "org.freedesktop.DBus.Properties"
INTRO = "org.freedesktop.DBus.Introspectable"
ASRV = "com.syng.AudioSourceServer"
ASRC_IF = "com.syng.AudioSource"
RUN_IF = "com.syng.AudioSource.Runtime"
PORT = int(os.environ.get("PORT", "8080"))
MUSIC = ("airplay2", "spotify", "tidal")
def is_music(sid):
    """A selectable playback source: anything the sound server registers except the calibration client and
       one-shot file players (file@…), so hot-plugged USB-C / HDMI / Pure Data sources show up too."""
    sid = str(sid or "")
    return bool(sid) and not sid.startswith(("Calibration", "file@", "file")) and sid != "saplayer"
VIZ_OBJ = "/com/syng/AudioSource/VisualizationDataBroadcast"
VIZ_IF = "com.syng.AudioSource.VisualizationDataBroadcast"
VIZ_LED_PORT = int(os.environ.get("VIZ_LED_PORT", "7249"))
VIZ_WEB_PORT = int(os.environ.get("VIZ_WEB_PORT", "7250"))
import urllib.request, threading, socket, struct, math

def log(msg):
    try: sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg)); sys.stderr.flush()
    except Exception: pass

def peer_ips():
    try:
        st = get_state()
        return [c["ip"] for c in st.get("fleet", []) if not c.get("isMe") and c.get("ip")]
    except Exception:
        return []

def fanout(path, body):
    """Replicate a control action to every other cell in the room (needs the agent on them)."""
    data = json.dumps(dict(body, _local=True)).encode()
    for ip in peer_ips():
        try:
            urllib.request.urlopen(urllib.request.Request(
                "http://%s:%d%s" % (ip, PORT, path), data=data,
                headers={"Content-Type": "application/json"}, method="POST"), timeout=2)
        except Exception: pass

# ---------- viz feed fan-out (VisualizationDataBroadcast -> all cells) ----------
def viz_if():
    return dbus.Interface(BUS.get_object(ASRV, VIZ_OBJ), VIZ_IF)

def viz_subscribers():
    try:
        with DBUS_LOCK: return [(str(ip), int(port)) for ip, port in viz_if().getSubscriberList()]
    except Exception: return []

def viz_subscribe(ip, port):
    try:
        with DBUS_LOCK: viz_if().subscribe(str(ip), dbus.UInt32(int(port)))
        return True
    except Exception: return False

def viz_unsubscribe(ip):
    try:
        with DBUS_LOCK: viz_if().unsubscribe(str(ip))
        return True
    except Exception: return False

def is_primary_cell(st):
    """True if this cell has a local viz feed: primary of its room, or standalone."""
    me = next((c for c in st.get("fleet", []) if c.get("isMe")), None)
    if not me: return True
    return "secondary" not in (me.get("role") or "").lower()

def room_member_ips(st):
    ips = []
    for c in st.get("fleet", []):
        ip = c.get("ip")
        if ip and ip not in ips:
            ips.append(ip)
    return ips

def viz_fanout_loop():
    """On the primary, subscribe every room member (incl. self) to the local viz
    feed at VIZ_WEB_PORT. The broadcaster allows one subscription per IP, so each
    cell's agent is the single receiver; it relays packets locally to the LED
    daemon. The primary is included, so solo cells self-feed."""
    while True:
        try:
            st = get_state()
            if is_primary_cell(st):
                subs = dict((ip, port) for ip, port in viz_subscribers())
                for ip in room_member_ips(st):
                    if subs.get(ip) != VIZ_WEB_PORT:
                        viz_unsubscribe(ip)                 # clear any stale port for this IP
                        viz_subscribe(ip, VIZ_WEB_PORT)
                        log("VIZ subscribe %s:%d" % (ip, VIZ_WEB_PORT))
        except Exception:
            pass
        time.sleep(8)

# ---------- web-field viz receiver (latest frame for the browser) ----------
LAST_VIZ = {"energy": 0.0, "bass": 0.0, "spec": [], "src": None, "t": 0.0}
_viz_env = [0.0, 0.0]  # smoothed [energy, bass]

def _mpd(b, i=0):
    c = b[i]; i += 1
    if c < 0x80: return c, i
    if c >= 0xe0: return c - 256, i
    if 0x80 <= c <= 0x8f:
        d = {}
        for _ in range(c & 0x0f):
            k, i = _mpd(b, i); v, i = _mpd(b, i); d[k] = v
        return d, i
    if 0x90 <= c <= 0x9f:
        a = []
        for _ in range(c & 0x0f):
            v, i = _mpd(b, i); a.append(v)
        return a, i
    if 0xa0 <= c <= 0xbf:
        n = c & 0x1f; return b[i:i+n].decode("utf-8", "replace"), i + n
    if c == 0xc0: return None, i
    if c == 0xc2: return False, i
    if c == 0xc3: return True, i
    if c == 0xd0: return struct.unpack_from(">b", b, i)[0], i + 1
    if c == 0xd1: return struct.unpack_from(">h", b, i)[0], i + 2
    if c == 0xd2: return struct.unpack_from(">i", b, i)[0], i + 4
    if c == 0xcc: return b[i], i + 1
    if c == 0xcd: return struct.unpack_from(">H", b, i)[0], i + 2
    if c == 0xce: return struct.unpack_from(">I", b, i)[0], i + 4
    if c == 0xd9:
        n = b[i]; i += 1; return b[i:i+n].decode("utf-8", "replace"), i + n
    if c == 0xda:
        n = struct.unpack_from(">H", b, i)[0]; i += 2
        return b[i:i+n].decode("utf-8", "replace"), i + n
    if c == 0xdc:
        n = struct.unpack_from(">H", b, i)[0]; i += 2; a = []
        for _ in range(n):
            v, i = _mpd(b, i); a.append(v)
        return a, i
    return None, i

def _db_norm(centi, lo=-56.0, hi=-6.0):
    db = centi / 100.0
    return max(0.0, min(1.0, (db - lo) / (hi - lo)))

VIZ_COND = threading.Condition()
VIZ_SEQ = [0]
_vz = {"peak": -40.0, "ema": None, "pk": None, "beat": 0.0, "e": 0.0, "b": 0.0, "fps": 30.0, "last": 0.0}

def viz_web_loop():
    """Receive the cell's VisualizationDataBroadcast (msgpack over UDP, 88 dB bins) and turn it
    into what the field needs: per-bin *level* (relative to a slowly decaying per-bin peak, 30 dB
    window), per-bin *flux* (onset above a short running average), energy (RMS relative to a
    decaying peak, 20 dB window), bass level and a fast beat signal. Wakes the SSE streamers."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", VIZ_WEB_PORT))
    except Exception as e:
        log("VIZ web bind failed: %s" % e); return
    relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    B = 24
    while True:
        try:
            data, _ = s.recvfrom(65535)
            try: relay.sendto(data, ("127.0.0.1", VIZ_LED_PORT))   # feed the local LED daemon
            except Exception: pass
            obj, _ = _mpd(data, 0)
            if not isinstance(obj, dict): continue
            spec = (obj.get("stems") or [[]])[0] or []
            rms = obj.get("stemsRms") or []
            n = len(spec)
            if not n: continue
            now = time.time()
            if _vz["last"]:
                dt = now - _vz["last"]
                if 0.005 < dt < 0.5: _vz["fps"] += 0.1 * (1.0 / dt - _vz["fps"])
            _vz["last"] = now
            db = [x / 100.0 for x in spec]
            if _vz["ema"] is None or len(_vz["ema"]) != n:
                _vz["ema"] = list(db); _vz["pk"] = list(db)
            ema = _vz["ema"]; pk = _vz["pk"]
            level = [0.0] * n; flux = [0.0] * n
            for i in range(n):
                d = db[i]
                pk[i] = d if d > pk[i] else pk[i] - 0.04           # ~1.2 dB/s decay at 30 fps
                level[i] = max(0.0, min(1.0, (d - (pk[i] - 30.0)) / 30.0))
                flux[i] = max(0.0, min(1.0, (d - ema[i]) / 9.0))
                ema[i] += 0.10 * (d - ema[i])
            rms_db = (max(rms) / 100.0) if rms else (sum(db) / n)
            _vz["peak"] = rms_db if rms_db > _vz["peak"] else _vz["peak"] - 0.03
            energy = max(0.0, min(1.0, (rms_db - (_vz["peak"] - 20.0)) / 20.0))
            nb = max(2, n // 8)
            bass = sum(level[1:nb]) / (nb - 1)
            beat = min(1.0, sum(flux[1:nb]) / (nb - 1) * 1.6)
            _vz["e"] += (0.6 if energy > _vz["e"] else 0.2) * (energy - _vz["e"])
            _vz["b"] += (0.6 if bass > _vz["b"] else 0.25) * (bass - _vz["b"])
            _vz["beat"] = beat if beat > _vz["beat"] else _vz["beat"] * 0.75
            step = n / float(B); sp = []; fx = []
            for k in range(B):
                a = int(k * step); b2 = max(a + 1, int((k + 1) * step))
                sp.append(round(sum(level[a:b2]) / (b2 - a), 3)); fx.append(round(max(flux[a:b2]), 3))
            with VIZ_COND:
                VIZ_SEQ[0] += 1
                LAST_VIZ.update({"energy": round(_vz["e"], 3), "bass": round(_vz["b"], 3), "beat": round(_vz["beat"], 3),
                                 "spec": sp, "flux": fx, "fps": round(_vz["fps"], 1), "src": obj.get("sourceId"), "t": now, "seq": VIZ_SEQ[0]})
                VIZ_COND.notify_all()
        except Exception:
            time.sleep(0.05)

def viz_frame():
    if time.time() - LAST_VIZ.get("t", 0) < 0.6:
        d = dict(LAST_VIZ); d["live"] = True; return d
    return {"energy": 0.0, "bass": 0.0, "beat": 0.0, "spec": [], "flux": [], "src": None, "live": False, "seq": VIZ_SEQ[0]}

def py(v):
    if isinstance(v, dbus.ByteArray): return bytes(v).decode("latin1", "replace")
    if isinstance(v, dbus.Dictionary): return {str(py(k)): py(x) for k, x in v.items()}
    if isinstance(v, (dbus.Array, list, tuple)): return [py(x) for x in v]
    if isinstance(v, dbus.Boolean): return bool(v)
    if isinstance(v, (dbus.Int16, dbus.Int32, dbus.Int64, dbus.UInt16, dbus.UInt32, dbus.UInt64, dbus.Byte)): return int(v)
    if isinstance(v, dbus.Double): return float(v)
    if isinstance(v, (dbus.String, str)): return str(v)
    return v

# python-dbus shares one connection; ThreadingHTTPServer calls it from many threads.
# Serialize every bus op so concurrent requests can't contend/stall the connection.
DBUS_LOCK = threading.RLock()

def get_all(svc, path, iface):
    try:
        with DBUS_LOCK:
            return py(dbus.Interface(BUS.get_object(svc, path), PROPS).GetAll(iface))
    except Exception: return {}

def set_prop(svc, path, iface, prop, variant):
    with DBUS_LOCK:
        dbus.Interface(BUS.get_object(svc, path), PROPS).Set(iface, prop, variant)

def call(svc, path, iface, method, *args):
    try:
        with DBUS_LOCK:
            return py(getattr(dbus.Interface(BUS.get_object(svc, path), iface), method)(*args))
    except Exception: return None

def children(svc, path):
    try:
        from xml.etree import ElementTree as ET
        with DBUS_LOCK:
            xml = dbus.Interface(BUS.get_object(svc, path), INTRO).Introspect()
        return [n.get("name") for n in ET.fromstring(xml).findall("node") if n.get("name")]
    except Exception: return []

def source_ids():
    ids = children(ASRV, "/com/syng/AudioSource/Runtime")
    return ids or ["airplay2", "spotify", "tidal", "CalibrationClient"]

def source_target(sid):
    """The per-source property object is owned by the source's own connection.
    The Runtime object exposes its `destination` (bus name) and `path`."""
    rt = get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF)
    return rt.get("destination"), rt.get("path"), rt

# ---------- controls (methods mirrored from the Syng app's D-Bus traffic) ----------
def patch_state(patch):
    """Merge a patch into the cloudsyncd state doc (what the Syng app displays)."""
    try:
        with DBUS_LOCK:
            dbus.Interface(BUS.get_object("com.syng.cloudsyncd", "/com/syng/StateFetcher"),
                           "com.syng.StateFetcher").PatchState(json.dumps(patch))
        return True
    except Exception:
        return False

def set_tone(bass, tilt):
    bass = max(-1.0, min(1.0, float(bass))); tilt = max(-1.0, min(1.0, float(tilt)))
    tc = {"bass": bass, "tilt": tilt, "mapping_version": "1.0.0", "version": str(uuid.uuid4()).upper()}
    val = json.dumps(tc)
    n = 0
    for sid in source_ids():
        dest, path, _ = source_target(sid)
        if not dest or not path: continue
        try:
            set_prop(dest, path, ASRC_IF, "toneControl", dbus.String(val, variant_level=1)); n += 1
        except Exception: pass
    # mirror into the room state doc so the Syng app's EQ display follows our change
    patch_state({"room_state": {"tone_control": tc}})
    return n

def set_volume(sid, vol):
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

def set_source(sid, on):
    with DBUS_LOCK:
        i = dbus.Interface(BUS.get_object(ASRV, "/com/syng/AudioSource/Runtime/" + sid), RUN_IF)
        (i.Activate if on else i.Deactivate)()
    return True

def set_source_prop(prop, variant, music_only=True):
    """Set a boolean/typed AudioSource property on each (music) source's owner."""
    n = 0
    for sid in source_ids():
        if music_only and not is_music(sid): continue
        dest, path, _ = source_target(sid)
        if not dest or not path: continue
        try: set_prop(dest, path, ASRC_IF, prop, variant); n += 1
        except Exception: pass
    return n

def set_bool_prop(prop, on):
    return set_source_prop(prop, dbus.Boolean(bool(on), variant_level=1))

# ---------- listener position / room EQ / soundcheck (calibration) ----------
LO_OBJ = "/com/syng/AudioSource/ListenerOffset"
LO_IF = "com.syng.AudioSource.ListenerOffset"
_lo_ver = [0]
def set_listener(x, y):
    _lo_ver[0] += 1
    val = json.dumps({"x_m": float(x), "y_m": float(y), "version": str(_lo_ver[0])})
    set_prop(ASRV, LO_OBJ, LO_IF, "position", dbus.String(val, variant_level=1))
    try: set_prop(ASRV, LO_OBJ, LO_IF, "enable", dbus.Boolean(True, variant_level=1))
    except Exception: pass
    return True

def listener_live():
    try:
        s = get_all(ASRV, LO_OBJ, LO_IF).get("position")
        d = json.loads(s) if s else None
        if d and d.get("x_m") is not None: return {"x_m": d["x_m"], "y_m": d["y_m"]}
    except Exception: pass
    return None

def room_eq(on):
    return call("com.syng.OutputGraph", "/com/syng/OutputGraph", "com.syng.OutputGraph",
                "enableRoomEQs" if on else "disableRoomEQs") is not None or True

# ---------- spatial: "move sound around" (Runtime.SetObjectPositions) ----------
# Baseline constellation captured from the Syng app (stereo content): 10 objects whose
# offsets from the soundstage center are fixed; the app moves the stage by translating
# them together. Objects 0-2 are the audible image; 3-9 are parked/muted (-100 dB).
OBJ_TEMPLATE = [
    {"dx": 0.05, "dy": 1.94, "focus": 0.8, "vol": 0.0},
    {"dx": 0.33, "dy": 1.91, "focus": 0.8, "vol": 0.0},
    {"dx": 0.18, "dy": 1.79, "focus": 0.9, "vol": -3.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
    {"dx": -0.08, "dy": -0.81, "focus": 0.5, "vol": -100.0},
]
SOUND_CENTER = [None]   # [x_m, y_m]; lazy-init to the listener position
SOUND_FOCUS = [None]    # override focus for the audible objects (None = template)
OBJ_ROT = [0.0]       # rotation of the field (radians) — the app's handle gesture
# --- captured from the real app (SetObjectPositions): the 10-object surround ring
#     (rel_angle deg, dist_m, focus, vol_dB), authored around aim ~19 deg. ---
SURROUND_RING = [(33,2.77,0.3,-5.0),(73,2.75,0.2,-5.0),(113,2.72,0.3,-5.0),(214,2.71,0.6,-6.0),
                 (294,2.76,0.6,-6.0),(213,2.18,0.6,-4.5),(294,2.19,0.6,-4.5),(254,2.19,0.1,-6.5),
                 (113,4.11,0.6,-4.5),(33,4.12,0.6,-4.5)]
SOUND_AIM = [0.0]       # degrees, 0 = front
SOUND_SPREAD = [55.0]   # degrees, angular width (>=300 -> full surround ring)
SOUND_DIST = [0.0]      # metres from the listener (0 = the puck rests on the listener, like the app)
SOUND_ROT = [0.0]       # degrees, field direction (the notch), 0 = front
SOUND_SPATIAL = [False] # False = classic / sound all around; True = the spatial fan
FAN_R = float(os.environ.get('SOUND_FAN_R', '1.0'))  # metres: radius of the object fan around the sound image
_active_cache = [0.0, None]  # [ts, sid] — avoid re-introspecting on every drag frame

def active_music_src():
    if time.time() - _active_cache[0] < 3 and _active_cache[1]:
        return _active_cache[1]
    sid = None
    for s in source_ids():
        if not is_music(s): continue
        if get_all(ASRV, "/com/syng/AudioSource/Runtime/" + s, RUN_IF).get("active"):
            sid = s; break
    _active_cache[0] = time.time(); _active_cache[1] = sid
    return sid

def set_objects(cx, cy, focus=None, rot=None):
    cx = float(cx); cy = float(cy)
    if focus is not None: SOUND_FOCUS[0] = max(0.1, min(1.0, float(focus)))
    if rot is not None: OBJ_ROT[0] = float(rot)
    ca = math.cos(OBJ_ROT[0]); sa = math.sin(OBJ_ROT[0])
    lp = listener_live() or {"x_m": cx, "y_m": cy}
    objects = {}
    for i, o in enumerate(OBJ_TEMPLATE):
        f = o["focus"]
        if SOUND_FOCUS[0] is not None and o["vol"] > -50:  # only the audible objects
            f = SOUND_FOCUS[0]
        dx = o["dx"] * ca - o["dy"] * sa     # rotate the constellation (the handle gesture)
        dy = o["dx"] * sa + o["dy"] * ca
        objects[str(i)] = {
            "snapToHorn": False, "distanceAttenuation": False, "volumeDecibels": o["vol"],
            "position": {"scope": "room", "focus": f, "height_layer": 0,
                         "location": {"x": round(cx + dx, 3), "y": round(cy + dy, 3), "z": 0}}}
    payload = json.dumps({"listener_position": {"x_m": lp["x_m"], "y_m": lp["y_m"], "version": "0"},
                          "objects": objects})
    sid = active_music_src()
    n = 0
    if sid:
        try:
            with DBUS_LOCK:
                dbus.Interface(BUS.get_object(ASRV, "/com/syng/AudioSource/Runtime/" + sid), RUN_IF).SetObjectPositions(payload)
            n = 1
        except Exception: pass
    SOUND_CENTER[0] = [cx, cy]
    return n

# ---------- cellbroker JSON-RPC (wss://127.0.0.1:8765): the same local API the Syng app uses ----------
# Methods live in syng/cellbroker/ProxyManager.py (dict params, e.g. source_set_stem_positions,
# room_set_listener_position, room_calibrate) or the legacy Router (positional params).
import ssl as _ssl, asyncio as _asyncio
try:
    import websockets as _websockets
except Exception:
    _websockets = None

class CellRPC:
    def __init__(self, url="wss://127.0.0.1:8765"):
        self.url = url; self.loop = _asyncio.new_event_loop(); self.ws = None; self.lock = None; self.n = 0
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
    async def _ensure(self):
        if self.ws is None or getattr(self.ws, "closed", False):
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
            self.ws = await _asyncio.wait_for(_websockets.connect(self.url, ssl=ctx, ping_interval=None, close_timeout=1), 5)
    async def _call(self, method, params):
        if self.lock is None: self.lock = _asyncio.Lock()
        async with self.lock:
            for attempt in range(2):
                try:
                    await self._ensure(); self.n += 1
                    await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": str(self.n), "method": method, "params": params}))
                    r = json.loads(await _asyncio.wait_for(self.ws.recv(), 8))
                    if "error" in r: raise RuntimeError(str(r["error"]))
                    return r.get("result")
                except RuntimeError:
                    raise
                except Exception:
                    try: await self.ws.close()
                    except Exception: pass
                    self.ws = None
                    if attempt: raise
    def call(self, method, params, timeout=10):
        return _asyncio.run_coroutine_threadsafe(self._call(method, params), self.loop).result(timeout)

CELL = CellRPC() if _websockets else None

# ---------- one-puck sound field through the cell's own panner (panner_v2 handles -> shaders.py) ----------
FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "snap": "", "version": "0"}
FIELD_DIRTY = [False]

def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, snap=None, **legacy):
    """Set the stereo upmix 'handle' exactly like the Syng app does: x/y in room-circle units
    (reference circle = 1, y up), rotation (deg, CCW, 90 = the top of the room view), surround_width
    (deg, the dot half-angle) and radius (0 = classic single-puck stereo; 0.7 = spatial default).
    The cell's cellbroker runs the same spatialisation shader the app's cloud path uses."""
    if ux is not None: FIELD["ux"] = max(-1.0, min(1.0, float(ux)))
    if uy is not None: FIELD["uy"] = max(-1.0, min(1.0, float(uy)))
    if rot is not None: FIELD["rot"] = float(rot) % 360.0
    if width is not None: FIELD["width"] = max(0.0, min(180.0, float(width)))
    if spatial is not None: FIELD["spatial"] = bool(spatial)
    if snap is not None: FIELD["snap"] = str(snap or "")
    if radius is not None: FIELD["radius"] = max(0.0, min(1.0, float(radius)))
    elif spatial is not None: FIELD["radius"] = 0.7 if FIELD["spatial"] else 0.0
    FIELD["version"] = str(uuid.uuid4()).upper()
    try: FIELD_DIRTY[0] = True
    except Exception: pass
    handles = {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),
                     "rotation": round(FIELD["rot"], 3), "radius": round(FIELD["radius"] if FIELD["spatial"] else 0.0, 3),
                     "surround_width": round(FIELD["width"], 3), "snap_to_cell": FIELD.get("snap") or "",
                     "snapped_rotation": round(FIELD["rot"], 3) if FIELD.get("snap") else 0.0,
                     "version": FIELD["version"]}}
    n = 0
    if CELL is None: return 0
    for sid in source_ids():
        if not is_music(sid): continue
        try:
            if not get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"): continue
            CELL.call("source_set_stem_positions", {"source_id": sid, "handles": handles, "skip_persist": True}); n += 1
        except Exception as e:
            log("stem positions %s: %s" % (sid, e))
    return n

def calibration():
    out = {}
    try:
        with DBUS_LOCK:
            C = dbus.Interface(BUS.get_object("com.syng.DasCoordinator", "/com/syng/DasCoordinator"),
                               "com.syng.DasCoordinator.Client")
            out["ready"] = bool(C.GetIsReadyToCalibrate())
            out["noiseRMS"] = round(float(C.GetRoomNoiseRMS()), 1)
            out["noiseLongTerm"] = round(float(C.GetLongTermRoomNoiseRMS()), 1)
    except Exception: pass
    return out

# ---------- reads ----------
def live_sources():
    out = {}
    for sid in source_ids():
        dest, path, rt = source_target(sid)
        if not dest or not path:
            out[sid] = {"active": bool(rt.get("active")), "music": is_music(sid)}; continue
        base = get_all(dest, path, ASRC_IF)
        meta = get_all(dest, path, "com.syng.AudioSource.Metadata")
        tc = get_all(dest, path, "com.syng.AudioSource.TransportControl")
        tone = None
        try: tone = json.loads(base.get("toneControl", "")) if base.get("toneControl") else None
        except Exception: tone = None
        art = get_all(dest, path, "com.syng.AudioSource.Artwork")
        out[sid] = {"active": bool(rt.get("active")), "volume": base.get("volume"),
                    "tone": tone, "title": meta.get("title"), "artist": meta.get("artist"), "album": meta.get("album"),
                    "duration": meta.get("duration"), "position": tc.get("position"),
                    "playing": tc.get("playing"), "shuffling": tc.get("shuffling"), "repeatMode": tc.get("repeatMode"),
                    "controls": [str(x) for x in (tc.get("supportedControls") or [])], "music": is_music(sid), "displayName": base.get("displayName"),
                    "artwork": bool(art.get("artworkUrl")), "mixType": base.get("mixType"), "mediaType": base.get("mediaType"),
                    "badging": base.get("badgingType"), "layout": base.get("channelLayout"), "state": base.get("state"),
                    "mute": base.get("mute"), "height": base.get("enableHeightProcessing"),
                    "beyond": base.get("enableBeyondStereoDuplication")}
    return out

def cell_summary(c, me):
    def act(cc):
        for s in cc.get("sources", []) or []:
            if s.get("active"): return s.get("displayName") or s.get("id")
        return None
    return {"serial": c.get("serial_number"), "role": c.get("role"), "ip": c.get("ip_address"),
            "online": str(c.get("online")).lower() == "true", "calibrated": c.get("calibrated"),
            "model": c.get("model"), "position": c.get("position") or {},
            "activeSource": act(c), "isMe": c.get("serial_number") == me}

def get_state():
    s = call("com.syng.cloudsyncd", "/com/syng/StateFetcher", "com.syng.StateFetcher", "GetState")
    if not s: return {}
    try: d = json.loads(s)
    except Exception: return {}
    cs = d.get("cell_state", {}) or {}; me = cs.get("serial_number")
    rs = d.get("room_state", {}) or {}
    return {"room": {"name": rs.get("room_name"), "tone": rs.get("tone_control") or {},
                     "listener": rs.get("listener_position") or {}, "reference": rs.get("reference_circle") or {}},
            "fleet": [cell_summary(cs, me)] + [cell_summary(n, me) for n in (d.get("room_neighbours") or [])]}

# ---------- lightweight system utilization (/proc, cheap) ----------
_cpu_prev = [0, 0]
def cpu_percent():
    try:
        with open("/proc/stat") as f:
            v = list(map(int, f.readline().split()[1:]))
        idle = v[3] + (v[4] if len(v) > 4 else 0); total = sum(v)
        d, di = total - _cpu_prev[0], idle - _cpu_prev[1]
        _cpu_prev[0], _cpu_prev[1] = total, idle
        return round(100 * (1 - di / d), 1) if d > 0 else None
    except Exception:
        return None

def mem_info():
    try:
        d = {}
        for ln in open("/proc/meminfo"):
            k, _, rest = ln.partition(":"); d[k] = int(rest.split()[0])
        tot = d.get("MemTotal", 0); av = d.get("MemAvailable", d.get("MemFree", 0))
        return {"usedPct": round(100 * (tot - av) / tot, 1) if tot else None,
                "usedMB": round((tot - av) / 1024), "totalMB": round(tot / 1024)}
    except Exception:
        return {}

def disk_pct(path):
    try:
        s = os.statvfs(path); tot = s.f_blocks * s.f_frsize; free = s.f_bavail * s.f_frsize
        return round(100 * (tot - free) / tot, 1) if tot else None
    except Exception:
        return None

def sys_stats():
    la = None
    try: la = round(os.getloadavg()[0], 2)
    except Exception: pass
    return {"cpuPct": cpu_percent(), "mem": mem_info(), "load": la,
            "diskData": disk_pct("/data"), "diskRoot": disk_pct("/"), "uptime": uptime_s()}

def uptime_s():
    try:
        with open("/proc/uptime") as f: return int(float(f.readline().split()[0]))
    except Exception: return None

def collect():
    core = get_all("com.syng.CellServer", "/com/syng/localcell", "com.syng.Cell.CoreServices")
    sensors = {
        "temps": call("com.syng.Sensor", "/com/syng/sensormanager", "com.syng.Sensor", "GetTemperatureReadings") or {},
        "faults": call("com.syng.Sensor", "/com/syng/sensormanager", "com.syng.Sensor", "GetSpkFaultCount") or {},
        "accel": call("com.syng.Sensor", "/com/syng/sensormanager", "com.syng.Sensor", "GetAccelerationVectors") or {},
        "mmc": call("com.syng.Sensor", "/com/syng/sensormanager", "com.syng.Sensor", "GetMMCLifetime") or {},
    }
    mqtt = get_all("com.syng.mqttd", "/com/syng/mqtt", "com.syng.mqtt")
    out = {"core": core, "sensors": sensors, "cloudOnline": bool(mqtt.get("online", False)),
           "sources": live_sources(), "system": sys_stats(), "calibration": calibration(),
           "sleep": dict(SLEEP, now=time.time()), "settings": dict(SETTINGS),
           "bass": get_all(ASRV, "/com/syng/AudioSource/BassDistribution",
                           "com.syng.AudioSource.BassDistribution")}
    out.update(get_state())
    # prefer the live listener offset over the cloud state-doc value, so the
    # draggable listener reflects what we just set
    live = listener_live()
    if live and isinstance(out.get("room"), dict):
        out["room"]["listener"] = live
    if isinstance(out.get("room"), dict):
        # one-puck soundfield: the sound image sits at (aim, distance) from the listener
        lp = live or {"x_m": 2.0137, "y_m": 1.2239}
        a = math.radians(SOUND_AIM[0]); d = SOUND_DIST[0]
        out["room"]["field"] = dict(FIELD)
        out["room"]["sound"] = {"x_m": round(lp["x_m"] + d * math.sin(a), 3),
                                "y_m": round(lp["y_m"] - d * math.cos(a), 3),
                                "focus": SOUND_FOCUS[0] if SOUND_FOCUS[0] is not None else 0.8,
                                "rot": OBJ_ROT[0]}
    return out

STAT_PROVIDERS = [
    ("Network", "com.syng.CellStats", "/com/syng/CellStatsProvider", "com.syng.CellStatsProvider", "GetNetworkStats"),
    ("SyFi", "com.syng.CellStats", "/com/syng/CellStatsProvider", "com.syng.CellStatsProvider", "GetSyFiStats"),
    ("AirPlay", "com.syng.AirplayAnalytics", "/com/syng/AirplayAnalytics/Provider", "com.syng.AirplayAnalyticsProvider", "GetAirplayAnalytics"),
    ("AC power", "com.syng.ACPowerAnalytics", "/com/syng/ACPowerAnalytics/Provider", "com.syng.ACPowerAnalyticsProvider", "GetACPowerAnalytics"),
    ("SourceCast RX", "com.syng.SourceCast.Analytics.Network.Receiver", "/com/syng/SourceCast/Analytics/Network/Receiver", "com.syng.SourceCast.NetworkAnalyticsProvider", "GetNetworkAnalytics"),
    ("SourceCast SyFi", "com.syng.SourceCast.Analytics.SyFi", "/com/syng/SourceCast/Analytics/SyFi", "com.syng.SourceCast.SyFiAnalyticsProvider", "GetSyFiAnalytics"),
]
def stats():
    out = {}
    for name, svc, path, iface, meth in STAT_PROVIDERS:
        s = call(svc, path, iface, meth)
        if s:
            try: out[name] = json.loads(s)
            except Exception: out[name] = {"raw": s[:400]}
    return out

# ---------- LED (com.syng.Led D-Bus) + reactive service + vision files ----------
LED_SVC = "com.syng.Led"; LED_OBJ = "/com/syng/ledmanager"; LED_IF = "com.syng.Led"
VIS = "/usr/lib/syng/visions"
REACTIVE_MODES = ["pulse", "party", "flow", "spectrum", "vu", "beat"]

def bus_has(name):
    try:
        with DBUS_LOCK:
            d = dbus.Interface(BUS.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"), "org.freedesktop.DBus")
            names = [str(x) for x in d.ListNames()]
        return name in names
    except Exception:
        return False

def sysctl(*a):
    try:
        return subprocess.run(["systemctl", *a], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=12).stdout.decode().strip()
    except Exception:
        return ""

def visions():
    try: return sorted(f[:-5] for f in os.listdir(VIS) if f.endswith(".json"))
    except Exception: return []

def led_status():
    reactive = sysctl("is-active", "syng-led-reactive") == "active"
    mode = None; palette = "free"; source = None; tune = {}
    try:
        for ln in open("/etc/default/syng-led-reactive"):
            if ln.startswith("ARGS="):
                args = shlex.split(ln.split("=", 1)[1])
                def av(flag):
                    return args[args.index(flag) + 1] if flag in args else None
                mode = av("--mode") or mode
                palette = av("--palette") or palette
                source = av("--source") or source
                tune = {"beatSens": av("--beat-sens"), "floor": av("--floor"), "cycle": av("--cycle"),
                        "silentFade": av("--silent-fade"), "beatFade": av("--beat-fade")}
    except Exception: pass
    return {"reactive": reactive, "mode": mode, "palette": palette, "source": source, "tune": tune,
            "ledmanager": bus_has(LED_SVC), "visions": visions(), "reactiveModes": REACTIVE_MODES}

def set_led_flags(pairs):
    """Add/replace each `flag value` in /etc/default/syng-led-reactive ARGS, restart once."""
    path = "/etc/default/syng-led-reactive"
    try:
        lines, found = [], False
        for ln in open(path):
            if ln.startswith("ARGS="):
                args = shlex.split(ln.split("=", 1)[1])
                for flag, value in pairs:
                    if flag in args: args[args.index(flag) + 1] = value
                    else: args += [flag, value]
                lines.append("ARGS=" + " ".join(args) + "\n"); found = True
            else: lines.append(ln)
        if not found:
            base = "--mode flow --source viz " + " ".join("%s %s" % (f, v) for f, v in pairs)
            lines.append("ARGS=" + base + "\n")
        open(path, "w").writelines(lines)
    except Exception:
        base = "--mode flow --source viz " + " ".join("%s %s" % (f, v) for f, v in pairs)
        open(path, "w").write("ARGS=" + base + "\n")
    sysctl("restart", "syng-led-reactive")

def set_led_flag(flag, value):
    set_led_flags([(flag, value)])

def led_dbus(method, *args):
    with DBUS_LOCK:
        getattr(dbus.Interface(BUS.get_object(LED_SVC, LED_OBJ), LED_IF), method)(*args)

def ensure_manual():
    """Hand the LED to ledmanager (stop reactive) so D-Bus color/animation works."""
    if sysctl("is-active", "syng-led-reactive") == "active":
        sysctl("stop", "syng-led-reactive")
    if not bus_has(LED_SVC):
        sysctl("start", "ledmanager")
        for _ in range(10):
            if bus_has(LED_SVC): break
            time.sleep(0.3)

def led_color(r, g, b, w):
    ensure_manual()
    arr = dbus.Array([dbus.Double(max(0.0, min(1.0, float(x)))) for x in (r, g, b, w)], signature="d")
    led_dbus("SetColor", dbus.Int32(5), arr)

def led_anim(action, name=None):
    ensure_manual()
    if action == "play": led_dbus("PlayAnimation", dbus.String(VIS + "/" + name + ".json"))
    elif action == "pause": led_dbus("PauseAnimation")
    elif action == "resume": led_dbus("ResumeAnimation")
    elif action == "clear": led_dbus("ClearAnimationQueue")

def set_reactive(mode):
    if mode in (None, "", "off", "manual"):
        sysctl("stop", "syng-led-reactive"); sysctl("start", "ledmanager"); return
    try:
        lines, found = [], False
        for ln in open("/etc/default/syng-led-reactive"):
            if ln.startswith("ARGS="):
                args = shlex.split(ln.split("=", 1)[1])
                if "--mode" in args: args[args.index("--mode") + 1] = mode
                else: args = ["--mode", mode] + args
                lines.append("ARGS=" + " ".join(args) + "\n"); found = True
            else: lines.append(ln)
        if not found: lines.append("ARGS=--mode %s\n" % mode)
        open("/etc/default/syng-led-reactive", "w").writelines(lines)
    except Exception:
        open("/etc/default/syng-led-reactive", "w").write("ARGS=--mode %s --source viz\n" % mode)
    sysctl("restart", "syng-led-reactive")

def vision_read(name):
    with open(os.path.join(VIS, os.path.basename(name) + ".json")) as f:
        return f.read()

def vision_write(name, content):
    json.loads(content)  # validate JSON before writing
    with open(os.path.join(VIS, os.path.basename(name) + ".json"), "w") as f:
        f.write(content)

# ====================================================================================
# b26 — local cloud: the agent replaces Syng's cloud for room membership, roles,
# layout, reference circle / listener, calibration results, plus a USB setup link
# and Wi-Fi provisioning. Everything is driven through cloudsyncd.PatchState, which
# emits PropertiesChanged -> celldirector / cellbroker / syngsoundserver react exactly
# as if the state came from api.prod.syng.space.
# ====================================================================================
import socket, struct, random, ipaddress, math, re, threading, subprocess, urllib.request

AGENT_BUILD = "b31"
SSH_PUB = "/data/home/root/.ssh/id_ed25519.pub"
HOST_PUB = "/data/ssh/ssh_host_ed25519_key.pub"
CLOUD_STATE_FILE = "/data/syng/cloud_state.json"
CD_STATE_FILE = "/data/syng/celldirector_state.json"
LC_DIR = "/data/syng-agent"
LC_BACKUP = LC_DIR + "/state-before-agent.json"
LC_LAST_APPLY = LC_DIR + "/last-apply.json"
LC_CAL_LOG = LC_DIR + "/calibrations.jsonl"
CLOUD_BLOCK_MARK = "/data/syng-cloud-blocked"
CELL_PLACEMENT = 0.85          # the cells sit at 85 % of the reference circle (fitted from a real room)
USB_NETS = {"usb0": "10.86.0.1", "usb1": "10.86.1.1"}   # setup link: plug the cell straight into a laptop
LC_LOCK = threading.RLock()

def _sh(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout).stdout.decode(errors="replace")
    except Exception as e:
        return "ERR %s" % e

def _read(path, default=""):
    try: return open(path).read()
    except Exception: return default

def _ensure_dir():
    try: os.makedirs(LC_DIR, exist_ok=True)
    except Exception: pass

def raw_state():
    """The cloudsyncd state document (cell_state / room_state / room_neighbours)."""
    s = call("com.syng.cloudsyncd", "/com/syng/StateFetcher", "com.syng.StateFetcher", "GetState")
    try: return json.loads(s) if s else {}
    except Exception: return {}

def patch_state_strict(patch):
    with DBUS_LOCK:
        dbus.Interface(BUS.get_object("com.syng.cloudsyncd", "/com/syng/StateFetcher"),
                       "com.syng.StateFetcher").PatchState(json.dumps(patch))
    return True

def backup_state_once():
    _ensure_dir()
    if not os.path.exists(LC_BACKUP):
        st = raw_state()
        if st:
            try: open(LC_BACKUP, "w").write(json.dumps(st, indent=1))
            except Exception: pass

def http_json(method, url, body=None, timeout=3.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")

def agent_get(ip, path, timeout=3.0):
    return http_json("GET", "http://%s:%d%s" % (ip, PORT, path), None, timeout)

def agent_post(ip, path, body, timeout=6.0):
    return http_json("POST", "http://%s:%d%s" % (ip, PORT, path), body, timeout)

# ---------- identity ----------
def iface_addrs():
    """[(ifname, ip, prefixlen)] for IPv4 addresses, loopback excluded."""
    out = []
    for line in _sh("ip -4 -o addr").splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if m and m.group(1) != "lo":
            out.append((m.group(1), m.group(2), int(m.group(3))))
    return out

def lan_ip():
    """The address other cells reach us at: the infra interface first, then anything that is not usb/SyFi."""
    addrs = iface_addrs()
    for pref in ("winfra0", "eth", "en", ""):
        for ifn, ip, _ in addrs:
            if ifn.startswith(pref) and not ifn.startswith(("usb", "wsyng")):
                return ip
    return addrs[0][1] if addrs else None

def local_cell_props():
    try:
        return get_all("com.syng.CellServer", "/com/syng/localcell", "com.syng.Cell.CoreServices")
    except Exception:
        return {}

def whoami():
    st = raw_state(); cs = st.get("cell_state") or {}; rs = st.get("room_state") or {}
    lp = local_cell_props()
    ssh_key = _read(SSH_PUB).strip() or cs.get("ssh_key")
    hk = _read(HOST_PUB).strip()
    ip = lan_ip() or cs.get("ip_address")
    ssh_host = ("%s %s" % (ip, hk)) if (hk and ip) else cs.get("ssh_host")
    return {"serial": cs.get("serial_number") or (str(lp.get("uniqueID")) if lp.get("uniqueID") else None) or socket.gethostname().replace("syng", "", 1),
            "ip": ip, "ips": iface_addrs(), "hostname": socket.gethostname(),
            "model": cs.get("model") or "Cell Alpha", "version": cs.get("syng_os_version"),
            "ssh_key": ssh_key, "ssh_host": ssh_host,
            "room_id": cs.get("room_id") or (str(lp.get("roomID")) if lp.get("roomID") else None),
            "place_id": cs.get("place_id"), "room_name": rs.get("room_name") or (str(lp.get("roomName")) if lp.get("roomName") else None),
            "role": (str(lp.get("role")) if lp.get("role") else None) or cs.get("role"),
            "doc_role": cs.get("role"), "role_pinned": cs.get("role_pinned"),
            "calibrated": cs.get("calibrated"), "position": cs.get("position") or {},
            "is_upside_down": cs.get("is_upside_down"), "height_layer": cs.get("height_layer"),
            "nearby_walls": cs.get("nearby_walls") or [],
            "syfiGood": bool(lp.get("syfiGood", False)), "timeSyncGood": bool(lp.get("timeSyncGood", False)),
            "members": [cs.get("serial_number")] + [n.get("serial_number") for n in (st.get("room_neighbours") or [])],
            "cloud_blocked": os.path.exists(CLOUD_BLOCK_MARK), "agent": AGENT_BUILD, "rssi": wifi_rssi(),
            "setup_phase": (json.loads(_read(CD_STATE_FILE, "{}") or "{}").get("phase")),
            "wifi": wifi_status(brief=True)}

# ---------- discovery ----------
def lan_subnets():
    nets = []
    for ifn, ip, plen in iface_addrs():
        if ifn.startswith(("usb", "wsyng")): continue
        try:
            n = ipaddress.ip_network("%s/%d" % (ip, plen), strict=False)
            if n.num_addresses <= 1024 and n not in nets: nets.append(n)
        except Exception: pass
    return nets

def mdns_syng(timeout=1.5):
    """Ask the LAN for _syng._tcp (what celldirector advertises); returns {ip: {name, port, txt}}."""
    found = {}
    def qname(name):
        return b"".join(struct.pack("B", len(p)) + p.encode() for p in name.split(".")) + b"\0"
    def rdname(buf, off):
        parts = []; jumped = False; end = off
        while True:
            l = buf[off]
            if l == 0:
                off += 1; break
            if l & 0xC0 == 0xC0:
                ptr = struct.unpack("!H", buf[off:off+2])[0] & 0x3FFF
                if not jumped: end = off + 2
                jumped = True; off = ptr; continue
            parts.append(buf[off+1:off+1+l].decode(errors="replace")); off += 1 + l
        if not jumped: end = off
        return ".".join(parts), end
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception: pass
        try:
            s.bind(("", 5353))
            mreq = struct.pack("4sl", socket.inet_aton("224.0.0.251"), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            unicast = False
        except Exception:
            s.close(); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("", 0)); unicast = True
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        q = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + qname("_syng._tcp.local") + struct.pack("!HH", 12, (0x8000 if unicast else 0) | 1)
        s.sendto(q, ("224.0.0.251", 5353))
        s.settimeout(0.25); t0 = time.time()
        while time.time() - t0 < timeout:
            try: buf, addr = s.recvfrom(9000)
            except socket.timeout: continue
            except Exception: break
            try:
                qd, an, ns, ar = struct.unpack("!HHHH", buf[4:12]); off = 12
                for _ in range(qd):
                    _, off = rdname(buf, off); off += 4
                recs = []
                for _ in range(an + ns + ar):
                    nm, off = rdname(buf, off)
                    typ, cls, ttl, rl = struct.unpack("!HHIH", buf[off:off+10]); off += 10
                    recs.append((nm, typ, buf[off:off+rl], off)); off += rl
                inst = None; port = None; txt = {}; host = None; a = None
                for nm, typ, rd, roff in recs:
                    if typ == 12 and nm.startswith("_syng._tcp"): inst, _ = rdname(buf, roff)
                    elif typ == 33: port = struct.unpack("!H", rd[4:6])[0]; host, _ = rdname(buf, roff + 6)
                    elif typ == 16:
                        i = 0
                        while i < len(rd):
                            l = rd[i]; kv = rd[i+1:i+1+l].decode(errors="replace"); i += 1 + l
                            if "=" in kv: k, v = kv.split("=", 1); txt[k] = v
                    elif typ == 1 and len(rd) == 4: a = socket.inet_ntoa(rd)
                if inst and "_syng._tcp" in inst:      # only answers to OUR question (the LAN is full of AirPlay chatter)
                    ip = a or addr[0]
                    found[ip] = {"name": inst.replace("._syng._tcp.local", ""), "port": port, "txt": txt, "host": host}
            except Exception:
                continue
        s.close()
    except Exception as e:
        log("mdns error %s" % e)
    return found

def discover(timeout=1.2):
    """Cells on the LAN: a parallel sweep of every local /24 for the agent port, plus mDNS for stock cells."""
    me = whoami(); myip = me.get("ip")
    hosts = []
    for n in lan_subnets():
        hosts += [str(h) for h in n.hosts()]
    results = {}; lock = threading.Lock()
    def probe(ip):
        try:
            s = socket.create_connection((ip, PORT), timeout=0.35); s.close()
        except Exception:
            return
        try:
            w = agent_get(ip, "/api/whoami", timeout=timeout + 1.5)
            with lock: results[ip] = w
        except Exception:
            with lock: results[ip] = {"ip": ip, "agent": None}
    threads = []
    for ip in hosts:
        if ip == myip: continue
        t = threading.Thread(target=probe, args=(ip,), daemon=True); t.start(); threads.append(t)
        while sum(1 for t in threads if t.is_alive()) > 96: time.sleep(0.01)
    for t in threads: t.join(timeout + 3)
    results[myip] = dict(me, isMe=True)
    md = mdns_syng()
    for ip, m in md.items():
        txt = m.get("txt", {}); serial = (m.get("name") or "").replace("syng", "", 1) or None
        if ip not in results or not results[ip].get("agent"):
            results[ip] = {"ip": ip, "agent": results.get(ip, {}).get("agent"), "serial": serial, "room_id": txt.get("roomID"),
                           "place_id": txt.get("homeID"), "role": txt.get("role"), "mdns": m, "stock": not results.get(ip, {}).get("agent")}
        else:
            results[ip]["mdns"] = m
    # a host with port 8080 open that is neither an agent nor a cell on mDNS is some other device
    for ip in list(results):
        if not results[ip].get("agent") and ip not in md and not results[ip].get("isMe"): results.pop(ip)
    rooms = {}
    for ip, c in results.items():
        rid = c.get("room_id") or "?"
        r = rooms.setdefault(rid, {"room_id": rid, "room_name": c.get("room_name"), "cells": [], "primary_ip": None})
        if not r["room_name"] and c.get("room_name"): r["room_name"] = c["room_name"]
        r["cells"].append(c)
        if (c.get("role") or "").lower() == "primary": r["primary_ip"] = ip
    return {"me": me, "cells": sorted(results.values(), key=lambda c: (c.get("room_name") or "~", c.get("ip") or "")),
            "rooms": sorted(rooms.values(), key=lambda r: r["room_name"] or "~")}

# ---------- the room document ----------
def room_doc():
    """Our view of the room, derived from the live state: {room_state, cells{serial: cell_state}}."""
    st = raw_state(); cs = st.get("cell_state") or {}
    cells = {}
    if cs.get("serial_number"): cells[cs["serial_number"]] = cs
    for n in st.get("room_neighbours") or []:
        if n.get("serial_number"): cells[n["serial_number"]] = n
    return {"room_state": st.get("room_state") or {}, "cells": cells, "me": cs.get("serial_number")}

OWN_KEYS = ("serial_number", "room_id", "place_id", "role", "role_pinned", "calibrated", "position", "nearby_walls",
            "is_upside_down", "height_layer", "online", "ip_address", "version")

def member_view(doc, serial):
    """What a member gets: only the room-owned keys of its own cell_state (its sources, Wi-Fi, OTA… stay its
       business), the room_state, and full entries for every other cell."""
    others = [c for s, c in doc["cells"].items() if s != serial]
    own = {k: v for k, v in doc["cells"][serial].items() if k in OWN_KEYS}
    return {"cell_state": own, "room_state": doc["room_state"], "room_neighbours": others}

def apply_view(view):
    """Apply a member view locally (PatchState) and remember it."""
    _ensure_dir(); backup_state_once()
    patch = {"cell_state": view.get("cell_state") or {}, "room_state": view.get("room_state") or {},
             "room_neighbours": view.get("room_neighbours") or []}
    ok = patch_state_strict(patch)
    try: open(LC_LAST_APPLY, "w").write(json.dumps(patch, indent=1))
    except Exception: pass
    return ok

def fan_out(doc, only=None):
    """Push every member's view to its agent (ourselves included). Returns {serial: ok|error}."""
    res = {}
    for serial, c in doc["cells"].items():
        if only and serial not in only: continue
        view = member_view(doc, serial)
        if serial == doc.get("me"):
            try: res[serial] = apply_view(view)
            except Exception as e: res[serial] = "local: %s" % e
            continue
        ip = c.get("ip_address")
        try:
            r = agent_post(ip, "/api/room/apply", dict(view, _from=doc.get("me")), timeout=8)
            res[serial] = bool(r.get("ok"))
        except Exception as e:
            res[serial] = "%s: %s" % (ip, e)
    log("ROOM fan-out %s" % res)
    return res

def new_cell_entry(w, room_state, role="secondary", template=None):
    """A cell_state for a cell described by its whoami, inside this room."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    base = dict(template or {})
    base.update({
        "serial_number": w["serial"], "ip_address": w.get("ip"), "wired_ip": None, "wifi_ip": None,
        "model": w.get("model") or "Cell Alpha", "syng_os_version": w.get("version"),
        "ssh_key": w.get("ssh_key"), "ssh_host": w.get("ssh_host"),
        "place_id": room_state.get("place_id"), "room_id": room_state.get("room_id"),
        "role": role, "role_pinned": False, "online": "true", "last_seen_at": now,
        "calibrated": False, "is_upside_down": bool(w.get("is_upside_down")) if w.get("is_upside_down") is not None else False,
        "height_layer": float(w.get("height_layer") or 0.0),
        "position": {"x": 0.0, "y": 0.0, "z": 1.5, "rotation": 0.0}, "nearby_walls": [],
        "version": str(uuid.uuid4()),
    })
    if "sources" not in base: base["sources"] = []
    if "cell_data" not in base: base["cell_data"] = {}
    return base

def fresh_room_state(name, place_id=None):
    return {"place_id": place_id or str(uuid.uuid4()), "room_id": str(uuid.uuid4()), "room_name": name,
            "version": str(uuid.uuid4()), "syfi_password": None, "syfi_ssid": None,
            "listener_position": {"x_m": 0.0, "y_m": 0.0, "version": "0"},
            "reference_circle": {"x_m": 0.0, "y_m": 0.0, "radius_m": 1.0, "rotation_deg": 0.0, "version": "0"},
            "tone_control": {"bass": 0.0, "tilt": 0.0, "version": str(uuid.uuid4()).upper(), "mapping_version": "1.0.0"}}

def bump(d, key="version"):
    d[key] = str(uuid.uuid4()); return d

# geometry: smallest enclosing circle (Welzl, tiny n) -> the listener default and the reference circle
def _dist(a, b): return math.hypot(a[0]-b[0], a[1]-b[1])

def enclosing_circle(pts):
    pts = [tuple(p) for p in pts]
    if not pts: return (0.0, 0.0, 1.0)
    if len(pts) == 1: return (pts[0][0], pts[0][1], 1.0)
    def circ2(a, b):
        return ((a[0]+b[0])/2, (a[1]+b[1])/2, _dist(a, b)/2)
    def circ3(a, b, c):
        ax, ay = a; bx, by = b; cx, cy = c
        d = 2*(ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
        if abs(d) < 1e-12: return None
        ux = ((ax*ax+ay*ay)*(by-cy) + (bx*bx+by*by)*(cy-ay) + (cx*cx+cy*cy)*(ay-by))/d
        uy = ((ax*ax+ay*ay)*(cx-bx) + (bx*bx+by*by)*(ax-cx) + (cx*cx+cy*cy)*(bx-ax))/d
        return (ux, uy, _dist((ux, uy), a))
    def inside(c, p): return _dist((c[0], c[1]), p) <= c[2] + 1e-9
    best = None
    import itertools
    for a, b in itertools.combinations(pts, 2):
        c = circ2(a, b)
        if all(inside(c, p) for p in pts) and (best is None or c[2] < best[2]): best = c
    for a, b, c3 in itertools.combinations(pts, 3):
        c = circ3(a, b, c3)
        if c and all(inside(c, p) for p in pts) and (best is None or c[2] < best[2]): best = c
    return best or (pts[0][0], pts[0][1], 1.0)

def auto_circle(doc, keep_rotation=True, set_listener=True):
    pts = [(c["position"]["x"], c["position"]["y"]) for c in doc["cells"].values()
           if c.get("position") and "x" in c["position"]]
    cx, cy, r = enclosing_circle(pts)
    rs = doc["room_state"]
    rc = dict(rs.get("reference_circle") or {})
    rc.update({"x_m": cx, "y_m": cy, "radius_m": max(0.5, r / CELL_PLACEMENT),
               "rotation_deg": float(rc.get("rotation_deg") or 0.0) if keep_rotation else 0.0})
    rs["reference_circle"] = bump(rc)
    if set_listener:
        rs["listener_position"] = bump({"x_m": cx, "y_m": cy})
    bump(rs)
    return rs

# ---------- operations ----------
def require_primary():
    w = whoami()
    if (w.get("role") or "").lower() == "secondary":
        raise RuntimeError("this cell is a secondary; talk to the primary (%s)" % primary_ip_of_room())
    return w

def primary_ip_of_room():
    doc = room_doc()
    for c in doc["cells"].values():
        if (c.get("role") or "").lower() == "primary": return c.get("ip_address")
    return None

def op_create_room(name, place_id=None, keep_sources=True):
    """This cell becomes the primary of a brand-new room (leaving its old one if it had neighbours)."""
    with LC_LOCK:
        backup_state_once()
        st = raw_state(); cs = dict(st.get("cell_state") or {})
        old_neigh = st.get("room_neighbours") or []
        w = whoami()
        rs = fresh_room_state(name or ("Room %s" % (w["serial"] or "")[-4:]), place_id or cs.get("place_id"))
        if old_neigh:
            try: op_leave_notify(cs, old_neigh)
            except Exception as e: log("leave notify failed: %s" % e)
        entry = new_cell_entry(w, rs, role="primary", template=cs)
        entry["position"] = {"x": 0.0, "y": 0.0, "z": 1.5, "rotation": 0.0}
        ok = apply_view({"cell_state": entry, "room_state": rs, "room_neighbours": []})
        set_placed()
        log("ROOM create '%s' %s -> %s" % (rs["room_name"], rs["room_id"], ok))
        return {"ok": ok, "room": rs}

def op_leave_notify(cs, neighbours):
    """Tell the old room's primary we're gone (it removes us and fans out)."""
    prim = next((n for n in neighbours if (n.get("role") or "").lower() == "primary"), None)
    if prim and prim.get("ip_address"):
        agent_post(prim["ip_address"], "/api/room/remove", {"serial": cs.get("serial_number"), "_notify": False}, timeout=10)
    else:
        # we were the primary: hand the room to the first remaining neighbour
        if neighbours:
            doc = room_doc(); me = doc["me"]
            doc["cells"].pop(me, None)
            first = sorted(doc["cells"].keys())[0]
            for s, c in doc["cells"].items(): c["role"] = "primary" if s == first else "secondary"
            doc["me"] = None
            fan_out(doc)

def op_admit(w):
    """(primary) Add the cell described by whoami `w` to our room and fan out, the new cell included."""
    with LC_LOCK:
        me = require_primary()
        doc = room_doc(); rs = doc["room_state"]
        if not rs.get("room_id"):
            raise RuntimeError("this cell is not in a room yet; create one first")
        if w["serial"] == me["serial"]: raise RuntimeError("that is me")
        existing = doc["cells"].get(w["serial"])
        entry = new_cell_entry(w, rs, role="secondary", template=existing)
        if existing:   # re-admit keeps its calibration
            entry["calibrated"] = existing.get("calibrated", False); entry["position"] = existing.get("position") or entry["position"]
            entry["nearby_walls"] = existing.get("nearby_walls") or []
        doc["cells"][w["serial"]] = entry
        bump(rs)
        res = fan_out(doc)
        log("ROOM admit %s @ %s -> %s" % (w["serial"], w.get("ip"), res))
        return {"ok": all(v is True for v in res.values()), "fanout": res, "room": rs, "members": sorted(doc["cells"].keys())}

def op_join(target_ip):
    """(this cell) Join the room that the cell at target_ip belongs to."""
    with LC_LOCK:
        backup_state_once()
        t = agent_get(target_ip, "/api/whoami", timeout=4)
        prim_ip = target_ip
        if (t.get("role") or "").lower() == "secondary":
            r = agent_get(target_ip, "/api/room", timeout=4)
            prim_ip = r.get("primary_ip") or target_ip
        st = raw_state(); cs = st.get("cell_state") or {}; old_neigh = st.get("room_neighbours") or []
        if cs.get("room_id") and cs.get("room_id") == t.get("room_id"):
            return {"ok": True, "already": True}
        if old_neigh:
            try: op_leave_notify(cs, old_neigh)
            except Exception as e: log("leave notify failed: %s" % e)
        me = whoami()
        r = agent_post(prim_ip, "/api/room/admit", {"cell": me}, timeout=30)
        set_placed()
        log("ROOM join via %s -> %s" % (prim_ip, r))
        return r

def op_remove(serial, notify=True):
    """(primary) Remove a member; it becomes a room of its own."""
    with LC_LOCK:
        require_primary()
        doc = room_doc()
        c = doc["cells"].pop(serial, None)
        if not c: raise RuntimeError("not a member: %s" % serial)
        bump(doc["room_state"])
        res = fan_out(doc)
        if notify and c.get("ip_address"):
            try: agent_post(c["ip_address"], "/api/room/create", {"name": "Cell %s" % serial[-4:], "_solo": True}, timeout=10)
            except Exception as e: res[serial] = "solo: %s" % e
        return {"ok": True, "fanout": res, "members": sorted(doc["cells"].keys())}

def op_rename(name):
    with LC_LOCK:
        require_primary()
        doc = room_doc(); doc["room_state"]["room_name"] = name; bump(doc["room_state"])
        return {"ok": True, "fanout": fan_out(doc)}

def op_layout(body):
    """(primary) positions / rotations / upside-down / height per cell, reference circle, listener."""
    with LC_LOCK:
        require_primary()
        doc = room_doc(); rs = doc["room_state"]
        for serial, p in (body.get("cells") or {}).items():
            c = doc["cells"].get(serial)
            if not c: continue
            pos = dict(c.get("position") or {"x": 0.0, "y": 0.0, "z": 1.5, "rotation": 0.0})
            for k in ("x", "y", "z", "rotation"):
                if k in p and p[k] is not None: pos[k] = float(p[k])
            c["position"] = pos
            if "is_upside_down" in p: c["is_upside_down"] = bool(p["is_upside_down"])
            if "height_layer" in p: c["height_layer"] = float(p["height_layer"])
            if "calibrated" in p: c["calibrated"] = bool(p["calibrated"])
            c["version"] = str(uuid.uuid4())
        if body.get("reference_circle"):
            rc = dict(rs.get("reference_circle") or {}); rc.update({k: float(v) for k, v in body["reference_circle"].items() if k in ("x_m", "y_m", "radius_m", "rotation_deg")})
            rs["reference_circle"] = bump(rc)
        if body.get("listener_position"):
            lp = dict(rs.get("listener_position") or {}); lp.update({k: float(v) for k, v in body["listener_position"].items() if k in ("x_m", "y_m")})
            rs["listener_position"] = bump(lp)
        if body.get("auto_circle"):
            auto_circle(doc, keep_rotation=True, set_listener=bool(body.get("auto_listener", True)))
        bump(rs)
        res = fan_out(doc)
        return {"ok": True, "fanout": res, "room": rs,
                "cells": {s: {"position": c.get("position"), "is_upside_down": c.get("is_upside_down"), "height_layer": c.get("height_layer")} for s, c in doc["cells"].items()}}

def op_pin(serial, role):
    """Pin primary/secondary through celldirector (what the app's 'make primary' does) and mirror it in the doc."""
    with LC_LOCK:
        doc = room_doc()
        if serial not in doc["cells"]: raise RuntimeError("not a member")
        try:
            with DBUS_LOCK:
                CS = dbus.Interface(BUS.get_object("com.syng.CellServer", "/com/syng/localcell"), "com.syng.Cell.CoreServices")
                if role == "primary": CS.MigratePrimary(serial) if serial != doc["me"] else CS.PinPrimary()
        except Exception as e:
            log("pin via CellServer failed: %s" % e)
        for s, c in doc["cells"].items():
            c["role"] = "primary" if s == serial else "secondary"; c["role_pinned"] = (s == serial)
        bump(doc["room_state"])
        return {"ok": True, "fanout": fan_out(doc)}

def set_placed():
    try:
        d = json.loads(_read(CD_STATE_FILE, "{}") or "{}")
        if d.get("phase") != "placed":
            d["phase"] = "placed"; open(CD_STATE_FILE, "w").write(json.dumps(d, indent=4))
    except Exception as e:
        log("celldirector_state: %s" % e)

def room_view():
    doc = room_doc(); st = raw_state()
    cells = []
    for s, c in doc["cells"].items():
        cells.append({"serial": s, "ip": c.get("ip_address"), "role": c.get("role"), "online": str(c.get("online")).lower() == "true",
                      "calibrated": c.get("calibrated"), "position": c.get("position") or {}, "nearby_walls": c.get("nearby_walls") or [],
                      "is_upside_down": c.get("is_upside_down"), "height_layer": c.get("height_layer"), "model": c.get("model"),
                      "version": c.get("syng_os_version"), "isMe": s == doc["me"], "role_pinned": c.get("role_pinned")})
    rs = doc["room_state"]
    w = whoami(); iam = (w.get("role") or "").lower() != "secondary"
    return {"room_state": rs, "cells": cells, "me": doc["me"], "primary_ip": w.get("ip") if iam else primary_ip_of_room(),
            "i_am_primary": iam, "calibration": CAL_STATUS,
            "cloud_blocked": os.path.exists(CLOUD_BLOCK_MARK)}

# ---------- calibration (Soundcheck) ----------
CAL_STATUS = {"running": False, "started": None, "finished": None, "ok": None, "log": [], "last_results": None}

def cal_log(msg):
    CAL_STATUS["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg)); CAL_STATUS["log"] = CAL_STATUS["log"][-80:]
    log("CAL " + msg)

def cal_run_thread():
    try:
        cal_log("DasCalibrator.Runner.Run() on this cell — chirps on every cell, keep the room quiet")
        bus = dbus.SystemBus()   # own connection: Run blocks for minutes and must not hold DBUS_LOCK
        R = dbus.Interface(bus.get_object("com.syng.DasCalibrator", "/com/syng/DasCalibrator/Runner"), "com.syng.DasCalibrator")
        R.Run(timeout=1200)
        cal_log("Run() returned")
        for _ in range(20):
            if CAL_STATUS.get("last_results") is not None: break
            time.sleep(0.5)
        if CAL_STATUS.get("last_results") is None:
            cal_log("no results were posted back — is pycalibrator/cloudapi.py patched on this cell?")
            CAL_STATUS["ok"] = False
    except Exception as e:
        cal_log("Run() failed: %s" % e); CAL_STATUS["ok"] = False
    finally:
        CAL_STATUS["running"] = False; CAL_STATUS["finished"] = time.time()

def op_calibrate():
    require_primary()
    if CAL_STATUS["running"]: return {"ok": False, "error": "already running"}
    CAL_STATUS.update({"running": True, "started": time.time(), "finished": None, "ok": None, "log": [], "last_results": None})
    threading.Thread(target=cal_run_thread, daemon=True).start()
    return {"ok": True}

def op_calibration_results(results):
    """pycalibrator's PUT /v2/cell/calibration, redirected here: positions + walls -> doc, circle, listener."""
    with LC_LOCK:
        _ensure_dir()
        try: open(LC_CAL_LOG, "a").write(json.dumps({"t": time.time(), "results": results}) + "\n")
        except Exception: pass
        doc = room_doc(); rs = doc["room_state"]
        # The calibrator's layout comes in its own frame. If the room was calibrated before, rigidly align the
        # new layout to the old one (2-D Kabsch on the cells both runs know), so the map, the reference circle
        # and the listener keep their meaning; otherwise anchor the centroid at the origin.
        new = {r["serial_number"]: r["position"] for r in (results or []) if r.get("position") and r.get("serial_number") in doc["cells"]}
        old = {s: c["position"] for s, c in doc["cells"].items() if c.get("calibrated") and c.get("position") and s in new}
        th = 0.0; tx = ty = 0.0; aligned = False
        if len(old) >= 2:
            P = [(new[s]["x"], new[s]["y"]) for s in old]; Q = [(old[s]["x"], old[s]["y"]) for s in old]
            pc = (sum(p[0] for p in P) / len(P), sum(p[1] for p in P) / len(P)); qc = (sum(q[0] for q in Q) / len(Q), sum(q[1] for q in Q) / len(Q))
            h00 = h01 = h10 = h11 = 0.0
            for p, q in zip(P, Q):
                px, py = p[0] - pc[0], p[1] - pc[1]; qx, qy = q[0] - qc[0], q[1] - qc[1]
                h00 += px * qx; h01 += px * qy; h10 += py * qx; h11 += py * qy
            th = math.atan2(h01 - h10, h00 + h11)
            tx = qc[0] - (math.cos(th) * pc[0] - math.sin(th) * pc[1]); ty = qc[1] - (math.sin(th) * pc[0] + math.cos(th) * pc[1])
            aligned = True
        elif new:
            cx = sum(p["x"] for p in new.values()) / len(new); cy = sum(p["y"] for p in new.values()) / len(new)
            tx, ty = -cx, -cy
        n = 0
        for r in results or []:
            c = doc["cells"].get(r.get("serial_number"))
            if not c: continue
            if r.get("position"):
                p = r["position"]; x, y = float(p.get("x", 0)), float(p.get("y", 0))
                xr = math.cos(th) * x - math.sin(th) * y + tx; yr = math.sin(th) * x + math.cos(th) * y + ty
                oldz = (c.get("position") or {}).get("z")
                c["position"] = {"x": round(xr, 4), "y": round(yr, 4), "z": float(oldz if oldz not in (None, 0.0) else p.get("z", 0.0)),
                                 "rotation": round((float(p.get("rotation", 0)) + math.degrees(th) + 180.0) % 360.0 - 180.0, 3)}
            if "nearby_walls" in r: c["nearby_walls"] = r["nearby_walls"] or []
            c["calibrated"] = True; c["version"] = str(uuid.uuid4()); n += 1
        if n:
            if aligned:
                bump(rs)   # the circle and the listener are the user's; "Auto circle" on the setup page recomputes them
            else:
                auto_circle(doc, keep_rotation=True, set_listener=True)
            res = fan_out(doc)
        else:
            res = {}
        cal_log("layout %s (rot %.1f°, shift %.2f,%.2f)" % ("aligned to the previous calibration" if aligned else "anchored at the centroid", math.degrees(th), tx, ty))
        CAL_STATUS["last_results"] = results; CAL_STATUS["ok"] = bool(n)
        cal_log("results for %d cell(s) applied; reference circle r=%.2f m; fan-out %s" % (n, rs.get("reference_circle", {}).get("radius_m", 0), res))
        return {"ok": True, "applied": n, "fanout": res, "room": rs}

# ---------- USB setup link + Wi-Fi provisioning ----------
def usbnet_up():
    """Give the USB-gadget Ethernet ports fixed addresses and a DHCP server, so a laptop on a USB-C
       cable lands on http://10.86.0.1:8080/setup (10.86.1.1 on the other port)."""
    out = {}
    for ifn, ip in USB_NETS.items():
        if not os.path.exists("/sys/class/net/%s" % ifn): continue
        _sh("ip link set %s up" % ifn); _sh("ip addr replace %s/24 dev %s" % (ip, ifn))
        net = ip.rsplit(".", 1)[0]
        conf = "/tmp/udhcpd-%s.conf" % ifn
        try:
            open(conf, "w").write("start %s.10\nend %s.60\ninterface %s\nlease_file /tmp/udhcpd-%s.leases\npidfile /tmp/udhcpd-%s.pid\n"
                                  "option subnet 255.255.255.0\noption router %s\noption lease 3600\n" % (net, net, ifn, ifn, ifn, ip))
            open("/tmp/udhcpd-%s.leases" % ifn, "a").close()
            pid = _read("/tmp/udhcpd-%s.pid" % ifn).strip()
            if not (pid and os.path.exists("/proc/%s" % pid)):
                subprocess.Popen(["udhcpd", "-S", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            out[ifn] = ip
        except Exception as e:
            out[ifn] = "ERR %s" % e
    return out

def wifi_services():
    """connmanctl services -> [{name, id, ssid, state, strength, security}]"""
    out = []
    for line in _sh("connmanctl services", 8).splitlines():
        m = re.match(r"([ *AOR]{0,4})\s*(.*?)\s+(\S+_(?:managed|hidden)_(?:psk|none|ieee8021x|wep)|\S+)$", line.rstrip())
        if not m: continue
        flags, name, sid = m.groups()
        if not sid.startswith("wifi_"): continue
        out.append({"id": sid, "name": name, "connected": "R" in flags or "O" in flags, "online": "O" in flags,
                    "autoconnect": "A" in flags, "security": sid.rsplit("_", 1)[-1]})
    return out

def wifi_status(brief=False):
    st = {"state": None}
    for line in _sh("connmanctl state", 5).splitlines():
        if line.strip().startswith("State"): st["state"] = line.split("=")[-1].strip()
    if brief:
        cur = [s for s in wifi_services() if s["connected"]]
        st["ssid"] = cur[0]["name"] if cur else None
        st["ip"] = lan_ip()
        return st
    st["services"] = wifi_services(); st["ip"] = lan_ip(); st["ips"] = iface_addrs()
    return st

def wifi_scan():
    _sh("connmanctl scan wifi", 20)
    return wifi_services()

def wifi_connect(ssid, psk=None, hidden=False):
    """Provision through a connman config file (no interactive agent needed); connman auto-connects."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", ssid)[:40]
    path = "/var/lib/connman/agent-%s.config" % name
    cfg = "[service_%s]\nType = wifi\nName = %s\nIPv4 = dhcp\nAutoConnect = true\n" % (name, ssid)
    if psk: cfg += "Passphrase = %s\nSecurity = psk\n" % psk
    else: cfg += "Security = none\n"
    if hidden: cfg += "Hidden = true\n"
    open(path, "w").write(cfg)
    _sh("connmanctl enable wifi", 5); _sh("connmanctl scan wifi", 20)
    # nudge: connect by service id if it is visible
    sid = next((s["id"] for s in wifi_services() if s["name"] == ssid), None)
    out = _sh("connmanctl connect %s" % sid, 25) if sid else "provisioned; waiting for connman"
    return {"ok": True, "config": path, "service": sid, "out": out.strip()[-300:]}

def wifi_forget(ssid):
    sid = next((s["id"] for s in wifi_services() if s["name"] == ssid), None)
    if sid: _sh("connmanctl remove %s" % sid, 10)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", ssid)[:40]
    try: os.remove("/var/lib/connman/agent-%s.config" % name)
    except Exception: pass
    return {"ok": True}

def cloud_block_status():
    return {"blocked": os.path.exists(CLOUD_BLOCK_MARK), "tool": os.path.exists("/usr/bin/syng-cloud-block")}

def cloud_block(on):
    if not os.path.exists("/usr/bin/syng-cloud-block"): return {"ok": False, "error": "syng-cloud-block not installed"}
    return {"ok": True, "out": _sh("/usr/bin/syng-cloud-block %s" % ("on" if on else "off"), 60)[-400:]}

# ---------- membership keeper (primary): keep ip_address current, mark online ----------
def keeper_loop():
    time.sleep(20)
    while True:
        try:
            try: usbnet_up()
            except Exception: pass
            w = whoami()
            if (w.get("role") or "").lower() != "secondary":
                doc = room_doc(); changed = False
                for s, c in doc["cells"].items():
                    if s == doc["me"]:
                        if w.get("ip") and c.get("ip_address") != w["ip"]: c["ip_address"] = w["ip"]; changed = True
                        if w.get("role") and c.get("role") != w["role"]: c["role"] = w["role"]; changed = True
                        continue
                    ip = c.get("ip_address")
                    try:
                        o = agent_get(ip, "/api/whoami", timeout=3)
                        if o.get("serial") == s and o.get("ip") and o["ip"] != ip:
                            c["ip_address"] = o["ip"]; changed = True
                        if o.get("serial") == s and o.get("role") and o["role"] != c.get("role"):   # celldirector re-elected
                            c["role"] = o["role"]; changed = True
                        if o.get("ssh_key") and o["ssh_key"] != c.get("ssh_key"): c["ssh_key"] = o["ssh_key"]; changed = True
                    except Exception:
                        # moved? look it up by serial on the LAN
                        try:
                            d = discover(timeout=0.8)
                            hit = next((x for x in d["cells"] if x.get("serial") == s and x.get("ip")), None)
                            if hit and hit["ip"] != ip: c["ip_address"] = hit["ip"]; changed = True
                        except Exception: pass
                if changed:
                    bump(doc["room_state"]); fan_out(doc)
        except Exception as e:
            log("keeper: %s" % e)
        time.sleep(90)

# ---------- HTTP glue ----------
# ---------- housekeeping: what eats /data and RAM, and the safe ways to free it ----------
def housekeeping():
    out = {"journal_bytes": 0, "coredumps": [], "coredump_bytes": 0, "top": [], "data": {}, "tmp": []}
    try:
        m = re.search(r"take up ([\d.]+)([KMG])", _sh("journalctl --disk-usage", 10))
        if m: out["journal_bytes"] = int(float(m.group(1)) * {"K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)])
    except Exception: pass
    for d in ("/data/tmp/coredump", "/var/lib/systemd/coredump"):
        try:
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f); st = os.stat(p)
                out["coredumps"].append({"file": f, "bytes": st.st_size, "mtime": int(st.st_mtime), "path": p}); out["coredump_bytes"] += st.st_size
        except Exception: pass
    try:
        for line in _sh("ps -eo rss,etime,comm --sort=-rss", 10).splitlines()[1:8]:
            parts = line.split(None, 2)
            if len(parts) == 3: out["top"].append({"rss_kb": int(parts[0]), "elapsed": parts[1], "name": parts[2]})
    except Exception: pass
    try:
        sv = os.statvfs("/data"); out["data"] = {"total": sv.f_blocks * sv.f_frsize, "free": sv.f_bavail * sv.f_frsize}
        for line in _sh("du -xsk /data/* 2>/dev/null | sort -rn | head -8", 20).splitlines():
            k, p = line.split(None, 1); out["tmp"].append({"path": p, "bytes": int(k) * 1024})
    except Exception: pass
    return out

def housekeeping_action(action, name=None):
    if action == "vacuum_journal":
        return {"ok": True, "out": _sh("journalctl --vacuum-size=32M 2>&1 | tail -2", 60)}
    if action == "clear_coredumps":
        n = 0
        for d in ("/data/tmp/coredump", "/var/lib/systemd/coredump"):
            try:
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f)); n += 1
            except Exception: pass
        return {"ok": True, "removed": n}
    if action == "restart_service":
        allowed = ("airplay2source", "spotifysource", "tidalsource", "syng-agent", "pycalibrator", "cellbroker", "ledmanager")
        if name not in allowed: return {"ok": False, "error": "not allowed: %s (one of %s)" % (name, ", ".join(allowed))}
        return {"ok": True, "out": _sh("systemctl restart %s; sleep 1; systemctl is-active %s" % (name, name), 30).strip()}
    return {"ok": False, "error": "unknown action"}


# ---------- transport, artwork proxy, sleep timer, volume limit, ring settings, live fleet ----------
def transport(sid, action, position=None):
    dest, path, rt = source_target(sid)
    if not dest or not path: raise RuntimeError("source %s has no object" % sid)
    m = {"play": "play", "pause": "pause", "stop": "stop", "next": "next", "previous": "previous", "seek": "seek",
         "shuffle_on": "enableShuffle", "shuffle_off": "disableShuffle", "repeat_off": "disableRepeat",
         "repeat_context": "enableRepeatContext", "repeat_track": "enableRepeatTrack"}.get(action)
    if not m: raise RuntimeError("unknown action %s" % action)
    with DBUS_LOCK:
        I = dbus.Interface(BUS.get_object(dest, path), "com.syng.AudioSource.TransportControl")
        if m == "seek": I.seek(dbus.Double(float(position or 0)))
        else: getattr(I, m)()
    return True

def artwork_url(sid):
    dest, path, _ = source_target(sid)
    if not dest or not path: return None
    try: return str(get_all(dest, path, "com.syng.AudioSource.Artwork").get("artworkUrl") or "") or None
    except Exception: return None

SETTINGS_FILE = LC_DIR + "/settings.json"
SETTINGS = {"volume_limit": 0}
def load_settings():
    try: SETTINGS.update(json.loads(_read(SETTINGS_FILE, "{}") or "{}"))
    except Exception: pass
def save_settings():
    _ensure_dir()
    try: open(SETTINGS_FILE, "w").write(json.dumps(SETTINGS))
    except Exception as e: log("settings: %s" % e)

SLEEP = {"until": None, "fade_s": 30, "started": None, "state": "off"}
def sleep_loop():
    while True:
        try:
            u = SLEEP.get("until")
            if u and time.time() >= u:
                SLEEP["state"] = "fading"; SLEEP["until"] = None
                srcs = live_sources(); act = [k for k, v in srcs.items() if v.get("music") and v.get("active")]
                if act:
                    v0 = float(srcs[act[0]].get("volume") or 0); steps = 20
                    for i in range(1, steps + 1):
                        set_volume(None, v0 * (1 - i / steps)); time.sleep(SLEEP["fade_s"] / steps)
                    for sid in act:
                        try: set_source(sid, False)
                        except Exception: pass
                    time.sleep(1); set_volume(None, v0)   # restore the level for next time, source is off
                log("SLEEP timer fired: faded %s" % act); SLEEP["state"] = "off"
        except Exception as e:
            log("sleep loop: %s" % e); SLEEP["state"] = "off"
        time.sleep(1)

def set_sleep(minutes):
    if not minutes: SLEEP.update({"until": None, "state": "off"}); return SLEEP
    SLEEP.update({"until": time.time() + float(minutes) * 60, "started": time.time(), "state": "armed"}); return SLEEP

def volume_guard_loop():
    while True:
        try:
            lim = float(SETTINGS.get("volume_limit") or 0)
            if lim > 0:
                for sid, v in live_sources().items():
                    if v.get("music") and v.get("volume") is not None and float(v["volume"]) > lim / 100.0 + 0.004:
                        set_volume(sid, lim / 100.0); log("VOLUME LIMIT: %s %.0f%% -> %.0f%%" % (sid, float(v["volume"]) * 100, lim))
        except Exception as e: log("volume guard: %s" % e)
        time.sleep(2)

RING_FILES = {"hid": "/etc/syng/apps/hidserver/options.conf", "cd": "/etc/syng/apps/celldirector/options.conf"}
def ring_get():
    out = {}
    for k, p in RING_FILES.items():
        try: out[k] = json.loads(_read(p, "{}") or "{}")
        except Exception: out[k] = {}
    return {"steps": out["hid"].get("control_ring_vol_steps", out["cd"].get("control_ring_vol_steps")),
            "clockwise_up": out["hid"].get("control_ring_clockwise_vol_up", out["cd"].get("control_ring_clockwise_vol_up")),
            "ui_sound_pct": out["cd"].get("ui_sound_vol_pct"), "role_strategy": out["cd"].get("role_strategy"),
            "raw": out}
def ring_set(steps=None, clockwise_up=None, ui_sound_pct=None):
    changed = []
    for k, p in RING_FILES.items():
        try: d = json.loads(_read(p, "{}") or "{}")
        except Exception: d = {}
        before = json.dumps(d, sort_keys=True)
        if steps is not None and "control_ring_vol_steps" in d: d["control_ring_vol_steps"] = int(max(10, min(400, int(steps))))
        if clockwise_up is not None and "control_ring_clockwise_vol_up" in d: d["control_ring_clockwise_vol_up"] = bool(clockwise_up)
        if ui_sound_pct is not None and k == "cd": d["ui_sound_vol_pct"] = int(max(0, min(60, int(ui_sound_pct))))
        if json.dumps(d, sort_keys=True) != before:
            try: open(p + ".orig", "x").write(before)   # keep the first original
            except Exception: pass
            open(p, "w").write(json.dumps(d, indent=2) + "\n"); changed.append(k)
    if "hid" in changed: _sh("systemctl restart hidserver", 20)   # the ring daemon; harmless to restart
    return {"ok": True, "changed": changed, "now": ring_get(), "note": "celldirector reads ui_sound_vol_pct at start; the Ping button uses the new value right away"}

def wifi_rssi():
    try:
        m = re.search(r"signal:\s*(-?\d+) dBm", _sh("iw dev winfra0 link", 4)); return int(m.group(1)) if m else None
    except Exception: return None

def fleet_live():
    me = whoami(); rows = [{"serial": me.get("serial"), "ip": me.get("ip"), "role": me.get("role"), "syfi": me.get("syfiGood"), "sync": me.get("timeSyncGood"),
                            "ssid": (me.get("wifi") or {}).get("ssid"), "rssi": wifi_rssi(), "agent": me.get("agent"), "isMe": True, "ok": True}]
    lock = threading.Lock(); ths = []
    def q(ip, serial):
        try:
            o = agent_get(ip, "/api/whoami", timeout=3)
            with lock: rows.append({"serial": serial, "ip": ip, "role": o.get("role"), "syfi": o.get("syfiGood"), "sync": o.get("timeSyncGood"),
                                    "ssid": (o.get("wifi") or {}).get("ssid"), "rssi": o.get("rssi"), "agent": o.get("agent"), "isMe": False, "ok": True})
        except Exception as e:
            with lock: rows.append({"serial": serial, "ip": ip, "ok": False, "error": str(e)[:60], "isMe": False})
    for serial, c in room_doc()["cells"].items():
        if serial == me.get("serial") or not c.get("ip_address"): continue
        t = threading.Thread(target=q, args=(c["ip_address"], serial), daemon=True); t.start(); ths.append(t)
    for t in ths: t.join(4)
    return {"cells": rows}


# ---------- the sound field survives restarts and new sessions ----------
FIELD_FILE = LC_DIR + "/field.json"
def field_load():
    try:
        d = json.loads(_read(FIELD_FILE, "") or "{}")
        for k in ("ux", "uy", "rot", "width", "radius", "spatial", "snap"):
            if k in d: FIELD[k] = d[k]
        log("field restored: %s" % {k: FIELD[k] for k in ("ux", "uy", "rot", "width", "spatial", "snap")})
    except Exception as e: log("field load: %s" % e)
def field_keeper_loop():
    """Save the handle when it changes; when a source goes active (a new AirPlay/Spotify session), the sound server
       starts from the cloud-persisted default, so push ours again a moment later."""
    active = {}
    while True:
        try:
            if FIELD_DIRTY[0]:
                FIELD_DIRTY[0] = False; _ensure_dir(); open(FIELD_FILE, "w").write(json.dumps(FIELD))
            now = {}
            for sid in source_ids():
                if not is_music(sid): continue
                try: now[sid] = bool(get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"))
                except Exception: now[sid] = False
            fresh = [sid for sid, a in now.items() if a and not active.get(sid)]
            active = now
            if fresh and (FIELD.get("spatial") or FIELD.get("snap") or abs(FIELD.get("ux", 0)) > 0.01 or abs(FIELD.get("uy", 0)) > 0.01 or abs(FIELD.get("rot", 90) - 90) > 0.5):
                time.sleep(1.5); n = set_soundfield(); log("field re-applied to %s (%d)" % (fresh, n))
        except Exception as e: log("field keeper: %s" % e)
        time.sleep(2)

def cloudblock_all():
    me = whoami(); cells = [{"serial": me.get("serial"), "ip": me.get("ip"), "blocked": os.path.exists(CLOUD_BLOCK_MARK), "isMe": True}]
    lock = threading.Lock(); ths = []
    def q(ip, serial):
        try: b = agent_get(ip, "/api/cloudblock", timeout=3).get("blocked")
        except Exception: b = None
        with lock: cells.append({"serial": serial, "ip": ip, "blocked": b, "isMe": False})
    for serial, c in room_doc()["cells"].items():
        if serial == me.get("serial") or not c.get("ip_address"): continue
        t = threading.Thread(target=q, args=(c["ip_address"], serial), daemon=True); t.start(); ths.append(t)
    for t in ths: t.join(4)
    return {"cells": sorted(cells, key=lambda c: (not c["isMe"], c.get("ip") or ""))}


# ---------- ping: what the app's "Ping" does — the three-note identify sound + the IdentifyCell LED vision ----------
PING_WAV = "/usr/lib/syng/sounds/multi-cell-orientation.wav"
def ui_sound_level():
    try: return max(0.05, min(0.6, float(json.loads(_read("/etc/syng/apps/celldirector/options.conf", "{}")).get("ui_sound_vol_pct", 25)) / 100.0))
    except Exception: return 0.25

def ping_local(vol=None):
    lvl = ui_sound_level() if vol is None else max(0.05, min(0.6, float(vol)))
    out = {"level": lvl}
    try:
        with DBUS_LOCK:
            dbus.Interface(BUS.get_object("com.syng.SAPlayer", "/com/syng/SAPlayer"), "com.syng.SAPlayer").PlayOneShot(PING_WAV, lvl)
        out["sound"] = True
    except Exception as e: out["sound"] = str(e)
    try:
        with DBUS_LOCK:
            dbus.Interface(BUS.get_object("com.syng.CellServer", "/com/syng/localcell"), "com.syng.Cell.CoreServices").IdentifyCell()
        out["led"] = True
    except Exception as e: out["led"] = str(e)
    return out

def op_ping(serial=None, vol=None, everyone=False):
    me = whoami()
    if everyone:
        res = {}
        for sn, c in room_doc()["cells"].items():
            if sn == me.get("serial"): res[sn] = ping_local(vol)
            else:
                try: res[sn] = agent_post(c.get("ip_address"), "/api/ping", {"vol": vol}, timeout=8)
                except Exception as e: res[sn] = str(e)
        return {"ok": True, "cells": res}
    if not serial or serial == me.get("serial"): return dict(ping_local(vol), ok=True, serial=me.get("serial"))
    c = room_doc()["cells"].get(serial)
    ip = c.get("ip_address") if c else None
    if not ip:   # not in my room: find it on the LAN
        hit = next((x for x in discover(timeout=0.8)["cells"] if x.get("serial") == serial and x.get("agent")), None)
        ip = hit and hit.get("ip")
    if not ip: raise RuntimeError("no agent found for %s" % serial)
    return dict(agent_post(ip, "/api/ping", {"vol": vol}, timeout=8), relayed=ip)

def lc_get(path):
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()
    if path.startswith("/api/fleet/live"): return fleet_live()
    if path.startswith("/api/sleep"): return dict(SLEEP, now=time.time())
    if path.startswith("/api/settings"): return dict(SETTINGS, sleep=SLEEP, ring=ring_get())
    if path.startswith("/api/ring"): return ring_get()
    if path.startswith("/api/housekeeping"): return housekeeping()
    if path.startswith("/api/whoami"): return whoami()
    if path.startswith("/api/room/backup"):
        return json.loads(_read(LC_BACKUP, "null") or "null") or {"error": "no backup on this cell"}
    if path.startswith("/api/discover"): return discover()
    if path.startswith("/api/room/doc"): return raw_state()
    if path.startswith("/api/room"): return room_view()
    if path.startswith("/api/calibration/status"): return CAL_STATUS
    if path.startswith("/api/wifi/scan"): return {"services": wifi_scan()}
    if path.startswith("/api/wifi"): return wifi_status()
    if path.startswith("/api/cloudblock"): return cloud_block_status()
    if path.startswith("/api/usbnet"): return {"usb": USB_NETS, "addrs": iface_addrs()}
    return None

def lc_post(path, body):
    if path.startswith("/api/relay"):
        # the setup page talks to the primary through whichever cell it opened (works over the USB link)
        ip = body.get("ip"); sub = body.get("path", "/")
        if not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip or "") or not sub.startswith("/api/"): raise RuntimeError("bad relay target")
        if body.get("method", "GET").upper() == "POST": return agent_post(ip, sub, body.get("body") or {}, timeout=40)
        return agent_get(ip, sub, timeout=20)
    if path.startswith("/api/room/apply"): return {"ok": apply_view(body)}
    if path.startswith("/api/room/admit"): return op_admit(body["cell"])
    if path.startswith("/api/room/join"): return op_join(body["ip"])
    if path.startswith("/api/room/create"): return op_create_room(body.get("name"), body.get("place_id"))
    if path.startswith("/api/room/remove"): return op_remove(body["serial"], body.get("_notify", True))
    if path.startswith("/api/room/leave"):
        st = raw_state(); cs = st.get("cell_state") or {}
        return op_create_room("Cell %s" % (cs.get("serial_number") or "")[-4:], cs.get("place_id"))
    if path.startswith("/api/room/rename"): return op_rename(body["name"])
    if path.startswith("/api/room/layout"): return op_layout(body)
    if path.startswith("/api/room/pin"): return op_pin(body["serial"], body.get("role", "primary"))
    if path.startswith("/api/room/restore"):
        backup_state_once(); return {"ok": patch_state_strict(body["state"])}
    if path.startswith("/api/calibration/run"): return op_calibrate()
    if path.startswith("/api/calibration/results"):
        return op_calibration_results(body if isinstance(body, list) else body.get("results"))
    if path.startswith("/api/wifi/connect"): return wifi_connect(body["ssid"], body.get("psk"), bool(body.get("hidden")))
    if path.startswith("/api/wifi/forget"): return wifi_forget(body["ssid"])
    if path.startswith("/api/cloudblock"):
        r = cloud_block(bool(body.get("on")))
        if body.get("scope") == "group" and not body.get("_local"):
            r["group"] = {}
            for serial, c in room_doc()["cells"].items():
                if c.get("ip_address") and serial != whoami().get("serial"):
                    try: r["group"][serial] = agent_post(c["ip_address"], "/api/cloudblock", {"on": bool(body.get("on")), "_local": True}, timeout=70).get("ok")
                    except Exception as e: r["group"][serial] = str(e)
        return r
    if path.startswith("/api/housekeeping"): return housekeeping_action(body.get("action"), body.get("name"))
    if path.startswith("/api/ping"): return op_ping(body.get("serial"), body.get("vol"), bool(body.get("all")))
    if path.startswith("/api/transport"): return {"ok": transport(body.get("source"), body.get("action"), body.get("position"))}
    if path.startswith("/api/sleep"): return set_sleep(body.get("minutes"))
    if path.startswith("/api/settings"):
        if "volume_limit" in body: SETTINGS["volume_limit"] = int(max(0, min(100, int(body["volume_limit"] or 0))))
        save_settings(); return dict(SETTINGS)
    if path.startswith("/api/ring"): return ring_set(body.get("steps"), body.get("clockwise_up"), body.get("ui_sound_pct"))
    if path.startswith("/api/usbnet"): return {"ok": True, "up": usbnet_up()}
    return None

def lc_start():
    _ensure_dir(); backup_state_once()
    try: log("usbnet %s" % usbnet_up())
    except Exception as e: log("usbnet: %s" % e)
    threading.Thread(target=keeper_loop, daemon=True).start()


PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Syng · LAN</title>
<style>
:root{--bg:#14161b;--surface:#1b1e26;--surface2:#222633;--line:#2b3140;--text:#e8eaf0;
 --muted:#98a0b0;--accent:#8a7cff;--live:#4fd1c5;--warn:#e6a23c;--bad:#e5618a;--radius:18px}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;
 --line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}}
:root[data-theme=light]{--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;--line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}
.themebtn{font:inherit;font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer;margin-left:10px}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--text);font-variant-numeric:tabular-nums;
 font-family:ui-rounded,"SF Pro Rounded","SF Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased;padding:16px;line-height:1.4}
.wrap{max-width:900px;margin:0 auto}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:6px 2px 16px}
.mark{font-size:13px;letter-spacing:.14em;color:var(--muted);font-weight:600}.mark b{color:var(--accent)}
.conn{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--muted);margin-right:5px;vertical-align:middle}
.dot.on{background:var(--live)}
.hero{display:flex;gap:18px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;margin-bottom:14px}
.orb{--ring:var(--muted);position:relative;width:88px;height:88px;flex:none;border-radius:50%;
 background:radial-gradient(60% 60% at 38% 32%,#2b3040,#14161b 78%);box-shadow:inset 0 0 0 1px #0007,0 10px 30px #0006}
.orb::after{content:"";position:absolute;inset:-5px;border-radius:50%;background:conic-gradient(from 210deg,var(--ring),transparent 55%,var(--ring));
 -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px));mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px))}
.orb.live{--ring:var(--live)}.orb.primary{--ring:var(--accent)}.orb.live::after{animation:spin 6s linear infinite}
@media(prefers-reduced-motion:reduce){.orb.live::after{animation:none}}@keyframes spin{to{transform:rotate(360deg)}}
.id h1{margin:0 0 2px;font-size:26px;font-weight:650;letter-spacing:-.01em}.id .sub{color:var(--muted);font-size:14px}
.badges{margin-top:8px;display:flex;gap:7px;flex-wrap:wrap}
.badge{font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.badge.role{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin-bottom:14px}
.card h2{margin:0 0 12px;font-size:12px;letter-spacing:.1em;color:var(--muted);font-weight:600}
.card h2 .rn{color:var(--text);letter-spacing:0;font-size:15px;margin-left:8px}
.srcs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.srcbtn{flex:1 1 90px;min-width:90px;padding:10px;border-radius:12px;border:1px solid var(--line);background:var(--surface2);
 color:var(--text);font:inherit;font-size:13px;font-weight:600;cursor:pointer;text-align:center}
.srcbtn.on{border-color:var(--live);color:var(--live);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--live) 40%,transparent)}
.np{color:var(--muted);font-size:13px;margin-bottom:12px;min-height:1.2em}.np b{color:var(--text);font-weight:600}
.ctl{margin:12px 0}.ctl label{display:flex;justify-content:space-between;font-size:13px;color:var(--muted);margin-bottom:6px}
.ctl label b{color:var(--text);font-weight:600}
input[type=range]{width:100%;-webkit-appearance:none;appearance:none;height:6px;border-radius:3px;background:var(--surface2);outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:var(--accent);cursor:pointer;border:2px solid var(--surface)}
input[type=range].tone::-webkit-slider-thumb{background:var(--live)}
#map{width:100%;height:auto;display:block;background:var(--surface2);border-radius:12px}
.legend{display:flex;gap:14px;margin-top:10px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.legend i{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.row{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px}
.row span:first-child{color:var(--muted)}.hot{color:var(--warn)}.fault{color:var(--bad)}.good{color:var(--live)}
.fleet{display:grid;gap:8px}
.fcell{display:flex;align-items:center;gap:12px;padding:9px 12px;background:var(--surface2);border-radius:12px;border:1px solid transparent}
.fcell.me{border-color:color-mix(in srgb,var(--accent) 40%,var(--line))}
.fdot{width:10px;height:10px;border-radius:50%;background:var(--muted);flex:none}.fdot.on{background:var(--live)}.fdot.pri{background:var(--accent)}
.fcell .nm{font-weight:600;font-size:14px}.fcell .mt{color:var(--muted);font-size:12px;margin-left:auto;text-align:right}
footer{color:var(--muted);font-size:12px;text-align:center;padding:18px 0 6px}
.sysrow{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.sysmetric{background:var(--surface2);border:1px solid var(--line);border-radius:12px;padding:12px}
.sysk{color:var(--muted);font-size:12px;font-weight:600}
.sysv{font-size:22px;font-weight:650;margin:2px 0 6px;letter-spacing:-.01em}
.spark{width:100%;height:30px;display:block}
.spark polyline{fill:none;stroke:var(--accent);stroke-width:1.5;vector-effect:non-scaling-stroke}
.spark .area{fill:color-mix(in srgb,var(--accent) 14%,transparent);stroke:none}
</style></head><body><div class=wrap>
<script>(function(){const T=['auto','dark','light'];let t='auto';try{t=localStorage.getItem('syngTheme')||'auto';}catch(e){}
function apply(){if(t==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',t);document.querySelectorAll('.themebtn').forEach(b=>b.textContent='theme: '+t);}
window.cycleTheme=function(){t=T[(T.indexOf(t)+1)%T.length];try{localStorage.setItem('syngTheme',t);}catch(e){}apply();};apply();window.addEventListener('DOMContentLoaded',apply);})();</script>
<header><div class=mark>SYNG <b>·</b> LAN <a href="/setup" style="margin-left:14px;letter-spacing:0;font-weight:500;color:var(--muted);text-decoration:none">setup ›</a><button class=themebtn onclick="cycleTheme()">theme</button></div><div class=conn id=conn><span class=dot></span>connecting…</div></header>
<section class=hero><div class=orb id=orb></div><div class=id><h1 id=host>—</h1><div class=sub id=sub></div><div class=badges id=badges></div></div><button class=srcbtn id=pingme title="Play the identify sound + LED on this speaker" style="flex:0 0 auto;margin-left:auto;padding:10px 16px">Ping</button></section>

<section class=card id=playcard><h2>PLAYBACK <span class=rn id=mixbadge style="font-size:12px;color:var(--muted);font-weight:500"></span></h2>
  <div id=nowplaying style="display:none;align-items:center;gap:14px;margin-bottom:12px">
    <img id=npart alt="" style="width:84px;height:84px;border-radius:12px;object-fit:cover;background:var(--surface2);flex:none">
    <div style="flex:1;min-width:0"><div id=nptitle style="font-weight:650;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">—</div>
      <div id=npsub class=muted style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>
      <div id=npbar style="height:4px;border-radius:2px;background:var(--surface2);margin-top:8px;overflow:hidden"><div id=npfill style="height:100%;width:0;background:var(--accent)"></div></div>
      <div id=nptime class=muted style="font-size:11px;margin-top:3px"></div></div>
    <div id=transport style="display:flex;gap:6px;flex:none">
      <button class=srcbtn data-t=previous title="Previous" style="min-width:44px;padding:8px">⏮</button>
      <button class=srcbtn data-t=playpause title="Play / pause" style="min-width:52px;padding:8px">⏯</button>
      <button class=srcbtn data-t=next title="Next" style="min-width:44px;padding:8px">⏭</button></div>
  </div>
  <div class=np id=np></div>
  <div class=srcs id=srcs></div>
  <div class=ctl><label>Volume <b id=volval></b></label><input type=range id=vol min=0 max=100 value=30></div>
  <div class=srcs><button class=srcbtn id=mutebtn>Mute</button></div>
  <div class=row style="margin-top:4px"><span>Sleep timer <b id=sleepstate style="color:var(--text)"></b></span><span class=srcs style="margin:0;gap:6px">
    <button class="srcbtn sm" data-sleep=0 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">off</button>
    <button class="srcbtn sm" data-sleep=15 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">15</button>
    <button class="srcbtn sm" data-sleep=30 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">30</button>
    <button class="srcbtn sm" data-sleep=60 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">60</button>
    <button class="srcbtn sm" data-sleep=90 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">90 min</button></span></div>
  <div class=row><span>Volume limit <b id=limval style="color:var(--text)"></b></span><span class=srcs style="margin:0;gap:6px">
    <button class="srcbtn sm" data-lim=0 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">off</button>
    <button class="srcbtn sm" data-lim=50 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">50</button>
    <button class="srcbtn sm" data-lim=70 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">70</button>
    <button class="srcbtn sm" data-lim=85 style="flex:none;min-width:0;padding:4px 9px;font-size:12px">85 %</button></span></div>
</section>

<section class=card><h2>TONE</h2>
  <svg id=tonecurve viewBox="0 0 300 66" preserveAspectRatio=none style="width:100%;height:58px;display:block;margin-bottom:10px;background:var(--surface2);border:1px solid var(--line);border-radius:12px"></svg>
  <div class=ctl><label>Lows <b id=bassval>0</b></label><input type=range class=tone id=bass min=-100 max=100 value=0></div>
  <div class=ctl><label>Highs <b id=tiltval>0</b></label><input type=range class=tone id=tilt min=-100 max=100 value=0></div>
</section>

<section class=card id=spatialcard style=display:none><h2>SPATIAL</h2>
  <div class=srcs id=spatialtoggles></div>
  <div id=soundstage style=display:none>
    <div class=ctl style=margin-top:12px><label>Spread <b id=focv>55°</b></label><input type=range id=soundfocus min=20 max=360 value=55></div>
    <div class=srcs><button class=srcbtn id=soundreset>Center the sound</button></div>
    <div style="color:var(--muted);font-size:12px;margin-top:4px">On the field: drag the puck to move the sound. Lift the small dot off it to go spatial, then drag the two handles to aim and widen — swing them around the back for full surround.</div>
  </div>
  <div style="color:var(--muted);font-size:12px;margin-top:6px">Applies to the music sources.</div>
</section>

<section class=card id=calibcard style=display:none><h2>SOUNDCHECK</h2>
  <div class=ctl><label>Room correction</label><div class=srcs id=roomeq></div></div>
  <div class=grid id=calibgrid style=margin-top:4px></div>
</section>

<section class=card><h2>LED</h2>
  <div class=ctl><label>Applies to</label><div class=srcs id=ledscope></div></div>
  <div class=ctl><label>Mode</label><div class=srcs id=ledmodes></div></div>
  <div class=ctl id=palrow style=display:none><label>Group color</label><div class=srcs id=ledpalette></div></div>
  <div id=tunewrap style=display:none>
    <div class=ctl><label>Beat sensitivity <b id=bsv></b></label><input type=range id=beatsens min=2 max=40 value=10></div>
    <div class=ctl><label>Brightness floor <b id=flv></b></label><input type=range id=ledfloor min=0 max=50 value=7></div>
    <div class=ctl><label>Flow fade · silent <b id=sfv>3.0s</b></label><input type=range id=silentfade min=2 max=80 value=30></div>
    <div class=ctl><label>Flow fade · beat <b id=bfv>0.35s</b></label><input type=range id=beatfade min=5 max=200 value=35></div>
  </div>
  <div id=ledmanual>
    <div class=ctl><label>Color</label>
      <div style="display:flex;gap:10px;align-items:center">
        <input type=color id=ledcol value="#8a7cff" style="width:50px;height:36px;padding:0;border:1px solid var(--line);border-radius:8px;background:none">
        <span style="color:var(--muted);font-size:13px">White</span>
        <input type=range id=ledw min=0 max=100 value=0 style="flex:1">
        <button class=srcbtn id=ledapply style="flex:0 0 auto;min-width:64px">Set</button>
      </div></div>
    <div class=ctl><label>Animations</label><div class=srcs id=ledanims></div></div>
    <div class=srcs><button class=srcbtn data-anim=pause>Pause</button><button class=srcbtn data-anim=resume>Resume</button><button class=srcbtn data-anim=clear>Clear</button></div>
    <div class=ctl><label>Edit animation</label>
      <select id=visedit style="width:100%;padding:8px;border-radius:10px;background:var(--surface2);color:var(--text);border:1px solid var(--line);margin-bottom:8px;font:inherit"></select>
      <textarea id=visjson spellcheck=false style="width:100%;height:120px;font-family:ui-monospace,Menlo,monospace;font-size:12px;background:var(--surface2);color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px"></textarea>
      <div class=srcs style=margin-top:8px><button class=srcbtn id=vissave style="flex:0 0 auto">Save animation</button><span id=vismsg style="color:var(--muted);font-size:12px;align-self:center"></span></div>
    </div>
  </div>
  <div id=lednote style="color:var(--muted);font-size:12px;display:none">Reactive mode owns the LED. Switch to Manual for color &amp; animations.</div>
</section>

<section class=card id=roomcard style=display:none><h2>ROOM<span class=rn id=rname></span></h2>
  <div id=fieldwrap style="position:relative;width:100%;border-radius:14px;overflow:hidden;background:#000"></div>
  <div class=legend><span>◉ drag the puck to move the sound</span><span>• lift the dot to go spatial</span><span>◗ handles aim &amp; widen</span><span>▂ bottom rim = volume</span></div>
  <details id=fieldtweaks style="margin-top:6px"><summary style="cursor:pointer;color:var(--muted);font-size:12px">Field tweaks</summary>
    <div class=srcs style=margin-top:8px><button class=srcbtn id=fldreset>Reset particles</button><button class=srcbtn id=fldcenter>Center the sound</button><button class=srcbtn id=fldclassic>Classic (all around)</button></div>
    <div id=fieldsliders></div>
  </details>
</section>
<section class=card id=fleetcard style=display:none><h2>SPEAKERS <span class=rn id=fcount></span><button class="srcbtn" id=pingall style="float:right;flex:none;min-width:0;padding:4px 10px;font-size:12px">Ping all</button></h2><div class=fleet id=fleet></div></section>

<section class=card id=cloudcard><h2>SYNG CLOUD <span class=rn id=cloudsum></span></h2>
  <div class=fleet id=cloudlist style="margin-bottom:12px"></div>
  <div class=srcs>
    <button class=srcbtn id=cbme>…</button>
    <button class=srcbtn id=cball>Block on every cell</button>
    <button class=srcbtn id=cbnone>Unblock on every cell</button>
  </div>
  <p class=hint style="font-size:12px;color:var(--muted);margin:0">Blocked = the cell cannot reach api.prod.syng.space (DNS + firewall), the room document on the cells is authoritative and the setup page owns grouping/layout. Unblocked = Syng's cloud takes over again and overwrites local changes with what it last knew. The setting survives reboots.</p>
</section>
<div class=grid id=grid></div>
<section class=card id=syscard><h2>SYSTEM</h2>
  <div class=sysrow>
    <div class=sysmetric><div class=sysk>CPU</div><div class=sysv id=cpuv>—</div><svg class=spark id=cpuspark viewBox="0 0 120 30" preserveAspectRatio=none></svg></div>
    <div class=sysmetric><div class=sysk>Memory</div><div class=sysv id=memv>—</div><svg class=spark id=memspark viewBox="0 0 120 30" preserveAspectRatio=none></svg></div>
  </div>
  <div class=grid id=sysgrid style=margin-top:12px></div>
</section>
<section class=card id=statscard style=display:none><h2>STATISTICS</h2><div class=grid id=stats></div></section>
<footer id=foot></footer></div>
<script>
const $=s=>document.querySelector(s);
const short=s=>s?s.slice(-4):'—';
const TEMP={SoC_Temperature:'SoC',PMIC_Temperature:'PMIC',CPU_Temperature:'CPU',eMMC_Temperature:'eMMC',
 Wireless_Module_Temperature:'WiFi',PSAB_Woofer2_Amplifier_Temperature:'Amp',PSAB_MLB_Connector_Temperature:'MLB'};
const ICON={airplay2:'AirPlay',spotify:'Spotify',tidal:'TIDAL',CalibrationClient:'Calibrate',usbgadget:'USB-C',usbgadgetsource:'USB-C',usbhost:'USB in',usbhostsource:'USB in',hdmi:'HDMI',hdmisource:'HDMI',pd:'Pure Data',pdaudiosource:'Pure Data'};
const srcName=(id,s)=>ICON[id]||(s&&s.displayName)||id;
let dragging={}, lastPost={};
async function post(path,body){try{await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}catch(e){}}
function throttle(key,fn,ms){const t=Date.now();if(t-(lastPost[key]||0)>=ms){lastPost[key]=t;fn();}}
function tile(t,rows){const r=rows.filter(Boolean).map(([k,v,c])=>`<div class=row><span>${k}</span><span class="${c||''}">${v}</span></div>`).join('');return r?`<div class=card><h2>${t}</h2>${r}</div>`:'';}
function activeSrc(sources){for(const[id,s]of Object.entries(sources||{}))if(s.active)return id;return null;}
// Translate raw eMMC JEDEC health registers into plain-language rows.
function mmcHuman(m){
  if(!m||!Object.keys(m).length) return {rows:[],raw:[]};
  const g=k=>m[k], P=v=>parseInt(v);
  // DEVICE_LIFE_TIME_EST_TYP_A/B: 1=>0-10% consumed … 10=>90-100%, 11=>exceeded
  const band=v=>{v=P(v);return (!v||v<1)?'n/a':v>=11?'over 100% — worn out':((v-1)*10)+'–'+(v*10)+'% used';};
  const bandCls=v=>{v=P(v);return v>=9?'fault':v>=7?'hot':v>=1?'good':'';};
  const eol=P(g('eMMC PRE EOL Info'));
  const eolTxt=eol===1?'Normal':eol===2?'Warning — 80% of reserve used':eol===3?'Urgent — 90% of reserve used':'unknown';
  const eolCls=eol===1?'good':eol===2?'hot':eol===3?'fault':'';
  const laterBB=P(g('later BB')), spare=g('spare blocks');
  const rows=[
    ['Overall health', eolTxt, eolCls],
    ['Life used · main storage', band(g('eMMC Life Time Estimation A')), bandCls(g('eMMC Life Time Estimation A'))],
    ['Life used · boot area', band(g('eMMC Life Time Estimation B')), bandCls(g('eMMC Life Time Estimation B'))],
    ['Failed blocks in service', (isNaN(laterBB)?'—':(laterBB+(laterBB===0?' — none':''))), laterBB>0?'hot':'good'],
    ['Spare blocks left', spare==null?'—':spare],
  ];
  const avgM=g('avg P/E cnt MLC'), avgS=g('avg P/E cnt pSLC');
  if(avgM!=null) rows.push(['Avg write/erase cycles','main '+avgM+' · boot '+avgS]);
  return {rows, raw:Object.entries(m).map(([k,v])=>[k.replace(/_/g,' '),v])};
}

const SYNG_TEX={"orbB": "data:image/png;base64,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", "orbC": "data:image/png;base64,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", "orbA": "data:image/png;base64,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", "lens": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAQAAAAABCAAAAAAUMi+rAAAApElEQVR42k1JOQ7CQBDbHc8QdkZACgp4Q95ABQ+D99DTwT+okCiREBKQcIQkFMsRy5dsn6iaWjT79c5oMR8n2loiQ1pkxUBVW2MwtaCDIsvTYMFU1YKaarxCdzjd9En+4A5/Wys46kPS2bpHLMwiLBCBCFgELBwBAQTMAIMBMIEJIBCIiMiT9+S8c865pm7qqqpeZfl83G/55Xw6HnbX1X65ndRvgPEmgAZLHgwAAAAASUVORK5CYII=", "airplay": "data:image/png;base64,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", "noise": "data:image/png;base64,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"};

// SyngField — faithful reproduction of the Syng Space app's sound-field control.
// Reverse-engineered from the decrypted Syng Space 2.1.0 IPA:
//   * ProtoTools particle system (updateParticle / vertexShader / fragmentShader Metal kernels)
//   * Aurora ShapeLayer "Orb" content (lens-table refraction + gloss maps OrbB / OrbC)
//   * Auras_airplay.json palette (CalculateColorBins histogram of the AirPlay aura)
//   * UISpringTimingParameters(mass 1, stiffness 218.566, damping 29.568)
// Usage: SyngField.create(wrapElement, { post(path, body), viz() -> {energy,bass,spec[]}, scaleM })
(function(){
'use strict';
const TAU = Math.PI*2;
const LENS = [0.251,0.261,0.271,0.281,0.291,0.302,0.312,0.322,0.332,0.342,0.352,0.362,0.372,0.383,0.393,0.403,0.413,0.423,0.433,0.443,0.453,0.463,0.472,0.482,0.492,0.502,0.512,0.521,0.531,0.541,0.55,0.56,0.569,0.579,0.588,0.597,0.606,0.616,0.625,0.633,0.642,0.651,0.659,0.667,0.676,0.684,0.691,0.699,0.706,0.712,0.719,0.725,0.73,0.734,0.738,0.741,0.742,0.741,0.738,0.731,0.719,0.697,0.657,0.57];

// ---------- defaults (all tweakable live through field.cfg) ----------
const DEFAULTS = {
  particles: 18000,        // ProtoTools particle count (CPU sim)
  simHz: 30,              // sim rate; rendering interpolates between sim steps (the app runs its kernel at 60)
  weightSpatial: 0.88,    // torus "weight": ra 0.28 / rb 0.22 (mask videos + the live recording's stem width)
  weightOmni: 2.0,        // 2 = full sphere (non-spatial: sound all around)
  handleRadius: 0.60,     // handles / notch sit on this fraction of the field radius
  defaultHalfDeg: 95,     // half-spread when spatial is first enabled
  snapToCell: true, snapFrac: 0.13,   // puck magnetises onto a speaker ring within this fraction of R
  minHalfDeg: 12, maxHalfDeg: 178,
  arcFrac: 1.0, capsAtDots: false, capLo: 0.5, capHi: 0.7, capScale: 1.0,
  liftFrac: 0.26,         // drag the dot this far (x field radius) to enable spatial
  collapseFrac: 0.2,      // drag a handle this close to the puck to collapse (spatial off)
  pulseScale: 2.0,        // distanceScale: field radius -> pulse texture v (rows scroll at the feed rate, ~30/s)
  pulseRowsPerSec: 56,    // how fast a beat travels outward
  pulseGain: 1.0,         // multiplies the particle pulse displacement
  pulseJitter: 0.25,       // row-to-row noise in the pulse texture (the app feeds a raw 60 Hz spectrogram; our feed is ~15 fps)
  pulseEmitter: 0.06,     // emitterRadius (field units; 0.5 = rim)
  glowGain: 1.0, grain: 1.0, flurry: 1.0, haloGain: 1.0,
  pointScale: 1.0, pointGain: 1.0,
  colors: { cyanDeep:[0.0,0.36,0.54], cyan:[0.14,0.64,0.84], cyanLight:[0.54,0.84,0.95], white:[0.90,0.98,1.0], blue:[0.10,0.42,0.86], teal:[0.08,0.60,0.70],
            room:[0.10,0.26,0.33], roomEdge:[0.07,0.19,0.23], bg:[0,0,0] },
  showVolume: true, showRings: true, classicParticles: true,
  volume: 0.25,
  scaleM: 2.5,            // metres per room radius (only used when no reference circle is known)
  radiusUnits: 0.7,       // panner_v2 'radius' sent when spatial is on (the app's default)
};

function clamp(v,a,b){return v<a?a:v>b?b:v;}
function wrapPi(a){return ((a+Math.PI)%TAU+TAU)%TAU-Math.PI;}
function smoothstep(e0,e1,x){const t=clamp((x-e0)/(e1-e0),0,1);return t*t*(3-2*t);}

// ---------- the capped-torus SDF (exact port of ProtoTools cappedTorus/sampleNormalAndDistance) ----------
// Field space: puck at origin, rim at radius 0.5, screen y down. S = prepared params.
function prepSDF(S, dirAngle, halfRad, weight, cfg){
  cfg = cfg || {};
  const arcHalf = halfRad*(cfg.arcFrac==null?1:cfg.arcFrac);
  const spread = clamp(arcHalf/Math.PI, 0.05, 1.0);
  S.sinA = Math.sin(spread*Math.PI); S.cosA = Math.cos(spread*Math.PI);
  S.nx = Math.sin(dirAngle); S.ny = -Math.cos(dirAngle);        // axis of the arc (toward the notch)
  const w = weight*0.25; S.ra = 0.5 - w; S.rb = w;
  const sp2 = clamp(halfRad/Math.PI, 0, 1);
  const capLo = cfg.capLo==null?0.5:cfg.capLo, capHi = cfg.capHi==null?0.7:cfg.capHi;
  const t = clamp((sp2-capLo)/Math.max(1e-4,capHi-capLo), 0, 1); S.capR = S.rb*(t*t*(3-2*t))*(cfg.capScale==null?1:cfg.capScale);
  S.blend = sp2 > capLo ? 0.1 : 1e-5;
  const ca = Math.cos(halfRad), sa = Math.sin(halfRad);
  const cr = cfg.capsAtDots ? (cfg.handleRadius==null?0.62:cfg.handleRadius)*0.5 : S.ra;   // cap centres: at the dots or on the torus centre line
  S.c1x = cr*(S.nx*ca - S.ny*sa); S.c1y = cr*(S.nx*sa + S.ny*ca);
  S.c2x = cr*(S.nx*ca + S.ny*sa); S.c2y = cr*(-S.nx*sa + S.ny*ca);
  S.spread = spread; S.weight = weight; S.half = halfRad; S.dir = dirAngle;
  return S;
}
function sdf3(S, x, y, z){
  const yr = x*S.nx + y*S.ny, xr = Math.abs(x*(-S.ny) + y*S.nx);
  const k = (S.cosA*xr > S.sinA*yr) ? (xr*S.sinA + yr*S.cosA) : Math.sqrt(xr*xr + yr*yr);
  const k2 = Math.sqrt(xr*xr + yr*yr);
  const pp = xr*xr + yr*yr + z*z, ra2 = S.ra*S.ra, two = 2*S.ra;
  const d1 = Math.sqrt(Math.max(0, pp + ra2 - two*k)) - S.rb;
  const d2 = Math.sqrt(Math.max(0, pp + ra2 - two*k2)) - S.rb;
  let dx = x - S.c1x, dy = y - S.c1y; const dS = Math.sqrt(dx*dx + dy*dy + z*z) - S.capR;
  dx = x - S.c2x; dy = y - S.c2y; const dE = Math.sqrt(dx*dx + dy*dy + z*z) - S.capR;
  const h = clamp(0.5*(dE - dS)/S.blend + 0.5, 0, 1);
  const smin = dE*(1-h) + dS*h - S.blend*h*(1-h);
  return Math.max(Math.min(smin, d1), d2);
}

// ---------- WebGL helpers ----------
function mkShader(gl, type, src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);
  if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error('shader: '+gl.getShaderInfoLog(s)+'\n'+src);return s;}
function mkProg(gl, vs, fs, names){const p=gl.createProgram();gl.attachShader(p,mkShader(gl,gl.VERTEX_SHADER,vs));
  gl.attachShader(p,mkShader(gl,gl.FRAGMENT_SHADER,fs));gl.linkProgram(p);
  if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error('link: '+gl.getProgramInfoLog(p));
  const u={};for(const n of names)u[n]=gl.getUniformLocation(p,n);return {p,u};}
function loadTex(gl, src, opts){const t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,(opts&&opts.repeat)?gl.REPEAT:gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,(opts&&opts.repeat)?gl.REPEAT:gl.CLAMP_TO_EDGE);
  const im=new Image();im.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,im);};im.src=src;return t;}

const QUAD_VS = 'attribute vec2 p;varying vec2 vUv;void main(){vUv=p*0.5+0.5;gl_Position=vec4(p,0.0,1.0);}';

// Scene: room disc + aura (palette-recoloured glow of the SDF) + flurry blobs. Rendered to an FBO.
const SCENE_FS = `precision highp float;
varying vec2 vUv;
uniform vec2 uRes,uC,uP; uniform float uR,uRf,uTime,uE,uB,uGlow,uGrain,uFlurry,uSpatial,uPuckR,uHaloGain;
uniform vec3 cBlue,cTeal; uniform vec4 uPulseE[6],uPulseP[6];
uniform vec2 uN; uniform float uSinA,uCosA,uRa,uRb,uCapR,uBlend; uniform vec2 uC1,uC2;
uniform vec3 cDeep,cCyan,cLight,cWhite,cRoom,cRoomEdge;
uniform sampler2D uNoise;
uniform vec4 uBlob[24];
float sdf(vec2 p){
  float yr=dot(p,uN); float xr=abs(dot(p,vec2(-uN.y,uN.x)));
  float k=(uCosA*xr>uSinA*yr)?(xr*uSinA+yr*uCosA):length(vec2(xr,yr));
  float k2=length(vec2(xr,yr)); float pp=xr*xr+yr*yr;
  float d1=sqrt(max(0.0,pp+uRa*uRa-2.0*uRa*k))-uRb;
  float d2=sqrt(max(0.0,pp+uRa*uRa-2.0*uRa*k2))-uRb;
  float dS=length(p-uC1)-uCapR; float dE=length(p-uC2)-uCapR;
  float h=clamp(0.5*(dE-dS)/uBlend+0.5,0.0,1.0);
  float sm=mix(dE,dS,h)-uBlend*h*(1.0-h);
  return max(min(sm,d1),d2);
}
void main(){
  vec2 px=vec2(vUv.x*uRes.x,(1.0-vUv.y)*uRes.y);
  float dC=length(px-uC); float rr=dC/uR;
  float inRoom=1.0-smoothstep(uR-1.0,uR+1.0,dC);
  vec2 f=(px-uP)/(2.0*uRf);              // field space (rim at 0.5)
  float d=sdf(f); float fr=length(f);
  float px2f=1.0/(2.0*uRf);              // one pixel in field units
  // room disc: dark navy-teal, darker toward the rim and the bottom (glass sphere shading)
  vec3 col=mix(cRoom,cRoomEdge,smoothstep(0.35,1.0,rr));
  col*=1.0-0.28*smoothstep(-0.2,1.0,(px.y-uC.y)/uR);
  // aura: the SDF interior glows (palette ramp), soft spill outside, white core at the puck
  float inside=1.0-smoothstep(-6.0*px2f,2.0*px2f,d);
  float spill=exp(-max(d,0.0)*16.0);
  float core=exp(-fr*7.0);
  float g=inside*(0.11+0.12*exp(-fr*3.0))+spill*0.12*(1.0-inside);
  g+=exp(-abs(d)*30.0)*0.16*inside;
  g*=0.80+0.45*uE;
  g+=core*0.22*(1.0+0.5*uB);
  float fl=0.0; vec3 flc=vec3(0.0);
  for(int i=0;i<24;i++){vec4 bb=uBlob[i]; float q=length(f-bb.xy)/max(bb.z,0.001); float w=bb.w*(1.0-smoothstep(0.1,1.0,q)); fl+=w; flc+=w*mix(cBlue,cTeal,mod(float(i),2.0));}
  g+=fl*0.10*uFlurry*inside;
  g*=uGlow;
  vec3 aura=mix(mix(cDeep,cCyan,clamp(g*2.0,0.0,1.0)),cWhite,clamp((g-0.5)*2.0,0.0,1.0));
  // classic (sound all around): the lit disc is a smooth saturated blue, brighter toward the puck, no sparkle needed
  float gc=inside*(0.55+0.30*exp(-fr*2.2))+spill*0.25*(1.0-inside);
  gc*=0.85+0.3*uE;
  vec3 classic=mix(cBlue,mix(cCyan,cWhite,0.35),clamp((gc-0.55)*2.2,0.0,1.0));
  classic=mix(classic,flc/max(fl,0.001),clamp(fl*0.35*uFlurry,0.0,0.45));
  classic+=cWhite*core*0.6*(1.0+0.4*uB);
  vec3 auraS=mix(col,aura,smoothstep(0.0,0.55,g));
  vec3 auraC=mix(col,classic,smoothstep(0.0,0.5,gc));
  auraC=mix(auraC,auraC*1.0+flc*0.12*uFlurry,inside);
  col=mix(auraC,auraS,uSpatial);
  // the pulse field around the puck (Aurora PulseField): several soft, wobbly white blobs, each driven by one
  // frequency band, drifting around the puck and layered translucently (screen blend)
  float acc=0.0;
  for(int k=0;k<6;k++){ vec4 pe=uPulseE[k]; vec4 ph=uPulseP[k];
    vec2 c=uP+pe.xy*uPuckR; vec2 dv=px-c; float dd=length(dv)/max(uPuckR,1.0); float th=atan(dv.y,dv.x);
    float rr=pe.z*(1.0+ph.z*cos(2.0*th+ph.x)+ph.w*cos(3.0*th+ph.y));
    float cov=1.0-smoothstep(rr-0.22,rr+0.22,dd);
    acc=acc+cov*pe.w*(1.0-acc); }
  acc*=uHaloGain*mix(0.75,1.0,uSpatial);
  col=mix(col,mix(cWhite,vec3(1.0),0.5),clamp(acc,0.0,0.97));
  // glow bleeding past the room rim (the lobes light up the edge)
  float outer=exp(-max(dC-uR,0.0)/(0.05*uR))*(1.0-inRoom)*smoothstep(0.0,0.4,g)*0.35;
  col+=cCyan*outer;
  // grain (Aurora NoiseRGB)
  float n=texture2D(uNoise,px/96.0+vec2(uTime*0.013,-uTime*0.009)).r;
  col*=1.0+(n-0.5)*0.22*uGrain;
  // glass room rim: faint bright ring + darker just inside
  col+=vec3(0.10,0.22,0.28)*smoothstep(0.90,0.985,rr)*(1.0-smoothstep(0.985,1.0,rr));
  col=mix(col,cCyan*outer,1.0-inRoom);
  gl_FragColor=vec4(col,1.0);
}`;

// Particles: positions from the CPU sim (field space); pulse displacement + size/brightness from the pulse texture
const PART_VS = `precision highp float;
attribute vec2 aPos,aPrev; attribute float aSeed; uniform float uLerp;
uniform vec2 uRes,uP,uN; uniform float uRf,uDPR,uPtScale,uHead,uPScale,uPEmit,uPGain,uPRows;
uniform sampler2D uPulse;
varying float vCol;
void main(){
  vec2 p=mix(aPrev,aPos,uLerp);
  float dist=length(p);
  float ang=(dist>0.001)?atan(dot(p,vec2(-uN.y,uN.x)),dot(p,uN)):0.0;  // 0 at the notch, +-pi at the back
  float u=0.5+0.5*ang/3.14159265;        // mirrored spectrum: bass at the notch, treble behind
  float v=uPScale*abs(dist-uPEmit);
  float row=uHead+v;                     // newest row at uHead, older rows further along +v (REPEAT)
  float s1=texture2D(uPulse,vec2(u,row+1.0/32.0)).r;
  float s2=texture2D(uPulse,vec2(u,row-1.0/32.0)).r;
  float amt=sqrt(max(1.0-v,0.0))*(s1-s2)*uPGain;
  vec2 disp=(dist>0.001?p/dist:vec2(0.0))*0.05*amt;
  vec2 sp=uP+(p+disp)*2.0*uRf;
  vec2 cs=sp/uRes*2.0-1.0; cs.y=-cs.y;
  gl_Position=vec4(cs,0.0,1.0);
  float a=abs(amt)*2.0;
  gl_PointSize=(0.9+min(a*1.2,2.0))*uDPR*uPtScale*(0.7+0.6*aSeed);
  vCol=0.75+a*0.9;
}`;
const PART_FS = `precision mediump float; varying float vCol; uniform float uPtGain;
void main(){vec2 q=gl_PointCoord*2.0-1.0; float r=length(q); float a=max(0.0,1.0-r); gl_FragColor=vec4(vec3(0.66,0.92,1.0)*vCol*a*0.5*uPtGain,1.0);}`;

// Composite: scene + glass orbs (puck / handles / notch) refracting the scene with the lens table + gloss maps
const BLIT_FS = `precision highp float; varying vec2 vUv; uniform sampler2D uScene; void main(){gl_FragColor=texture2D(uScene,vUv);}`;

const ORB_VS = `attribute vec2 p; uniform vec2 uRes,uCenter,uSize; uniform float uRot; varying vec2 vL;
void main(){vL=p; vec2 r=vec2(p.x*uSize.x,p.y*uSize.y); float c=cos(uRot),s=sin(uRot); vec2 q=vec2(r.x*c-r.y*s,r.x*s+r.y*c);
  vec2 sp=uCenter+q; vec2 cs=sp/uRes*2.0-1.0; cs.y=-cs.y; gl_Position=vec4(cs,0.0,1.0);}`;
// uMode 0 = orb (circle), 1 = capsule (vL.y stretched); uRadius = circle radius px (capsule: half width)
const ORB_FS = `precision highp float; varying vec2 vL;
uniform sampler2D uScene,uGloss,uGlyph; uniform vec2 uRes,uCenter,uSize; uniform float uRot,uRadius,uAlpha,uGlyphOn,uHalo,uDarken,uMode,uTint;
uniform sampler2D uLensTex;
float lens(float r){return texture2D(uLensTex,vec2(clamp(r,0.0,1.0)*(63.0/64.0)+0.5/64.0,0.5)).r;}
void main(){
  vec2 l=vL; vec2 q=l;
  if(uMode>2.5){ float rh=length(l); float sh=uHalo*smoothstep(1.0,0.55,rh); gl_FragColor=vec4(0.02,0.08,0.14,sh); return; }
  if(uMode>1.5){ float rh=length(l); gl_FragColor=vec4(1.0,1.0,1.0,uHalo*(1.0-smoothstep(0.0,1.0,rh))*0.5); return; }
  float along=0.0;
  if(uMode>0.5){ float ext=uSize.y/uSize.x-1.0; along=clamp(l.y*(uSize.y/uSize.x),-ext,ext); q=vec2(l.x,l.y*(uSize.y/uSize.x)-along); }
  float r=length(q);
  float aa=1.0-smoothstep(1.0-2.0/uRadius,1.0,r);
  if(aa<=0.0) discard;
  vec2 dir=(r>0.0001)?q/r:vec2(0.0);
  float off=lens(r)*2.0-0.5;              // Aurora Orb: lensTable*2-0.5
  float c=cos(uRot),s=sin(uRot);
  vec2 local=vec2(dir.x*off, along+dir.y*off)*uRadius;
  vec2 sp=uCenter+vec2(local.x*c-local.y*s,local.x*s+local.y*c);
  vec2 uv=sp/uRes; uv.y=1.0-uv.y;
  vec3 bg=texture2D(uScene,uv).rgb;
  bg=mix(bg,bg*vec3(0.30,0.62,1.0)+vec3(0.02,0.16,0.38),uTint);
  // gloss map: r darkens, g contrast, b adds (ShapeLayer Orb content)
  vec2 guv=vec2(0.5+0.5*q.x,0.5+0.5*q.y);
  vec3 g=texture2D(uGloss,guv).rgb;
  vec3 c1=clamp(bg-1.0+g.r*mix(1.0,0.75,uDarken)+ (1.0-g.r)*0.0,0.0,1.0);
  vec3 c2=clamp(1.0-(1.0-c1)/max(g.g,0.05),0.0,1.0);
  vec3 col=clamp(c2+g.b,0.0,1.0);
  // bubble treatment: slightly darker interior, bright thin rim (the handles / notch look)
  float rim=smoothstep(0.74,0.90,r)*(1.0-smoothstep(0.93,1.0,r));
  col=mix(col,col*mix(1.0,0.72,uDarken),1.0-smoothstep(0.7,0.9,r));
  col+=vec3(0.9,0.98,1.0)*rim*uDarken*1.4;
  if(uGlyphOn>0.5){ vec4 gl=texture2D(uGlyph,vec2(0.5+0.5*q.x*1.18,0.5+0.5*q.y*1.18)); col=mix(col,vec3(1.0),gl.a*0.95); }
  gl_FragColor=vec4(col,aa*uAlpha);
}`;

function create(wrap, opts){
  opts = opts || {};
  const cfg = JSON.parse(JSON.stringify(DEFAULTS)); if(navigator.maxTouchPoints>0 && !(opts.cfg&&opts.cfg.particles)) cfg.particles = 12000; if(opts.cfg) Object.assign(cfg, opts.cfg);
  const post = opts.post || (()=>{});
  const vizFn = opts.viz || null;
  wrap.style.position = 'relative'; wrap.style.overflow = 'hidden'; wrap.style.touchAction = 'none';
  const glc = document.createElement('canvas'); glc.style.cssText = 'display:block;width:100%;';
  const ui = document.createElement('canvas'); ui.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none';
  wrap.appendChild(glc); wrap.appendChild(ui);
  const gl = glc.getContext('webgl', {alpha:false, antialias:false, premultipliedAlpha:false, preserveDrawingBuffer:true});
  if(!gl) throw new Error('WebGL unavailable');
  const ctx = ui.getContext('2d');
  const TEX = (opts.textures) || (typeof SYNG_TEX !== 'undefined' ? SYNG_TEX : {});

  // ----- programs -----
  const scene = mkProg(gl, QUAD_VS, SCENE_FS, ['uRes','uC','uP','uR','uRf','uTime','uE','uB','uGlow','uGrain','uFlurry','uN','uSinA','uCosA','uRa','uRb','uCapR','uBlend','uC1','uC2','cDeep','cCyan','cLight','cWhite','cRoom','cRoomEdge','uNoise','uBlob','uSpatial','uPuckR','uHaloGain','cBlue','cTeal','uPulseE','uPulseP']);
  const part = mkProg(gl, PART_VS, PART_FS, ['uRes','uP','uN','uRf','uDPR','uPtScale','uHead','uPScale','uPEmit','uPGain','uPRows','uPulse','uPtGain','uLerp']);
  const blit = mkProg(gl, QUAD_VS, BLIT_FS, ['uScene']);
  const orb = mkProg(gl, ORB_VS, ORB_FS, ['uScene','uGloss','uGlyph','uRes','uCenter','uSize','uRot','uRadius','uAlpha','uGlyphOn','uHalo','uDarken','uMode','uTint','uLensTex']);
  const quad = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, quad); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
  const texNoise = loadTex(gl, TEX.noise, {repeat:true});
  const texOrbB = loadTex(gl, TEX.orbB), texOrbC = loadTex(gl, TEX.orbC), texGlyph = loadTex(gl, TEX.airplay);
  const lensTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, lensTex);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.LUMINANCE,64,1,0,gl.LUMINANCE,gl.UNSIGNED_BYTE,new Uint8Array(LENS.map(v=>Math.round(v*255))));
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  // pulse texture: 64 angle columns x PR time rows (luminance), ring buffer
  const PR = 128, PW = 64; /* power-of-two: WebGL1 needs it for REPEAT wrap */ const pulseTex = gl.createTexture(); const pulseBuf = new Uint8Array(PW*PR);
  gl.bindTexture(gl.TEXTURE_2D, pulseTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, PW, PR, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, pulseBuf);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  let pulseHead = 0, pulseAcc = 0;
  // FBO
  const fbo = gl.createFramebuffer(); const sceneTex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, sceneTex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  // ----- particles (CPU sim, exact port of updateParticle) -----
  let NP = 0, P = null, V = null, O = null, L = null, seeds = null, posBuf = gl.createBuffer(), prevBuf = gl.createBuffer(), seedBuf = gl.createBuffer(), pos2 = null, prev2 = null, posDirty = true;
  function rand1(x){ const s = Math.sin(x)*43758.5453; return s - Math.floor(s); }
  function spawn(i){ const t = i*0.125; P[i*3] = rand1(t)-0.5; P[i*3+1] = rand1(t+1)-0.5; P[i*3+2] = rand1(t+2)-0.5;
    V[i*3]=V[i*3+1]=V[i*3+2]=0; O[i*3]=P[i*3];O[i*3+1]=P[i*3+1];O[i*3+2]=P[i*3+2]; L[i] = 10+90*rand1(i*0.37+1.3); }
  function allocParticles(n){ NP = n; P = new Float32Array(n*3); V = new Float32Array(n*3); O = new Float32Array(n*3); L = new Float32Array(n);
    pos2 = new Float32Array(n*2); prev2 = new Float32Array(n*2); seeds = new Float32Array(n); for(let i=0;i<n;i++){ spawn(i); seeds[i] = Math.random(); }
    gl.bindBuffer(gl.ARRAY_BUFFER, seedBuf); gl.bufferData(gl.ARRAY_BUFFER, seeds, gl.STATIC_DRAW); }
  allocParticles(cfg.particles);
  const S = {};
  function simStep(dt, simT){
    const eps = 0.001; prev2.set(pos2);
    for(let i=0;i<NP;i++){
      const ix=i*3; let x=P[ix], y=P[ix+1], z=P[ix+2], vx=V[ix], vy=V[ix+1], vz=V[ix+2];
      const life = L[i] - dt*0.05*60;   // the app decrements per frame
      let nx, ny, nz;
      if(life > 0){
        const px=x+vx, py=y+vy, pz=z+vz;
        const d0 = sdf3(S,px,py,pz);
        let gx = sdf3(S,px+eps,py,pz)-d0, gy = sdf3(S,px,py+eps,pz)-d0, gz = sdf3(S,px,py,pz+eps)-d0;
        const gl2 = Math.sqrt(gx*gx+gy*gy+gz*gz);
        if(gl2>1e-9){gx/=gl2;gy/=gl2;gz/=gl2;} else {gx=gy=gz=0;}
        const kk = (d0>0?1.2:0.5)*d0;
        const fx=-kk*gx, fy=-kk*gy, fz=-kk*gz;
        vx+=fx; vy+=fy; vz+=fz;
        nx = x+fx+vx; ny = y+fy+vy; nz = z+fz+vz;
        L[i] = life;
      } else {
        const s = Math.sin(life + simT*0.01);
        nx = O[ix]*s; ny = O[ix+1]*s; nz = O[ix+2]*s; L[i] = 100;
      }
      const len = Math.sqrt(nx*nx+ny*ny+nz*nz);
      if(len>1){nx/=len;ny/=len;nz/=len;}
      P[ix]=nx; P[ix+1]=ny; P[ix+2]=nz; V[ix]=vx*0.99; V[ix+1]=vy*0.99; V[ix+2]=vz*0.99;
      pos2[i*2]=nx; pos2[i*2+1]=ny;
    }
    posDirty = true;
  }

  // ----- state -----
  const st = {
    W:0, H:0, DPR:1, C:[0,0], R:1,
    puck:[0,0], puckT:[0,0], puckV:[0,0], grab:null, grabOff:[0,0], puckScale:1, puckScaleV:0,
    spatial:false, dir:0, dirT:0, dirV:0, half:0, halfT:0, halfV:0, weight:cfg.weightOmni, weightV:0,
    volume: cfg.volume, volT: cfg.volume,
    e:0.25, b:0, beat:0, spec:new Float32Array(24), rawSpec:new Float32Array(24), flux:new Float32Array(24), emaSpec:new Float32Array(24), seq:-1, fps:30, lastRowT:0, t:0,
    blobs:[], liftDot:null, pulseE:new Float32Array(24), pulseP:new Float32Array(24), bands:new Float32Array(6), pph:[],
  };
  for(let k=0;k<6;k++) st.pph.push({ang:Math.random()*TAU, w:(0.25+0.35*Math.random())*(k%2?1:-1), p1:Math.random()*TAU, p2:Math.random()*TAU, w1:(0.4+0.5*Math.random())*(k%2?-1:1), w2:0.3+0.4*Math.random(), off:0.10+0.12*Math.random()});
  for(let i=0;i<24;i++) st.blobs.push({x:(Math.random()-0.5)*0.8, y:(Math.random()-0.5)*0.8, r:0.36+Math.random()*0.09, a:0, vx:0, vy:0, life:Math.random()*10, ph:Math.random()*TAU});

  function resize(){
    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min((navigator.maxTouchPoints>0)?1.5:2, window.devicePixelRatio||1);
    const k = (st.W && W!==st.W) ? W/st.W : 1;   // the page re-flowed (fonts, scrollbar, first layout): keep the puck where it was
    if(k!==1 && st.puckInit){ st.puck[0]*=k; st.puck[1]*=k; st.puckT[0]*=k; st.puckT[1]*=k; if(st.liftDot){ st.liftDot[0]*=k; st.liftDot[1]*=k; } }
    st.W=W; st.H=H; st.DPR=DPR;
    glc.width = Math.round(W*DPR); glc.height = Math.round(H*DPR); glc.style.height = H+'px';
    ui.width = glc.width; ui.height = glc.height; ui.style.height = H+'px';
    gl.bindTexture(gl.TEXTURE_2D, sceneTex); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,glc.width,glc.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, sceneTex, 0); gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const R = W*0.385; st.R = R; st.C = [W/2, 0.045*W + R];
    if(!st.puckInit){ st.puck=[st.C[0],st.C[1]]; st.puckT=[st.C[0],st.C[1]]; st.puckInit=true; }
  }
  resize(); window.addEventListener('resize', resize);
  try{ new ResizeObserver(()=>{ if(wrap.clientWidth && wrap.clientWidth!==st.W) resize(); }).observe(wrap); }catch(e){}

  // ----- geometry helpers -----
  function fieldR(){ const d = Math.hypot(st.puck[0]-st.C[0], st.puck[1]-st.C[1]); return Math.max(st.R*0.2, st.R - d); }
  function puckR(){ return st.R*0.165*st.puckScale; }
  function handleR(){ return st.R*0.052; }
  function notchSize(){ return [st.R*0.042, st.R*0.115]; }   // half-width, half-length
  function dirVec(a){ return [Math.sin(a), -Math.cos(a)]; }
  function handlePos(angle){ const Rf = fieldR(); const v = dirVec(angle); const r = cfg.handleRadius*Rf; return [st.puck[0]+v[0]*r, st.puck[1]+v[1]*r]; }
  function liftDotPos(){ const r = puckR()+handleR()+st.R*0.015; return [st.puck[0], st.puck[1]-r]; }

  // ----- springs (UISpringTimingParameters mass 1, stiffness 218.566, damping 29.568 => critically damped) -----
  const K=218.566, Cd=29.568;
  function spring1(x, v, target, dt){ const h=1/240; let t=0; while(t<dt){ const a=-K*(x-target)-Cd*v; v+=a*h; x+=v*h; t+=h; } return [x,v]; }
  function springAngle(x, v, target, dt){ x = target + wrapPi(x-target); return spring1(x, v, target, dt); }

  // ----- input -----
  function evPos(e){ const r = glc.getBoundingClientRect(); return [e.clientX-r.left, e.clientY-r.top]; }
  function near(p, q, rad){ return Math.hypot(p[0]-q[0], p[1]-q[1]) <= rad; }
  function angleOf(p){ return Math.atan2(p[0]-st.puck[0], -(p[1]-st.puck[1])); }   // 0 = up, clockwise +
  let lastPost = 0, postTimer = null;
  function sendState(final){
    // the app's panner_v2 handle: x/y in room-circle units (y up), rotation CCW with 90 = up, surround_width = dot half-angle
    const ux = (st.puckT[0]-st.C[0])/st.R, uy = -(st.puckT[1]-st.C[1])/st.R;
    const body = { ux: Math.round(ux*1000)/1000, uy: Math.round(uy*1000)/1000,
      rot: Math.round(((90 - st.dirT*180/Math.PI)%360+360)%360*10)/10,
      width: Math.round(st.halfT*180/Math.PI*10)/10,
      radius: st.spatial ? cfg.radiusUnits : 0,
      spatial: st.spatial, snap: st.snap || '' };
    const now = performance.now();
    if(final || now-lastPost > 110){ lastPost = now; clearTimeout(postTimer); postTimer=null; post('/api/soundfield', body); }
    else if(!postTimer){ postTimer = setTimeout(()=>{postTimer=null; sendState(true);}, 120); }
    if(opts.onChange) opts.onChange(body);
  }
  function sendVolume(final){ const now=performance.now(); if(final || now-lastPost>110){ lastPost=now; post('/api/volume', {source:'all', volume: Math.round(st.volT*1000)/1000}); } }
  function clampPuck(p){ const dx=p[0]-st.C[0], dy=p[1]-st.C[1]; const d=Math.hypot(dx,dy), m=st.R*0.78; if(d>m) return [st.C[0]+dx/d*m, st.C[1]+dy/d*m]; return p; }
  function volFromPoint(p){ const a = Math.atan2(p[1]-st.C[1], p[0]-st.C[0]); // 0 = right, pi/2 = down
    const lo = Math.PI/2 - VOL_HALF, hi = Math.PI/2 + VOL_HALF; let aa = clamp(a, lo, hi);
    return (hi - aa)/(hi-lo); }   // left = quiet, clockwise to the right = loud
  const VOL_HALF = 52*Math.PI/180;
  glc.addEventListener('pointerdown', e=>{
    const p = evPos(e); glc.setPointerCapture(e.pointerId); e.preventDefault();
    const hr = handleR()*2.2;
    if(st.spatial){
      const n = handlePos(st.dir); if(near(p, n, hr*1.3)){ st.grab={t:'notch'}; return; }
      for(const sgn of [1,-1]){ const h = handlePos(st.dir+sgn*st.half); if(near(p,h,hr)){ st.grab={t:'dot', sgn}; return; } }
    } else {
      const ld = liftDotPos(); if(near(p, ld, hr)){ st.grab={t:'lift'}; return; }
    }
    if(near(p, st.puck, puckR()*1.25)){ st.grab={t:'puck'}; st.grabOff=[st.puck[0]-p[0], st.puck[1]-p[1]]; return; }
    const d = Math.hypot(p[0]-st.C[0], p[1]-st.C[1]); const a = Math.atan2(p[1]-st.C[1], p[0]-st.C[0]);
    if(cfg.showVolume && d > st.R*0.92 && Math.abs(a-Math.PI/2) < VOL_HALF+0.1){ st.grab={t:'vol'}; st.volT = volFromPoint(p); sendVolume(false); }
  });
  glc.addEventListener('pointermove', e=>{
    if(!st.grab) return; const p = evPos(e); const g = st.grab;
    if(g.t==='puck'){ st.puckT = clampPuck([p[0]+st.grabOff[0], p[1]+st.grabOff[1]]);
      // snap-to-cell: within snapFrac*R of a speaker ring the puck magnetises onto it (the app's SnappingEllipse)
      st.snap = null;
      if(cfg.snapToCell && opts.rings){ const rl = opts.rings()||[]; let best=null, bd=cfg.snapFrac*st.R;
        for(const rg of rl){ if(!rg[3]) continue; const d=Math.hypot(rg[0]-st.puckT[0], rg[1]-st.puckT[1]); if(d<bd){ bd=d; best=rg; } }
        if(best){ st.puckT=[best[0],best[1]]; st.snap=best[3]; } }
      sendState(false); }
    else if(g.t==='lift'){ const Rf = fieldR(); const dd = Math.hypot(p[0]-st.puck[0], p[1]-st.puck[1]);
      st.liftDot = p;
      if(dd > cfg.liftFrac*Rf){ st.spatial = true; st.dir = st.dirT = angleOf(p); st.halfT = cfg.defaultHalfDeg*Math.PI/180; st.half = Math.min(st.halfT, 0.35); st.halfV = 0;
        st.grab = {t:'notch'}; st.liftDot=null; sendState(false); } }
    else if(g.t==='notch'){ st.dirT = angleOf(p); sendState(false); }
    else if(g.t==='dot'){ const dd = Math.hypot(p[0]-st.puck[0], p[1]-st.puck[1]); const Rf = fieldR();
      if(dd < cfg.collapseFrac*Rf){ st.spatial=false; st.grab=null; sendState(true); return; }
      let h = Math.abs(wrapPi(angleOf(p) - st.dirT)); st.halfT = clamp(h, cfg.minHalfDeg*Math.PI/180, cfg.maxHalfDeg*Math.PI/180); sendState(false); }
    else if(g.t==='vol'){ st.volT = volFromPoint(p); sendVolume(false); }
  });
  const up = e=>{ if(!st.grab) return; const g=st.grab; st.grab=null; st.liftDot=null;
    if(g.t==='vol') sendVolume(true); else if(g.t!=='lift') sendState(true); };
  glc.addEventListener('pointerup', up); glc.addEventListener('pointercancel', up);

  // ----- audio feed -----
  function pullViz(){ if(!vizFn) return; const v = vizFn(); if(!v) return;
    st.e += ((v.energy||0) - st.e)*0.35; st.b += ((v.bass||0) - st.b)*0.4;
    const sp = v.spec||[]; for(let i=0;i<24;i++){ const t = sp.length? sp[Math.min(sp.length-1, Math.floor(i*sp.length/24))] : 0; st.rawSpec[i]=t; st.spec[i] += (t - st.spec[i])*0.5; }
    const isNew = (v.seq==null) ? (sp.length>0) : (v.seq!==st.seq);
    if(!isNew) return;
    st.seq = v.seq==null ? st.seq : v.seq;
    if(v.fps) st.fps += (v.fps - st.fps)*0.2;
    const fx = v.flux;
    for(let i=0;i<24;i++){ let f;
      if(fx && fx.length){ f = fx[Math.min(fx.length-1, Math.floor(i*fx.length/24))]; }
      else { f = Math.max(0, st.rawSpec[i]-st.emaSpec[i])*3.0; st.emaSpec[i] += (st.rawSpec[i]-st.emaSpec[i])*0.12; }
      st.flux[i] = Math.min(1, f); }
    st.beat = Math.max(st.beat*0.8, v.beat!=null ? v.beat : Math.min(1, (st.flux[1]+st.flux[2]+st.flux[3])/3*1.6));
    writePulseRow(); st.lastRowT = performance.now(); }
  function writePulseRow(){ const row = pulseHead; const nb = 24;
    for(let c=0;c<PW;c++){ const u = c/(PW-1); const bi = Math.abs(u*2-1)*(nb-1); const i0=Math.floor(bi), i1=Math.min(nb-1,i0+1), f=bi-i0;
      const val = (st.flux[i0]*(1-f)+st.flux[i1]*f)*(1-cfg.pulseJitter*0.5+cfg.pulseJitter*Math.random()); pulseBuf[row*PW+c] = clamp(Math.round(val*255),0,255); }
    gl.bindTexture(gl.TEXTURE_2D, pulseTex); gl.texSubImage2D(gl.TEXTURE_2D,0,0,row,PW,1,gl.LUMINANCE,gl.UNSIGNED_BYTE,pulseBuf.subarray(row*PW,row*PW+PW));
    pulseHead = (pulseHead - 1 + PR) % PR; }

  // ----- frame -----
  let last = null, simAcc = 0;
  function frame(now){
    requestAnimationFrame(frame);
    if(wrap.clientWidth && wrap.clientWidth!==st.W) resize();
    if(last===null) last = now; let dt = Math.min(0.05, (now-last)/1000); last = now; st.t += dt;
    pullViz();
    // springs
    let r = spring1(st.puck[0], st.puckV[0], st.puckT[0], dt); st.puck[0]=r[0]; st.puckV[0]=r[1];
    r = spring1(st.puck[1], st.puckV[1], st.puckT[1], dt); st.puck[1]=r[0]; st.puckV[1]=r[1];
    r = spring1(st.puckScale, st.puckScaleV, (st.grab&&st.grab.t==='puck')?1.1:1.0, dt); st.puckScale=r[0]; st.puckScaleV=r[1];
    r = springAngle(st.dir, st.dirV, st.dirT, dt); st.dir=r[0]; st.dirV=r[1];
    r = spring1(st.half, st.halfV, st.halfT, dt); st.half=r[0]; st.halfV=r[1];
    r = spring1(st.weight, st.weightV, st.spatial?cfg.weightSpatial:cfg.weightOmni, dt); st.weight=r[0]; st.weightV=r[1];
    st.volume += (st.volT-st.volume)*Math.min(1,dt*14);
    // particle sim at fixed rate
    prepSDF(S, st.dir, st.spatial?st.half:Math.PI, st.weight, cfg);
    simAcc += dt; const h = 1/cfg.simHz; let steps=0; while(simAcc >= h && steps<2){ simStep(h, st.t); simAcc -= h; steps++; } if(simAcc>h) simAcc=h*0.99;
    // pulse rows
    if(performance.now()-st.lastRowT > 400){ pulseAcc += dt*30; while(pulseAcc>=1){ for(let i=0;i<24;i++) st.flux[i]*=0.5; writePulseRow(); pulseAcc-=1; } }
    // pulse field elements: 6 bands (bass -> treble), fast attack / slow release, wobbling + drifting around the puck
    for(let k=0;k<6;k++){ let lv=0; for(let i=k*4;i<k*4+4;i++) lv+=0.55*st.spec[i]+0.9*st.flux[i]; lv=Math.min(1,lv/4);
      const tgt=lv+0.6*st.beat*(k<2?1:0.4); st.bands[k]+= (tgt>st.bands[k]?0.55:0.10)*(tgt-st.bands[k]);
      const q=st.pph[k]; q.ang+=q.w*dt; q.p1+=q.w1*dt; q.p2+=q.w2*dt;
      const r=1.18+0.22*k/5+0.9*st.bands[k];
      st.pulseE[k*4]=Math.cos(q.ang)*q.off*(0.6+st.bands[k]); st.pulseE[k*4+1]=Math.sin(q.ang)*q.off*(0.6+st.bands[k]); st.pulseE[k*4+2]=r; st.pulseE[k*4+3]=0.22+0.30*st.bands[k];
      st.pulseP[k*4]=q.p1; st.pulseP[k*4+1]=q.p2; st.pulseP[k*4+2]=0.10+0.14*st.bands[k]; st.pulseP[k*4+3]=0.06+0.10*st.bands[k]; }
    // flurry blobs
    for(const b of st.blobs){ b.life -= dt; if(b.life<=0){ b.life = 10*(0.5+Math.random()); const a=Math.random()*TAU, rr=Math.random()*0.42; b.x=Math.cos(a)*rr; b.y=Math.sin(a)*rr; b.r=0.36+Math.random()*0.09; b.ph=Math.random()*TAU; const va=Math.random()*TAU; b.vx=Math.cos(va)*0.02; b.vy=Math.sin(va)*0.02; }
      b.x += b.vx*dt; b.y += b.vy*dt; const lf = clamp(b.life/2,0,1)*clamp((10-b.life)/2,0,1); b.a = lf*(0.55+0.45*Math.sin(st.t*0.7+b.ph)); }
    draw();
  }
  function draw(){
    const W=st.W, H=st.H, D=st.DPR, RW=glc.width, RH=glc.height;
    const Rf = fieldR(); const px = [st.puck[0]*D, st.puck[1]*D];
    // ---- scene to FBO ----
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.viewport(0,0,RW,RH); gl.disable(gl.BLEND);
    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(scene.p); gl.bindBuffer(gl.ARRAY_BUFFER, quad); const ap=gl.getAttribLocation(scene.p,'p'); gl.enableVertexAttribArray(ap); gl.vertexAttribPointer(ap,2,gl.FLOAT,false,0,0);
    gl.uniform2f(scene.u.uRes,RW,RH); gl.uniform2f(scene.u.uC,st.C[0]*D,st.C[1]*D); gl.uniform2f(scene.u.uP,px[0],px[1]);
    gl.uniform1f(scene.u.uR,st.R*D); gl.uniform1f(scene.u.uRf,Rf*D); gl.uniform1f(scene.u.uTime,st.t);
    gl.uniform1f(scene.u.uE,st.e); gl.uniform1f(scene.u.uB,st.b); gl.uniform1f(scene.u.uGlow,cfg.glowGain); gl.uniform1f(scene.u.uGrain,cfg.grain); gl.uniform1f(scene.u.uFlurry,cfg.flurry);
    gl.uniform2f(scene.u.uN,S.nx,S.ny); gl.uniform1f(scene.u.uSinA,S.sinA); gl.uniform1f(scene.u.uCosA,S.cosA); gl.uniform1f(scene.u.uRa,S.ra); gl.uniform1f(scene.u.uRb,S.rb);
    gl.uniform1f(scene.u.uCapR,S.capR); gl.uniform1f(scene.u.uBlend,S.blend); gl.uniform2f(scene.u.uC1,S.c1x,S.c1y); gl.uniform2f(scene.u.uC2,S.c2x,S.c2y);
    const c=cfg.colors; gl.uniform3fv(scene.u.cBlue,c.blue); gl.uniform3fv(scene.u.cTeal,c.teal);
    gl.uniform1f(scene.u.uSpatial,1-clamp((st.weight-cfg.weightSpatial)/(cfg.weightOmni-cfg.weightSpatial),0,1)); gl.uniform1f(scene.u.uPuckR,puckR()*D); gl.uniform1f(scene.u.uHaloGain,cfg.haloGain);
    gl.uniform4fv(scene.u.uPulseE,st.pulseE); gl.uniform4fv(scene.u.uPulseP,st.pulseP);
    gl.uniform3fv(scene.u.cDeep,c.cyanDeep); gl.uniform3fv(scene.u.cCyan,c.cyan); gl.uniform3fv(scene.u.cLight,c.cyanLight); gl.uniform3fv(scene.u.cWhite,c.white); gl.uniform3fv(scene.u.cRoom,c.room); gl.uniform3fv(scene.u.cRoomEdge,c.roomEdge);
    const bl = new Float32Array(24*4); st.blobs.forEach((b,i)=>{bl[i*4]=b.x;bl[i*4+1]=b.y;bl[i*4+2]=b.r;bl[i*4+3]=b.a;}); gl.uniform4fv(scene.u.uBlob, bl);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texNoise); gl.uniform1i(scene.u.uNoise,0);
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    // particles (additive)
    if(posDirty){ gl.bindBuffer(gl.ARRAY_BUFFER,posBuf); gl.bufferData(gl.ARRAY_BUFFER,pos2,gl.DYNAMIC_DRAW); gl.bindBuffer(gl.ARRAY_BUFFER,prevBuf); gl.bufferData(gl.ARRAY_BUFFER,prev2,gl.DYNAMIC_DRAW); posDirty=false; }
    gl.useProgram(part.p); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE,gl.ONE);
    gl.bindBuffer(gl.ARRAY_BUFFER,posBuf); const aPos=gl.getAttribLocation(part.p,'aPos'); gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos,2,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,prevBuf); const aPrev=gl.getAttribLocation(part.p,'aPrev'); gl.enableVertexAttribArray(aPrev); gl.vertexAttribPointer(aPrev,2,gl.FLOAT,false,0,0);
    gl.uniform1f(part.u.uLerp, clamp(simAcc*cfg.simHz,0,1));
    gl.bindBuffer(gl.ARRAY_BUFFER,seedBuf); const aSeed=gl.getAttribLocation(part.p,'aSeed'); gl.enableVertexAttribArray(aSeed); gl.vertexAttribPointer(aSeed,1,gl.FLOAT,false,0,0);
    gl.uniform2f(part.u.uRes,RW,RH); gl.uniform2f(part.u.uP,px[0],px[1]); gl.uniform2f(part.u.uN,S.nx,S.ny); gl.uniform1f(part.u.uRf,Rf*D); gl.uniform1f(part.u.uDPR,D);
    gl.uniform1f(part.u.uPtScale,cfg.pointScale*Math.max(0.7,st.R/180)); gl.uniform1f(part.u.uHead,(pulseHead+1-clamp((performance.now()-st.lastRowT)/1000*st.fps,0,1))/PR); gl.uniform1f(part.u.uPScale,cfg.pulseScale); gl.uniform1f(part.u.uPEmit,cfg.pulseEmitter); gl.uniform1f(part.u.uPGain,cfg.pulseGain); gl.uniform1f(part.u.uPtGain,cfg.pointGain); gl.uniform1f(part.u.uPRows,PR);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,pulseTex); gl.uniform1i(part.u.uPulse,0);
    if(cfg.classicParticles || st.spatial || st.weight<cfg.weightOmni-0.05) gl.drawArrays(gl.POINTS,0,NP);
    gl.disableVertexAttribArray(aSeed); gl.disableVertexAttribArray(aPrev);
    // ---- composite ----
    gl.bindFramebuffer(gl.FRAMEBUFFER,null); gl.viewport(0,0,RW,RH); gl.disable(gl.BLEND);
    gl.useProgram(blit.p); gl.bindBuffer(gl.ARRAY_BUFFER,quad); const bp=gl.getAttribLocation(blit.p,'p'); gl.enableVertexAttribArray(bp); gl.vertexAttribPointer(bp,2,gl.FLOAT,false,0,0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,sceneTex); gl.uniform1i(blit.u.uScene,0); gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    // orbs
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(orb.p); const op=gl.getAttribLocation(orb.p,'p'); gl.enableVertexAttribArray(op); gl.vertexAttribPointer(op,2,gl.FLOAT,false,0,0);
    gl.uniform2f(orb.u.uRes,RW,RH);
    gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D,lensTex); gl.uniform1i(orb.u.uLensTex,3);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,sceneTex); gl.uniform1i(orb.u.uScene,0);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D,texGlyph); gl.uniform1i(orb.u.uGlyph,2);
    function drawOrb(cx,cy,rad,glossTex,o){ o=o||{}; gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D,glossTex); gl.uniform1i(orb.u.uGloss,1);
      const mode = o.shadow ? 3 : (o.halo ? 2 : (o.capsule ? 1 : 0)); const len = o.len||rad; const ext = o.halo ? 3.4 : 1.0;
      gl.uniform2f(orb.u.uCenter,cx*D,cy*D); gl.uniform2f(orb.u.uSize,rad*D*ext,(mode===1?len:rad)*D*ext); gl.uniform1f(orb.u.uRot,o.rot||0);
      gl.uniform1f(orb.u.uRadius,rad*D); gl.uniform1f(orb.u.uAlpha,o.alpha==null?1:o.alpha); gl.uniform1f(orb.u.uGlyphOn,o.glyph?1:0);
      gl.uniform1f(orb.u.uHalo,o.halo||o.shadow||0); gl.uniform1f(orb.u.uDarken,o.darken||0); gl.uniform1f(orb.u.uMode,mode); gl.uniform1f(orb.u.uTint,o.tint||0);
      gl.drawArrays(gl.TRIANGLE_STRIP,0,4); }
    const hr=handleR();
    if(st.spatial){
      const vis = clamp((st.weight-cfg.weightSpatial)/(cfg.weightOmni-cfg.weightSpatial),0,1); // 1 = still omni
      const alpha = 1-vis;
      for(const sgn of [1,-1]){ const hp=handlePos(st.dir+sgn*st.half); const grabbed = st.grab&&st.grab.t==='dot'&&st.grab.sgn===sgn;
        if(grabbed) drawOrb(hp[0],hp[1],hr,texOrbC,{halo:0.55}); drawOrb(hp[0],hp[1],hr,texOrbC,{alpha,darken:0.35}); }
      const np=handlePos(st.dir); const ns=notchSize(); const grabbedN = st.grab&&st.grab.t==='notch';
      if(grabbedN) drawOrb(np[0],np[1],ns[1]*0.8,texOrbC,{halo:0.55});
      drawOrb(np[0],np[1],ns[0],texOrbC,{capsule:true,len:ns[1],rot:st.dir,alpha,darken:0.4});
    } else {
      const ld = st.liftDot || liftDotPos(); const grabbed = st.grab&&st.grab.t==='lift';
      if(grabbed) drawOrb(ld[0],ld[1],hr,texOrbC,{halo:0.55});
      drawOrb(ld[0],ld[1],hr*0.85,texOrbC,{darken:0.5});
    }
    drawOrb(st.puck[0],st.puck[1],puckR()*1.32,texOrbB,{shadow:0.75}); drawOrb(st.puck[0],st.puck[1],puckR(),texOrbB,{glyph:true,tint:0.85,darken:0.25});
    // ---- 2D UI: volume ticks ----
    ctx.setTransform(D,0,0,D,0,0); ctx.clearRect(0,0,W,H);
    if(cfg.showVolume){ const Rv=st.R*1.10, tl=st.R*0.065; const n=44; const cur=st.volume;
      for(let i=0;i<=n;i++){ const f=i/n; const a=Math.PI/2+VOL_HALF-f*2*VOL_HALF; const on=f<=cur+1e-6; const isCur=Math.abs(f-cur)<=0.5/n;
        const r0=Rv, r1=Rv+tl*(isCur?1.6:1); const ca=Math.cos(a), sa=Math.sin(a);
        ctx.strokeStyle = on? 'rgba(255,255,255,'+(isCur?1:0.85)+')' : 'rgba(255,255,255,0.28)'; ctx.lineWidth = isCur?2.2:1.6; ctx.lineCap='round';
        ctx.beginPath(); ctx.moveTo(st.C[0]+ca*r0, st.C[1]+sa*r0); ctx.lineTo(st.C[0]+ca*r1, st.C[1]+sa*r1); ctx.stroke(); } }
    if(cfg.showRings && opts.rings){ const rr=st.R*0.085; const rl=opts.rings()||[]; if(rl.listener){ ctx.strokeStyle='rgba(231,243,247,0.45)'; ctx.lineWidth=1.2; ctx.beginPath(); ctx.arc(rl.listener[0],rl.listener[1],4,0,TAU); ctx.stroke(); }
      for(const rg of rl){ const dC=Math.hypot(rg[0]-st.C[0],rg[1]-st.C[1]); if(dC>st.R-rr*0.3) continue;
        const snapped = st.snap && rg[3]===st.snap;
        ctx.strokeStyle=snapped?'rgba(235,250,255,0.75)':(rg[2]?'rgba(220,240,248,0.26)':'rgba(205,225,235,0.16)'); ctx.lineWidth=snapped?2.6:2.0; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr*(snapped?1.18:1),0,TAU); ctx.stroke();
        ctx.strokeStyle='rgba(205,225,235,0.09)'; ctx.lineWidth=1.2; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr*0.62,0,TAU); ctx.stroke();
        ctx.fillStyle='rgba(200,225,235,0.04)'; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr,0,TAU); ctx.fill(); } }
  }
  requestAnimationFrame(frame);

  const api = {
    cfg, state: st,
    resetParticles(){ for(let i=0;i<NP;i++) spawn(i); },
    setParticles(n){ allocParticles(n|0); },
    setSpatial(on, halfDeg, dirDeg){ st.spatial=!!on; if(halfDeg!=null) st.halfT = halfDeg*Math.PI/180; if(dirDeg!=null) st.dirT = dirDeg*Math.PI/180; if(st.half===0) st.half=st.halfT; },
    setPuckPolar(aimDeg, distFrac){ const a=aimDeg*Math.PI/180; const r=clamp(distFrac,0,0.78)*st.R; st.puckT=[st.C[0]+Math.sin(a)*r, st.C[1]-Math.cos(a)*r]; },
    setPuckUnits(ux,uy){ const p=clampPuck([st.C[0]+ux*st.R, st.C[1]-uy*st.R]); st.puckT=p; },
    setVolume(v){ st.volT = clamp(v,0,1); },
    center(){ st.puckT=[st.C[0],st.C[1]]; st.snap=null; },
  };
  return api;
}
window.SyngField = { create, DEFAULTS, sdf3, prepSDF };
})();

// ---- the sound field (SyngField: faithful port of the Syng app's ProtoTools/Aurora field) ----
let fieldData=null, FIELD=null, fieldTouched=0;
function setField(fleet,room){fieldData={fleet,room};syncField();}
function syncField(){
  if(!FIELD||!fieldData)return; const room=fieldData.room, fld=room&&room.field; if(!fld)return;
  const st=FIELD.state; if(st.grab||performance.now()-fieldTouched<2500)return;
  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);
  FIELD.state.snap = fld.snap || null;
  FIELD.setSpatial(!!fld.spatial, Math.max(FIELD.cfg.minHalfDeg,Math.min(FIELD.cfg.maxHalfDeg,fld.width!=null?fld.width:95)), 90-(fld.rot!=null?fld.rot:90));
}
function initField(){
  const wrap=$('#fieldwrap'); if(!wrap)return;
  const toUnits=(xm,ym)=>{const fd=fieldData; const ref=(fd&&fd.room&&fd.room.reference&&fd.room.reference.radius_m)?fd.room.reference:{x_m:0,y_m:0,radius_m:1.15,rotation_deg:0};
    const dx=(xm-ref.x_m)/ref.radius_m, dy=(ym-ref.y_m)/ref.radius_m, a=-ref.rotation_deg*Math.PI/180; return [dx*Math.cos(a)-dy*Math.sin(a), dx*Math.sin(a)+dy*Math.cos(a)];};
  const rings=()=>{const fd=fieldData; if(!fd)return []; const st=FIELD.state;
    const out=(fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>{const u=toUnits(c.position.x,c.position.y); return [st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R, !!c.activeSource, c.serial];});
    const lis=fd.room&&fd.room.listener; if(lis&&lis.x_m!=null){const u=toUnits(lis.x_m,lis.y_m); out.listener=[st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R];}
    return out;};
  try{ FIELD=SyngField.create(wrap,{post:(p,b)=>post(p,b), viz:()=>window.__viz, rings, cfg:{scaleM:2.5},
       onChange:()=>{fieldTouched=performance.now();}}); }
  catch(e){ wrap.innerHTML='<div style="padding:30px;color:var(--muted);font-size:13px">WebGL is unavailable in this browser - the sound field needs it.</div>'; return; }
  window.__field=FIELD;
  $('#fldreset').onclick=()=>FIELD.resetParticles();
  $('#fldcenter').onclick=()=>{FIELD.center();fieldTouched=performance.now();post('/api/soundfield',{ux:0,uy:0});};
  $('#fldclassic').onclick=()=>{FIELD.setSpatial(false);fieldTouched=performance.now();post('/api/soundfield',{spatial:false});};
  const T=[['particles','Particles',500,16000,100],['glowGain','Glow',0,2.5,0.05],['pulseGain','Pulse strength',0,5,0.05],['pulseScale','Pulse reach (lower = farther)',0.5,5,0.1],
           ['pointScale','Sparkle size',0.3,3,0.05],['pointGain','Sparkle brightness',0,3,0.05],['haloGain','Puck halo',0,3,0.05],['grain','Grain',0,2,0.05],['flurry','Flurry',0,3,0.05],['weightSpatial','Field weight',0.5,2,0.01],['handleRadius','Handle radius',0.3,0.9,0.01],['scaleM','Metres per room radius',1,6,0.1]];
  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim'],['snapToCell','Snap the puck to a speaker']];
  const box=$('#fieldsliders');
  for(const [k,label,lo,hi,stp] of T){const row=document.createElement('div');row.className='ctl';
    row.innerHTML='<label>'+label+' <b></b></label><input type=range min='+lo+' max='+hi+' step='+stp+' value='+FIELD.cfg[k]+'>';
    const b=row.querySelector('b'),inp=row.querySelector('input');b.textContent=FIELD.cfg[k];
    inp.oninput=()=>{const v=parseFloat(inp.value);b.textContent=v;if(k==='particles')FIELD.setParticles(v);else FIELD.cfg[k]=v;
      try{localStorage.setItem('fieldcfg.'+k,String(v));}catch(e){}};
    try{const sv=localStorage.getItem('fieldcfg.'+k);if(sv!=null){inp.value=sv;inp.oninput();}}catch(e){}
    box.appendChild(row);}
  for(const [k,label] of toggles){const row=document.createElement('div');row.className='ctl';row.innerHTML='<label>'+label+'</label><input type=checkbox'+(FIELD.cfg[k]?' checked':'')+'>';
    const inp=row.querySelector('input');inp.onchange=()=>{FIELD.cfg[k]=inp.checked;try{localStorage.setItem('fieldcfg.'+k,inp.checked?'1':'0');}catch(e){}};
    try{const sv=localStorage.getItem('fieldcfg.'+k);if(sv!=null){inp.checked=sv==='1';FIELD.cfg[k]=inp.checked;}}catch(e){}
    box.appendChild(row);}
}
// ---- system sparklines (client-side history; no device cost) ----
const SH={cpu:[],mem:[]};
function spark(id,arr){const el=$('#'+id);if(!el)return;const w=120,h=30,n=60;
  if(arr.length<2){el.innerHTML='';return;}
  const pts=arr.map((v,i)=>[(i/(n-1)*w),(h-1-(Math.max(0,Math.min(100,v))/100)*(h-2))]);
  const d=pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  el.innerHTML=`<polygon class=area points="0,${h} ${d} ${pts[pts.length-1][0].toFixed(1)},${h}"/><polyline points="${d}"/>`;}
function updateSystem(sys){if(!sys)return;const mem=sys.mem||{};
  if(sys.cpuPct!=null){SH.cpu.push(sys.cpuPct);if(SH.cpu.length>60)SH.cpu.shift();}
  if(mem.usedPct!=null){SH.mem.push(mem.usedPct);if(SH.mem.length>60)SH.mem.shift();}
  $('#cpuv').textContent=sys.cpuPct!=null?sys.cpuPct+'%':'—';
  $('#memv').textContent=mem.usedPct!=null?mem.usedPct+'%':'—';
  spark('cpuspark',SH.cpu);spark('memspark',SH.mem);
  const up=sys.uptime,upr=up==null?'—':(up>=86400?Math.floor(up/86400)+'d '+Math.floor(up%86400/3600)+'h':Math.floor(up/3600)+'h '+Math.floor(up%3600/60)+'m');
  $('#sysgrid').innerHTML=[['Memory',(mem.usedMB||'—')+' / '+(mem.totalMB||'—')+' MB'],['Load (1m)',sys.load!=null?sys.load:'—'],
    ['Data partition',sys.diskData!=null?sys.diskData+'%':'—'],['Root fs',sys.diskRoot!=null?sys.diskRoot+'%':'—'],['Uptime',upr]]
    .map(([k,v])=>`<div class=sysmetric><div class=sysk>${k}</div><div style="font-size:15px;margin-top:4px">${v}</div></div>`).join('');
}

function smooth(a,b,x){x=Math.max(0,Math.min(1,(x-a)/(b-a)));return x*x*(3-2*x);}
function drawTone(){
  const el=$('#tonecurve');if(!el)return;
  const bass=(+$('#bass').value)/100, tilt=(+$('#tilt').value)/100, W=Math.max(120,el.clientWidth||300),H=66,mid=H/2;
  el.setAttribute('viewBox','0 0 '+W+' '+H);
  let d='';
  for(let i=0;i<=60;i++){const x=i/60,gL=bass*(1-smooth(0.12,0.5,x)),gH=tilt*smooth(0.5,0.9,x);
    const g=gL+gH, px=(x*W).toFixed(1), py=(mid-g*24).toFixed(1);d+=(i?'L':'M')+px+','+py+' ';}
  el.innerHTML='<line x1="0" y1="'+mid+'" x2="'+W+'" y2="'+mid+'" stroke="var(--line)" stroke-width="1" />'+
    '<path d="'+d+'" fill="none" stroke="var(--accent)" stroke-width="2.5" vector-effect="non-scaling-stroke" />'+
    '<text x="6" y="'+(H-6)+'" fill="var(--muted)" font-size="9">LOWS</text>'+
    '<text x="'+(W-34)+'" y="'+(H-6)+'" fill="var(--muted)" font-size="9">HIGHS</text>';
}
function updateSleep(st){
  const sl=st.sleep||{}; const left=sl.until?Math.max(0,Math.round((sl.until-sl.now)/60)):0;
  $('#sleepstate').textContent=sl.state==='fading'?'fading out…':(sl.until?('off in '+left+' min'):'off');
  const lim=(st.settings||{}).volume_limit||0; $('#limval').textContent=lim?lim+' %':'off';
  document.querySelectorAll('[data-lim]').forEach(b=>b.classList.toggle('on',+b.dataset.lim===lim));
  const vol=$('#vol'); if(vol){vol.max=lim||100;}
}
document.querySelectorAll('[data-sleep]').forEach(b=>b.onclick=()=>post('/api/sleep',{minutes:+b.dataset.sleep}).then(()=>tick()));
document.querySelectorAll('[data-lim]').forEach(b=>b.onclick=()=>post('/api/settings',{volume_limit:+b.dataset.lim}).then(()=>tick()));
function updateCalib(cal){
  if(!cal||cal.ready===undefined){$('#calibcard').style.display='none';return;}
  $('#calibcard').style.display='';
  $('#roomeq').innerHTML=[['on','On'],['off','Off']].map(([v,l])=>`<button class=srcbtn data-eq="${v}">${l}</button>`).join('');
  $('#roomeq').querySelectorAll('[data-eq]').forEach(b=>b.onclick=()=>{post('/api/roomeq',{on:b.dataset.eq==='on',scope:'group'});});
  const noise=cal.noiseRMS, pct=noise==null?0:Math.max(0,Math.min(100,(noise-30)/40*100));
  const rows=[['Ready to calibrate',cal.ready?'yes':'no',cal.ready?'':'hot'],
    ['Room noise',noise==null?'—':noise+' dB',noise>55?'hot':''],
    ['Long-term noise',cal.noiseLongTerm==null?'—':cal.noiseLongTerm+' dB','']];
  $('#calibgrid').innerHTML=rows.map(([k,v,c])=>`<div class=sysmetric><div class=sysk>${k}</div><div class="sysv ${c||''}" style="font-size:16px">${v}</div></div>`).join('');
}
function renderControls(sources){
  const ids=Object.keys(sources||{}).filter(id=>sources[id].music);
  $('#srcs').innerHTML=ids.map(id=>`<button class="srcbtn ${sources[id].active?'on':''}" data-src="${id}">${srcName(id,sources[id])}</button>`).join('');
  $('#srcs').querySelectorAll('.srcbtn').forEach(b=>b.onclick=()=>{
    const id=b.dataset.src;post('/api/source',{id,on:!sources[id].active});b.classList.toggle('on');});
  const act=activeSrc(sources);
  const np=act&&sources[act].playing&&sources[act].title?`<b>${sources[act].title}</b>${sources[act].artist?' — '+sources[act].artist:''}`:(act?('<b>'+(ICON[act]||act)+'</b> active'):'Nothing playing');
  const A=act&&sources[act]; const hasMeta=A&&(A.title||A.artist);
  $('#nowplaying').style.display=hasMeta?'flex':'none'; $('#np').style.display=hasMeta?'none':'';
  $('#np').innerHTML=np;
  if(hasMeta){
    $('#nptitle').textContent=A.title||'—'; $('#npsub').textContent=[A.artist,A.album].filter(Boolean).join(' · ');
    const key=act+'|'+(A.title||'')+'|'+(A.album||''); if(window.__artKey!==key){window.__artKey=key;$('#npart').src=A.artwork?('/api/artwork?source='+act+'&k='+encodeURIComponent(key)):'';$('#npart').style.display=A.artwork?'':'none';}
    const dur=+A.duration||0,pos=+A.position||0; $('#npbar').style.display=dur>0?'':'none'; if(dur>0)$('#npfill').style.width=Math.max(0,Math.min(100,pos/dur*100))+'%';
    const mmss=t=>{t=Math.max(0,Math.round(t));return Math.floor(t/60)+':'+String(t%60).padStart(2,'0');}; $('#nptime').textContent=dur>0?mmss(pos)+' / '+mmss(dur):'';
    const ctl=A.controls||[]; $('#transport').querySelectorAll('[data-t]').forEach(b=>{const t=b.dataset.t; const need=t==='playpause'?(A.playing?'pause':'play'):t; b.disabled=ctl.length&&!ctl.includes(need); b.textContent=t==='playpause'?(A.playing?'❚❚':'▶'):(t==='next'?'⏭':'⏮'); b.onclick=()=>{post('/api/transport',{source:act,action:need});};});
  }
  const badge=A?[...new Set([A.badging,A.mixType,A.layout].filter(x=>x&&x!=='none'&&x!=='unknown'))].join(' · '):''; $('#mixbadge').textContent=badge;
  // volume reflects active (or first music) source
  const vs=act?sources[act]:(sources[ids[0]]||{});
  if(!dragging.vol&&vs.volume!=null){const v=Math.round(vs.volume*100);$('#vol').value=v;$('#volval').textContent=v;if(FIELD&&!(FIELD.state.grab&&FIELD.state.grab.t==='vol'))FIELD.setVolume(vs.volume);}
  // tone reflects any source carrying toneControl
  let tone=null;for(const id of ids)if(sources[id].tone){tone=sources[id].tone;break;}
  if(tone){if(!dragging.bass){$('#bass').value=Math.round((tone.bass||0)*100);$('#bassval').textContent=$('#bass').value;}
    if(!dragging.tilt){$('#tilt').value=Math.round((tone.tilt||0)*100);$('#tiltval').textContent=$('#tilt').value;}}
  // mute reflects the representative source
  const rep=act?sources[act]:(sources[ids[0]]||{});
  const muted=!!rep.mute;
  const mb=$('#mutebtn');if(mb){mb.classList.toggle('on',muted);mb.textContent=muted?'Muted':'Mute';
    mb.onclick=()=>{post('/api/mute',{on:!muted});mb.classList.toggle('on');};}
  // spatial toggles (height / beyond-stereo)
  const sp=$('#spatialtoggles');
  if(sp&&ids.length){ $('#spatialcard').style.display='';
    const defs=[['height','Height processing',rep.height],['beyond','Beyond-stereo',rep.beyond]];
    sp.innerHTML=defs.map(([k,l,v])=>`<button class="srcbtn ${v?'on':''}" data-sp="${k}">${l}</button>`).join('');
    sp.querySelectorAll('[data-sp]').forEach(b=>b.onclick=()=>{const k=b.dataset.sp,cur=b.classList.contains('on');
      post('/api/spatial',{[k]:!cur});b.classList.toggle('on');});
  } else if(sp){ $('#spatialcard').style.display='none'; }
}
function wireSlider(id,label,onsend){const el=$('#'+id);
  el.addEventListener('pointerdown',()=>dragging[id]=true);
  const end=()=>{dragging[id]=false;onsend(+el.value);};
  el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);
  el.addEventListener('input',()=>{$('#'+label).textContent=el.value;throttle(id,()=>onsend(+el.value),120);});
  el.addEventListener('change',()=>onsend(+el.value));
}
wireSlider('vol','volval',v=>post('/api/volume',{source:'all',volume:v/100}));
wireSlider('bass','bassval',v=>post('/api/tone',{bass:v/100,tilt:$('#tilt').value/100}));
wireSlider('tilt','tiltval',v=>post('/api/tone',{bass:$('#bass').value/100,tilt:v/100}));
$('#bass').addEventListener('input',drawTone);$('#tilt').addEventListener('input',drawTone);
// LED tuning sliders restart the daemon, so only post on release (change), not during drag
function wireTune(id,label,onsend,fmt){const el=$('#'+id),f=fmt||(v=>v);
  el.addEventListener('pointerdown',()=>dragging[id]=true);
  const end=()=>{dragging[id]=false;onsend(+el.value);};
  el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);
  el.addEventListener('input',()=>{$('#'+label).textContent=f(+el.value);});
  el.addEventListener('change',()=>onsend(+el.value));}
wireTune('beatsens','bsv',v=>ledPost('/api/led/tune',{beatSens:(v/100).toFixed(2)}));
wireTune('ledfloor','flv',v=>ledPost('/api/led/tune',{floor:(v/100).toFixed(2)}));
// flow crossfade durations (shown in seconds): silent=value/10 s, beat=value/100 s
function wireFade(id,label,div,key){const el=$('#'+id);
  const dp=div===100?2:1, show=()=>{$('#'+label).textContent=(el.value/div).toFixed(dp)+'s';};
  el.addEventListener('pointerdown',()=>dragging[id]=true);
  const send=()=>ledPost('/api/led/tune',{[key]:(el.value/div).toFixed(2)});
  const end=()=>{dragging[id]=false;send();};
  el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);
  el.addEventListener('input',show);el.addEventListener('change',send);show();}
wireFade('silentfade','sfv',10,'silentFade');
wireFade('beatfade','bfv',100,'beatFade');
// spatial spread (angle) + reset-to-center — one-puck soundfield model
wireTune('soundfocus','focv',v=>{post('/api/soundfield',{width:Math.min(180,v/2),spatial:v<300});},v=>v+'°'+(v>=300?' · surround':''));
$('#soundreset').onclick=()=>{if(FIELD)FIELD.center();post('/api/soundfield',{ux:0,uy:0});
  $('#soundfocus').value=55;$('#focv').textContent='55°';};

async function tick(){
  let st;try{st=await(await fetch('/api/state',{cache:'no-store'})).json();}catch(e){$('#conn').innerHTML='<span class=dot></span>offline';return;}
  if(st.error){$('#grid').innerHTML=`<div class=card style=color:var(--warn)>${st.error}</div>`;return;}
  const c=st.core||{},fleet=st.fleet||[],room=st.room||null,me=fleet.find(x=>x.isMe)||{};
  window.__fleetN=fleet.length;
  const act=activeSrc(st.sources);
  $('#host').textContent=c.hostname||'Syng Cell';
  $('#sub').textContent=room&&room.name?('Room · '+room.name):'Solo';
  $('#orb').className='orb'+(act?' live':'')+((c.role||'').toLowerCase().includes('primary')?' primary':'');
  const b=[];if(c.role)b.push(`<span class="badge role">${c.role}</span>`);
  b.push(act?`<span class="badge">${ICON[act]||act}</span>`:'<span class=badge>idle</span>');
  if(c.hardwareModel)b.push(`<span class=badge>${c.hardwareModel}</span>`);$('#badges').innerHTML=b.join('');
  $('#conn').innerHTML=`<span class="dot ${st.cloudOnline?'on':''}"></span>${st.cloudOnline?'cloud up':'LAN only'}`;
  renderControls(st.sources);
  drawTone();updateCalib(st.calibration);
  if(fleet.length){$('#roomcard').style.display='';$('#rname').textContent=room&&room.name?room.name:'';setField(fleet,room);}else $('#roomcard').style.display='none';
  window.__room=room;
  const fld=room&&room.field;
  $('#soundstage').style.display=fld?'':'none';
  if(fld&&!dragging.soundfocus){const sv=Math.round(fld.spatial?fld.width*2:360);
    $('#soundfocus').value=Math.max(20,Math.min(360,sv));$('#focv').textContent=sv+'°'+(sv>=300?' · surround':'');}
  updateSystem(st.system);
  try{updateSleep(st);}catch(e){}
  if(fleet.length>1){$('#fleetcard').style.display='';$('#fcount').textContent=fleet.length+' cells';
    $('#fleet').innerHTML=fleet.map(c=>{const pri=(c.role||'').includes('primary');
     return `<div class="fcell ${c.isMe?'me':''}"><span class="fdot ${pri?'pri':(c.activeSource?'on':'')}"></span>
      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}<div class=muted style="font-size:11px;font-weight:500" data-live="${c.serial}"></div></span>
      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}${c.calibrated?'':'uncalibrated · '}${c.isMe?(c.ip||''):`<a href="http://${c.ip}:8080/" style="color:var(--accent);text-decoration:none">${c.ip||''} ›</a>`}
      <button class=srcbtn data-ping="${c.serial}" style="flex:none;min-width:0;padding:3px 9px;font-size:11px;margin-left:8px">ping</button></span></div>`;}).join('');
    $('#fleet').querySelectorAll('[data-ping]').forEach(b=>b.onclick=e=>{e.preventDefault();b.disabled=true;post('/api/ping',{serial:b.dataset.ping}).finally(()=>setTimeout(()=>b.disabled=false,3000));});}
  else $('#fleetcard').style.display='none';
  const temps=st.sensors&&st.sensors.temps||{},faults=st.sensors&&st.sensors.faults||{},accel=st.sensors&&st.sensors.accel||{},mmc=st.sensors&&st.sensors.mmc||{};
  const trows=Object.entries(temps).map(([k,v])=>[TEMP[k]||k,(Math.round(v*10)/10)+'°C',v>=80?'hot':'']);
  const frows=Object.entries(faults).map(([k,v])=>[k.replace(/_/g,' '),v,v>0?'fault':'']);
  const arows=Object.entries(accel).map(([k,v])=>[k.replace(/_/g,' '),(Math.round(v*100)/100)]);
  const mm=mmcHuman(mmc);
  const net=tile('Network',[['Address',c.infraIP||me.ip||'—'],['SyFi mesh',c.syfiIP||'—'],
    ['Time sync',c.timeSyncGood?'locked':'—',c.timeSyncGood?'':'hot'],['SyFi link',c.syfiGood?'good':(c.syfiGood===false?'down':'—'),c.syfiGood?'':'fault'],
    ['Orientation',c.cellIsUpsideDown?'inverted':'upright']]);
  $('#grid').innerHTML=[net,tile('Temperatures',trows),tile('Speaker faults',frows),tile('Orientation (g)',arows),tile('eMMC health',mm.rows),tile('eMMC raw',mm.raw)].join('');
  $('#foot').textContent=`${c.uniqueID||me.serial||''}  ·  ${c.buildName||''}  ·  ${new Date().toLocaleTimeString()}`;
}
async function tickStats(){
  let s;try{s=await(await fetch('/api/stats',{cache:'no-store'})).json();}catch(e){return;}
  const keys=Object.keys(s||{});if(!keys.length){$('#statscard').style.display='none';return;}
  $('#statscard').style.display='';
  $('#stats').innerHTML=keys.map(name=>{
    const o=s[name]||{};const rows=Object.entries(o).slice(0,10).map(([k,v])=>
      [k,typeof v==='object'?JSON.stringify(v).slice(0,24):String(v)]);
    return tile(name,rows);}).join('');
}
// ---------- LED ----------
let ledState={}, ledScope='group';
function hexRgb(h){return[parseInt(h.slice(1,3),16)/255,parseInt(h.slice(3,5),16)/255,parseInt(h.slice(5,7),16)/255];}
function ledPost(path,body){return post(path,Object.assign({scope:ledScope},body));}
function renderScope(hasPeers){
  $('#ledscope').innerHTML=[['group','Whole room'],['this','This speaker']].map(([v,l])=>
    `<button class="srcbtn ${v===ledScope?'on':''}" data-scope="${v}" ${v==='group'&&!hasPeers?'style=opacity:.5':''}>${l}</button>`).join('');
  $('#ledscope').querySelectorAll('[data-scope]').forEach(b=>b.onclick=()=>{ledScope=b.dataset.scope;renderScope(hasPeers);});
}
async function tickLed(){
  let s;try{s=await(await fetch('/api/led',{cache:'no-store'})).json();}catch(e){return;}
  ledState=s;
  renderScope((window.__fleetN||1)>1);
  const cur=s.reactive?s.mode:'manual';
  $('#ledmodes').innerHTML=['manual',...s.reactiveModes].map(m=>
    `<button class="srcbtn ${m===cur?'on':''}" data-mode="${m}">${m==='manual'?'Manual':m}</button>`).join('');
  $('#ledmodes').querySelectorAll('.srcbtn').forEach(b=>b.onclick=()=>ledPost('/api/led/reactive',{mode:b.dataset.mode}).then(()=>setTimeout(tickLed,900)));
  const manual=!s.reactive;
  $('#ledmanual').style.display=manual?'':'none';
  $('#lednote').style.display=manual?'none':'';
  // group-color (palette) toggle — only meaningful in a reactive mode with peers
  const pal=s.palette||'free';
  $('#palrow').style.display=(!manual&&(window.__fleetN||1)>1)?'':'none';
  $('#ledpalette').innerHTML=[['free','Each its own'],['shared','Match room']].map(([v,l])=>
    `<button class="srcbtn ${v===pal?'on':''}" data-pal="${v}">${l}</button>`).join('');
  $('#ledpalette').querySelectorAll('[data-pal]').forEach(b=>b.onclick=()=>ledPost('/api/led/palette',{palette:b.dataset.pal}).then(()=>setTimeout(tickLed,900)));
  // reactive tuning (beat sensitivity + brightness floor) — reflect from ARGS
  $('#tunewrap').style.display=manual?'none':'';
  const tn=s.tune||{};
  if(!dragging.beatsens&&tn.beatSens!=null){$('#beatsens').value=Math.round(parseFloat(tn.beatSens)*100);$('#bsv').textContent=$('#beatsens').value;}
  if(!dragging.ledfloor&&tn.floor!=null){$('#ledfloor').value=Math.round(parseFloat(tn.floor)*100);$('#flv').textContent=$('#ledfloor').value;}
  if(!dragging.silentfade&&tn.silentFade!=null){$('#silentfade').value=Math.round(parseFloat(tn.silentFade)*10);$('#sfv').textContent=parseFloat(tn.silentFade).toFixed(1)+'s';}
  if(!dragging.beatfade&&tn.beatFade!=null){$('#beatfade').value=Math.round(parseFloat(tn.beatFade)*100);$('#bfv').textContent=parseFloat(tn.beatFade).toFixed(2)+'s';}
  // animation buttons
  $('#ledanims').innerHTML=(s.visions||[]).map(v=>`<button class=srcbtn data-play="${v}">${v}</button>`).join('')||'<span style="color:var(--muted);font-size:12px">no animations</span>';
  $('#ledanims').querySelectorAll('[data-play]').forEach(b=>b.onclick=()=>ledPost('/api/led/anim',{action:'play',name:b.dataset.play}));
  // editor select (preserve current selection + don't clobber textarea mid-edit)
  const sel=$('#visedit');const keep=sel.value;
  sel.innerHTML=(s.visions||[]).map(v=>`<option ${v===keep?'selected':''}>${v}</option>`).join('');
  if(!keep&&s.visions&&s.visions.length){sel.value=s.visions[0];loadVision();}
}
async function loadVision(){const name=$('#visedit').value;if(!name)return;
  try{const d=await(await fetch('/api/vision?name='+encodeURIComponent(name))).json();$('#visjson').value=d.content;}catch(e){}}
$('#visedit').addEventListener('change',loadVision);
$('#vissave').onclick=async()=>{const name=$('#visedit').value;
  try{JSON.parse($('#visjson').value);}catch(e){$('#vismsg').textContent='invalid JSON';return;}
  const r=await fetch('/api/vision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,content:$('#visjson').value})});
  $('#vismsg').textContent=r.ok?'saved':'error';setTimeout(()=>$('#vismsg').textContent='',2000);};
$('#ledapply').onclick=()=>{const[r,g,b]=hexRgb($('#ledcol').value);ledPost('/api/led/color',{r,g,b,w:$('#ledw').value/100});};
document.querySelectorAll('[data-anim]').forEach(b=>b.onclick=()=>ledPost('/api/led/anim',{action:b.dataset.anim}));

// live audio-reactive field: poll the latest viz frame into window.__viz.
// Single-in-flight (the `vizBusy` guard) so slow polls can't stack and saturate the
// browser's ~6-connection pool — otherwise control POSTs queue behind the viz flood.
let vizBusy=false, vizES=null;
try{ vizES=new EventSource('/api/viz/stream'); vizES.onmessage=e=>{try{window.__viz=JSON.parse(e.data);window.__vizT=performance.now();}catch(x){}}; vizES.onerror=()=>{}; }catch(e){ vizES=null; }
async function tickViz(){ if(vizBusy)return; vizBusy=true;
  try{const v=await(await fetch('/api/viz/frame',{cache:'no-store'})).json();window.__viz=v;}
  catch(e){window.__viz={live:false};}
  finally{vizBusy=false;}
}
// in-flight guards so a slow /api/state can't stack either
function guarded(fn){let busy=false;return async()=>{if(busy)return;busy=true;try{await fn();}finally{busy=false;}};}
const tickG=guarded(tick),statsG=guarded(tickStats),ledG=guarded(tickLed);
window.addEventListener('resize',()=>{try{drawTone();}catch(e){}});async function loadCloud(){
  try{const d=await (await fetch('/api/cloudblock/all')).json(); const cells=d.cells||[];
    const nb=cells.filter(c=>c.blocked===true).length, nu=cells.filter(c=>c.blocked===false).length;
    $('#cloudsum').textContent=cells.length?`${nb} blocked · ${nu} open${cells.length-nb-nu?' · '+(cells.length-nb-nu)+' unknown':''}`:'';
    $('#cloudlist').innerHTML=cells.map(c=>`<div class="fcell ${c.isMe?'me':''}"><span class="fdot ${c.blocked===true?'pri':(c.blocked===false?'on':'')}"></span><span class=nm>${short(c.serial)||c.ip}${c.isMe?' · this one':''}</span>
      <span class=mt>${c.blocked===true?'cloud blocked':(c.blocked===false?'cloud open':'unknown')} · ${c.ip}</span></div>`).join('');
    const me=cells.find(c=>c.isMe); $('#cbme').textContent=me&&me.blocked?'Unblock on this cell':'Block on this cell'; $('#cbme').dataset.on=me&&me.blocked?'0':'1';
  }catch(e){}
}
$('#cbme').onclick=async()=>{const on=$('#cbme').dataset.on==='1'; if(!confirm((on?'Block':'Unblock')+' Syng\'s cloud on this cell?'))return; await post('/api/cloudblock',{on}); setTimeout(loadCloud,2500);};
$('#cball').onclick=async()=>{if(!confirm('Block Syng\'s cloud on every cell in the room?'))return; await post('/api/cloudblock',{on:true,scope:'group'}); setTimeout(loadCloud,3000);};
$('#cbnone').onclick=async()=>{if(!confirm('Unblock on every cell? Syng\'s cloud will overwrite the local room document (6-cell rooms, local calibration) with what it last knew.'))return; await post('/api/cloudblock',{on:false,scope:'group'}); setTimeout(loadCloud,3000);};
loadCloud();setInterval(loadCloud,30000);
async function loadFleetLive(){
  try{const d=await (await fetch('/api/fleet/live')).json(); window.__fleetLive={}; (d.cells||[]).forEach(c=>window.__fleetLive[c.serial]=c);
    document.querySelectorAll('[data-live]').forEach(el=>{const c=window.__fleetLive[el.dataset.live]; if(!c)return;
      el.innerHTML=c.ok?`${c.syfi?'<span class=good>SyFi ✓</span>':'<span class=fault>SyFi ✗</span>'} · ${c.sync?'<span class=good>sync ✓</span>':'<span class=fault>sync ✗</span>'}${c.rssi!=null?' · '+c.rssi+' dBm':''}`:'<span class=fault>agent unreachable</span>';});
  }catch(e){}
}
loadFleetLive();setInterval(loadFleetLive,20000);
$('#pingme').onclick=()=>{const b=$('#pingme');b.disabled=true;post('/api/ping',{}).finally(()=>setTimeout(()=>b.disabled=false,3000));};
$('#pingall').onclick=()=>{const b=$('#pingall');b.disabled=true;post('/api/ping',{all:true}).finally(()=>setTimeout(()=>b.disabled=false,4000));};
initField();tickG();setInterval(tickG,1500);statsG();setInterval(statsG,5000);ledG();setInterval(ledG,4000);
if(!vizES){tickViz();setInterval(tickViz,90);} else setInterval(()=>{ if(performance.now()-(window.__vizT||0)>1500) tickViz(); },500);
</script></body></html>"""

SETUP_PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Syng · Setup</title>
<style>
:root{--bg:#14161b;--surface:#1b1e26;--surface2:#222633;--line:#2b3140;--text:#e8eaf0;
 --muted:#98a0b0;--accent:#8a7cff;--live:#4fd1c5;--warn:#e6a23c;--bad:#e5618a;--radius:18px}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;
 --line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}}
:root[data-theme=light]{--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;--line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}
.themebtn{font:inherit;font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer;margin-left:10px}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--text);font-variant-numeric:tabular-nums;
 font-family:ui-rounded,"SF Pro Rounded","SF Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased;padding:16px;line-height:1.4}
.wrap{max-width:900px;margin:0 auto}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:6px 2px 16px}
.mark{font-size:13px;letter-spacing:.14em;color:var(--muted);font-weight:600}.mark b{color:var(--accent)}
.mark a{color:var(--muted);text-decoration:none;margin-left:14px;letter-spacing:0;font-weight:500}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin-bottom:14px}
.card h2{margin:0 0 12px;font-size:12px;letter-spacing:.1em;color:var(--muted);font-weight:600;display:flex;justify-content:space-between;align-items:center}
.card h2 .rn{color:var(--text);letter-spacing:0;font-size:15px;margin-left:8px;font-weight:650}
.hero{display:flex;gap:18px;align-items:center}
.orb{--ring:var(--muted);position:relative;width:72px;height:72px;flex:none;border-radius:50%;
 background:radial-gradient(60% 60% at 38% 32%,#2b3040,#14161b 78%);box-shadow:inset 0 0 0 1px #0007,0 10px 30px #0006}
.orb::after{content:"";position:absolute;inset:-5px;border-radius:50%;background:conic-gradient(from 210deg,var(--ring),transparent 55%,var(--ring));
 -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px));mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px))}
.orb.primary{--ring:var(--accent)}.orb.secondary{--ring:var(--live)}
.id h1{margin:0 0 2px;font-size:22px;font-weight:650;letter-spacing:-.01em}.id .sub{color:var(--muted);font-size:13px}
.badges{margin-top:8px;display:flex;gap:7px;flex-wrap:wrap}
.badge{font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.badge.role{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
.badge.good{color:var(--live)}.badge.warn{color:var(--warn)}.badge.bad{color:var(--bad)}
.btn{padding:9px 14px;border-radius:12px;border:1px solid var(--line);background:var(--surface2);color:var(--text);font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.btn.pri{background:var(--accent);border-color:var(--accent);color:#fff}.btn.live{border-color:var(--live);color:var(--live)}
.btn.sm{padding:5px 10px;font-size:12px;border-radius:9px}.btn:disabled{opacity:.45;cursor:default}
.btn.danger{border-color:color-mix(in srgb,var(--bad) 50%,var(--line));color:var(--bad)}
.rowb{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
input[type=text],input[type=password],input[type=number],select{font:inherit;font-size:13px;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--surface2);color:var(--text);min-width:0}
input[type=number]{width:74px}
.list{display:grid;gap:8px}
.item{display:flex;align-items:center;gap:12px;padding:9px 12px;background:var(--surface2);border-radius:12px;border:1px solid transparent;font-size:13px;flex-wrap:wrap}
.item.me{border-color:color-mix(in srgb,var(--accent) 55%,var(--line))}
.item .k{color:var(--muted)}.item b{font-weight:650}.item .grow{flex:1 1 120px}
.room{border:1px solid var(--line);border-radius:14px;padding:10px 12px;margin-bottom:10px}
.room h3{margin:0 0 8px;font-size:14px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.room h3 small{color:var(--muted);font-weight:500}
.muted{color:var(--muted);font-size:12px}.ok{color:var(--live)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.row{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px}.row span:first-child{color:var(--muted)}.row span:last-child{text-align:right}
#map{width:100%;height:auto;display:block;background:var(--surface2);border-radius:12px;touch-action:none;cursor:grab}
.log{font:12px ui-monospace,Menlo,monospace;background:var(--surface2);border-radius:10px;padding:10px;max-height:180px;overflow:auto;white-space:pre-wrap;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 6px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600;font-size:11px;letter-spacing:.08em}
td input[type=number]{width:66px;padding:5px 6px}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:var(--surface);border:1px solid var(--line);padding:10px 16px;border-radius:12px;font-size:13px;box-shadow:0 10px 30px #0008;opacity:0;transition:opacity .2s;pointer-events:none;max-width:90vw}
.toast.show{opacity:1}
details summary{cursor:pointer;color:var(--muted);font-size:13px}
.hint{font-size:12px;color:var(--muted);margin:6px 0 0}
</style></head><body><div class=wrap>
<script>(function(){const T=['auto','dark','light'];let t='auto';try{t=localStorage.getItem('syngTheme')||'auto';}catch(e){}
function apply(){if(t==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',t);document.querySelectorAll('.themebtn').forEach(b=>b.textContent='theme: '+t);}
window.cycleTheme=function(){t=T[(T.indexOf(t)+1)%T.length];try{localStorage.setItem('syngTheme',t);}catch(e){}apply();};apply();window.addEventListener('DOMContentLoaded',apply);})();</script>
<header><div class=mark><b>SYNG</b> · SETUP <a href="/">← dashboard</a><button class=themebtn onclick="cycleTheme()">theme</button></div><div class=muted id=agentv></div></header>

<div class=card><div class=hero><div class=orb id=orb></div><div class=id>
  <h1 id=title>…</h1><div class=sub id=sub></div><div class=badges id=badges></div></div></div></div>

<div class=card id=netcard><h2>NETWORK <button class="btn sm" id=wifiscan>Scan Wi-Fi</button></h2>
  <div id=netstatus class=muted>…</div>
  <div class=list id=wifilist style="margin-top:10px"></div>
  <details style="margin-top:10px"><summary>Join a hidden network / enter manually</summary>
    <div class=rowb><input type=text id=wssid placeholder="Network name"><input type=password id=wpsk placeholder="Password"><button class=btn id=wjoin>Connect</button></div></details>
  <p class=hint>Setup link: plug this speaker into a laptop with a USB-C cable and open <b>http://10.86.0.1:8080/setup</b> (the other port is 10.86.1.1). It also works over a USB-C Ethernet adapter or Wi-Fi.</p>
</div>

<div class=card><h2>ROOMS ON THIS NETWORK <span><button class="btn sm" id=rescan>Rescan</button></span></h2>
  <div id=rooms class=muted>scanning…</div>
  <div class=rowb style="margin-top:12px"><input type=text id=newroom placeholder="New room name" style="flex:1 1 180px"><button class="btn pri" id=mkroom>Create room with this speaker</button></div>
  <p class=hint id=mkhint></p>
</div>

<div class=card id=layoutcard style="display:none"><h2>ROOM LAYOUT <span class=rn id=roomname></span><span style="flex:1"></span>
  <button class="btn sm" id=rename>Rename</button></h2>
  <canvas id=map></canvas>
  <div class=muted style="margin-top:6px">Drag a speaker to move it · drag its tick to turn it · dashed circle = the sound-field reference circle (drag its centre, edge or ◆ to rotate) · ◎ = listener. Front of the room is up.</div>
  <div class=rowb style="margin-top:10px">
    <button class="btn pri" id=save disabled>Save layout</button>
    <button class=btn id=autocircle>Auto circle &amp; listener</button>
    <button class="btn live" id=calib>Soundcheck (calibrate)</button>
    <span class=muted id=calstate></span></div>
  <div class=log id=callog style="display:none"></div>
  <div style="overflow:auto;margin-top:10px"><table id=cells></table></div>
  <p class=hint>Soundcheck plays chirps from every speaker in turn (about a minute per speaker) and measures positions, orientation, walls and room EQ on the speakers themselves. Keep the room quiet. Results are stored by this firmware — no Syng account involved.</p>
</div>

<div class=card><details><summary>Advanced</summary>
  <div class=rowb style="margin-top:10px"><span class=muted id=cbstate></span><button class="btn sm" id=cbtoggle>…</button></div>
  <div class=rowb><button class="btn sm" id=showdoc>Show room document</button><button class="btn sm danger" id=restore>Restore pre-agent state on this speaker</button></div>
  <div class=log id=doc style="display:none"></div>
  <h2 style="margin-top:16px">CONTROL RING &amp; SOUNDS</h2>
  <div class=rowb><label class=muted>Ring volume steps <input type=number id=ringsteps min=10 max=400 step=10 style="width:80px"></label>
    <label class=muted><input type=checkbox id=ringcw> clockwise = louder</label>
    <label class=muted>UI sound level <input type=number id=uisnd min=0 max=60 step=5 style="width:70px"> %</label>
    <button class="btn sm" id=ringsave>Apply</button></div>
  <p class=hint>Ring steps = how many ring positions span the volume range (stock 100 — fewer = coarser, more = finer). Direction restarts the ring daemon (hidserver) on this cell only; the UI sound level is what Ping and the setup chimes play at (stock 25 %).</p>
  <h2 style="margin-top:16px">HOUSEKEEPING <span><button class="btn sm" id=hkrefresh>Refresh</button></span></h2>
  <div id=hk class=muted>…</div>
  <div class=rowb><button class="btn sm" id=hkvac>Vacuum journal to 32 MB</button><button class="btn sm" id=hkcores>Delete core dumps</button>
    <select id=hksvc><option value="">restart a service…</option><option>airplay2source</option><option>spotifysource</option><option>tidalsource</option><option>cellbroker</option><option>ledmanager</option><option>pycalibrator</option><option>syng-agent</option></select></div>
  <p class=hint>Everything here is applied through the speaker's own state service (cloudsyncd PatchState) — the same path Syng's cloud uses — so celldirector, the spatialiser and the SyFi link react as they would to the app. Nothing resets, nothing touches volume.</p>
</details></div>
<div class=toast id=toast></div>
</div>
<script>
const $=s=>document.querySelector(s);
let ME=null, ROOM=null, DISC=null, dirty=false, primaryIp=null;
const toast=(m,bad)=>{const t=$('#toast');t.textContent=m;t.style.borderColor=bad?'var(--bad)':'var(--line)';t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),3200);};
async function api(path,body,ip){ // ip: another cell (relayed through this one so it works over the USB link)
  let r;
  if(ip&&ME&&ip!==ME.ip) r=await fetch('/api/relay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ip,path,method:body?'POST':'GET',body})});
  else r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);
  const j=await r.json().catch(()=>({}));
  if(!r.ok||j.error){throw new Error(j.error||('HTTP '+r.status));}
  return j;
}
const short=s=>(s||'').slice(-5);
function renderMe(){
  const m=ME; $('#title').textContent=(m.room_name||'Not in a room')+(m.room_name?'':'');
  $('#sub').textContent=`Cell ${short(m.serial)} · ${m.serial} · ${m.ip||'no IP'}`;
  $('#orb').className='orb '+((m.role||'').toLowerCase());
  const b=[];
  b.push(`<span class="badge role">${m.role||'standalone'}</span>`);
  b.push(`<span class="badge ${m.calibrated?'good':'warn'}">${m.calibrated?'calibrated':'not calibrated'}</span>`);
  if(m.wifi&&m.wifi.ssid) b.push(`<span class=badge>Wi-Fi ${m.wifi.ssid}</span>`); else b.push(`<span class="badge warn">no Wi-Fi</span>`);
  if(m.room_name) b.push(`<span class=badge>${(m.members||[]).length} in room</span>`);
  b.push(`<span class="badge ${m.cloud_blocked?'good':'warn'}">${m.cloud_blocked?'cloud blocked · offline mode':'Syng cloud reachable'}</span>`);
  if(m.syfiGood!=null&&m.role) b.push(`<span class="badge ${m.syfiGood?'good':''}">SyFi ${m.syfiGood?'ok':'—'}</span>`);
  $('#badges').innerHTML=b.join('');
  $('#agentv').textContent='agent '+(m.agent||'');
  $('#mkhint').textContent=(m.members||[]).length>1?'This speaker will leave "'+m.room_name+'" and start the new room as its primary.':'This speaker becomes the primary of the new room; join the others from their own setup pages or from the list above.';
  $('#cbstate').textContent=m.cloud_blocked?'Syng cloud: blocked on this speaker (the local room document is authoritative).':'Syng cloud: NOT blocked — if this speaker is online with Syng, the cloud will overwrite local room changes.';
  $('#cbtoggle').textContent=m.cloud_blocked?'Unblock cloud':'Block cloud';
}
async function loadMe(){ME=await api('/api/whoami');renderMe();}
async function loadWifi(scan){
  $('#netstatus').textContent='…';
  const w=scan?{services:(await api('/api/wifi/scan')).services}:await api('/api/wifi');
  const cur=(w.services||[]).find(s=>s.connected);
  $('#netstatus').innerHTML=`${cur?'Connected to <b>'+cur.name+'</b>':'<span class=warn>Not connected to Wi-Fi</span>'} · address ${ME.ip||'—'}`+((ME.ips||[]).length>1?' <span class=muted>('+ME.ips.map(a=>a[0]+' '+a[1]).join(', ')+')</span>':'');
  $('#wifilist').innerHTML=(w.services||[]).map(s=>`<div class=item><span class=grow><b>${s.name||'(hidden)'}</b> <span class=k>${s.security}${s.connected?' · connected':''}${s.autoconnect?' · saved':''}</span></span>
    ${s.connected?'':`<button class="btn sm" data-c="${s.name}">Connect</button>`}${s.autoconnect?`<button class="btn sm" data-f="${s.name}">Forget</button>`:''}</div>`).join('')||'<div class=muted>No networks listed — press Scan Wi-Fi.</div>';
  $('#wifilist').querySelectorAll('[data-c]').forEach(b=>b.onclick=()=>wifiJoin(b.dataset.c));
  $('#wifilist').querySelectorAll('[data-f]').forEach(b=>b.onclick=async()=>{await api('/api/wifi/forget',{ssid:b.dataset.f});loadWifi();});
}
async function wifiJoin(ssid,psk){
  if(psk==null){psk=prompt('Password for "'+ssid+'" (leave empty for an open network)');if(psk===null)return;}
  toast('Connecting to '+ssid+'…');
  try{const r=await api('/api/wifi/connect',{ssid,psk:psk||null});toast(r.service?'Connected / provisioned: '+ssid:'Saved; the speaker will connect when it sees '+ssid);}
  catch(e){toast('Wi-Fi: '+e.message,true);}
  setTimeout(()=>{loadMe().then(()=>loadWifi());},4000);
}
$('#wifiscan').onclick=()=>loadWifi(true);
$('#wjoin').onclick=()=>wifiJoin($('#wssid').value.trim(),$('#wpsk').value);

async function loadDiscover(){
  $('#rooms').textContent='scanning the network…';
  try{DISC=await api('/api/discover');}catch(e){$('#rooms').textContent='scan failed: '+e.message;return;}
  const rooms=DISC.rooms||[];
  $('#rooms').innerHTML=rooms.map(r=>{
    const mine=ME.room_id&&r.room_id===ME.room_id; const noRoom=r.room_id==='?';
    const cells=r.cells.map(c=>`<div class="item ${c.isMe?'me':''}"><span class=grow><b>Cell ${short(c.serial)}</b> <span class=k>${c.ip}${c.isMe?' · this speaker':''}${c.stock?' · stock firmware (no agent)':''}</span></span>
       <span class=k>${c.role||''}${c.calibrated?' · calibrated':''}</span>${c.agent?`<button class="btn sm" data-ping="${c.serial}">ping</button>`:''}${c.isMe||!c.agent?'':`<a class="btn sm" href="http://${c.ip}:8080/setup">open</a>`}</div>`).join('');
    const title=noRoom?'Not in a room':(r.room_name||'(unnamed room)');
    const btn=(!mine&&!noRoom&&r.primary_ip)?`<button class="btn sm pri" data-join="${r.primary_ip}">Join this room with this speaker</button>`:(mine?'<small>your room</small>':'');
    return `<div class=room><h3><span>${title} <small>${r.cells.length} speaker${r.cells.length>1?'s':''}</small></span>${btn}</h3><div class=list>${cells}</div></div>`;
  }).join('')||'<div class=muted>No other speakers found.</div>';
  $('#rooms').querySelectorAll('[data-ping]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/ping',{serial:b.dataset.ping});}catch(e){toast('Ping: '+e.message,true);}setTimeout(()=>b.disabled=false,3000);});
  $('#rooms').querySelectorAll('[data-join]').forEach(b=>b.onclick=async()=>{
    if(!confirm('Join the room via '+b.dataset.join+'? This speaker becomes a secondary there'+((ME.members||[]).length>1?' and leaves "'+ME.room_name+'"':'')+'.'))return;
    b.disabled=true;toast('Joining…');
    try{const r=await api('/api/room/join',{ip:b.dataset.join});toast(r.already?'Already in that room':'Joined · '+(r.members||[]).length+' speakers');}
    catch(e){toast('Join failed: '+e.message,true);}
    setTimeout(refreshAll,2500);
  });
}
$('#rescan').onclick=loadDiscover;
$('#mkroom').onclick=async()=>{
  const name=$('#newroom').value.trim(); if(!name){toast('Give the room a name',true);return;}
  if((ME.members||[]).length>1&&!confirm('Leave "'+ME.room_name+'" and start "'+name+'"?'))return;
  toast('Creating room…');
  try{await api('/api/room/create',{name});toast('Room "'+name+'" created');}catch(e){toast('Failed: '+e.message,true);}
  setTimeout(refreshAll,2500);
};

// ---------- layout map ----------
const cv=$('#map'), ctx=cv.getContext('2d');
let view={cx:0,cy:0,ppm:80}, L=null, grab=null;
function fromRoom(){ // ROOM -> editable layout model
  const rs=ROOM.room_state||{}; const rc=rs.reference_circle||{x_m:0,y_m:0,radius_m:1,rotation_deg:0}; const lp=rs.listener_position||{x_m:0,y_m:0};
  L={cells:ROOM.cells.map(c=>({serial:c.serial,ip:c.ip,role:c.role,x:+(c.position.x||0),y:+(c.position.y||0),z:+(c.position.z||1.5),rot:+(c.position.rotation||0),
      ud:!!c.is_upside_down,hl:+(c.height_layer||0),cal:!!c.calibrated,isMe:c.isMe,online:c.online,walls:c.nearby_walls||[]})),
     rc:{x:+rc.x_m,y:+rc.y_m,r:+rc.radius_m,rot:+rc.rotation_deg}, lis:{x:+lp.x_m,y:+lp.y_m}};
  fitView(); dirty=false; $('#save').disabled=true;
}
function fitView(){
  const xs=[],ys=[]; L.cells.forEach(c=>{xs.push(c.x);ys.push(c.y);}); xs.push(L.rc.x-L.rc.r,L.rc.x+L.rc.r); ys.push(L.rc.y-L.rc.r,L.rc.y+L.rc.r);
  const minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);
  const W=cv.width,H=cv.height; view.cx=(minx+maxx)/2; view.cy=(miny+maxy)/2;
  view.ppm=Math.min(W/Math.max(1,(maxx-minx)+1.6),H/Math.max(1,(maxy-miny)+1.6));
}
const toPx=(x,y)=>[cv.width/2+(x-view.cx)*view.ppm, cv.height/2-(y-view.cy)*view.ppm];
const toM=(px,py)=>[(px-cv.width/2)/view.ppm+view.cx, -(py-cv.height/2)/view.ppm+view.cy];
function sizeCanvas(){const w=cv.clientWidth||600; const dpr=Math.min(2,window.devicePixelRatio||1); cv.width=Math.round(w*dpr); cv.height=Math.round(Math.min(520,w*0.72)*dpr); cv.style.height=(cv.height/dpr)+'px'; if(L)fitView(); draw();}
function draw(){
  if(!L)return; const W=cv.width,H=cv.height,dpr=Math.min(2,window.devicePixelRatio||1); ctx.clearRect(0,0,W,H);
  const css=getComputedStyle(document.documentElement); const C={muted:css.getPropertyValue('--muted').trim(),text:css.getPropertyValue('--text').trim(),acc:css.getPropertyValue('--accent').trim(),live:css.getPropertyValue('--live').trim(),warn:css.getPropertyValue('--warn').trim(),line:css.getPropertyValue('--line').trim()};
  // grid 0.5 m
  ctx.lineWidth=1; ctx.strokeStyle=C.line; ctx.globalAlpha=.7;
  const [mx0,my1]=toM(0,0),[mx1,my0]=toM(W,H);
  for(let x=Math.floor(mx0*2)/2;x<=mx1;x+=0.5){const [px]=toPx(x,0);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,H);ctx.stroke();}
  for(let y=Math.floor(my0*2)/2;y<=my1;y+=0.5){const [,py]=toPx(0,y);ctx.beginPath();ctx.moveTo(0,py);ctx.lineTo(W,py);ctx.stroke();}
  ctx.globalAlpha=1;
  // reference circle
  const [rx,ry]=toPx(L.rc.x,L.rc.y), rr=L.rc.r*view.ppm;
  ctx.setLineDash([6*dpr,5*dpr]); ctx.strokeStyle=C.acc; ctx.lineWidth=1.5*dpr; ctx.beginPath(); ctx.arc(rx,ry,rr,0,Math.PI*2); ctx.stroke(); ctx.setLineDash([]);
  // rotation handle: the "front" of the circle (rotation_deg, CCW from +x; front = +90)
  const fa=(90+L.rc.rot)*Math.PI/180; const hx=rx+Math.cos(fa)*rr, hy=ry-Math.sin(fa)*rr;
  ctx.fillStyle=C.acc; ctx.beginPath(); ctx.moveTo(hx,hy-7*dpr);ctx.lineTo(hx+7*dpr,hy);ctx.lineTo(hx,hy+7*dpr);ctx.lineTo(hx-7*dpr,hy);ctx.closePath();ctx.fill();
  ctx.beginPath(); ctx.arc(rx,ry,4*dpr,0,Math.PI*2); ctx.fill();
  // listener
  const [lx,ly]=toPx(L.lis.x,L.lis.y); ctx.strokeStyle=C.text; ctx.lineWidth=1.5*dpr; ctx.beginPath(); ctx.arc(lx,ly,9*dpr,0,Math.PI*2); ctx.stroke(); ctx.beginPath(); ctx.arc(lx,ly,3*dpr,0,Math.PI*2); ctx.fillStyle=C.text; ctx.fill();
  // cells
  L.cells.forEach(c=>{
    const [px,py]=toPx(c.x,c.y), R=14*dpr;
    // walls
    (c.walls||[]).forEach(w=>{ if(!w.close_to_wall&&w.likelihood<0.5)return; const a=(c.rot+ (w.angle_deg||0))*Math.PI/180; const d=(w.distance_m||0.5)*view.ppm; const wx=px+Math.cos(a)*d, wy=py-Math.sin(a)*d;
      ctx.strokeStyle=C.warn; ctx.globalAlpha=Math.min(1,w.likelihood||0.5); ctx.lineWidth=3*dpr; ctx.beginPath(); ctx.moveTo(wx+Math.cos(a+Math.PI/2)*16*dpr,wy-Math.sin(a+Math.PI/2)*16*dpr); ctx.lineTo(wx-Math.cos(a+Math.PI/2)*16*dpr,wy+Math.sin(a+Math.PI/2)*16*dpr); ctx.stroke(); ctx.globalAlpha=1; });
    ctx.fillStyle=c.isMe?C.acc:(c.online?C.live:C.muted); ctx.globalAlpha=c.cal?1:.55; ctx.beginPath(); ctx.arc(px,py,R,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=1;
    if(grab&&grab.c===c){ctx.strokeStyle=C.text;ctx.lineWidth=2*dpr;ctx.stroke();}
    // orientation tick (rotation CCW from +x)
    const a=c.rot*Math.PI/180; ctx.strokeStyle=C.text; ctx.lineWidth=2.5*dpr; ctx.beginPath(); ctx.moveTo(px+Math.cos(a)*R*0.6,py-Math.sin(a)*R*0.6); ctx.lineTo(px+Math.cos(a)*R*1.9,py-Math.sin(a)*R*1.9); ctx.stroke();
    ctx.beginPath(); ctx.arc(px+Math.cos(a)*R*1.9,py-Math.sin(a)*R*1.9,4*dpr,0,Math.PI*2); ctx.fillStyle=C.text; ctx.fill();
    ctx.fillStyle=C.text; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='center'; ctx.fillText(short(c.serial)+(c.role==='primary'?' ★':''),px,py+R+12*dpr);
    if(c.ud){ctx.fillStyle=C.muted; ctx.fillText('⤒ ceiling',px,py+R+24*dpr);}
  });
  ctx.fillStyle=C.muted; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='left'; ctx.fillText('1 m = '+Math.round(view.ppm/dpr)+' px · front ↑',8*dpr,H-8*dpr);
}
function pick(px,py){
  const dpr=Math.min(2,window.devicePixelRatio||1);
  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); const a=c.rot*Math.PI/180; const tx=cx+Math.cos(a)*14*dpr*1.9, ty=cy-Math.sin(a)*14*dpr*1.9;
    if(Math.hypot(px-tx,py-ty)<10*dpr) return {t:'rot',c}; }
  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); if(Math.hypot(px-cx,py-cy)<16*dpr) return {t:'cell',c};}
  const [lx,ly]=toPx(L.lis.x,L.lis.y); if(Math.hypot(px-lx,py-ly)<12*dpr) return {t:'lis'};
  const [rx,ry]=toPx(L.rc.x,L.rc.y), rr=L.rc.r*view.ppm; const fa=(90+L.rc.rot)*Math.PI/180;
  if(Math.hypot(px-(rx+Math.cos(fa)*rr),py-(ry-Math.sin(fa)*rr))<12*dpr) return {t:'rcrot'};
  if(Math.hypot(px-rx,py-ry)<10*dpr) return {t:'rcc'};
  if(Math.abs(Math.hypot(px-rx,py-ry)-rr)<8*dpr) return {t:'rcr'};
  return {t:'pan'};
}
function evpos(e){const r=cv.getBoundingClientRect(); const dpr=cv.width/r.width; return [(e.clientX-r.left)*dpr,(e.clientY-r.top)*dpr];}
cv.addEventListener('pointerdown',e=>{if(!L)return; cv.setPointerCapture(e.pointerId); const [px,py]=evpos(e); grab=pick(px,py); grab.px=px;grab.py=py;grab.view={...view}; if(grab.t==='rcr')grab.r0=L.rc.r; draw();});
cv.addEventListener('pointermove',e=>{if(!grab)return; const [px,py]=evpos(e); const [mx,my]=toM(px,py);
  if(grab.t==='cell'){grab.c.x=Math.round(mx*100)/100;grab.c.y=Math.round(my*100)/100;}
  else if(grab.t==='rot'){grab.c.rot=Math.round(Math.atan2(-(py-toPx(grab.c.x,grab.c.y)[1]),px-toPx(grab.c.x,grab.c.y)[0])*180/Math.PI*10)/10;}
  else if(grab.t==='lis'){L.lis.x=Math.round(mx*100)/100;L.lis.y=Math.round(my*100)/100;}
  else if(grab.t==='rcc'){L.rc.x=Math.round(mx*100)/100;L.rc.y=Math.round(my*100)/100;}
  else if(grab.t==='rcr'){L.rc.r=Math.max(0.5,Math.round(Math.hypot(mx-L.rc.x,my-L.rc.y)*100)/100);}
  else if(grab.t==='rcrot'){L.rc.rot=Math.round(((Math.atan2(my-L.rc.y,mx-L.rc.x)*180/Math.PI-90)+3600)%360*10)/10;}
  else if(grab.t==='pan'){view.cx=grab.view.cx-(px-grab.px)/view.ppm; view.cy=grab.view.cy+(py-grab.py)/view.ppm; draw(); return;}
  dirty=true; $('#save').disabled=false; draw(); renderTable(false);});
const up=()=>{grab=null;draw();}; cv.addEventListener('pointerup',up); cv.addEventListener('pointercancel',up);
cv.addEventListener('wheel',e=>{e.preventDefault(); view.ppm=Math.max(20,Math.min(400,view.ppm*(e.deltaY<0?1.1:0.9))); draw();},{passive:false});

function renderTable(full=true){
  if(!L)return;
  if(full){$('#cells').innerHTML='<tr><th>SPEAKER</th><th>ROLE</th><th>X m</th><th>Y m</th><th>Z m</th><th>TURN °</th><th>CEILING</th><th>LAYER</th><th></th></tr>'+L.cells.map((c,i)=>`<tr data-i=${i}>
    <td><b>${short(c.serial)}</b>${c.isMe?' <span class=muted>(this)</span>':''}<div class=muted>${c.ip}${c.cal?'':' · uncalibrated'}</div></td>
    <td>${c.role||''}${c.role!=='primary'?` <button class="btn sm" data-pin=${i}>make primary</button>`:''}</td>
    <td><input type=number step=0.01 data-f=x value="${c.x}"></td><td><input type=number step=0.01 data-f=y value="${c.y}"></td><td><input type=number step=0.05 data-f=z value="${c.z}"></td>
    <td><input type=number step=1 data-f=rot value="${c.rot}"></td>
    <td><input type=checkbox data-f=ud ${c.ud?'checked':''}></td>
    <td><select data-f=hl><option value=0 ${c.hl==0?'selected':''}>0 · floor/shelf</option><option value=1 ${c.hl==1?'selected':''}>1 · mid</option><option value=2 ${c.hl==2?'selected':''}>2 · high/ceiling</option></select></td>
    <td style="white-space:nowrap"><button class="btn sm" data-ping="${c.serial}">ping</button> ${L.cells.length>1?`<button class="btn sm danger" data-rm=${i}>remove</button>`:''}</td></tr>`).join('');
    $('#cells').querySelectorAll('[data-ping]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/ping',{serial:b.dataset.ping});}catch(e){toast('Ping: '+e.message,true);}setTimeout(()=>b.disabled=false,3000);});
    $('#cells').querySelectorAll('input,select').forEach(el=>el.onchange=()=>{const c=L.cells[+el.closest('tr').dataset.i]; const f=el.dataset.f; c[f]=f==='ud'?el.checked:+el.value; dirty=true;$('#save').disabled=false; draw();});
    $('#cells').querySelectorAll('[data-rm]').forEach(b=>b.onclick=async()=>{const c=L.cells[+b.dataset.rm]; if(!confirm('Remove Cell '+short(c.serial)+' from "'+(ROOM.room_state.room_name)+'"? It becomes a room of its own.'))return;
      try{await api('/api/room/remove',{serial:c.serial},primaryIp);toast('Removed');}catch(e){toast('Remove failed: '+e.message,true);} setTimeout(refreshAll,2000);});
    $('#cells').querySelectorAll('[data-pin]').forEach(b=>b.onclick=async()=>{const c=L.cells[+b.dataset.pin]; if(!confirm('Make Cell '+short(c.serial)+' the primary? Playback pauses for a moment while the room re-elects.'))return;
      try{await api('/api/room/pin',{serial:c.serial,role:'primary'},primaryIp);toast('Requested');}catch(e){toast('Failed: '+e.message,true);} setTimeout(refreshAll,4000);});
  } else {
    $('#cells').querySelectorAll('tr[data-i]').forEach(tr=>{const c=L.cells[+tr.dataset.i]; tr.querySelectorAll('input[type=number]').forEach(inp=>{inp.value=c[inp.dataset.f];});});
  }
}
$('#save').onclick=async()=>{
  const body={cells:{},reference_circle:{x_m:L.rc.x,y_m:L.rc.y,radius_m:L.rc.r,rotation_deg:L.rc.rot},listener_position:{x_m:L.lis.x,y_m:L.lis.y}};
  L.cells.forEach(c=>body.cells[c.serial]={x:c.x,y:c.y,z:c.z,rotation:c.rot,is_upside_down:c.ud,height_layer:c.hl});
  try{const r=await api('/api/room/layout',body,primaryIp); toast('Layout saved · '+Object.values(r.fanout||{}).filter(v=>v===true).length+'/'+Object.keys(r.fanout||{}).length+' speakers updated'); dirty=false;$('#save').disabled=true;}
  catch(e){toast('Save failed: '+e.message,true);}
  setTimeout(loadRoom,1200);
};
$('#autocircle').onclick=async()=>{try{await api('/api/room/layout',{auto_circle:true,auto_listener:true},primaryIp);toast('Circle & listener recomputed');}catch(e){toast(e.message,true);} setTimeout(loadRoom,800);};
$('#rename').onclick=async()=>{const n=prompt('Room name',ROOM.room_state.room_name||'');if(!n)return;try{await api('/api/room/rename',{name:n},primaryIp);toast('Renamed');}catch(e){toast(e.message,true);} setTimeout(refreshAll,1000);};
let calTimer=null;
$('#calib').onclick=async()=>{
  if(!confirm('Run Soundcheck now? Every speaker will play measurement chirps (at a moderate level) for a few minutes. Keep the room quiet.'))return;
  try{const r=await api('/api/calibration/run',null,primaryIp); if(r.ok===false)throw new Error(r.error||'busy'); toast('Soundcheck started'); pollCal();}catch(e){toast('Soundcheck: '+e.message,true);}
};
async function pollCal(){
  clearTimeout(calTimer);
  try{const s=await api('/api/calibration/status',null,primaryIp); $('#callog').style.display=(s.log&&s.log.length)?'block':'none'; $('#callog').textContent=(s.log||[]).join('\n'); $('#callog').scrollTop=1e6;
    $('#calstate').textContent=s.running?('running · '+Math.round((Date.now()/1000-s.started))+' s'):(s.finished?(s.ok?'last Soundcheck ok':'last Soundcheck failed'):'');
    $('#calib').disabled=!!s.running;
    if(s.running) calTimer=setTimeout(pollCal,2000); else if(s.finished&&Date.now()/1000-s.finished<30){loadRoom();}
  }catch(e){}
}
async function loadRoom(){
  try{ROOM=await api('/api/room');}catch(e){ROOM=null;}
  const inRoom=ROOM&&ROOM.room_state&&ROOM.room_state.room_id;
  $('#layoutcard').style.display=inRoom?'block':'none';
  if(!inRoom)return;
  primaryIp=ROOM.primary_ip||ME.ip;
  $('#roomname').textContent=ROOM.room_state.room_name||'';
  if(!dirty){fromRoom();renderTable(true);}
  sizeCanvas(); pollCal();
}
$('#cbtoggle').onclick=async()=>{const on=!ME.cloud_blocked; if(!confirm((on?'Block':'Unblock')+' the Syng cloud on this speaker?'+(on?'':' Syng\'s servers will then overwrite the local room document when the speaker reconnects.')))return;
  try{await api('/api/cloudblock',{on});toast(on?'Cloud blocked':'Cloud unblocked');}catch(e){toast(e.message,true);} setTimeout(loadMe,1500);};
$('#showdoc').onclick=async()=>{const d=await api('/api/room/doc');const el=$('#doc');el.style.display=el.style.display==='none'?'block':'none';el.textContent=JSON.stringify(d,null,1);};
$('#restore').onclick=async()=>{if(!confirm('Restore the state document saved before the agent first changed it on THIS speaker? (Only this speaker; others keep their documents.)'))return;
  try{const b=await api('/api/room/backup');await api('/api/room/restore',{state:b});toast('Restored');}catch(e){toast(e.message,true);} setTimeout(refreshAll,2000);};
const fmtB=b=>b>=1048576?(b/1048576).toFixed(0)+' MB':b>=1024?(b/1024).toFixed(0)+' KB':b+' B';
async function loadRing(){try{const r=await api('/api/ring');$('#ringsteps').value=r.steps||100;$('#ringcw').checked=!!r.clockwise_up;$('#uisnd').value=r.ui_sound_pct==null?25:r.ui_sound_pct;}catch(e){}}
$('#ringsave').onclick=async()=>{try{const r=await api('/api/ring',{steps:+$('#ringsteps').value,clockwise_up:$('#ringcw').checked,ui_sound_pct:+$('#uisnd').value});toast('Applied ('+(r.changed||[]).join(', ')+')');}catch(e){toast(e.message,true);}loadRing();};
async function loadHK(){
  try{const h=await api('/api/housekeeping');
    $('#hk').innerHTML=`<div class=row><span>/data used</span><span>${fmtB(h.data.total-h.data.free)} of ${fmtB(h.data.total)}</span></div>
      <div class=row><span>journal</span><span>${fmtB(h.journal_bytes)}</span></div>
      <div class=row><span>core dumps</span><span>${h.coredumps.length} · ${fmtB(h.coredump_bytes)}${h.coredumps.length?' · newest '+new Date(Math.max(...h.coredumps.map(c=>c.mtime))*1000).toLocaleString():''}</span></div>
      ${h.tmp.slice(0,4).map(t=>`<div class=row><span>${t.path}</span><span>${fmtB(t.bytes)}</span></div>`).join('')}
      <div class=row><span>biggest processes</span><span>${h.top.slice(0,5).map(p=>p.name+' '+fmtB(p.rss_kb*1024)+' ('+p.elapsed+')').join(' · ')}</span></div>`;
  }catch(e){$('#hk').textContent='n/a: '+e.message;}
}
$('#hkrefresh').onclick=loadHK;
$('#hkvac').onclick=async()=>{await api('/api/housekeeping',{action:'vacuum_journal'});toast('Journal vacuumed');loadHK();};
$('#hkcores').onclick=async()=>{if(!confirm('Delete all core dumps on this cell?'))return;const r=await api('/api/housekeeping',{action:'clear_coredumps'});toast('Removed '+r.removed+' file(s)');loadHK();};
$('#hksvc').onchange=async()=>{const n=$('#hksvc').value;if(!n)return;if(!confirm('Restart '+n+' on this cell?')){$('#hksvc').value='';return;}const r=await api('/api/housekeeping',{action:'restart_service',name:n});toast(n+': '+(r.out||r.error));$('#hksvc').value='';setTimeout(loadHK,2000);};
async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();}
window.addEventListener('resize',sizeCanvas);
refreshAll();
</script></body></html>
"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(b)
    def _json(self, code, obj): self._send(code, json.dumps(obj), "application/json")
    def do_GET(self):
        try:
            if self.path.startswith("/api/state"): self._json(200, collect())
            elif self.path.startswith("/api/stats"): self._json(200, stats())
            elif self.path.startswith("/api/viz/frame"): self._json(200, viz_frame())
            elif self.path.startswith("/api/viz/stream"):
                # Server-Sent Events: one frame per feed packet (~30/s); keepalives when idle; ends when the client goes away
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive"); self.end_headers()
                seen = -1; idle = 0
                while True:
                    with VIZ_COND:
                        if VIZ_SEQ[0] == seen: VIZ_COND.wait(0.5)
                        cur = VIZ_SEQ[0]
                    if cur == seen:
                        idle += 1
                        if idle % 4 == 0:
                            self.wfile.write(("data: %s\n\n" % json.dumps(viz_frame())).encode()); self.wfile.flush()
                        continue
                    idle = 0; seen = cur
                    self.wfile.write(("data: %s\n\n" % json.dumps(viz_frame())).encode()); self.wfile.flush()
            elif self.path.startswith("/api/viz/status"):
                st = get_state()
                self._json(200, {"primary": is_primary_cell(st), "ledPort": VIZ_LED_PORT,
                                 "members": room_member_ips(st), "subscribers": viz_subscribers()})
            elif self.path.startswith("/api/led"): self._json(200, led_status())
            elif self.path.startswith("/api/vision"):
                q = parse_qs(urlparse(self.path).query)
                self._json(200, {"name": q["name"][0], "content": vision_read(q["name"][0])})
            elif self.path in ("/", "/index.html"): self._send(200, PAGE, "text/html; charset=utf-8")
            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")
            elif self.path.startswith("/api/artwork"):
                q = parse_qs(urlparse(self.path).query); url = artwork_url((q.get("source") or ["airplay2"])[0])
                if not url: self._send(404, "no artwork", "text/plain"); return
                try:
                    with urllib.request.urlopen(url, timeout=5) as r:
                        data = r.read(); ctype = r.headers.get("Content-Type", "image/jpeg")
                    self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)
                except Exception as e:
                    self._send(502, "artwork fetch failed: %s" % e, "text/plain")
            else:
                r = lc_get(self.path)
                if r is None: self._send(404, "not found", "text/plain")
                else: self._json(200, r)
        except Exception:
            self._json(500, {"error": traceback.format_exc().splitlines()[-1]})
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if isinstance(body, list): body = {"results": body}   # pycalibrator PUTs a bare list
            p = self.path
            grp = (not body.get("_local")) and body.get("scope") == "group"
            if p.startswith("/api/tone"):
                r = set_tone(body.get("bass", 0), body.get("tilt", 0))
                log("TONE bass=%s tilt=%s -> set=%s" % (body.get("bass"), body.get("tilt"), r))
                self._json(200, {"ok": True, "set": r})
            elif p.startswith("/api/volume"):
                r = set_volume(body.get("source"), body.get("volume", 0))
                log("VOLUME %s=%s -> set=%s" % (body.get("source"), body.get("volume"), r))
                self._json(200, {"ok": True, "set": r})
            elif p.startswith("/api/source"):
                r = set_source(body["id"], bool(body.get("on", True)))
                log("SOURCE %s on=%s" % (body.get("id"), body.get("on")))
                self._json(200, {"ok": r})
            elif p.startswith("/api/mute"):
                r = set_bool_prop("mute", bool(body.get("on")))
                log("MUTE on=%s -> %s" % (body.get("on"), r)); self._json(200, {"ok": True, "set": r})
            elif p.startswith("/api/spatial"):
                done = {}
                if "height" in body: done["height"] = set_bool_prop("enableHeightProcessing", bool(body["height"]))
                if "beyond" in body: done["beyond"] = set_bool_prop("enableBeyondStereoDuplication", bool(body["beyond"]))
                log("SPATIAL %s" % done); self._json(200, {"ok": True, "set": done})
            elif p.startswith("/api/listener"):
                set_listener(body.get("x_m", 0), body.get("y_m", 0))
                log("LISTENER x=%s y=%s" % (body.get("x_m"), body.get("y_m")))
                self._json(200, {"ok": True})
            elif p.startswith("/api/roomeq"):
                room_eq(bool(body.get("on")))
                if grp: fanout(p, body)
                log("ROOMEQ on=%s grp=%s" % (body.get("on"), grp)); self._json(200, {"ok": True})
            elif p.startswith("/api/objects"):
                r = set_objects(body.get("x_m", 0), body.get("y_m", 0), body.get("focus"), body.get("rot"))
                log("OBJECTS c=(%s,%s) focus=%s rot=%s -> set=%s" % (body.get("x_m"), body.get("y_m"), body.get("focus"), body.get("rot"), r))
                self._json(200, {"ok": True, "set": r})
            elif p.startswith("/api/soundfield"):
                keys = ("ux", "uy", "rot", "width", "radius", "spatial", "snap"); args = {k: body.get(k) for k in keys if k in body}
                r = set_soundfield(**args)
                log("SOUNDFIELD %s -> set=%s" % (args, r))
                self._json(200, dict(FIELD, ok=True, set=r))
            elif p.startswith("/api/led/tune"):
                pairs = [(flag, str(body[k])) for k, flag in
                         (("beatSens", "--beat-sens"), ("floor", "--floor"), ("cycle", "--cycle"),
                          ("silentFade", "--silent-fade"), ("beatFade", "--beat-fade"))
                         if body.get(k) is not None]
                if pairs: set_led_flags(pairs)
                if grp: fanout(p, body)
                log("LED tune %s grp=%s" % (pairs, grp)); self._json(200, {"ok": True})
            elif p.startswith("/api/led/color"):
                led_color(body.get("r", 0), body.get("g", 0), body.get("b", 0), body.get("w", 0))
                if grp: fanout(p, body)
                log("LED color grp=%s" % grp); self._json(200, {"ok": True})
            elif p.startswith("/api/led/reactive"):
                set_reactive(body.get("mode"))
                if grp: fanout(p, body)
                log("LED reactive=%s grp=%s" % (body.get("mode"), grp)); self._json(200, {"ok": True})
            elif p.startswith("/api/led/palette"):
                set_led_flag("--palette", "shared" if body.get("palette") == "shared" else "free")
                if grp: fanout(p, body)
                log("LED palette=%s grp=%s" % (body.get("palette"), grp)); self._json(200, {"ok": True})
            elif p.startswith("/api/led/anim"):
                led_anim(body.get("action"), body.get("name"))
                if grp: fanout(p, body)
                log("LED anim=%s grp=%s" % (body.get("action"), grp)); self._json(200, {"ok": True})
            elif p.startswith("/api/viz/subscribe"):
                ok = viz_subscribe(body["ip"], body.get("port", VIZ_LED_PORT))
                log("VIZ subscribe(req) %s:%s -> %s" % (body.get("ip"), body.get("port"), ok))
                self._json(200, {"ok": ok})
            elif p.startswith("/api/vision"):
                vision_write(body["name"], body["content"])
                if grp: fanout(p, body)
                self._json(200, {"ok": True})
            else:
                r = lc_post(p, body)
                if r is None: self._send(404, "not found", "text/plain")
                else:
                    if p.startswith("/api/room") or p.startswith("/api/calibration") or p.startswith("/api/wifi"): log("%s %s -> %s" % (p, json.dumps(body)[:160], json.dumps(r)[:200]))
                    self._json(200, r)
        except Exception:
            log("POST %s ERROR %s" % (self.path, traceback.format_exc().splitlines()[-1]))
            self._json(500, {"error": traceback.format_exc().splitlines()[-1]})

def main():
    sys.stderr.write("syng-agent (b31) on http://0.0.0.0:%d\n" % PORT)
    threading.Thread(target=viz_fanout_loop, daemon=True).start()
    threading.Thread(target=viz_web_loop, daemon=True).start()
    lc_start()
    load_settings(); field_load()
    threading.Thread(target=field_keeper_loop, daemon=True).start()
    threading.Thread(target=sleep_loop, daemon=True).start()
    threading.Thread(target=volume_guard_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

if __name__ == "__main__":
    main()

