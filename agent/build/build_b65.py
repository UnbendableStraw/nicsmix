#!/usr/bin/env python3
"""syng-agent-b65.py = b64 + "Unstick silent track changes" (AirPlay goes quiet between tracks).

What happens (airplay2source's own journal, 2026-10-18 19:07 UTC and earlier): when a sender moves to the next track
inside the same buffered stream (Apple Music on an Apple TV or iPhone does this; HomePods carry on), Syng's receiver
logs `_bufProcessPacket err = -6743` (CoreUtils kSizeErr) and drops the stream: the sender keeps going, the source
still says "playing", the room is silent. It comes back only when the sender flushes with a real sequence number —
a second skip, or play after a pause. Senders that tear the stream down between tracks never get stuck.

The watchdog (on whichever cell has the AirPlay source active — the primary):
  * follows airplay2source's journal: a -6743 marks a suspect; any of "ProcessPkt Flush Complete", "Teardown",
    "Format Changed", "Stopping stream", "session started" clears it;
  * reads the source's absolute level from the viz feed (rms_db, now kept in LAST_VIZ; a stale feed counts as silent);
  * stuck = a suspect not cleared, the source active and its TransportControl "playing", and silence (< -70 dB) for
    4 s -> pause, 1.2 s, play (the same flush the user's skip caused, but the track is kept). At most once per 45 s;
    two attempts in a row without the sound coming back and it stands down until the next track.
  * every event is kept (last 30) for GET /api/airplay ("silence": {events, recovered, failed}).
Setting "airplay_silence_fix" (default on), POST /api/airplay {"silence_fix": bool}; a switch in the AIRPLAY & ATMOS
card. Volume is never touched."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b64.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- the absolute level in the viz feed ----------
rep('''                LAST_VIZ.update({"energy": round(_vz["e"], 3), "bass": round(_vz["b"], 3), "beat": round(_vz["beat"], 3),''',
    '''                LAST_VIZ.update({"energy": round(_vz["e"], 3), "bass": round(_vz["b"], 3), "beat": round(_vz["beat"], 3), "rms_db": round(rms_db, 1),''')

# ---------- setting ----------
rep('''            "atmos_fill": False, "airplay_quick_recover": False}''',
    '''            "atmos_fill": False, "airplay_quick_recover": False, "airplay_silence_fix": True}''')

# ---------- the watchdog ----------
rep('''def airplay_local():''', r'''# ---------- unstick silent track changes (b65) ----------
SIL = {"suspect": None, "silent_since": None, "attempts": 0, "last_try": 0.0, "events": [], "recovered": 0, "failed": 0}
SIL_LOCK = threading.Lock()
SIL_CLEAR = ("ProcessPkt Flush Complete", "Teardown", "Format Changed", "Stopping stream", "session started")

def _sil_event(kind, **kw):
    e = dict({"t": round(time.time(), 1), "kind": kind}, **kw)
    with SIL_LOCK:
        SIL["events"] = (SIL["events"] + [e])[-30:]
    log("AIRPLAY SILENCE %s %s" % (kind, kw))

def _sil_resolved(how):
    """The stream is back (the receiver flushed properly, or the sound returned): count it if we had stepped in."""
    with SIL_LOCK:
        pend = SIL.get("pending"); SIL["pending"] = None
        SIL["suspect"] = None; SIL["attempts"] = 0; SIL["silent_since"] = None
        if pend: SIL["recovered"] += 1
    if pend: _sil_event("recovered", how=how, after_s=round(time.time() - pend, 1))

def airplay_journal_loop():
    """Follow airplay2source's journal for the -6743 that starts a stuck stream, and what clears it."""
    while True:
        try:
            p = subprocess.Popen(["journalctl", "-u", "airplay2source", "-f", "-n", "0", "-o", "cat", "--no-pager"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, bufsize=1)
            for ln in p.stdout:
                if "err = -6743" in ln:
                    with SIL_LOCK:
                        if SIL["suspect"] is None: SIL["suspect"] = time.time()
                elif any(k in ln for k in SIL_CLEAR):
                    _sil_resolved("receiver flushed")
        except Exception as e:
            log("airplay journal: %s" % e)
        time.sleep(5)

def _ap_playing():
    try:
        dest, path = source_target_cached("airplay2")
        if not dest or not path: return False
        rt = get_all(ASRV, "/com/syng/AudioSource/Runtime/airplay2", RUN_IF)
        if not rt.get("active"): return False
        tc = get_all(dest, path, "com.syng.AudioSource.TransportControl")
        return bool(tc.get("playing"))
    except Exception:
        return False

def _silent():
    VIZ_WANT[0] = time.time()                       # keep the viz receiver decoding every packet while we watch
    if time.time() - LAST_VIZ.get("t", 0) > 2.0: return True
    v = LAST_VIZ.get("rms_db")
    return v is not None and v < -70.0

def airplay_silence_loop():
    time.sleep(40)
    while True:
        time.sleep(1.0)
        try:
            if not SETTINGS.get("airplay_silence_fix", True): continue
            with SIL_LOCK: sus = SIL["suspect"]
            if sus is None: continue
            now = time.time()
            if not _silent():
                SIL["silent_since"] = None
                # sound after the error: either the buffer still draining (wait) or a stream that never stuck (clear)
                if SIL.get("pending") or now - sus > 8.0: _sil_resolved("sound came back")
                continue
            if SIL["silent_since"] is None: SIL["silent_since"] = now
            if now - SIL["silent_since"] < 4.0: continue
            if SIL["attempts"] >= 2:
                if SIL.get("pending") and now - SIL["last_try"] > 15:
                    with SIL_LOCK: SIL["pending"] = None; SIL["failed"] += 1; SIL["suspect"] = None
                    _sil_event("gave_up")
                continue
            if now - SIL["last_try"] < 45 and SIL["attempts"] == 0: continue
            if SIL["attempts"] == 1 and now - SIL["last_try"] < 8: continue
            if not _ap_playing(): continue
            SIL["attempts"] += 1; SIL["last_try"] = now
            if not SIL.get("pending"): SIL["pending"] = now
            _sil_event("stuck", silent_s=round(now - SIL["silent_since"], 1), since_err_s=round(now - sus, 1), attempt=SIL["attempts"])
            transport("airplay2", "pause"); time.sleep(1.2); transport("airplay2", "play")
        except Exception as e:
            log("airplay silence: %s" % e)

def airplay_local():''')

rep('''           "settings": {"fill": bool(SETTINGS.get("atmos_fill")),''',
    '''           "silence": {"on": bool(SETTINGS.get("airplay_silence_fix", True)), "recovered": SIL["recovered"], "failed": SIL["failed"],
                       "events": SIL["events"][-10:], "watching": SIL["suspect"] is not None},
           "settings": {"fill": bool(SETTINGS.get("atmos_fill")), "silence_fix": bool(SETTINGS.get("airplay_silence_fix", True)),''')
rep('''    for k, key in (("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover")):''',
    '''    for k, key in (("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover"), ("silence_fix", "airplay_silence_fix")):''')
rep('''        fwd = {k: body[k] for k in ("height", "fill", "quick_recover") if k in body}''',
    '''        fwd = {k: body[k] for k in ("fill", "quick_recover", "silence_fix") if k in body}''')
rep('''    threading.Thread(target=airplay_keeper_loop, daemon=True).start()
''', '''    threading.Thread(target=airplay_keeper_loop, daemon=True).start()
    threading.Thread(target=airplay_journal_loop, daemon=True).start()
    threading.Thread(target=airplay_silence_loop, daemon=True).start()
''')

# ---------- the card ----------
rep('''  <div class="rowb sw"><div><b>Quick AirPlay recovery</b>''',
'''  <div class="rowb sw"><div><b>Unstick silent track changes</b><div class=muted style="font-size:12px">When AirPlay goes quiet at a new track but still says "playing", pause and play it back in (<span id=apsil>…</span>)</div></div>
    <label class=switch><input type=checkbox id=apsilence><span></span></label></div>
  <div class="rowb sw"><div><b>Quick AirPlay recovery</b>''')
rep('''    const st=(me.settings||{}); $('#apfill').checked=!!st.fill; $('#apquick').checked=!!st.quick_recover;''',
'''    const st=(me.settings||{}); $('#apfill').checked=!!st.fill; $('#apquick').checked=!!st.quick_recover; $('#apsilence').checked=st.silence_fix!==false;
    const sl=cs.reduce((a,c)=>{const x=c.silence||{};a.r+=x.recovered||0;a.f+=x.failed||0;(x.events||[]).forEach(e=>{if(e.kind==='stuck'&&(!a.t||e.t>a.t))a.t=e.t;});return a;},{r:0,f:0,t:0});
    $('#apsil').textContent=(sl.r||sl.f)?`unstuck ${sl.r}×${sl.f?', '+sl.f+' not':''}${sl.t?' · last '+new Date(sl.t*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):''}`:'nothing to unstick yet';''')
rep('''async function apSet(k,v){try{const b={};b[k]=v;await api('/api/airplay',b);toast((k==='fill'?'Fill surround':'Quick recovery')+(v?' on':' off')+' — every speaker');setTimeout(loadAP,1500);}catch(e){toast(e.message,true);loadAP();}}''',
'''async function apSet(k,v){try{const b={};b[k]=v;await api('/api/airplay',b);toast((k==='fill'?'Fill surround':k==='silence_fix'?'Unstick silent track changes':'Quick recovery')+(v?' on':' off')+' — every speaker');setTimeout(loadAP,1500);}catch(e){toast(e.message,true);loadAP();}}
$('#apsilence').onchange=e=>apSet('silence_fix',e.target.checked);''')

s = s.replace('"syng-agent (b64)"', '"syng-agent (b65)"').replace("syng-agent (b64) on", "syng-agent (b65) on").replace('AGENT_BUILD = "b64"', 'AGENT_BUILD = "b65"')
out = os.path.join(here, "syng-agent-b65.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
