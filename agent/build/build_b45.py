#!/usr/bin/env python3
"""syng-agent-b45.py = b44 +
- THE reboot bug: cloudsyncd keeps the room document in memory and writes /data/syng/cloud_state.json only now and
  then (on a cloud fetch, at start, occasionally) — never on our PatchState and not on shutdown. A cell rebooted hours
  after a room change came back with the stale file: the fresh cell rebooted after its slot update and woke up as the
  primary of its old test room; the five Ball Room cells' files still showed four neighbours. The agent now persists
  the live document to that file after every patch and on every keeper pass (atomic rename), so reboots and firmware
  updates keep the room.
- Speaker names: `display_name` in the cell's entry of the room document (travels with the room, survives reboots,
  visible on every page). Rename from the setup page's layout table or the dashboard's speaker list; shown in the
  hero, the fleet, the map, discovery and the table."""
import os, re
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b44.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---- persist the document ----
rep('''def apply_view(view):
    """Apply a member view locally (PatchState) and remember it."""''', '''def persist_state():
    """Write cloudsyncd's live document to the file it loads at start. cloudsyncd itself writes that file only on a
    cloud fetch / at start / now and then — not on PatchState and not on shutdown — so without this a reboot
    (or a firmware update) brought back whatever room the cell had hours ago."""
    try:
        st = raw_state()
        if not (st.get("cell_state") or {}).get("serial_number"): return False
        cur = None
        try: cur = json.loads(_read(CLOUD_STATE_FILE, "") or "null")
        except Exception: cur = None
        if cur == st: return True
        tmp = CLOUD_STATE_FILE + ".agent"
        open(tmp, "w").write(json.dumps(st)); os.replace(tmp, CLOUD_STATE_FILE)
        return True
    except Exception as e:
        log("persist_state: %s" % e); return False

def apply_view(view):
    """Apply a member view locally (PatchState) and remember it."""''')
rep('''    ok = patch_state_strict(patch)
    if fresh or not (raw_state().get("room_state") or {}).get("room_id"):
        ok = seed_state(patch) and ok''', '''    ok = patch_state_strict(patch)
    if fresh or not (raw_state().get("room_state") or {}).get("room_id"):
        ok = seed_state(patch) and ok
    persist_state()''')
rep('''            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)''', '''            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)
            persist_state()''')
# the keeper only runs its member loop on primaries; make sure secondaries persist too, and persist right before a reboot
rep('''            w = whoami()
            if (w.get("role") or "").lower() != "secondary":
                doc = room_doc(); changed = False''', '''            w = whoami()
            persist_state()
            if (w.get("role") or "").lower() != "secondary":
                doc = room_doc(); changed = False''')
rep('''            if reboot:
                fw_log("rebooting"); _slot_cache["t"] = 0
                time.sleep(2); _sh("systemctl reboot", 10)''', '''            if reboot:
                persist_state(); fw_log("room document persisted; rebooting"); _slot_cache["t"] = 0
                time.sleep(2); _sh("systemctl reboot", 10)''')
rep('''    _sh("fw_setenv -s %s" % scr, 20); fw_log("boot slot set to p%d, rebooting" % other)''',
    '''    _sh("fw_setenv -s %s" % scr, 20); persist_state(); fw_log("boot slot set to p%d, rebooting" % other)''')
# agent start: persist once the stack is up (covers reboots after changes made by older agents)
rep('''    threading.Timer(45.0, fw_commit_check).start()''', '''    threading.Timer(45.0, fw_commit_check).start()
    threading.Timer(40.0, persist_state).start()''')

# ---- names ----
rep('''OWN_KEYS = ("serial_number", "room_id", "place_id", "role", "role_pinned", "calibrated", "position", "nearby_walls",
            "is_upside_down", "height_layer", "online", "ip_address", "version", "syfi_ip", "syfi_good", "bssid")''',
    '''OWN_KEYS = ("serial_number", "room_id", "place_id", "role", "role_pinned", "calibrated", "position", "nearby_walls",
            "is_upside_down", "height_layer", "online", "ip_address", "version", "syfi_ip", "syfi_good", "bssid", "display_name")''')
