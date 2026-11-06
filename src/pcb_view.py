"""``prometheus view out/``: a local page to look at a board - its layout (layers to switch on and off), a 3D view,
a net-label schematic, every finding (click one: the board zooms to it) - and, while a run is going
(every ``prometheus board.py out/`` opens it), its stage, the try it is on and about how long is left.

Python's standard library only (http.server); the 3D view's three.js (r128, MIT) ships beside the page.
"""
from __future__ import annotations

import json
import math
import re
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
PAGE = HERE / "pcb_viewer_assets" / "index.html"


# ---- progress: the run writes out/progress.json, the page reads it ----

class Progress:
    """the run's progress for the page: each stage as it starts and ends, each place-and-route try with its time,
    and an estimate of what is left (the tries still to run, at the time the tries so far took, a batch of
    ``workers`` at a time, plus the export)"""

    def __init__(self, out_dir: str, script: str, parts: int = 0):
        self.path = Path(out_dir) / "progress.json"
        self.t0 = time.time()
        self.d = {"script": os.path.abspath(script), "started": self.t0, "stage": "loading", "done": False,
                  "tries": None, "tries_done": 0, "try_times": [], "workers": 1, "parts": parts, "stages": [],
                  "snap": 0, "pid": os.getpid()}
        self._write()

    def stage(self, name: str):
        now = time.time()
        if self.d["stages"] and self.d["stages"][-1].get("end") is None:
            self.d["stages"][-1]["end"] = now - self.t0
        self.d["stages"].append({"name": name, "start": now - self.t0, "end": None})
        self.d["stage"] = name
        self._write()

    def event(self, ev: dict):
        """pcb.PROGRESS's callback: {"kind": "tries", "tries": n, "workers": w} then {"kind": "try", "k": k,
        "seconds": s, "clean": bool, "line": text} per try"""
        if ev.get("kind") == "tries":
            self.d["tries"], self.d["workers"] = ev["tries"], ev.get("workers", 1)
        elif ev.get("kind") == "router":                # (the router's own progress inside a try)
            self.d["router_line"] = ev.get("line", "")
        elif ev.get("kind") == "try":
            self.d["tries_done"] += 1
            self.d["try_times"].append(ev.get("seconds", 0.0))
            self.d["last_try"] = ev.get("line", "")
            if ev.get("clean"):
                self.d["tries"] = self.d["tries_done"]      # (a clean try ends the search)
        self._write()

    def snapshot(self, b, script: str):
        """the board as it stands (out/board.json): the page shows it at once, before the run is done"""
        write_board(b, str(self.path.parent), script)
        self.d["snap"] += 1
        self._write()

    def finish(self, clean: bool):
        self.stage("done")
        self.d["stages"][-1]["end"] = time.time() - self.t0
        self.d["done"], self.d["clean"] = True, clean
        self._write()

    def eta(self) -> Optional[float]:
        d = self.d
        if d["done"]:
            return 0.0
        export = 5.0 + 0.15 * d.get("parts", 10)           # (Gerbers, KiCad project, schematic, KiCad's DRC)
        if d["stage"] in ("place+route", "place", "route"):
            times = d["try_times"]
            t_try = (sum(times) / len(times)) if times else 3.0 + 0.3 * d.get("parts", 10)
            n = d["tries"] or 1
            left = max(0, n - d["tries_done"])
            w = max(1, d["workers"])
            batches = 0 if left == 0 else (1 if d["tries_done"] == 0 else math.ceil(left / w))
            if d["tries_done"] == 0:               # (the first try runs alone; the rest only if it is not clean)
                started = time.time() - self.t0 - (d["stages"][-1]["start"] if d["stages"] else 0)
                return None if started > t_try else t_try - started + export   # (past the guess: unknown,
                                                                                #  not a number that stops counting)
            return batches * t_try + export
        if d["stage"] in ("check", "critic"):
            return export
        if d["stage"] == "export":
            return 1.0
        return None

    def _write(self):
        d = dict(self.d, elapsed=round(time.time() - self.t0, 1))
        e = self.eta()
        d["eta"] = None if e is None else round(e, 1)
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(d))
        os.replace(tmp, self.path)


# ---- the board as the page draws it ----

def _poly(g, tol=0.01):
    """shapely geometry -> [[outer ring, hole ring, ...], ...] (rings as [x, y] lists)"""
    if g is None or g.is_empty:
        return []
    g = g.simplify(tol, preserve_topology=True)
    out = []
    for p in getattr(g, "geoms", None) or [g]:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        rings = [p.exterior] + list(p.interiors)
        out.append([[[round(x, 3), round(y, 3)] for x, y in r.coords] for r in rings])
    return out


def height_of(fp: str) -> float:
    """a part's height for the 3D view, by its footprint's family (mm, near enough to look right)"""
    n = fp.split(":")[-1]
    for key, h in (("CP_Radial", 11.5), ("CP_Elec_8x10", 10.0), ("CP_Elec", 7.7), ("Potentiometer", 10.0),
                   ("PinHeader", 8.5), ("TerminalBlock", 10.0), ("USB_A", 7.0), ("USB_C", 3.3), ("JST_XH", 7.0),
                   ("JST_PH", 6.0), ("JST_SH", 4.3), ("SRR1260", 6.0), ("SRN6045", 4.5), ("L_", 3.5),
                   ("TO-263", 4.5), ("TO-252", 2.4), ("SW_", 3.5), ("Crystal", 0.8), ("QFN", 0.9), ("DFN", 0.9),
                   ("SOIC", 1.6), ("SO-", 1.6), ("TSSOP", 1.1), ("MSOP", 1.1), ("HTSSOP", 1.1), ("LQFP", 1.6),
                   ("SOT-23", 1.1), ("SOT", 1.2), ("D_SMA", 2.3), ("D_SMB", 2.3), ("SOD", 1.2), ("WS2812", 1.6),
                   ("PLCC", 1.9), ("1206", 1.0), ("0805", 0.8), ("0603", 0.6), ("0402", 0.4), ("TestPoint", 0.05),
                   ("Fuse", 1.0), ("ESP32", 3.1), ("RP2040", 0.9)):
        if key in n:
            return h
    return 1.5


