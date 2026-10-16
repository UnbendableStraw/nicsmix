#!/usr/bin/env python3
"""Build syng-agent-b19.py from b18 + the new SyngField renderer."""
import sys, re, os
src_path, field_js, tex_js, out_path = sys.argv[1:5]
s = open(src_path).read()
field = open(field_js).read()
tex = open(tex_js).read()
assert '"""' not in field and '"""' not in tex

def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:60], s.count(old))
    s = s.replace(old, new)

# ---------------- backend ----------------
rep("SOUND_DIST = [2.3]      # metres from the listener",
    "SOUND_DIST = [0.0]      # metres from the listener (0 = the puck rests on the listener, like the app)\n"
    "SOUND_ROT = [0.0]       # degrees, field direction (the notch), 0 = front\n"
    "SOUND_SPATIAL = [False] # False = classic / sound all around; True = the spatial fan\n"
    "FAN_R = float(os.environ.get('SOUND_FAN_R', '1.0'))  # metres: radius of the object fan around the sound image")
# the old set_objects() also used a SOUND_ROT list (radians); rename its uses to keep both working
s = s.replace("if rot is not None: SOUND_ROT[0] = float(rot)\n    ca = math.cos(SOUND_ROT[0]); sa = math.sin(SOUND_ROT[0])",
              "if rot is not None: OBJ_ROT[0] = float(rot)\n    ca = math.cos(OBJ_ROT[0]); sa = math.sin(OBJ_ROT[0])")
rep('"rot": SOUND_ROT[0]}', '"rot": OBJ_ROT[0]}')
# find the old SOUND_ROT definition used by set_objects and rename it
m = re.search(r"^SOUND_ROT = \[[^\]]*\].*$", s, re.M)
assert m and s.count("\nSOUND_ROT = [") == 2
first = s.index("\nSOUND_ROT = [")
second = s.index("\nSOUND_ROT = [", first + 1)
# whichever one is NOT ours (ours is followed by the SOUND_SPATIAL line)
if "SOUND_SPATIAL" in s[first:first+200]:
    old_def_at = second
else:
    old_def_at = first
line_end = s.index("\n", old_def_at + 1)
s = s[:old_def_at] + "\nOBJ_ROT = [" + s[old_def_at + len("\nSOUND_ROT = ["):line_end] + s[line_end:]

old_sf = s[s.index("def set_soundfield(aim=None, spread=None, dist=None):"):s.index("def calibration():")]
new_sf = '''def set_soundfield(aim=None, spread=None, dist=None, rot=None, spatial=None):
    """One-puck sound field, matching the Syng app's SetObjectPositions (decoded from a
    live capture). The puck = the sound image at (aim, dist) from the listener. Spatial on:
    a symmetric 5-object fan of radius FAN_R around the image, pointing along `rot` (the
    notch) - centre at focus 0.3, a pair at +/-spread/2 (0 dB), an inner pair at
    +/-spread*0.26 (-13 dB), the other 5 parked and muted. Spatial off (classic, sound all
    around) or spread >= 300: the captured 10-object surround ring, translated to the image."""
    if aim is not None: SOUND_AIM[0] = float(aim) % 360.0
    if spread is not None: SOUND_SPREAD[0] = max(0.0, min(360.0, float(spread)))
    if dist is not None: SOUND_DIST[0] = max(0.0, min(6.0, float(dist)))
    if rot is not None: SOUND_ROT[0] = float(rot) % 360.0
    if spatial is not None: SOUND_SPATIAL[0] = bool(spatial)
    aimd = SOUND_AIM[0]; sp = SOUND_SPREAD[0]; d0 = SOUND_DIST[0]; rotd = SOUND_ROT[0]
    lp = listener_live() or {"x_m": 2.0137, "y_m": 1.2239}
    lx = float(lp["x_m"]); ly = float(lp["y_m"])
    # the sound image (puck) in room metres
    a = math.radians(aimd); ix = lx + d0 * math.sin(a); iy = ly - d0 * math.cos(a)
    a0 = math.radians(rotd); half = math.radians(sp / 2.0)
    def obj(A, d, vol, focus, cx=ix, cy=iy):
        return {"snapToHorn": False, "distanceAttenuation": False, "volumeDecibels": round(vol, 2),
                "position": {"scope": "room", "focus": round(focus, 3), "height_layer": 0,
                             "location": {"x": round(cx + d * math.sin(A), 3),
                                          "y": round(cy - d * math.cos(A), 3), "z": 0}}}
    objs = {}
    if (not SOUND_SPATIAL[0]) or sp >= 300.0:
        base = math.radians(rotd - 19.0)
        for i, (ra, d, f, v) in enumerate(SURROUND_RING):
            objs[str(i)] = obj(math.radians(ra) + base, d, v, f)
    else:
        r = FAN_R
        objs["2"] = obj(a0,               r,       0.0,   0.3)
        objs["0"] = obj(a0 + half,        r,       0.0,   0.6)
        objs["1"] = obj(a0 - half,        r,       0.0,   0.6)
        objs["3"] = obj(a0 + half * 0.52, r * 1.1, -13.0, 0.6)
        objs["4"] = obj(a0 - half * 0.52, r * 1.1, -13.0, 0.6)
        for i in range(5, 10):
            objs[str(i)] = {"snapToHorn": False, "distanceAttenuation": False, "volumeDecibels": -100.0,
                            "position": {"scope": "room", "focus": 0.5, "height_layer": 0,
                                         "location": {"x": round(lx + 11, 3), "y": round(ly, 3), "z": 0}}}
    payload = json.dumps({"listener_position": {"x_m": lx, "y_m": ly, "version": "0"}, "objects": objs})
    sid = active_music_src(); n = 0
    if sid:
        try:
            with DBUS_LOCK:
                dbus.Interface(BUS.get_object(ASRV, "/com/syng/AudioSource/Runtime/" + sid), RUN_IF).SetObjectPositions(payload)
            n = 1
        except Exception: pass
    return n

'''
s = s.replace(old_sf, new_sf)
rep('''        out["room"]["field"] = {"aim": SOUND_AIM[0], "spread": SOUND_SPREAD[0],
                                "distance": d, "spatial": SOUND_SPREAD[0] > 70.0}''',
    '''        out["room"]["field"] = {"aim": SOUND_AIM[0], "spread": SOUND_SPREAD[0],
                                "distance": d, "spatial": SOUND_SPATIAL[0], "rot": SOUND_ROT[0]}''')
