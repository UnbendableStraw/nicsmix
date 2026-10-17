#!/usr/bin/env python3
"""syng-agent-b44.py = b43 +
- FIRMWARE (setup page): in-place A/B updates. The cells are stock mender A/B (rootfs slots p2/p3, U-Boot
  `mender_boot_part` / `upgrade_available` / `bootcount` with bootlimit 1 → automatic fallback). Upload a rootfs image
  (build-troubadour.sh now writes troubadour-rootfs-<artifact>.img.gz next to image.sdcard), the agent writes it to the
  idle slot, verifies it by reading it back, carries this cell's Troubadour bits across (agent, LED daemon, cloud block,
  authorized keys, root password), flips the boot slot with upgrade_available=1 and reboots; the agent on the new slot
  commits once the stack is up, U-Boot rolls back by itself if it never gets that far. Room-wide: send the image to
  every member and install on all (secondaries first, primary last). Rollback = boot the other slot.
- NETWORK (setup page): Wi-Fi / Ethernet power, every connman service with its IPv4 (DHCP or static address, netmask,
  gateway, DNS), interfaces with MAC addresses."""
import os, re
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b43.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

PY = r'''
# ---------- network settings: connman technologies + per-service IPv4 ----------
def net_technologies():
    out = {}; cur = None
    for line in _sh("connmanctl technologies", 8).splitlines():
        line = line.rstrip()
        if line.startswith("/net/connman/technology/"): cur = line.rsplit("/", 1)[-1]; out[cur] = {}
        elif cur and "=" in line:
            k, v = [x.strip() for x in line.split("=", 1)]; out[cur][k.lower()] = (v == "True") if v in ("True", "False") else v
    return out

def _parse_bracket(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"): v = v[1:-1].strip()
    d = {}; items = []
    for part in [p.strip() for p in v.split(",") if p.strip()]:
        if "=" in part: k, x = part.split("=", 1); d[k.strip()] = x.strip()
        else: items.append(part)
    return d if d else items

def net_service(sid):
    p = {"id": sid}
    for line in _sh("connmanctl services %s" % sid, 8).splitlines():
        if "=" not in line: continue
        k, v = [x.strip() for x in line.strip().split("=", 1)]
        if k in ("Type", "State", "Name", "Favorite", "AutoConnect", "Strength", "Security", "Error"): p[k.lower()] = v
        elif k in ("IPv4", "IPv4.Configuration", "Ethernet", "Nameservers", "Nameservers.Configuration", "Domains"): p[k.lower()] = _parse_bracket(v)
    return p

def net_services():
    out = []
    for line in _sh("connmanctl services", 8).splitlines():
        m = re.match(r"([ *AOR]{0,4})\s*(.*?)\s+(\S+)$", line.rstrip())
        if not m: continue
        flags, name, sid = m.groups()
        if not (sid.startswith("wifi_") or sid.startswith("ethernet_")): continue
        if not ("R" in flags or "O" in flags or "A" in flags or sid.startswith("ethernet_")): continue   # connected or saved only
        d = net_service(sid); d["name"] = d.get("name") or name; d["flags"] = flags.strip(); out.append(d)
    return out

def net_info():
    gw = None
    for line in _sh("ip route", 5).splitlines():
        if line.startswith("default"): gw = line; break
    macs = {}
    for line in _sh("ip -o link", 5).splitlines():
        m = re.match(r"\d+:\s+(\S+?)(?:@\S+)?:.*link/\w+\s+([0-9a-f:]{17})", line)
        if m: macs[m.group(1)] = m.group(2)
    return {"technologies": net_technologies(), "services": net_services(), "interfaces": [{"ifname": i, "ip": ip, "plen": pl, "mac": macs.get(i)} for i, ip, pl in iface_addrs()],
            "default_route": gw, "hostname": socket.gethostname()}

def net_power(tech, on, force=False):
    if tech not in ("wifi", "ethernet"): raise RuntimeError("tech must be wifi or ethernet")
    if not on and not force:
        # don't saw off the branch: refuse to power down the only technology that carries a LAN address
        others = [i for i, ip, pl in iface_addrs() if not i.startswith(("usb", "wsyng")) and not (tech == "wifi" and i.startswith("w")) and not (tech == "ethernet" and i.startswith(("e", "en")))]
        if not others: raise RuntimeError("that is the only network link this speaker has — plug in another one first, or pass force")
    out = _sh("connmanctl %s %s" % ("enable" if on else "disable", tech), 15)
    return {"ok": "Error" not in out, "out": out.strip()[-200:]}

def net_config(sid, method, address=None, netmask=None, gateway=None, dns=None):
    if not re.match(r"^(wifi|ethernet)_[A-Za-z0-9_]+$", sid or ""): raise RuntimeError("bad service id")
    outs = []
    if method == "dhcp":
        outs.append(_sh("connmanctl config %s --ipv4 dhcp" % sid, 15))
        outs.append(_sh("connmanctl config %s --nameservers" % sid, 15) if not dns else _sh("connmanctl config %s --nameservers %s" % (sid, " ".join(re.findall(r"[0-9.]+", dns))), 15))
    elif method == "manual":
        for v in (address, netmask, gateway):
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", v or ""): raise RuntimeError("address, netmask and gateway must be IPv4 dotted quads")
        outs.append(_sh("connmanctl config %s --ipv4 manual %s %s %s" % (sid, address, netmask, gateway), 15))
        if dns: outs.append(_sh("connmanctl config %s --nameservers %s" % (sid, " ".join(re.findall(r"[0-9.]+", dns))), 15))
    else: raise RuntimeError("method must be dhcp or manual")
    return {"ok": not any("Error" in o for o in outs), "out": " | ".join(o.strip()[-120:] for o in outs if o.strip()), "service": net_service(sid)}

# ---------- firmware: in-place A/B slot updates (stock mender U-Boot integration) ----------
FW_DIR = "/data/firmware"; FW_IMG = FW_DIR + "/rootfs.img.gz"; FW_META = FW_DIR + "/image.json"; FW_LAST = FW_DIR + "/last-install.json"
FW = {"busy": None, "log": [], "progress": 0.0, "room": None, "error": None, "done": None}
FW_LOCK = threading.Lock()
ROOTFS_BLOCKS_KIB = 2097152   # p2/p3 are 2 GiB each
CARRY = ["/usr/bin/syng-agent", "/usr/bin/syng-led-reactive", "/usr/bin/syng-led", "/usr/bin/syng-cloud-block", "/usr/bin/syng-dbus-dump",
         "/etc/default/syng-agent", "/etc/default/syng-led-reactive", "/etc/ssh/authorized_keys.d/root"]

def fw_log(msg):
    FW["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg)); FW["log"] = FW["log"][-60:]; log("FW " + msg)

def fw_env():
    out = {}
    for line in _sh("fw_printenv mender_boot_part upgrade_available bootcount bootlimit 2>/dev/null", 10).splitlines():
        if "=" in line: k, v = line.split("=", 1); out[k.strip()] = v.strip()
    return out

def fw_active_part():
    m = re.search(r"root=/dev/mmcblk2p(\d)", _read("/proc/cmdline"))
    return int(m.group(1)) if m else None

def fw_slot_info(part):
    """artifact / version / agent build of a rootfs slot, by mounting it read-only for a moment."""
    mp = "/mnt/slot%d" % part; info = {"part": part}
    try:
        os.makedirs(mp, exist_ok=True)
        if part == fw_active_part():
            root = ""
        else:
            _sh("mount -o ro /dev/mmcblk2p%d %s" % (part, mp), 20); root = mp
        a = _read(root + "/etc/mender/artifact_info"); info["artifact"] = a.split("=", 1)[1].strip() if "=" in a else None
        info["version"] = _read(root + "/etc/version").strip() or None
        ag = _read(root + "/usr/bin/syng-agent")[:400000]
        m = re.search(r'AGENT_BUILD = "(b\d+)"', ag); info["agent"] = m.group(1) if m else (None if ag else "none")
    except Exception as e: info["error"] = str(e)[:80]
    finally:
        if part != fw_active_part(): _sh("umount %s" % mp, 20)
    return info

_slot_cache = {"t": 0, "v": None}
def fw_status():
    act = fw_active_part(); other = 3 if act == 2 else 2
    now = time.time()
    if now - _slot_cache["t"] > 60 or _slot_cache["v"] is None:
        _slot_cache["v"] = fw_slot_info(other); _slot_cache["t"] = now
    meta = None
    try: meta = json.loads(_read(FW_META, "") or "null")
    except Exception: meta = None
    if meta and not os.path.exists(FW_IMG): meta = None
    last = None
    try: last = json.loads(_read(FW_LAST, "") or "null")
    except Exception: last = None
    return {"running": {"artifact": fw_version(), "version": _read("/etc/version").strip(), "part": act, "agent": AGENT_BUILD},
            "other": _slot_cache["v"], "env": fw_env(), "image": meta, "last": last,
            "busy": FW["busy"], "progress": FW["progress"], "log": FW["log"][-25:], "room": FW["room"], "error": FW["error"], "done": FW["done"],
            "free_bytes": (lambda sv: sv.f_bavail * sv.f_frsize)(os.statvfs("/data"))}

def _fw_finalize(tmp, name, h, size, source):
    if size < 50 * 1024 * 1024: raise RuntimeError("that is too small to be a rootfs image (%d bytes)" % size)
    with open(tmp, "rb") as f: magic = f.read(2)
    if magic != b"\x1f\x8b": raise RuntimeError("not a gzip file — expected troubadour-rootfs-<artifact>.img.gz")
    os.replace(tmp, FW_IMG)
    m = re.search(r"rootfs-([A-Za-z0-9._-]+?)\.img", name or "")
    meta = {"name": name, "size": size, "sha256": h, "artifact": m.group(1) if m else None, "at": time.time(), "source": source}
    open(FW_META, "w").write(json.dumps(meta)); _slot_cache["t"] = 0
    return meta

def fw_receive(stream, length, name, source):
    """Stream an image into /data/firmware with a running sha256 (browser upload or a pull from another cell)."""
    os.makedirs(FW_DIR, exist_ok=True)
    tmp = FW_IMG + ".part"; h = hashlib.sha256(); got = 0
    FW["busy"] = "receiving"; FW["progress"] = 0.0; FW["error"] = None
    try:
        with open(tmp, "wb") as f:
            while length is None or got < length:
                chunk = stream.read(min(1 << 20, (length - got) if length else 1 << 20))
                if not chunk: break
                f.write(chunk); h.update(chunk); got += len(chunk)
                if length: FW["progress"] = got / float(length)
            f.flush(); os.fsync(f.fileno())
        if length and got != length: raise RuntimeError("short upload: %d of %d bytes" % (got, length))
        meta = _fw_finalize(tmp, name, h.hexdigest(), got, source)
        fw_log("image received: %s %.0f MB sha256 %s…" % (name, got / 1e6, meta["sha256"][:12]))
        return meta
    except Exception as e:
        try: os.remove(tmp)
        except Exception: pass
        FW["error"] = str(e)[:160]; fw_log("receive failed: %s" % e); raise
    finally:
        FW["busy"] = None; FW["progress"] = 0.0

def fw_fetch(ip):
    """Pull the image another cell holds (room distribution: members pull from the primary)."""
    if not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip or ""): raise RuntimeError("bad ip")
    meta = agent_get(ip, "/api/firmware", timeout=5).get("image")
    if not meta: raise RuntimeError("%s has no image" % ip)
    mine = None
    try: mine = json.loads(_read(FW_META, "") or "null")
    except Exception: pass
    if mine and os.path.exists(FW_IMG) and mine.get("sha256") == meta.get("sha256"): return dict(mine, already=True)
    req = urllib.request.Request("http://%s:%d/api/firmware/image" % (ip, PORT))
    with urllib.request.urlopen(req, timeout=60) as r:
        n = int(r.headers.get("Content-Length") or 0) or None
        got = fw_receive(r, n, meta.get("name"), "from %s" % ip)
    if got["sha256"] != meta.get("sha256"): raise RuntimeError("sha256 mismatch after transfer")
    return got

def fw_install(reboot=True, carry=True):
    with FW_LOCK:
        if FW["busy"]: raise RuntimeError("busy: %s" % FW["busy"])
        if not os.path.exists(FW_IMG): raise RuntimeError("no image on this speaker — upload one first")
        FW["busy"] = "installing"; FW["progress"] = 0.0; FW["error"] = None; FW["done"] = None; FW["log"] = []
    def run():
        act = fw_active_part(); tgt = 3 if act == 2 else 2; dev = "/dev/mmcblk2p%d" % tgt
        try:
            meta = json.loads(_read(FW_META, "{}") or "{}")
            fw_log("installing %s into slot p%d (running from p%d)" % (meta.get("name"), tgt, act))
            import gzip
            h = hashlib.sha256(); written = 0; cap = ROOTFS_BLOCKS_KIB * 1024; gz_size = os.path.getsize(FW_IMG)
            with gzip.open(FW_IMG, "rb") as src, open(dev, "wb", buffering=0) as dst:
                while True:
                    chunk = src.read(4 << 20)
                    if not chunk: break
                    if written + len(chunk) > cap: raise RuntimeError("image larger than the slot (%d bytes)" % cap)
                    dst.write(chunk); h.update(chunk); written += len(chunk)
                    try: FW["progress"] = 0.75 * min(1.0, src.fileobj.tell() / float(gz_size))
                    except Exception: pass
                dst.flush(); os.fsync(dst.fileno())
            want = h.hexdigest(); fw_log("wrote %.0f MB, verifying…" % (written / 1e6))
            h2 = hashlib.sha256(); left = written
            with open(dev, "rb", buffering=0) as f:
                while left > 0:
                    b = f.read(min(4 << 20, left))
                    if not b: break
                    h2.update(b); left -= len(b); FW["progress"] = 0.75 + 0.2 * (1 - left / float(written))
            if h2.hexdigest() != want: raise RuntimeError("read-back differs from what was written — not switching slots")
            fw_log("verified (sha256 %s…)" % want[:12])
            mp = "/mnt/slot%d" % tgt; os.makedirs(mp, exist_ok=True)
            if _sh("mount %s %s && echo MOUNTED" % (dev, mp), 30).strip().endswith("MOUNTED"):
                try:
                    newart = _read(mp + "/etc/mender/artifact_info").strip()
                    if carry:
                        n = 0
                        for p in CARRY:
                            if not os.path.exists(p): continue
                            d = os.path.dirname(mp + p); os.makedirs(d, exist_ok=True)
                            _sh("cp -p %s %s" % (p, mp + p), 20); n += 1
                        # root password: keep this cell's
                        try:
                            me = next((l for l in _read("/etc/shadow").splitlines() if l.startswith("root:")), None)
                            sp = mp + "/etc/shadow"; lines = _read(sp).splitlines()
                            if me and lines:
                                lines = [me if l.startswith("root:") else l for l in lines]; open(sp, "w").write("\n".join(lines) + "\n")
                        except Exception as e: fw_log("shadow carry skipped: %s" % e)
                        fw_log("carried %d Troubadour files + root password into the new slot (%s)" % (n, newart))
                finally:
                    _sh("sync; umount %s" % mp, 30)
            else:
                raise RuntimeError("the new slot does not mount — not switching")
            scr = "/tmp/fw_env.txt"
            open(scr, "w").write("mender_boot_part %d\nmender_boot_part_hex %d\nupgrade_available 1\nbootcount 0\n" % (tgt, tgt))
            out = _sh("fw_setenv -s %s" % scr, 20)
            env = fw_env()
            if env.get("mender_boot_part") != str(tgt) or env.get("upgrade_available") != "1": raise RuntimeError("boot environment did not take: %s %s" % (env, out.strip()[-100:]))
            FW["progress"] = 1.0
            open(FW_LAST, "w").write(json.dumps({"at": time.time(), "from": fw_version(), "to": meta.get("artifact"), "slot": tgt, "state": "staged"}))
            fw_log("boot slot set to p%d with upgrade_available=1 — U-Boot falls back to p%d by itself if the new slot does not come up" % (tgt, act))
            FW["done"] = "staged"
            if reboot:
                fw_log("rebooting"); _slot_cache["t"] = 0
                time.sleep(2); _sh("systemctl reboot", 10)
        except Exception as e:
            FW["error"] = str(e)[:200]; fw_log("FAILED: %s" % e)
        finally:
            FW["busy"] = None
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True}

def fw_rollback():
    act = fw_active_part(); other = 3 if act == 2 else 2
    scr = "/tmp/fw_env.txt"; open(scr, "w").write("mender_boot_part %d\nmender_boot_part_hex %d\nupgrade_available 0\nbootcount 0\n" % (other, other))
    _sh("fw_setenv -s %s" % scr, 20); fw_log("boot slot set to p%d, rebooting" % other)
    threading.Timer(1.5, lambda: _sh("systemctl reboot", 10)).start()
    return {"ok": True, "slot": other}

def fw_commit_check():
    """On the new slot after an update: the stack is up (we are running, celldirector + sound server active) → commit,
    so U-Boot stops counting boots against this slot."""
    try:
        env = fw_env()
        if env.get("upgrade_available") != "1": return
        for _ in range(12):
            st = _sh("systemctl is-active celldirector syngsoundserver cloudsyncd", 10).split()
            if st and all(x == "active" for x in st): break
            time.sleep(10)
        else:
            fw_log("commit check: services not all active after 2 min; leaving upgrade_available=1 (U-Boot may fall back on the next boot)"); return
        _sh("fw_setenv upgrade_available 0 && fw_setenv bootcount 0", 20)
        try:
            last = json.loads(_read(FW_LAST, "{}") or "{}"); last["state"] = "committed"; last["committed_at"] = time.time(); last["running"] = fw_version()
            open(FW_LAST, "w").write(json.dumps(last))
        except Exception: pass
        fw_log("committed: running %s from p%s" % (fw_version(), fw_active_part()))
    except Exception as e:
        log("fw commit check: %s" % e)

def fw_room(action):
    """(primary) distribute: every member pulls the image from us. install: members install + reboot and come back on
    the new version one after another, then we do ourselves."""
    me = whoami(); myip = me.get("ip")
    if (me.get("role") or "").lower() == "secondary": raise RuntimeError("run this on the room's primary")
    if FW["room"] and FW["room"].get("busy"): raise RuntimeError("a room operation is already running")
    doc = room_doc(); members = [(sn, c.get("ip_address")) for sn, c in doc["cells"].items() if sn != doc["me"] and c.get("ip_address")]
    FW["room"] = {"action": action, "busy": True, "steps": {}, "started": time.time()}
    def run():
        try:
            if action == "distribute":
                for sn, ip in members:
                    FW["room"]["steps"][sn] = "sending"
                    try:
                        r = agent_post(ip, "/api/firmware/fetch", {"ip": myip}, timeout=900)
                        FW["room"]["steps"][sn] = "ok" if r.get("sha256") else "failed"
                    except Exception as e: FW["room"]["steps"][sn] = "failed: %s" % str(e)[:60]
            elif action == "install":
                for sn, ip in members:
                    try:
                        st = agent_get(ip, "/api/firmware", timeout=5)
                        if not st.get("image"): FW["room"]["steps"][sn] = "no image (send first)"; continue
                        FW["room"]["steps"][sn] = "installing"
                        agent_post(ip, "/api/firmware/install", {"reboot": True}, timeout=10)
                        ok = False; t0 = time.time(); was = st.get("running", {}).get("artifact")
                        while time.time() - t0 < 600:
                            time.sleep(10)
                            try:
                                cur = agent_get(ip, "/api/firmware", timeout=4)
                                if cur.get("running", {}).get("part") != st.get("running", {}).get("part"): ok = True; break
                                if cur.get("error") and not cur.get("busy"): FW["room"]["steps"][sn] = "failed: " + cur["error"][:60]; break
                            except Exception: pass
                        if ok: FW["room"]["steps"][sn] = "up on new slot"
                        elif FW["room"]["steps"][sn] == "installing": FW["room"]["steps"][sn] = "no answer after 10 min"
                    except Exception as e: FW["room"]["steps"][sn] = "failed: %s" % str(e)[:60]
                FW["room"]["steps"][doc["me"]] = "installing (this speaker, last)"
                fw_install(reboot=True)
        finally:
            FW["room"]["busy"] = False; FW["room"]["finished"] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "members": [sn for sn, _ in members]}
'''
rep("# ---------- the room document ----------", PY + "\n# ---------- the room document ----------")
rep("import urllib.request, threading, socket, struct, math", "import urllib.request, urllib.parse, threading, socket, struct, math, hashlib")

