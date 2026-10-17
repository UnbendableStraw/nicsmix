#!/usr/bin/env python3
"""syng-agent-b38.py = b37 + field v38 (puffed particles no longer ratchet out to the rim under continuous music)."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from field_b38_patch import patch as patch_field
s = open(os.path.join(here, "syng-agent-b37.py")).read()
s = patch_field(s)
s = s.replace('"syng-agent (b37)"', '"syng-agent (b38)"').replace("syng-agent (b37) on", "syng-agent (b38) on").replace('AGENT_BUILD = "b37"', 'AGENT_BUILD = "b38"')
out = os.path.join(here, "syng-agent-b38.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True); print("wrote", out, len(s))
