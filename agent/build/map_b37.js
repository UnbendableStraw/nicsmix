// ---------- layout map: the Syng app's cell-mapping model ----------
// The sound-field reference circle is the fixed frame (drawn at 0.85 of the view, front of the room up) and the speakers
// sit at their normalised positions n = R(-rot)·(p - c)/r. Dragging the room, scrolling/pinching and twisting change the
// circle (c, r, rot) so that the physical points under the pointers stay under them — the same solve the app's
// RoomMappingGestureRecognizer does with one or two fingers. Changes preview live on the speakers (cellbroker
// reference_circle override, like the app's ReferenceCircleUpdater) and are committed when the gesture ends.
const cv=$('#map'), ctx=cv.getContext('2d');
let L=null, grab=null, hist=[], ptrs=new Map(), hiCell=null, snapInfo=null, gestureBase=null, inGesture=false, rafPending=false, hoverT=null;
const DPR=()=>Math.min(2,window.devicePixelRatio||1);
const S=()=>0.85*Math.min(cv.width,cv.height)/2;            // reference-circle radius in px
const rad=d=>d*Math.PI/180, deg=r=>r*180/Math.PI, r2=v=>Math.round(v*100)/100;
function rot2(x,y,a){const c=Math.cos(a),s=Math.sin(a);return [c*x-s*y,s*x+c*y];}
function toN(x,y,rc){rc=rc||L.rc; const [dx,dy]=rot2(x-rc.x,y-rc.y,-rad(rc.rot)); return [dx/rc.r,dy/rc.r];}       // physical m -> circle units
function toP(nx,ny,rc){rc=rc||L.rc; const [dx,dy]=rot2(nx*rc.r,ny*rc.r,rad(rc.rot)); return [rc.x+dx,rc.y+dy];}  // circle units -> physical m
const nToPx=(nx,ny)=>[cv.width/2+nx*S(), cv.height/2-ny*S()];
const pxToN=(px,py)=>[(px-cv.width/2)/S(), -(py-cv.height/2)/S()];
const toPx=(x,y)=>{const [nx,ny]=toN(x,y);return nToPx(nx,ny);};
function fromRoom(){ // ROOM -> editable layout model
  const rs=ROOM.room_state||{}; const rc=rs.reference_circle||{x_m:0,y_m:0,radius_m:1,rotation_deg:0}; const lp=rs.listener_position||{x_m:0,y_m:0};
  L={cells:ROOM.cells.map(c=>({serial:c.serial,ip:c.ip,role:c.role,x:+(c.position.x||0),y:+(c.position.y||0),z:+(c.position.z||1.5),rot:+(c.position.rotation||0),
      ud:!!c.is_upside_down,hl:+(c.height_layer||0),cal:!!c.calibrated,isMe:c.isMe,online:c.online,walls:c.nearby_walls||[]})),
     rc:{x:+rc.x_m,y:+rc.y_m,r:Math.max(0.4,+rc.radius_m||1),rot:+rc.rotation_deg||0}, lis:{x:+lp.x_m,y:+lp.y_m}};
  dirty=false; $('#save').disabled=true;
}
function sizeCanvas(){const w=cv.clientWidth||600; const dpr=DPR(); cv.width=Math.round(w*dpr); cv.height=Math.round(Math.min(560,Math.max(300,w*0.78))*dpr); cv.style.height=(cv.height/dpr)+'px'; draw();}
function clampCircle(rc){rc.r=Math.max(0.4,Math.min(12,rc.r)); rc.rot=((rc.rot%360)+360)%360; return rc;}
function circleBody(){return {x_m:r2(L.rc.x),y_m:r2(L.rc.y),radius_m:r2(L.rc.r),rotation_deg:Math.round(L.rc.rot*10)/10};}
function layoutBody(){const b={cells:{},reference_circle:circleBody(),listener_position:{x_m:r2(L.lis.x),y_m:r2(L.lis.y)}};
  L.cells.forEach(c=>b.cells[c.serial]={x:c.x,y:c.y,z:c.z,rotation:c.rot,is_upside_down:c.ud,height_layer:c.hl}); return b;}
