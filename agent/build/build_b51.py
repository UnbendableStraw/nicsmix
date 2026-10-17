#!/usr/bin/env python3
"""syng-agent-b51.py = b50 + Soundcheck quality:
  - playback is paused on every active source for the duration of a Soundcheck and resumed after (chirps measured
    against music gave layouts that differed by metres between runs)
  - the patched pycalibrator runner keeps each run's raw ToF/AoA matrices in /data/syng-agent/calib-matrices.jsonl
  - "Soundcheck x3": three runs back to back, then one robust solve on the per-pair *median* ToF / circular-median
    AoA with the firmware's own physical_layout_estimation, tried from every reference cell, lowest residual wins;
    per-pair spread is reported so a bad pair (a speaker behind a wall, a door) is visible
  - /api/calibration/run {runs: 1|3}; status carries per-run progress and the residual"""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b50.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

rep('''def cal_run_thread():
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
    return {"ok": True}''', r'''CAL_MATRICES = LC_DIR + "/calib-matrices.jsonl"   # written by the patched pycalibrator runner (overlay)

def cal_pause_sources():
    """Pause whatever is playing (chirps measured against music gave layouts metres apart). Returns what to resume."""
    paused = []
    try:
        for sid, v in live_sources().items():
            if v.get("music") and v.get("active") and v.get("playing"):
                try: transport(sid, "pause"); paused.append(sid)
                except Exception as e: cal_log("could not pause %s: %s" % (sid, e))
    except Exception as e: cal_log("pause: %s" % e)
    if paused: cal_log("paused %s for the measurement" % ", ".join(paused))
    return paused

def cal_resume_sources(paused):
    for sid in paused:
        try: transport(sid, "play"); cal_log("resumed %s" % sid)
        except Exception as e: cal_log("could not resume %s: %s" % (sid, e))

def cal_one_run():
    """One DasCalibrator run; returns the results list pycalibrator posted back (or None)."""
    CAL_STATUS["last_results"] = None
    bus = dbus.SystemBus()   # own connection: Run blocks for minutes and must not hold DBUS_LOCK
    R = dbus.Interface(bus.get_object("com.syng.DasCalibrator", "/com/syng/DasCalibrator/Runner"), "com.syng.DasCalibrator")
    R.Run(timeout=1200)
    for _ in range(40):
        if CAL_STATUS.get("last_results") is not None: break
        time.sleep(0.5)
    return CAL_STATUS.get("last_results")

def cal_recent_matrices(n, since):
    out = []
    try:
        for line in open(CAL_MATRICES):
            try: d = json.loads(line)
            except Exception: continue
            if d.get("t", 0) >= since - 5: out.append(d)
    except Exception: pass
    return out[-n:]

def cal_combine(mats):
    """Robust layout from several runs: per-pair median ToF (both directions kept), circular-median AoA, the
    firmware's own solver tried with every cell as the reference, lowest residual wins. Returns (results, info)."""
    import numpy as np
    sys.path.insert(0, "/usr/lib/python3.7/site-packages")
    from syng.dsp.physical_layout_estimation import physical_layout_estimation as ple
    cells = mats[0]["cells"]
    if any(m["cells"] != cells for m in mats): raise RuntimeError("runs cover different cells")
    n = len(cells)
    tof = np.median(np.array([m["tof"] for m in mats]), axis=0)
    spread = (np.max(np.array([m["tof"] for m in mats]), axis=0) - np.min(np.array([m["tof"] for m in mats]), axis=0)) * 343.0
    A = np.array([m["aoa"] for m in mats])
    aoa = np.angle(np.exp(1j * A).mean(axis=0))
    up = np.array(mats[-1]["upside"])
    best = None
    for ref in range(n):
        order = [ref] + [i for i in range(n) if i != ref]
        T = tof[np.ix_(order, order)]; Ao = aoa[np.ix_(order, order)]
        try: xy, rot, err = ple(T, Ao)
        except Exception as e: continue
        res = float(err[-1])
        if best is None or res < best[0]:
            inv = [order.index(i) for i in range(n)]
            best = (res, xy[inv], rot[inv], ref)
    if best is None: raise RuntimeError("the solver failed for every reference cell")
    res, xy, rot, ref = best
    z = [1.5 if up[i][i] else 0.0 for i in range(n)]
    results = [{"serial_number": cells[i], "position": {"x": float(xy[i][0]), "y": float(xy[i][1]), "z": z[i], "rotation": float(rot[i])}} for i in range(n)]
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            d = (tof[i][j] + tof[j][i]) / 2 * 343.0
            pairs.append({"a": cells[i][-5:], "b": cells[j][-5:], "distance_m": round(float(d), 2), "spread_m": round(float(max(spread[i][j], spread[j][i])), 2),
                          "reciprocity_m": round(float(abs(tof[i][j] - tof[j][i]) * 343.0), 2)})
    return results, {"residual": res, "reference": cells[ref][-5:], "pairs": pairs, "runs": len(mats)}

def cal_run_thread(runs=1):
    paused = []
    try:
        cal_log("Soundcheck x%d: DasCalibrator.Runner.Run() on this cell — chirps on every cell, keep the room quiet" % runs)
        paused = cal_pause_sources()
        time.sleep(1.5)
        t0 = time.time(); last = None
        CAL_STATUS["collect"] = runs > 1
        for k in range(runs):
            CAL_STATUS["run"] = k + 1; CAL_STATUS["runs"] = runs
            cal_log("run %d of %d" % (k + 1, runs))
            last = cal_one_run()
            if last is None:
                cal_log("no results were posted back — is pycalibrator/cloudapi.py patched on this cell?"); CAL_STATUS["ok"] = False; return
            cal_log("run %d done" % (k + 1))
        if runs > 1:
            mats = cal_recent_matrices(runs, t0)
            if len(mats) < runs:
                cal_log("only %d matrix dump(s) found (runner.py patch missing?) — applying the last run as is" % len(mats))
                CAL_STATUS["collect"] = False; op_calibration_results(last); return
            results, info = cal_combine(mats)
            walls = {r["serial_number"]: r.get("nearby_walls") for r in last if r.get("nearby_walls") is not None}
            for r in results:
                if r["serial_number"] in walls: r["nearby_walls"] = walls[r["serial_number"]]
            bad = [p for p in info["pairs"] if p["spread_m"] > 0.6 or p["reciprocity_m"] > 0.6]
            cal_log("combined %d runs: residual %.4f (reference %s)%s" % (info["runs"], info["residual"], info["reference"],
                    ("; noisy pairs: " + ", ".join("%s-%s spread %.1f m" % (p["a"], p["b"], p["spread_m"]) for p in bad)) if bad else ""))
            CAL_STATUS["combine"] = info
            CAL_STATUS["collect"] = False
            op_calibration_results(results)
    except Exception as e:
        cal_log("Run() failed: %s" % e); CAL_STATUS["ok"] = False
    finally:
        CAL_STATUS["collect"] = False
        cal_resume_sources(paused)
        CAL_STATUS["running"] = False; CAL_STATUS["finished"] = time.time()

def op_calibrate(runs=1):
    require_primary()
    if CAL_STATUS["running"]: return {"ok": False, "error": "already running"}
    runs = max(1, min(5, int(runs or 1)))
    CAL_STATUS.update({"running": True, "started": time.time(), "finished": None, "ok": None, "log": [], "last_results": None, "run": 0, "runs": runs, "combine": None})
    threading.Thread(target=cal_run_thread, args=(runs,), daemon=True).start()
    return {"ok": True, "runs": runs}''')

