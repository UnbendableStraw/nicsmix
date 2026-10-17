"""Field v36: the puff interpolates between neighbouring spectrum bands by angle (no stepped outline)."""
PATCHES = [
("          const ang = Math.atan2(-x*ny0 + y*nx0, x*nx0 + y*ny0); const bin = Math.min(23, Math.max(0, Math.floor((0.5+0.5*ang/Math.PI)*24)));\n          const imp = react*(cfg.puffGain*st.flux[bin] + beatK);",
 "          const ang = Math.atan2(-x*ny0 + y*nx0, x*nx0 + y*ny0); const ub = Math.min(23.999, Math.max(0, (0.5+0.5*ang/Math.PI)*24 - 0.5));\n          const b0 = Math.floor(ub), b1 = Math.min(23, b0+1), tb = ub-b0;   // blend the two nearest bands (plus a touch of the next ones) so the outline stays a curve\n          const fl = 0.75*(st.flux[b0]*(1-tb) + st.flux[b1]*tb) + 0.25*(st.flux[Math.max(0,b0-1)]*(1-tb) + st.flux[Math.min(23,b1+1)]*tb);\n          const imp = react*(cfg.puffGain*fl + beatK);"),
]
def patch(src):
    for a, b in PATCHES:
        assert src.count(a) == 1, ("field patch anchor", a[:70], src.count(a))
        src = src.replace(a, b)
    return src
if __name__ == "__main__":
    import sys
    p = sys.argv[1]; src = open(p).read(); out = patch(src); open(p, "w").write(out); print("patched", p)
