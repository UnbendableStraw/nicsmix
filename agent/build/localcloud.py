# ====================================================================================
# b26 — local cloud: the agent replaces Syng's cloud for room membership, roles,
# layout, reference circle / listener, calibration results, plus a USB setup link
# and Wi-Fi provisioning. Everything is driven through cloudsyncd.PatchState, which
# emits PropertiesChanged -> celldirector / cellbroker / syngsoundserver react exactly
# as if the state came from api.prod.syng.space.
# ====================================================================================
import socket, struct, random, ipaddress, math, re, threading, subprocess, urllib.request

AGENT_BUILD = "b26"
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
            "cloud_blocked": os.path.exists(CLOUD_BLOCK_MARK), "agent": AGENT_BUILD,
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
def lc_get(path):
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
    if path.startswith("/api/cloudblock"): return cloud_block(bool(body.get("on")))
    if path.startswith("/api/usbnet"): return {"ok": True, "up": usbnet_up()}
    return None

def lc_start():
    _ensure_dir(); backup_state_once()
    try: log("usbnet %s" % usbnet_up())
    except Exception as e: log("usbnet: %s" % e)
    threading.Thread(target=keeper_loop, daemon=True).start()