def kind_of(ref: str, fp: str) -> str:
    n = fp.split(":")[-1]
    if "CP_" in n:
        return "electrolytic"
    for pre, k in (("TP", "pad"), ("SW", "switch"), ("RV", "pot"), ("LED", "led"), ("J", "connector"),
                   ("P", "pad" if "TestPoint" in n else "connector"), ("U", "ic"), ("Q", "ic"), ("Y", "crystal"),
                   ("X", "crystal"), ("L", "inductor"), ("C", "cap"), ("R", "res"), ("D", "led" if "LED" in n else "diode"),
                   ("F", "fuse"), ("H", "hole")):
        if ref.startswith(pre):
            return k
    return "other"


_PAGES: dict = {}                                        # (datasheet url -> its pages' text, read once per viewer)


def _pages(url: str):
    import parts
    if url not in _PAGES:
        _PAGES[url] = parts.datasheet_text(url)
    return _PAGES[url]


def _evidence(cache, out, ref: str = "", cites: bool = False) -> dict:
    """on demand, for the Evidence tab: a part's wired pins, each with its net, the parts on that net and its datasheet
    row (page, quote, status); or the board's citations, each checked against its page (``found``)"""
    import types
    import parts
    comps = cache.board().get("comps", [])
    if cites:
        p = out / "result.json"
        ev = json.loads(p.read_text()).get("evidence", []) if p.exists() else []
        shim = types.SimpleNamespace(evidence=[dict(e) for e in ev], comps={})
        return {"cites": parts.evidence(shim, read=_pages)}
    c = next((c for c in comps if c["ref"] == ref and c.get("symbol")), None)
    if not c:
        return {"error": "no symbol"}
    on = {}                                              # (net -> the other parts on it)
    for o in comps:
        for q in o["pads"]:
            if q.get("net"):
                on.setdefault(q["net"], set()).add(o["ref"])
    net = {q["num"]: q["net"] for q in c["pads"] if q.get("net")}
    try:
        d = parts.datasheet_check(c["symbol"]["name"], _pages(c["symbol"]["datasheet"]))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}", "datasheet": c["symbol"].get("datasheet")}
    pins = [{**q, "net": net[q["num"]], "with": sorted(on.get(net[q["num"]], set()) - {ref}),
             "page": q.get("page") or d.get("page")} for q in d.get("pins", []) if q["num"] in net]
    return {"ref": ref, "datasheet": d.get("datasheet"), "page": d.get("page"), "pins": pins,
            "agree": sum(q["status"] == "agrees" for q in pins), "total": len(pins)}


def _datasheet_pdf(cache, out, ref: str, url: str):
    """the datasheet PDF (from the parts cache, fetched once) of a part by its ref, or one the board cites by url;
    only links the board itself names are fetched (this page is not a proxy)"""
    import hashlib
    import parts
    from vendor_parts import CACHE
    comps = cache.board().get("comps", [])
    links = {c["symbol"].get("datasheet") for c in comps if c.get("symbol")}
    p = out / "result.json"
    if p.exists():
        links |= {e.get("url") for e in json.loads(p.read_text()).get("evidence", []) if isinstance(e, dict)}
    if ref:
        url = next((c["symbol"].get("datasheet") for c in comps if c["ref"] == ref and c.get("symbol")), "")
    if not url or url not in links or not url.lower().startswith("http"):
        return None
    dst = CACHE / "datasheets" / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if not dst.exists() or dst.stat().st_size < 2000:
        try:
            parts.datasheet_text(url)                    # (fetches it into the cache, as the pin check does)
        except Exception:
            return None
    return dst if dst.exists() else None


