#!/usr/bin/env python3
"""syng-agent-b62.py = b61 + the group stream: its network buffer, and a history of its dropouts.

1. GET  /api/netbuffer           -> this room: every member's SourceCast buffer (maxNetworkLatency_ms /
                                    defaultNetworkLatency_ms in /etc/syng/SourceCast.conf, read by syngsoundserver).
   POST /api/netbuffer {ms}      -> write it on every member, then each restarts its audio engine (jackd ->
                                    syngsoundserver, ~3 s of silence) through audio_resync, which also puts the sound
                                    mode and the field back. The first original is kept as SourceCast.conf.orig.
   GET  /api/netbuffer/local     -> this cell only.
2. GET  /api/underruns?hours=N   -> every member's stream underruns from its journal (jackd NetworkDepacketizer
                                    "FIFO underrun after ..." lines, grouped into events with their burst size).
   GET  /api/underruns/local?hours=N -> this cell only (cached 60 s).
3. The setup page gets a GROUP STREAM card: the buffer per speaker with a slider + Apply (asks first), and the
   dropouts as a timeline (24 h / 7 d, stacked by speaker) and by minute past the hour (where an hourly culprit
   stands out)."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b61.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- 1 + 2: the API ----------
rep('''def lc_get(path):''', r'''SOURCECAST_CONF = "/etc/syng/SourceCast.conf"

def netbuffer_local():
    try: d = json.loads(_read(SOURCECAST_CONF, "{}") or "{}")
    except Exception: d = {}
    me = whoami()
    return {"ip": me.get("ip"), "serial": me.get("serial"), "name": me.get("name"), "role": (me.get("role") or "").lower(),
            "ms": d.get("maxNetworkLatency_ms"), "default_ms": d.get("defaultNetworkLatency_ms"), "dualPath": d.get("dualPath")}

def netbuffer_set_local(ms, restart=True):
    ms = int(max(20, min(1000, int(ms))))
    raw = _read(SOURCECAST_CONF, "")
    d = json.loads(raw or "{}")
    if d.get("maxNetworkLatency_ms") == ms and d.get("defaultNetworkLatency_ms") == ms:
        return dict(netbuffer_local(), changed=False)
    if raw and not os.path.exists(SOURCECAST_CONF + ".orig"):
        open(SOURCECAST_CONF + ".orig", "w").write(raw)
    d["maxNetworkLatency_ms"] = ms; d["defaultNetworkLatency_ms"] = ms
    open(SOURCECAST_CONF + ".tmp", "w").write(json.dumps(d, indent=2) + "\n")
    os.replace(SOURCECAST_CONF + ".tmp", SOURCECAST_CONF)
    log("NETBUFFER %d ms written%s" % (ms, ", restarting the audio engine" if restart else ""))
    if restart:
        threading.Thread(target=audio_resync, args=("network buffer %d ms" % ms,), daemon=True).start()
    return dict(netbuffer_local(), changed=True)

def _members_call(path, timeout=8):
    """GET `path` on every room member in parallel (this cell answers locally)."""
    try: ips = room_member_ips(get_state())
    except Exception: ips = []
    me = whoami().get("ip")
    out = {}; lock = threading.Lock()
    def one(ip):
        try: r = agent_get(ip, path, timeout=timeout)
        except Exception as e: r = {"ip": ip, "error": str(e)[:80]}
        with lock: out[ip] = r
    ths = [threading.Thread(target=one, args=(ip,), daemon=True) for ip in ips if ip and ip != me]
    for t in ths: t.start()
    for t in ths: t.join(timeout + 2)
    return out

def netbuffer_room():
    cells = [netbuffer_local()] + [v for v in _members_call("/api/netbuffer/local").values()]
    vals = sorted({c.get("ms") for c in cells if c.get("ms") is not None})
    return {"cells": cells, "ms": vals[0] if len(vals) == 1 else None, "mixed": len(vals) > 1}

_UND = {}
def underruns_local(hours=168):
    """Stream underruns from the journal: one event per "FIFO underrun after N good pkts", its burst = the largest
    "K FIFO underruns" that follows within 3 s (the depacketizer doubles the count while the stream stays dry)."""
    hours = int(max(1, min(24 * 31, int(hours))))
    hit = _UND.get(hours)
    if hit and time.time() - hit[0] < 60: return hit[1]
    raw = _sh("journalctl -u jackd --since -%dh -o short-unix --no-pager -q | grep 'FIFO underrun'" % hours, 40)
    events = []
    for ln in raw.splitlines():
        p = ln.split(None, 1)
        try: t = float(p[0])
        except (ValueError, IndexError): continue
        m = re.search(r"FIFO underrun after (\d+) good pkts", ln)
        if m:
            events.append({"t": round(t, 3), "good": int(m.group(1)), "burst": 1}); continue
        m = re.search(r"(\d+) FIFO underruns", ln)
        if m and events and t - events[-1]["t"] < 3.0:
            events[-1]["burst"] = max(events[-1]["burst"], int(m.group(1)))
    me = whoami()
    res = {"ip": me.get("ip"), "serial": me.get("serial"), "name": me.get("name"), "role": (me.get("role") or "").lower(),
           "hours": hours, "now": time.time(), "events": events}
    _UND[hours] = (time.time(), res)
    return res

def underruns_room(hours=168):
    cells = [underruns_local(hours)] + list(_members_call("/api/underruns/local?hours=%d" % int(hours), timeout=45).values())
    return {"hours": int(hours), "now": time.time(), "cells": cells}

def lc_get(path):''')

rep('''    if path.startswith("/api/storm"): return {"underruns_per_s": round(STORM["last_rate"], 1), "strikes": STORM["strikes"],''',
'''    if path.startswith("/api/netbuffer/local"): return netbuffer_local()
    if path.startswith("/api/netbuffer"): return netbuffer_room()
    if path.startswith("/api/underruns"):
        q = parse_qs(urlparse(path).query); h = int((q.get("hours") or ["168"])[0])
        return underruns_local(h) if path.startswith("/api/underruns/local") else underruns_room(h)
    if path.startswith("/api/storm"): return {"underruns_per_s": round(STORM["last_rate"], 1), "strikes": STORM["strikes"],''')

rep('''def lc_post(path, body):
    if path.startswith("/api/relay"):''',
'''def lc_post(path, body):
    if path.startswith("/api/netbuffer"):
        ms = int(body.get("ms"))
        mine = netbuffer_set_local(ms, restart=body.get("restart", True) is not False)
        if body.get("_local"): return mine
        others = {}
        for ip in [i for i in room_member_ips(get_state()) if i and i != whoami().get("ip")]:
            try: others[ip] = agent_post(ip, "/api/netbuffer", {"ms": ms, "_local": True}, timeout=8)
            except Exception as e: others[ip] = {"error": str(e)[:80]}
        return {"ok": True, "ms": ms, "me": mine, "members": others}
    if path.startswith("/api/relay"):''')

# ---------- 3: the setup page ----------
rep('''<div class=card id=fwcard><h2>FIRMWARE''', '''<div class=card id=streamcard><h2>GROUP STREAM <span><button class="btn sm" id=strefresh>Refresh</button></span></h2>
  <div class=muted id=nbinfo>…</div>
  <div class=rowb style="margin-top:8px"><label class=muted style="flex:1;display:flex;gap:10px;align-items:center">Network buffer
    <input type=range id=nbms min=20 max=500 step=10 value=50 style="flex:1"><b id=nbval style="min-width:56px;text-align:right">50 ms</b></label>
    <button class="btn sm" id=nbapply disabled>Apply to the room</button></div>
  <p class=hint>How far ahead the primary streams to the other speakers (Syng's SourceCast <code>maxNetworkLatency_ms</code>, stock 50 ms).
    A gap in the Wi-Fi shorter than this is inaudible; a longer one is a dropout. More buffer = more delay before sound starts (music
    doesn't care). Applying restarts the audio engine on every speaker in the room — a few seconds of silence.</p>
  <h2 style="margin-top:14px">STREAM DROPOUTS <span><button class="btn sm" id=und24>24 h</button><button class="btn sm" id=und7>7 days</button></span></h2>
  <canvas id=undtl style="width:100%;height:150px;display:block"></canvas>
  <div class=muted style="margin:10px 0 4px;font-size:12px">By minute past the hour — an hourly culprit stands out as one tall bar</div>
  <canvas id=undmin style="width:100%;height:90px;display:block"></canvas>
  <div id=undleg class=muted style="display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:12px"></div>
  <p class=hint>Each mark is a moment the stream from the primary ran dry on that speaker (its jackd logs "FIFO underrun"); taller =
    longer. The primary itself never logs these — it is the sender. A speaker alone in its room has no stream and shows nothing.</p>
</div>

<div class=card id=fwcard><h2>FIRMWARE''')

rep('''async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();loadFw();}''',
r'''// ---- group stream: buffer + dropout history ----
let NB=null, UND=null, UNDH=24;
const UCOL=['#5ab0ff','#79de96','#e0b35a','#d98cf2','#ff7a6b','#6fe0d6','#c9c9c9','#f2d16b'];
function nbShow(){
  const v=+$('#nbms').value; $('#nbval').textContent=v+' ms';
  $('#nbapply').disabled=!NB||(!NB.mixed&&NB.ms===v);
}
async function loadNB(){
  try{NB=await api('/api/netbuffer');
    const cs=NB.cells.filter(c=>c.ms!=null);
    $('#nbinfo').innerHTML=NB.mixed?('Not the same on every speaker: '+cs.map(c=>`${c.name||short(c.serial)} ${c.ms} ms`).join(' · '))
      :`${NB.ms} ms on ${cs.length} speaker${cs.length==1?'':'s'}${cs.length&&cs[0].dualPath?' · two paths (Wi-Fi + SyFi)':''}`;
    if(NB.ms!=null){$('#nbms').value=NB.ms;} nbShow();
  }catch(e){$('#nbinfo').textContent='n/a: '+e.message;}
}
$('#nbms').oninput=nbShow;
$('#nbapply').onclick=async()=>{const v=+$('#nbms').value;
  if(!confirm(`Set the network buffer to ${v} ms on every speaker in this room?\n\nEach speaker restarts its audio engine: a few seconds of silence.`))return;
  try{await api('/api/netbuffer',{ms:v});toast('Buffer set to '+v+' ms — audio restarting');setTimeout(loadNB,6000);}catch(e){toast(e.message,true);}};
function canvasCtx(c){const r=c.getBoundingClientRect(),d=window.devicePixelRatio||1;c.width=Math.max(1,r.width*d);c.height=Math.max(1,r.height*d);
  const x=c.getContext('2d');x.scale(d,d);return [x,r.width,r.height];}
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()||'#888';}
function drawUND(){
  if(!UND)return;
  const cells=UND.cells.filter(c=>c.events);
  const now=UND.now, H=UNDH, bin=H<=24?900:3600, nb=Math.ceil(H*3600/bin), t0=now-H*3600;
  // timeline, stacked by speaker, height = sum of log2(burst)+1
  let [x,w,h]=canvasCtx($('#undtl')); x.clearRect(0,0,w,h);
  const sums=Array.from({length:nb},()=>cells.map(()=>0));
  cells.forEach((c,ci)=>c.events.forEach(e=>{if(e.t<t0)return;const b=Math.min(nb-1,Math.floor((e.t-t0)/bin));sums[b][ci]+=1+Math.log2(e.burst||1);}));
  const mx=Math.max(1,...sums.map(s=>s.reduce((a,b)=>a+b,0))), bw=w/nb, ph=h-16;
  x.strokeStyle=css('--line');x.beginPath();x.moveTo(0,ph+.5);x.lineTo(w,ph+.5);x.stroke();
  sums.forEach((s,i)=>{let y=ph;s.forEach((v,ci)=>{if(!v)return;const hh=v/mx*(ph-4);x.fillStyle=UCOL[ci%UCOL.length];x.fillRect(i*bw+.5,y-hh,Math.max(1,bw-1),hh);y-=hh;});});
  x.fillStyle=css('--muted');x.font='10px system-ui';
  const step=H<=24?6:24; for(let k=0;k<=H;k+=step){const tt=t0+k*3600,px=k/H*w;const d=new Date(tt*1000);
    x.fillText(H<=24?d.toLocaleTimeString([], {hour:'numeric'}):d.toLocaleDateString([], {weekday:'short'}),Math.min(w-30,px+2),h-3);}
  // by minute past the hour (local time)
  [x,w,h]=canvasCtx($('#undmin')); x.clearRect(0,0,w,h);
  const mins=Array.from({length:60},()=>cells.map(()=>0));
  cells.forEach((c,ci)=>c.events.forEach(e=>{if(e.t<t0)return;mins[new Date(e.t*1000).getMinutes()][ci]+=1;}));
  const mm=Math.max(1,...mins.map(s=>s.reduce((a,b)=>a+b,0))), mw=w/60, mh=h-14;
  mins.forEach((s,i)=>{let y=mh;s.forEach((v,ci)=>{if(!v)return;const hh=v/mm*(mh-4);x.fillStyle=UCOL[ci%UCOL.length];x.fillRect(i*mw+.5,y-hh,Math.max(1,mw-1),hh);y-=hh;});});
  x.fillStyle=css('--muted');x.font='10px system-ui';[0,15,30,45].forEach(m=>x.fillText(':'+String(m).padStart(2,'0'),m*mw+1,h-2));
  const top=mins.map((s,i)=>[s.reduce((a,b)=>a+b,0),i]).sort((a,b)=>b[0]-a[0])[0];
  $('#undleg').innerHTML=cells.map((c,ci)=>`<span><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${UCOL[ci%UCOL.length]};margin-right:5px"></span>${c.name||'Cell '+short(c.serial)} · ${c.events.filter(e=>e.t>=t0).length}${c.role==='primary'?' (sender)':''}</span>`).join('')
    +(top&&top[0]>=3?`<span style="flex-basis:100%">Most at <b>:${String(top[1]).padStart(2,'0')}</b> past the hour (${top[0]} of ${cells.reduce((a,c)=>a+c.events.filter(e=>e.t>=t0).length,0)})</span>`:'');
}
async function loadUND(h){UNDH=h||UNDH;$('#und24').classList.toggle('pri',UNDH===24);$('#und7').classList.toggle('pri',UNDH!==24);
  try{UND=await api('/api/underruns?hours='+UNDH);drawUND();}catch(e){$('#undleg').textContent='n/a: '+e.message;}}
$('#und24').onclick=()=>loadUND(24); $('#und7').onclick=()=>loadUND(168);
$('#strefresh').onclick=()=>{loadNB();loadUND();};
window.addEventListener('resize',()=>drawUND());
async function refreshAll(){await loadMe();loadWifi();loadRoom();loadDiscover();loadHK();loadRing();loadFw();loadNB();loadUND(24);}''')

s = s.replace('"syng-agent (b61)"', '"syng-agent (b62)"').replace("syng-agent (b61) on", "syng-agent (b62) on").replace('AGENT_BUILD = "b61"', 'AGENT_BUILD = "b62"')
out = os.path.join(here, "syng-agent-b62.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
