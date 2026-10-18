#!/usr/bin/env python3
"""syng-agent-b59.py = b58 + the web sound field stops washing out (the same change as the apps).

1. The palette made from the speakers' LED colour keeps its hue and saturation but holds each role to the perceived
   brightness of the same role in the blue palette (x1.15). HSV value is not brightness: yellow and green at blue's
   value were two to three times as bright and drowned the particles.
2. The classic (all-around) disc gets depth — bright at the puck, deeper toward the rim — instead of a flat fill,
   so the particles have something to show against."""
import json, os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b58.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
for old, new in json.load(open(os.path.join(here, "b59_webpairs.json"))):
    rep(old, new)
s = s.replace('"syng-agent (b58)"', '"syng-agent (b59)"').replace("syng-agent (b58) on", "syng-agent (b59) on").replace('AGENT_BUILD = "b58"', 'AGENT_BUILD = "b59"')
out = os.path.join(here, "syng-agent-b59.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
