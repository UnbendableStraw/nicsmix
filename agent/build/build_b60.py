#!/usr/bin/env python3
"""syng-agent-b60.py = b59 + two new LED modes on offer: bloom and shimmer (syng-led-reactive).

bloom   — brightness on the beat (kicks bloom and fall away, timed to the tempo when it locks), colour from the treble.
shimmer — brightness from the treble (sparks on hi-hat / cymbal onsets), colour on the beat (small steps, an accent
          moves on).
Both use the daemon's new spectral onset detector (VizBands); the agent only lists them. They go right after groove
so the widgets, which show the first seven, include them."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b59.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
rep('''REACTIVE_MODES = ["pulse", "party", "flow", "groove", "spectrum", "vu", "beat"]''',
    '''REACTIVE_MODES = ["pulse", "party", "flow", "groove", "bloom", "shimmer", "spectrum", "vu", "beat"]''')
s = s.replace('"syng-agent (b59)"', '"syng-agent (b60)"').replace("syng-agent (b59) on", "syng-agent (b60) on").replace('AGENT_BUILD = "b59"', 'AGENT_BUILD = "b60"')
out = os.path.join(here, "syng-agent-b60.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
