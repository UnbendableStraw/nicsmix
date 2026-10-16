#!/usr/bin/env python3
"""Minimal Assets.car extractor (BOM + CSI). Usage: carx.py Assets.car outdir [--list]"""
import sys, os, struct, zlib, subprocess, tempfile
LZFSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lzfse_src', 'build', 'bin', 'lzfse')

def lzfse_decode(buf):
    with tempfile.NamedTemporaryFile(delete=False) as fi:
        fi.write(buf); fi.flush()
        fo = fi.name + '.out'
    subprocess.run([LZFSE, '-decode', '-i', fi.name, '-o', fo], check=True)
    out = open(fo, 'rb').read(); os.unlink(fi.name); os.unlink(fo)
    return out

class BOM:
    def __init__(self, data):
        self.d = data
        magic, ver, nblocks, ioff, ilen, voff, vlen = struct.unpack_from('>8sIIIIII', data, 0)
        assert magic == b'BOMStore'
        n = struct.unpack_from('>I', data, ioff)[0]
        self.blocks = [struct.unpack_from('>II', data, ioff + 4 + 8*i) for i in range(n)]
        nv = struct.unpack_from('>I', data, voff)[0]
        p = voff + 4
        self.vars = {}
        for i in range(nv):
            idx, ln = struct.unpack_from('>IB', data, p); p += 5
            name = data[p:p+ln].decode(); p += ln
            self.vars[name] = idx
    def block(self, idx):
        a, l = self.blocks[idx]
        return self.d[a:a+l]
    def tree(self, name):
        """yield (key_bytes, value_bytes) for a tree variable"""
        t = self.block(self.vars[name])
        assert t[:4] == b'tree', t[:4]
        ver, child, bsize, count = struct.unpack_from('>IIII', t, 4)
        return list(self._paths(child))
    def _paths(self, idx):
        b = self.block(idx)
        isleaf, count, fwd, bwd = struct.unpack_from('>HHII', b, 0)
        ents = [struct.unpack_from('>II', b, 12 + 8*i) for i in range(count)]
        if not isleaf:
            # descend to first child and then follow forward links
            yield from self._paths(ents[0][0])
            return
        while True:
            for vi, ki in ents:
                yield self.block(ki), self.block(vi)
            if fwd == 0:
                return
            b = self.block(fwd)
            isleaf, count, fwd, bwd = struct.unpack_from('>HHII', b, 0)
            ents = [struct.unpack_from('>II', b, 12 + 8*i) for i in range(count)]

ATTR_NAMES = {1:'Element',2:'Part',3:'Size',4:'Direction',5:'placeholder',6:'Value',7:'Appearance',8:'Dimension1',9:'Dimension2',10:'State',11:'Layer',12:'Scale',13:'Localization',14:'PresentationState',15:'Idiom',16:'Subtype',17:'Identifier',18:'PreviousValue',19:'PreviousState',20:'SizeClassHorizontal',21:'SizeClassVertical',22:'MemoryClass',23:'GraphicsClass',24:'DisplayGamut',25:'DeploymentTarget',26:'Glyph Weight', 27:'Glyph Size'}

def fourcc(v):
    return struct.pack('<I', v)[::-1].decode('latin1')

def parse_csi(v):
    if v[:4] != b'ISTC':
        return None
    (magic, ver, flags, w, h, scale, pixfmt, cspace, modtime, layout, zero) = struct.unpack_from('<4sIIIIIIIIHH', v, 0)
    name = v[0x28:0x28+128].split(b'\0')[0].decode(errors='replace')
    tlvlen, unk, zero2, rlen = struct.unpack_from('<IIII', v, 0xa8)
    p = 0xb8
    tlv = {}
    end = p + tlvlen
    while p + 8 <= end:
        t, l = struct.unpack_from('<II', v, p); p += 8
        tlv[t] = v[p:p+l]; p += l
    payload = v[end:end+rlen]
    return dict(w=w, h=h, scale=scale, pixfmt=fourcc(pixfmt), layout=layout, name=name, tlv=tlv, payload=payload, flags=flags)

def decode_payload(csi):
    pl = csi['payload']
    if len(pl) < 4: return None, 'empty'
    mg = pl[:4]
    if mg == b'MLEC':  # CELM
        ver, ctype, rawlen = struct.unpack_from('<III', pl, 4)
        data = pl[16:]
        if ctype == 0: return data, 'raw'
        if ctype == 2:
            try: return zlib.decompress(data), 'zlib'
            except Exception as e: return None, f'zlib-fail {e}'
        if ctype in (4, 5):
            try:
                if data[:4] == b'KCBC':
                    out = b''; p = 0
                    while p + 20 <= len(data):
                        rows, clen = struct.unpack_from('<II', data, p + 12)
                        out += lzfse_decode(data[p+20:p+20+clen]); p += 20 + clen
                    return out, 'lzfse-chunked'
                return lzfse_decode(data), 'lzfse'
            except Exception as e: return None, f'lzfse-fail {e}'
        if ctype == 3:
            try: return lzfse_decode(data), 'lzvn'
            except Exception as e: return None, f'lzvn-fail {e}'
        if ctype == 10 or ctype == 11:
            return data, f'deepmap{ctype}'
        return None, f'celm-type-{ctype}'
    if mg == b'DWAR': return pl, 'rawdata'
    return pl, 'unknown-' + mg.hex()

