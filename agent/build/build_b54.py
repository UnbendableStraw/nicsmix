#!/usr/bin/env python3
"""syng-agent-b54.py = b53 + the bass-distribution watchdog runs every 5 s.

b53 corrects cellbroker's `disabledCells` on every field push and on the keeper pass, but the keeper
only comes round every 90 s — long enough to sit through a silent primary if something other than our
own push puts a cell in there (the Syng app, a cloud state apply, a snapped handle restored from the
document). This adds a cheap dedicated watchdog: one D-Bus property read every 5 s, a write only when
it disagrees with the live field."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b53.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

rep('''# ---------- sound mode: Syng's steering vs. Direct (all drivers, all cells, same level) ----------''',
    '''def bass_watch_loop():
    """A silent primary is the worst failure this firmware has, and it is one property away. Watch it."""
    time.sleep(25)
    while True:
        try: bass_sync("watch")
        except Exception: pass
        time.sleep(5)

# ---------- sound mode: Syng's steering vs. Direct (all drivers, all cells, same level) ----------''')

rep('''    threading.Thread(target=keeper_loop, daemon=True).start()''',
    '''    threading.Thread(target=keeper_loop, daemon=True).start()
    threading.Thread(target=bass_watch_loop, daemon=True).start()''')

s = s.replace('"syng-agent (b53)"', '"syng-agent (b54)"').replace("syng-agent (b53) on", "syng-agent (b54) on").replace('AGENT_BUILD = "b53"', 'AGENT_BUILD = "b54"')
out = os.path.join(here, "syng-agent-b54.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
