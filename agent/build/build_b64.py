#!/usr/bin/env python3
"""syng-agent-b64.py = b63, with height processing left to Syng, and the height layer per speaker instead.

b63 added a "Height channels" switch that forced the AirPlay source's enableHeightProcessing and re-applied it every
15 s. That was wrong: cellbroker owns that property (ProxyManager.set_room_state ->
AudioSourceProxy.enable_height_processing(have_height_cells), have_height_cells = the room has more than one height
layer) and writes it whenever the room state changes. The two fought, and turning it off never held.

How Syng renders Atmos (cellbroker/shaders.py): a 7.1.4 source gets variable_71_4_shader (12 objects around the
circle; with the puck at radius 0 — Direct, or Spatial off — the fixed staging_71_4 layout). The four height channels
are always rendered. With every speaker on height layer 0 they are extra objects on the floor ring (±45°, ±135°); once
some speaker is on a higher layer they move up to 0.6 × that layer, and cellbroker turns height processing on.
The height layer is per speaker (cell.height_layer in the room doc). Syng Studio's "height layer" switch sends it
through the Syng cloud (MQTT), which an offline room never hears back from; our layout save writes it locally.

1. The height switch is gone: the agent never writes enableHeightProcessing; the old setting is dropped on load.
2. GET /api/airplay also returns height_layers {serial: layer} for the room and height_processing (Syng's live value).
3. The AIRPLAY & ATMOS card shows height processing as Syng's status and a height-layer picker per speaker (posts
   /api/room/layout to the primary, like the Layout card's column). Fill surround stays: nothing in the firmware or
   Syng's apps sets enableBeyondStereoDuplication, so it fights nobody — labelled as the experiment it is."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b63.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

rep('''            "atmos_height": False, "atmos_fill": False, "airplay_quick_recover": False}''',
    '''            "atmos_fill": False, "airplay_quick_recover": False}''')
rep('''def load_settings():
    try: SETTINGS.update(json.loads(_read(SETTINGS_FILE, "{}") or "{}"))
    except Exception: pass''',
'''def load_settings():
    try: SETTINGS.update(json.loads(_read(SETTINGS_FILE, "{}") or "{}"))
    except Exception: pass
    SETTINGS.pop("atmos_height", None)          # b63's height switch: cellbroker owns that property (b64)''')

rep('''ATMOS_PROPS = (("atmos_height", "enableHeightProcessing"), ("atmos_fill", "enableBeyondStereoDuplication"))''',
    '''# enableHeightProcessing is NOT here: cellbroker sets it from the room's height layers (b64)
ATMOS_PROPS = (("atmos_fill", "enableBeyondStereoDuplication"),)''')
rep('''            if SETTINGS.get("atmos_height") or SETTINGS.get("atmos_fill"): atmos_apply()''',
    '''            if SETTINGS.get("atmos_fill"): atmos_apply()''')

rep('''           "settings": {"height": bool(SETTINGS.get("atmos_height")), "fill": bool(SETTINGS.get("atmos_fill")),''',
    '''           "settings": {"fill": bool(SETTINGS.get("atmos_fill")),''')

rep('''def airplay_room():
    cells = [airplay_local()] + list(_members_call("/api/airplay/local").values())
    return {"cells": cells}''',
'''def airplay_room():
    cells = [airplay_local()] + list(_members_call("/api/airplay/local").values())
    layers = {}
    try:
        for serial, c in room_doc()["cells"].items():
            layers[serial] = int(round(float(c.get("height_layer") or 0)))
    except Exception: pass
    hp = next((c.get("height") for c in cells if c.get("height") is not None), None)
    return {"cells": cells, "height_layers": layers, "height_processing": hp,
            "primary_ip": next((c.get("ip") for c in cells if c.get("role") == "primary"), None)}''')

rep('''    for k, key in (("height", "atmos_height"), ("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover")):''',
    '''    for k, key in (("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover")):''')
rep('''    if "height" in changed or "fill" in changed: atmos_apply(force=True)''',
    '''    if "fill" in changed: atmos_apply(force=True)''')

# ---------- the card ----------
rep('''  <div class="rowb sw" style="margin-top:10px"><div><b>Height channels</b><div class=muted style="font-size:12px">Atmos' overhead channels (Tfl Tfr Trl Trr) — Syng's <code>enableHeightProcessing</code></div></div>
    <label class=switch><input type=checkbox id=apheight><span></span></label></div>
  <div class="rowb sw"><div><b>Fill surround channels</b><div class=muted style="font-size:12px">Spread beyond-stereo channels — Syng's <code>enableBeyondStereoDuplication</code></div></div>''',
'''  <div style="margin-top:12px"><b>Height</b> <span id=aphp class=muted></span>
    <div class=muted style="font-size:12px;margin-top:2px">Atmos' four overhead channels always play. With every speaker on the floor layer they are spread around the
      ring at ear height; mark the speakers that sit up high and Syng lifts the height channels onto them (it turns height processing on by itself).</div></div>
  <div id=aplayers style="display:flex;flex-direction:column;gap:6px;margin:8px 0 6px"></div>
  <div class="rowb sw"><div><b>Fill surround channels</b><div class=muted style="font-size:12px">Syng's <code>enableBeyondStereoDuplication</code> — nothing in the firmware or Syng's apps sets it; an experiment</div></div>''')
rep('''  <p class=hint>Both Atmos switches are properties the speakers have always had but nothing ever turned on (stock: off). Each switch
    applies to every speaker on the network at once; flip one while an Atmos track plays and listen. Syng's own badge says "Stereo"
    even during Atmos — the line above reads the real channel layout instead.</p>''',
'''  <p class=hint>The switches apply to every speaker on the network at once. Syng's own badge says "Stereo" even during Atmos — the
    line at the top reads the real channel layout instead.</p>''')
rep('''    const st=(me.settings||{}); $('#apheight').checked=!!st.height; $('#apfill').checked=!!st.fill; $('#apquick').checked=!!st.quick_recover;
    $('#apcells').innerHTML=cs.map(c=>`${c.name||short(c.serial)}${c.role?' ('+c.role+')':''}: `+(c.height==null?'AirPlay runs on the primary':`height ${c.height?'on':'off'} · fill ${c.fill?'on':'off'}`)+` · quick recovery ${c.quick_recover&&c.quick_recover.on?'on':'off'}`).join('<br>');''',
'''    const st=(me.settings||{}); $('#apfill').checked=!!st.fill; $('#apquick').checked=!!st.quick_recover;
    $('#aphp').textContent=AP.height_processing==null?'':(AP.height_processing?'· height processing on (set by Syng)':'· height processing off (set by Syng: no speaker is up high)');
    const HL=AP.height_layers||{}, names={}; cs.forEach(c=>{if(c.serial)names[c.serial]=c.name||'Cell '+short(c.serial);});
    $('#aplayers').innerHTML=Object.keys(HL).map(s=>`<label class="rowb sw" style="margin:0"><span>${names[s]||'Cell '+short(s)}</span>
      <select data-hl="${s}"><option value=0 ${HL[s]==0?'selected':''}>Floor / shelf</option><option value=1 ${HL[s]==1?'selected':''}>Up high</option><option value=2 ${HL[s]==2?'selected':''}>Ceiling</option></select></label>`).join('');
    document.querySelectorAll('[data-hl]').forEach(el=>el.onchange=async()=>{const b={cells:{}};b.cells[el.dataset.hl]={height_layer:+el.value};
      try{await api('/api/room/layout',b,AP.primary_ip||primaryIp);toast('Height layer saved');setTimeout(loadAP,2500);}catch(e){toast(e.message,true);loadAP();}});
    $('#apcells').innerHTML=cs.map(c=>`${c.name||short(c.serial)}${c.role?' ('+c.role+')':''}: `+(c.height==null?'AirPlay runs on the primary':`height processing ${c.height?'on':'off'} · fill ${c.fill?'on':'off'}`)+` · quick recovery ${c.quick_recover&&c.quick_recover.on?'on':'off'}`).join('<br>');''')
rep('''async function apSet(k,v){try{const b={};b[k]=v;await api('/api/airplay',b);toast((k==='height'?'Height channels':k==='fill'?'Fill surround':'Quick recovery')+(v?' on':' off')+' — every speaker');setTimeout(loadAP,1500);}catch(e){toast(e.message,true);loadAP();}}
$('#apheight').onchange=e=>apSet('height',e.target.checked);''',
'''async function apSet(k,v){try{const b={};b[k]=v;await api('/api/airplay',b);toast((k==='fill'?'Fill surround':'Quick recovery')+(v?' on':' off')+' — every speaker');setTimeout(loadAP,1500);}catch(e){toast(e.message,true);loadAP();}}''')

s = s.replace('"syng-agent (b63)"', '"syng-agent (b64)"').replace("syng-agent (b63) on", "syng-agent (b64) on").replace('AGENT_BUILD = "b63"', 'AGENT_BUILD = "b64"')
out = os.path.join(here, "syng-agent-b64.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
