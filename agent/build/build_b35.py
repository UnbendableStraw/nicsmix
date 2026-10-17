#!/usr/bin/env python3
"""syng-agent-b35.py = b34 + Scenes: one-tap presets (volume, tone, sound field, LED mode/tune/palette, volume limit,
height/beyond toggles), applied room-wide, optionally scheduled by time of day."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b34.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

SCENES_PY = r'''
# ---------- scenes: capture the room's sound + light and bring it back with one tap (or on a schedule) ----------
def scene_capture():
    srcs = live_sources(); act = next((k for k, v in srcs.items() if v.get("music") and v.get("active")), None)
    rep_src = srcs.get(act) or next((v for v in srcs.values() if v.get("music")), {})
    tone = rep_src.get("tone") or next((v.get("tone") for v in srcs.values() if v.get("tone")), None) or {}
    led = led_status()
    return {"volume": rep_src.get("volume"), "tone": {"bass": tone.get("bass", 0.0), "tilt": tone.get("tilt", 0.0)},
            "field": {k: FIELD[k] for k in ("ux", "uy", "rot", "width", "radius", "spatial", "snap")},
            "led": {"mode": led.get("mode") if led.get("reactive") else "off", "palette": led.get("palette"), "tune": {k: v for k, v in (led.get("tune") or {}).items() if v is not None}},
            "spatial": {"height": bool(rep_src.get("height")), "beyond": bool(rep_src.get("beyond"))},
            "volume_limit": SETTINGS.get("volume_limit", 0), "source": act}

def scene_apply(sc, with_source=False):
    done = {}
    if sc.get("volume_limit") is not None: SETTINGS["volume_limit"] = int(sc["volume_limit"]); save_settings(); done["volume_limit"] = SETTINGS["volume_limit"]
    if sc.get("volume") is not None:
        v = float(sc["volume"]); lim = float(SETTINGS.get("volume_limit") or 0) / 100.0
        if lim > 0: v = min(v, lim)
        done["volume"] = set_volume(None, v)
    if sc.get("tone"): done["tone"] = set_tone(sc["tone"].get("bass", 0), sc["tone"].get("tilt", 0))
    if sc.get("spatial"):
        done["height"] = set_bool_prop("enableHeightProcessing", bool(sc["spatial"].get("height")))
        done["beyond"] = set_bool_prop("enableBeyondStereoDuplication", bool(sc["spatial"].get("beyond")))
    if with_source and sc.get("source"):
        try: set_source(sc["source"], True); done["source"] = sc["source"]; time.sleep(1.0)
        except Exception as e: done["source"] = str(e)
    if sc.get("field"): done["field"] = set_soundfield(**{k: sc["field"].get(k) for k in ("ux", "uy", "rot", "width", "radius", "spatial", "snap") if k in sc["field"]})
    led = sc.get("led") or {}
    if led:
        body = {"mode": led.get("mode") or "off"}
        flags = []
        tune = led.get("tune") or {}
        for k, flag in (("beatSens", "--beat-sens"), ("floor", "--floor"), ("cycle", "--cycle"), ("silentFade", "--silent-fade"), ("beatFade", "--beat-fade")):
            if tune.get(k) is not None: flags.append((flag, str(tune[k])))
        if led.get("palette"): flags.append(("--palette", led["palette"]))
        def led_local():
            if flags and body["mode"] != "off": set_led_flags(flags)   # writes the args and restarts once
            set_reactive(body["mode"])
        led_local()
        # every other cell in the room: same args, same mode (their agents apply locally)
        for serial, c in room_doc()["cells"].items():
            if serial == whoami().get("serial") or not c.get("ip_address"): continue
            try:
                if flags and body["mode"] != "off": agent_post(c["ip_address"], "/api/led/tune", dict({k: tune[k] for k in tune}, _local=True), timeout=15)
                if led.get("palette"): agent_post(c["ip_address"], "/api/led/palette", {"palette": led["palette"], "_local": True}, timeout=15)
                agent_post(c["ip_address"], "/api/led/reactive", {"mode": body["mode"], "_local": True}, timeout=15)
            except Exception as e: done.setdefault("led_peers", {})[serial] = str(e)
        done["led"] = body["mode"]
    return done

def scenes_get(): return SETTINGS.setdefault("scenes", {})
def scene_save(name, include=None, capture=None):
    sc = capture or scene_capture()
    if include:   # only keep the parts asked for
        sc = {k: v for k, v in sc.items() if k in include or k == "source"}
    old = scenes_get().get(name) or {}
    sc["at"] = old.get("at"); sc["days"] = old.get("days"); sc["saved"] = time.time()
    scenes_get()[name] = sc; save_settings(); return sc
def scene_delete(name):
    scenes_get().pop(name, None); save_settings(); return True
def scene_schedule(name, at, days):
    sc = scenes_get().get(name)
    if not sc: raise RuntimeError("no scene %s" % name)
    sc["at"] = at or None; sc["days"] = days if days else None; save_settings(); return sc
LAST_SCENE = {"name": None, "t": 0, "fired": {}}
def scene_localtime():
    """The cells run on UTC; the dashboard tells us the browser's UTC offset (minutes, JS getTimezoneOffset sign) once."""
    off = SETTINGS.get("tz_offset_min")
    return time.gmtime(time.time() - 60 * float(off)) if off is not None else time.localtime()
