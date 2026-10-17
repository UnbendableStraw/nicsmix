"""field v38: puffed particles stop ratcheting outward. Under continuous music the float window was being
extended by every onset, so the home pull never ran and the weak field spring let repeated impulses random-walk
every reactive particle out to the reach cap (an empty field with a crowded rim). Now: the home radius is captured
once and may only creep outward, the outward impulse fades to nothing once a particle is `puffReach` beyond its
home radius, and the home pull keeps acting (gently) while floating."""
import re
def patch(s):
    def rep(old, new, count=None):
        nonlocal s
        c = s.count(old); assert c >= 1 and (count is None or c == count), (old[:70], c)
        s = s.replace(old, new); return c
    rep("puffGain: 0.005, puffBeat: 0.005, puffFloat: 0.9, puffVmax: 0.008,",
        "puffGain: 0.005, puffBeat: 0.005, puffFloat: 0.9, puffVmax: 0.008, puffReach: 0.2,")
    rep("          const imp = react*(cfg.puffGain*fl + beatK);\n",
        "          const home0 = HM[i] > 0.02 ? HM[i] : dist;   // how far past its resting radius it already is: the push fades out over puffReach, so particles float but never pile up at the rim\n"
        "          const att = Math.max(0, Math.min(1, 1 - (dist - home0)/Math.max(0.05, cfg.puffReach||0.2)));\n"
        "          const imp = react*(cfg.puffGain*fl + beatK)*att;\n")
    rep("if(fr<=0 && RT[i]<=0) HM[i]=dist; fr = Math.max(fr, cfg.puffFloat*react);",
        "if(fr<=0 && RT[i]<=0) HM[i] = HM[i] > 0.02 ? HM[i] + Math.max(-0.02, Math.min(0.02, dist-HM[i])) : dist; fr = Math.max(fr, cfg.puffFloat*react);")
    rep("      if(fr<=0 && RT[i]>0){ // drift home after the float: a gentle pull back to the radius it had before the puff\n"
        "        const d2 = Math.sqrt(nx*nx+ny*ny); if(d2>0.02){ const g = (HM[i]-d2)*0.04; vx += nx/d2*g; vy += ny/d2*g; } RT[i] -= dt; }",
        "      if(RT[i]>0 && HM[i]>0.02){ // pull back toward the radius it had before the puff: gentle while floating, firmer once the float ends\n"
        "        const d2 = Math.sqrt(nx*nx+ny*ny); if(d2>0.02){ const g = (HM[i]-d2)*(fr>0 ? 0.012 : 0.04); vx += nx/d2*g; vy += ny/d2*g; } if(fr<=0) RT[i] -= dt; }")
    rep("    debugRadius(){ let a=0; for(let i=0;i<NP;i++) a+=Math.hypot(pos2[i*2],pos2[i*2+1]); return a/NP; },",
        "    debugRadius(){ let a=0; for(let i=0;i<NP;i++) a+=Math.hypot(pos2[i*2],pos2[i*2+1]); return a/NP; },\n"
        "    debugDist(){ let inner=0, outer=0; for(let i=0;i<NP;i++){ const d=Math.hypot(pos2[i*2],pos2[i*2+1]); if(d<0.25) inner++; else if(d>0.45) outer++; } return {inner:inner/NP, outer:outer/NP}; },")
    s = re.sub(r"SyngField v3[0-9]", "SyngField v38", s)
    return s
if __name__ == "__main__":
    import sys
    p = sys.argv[1]; src = open(p).read(); out = patch(src); open(p, "w").write(out); print("patched", p)
