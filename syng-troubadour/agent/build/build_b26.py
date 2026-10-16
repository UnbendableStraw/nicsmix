#!/usr/bin/env python3
"""syng-agent-b26.py = b25 + localcloud (room authority, discovery, join/leave, layout, calibration, USB link, Wi-Fi) + /setup page."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b25.py")).read()
lc = open(os.path.join(here, "localcloud.py")).read()
setup = open(os.path.join(here, "setup.html")).read()
assert '"""' not in setup
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)
# module before the page
rep('PAGE = r"""<!doctype html>', lc + '\n\nPAGE = r"""<!doctype html>')
# setup page after PAGE (before the handler)
rep('class H(BaseHTTPRequestHandler):', 'SETUP_PAGE = r"""' + setup + '"""\n\nclass H(BaseHTTPRequestHandler):')
# routes
rep('            elif self.path in ("/", "/index.html"): self._send(200, PAGE, "text/html; charset=utf-8")\n            else: self._send(404, "not found", "text/plain")',
    '            elif self.path in ("/", "/index.html"): self._send(200, PAGE, "text/html; charset=utf-8")\n'
    '            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")\n'
    '            else:\n'
    '                r = lc_get(self.path)\n'
    '                if r is None: self._send(404, "not found", "text/plain")\n'
    '                else: self._json(200, r)')
rep('            else:\n                self._send(404, "not found", "text/plain")\n        except Exception:\n            log("POST %s ERROR',
    '            else:\n'
    '                r = lc_post(p, body)\n'
    '                if r is None: self._send(404, "not found", "text/plain")\n'
    '                else:\n'
    '                    if p.startswith("/api/room") or p.startswith("/api/calibration") or p.startswith("/api/wifi"): log("%s %s -> %s" % (p, json.dumps(body)[:160], json.dumps(r)[:200]))\n'
    '                    self._json(200, r)\n'
    '        except Exception:\n            log("POST %s ERROR')
# CORS preflight (so another cell's setup page can talk to us directly too)
rep('    def do_POST(self):', '    def do_OPTIONS(self):\n        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*")\n        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()\n    def do_POST(self):')
# header link to /setup
rep('<header><div class=mark>SYNG <b>·</b> LAN</div>', '<header><div class=mark>SYNG <b>·</b> LAN <a href="/setup" style="margin-left:14px;letter-spacing:0;font-weight:500;color:var(--muted);text-decoration:none">setup ›</a></div>')
# startup
rep('    threading.Thread(target=viz_web_loop, daemon=True).start()\n', '    threading.Thread(target=viz_web_loop, daemon=True).start()\n    lc_start()\n')
s = s.replace('"syng-agent (b25 field)"', '"syng-agent (b26 rooms)"').replace("syng-agent (b24 field) on", "syng-agent (b26 rooms) on")
out = os.path.join(here, "syng-agent-b26.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