// --- history (undo) ---
function pushHist(){if(!L)return; const now=performance.now(); if(hist.length&&now-hist[hist.length-1].t<800)return;
  hist.push({t:now,s:JSON.stringify({cells:L.cells.map(c=>({x:c.x,y:c.y,rot:c.rot})),rc:L.rc,lis:L.lis})}); if(hist.length>40)hist.shift(); $('#undo').disabled=false;}
function undo(){const h=hist.pop(); if(!h)return; const o=JSON.parse(h.s); o.cells.forEach((p,i)=>{if(L.cells[i])Object.assign(L.cells[i],p);}); L.rc=o.rc; L.lis=o.lis; $('#undo').disabled=!hist.length; changed(true); scheduleCommit();}
// --- live preview + commit ---
let pvT=0,pvPending=false,cmT=null;
function schedulePreview(){const now=performance.now(); if(now-pvT>90){pvT=now;sendPreview();} else if(!pvPending){pvPending=true;setTimeout(()=>{pvPending=false;pvT=performance.now();sendPreview();},90-(now-pvT));}}
function sendPreview(){if(!L)return; api('/api/room/preview',{reference_circle:circleBody()},primaryIp).catch(()=>{});}
function scheduleCommit(){clearTimeout(cmT); cmT=setTimeout(commit,500);}
async function commit(){if(!L||!dirty)return; clearTimeout(cmT);
  try{const r=await api('/api/room/layout',layoutBody(),primaryIp); dirty=false; $('#save').disabled=true; const n=Object.values(r.fanout||{}).filter(v=>v===true).length; $('#savestate').textContent='saved · '+n+' speaker'+(n===1?'':'s')+' updated';}
  catch(e){toast('Save failed: '+e.message,true);}}