rep('''    return {"serial": c.get("serial_number"), "role": c.get("role"), "ip": c.get("ip_address"),''',
    '''    return {"serial": c.get("serial_number"), "name": c.get("display_name"), "role": c.get("role"), "ip": c.get("ip_address"),''')
rep('''        cells.append({"serial": s, "ip": c.get("ip_address"), "role": c.get("role"), "online": str(c.get("online")).lower() == "true",''',
    '''        cells.append({"serial": s, "name": c.get("display_name"), "ip": c.get("ip_address"), "role": c.get("role"), "online": str(c.get("online")).lower() == "true",''')
rep('''            "ip": ip, "ips": iface_addrs(), "hostname": socket.gethostname(),
            "syfi_ip":''', '''            "ip": ip, "ips": iface_addrs(), "hostname": socket.gethostname(), "name": cs.get("display_name"),
            "syfi_ip":''')
# keep a re-admitted / renamed cell's name through new_cell_entry (template carries it) — and the rename op
rep('''def op_rename(name):''', '''def op_cell_name(serial, name):
    """(primary) Name a speaker: display_name in its entry of the room document, fanned out to everyone."""
    name = re.sub(r"[\\x00-\\x1f]", "", str(name or ""))[:40].strip()
    with LC_LOCK:
        require_primary()
        doc = room_doc()
        if serial not in doc["cells"]: raise RuntimeError("not a member of this room")
        if name: doc["cells"][serial]["display_name"] = name
        else: doc["cells"][serial].pop("display_name", None)
        doc["cells"][serial]["version"] = str(uuid.uuid4()); bump(doc["room_state"])
        return {"ok": True, "name": name or None, "fanout": fan_out(doc)}

def op_rename(name):''')
rep('''    if path.startswith("/api/room/rename"): return op_rename(body["name"])''',
    '''    if path.startswith("/api/room/rename"): return op_rename(body["name"])
    if path.startswith("/api/cell/name"): return op_cell_name(body.get("serial"), body.get("name"))''')
rep('RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener", "/api/soundmode")',
    'RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener", "/api/soundmode", "/api/cell/name")')

# dashboard: hero + fleet
rep('''  $('#host').textContent=c.hostname||'Syng Cell';
  $('#sub').textContent=room&&room.name?('Room · '+room.name):'Solo';''',
    '''  const meCell=fleet.find(x=>x.isMe)||{}; $('#host').textContent=meCell.name||c.hostname||'Syng Cell';
  $('#sub').textContent=(room&&room.name?('Room · '+room.name):'Solo')+(meCell.name&&c.hostname?' · '+c.hostname:'');''')
rep('''      <span class=nm>${short(c.serial)}${c.isMe?' · this one':''}<div class=muted style="font-size:11px;font-weight:500" data-live="${c.serial}"></div></span>''',
    '''      <span class=nm title="double-click to name" data-name="${c.serial}" style="cursor:text">${c.name?c.name+' <span class=muted style="font-weight:500">'+short(c.serial)+'</span>':short(c.serial)}${c.isMe?' · this one':''}<div class=muted style="font-size:11px;font-weight:500" data-live="${c.serial}"></div></span>''')
rep('''    $('#fleet').querySelectorAll('[data-ping]').forEach(b=>b.onclick=e=>{e.preventDefault();b.disabled=true;post('/api/ping',{serial:b.dataset.ping}).finally(()=>setTimeout(()=>b.disabled=false,3000));});}''',
    '''    $('#fleet').querySelectorAll('[data-ping]').forEach(b=>b.onclick=e=>{e.preventDefault();b.disabled=true;post('/api/ping',{serial:b.dataset.ping}).finally(()=>setTimeout(()=>b.disabled=false,3000));});
    $('#fleet').querySelectorAll('[data-name]').forEach(el=>el.ondblclick=()=>{const cur=(fleet.find(x=>x.serial===el.dataset.name)||{}).name||''; const n=prompt('Name this speaker (empty to clear)',cur); if(n===null)return; post('/api/cell/name',{serial:el.dataset.name,name:n}).then(()=>setTimeout(tick,800));});}''')