# GET endpoints
rep('''def lc_get(path):
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()''', '''def lc_get(path):
    if path.startswith("/api/net"): return net_info()
    if path.startswith("/api/firmware"): return fw_status()
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()''')
# POST endpoints
rep('''    if path.startswith("/api/wifi/connect"): return wifi_connect(body["ssid"], body.get("psk"), bool(body.get("hidden")))''',
    '''    if path.startswith("/api/net/power"): return net_power(body.get("tech"), bool(body.get("on")), bool(body.get("force")))
    if path.startswith("/api/net/config"): return net_config(body.get("service"), body.get("method"), body.get("address"), body.get("netmask"), body.get("gateway"), body.get("dns"))
    if path.startswith("/api/firmware/fetch"): return fw_fetch(body.get("ip"))
    if path.startswith("/api/firmware/install"): return fw_install(bool(body.get("reboot", True)), bool(body.get("carry", True)))
    if path.startswith("/api/firmware/rollback"): return fw_rollback()
    if path.startswith("/api/firmware/room"): return fw_room(body.get("action"))
    if path.startswith("/api/wifi/connect"): return wifi_connect(body["ssid"], body.get("psk"), bool(body.get("hidden")))''')
# raw upload + image download in the handler (not JSON)
rep('''    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")''', '''    def do_POST(self):
        if self.path.startswith("/api/firmware/upload"):
            try:
                n = int(self.headers.get("Content-Length", 0)) or None
                name = urllib.parse.unquote(self.headers.get("X-Filename", "") or "rootfs.img.gz")
                meta = fw_receive(self.rfile, n, os.path.basename(name), "upload")
                self._json(200, dict(meta, ok=True))
            except Exception as e:
                self._json(400, {"error": str(e)[:200]})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")''')
