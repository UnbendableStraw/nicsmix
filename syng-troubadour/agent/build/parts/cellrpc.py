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
FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "version": "0"}

def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, **legacy):
    """Set the stereo upmix 'handle' exactly like the Syng app does: x/y in room-circle units
    (reference circle = 1, y up), rotation (deg, CCW, 90 = the top of the room view), surround_width
    (deg, the dot half-angle) and radius (0 = classic single-puck stereo; 0.7 = spatial default).
    The cell's cellbroker runs the same spatialisation shader the app's cloud path uses."""
    if ux is not None: FIELD["ux"] = max(-1.0, min(1.0, float(ux)))
    if uy is not None: FIELD["uy"] = max(-1.0, min(1.0, float(uy)))
    if rot is not None: FIELD["rot"] = float(rot) % 360.0
    if width is not None: FIELD["width"] = max(0.0, min(180.0, float(width)))
    if spatial is not None: FIELD["spatial"] = bool(spatial)
    if radius is not None: FIELD["radius"] = max(0.0, min(1.0, float(radius)))
    elif spatial is not None: FIELD["radius"] = 0.7 if FIELD["spatial"] else 0.0
    FIELD["version"] = str(uuid.uuid4()).upper()
    handles = {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),
                     "rotation": round(FIELD["rot"], 3), "radius": round(FIELD["radius"] if FIELD["spatial"] else 0.0, 3),
                     "surround_width": round(FIELD["width"], 3), "snap_to_cell": "", "snapped_rotation": 0.0,
                     "version": FIELD["version"]}}
    n = 0
    if CELL is None: return 0
    for sid in source_ids():
        if sid not in MUSIC: continue
        try:
            if not get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"): continue
            CELL.call("source_set_stem_positions", {"source_id": sid, "handles": handles, "skip_persist": True}); n += 1
        except Exception as e:
            log("stem positions %s: %s" % (sid, e))
    return n

