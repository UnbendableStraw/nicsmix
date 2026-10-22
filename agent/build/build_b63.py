#!/usr/bin/env python3
"""syng-agent-b63.py = b62 + AirPlay / Atmos settings, and what is really playing.

Found while chasing the AirPlay "stops responding for a minute" crash (docs/HACKING.md):
  * Atmos over AirPlay does decode: an Apple TV sends APAC 7.1.4 and airplay2source hands syngsoundserver 12
    channels (channelLayout "L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs").
  * The AirPlay source carries two writable properties that nothing ever sets — not the Syng app, not the cell —
    so they sit at false: enableHeightProcessing and enableBeyondStereoDuplication (syngsoundserver reads both).
  * Syng's badgingType says "Stereo" even while 12-channel Atmos plays, so the app badge can't be trusted.
  * The crash (an uncaught sdbus::Error in airplay2source while a spatial stream starts) is followed by a ~150 MB
    core dump that takes 2+ minutes to write; AirPlay is gone until it finishes.

1. Three settings, each a switch, stock (off) by default:
     atmos_height        -> enableHeightProcessing on every music source that has it
     atmos_fill          -> enableBeyondStereoDuplication
     airplay_quick_recover -> airplay2source never dumps core (LimitCORE=0 drop-in + the running process), so a
                            crash costs systemd's 3 s restart instead of minutes. Turning it off removes the drop-in.
   The keeper re-applies them every 15 s, only when the live value differs (airplay2source restarts reset them;
   writes are kept rare on purpose — the crash lives in airplay2source's own D-Bus emissions).
2. GET  /api/airplay[/local]  -> per speaker: what the AirPlay source is producing (layout, channel count, a real
                                 label such as "Dolby Atmos 7.1.4", Syng's badge), the live property values and
                                 the settings.
   POST /api/airplay {height?, fill?, quick_recover?} -> this speaker, then every other speaker found on the
                                 network (pass "scope": "room" for the room only). Volume is never touched.
3. /api/state sources gain "format" (the label) so the apps can show it.
4. The setup page gets an AIRPLAY & ATMOS card."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b62.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- settings defaults ----------
rep('''SETTINGS = {"volume_limit": 0, "sound_mode": "syng", "link_mode": "syfi"}''',
    '''SETTINGS = {"volume_limit": 0, "sound_mode": "syng", "link_mode": "syfi",
            "atmos_height": False, "atmos_fill": False, "airplay_quick_recover": False}''')

# ---------- 3: the format label in /api/state ----------
rep('''                    "mute": base.get("mute"), "height": base.get("enableHeightProcessing"),
                    "beyond": base.get("enableBeyondStereoDuplication")}''',
'''                    "mute": base.get("mute"), "height": base.get("enableHeightProcessing"),
                    "beyond": base.get("enableBeyondStereoDuplication"),
                    "format": format_label(base.get("channelLayout")) if rt.get("active") else None}''')

# ---------- 1 + 2: settings, keeper, API ----------
rep('''def lc_get(path):''', r'''# ---------- AirPlay / Atmos (b63) ----------
def format_label(layout):
    """A truthful name for a source channel layout ("L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs" -> Dolby Atmos 7.1.4)."""
    ch = [c for c in str(layout or "").split() if c]
    n = len(ch)
    if not n: return None
    top = sum(1 for c in ch if c.startswith("T") or c in ("Vhl", "Vhr", "Ltf", "Rtf", "Ltr", "Rtr"))
    lfe = 1 if any(c.lower().startswith("lfe") for c in ch) else 0
    if all(c.isdigit() for c in ch): return "%d ch" % n          # an idle source reports bare channel numbers
    if n == 1: return "Mono"
    if n == 2 and not top: return "Stereo"
    bed = n - top - lfe
    name = "%d.%d%s" % (bed, lfe, (".%d" % top) if top else "")
    return ("Dolby Atmos " if top else "Surround ") + name

AP_DROPIN_DIR = "/etc/systemd/system/airplay2source.service.d"
AP_DROPIN = AP_DROPIN_DIR + "/troubadour-quick-recover.conf"

def _ap_pid():
    try: return int(_sh("pidof airplay2source", 3).split()[0])
    except Exception: return None

def quick_recover_live():
    pid = _ap_pid(); lim = None
    if pid:
        try:
            import resource
            lim = resource.prlimit(pid, resource.RLIMIT_CORE)[0]
        except Exception: pass
    return {"dropin": os.path.exists(AP_DROPIN), "core_limit": lim, "pid": pid,
            "on": os.path.exists(AP_DROPIN) and (lim == 0 if pid else True)}

def quick_recover_apply(on):
    """No core dump for airplay2source: the unit (future starts) and the running process (now). syng-coredump
    passes the limit on to systemd-coredump, which then skips the dump at once (measured 0.3 s to reap)."""
    import resource
    if on:
        if not os.path.exists(AP_DROPIN):
            os.makedirs(AP_DROPIN_DIR, exist_ok=True)
            open(AP_DROPIN, "w").write("# Troubadour: a crash restarts in seconds instead of writing a 150 MB core dump first\n"
                                       "[Service]\nLimitCORE=0\n")
            _sh("systemctl daemon-reload", 20)
        lim = (0, 0)
    else:
        if os.path.exists(AP_DROPIN):
            os.remove(AP_DROPIN)
            _sh("systemctl daemon-reload", 20)
        lim = (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
    pid = _ap_pid()
    if pid:
        try: resource.prlimit(pid, resource.RLIMIT_CORE, lim)
        except Exception as e: log("quick recover prlimit: %s" % e)
    return quick_recover_live()

ATMOS_PROPS = (("atmos_height", "enableHeightProcessing"), ("atmos_fill", "enableBeyondStereoDuplication"))

def atmos_apply(force=False):
    """Bring each music source's two properties to the settings; writes only what differs."""
    n = 0
    for sid in source_ids():
        if not is_music(sid): continue
        dest, path = source_target_cached(sid)
        if not dest or not path: continue
        try: cur = get_all(dest, path, ASRC_IF)
        except Exception: continue
        for key, prop in ATMOS_PROPS:
            if prop not in cur: continue
            want = bool(SETTINGS.get(key))
            if force or bool(cur.get(prop)) != want:
                try:
                    set_prop(dest, path, ASRC_IF, prop, dbus.Boolean(want, variant_level=1)); n += 1
                    log("ATMOS %s %s=%s" % (sid, prop, want))
                except Exception as e:
                    _src_cache["targets"].pop(sid, None); log("ATMOS %s %s: %s" % (sid, prop, e))
    return n

