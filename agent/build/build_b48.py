#!/usr/bin/env python3
"""syng-agent-b48.py = b47 + the slot being written is never mounted meanwhile: the status call mounts the idle slot
read-only to read its version, and the setup page polls status every 2 s during an install — on one cell that mount
landed mid-write (EXT4 errors in dmesg) and the read-back then went through the page cache of a stale mount and
failed verification. Now: slot info is cached and skipped while busy, a SLOT_LOCK serialises mounting and writing,
the target is unmounted and its buffers flushed before the write, and the page cache is dropped before read-back."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b47.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
rep('''FW_LOCK = threading.Lock()''', '''FW_LOCK = threading.Lock(); SLOT_LOCK = threading.Lock()   # SLOT_LOCK: nobody mounts a slot while it is being written''')
rep('''def fw_slot_info(part):
    """artifact / version / agent build of a rootfs slot, by mounting it read-only for a moment."""
    mp = "/mnt/slot%d" % part; info = {"part": part}
    try:''', '''def fw_slot_info(part):
    """artifact / version / agent build of a rootfs slot, by mounting it read-only for a moment."""
    mp = "/mnt/slot%d" % part; info = {"part": part}
    if FW["busy"]: return {"part": part, "artifact": None, "note": "busy"}
    if not SLOT_LOCK.acquire(timeout=0.5): return {"part": part, "artifact": None, "note": "busy"}
    try:''')
rep('''    finally:
        if part != fw_active_part(): _sh("umount %s" % mp, 20)
    return info''', '''    finally:
        if part != fw_active_part(): _sh("umount %s" % mp, 20)
        SLOT_LOCK.release()
    return info''')
rep('''    if now - _slot_cache["t"] > 60 or _slot_cache["v"] is None:
        _slot_cache["v"] = fw_slot_info(other); _slot_cache["t"] = now''', '''    if (now - _slot_cache["t"] > 60 or _slot_cache["v"] is None) and not FW["busy"]:
        _slot_cache["v"] = fw_slot_info(other); _slot_cache["t"] = now''')
rep('''            import gzip
            h = hashlib.sha256(); written = 0;''', '''            import gzip
            SLOT_LOCK.acquire()
            _sh("umount %s 2>/dev/null; umount /mnt/slot%d 2>/dev/null; blockdev --flushbufs %s" % (dev, tgt, dev), 30)
            h = hashlib.sha256(); written = 0;''')
rep('''            want = h.hexdigest(); fw_log("wrote %.0f MB%s, verifying…" % (written / 1e6, (" (%d short writes completed)" % shorts[0]) if shorts[0] else ""))
            h2 = hashlib.sha256(); left = written''', '''            want = h.hexdigest(); fw_log("wrote %.0f MB%s, verifying…" % (written / 1e6, (" (%d short writes completed)" % shorts[0]) if shorts[0] else ""))
            _sh("sync; blockdev --flushbufs %s; echo 3 > /proc/sys/vm/drop_caches" % dev, 30)   # read the device, not the page cache
            h2 = hashlib.sha256(); left = written''')
rep('''        except Exception as e:
            FW["error"] = str(e)[:200]; fw_log("FAILED: %s" % e)
        finally:
            FW["busy"] = None
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True}''', '''        except Exception as e:
            FW["error"] = str(e)[:200]; fw_log("FAILED: %s" % e)
        finally:
            try: SLOT_LOCK.release()
            except Exception: pass
            FW["busy"] = None
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True}''')
s = s.replace('"syng-agent (b47)"', '"syng-agent (b48)"').replace("syng-agent (b47) on", "syng-agent (b48) on").replace('AGENT_BUILD = "b47"', 'AGENT_BUILD = "b48"')
out = os.path.join(here, "syng-agent-b48.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
