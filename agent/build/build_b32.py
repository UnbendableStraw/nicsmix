#!/usr/bin/env python3
"""syng-agent-b32.py = b31 + field v32 (half-res background pass, two particle populations, DPR cap, tweak sliders),
LED 'groove' mode in the LED card."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from field_b32_patch import patch as patch_field
s = open(os.path.join(here, "syng-agent-b31.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)
s = patch_field(s)
# tweak sliders for the new knobs
rep("['pointScale','Sparkle size',0.3,3,0.05]", "['pointScale','Sparkle size',0.3,3,0.05],['reactiveFrac','Reactive particles (share)',0.05,1,0.05],['sceneScale','Background resolution',0.25,1,0.05]")
# LED modes
rep('REACTIVE_MODES = ["pulse", "party", "flow", "spectrum", "vu", "beat"]', 'REACTIVE_MODES = ["pulse", "party", "flow", "groove", "spectrum", "vu", "beat"]')
s = s.replace('"syng-agent (b31)"', '"syng-agent (b32)"').replace("syng-agent (b31) on", "syng-agent (b32) on").replace('AGENT_BUILD = "b31"', 'AGENT_BUILD = "b32"')
out = os.path.join(here, "syng-agent-b32.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