def board_data(b) -> dict:
    """everything the page draws, board mm (y up)"""
    pn = b.pad_net()
    comps = []
    shapes = b.pad_shapes()
    by_ref = {}
    for ref, num, L, g, d in shapes:
        by_ref.setdefault(ref, []).append({"num": num, "layer": L, "net": pn.get((ref, num)), "poly": _poly(g, 0.005),
                                           "drill": d, "at": [round(g.centroid.x, 3), round(g.centroid.y, 3)]})
    import pcb
    # (each label where the fab prints it, or None where it stays off the silkscreen: what the "made" look shows)
    silk = pcb.place_refs(b) if b.comps and not any(c.get("unplaced") or c.get("at") is None for c in b.comps.values()) else {}
    for ref, c in b.comps.items():
        cy = b.courtyard(ref)
        comps.append({"silk": silk.get(ref, "center") if silk else "center", "ref": ref, "value": c.get("value", ""), "mpn": c.get("mpn", ""), "fp": c["fp"],
                      "at": [round(c["at"][0], 3), round(c["at"][1], 3)], "rot": c["rot"], "side": c.get("side", "F"),
                      "courtyard": _poly(cy) if cy is not None else [], "pads": by_ref.get(ref, []),
                      "height": height_of(c["fp"]), "kind": kind_of(ref, c["fp"]),
                      "unplaced": bool(c.get("unplaced")),
                      "symbol": ({k: c["symbol"].get(k, []) for k in ("name", "description", "datasheet", "pins", "body", "draw",
                                                            "hide_names", "hide_numbers")}
                                 if c.get("symbol") else None)})
    cu = b.copper()
    copper = [{"net": n, "layer": L, "poly": _poly(g, 0.01)} for (n, L), g in sorted(cu.items()) if g is not None]
    holes = [[x, y, d] for x, y, d in b.all_holes()] if hasattr(b, "all_holes") else [[x, y, d] for x, y, d in b.holes]
    nets = {n: {"pins": [list(p) for p in v["pins"]], "amps": v.get("amps"), "width": b.net_width(n)}
            for n, v in b.nets.items()}
    return {"name": b.name, "outline": _poly(b.outline)[0][0] if not b.outline.is_empty else [],
            "cutouts": _poly(b.outline)[0][1:] if not b.outline.is_empty else [],
            "bounds": list(b.outline.bounds), "layers": list(b.layers), "thickness": getattr(b, "thickness", 1.6),
            "comps": comps, "tracks": [{"net": t["net"], "layer": t["layer"], "pts": t["pts"], "w": t["w"]}
                                       for t in b.tracks],
            "vias": [{"net": v["net"], "at": list(v["at"]), "dia": v["dia"], "drill": v["drill"]} for v in b.vias],
            "copper": copper, "holes": holes, "nets": nets,
            "pours": [{"net": p["net"], "layer": p["layer"]} for p in b.pours]}