def airplay_keeper_loop():
    time.sleep(30)
    while True:
        try:
            if SETTINGS.get("atmos_height") or SETTINGS.get("atmos_fill"): atmos_apply()
            if SETTINGS.get("airplay_quick_recover") and not quick_recover_live()["on"]: quick_recover_apply(True)
        except Exception as e: log("airplay keeper: %s" % e)
        time.sleep(15)

def airplay_local():
    me = whoami()
    out = {"ip": me.get("ip"), "serial": me.get("serial"), "name": me.get("name"), "role": (me.get("role") or "").lower(),
           "settings": {"height": bool(SETTINGS.get("atmos_height")), "fill": bool(SETTINGS.get("atmos_fill")),
                        "quick_recover": bool(SETTINGS.get("airplay_quick_recover"))},
           "quick_recover": quick_recover_live()}
    try:
        dest, path = source_target_cached("airplay2")
        rt = get_all(ASRV, "/com/syng/AudioSource/Runtime/airplay2", RUN_IF)
        b = get_all(dest, path, ASRC_IF) if dest and path else {}
        lay = b.get("channelLayout")
        out.update({"active": bool(rt.get("active")), "state": b.get("state"), "layout": lay,
                    "channels": len(str(lay or "").split()), "format": format_label(lay), "badge": b.get("badgingType"),
                    "height": b.get("enableHeightProcessing"), "fill": b.get("enableBeyondStereoDuplication")})
    except Exception as e:
        out["error"] = str(e)[:120]
    return out

def airplay_room():
    cells = [airplay_local()] + list(_members_call("/api/airplay/local").values())
    return {"cells": cells}

def airplay_set_local(body):
    changed = {}
    for k, key in (("height", "atmos_height"), ("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover")):
        if k in body and body[k] is not None:
            SETTINGS[key] = bool(body[k]); changed[k] = SETTINGS[key]
    if changed: save_settings()
    if "height" in changed or "fill" in changed: atmos_apply(force=True)
    if "quick_recover" in changed: quick_recover_apply(changed["quick_recover"])
    log("AIRPLAY settings %s" % changed)
    return airplay_local()

def _fleet_ips():
    ips = set()
    try:
        for room in (discover() or {}).get("rooms", []):
            for c in room.get("cells", []):
                if c.get("ip"): ips.add(c["ip"])
    except Exception: pass
    try: ips |= set(i for i in room_member_ips(get_state()) if i)
    except Exception: pass
    ips.discard(whoami().get("ip"))
    return sorted(ips)

def lc_get(path):''')

rep('''    if path.startswith("/api/netbuffer/local"): return netbuffer_local()''',
'''    if path.startswith("/api/airplay/local"): return airplay_local()
    if path.startswith("/api/airplay"): return airplay_room()
    if path.startswith("/api/netbuffer/local"): return netbuffer_local()''')

rep('''def lc_post(path, body):
    if path.startswith("/api/netbuffer"):''',
'''def lc_post(path, body):
    if path.startswith("/api/airplay"):
        mine = airplay_set_local(body)
        if body.get("_local"): return mine
        fwd = {k: body[k] for k in ("height", "fill", "quick_recover") if k in body}
        fwd["_local"] = True
        ips = [i for i in room_member_ips(get_state()) if i and i != whoami().get("ip")] if body.get("scope") == "room" else _fleet_ips()
        others = {}; lock = threading.Lock()
        def one(ip):
            try: r = agent_post(ip, "/api/airplay", fwd, timeout=10)
            except Exception as e: r = {"error": str(e)[:80]}
            with lock: others[ip] = r
        ths = [threading.Thread(target=one, args=(ip,), daemon=True) for ip in ips]
        for t in ths: t.start()
        for t in ths: t.join(14)
        return {"ok": True, "me": mine, "others": others}
    if path.startswith("/api/netbuffer"):''')

rep('''    threading.Thread(target=storm_watch_loop, daemon=True).start()
''', '''    threading.Thread(target=storm_watch_loop, daemon=True).start()
    threading.Thread(target=airplay_keeper_loop, daemon=True).start()
