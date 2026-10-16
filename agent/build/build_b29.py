#!/usr/bin/env python3
"""syng-agent-b29.py = b28 + now-playing (artwork, album, progress, mix badge) + transport controls,
sleep timer, volume limit, control-ring / UI-sound settings, live per-cell link status in the fleet."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b28.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# ---- 1. richer live_sources
rep('''        out[sid] = {"active": bool(rt.get("active")), "volume": base.get("volume"),
                    "tone": tone, "title": meta.get("title"), "artist": meta.get("artist"),
                    "playing": tc.get("playing"), "music": sid in MUSIC,
                    "mute": base.get("mute"), "height": base.get("enableHeightProcessing"),
                    "beyond": base.get("enableBeyondStereoDuplication")}''',
    '''        art = get_all(dest, path, "com.syng.AudioSource.Artwork")
        out[sid] = {"active": bool(rt.get("active")), "volume": base.get("volume"),
                    "tone": tone, "title": meta.get("title"), "artist": meta.get("artist"), "album": meta.get("album"),
                    "duration": meta.get("duration"), "position": tc.get("position"),
                    "playing": tc.get("playing"), "shuffling": tc.get("shuffling"), "repeatMode": tc.get("repeatMode"),
                    "controls": [str(x) for x in (tc.get("supportedControls") or [])], "music": sid in MUSIC,
                    "artwork": bool(art.get("artworkUrl")), "mixType": base.get("mixType"), "mediaType": base.get("mediaType"),
                    "badging": base.get("badgingType"), "layout": base.get("channelLayout"), "state": base.get("state"),
                    "mute": base.get("mute"), "height": base.get("enableHeightProcessing"),
                    "beyond": base.get("enableBeyondStereoDuplication")}''')

# ---- 2. backend features (inserted before the HTTP glue of localcloud)
FEATURES = r'''
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
'''
rep("def cloudblock_all():", FEATURES + "\ndef cloudblock_all():")
# whoami gets rssi
rep('''            "cloud_blocked": os.path.exists(CLOUD_BLOCK_MARK), "agent": AGENT_BUILD,''',
    '''            "cloud_blocked": os.path.exists(CLOUD_BLOCK_MARK), "agent": AGENT_BUILD, "rssi": wifi_rssi(),''')
# routes
rep('''def lc_get(path):
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()''',
    '''def lc_get(path):
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()
    if path.startswith("/api/fleet/live"): return fleet_live()
    if path.startswith("/api/sleep"): return dict(SLEEP, now=time.time())
    if path.startswith("/api/settings"): return dict(SETTINGS, sleep=SLEEP, ring=ring_get())
    if path.startswith("/api/ring"): return ring_get()''')
rep('''    if path.startswith("/api/ping"): return op_ping(body.get("serial"), body.get("vol"), bool(body.get("all")))''',
    '''    if path.startswith("/api/ping"): return op_ping(body.get("serial"), body.get("vol"), bool(body.get("all")))
    if path.startswith("/api/transport"): return {"ok": transport(body.get("source"), body.get("action"), body.get("position"))}
    if path.startswith("/api/sleep"): return set_sleep(body.get("minutes"))
    if path.startswith("/api/settings"):
        if "volume_limit" in body: SETTINGS["volume_limit"] = int(max(0, min(100, int(body["volume_limit"] or 0))))
        save_settings(); return dict(SETTINGS)
    if path.startswith("/api/ring"): return ring_set(body.get("steps"), body.get("clockwise_up"), body.get("ui_sound_pct"))''')
# artwork proxy (binary) in do_GET, before the generic lc_get
rep('''            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")''',
    '''            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")
            elif self.path.startswith("/api/artwork"):
                q = parse_qs(urlparse(self.path).query); url = artwork_url((q.get("source") or ["airplay2"])[0])
                if not url: self._send(404, "no artwork", "text/plain"); return
                try:
                    with urllib.request.urlopen(url, timeout=5) as r:
                        data = r.read(); ctype = r.headers.get("Content-Type", "image/jpeg")
                    self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)
                except Exception as e:
                    self._send(502, "artwork fetch failed: %s" % e, "text/plain")''')
# startup
rep("    lc_start()\n", "    lc_start()\n    load_settings()\n    threading.Thread(target=sleep_loop, daemon=True).start()\n    threading.Thread(target=volume_guard_loop, daemon=True).start()\n")
# state carries sleep + settings
rep('''           "sources": live_sources(), "system": sys_stats(), "calibration": calibration(),''',
    '''           "sources": live_sources(), "system": sys_stats(), "calibration": calibration(),
           "sleep": dict(SLEEP, now=time.time()), "settings": dict(SETTINGS),''')

# ---- 3. dashboard: now playing + transport + sleep + limit
rep('''<section class=card id=playcard><h2>PLAYBACK</h2>
  <div class=np id=np></div>
  <div class=srcs id=srcs></div>''',
    '''<section class=card id=playcard><h2>PLAYBACK <span class=rn id=mixbadge style="font-size:12px;color:var(--muted);font-weight:500"></span></h2>
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
  <div class=srcs id=srcs></div>''')
rep('''  <div class=srcs><button class=srcbtn id=mutebtn>Mute</button></div>
</section>''',
    '''  <div class=srcs><button class=srcbtn id=mutebtn>Mute</button></div>
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
</section>''')
# render now playing
rep('''  const act=activeSrc(sources);
  const np=act&&sources[act].playing&&sources[act].title?`<b>${sources[act].title}</b>${sources[act].artist?' — '+sources[act].artist:''}`:(act?('<b>'+(ICON[act]||act)+'</b> active'):'Nothing playing');
  $('#np').innerHTML=np;''',
    '''  const act=activeSrc(sources);
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
  const badge=A?[...new Set([A.badging,A.mixType,A.layout].filter(x=>x&&x!=='none'&&x!=='unknown'))].join(' · '):''; $('#mixbadge').textContent=badge;''')
# sleep/limit state + wiring
rep("function updateCalib(cal){", '''function updateSleep(st){
  const sl=st.sleep||{}; const left=sl.until?Math.max(0,Math.round((sl.until-sl.now)/60)):0;
  $('#sleepstate').textContent=sl.state==='fading'?'fading out…':(sl.until?('off in '+left+' min'):'off');
  const lim=(st.settings||{}).volume_limit||0; $('#limval').textContent=lim?lim+' %':'off';
  document.querySelectorAll('[data-lim]').forEach(b=>b.classList.toggle('on',+b.dataset.lim===lim));
  const vol=$('#vol'); if(vol){vol.max=lim||100;}
}
document.querySelectorAll('[data-sleep]').forEach(b=>b.onclick=()=>post('/api/sleep',{minutes:+b.dataset.sleep}).then(()=>tick()));
document.querySelectorAll('[data-lim]').forEach(b=>b.onclick=()=>post('/api/settings',{volume_limit:+b.dataset.lim}).then(()=>tick()));
function updateCalib(cal){''')
rep("  updateSystem(st.system);\n", "  updateSystem(st.system);\n  try{updateSleep(st);}catch(e){}\n")

# ---- 4. fleet rows with live link status (SyFi / time sync / Wi-Fi RSSI), refreshed every 20 s
rep("loadCloud();setInterval(loadCloud,30000);",
    '''loadCloud();setInterval(loadCloud,30000);
async function loadFleetLive(){
  try{const d=await (await fetch('/api/fleet/live')).json(); window.__fleetLive={}; (d.cells||[]).forEach(c=>window.__fleetLive[c.serial]=c);
    document.querySelectorAll('[data-live]').forEach(el=>{const c=window.__fleetLive[el.dataset.live]; if(!c)return;
      el.innerHTML=c.ok?`${c.syfi?'<span class=good>SyFi ✓</span>':'<span class=fault>SyFi ✗</span>'} · ${c.sync?'<span class=good>sync ✓</span>':'<span class=fault>sync ✗</span>'}${c.rssi!=null?' · '+c.rssi+' dBm':''}`:'<span class=fault>agent unreachable</span>';});
  }catch(e){}
}
loadFleetLive();setInterval(loadFleetLive,20000);''')
rep('''      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}</span>
      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}''',
    '''      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}<div class=muted style="font-size:11px;font-weight:500" data-live="${c.serial}"></div></span>
      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}''')

# ---- 5. setup page: control ring + UI sound
rep('''  <h2 style="margin-top:16px">HOUSEKEEPING''', '''  <h2 style="margin-top:16px">CONTROL RING &amp; SOUNDS</h2>
  <div class=rowb><label class=muted>Ring volume steps <input type=number id=ringsteps min=10 max=400 step=10 style="width:80px"></label>
    <label class=muted><input type=checkbox id=ringcw> clockwise = louder</label>
    <label class=muted>UI sound level <input type=number id=uisnd min=0 max=60 step=5 style="width:70px"> %</label>
    <button class="btn sm" id=ringsave>Apply</button></div>
  <p class=hint>Ring steps = how many ring positions span the volume range (stock 100 — fewer = coarser, more = finer). Direction restarts the ring daemon (hidserver) on this cell only; the UI sound level is what Ping and the setup chimes play at (stock 25 %).</p>
  <h2 style="margin-top:16px">HOUSEKEEPING''')
rep("async function loadHK(){", '''async function loadRing(){try{const r=await api('/api/ring');$('#ringsteps').value=r.steps||100;$('#ringcw').checked=!!r.clockwise_up;$('#uisnd').value=r.ui_sound_pct==null?25:r.ui_sound_pct;}catch(e){}}
$('#ringsave').onclick=async()=>{try{const r=await api('/api/ring',{steps:+$('#ringsteps').value,clockwise_up:$('#ringcw').checked,ui_sound_pct:+$('#uisnd').value});toast('Applied ('+(r.changed||[]).join(', ')+')');}catch(e){toast(e.message,true);}loadRing();};
async function loadHK(){''')
rep("async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();}",
    "async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();}")

s = s.replace('"syng-agent (b28)"', '"syng-agent (b29)"').replace("syng-agent (b28) on", "syng-agent (b29) on").replace('AGENT_BUILD = "b28"', 'AGENT_BUILD = "b29"')
out = os.path.join(here, "syng-agent-b29.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