class _Cache:
    def __init__(self, out_dir: str):
        self.out = Path(out_dir)
        self.key, self.data, self.lock = None, None, threading.Lock()
        self.live, self.undo = None, []                  # (the board a live edit moves parts on, and what it was)
        self.rev, self.last = 0, {}                      # (the script's saved edits shown so far; the last one's result)
        self.script_m = self.state_m = None              # (the script's and the run's state's versions the board is)

    def watch(self, every: float = 0.5) -> None:
        """the board script, followed: each time it is saved, ``refresh`` (in the background, for the page's poll of
        /api/live)"""
        def run():
            while True:
                try:
                    script = self.script()
                    m = Path(script).stat().st_mtime if script and not script.endswith(".net") else None
                    if m:
                        with self.lock:                  # (built, or built again on a run's new state)
                            self._board()
                        if m != self.script_m:           # (a save since the version shown)
                            self.refresh()
                except Exception as e:                   # (a script mid-save, or one that does not run yet: said)
                    self.last = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "rev": self.rev}
                time.sleep(every)
        threading.Thread(target=run, daemon=True).start()

    def refresh(self) -> dict:
        """the board script as just saved, on the page in a second or two: the parts it does not place where they were,
        a new one placed, and only the nets the edit touched routed again (the harness's router; the rest keep their
        copper). A change to the outline or the layers needs a run: said, not attempted"""
        import pcb
        import pcb_cli
        with self.lock:
            t0 = time.perf_counter()
            was = self._board()
            self.script_m = Path(self.script()).stat().st_mtime
            b, _ = pcb_cli.load(self.script())
            if b.layers != was.layers or not b.outline.equals(was.outline):
                self.last = {"ok": False, "rev": self.rev, "error": "the outline or the layers changed: run it again "
                             "(prometheus board.py out/) to place and route it as a whole"}
                return self.last
            for r, c in b.comps.items():                 # (a part the script leaves to the placer: where it was)
                if c.get("unplaced") and r in was.comps and was.comps[r]["fp"] == c["fp"]:
                    w = was.comps[r]
                    c.update(at=w["at"], rot=w["rot"])
                    c.pop("unplaced", None)
            new = [r for r, c in b.comps.items() if c.get("unplaced")]
            if new:
                pcb.autoplace(b, move=new)
            old = {"placement": pcb.placement(was), "tracks": [dict(t, pts=list(t["pts"])) for t in was.tracks],
                   "vias": [dict(v) for v in was.vias]}
            b.tracks, b.vias = [t for t in b.tracks], [v for v in b.vias]
            redo = [n for n in pcb.reuse_routes(b, old) if n in b.nets]
            redo += [n for n in b.nets if n not in redo and len(b.islands(n)) > 1]   # (a net that gained a pin)
            probs = pcb.route_jobs(b, [(n, b.net_width(n), None) for n in sorted(redo)]) if redo else []
            self.live, self.live_key, self.undo = b, pcb_cli.design_key(b), []
            got = self._after(b, t0, redo, probs)
            got.update(rev=self.rev + 1, placed=new)
            self.last = {k: v for k, v in got.items() if k != "board"}
            self.rev += 1                                # (last: the page reads rev and its result together)
            return got

    def _board(self):
        """the run's board, for live edits (the script, the run's placement and copper): built once, and again
        whenever a run saves its state (a viewer opens while its run is still routing)"""
        import pcb_cli
        st = self.out / pcb_cli.STATE
        sm = st.stat().st_mtime if st.exists() else 0
        if self.live is None or sm != self.state_m:
            if self.script_m is None:                    # (the script's version shown: a run's new state does not
                self.script_m = Path(self.script()).stat().st_mtime   # make an edit since it count as seen)
            self.state_m = sm
            b, _ = pcb_cli.load(self.script())
            self.live_key = pcb_cli.design_key(b)        # (the design's, as the script builds it: what a later run
            if not pcb_cli.load_state(b, str(self.out), self.live_key) and st.exists():   # checks it against)
                import pickle                            # (the run's state of an earlier version of the script: its
                with open(st, "rb") as f:                #  placement and copper still the start; refresh routes again
                    sv = pickle.load(f)                  #  what the edit disturbs)
                for r, c in sv["place"].items():
                    if r in b.comps:
                        b.comps[r].update(c)
                        b.comps[r].pop("unplaced", None)
                b.tracks, b.vias = sv["tracks"], sv["vias"]
            self.live = b
        return self.live

    def _after(self, b, t0: float, redo, probs) -> dict:
        """a live edit kept: the run's state saved (later stages start from it), the page's board, what is left"""
        import pcb_cli
        pcb_cli.save_state(b, str(self.out), self.live_key)
        self.state_m = (self.out / pcb_cli.STATE).stat().st_mtime   # (its own save: not a run's, no rebuild)
        self.data, self.key = board_data(b), _board_key(self.out, self.script())
        return {"ok": True, "board": self.data, "rerouted": sorted(redo), "unrouted": probs, "check": b.check(),
                "seconds": round(time.perf_counter() - t0, 2)}

    def move(self, ref: str, at, rot=None, side=None) -> dict:
        """a part moved on the page: its nets routed again (the harness's own router, only those: what keeps a drag
        quick; FreeRouting is ``route``), every other net's copper kept"""
        import pcb
        from shapely.geometry import Point
        with self.lock:
            b = self._board()
            c = b.comps.get(ref)
            if c is None:
                return {"ok": False, "error": f"no part {ref} on the board"}
            if side not in (None, c.get("side", "F")):
                return {"ok": False, "error": f"{ref}: a part changes side in board.py (side=), not by dragging"}
            at = (round(float(at[0]), 3), round(float(at[1]), 3))
            if not b.outline.contains(Point(at)):
                return {"ok": False, "error": f"{ref}: ({at[0]}, {at[1]}) is off the board"}
            t0 = time.perf_counter()
            old = {"placement": pcb.placement(b), "tracks": [dict(t, pts=list(t["pts"])) for t in b.tracks],
                   "vias": [dict(v) for v in b.vias]}
            self.undo.append((ref, c["at"], c["rot"], old["tracks"], old["vias"]))
            c["at"], c["rot"] = at, float(c["rot"] if rot is None else rot) % 360.0
            b.tracks, b.vias = [], []
            b.__dict__.pop("_cu_memo", None)
            redo = [n for n in pcb.reuse_routes(b, old) if n in b.nets]
            probs = pcb.route_jobs(b, [(n, b.net_width(n), None) for n in sorted(redo)]) if redo else []
            got = self._after(b, t0, redo, probs)
            got["line"] = f"{ref}: at=({at[0]}, {at[1]}), rot={c['rot']:g}"   # (for board.py: the page does not edit it)
            moves = self.out / "moves.json"
            log = json.loads(moves.read_text()) if moves.exists() else []
            log.append({"ref": ref, "at": list(at), "rot": c["rot"], "side": c.get("side", "F"), "line": got["line"]})
            moves.write_text(json.dumps(log, indent=1))
            return got

    def undo_move(self) -> dict:
        with self.lock:
            if not self.undo:
                return {"ok": False, "error": "nothing to undo"}
            b = self._board()
            t0 = time.perf_counter()
            ref, at, rot, tracks, vias = self.undo.pop()
            b.tracks, b.vias = tracks, vias
            b.__dict__.pop("_cu_memo", None)
            moves = self.out / "moves.json"
            if ref is None:                              # (a "route properly": its copper only)
                return self._after(b, t0, [], [])
            b.comps[ref]["at"], b.comps[ref]["rot"] = at, rot
            if moves.exists():
                moves.write_text(json.dumps(json.loads(moves.read_text())[:-1], indent=1))
            return self._after(b, t0, [], [])

    def route_rest(self) -> dict:
        """the board routed again properly once live edits left nets unrouted: all of it, as a run routes it
        (FreeRouting through KiCad when installed; routing round copper kept from before leaves shorts), kept only
        when it leaves fewer problems than there are"""
        import pcb
        import freerouting
        with self.lock:
            b = self._board()
            t0 = time.perf_counter()
            torn = sorted(n for n in b.nets if len(b.islands(n)) > 1)
            before = b.check()
            if not torn and not before:
                return self._after(b, t0, [], [])
            keep = [dict(t, pts=list(t["pts"])) for t in b.tracks], [dict(v) for v in b.vias]
            self.undo.append((None, None, None) + keep)  # (undone like a move)
            b.tracks, b.vias = [], []
            b.__dict__.pop("_cu_memo", None)
            probs = pcb.place_and_route(b, move=[], router="freerouting" if freerouting.kicad_available()
                                        and freerouting.available() else "harness")   # (a run's tries; no part moves)
            short = lambda ps: sum(1 for p_ in ps if " short " in p_ or "apart (clearance" in p_)
            after = b.check()
            if short(after) > short(before) or len(probs) + len(after) >= len(before):   # (no better: as it was)
                self.undo.pop()
                b.tracks, b.vias = keep
                b.__dict__.pop("_cu_memo", None)
                got = self._after(b, t0, [], [])
                got["unrouted"] = probs or ["routing it again did not leave fewer problems: move the part again"]
                return got
            return self._after(b, t0, torn, probs)

    def script(self) -> Optional[str]:
        for f in ("result.json", "progress.json"):
            p = self.out / f
            if p.exists():
                try:
                    s = json.loads(p.read_text()).get("script")
                    if s:
                        return s
                except ValueError:
                    pass
        return None

    def board(self) -> dict:
        """the board as the script builds it, with the last run's placement and copper (out/board_state.pkl)"""
        import pcb_cli
        st = self.out / pcb_cli.STATE
        script = self.script()
        if not script:
            return {"error": "no run in this folder yet (result.json names the board script)"}
        key = _board_key(self.out, script)
        with self.lock:
            if key != self.key:                          # (the run's own board.json when it is this board: no rebuild)
                try:
                    saved = json.loads((self.out / "board.json").read_text())
                    if saved.get("key") == list(key):
                        self.data, self.key = saved["data"], key
                        return self.data
                except (OSError, ValueError):
                    pass
                if script.endswith(".net"):              # (a netlist: as the command line read it, by default)
                    from types import SimpleNamespace
                    b, _ = pcb_cli.load_netlist(script, SimpleNamespace(size=None, layers=2, at=None, amps=None))
                else:
                    b, _ = pcb_cli.load(script)
                pcb_cli.load_state(b, str(self.out), pcb_cli.design_key(b))
                self.data, self.key = board_data(b), key
            return self.data


