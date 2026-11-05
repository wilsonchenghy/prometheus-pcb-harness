"""``prometheus view out/``: a local page to look at a board - its layout (layers to switch on and off), a 3D view,
a net-label schematic, every finding (click one: the board zooms to it) - and, while a run is going
(``prometheus board.py out/ --view``), its stage, the try it is on and about how long is left.

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
                  "tries": None, "tries_done": 0, "try_times": [], "workers": 1, "parts": parts, "stages": []}
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
        elif ev.get("kind") == "try":
            self.d["tries_done"] += 1
            self.d["try_times"].append(ev.get("seconds", 0.0))
            self.d["last_try"] = ev.get("line", "")
            if ev.get("clean"):
                self.d["tries"] = self.d["tries_done"]      # (a clean try ends the search)
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
        export = 2.0
        if d["stage"] in ("place+route", "place", "route"):
            times = d["try_times"]
            t_try = (sum(times) / len(times)) if times else 3.0 + 0.3 * d.get("parts", 10)
            n = d["tries"] or 1
            left = max(0, n - d["tries_done"])
            w = max(1, d["workers"])
            batches = 0 if left == 0 else (1 if d["tries_done"] == 0 else math.ceil(left / w))
            if d["tries_done"] == 0:               # (the first try runs alone; the rest only if it is not clean)
                started = time.time() - self.t0 - (d["stages"][-1]["start"] if d["stages"] else 0)
                return max(0.5, t_try - started) + export
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


def board_data(b) -> dict:
    """everything the page draws, board mm (y up)"""
    pn = b.pad_net()
    comps = []
    shapes = b.pad_shapes()
    by_ref = {}
    for ref, num, L, g, d in shapes:
        by_ref.setdefault(ref, []).append({"num": num, "layer": L, "net": pn.get((ref, num)), "poly": _poly(g, 0.005),
                                           "drill": d, "at": [round(g.centroid.x, 3), round(g.centroid.y, 3)]})
    for ref, c in b.comps.items():
        cy = b.courtyard(ref)
        comps.append({"ref": ref, "value": c.get("value", ""), "mpn": c.get("mpn", ""), "fp": c["fp"],
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
    if not pcb.kicad_version():
        return {"error": "KiCad is not installed: the 3D view shows the bare board"}
    glb = out / "board.glb"
    if not glb.exists() or glb.stat().st_mtime < kpcbs[0].stat().st_mtime:
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


def _schematic(out: Path, script: str = "") -> dict:
    """the board's KiCad schematic as KiCad draws it (``kicad-cli sch export svg``): the sheet, or the cover and each
    page of a larger design; cached as out/kicad/<name>[-<page>].svg until the schematic changes"""
    import subprocess
    from urllib.parse import quote
    import pcb
    schs = sorted((out / "kicad").glob("*.kicad_sch"), key=lambda f: len(f.name))   # (the root: pages are <name>_<n>)
    if not schs:
        return {"error": "no KiCad schematic in this folder yet"}
    if not pcb.kicad_version():
        return {"error": "KiCad is not installed: the schematic view needs it to draw the sheet"}
    svg = schs[0].with_suffix(".svg")
    num = lambda f: int((re.match(r"\d+", f.stem[len(svg.stem) + 1:]) or re.match(r"", "")).group() or 0)
    pages = lambda: sorted(svg.parent.glob(svg.stem + "-*.svg"), key=num)
    if not svg.exists() or svg.stat().st_mtime < max(f.stat().st_mtime for f in schs):
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
    return {"svgs": ["/files/kicad/%s?t=%d" % (quote(f.name), int(f.stat().st_mtime)) for f in sheets], "sizes": sizes}


def make_handler(out_dir: str):
    out = Path(out_dir).resolve()
    cache = _Cache(str(out))

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
                    return self._json(d)
                if path == "/api/result":
                    p = out / "result.json"
                    return self._json(json.loads(p.read_text()) if p.exists() else {})
                if path == "/api/board":
                    return self._json(cache.board())
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


def launch(out_dir: str) -> Optional[str]:
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
    pr = subprocess.Popen([sys.executable, "-m", "pcb_cli", "view", str(Path(out_dir).resolve()), "--port", str(port)],
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
        print(f"{a.out}: no run here yet: prometheus board.py {a.out} first (or add --view to that run)", file=sys.stderr)
        return 2
    serve(a.out, a.port, not a.no_open)
    return 0
