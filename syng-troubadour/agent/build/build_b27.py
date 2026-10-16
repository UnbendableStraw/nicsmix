#!/usr/bin/env python3
"""syng-agent-b27.py = b26 + field resize fix, un-stretched tone curve, theme switch, Syng-cloud toggles
(this cell / whole room), housekeeping (journal, core dumps, process RSS), richer fleet rows."""
import os, re
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b26.py")).read()
hk = open(os.path.join(here, "housekeeping.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# ---- 1. the field: never trust the first measurement; rescale pixel-space state when the wrap changes size
FIELD_RESIZE_OLD = '''  function resize(){
    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min((navigator.maxTouchPoints>0)?1.5:2, window.devicePixelRatio||1);
    st.W=W; st.H=H; st.DPR=DPR;'''
FIELD_RESIZE_NEW = '''  function resize(){
    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min((navigator.maxTouchPoints>0)?1.5:2, window.devicePixelRatio||1);
    const k = (st.W && W!==st.W) ? W/st.W : 1;   // the page re-flowed (fonts, scrollbar, first layout): keep the puck where it was
    if(k!==1 && st.puckInit){ st.puck[0]*=k; st.puck[1]*=k; st.puckT[0]*=k; st.puckT[1]*=k; if(st.liftDot){ st.liftDot[0]*=k; st.liftDot[1]*=k; } }
    st.W=W; st.H=H; st.DPR=DPR;'''
FIELD_OBS_OLD = "  resize(); window.addEventListener('resize', resize);"
FIELD_OBS_NEW = '''  resize(); window.addEventListener('resize', resize);
  try{ new ResizeObserver(()=>{ if(wrap.clientWidth && wrap.clientWidth!==st.W) resize(); }).observe(wrap); }catch(e){}'''
FIELD_FRAME_OLD = "  function frame(now){\n    requestAnimationFrame(frame);"
FIELD_FRAME_NEW = "  function frame(now){\n    requestAnimationFrame(frame);\n    if(wrap.clientWidth && wrap.clientWidth!==st.W) resize();"
def patch_field(src):
    for a, b in ((FIELD_RESIZE_OLD, FIELD_RESIZE_NEW), (FIELD_OBS_OLD, FIELD_OBS_NEW), (FIELD_FRAME_OLD, FIELD_FRAME_NEW)):
        assert src.count(a) == 1, a[:50]
        src = src.replace(a, b)
    return src
s = patch_field(s)
for fj in (os.path.join(here, "..", "web", "field.js"),):
    if os.path.exists(fj):
        t = open(fj).read()
        if FIELD_RESIZE_OLD in t: open(fj, "w").write(patch_field(t)); print("patched", fj)

# ---- 2. tone curve: draw at the element's real width instead of stretching a 300-unit viewBox (text got stretched)
rep('''  const bass=(+$('#bass').value)/100, tilt=(+$('#tilt').value)/100, W=300,H=66,mid=H/2;
  let d='';''', '''  const bass=(+$('#bass').value)/100, tilt=(+$('#tilt').value)/100, W=Math.max(120,el.clientWidth||300),H=66,mid=H/2;
  el.setAttribute('viewBox','0 0 '+W+' '+H);
  let d='';''')
rep("'<text x=\"'+(W-30)+'\" y=\"'+(H-6)+'\" fill=\"var(--muted)\" font-size=\"9\">HIGHS</text>';",
    "'<text x=\"'+(W-34)+'\" y=\"'+(H-6)+'\" fill=\"var(--muted)\" font-size=\"9\">HIGHS</text>';")
rep("window.addEventListener('resize',sizeCanvas);", "window.addEventListener('resize',sizeCanvas);")  # (setup page, untouched; sanity)
s = s.replace("initField();tickG();", "window.addEventListener('resize',()=>{try{drawTone();}catch(e){}});initField();tickG();", 1)

# ---- 3. theme: auto / dark / light (saved per browser), on both pages
CSS_OLD = '''@media (prefers-color-scheme:light){:root{--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;
 --line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}}'''
CSS_NEW = '''@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;
 --line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}}
:root[data-theme=light]{--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;--line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}
.themebtn{font:inherit;font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer;margin-left:10px}'''
rep(CSS_OLD, CSS_NEW, 2)
THEME_JS = '''<script>(function(){const T=['auto','dark','light'];let t='auto';try{t=localStorage.getItem('syngTheme')||'auto';}catch(e){}
function apply(){if(t==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',t);document.querySelectorAll('.themebtn').forEach(b=>b.textContent='theme: '+t);}
window.cycleTheme=function(){t=T[(T.indexOf(t)+1)%T.length];try{localStorage.setItem('syngTheme',t);}catch(e){}apply();};apply();window.addEventListener('DOMContentLoaded',apply);})();</script>'''
rep('<header><div class=mark>SYNG <b>·</b> LAN <a href="/setup" style="margin-left:14px;letter-spacing:0;font-weight:500;color:var(--muted);text-decoration:none">setup ›</a></div>',
    THEME_JS + '\n<header><div class=mark>SYNG <b>·</b> LAN <a href="/setup" style="margin-left:14px;letter-spacing:0;font-weight:500;color:var(--muted);text-decoration:none">setup ›</a><button class=themebtn onclick="cycleTheme()">theme</button></div>')
rep('<header><div class=mark><b>SYNG</b> · SETUP <a href="/">← dashboard</a></div>',
    THEME_JS + '\n<header><div class=mark><b>SYNG</b> · SETUP <a href="/">← dashboard</a><button class=themebtn onclick="cycleTheme()">theme</button></div>')

# ---- 4. Syng cloud card on the dashboard (this cell / every cell in the room) + richer fleet rows
rep('<section class=card id=fleetcard style=display:none><h2>SPEAKERS <span class=rn id=fcount></span></h2><div class=fleet id=fleet></div></section>',
    '''<section class=card id=fleetcard style=display:none><h2>SPEAKERS <span class=rn id=fcount></span></h2><div class=fleet id=fleet></div></section>

<section class=card id=cloudcard><h2>SYNG CLOUD <span class=rn id=cloudsum></span></h2>
  <div class=fleet id=cloudlist style="margin-bottom:12px"></div>
  <div class=srcs>
    <button class=srcbtn id=cbme>…</button>
    <button class=srcbtn id=cball>Block on every cell</button>
    <button class=srcbtn id=cbnone>Unblock on every cell</button>
  </div>
  <p class=hint style="font-size:12px;color:var(--muted);margin:0">Blocked = the cell cannot reach api.prod.syng.space (DNS + firewall), the room document on the cells is authoritative and the setup page owns grouping/layout. Unblocked = Syng's cloud takes over again and overwrites local changes with what it last knew. The setting survives reboots.</p>
</section>''')
rep('''     return `<div class="fcell ${c.isMe?'me':''}"><span class="fdot ${pri?'pri':(c.activeSource?'on':'')}"></span>
      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}</span>
      <span class=mt>${c.role||''} · ${c.ip||''}${c.calibrated?'':' · uncalibrated'}</span></div>`;}).join('');}''',
    '''     return `<div class="fcell ${c.isMe?'me':''}"><span class="fdot ${pri?'pri':(c.activeSource?'on':'')}"></span>
      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}</span>
      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}${c.calibrated?'':'uncalibrated · '}${c.isMe?(c.ip||''):`<a href="http://${c.ip}:8080/" style="color:var(--accent);text-decoration:none">${c.ip||''} ›</a>`}</span></div>`;}).join('');}''')
rep("initField();tickG();", '''async function loadCloud(){
  try{const d=await (await fetch('/api/cloudblock/all')).json(); const cells=d.cells||[];
    const nb=cells.filter(c=>c.blocked===true).length, nu=cells.filter(c=>c.blocked===false).length;
    $('#cloudsum').textContent=cells.length?`${nb} blocked · ${nu} open${cells.length-nb-nu?' · '+(cells.length-nb-nu)+' unknown':''}`:'';
    $('#cloudlist').innerHTML=cells.map(c=>`<div class="fcell ${c.isMe?'me':''}"><span class="fdot ${c.blocked===true?'pri':(c.blocked===false?'on':'')}"></span><span class=nm>${short(c.serial)||c.ip}${c.isMe?' · this one':''}</span>
      <span class=mt>${c.blocked===true?'cloud blocked':(c.blocked===false?'cloud open':'unknown')} · ${c.ip}</span></div>`).join('');
    const me=cells.find(c=>c.isMe); $('#cbme').textContent=me&&me.blocked?'Unblock on this cell':'Block on this cell'; $('#cbme').dataset.on=me&&me.blocked?'0':'1';
  }catch(e){}
}
$('#cbme').onclick=async()=>{const on=$('#cbme').dataset.on==='1'; if(!confirm((on?'Block':'Unblock')+' Syng\\'s cloud on this cell?'))return; await post('/api/cloudblock',{on}); setTimeout(loadCloud,2500);};
$('#cball').onclick=async()=>{if(!confirm('Block Syng\\'s cloud on every cell in the room?'))return; await post('/api/cloudblock',{on:true,scope:'group'}); setTimeout(loadCloud,3000);};
$('#cbnone').onclick=async()=>{if(!confirm('Unblock on every cell? Syng\\'s cloud will overwrite the local room document (6-cell rooms, local calibration) with what it last knew.'))return; await post('/api/cloudblock',{on:false,scope:'group'}); setTimeout(loadCloud,3000);};
loadCloud();setInterval(loadCloud,30000);
initField();tickG();''')

# ---- 5. backend: cloud status of the whole room, group fan-out for the toggle, housekeeping endpoints
rep("def lc_get(path):\n    if path.startswith(\"/api/whoami\"): return whoami()",
    hk + '''
def cloudblock_all():
    me = whoami(); cells = [{"serial": me.get("serial"), "ip": me.get("ip"), "blocked": os.path.exists(CLOUD_BLOCK_MARK), "isMe": True}]
    lock = threading.Lock(); ths = []
    def q(ip, serial):
        try: b = agent_get(ip, "/api/cloudblock", timeout=3).get("blocked")
        except Exception: b = None
        with lock: cells.append({"serial": serial, "ip": ip, "blocked": b, "isMe": False})
    for serial, c in room_doc()["cells"].items():
        if serial == me.get("serial") or not c.get("ip_address"): continue
        t = threading.Thread(target=q, args=(c["ip_address"], serial), daemon=True); t.start(); ths.append(t)
    for t in ths: t.join(4)
    return {"cells": sorted(cells, key=lambda c: (not c["isMe"], c.get("ip") or ""))}

def lc_get(path):
    if path.startswith("/api/cloudblock/all"): return cloudblock_all()
    if path.startswith("/api/housekeeping"): return housekeeping()
    if path.startswith("/api/whoami"): return whoami()''')
rep('    if path.startswith("/api/cloudblock"): return cloud_block(bool(body.get("on")))',
    '''    if path.startswith("/api/cloudblock"):
        r = cloud_block(bool(body.get("on")))
        if body.get("scope") == "group" and not body.get("_local"):
            r["group"] = {}
            for serial, c in room_doc()["cells"].items():
                if c.get("ip_address") and serial != whoami().get("serial"):
                    try: r["group"][serial] = agent_post(c["ip_address"], "/api/cloudblock", {"on": bool(body.get("on")), "_local": True}, timeout=70).get("ok")
                    except Exception as e: r["group"][serial] = str(e)
        return r
    if path.startswith("/api/housekeeping"): return housekeeping_action(body.get("action"), body.get("name"))''')

# ---- 6. setup page: housekeeping panel under Advanced
rep('''  <div class=log id=doc style="display:none"></div>
  <p class=hint>Everything here is applied''', '''  <div class=log id=doc style="display:none"></div>
  <h2 style="margin-top:16px">HOUSEKEEPING <span><button class="btn sm" id=hkrefresh>Refresh</button></span></h2>
  <div id=hk class=muted>…</div>
  <div class=rowb><button class="btn sm" id=hkvac>Vacuum journal to 32 MB</button><button class="btn sm" id=hkcores>Delete core dumps</button>
    <select id=hksvc><option value="">restart a service…</option><option>airplay2source</option><option>spotifysource</option><option>tidalsource</option><option>cellbroker</option><option>ledmanager</option><option>pycalibrator</option><option>syng-agent</option></select></div>
  <p class=hint>Everything here is applied''')
rep("async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();}",
    '''const fmtB=b=>b>=1048576?(b/1048576).toFixed(0)+' MB':b>=1024?(b/1024).toFixed(0)+' KB':b+' B';
async function loadHK(){
  try{const h=await api('/api/housekeeping');
    $('#hk').innerHTML=`<div class=row><span>/data used</span><span>${fmtB(h.data.total-h.data.free)} of ${fmtB(h.data.total)}</span></div>
      <div class=row><span>journal</span><span>${fmtB(h.journal_bytes)}</span></div>
      <div class=row><span>core dumps</span><span>${h.coredumps.length} · ${fmtB(h.coredump_bytes)}${h.coredumps.length?' · newest '+new Date(Math.max(...h.coredumps.map(c=>c.mtime))*1000).toLocaleString():''}</span></div>
      ${h.tmp.slice(0,4).map(t=>`<div class=row><span>${t.path}</span><span>${fmtB(t.bytes)}</span></div>`).join('')}
      <div class=row><span>biggest processes</span><span>${h.top.slice(0,5).map(p=>p.name+' '+fmtB(p.rss_kb*1024)+' ('+p.elapsed+')').join(' · ')}</span></div>`;
  }catch(e){$('#hk').textContent='n/a: '+e.message;}
}
$('#hkrefresh').onclick=loadHK;
$('#hkvac').onclick=async()=>{await api('/api/housekeeping',{action:'vacuum_journal'});toast('Journal vacuumed');loadHK();};
$('#hkcores').onclick=async()=>{if(!confirm('Delete all core dumps on this cell?'))return;const r=await api('/api/housekeeping',{action:'clear_coredumps'});toast('Removed '+r.removed+' file(s)');loadHK();};
$('#hksvc').onchange=async()=>{const n=$('#hksvc').value;if(!n)return;if(!confirm('Restart '+n+' on this cell?')){$('#hksvc').value='';return;}const r=await api('/api/housekeeping',{action:'restart_service',name:n});toast(n+': '+(r.out||r.error));$('#hksvc').value='';setTimeout(loadHK,2000);};
async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();}''')
s = s.replace(".row{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px}\n.row span:first-child{color:var(--muted)}",
              ".row{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px}\n.row span:first-child{color:var(--muted)}")
# the setup page has no .row style of its own: add one
rep(".muted{color:var(--muted);font-size:12px}.ok{color:var(--live)}.bad{color:var(--bad)}.warn{color:var(--warn)}",
    ".muted{color:var(--muted);font-size:12px}.ok{color:var(--live)}.bad{color:var(--bad)}.warn{color:var(--warn)}\n.row{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px}.row span:first-child{color:var(--muted)}.row span:last-child{text-align:right}")

# ---- 7. version
s = s.replace('"syng-agent (b26 rooms)"', '"syng-agent (b27)"').replace("syng-agent (b26 rooms) on", "syng-agent (b27) on").replace('AGENT_BUILD = "b26"', 'AGENT_BUILD = "b27"')
out = os.path.join(here, "syng-agent-b27.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