rep('''            elif self.path.startswith("/fonts/"):''', '''            elif self.path.startswith("/api/firmware/image"):
                if not os.path.exists(FW_IMG): self._send(404, "no image", "text/plain"); return
                size = os.path.getsize(FW_IMG)
                self.send_response(200); self.send_header("Content-Type", "application/gzip"); self.send_header("Content-Length", str(size)); self.end_headers()
                with open(FW_IMG, "rb") as f:
                    while True:
                        b = f.read(1 << 20)
                        if not b: break
                        self.wfile.write(b)
            elif self.path.startswith("/fonts/"):''')
# commit check at start
rep('''    if SETTINGS.get("sound_mode") == "direct":
        threading.Timer(25.0, lambda: apply_sound_mode("direct", local_only=True)).start()''', '''    if SETTINGS.get("sound_mode") == "direct":
        threading.Timer(25.0, lambda: apply_sound_mode("direct", local_only=True)).start()
    threading.Timer(45.0, fw_commit_check).start()''')

# ---- setup page: NETWORK settings + FIRMWARE card ----
rep('''  <p class=hint>Setup link: plug this speaker into a laptop with a USB-C cable and open <b>http://10.86.0.1:8080/setup</b> (the other port is 10.86.1.1). It also works over a USB-C Ethernet adapter or Wi-Fi.</p>
</div>''', '''  <details style="margin-top:10px"><summary>Network settings — radios, addresses, DNS</summary>
    <div id=netsettings class=muted style="margin-top:8px">…</div></details>
  <p class=hint>Setup link: plug this speaker into a laptop with a USB-C cable and open <b>http://10.86.0.1:8080/setup</b> (the other port is 10.86.1.1). It also works over a USB-C Ethernet adapter or Wi-Fi.</p>
</div>''')
rep('''<div class=card><details><summary>Advanced</summary>''', '''<div class=card id=fwcard><h2>FIRMWARE <span><button class="btn sm" id=fwrefresh>Refresh</button></span></h2>
  <div id=fwinfo class=muted>…</div>
  <div class=rowb style="margin-top:10px">
    <input type=file id=fwfile accept=".gz,.img.gz,application/gzip" style="display:none">
    <button class="btn" id=fwupload>Upload image…</button>
    <button class="btn pri" id=fwinstall>Install on this speaker</button>
    <button class="btn" id=fwsend>Send to all speakers</button>
    <button class="btn" id=fwroom>Install on all speakers</button>
    <button class="btn danger sm" id=fwroll>Boot the other slot</button>
    <span class=muted id=fwbusy></span></div>
  <div class=log id=fwlog style="display:none;margin-top:8px"></div>
  <p class=hint>The speakers keep two system slots (p2 / p3). An update is written to the idle one, read back and verified, your Troubadour files (agent, LED daemon, cloud block, keys, root password) are carried over, then the boot slot is switched with U-Boot's <i>upgrade_available</i> set, so a slot that does not come up is undone by the bootloader on the next boot. Settings, rooms, Wi-Fi and calibration live on the data partition and survive. Images come from <code>tools/fw/build-troubadour.sh</code> (<code>troubadour-rootfs-&lt;artifact&gt;.img.gz</code>, next to image.sdcard). Room-wide: <b>Send</b> copies the image to every member, <b>Install on all</b> updates the secondaries one by one and this primary last.</p>
</div>

<div class=card><details><summary>Advanced</summary>''')

