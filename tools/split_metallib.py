import sys, os, re, struct
path, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)
data = open(path, 'rb').read()
names = [m.group(1).decode() for m in re.finditer(rb'NAME..([A-Za-z_][A-Za-z0-9_]*)\x00', data)]
offs = [m.start() for m in re.finditer(rb'\xde\xc0\x17\x0b', data)]
for i, off in enumerate(offs):
    _, ver, boff, bsize = struct.unpack_from('<IIII', data, off)
    blob = data[off:off+boff+bsize]
    name = names[i] if i < len(names) else f'fn{i}'
    open(os.path.join(outdir, name + '.bc'), 'wb').write(blob)
    print(name, len(blob))
