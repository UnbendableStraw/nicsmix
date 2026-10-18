#!/usr/bin/env python3
"""syng-agent-b52.py = b51 + the sound field follows the speakers' LEDs:
  - syng-led-reactive publishes what it is glowing to /dev/shm/syng-led-rgbw (~20 Hz); the agent reads it and
    puts it on the viz feed (`led: [r,g,b,w]` in /api/viz/frame and the SSE stream), and /api/led reports it
  - a manual colour (/api/led/color) publishes to the same place, so the field follows that too
  - the field (embedded and apps/) derives its whole palette from that colour — hue from the LEDs, the app's
    own value/saturation structure — and falls back to the source palette when the LEDs are off or white
  - new toggle on the dashboard: "Field follows the LEDs" (cfg.followLed)"""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b51.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- agent: read what the LED daemon publishes ----------
rep('''def viz_frame():
    if time.time() - LAST_VIZ.get("t", 0) < 0.6:
        d = dict(LAST_VIZ); d["live"] = True; return d
    return {"energy": 0.0, "bass": 0.0, "beat": 0.0, "spec": [], "flux": [], "src": None, "live": False, "seq": VIZ_SEQ[0]}''',
    '''LED_PUB = "/dev/shm/syng-led-rgbw"   # written by syng-led-reactive (and by a manual colour, below)

def led_live_color(max_age=1.5):
    """What this speaker is glowing right now, [r,g,b,w] 0..1, or None when nothing is driving the LEDs."""
    try:
        with open(LED_PUB) as f: p = f.read().split()
        if len(p) >= 5 and 0 <= time.time() - float(p[4]) < max_age:
            return [float(p[0]), float(p[1]), float(p[2]), float(p[3])]
    except Exception: pass
    return None

def led_publish(r, g, b, w):
    try:
        with open(LED_PUB + ".tmp", "w") as f:
            f.write("%.4f %.4f %.4f %.4f %.3f\\n" % (r, g, b, w, time.time()))
        os.replace(LED_PUB + ".tmp", LED_PUB)
    except OSError: pass

def viz_frame():
    led = led_live_color()
    if time.time() - LAST_VIZ.get("t", 0) < 0.6:
        d = dict(LAST_VIZ); d["live"] = True
        if led: d["led"] = led
        return d
    out = {"energy": 0.0, "bass": 0.0, "beat": 0.0, "spec": [], "flux": [], "src": None, "live": False, "seq": VIZ_SEQ[0]}
    if led: out["led"] = led
    return out''')

rep('''    return {"reactive": reactive, "mode": mode, "palette": palette, "source": source, "tune": tune,
            "ledmanager": bus_has(LED_SVC), "visions": visions(), "reactiveModes": REACTIVE_MODES}''',
    '''    return {"reactive": reactive, "mode": mode, "palette": palette, "source": source, "tune": tune,
            "color": led_live_color(3.0), "ledmanager": bus_has(LED_SVC), "visions": visions(), "reactiveModes": REACTIVE_MODES}''')

rep('''def led_color(r, g, b, w):
    ensure_manual()
    arr = dbus.Array([dbus.Double(max(0.0, min(1.0, float(x)))) for x in (r, g, b, w)], signature="d")
    led_dbus("SetColor", dbus.Int32(5), arr)''',
    '''def led_color(r, g, b, w):
    ensure_manual()
    v = [max(0.0, min(1.0, float(x))) for x in (r, g, b, w)]
    arr = dbus.Array([dbus.Double(x) for x in v], signature="d")
    led_dbus("SetColor", dbus.Int32(5), arr)
    led_publish(*v)   # a hand-picked colour drives the sound field too''')

# ---------- field.js (embedded): a palette out of the live colour ----------
rep('''  autoPalette: true, palette: 'airplay',   // the field takes the active source's colours, like the app's Auras_* palettes''',
    '''  autoPalette: true, palette: 'airplay',   // the field takes the active source's colours, like the app's Auras_* palettes
  followLed: true,        // ...unless the speakers' LEDs are lit, and then the field glows what the room glows''')

rep('''function smoothstep(e0,e1,x){const t=clamp((x-e0)/(e1-e0),0,1);return t*t*(3-2*t);}''',
    '''function smoothstep(e0,e1,x){const t=clamp((x-e0)/(e1-e0),0,1);return t*t*(3-2*t);}

// ---------- a palette out of one live colour (what the speaker's LEDs are glowing) ----------
// Hue (and some of the saturation) comes from the LEDs; the value/saturation structure is the app's own,
// so the field keeps its depth instead of turning into a flat wash of one colour.
function rgb2hsv(r,g,b){const mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn;let h=0;
  if(d>1e-6){ if(mx===r)h=((g-b)/d+(g<b?6:0))/6; else if(mx===g)h=((b-r)/d+2)/6; else h=((r-g)/d+4)/6; }
  return [h, mx>1e-6?d/mx:0, mx];}
function hsv2rgb(h,s,v){h=((h%1)+1)%1;const i=Math.floor(h*6),f=h*6-i,p=v*(1-s),q=v*(1-s*f),t=v*(1-s*(1-f));
  switch(i%6){case 0:return[v,t,p];case 1:return[q,v,p];case 2:return[p,v,t];case 3:return[p,q,v];case 4:return[t,p,v];default:return[v,p,q];}}
function paletteFromColor(r,g,b,w){
  const c=rgb2hsv(r,g,b), h=c[0]; let s=c[1]*(1-0.5*(w||0));
  if(c[2] < 0.03 || s < 0.12) return null;     // LEDs off, or white light: keep the source's own colours
  s = clamp(s, 0.45, 1.0);
  const P=(sm,vv,dh)=>hsv2rgb(h+(dh||0), clamp(s*sm,0,1), vv);
  return { cyanDeep:P(1.00,0.54), cyan:P(0.83,0.84), cyanLight:P(0.43,0.95), white:P(0.10,1.00),
           blue:P(0.88,0.86,-0.045), teal:P(0.89,0.70,0.035), room:P(0.70,0.33), roomEdge:P(0.70,0.23) };
}''')

rep('''  function pullViz(){ if(!vizFn) return; const v = vizFn(); if(!v) return;
    st.e += ((v.energy||0) - st.e)*0.35; st.b += ((v.bass||0) - st.b)*0.4;''',
    '''  function pullViz(){ if(!vizFn) return; const v = vizFn(); if(!v) return;
    st.ledPal = (cfg.followLed && v.led && v.led.length >= 3) ? paletteFromColor(v.led[0], v.led[1], v.led[2], v.led[3]||0) : null;
    st.e += ((v.energy||0) - st.e)*0.35; st.b += ((v.bass||0) - st.b)*0.4;''')

rep('''    { const tp = cfg.palettes[cfg.palette] || cfg.palettes.airplay; const k = Math.min(1, dt*1.6);''',
    '''    { const tp = st.ledPal || cfg.palettes[cfg.palette] || cfg.palettes.airplay; const k = Math.min(1, dt*(st.ledPal?2.4:1.6));''')

# ---------- dashboard toggle ----------
rep('''  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim'],['snapToCell','Snap the puck to a speaker'],['autoPalette','Colours follow the source']];''',
    '''  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim'],['snapToCell','Snap the puck to a speaker'],['autoPalette','Colours follow the source'],['followLed','Colours follow the LEDs']];''')

s = s.replace('"syng-agent (b51)"', '"syng-agent (b52)"').replace("syng-agent (b51) on", "syng-agent (b52) on").replace('AGENT_BUILD = "b51"', 'AGENT_BUILD = "b52"')
out = os.path.join(here, "syng-agent-b52.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
