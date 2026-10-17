#!/usr/bin/env python3
"""syng-agent-b42.py = b41 +
room map: a page that was open with an older layout committed its whole stale model on the first gesture and
overwrote positions changed elsewhere (that is how the new cell went back to 0,0); now a commit carries only what
this page changed (the circle / the dragged speakers / the listener), the page refreshes from the room every 5 s
when idle, trackpad momentum can no longer zoom the circle down to nothing in one flick (per-event clamp, the circle
never shrinks past the point where a speaker leaves the view, settle + save 300 ms after the last tick).
discover: one entry per cell — a cell answers on every address it has (Wi-Fi, wired, both USB gadget links),
which showed as "9 speakers" with phantom "stock firmware" rows for 10.86.x."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b41.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# ---- partial commits ----
rep("""function layoutBody(){const b={cells:{},reference_circle:circleBody(),listener_position:{x_m:r2(L.lis.x),y_m:r2(L.lis.y)}};
  L.cells.forEach(c=>b.cells[c.serial]={x:c.x,y:c.y,z:c.z,rotation:c.rot,is_upside_down:c.ud,height_layer:c.hl}); return b;}""",
"""const DIRTY={rc:false,lis:false,cells:new Set()};   // only what this page changed goes to the room (another page or the agent may have moved the rest)
function layoutBody(){const b={cells:{}}; if(DIRTY.rc)b.reference_circle=circleBody(); if(DIRTY.lis)b.listener_position={x_m:r2(L.lis.x),y_m:r2(L.lis.y)};
  L.cells.forEach(c=>{if(DIRTY.cells.has(c.serial))b.cells[c.serial]={x:c.x,y:c.y,z:c.z,rotation:c.rot,is_upside_down:c.ud,height_layer:c.hl};}); return b;}
