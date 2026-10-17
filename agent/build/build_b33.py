#!/usr/bin/env python3
"""syng-agent-b33.py = b32 + source palettes on the field (auto, like the app), toggle in Field tweaks."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from field_b33_patch import patch as patch_field
s = open(os.path.join(here, "syng-agent-b32.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)
s = patch_field(s)
# the page: follow the active source
rep("  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);\n  FIELD.state.snap = fld.snap || null;",
    """  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);
  FIELD.state.snap = fld.snap || null;
  if(FIELD.cfg.autoPalette){ const a=activeSrc(fieldData.sources||{}); const id=(a||'').toLowerCase();
    FIELD.setPalette(id.startsWith('spotify')?'spotify':id.startsWith('tidal')?'tidal':id.includes('hdmi')?'hdmi':id.includes('pd')||id.includes('pure')?'puredata':id.includes('usb')?'usb':'airplay'); }""")
rep("['snapToCell','Snap the puck to a speaker']];", "['snapToCell','Snap the puck to a speaker'],['autoPalette','Colours follow the source']];")
s = s.replace('"syng-agent (b32)"', '"syng-agent (b33)"').replace("syng-agent (b32) on", "syng-agent (b33) on").replace('AGENT_BUILD = "b32"', 'AGENT_BUILD = "b33"')
out = os.path.join(here, "syng-agent-b33.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
