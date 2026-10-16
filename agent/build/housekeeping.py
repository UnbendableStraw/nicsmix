# ---------- housekeeping: what eats /data and RAM, and the safe ways to free it ----------
def housekeeping():
    out = {"journal_bytes": 0, "coredumps": [], "coredump_bytes": 0, "top": [], "data": {}, "tmp": []}
    try:
        m = re.search(r"take up ([\d.]+)([KMG])", _sh("journalctl --disk-usage", 10))
        if m: out["journal_bytes"] = int(float(m.group(1)) * {"K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)])
    except Exception: pass
    for d in ("/data/tmp/coredump", "/var/lib/systemd/coredump"):
        try:
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f); st = os.stat(p)
                out["coredumps"].append({"file": f, "bytes": st.st_size, "mtime": int(st.st_mtime), "path": p}); out["coredump_bytes"] += st.st_size
        except Exception: pass
    try:
        for line in _sh("ps -eo rss,etime,comm --sort=-rss", 10).splitlines()[1:8]:
            parts = line.split(None, 2)
            if len(parts) == 3: out["top"].append({"rss_kb": int(parts[0]), "elapsed": parts[1], "name": parts[2]})
    except Exception: pass
    try:
        sv = os.statvfs("/data"); out["data"] = {"total": sv.f_blocks * sv.f_frsize, "free": sv.f_bavail * sv.f_frsize}
        for line in _sh("du -xsk /data/* 2>/dev/null | sort -rn | head -8", 20).splitlines():
            k, p = line.split(None, 1); out["tmp"].append({"path": p, "bytes": int(k) * 1024})
    except Exception: pass
    return out

def housekeeping_action(action, name=None):
    if action == "vacuum_journal":
        return {"ok": True, "out": _sh("journalctl --vacuum-size=32M 2>&1 | tail -2", 60)}
    if action == "clear_coredumps":
        n = 0
        for d in ("/data/tmp/coredump", "/var/lib/systemd/coredump"):
            try:
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f)); n += 1
            except Exception: pass
        return {"ok": True, "removed": n}
    if action == "restart_service":
        allowed = ("airplay2source", "spotifysource", "tidalsource", "syng-agent", "pycalibrator", "cellbroker", "ledmanager")
        if name not in allowed: return {"ok": False, "error": "not allowed: %s (one of %s)" % (name, ", ".join(allowed))}
        return {"ok": True, "out": _sh("systemctl restart %s; sleep 1; systemctl is-active %s" % (name, name), 30).strip()}
    return {"ok": False, "error": "unknown action"}
