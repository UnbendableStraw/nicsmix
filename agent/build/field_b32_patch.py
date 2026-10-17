"""Field v32 patches: half-resolution background pass, per-particle reactivity, DPR cap. Applied to web/field.js and to the copy embedded in the agent."""
PATCHES = [
("  particles: 18000,        // ProtoTools particle count (CPU sim)",
 "  particles: 18000,        // ProtoTools particle count (CPU sim)\n  sceneScale: 0.5,         // the glow/flurry background renders at this fraction of the canvas (fragment-bound on phones/tablets)\n  reactiveFrac: 0.5,       // share of particles that take the full music push; the rest mostly idle (the app's two populations)\n  maxDPR: 1.5,"),
("    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min((navigator.maxTouchPoints>0)?1.5:2, window.devicePixelRatio||1);",
 "    const W = wrap.clientWidth || 360; const H = Math.round(W*0.94); const DPR = Math.min(cfg.maxDPR||1.5, window.devicePixelRatio||1);"),
("  const fbo = gl.createFramebuffer(); const sceneTex = gl.createTexture();\n  gl.bindTexture(gl.TEXTURE_2D, sceneTex);\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);",
 "  const fbo = gl.createFramebuffer(); const sceneTex = gl.createTexture();\n  gl.bindTexture(gl.TEXTURE_2D, sceneTex);\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);\n  const sceneLowTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, sceneLowTex);   // the soft background, rendered small and upscaled\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);\n  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);\n  let LW = 1, LH = 1;"),
("    gl.bindTexture(gl.TEXTURE_2D, sceneTex); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,glc.width,glc.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);",
 "    gl.bindTexture(gl.TEXTURE_2D, sceneTex); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,glc.width,glc.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);\n    LW = Math.max(1, Math.round(glc.width*(cfg.sceneScale||0.5))); LH = Math.max(1, Math.round(glc.height*(cfg.sceneScale||0.5)));\n    gl.bindTexture(gl.TEXTURE_2D, sceneLowTex); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,LW,LH,0,gl.RGBA,gl.UNSIGNED_BYTE,null);"),
("    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.viewport(0,0,RW,RH); gl.disable(gl.BLEND);\n    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);",
 "    const SS = LW/RW;   // background pass at reduced resolution\n    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, sceneLowTex, 0); gl.viewport(0,0,LW,LH); gl.disable(gl.BLEND);\n    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);"),
("    gl.uniform2f(scene.u.uRes,RW,RH); gl.uniform2f(scene.u.uC,st.C[0]*D,st.C[1]*D); gl.uniform2f(scene.u.uP,px[0],px[1]);\n    gl.uniform1f(scene.u.uR,st.R*D); gl.uniform1f(scene.u.uRf,Rf*D); gl.uniform1f(scene.u.uTime,st.t);",
 "    gl.uniform2f(scene.u.uRes,LW,LH); gl.uniform2f(scene.u.uC,st.C[0]*D*SS,st.C[1]*D*SS); gl.uniform2f(scene.u.uP,px[0]*SS,px[1]*SS);\n    gl.uniform1f(scene.u.uR,st.R*D*SS); gl.uniform1f(scene.u.uRf,Rf*D*SS); gl.uniform1f(scene.u.uTime,st.t);"),
("gl.uniform1f(scene.u.uPuckR,puckR()*D); gl.uniform1f(scene.u.uHaloGain,cfg.haloGain);",
 "gl.uniform1f(scene.u.uPuckR,puckR()*D*SS); gl.uniform1f(scene.u.uHaloGain,cfg.haloGain);"),
("    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);\n    // particles (additive)",
 "    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);\n    // upscale the background into the full-size scene texture, then the particles go on top at full resolution\n    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, sceneTex, 0); gl.viewport(0,0,RW,RH);\n    gl.useProgram(blit.p); const bp0=gl.getAttribLocation(blit.p,'p'); gl.enableVertexAttribArray(bp0); gl.vertexAttribPointer(bp0,2,gl.FLOAT,false,0,0);\n    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,sceneLowTex); gl.uniform1i(blit.u.uScene,0); gl.drawArrays(gl.TRIANGLE_STRIP,0,4);\n    // particles (additive)"),
# per-particle reactivity: about reactiveFrac of them take the full push, the rest mostly idle
("  float amt=sqrt(max(1.0-v,0.0))*(s1-s2)*uPGain;",
 "  float react=mix(0.12,1.0,smoothstep(uReactLo,uReactLo+0.15,fract(aSeed*7.13+0.37)));   // two populations, like the app\n  float amt=sqrt(max(1.0-v,0.0))*(s1-s2)*uPGain*react;"),
("uniform vec2 uRes,uP,uN; uniform float uRf,uDPR,uPtScale,uHead,uPScale,uPEmit,uPGain,uPRows;\nuniform sampler2D uPulse;",
 "uniform vec2 uRes,uP,uN; uniform float uRf,uDPR,uPtScale,uHead,uPScale,uPEmit,uPGain,uPRows,uReactLo;\nuniform sampler2D uPulse;"),
("  const part = mkProg(gl, PART_VS, PART_FS, ['uRes','uP','uN','uRf','uDPR','uPtScale','uHead','uPScale','uPEmit','uPGain','uPRows','uPulse','uPtGain','uLerp']);",
 "  const part = mkProg(gl, PART_VS, PART_FS, ['uRes','uP','uN','uRf','uDPR','uPtScale','uHead','uPScale','uPEmit','uPGain','uPRows','uPulse','uPtGain','uLerp','uReactLo']);"),
("    gl.uniform1f(part.u.uLerp, clamp(simAcc*cfg.simHz,0,1));",
 "    gl.uniform1f(part.u.uLerp, clamp(simAcc*cfg.simHz,0,1)); gl.uniform1f(part.u.uReactLo, 1.0-clamp(cfg.reactiveFrac,0.05,1.0));"),
]
def patch(src):
    for a, b in PATCHES:
        assert src.count(a) == 1, ("field patch anchor", a[:70], src.count(a))
        src = src.replace(a, b)
    return src
if __name__ == "__main__":
    import sys
    p = sys.argv[1]; src = open(p).read(); out = patch(src); open(p, "w").write(out); print("patched", p)
