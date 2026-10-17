"""Field v33: per-source palettes (the app's Auras_* colour generators) with a smooth crossfade."""
PATCHES = [
("  showVolume: true, showRings: true, classicParticles: true,",
 """  showVolume: true, showRings: true, classicParticles: true,
  autoPalette: true, palette: 'airplay',   // the field takes the active source's colours, like the app's Auras_* palettes
  palettes: {   // decoded from the app's 16x16x16 colour histograms (_re/auras): deep / mid / light / white / blob A / blob B / room / room edge
    airplay: { cyanDeep:[0.0,0.36,0.54], cyan:[0.14,0.64,0.84], cyanLight:[0.54,0.84,0.95], white:[0.90,0.98,1.0], blue:[0.10,0.42,0.86], teal:[0.08,0.60,0.70], room:[0.10,0.26,0.33], roomEdge:[0.07,0.19,0.23] },
    spotify: { cyanDeep:[0.0,0.30,0.18], cyan:[0.13,0.56,0.33], cyanLight:[0.55,0.86,0.66], white:[0.92,1.0,0.95], blue:[0.08,0.46,0.30], teal:[0.12,0.62,0.40], room:[0.08,0.22,0.15], roomEdge:[0.06,0.16,0.11] },
    tidal:   { cyanDeep:[0.0,0.33,0.33], cyan:[0.10,0.58,0.58], cyanLight:[0.55,0.86,0.86], white:[0.90,1.0,1.0], blue:[0.05,0.45,0.50], teal:[0.08,0.60,0.58], room:[0.08,0.24,0.24], roomEdge:[0.06,0.17,0.17] },
    hdmi:    { cyanDeep:[0.10,0.08,0.40], cyan:[0.30,0.28,0.80], cyanLight:[0.65,0.62,0.95], white:[0.95,0.93,1.0], blue:[0.20,0.20,0.75], teal:[0.35,0.25,0.70], room:[0.12,0.10,0.30], roomEdge:[0.08,0.07,0.22] },
    puredata:{ cyanDeep:[0.45,0.17,0.08], cyan:[0.93,0.48,0.33], cyanLight:[1.0,0.78,0.62], white:[1.0,0.96,0.92], blue:[0.85,0.35,0.20], teal:[0.95,0.55,0.30], room:[0.30,0.16,0.10], roomEdge:[0.22,0.11,0.07] },
    usb:     { cyanDeep:[0.25,0.25,0.26], cyan:[0.55,0.55,0.56], cyanLight:[0.80,0.80,0.82], white:[0.98,0.98,1.0], blue:[0.40,0.42,0.48], teal:[0.50,0.52,0.55], room:[0.20,0.21,0.22], roomEdge:[0.14,0.15,0.16] },
  },"""),
# crossfade the live colours toward the palette every frame
("    st.volume += (st.volT-st.volume)*Math.min(1,dt*14);",
 """    st.volume += (st.volT-st.volume)*Math.min(1,dt*14);
    { const tp = cfg.palettes[cfg.palette] || cfg.palettes.airplay; const k = Math.min(1, dt*1.6);
      for(const key in tp){ const a = cfg.colors[key], b = tp[key]; if(!a) continue; for(let i=0;i<3;i++) a[i] += (b[i]-a[i])*k; } }"""),
("    center(){ st.puckT=[st.C[0],st.C[1]]; st.snap=null; },",
 "    center(){ st.puckT=[st.C[0],st.C[1]]; st.snap=null; },\n    setPalette(name){ if(cfg.palettes[name]) cfg.palette = name; },"),
]
def patch(src):
    for a, b in PATCHES:
        assert src.count(a) == 1, ("field patch anchor", a[:70], src.count(a))
        src = src.replace(a, b)
    return src
if __name__ == "__main__":
    import sys
    p = sys.argv[1]; src = open(p).read(); out = patch(src); open(p, "w").write(out); print("patched", p)
