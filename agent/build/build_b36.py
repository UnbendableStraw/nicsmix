#!/usr/bin/env python3
"""syng-agent-b36.py = b35 + secondaries show the room's now-playing and relay playback controls to the primary;
puff interpolates across spectrum bands (smooth outline)."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from field_b36_patch import patch as patch_field
s = open(os.path.join(here, "syng-agent-b35.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)
s = patch_field(s)

RELAY = r'''
# ---------- secondaries: the primary owns every source, so playback state and controls go through it ----------
RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener")
_relay_cache = {"t": 0, "ip": None}
def relay_ip():
    """The primary's agent address when this cell is a secondary (cached 10 s), else None."""
    now = time.time()
    if now - _relay_cache["t"] < 10: return _relay_cache["ip"]
    ip = None
    try:
        lp = local_cell_props()
        if str(lp.get("role", "")).lower() == "secondary":
            ip = primary_ip_of_room()
    except Exception: pass
    _relay_cache.update({"t": now, "ip": ip}); return ip
def nowplaying():
    return {"sources": live_sources(), "sleep": dict(SLEEP, now=time.time()), "settings": dict(SETTINGS), "field": dict(FIELD)}
'''
rep("def cloudblock_all():", RELAY + "\ndef cloudblock_all():")
rep('''    if path.startswith("/api/fleet/live"): return fleet_live()''',
    '''    if path.startswith("/api/fleet/live"): return fleet_live()
    if path.startswith("/api/nowplaying"): return nowplaying()''')
# collect(): merge the primary's playback view on a secondary
rep("    out.update(get_state())\n",
    '''    out.update(get_state())
    rip = relay_ip()
    if rip:
        try:
            np_ = agent_get(rip, "/api/nowplaying", timeout=2.5)
            out["sources"] = np_.get("sources") or out["sources"]; out["sleep"] = np_.get("sleep") or out.get("sleep")
            out["settings"] = np_.get("settings") or out.get("settings"); out["viaPrimary"] = rip
            if np_.get("field"): FIELD.update({k: v for k, v in np_["field"].items() if k in FIELD})
        except Exception as e:
            out["viaPrimary"] = None; out["viaPrimaryError"] = str(e)[:80]
''')
# POST relay
rep('''            p = self.path
            grp = (not body.get("_local")) and body.get("scope") == "group"
''', '''            p = self.path
            grp = (not body.get("_local")) and body.get("scope") == "group"
            rip = relay_ip() if (not body.get("_local") and any(p.startswith(x) for x in RELAY_PATHS)) else None
            if rip:
                try:
                    r = agent_post(rip, p, dict(body, _local=True), timeout=8); log("RELAY %s -> %s" % (p, rip)); self._json(200, dict(r, relayed=rip) if isinstance(r, dict) else {"ok": True, "relayed": rip}); return
                except Exception as e:
                    self._json(502, {"error": "primary %s unreachable: %s" % (rip, e)}); return
''')
# artwork relay
rep('''            elif self.path.startswith("/api/artwork"):
                q = parse_qs(urlparse(self.path).query); url = artwork_url((q.get("source") or ["airplay2"])[0])''',
    '''            elif self.path.startswith("/api/artwork"):
                q = parse_qs(urlparse(self.path).query); rip = relay_ip()
                url = ("http://%s:%d%s" % (rip, PORT, self.path)) if rip else artwork_url((q.get("source") or ["airplay2"])[0])''')
# page: say so in the playback header
rep("  const badge=A?[...new Set([A.badging,A.mixType,A.layout].filter(x=>x&&x!=='none'&&x!=='unknown'))].join(' · '):''; $('#mixbadge').textContent=badge;",
    "  const badge=A?[...new Set([A.badging,A.mixType,A.layout].filter(x=>x&&x!=='none'&&x!=='unknown'))].join(' · '):''; $('#mixbadge').textContent=(window.__viaPrimary?'via primary '+window.__viaPrimary+(badge?' · ':''):'')+badge;")
rep("  renderControls(st.sources);\n", "  window.__viaPrimary=st.viaPrimary||null;\n  renderControls(st.sources);\n")
s = s.replace('"syng-agent (b35)"', '"syng-agent (b36)"').replace("syng-agent (b35) on", "syng-agent (b36) on").replace('AGENT_BUILD = "b35"', 'AGENT_BUILD = "b36"')
out = os.path.join(here, "syng-agent-b36.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