def compact_glb(path: Path) -> None:
    """KiCad's glTF holds one primitive per copper shape (~40 000 on a flight controller: 15 s for a browser to
    parse); each mesh's primitives of one material merged into one (a few hundred), the same triangles"""
    import struct
    import numpy as np
    raw = path.read_bytes()
    jl = struct.unpack_from("<I", raw, 12)[0]
    j = json.loads(raw[20:20 + jl])
    if j.get("asset", {}).get("extras", {}).get("prometheus_compact"):
        return
    bl = struct.unpack_from("<I", raw, 20 + jl)[0]
    blob = memoryview(raw)[28 + jl:28 + jl + bl]
    size = {5126: 4, 5125: 4, 5123: 2, 5121: 1}
    kind = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def read(i):
        a = j["accessors"][i]
        v = j["bufferViews"][a["bufferView"]]
        n, c, t = a["count"], comps[a["type"]], a["componentType"]
        st = v.get("byteStride") or size[t] * c
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        rows = np.frombuffer(blob, np.uint8, count=(n - 1) * st + size[t] * c, offset=start)
        rows = np.lib.stride_tricks.as_strided(rows, (n, size[t] * c), (st, 1))
        return np.ascontiguousarray(rows).view(kind[t]).reshape(n, c) if c > 1 else \
            np.ascontiguousarray(rows).view(kind[t]).reshape(n)
    out, views, accs = bytearray(), [], []

    def put(arr, target, minmax=False):
        while len(out) % 4:
            out.append(0)
        views.append({"buffer": 0, "byteOffset": len(out), "byteLength": arr.nbytes, "target": target})
        out.extend(arr.tobytes())
        a = {"bufferView": len(views) - 1, "componentType": {np.float32: 5126, np.uint32: 5125}[arr.dtype.type],
             "count": arr.shape[0], "type": "VEC3" if arr.ndim == 2 else "SCALAR"}
        if minmax:
            a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1
    for m in j["meshes"]:
        groups = {}
        for pr in m["primitives"]:
            groups.setdefault((pr.get("material"), pr.get("mode", 4)), []).append(pr)
        prims = []
        for (mat, mode), prs in groups.items():
            pos, nor, idx, base = [], [], [], 0
            for pr in prs:
                p_ = read(pr["attributes"]["POSITION"])
                pos.append(p_)
                nor.append(read(pr["attributes"]["NORMAL"]) if "NORMAL" in pr["attributes"] else np.zeros_like(p_))
                idx.append((read(pr["indices"]).astype(np.uint32) if "indices" in pr else
                            np.arange(len(p_), dtype=np.uint32)) + base)
                base += len(p_)
            np_ = {"attributes": {"POSITION": put(np.concatenate(pos).astype(np.float32), 34962, True),
                                  "NORMAL": put(np.concatenate(nor).astype(np.float32), 34962)},
                   "indices": put(np.concatenate(idx), 34963), "mode": mode}
            if mat is not None:
                np_["material"] = mat
            prims.append(np_)
        m["primitives"] = prims
    j["accessors"], j["bufferViews"], j["buffers"] = accs, views, [{"byteLength": len(out)}]
    j.setdefault("asset", {}).setdefault("extras", {})["prometheus_compact"] = 1
    js = json.dumps(j).encode()
    js += b" " * (-len(js) % 4)
    out += b"\0" * (-len(out) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(out), b"BIN\0") + bytes(out)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def _board_key(out: Path, script: str) -> tuple:
    """what the page's board depends on: the script, the run's saved state, the script's edits"""
    import pcb_cli
    st = Path(out) / pcb_cli.STATE
    return (script, st.stat().st_mtime if st.exists() else 0, Path(script).stat().st_mtime)


def write_board(b, out: str, script: str) -> None:
    """the page's board, as the run leaves it: out/board.json (so the page opens without rebuilding the board)"""
    (Path(out) / "board.json").write_text(json.dumps({"key": list(_board_key(Path(out), script)), "data": board_data(b)}))


