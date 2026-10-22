import sys,re
lo=int(sys.argv[1],16); hi=int(sys.argv[2],16)
txt=open('/home/claude/re/decomp.txt',errors='replace').read()
for f in txt.split('//// ==== FUNCTION ')[1:]:
    head=f.split('\n',1)[0]
    m=re.search(r'@ ([0-9a-f]+) size=(\d+)',head)
    a=int(m.group(1),16)
    if lo<=a<hi:
        print('//// ==== FUNCTION '+f)
