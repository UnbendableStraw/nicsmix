// SyngField — faithful reproduction of the Syng Space app's sound-field control.
// Reverse-engineered from the decrypted Syng Space 2.1.0 IPA:
//   * ProtoTools particle system (updateParticle / vertexShader / fragmentShader Metal kernels)
//   * Aurora ShapeLayer "Orb" content (lens-table refraction + gloss maps OrbB / OrbC)
//   * Auras_airplay.json palette (CalculateColorBins histogram of the AirPlay aura)
//   * UISpringTimingParameters(mass 1, stiffness 218.566, damping 29.568)
// Usage: SyngField.create(wrapElement, { post(path, body), viz() -> {energy,bass,spec[]}, scaleM })
(function(){
'use strict';
const TAU = Math.PI*2;
const LENS = [0.251,0.261,0.271,0.281,0.291,0.302,0.312,0.322,0.332,0.342,0.352,0.362,0.372,0.383,0.393,0.403,0.413,0.423,0.433,0.443,0.453,0.463,0.472,0.482,0.492,0.502,0.512,0.521,0.531,0.541,0.55,0.56,0.569,0.579,0.588,0.597,0.606,0.616,0.625,0.633,0.642,0.651,0.659,0.667,0.676,0.684,0.691,0.699,0.706,0.712,0.719,0.725,0.73,0.734,0.738,0.741,0.742,0.741,0.738,0.731,0.719,0.697,0.657,0.57];

// ---------- defaults (all tweakable live through field.cfg) ----------
const DEFAULTS = {
  particles: 12000,        // ProtoTools particle count (CPU sim)
  simHz: 60,              // app runs the kernel per frame at 60 fps
  weightSpatial: 0.88,    // torus "weight": ra 0.28 / rb 0.22 (mask videos + the live recording's stem width)
  weightOmni: 2.0,        // 2 = full sphere (non-spatial: sound all around)
  handleRadius: 0.60,     // handles / notch sit on this fraction of the field radius
  defaultHalfDeg: 95,     // half-spread when spatial is first enabled
  minHalfDeg: 12, maxHalfDeg: 178,
  arcFrac: 1.0, capsAtDots: false, capLo: 0.5, capHi: 0.7, capScale: 1.0,
  liftFrac: 0.26,         // drag the dot this far (x field radius) to enable spatial
  collapseFrac: 0.2,      // drag a handle this close to the puck to collapse (spatial off)
  pulseScale: 2.3,        // distanceScale: field radius -> pulse texture v
  pulseRowsPerSec: 56,    // how fast a beat travels outward
  pulseGain: 1.0,         // multiplies the particle pulse displacement
  pulseEmitter: 0.06,     // emitterRadius (field units; 0.5 = rim)
  glowGain: 1.0, grain: 1.0, flurry: 1.0, haloGain: 1.0,
  pointScale: 1.0, pointGain: 1.0,
  colors: { cyanDeep:[0.0,0.36,0.54], cyan:[0.14,0.64,0.84], cyanLight:[0.54,0.84,0.95], white:[0.90,0.98,1.0], blue:[0.10,0.42,0.86], teal:[0.08,0.60,0.70],
            room:[0.10,0.26,0.33], roomEdge:[0.07,0.19,0.23], bg:[0,0,0] },
  showVolume: true, showRings: true, classicParticles: true,
  volume: 0.25,
  scaleM: 2.5,            // metres per room radius (puck -> listener distance)
};

function clamp(v,a,b){return v<a?a:v>b?b:v;}
function wrapPi(a){return ((a+Math.PI)%TAU+TAU)%TAU-Math.PI;}
function smoothstep(e0,e1,x){const t=clamp((x-e0)/(e1-e0),0,1);return t*t*(3-2*t);}

// ---------- the capped-torus SDF (exact port of ProtoTools cappedTorus/sampleNormalAndDistance) ----------
// Field space: puck at origin, rim at radius 0.5, screen y down. S = prepared params.
function prepSDF(S, dirAngle, halfRad, weight, cfg){
  cfg = cfg || {};
  const arcHalf = halfRad*(cfg.arcFrac==null?1:cfg.arcFrac);
  const spread = clamp(arcHalf/Math.PI, 0.05, 1.0);
  S.sinA = Math.sin(spread*Math.PI); S.cosA = Math.cos(spread*Math.PI);
  S.nx = Math.sin(dirAngle); S.ny = -Math.cos(dirAngle);        // axis of the arc (toward the notch)
  const w = weight*0.25; S.ra = 0.5 - w; S.rb = w;
  const sp2 = clamp(halfRad/Math.PI, 0, 1);
  const capLo = cfg.capLo==null?0.5:cfg.capLo, capHi = cfg.capHi==null?0.7:cfg.capHi;
  const t = clamp((sp2-capLo)/Math.max(1e-4,capHi-capLo), 0, 1); S.capR = S.rb*(t*t*(3-2*t))*(cfg.capScale==null?1:cfg.capScale);
  S.blend = sp2 > capLo ? 0.1 : 1e-5;
  const ca = Math.cos(halfRad), sa = Math.sin(halfRad);
  const cr = cfg.capsAtDots ? (cfg.handleRadius==null?0.62:cfg.handleRadius)*0.5 : S.ra;   // cap centres: at the dots or on the torus centre line
  S.c1x = cr*(S.nx*ca - S.ny*sa); S.c1y = cr*(S.nx*sa + S.ny*ca);
  S.c2x = cr*(S.nx*ca + S.ny*sa); S.c2y = cr*(-S.nx*sa + S.ny*ca);
  S.spread = spread; S.weight = weight; S.half = halfRad; S.dir = dirAngle;
  return S;
}
function sdf3(S, x, y, z){
  const yr = x*S.nx + y*S.ny, xr = Math.abs(x*(-S.ny) + y*S.nx);
  const k = (S.cosA*xr > S.sinA*yr) ? (xr*S.sinA + yr*S.cosA) : Math.sqrt(xr*xr + yr*yr);
  const k2 = Math.sqrt(xr*xr + yr*yr);
  const pp = xr*xr + yr*yr + z*z, ra2 = S.ra*S.ra, two = 2*S.ra;
  const d1 = Math.sqrt(Math.max(0, pp + ra2 - two*k)) - S.rb;
  const d2 = Math.sqrt(Math.max(0, pp + ra2 - two*k2)) - S.rb;
  let dx = x - S.c1x, dy = y - S.c1y; const dS = Math.sqrt(dx*dx + dy*dy + z*z) - S.capR;
  dx = x - S.c2x; dy = y - S.c2y; const dE = Math.sqrt(dx*dx + dy*dy + z*z) - S.capR;
  const h = clamp(0.5*(dE - dS)/S.blend + 0.5, 0, 1);
  const smin = dE*(1-h) + dS*h - S.blend*h*(1-h);
  return Math.max(Math.min(smin, d1), d2);
}

// ---------- WebGL helpers ----------
function mkShader(gl, type, src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);
  if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error('shader: '+gl.getShaderInfoLog(s)+'\n'+src);return s;}
function mkProg(gl, vs, fs, names){const p=gl.createProgram();gl.attachShader(p,mkShader(gl,gl.VERTEX_SHADER,vs));
  gl.attachShader(p,mkShader(gl,gl.FRAGMENT_SHADER,fs));gl.linkProgram(p);
  if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error('link: '+gl.getProgramInfoLog(p));
  const u={};for(const n of names)u[n]=gl.getUniformLocation(p,n);return {p,u};}
function loadTex(gl, src, opts){const t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,(opts&&opts.repeat)?gl.REPEAT:gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,(opts&&opts.repeat)?gl.REPEAT:gl.CLAMP_TO_EDGE);
  const im=new Image();im.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,im);};im.src=src;return t;}