rep('''                r = set_soundfield(body.get("aim"), body.get("spread"), body.get("distance"))
                log("SOUNDFIELD aim=%s spread=%s dist=%s -> set=%s" % (body.get("aim"), body.get("spread"), body.get("distance"), r))
                self._json(200, {"ok": True, "set": r, "aim": SOUND_AIM[0], "spread": SOUND_SPREAD[0], "distance": SOUND_DIST[0]})''',
    '''                r = set_soundfield(body.get("aim"), body.get("spread"), body.get("distance"), body.get("rot"), body.get("spatial"))
                log("SOUNDFIELD aim=%s spread=%s dist=%s rot=%s spatial=%s -> set=%s" % (body.get("aim"), body.get("spread"), body.get("distance"), body.get("rot"), body.get("spatial"), r))
                self._json(200, {"ok": True, "set": r, "aim": SOUND_AIM[0], "spread": SOUND_SPREAD[0], "distance": SOUND_DIST[0], "rot": SOUND_ROT[0], "spatial": SOUND_SPATIAL[0]})''')


# ---------------- viz feed v2 + SSE ----------------
_parts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parts")
viz_v2 = open(os.path.join(_parts, "viz_v2.py")).read()
sse = open(os.path.join(_parts, "sse.py")).read()
a = s.index("def viz_web_loop():"); b = s.index("def py(v):")
s = s[:a] + viz_v2 + s[b:]
rep('            elif self.path.startswith("/api/viz/frame"): self._json(200, viz_frame())\n', sse)
rep("let vizBusy=false;", "let vizBusy=false, vizES=null;\ntry{ vizES=new EventSource('/api/viz/stream'); vizES.onmessage=e=>{try{window.__viz=JSON.parse(e.data);window.__vizT=performance.now();}catch(x){}}; vizES.onerror=()=>{}; }catch(e){ vizES=null; }")
rep("tickViz();setInterval(tickViz,90);", "if(!vizES){tickViz();setInterval(tickViz,90);} else setInterval(()=>{ if(performance.now()-(window.__vizT||0)>1500) tickViz(); },500);")

# ---------------- page: markup ----------------
rep('''  <div id=fieldwrap style="position:relative;width:100%;border-radius:14px;overflow:hidden;background:#02070c">
    <canvas id=fieldgl style="position:absolute;left:0;top:0;width:100%;height:100%;display:block"></canvas>
    <canvas id=field style="position:relative;width:100%;display:block;background:transparent;touch-action:none"></canvas>
  </div>
  <div class=legend><span>◉ drag the puck to move the sound</span><span>• lift the dot to go spatial</span><span>◗ handles aim &amp; widen</span><span>▂ bottom rim = volume</span></div>''',
    '''  <div id=fieldwrap style="position:relative;width:100%;border-radius:14px;overflow:hidden;background:#000"></div>
  <div class=legend><span>◉ drag the puck to move the sound</span><span>• lift the dot to go spatial</span><span>◗ handles aim &amp; widen</span><span>▂ bottom rim = volume</span></div>
  <details id=fieldtweaks style="margin-top:6px"><summary style="cursor:pointer;color:var(--muted);font-size:12px">Field tweaks</summary>
    <div class=srcs style=margin-top:8px><button class=srcbtn id=fldreset>Reset particles</button><button class=srcbtn id=fldcenter>Center the sound</button><button class=srcbtn id=fldclassic>Classic (all around)</button></div>
    <div id=fieldsliders></div>
  </details>''')

