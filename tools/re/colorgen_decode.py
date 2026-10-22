import struct, zlib, sys, json
def readpng(p):
    d=open(p,'rb').read(); i=8; w=h=bd=ct=None; idat=b''
    while i<len(d):
        n,=struct.unpack('>I',d[i:i+4]); t=d[i+4:i+8]; c=d[i+8:i+8+n]; i+=12+n
        if t==b'IHDR': w,h,bd,ct=struct.unpack('>IIBB',c[:10])
        if t==b'IDAT': idat+=c
    raw=zlib.decompress(idat); bpp={0:1,2:3,4:2,6:4}[ct]*(bd//8); stride=w*bpp
    rows=[]; prev=bytearray(stride); j=0
    for y in range(h):
        f=raw[j]; j+=1; line=bytearray(raw[j:j+stride]); j+=stride
        for x in range(stride):
            a=line[x-bpp] if x>=bpp else 0; b=prev[x]; c=prev[x-bpp] if x>=bpp else 0
            if f==1: line[x]=(line[x]+a)&255
            elif f==2: line[x]=(line[x]+b)&255
            elif f==3: line[x]=(line[x]+(a+b)//2)&255
            elif f==4:
                p=a+b-c; pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
                pr=a if pa<=pb and pa<=pc else (b if pb<=pc else c); line[x]=(line[x]+pr)&255
        rows.append(bytes(line)); prev=line
    return w,h,bd,ct,rows
def bins(p):
    w,h,bd,ct,rows=readpng(p); out=[]
    for y in range(h):
        for x in range(w):
            v=struct.unpack('>H',rows[y][2*x:2*x+2])[0] if bd==16 else rows[y][x]
            i=y*w+x
            if v: out.append(((i//256)/15,((i//16)%16)/15,(i%16)/15,v))
    return out
if __name__=='__main__':
    for p in sys.argv[1:]:
        b=bins(p); tot=sum(x[3] for x in b)
        print(p, len(b), 'bins')
        # summarise by hue buckets
        import colorsys
        hs=[0]*12; S=0; V=0
        for r,g,bb,c in b:
            hh,ss,vv=colorsys.rgb_to_hsv(r,g,bb); hs[int(hh*12)%12]+=c/tot; S+=ss*c/tot; V+=vv*c/tot
        print(' hue hist (30deg buckets):',[round(x,2) for x in hs],' meanS',round(S,2),'meanV',round(V,2))
        print(' sample:', ['#%02x%02x%02x'%(int(r*255),int(g*255),int(bb*255)) for r,g,bb,c in sorted(b,key=lambda t:-t[3])[::16]][:16])
