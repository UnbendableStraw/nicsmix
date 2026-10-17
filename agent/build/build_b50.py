#!/usr/bin/env python3
"""syng-agent-b50.py = b49 + Direct sound mode no longer disables bass distribution: with BassDistribution off the
primary's own output is exactly zero (its secondaries keep playing), which silenced whichever cell is primary. Direct
is now: focus 0.0 on every object, room EQ off, listener-offset compensation off (equal level everywhere is the point),
bass distribution left on. Syng restores focus -1, room EQ, compensation."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b49.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
rep('''    try: room_eq(not direct); out["room_eq"] = not direct
    except Exception as e: out["room_eq_error"] = str(e)[:80]
    try:
        set_prop(ASRV, "/com/syng/AudioSource/BassDistribution", "com.syng.AudioSource.BassDistribution", "enable", dbus.Boolean(not direct, variant_level=1))
        out["bass_distribution"] = not direct
    except Exception as e: out["bass_error"] = str(e)[:80]''', '''    try: room_eq(not direct); out["room_eq"] = not direct
    except Exception as e: out["room_eq_error"] = str(e)[:80]
    # Bass distribution stays ON in both modes: switching it off zeroes the primary's own output (b50 finding).
    try:
        set_prop(ASRV, "/com/syng/AudioSource/BassDistribution", "com.syng.AudioSource.BassDistribution", "enable", dbus.Boolean(True, variant_level=1))
        out["bass_distribution"] = True
    except Exception as e: out["bass_error"] = str(e)[:80]
    # Listener-offset compensation (per-cell delay/gain towards the listener position) contradicts "equal level everywhere"
    try:
        set_prop(ASRV, LO_OBJ, LO_IF, "enable", dbus.Boolean(not direct, variant_level=1)); out["listener_compensation"] = not direct
    except Exception as e: out["listener_comp_error"] = str(e)[:80]''')
rep('''       'direct' = focus 0.0 on every object (cellbroker's fixed-focus override: 'all cells playing towards
                 outside' — every cell plays every object, omni), room correction off, bass distribution off.''',
    '''       'direct' = focus 0.0 on every object (cellbroker's fixed-focus override: 'all cells playing towards
                 outside' — every cell plays every object, omni), room correction off, listener-offset compensation
                 off. Bass distribution stays on: off, the primary's own output is zero.''')
s = s.replace('"syng-agent (b49)"', '"syng-agent (b50)"').replace("syng-agent (b49) on", "syng-agent (b50) on").replace('AGENT_BUILD = "b49"', 'AGENT_BUILD = "b50"')
out = os.path.join(here, "syng-agent-b50.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
