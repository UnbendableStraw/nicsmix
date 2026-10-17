#!/usr/bin/env python3
"""syng-agent-b49.py = b48 + the room's cell-to-cell link can be "wired": audio over Ethernet and the shared clock
from PTP instead of SyFi's Wi-Fi hardware clock (docs/WIRED.md).
  - SourceCast.conf timesync cync|ntp (+ interface) managed per cell, backed up once, audio stack restarted on change
  - syng-ptp (our ptp4l build: /usr/bin/syng-ptp4l, software time stamping even on NICs that don't advertise it),
    primary = grandmaster (priority1 10), secondaries clientOnly; systemd-timesyncd stopped on clients
  - room document: syfi_ip <- wired IPv4 in wired mode (the primary streams its main packets there)
  - /api/link GET (mode, wired address, PTP state/offset, members) and POST {mode}, setup page controls
  - celldirector restart guard: a restart with Wi-Fi already up leaves stock celldirector in 'wifiRepair' after 20 s
    (no connected event arrives); the agent bounces the Wi-Fi service once if it sees that"""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b48.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---- link module (before the firmware module) ----
rep('''# ---------- firmware: in-place A/B slot updates (stock mender U-Boot integration) ----------''', r'''# ---------- cell-to-cell link: SyFi (stock) or Wired (Ethernet audio + PTP-disciplined wall clock) — docs/WIRED.md ----------
SC_CONF = "/etc/syng/SourceCast.conf"; SC_STOCK = LC_DIR + "/SourceCast.conf.stock"
PTP_CONF = LC_DIR + "/ptp4l.conf"; PTP_UNIT = "/etc/systemd/system/syng-ptp.service"; PTP_BIN = "/usr/bin/syng-ptp4l"; PMC_BIN = "/usr/bin/syng-pmc"
LINK = {"busy": None, "log": [], "last": None}

def wired_addr():
    """(ifname, ipv4) of the wired interface, or (None, None)."""
    for ifn, ip, pl in iface_addrs():
        if ifn.startswith(("eth", "en")) and not ifn.startswith("usb"): return ifn, ip
    return None, None

def sc_conf():
    try: return json.loads(_read(SC_CONF, "{}") or "{}")
    except Exception: return {}

def sc_set(timesync, iface):
    d = sc_conf()
    if not os.path.exists(SC_STOCK):
        try: open(SC_STOCK, "w").write(json.dumps(d, indent=2) + "\n")
        except Exception: pass
    if d.get("timesync") == timesync and d.get("timesyncInterface") == iface: return False
    d["timesync"] = timesync; d["timesyncInterface"] = iface
    open(SC_CONF, "w").write(json.dumps(d, indent=2) + "\n"); return True

def ptp_conf_text(iface, master):
    return ("[global]\ntime_stamping software\ndelay_mechanism E2E\nnetwork_transport UDPv4\n"
            "priority1 %d\nclientOnly %d\nstep_threshold 0.0\nfirst_step_threshold 0.00002\n"
            "tx_timestamp_timeout 50\nsummary_interval 3\nlogging_level 6\nlogSyncInterval -3\nlogMinDelayReqInterval -3\n"
            "[%s]\n" % (10 if master else 128, 0 if master else 1, iface))

def ptp_unit_text():
    return ("[Unit]\nDescription=Troubadour PTP (wired cell-to-cell clock)\nAfter=network.target\n\n"
            "[Service]\nExecStartPre=/bin/mkdir -p /var/run/ptp\nExecStart=%s -f %s\nRestart=always\nRestartSec=3\n\n"
            "[Install]\nWantedBy=multi-user.target\n" % (PTP_BIN, PTP_CONF))

def ptp_ensure(iface, master):
    """syng-ptp running on iface in the right role; (re)started only when something changed."""
    if not os.path.exists(PTP_BIN): raise RuntimeError("%s is missing on this speaker (deploy the overlay's usr/bin)" % PTP_BIN)
    want = ptp_conf_text(iface, master); changed = False
    if _read(PTP_CONF, "") != want: open(PTP_CONF, "w").write(want); changed = True
    if _read(PTP_UNIT, "") != ptp_unit_text():
        open(PTP_UNIT, "w").write(ptp_unit_text()); _sh("systemctl daemon-reload", 20); changed = True
    if changed or sysctl("is-active", "syng-ptp") != "active":
        _sh("systemctl enable syng-ptp 2>/dev/null; systemctl restart syng-ptp", 30); changed = True
    return changed

def ptp_stop():
    _sh("systemctl disable syng-ptp 2>/dev/null; systemctl stop syng-ptp", 20)

def ptp_status():
    st = {"active": sysctl("is-active", "syng-ptp") == "active", "state": None, "offset_ns": None, "path_delay_ns": None, "master": None}
    if not st["active"]: return st
    out = _sh("%s -u -b 0 -f %s 'GET PORT_DATA_SET' 'GET CURRENT_DATA_SET' 'GET PARENT_DATA_SET' 2>/dev/null" % (PMC_BIN, PTP_CONF), 6)
    m = re.search(r"portState\s+(\w+)", out); st["state"] = m.group(1) if m else None
    m = re.search(r"offsetFromMaster\s+(-?[\d.]+)", out); st["offset_ns"] = float(m.group(1)) if m else None
    m = re.search(r"meanPathDelay\s+(-?[\d.]+)", out); st["path_delay_ns"] = float(m.group(1)) if m else None
    m = re.search(r"grandmasterIdentity\s+(\S+)", out); st["master"] = m.group(1) if m else None
    return st

def link_log(msg):
    LINK["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg)); LINK["log"] = LINK["log"][-40:]; log("LINK " + msg)

def link_apply_local(mode, restart_audio=True):
    """This cell's share of a link mode: SourceCast timesync, PTP, timesyncd. Idempotent; restarts the audio
    stack (jackd -> syngsoundserver -> sources, a few seconds of silence) only when SourceCast.conf changed."""
    out = {"mode": mode}
    master = (whoami().get("role") or "").lower() != "secondary"
    if mode == "wired":
        ifn, ip = wired_addr()
        if not ip: raise RuntimeError("no wired address on this speaker")
        out["iface"] = ifn; out["ip"] = ip
        out["sourcecast_changed"] = sc_set("ntp", ifn)
        out["ptp_changed"] = ptp_ensure(ifn, master)
        if master: _sh("systemctl start systemd-timesyncd", 20)
        else: _sh("systemctl stop systemd-timesyncd", 20)
        out["timesyncd"] = "on (grandmaster keeps NTP time)" if master else "off (PTP client)"
    else:
        try: stock = json.loads(_read(SC_STOCK, "{}") or "{}")
        except Exception: stock = {}
        out["sourcecast_changed"] = sc_set(stock.get("timesync") or "cync", stock.get("timesyncInterface") or "wsyng0")
        ptp_stop(); _sh("systemctl start systemd-timesyncd", 20)
    if out.get("sourcecast_changed") and restart_audio:
        _sh("systemctl restart jackd", 90); out["audio_restarted"] = True
    SETTINGS["link_mode"] = mode; save_settings()
    return out

def link_status():
    ifn, ip = wired_addr()
    st = {"mode": SETTINGS.get("link_mode", "syfi"), "wired": {"ifname": ifn, "ip": ip} if ip else None,
          "timesync": sc_conf().get("timesync"), "timesync_iface": sc_conf().get("timesyncInterface"),
          "ptp": ptp_status(), "busy": LINK["busy"], "log": LINK["log"][-12:], "last": LINK["last"],
          "time_sync_good": whoami().get("time_sync_good")}
    if (whoami().get("role") or "").lower() != "secondary":
        members = []
        try:
            doc = room_doc()
            for s_, c in doc["cells"].items():
                if s_ == doc["me"]:
                    members.append({"serial": s_, "name": c.get("display_name"), "ip": c.get("ip_address"), "role": c.get("role"), "wired_ip": ip, "wired_if": ifn,
                                    "syfi_ip": c.get("syfi_ip"), "ptp": st["ptp"], "time_sync_good": st["time_sync_good"], "link_mode": st["mode"], "isMe": True})
                    continue
                try:
                    w = agent_get(c.get("ip_address"), "/api/whoami", timeout=3)
                    members.append({"serial": s_, "name": c.get("display_name"), "ip": c.get("ip_address"), "role": c.get("role"), "wired_ip": w.get("wired_ip"), "wired_if": w.get("wired_if"),
                                    "syfi_ip": c.get("syfi_ip"), "ptp": w.get("ptp"), "time_sync_good": w.get("time_sync_good"), "link_mode": w.get("link_mode")})
                except Exception as e:
                    members.append({"serial": s_, "name": c.get("display_name"), "ip": c.get("ip_address"), "role": c.get("role"), "error": str(e)[:80]})
        except Exception as e:
            st["members_error"] = str(e)[:120]
        st["members"] = members
    return st

def op_link(mode):
    """Primary: switch the whole room's link. Runs in the background (members restart their audio stacks);
    progress in LINK['log'], result in LINK['last']."""
    if mode not in ("syfi", "wired"): raise RuntimeError("mode must be syfi or wired")
    require_primary()
    if LINK["busy"]: raise RuntimeError("a link change is already running")
    doc = room_doc(); me = whoami(); members = []
    for s_, c in doc["cells"].items():
        if s_ == doc["me"]: w = me
        else:
            try: w = agent_get(c.get("ip_address"), "/api/whoami", timeout=4)
            except Exception as e: raise RuntimeError("%s (%s) is not reachable: %s" % (c.get("display_name") or s_[-5:], c.get("ip_address"), e))
            if not w.get("agent"): raise RuntimeError("%s is not running the Troubadour agent" % (c.get("display_name") or s_[-5:]))
        members.append((s_, c, w))
    if mode == "wired":
        missing = [(c.get("display_name") or s_[-5:]) for s_, c, w in members if not w.get("wired_ip")]
        if missing: raise RuntimeError("no wired address on: %s — plug them in first" % ", ".join(missing))
    LINK["busy"] = mode; LINK["log"] = []; LINK["last"] = None
    def run():
        res = {}
        try:
            link_log("switching the room to %s" % mode)
            SETTINGS["link_mode"] = mode; save_settings()
            # 1. the document first: the main audio path follows syfi_ip
            with LC_LOCK:
                d = room_doc(); changed = False
                for s_, c, w in members:
                    cc = d["cells"].get(s_)
                    if cc and link_sync(cc, w): changed = True
                if changed: bump(d["room_state"]); fan_out(d)
            link_log("room document updated (syfi_ip -> %s addresses)" % ("wired" if mode == "wired" else "SyFi"))
            # 2. secondaries, one at a time (each restarts its own audio stack)
            for s_, c, w in members:
                if s_ == doc["me"]: continue
                nm = c.get("display_name") or s_[-5:]
                try:
                    r = agent_post(c["ip_address"], "/api/link", {"mode": mode, "_local": True}, timeout=120); res[s_] = r
                    link_log("%s: %s" % (nm, ", ".join("%s=%s" % (k, v) for k, v in r.items() if k in ("iface", "ip", "sourcecast_changed", "ptp_changed", "timesyncd", "audio_restarted"))))
                except Exception as e:
                    res[s_] = {"error": str(e)[:160]}; link_log("%s: FAILED %s" % (nm, e))
            # 3. ourselves last
            r = link_apply_local(mode); res[doc["me"]] = r
            link_log("this speaker: %s" % ", ".join("%s=%s" % (k, v) for k, v in r.items() if k != "mode"))
            link_log("done")
        except Exception as e:
            link_log("failed: %s" % e)
        finally:
            LINK["last"] = {"mode": mode, "members": res, "t": time.time()}; LINK["busy"] = None
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True, "mode": mode}

def link_boot():
    """After a reboot / firmware update: re-assert this cell's share of the link mode (SourceCast.conf lives in the
    rootfs — a new slot comes with the stock one; syng-ptp is enabled but its config/unit are checked too)."""
    try:
        if SETTINGS.get("link_mode") == "wired":
            for _ in range(20):
                if wired_addr()[1]: break
                time.sleep(3)
            r = link_apply_local("wired"); log("LINK boot: %s" % r)
    except Exception as e:
        log("LINK boot: %s" % e)

def cd_restart(reason=""):
    """Restart celldirector and work around a stock quirk: started while Wi-Fi is already up it gets no 'connected'
    event and enters Wi-Fi repair mode 20 s later. If that happens, bounce the Wi-Fi service once so it leaves."""
    _sh("systemctl restart celldirector", 30); log("celldirector restarted %s" % reason)
    def guard():
        time.sleep(32)
        try:
            ph = json.loads(_read(CD_STATE_FILE, "{}") or "{}").get("phase")
            if ph != "wifiRepair": return
            svc = None
            for line in _sh("connmanctl services", 10).splitlines():
                m = re.match(r"\*A[OR]\s+.*?(wifi_\S+)$", line.strip())
                if m: svc = m.group(1); break
            if not svc: log("cd guard: wifiRepair but no connected Wi-Fi service to bounce"); return
            log("cd guard: celldirector in wifiRepair after restart — bouncing %s" % svc)
            _sh("connmanctl disconnect %s; sleep 3; connmanctl connect %s" % (svc, svc), 40)
            time.sleep(15)
            log("cd guard: phase now %s" % json.loads(_read(CD_STATE_FILE, "{}") or "{}").get("phase"))
        except Exception as e:
            log("cd guard: %s" % e)
    threading.Thread(target=guard, daemon=True).start()

# ---------- firmware: in-place A/B slot updates (stock mender U-Boot integration) ----------''')

