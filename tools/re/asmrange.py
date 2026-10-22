#!/usr/bin/env python3
"""Print disassembly for [lo,hi) from SyngSpace.asm, resolving adrp+ldr/add pairs to selector names,
cstrings and float constants."""
import sys, re, struct
sys.path.insert(0, '/home/claude/re/tools')
from swiftmeta import Macho

m = Macho('/home/claude/re/SyngSpace')
lo = int(sys.argv[1], 16); hi = int(sys.argv[2], 16)
lines = []
with open('/home/claude/re/SyngSpace.asm', errors='replace') as f:
    for ln in f:
        mm = re.match(r'^([0-9a-f]+):', ln)
        if not mm: continue
        a = int(mm.group(1), 16)
        if lo <= a < hi: lines.append((a, ln.rstrip()))
        elif a >= hi: break

def selref(addr):
    try:
        p = struct.unpack_from('<Q', m.d, m.va2off(addr))[0] & 0xffffffffffff
        return m.cstr(p)
    except Exception:
        return None
def cstr(addr):
    try:
        s = m.cstr(addr)
        return s if s.isprintable() and len(s) < 80 else None
    except Exception: return None
def fconst(addr):
    try:
        o = m.va2off(addr)
        f = struct.unpack_from('<f', m.d, o)[0]; d = struct.unpack_from('<d', m.d, o)[0]
        return 'f32=%g f64=%g' % (f, d)
    except Exception: return None

pages = {}
for a, ln in lines:
    mm = re.search(r'adrp\s+(x\d+), (0x[0-9a-f]+)', ln)
    if mm: pages[mm.group(1)] = int(mm.group(2), 16)
    note = ''
    mm = re.search(r'ldr\s+([xwqsd]\d+), \[(x\d+), #(0x[0-9a-f]+|\d+)\]', ln)
    if mm and mm.group(2) in pages:
        addr = pages[mm.group(2)] + int(mm.group(3), 0)
        s = selref(addr)
        if s: note = '  ; SEL/ref %s' % s
        else:
            c = fconst(addr)
            if c: note = '  ; @%x %s' % (addr, c)
    mm = re.search(r'add\s+(x\d+), (x\d+), #(0x[0-9a-f]+|\d+)$', ln.split(';')[0].strip())
    if mm and mm.group(2) in pages:
        addr = pages[mm.group(2)] + int(mm.group(3), 0)
        c = cstr(addr)
        if c: note = '  ; str "%s"' % c
        else:
            c = fconst(addr)
            if c: note = '  ; @%x %s' % (addr, c)
    print(ln[:110] + note)