def _render3d(out: Path, script: str) -> dict:
    """KiCad's own 3D render of the exported board (its parts' library models): out/render_iso.png"""
    import pcb
    import pcb_cli
    kpcbs = list((out / "kicad").glob("*.kicad_pcb"))
    if not kpcbs:
        return {"error": "no KiCad board in this folder yet (the export stage writes it)"}
    ver = pcb.kicad_version()
    if not ver:
        return {"error": "KiCad is not installed: the interactive view above is the harness's own"}
    b, _ = pcb_cli.load(script)
    models = out / "models3d"
    pcb.kicad_models(b, str(models))
    png = out / "render_iso.png"
    env = dict(os.environ)
    for v in ("KICAD8_3DMODEL_DIR", "KICAD9_3DMODEL_DIR", "KICAD10_3DMODEL_DIR"):
        env[v] = str(models)
    import subprocess
    r = subprocess.run(["kicad-cli", "pcb", "render", "--side", "top", "-w", "1600", "-h", "1000", "--perspective",
                        "--rotate", "-50,0,-25", "--zoom", "1.1", "--quality", "high", "--background", "opaque",
                        "-o", str(png), str(kpcbs[0])], env=env, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not png.exists():
        return {"error": "KiCad's render failed: " + (r.stderr or r.stdout)[-300:]}
    return {"png": "/files/render_iso.png?t=%d" % int(time.time())}


def _model(out: Path, script: str) -> dict:
    """the board as KiCad exports it in 3D (binary glTF): the board, copper, pads, mask, silkscreen and every part's
    library model, for the page's 3D view; cached as out/board.glb until the KiCad board changes"""
    import subprocess
    import pcb
    import pcb_cli
    kpcbs = list((out / "kicad").glob("*.kicad_pcb"))
    if not kpcbs:
        return {"error": "no KiCad board in this folder yet"}
    glb = out / "board.glb"
    if not glb.exists() or glb.stat().st_mtime < kpcbs[0].stat().st_mtime:
        if not pcb.kicad_version():                      # (asked only to export: kicad-cli takes 0.2 s to answer)
            return {"error": "KiCad is not installed: the 3D view shows the bare board"}
        b, _ = pcb_cli.load(script)
        models = out / "models3d"
        pcb.kicad_models(b, str(models))
        env = dict(os.environ)
        for v in ("KICAD8_3DMODEL_DIR", "KICAD9_3DMODEL_DIR", "KICAD10_3DMODEL_DIR"):
            env[v] = str(models)
        r = subprocess.run(["kicad-cli", "pcb", "export", "glb", "--force", "--subst-models", "--include-tracks",
                            "--include-pads", "--include-zones", "--include-silkscreen", "--include-soldermask",
                            "-o", str(glb), str(kpcbs[0])], env=env, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not glb.exists():
            return {"error": "KiCad's 3D export failed: " + (r.stderr or r.stdout)[-300:]}
    compact_glb(glb)                                     # (a no-op once done)
    return {"glb": "/files/board.glb?t=%d" % int(glb.stat().st_mtime)}


def _sch_pins(path) -> list:
    """each part's pin ends on a KiCad schematic sheet, sheet mm (y down, as its SVG): [{ref, num, x, y}]; the power
    symbols left out. A symbol's pins are in its own frame, y up: turned by its angle and mirrored as it is placed"""
    import pcb
    t = pcb._sx_parse(Path(path).read_text())
    find = lambda n, k: [x for x in n if isinstance(x, list) and x and x[0] == k]
    libs = {x[1].strip('"'): x for x in find((find(t, "lib_symbols") or [[]])[0], "symbol")}
    out = []
    for inst in find(t, "symbol"):
        lid = find(inst, "lib_id")
        if not lid or lid[0][1].strip('"').startswith("power:") or lid[0][1].strip('"') not in libs:
            continue
        at = find(inst, "at")[0]
        X, Y, A = float(at[1]), float(at[2]), math.radians(float(at[3]) if len(at) > 3 else 0.0)
        mir = (find(inst, "mirror") or [[None, ""]])[0][1]
        unit = int(find(inst, "unit")[0][1]) if find(inst, "unit") else 1
        ref = next((q[2].strip('"') for q in find(inst, "property") if q[1].strip('"') == "Reference"), "")
        for sub in find(libs[lid[0][1].strip('"')], "symbol"):
            m = re.search(r"_(\d+)_\d+$", sub[1].strip('"'))
            if m and int(m.group(1)) not in (0, unit):
                continue
            for q in find(sub, "pin"):
                pa = find(q, "at")[0]
                x, y = float(pa[1]) * (-1 if mir == "y" else 1), -float(pa[2]) * (-1 if mir == "x" else 1)
                out.append({"ref": ref, "num": find(q, "number")[0][1].strip('"'),
                            "x": round(X + x * math.cos(A) + y * math.sin(A), 3),
                            "y": round(Y - x * math.sin(A) + y * math.cos(A), 3)})
    return out


def _schematic(out: Path, script: str = "") -> dict:
    """the board's KiCad schematic as KiCad draws it (``kicad-cli sch export svg``): the sheet, or the cover and each
    page of a larger design; cached as out/kicad/<name>[-<page>].svg until the schematic changes"""
    import subprocess
    from urllib.parse import quote
    import pcb
    schs = sorted((out / "kicad").glob("*.kicad_sch"), key=lambda f: len(f.name))   # (the root: pages are <name>_<n>)
    if not schs:
        return {"error": "no KiCad schematic in this folder yet"}
    svg = schs[0].with_suffix(".svg")
    num = lambda f: int((re.match(r"\d+", f.stem[len(svg.stem) + 1:]) or re.match(r"", "")).group() or 0)
    pages = lambda: sorted(svg.parent.glob(svg.stem + "-*.svg"), key=num)
    if not svg.exists() or svg.stat().st_mtime < max(f.stat().st_mtime for f in schs):
        if not pcb.kicad_version():                      # (asked only to draw: kicad-cli takes 0.2 s to answer)
            return {"error": "KiCad is not installed: the schematic view needs it to draw the sheet"}
        for f in pages():                                # (a page gone from the design: its drawing too)
            f.unlink()
        r = subprocess.run(["kicad-cli", "sch", "export", "svg", "-o", str(schs[0].parent), str(schs[0])],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not svg.exists():
            return {"error": "KiCad's schematic export failed: " + (r.stderr or r.stdout)[-300:]}
    sheets = [svg] + (pages() if len(schs) > 1 else [])
    sizes = []
    for f in sheets:                                     # (each sheet's size from its head: the page need not parse it)
        with open(f, "rb") as fh:
            vb = re.search(rb'viewBox="([\d.\s-]+)"', fh.read(4096))
        sizes.append([float(v) for v in vb.group(1).split()[2:4]] if vb else [0.0, 0.0])
    # (each sheet's part pins, for the pin marks: the root, then its pages in their order)
    src = [schs[0]] + (sorted(schs[1:], key=lambda f: int((re.search(r"_(\d+)$", f.stem) or [0, 0])[1])) if len(schs) > 1 else [])
    try:
        pins = [_sch_pins(f) for f in src] if len(src) == len(sheets) else [[] for _ in sheets]
    except Exception:                                    # (marks are an aid: a sheet they cannot read keeps none)
        pins = [[] for _ in sheets]
    return {"svgs": ["/files/kicad/%s?t=%d" % (quote(f.name), int(f.stat().st_mtime)) for f in sheets], "sizes": sizes,
            "pins": pins}


def _boards_file() -> Path:
    from vendor_parts import CACHE
    return CACHE / "boards.json"


def _throwaway(out: str) -> bool:
    import tempfile
    return out.startswith(str(Path(tempfile.gettempdir()).resolve())) or "/pytest-of-" in out


def remember(out_dir) -> None:
    """this board's run folder in the list the viewer's switcher shows (every board a viewer has opened, newest first)"""
    out = str(Path(out_dir).resolve())
    if _throwaway(out):                                  # (a test's or a temporary folder: not a board to come back to)
        return
    f = _boards_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "w") as lk:        # (two viewers starting at once: both kept, neither write lost)
        try:
            import fcntl
            fcntl.flock(lk, fcntl.LOCK_EX)
        except ImportError:                              # (Windows: no flock; a race there loses one entry, said here)
            pass
        try:
            known = [o for o in json.loads(f.read_text()) if o != out] if f.exists() else []
        except Exception:
            known = []
        f.write_text(json.dumps([out] + known[:49]))


def boards(current: Path) -> list:
    """the boards in the switcher: each remembered run folder still holding a run, with its name, verdict and age"""
    f = _boards_file()
    try:
        outs = json.loads(f.read_text()) if f.exists() else []
    except Exception:
        outs = []
    got = []
    for o in outs:
        p = Path(o) / "result.json"
        if not p.exists():
            continue
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        got.append({"out": o, "name": r.get("board") or Path(o).parent.name, "folder": Path(r.get("script") or o).parent.name,
                    "clean": bool(r.get("clean")), "time": p.stat().st_mtime, "current": Path(o) == current})
    return sorted(got, key=lambda b: -b["time"])


def make_handler(out_dir: str):
    out = Path(out_dir).resolve()
    cache = _Cache(str(out))
    cache.watch()
    remember(out)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):                       # (quiet: the run's own output stays readable)
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, d, code=200):
            self._send(code, json.dumps(d).encode())

        def do_POST(self):                               # (a live edit: /api/move, /api/undo, /api/route)
            path = self.path.split("?")[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/move":
                    got = cache.move(str(body["ref"]), body["at"], body.get("rot"), body.get("side"))
                elif path == "/api/undo":
                    got = cache.undo_move()
                elif path == "/api/route":
                    got = cache.route_rest()
                else:
                    return self._json({"ok": False, "error": "no such action"}, 404)
                return self._json(got, 200 if got["ok"] else 400)
            except (KeyError, ValueError, TypeError) as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)

        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if path == "/fonts.css":
                    return self._send(200, (PAGE.parent / "fonts.css").read_bytes(), "text/css")
                if path == "/three.min.js":
                    return self._send(200, (PAGE.parent / "three.min.js").read_bytes(), "text/javascript")
                if path == "/api/progress":
                    p = out / "progress.json"
                    if not p.exists():
                        return self._json({"done": True, "stage": "none"})
                    d = json.loads(p.read_text())
                    if not d.get("done"):                # (elapsed counts on while the page waits)
                        d["elapsed"] = round(time.time() - d["started"], 1)
                        try:                             # (a run that was stopped: said so, not "routing" for ever)
                            os.kill(d.get("pid") or 0, 0) if d.get("pid") else None
                        except ProcessLookupError:
                            d.update(done=True, stopped=True, stage="stopped")
                    return self._json(d)
                if path == "/api/result":
                    p = out / "result.json"
                    return self._json(json.loads(p.read_text()) if p.exists() else {})
                if path == "/api/board":
                    return self._json(cache.board())
                if path == "/api/boards":               # (the switcher: every board a viewer has opened)
                    return self._json({"boards": boards(out)})
                if path == "/api/open":                 # (?out= one of those: its own viewer, reused or started)
                    import urllib.parse as up
                    import urllib.request
                    want = up.parse_qs(self.path.split("?", 1)[-1]).get("out", [""])[0]
                    if want not in {b["out"] for b in boards(out)}:
                        return self._json({"error": "not a board this viewer knows"})
                    url = launch(want, open_browser=False)
                    for _ in range(40):                  # (a viewer just started: up in a second or two)
                        try:
                            urllib.request.urlopen(url, timeout=0.5)
                            break
                        except Exception:
                            time.sleep(0.25)
                    return self._json({"url": url})
                if path == "/api/live":                 # (the script's saved edits: rev goes up with each one shown)
                    return self._json({"rev": cache.rev, **cache.last})
                if path == "/api/datasheet":            # (?ref=U1: its pins against its datasheet)
                    import urllib.parse as up
                    ref = up.parse_qs(self.path.split("?", 1)[-1]).get("ref", [""])[0]
                    import parts
                    sym = next((c["symbol"]["name"] for c in cache.board().get("comps", [])
                                if c["ref"] == ref and c.get("symbol")), None)
                    if not sym:
                        return self._json({"error": "no symbol"})
                    try:
                        return self._json(parts.datasheet_check(sym))
                    except Exception as e:
                        return self._json({"error": f"{type(e).__name__}: {str(e)[:120]}"})
                if path == "/api/model":
                    s = cache.script()
                    with cache.lock:
                        return self._json(_model(out, s) if s else {"error": "no run in this folder yet"})
                if path == "/api/schematic":
                    with cache.lock:
                        return self._json(_schematic(out))
                if path in ("/pdf.min.js", "/pdf.worker.min.js"):        # (PDF.js, for the datasheet panel)
                    return self._send(200, (PAGE.parent / path[1:]).read_bytes(), "text/javascript")
                if path == "/api/evidence":             # (?ref=U1: its wired pins; ?cites=1: the citations)
                    import urllib.parse as up
                    q = up.parse_qs(self.path.split("?", 1)[-1])
                    return self._json(_evidence(cache, out, q.get("ref", [""])[0], bool(q.get("cites"))))
                if path == "/api/datasheet.pdf":        # (?ref=U1, or ?url= one the board cites: the PDF itself)
                    import urllib.parse as up
                    q = up.parse_qs(self.path.split("?", 1)[-1])
                    pdf = _datasheet_pdf(cache, out, q.get("ref", [""])[0], q.get("url", [""])[0])
                    if pdf is None:
                        return self._send(404, b"no datasheet", "text/plain")
                    return self._send(200, pdf.read_bytes(), "application/pdf")
                if path == "/GLTFLoader.js":
                    return self._send(200, (PAGE.parent / "GLTFLoader.js").read_bytes(), "text/javascript")
                if path == "/api/render3d":
                    s = cache.script()
                    return self._json(_render3d(out, s) if s else {"error": "no run in this folder yet"})
                if path.startswith("/files/"):
                    from urllib.parse import unquote
                    f = (out / unquote(path[len("/files/"):])).resolve()
                    if out not in f.parents or not f.is_file():
                        return self._send(404, b"not found", "text/plain")
                    ct = {".png": "image/png", ".html": "text/html", ".svg": "image/svg+xml",
                          ".csv": "text/csv", ".json": "application/json", ".glb": "model/gltf-binary"}.get(f.suffix, "application/octet-stream")
                    return self._send(200, f.read_bytes(), ct)
                return self._send(404, b"not found", "text/plain")
            except Exception as e:                       # (the page shows the error; the server stays up)
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    return H