# ---------------- page: script ----------------
start = s.index("// ---- canvas room field (sparkles + spectrum ripples + positioned cells) ----")
end = s.index("// ---- system sparklines (client-side history; no device cost) ----")
new_js = tex + "\n" + field + r'''
// ---- the sound field (SyngField: faithful port of the Syng app's ProtoTools/Aurora field) ----
let fieldData=null, FIELD=null, fieldTouched=0;
function setField(fleet,room){fieldData={fleet,room};syncField();}
function syncField(){
  if(!FIELD||!fieldData)return; const room=fieldData.room, fld=room&&room.field; if(!fld)return;
  const st=FIELD.state; if(st.grab||performance.now()-fieldTouched<2500)return;
  FIELD.setPuckPolar(fld.aim||0,(fld.distance||0)/FIELD.cfg.scaleM);
  const sp=fld.spread!=null?fld.spread:190;
  FIELD.setSpatial(!!fld.spatial, Math.max(FIELD.cfg.minHalfDeg,Math.min(FIELD.cfg.maxHalfDeg,sp/2)), fld.rot||0);
}
function initField(){
  const wrap=$('#fieldwrap'); if(!wrap)return;
  const rings=()=>{const fd=fieldData; if(!fd)return []; const lis=(fd.room&&fd.room.listener)||{x_m:2.0137,y_m:1.2239}; const st=FIELD.state, s=st.R/FIELD.cfg.scaleM;
    return (fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>[st.C[0]+(c.position.x-lis.x_m)*s, st.C[1]-(c.position.y-lis.y_m)*s, !!c.activeSource]);};
  try{ FIELD=SyngField.create(wrap,{post:(p,b)=>post(p,b), viz:()=>window.__viz, rings, cfg:{scaleM:2.5},
       onChange:()=>{fieldTouched=performance.now();}}); }
  catch(e){ wrap.innerHTML='<div style="padding:30px;color:var(--muted);font-size:13px">WebGL is unavailable in this browser - the sound field needs it.</div>'; return; }
  window.__field=FIELD;
  $('#fldreset').onclick=()=>FIELD.resetParticles();
  $('#fldcenter').onclick=()=>{FIELD.center();fieldTouched=performance.now();post('/api/soundfield',{aim:0,distance:0});};
  $('#fldclassic').onclick=()=>{FIELD.setSpatial(false);fieldTouched=performance.now();post('/api/soundfield',{spatial:false,spread:360});};
  const T=[['particles','Particles',500,16000,100],['glowGain','Glow',0,2.5,0.05],['pulseGain','Pulse strength',0,5,0.05],['pulseScale','Pulse reach (lower = farther)',0.5,5,0.1],
           ['pointScale','Sparkle size',0.3,3,0.05],['pointGain','Sparkle brightness',0,3,0.05],['haloGain','Puck halo',0,3,0.05],['grain','Grain',0,2,0.05],['flurry','Flurry',0,3,0.05],['weightSpatial','Field weight',0.5,2,0.01],['handleRadius','Handle radius',0.3,0.9,0.01],['scaleM','Metres per room radius',1,6,0.1]];
  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim']];
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
'''
s = s[:start] + new_js + s[end:]

# volume sync from the state tick into the field's rim
rep("  if(!dragging.vol&&vs.volume!=null){const v=Math.round(vs.volume*100);$('#vol').value=v;$('#volval').textContent=v;}",
    "  if(!dragging.vol&&vs.volume!=null){const v=Math.round(vs.volume*100);$('#vol').value=v;$('#volval').textContent=v;if(FIELD&&!(FIELD.state.grab&&FIELD.state.grab.t==='vol'))FIELD.setVolume(vs.volume);}")
# the old soundstage slider/reset keep working through the API; drop the stale window flags
rep("wireTune('soundfocus','focv',v=>{window.__fieldSpread=v;post('/api/soundfield',{spread:v});},v=>v+'°'+(v>=300?' · surround':''));",
    "wireTune('soundfocus','focv',v=>{post('/api/soundfield',{spread:v,spatial:v<300});},v=>v+'°'+(v>=300?' · surround':''));")
rep("$('#soundreset').onclick=()=>{window.__fieldReset=true;post('/api/soundfield',{aim:0,spread:55,distance:2.3});",
    "$('#soundreset').onclick=()=>{if(FIELD)FIELD.center();post('/api/soundfield',{aim:0,distance:0});")
rep("  if(fld&&!dragging.soundfocus&&window.__fieldSpread==null){const sv=Math.round(fld.spread);",
    "  if(fld&&!dragging.soundfocus){const sv=Math.round(fld.spread);")
s = s.replace("syng-agent (phase2)", "syng-agent (b24 field)")
exec(compile(open(os.path.join(_parts, 'step_b25.py')).read(), 'step_b25', 'exec'))

open(out_path, "w").write(s)
print("wrote", out_path, len(s))
