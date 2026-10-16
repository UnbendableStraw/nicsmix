            elif self.path.startswith("/api/viz/frame"): self._json(200, viz_frame())
            elif self.path.startswith("/api/viz/stream"):
                # Server-Sent Events: one frame per feed packet (~30/s); keepalives when idle; ends when the client goes away
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive"); self.end_headers()
                seen = -1; idle = 0
                while True:
                    with VIZ_COND:
                        if VIZ_SEQ[0] == seen: VIZ_COND.wait(0.5)
                        cur = VIZ_SEQ[0]
                    if cur == seen:
                        idle += 1
                        if idle % 4 == 0:
                            self.wfile.write(("data: %s\n\n" % json.dumps(viz_frame())).encode()); self.wfile.flush()
                        continue
                    idle = 0; seen = cur
                    self.wfile.write(("data: %s\n\n" % json.dumps(viz_frame())).encode()); self.wfile.flush()
