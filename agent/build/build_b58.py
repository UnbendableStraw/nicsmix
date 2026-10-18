#!/usr/bin/env python3
"""syng-agent-b58.py = b57 + the agent stops eating a core's worth of slices it never uses.

Measured on an idle secondary: the agent sat at ~14 % CPU, almost all of it one thread — the viz receiver,
which decodes every VisualizationDataBroadcast packet (~30/s, msgpack in pure Python, then a per-bin level /
flux / peak pass) on every cell, all day. Only /api/viz/frame and /api/viz/stream ever read the result, and
the apps and widgets only ever ask the primary. That CPU is shared with jackd, and an underrun storm is what
"a speaker plays late" looked like.

1. The receiver always relays the raw packet to the local LED daemon (that is what the LEDs need), but while
   nobody has asked for viz in the last 5 s it decodes one packet in eight, with the peak / average trackers'
   rates scaled to match — so their state stays right and a one-shot reader (the widget's /api/viz/frame)
   gets real levels, not the "everything at full scale" a freshly started tracker gives.
   The decoder gets a fast path for the feed's int16 arrays (one struct call per array: 1.8 ms -> ~0.35 ms
   per packet on the Cell), which is most of what the primary spends while an app is streaming.
2. A client that hangs up mid-reply (the app cancelling a poll, a widget timing out, a drag superseding a
   request) is no longer logged as a three-part traceback: the server ignores BrokenPipe / ConnectionReset,
   and a failed POST is logged once, without trying to write an error reply to a closed socket."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b57.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- 1. decode on demand ----------
rep('''LAST_VIZ = {"energy": 0.0, "bass": 0.0, "spec": [], "src": None, "t": 0.0}''',
    '''LAST_VIZ = {"energy": 0.0, "bass": 0.0, "spec": [], "src": None, "t": 0.0}
VIZ_WANT = [0.0]          # last time anyone read viz; the receiver only decodes while this is recent (b58)
VIZ_IDLE_AFTER = 5.0''')

rep('''            data, _ = s.recvfrom(65535)
            try: relay.sendto(data, ("127.0.0.1", VIZ_LED_PORT))   # feed the local LED daemon
            except Exception: pass
            obj, _ = _mpd(data, 0)''',
'''            data, _ = s.recvfrom(65535)
            try: relay.sendto(data, ("127.0.0.1", VIZ_LED_PORT))   # feed the local LED daemon
            except Exception: pass
            _vz["n"] = _vz.get("n", 0) + 1
            K = 1
            if time.time() - VIZ_WANT[0] > VIZ_IDLE_AFTER:
                # nobody is looking: keep the trackers alive on one packet in eight (rates scaled by 8 below)
                if _vz["n"] % 8: continue
                K = 8; _vz["last"] = 0.0
            obj, _ = _mpd(data, 0)''')
rep('''                pk[i] = d if d > pk[i] else pk[i] - 0.04           # ~1.2 dB/s decay at 30 fps''',
    '''                pk[i] = d if d > pk[i] else pk[i] - 0.04 * K       # ~1.2 dB/s decay at 30 fps''')
rep('''                ema[i] += 0.10 * (d - ema[i])''', '''                ema[i] += (1.0 - 0.9 ** K) * (d - ema[i])''')
rep('''            _vz["peak"] = rms_db if rms_db > _vz["peak"] else _vz["peak"] - 0.03''',
    '''            _vz["peak"] = rms_db if rms_db > _vz["peak"] else _vz["peak"] - 0.03 * K''')

rep('''def viz_frame():
    led = led_live_color()''',
'''def viz_frame():
    cold = time.time() - VIZ_WANT[0] > VIZ_IDLE_AFTER
    VIZ_WANT[0] = time.time()
    if cold and time.time() - LAST_VIZ.get("t", 0) >= 0.6:
        # no fresh frame (the slow idle pace, or no music): give the receiver one packet before answering
        with VIZ_COND:
            seen = VIZ_SEQ[0]; end = time.time() + 0.4
            while VIZ_SEQ[0] == seen and time.time() < end:
                VIZ_COND.wait(max(0.01, end - time.time()))
    led = led_live_color()''')

# ---------- 1b. the decoder's fast path: the feed's arrays are all int16 (5x faster on the Cell) ----------
rep('''def _mpd(b, i=0):
    c = b[i]; i += 1''',
'''_INTS16 = {}
def _ints16(b, i, n):
    """n msgpack int16s (0xd1 hi lo each) from i in one struct call; None when the array is any other shape."""
    end = i + 3 * n
    if n and b[i:end:3] == b"\\xd1" * n:
        f = _INTS16.get(n)
        if f is None: f = _INTS16[n] = struct.Struct(">" + "xh" * n).unpack_from
        return list(f(b, i)), end
    return None, i

def _mpd(b, i=0):
    c = b[i]
    if c == 0xdc:
        v, j = _ints16(b, i + 3, (b[i + 1] << 8) | b[i + 2])
        if v is not None: return v, j
    elif 0x91 <= c <= 0x9f:
        v, j = _ints16(b, i + 1, c & 0x0f)
        if v is not None: return v, j
    i += 1''')

# ---------- 2. quiet hang-ups ----------
rep('''        except Exception:
            log("POST %s ERROR %s" % (self.path, traceback.format_exc().splitlines()[-1]))
            self._json(500, {"error": traceback.format_exc().splitlines()[-1]})''',
'''        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                   # the client went away; there is nobody to answer
        except Exception:
            err = traceback.format_exc().splitlines()[-1]
            log("POST %s ERROR %s" % (self.path, err))
            try: self._json(500, {"error": err})
            except Exception: pass''')
rep('''        except Exception:
            self._json(500, {"error": traceback.format_exc().splitlines()[-1]})
    def do_OPTIONS(self):''',
'''        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            try: self._json(500, {"error": traceback.format_exc().splitlines()[-1]})
            except Exception: pass
    def do_OPTIONS(self):''')
rep('''    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()''',
'''    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout)):
                return                             # a client hanging up is not an error worth a traceback
            ThreadingHTTPServer.handle_error(self, request, client_address)
    Server(("0.0.0.0", PORT), H).serve_forever()''')

s = s.replace('"syng-agent (b57)"', '"syng-agent (b58)"').replace("syng-agent (b57) on", "syng-agent (b58) on").replace('AGENT_BUILD = "b57"', 'AGENT_BUILD = "b58"')
out = os.path.join(here, "syng-agent-b58.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