# results handler: while collecting runs, just record
rep('''def op_calibration_results(results):
    """pycalibrator's PUT /v2/cell/calibration, redirected here: positions + walls -> doc, circle, listener."""
    with LC_LOCK:
        _ensure_dir()
        try: open(LC_CAL_LOG, "a").write(json.dumps({"t": time.time(), "results": results}) + "\\n")
        except Exception: pass''', '''def op_calibration_results(results):
    """pycalibrator's PUT /v2/cell/calibration, redirected here: positions + walls -> doc, circle, listener."""
    with LC_LOCK:
        _ensure_dir()
        try: open(LC_CAL_LOG, "a").write(json.dumps({"t": time.time(), "results": results}) + "\\n")
        except Exception: pass
        if CAL_STATUS.get("collect"):   # a multi-run Soundcheck: keep this run, the combined solve is applied at the end
            CAL_STATUS["last_results"] = results
            cal_log("run result recorded (%d cells), waiting for the other runs" % len(results or []))
            return {"ok": True, "collected": True}''')

rep('''    if path.startswith("/api/calibration/run"): return op_calibrate()''', '''    if path.startswith("/api/calibration/run"): return op_calibrate(body.get("runs", 1))''')

# page: x3 button + copy
rep('''    <button class="btn live" id=calib>Soundcheck (calibrate)</button>''', '''    <button class="btn live" id=calib>Soundcheck (calibrate)</button>
    <button class="btn" id=calib3 title="Three measurement passes combined robustly — about three times as long, much steadier layout">Soundcheck ×3</button>''')