const QUAD_VS = 'attribute vec2 p;varying vec2 vUv;void main(){vUv=p*0.5+0.5;gl_Position=vec4(p,0.0,1.0);}';

// Scene: room disc + aura (palette-recoloured glow of the SDF) + flurry blobs. Rendered to an FBO.
const SCENE_FS = `precision highp float;
varying vec2 vUv;
uniform vec2 uRes,uC,uP; uniform float uR,uRf,uTime,uE,uB,uGlow,uGrain,uFlurry,uSpatial,uPuckR,uHaloGain;
uniform vec3 cBlue,cTeal; uniform float uSpec[12];
uniform vec2 uN; uniform float uSinA,uCosA,uRa,uRb,uCapR,uBlend; uniform vec2 uC1,uC2;
uniform vec3 cDeep,cCyan,cLight,cWhite,cRoom,cRoomEdge;
uniform sampler2D uNoise;
uniform vec4 uBlob[24];
float sdf(vec2 p){
  float yr=dot(p,uN); float xr=abs(dot(p,vec2(-uN.y,uN.x)));
  float k=(uCosA*xr>uSinA*yr)?(xr*uSinA+yr*uCosA):length(vec2(xr,yr));
  float k2=length(vec2(xr,yr)); float pp=xr*xr+yr*yr;
  float d1=sqrt(max(0.0,pp+uRa*uRa-2.0*uRa*k))-uRb;
  float d2=sqrt(max(0.0,pp+uRa*uRa-2.0*uRa*k2))-uRb;
  float dS=length(p-uC1)-uCapR; float dE=length(p-uC2)-uCapR;
  float h=clamp(0.5*(dE-dS)/uBlend+0.5,0.0,1.0);
  float sm=mix(dE,dS,h)-uBlend*h*(1.0-h);
  return max(min(sm,d1),d2);
}
void main(){
  vec2 px=vec2(vUv.x*uRes.x,(1.0-vUv.y)*uRes.y);
  float dC=length(px-uC); float rr=dC/uR;
  float inRoom=1.0-smoothstep(uR-1.0,uR+1.0,dC);
  vec2 f=(px-uP)/(2.0*uRf);              // field space (rim at 0.5)
  float d=sdf(f); float fr=length(f);
  float px2f=1.0/(2.0*uRf);              // one pixel in field units
  // room disc: dark navy-teal, darker toward the rim and the bottom (glass sphere shading)
  vec3 col=mix(cRoom,cRoomEdge,smoothstep(0.35,1.0,rr));
  col*=1.0-0.28*smoothstep(-0.2,1.0,(px.y-uC.y)/uR);
  // aura: the SDF interior glows (palette ramp), soft spill outside, white core at the puck
  float inside=1.0-smoothstep(-6.0*px2f,2.0*px2f,d);
  float spill=exp(-max(d,0.0)*16.0);
  float core=exp(-fr*7.0);
  float g=inside*(0.22+0.22*exp(-fr*3.2))+spill*0.14*(1.0-inside);
  g+=exp(-abs(d)*30.0)*0.22*inside;
  g*=0.80+0.45*uE;
  g+=core*0.55*(1.0+0.5*uB);
  float fl=0.0; vec3 flc=vec3(0.0);
  for(int i=0;i<24;i++){vec4 bb=uBlob[i]; float q=length(f-bb.xy)/max(bb.z,0.001); float w=bb.w*(1.0-smoothstep(0.1,1.0,q)); fl+=w; flc+=w*mix(cBlue,cTeal,mod(float(i),2.0));}
  g+=fl*0.10*uFlurry*inside;
  g*=uGlow;
  vec3 aura=mix(mix(cDeep,cCyan,clamp(g*2.0,0.0,1.0)),cWhite,clamp((g-0.5)*2.0,0.0,1.0));
  // classic (sound all around): the lit disc is a smooth saturated blue, brighter toward the puck, no sparkle needed
  float gc=inside*(0.55+0.30*exp(-fr*2.2))+spill*0.25*(1.0-inside);
  gc*=0.85+0.3*uE;
  vec3 classic=mix(cBlue,mix(cCyan,cWhite,0.35),clamp((gc-0.55)*2.2,0.0,1.0));
  classic=mix(classic,flc/max(fl,0.001),clamp(fl*0.35*uFlurry,0.0,0.45));
  classic+=cWhite*core*0.6*(1.0+0.4*uB);
  vec3 auraS=mix(col,aura,smoothstep(0.0,0.55,g));
  vec3 auraC=mix(col,classic,smoothstep(0.0,0.5,gc));
  auraC=mix(auraC,auraC*1.0+flc*0.12*uFlurry,inside);
  col=mix(auraC,auraS,uSpatial);
  // the pulse halo around the puck (Aurora PulseField): a white ring whose outer edge breathes with the spectrum
  float rp=length(px-uP)/max(uPuckR,1.0);
  float pa=atan(px.x-uP.x,-(px.y-uP.y)); float su=abs(pa)/3.14159265*11.0; int si=int(su); float sf=su-float(si); float sv=0.0;
  for(int k=0;k<12;k++){ if(k==si) sv+=uSpec[k]*(1.0-sf); if(k==si+1) sv+=uSpec[k]*sf; }
  float outerR=1.55+0.55*sv+0.25*uB;
  float ring=smoothstep(1.02,1.22,rp)*(1.0-smoothstep(outerR-0.25,outerR+0.35,rp));
  float haloW=ring*(0.35+0.40*uE)*uHaloGain*mix(0.45,1.0,uSpatial);
  col=mix(col,cWhite,clamp(haloW,0.0,0.95));
  col+=cWhite*exp(-max(rp-1.0,0.0)*2.2)*0.10*uHaloGain*(0.6+0.4*uE);
  // glow bleeding past the room rim (the lobes light up the edge)
  float outer=exp(-max(dC-uR,0.0)/(0.05*uR))*(1.0-inRoom)*smoothstep(0.0,0.4,g)*0.35;
  col+=cCyan*outer;
  // grain (Aurora NoiseRGB)
  float n=texture2D(uNoise,px/96.0+vec2(uTime*0.013,-uTime*0.009)).r;
  col*=1.0+(n-0.5)*0.22*uGrain;
  // glass room rim: faint bright ring + darker just inside
  col+=vec3(0.10,0.22,0.28)*smoothstep(0.90,0.985,rr)*(1.0-smoothstep(0.985,1.0,rr));
  col=mix(col,cCyan*outer,1.0-inRoom);
  gl_FragColor=vec4(col,1.0);
}`;

