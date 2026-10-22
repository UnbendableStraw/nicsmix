#!/usr/bin/env python3
"""Minimal Swift 5 class/struct descriptor walker for an arm64 Mach-O (no deps).
Prints, per nominal type: module.name, access function, field names, and the vtable
(method kind + implementation address) so stripped methods can be mapped by position."""
import struct, sys

class Macho:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        magic, cputype, cpusub, ft, ncmds, sizeofcmds, flags, res = struct.unpack_from('<IIIIIIII', self.d, 0)
        assert magic == 0xfeedfacf
        self.segs = []; self.sects = {}
        off = 32
        for _ in range(ncmds):
            cmd, csize = struct.unpack_from('<II', self.d, off)
            if cmd == 0x19:  # LC_SEGMENT_64
                segname = self.d[off+8:off+24].rstrip(b'\0').decode()
                vmaddr, vmsize, fileoff, filesize, maxp, initp, nsects, fl = struct.unpack_from('<QQQQIIII', self.d, off+24)
                self.segs.append((segname, vmaddr, vmsize, fileoff, filesize))
                so = off + 72
                for _ in range(nsects):
                    sname = self.d[so:so+16].rstrip(b'\0').decode()
                    sgn = self.d[so+16:so+32].rstrip(b'\0').decode()
                    addr, size, soff = struct.unpack_from('<QQI', self.d, so+32)
                    self.sects[(sgn, sname)] = (addr, size, soff)
                    so += 80
            off += csize
    def va2off(self, va):
        for name, vmaddr, vmsize, fileoff, filesize in self.segs:
            if vmaddr <= va < vmaddr + vmsize:
                return va - vmaddr + fileoff
        raise ValueError('va %x' % va)
    def u32(self, va): return struct.unpack_from('<I', self.d, self.va2off(va))[0]
    def i32(self, va): return struct.unpack_from('<i', self.d, self.va2off(va))[0]
    def u16(self, va): return struct.unpack_from('<H', self.d, self.va2off(va))[0]
    def rel(self, va):
        r = self.i32(va)
        return va + r if r else 0
    def relind(self, va):  # relative pointer that may be indirect (low bit)
        r = self.i32(va)
        if r == 0: return 0
        t = va + (r & ~1)
        if r & 1:
            return struct.unpack_from('<Q', self.d, self.va2off(t))[0] & 0xffffffffffff
        return t
    def cstr(self, va):
        o = self.va2off(va); e = self.d.index(b'\0', o)
        return self.d[o:e].decode('utf-8', 'replace')

KINDS = {0: 'method', 1: 'init', 2: 'getter', 3: 'setter', 4: 'modify', 5: 'read'}

def ctx_name(m, desc):
    """Walk parent chain to build Module.Name"""
    names = []
    cur = desc
    for _ in range(8):
        if not cur: break
        flags = m.u32(cur)
        kind = flags & 0x1f
        if kind == 0:  # module
            names.append(m.cstr(m.rel(cur + 8))); break
        elif kind in (16, 17, 18):  # class, struct, enum
            names.append(m.cstr(m.rel(cur + 8)))
            cur = m.relind(cur + 4)
        elif kind == 1:  # extension
            names.append('(ext)'); cur = m.relind(cur + 4)
        elif kind == 2:  # anonymous
            cur = m.relind(cur + 4)
        else:
            names.append('?%d' % kind); break
    return '.'.join(reversed(names))

def fields(m, desc):
    fd = m.rel(desc + 16)
    if not fd: return []
    # FieldDescriptor: mangledTypeName rel, superclass rel, kind u16, fieldRecordSize u16, numFields u32, records
    kind, recsz = struct.unpack_from('<HH', m.d, m.va2off(fd + 8))
    n = m.u32(fd + 12)
    out = []
    for i in range(n):
        r = fd + 16 + i * recsz
        fl = m.u32(r)
        name = m.cstr(m.rel(r + 8)) if m.i32(r + 8) else ''
        tn = m.rel(r + 4)
        out.append(name)
    return out

def walk(m):
    addr, size, soff = m.sects[('__TEXT', '__swift5_types')]
    res = []
    for i in range(size // 4):
        p = addr + i * 4
        desc = m.rel(p)
        if not desc: continue
        flags = m.u32(desc)
        kind = flags & 0x1f
        if kind not in (16, 17, 18): continue
        try:
            name = ctx_name(m, desc)
        except Exception as ex:
            continue
        entry = {'desc': desc, 'kind': {16: 'class', 17: 'struct', 18: 'enum'}[kind], 'name': name,
                 'access': m.rel(desc + 12), 'fields': fields(m, desc), 'vtable': []}
        if kind == 16:
            tflags = flags >> 16
            # class descriptor layout: flags, parent, name, access, fields, superclass, metaNeg, metaPos, numImm, numFields, fieldOffsetVecOffset
            p2 = desc + 44
            has_vtable = tflags & 0x8000
            has_override = tflags & 0x4000
            has_resilient_super = tflags & 0x2000
            is_generic = flags & 0x80
            if is_generic:
                p2 += 8  # TypeGenericContextDescriptorHeader: instantiation cache + pattern
                np_, nr, nk, ne = struct.unpack_from('<HHHH', m.d, m.va2off(p2))
                p2 += 8 + ((np_ + 3) & ~3) + nr * 12
            if has_resilient_super: p2 += 4
            mi = (flags >> 3) & 3
            if mi == 1: p2 += 12
            elif mi == 2: p2 += 4
            if has_vtable:
                voff = m.u32(p2); vsize = m.u32(p2 + 4); p2 += 8
                if vsize > 2000: vsize = 0
                for j in range(vsize):
                    mf = m.u32(p2); impl = m.rel(p2 + 4); p2 += 8
                    entry['vtable'].append((KINDS.get(mf & 0xf, str(mf & 0xf)), 'inst' if mf & 0x10 else 'cls', impl))
        res.append(entry)
    return res

if __name__ == '__main__':
    m = Macho(sys.argv[1])
    pat = sys.argv[2] if len(sys.argv) > 2 else ''
    for e in walk(m):
        if pat and pat not in e['name']: continue
        print('%s %s desc=%x access=%x' % (e['kind'], e['name'], e['desc'], e['access']))
        print('  fields:', e['fields'])
        for k, (kind, scope, impl) in enumerate(e['vtable']):
            print('  vt[%2d] %-7s %s %x' % (k, kind, scope, impl))