function changed(circle){dirty=true; $('#save').disabled=false; $('#savestate').textContent='unsaved'; draw(); renderTable(false); if(circle)schedulePreview();}
// --- the room gesture solve: keep the physical anchor under each pointer ---
function solveRoom(){
  const a=[...ptrs.values()]; if(!a.length)return; const rc={...L.rc};
  if(a.length===1){const p=a[0];
    if(p.alt){const a0=Math.atan2(p.n0[1],p.n0[0]), a1=Math.atan2(p.n[1],p.n[0]); rc.rot=p.rot0-deg(a1-a0);}       // ⌥-drag: twist about the centre
    else {const [dx,dy]=rot2(p.n[0]*rc.r,p.n[1]*rc.r,rad(rc.rot)); rc.x=p.P[0]-dx; rc.y=p.P[1]-dy;}                 // drag: pan
  } else {const [p,q]=a; const dP=[q.P[0]-p.P[0],q.P[1]-p.P[1]], dN=[q.n[0]-p.n[0],q.n[1]-p.n[1]]; const lN=Math.hypot(dN[0],dN[1]); if(lN<1e-4)return;
    rc.r=Math.hypot(dP[0],dP[1])/lN; rc.rot=deg(Math.atan2(dP[1],dP[0])-Math.atan2(dN[1],dN[0])); clampCircle(rc);        // two fingers: pan + pinch + twist
    const [dx,dy]=rot2(p.n[0]*rc.r,p.n[1]*rc.r,rad(rc.rot)); rc.x=p.P[0]-dx; rc.y=p.P[1]-dy;}
  L.rc=clampCircle(rc); changed(true);
}
function settle(){ // after a gesture: if a speaker left the view, grow the circle until it is back (the app rubber-bands the same way)
  let m=0; L.cells.forEach(c=>{const [nx,ny]=toN(c.x,c.y); m=Math.max(m,Math.hypot(nx,ny));});
  if(m>1.16){L.rc.r=Math.min(12,L.rc.r*m/1.16); changed(true);}
}
function scaleAt(px,py,f){const n=pxToN(px,py); const P=toP(n[0],n[1]); L.rc.r=L.rc.r/f; clampCircle(L.rc); const [dx,dy]=rot2(n[0]*L.rc.r,n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=P[0]-dx; L.rc.y=P[1]-dy;}
function rotateBy(d){pushHist(); const from=L.rc.rot, t0=performance.now(); const step=()=>{const k=Math.min(1,(performance.now()-t0)/260), e=1-Math.pow(1-k,3); L.rc.rot=from+d*e; clampCircle(L.rc); changed(true); if(k<1)requestAnimationFrame(step); else scheduleCommit();}; step();}
// --- drawing ---
function draw(){
  if(!L)return; const W=cv.width,H=cv.height,dpr=DPR(),s=S(); ctx.clearRect(0,0,W,H);
  const css=getComputedStyle(document.documentElement); const C={muted:css.getPropertyValue('--muted').trim(),text:css.getPropertyValue('--text').trim(),acc:css.getPropertyValue('--accent').trim(),live:css.getPropertyValue('--live').trim(),warn:css.getPropertyValue('--warn').trim(),line:css.getPropertyValue('--line').trim()};
  const cx=W/2, cy=H/2;
  // metre rings around the circle centre (physical scale), faint
  ctx.lineWidth=1; ctx.strokeStyle=C.line; ctx.globalAlpha=.55;
  for(let k=0.5;k/L.rc.r*s<Math.hypot(W,H)/2;k+=0.5){ctx.beginPath();ctx.arc(cx,cy,k/L.rc.r*s,0,Math.PI*2);ctx.globalAlpha=(k%1===0)?.55:.25;ctx.stroke();}
  ctx.globalAlpha=1;
  // the reference circle (fixed) + front marker
  ctx.setLineDash([6*dpr,5*dpr]); ctx.strokeStyle=C.acc; ctx.lineWidth=1.5*dpr; ctx.beginPath(); ctx.arc(cx,cy,s,0,Math.PI*2); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle=C.acc; ctx.beginPath(); ctx.moveTo(cx,cy-s-9*dpr);ctx.lineTo(cx+6*dpr,cy-s+1*dpr);ctx.lineTo(cx-6*dpr,cy-s+1*dpr);ctx.closePath();ctx.fill();
  ctx.globalAlpha=.6; ctx.beginPath(); ctx.arc(cx,cy,3*dpr,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=1;
  ctx.fillStyle=C.muted; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='center'; ctx.fillText('front',cx,cy-s-14*dpr);
  // listener
  const [lx,ly]=toPx(L.lis.x,L.lis.y); ctx.strokeStyle=C.text; ctx.lineWidth=1.5*dpr; ctx.beginPath(); ctx.arc(lx,ly,9*dpr,0,Math.PI*2); ctx.stroke(); ctx.beginPath(); ctx.arc(lx,ly,3*dpr,0,Math.PI*2); ctx.fillStyle=C.text; ctx.fill();
  // cells
  const now=performance.now();
  L.cells.forEach(c=>{
    const [px,py]=toPx(c.x,c.y), R=14*dpr, sa=rad(c.rot-L.rc.rot); // screen angle of the speaker's front
    (c.walls||[]).forEach(w=>{ if(!w.close_to_wall&&w.likelihood<0.5)return; const a=sa+rad(w.angle_deg||0); const d=(w.distance_m||0.5)/L.rc.r*s; const wx=px+Math.cos(a)*d, wy=py-Math.sin(a)*d;
      ctx.strokeStyle=C.warn; ctx.globalAlpha=Math.min(1,w.likelihood||0.5); ctx.lineWidth=3*dpr; ctx.beginPath(); ctx.moveTo(wx+Math.cos(a+Math.PI/2)*16*dpr,wy-Math.sin(a+Math.PI/2)*16*dpr); ctx.lineTo(wx-Math.cos(a+Math.PI/2)*16*dpr,wy+Math.sin(a+Math.PI/2)*16*dpr); ctx.stroke(); ctx.globalAlpha=1; });
    if(hiCell===c.serial){const k=(now/1200)%1; ctx.strokeStyle=C.acc; ctx.globalAlpha=1-k; ctx.lineWidth=2*dpr; ctx.beginPath(); ctx.arc(px,py,R+6*dpr+k*22*dpr,0,Math.PI*2); ctx.stroke(); ctx.globalAlpha=1;}
    ctx.fillStyle=c.isMe?C.acc:(c.online?C.live:C.muted); ctx.globalAlpha=c.cal?1:.55; ctx.beginPath(); ctx.arc(px,py,R,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=1;
    if(grab&&grab.c===c){ctx.strokeStyle=C.text;ctx.lineWidth=2*dpr;ctx.stroke();}
    ctx.strokeStyle=C.text; ctx.lineWidth=2.5*dpr; ctx.beginPath(); ctx.moveTo(px+Math.cos(sa)*R*0.6,py-Math.sin(sa)*R*0.6); ctx.lineTo(px+Math.cos(sa)*R*1.9,py-Math.sin(sa)*R*1.9); ctx.stroke();
    ctx.beginPath(); ctx.arc(px+Math.cos(sa)*R*1.9,py-Math.sin(sa)*R*1.9,4*dpr,0,Math.PI*2); ctx.fillStyle=C.text; ctx.fill();
    ctx.fillStyle=C.text; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='center'; ctx.fillText(short(c.serial)+(c.role==='primary'?' ★':''),px,py+R+12*dpr);
    if(c.ud){ctx.fillStyle=C.muted; ctx.fillText('⤒ ceiling',px,py+R+24*dpr);}
    if(grab&&grab.c===c&&snapInfo){ctx.fillStyle=C.acc; ctx.fillText(snapInfo==='centre'?'⌖ centre':'▭ wall',px,py-R-8*dpr);}
  });
  // scale bar + circle readout
  let bar=1; while(bar/L.rc.r*s>W*0.4)bar/=2; const bl=bar/L.rc.r*s;
  ctx.strokeStyle=C.muted; ctx.lineWidth=1.5*dpr; ctx.beginPath(); ctx.moveTo(10*dpr,H-10*dpr); ctx.lineTo(10*dpr+bl,H-10*dpr); ctx.stroke();
  ctx.fillStyle=C.muted; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='left'; ctx.fillText(bar+' m',10*dpr,H-15*dpr);
  ctx.textAlign='right'; ctx.fillText('circle r '+L.rc.r.toFixed(2)+' m · '+Math.round(L.rc.rot)+'°',W-10*dpr,H-10*dpr);
  if(hiCell&&!rafPending){rafPending=true;requestAnimationFrame(()=>{rafPending=false;draw();});}
}
function pick(px,py){
  const dpr=DPR();
  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); const a=rad(c.rot-L.rc.rot); const tx=cx+Math.cos(a)*14*dpr*1.9, ty=cy-Math.sin(a)*14*dpr*1.9;
    if(Math.hypot(px-tx,py-ty)<10*dpr) return {t:'rot',c}; }
  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); if(Math.hypot(px-cx,py-cy)<16*dpr) return {t:'cell',c};}
  const [lx,ly]=toPx(L.lis.x,L.lis.y); if(Math.hypot(px-lx,py-ly)<12*dpr) return {t:'lis'};
  return {t:'room'};
}
function evpos(e){const r=cv.getBoundingClientRect(); const dpr=cv.width/r.width; return [(e.clientX-r.left)*dpr,(e.clientY-r.top)*dpr];}
cv.addEventListener('pointerdown',e=>{if(!L)return; try{cv.setPointerCapture(e.pointerId);}catch(_){} cv.focus(); const [px,py]=evpos(e); const n=pxToN(px,py);
  if(ptrs.size===0&&!grab){grab=pick(px,py); pushHist();}
  else if(grab&&grab.t!=='room') return;                                  // a second finger during a speaker drag is ignored
  else grab={t:'room'};
  if(grab.t==='room'){ptrs.set(e.pointerId,{n,n0:n,P:toP(n[0],n[1]),alt:e.altKey,rot0:L.rc.rot}); inGesture=true; cv.style.cursor='grabbing';}
  else grab.id=e.pointerId;
  draw();});