''')

# ---------- switch style for the setup page ----------
rep(""".rowb{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}""",
""".rowb{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
.rowb.sw{justify-content:space-between;flex-wrap:nowrap;gap:14px}
.switch{position:relative;display:inline-block;width:46px;height:26px;flex:none}
.switch input{opacity:0;width:0;height:0}
.switch span{position:absolute;inset:0;border-radius:13px;background:var(--line);transition:background .15s}
.switch span:before{content:"";position:absolute;width:20px;height:20px;left:3px;top:3px;border-radius:50%;background:#fff;transition:transform .15s}
.switch input:checked+span{background:var(--live)}
.switch input:checked+span:before{transform:translateX(20px)}""")

# ---------- 4: the setup page ----------
rep('''<div class=card id=streamcard><h2>GROUP STREAM''', '''<div class=card id=apcard><h2>AIRPLAY &amp; ATMOS <span><button class="btn sm" id=aprefresh>Refresh</button></span></h2>
  <div id=apnow class=muted>…</div>
  <div class="rowb sw" style="margin-top:10px"><div><b>Height channels</b><div class=muted style="font-size:12px">Atmos' overhead channels (Tfl Tfr Trl Trr) — Syng's <code>enableHeightProcessing</code></div></div>
    <label class=switch><input type=checkbox id=apheight><span></span></label></div>
  <div class="rowb sw"><div><b>Fill surround channels</b><div class=muted style="font-size:12px">Spread beyond-stereo channels — Syng's <code>enableBeyondStereoDuplication</code></div></div>
    <label class=switch><input type=checkbox id=apfill><span></span></label></div>
  <div class="rowb sw"><div><b>Quick AirPlay recovery</b><div class=muted style="font-size:12px">If AirPlay crashes, it's back in ~3 s instead of a 2-minute core dump</div></div>
    <label class=switch><input type=checkbox id=apquick><span></span></label></div>
  <p class=hint>Both Atmos switches are properties the speakers have always had but nothing ever turned on (stock: off). Each switch
    applies to every speaker on the network at once; flip one while an Atmos track plays and listen. Syng's own badge says "Stereo"
    even during Atmos — the line above reads the real channel layout instead.</p>
  <div id=apcells class=muted style="font-size:12px;margin-top:6px"></div>
</div>

<div class=card id=streamcard><h2>GROUP STREAM''')

rep('''async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();loadFw();loadNB();loadUND(24);}''',
r'''// ---- AirPlay & Atmos ----
let AP=null;
async function loadAP(){
  try{AP=await api('/api/airplay');
    const cs=AP.cells.filter(c=>!c.error||c.settings);
    const me=cs.find(c=>c.active)||cs[0]||{};
    const playing=cs.filter(c=>c.active);
    $('#apnow').innerHTML=playing.length?playing.map(c=>`<b>${c.format||'—'}</b> · ${c.channels} ch${c.badge&&c.format&&c.badge!==c.format?` <span class=muted>(Syng's badge: ${c.badge})</span>`:''} on ${c.name||short(c.serial)}`).join('<br>'):'Nothing is playing over AirPlay right now';
    const st=(me.settings||{}); $('#apheight').checked=!!st.height; $('#apfill').checked=!!st.fill; $('#apquick').checked=!!st.quick_recover;
    $('#apcells').innerHTML=cs.map(c=>`${c.name||short(c.serial)}${c.role?' ('+c.role+')':''}: `+(c.height==null?'AirPlay runs on the primary':`height ${c.height?'on':'off'} · fill ${c.fill?'on':'off'}`)+` · quick recovery ${c.quick_recover&&c.quick_recover.on?'on':'off'}`).join('<br>');
  }catch(e){$('#apnow').textContent='n/a: '+e.message;}
}
async function apSet(k,v){try{const b={};b[k]=v;await api('/api/airplay',b);toast((k==='height'?'Height channels':k==='fill'?'Fill surround':'Quick recovery')+(v?' on':' off')+' — every speaker');setTimeout(loadAP,1500);}catch(e){toast(e.message,true);loadAP();}}
$('#apheight').onchange=e=>apSet('height',e.target.checked);
$('#apfill').onchange=e=>apSet('fill',e.target.checked);
$('#apquick').onchange=e=>apSet('quick_recover',e.target.checked);
$('#aprefresh').onclick=loadAP;
async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();loadFw();loadNB();loadUND(24);loadAP();}''')

s = s.replace('"syng-agent (b62)"', '"syng-agent (b63)"').replace("syng-agent (b62) on", "syng-agent (b63) on").replace('AGENT_BUILD = "b62"', 'AGENT_BUILD = "b63"')
out = os.path.join(here, "syng-agent-b63.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
