#!/usr/bin/env python3
"""syng-agent-b47.py = b46 + firmware write loop: an unbuffered write to the block device may be short (seen on a
busy 2.2.1 cell: the read-back check caught it and refused to switch slots) — write each chunk until it is all on
the device, and log the retry."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b46.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)
rep('''                    if written + len(chunk) > cap: raise RuntimeError("image larger than the slot (%d bytes)" % cap)
                    dst.write(chunk); h.update(chunk); written += len(chunk)''',
    '''                    if written + len(chunk) > cap: raise RuntimeError("image larger than the slot (%d bytes)" % cap)
                    mv = memoryview(chunk); off = 0
                    while off < len(mv):                       # a raw write can be short on a busy cell
                        n = dst.write(mv[off:])
                        if not n: raise RuntimeError("write to %s returned 0 at byte %d" % (dev, written + off))
                        if n < len(mv) - off: shorts[0] += 1
                        off += n
                    h.update(chunk); written += len(chunk)''')
rep('''            h = hashlib.sha256(); written = 0; cap = ROOTFS_BLOCKS_KIB * 1024; gz_size = os.path.getsize(FW_IMG)''',
    '''            h = hashlib.sha256(); written = 0; cap = ROOTFS_BLOCKS_KIB * 1024; gz_size = os.path.getsize(FW_IMG); shorts = [0]''')
rep('''            want = h.hexdigest(); fw_log("wrote %.0f MB, verifying…" % (written / 1e6))''',
    '''            want = h.hexdigest(); fw_log("wrote %.0f MB%s, verifying…" % (written / 1e6, (" (%d short writes completed)" % shorts[0]) if shorts[0] else ""))''')
s = s.replace('"syng-agent (b46)"', '"syng-agent (b47)"').replace("syng-agent (b46) on", "syng-agent (b47) on").replace('AGENT_BUILD = "b46"', 'AGENT_BUILD = "b47"')
out = os.path.join(here, "syng-agent-b47.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
