#!/usr/bin/env python3
"""Patch Syng's cellbroker ProxyManager.py so a height-layer change applies to THIS cell's renderer
immediately, offline -- the stock cell_set_height_layer only publishes it over MQTT and waits for the
cloud room document to come back (docs/SYNG-AIRPLAY-FIXES.md #3), so an offline room never hears it.

Additive and conservative: the MQTT publish is untouched; we add a local apply that updates our own
cell_positions entry, re-derives have_height_cells and recomputes object positions using the same calls
the stock set_room_state / set_cell_state already make. With no height change requested, behaviour is
identical to stock.

  usage: patch_proxymanager.py IN.py OUT.py        (asserts each anchor exists exactly once; py_compiles OUT)
"""
import sys, py_compile
src = open(sys.argv[1]).read()
def rep(old, new, n=1):
    global src
    c = src.count(old); assert c == n, ("anchor x%d" % c, old[:60])
    src = src.replace(old, new)

# 1) add the local-apply helper just above cell_set_height_layer
rep("    def cell_set_height_layer(self, height_layer):\n",
r'''    def _apply_height_layer_local(self, height_layer):
        """Apply a height-layer change to THIS cell's renderer now, without waiting for the cloud.
        Troubadour fix for docs/SYNG-AIRPLAY-FIXES.md #3: stock cell_set_height_layer only publishes over
        MQTT, so an offline room never applies it. We update our own cell_positions entry, re-derive
        height processing and recompute object positions with the same calls set_room_state/set_cell_state
        use. The MQTT publish still runs, so the cloud stays in sync when reachable."""
        try:
            my = syngnative.get_serial_number()
        except Exception:
            my = None
        try:
            hl = float(height_layer)
        except Exception:
            return
        matched = [c for c in self.cell_positions if c.get('serial_number') == my]
        if not matched and len(self.cell_positions) == 1:
            matched = self.cell_positions          # the lone default entry is us
        if not matched:
            logging.info('[ProxyManager] local height apply: own cell not in cell_positions yet; MQTT only')
            return
        changed = False
        for c in matched:
            if c.get('height_layer') != hl:
                c['height_layer'] = hl; changed = True
        if not changed:
            return
        logging.info(f'[ProxyManager] local height_layer apply: {my} -> {hl}')
        have_height_cells = True if get_number_of_height_layers(self.cell_positions) > 1 else False
        try:
            for sp in self.source_proxies:
                self.source_proxies[sp].enable_height_processing(have_height_cells)
        except Exception as e:
            logging.error(f"[ProxyManager] local height apply, enable_height_processing: {e}")
        for source_id in list(self.source_proxies):
            handles = self.handle_positions.get(source_id)
            if not handles:
                continue
            try:
                self.source_set_stem_positions(source_id=source_id, handles=handles, skip_persist=True)
            except Exception as e:
                logging.info(f"[ProxyManager] local height apply, recompute {source_id}: {e}")

    def cell_set_height_layer(self, height_layer):
        # Troubadour: apply locally first so it works offline, then publish to the cloud as stock does.
        try:
            self._apply_height_layer_local(height_layer)
        except Exception as e:
            logging.error(f"[ProxyManager] _apply_height_layer_local failed, continuing to MQTT: {e}")
''')

out = sys.argv[2]
open(out, "w").write(src)
py_compile.compile(out, doraise=True)
print("patched + compiled:", out, len(src), "bytes")
