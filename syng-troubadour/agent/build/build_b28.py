#!/usr/bin/env python3
"""syng-agent-b28.py = b27 + Ping (the app's "ta ta ta" + LED identify) for this cell, any cell, or the whole room."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b27.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

PING_PY = '''
# ---------- ping: what the app's "Ping" does — the three-note identify sound + the IdentifyCell LED vision ----------
PING_WAV = "/usr/lib/syng/sounds/multi-cell-orientation.wav"
def ui_sound_level():
    try: return max(0.05, min(0.6, float(json.loads(_read("/etc/syng/apps/celldirector/options.conf", "{}")).get("ui_sound_vol_pct", 25)) / 100.0))
    except Exception: return 0.25

def ping_local(vol=None):
    lvl = ui_sound_level() if vol is None else max(0.05, min(0.6, float(vol)))
    out = {"level": lvl}
    try:
        with DBUS_LOCK:
            dbus.Interface(BUS.get_object("com.syng.SAPlayer", "/com/syng/SAPlayer"), "com.syng.SAPlayer").PlayOneShot(PING_WAV, lvl)
        out["sound"] = True
    except Exception as e: out["sound"] = str(e)
    try:
        with DBUS_LOCK:
            dbus.Interface(BUS.get_object("com.syng.CellServer", "/com/syng/localcell"), "com.syng.Cell.CoreServices").IdentifyCell()
        out["led"] = True
    except Exception as e: out["led"] = str(e)
    return out

def op_ping(serial=None, vol=None, everyone=False):
    me = whoami()
    if everyone:
        res = {}
        for sn, c in room_doc()["cells"].items():
            if sn == me.get("serial"): res[sn] = ping_local(vol)
            else:
                try: res[sn] = agent_post(c.get("ip_address"), "/api/ping", {"vol": vol}, timeout=8)
                except Exception as e: res[sn] = str(e)
        return {"ok": True, "cells": res}
    if not serial or serial == me.get("serial"): return dict(ping_local(vol), ok=True, serial=me.get("serial"))
    c = room_doc()["cells"].get(serial)
    ip = c.get("ip_address") if c else None
    if not ip:   # not in my room: find it on the LAN
        hit = next((x for x in discover(timeout=0.8)["cells"] if x.get("serial") == serial and x.get("agent")), None)
        ip = hit and hit.get("ip")
    if not ip: raise RuntimeError("no agent found for %s" % serial)
    return dict(agent_post(ip, "/api/ping", {"vol": vol}, timeout=8), relayed=ip)
'''
rep("def lc_get(path):\n    if path.startswith(\"/api/cloudblock/all\"): return cloudblock_all()", PING_PY + "\ndef lc_get(path):\n    if path.startswith(\"/api/cloudblock/all\"): return cloudblock_all()")
rep('    if path.startswith("/api/housekeeping"): return housekeeping_action(body.get("action"), body.get("name"))',
    '    if path.startswith("/api/housekeeping"): return housekeeping_action(body.get("action"), body.get("name"))\n'
    '    if path.startswith("/api/ping"): return op_ping(body.get("serial"), body.get("vol"), bool(body.get("all")))')

# ---- dashboard: Ping in the hero, per fleet row, Ping all
rep('<section class=hero><div class=orb id=orb></div><div class=id><h1 id=host>—</h1><div class=sub id=sub></div><div class=badges id=badges></div></div></section>',
    '<section class=hero><div class=orb id=orb></div><div class=id><h1 id=host>—</h1><div class=sub id=sub></div><div class=badges id=badges></div></div>'
    '<button class=srcbtn id=pingme title="Play the identify sound + LED on this speaker" style="flex:0 0 auto;margin-left:auto;padding:10px 16px">Ping</button></section>')
rep('<section class=card id=fleetcard style=display:none><h2>SPEAKERS <span class=rn id=fcount></span></h2><div class=fleet id=fleet></div></section>',
    '<section class=card id=fleetcard style=display:none><h2>SPEAKERS <span class=rn id=fcount></span><button class="srcbtn" id=pingall style="float:right;flex:none;min-width:0;padding:4px 10px;font-size:12px">Ping all</button></h2><div class=fleet id=fleet></div></section>')
rep('''      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}${c.calibrated?'':'uncalibrated · '}${c.isMe?(c.ip||''):`<a href="http://${c.ip}:8080/" style="color:var(--accent);text-decoration:none">${c.ip||''} ›</a>`}</span></div>`;}).join('');}''',
    '''      <span class=mt>${c.role||''} · ${c.activeSource?c.activeSource+' · ':''}${c.calibrated?'':'uncalibrated · '}${c.isMe?(c.ip||''):`<a href="http://${c.ip}:8080/" style="color:var(--accent);text-decoration:none">${c.ip||''} ›</a>`}
      <button class=srcbtn data-ping="${c.serial}" style="flex:none;min-width:0;padding:3px 9px;font-size:11px;margin-left:8px">ping</button></span></div>`;}).join('');
    $('#fleet').querySelectorAll('[data-ping]').forEach(b=>b.onclick=e=>{e.preventDefault();b.disabled=true;post('/api/ping',{serial:b.dataset.ping}).finally(()=>setTimeout(()=>b.disabled=false,3000));});}''')
rep("loadCloud();setInterval(loadCloud,30000);",
    "loadCloud();setInterval(loadCloud,30000);\n$('#pingme').onclick=()=>{const b=$('#pingme');b.disabled=true;post('/api/ping',{}).finally(()=>setTimeout(()=>b.disabled=false,3000));};\n$('#pingall').onclick=()=>{const b=$('#pingall');b.disabled=true;post('/api/ping',{all:true}).finally(()=>setTimeout(()=>b.disabled=false,4000));};")

# ---- setup page: ping per cell in the layout table and in the discovery list
rep('''    <td>${L.cells.length>1?`<button class="btn sm danger" data-rm=${i}>remove</button>`:''}</td></tr>`).join('');''',
    '''    <td style="white-space:nowrap"><button class="btn sm" data-ping="${c.serial}">ping</button> ${L.cells.length>1?`<button class="btn sm danger" data-rm=${i}>remove</button>`:''}</td></tr>`).join('');
    $('#cells').querySelectorAll('[data-ping]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/ping',{serial:b.dataset.ping});}catch(e){toast('Ping: '+e.message,true);}setTimeout(()=>b.disabled=false,3000);});''')
rep('''       <span class=k>${c.role||''}${c.calibrated?' · calibrated':''}</span>${c.isMe||!c.agent?'':`<a class="btn sm" href="http://${c.ip}:8080/setup">open</a>`}</div>`).join('');''',
    '''       <span class=k>${c.role||''}${c.calibrated?' · calibrated':''}</span>${c.agent?`<button class="btn sm" data-ping="${c.serial}">ping</button>`:''}${c.isMe||!c.agent?'':`<a class="btn sm" href="http://${c.ip}:8080/setup">open</a>`}</div>`).join('');''')
rep('''  $('#rooms').querySelectorAll('[data-join]').forEach(b=>b.onclick=async()=>{''',
    '''  $('#rooms').querySelectorAll('[data-ping]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/ping',{serial:b.dataset.ping});}catch(e){toast('Ping: '+e.message,true);}setTimeout(()=>b.disabled=false,3000);});
  $('#rooms').querySelectorAll('[data-join]').forEach(b=>b.onclick=async()=>{''')

s = s.replace('"syng-agent (b27)"', '"syng-agent (b28)"').replace("syng-agent (b27) on", "syng-agent (b28) on").replace('AGENT_BUILD = "b27"', 'AGENT_BUILD = "b28"')
out = os.path.join(here, "syng-agent-b28.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
