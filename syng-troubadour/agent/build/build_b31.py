#!/usr/bin/env python3
"""syng-agent-b31.py = b30 + any plugged-in source (USB-C, HDMI, Pure Data) is selectable, the sound-field handle
is persisted and re-applied whenever a source becomes active (it used to reset to the cloud default on a new session)."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b30.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

rep('MUSIC = ("airplay2", "spotify", "tidal")',
    '''MUSIC = ("airplay2", "spotify", "tidal")
def is_music(sid):
    """A selectable playback source: anything the sound server registers except the calibration client and
       one-shot file players (file@…), so hot-plugged USB-C / HDMI / Pure Data sources show up too."""
    sid = str(sid or "")
    return bool(sid) and not sid.startswith(("Calibration", "file@", "file")) and sid != "saplayer"''')
rep('    targets = MUSIC if sid in (None, "", "all") else [sid]', '    targets = [x for x in source_ids() if is_music(x)] if sid in (None, "", "all") else [sid]')
rep('        if music_only and sid not in MUSIC: continue', '        if music_only and not is_music(sid): continue')
rep('        if s not in MUSIC: continue', '        if not is_music(s): continue')
rep('        if sid not in MUSIC: continue', '        if not is_music(sid): continue')
rep('            out[sid] = {"active": bool(rt.get("active")), "music": sid in MUSIC}; continue', '            out[sid] = {"active": bool(rt.get("active")), "music": is_music(sid)}; continue')
rep('''                    "controls": [str(x) for x in (tc.get("supportedControls") or [])], "music": sid in MUSIC,''',
    '''                    "controls": [str(x) for x in (tc.get("supportedControls") or [])], "music": is_music(sid), "displayName": base.get("displayName"),''')
rep("const ICON={airplay2:'AirPlay',spotify:'Spotify',tidal:'TIDAL',CalibrationClient:'Calibrate'};",
    "const ICON={airplay2:'AirPlay',spotify:'Spotify',tidal:'TIDAL',CalibrationClient:'Calibrate',usbgadget:'USB-C',usbgadgetsource:'USB-C',usbhost:'USB in',usbhostsource:'USB in',hdmi:'HDMI',hdmisource:'HDMI',pd:'Pure Data',pdaudiosource:'Pure Data'};\nconst srcName=(id,s)=>ICON[id]||(s&&s.displayName)||id;")
rep('''  $('#srcs').innerHTML=ids.map(id=>`<button class="srcbtn ${sources[id].active?'on':''}" data-src="${id}">${ICON[id]||id}</button>`).join('');''',
    '''  $('#srcs').innerHTML=ids.map(id=>`<button class="srcbtn ${sources[id].active?'on':''}" data-src="${id}">${srcName(id,sources[id])}</button>`).join('');''')

# persist + re-apply the field
rep('''    FIELD["version"] = str(uuid.uuid4()).upper()
    handles = {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),''',
    '''    FIELD["version"] = str(uuid.uuid4()).upper()
    try: FIELD_DIRTY[0] = True
    except Exception: pass
    handles = {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),''')
rep('FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "snap": "", "version": "0"}',
    'FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "snap": "", "version": "0"}\nFIELD_DIRTY = [False]')
FIELD_KEEPER = r'''
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
'''
rep("def cloudblock_all():", FIELD_KEEPER + "\ndef cloudblock_all():")
rep("    load_settings()\n", "    load_settings(); field_load()\n    threading.Thread(target=field_keeper_loop, daemon=True).start()\n")
s = s.replace('"syng-agent (b30)"', '"syng-agent (b31)"').replace("syng-agent (b30) on", "syng-agent (b31) on").replace('AGENT_BUILD = "b30"', 'AGENT_BUILD = "b31"')
out = os.path.join(here, "syng-agent-b31.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