# seed_state uses the guarded restart
rep('''    set_placed()   # celldirector reads its phase file only at start: in "factory" it ignores the room document and waits for the app's BLE setup
    _sh("systemctl restart celldirector", 30)''', '''    set_placed()   # celldirector reads its phase file only at start: in "factory" it ignores the room document and waits for the app's BLE setup
    cd_restart("(seeded state)")''')

# carry the PTP binaries and unit across firmware updates
rep('''CARRY = ["/usr/bin/syng-agent", "/usr/bin/syng-led-reactive", "/usr/bin/syng-led", "/usr/bin/syng-cloud-block", "/usr/bin/syng-dbus-dump",''',
    '''CARRY = ["/usr/bin/syng-agent", "/usr/bin/syng-led-reactive", "/usr/bin/syng-led", "/usr/bin/syng-cloud-block", "/usr/bin/syng-dbus-dump",
         "/usr/bin/syng-ptp4l", "/usr/bin/syng-pmc", "/etc/systemd/system/syng-ptp.service",''')

# whoami: wired address, link mode, PTP state
rep('''            "wifi": wifi_status(brief=True)}''', '''            "wifi": wifi_status(brief=True),
            "wired_if": wired_addr()[0], "wired_ip": wired_addr()[1], "link_mode": SETTINGS.get("link_mode", "syfi"),
            "ptp": ptp_status() if SETTINGS.get("link_mode") == "wired" else None}''')

