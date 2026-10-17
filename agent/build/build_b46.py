#!/usr/bin/env python3
"""syng-agent-b46.py = b45 + room install skips members (and the primary) already running the image's artifact."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b45.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
rep('''                        st = agent_get(ip, "/api/firmware", timeout=5)
                        if not st.get("image"): FW["room"]["steps"][sn] = "no image (send first)"; continue''',
    '''                        st = agent_get(ip, "/api/firmware", timeout=5)
                        if not st.get("image"): FW["room"]["steps"][sn] = "no image (send first)"; continue
                        if st["image"].get("artifact") and st["image"]["artifact"] == (st.get("running") or {}).get("artifact"):
                            FW["room"]["steps"][sn] = "already on %s" % st["image"]["artifact"]; continue''')
rep('''                FW["room"]["steps"][doc["me"]] = "installing (this speaker, last)"
                fw_install(reboot=True)''', '''                mine = None
                try: mine = json.loads(_read(FW_META, "") or "null")
                except Exception: pass
                if mine and mine.get("artifact") == fw_version():
                    FW["room"]["steps"][doc["me"]] = "already on %s" % fw_version()
                else:
                    FW["room"]["steps"][doc["me"]] = "installing (this speaker, last)"
                    fw_install(reboot=True)''')
s = s.replace('"syng-agent (b45)"', '"syng-agent (b46)"').replace("syng-agent (b45) on", "syng-agent (b46) on").replace('AGENT_BUILD = "b45"', 'AGENT_BUILD = "b46"')
out = os.path.join(here, "syng-agent-b46.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