JS = r'''
// ---------- network settings ----------
async function loadNet(){
  const box=$('#netsettings'); if(!box||box.closest('details').open===false&&box.dataset.loaded)return;
  let n; try{n=await api('/api/net');}catch(e){box.textContent='unavailable: '+e.message;return;}
  box.dataset.loaded='1';
  const T=n.technologies||{}; const tech=(k,l)=>{const t=T[k]||{}; return `<div class=item><span class=grow><b>${l}</b> <span class=k>${t.powered?(t.connected?'on · connected':'on'):'off'}</span></span><button class="btn sm" data-tech="${k}" data-on="${t.powered?0:1}">${t.powered?'Turn off':'Turn on'}</button></div>`;};
  const svc=(S)=>{const ip=S.ipv4||{}, cfg=S['ipv4.configuration']||{}, eth=S.ethernet||{}; const manual=(cfg.Method||ip.Method)==='manual'; const ns=(S.nameservers||[]).join(' ');
    return `<div class=item style="flex-direction:column;align-items:stretch;gap:6px"><div><b>${S.name||S.id}</b> <span class=k>${S.type} · ${S.state||''} · ${eth.Interface||''} ${eth.Address||''}</span></div>
      <div class=k>${ip.Address||'—'} / ${ip.Netmask||'—'} via ${ip.Gateway||'—'} · DNS ${ns||'—'} · ${manual?'static':'DHCP'}</div>
      <form class=rowb data-svc="${S.id}" style="margin:0"><select name=method><option value=dhcp ${manual?'':'selected'}>DHCP</option><option value=manual ${manual?'selected':''}>Static</option></select>
        <input type=text name=address placeholder="Address" value="${manual?(ip.Address||''):''}" style="width:130px"><input type=text name=netmask placeholder="Netmask" value="${ip.Netmask||'255.255.255.0'}" style="width:130px"><input type=text name=gateway placeholder="Gateway" value="${ip.Gateway||''}" style="width:130px"><input type=text name=dns placeholder="DNS (space-separated)" value="${(S['nameservers.configuration']||[]).join(' ')}" style="width:170px"><button class="btn sm pri">Apply</button></form></div>`;};
  box.innerHTML=`<div class=list>${tech('wifi','Wi-Fi')}${tech('ethernet','Ethernet')}</div>
    <div class=hint>Turning Wi-Fi off keeps this speaker out of any room: the firmware needs Wi-Fi for the room link (SyFi) and goes into "Wi-Fi repair" without it. Fine for a standalone wired speaker. The agent refuses to switch off the only link it is reached on.</div>
    <div class=list style="margin-top:10px">${(n.services||[]).map(svc).join('')||'<div class=muted>no services</div>'}</div>
    <div class=hint>Interfaces: ${(n.interfaces||[]).map(i=>`${i.ifname} ${i.ip}/${i.plen}${i.mac?' · '+i.mac:''}`).join(' · ')}. ${n.default_route?'Default route: '+n.default_route:''} A static address set here is kept by connman for that network; switching back to DHCP clears it.</div>`;
  box.querySelectorAll('[data-tech]').forEach(b=>b.onclick=async()=>{const on=b.dataset.on==='1'; if(!on&&!confirm('Turn '+b.dataset.tech+' off on this speaker?'))return; try{const r=await api('/api/net/power',{tech:b.dataset.tech,on}); toast(r.out||'done');}catch(e){toast(e.message,true);} setTimeout(()=>{box.dataset.loaded='';loadNet();},2500);});
  box.querySelectorAll('form[data-svc]').forEach(f=>f.onsubmit=async e=>{e.preventDefault(); const d=Object.fromEntries(new FormData(f).entries()); d.service=f.dataset.svc;
    if(!confirm((d.method==='manual'?'Set '+d.address+' on ':'Use DHCP on ')+d.service+'? If this is the link you are using, the page may move to the new address.'))return;
    try{const r=await api('/api/net/config',d); toast(r.ok?'Applied':'Failed: '+r.out,!r.ok);}catch(e){toast(e.message,true);} setTimeout(()=>{box.dataset.loaded='';loadNet();},3000);});
}
document.querySelector('#netsettings')&&document.querySelector('#netsettings').closest('details').addEventListener('toggle',e=>{if(e.target.open)loadNet();});

// ---------- firmware ----------
let fwTimer=null;
function fmtMB(b){return b==null?'—':(b/1e6).toFixed(0)+' MB';}
async function loadFw(){
  let f; try{f=await api('/api/firmware');}catch(e){$('#fwinfo').textContent='unavailable: '+e.message;return;}
  const r=f.running||{}, o=f.other||{}, im=f.image, env=f.env||{};
  const slot=p=>p===2?'A (p2)':p===3?'B (p3)':'?';
  $('#fwinfo').innerHTML=`<div class=row><span>Running</span><span><b>${r.artifact||'?'}</b> · slot ${slot(r.part)} · agent ${r.agent||''}${env.upgrade_available==='1'?' · <span class=warn>not yet committed</span>':''}</span></div>
    <div class=row><span>Other slot</span><span>${o.artifact||o.error||'empty'}${o.agent&&o.agent!=='none'?' · agent '+o.agent:''}</span></div>
    <div class=row><span>Image on this speaker</span><span>${im?`<b>${im.name}</b> · ${fmtMB(im.size)} · ${im.artifact||''} · sha ${(im.sha256||'').slice(0,10)}…`:'none'}</span></div>
    ${f.last?`<div class=row><span>Last install</span><span>${f.last.state||''} → ${f.last.to||''} (slot p${f.last.slot}) ${f.last.running?'· running '+f.last.running:''}</span></div>`:''}
    ${f.room?`<div class=row><span>Room</span><span>${f.room.action} ${f.room.busy?'· running':''} — ${Object.entries(f.room.steps||{}).map(([k,v])=>k.slice(-5)+': '+v).join(' · ')||'…'}</span></div>`:''}`;
  $('#fwbusy').textContent=f.busy?(f.busy+' '+Math.round((f.progress||0)*100)+'%'):(f.error?'error: '+f.error:'');
  const lg=$('#fwlog'); if((f.log||[]).length){lg.style.display='block';lg.textContent=f.log.join('\n');lg.scrollTop=1e6;}
  $('#fwinstall').disabled=!im||!!f.busy; $('#fwsend').disabled=!im||!!f.busy; $('#fwroom').disabled=!!f.busy;
  clearTimeout(fwTimer); if(f.busy||(f.room&&f.room.busy)) fwTimer=setTimeout(loadFw,2000);
}
$('#fwrefresh').onclick=loadFw;
$('#fwupload').onclick=()=>$('#fwfile').click();
$('#fwfile').onchange=async()=>{const file=$('#fwfile').files[0]; if(!file)return; if(!/\.gz$/i.test(file.name)){toast('Expected troubadour-rootfs-<artifact>.img.gz',true);return;}
  $('#fwbusy').textContent='uploading 0%'; const xhr=new XMLHttpRequest(); xhr.open('POST','/api/firmware/upload'); xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name)); xhr.setRequestHeader('Content-Type','application/octet-stream');
  xhr.upload.onprogress=e=>{if(e.lengthComputable)$('#fwbusy').textContent='uploading '+Math.round(e.loaded/e.total*100)+'%';};
  xhr.onload=()=>{try{const j=JSON.parse(xhr.responseText); if(j.error)toast('Upload: '+j.error,true); else toast('Image stored: '+j.name);}catch(e){toast('Upload failed',true);} loadFw();};
  xhr.onerror=()=>{toast('Upload failed',true);loadFw();}; xhr.send(file);};
$('#fwinstall').onclick=async()=>{if(!confirm('Write the image to the idle slot, switch to it and reboot this speaker? Takes 3–5 minutes; the bootloader falls back by itself if the new slot does not come up.'))return;
  try{await api('/api/firmware/install',{reboot:true});toast('Installing…');}catch(e){toast(e.message,true);} loadFw();};
$('#fwsend').onclick=async()=>{try{const r=await api('/api/firmware/room',{action:'distribute'},primaryIp);toast('Sending to '+(r.members||[]).length+' speakers');}catch(e){toast(e.message,true);} loadFw();};
$('#fwroom').onclick=async()=>{if(!confirm('Install on every speaker in the room: secondaries one by one (each reboots), this primary last. Music stops. Continue?'))return;
  try{await api('/api/firmware/room',{action:'install'},primaryIp);toast('Room update started');}catch(e){toast(e.message,true);} loadFw();};
$('#fwroll').onclick=async()=>{if(!confirm('Reboot into the other slot? (whatever is there now)'))return; try{await api('/api/firmware/rollback',{});toast('Rebooting into the other slot');}catch(e){toast(e.message,true);}};
'''
rep("async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();}",
    JS + "\nasync function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();loadFw();}")

s = s.replace('"syng-agent (b43)"', '"syng-agent (b44)"').replace("syng-agent (b43) on", "syng-agent (b44) on").replace('AGENT_BUILD = "b43"', 'AGENT_BUILD = "b44"')
out = os.path.join(here, "syng-agent-b44.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