# link_sync: wired mode -> the main audio path is the wired address
rep('''    changed = False
    for k in ("syfi_ip", "syfi_good", "bssid"):
        v = w.get(k)
        if v is not None and v != "" and c.get(k) != v: c[k] = v; changed = True
    return changed''', '''    changed = False
    wired = SETTINGS.get("link_mode") == "wired"
    for k in ("syfi_ip", "syfi_good", "bssid"):
        v = w.get(k)
        if k == "syfi_ip" and wired and w.get("wired_ip"): v = w["wired_ip"]   # wired room: main packets over the wire
        if v is not None and v != "" and c.get(k) != v: c[k] = v; changed = True
    if w.get("wired_ip") is not None and c.get("wired_ip") != w.get("wired_ip"): c["wired_ip"] = w.get("wired_ip"); changed = True
    return changed''')

# keeper: keep PTP in the right role (celldirector may re-elect)
rep('''            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)
            persist_state()''', '''            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)
            if SETTINGS.get("link_mode") == "wired" and not LINK["busy"]:
                try:
                    ifn, ip = wired_addr()
                    if ip and ptp_ensure(ifn, (whoami().get("role") or "").lower() != "secondary"): log("LINK keeper: PTP role/config refreshed")
                except Exception as e: log("link keeper: %s" % e)
            persist_state()''')

