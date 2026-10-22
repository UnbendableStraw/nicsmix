#!/usr/bin/env python3
# Troubadour: make the AirPlay "unstick silent track" watchdog fast.
# detect ~1s (was 4s), drop the 45s rate-limit bug, recover immediately.
import sys, py_compile, tempfile, os, shutil
F = sys.argv[1]
s = open(F, encoding="utf-8").read()
REPL = [
 ('        time.sleep(1.0)\n        try:\n            if not SETTINGS.get("airplay_silence_fix", True): continue',
  '        time.sleep(0.4)\n        try:\n            if not SETTINGS.get("airplay_silence_fix", True): continue'),
 ('            if now - SIL["silent_since"] < 4.0: continue',
  '            if now - SIL["silent_since"] < 1.0: continue'),
 ('            if SIL["attempts"] >= 2:',
  '            if SIL["attempts"] >= 3:'),
 ('                if SIL.get("pending") and now - SIL["last_try"] > 15:',
  '                if SIL.get("pending") and now - SIL["last_try"] > 10:'),
 ('            if now - SIL["last_try"] < 45 and SIL["attempts"] == 0: continue\n            if SIL["attempts"] == 1 and now - SIL["last_try"] < 8: continue',
  '            if now - SIL["last_try"] < 3: continue'),
 ('            transport("airplay2", "pause"); time.sleep(1.2); transport("airplay2", "play")',
  '            transport("airplay2", "pause"); time.sleep(0.8); transport("airplay2", "play")'),
 ('    if time.time() - LAST_VIZ.get("t", 0) > 2.0: return True',
  '    if time.time() - LAST_VIZ.get("t", 0) > 1.2: return True'),
]
applied=[]; missing=[]
for i,(old,new) in enumerate(REPL):
    if new in s and old not in s:
        applied.append("#%d already"%i); continue
    c=s.count(old)
    if c!=1: missing.append("#%d count=%d"%(i,c)); continue
    s=s.replace(old,new); applied.append("#%d ok"%i)
if missing:
    print("ABORT, unmatched:", missing); sys.exit(2)
# syntax check
tf=tempfile.NamedTemporaryFile("w",suffix=".py",delete=False,encoding="utf-8"); tf.write(s); tf.close()
try: py_compile.compile(tf.name, doraise=True)
except py_compile.PyCompileError as e: print("ABORT syntax:", e); sys.exit(3)
os.unlink(tf.name)
bak=F+".troubadour-presil.bak"
if not os.path.exists(bak): shutil.copy2(F,bak)
open(F,"w",encoding="utf-8").write(s)
print("PATCHED:", ", ".join(applied))