// Particles: positions from the CPU sim (field space); pulse displacement + size/brightness from the pulse texture
const PART_VS = `precision highp float;
attribute vec2 aPos; attribute float aSeed;
uniform vec2 uRes,uP,uN; uniform float uRf,uDPR,uPtScale,uHead,uPScale,uPEmit,uPGain,uPRows;
uniform sampler2D uPulse;
varying float vCol;
void main(){
  vec2 p=aPos;
  float dist=length(p);
  float ang=(dist>0.001)?atan(dot(p,vec2(-uN.y,uN.x)),dot(p,uN)):0.0;  // 0 at the notch, +-pi at the back
  float u=0.5+0.5*ang/3.14159265;        // mirrored spectrum: bass at the notch, treble behind
  float v=uPScale*abs(dist-uPEmit);
  float row=uHead+v;                     // newest row at uHead, older rows further along +v (REPEAT)
  float s1=texture2D(uPulse,vec2(u,row+1.0/32.0)).r;
  float s2=texture2D(uPulse,vec2(u,row-1.0/32.0)).r;
  float amt=sqrt(max(1.0-v,0.0))*(s1-s2)*uPGain;
  vec2 disp=(dist>0.001?p/dist:vec2(0.0))*0.05*amt;
  vec2 sp=uP+(p+disp)*2.0*uRf;
  vec2 cs=sp/uRes*2.0-1.0; cs.y=-cs.y;
  gl_Position=vec4(cs,0.0,1.0);
  float a=abs(amt)*2.0;
  gl_PointSize=(1.0+min(a*2.0,3.0))*uDPR*uPtScale*(0.75+0.6*aSeed);
  vCol=0.75+a;
}`;
const PART_FS = `precision mediump float; varying float vCol; uniform float uPtGain;
void main(){vec2 q=gl_PointCoord*2.0-1.0; float r=length(q); float a=max(0.0,1.0-r); gl_FragColor=vec4(vec3(0.58,0.92,1.0)*vCol*a*0.36*uPtGain,1.0);}`;

// Composite: scene + glass orbs (puck / handles / notch) refracting the scene with the lens table + gloss maps
const BLIT_FS = `precision highp float; varying vec2 vUv; uniform sampler2D uScene; void main(){gl_FragColor=texture2D(uScene,vUv);}`;

const ORB_VS = `attribute vec2 p; uniform vec2 uRes,uCenter,uSize; uniform float uRot; varying vec2 vL;
void main(){vL=p; vec2 r=vec2(p.x*uSize.x,p.y*uSize.y); float c=cos(uRot),s=sin(uRot); vec2 q=vec2(r.x*c-r.y*s,r.x*s+r.y*c);
  vec2 sp=uCenter+q; vec2 cs=sp/uRes*2.0-1.0; cs.y=-cs.y; gl_Position=vec4(cs,0.0,1.0);}`;
// uMode 0 = orb (circle), 1 = capsule (vL.y stretched); uRadius = circle radius px (capsule: half width)
const ORB_FS = `precision highp float; varying vec2 vL;
uniform sampler2D uScene,uGloss,uGlyph; uniform vec2 uRes,uCenter,uSize; uniform float uRot,uRadius,uAlpha,uGlyphOn,uHalo,uDarken,uMode,uTint;
uniform sampler2D uLensTex;
float lens(float r){return texture2D(uLensTex,vec2(clamp(r,0.0,1.0)*(63.0/64.0)+0.5/64.0,0.5)).r;}
void main(){
  vec2 l=vL; vec2 q=l;
  if(uMode>2.5){ float rh=length(l); float sh=uHalo*smoothstep(1.0,0.55,rh); gl_FragColor=vec4(0.02,0.08,0.14,sh); return; }
  if(uMode>1.5){ float rh=length(l); gl_FragColor=vec4(1.0,1.0,1.0,uHalo*(1.0-smoothstep(0.0,1.0,rh))*0.5); return; }
  float along=0.0;
  if(uMode>0.5){ float ext=uSize.y/uSize.x-1.0; along=clamp(l.y*(uSize.y/uSize.x),-ext,ext); q=vec2(l.x,l.y*(uSize.y/uSize.x)-along); }
  float r=length(q);
  float aa=1.0-smoothstep(1.0-2.0/uRadius,1.0,r);
  if(aa<=0.0) discard;
  vec2 dir=(r>0.0001)?q/r:vec2(0.0);
  float off=lens(r)*2.0-0.5;              // Aurora Orb: lensTable*2-0.5
  float c=cos(uRot),s=sin(uRot);
  vec2 local=vec2(dir.x*off, along+dir.y*off)*uRadius;
  vec2 sp=uCenter+vec2(local.x*c-local.y*s,local.x*s+local.y*c);
  vec2 uv=sp/uRes; uv.y=1.0-uv.y;
  vec3 bg=texture2D(uScene,uv).rgb;
  bg=mix(bg,bg*vec3(0.30,0.62,1.0)+vec3(0.02,0.16,0.38),uTint);
  // gloss map: r darkens, g contrast, b adds (ShapeLayer Orb content)
  vec2 guv=vec2(0.5+0.5*q.x,0.5+0.5*q.y);
  vec3 g=texture2D(uGloss,guv).rgb;
  vec3 c1=clamp(bg-1.0+g.r*mix(1.0,0.75,uDarken)+ (1.0-g.r)*0.0,0.0,1.0);
  vec3 c2=clamp(1.0-(1.0-c1)/max(g.g,0.05),0.0,1.0);
  vec3 col=clamp(c2+g.b,0.0,1.0);
  // bubble treatment: slightly darker interior, bright thin rim (the handles / notch look)
  float rim=smoothstep(0.74,0.90,r)*(1.0-smoothstep(0.93,1.0,r));
  col=mix(col,col*mix(1.0,0.72,uDarken),1.0-smoothstep(0.7,0.9,r));
  col+=vec3(0.9,0.98,1.0)*rim*uDarken*0.9;
  if(uGlyphOn>0.5){ vec4 gl=texture2D(uGlyph,vec2(0.5+0.5*q.x*1.18,0.5+0.5*q.y*1.18)); col=mix(col,vec3(1.0),gl.a*0.95); }
  gl_FragColor=vec4(col,aa*uAlpha);
}`;