function clearDirty(){DIRTY.rc=false;DIRTY.lis=false;DIRTY.cells.clear();}""")
rep("""  try{const r=await api('/api/room/layout',layoutBody(),primaryIp); dirty=false; $('#save').disabled=true;""",
    """  try{const body=layoutBody(); const r=await api('/api/room/layout',body,primaryIp); dirty=false; clearDirty(); $('#save').disabled=true;""")
rep("""function changed(circle){dirty=true; $('#save').disabled=false; $('#savestate').textContent='unsaved'; draw(); renderTable(false); if(circle)schedulePreview();}""",
    """function changed(circle){dirty=true; if(circle)DIRTY.rc=true; $('#save').disabled=false; $('#savestate').textContent='unsaved'; draw(); renderTable(false); if(circle)schedulePreview();}""")
rep("""    const [x,y]=toP(nx,ny); grab.c.x=r2(x); grab.c.y=r2(y);}""",
    """    const [x,y]=toP(nx,ny); grab.c.x=r2(x); grab.c.y=r2(y); DIRTY.cells.add(grab.c.serial);}""")
rep("""  else if(grab.t==='rot'){const [cx,cy]=toPx(grab.c.x,grab.c.y); grab.c.rot=Math.round((((deg(Math.atan2(-(py-cy),px-cx))+L.rc.rot)%360)+360)%360*10)/10;}
  else if(grab.t==='lis'){const [x,y]=toP(n[0],n[1]); L.lis.x=r2(x); L.lis.y=r2(y);}""",
    """  else if(grab.t==='rot'){const [cx,cy]=toPx(grab.c.x,grab.c.y); grab.c.rot=Math.round((((deg(Math.atan2(-(py-cy),px-cx))+L.rc.rot)%360)+360)%360*10)/10; DIRTY.cells.add(grab.c.serial);}
  else if(grab.t==='lis'){const [x,y]=toP(n[0],n[1]); L.lis.x=r2(x); L.lis.y=r2(y); DIRTY.lis=true;}""")
rep("""el.onchange=()=>{const c=L.cells[+el.closest('tr').dataset.i]; const f=el.dataset.f; c[f]=f==='ud'?el.checked:+el.value; dirty=true;$('#save').disabled=false; draw();});""",
    """el.onchange=()=>{const c=L.cells[+el.closest('tr').dataset.i]; const f=el.dataset.f; c[f]=f==='ud'?el.checked:+el.value; DIRTY.cells.add(c.serial); dirty=true;$('#save').disabled=false; $('#savestate').textContent='unsaved'; draw();});""")
# undo restores everything it touched -> mark all
rep("""function undo(){const h=hist.pop(); if(!h)return; const o=JSON.parse(h.s); o.cells.forEach((p,i)=>{if(L.cells[i])Object.assign(L.cells[i],p);}); L.rc=o.rc; L.lis=o.lis;""",
    """function undo(){const h=hist.pop(); if(!h)return; const o=JSON.parse(h.s); o.cells.forEach((p,i)=>{if(L.cells[i]){Object.assign(L.cells[i],p);DIRTY.cells.add(L.cells[i].serial);}}); L.rc=o.rc; L.lis=o.lis; DIRTY.rc=true; DIRTY.lis=true;""")

# ---- zoom: never past the point where a speaker leaves the view; momentum-proof ----
rep("""function scaleAt(px,py,f){const n=pxToN(px,py); const P=toP(n[0],n[1]); L.rc.r=L.rc.r/f; clampCircle(L.rc); const [dx,dy]=rot2(n[0]*L.rc.r,n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=P[0]-dx; L.rc.y=P[1]-dy;}""",
    """function scaleAt(px,py,f){const n=pxToN(px,py); const P=toP(n[0],n[1]); const r0=L.rc.r; L.rc.r=L.rc.r/f; clampCircle(L.rc); const [dx,dy]=rot2(n[0]*L.rc.r,n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=P[0]-dx; L.rc.y=P[1]-dy;
  if(f>1&&!allInView()){L.rc.r=r0; const [dx2,dy2]=rot2(n[0]*r0,n[1]*r0,rad(L.rc.rot)); L.rc.x=P[0]-dx2; L.rc.y=P[1]-dy2;}}   // zooming in stops when a speaker would leave the view
function allInView(){return L.cells.every(c=>{const [nx,ny]=toN(c.x,c.y); return Math.hypot(nx,ny)<=1.16;});}""")
rep("""cv.addEventListener('wheel',e=>{e.preventDefault(); if(!L||inGesture||gestureBase)return; const [px,py]=evpos(e); pushHist();
  if(e.shiftKey){L.rc.rot+=(Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY)*0.08; clampCircle(L.rc);}          // ⇧-scroll: twist
  else {const k=e.ctrlKey?0.012:0.0016; scaleAt(px,py,Math.exp(-Math.max(-120,Math.min(120,e.deltaY))*k));}              // scroll / trackpad pinch: grow or shrink about the cursor
  changed(true); scheduleCommit();},{passive:false});""",
    """let wheelT=null;
cv.addEventListener('wheel',e=>{e.preventDefault(); if(!L||inGesture||gestureBase)return; const [px,py]=evpos(e); pushHist();
  if(e.shiftKey){L.rc.rot+=Math.max(-6,Math.min(6,(Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY)*0.08)); clampCircle(L.rc);}          // ⇧-scroll: twist
  else {const k=e.ctrlKey?0.012:0.0016; scaleAt(px,py,Math.max(0.94,Math.min(1.06,Math.exp(-Math.max(-120,Math.min(120,e.deltaY))*k))));}   // scroll / trackpad pinch: ≤6 % per tick, so momentum can't run away
  changed(true); clearTimeout(wheelT); wheelT=setTimeout(()=>{settle(); scheduleCommit();},300);},{passive:false});""")
rep("""  const [dx,dy]=rot2(gestureBase.n[0]*L.rc.r,gestureBase.n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=gestureBase.P[0]-dx; L.rc.y=gestureBase.P[1]-dy; changed(true);});""",
    """  const [dx,dy]=rot2(gestureBase.n[0]*L.rc.r,gestureBase.n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=gestureBase.P[0]-dx; L.rc.y=gestureBase.P[1]-dy; settle(); changed(true);});""")
rep("""  L.rc=clampCircle(rc); changed(true);
}""", """  L.rc=clampCircle(rc); settle(); changed(true);
}""")
# ---- idle refresh from the room ----
rep("""async function loadRoom(){
  try{ROOM=await api('/api/room');}catch(e){ROOM=null;}""",
    """setInterval(()=>{if(ROOM&&!dirty&&!inGesture&&!grab&&!ptrs.size&&document.visibilityState==='visible')loadRoom();},5000);   // pick up what other pages / Soundcheck / the agent changed
async function loadRoom(){
  try{ROOM=await api('/api/room');}catch(e){ROOM=null;}""")

# ---- discover: one row per cell ----
rep("""    # a host with port 8080 open that is neither an agent nor a cell on mDNS is some other device
    for ip in list(results):
        if not results[ip].get("agent") and ip not in md and not results[ip].get("isMe"): results.pop(ip)""",
    """    # a host with port 8080 open that is neither an agent nor a cell on mDNS is some other device
    for ip in list(results):
        if not results[ip].get("agent") and ip not in md and not results[ip].get("isMe"): results.pop(ip)
    # one row per cell: a cell answers on every address it has (Wi-Fi + wired + both USB gadget links), prefer
    # ourselves, then an entry with an agent, then a LAN address over a 10.86.x setup link
    def rank(c): return (1 if c.get("isMe") else 0, 1 if c.get("agent") else 0, 0 if str(c.get("ip") or "").startswith("10.86.") else 1)
    by = {}
    for ip, c in results.items():
        key = c.get("serial") or ("ip:" + ip)
        if key not in by or rank(c) > rank(by[key]): by[key] = c
    results = {(c.get("ip") or k): c for k, c in by.items()}""")

s = s.replace('"syng-agent (b41)"', '"syng-agent (b42)"').replace("syng-agent (b41) on", "syng-agent (b42) on").replace('AGENT_BUILD = "b41"', 'AGENT_BUILD = "b42"')
out = os.path.join(here, "syng-agent-b42.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
