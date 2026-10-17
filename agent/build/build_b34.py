#!/usr/bin/env python3
"""syng-agent-b34.py = b33 + field v34 (puff physics, per-source puck glyphs, shimmer-level pulse), DOM dedupe so the
1.5 s dashboard refresh never re-renders unchanged cards (periodic jank)."""
import os, sys, re
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from field_b34_patch import patch as patch_field
s = open(os.path.join(here, "syng-agent-b33.py")).read()
tex = open(os.path.join(here, "..", "web", "textures.js")).read().strip()
assert tex.startswith("const SYNG_TEX=") and '"glyphs"' in tex and '"""' not in tex
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)
s = patch_field(s)
# textures: replace the embedded line
lines = s.split("\n"); idx = [i for i, l in enumerate(lines) if l.startswith("const SYNG_TEX=")]
assert len(idx) == 1, idx
lines[idx[0]] = tex
s = "\n".join(lines)
# sliders
rep("['reactiveFrac','Reactive particles (share)',0.05,1,0.05]", "['reactiveFrac','Reactive particles (share)',0.05,1,0.05],['puffGain','Puff strength',0,0.02,0.0005],['puffBeat','Puff on the beat',0,0.02,0.0005],['puffVmax','Puff max speed',0.001,0.02,0.0005],['puffFloat','Puff float time (s)',0,4,0.1]")
# DOM dedupe: identical innerHTML writes are skipped page-wide (the dashboard re-renders every 1.5 s)
rep("<script>(function(){const T=['auto','dark','light'];",
    "<script>(function(){try{const d=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML');Object.defineProperty(Element.prototype,'innerHTML',{configurable:true,get:d.get,set:function(v){if(this.__h===v)return;this.__h=v;d.set.call(this,v);}});}catch(e){}})();</script>\n<script>(function(){const T=['auto','dark','light'];", 2)
s = s.replace('"syng-agent (b33)"', '"syng-agent (b34)"').replace("syng-agent (b33) on", "syng-agent (b34) on").replace('AGENT_BUILD = "b33"', 'AGENT_BUILD = "b34"')
out = os.path.join(here, "syng-agent-b34.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