function create(wrap, opts){
  opts = opts || {};
  const cfg = JSON.parse(JSON.stringify(DEFAULTS)); if(opts.cfg) Object.assign(cfg, opts.cfg);
  const post = opts.post || (()=>{});
  const vizFn = opts.viz || null;
  wrap.style.position = 'relative'; wrap.style.overflow = 'hidden'; wrap.style.touchAction = 'none';
  const glc = document.createElement('canvas'); glc.style.cssText = 'display:block;width:100%;';
  const ui = document.createElement('canvas'); ui.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none';
  wrap.appendChild(glc); wrap.appendChild(ui);
  const gl = glc.getContext('webgl', {alpha:false, antialias:false, premultipliedAlpha:false, preserveDrawingBuffer:true});
  if(!gl) throw new Error('WebGL unavailable');
  const ctx = ui.getContext('2d');
  const TEX = (opts.textures) || (typeof SYNG_TEX !== 'undefined' ? SYNG_TEX : {});

  // ----- programs -----
  const scene = mkProg(gl, QUAD_VS, SCENE_FS, ['uRes','uC','uP','uR','uRf','uTime','uE','uB','uGlow','uGrain','uFlurry','uN','uSinA','uCosA','uRa','uRb','uCapR','uBlend','uC1','uC2','cDeep','cCyan','cLight','cWhite','cRoom','cRoomEdge','uNoise','uBlob','uSpatial','uPuckR','uHaloGain','cBlue','cTeal','uSpec']);
  const part = mkProg(gl, PART_VS, PART_FS, ['uRes','uP','uN','uRf','uDPR','uPtScale','uHead','uPScale','uPEmit','uPGain','uPRows','uPulse','uPtGain']);
  const blit = mkProg(gl, QUAD_VS, BLIT_FS, ['uScene']);
  const orb = mkProg(gl, ORB_VS, ORB_FS, ['uScene','uGloss','uGlyph','uRes','uCenter','uSize','uRot','uRadius','uAlpha','uGlyphOn','uHalo','uDarken','uMode','uTint','uLensTex']);
  const quad = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, quad); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
  const texNoise = loadTex(gl, TEX.noise, {repeat:true});
  const texOrbB = loadTex(gl, TEX.orbB), texOrbC = loadTex(gl, TEX.orbC), texGlyph = loadTex(gl, TEX.airplay);
  const lensTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, lensTex);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.LUMINANCE,64,1,0,gl.LUMINANCE,gl.UNSIGNED_BYTE,new Uint8Array(LENS.map(v=>Math.round(v*255))));
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  // pulse texture: 64 angle columns x PR time rows (luminance), ring buffer
  const PR = 96, PW = 64; const pulseTex = gl.createTexture(); const pulseBuf = new Uint8Array(PW*PR);
  gl.bindTexture(gl.TEXTURE_2D, pulseTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, PW, PR, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, pulseBuf);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  let pulseHead = 0, pulseAcc = 0;
  // FBO
  const fbo = gl.createFramebuffer(); const sceneTex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, sceneTex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  // ----- particles (CPU sim, exact port of updateParticle) -----
  let NP = 0, P = null, V = null, O = null, L = null, seeds = null, posBuf = gl.createBuffer(), seedBuf = gl.createBuffer(), pos2 = null;
  function rand1(x){ const s = Math.sin(x)*43758.5453; return s - Math.floor(s); }
  function spawn(i){ const t = i*0.125; P[i*3] = rand1(t)-0.5; P[i*3+1] = rand1(t+1)-0.5; P[i*3+2] = rand1(t+2)-0.5;
    V[i*3]=V[i*3+1]=V[i*3+2]=0; O[i*3]=P[i*3];O[i*3+1]=P[i*3+1];O[i*3+2]=P[i*3+2]; L[i] = 10+90*rand1(i*0.37+1.3); }
  function allocParticles(n){ NP = n; P = new Float32Array(n*3); V = new Float32Array(n*3); O = new Float32Array(n*3); L = new Float32Array(n);
    pos2 = new Float32Array(n*2); seeds = new Float32Array(n); for(let i=0;i<n;i++){ spawn(i); seeds[i] = Math.random(); }
    gl.bindBuffer(gl.ARRAY_BUFFER, seedBuf); gl.bufferData(gl.ARRAY_BUFFER, seeds, gl.STATIC_DRAW); }
  allocParticles(cfg.particles);
  const S = {};
  function simStep(dt, simT){
    const eps = 0.001;
    for(let i=0;i<NP;i++){
      const ix=i*3; let x=P[ix], y=P[ix+1], z=P[ix+2], vx=V[ix], vy=V[ix+1], vz=V[ix+2];
      const life = L[i] - dt*0.05*60;   // the app decrements per frame
      let nx, ny, nz;
      if(life > 0){
        const px=x+vx, py=y+vy, pz=z+vz;
        const d0 = sdf3(S,px,py,pz);
        let gx = sdf3(S,px+eps,py,pz)-d0, gy = sdf3(S,px,py+eps,pz)-d0, gz = sdf3(S,px,py,pz+eps)-d0;
        const gl2 = Math.sqrt(gx*gx+gy*gy+gz*gz);
        if(gl2>1e-9){gx/=gl2;gy/=gl2;gz/=gl2;} else {gx=gy=gz=0;}
        const kk = (d0>0?1.2:0.5)*d0;
        const fx=-kk*gx, fy=-kk*gy, fz=-kk*gz;
        vx+=fx; vy+=fy; vz+=fz;
        nx = x+fx+vx; ny = y+fy+vy; nz = z+fz+vz;
        L[i] = life;
      } else {
        const s = Math.sin(life + simT*0.01);
        nx = O[ix]*s; ny = O[ix+1]*s; nz = O[ix+2]*s; L[i] = 100;
      }
      const len = Math.sqrt(nx*nx+ny*ny+nz*nz);
      if(len>1){nx/=len;ny/=len;nz/=len;}
      P[ix]=nx; P[ix+1]=ny; P[ix+2]=nz; V[ix]=vx*0.99; V[ix+1]=vy*0.99; V[ix+2]=vz*0.99;
    }
  }

  // ----- state -----
  const st = {
    W:0, H:0, DPR:1, C:[0,0], R:1,
    puck:[0,0], puckT:[0,0], puckV:[0,0], grab:null, grabOff:[0,0], puckScale:1, puckScaleV:0,
    spatial:false, dir:0, dirT:0, dirV:0, half:0, halfT:0, halfV:0, weight:cfg.weightOmni, weightV:0,
    volume: cfg.volume, volT: cfg.volume,
    e:0.25, b:0, spec:new Float32Array(24), t:0,
    blobs:[], liftDot:null,
  };
  for(let i=0;i<24;i++) st.blobs.push({x:(Math.random()-0.5)*0.8, y:(Math.random()-0.5)*0.8, r:0.36+Math.random()*0.09, a:0, vx:0, vy:0, life:Math.random()*10, ph:Math.random()*TAU});

  function resize(){
    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min(2, window.devicePixelRatio||1);
    st.W=W; st.H=H; st.DPR=DPR;
    glc.width = Math.round(W*DPR); glc.height = Math.round(H*DPR); glc.style.height = H+'px';
    ui.width = glc.width; ui.height = glc.height; ui.style.height = H+'px';
    gl.bindTexture(gl.TEXTURE_2D, sceneTex); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,glc.width,glc.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, sceneTex, 0); gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const R = W*0.385; st.R = R; st.C = [W/2, 0.045*W + R];
    if(!st.puckInit){ st.puck=[st.C[0],st.C[1]]; st.puckT=[st.C[0],st.C[1]]; st.puckInit=true; }
  }
  resize(); window.addEventListener('resize', resize);

  // ----- geometry helpers -----
  function fieldR(){ const d = Math.hypot(st.puck[0]-st.C[0], st.puck[1]-st.C[1]); return Math.max(st.R*0.2, st.R - d); }
  function puckR(){ return st.R*0.165*st.puckScale; }
  function handleR(){ return st.R*0.052; }
  function notchSize(){ return [st.R*0.042, st.R*0.115]; }   // half-width, half-length
  function dirVec(a){ return [Math.sin(a), -Math.cos(a)]; }
  function handlePos(angle){ const Rf = fieldR(); const v = dirVec(angle); const r = cfg.handleRadius*Rf; return [st.puck[0]+v[0]*r, st.puck[1]+v[1]*r]; }
  function liftDotPos(){ const r = puckR()+handleR()+st.R*0.015; return [st.puck[0], st.puck[1]-r]; }

  // ----- springs (UISpringTimingParameters mass 1, stiffness 218.566, damping 29.568 => critically damped) -----
  const K=218.566, Cd=29.568;
  function spring1(x, v, target, dt){ const h=1/240; let t=0; while(t<dt){ const a=-K*(x-target)-Cd*v; v+=a*h; x+=v*h; t+=h; } return [x,v]; }
  function springAngle(x, v, target, dt){ x = target + wrapPi(x-target); return spring1(x, v, target, dt); }

  // ----- input -----
  function evPos(e){ const r = glc.getBoundingClientRect(); return [e.clientX-r.left, e.clientY-r.top]; }
  function near(p, q, rad){ return Math.hypot(p[0]-q[0], p[1]-q[1]) <= rad; }
  function angleOf(p){ return Math.atan2(p[0]-st.puck[0], -(p[1]-st.puck[1])); }   // 0 = up, clockwise +
  let lastPost = 0, postTimer = null;
  function sendState(final){
    const d = Math.hypot(st.puckT[0]-st.C[0], st.puckT[1]-st.C[1]);
    const body = { aim: Math.round(((Math.atan2(st.puckT[0]-st.C[0], -(st.puckT[1]-st.C[1]))*180/Math.PI)+360)%360*10)/10,
      distance: Math.round(d/st.R*cfg.scaleM*100)/100,
      spread: st.spatial ? Math.round(st.halfT*2*180/Math.PI) : 360,
      rot: Math.round(((st.dirT*180/Math.PI)+360)%360*10)/10,
      spatial: st.spatial };
    const now = performance.now();
    if(final || now-lastPost > 110){ lastPost = now; clearTimeout(postTimer); postTimer=null; post('/api/soundfield', body); }
    else if(!postTimer){ postTimer = setTimeout(()=>{postTimer=null; sendState(true);}, 120); }
    if(opts.onChange) opts.onChange(body);
  }
  function sendVolume(final){ const now=performance.now(); if(final || now-lastPost>110){ lastPost=now; post('/api/volume', {source:'all', volume: Math.round(st.volT*1000)/1000}); } }
  function clampPuck(p){ const dx=p[0]-st.C[0], dy=p[1]-st.C[1]; const d=Math.hypot(dx,dy), m=st.R*0.78; if(d>m) return [st.C[0]+dx/d*m, st.C[1]+dy/d*m]; return p; }
  function volFromPoint(p){ const a = Math.atan2(p[1]-st.C[1], p[0]-st.C[0]); // 0 = right, pi/2 = down
    const lo = Math.PI/2 - VOL_HALF, hi = Math.PI/2 + VOL_HALF; let aa = clamp(a, lo, hi);
    return (hi - aa)/(hi-lo); }   // left = quiet, clockwise to the right = loud
  const VOL_HALF = 52*Math.PI/180;
  glc.addEventListener('pointerdown', e=>{
    const p = evPos(e); glc.setPointerCapture(e.pointerId); e.preventDefault();
    const hr = handleR()*2.2;
    if(st.spatial){
      const n = handlePos(st.dir); if(near(p, n, hr*1.3)){ st.grab={t:'notch'}; return; }
      for(const sgn of [1,-1]){ const h = handlePos(st.dir+sgn*st.half); if(near(p,h,hr)){ st.grab={t:'dot', sgn}; return; } }
    } else {
      const ld = liftDotPos(); if(near(p, ld, hr)){ st.grab={t:'lift'}; return; }
    }
    if(near(p, st.puck, puckR()*1.25)){ st.grab={t:'puck'}; st.grabOff=[st.puck[0]-p[0], st.puck[1]-p[1]]; return; }
    const d = Math.hypot(p[0]-st.C[0], p[1]-st.C[1]); const a = Math.atan2(p[1]-st.C[1], p[0]-st.C[0]);
    if(cfg.showVolume && d > st.R*0.92 && Math.abs(a-Math.PI/2) < VOL_HALF+0.1){ st.grab={t:'vol'}; st.volT = volFromPoint(p); sendVolume(false); }
  });
  glc.addEventListener('pointermove', e=>{
    if(!st.grab) return; const p = evPos(e); const g = st.grab;
    if(g.t==='puck'){ st.puckT = clampPuck([p[0]+st.grabOff[0], p[1]+st.grabOff[1]]); sendState(false); }
    else if(g.t==='lift'){ const Rf = fieldR(); const dd = Math.hypot(p[0]-st.puck[0], p[1]-st.puck[1]);
      st.liftDot = p;
      if(dd > cfg.liftFrac*Rf){ st.spatial = true; st.dir = st.dirT = angleOf(p); st.halfT = cfg.defaultHalfDeg*Math.PI/180; st.half = Math.min(st.halfT, 0.35); st.halfV = 0;
        st.grab = {t:'notch'}; st.liftDot=null; sendState(false); } }
    else if(g.t==='notch'){ st.dirT = angleOf(p); sendState(false); }
    else if(g.t==='dot'){ const dd = Math.hypot(p[0]-st.puck[0], p[1]-st.puck[1]); const Rf = fieldR();
      if(dd < cfg.collapseFrac*Rf){ st.spatial=false; st.grab=null; sendState(true); return; }
      let h = Math.abs(wrapPi(angleOf(p) - st.dirT)); st.halfT = clamp(h, cfg.minHalfDeg*Math.PI/180, cfg.maxHalfDeg*Math.PI/180); sendState(false); }
    else if(g.t==='vol'){ st.volT = volFromPoint(p); sendVolume(false); }
  });
  const up = e=>{ if(!st.grab) return; const g=st.grab; st.grab=null; st.liftDot=null;
    if(g.t==='vol') sendVolume(true); else if(g.t!=='lift') sendState(true); };
  glc.addEventListener('pointerup', up); glc.addEventListener('pointercancel', up);

  // ----- audio feed -----
  function pullViz(){ if(!vizFn) return; const v = vizFn(); if(!v) return;
    st.e += ((v.energy||0) - st.e)*0.25; st.b += ((v.bass||0) - st.b)*0.3;
    const sp = v.spec||[]; for(let i=0;i<24;i++){ const t = sp.length? sp[Math.min(sp.length-1, Math.floor(i*sp.length/24))] : 0; st.spec[i] += (t - st.spec[i])*0.35; } }
  function writePulseRow(){ const row = pulseHead; const nb = 24;
    for(let c=0;c<PW;c++){ const u = c/(PW-1); const bi = Math.abs(u*2-1)*(nb-1); const i0=Math.floor(bi), i1=Math.min(nb-1,i0+1), f=bi-i0;
      const val = st.spec[i0]*(1-f)+st.spec[i1]*f; pulseBuf[row*PW+c] = clamp(Math.round(val*255),0,255); }
    gl.bindTexture(gl.TEXTURE_2D, pulseTex); gl.texSubImage2D(gl.TEXTURE_2D,0,0,row,PW,1,gl.LUMINANCE,gl.UNSIGNED_BYTE,pulseBuf.subarray(row*PW,row*PW+PW));
    pulseHead = (pulseHead - 1 + PR) % PR; }

  // ----- frame -----
  let last = null, simAcc = 0;
  function frame(now){
    requestAnimationFrame(frame);
    if(last===null) last = now; let dt = Math.min(0.05, (now-last)/1000); last = now; st.t += dt;
    pullViz();
    // springs
    let r = spring1(st.puck[0], st.puckV[0], st.puckT[0], dt); st.puck[0]=r[0]; st.puckV[0]=r[1];
    r = spring1(st.puck[1], st.puckV[1], st.puckT[1], dt); st.puck[1]=r[0]; st.puckV[1]=r[1];
    r = spring1(st.puckScale, st.puckScaleV, (st.grab&&st.grab.t==='puck')?1.1:1.0, dt); st.puckScale=r[0]; st.puckScaleV=r[1];
    r = springAngle(st.dir, st.dirV, st.dirT, dt); st.dir=r[0]; st.dirV=r[1];
    r = spring1(st.half, st.halfV, st.halfT, dt); st.half=r[0]; st.halfV=r[1];
    r = spring1(st.weight, st.weightV, st.spatial?cfg.weightSpatial:cfg.weightOmni, dt); st.weight=r[0]; st.weightV=r[1];
    st.volume += (st.volT-st.volume)*Math.min(1,dt*14);
    // particle sim at fixed rate
    prepSDF(S, st.dir, st.spatial?st.half:Math.PI, st.weight, cfg);
    simAcc += dt; const h = 1/cfg.simHz; let steps=0; while(simAcc >= h && steps<3){ simStep(h, st.t); simAcc -= h; steps++; } if(simAcc>h) simAcc=0;
    // pulse rows
    pulseAcc += dt*cfg.pulseRowsPerSec; while(pulseAcc>=1){ writePulseRow(); pulseAcc-=1; }
    // flurry blobs
    for(const b of st.blobs){ b.life -= dt; if(b.life<=0){ b.life = 10*(0.5+Math.random()); const a=Math.random()*TAU, rr=Math.random()*0.42; b.x=Math.cos(a)*rr; b.y=Math.sin(a)*rr; b.r=0.36+Math.random()*0.09; b.ph=Math.random()*TAU; const va=Math.random()*TAU; b.vx=Math.cos(va)*0.02; b.vy=Math.sin(va)*0.02; }
      b.x += b.vx*dt; b.y += b.vy*dt; const lf = clamp(b.life/2,0,1)*clamp((10-b.life)/2,0,1); b.a = lf*(0.55+0.45*Math.sin(st.t*0.7+b.ph)); }
    draw();
  }
  function draw(){
    const W=st.W, H=st.H, D=st.DPR, RW=glc.width, RH=glc.height;
    const Rf = fieldR(); const px = [st.puck[0]*D, st.puck[1]*D];
    // ---- scene to FBO ----
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.viewport(0,0,RW,RH); gl.disable(gl.BLEND);
    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(scene.p); gl.bindBuffer(gl.ARRAY_BUFFER, quad); const ap=gl.getAttribLocation(scene.p,'p'); gl.enableVertexAttribArray(ap); gl.vertexAttribPointer(ap,2,gl.FLOAT,false,0,0);
    gl.uniform2f(scene.u.uRes,RW,RH); gl.uniform2f(scene.u.uC,st.C[0]*D,st.C[1]*D); gl.uniform2f(scene.u.uP,px[0],px[1]);
    gl.uniform1f(scene.u.uR,st.R*D); gl.uniform1f(scene.u.uRf,Rf*D); gl.uniform1f(scene.u.uTime,st.t);
    gl.uniform1f(scene.u.uE,st.e); gl.uniform1f(scene.u.uB,st.b); gl.uniform1f(scene.u.uGlow,cfg.glowGain); gl.uniform1f(scene.u.uGrain,cfg.grain); gl.uniform1f(scene.u.uFlurry,cfg.flurry);
    gl.uniform2f(scene.u.uN,S.nx,S.ny); gl.uniform1f(scene.u.uSinA,S.sinA); gl.uniform1f(scene.u.uCosA,S.cosA); gl.uniform1f(scene.u.uRa,S.ra); gl.uniform1f(scene.u.uRb,S.rb);
    gl.uniform1f(scene.u.uCapR,S.capR); gl.uniform1f(scene.u.uBlend,S.blend); gl.uniform2f(scene.u.uC1,S.c1x,S.c1y); gl.uniform2f(scene.u.uC2,S.c2x,S.c2y);
    const c=cfg.colors; gl.uniform3fv(scene.u.cBlue,c.blue); gl.uniform3fv(scene.u.cTeal,c.teal);
    gl.uniform1f(scene.u.uSpatial,1-clamp((st.weight-cfg.weightSpatial)/(cfg.weightOmni-cfg.weightSpatial),0,1)); gl.uniform1f(scene.u.uPuckR,puckR()*D); gl.uniform1f(scene.u.uHaloGain,cfg.haloGain);
    {const sp=new Float32Array(12); for(let i=0;i<12;i++) sp[i]=st.spec[i*2]; gl.uniform1fv(scene.u.uSpec,sp);}
    gl.uniform3fv(scene.u.cDeep,c.cyanDeep); gl.uniform3fv(scene.u.cCyan,c.cyan); gl.uniform3fv(scene.u.cLight,c.cyanLight); gl.uniform3fv(scene.u.cWhite,c.white); gl.uniform3fv(scene.u.cRoom,c.room); gl.uniform3fv(scene.u.cRoomEdge,c.roomEdge);
    const bl = new Float32Array(24*4); st.blobs.forEach((b,i)=>{bl[i*4]=b.x;bl[i*4+1]=b.y;bl[i*4+2]=b.r;bl[i*4+3]=b.a;}); gl.uniform4fv(scene.u.uBlob, bl);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texNoise); gl.uniform1i(scene.u.uNoise,0);
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    // particles (additive)
    for(let i=0;i<NP;i++){ pos2[i*2]=P[i*3]; pos2[i*2+1]=P[i*3+1]; }
    gl.bindBuffer(gl.ARRAY_BUFFER,posBuf); gl.bufferData(gl.ARRAY_BUFFER,pos2,gl.DYNAMIC_DRAW);
    gl.useProgram(part.p); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE,gl.ONE);
    const aPos=gl.getAttribLocation(part.p,'aPos'); gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos,2,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,seedBuf); const aSeed=gl.getAttribLocation(part.p,'aSeed'); gl.enableVertexAttribArray(aSeed); gl.vertexAttribPointer(aSeed,1,gl.FLOAT,false,0,0);
    gl.uniform2f(part.u.uRes,RW,RH); gl.uniform2f(part.u.uP,px[0],px[1]); gl.uniform2f(part.u.uN,S.nx,S.ny); gl.uniform1f(part.u.uRf,Rf*D); gl.uniform1f(part.u.uDPR,D);
    gl.uniform1f(part.u.uPtScale,cfg.pointScale*Math.max(0.7,st.R/180)); gl.uniform1f(part.u.uHead,pulseHead/PR); gl.uniform1f(part.u.uPScale,cfg.pulseScale); gl.uniform1f(part.u.uPEmit,cfg.pulseEmitter); gl.uniform1f(part.u.uPGain,cfg.pulseGain); gl.uniform1f(part.u.uPtGain,cfg.pointGain); gl.uniform1f(part.u.uPRows,PR);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,pulseTex); gl.uniform1i(part.u.uPulse,0);
    if(cfg.classicParticles || st.spatial || st.weight<cfg.weightOmni-0.05) gl.drawArrays(gl.POINTS,0,NP);
    gl.disableVertexAttribArray(aSeed);
    // ---- composite ----
    gl.bindFramebuffer(gl.FRAMEBUFFER,null); gl.viewport(0,0,RW,RH); gl.disable(gl.BLEND);
    gl.useProgram(blit.p); gl.bindBuffer(gl.ARRAY_BUFFER,quad); const bp=gl.getAttribLocation(blit.p,'p'); gl.enableVertexAttribArray(bp); gl.vertexAttribPointer(bp,2,gl.FLOAT,false,0,0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,sceneTex); gl.uniform1i(blit.u.uScene,0); gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    // orbs
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(orb.p); const op=gl.getAttribLocation(orb.p,'p'); gl.enableVertexAttribArray(op); gl.vertexAttribPointer(op,2,gl.FLOAT,false,0,0);
    gl.uniform2f(orb.u.uRes,RW,RH);
    gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D,lensTex); gl.uniform1i(orb.u.uLensTex,3);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,sceneTex); gl.uniform1i(orb.u.uScene,0);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D,texGlyph); gl.uniform1i(orb.u.uGlyph,2);
    function drawOrb(cx,cy,rad,glossTex,o){ o=o||{}; gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D,glossTex); gl.uniform1i(orb.u.uGloss,1);
      const mode = o.shadow ? 3 : (o.halo ? 2 : (o.capsule ? 1 : 0)); const len = o.len||rad; const ext = o.halo ? 3.4 : 1.0;
      gl.uniform2f(orb.u.uCenter,cx*D,cy*D); gl.uniform2f(orb.u.uSize,rad*D*ext,(mode===1?len:rad)*D*ext); gl.uniform1f(orb.u.uRot,o.rot||0);
      gl.uniform1f(orb.u.uRadius,rad*D); gl.uniform1f(orb.u.uAlpha,o.alpha==null?1:o.alpha); gl.uniform1f(orb.u.uGlyphOn,o.glyph?1:0);
      gl.uniform1f(orb.u.uHalo,o.halo||o.shadow||0); gl.uniform1f(orb.u.uDarken,o.darken||0); gl.uniform1f(orb.u.uMode,mode); gl.uniform1f(orb.u.uTint,o.tint||0);
      gl.drawArrays(gl.TRIANGLE_STRIP,0,4); }
    const hr=handleR();
    if(st.spatial){
      const vis = clamp((st.weight-cfg.weightSpatial)/(cfg.weightOmni-cfg.weightSpatial),0,1); // 1 = still omni
      const alpha = 1-vis;
      for(const sgn of [1,-1]){ const hp=handlePos(st.dir+sgn*st.half); const grabbed = st.grab&&st.grab.t==='dot'&&st.grab.sgn===sgn;
        if(grabbed) drawOrb(hp[0],hp[1],hr,texOrbC,{halo:0.55}); drawOrb(hp[0],hp[1],hr,texOrbC,{alpha,darken:0.5}); }
      const np=handlePos(st.dir); const ns=notchSize(); const grabbedN = st.grab&&st.grab.t==='notch';
      if(grabbedN) drawOrb(np[0],np[1],ns[1]*0.8,texOrbC,{halo:0.55});
      drawOrb(np[0],np[1],ns[0],texOrbC,{capsule:true,len:ns[1],rot:st.dir,alpha,darken:0.55});
    } else {
      const ld = st.liftDot || liftDotPos(); const grabbed = st.grab&&st.grab.t==='lift';
      if(grabbed) drawOrb(ld[0],ld[1],hr,texOrbC,{halo:0.55});
      drawOrb(ld[0],ld[1],hr*0.85,texOrbC,{darken:0.5});
    }
    drawOrb(st.puck[0],st.puck[1],puckR()*1.32,texOrbB,{shadow:0.75}); drawOrb(st.puck[0],st.puck[1],puckR(),texOrbB,{glyph:true,tint:0.85,darken:0.25});
    // ---- 2D UI: volume ticks ----
    ctx.setTransform(D,0,0,D,0,0); ctx.clearRect(0,0,W,H);
    if(cfg.showVolume){ const Rv=st.R*1.10, tl=st.R*0.065; const n=44; const cur=st.volume;
      for(let i=0;i<=n;i++){ const f=i/n; const a=Math.PI/2+VOL_HALF-f*2*VOL_HALF; const on=f<=cur+1e-6; const isCur=Math.abs(f-cur)<=0.5/n;
        const r0=Rv, r1=Rv+tl*(isCur?1.6:1); const ca=Math.cos(a), sa=Math.sin(a);
        ctx.strokeStyle = on? 'rgba(255,255,255,'+(isCur?1:0.85)+')' : 'rgba(255,255,255,0.28)'; ctx.lineWidth = isCur?2.2:1.6; ctx.lineCap='round';
        ctx.beginPath(); ctx.moveTo(st.C[0]+ca*r0, st.C[1]+sa*r0); ctx.lineTo(st.C[0]+ca*r1, st.C[1]+sa*r1); ctx.stroke(); } }
    if(cfg.showRings && opts.rings){ const rr=st.R*0.085; for(const rg of (opts.rings()||[])){ const dC=Math.hypot(rg[0]-st.C[0],rg[1]-st.C[1]); if(dC>st.R-rr*0.3) continue;
        ctx.strokeStyle=rg[2]?'rgba(220,240,248,0.42)':'rgba(205,225,235,0.26)'; ctx.lineWidth=2.2; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr,0,TAU); ctx.stroke();
        ctx.strokeStyle='rgba(205,225,235,0.14)'; ctx.lineWidth=1.2; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr*0.62,0,TAU); ctx.stroke();
        ctx.fillStyle='rgba(200,225,235,0.06)'; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr,0,TAU); ctx.fill(); } }
  }
  requestAnimationFrame(frame);

  const api = {
    cfg, state: st,
    resetParticles(){ for(let i=0;i<NP;i++) spawn(i); },
    setParticles(n){ allocParticles(n|0); },
    setSpatial(on, halfDeg, dirDeg){ st.spatial=!!on; if(halfDeg!=null) st.halfT = halfDeg*Math.PI/180; if(dirDeg!=null) st.dirT = dirDeg*Math.PI/180; if(st.half===0) st.half=st.halfT; },
    setPuckPolar(aimDeg, distFrac){ const a=aimDeg*Math.PI/180; const r=clamp(distFrac,0,0.78)*st.R; st.puckT=[st.C[0]+Math.sin(a)*r, st.C[1]-Math.cos(a)*r]; },
    setVolume(v){ st.volT = clamp(v,0,1); },
    center(){ st.puckT=[st.C[0],st.C[1]]; },
  };
  return api;
}
window.SyngField = { create, DEFAULTS, sdf3, prepSDF };
})();