# setup page: hero, discovery, map, table
rep('''  $('#sub').textContent=`Cell ${short(m.serial)} · ${m.serial} · ${m.ip||'no IP'}`;''',
    '''  $('#sub').textContent=`${m.name?m.name+' · ':''}Cell ${short(m.serial)} · ${m.serial} · ${m.ip||'no IP'}`;''')
rep('''    const cells=r.cells.map(c=>`<div class="item ${c.isMe?'me':''}"><span class=grow><b>Cell ${short(c.serial)}</b> <span class=k>${c.ip}${c.isMe?' · this speaker':''}${c.stock?' · stock firmware (no agent)':''}</span></span>''',
    '''    const cells=r.cells.map(c=>`<div class="item ${c.isMe?'me':''}"><span class=grow><b>${c.name||('Cell '+short(c.serial))}</b> <span class=k>${c.name?short(c.serial)+' · ':''}${c.ip}${c.isMe?' · this speaker':''}${c.stock?' · stock firmware (no agent)':''}</span></span>''')
rep('''  L={cells:ROOM.cells.map(c=>({serial:c.serial,ip:c.ip,role:c.role,x:+(c.position.x||0),y:+(c.position.y||0),z:+(c.position.z||1.5),rot:+(c.position.rotation||0),''',
    '''  L={cells:ROOM.cells.map(c=>({serial:c.serial,name:c.name||'',ip:c.ip,role:c.role,x:+(c.position.x||0),y:+(c.position.y||0),z:+(c.position.z||1.5),rot:+(c.position.rotation||0),''')
rep('''    ctx.fillStyle=C.text; ctx.font=`${11*dpr}px system-ui`; ctx.textAlign='center'; ctx.fillText(short(c.serial)+(c.role==='primary'?' ★':''),px,py+R+12*dpr);''',
    '''    ctx.fillStyle=C.text; ctx.font=`500 ${11*dpr}px Graphik,system-ui`; ctx.textAlign='center'; ctx.fillText((c.name||short(c.serial))+(c.role==='primary'?' ★':''),px,py+R+12*dpr);''')
rep('''    <td><b>${short(c.serial)}</b>${c.isMe?' <span class=muted>(this)</span>':''}<div class=muted>${c.ip}${c.cal?'':' · uncalibrated'}</div></td>''',
    '''    <td><b>${c.name||short(c.serial)}</b>${c.isMe?' <span class=muted>(this)</span>':''} <button class="btn sm" data-nm=${i} title="Name this speaker" style="padding:2px 7px;font-size:11px">name</button><div class=muted>${c.name?short(c.serial)+' · ':''}${c.ip}${c.cal?'':' · uncalibrated'}</div></td>''')
rep('''    $('#cells').querySelectorAll('[data-rm]').forEach(b=>b.onclick=async()=>{''',
    '''    $('#cells').querySelectorAll('[data-nm]').forEach(b=>b.onclick=async()=>{const c=L.cells[+b.dataset.nm]; const n=prompt('Name for '+short(c.serial)+' (empty to clear)',c.name||''); if(n===null)return;
      try{await api('/api/cell/name',{serial:c.serial,name:n},primaryIp);toast(n?'Named '+n:'Name cleared');}catch(e){toast(e.message,true);} setTimeout(loadRoom,800);});
    $('#cells').querySelectorAll('[data-rm]').forEach(b=>b.onclick=async()=>{''')

s = s.replace('"syng-agent (b44)"', '"syng-agent (b45)"').replace("syng-agent (b44) on", "syng-agent (b45) on").replace('AGENT_BUILD = "b44"', 'AGENT_BUILD = "b45"')
out = os.path.join(here, "syng-agent-b45.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
