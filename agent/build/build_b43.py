#!/usr/bin/env python3
"""syng-agent-b43.py = b42 + the Syng app's look on both pages.
From the app (2.1.0 screenshots + bundle): pure black pages, #1A/#32 grey cards with no borders, white as the one
accent (white capsule = primary action, outlined capsule = secondary), status green, destructive red, small grey
uppercase section labels, Graphik as the typeface, black pucks with three amber dots for the cells.
Dark is the default (the app is dark-only); Light is the same system inverted; Auto follows the OS.
Graphik is served from /data/syng-agent/fonts/ when the files are there (they are the app's, not ours to ship);
otherwise the system grotesk is used."""
import os, re
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b42.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

OLD_ROOT = ''':root{--bg:#14161b;--surface:#1b1e26;--surface2:#222633;--line:#2b3140;--text:#e8eaf0;
 --muted:#98a0b0;--accent:#8a7cff;--live:#4fd1c5;--warn:#e6a23c;--bad:#e5618a;--radius:18px}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;
 --line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}}
:root[data-theme=light]{--bg:#eef0f4;--surface:#fff;--surface2:#f3f5f9;--line:#dde1e9;--text:#1a1d24;--muted:#5d6676;--accent:#6a5cff;--live:#1aa89a}
.themebtn{font:inherit;font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer;margin-left:10px}'''
NEW_ROOT = ''':root{--bg:#000;--surface:#1a1a1a;--surface2:#2a2a2a;--line:#303030;--text:#fff;--muted:#8e8e8e;
 --accent:#fff;--accent-ink:#000;--live:#79de96;--warn:#e0b35a;--bad:#e0514c;--radius:20px;color-scheme:dark}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#fff;--surface:#f2f2f2;--surface2:#e4e4e4;--line:#d9d9d9;--text:#000;--muted:#6e6e6e;
 --accent:#000;--accent-ink:#fff;--live:#1f9d4d;--warn:#b8831d;--bad:#d43f3a;color-scheme:light}}
:root[data-theme=light]{--bg:#fff;--surface:#f2f2f2;--surface2:#e4e4e4;--line:#d9d9d9;--text:#000;--muted:#6e6e6e;--accent:#000;--accent-ink:#fff;--live:#1f9d4d;--warn:#b8831d;--bad:#d43f3a;color-scheme:light}
@font-face{font-family:Graphik;src:url(/fonts/Graphik-Regular-App.ttf) format("truetype");font-weight:400;font-display:swap}
@font-face{font-family:Graphik;src:url(/fonts/Graphik-Medium-App.ttf) format("truetype");font-weight:500;font-display:swap}
@font-face{font-family:Graphik;src:url(/fonts/Graphik-Semibold-App.ttf) format("truetype");font-weight:600;font-display:swap}
@font-face{font-family:Graphik;src:url(/fonts/Graphik-Bold-App.ttf) format("truetype");font-weight:700;font-display:swap}
.themebtn{font:inherit;font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer;margin-left:10px}'''
rep(OLD_ROOT, NEW_ROOT, count=2)
rep(''' font-family:ui-rounded,"SF Pro Rounded","SF Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;''',
    ''' font-family:Graphik,-apple-system,"SF Pro Text","Helvetica Neue",Inter,system-ui,Arial,sans-serif;letter-spacing:-.004em;''', count=2)

# the look, appended to both style blocks so it wins over the component rules above it
LOOK = '''
/* ---- Syng look (dark = the app; light = the same system inverted) ---- */
.card,.hero{border-color:transparent;border-radius:var(--radius)}
.card h2{font-size:12px;letter-spacing:.08em;color:var(--muted);font-weight:500}
.card h2 .rn{font-weight:700}
.id h1{font-weight:700;letter-spacing:-.02em}
.btn,.srcbtn,.themebtn{border-radius:999px;font-weight:600}
.btn{border-color:var(--line);background:var(--surface2)}
.btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.btn.live{border-color:var(--text);color:var(--text);background:transparent}
.btn.danger{background:transparent}
.srcbtn{border-color:transparent;background:var(--surface2)}
.srcbtn.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);box-shadow:none}
.badge{border-color:var(--line);color:var(--muted)}.badge.role{color:var(--text);border-color:var(--text)}
.badge.good{color:var(--live);border-color:color-mix(in srgb,var(--live) 50%,transparent)}
.badge.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 50%,transparent)}
input[type=range]{background:var(--surface2)}
input[type=range]::-webkit-slider-thumb{background:var(--text);border:2px solid var(--surface)}
input[type=range].tone::-webkit-slider-thumb{background:var(--text)}
.mark{color:var(--muted);letter-spacing:.16em;font-weight:500}.mark b{color:var(--text)}
.dot.on{background:var(--live)}
.fcell,.item,.sysmetric{background:var(--surface2);border-color:transparent;border-radius:14px}
.fcell.me,.item.me{box-shadow:inset 0 0 0 1px var(--text)}
.fdot.pri{background:var(--text)}
.orb{background:radial-gradient(72% 72% at 42% 36%,#3b3b3b,#060606 76%);box-shadow:inset 0 0 0 1px #000,0 12px 30px #0009}
.orb::before{content:"";position:absolute;inset:0;border-radius:50%;
 background:radial-gradient(circle at 50% 37%,#e3a24c 0 6%,transparent 7%),radial-gradient(circle at 40% 57%,#e3a24c 0 6%,transparent 7%),radial-gradient(circle at 60% 57%,#e3a24c 0 6%,transparent 7%)}
.orb.primary{--ring:var(--text)}
.spark polyline{stroke:var(--text)}.spark .area{fill:color-mix(in srgb,var(--text) 10%,transparent)}
.toast{border-color:transparent;box-shadow:0 10px 30px #000a}
.room{border-color:var(--line);border-radius:16px}
table th{font-weight:500;letter-spacing:.08em}
#map{background:var(--surface);border-radius:var(--radius)}
</style>'''
rep("\n</style></head><body><div class=wrap>", LOOK + "</head><body><div class=wrap>", count=2)