rep('''  try{const r=await api('/api/calibration/run',null,primaryIp); if(r.ok===false)throw new Error(r.error||'busy'); toast('Soundcheck started'); pollCal();}catch(e){toast('Soundcheck: '+e.message,true);}
};''', '''  try{const r=await api('/api/calibration/run',{runs:1},primaryIp); if(r.ok===false)throw new Error(r.error||'busy'); toast('Soundcheck started'); pollCal();}catch(e){toast('Soundcheck: '+e.message,true);}
};
$('#calib3').onclick=async()=>{
  if(!confirm('Run Soundcheck three times and combine the measurements? Playback is paused for the duration (about three times a normal Soundcheck). Keep the room quiet.'))return;
  try{const r=await api('/api/calibration/run',{runs:3},primaryIp); if(r.ok===false)throw new Error(r.error||'busy'); toast('Soundcheck ×3 started'); pollCal();}catch(e){toast('Soundcheck: '+e.message,true);}
};''')
rep('''    $('#calstate').textContent=s.running?('running · '+Math.round((Date.now()/1000-s.started))+' s'):(s.finished?(s.ok?'last Soundcheck ok':'last Soundcheck failed'):'');''',
    '''    $('#calstate').textContent=s.running?('running'+(s.runs>1?' · pass '+s.run+' of '+s.runs:'')+' · '+Math.round((Date.now()/1000-s.started))+' s'):(s.finished?(s.ok?'last Soundcheck ok'+(s.combine?' · ×'+s.combine.runs+' residual '+s.combine.residual.toFixed(3):''):'last Soundcheck failed'):'');''')
rep('''<p class=hint>Soundcheck plays chirps from every speaker in turn (about a minute per speaker) and measures positions, orientation, walls and room EQ on the speakers themselves. Keep the room quiet.''',
    '''<p class=hint>Soundcheck plays chirps from every speaker in turn (about a minute per speaker) and measures positions, orientation, walls and room EQ on the speakers themselves; playback is paused meanwhile. Keep the room quiet. <b>×3</b> runs it three times and solves the layout from the median of the measurements — use it when a single run looks off.''')

s = s.replace('"syng-agent (b50)"', '"syng-agent (b51)"').replace("syng-agent (b50) on", "syng-agent (b51) on").replace('AGENT_BUILD = "b50"', 'AGENT_BUILD = "b51"')
out = os.path.join(here, "syng-agent-b51.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