def scene_scheduler_loop():
    time.sleep(30)
    while True:
        try:
            me = whoami()
            if (me.get("role") or "").lower() != "secondary":    # the primary runs the room's schedule
                lt = scene_localtime(); hhmm = "%02d:%02d" % (lt.tm_hour, lt.tm_min); wd = (lt.tm_wday + 1) % 7   # 0 = Sunday
                for name, sc in list(scenes_get().items()):
                    if sc.get("at") == hhmm and (not sc.get("days") or wd in sc["days"]):
                        key = name + "@" + time.strftime("%Y-%m-%d ") + hhmm
                        if LAST_SCENE["fired"].get(key): continue
                        LAST_SCENE["fired"][key] = True; LAST_SCENE.update({"name": name, "t": time.time()})
                        log("SCENE scheduled '%s' -> %s" % (name, scene_apply(sc)))
        except Exception as e: log("scene scheduler: %s" % e)
        time.sleep(20)
'''
rep("def cloudblock_all():", SCENES_PY + "\ndef cloudblock_all():")
rep('''    if path.startswith("/api/fleet/live"): return fleet_live()''',
    '''    if path.startswith("/api/fleet/live"): return fleet_live()
    if path.startswith("/api/scenes/current"): return scene_capture()
    if path.startswith("/api/scenes"): return {"scenes": scenes_get(), "last": LAST_SCENE["name"], "tz_now": time.strftime("%H:%M", scene_localtime()), "tz_offset_min": SETTINGS.get("tz_offset_min")}''')
rep('''    if path.startswith("/api/ring"): return ring_set(body.get("steps"), body.get("clockwise_up"), body.get("ui_sound_pct"))''',
    '''    if path.startswith("/api/ring"): return ring_set(body.get("steps"), body.get("clockwise_up"), body.get("ui_sound_pct"))
    if path.startswith("/api/scenes/tz"):
        SETTINGS["tz_offset_min"] = float(body.get("offset", 0)); save_settings(); return {"ok": True, "tz_now": time.strftime("%H:%M", scene_localtime())}
    if path.startswith("/api/scenes/save"): return {"ok": True, "scene": scene_save(body["name"], body.get("include"))}
    if path.startswith("/api/scenes/apply"):
        sc = scenes_get().get(body["name"])
        if not sc: raise RuntimeError("no scene %s" % body["name"])
        LAST_SCENE.update({"name": body["name"], "t": time.time()}); return {"ok": True, "done": scene_apply(sc, bool(body.get("source")))}
    if path.startswith("/api/scenes/delete"): return {"ok": scene_delete(body["name"])}
    if path.startswith("/api/scenes/schedule"): return {"ok": True, "scene": scene_schedule(body["name"], body.get("at"), body.get("days"))}''')
rep("    threading.Thread(target=field_keeper_loop, daemon=True).start()\n", "    threading.Thread(target=field_keeper_loop, daemon=True).start()\n    threading.Thread(target=scene_scheduler_loop, daemon=True).start()\n")

# ---- dashboard card (before SYNG CLOUD)
rep('<section class=card id=cloudcard><h2>SYNG CLOUD <span class=rn id=cloudsum></span></h2>',
    '''<section class=card id=scenecard><h2>SCENES <span class=rn id=scenenow style="font-size:12px;color:var(--muted);font-weight:500"></span><button class="srcbtn" id=scenesave style="float:right;flex:none;min-width:0;padding:4px 10px;font-size:12px">Save current as…</button></h2>
  <div class=srcs id=scenes style="margin-bottom:8px"></div>
  <div id=sceneedit style="display:none;margin:6px 0 10px;padding:10px 12px;background:var(--surface2);border-radius:12px;font-size:13px">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b id=scename></b>
      <span class=muted id=scedesc style="font-size:12px"></span></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px">
      <label class=muted style="font-size:12px">Every day at <input type=time id=sceat style="font:inherit;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:4px 6px"></label>
      <span id=scedays style="display:flex;gap:4px"></span>
      <button class="srcbtn" id=scesched style="flex:none;min-width:0;padding:4px 10px;font-size:12px">Set schedule</button>
      <button class="srcbtn" id=sceunsched style="flex:none;min-width:0;padding:4px 10px;font-size:12px">No schedule</button>
      <button class="srcbtn" id=sceupdate style="flex:none;min-width:0;padding:4px 10px;font-size:12px">Overwrite with current</button>
      <button class="srcbtn" id=scedel style="flex:none;min-width:0;padding:4px 10px;font-size:12px;color:var(--bad)">Delete</button>
      <button class="srcbtn" id=sceclose style="flex:none;min-width:0;padding:4px 10px;font-size:12px">Close</button></div>
  </div>
  <p class=hint style="font-size:12px;color:var(--muted);margin:0">A scene is the room's volume, tone, sound field, LED mode and volume limit, saved on the primary. Tap to recall everywhere; the gear edits a daily schedule (the primary fires it). Schedule times are in this browser's time zone (speaker thinks it is <span id=scetz>…</span>).</p>