# theme switcher: dark is home, then light, then auto
rep('''<script>(function(){const T=['auto','dark','light'];let t='auto';try{t=localStorage.getItem('syngTheme')||'auto';}catch(e){}''',
    '''<script>(function(){const T=['dark','light','auto'];let t='dark';try{t=localStorage.getItem('syngTheme')||'dark';}catch(e){}''', count=2)

# setup map: cells drawn as the app draws them — black pucks with three amber dots; this cell gets a white ring
rep('''    ctx.fillStyle=c.isMe?C.acc:(c.online?C.live:C.muted); ctx.globalAlpha=c.cal?1:.55; ctx.beginPath(); ctx.arc(px,py,R,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=1;
    if(grab&&grab.c===c){ctx.strokeStyle=C.text;ctx.lineWidth=2*dpr;ctx.stroke();}''',
    '''    ctx.globalAlpha=c.cal?1:.6; const g=ctx.createRadialGradient(px-R*.25,py-R*.3,R*.1,px,py,R); g.addColorStop(0,'#3b3b3b'); g.addColorStop(1,'#070707');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(px,py,R,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle=c.online?'#555':'#333'; ctx.lineWidth=1*dpr; ctx.stroke();
    ctx.fillStyle='#e3a24c'; [[0,-.3],[-.26,.17],[.26,.17]].forEach(([ox,oy])=>{ctx.beginPath();ctx.arc(px+ox*R,py+oy*R,R*.11,0,Math.PI*2);ctx.fill();}); ctx.globalAlpha=1;
    if(c.isMe){ctx.strokeStyle=C.text;ctx.lineWidth=1.5*dpr;ctx.beginPath();ctx.arc(px,py,R+3*dpr,0,Math.PI*2);ctx.stroke();}
    if(grab&&grab.c===c){ctx.strokeStyle=C.text;ctx.lineWidth=2*dpr;ctx.beginPath();ctx.arc(px,py,R,0,Math.PI*2);ctx.stroke();}''')

# agent: serve the typeface from /data/syng-agent/fonts when present
rep('''            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")''',
    '''            elif self.path.startswith("/setup"): self._send(200, SETUP_PAGE, "text/html; charset=utf-8")
            elif self.path.startswith("/fonts/"):
                name = os.path.basename(self.path.split("?", 1)[0])
                fp = os.path.join(LC_DIR, "fonts", name)
                if re.match(r"^[A-Za-z0-9_.-]+\\.(ttf|otf|woff2?)$", name) and os.path.isfile(fp):
                    data = open(fp, "rb").read()
                    self.send_response(200); self.send_header("Content-Type", "font/ttf" if name.endswith(".ttf") else "font/" + name.rsplit(".", 1)[1])
                    self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "max-age=604800"); self.end_headers(); self.wfile.write(data)
                else: self._send(404, "no such font", "text/plain")''')

rep("    const [px,py]=toPx(c.x,c.y), R=14*dpr, sa=rad(c.rot-L.rc.rot); // screen angle of the speaker's front", "    const [px,py]=toPx(c.x,c.y), R=18*dpr, sa=rad(c.rot-L.rc.rot); // screen angle of the speaker's front")
rep("  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); const a=rad(c.rot-L.rc.rot); const tx=cx+Math.cos(a)*14*dpr*1.9, ty=cy-Math.sin(a)*14*dpr*1.9;", "  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); const a=rad(c.rot-L.rc.rot); const tx=cx+Math.cos(a)*18*dpr*1.9, ty=cy-Math.sin(a)*18*dpr*1.9;")
rep("  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); if(Math.hypot(px-cx,py-cy)<16*dpr) return {t:'cell',c};}", "  for(const c of L.cells){const [cx,cy]=toPx(c.x,c.y); if(Math.hypot(px-cx,py-cy)<20*dpr) return {t:'cell',c};}")
s = s.replace('"syng-agent (b42)"', '"syng-agent (b43)"').replace("syng-agent (b42) on", "syng-agent (b43) on").replace('AGENT_BUILD = "b42"', 'AGENT_BUILD = "b43"')
out = os.path.join(here, "syng-agent-b43.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