# settings default + boot re-assert
rep('''SETTINGS = {"volume_limit": 0, "sound_mode": "syng"}''', '''SETTINGS = {"volume_limit": 0, "sound_mode": "syng", "link_mode": "syfi"}''')
rep('''    load_settings(); field_load()
    threading.Thread(target=field_keeper_loop, daemon=True).start()''', '''    load_settings(); field_load()
    threading.Thread(target=link_boot, daemon=True).start()
    threading.Thread(target=field_keeper_loop, daemon=True).start()''')

# API
rep('''RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener", "/api/soundmode", "/api/cell/name")''',
    '''RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener", "/api/soundmode", "/api/cell/name", "/api/link")''')
rep('''def lc_get(path):
    if path.startswith("/api/net"): return net_info()''', '''def lc_get(path):
    if path.startswith("/api/link"): return link_status()
    if path.startswith("/api/net"): return net_info()''')
rep('''    if path.startswith("/api/net/power"): return net_power(body.get("tech"), bool(body.get("on")), bool(body.get("force")))''',
    '''    if path.startswith("/api/link"):
        if body.get("_local"): return dict(link_apply_local(body.get("mode", "syfi")), ok=True)   # one member's share, sent by the primary
        return op_link(body.get("mode"))
    if path.startswith("/api/net/power"): return net_power(body.get("tech"), bool(body.get("on")), bool(body.get("force")))''')

# ---- setup page: link controls in the NETWORK card ----
rep('''  <details style="margin-top:10px"><summary>Network settings — radios, addresses, DNS</summary>
    <div id=netsettings class=muted style="margin-top:8px">…</div></details>''', '''  <details style="margin-top:10px"><summary>Network settings — radios, addresses, DNS</summary>
    <div id=netsettings class=muted style="margin-top:8px">…</div></details>
  <details style="margin-top:10px" id=linkdet><summary>Cell-to-cell link — SyFi or wired Ethernet</summary>
    <div id=linkbox class=muted style="margin-top:8px">…</div></details>''')
