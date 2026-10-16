VIZ_COND = threading.Condition()
VIZ_SEQ = [0]
_vz = {"peak": -40.0, "ema": None, "pk": None, "beat": 0.0, "e": 0.0, "b": 0.0, "fps": 30.0, "last": 0.0}

def viz_web_loop():
    """Receive the cell's VisualizationDataBroadcast (msgpack over UDP, 88 dB bins) and turn it
    into what the field needs: per-bin *level* (relative to a slowly decaying per-bin peak, 30 dB
    window), per-bin *flux* (onset above a short running average), energy (RMS relative to a
    decaying peak, 20 dB window), bass level and a fast beat signal. Wakes the SSE streamers."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", VIZ_WEB_PORT))
    except Exception as e:
        log("VIZ web bind failed: %s" % e); return
    relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    B = 24
    while True:
        try:
            data, _ = s.recvfrom(65535)
            try: relay.sendto(data, ("127.0.0.1", VIZ_LED_PORT))   # feed the local LED daemon
            except Exception: pass
            obj, _ = _mpd(data, 0)
            if not isinstance(obj, dict): continue
            spec = (obj.get("stems") or [[]])[0] or []
            rms = obj.get("stemsRms") or []
            n = len(spec)
            if not n: continue
            now = time.time()
            if _vz["last"]:
                dt = now - _vz["last"]
                if 0.005 < dt < 0.5: _vz["fps"] += 0.1 * (1.0 / dt - _vz["fps"])
            _vz["last"] = now
            db = [x / 100.0 for x in spec]
            if _vz["ema"] is None or len(_vz["ema"]) != n:
                _vz["ema"] = list(db); _vz["pk"] = list(db)
            ema = _vz["ema"]; pk = _vz["pk"]
            level = [0.0] * n; flux = [0.0] * n
            for i in range(n):
                d = db[i]
                pk[i] = d if d > pk[i] else pk[i] - 0.04           # ~1.2 dB/s decay at 30 fps
                level[i] = max(0.0, min(1.0, (d - (pk[i] - 30.0)) / 30.0))
                flux[i] = max(0.0, min(1.0, (d - ema[i]) / 9.0))
                ema[i] += 0.10 * (d - ema[i])
            rms_db = (max(rms) / 100.0) if rms else (sum(db) / n)
            _vz["peak"] = rms_db if rms_db > _vz["peak"] else _vz["peak"] - 0.03
            energy = max(0.0, min(1.0, (rms_db - (_vz["peak"] - 20.0)) / 20.0))
            nb = max(2, n // 8)
            bass = sum(level[1:nb]) / (nb - 1)
            beat = min(1.0, sum(flux[1:nb]) / (nb - 1) * 1.6)
            _vz["e"] += (0.6 if energy > _vz["e"] else 0.2) * (energy - _vz["e"])
            _vz["b"] += (0.6 if bass > _vz["b"] else 0.25) * (bass - _vz["b"])
            _vz["beat"] = beat if beat > _vz["beat"] else _vz["beat"] * 0.75
            step = n / float(B); sp = []; fx = []
            for k in range(B):
                a = int(k * step); b2 = max(a + 1, int((k + 1) * step))
                sp.append(round(sum(level[a:b2]) / (b2 - a), 3)); fx.append(round(max(flux[a:b2]), 3))
            with VIZ_COND:
                VIZ_SEQ[0] += 1
                LAST_VIZ.update({"energy": round(_vz["e"], 3), "bass": round(_vz["b"], 3), "beat": round(_vz["beat"], 3),
                                 "spec": sp, "flux": fx, "fps": round(_vz["fps"], 1), "src": obj.get("sourceId"), "t": now, "seq": VIZ_SEQ[0]})
                VIZ_COND.notify_all()
        except Exception:
            time.sleep(0.05)

def viz_frame():
    if time.time() - LAST_VIZ.get("t", 0) < 0.6:
        d = dict(LAST_VIZ); d["live"] = True; return d
    return {"energy": 0.0, "bass": 0.0, "beat": 0.0, "spec": [], "flux": [], "src": None, "live": False, "seq": VIZ_SEQ[0]}

