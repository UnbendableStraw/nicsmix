#!/usr/bin/env python3
"""syng-agent-b66.py = b65 + "Truthful AirPlay badge" (#2) + quick-recovery on by default (#1).

Reverse-engineering of airplay2source (dbcfb3cf, firmware 2.2.x) showed the "Stereo" badge during Atmos is
not a rendering bug: AirPlay2Source sets `badgingType` from a sender-supplied CF property
(CFGetInt64Ranged(..., default 0)) at every stream start, independently of the channel layout it just
negotiated. badgingType is an int enum (1 Stereo, 2 Surround, 3 Spatial Audio, 7 Dolby Atmos). So a
12-channel APAC 7.1.4 stream keeps whatever the sender sent (usually 1), and the app shows "Stereo".

The true fix is in Syng's binary (derive the badge from the negotiated format); we can't rebuild that, but
we can correct the published property from the agent: when a music source's channelLayout has height
channels we raise badgingType to Dolby Atmos (7); a non-height multichannel layout to Surround (2). We only
ever correct a clearly-wrong value (Unknown/Stereo/Undefined) -- never touch Spatial/Lossless/Dolby, and
never a correct badge. airplay2source rewrites the property on every format change, so the keeper
re-applies (same cadence as the atmos-fill keeper). Setting "airplay_badge_fix" (default on),
POST /api/airplay {"badge_fix": bool}. Nothing else changes; volume is never touched.

Also: "airplay_quick_recover" now defaults on. It only swaps a 2-minute core dump for a ~0.3 s restart after
an airplay2source crash (LimitCORE=0); there is no downside, and the crash (an uncaught sdbus::Error on the
format-change path -- see docs/SYNG-AIRPLAY-FIXES.md) can still happen until Syng wraps that boundary."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "..", "syng-agent.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- settings: quick recovery on by default, add the badge fix flag ----------
rep('''            "atmos_fill": False, "airplay_quick_recover": False, "airplay_silence_fix": True}''',
    '''            "atmos_fill": False, "airplay_quick_recover": True, "airplay_silence_fix": True,
            "airplay_badge_fix": True}''')

# ---------- the badge corrector, right after atmos_apply ----------
rep('''def airplay_keeper_loop():
    time.sleep(30)''', r'''# ---------- correct the AirPlay badge (b66) ----------
# badgingType enum in airplay2source: 0 Unknown, 1 Stereo, 2 Surround, 3 Spatial Audio, 4 Lossless,
# 5 Dolby Audio, 6 Undefined, 7 Dolby Atmos. AirPlay2Source sets it from the sender (default 0), not from
# the format, so Atmos shows as "Stereo". We correct only the plainly-wrong values for a multichannel/height
# stream; we never override Spatial/Lossless/Dolby, never downgrade, and leave stereo/mono alone.
BADGE_SURROUND, BADGE_ATMOS = 2, 7
BADGE_FIXABLE = (0, 1, 6)                 # Unknown / Stereo / Undefined -> wrong for a multichannel stream
_TOPCH = ("Vhl", "Vhr", "Ltf", "Rtf", "Ltr", "Rtr")

def badge_for_layout(layout):
    """The badge a channel layout deserves, or None to leave the stock value (idle, stereo, mono)."""
    ch = [c for c in str(layout or "").split() if c]
    if not ch or all(c.isdigit() for c in ch): return None          # idle source reports bare numbers
    if sum(1 for c in ch if c.startswith("T") or c in _TOPCH): return BADGE_ATMOS
    if len(ch) > 2: return BADGE_SURROUND
    return None

def badge_apply():
    """Raise each music source's badgingType to match its negotiated layout; writes only when plainly wrong."""
    n = 0
    for sid in source_ids():
        if not is_music(sid): continue
        dest, path = source_target_cached(sid)
        if not dest or not path: continue
        try: cur = get_all(dest, path, ASRC_IF)
        except Exception: continue
        if "badgingType" not in cur or "channelLayout" not in cur: continue
        want = badge_for_layout(cur.get("channelLayout"))
        if want is None: continue
        try: now = int(cur.get("badgingType"))
        except Exception: continue
        if now not in BADGE_FIXABLE or now == want: continue
        try:
            set_prop(dest, path, ASRC_IF, "badgingType", dbus.Int32(want, variant_level=1)); n += 1
            log("BADGE %s %s -> %d (layout %r)" % (sid, now, want, cur.get("channelLayout")))
        except Exception as e:
            _src_cache["targets"].pop(sid, None); log("BADGE %s: %s" % (sid, e))
    return n

def airplay_keeper_loop():
    time.sleep(30)''')

# ---------- run it in the keeper ----------
rep('''            if SETTINGS.get("atmos_fill"): atmos_apply()''',
    '''            if SETTINGS.get("atmos_fill"): atmos_apply()
            if SETTINGS.get("airplay_badge_fix", True): badge_apply()''')

# ---------- expose in /api/airplay ----------
rep('''           "settings": {"fill": bool(SETTINGS.get("atmos_fill")), "silence_fix": bool(SETTINGS.get("airplay_silence_fix", True)),''',
    '''           "settings": {"fill": bool(SETTINGS.get("atmos_fill")), "silence_fix": bool(SETTINGS.get("airplay_silence_fix", True)),
                        "badge_fix": bool(SETTINGS.get("airplay_badge_fix", True)),''')
rep('''    for k, key in (("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover"), ("silence_fix", "airplay_silence_fix")):''',
    '''    for k, key in (("fill", "atmos_fill"), ("quick_recover", "airplay_quick_recover"), ("silence_fix", "airplay_silence_fix"), ("badge_fix", "airplay_badge_fix")):''')
rep('''    if "fill" in changed: atmos_apply(force=True)''',
    '''    if "fill" in changed: atmos_apply(force=True)
    if changed.get("badge_fix"): badge_apply()''')
rep('''        fwd = {k: body[k] for k in ("fill", "quick_recover", "silence_fix") if k in body}''',
    '''        fwd = {k: body[k] for k in ("fill", "quick_recover", "silence_fix", "badge_fix") if k in body}''')

# ---------- build identity ----------
s = s.replace('"syng-agent (b65)"', '"syng-agent (b66)"').replace("syng-agent (b65) on", "syng-agent (b66) on").replace('AGENT_BUILD = "b65"', 'AGENT_BUILD = "b66"')

out = os.path.join(here, "syng-agent-b66.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s), "bytes")