cv.addEventListener('pointermove',e=>{if(!L)return; const [px,py]=evpos(e); const n=pxToN(px,py);
  if(ptrs.has(e.pointerId)){ptrs.get(e.pointerId).n=n; solveRoom(); return;}
  if(!grab||grab.id!==e.pointerId){const h=pick(px,py); cv.style.cursor=h.t==='room'?'grab':(h.t==='rot'?'crosshair':'move'); return;}
  if(grab.t==='cell'){let [nx,ny]=n; snapInfo=null; if(!e.shiftKey){const d=Math.hypot(nx,ny); if(d<0.09){nx=0;ny=0;snapInfo='centre';} else if(Math.abs(d-1)<0.07){nx/=d;ny/=d;snapInfo='wall';}}
    const [x,y]=toP(nx,ny); grab.c.x=r2(x); grab.c.y=r2(y);}
  else if(grab.t==='rot'){const [cx,cy]=toPx(grab.c.x,grab.c.y); grab.c.rot=Math.round((((deg(Math.atan2(-(py-cy),px-cx))+L.rc.rot)%360)+360)%360*10)/10;}
  else if(grab.t==='lis'){const [x,y]=toP(n[0],n[1]); L.lis.x=r2(x); L.lis.y=r2(y);}
  changed(false);});
