"""Field v34: puff physics (music pushes particles outward in the sim, they float and ease back), per-source puck glyphs,
GPU pulse displacement toned down to a shimmer."""
PATCHES = [
("  pulseGain: 1.0,         // multiplies the particle pulse displacement",
 "  pulseGain: 0.35,        // multiplies the particle pulse displacement (now a shimmer; the puff physics below carries the beat)\n  puffGain: 0.005, puffBeat: 0.005, puffFloat: 0.9, puffVmax: 0.008,   // velocity impulse per onset (field units/step), per beat, and how long a puffed particle floats before the field pulls it back (s)"),
# per-particle state: float timer
("  function allocParticles(n){ NP = n; P = new Float32Array(n*3); V = new Float32Array(n*3); O = new Float32Array(n*3); L = new Float32Array(n);",
 "  function allocParticles(n){ NP = n; P = new Float32Array(n*3); V = new Float32Array(n*3); O = new Float32Array(n*3); L = new Float32Array(n); FR = new Float32Array(n); HM = new Float32Array(n); RT = new Float32Array(n);"),
("  let NP = 0, P = null, V = null, O = null, L = null, seeds = null,",
 "  let NP = 0, P = null, V = null, O = null, L = null, FR = null, HM = null, RT = null, seeds = null,"),
# the sim: puff impulse + relaxed spring while floating
("  function simStep(dt, simT){\n    const eps = 0.001; prev2.set(pos2);\n    for(let i=0;i<NP;i++){\n      const ix=i*3; let x=P[ix], y=P[ix+1], z=P[ix+2], vx=V[ix], vy=V[ix+1], vz=V[ix+2];\n      const life = L[i] - dt*0.05*60;   // the app decrements per frame\n      let nx, ny, nz;\n      if(life > 0){\n        const px=x+vx, py=y+vy, pz=z+vz;\n        const d0 = sdf3(S,px,py,pz);\n        let gx = sdf3(S,px+eps,py,pz)-d0, gy = sdf3(S,px,py+eps,pz)-d0, gz = sdf3(S,px,py,pz+eps)-d0;\n        const gl2 = Math.sqrt(gx*gx+gy*gy+gz*gz);\n        if(gl2>1e-9){gx/=gl2;gy/=gl2;gz/=gl2;} else {gx=gy=gz=0;}\n        const kk = (d0>0?1.2:0.5)*d0;",
 """  function simStep(dt, simT){
    const eps = 0.001; prev2.set(pos2);
    // music -> puff: an outward velocity impulse per onset in the particle's own spectrum band (bass at the notch,
    // treble behind) plus a beat kick; puffed particles float (weak spring) for puffFloat s and then ease back
    const lo = 1.0-clamp(cfg.reactiveFrac,0.05,1.0); const beatK = cfg.puffBeat*st.beat; const nx0=S.nx, ny0=S.ny;
    let anyPuff = beatK > 0.0005; if(!anyPuff) for(let i=0;i<24;i++) if(st.flux[i] > 0.05){ anyPuff = true; break; }
    for(let i=0;i<NP;i++){
      const ix=i*3; let x=P[ix], y=P[ix+1], z=P[ix+2], vx=V[ix], vy=V[ix+1], vz=V[ix+2];
      const life = L[i] - dt*0.05*60;   // the app decrements per frame
      let nx, ny, nz;
      let fr = FR[i];
      if(anyPuff){
        const sd = seeds[i]*7.13+0.37; const fr0 = sd - Math.floor(sd); const tt = clamp((fr0-lo)/0.15,0,1); const react = 0.12 + 0.88*tt*tt*(3-2*tt);
        const dist = Math.sqrt(x*x+y*y);
        if(dist > 0.02){
          const ang = Math.atan2(-x*ny0 + y*nx0, x*nx0 + y*ny0); const bin = Math.min(23, Math.max(0, Math.floor((0.5+0.5*ang/Math.PI)*24)));
          const imp = react*(cfg.puffGain*st.flux[bin] + beatK);
          const vr = (vx*x+vy*y)/dist;   // current outward speed: a puff tops up to a ceiling, it never stacks without limit
          if(imp > 0.0002 && vr < cfg.puffVmax && dist < 0.6){ const add = Math.min(imp, cfg.puffVmax-vr); vx += x/dist*add; vy += y/dist*add; if(fr<=0 && RT[i]<=0) HM[i]=dist; fr = Math.max(fr, cfg.puffFloat*react); RT[i] = cfg.puffFloat*1.5; }
        }
      }
      if(life > 0){
        const px=x+vx, py=y+vy, pz=z+vz;
        const d0 = sdf3(S,px,py,pz);
        let gx = sdf3(S,px+eps,py,pz)-d0, gy = sdf3(S,px,py+eps,pz)-d0, gz = sdf3(S,px,py,pz+eps)-d0;
        const gl2 = Math.sqrt(gx*gx+gy*gy+gz*gz);
        if(gl2>1e-9){gx/=gl2;gy/=gl2;gz/=gl2;} else {gx=gy=gz=0;}
        const ks = fr>0.5 ? 0.08 : (fr>0 ? 0.08+0.92*(1-fr/0.5) : 1.0);   // floating particles barely feel the field, then it eases back in over the last 0.5 s
        const kk = (d0>0?1.2:0.5)*d0*ks;"""),
("      P[ix]=nx; P[ix+1]=ny; P[ix+2]=nz; V[ix]=vx*0.99; V[ix+1]=vy*0.99; V[ix+2]=vz*0.99;",
 "      if(fr<=0 && RT[i]>0){ // drift home after the float: a gentle pull back to the radius it had before the puff\n        const d2 = Math.sqrt(nx*nx+ny*ny); if(d2>0.02){ const g = (HM[i]-d2)*0.04; vx += nx/d2*g; vy += ny/d2*g; } RT[i] -= dt; }\n      let damp = fr>0 ? 0.94 : 0.99; if(fr>0 && nx*nx+ny*ny > 0.42) damp = 0.85;   // past the rim the puff dies quickly\n      FR[i] = fr>0 ? fr-dt : 0;\n      P[ix]=nx; P[ix+1]=ny; P[ix+2]=nz; V[ix]=vx*damp; V[ix+1]=vy*damp; V[ix+2]=vz*damp;"),
("    setPalette(name){ if(cfg.palettes[name]) cfg.palette = name; },",
 "    setPalette(name){ if(cfg.palettes[name]) cfg.palette = name; },\n    debugRadius(){ let a=0; for(let i=0;i<NP;i++) a+=Math.hypot(pos2[i*2],pos2[i*2+1]); return a/NP; },"),
# glyphs per source
("  const texOrbB = loadTex(gl, TEX.orbB), texOrbC = loadTex(gl, TEX.orbC), texGlyph = loadTex(gl, TEX.airplay);",
 "  const texOrbB = loadTex(gl, TEX.orbB), texOrbC = loadTex(gl, TEX.orbC);\n  const texGlyphs = {}; if(TEX.glyphs) for(const k in TEX.glyphs) texGlyphs[k] = loadTex(gl, TEX.glyphs[k]); if(!texGlyphs.airplay) texGlyphs.airplay = loadTex(gl, TEX.airplay);\n  const glyphFor = n => texGlyphs[n] || texGlyphs[{usb:'usb',puredata:'puredata',hdmi:'hdmi',tidal:'tidal',spotify:'spotify'}[n]] || texGlyphs.airplay;"),
("    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D,texGlyph); gl.uniform1i(orb.u.uGlyph,2);",
 "    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D,glyphFor(cfg.palette)); gl.uniform1i(orb.u.uGlyph,2);"),
]
def patch(src):
    for a, b in PATCHES:
        assert src.count(a) == 1, ("field patch anchor", a[:70], src.count(a))
        src = src.replace(a, b)
    return src
if __name__ == "__main__":
    import sys
    p = sys.argv[1]; src = open(p).read(); out = patch(src); open(p, "w").write(out); print("patched", p)