rep('''document.querySelector('#netsettings')&&document.querySelector('#netsettings').closest('details').addEventListener('toggle',e=>{if(e.target.open)loadNet();});''',
    '''document.querySelector('#netsettings')&&document.querySelector('#netsettings').closest('details').addEventListener('toggle',e=>{if(e.target.open)loadNet();});

// ---------- cell-to-cell link (SyFi / wired) ----------
let linkTimer=null;
const us=(ns)=>ns==null?'—':(Math.abs(ns)>=1e6?(ns/1e6).toFixed(1)+' ms':(ns/1e3).toFixed(0)+' µs');
async function loadLink(){
  const box=$('#linkbox'); if(!box) return;
  let L; try{L=await api('/api/link');}catch(e){box.textContent='unavailable: '+e.message;return;}
  const mode=L.mode||'syfi', busy=!!L.busy;
  const mem=(L.members||[]).map(m=>`<div class=item><span class=grow><b>${m.name||('Cell '+short(m.serial))}</b> <span class=k>${m.role||''}${m.isMe?' · this speaker':''}</span>
      <div class=k>${m.error?('unreachable: '+m.error):(`${m.wired_ip?('wired '+m.wired_if+' '+m.wired_ip):'<span style="color:var(--warn)">no wire</span>'} · main path → ${m.syfi_ip||'—'}`+
      (m.ptp&&m.ptp.active?` · PTP ${m.ptp.state||'?'}${m.ptp.state==='SLAVE'?' offset '+us(m.ptp.offset_ns)+' · delay '+us(m.ptp.path_delay_ns):''}`:'')+` · sync ${m.time_sync_good?'good':'<span style="color:var(--warn)">not good</span>'}`)}</div></span></div>`).join('');
  box.innerHTML=`<div class=srcs><button class="srcbtn ${mode==='syfi'?'on':''}" data-link=syfi ${busy?'disabled':''}>SyFi (stock)</button><button class="srcbtn ${mode==='wired'?'on':''}" data-link=wired ${busy?'disabled':''}>Wired</button>
      ${busy?'<span class=k>switching to '+L.busy+'…</span>':''}</div>
    <div class=hint><b>SyFi</b>: the primary runs a hidden Wi-Fi network, audio and a hardware clock from the Wi-Fi chip go over it. <b>Wired</b>: every speaker on Ethernet; audio goes over the wire and the clocks are kept together with PTP (the primary is the grandmaster), SyFi stays up idle. Needs a wired address on every member; switching restarts each speaker's audio for a few seconds. See docs/WIRED.md.</div>
    ${L.members?`<div class=list style="margin-top:8px">${mem}</div>`:`<div class=k>${L.wired?('wired '+L.wired.ifname+' '+L.wired.ip):'no wire on this speaker'} · timesync ${L.timesync||'?'} on ${L.timesync_iface||'?'}${L.ptp&&L.ptp.active?' · PTP '+(L.ptp.state||'?')+' offset '+us(L.ptp.offset_ns):''}</div>`}
    ${(L.log||[]).length?`<pre class=k style="white-space:pre-wrap;margin:8px 0 0">${L.log.join('\\n')}</pre>`:''}`;
  box.querySelectorAll('[data-link]').forEach(b=>b.onclick=async()=>{const m=b.dataset.link; if(m===mode)return;
    if(!confirm((m==='wired'?'Switch the room to the wired link? Every speaker needs an Ethernet address; each one restarts its audio for a few seconds.':'Switch the room back to SyFi? Each speaker restarts its audio for a few seconds.')))return;
    try{await api('/api/link',{mode:m}); toast('Switching…');}catch(e){toast(e.message,true);} setTimeout(loadLink,1500);});
  clearTimeout(linkTimer); if(busy||$('#linkdet').open) linkTimer=setTimeout(loadLink,busy?2500:10000);
}
document.querySelector('#linkdet')&&document.querySelector('#linkdet').addEventListener('toggle',e=>{if(e.target.open)loadLink();else clearTimeout(linkTimer);});''')

s = s.replace('"syng-agent (b48)"', '"syng-agent (b49)"').replace("syng-agent (b48) on", "syng-agent (b49) on").replace('AGENT_BUILD = "b48"', 'AGENT_BUILD = "b49"')
out = os.path.join(here, "syng-agent-b49.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