const up=e=>{if(!L)return;
  if(ptrs.has(e.pointerId)){ptrs.delete(e.pointerId); ptrs.forEach(p=>{p.n0=p.n;p.P=toP(p.n[0],p.n[1]);p.rot0=L.rc.rot;}); if(!ptrs.size){inGesture=false; grab=null; settle(); scheduleCommit(); cv.style.cursor='grab';}}
  else if(grab&&grab.id===e.pointerId){grab=null; snapInfo=null; scheduleCommit();}
  draw();};
cv.addEventListener('pointerup',up); cv.addEventListener('pointercancel',up);
cv.addEventListener('wheel',e=>{e.preventDefault(); if(!L||inGesture||gestureBase)return; const [px,py]=evpos(e); pushHist();
  if(e.shiftKey){L.rc.rot+=(Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY)*0.08; clampCircle(L.rc);}          // ⇧-scroll: twist
  else {const k=e.ctrlKey?0.012:0.0016; scaleAt(px,py,Math.exp(-Math.max(-120,Math.min(120,e.deltaY))*k));}              // scroll / trackpad pinch: grow or shrink about the cursor
  changed(true); scheduleCommit();},{passive:false});
// Safari trackpads: real pinch + twist, exactly the app's two-finger gesture
cv.addEventListener('gesturestart',e=>{e.preventDefault(); if(!L)return; const [px,py]=evpos(e); const n=pxToN(px,py); gestureBase={r:L.rc.r,rot:L.rc.rot,n,P:toP(n[0],n[1])}; pushHist();});
cv.addEventListener('gesturechange',e=>{e.preventDefault(); if(!gestureBase||!L)return; L.rc.r=gestureBase.r/(e.scale||1); L.rc.rot=gestureBase.rot+(e.rotation||0); clampCircle(L.rc);
  const [dx,dy]=rot2(gestureBase.n[0]*L.rc.r,gestureBase.n[1]*L.rc.r,rad(L.rc.rot)); L.rc.x=gestureBase.P[0]-dx; L.rc.y=gestureBase.P[1]-dy; changed(true);});
cv.addEventListener('gestureend',e=>{e.preventDefault(); gestureBase=null; if(L){settle(); scheduleCommit();}});
cv.addEventListener('keydown',e=>{if(!L)return; const big=e.shiftKey; let h=true;
  if(e.key==='ArrowLeft'){pushHist();L.rc.rot-=big?15:1;} else if(e.key==='ArrowRight'){pushHist();L.rc.rot+=big?15:1;}
  else if(e.key==='+'||e.key==='='){pushHist();scaleAt(cv.width/2,cv.height/2,1.05);} else if(e.key==='-'){pushHist();scaleAt(cv.width/2,cv.height/2,1/1.05);}
  else if((e.key==='z'||e.key==='Z')&&(e.metaKey||e.ctrlKey)){undo();return;} else if(e.key==='r'){rotateBy(90);return;} else if(e.key==='R'){rotateBy(-90);return;} else h=false;
  if(h){e.preventDefault(); clampCircle(L.rc); changed(true); scheduleCommit();}});
$('#rotl').onclick=()=>rotateBy(90); $('#rotr').onclick=()=>rotateBy(-90); $('#undo').onclick=undo;
let idTimer=null, idIdx=0;
$('#identify').onclick=()=>{ if(idTimer){clearInterval(idTimer);idTimer=null;hiCell=null;$('#identify').classList.remove('live');$('#identify').textContent='Identify in turn';draw();return;}
  $('#identify').classList.add('live'); $('#identify').textContent='Stop identifying';
  const step=()=>{if(!L||!L.cells.length)return; idIdx=idIdx%L.cells.length; hiCell=L.cells[idIdx].serial; idIdx++; api('/api/ping',{serial:hiCell}).catch(()=>{}); draw();};
  step(); idTimer=setInterval(step,2600); };
