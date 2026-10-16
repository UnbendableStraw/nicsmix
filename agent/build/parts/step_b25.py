# executed by integrate.py with `s` (agent source) and `rep` in scope
cellrpc = open(os.path.join(_parts, "cellrpc.py")).read()
a = s.index("def set_soundfield(aim=None, spread=None, dist=None, rot=None, spatial=None):"); b = s.index("def calibration():")
s = s[:a] + cellrpc + s[b:]
rep('        out["room"]["field"] = {"aim": SOUND_AIM[0], "spread": SOUND_SPREAD[0],\n                                "distance": d, "spatial": SOUND_SPATIAL[0], "rot": SOUND_ROT[0]}',
    '        out["room"]["field"] = dict(FIELD)')
old = s[s.index('                r = set_soundfield(body.get("aim"), body.get("spread"), body.get("distance"), body.get("rot"), body.get("spatial"))'):]
old = old[:old.index('\n', old.index('self._json(200, {"ok": True, "set": r, "aim"'))]
new = ('                keys = ("ux", "uy", "rot", "width", "radius", "spatial"); args = {k: body.get(k) for k in keys if k in body}\n'
       '                r = set_soundfield(**args)\n'
       '                log("SOUNDFIELD %s -> set=%s" % (args, r))\n'
       '                self._json(200, dict(FIELD, ok=True, set=r))')
s = s.replace(old, new)
rep("  FIELD.setPuckPolar(fld.aim||0,(fld.distance||0)/FIELD.cfg.scaleM);\n  const sp=fld.spread!=null?fld.spread:190;\n  FIELD.setSpatial(!!fld.spatial, Math.max(FIELD.cfg.minHalfDeg,Math.min(FIELD.cfg.maxHalfDeg,sp/2)), fld.rot||0);",
    "  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);\n  FIELD.setSpatial(!!fld.spatial, Math.max(FIELD.cfg.minHalfDeg,Math.min(FIELD.cfg.maxHalfDeg,fld.width!=null?fld.width:95)), 90-(fld.rot!=null?fld.rot:90));")
rep("$('#fldcenter').onclick=()=>{FIELD.center();fieldTouched=performance.now();post('/api/soundfield',{aim:0,distance:0});};",
    "$('#fldcenter').onclick=()=>{FIELD.center();fieldTouched=performance.now();post('/api/soundfield',{ux:0,uy:0});};")
rep("$('#fldclassic').onclick=()=>{FIELD.setSpatial(false);fieldTouched=performance.now();post('/api/soundfield',{spatial:false,spread:360});};",
    "$('#fldclassic').onclick=()=>{FIELD.setSpatial(false);fieldTouched=performance.now();post('/api/soundfield',{spatial:false});};")
rep("wireTune('soundfocus','focv',v=>{post('/api/soundfield',{spread:v,spatial:v<300});},v=>v+'°'+(v>=300?' · surround':''));",
    "wireTune('soundfocus','focv',v=>{post('/api/soundfield',{width:Math.min(180,v/2),spatial:v<300});},v=>v+'°'+(v>=300?' · surround':''));")
rep("$('#soundreset').onclick=()=>{if(FIELD)FIELD.center();post('/api/soundfield',{aim:0,distance:0});",
    "$('#soundreset').onclick=()=>{if(FIELD)FIELD.center();post('/api/soundfield',{ux:0,uy:0});")
rep("  if(fld&&!dragging.soundfocus){const sv=Math.round(fld.spread);", "  if(fld&&!dragging.soundfocus){const sv=Math.round(fld.spatial?fld.width*2:360);")
rep("  const rings=()=>{const fd=fieldData; if(!fd)return []; const lis=(fd.room&&fd.room.listener)||{x_m:2.0137,y_m:1.2239}; const st=FIELD.state, s=st.R/FIELD.cfg.scaleM;\n    return (fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>[st.C[0]+(c.position.x-lis.x_m)*s, st.C[1]-(c.position.y-lis.y_m)*s, !!c.activeSource]);};",
    "  const toUnits=(xm,ym)=>{const fd=fieldData; const ref=(fd&&fd.room&&fd.room.reference&&fd.room.reference.radius_m)?fd.room.reference:{x_m:0,y_m:0,radius_m:1.15,rotation_deg:0};\n"
    "    const dx=(xm-ref.x_m)/ref.radius_m, dy=(ym-ref.y_m)/ref.radius_m, a=-ref.rotation_deg*Math.PI/180; return [dx*Math.cos(a)-dy*Math.sin(a), dx*Math.sin(a)+dy*Math.cos(a)];};\n"
    "  const rings=()=>{const fd=fieldData; if(!fd)return []; const st=FIELD.state;\n"
    "    const out=(fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>{const u=toUnits(c.position.x,c.position.y); return [st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R, !!c.activeSource];});\n"
    "    const lis=fd.room&&fd.room.listener; if(lis&&lis.x_m!=null){const u=toUnits(lis.x_m,lis.y_m); out.listener=[st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R];}\n"
    "    return out;};")
s = s.replace('"syng-agent (b24 field)"', '"syng-agent (b25 field)"')