</section>

<section class=card id=cloudcard><h2>SYNG CLOUD <span class=rn id=cloudsum></span></h2>''')
rep("loadCloud();setInterval(loadCloud,30000);", '''loadCloud();setInterval(loadCloud,30000);
let SCENES={}, sceneSel=null; const DAYN=['Su','Mo','Tu','We','Th','Fr','Sa'];
async function loadScenes(){ try{ const d=await (await fetch('/api/scenes')).json(); SCENES=d.scenes||{}; $('#scetz').textContent=d.tz_now||'';
  const off=new Date().getTimezoneOffset(); if(d.tz_offset_min==null||Math.abs(d.tz_offset_min-off)>0.5){ post('/api/scenes/tz',{offset:off}); }
  $('#scenenow').textContent=d.last?('last: '+d.last):'';
  const names=Object.keys(SCENES).sort();
  $('#scenes').innerHTML=names.map(n=>{const sc=SCENES[n]; const sch=sc.at?(' · '+sc.at+(sc.days&&sc.days.length&&sc.days.length<7?' '+sc.days.map(x=>DAYN[x]).join(''):'')):'';
    return `<span style="display:flex;flex:1 1 140px;min-width:140px"><button class="srcbtn ${d.last===n?'on':''}" data-scene="${n}" style="flex:1;border-radius:12px 0 0 12px">${n}<span class=muted style="font-weight:500;font-size:11px">${sch}</span></button><button class=srcbtn data-sceneedit="${n}" title="Schedule / edit" style="flex:none;min-width:0;padding:0 10px;border-radius:0 12px 12px 0;border-left:none">⚙</button></span>`;}).join('')||'<span class=muted style="font-size:13px">No scenes yet — set the room the way you like it, then “Save current as…”.</span>';
  $('#scenes').querySelectorAll('[data-scene]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post('/api/scenes/apply',{name:b.dataset.scene});}finally{setTimeout(()=>{b.disabled=false;loadScenes();tick();},1500);}});
  $('#scenes').querySelectorAll('[data-sceneedit]').forEach(b=>b.onclick=()=>openScene(b.dataset.sceneedit));
 }catch(e){} }
function openScene(n){ const sc=SCENES[n]; if(!sc)return; sceneSel=n; $('#sceneedit').style.display=''; $('#scename').textContent=n;
  const parts=[]; if(sc.volume!=null)parts.push('vol '+Math.round(sc.volume*100)); if(sc.tone)parts.push('tone '+Math.round((sc.tone.bass||0)*100)+'/'+Math.round((sc.tone.tilt||0)*100)); if(sc.field)parts.push(sc.field.spatial?'spatial':'classic'); if(sc.led)parts.push('LED '+sc.led.mode); if(sc.volume_limit)parts.push('limit '+sc.volume_limit+'%');
  $('#scedesc').textContent=parts.join(' · '); $('#sceat').value=sc.at||'';
  const days=sc.days&&sc.days.length?sc.days:[0,1,2,3,4,5,6];
  $('#scedays').innerHTML=DAYN.map((d,i)=>`<button class="srcbtn ${days.includes(i)?'on':''}" data-day=${i} style="flex:none;min-width:0;padding:3px 7px;font-size:11px">${d}</button>`).join('');
  $('#scedays').querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>b.classList.toggle('on'));
}
$('#scenesave').onclick=async()=>{const n=prompt('Scene name (e.g. Movie, Party, Night)'); if(!n)return; await post('/api/scenes/save',{name:n.trim()}); loadScenes();};
$('#scesched').onclick=async()=>{const at=$('#sceat').value; if(!at){alert('Pick a time first');return;} const days=[...$('#scedays').querySelectorAll('[data-day].on')].map(b=>+b.dataset.day); await post('/api/scenes/schedule',{name:sceneSel,at,days:days.length===7?null:days}); loadScenes();};
$('#sceunsched').onclick=async()=>{await post('/api/scenes/schedule',{name:sceneSel,at:null,days:null}); loadScenes();};
$('#sceupdate').onclick=async()=>{if(!confirm('Overwrite "'+sceneSel+'" with the current room state?'))return; await post('/api/scenes/save',{name:sceneSel}); loadScenes(); openScene(sceneSel);};
$('#scedel').onclick=async()=>{if(!confirm('Delete scene "'+sceneSel+'"?'))return; await post('/api/scenes/delete',{name:sceneSel}); $('#sceneedit').style.display='none'; loadScenes();};
$('#sceclose').onclick=()=>{$('#sceneedit').style.display='none';};
loadScenes();setInterval(loadScenes,30000);''')
s = s.replace('"syng-agent (b34)"', '"syng-agent (b35)"').replace("syng-agent (b34) on", "syng-agent (b35) on").replace('AGENT_BUILD = "b34"', 'AGENT_BUILD = "b35"')
out = os.path.join(here, "syng-agent-b35.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
