"""A KiCad layout (``.kicad_pcb``, KiCad 6 to 10) as a board script, for a revision of a board designed elsewhere.

    prometheus import design.kicad_pcb myboard/       # myboard/board.py, routes.json, footprints/

``board.py`` is an ordinary board script: every part at its place on the design's own footprints (written to
``footprints/``, as the board file carries them), every net, the zones as pours, the outline, the silkscreen text.
The routed copper is in ``routes.json``: ``routes()`` puts it back, all of it while the parts stay where they were;
once parts move, the nets they touch are routed again and the rest keep their copper (``pcb.reuse_routes``).
The design itself is kept in ``source/``; every run writes it back revised (``revise``: out/revised/), the designer's
own file with only what the revision changed changed.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List

import pcb
from pcb import _Q, _find, _sx_dump, _sx_parse

CU = {"F.Cu": "F", "B.Cu": "B", "In1.Cu": "1", "In2.Cu": "2"}
MPN_KEYS = ("mpn", "manufacturer_part_number", "part number", "manufacturer part number", "mfr part", "mfr. part #")
LCSC_KEYS = ("lcsc", "lcsc part", "lcsc part #", "jlcpcb", "jlcpcb part #", "jlc")
_DROP = {"uuid", "tstamp", "path", "sheetname", "sheetfile", "units", "locked", "net", "at"}


def _arc(p0, pm, p1, step: float = 0.02):
    """points along the circle through p0, pm, p1 (KiCad's start / mid / end), from p0 to p1"""
    (ax, ay), (bx, by), (cx, cy) = p0, pm, p1
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [p0, p1]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, am, a1 = (math.atan2(y - uy, x - ux) for x, y in (p0, pm, p1))
    sweep = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:                # (the mid point says which way round)
        sweep -= 2 * math.pi
    n = max(2, int(abs(sweep) * r / step) + 1)
    return [(ux + r * math.cos(a0 + sweep * k / n), uy + r * math.sin(a0 + sweep * k / n)) for k in range(n + 1)]


def _xy(node, key):
    f = _find(node, key)
    return (float(f[0][1]), float(f[0][2])) if f else None


def _pts(node):
    """a pts list's points, arcs walked"""
    out = []
    for q in _find(node, "pts")[0][1:] if _find(node, "pts") else []:
        if q[0] == "xy":
            out.append((float(q[1]), float(q[2])))
        elif q[0] == "arc":
            out += _arc(_xy(q, "start"), _xy(q, "mid"), _xy(q, "end"))[0 if not out else 1:]
    return out


def _library_footprint(fp, rot: float, back: bool):
    """a placed footprint back in its library frame: the inverse of ``pcb._kicad_footprint`` (pad and text angles
    made relative again, a bottom part's flip and layers undone, the board's own items dropped). ``rot``: the angle
    KiCad gives it (a bottom part's: the harness's plus 180)"""
    flip = lambda s: _Q(("B." + s[2:]) if s.startswith("F.") else ("F." + s[2:]) if s.startswith("B.") else s)

    def walk(node, owner):
        out = [node[0]]
        for v in node[1:]:
            if not isinstance(v, list) or not v:
                out.append(v)
            elif v[0] == "model":
                out.append(v)
            elif v[0] in ("uuid", "tstamp", "net"):
                continue
            elif v[0] == "at" and owner in ("pad", "property", "fp_text"):
                y, a = float(v[2]), float(v[3]) if len(v) > 3 and v[3] != "unlocked" else 0.0   # (absolute)
                a = (rot - a) if back else (a - rot)
                out.append(["at", float(v[1]), -y if back else y, round(a % 360.0, 6)])
            elif v[0] in ("at", "start", "end", "mid", "center", "xy") and len(v) >= 3:
                out.append([v[0], float(v[1]), -float(v[2]) if back else float(v[2])] + v[3:])
            elif v[0] == "angle" and back:
                out.append(["angle", -float(v[1])])
            elif v[0] in ("layer", "layers") and back:
                out.append([v[0]] + [flip(s) if isinstance(s, str) else s for s in v[1:]])
            elif v[0] == "justify" and back and owner == "effects":
                out.append([w for w in v if w != "mirror"])
            else:
                out.append(walk(v, v[0]))
        return out
    body = [v for v in fp[2:] if not (isinstance(v, list) and v and v[0] in _DROP)]
    return walk(["footprint", _Q(fp[1].split(":")[-1])] + body, "footprint")


def read(path: str, fp_dir: str):
    """(Board, routes, notes): the layout in ``path`` as a Board, its routed copper as ``pcb.reuse_routes`` takes it,
    and what could not be carried over. The design's footprints are written into ``fp_dir``"""
    tree = _sx_parse(Path(path).read_text(encoding="utf-8"))
    notes: List[str] = []
    numbered = {str(n[1]): str(n[2]) for n in _find(tree, "net") if len(n) > 2}

    def net_of(node):                                    # (KiCad 10: (net "GND"); before: (net 3 "GND") or (net 3))
        nn = _find(node, "net_name")
        if nn:
            return str(nn[0][1])
        n = _find(node, "net")
        if not n:
            return ""
        return str(n[0][2]) if len(n[0]) > 2 else (str(n[0][1]) if isinstance(n[0][1], _Q) else numbered.get(str(n[0][1]), ""))
    cu_layers = [str(r[1]) for lay in _find(tree, "layers") for r in lay[1:] if str(r[1]).endswith(".Cu")]
    if len(cu_layers) not in (2, 4):
        raise ValueError(f"{path}: {len(cu_layers)} copper layers; Prometheus takes 2 or 4")
    # the outline: Edge.Cuts' lines, arcs, rectangles, circles and polygons, joined
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    segs = []
    for g in tree:
        if not (isinstance(g, list) and g and str(g[0]).startswith("gr_") and _find(g, "layer")
                and _find(g, "layer")[0][1] == "Edge.Cuts"):
            continue
        k = g[0]
        if k == "gr_line":
            segs.append([_xy(g, "start"), _xy(g, "end")])
        elif k == "gr_arc":
            segs.append(_arc(_xy(g, "start"), _xy(g, "mid"), _xy(g, "end")))
        elif k == "gr_rect":
            (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
            segs.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
        elif k == "gr_circle":
            (cx, cy), (ex, ey) = _xy(g, "center"), _xy(g, "end")
            r = math.hypot(ex - cx, ey - cy)
            segs.append([(cx + r * math.cos(a / 64 * 2 * math.pi), cy + r * math.sin(a / 64 * 2 * math.pi))
                         for a in range(65)])
        elif k == "gr_poly":
            p = _pts(g)
            segs.append(p + p[:1])
    lines = [LineString([(round(x, 3), round(y, 3)) for x, y in s]) for s in segs if len(s) > 1]
    polys = sorted(polygonize(unary_union(lines)), key=lambda p: -p.area) if lines else []
    if not polys:
        raise ValueError(f"{path}: no closed board outline on Edge.Cuts")
    edge = polys[0]
    cuts = [p for p in polys[1:] if Polygon(edge.exterior).contains(p.representative_point())
            and not any(q.contains(p.representative_point()) for q in polys[1:] if q is not p and q.area > p.area)]
    X0, _, _, Y1 = edge.bounds
    tx = lambda x, y: (round(float(x) - X0, 6), round(Y1 - float(y), 6))     # (KiCad's page, y down -> board mm, y up)
    outline = [tx(x, y) for x, y in list(edge.exterior.coords)[:-1]]
    # the board's rules: the project's default clearance and edge gap, the stackup's copper
    pro = Path(path).with_suffix(".kicad_pro")
    clear, edge_gap, hole_gap, hole_clear, kicad_rules = 0.2, None, None, None, {}
    if pro.exists():
        pj = json.loads(pro.read_text())
        cls = (pj.get("net_settings") or {}).get("classes") or []
        d = next((c for c in cls if c.get("name") == "Default"), cls[0] if cls else {})
        clear = float(d.get("clearance", clear))
        rules = ((pj.get("board") or {}).get("design_settings") or {}).get("rules") or {}
        edge_gap, hole_gap = rules.get("min_copper_edge_clearance"), rules.get("min_hole_to_hole")
        hole_clear = rules.get("min_hole_clearance")
        kicad_rules = {"rules": rules, "rule_severities":
                       ((pj.get("board") or {}).get("design_settings") or {}).get("rule_severities") or {}}
    else:
        notes.append(f"no {pro.name} beside it: its rules unknown, clearance {clear} mm taken (set Board(clearance=...))")
    thick = float(_find(_find(tree, "general")[0], "thickness")[0][1]) if _find(tree, "general") else 1.6
    oz = 1.0
    for s in (st for su in _find(tree, "setup") for st in _find(su, "stackup")):
        t_ = [float(_find(l_, "thickness")[0][1]) for l_ in _find(s, "layer") if l_[1] == "F.Cu" and _find(l_, "thickness")]
        if t_:
            oz = max(0.5, round(t_[0] / pcb.OZ_MM * 2) / 2)
    b = pcb.Board(Path(path).stem, outline, thickness=thick, copper_oz=oz, clearance=clear, layers=len(cu_layers),
                  corner_r=0)
    if edge_gap is not None:
        b.edge = float(edge_gap)
    if hole_gap is not None:
        b.hole_gap = float(hole_gap)
    if hole_clear is not None:
        b.hole_clear = float(hole_clear)
    b.kicad_rules = kicad_rules
    for p in cuts:
        b.cutout([tx(x, y) for x, y in list(p.exterior.coords)[:-1]])
    b.graphics = []

    def art(g, place=None):                              # (a stroke or shape on the silkscreen or the mask: kept)
        lay = str(_find(g, "layer")[0][1]) if _find(g, "layer") else ""
        if lay not in ("F.SilkS", "B.SilkS", "F.Mask", "B.Mask"):
            return False
        k = str(g[0])[3:]
        if k == "line":
            pts, closed = [_xy(g, "start"), _xy(g, "end")], False
        elif k == "arc":
            pts, closed = _arc(_xy(g, "start"), _xy(g, "mid"), _xy(g, "end"), 0.05), False
        elif k == "rect":
            (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
            pts, closed = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True
        elif k == "circle":
            (cx, cy), (ex, ey) = _xy(g, "center"), _xy(g, "end")
            r_ = math.hypot(ex - cx, ey - cy)
            pts, closed = [(cx + r_ * math.cos(i / 64 * 2 * math.pi), cy + r_ * math.sin(i / 64 * 2 * math.pi))
                           for i in range(64)], True
        elif k == "poly":
            pts, closed = _pts(g), True
        else:                                            # (a bezier curve: not carried)
            return False
        if place is not None:                            # (a footprint's own: its frame -> the page)
            ax, ay, a = place
            pts = [(ax + x * math.cos(a) + y * math.sin(a), ay - x * math.sin(a) + y * math.cos(a)) for x, y in pts]
        st = _find(g, "stroke")
        w = _find(st[0], "width") if st else _find(g, "width")
        fill = _find(g, "fill")
        b.graphics.append({"layer": lay, "pts": [tx(x, y) for x, y in pts], "w": float(w[0][1]) if w else 0.0,
                           "closed": closed, "fill": bool(fill) and str(fill[0][1]) in ("yes", "solid")})
        return True
    # the parts, each on the footprint the board carries
    fp_root = Path(fp_dir)
    if str(fp_root) not in map(str, pcb.FP_DIRS):
        pcb.FP_DIRS.insert(0, fp_root)
    pins: Dict[str, list] = {}
    seen, written = set(), set()
    for fp in _find(tree, "footprint"):
        props = {str(p[1]): str(p[2]) for p in _find(fp, "property") if len(p) > 2}
        props.update({("Reference" if t[1] == "reference" else "Value"): str(t[2]) for t in _find(fp, "fp_text")
                      if len(t) > 2 and t[1] in ("reference", "value")})
        pads = [p for p in _find(fp, "pad") if str(p[1])]
        drills = [p for p in _find(fp, "pad") if _find(p, "drill")]
        if not pads and drills and not any("oval" in _find(p, "drill")[0] for p in drills):   # (a mounting hole: a
                                                         # board hole at each drill; a slot stays a footprint)
            at = _find(fp, "at")[0]
            a = math.radians(float(at[3]) if len(at) > 3 else 0.0)
            for p in drills:
                lx, ly = _xy(p, "at")
                d = float(next(v for v in _find(p, "drill")[0][1:] if v != "oval"))
                b.hole(tx(float(at[1]) + lx * math.cos(a) + ly * math.sin(a),
                          float(at[2]) - lx * math.sin(a) + ly * math.cos(a)), d)
            continue
        if not pads and not drills:                      # (a logo: its strokes and shapes, as board artwork)
            at = _find(fp, "at")[0]
            place = (float(at[1]), float(at[2]), math.radians(float(at[3]) if len(at) > 3 else 0.0))
            kept = [art(g, place) for g in fp if isinstance(g, list) and g and str(g[0]).startswith("fp_")
                    and g[0] != "fp_text"]
            if not any(kept):
                notes.append(f"{props.get('Reference', fp[1])} ({fp[1]}): nothing on the silkscreen or mask: left out")
            continue
        ref = props.get("Reference", "")
        if not ref or "*" in ref or ref in seen:
            stem = re.sub(r"[^A-Za-z]", "", ref) or "X"
            ref = next(f"{stem}{i}" for i in range(1, 10000) if f"{stem}{i}" not in seen)
        seen.add(ref)
        name = str(fp[1]) if ":" in str(fp[1]) else f"Imported:{fp[1]}"
        lib, nm = name.split(":", 1)
        nm = re.sub(r"[/\\]", "_", nm)
        name = f"{lib}:{nm}"
        at = _find(fp, "at")[0]
        rot = float(at[3]) if len(at) > 3 else 0.0
        back = str(_find(fp, "layer")[0][1]) == "B.Cu"
        if name not in written:                          # (the first one placed is the library's copy)
            f_ = fp_root / f"{lib}.pretty" / f"{nm}.kicad_mod"
            f_.parent.mkdir(parents=True, exist_ok=True)
            f_.write_text(_sx_dump(_library_footprint(fp, rot, back)) + "\n")
            pcb._FP.pop(name, None)
            written.add(name)
        low = {k.lower(): v for k, v in props.items() if v}
        mpn = next((low[k] for k in MPN_KEYS if k in low), "")
        lcsc = next((low[k] for k in LCSC_KEYS if k in low), "")
        x, y = tx(at[1], at[2])
        label = False                                    # (its designator where the designer put it, or off)
        for t in [p for p in _find(fp, "property") if len(p) > 2 and p[1] == "Reference"] + \
                 [p for p in _find(fp, "fp_text") if len(p) > 2 and p[1] == "reference"]:
            lay_ = str(_find(t, "layer")[0][1]) if _find(t, "layer") else ""
            hid = ["hide", "yes"] in t or "hide" in t or any("hide" in e for e in _find(t, "effects"))
            if "SilkS" in lay_ and not hid and _find(t, "at"):
                la = _find(t, "at")[0]
                lx, ly, a = float(la[1]), float(la[2]), math.radians(rot)
                size = next((float(z[1]) for e in _find(t, "effects") for f in _find(e, "font") for z in _find(f, "size")), 1.0)
                label = tx(float(at[1]) + lx * math.cos(a) + ly * math.sin(a), float(at[2]) - lx * math.sin(a) + ly * math.cos(a)) + \
                    (float(la[3]) if len(la) > 3 and la[3] != "unlocked" else 0.0, size)
            break
        b.place(ref, name, (x, y), round((rot - 180.0 if back else rot) % 360.0, 6), props.get("Value", ""), mpn,
                side="B" if back else "F", lcsc=lcsc, label=label)
        for p in pads:
            n = net_of(p)
            if n:
                pins.setdefault(n, []).append((ref, str(p[1])))
    for n, ps in pins.items():
        b.net(n, list(dict.fromkeys(ps)))
    # copper: tracks, arcs and vias of the nets on pads; zones as pours, rule areas as keep-outs
    tracks, vias, lost = [], [], 0
    for g in tree:
        if not isinstance(g, list) or not g or g[0] not in ("segment", "arc", "via"):
            continue
        n = net_of(g)
        if n not in b.nets:
            lost += 1
            continue
        if g[0] == "via":
            if len(g) > 1 and g[1] in ("blind", "micro"):
                notes.append(f"a {g[1]} via on {n} is taken as a through via")
            vias.append({"net": n, "at": tx(*_xy(g, "at")), "drill": float(_find(g, "drill")[0][1]),
                         "dia": float(_find(g, "size")[0][1])})
            continue
        L = CU.get(str(_find(g, "layer")[0][1]))
        if L is None:
            lost += 1
            continue
        pts = [_xy(g, "start"), _xy(g, "end")] if g[0] == "segment" else _arc(_xy(g, "start"), _xy(g, "mid"), _xy(g, "end"))
        tracks.append({"net": n, "layer": L, "pts": [tx(*p) for p in pts], "w": float(_find(g, "width")[0][1]),
                       "amps": None})
    if lost:
        notes.append(f"{lost} tracks and vias on no part's net (or on more than 4 layers) left out")
    zones = []
    for z in _find(tree, "zone"):
        lays = [str(s) for k in ("layer", "layers") for f in _find(z, k) for s in f[1:]]
        lays = [L for s in lays for L in (b.layers if s in ("*.Cu", "F&B.Cu") else [CU.get(s)]) if L in b.layers]
        polys = [[tx(*p) for p in _pts(pg)] for pg in _find(z, "polygon")]
        if not polys or not lays:
            continue
        if _find(z, "keepout"):
            b.keepout(polys[0], "".join(lays), str(_find(z, "name")[0][1]) if _find(z, "name") else "")
            continue
        n = net_of(z)
        if n in b.nets:
            pr = int(_find(z, "priority")[0][1]) if _find(z, "priority") else 0
            zones += [(pr, n, L, polys) for L in lays]
    for pr, n, L, polys in zones:                        # (a zone under a higher-priority one of another net is
        area = Polygon(polys[0], polys[1:]).buffer(0)     #  filled round it, its clearance apart, as KiCad fills it)
        over = [Polygon(q[0], q[1:]).buffer(b.clear + 0.002, 8) for p2, n2, L2, q in zones
                if L2 == L and n2 != n and p2 > pr and Polygon(q[0], q[1:]).intersects(area)]
        if over:
            area = area.difference(unary_union(over))
        for g in getattr(area, "geoms", [area]):
            if g.geom_type == "Polygon" and g.area > 1e-3:
                ring = lambda r_: [(round(x, 6), round(y, 6)) for x, y in r_.coords[:-1]]
                b.pour(n, L, ring(g.exterior), holes=[ring(h) for h in g.interiors] or None)
    b.silk_text = []
    for t in _find(tree, "gr_text"):
        lay = _find(t, "layer")[0][1] if _find(t, "layer") else ""
        if lay in ("F.SilkS", "B.SilkS") and _find(t, "at"):
            a = _find(t, "at")[0]
            size = next((float(s[1]) for e in _find(t, "effects") for f in _find(e, "font") for s in _find(f, "size")), 1.0)
            just = " ".join(str(w) for e in _find(t, "effects") for j in _find(e, "justify") for w in j[1:]
                            if w != "mirror")
            b.silk_text.append((str(t[1]), tx(a[1], a[2]), size, float(a[3]) if len(a) > 3 else 0.0) +
                               ((("B" if lay == "B.SilkS" else "F"), just) if just or lay == "B.SilkS" else ()))
    other = [g for g in tree if isinstance(g, list) and g and str(g[0]).startswith("gr_") and g[0] != "gr_text"
             and _find(g, "layer") and _find(g, "layer")[0][1] != "Edge.Cuts" and not art(g)]
    if other:
        notes.append(f"{len(other)} drawings on layers the board house does not make (fab, user, comments) left out")
    return b, {"placement": pcb.placement(b), "tracks": tracks, "vias": vias, "frame": [X0, Y1]}, notes


def artwork(board, path: str) -> None:
    """the logos and the silkscreen and mask drawings saved by ``prometheus import`` (``board.graphics``, drawn into
    the Gerbers and the KiCad board), and the design's own KiCad rules and DRC severities, back on ``board``"""
    a = json.loads(Path(path).read_text())
    board.graphics, board.kicad_rules = a["graphics"], a["kicad_rules"]


def routes(board, path: str) -> List[str]:
    """the routed copper saved by ``prometheus import``, back on ``board``: all of it while every part is where it
    was; once parts have moved, the nets they touch are routed again (the rest keep theirs). Routing problems"""
    old = json.loads(Path(path).read_text())
    old["placement"] = {r: tuple(tuple(v) if isinstance(v, list) else v for v in p) for r, p in old["placement"].items()}
    for t in old["tracks"]:
        t["pts"] = [tuple(p) for p in t["pts"]]
    for v in old["vias"]:
        v["at"] = tuple(v["at"])
    src = Path(path).parent / "source" / f"{Path(path).parent.name}.kicad_pcb"
    if "frame" in old and src.exists():                  # (the design itself, for revise() to write back)
        board.kicad_source = {"file": str(src), "frame": old["frame"], "placement": old["placement"],
                              "tracks": [dict(t) for t in old["tracks"]], "vias": [dict(v) for v in old["vias"]]}
    if pcb.placement(board) == old["placement"]:
        board.tracks += old["tracks"]
        board.vias += old["vias"]
        return []
    redo = sorted(n for n in pcb.reuse_routes(board, old) if n in board.nets)
    keep, pcb._NO_LATEST = pcb._NO_LATEST, True         # (this copper, not another board's of the same name)
    try:
        return pcb.route_jobs(board, [(n, board.net_width(n), None) for n in redo]) if redo else []
    finally:
        pcb._NO_LATEST = keep


_REVISE = r"""
import json, sys, pcbnew
j = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(j["src"])
mm, (X0, Y1) = pcbnew.FromMM, j["frame"]
P = lambda x, y: pcbnew.VECTOR2I(mm(x + X0), mm(Y1 - y))
LAY = {"F": pcbnew.F_Cu, "B": pcbnew.B_Cu, "1": pcbnew.In1_Cu, "2": pcbnew.In2_Cu}
fps = {f.GetReference(): f for f in b.GetFootprints()}

def flip(f):
    try:
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)     # (KiCad 9+)
    except (AttributeError, TypeError):
        f.Flip(f.GetPosition(), False)

def put(f, x, y, rot, side):
    if (f.GetLayer() == pcbnew.B_Cu) != (side == "B"):
        flip(f)
    f.SetPosition(P(x, y))
    f.SetOrientationDegrees(rot)

for r in j["removed"]:
    b.Remove(fps.pop(r))
for r, (x, y, rot, side) in j["moved"].items():
    put(fps[r], x, y, rot, side)
for r, a in j["added"].items():
    f = pcbnew.FootprintLoad(a["lib"], a["name"])
    f.SetReference(r)
    f.SetValue(a["value"])
    b.Add(f)
    put(f, *a["at"])
    fps[r] = f
for n in j["nets"]:
    if b.FindNet(n) is None:
        b.Add(pcbnew.NETINFO_ITEM(b, n))
for r, f in fps.items():
    for p in f.Pads():
        n = j["pads"].get(r + "\t" + p.GetNumber())
        if n is not None and p.GetNetname() != n:
            p.SetNet(b.FindNet(n))
for t in list(b.GetTracks()):
    if t.GetNetname() in j["redo"]:
        b.Remove(t)
for t in j["tracks"]:
    for p, q in zip(t["pts"], t["pts"][1:]):
        s = pcbnew.PCB_TRACK(b)
        s.SetStart(P(*p)); s.SetEnd(P(*q)); s.SetWidth(mm(t["w"])); s.SetLayer(LAY[t["layer"]])
        s.SetNet(b.FindNet(t["net"]))
        b.Add(s)
for v in j["vias"]:
    w = pcbnew.PCB_VIA(b)
    w.SetPosition(P(*v["at"])); w.SetDrill(mm(v["drill"])); w.SetWidth(mm(v["dia"])); w.SetNet(b.FindNet(v["net"]))
    b.Add(w)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(j["out"])
"""


def revise(board, out_dir: str) -> dict:
    """the imported design written back revised (out_dir/<name>.kicad_pcb, by KiCad itself): the parts that moved
    moved, the ones added or taken away so, each pad on its net, and the copper of every net whose copper changed
    replaced by the board's; every other net's tracks, arcs and vias, the zones (refilled), the rules, the net
    classes, the drawings and each footprint's link to the schematic left as the designer had them. The schematic
    is copied unchanged. Returns what changed"""
    import shutil
    import subprocess
    import tempfile
    ks = board.kicad_source
    now, was = pcb.placement(board), ks["placement"]
    flat = lambda p: [p[1][0], p[1][1], (p[2] + 180.0 if p[3] == "B" else p[2]) % 360.0, p[3]]   # (KiCad's angle)
    same_fp = lambda r: r in was and now[r][0] == was[r][0]   # (a part on another footprint: taken away, added anew)
    moved = {r: flat(now[r]) for r in now if same_fp(r) and now[r] != was[r]}
    added = {}
    for r in now:
        if not same_fp(r):
            lib, name = board.comps[r]["fp"].split(":", 1)
            d = next((d / f"{lib}.pretty" for d in pcb.FP_DIRS if (d / f"{lib}.pretty" / f"{name}.kicad_mod").exists()), None)
            if d is None:
                raise ValueError(f"{r}: footprint {board.comps[r]['fp']} not found to add to the design")
            added[r] = {"lib": str(d), "name": name, "value": board.comps[r]["value"], "at": flat(now[r])}
    key_t = lambda t: (t["layer"], tuple((round(x, 4), round(y, 4)) for x, y in t["pts"]), round(t["w"], 4))
    key_v = lambda v: ((round(v["at"][0], 4), round(v["at"][1], 4)), round(v["dia"], 4))
    nets = set(board.nets) | {t["net"] for t in ks["tracks"]}
    redo = sorted(n for n in nets
                  if sorted(key_t(t) for t in board.tracks if t["net"] == n) != sorted(key_t(t) for t in ks["tracks"] if t["net"] == n)
                  or sorted(key_v(v) for v in board.vias if v["net"] == n) != sorted(key_v(v) for v in ks["vias"] if v["net"] == n))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(ks["file"])
    job = {"src": str(src), "out": str(out / src.name), "frame": ks["frame"], "moved": moved, "added": added,
           "removed": sorted(r for r in was if r not in now or not same_fp(r)), "nets": sorted(board.nets),
           "pads": {f"{r}\t{n}": net for (r, n), net in board.pad_net().items()}, "redo": redo,
           "tracks": [t for t in board.tracks if t["net"] in redo], "vias": [v for v in board.vias if v["net"] in redo]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(job, f)
    r = subprocess.run([pcb._pcbnew_python(), "-c", _REVISE, f.name], capture_output=True, text=True, timeout=300)
    os.unlink(f.name)
    if r.returncode != 0:
        raise RuntimeError("KiCad could not write the revision: " + (r.stderr or r.stdout)[-400:])
    for ext in (".kicad_pro", ".kicad_sch", ".kicad_prl"):
        if src.with_suffix(ext).exists():
            shutil.copy(src.with_suffix(ext), out / src.with_suffix(ext).name)
    return {"file": job["out"], "moved": sorted(moved), "added": sorted(added), "removed": job["removed"],
            "rerouted": redo, "kept": len(nets) - len(redo)}


def write(path: str, out_dir: str) -> dict:
    """``out_dir``/board.py, routes.json and footprints/ from the layout in ``path``; what was written and the notes"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    b, rt, notes = read(path, str(out / "footprints"))
    (out / "routes.json").write_text(json.dumps(rt))
    import shutil                                        # (the design as it was: revise() writes it back changed)
    (out / "source").mkdir(exist_ok=True)
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_sch", ".kicad_prl"):
        if Path(path).with_suffix(ext).exists():
            shutil.copy(Path(path).with_suffix(ext), out / "source" / f"{out.name}{ext}")
    (out / "artwork.json").write_text(json.dumps({"graphics": b.graphics, "kicad_rules": b.kicad_rules}))
    r = lambda v: round(v, 6)
    L = [f'"""{b.name}: imported from {Path(path).name} (prometheus import).',
         "",
         "A board script like any other: move a part, change a net, add a part, then `prometheus board.py out/`.",
         "The routed copper is in routes.json: nets whose parts have not moved keep it, the rest are routed again.",
         '"""', "import os", "", "import kicad_import", "import pcb", "",
         f"b = pcb.Board({b.name!r}, {[(r(x), r(y)) for x, y in b.outline.exterior.coords[:-1]]!r},",
         f"              thickness={b.t}, copper_oz={b.oz}, clearance={b.clear}, layers={len(b.layers)}, corner_r=0)"]
    if b.edge != pcb.EDGE:
        L.append(f"b.edge = {b.edge}")
    if b.hole_gap != pcb.HOLE_GAP:
        L.append(f"b.hole_gap = {b.hole_gap}")
    if b.hole_clear != pcb.HOLE_CLEAR:
        L.append(f"b.hole_clear = {b.hole_clear}")
    for ring in b.outline.interiors:
        L.append(f"b.cutout({[(r(x), r(y)) for x, y in ring.coords[:-1]]!r})")
    L.append("")
    for ref, c in b.comps.items():
        kw = "".join(f", {k}={v!r}" for k, v in (("mpn", c["mpn"]), ("lcsc", c["lcsc"])) if v)
        kw += ', side="B"' if c["side"] == "B" else ""
        kw += f", label={tuple(round(v, 4) for v in c['label'])!r}" if c.get("label") else ", label=False"
        L.append(f"b.place({ref!r}, {c['fp']!r}, ({r(c['at'][0])}, {r(c['at'][1])}), {r(c['rot'])}, {c['value']!r}{kw})")
    L.append("")
    for n, d in b.nets.items():
        L.append(f"b.net({n!r}, {d['pins']!r})")
    if b.pours or getattr(b, "keepouts", None) or b.silk_text or b.holes:
        L.append("")
    for x, y, d in b.holes:
        L.append(f"b.hole(({r(x)}, {r(y)}), {d})")
    for p in b.pours:
        L.append(f"b.pour({p['net']!r}, {p['layer']!r}, {p['poly']!r}" + (f", holes={p['holes']!r})" if p["holes"] else ")"))
    for k in getattr(b, "keepouts", []):
        L.append(f"b.keepout({k['poly']!r}, {k['layers']!r}, {k['why']!r})")
    if b.silk_text:
        L.append(f"b.silk_text = {b.silk_text!r}")
    L += ["", "HERE = os.path.dirname(os.path.abspath(__file__))",
          'kicad_import.artwork(b, os.path.join(HERE, "artwork.json"))   # logos, silkscreen and mask drawings, KiCad rules',
          'PCB_ROUTE_PROBLEMS = kicad_import.routes(b, os.path.join(HERE, "routes.json"))', ""]
    (out / "board.py").write_text("\n".join(L))
    return {"script": str(out / "board.py"), "parts": len(b.comps), "nets": len(b.nets), "tracks": len(rt["tracks"]),
            "vias": len(rt["vias"]), "pours": len(b.pours), "notes": notes}


def main(argv) -> int:
    if not argv or not argv[0].endswith(".kicad_pcb"):
        print("usage: prometheus import design.kicad_pcb [folder]   (default folder: the design's name)")
        return 2
    try:
        got = write(argv[0], argv[1] if len(argv) > 1 else Path(argv[0]).stem)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    print(f"{got['script']}: {got['parts']} parts, {got['nets']} nets, {got['tracks']} tracks, {got['vias']} vias, "
          f"{got['pours']} pours")
    for n in got["notes"]:
        print(f"  note: {n}")
    print(f"next: prometheus {got['script']} {Path(got['script']).parent / 'out'}/")
    return 0
