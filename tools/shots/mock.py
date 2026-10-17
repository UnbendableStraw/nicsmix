#!/usr/bin/env python3
"""Serve the agent's own pages against canned data, for screenshots and UI work.

    python3 tools/shots/mock.py 8099      # then open http://127.0.0.1:8099/ and /setup

The pages come straight out of agent/syng-agent.py (PAGE and SETUP_PAGE), so what you see is what a
cell serves; every /api/... GET is answered from tools/shots/demo-data/*.json (a real room's responses
with the serials, keys and network names replaced). POSTs are accepted and discarded.
"""
import json, os, sys, re
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
AGENT = os.path.join(ROOT, "agent", "syng-agent.py")
DATA = os.path.join(HERE, "demo-data")

# /api/<path> -> demo-data/<file>.json   (longest prefix wins)
FX = {"/api/scenes": "scenes", "/api/fleet/live": "fleet_live", "/api/cloudblock/all": "cloudblock_all",
      "/api/housekeeping": "housekeeping", "/api/state": "state", "/api/stats": "stats", "/api/led": "led",
      "/api/viz/frame": "viz_frame", "/api/whoami": "whoami", "/api/room/doc": "room_doc", "/api/room": "room",
      "/api/discover": "discover", "/api/wifi": "wifi", "/api/calibration/status": "calibration_status",
      "/api/net": "net", "/api/link": "link", "/api/firmware": "firmware"}

def page(which):
    src = open(AGENT).read()
    key = 'SETUP_PAGE = r"""' if which == "setup" else 'PAGE = r"""'
    a = src.index(key) + len(key)
    return src[a:src.index('"""', a)].encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"): return self._send(page("dash"), "text/html")
        if p.startswith("/setup"): return self._send(page("setup"), "text/html")
        if p.startswith("/api/viz/stream") or p.startswith("/fonts/"):
            self.send_response(404); self.end_headers(); return
        for k in sorted(FX, key=len, reverse=True):
            if p == k or p.startswith(k + "/") or p.startswith(k + "?"):
                f = os.path.join(DATA, FX[k] + ".json")
                if os.path.exists(f): return self._send(open(f, "rb").read(), "application/json")
        self._send(json.dumps({"error": "no demo data for " + p}).encode(), "application/json")
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        self._send(json.dumps({"ok": True}).encode(), "application/json")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print("agent pages on http://127.0.0.1:%d/  and  /setup" % port)
    HTTPServer(("127.0.0.1", port), H).serve_forever()