def serve(out_dir: str, port: int = 0, open_browser: bool = True, block: bool = True):
    """serve the page for ``out_dir`` on localhost (``port`` 0: any free one); returns the server and its URL"""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(out_dir))
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"viewer: {url}  (Ctrl-C to stop)", file=sys.stderr, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if block:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            srv.shutdown()
    return srv, url


def launch(out_dir: str, open_browser: bool = True) -> Optional[str]:
    """``prometheus view OUT_DIR`` in the background, once per folder: it opens the browser and outlives the run; a viewer
    already up for the folder is reused (its page follows the new run), so a run-edit-run loop opens one tab"""
    import socket
    import subprocess
    import urllib.request
    f = Path(out_dir) / "viewer.json"
    here = Path(__file__).resolve().parent              # (the harness's code: a viewer started on older code is
    code = max(q.stat().st_mtime for q in [*here.glob("*.py"), PAGE])  # replaced, not reused)
    try:
        v = json.loads(f.read_text())
        if v.get("code") == code:
            urllib.request.urlopen(v["url"], timeout=1)
            return v["url"]
        if v.get("pid"):
            os.kill(v["pid"], 15)
    except Exception:
        pass
    with socket.socket() as s:                           # (a free port, so the address is known before it starts)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    pr = subprocess.Popen([sys.executable, "-m", "pcb_cli", "view", str(Path(out_dir).resolve()), "--port", str(port)]
                          + ([] if open_browser else ["--no-open"]),       # (the switcher: the page goes there itself)
                          cwd=os.path.dirname(os.path.abspath(__file__)), start_new_session=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f.write_text(json.dumps({"url": url, "pid": pr.pid, "code": code}))
    return url


def main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="prometheus view", description="look at a board run's folder in a browser")
    ap.add_argument("out", help="the run's output folder (the one result.json is in)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true", help="do not open a browser (print the address)")
    a = ap.parse_args(argv)
    if not (Path(a.out) / "result.json").exists() and not (Path(a.out) / "progress.json").exists():
        print(f"{a.out}: no run here yet: prometheus board.py {a.out} first", file=sys.stderr)
        return 2
    serve(a.out, a.port, not a.no_open)
    return 0
