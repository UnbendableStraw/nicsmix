#!/usr/bin/env python3
"""syng-agent-b61.py = b60 + /api/discover stops ARP-storming the Wi-Fi.

discover() swept every address of the local /24 with a TCP connect on each call, and the apps call it every 15 s
while open: ~250 ARP broadcasts per call, which an access point with multicast-to-unicast on copies to every
client (captured 2026-10-18: one cell sweeping the subnet four times a minute). Now:
  - answers come from a 30 s cache;
  - the full /24 sweep runs at most every 10 minutes (and on the first call);
  - in between, only the addresses already known to be cells are probed, plus mDNS (stock cells advertise
    _syng._tcp), which finds a new or moved cell without touching every host."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b60.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

rep('''def discover(timeout=1.2):
    """Cells on the LAN: a parallel sweep of every local /24 for the agent port, plus mDNS for stock cells."""
    me = whoami(); myip = me.get("ip")
    hosts = []
    for n in lan_subnets():
        hosts += [str(h) for h in n.hosts()]''',
'''_DISC = {"t": 0.0, "full_t": 0.0, "result": None, "known": set(), "lock": threading.Lock()}

def discover(timeout=1.2):
    """Cached front for _discover (b61): 30 s cache, a full /24 sweep at most every 10 min, known cells + mDNS between."""
    with _DISC["lock"]:
        now = time.time()
        if _DISC["result"] is not None and now - _DISC["t"] < 30:
            return _DISC["result"]
        full = _DISC["result"] is None or now - _DISC["full_t"] > 600
        r = _discover(timeout, None if full else sorted(_DISC["known"]))
        _DISC["t"] = time.time()
        if full: _DISC["full_t"] = _DISC["t"]
        try:
            _DISC["known"] |= {c.get("ip") for room in r.get("rooms", []) for c in room.get("cells", []) if c.get("ip")}
        except Exception:
            pass
        _DISC["result"] = r
        return r

def _discover(timeout=1.2, only=None):
    """Cells on the LAN: a parallel sweep of every local /24 for the agent port (or just `only`), plus mDNS."""
    me = whoami(); myip = me.get("ip")
    hosts = []
    if only is not None:
        hosts = [ip for ip in only if ip]
    else:
        for n in lan_subnets():
            hosts += [str(h) for h in n.hosts()]''')
s = s.replace('"syng-agent (b60)"', '"syng-agent (b61)"').replace("syng-agent (b60) on", "syng-agent (b61) on").replace('AGENT_BUILD = "b60"', 'AGENT_BUILD = "b61"')
out = os.path.join(here, "syng-agent-b61.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