def write_png(path, w, h, rgba):
    import zlib as z
    raw = b''.join(b'\0' + rgba[y*w*4:(y+1)*w*4] for y in range(h))
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', z.crc32(t + d) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', z.compress(raw)) + chunk(b'IEND', b'')
    open(path, 'wb').write(png)

def main():
    car, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    bom = BOM(open(car, 'rb').read())
    print('vars:', list(bom.vars))
    # key format
    kf = bom.block(bom.vars['KEYFORMAT'])
    nattr = struct.unpack_from('<I', kf, 8)[0]
    attrs = struct.unpack_from('<%dI' % nattr, kf, 12)
    attr_names = [ATTR_NAMES.get(a, str(a)) for a in attrs]
    # facet names: identifier -> name
    names = {}
    if 'FACETKEYS' in bom.vars:
        for k, v in bom.tree('FACETKEYS'):
            name = k.split(b'\0')[0].decode(errors='replace')
            hx, hy, na = struct.unpack_from('<HHH', v, 0)
            for i in range(na):
                an, av = struct.unpack_from('<HH', v, 6 + 4*i)
                if an == 17: names[av] = name
    seen = {}
    for k, v in bom.tree('RENDITIONS'):
        vals = struct.unpack_from('<%dH' % nattr, k, 0)
        key = dict(zip(attr_names, vals))
        csi = parse_csi(v)
        if not csi: continue
        ident = key.get('Identifier')
        fname = names.get(ident, csi['name'] or f'id{ident}')
        base = f"{fname}@{key.get('Scale',1)}x" if key.get('Scale', 1) != 1 else fname
        base = base.replace('/', '_')
        n = seen.get(base, 0); seen[base] = n + 1
        if n: base += f'~{n}'
        data, how = decode_payload(csi)
        info = f"{base:50s} {csi['w']}x{csi['h']} {csi['pixfmt']} layout={csi['layout']} {how} len={len(csi['payload'])} key={ {kk:vv for kk,vv in key.items() if vv} }"
        print(info)
        if data is None: continue
        pf = csi['pixfmt']
        if pf in ('ARGB',) and len(data) >= csi['w']*csi['h']*4:
            w, h = csi['w'], csi['h']
            # BGRA premultiplied -> RGBA
            b = bytearray(data[:w*h*4])
            pass
            write_png(os.path.join(out, base + '.png'), w, h, bytes(b))
        elif pf == 'GA8 ' and len(data) >= csi['w']*csi['h']*2:
            w, h = csi['w'], csi['h']
            g = data[:w*h*2]
            rgba = bytearray(w*h*4)
            rgba[0::4] = g[0::2]; rgba[1::4] = g[0::2]; rgba[2::4] = g[0::2]; rgba[3::4] = g[1::2]
            write_png(os.path.join(out, base + '.png'), w, h, bytes(rgba))
        elif pf == 'PDF ' or pf == 'JPEG' or pf == 'HEIF' or pf == 'DATA':
            ext = {'PDF ':'pdf','JPEG':'jpg','HEIF':'heic','DATA':'bin'}[pf]
            open(os.path.join(out, base + '.' + ext), 'wb').write(data)
        else:
            open(os.path.join(out, base + f'.{pf.strip()}.raw'), 'wb').write(data)
    if 'COLORS' in bom.vars:
        kf2 = bom.tree('COLORS')
        print('COLORS entries:', len(kf2))
        for k, v in kf2:
            # key: attributes; value: color csi 'CTSI' with payload 'COLR'
            csi = parse_csi(v)
            if csi:
                pl = csi['payload']
                if pl[:4] == b'RLOC' or pl[:4] == b'COLR':
                    ver, flags, ncomp = struct.unpack_from('<III', pl, 4)
                    comps = struct.unpack_from('<%dd' % ncomp, pl, 16)
                    vals = struct.unpack_from('<%dH' % nattr, k, 0)
                    key = dict(zip(attr_names, vals))
                    nm = names.get(key.get('Identifier'), csi['name'])
                    print('COLOR', nm, key.get('Appearance'), [round(c, 4) for c in comps])

main()
