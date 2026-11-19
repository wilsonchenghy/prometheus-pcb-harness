"""Output: STEP, silkscreen and Gerbers, the KiCad project (KiCad 9/10 and KiCad 8), KiCad's DRC and render, the
project pack, and the board's own schematic drawing. Split from ``pcb``; ``pcb.<name>`` still reaches every
function here.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pcb
from pcb import (Board, HOLE_CLEAR, MIN_FILL, OZ_MM, _Q, _find, _fp_file, _pcbnew_python, _sx_dump, _sx_parse, _uuid,
        footprint, kicad_lib_cache)


def export_step(board: Board, path: str, models: bool = True, z0: float = 0.0) -> str:
    """the assembled board as one STEP file (the board with its drills, the copper, the pads and, with ``models``,
    every part's KiCad 3D model), for checking the fit in an enclosure in any mechanical CAD. Needs build123d"""
    import build123d as bd
    import os
    parts = board.parts(z0, models=models)
    shapes = []
    for name, sh in parts.items():
        try:                                             # (an empty layer - no parts with models - is no shape)
            if sh is None or sh.wrapped is None:
                continue
        except AssertionError:
            continue
        sh.label = name
        shapes.append(sh)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bd.export_step(bd.Compound(label=board.name, children=shapes), path)
    return path


def _text_poly(txt: str, x: float, y: float, size: float, angle: float = 0.0, justify: str = "",
               mirror: bool = False):
    """``txt`` as filled outline polygons (a sans font from matplotlib), ``size`` mm cap height, on (x, y) as KiCad
    anchors it (centred, or ``justify`` "left" / "right" / "top" / "bottom"; lines split at newlines), mirrored for
    the bottom side, turned ``angle`` degrees: silkscreen text a fab can print"""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    from shapely import affinity
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    lines = []
    for k, line in enumerate(txt.split("\n")):
        tp = TextPath((0, 0), line or " ", size=size * 1.38, prop=FontProperties(family="DejaVu Sans", weight="bold"))
        shape = None
        for ring in tp.to_polygons():                    # rings alternate outline / hole: even-odd fill
            if len(ring) < 3:
                continue
            p_ = Polygon(ring).buffer(0)
            shape = p_ if shape is None else shape.symmetric_difference(p_)
        if shape is None or shape.is_empty:
            continue
        x0, _, x1, _ = shape.bounds                      # (each line on the anchor's side, lines 1.62 x size apart)
        dx = -x0 if "left" in justify else -x1 if "right" in justify else -(x0 + x1) / 2
        lines.append(affinity.translate(shape, dx, -k * size * 1.62))
    if not lines:
        return Polygon()
    shape = unary_union(lines)
    _, y0, _, y1 = shape.bounds
    shape = affinity.translate(shape, 0, -y1 if "top" in justify else -y0 if "bottom" in justify else -(y0 + y1) / 2)
    if mirror:
        shape = affinity.scale(shape, -1, 1, origin=(0, 0))
    shape = affinity.rotate(shape, angle, origin=(0, 0)) if angle else shape
    return affinity.translate(shape, x, y)


def _fp_silk(board: Board, ref: str):
    """one part's footprint silk strokes (as KiCad draws them), board mm"""
    import math
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    c = board.comps[ref]
    ax, ay = c["at"]
    a = math.radians(c["rot"])
    ca, sa = math.cos(a), math.sin(a)
    mx = -1.0 if c.get("side", "F") == "B" else 1.0
    tf = lambda px, py: (ax + px * mx * ca - py * sa, ay + px * mx * sa + py * ca)
    return unary_union([LineString([tf(x1, y1), tf(x2, y2)]).buffer(max(w, 0.1) / 2, 4)
                        for x1, y1, x2, y2, w in footprint(c["fp"]).get("silk", [])])


def _graphic(g):
    """one of ``board.graphics`` (a logo's or a drawing's stroke or shape, imported from KiCad: {"layer", "pts", "w",
    "closed", "fill"}) as shapely geometry"""
    from shapely.geometry import LineString, Polygon
    w = g.get("w") or 0.0
    if g.get("closed") and g.get("fill") and len(g["pts"]) > 2:
        p = Polygon(g["pts"]).buffer(0)
        return p.buffer(w / 2, 8) if w > 0 else p
    pts = g["pts"] + (g["pts"][:1] if g.get("closed") else [])
    return LineString(pts).buffer(max(w, 0.1) / 2, 8) if len(pts) > 1 else Polygon()


def silkscreen(board: Board, layer: str = "F"):
    """the silkscreen of one side as shapely geometry: each part's footprint silk lines (as KiCad draws them) and its
    reference designator where ``place_refs`` finds room, the board's own ``silk_text``; clipped clear of the pads
    and the drills (a fab clips silk off exposed copper)"""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    geoms = [_fp_silk(board, ref) for ref, c in board.comps.items() if c.get("side", "F") == layer]
    for ref, at in place_refs(board).items():
        if at and board.comps[ref].get("side", "F") == layer:
            x, y, ang, size = at
            geoms.append(_text_poly(ref, x, y, size, ang, "", layer == "B"))
    for txt, (x, y), size, ang, *on in getattr(board, "silk_text", []):     # (then the side, "B", and the justify)
        if (on[0] if on else "F") == layer:
            geoms.append(_text_poly(txt, x, y, size, ang, on[1] if len(on) > 1 else "", layer == "B"))
    geoms += [_graphic(g) for g in getattr(board, "graphics", []) if g["layer"] == f"{layer}.SilkS"]
    silk = unary_union([g for g in geoms if not g.is_empty]) if geoms else None
    if silk is None or silk.is_empty:
        return silk
    keep_off = unary_union([g.buffer(0.1) for _, _, L, g, d in board.pad_shapes() if L == layer or d] +
                           [Point(x, y).buffer(d / 2 + 0.2) for x, y, d in board.all_holes()] +
                           [Point(v["at"]).buffer(v["dia"] / 2 + 0.05) for v in board.vias])
    return silk.difference(keep_off).intersection(board.outline.buffer(-0.1))


def export_gerbers(board: Board, out_dir: str) -> List[str]:
    """RS-274X Gerbers (top / bottom copper as filled regions, top / bottom solder mask openings at the pads, top /
    bottom paste (the stencil: SMD pads) where a side has SMD parts, the silkscreen (footprint outlines and
    reference designators, clipped off the pads), the board outline) and an Excellon drill file (plated: pads and vias; non-plated: mounting holes) for a fab.
    Coordinates in mm, format 4.6. Review in a Gerber viewer before ordering."""
    import os
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    os.makedirs(out_dir, exist_ok=True)
    cu = board.copper()
    fmt = lambda v: str(int(round(v * 1e6)))

    def regions(geom, lines):
        # outermost first: a clear (hole) region erases everything drawn before it, so copper lying inside another
        # polygon's hole (a pad in a pour's clearance) must come after that polygon, i.e. by falling outline area
        # (a polygon inside another's hole has the smaller outline, so this order always works for the disjoint
        # polygons of a copper layer; PCB_GERBER_VERIFY=1 replays the layers to prove it, slowly on a big board)
        polys = [p for p in (getattr(geom, "geoms", None) or [geom]) if not p.is_empty and hasattr(p, "exterior")]
        verify = os.environ.get("PCB_GERBER_VERIFY") == "1"
        drawn = None
        for p in sorted(polys, key=lambda q: -Polygon(q.exterior).area):
            p = orient(p, 1.0)
            if verify:
                drawn = Polygon(p.exterior) if drawn is None else drawn.union(Polygon(p.exterior))
                for r in p.interiors:
                    drawn = drawn.difference(Polygon(r))
            for ring, pol in [(p.exterior, "D")] + [(r, "C") for r in p.interiors]:
                lines.append(f"%LP{pol}*%")
                pts = list(ring.coords)
                lines.append("G36*")
                lines.append(f"X{fmt(pts[0][0])}Y{fmt(pts[0][1])}D02*")
                for x, y in pts[1:]:
                    lines.append(f"X{fmt(x)}Y{fmt(y)}D01*")
                lines.append("G37*")
            lines.append("%LPD*%")
        if polys and verify:                        # replay the dark / clear layers: they must give back the copper
            lost = unary_union(polys).symmetric_difference(drawn).area
            assert lost < 1e-6, f"Gerber regions do not reproduce the copper ({lost:.4f} mm2 differ)"
        elif len(polys) > 1:                        # the cheap guard: the polygons are disjoint (areas add up)
            whole = unary_union(polys).area
            assert abs(sum(p.area for p in polys) - whole) < 1e-6 * max(1.0, whole), "copper polygons overlap"

    def write(name, func, body):
        head = ["%FSLAX46Y46*%", "%MOMM*%", f"%TF.FileFunction,{func}*%", "%TF.Part,Single*%", "%LPD*%", "G01*"]
        path = os.path.join(out_dir, f"{board.name}-{name}")
        with open(path, "w") as f:
            f.write("\n".join(head + body + ["M02*"]) + "\n")
        return path
    files = []
    from shapely.ops import unary_union
    nl = len(board.layers)
    for L, nm, ff in [("F", "F_Cu.gtl", "Copper,L1,Top")] + \
            [(L_, f"In{L_}_Cu.g{int(L_) + 1}", f"Copper,L{int(L_) + 1},Inr") for L_ in board.layers if L_ not in ("F", "B")] + \
            [("B", "B_Cu.gbl", f"Copper,L{nl},Bot")]:
        body = []
        regions(unary_union([g for (n, l), g in cu.items() if l == L]), body)
        files.append(write(nm, ff, body))
    for L, nm, ff in (("F", "F_Mask.gts", "Soldermask,Top"), ("B", "B_Mask.gbs", "Soldermask,Bot")):
        body = []
        pads = unary_union([g.buffer(0.05) for r, n, l, g, d in board.pad_shapes() if l == L] +
                           [_graphic(g) for g in getattr(board, "graphics", []) if g["layer"] == f"{L}.Mask"] or [])
        regions(pads, body)
        files.append(write(nm, ff, body))
    for L, nm, ff in (("F", "F_Silkscreen.gto", "Legend,Top"), ("B", "B_Silkscreen.gbo", "Legend,Bot")):
        try:
            sk = silkscreen(board, L)
        except ImportError:                              # (no matplotlib for the text: no legend layer)
            sk = None
        if sk is not None and not sk.is_empty:
            body = []
            regions(sk, body)
            files.append(write(nm, ff, body))
    for L, nm, ff in (("F", "F_Paste.gtp", "Paste,Top"), ("B", "B_Paste.gbp", "Paste,Bot")):
        smd = [g for r, n, l, g, d in board.pad_shapes() if l == L and not d]      # the stencil: SMD pads only
        if smd:
            body = []
            regions(unary_union(smd), body)
            files.append(write(nm, ff, body))
    body = ["%ADD10C,0.100000*%", "D10*"]
    for ring in [board.outline.exterior] + list(board.outline.interiors):   # (the edge and each cut-out)
        pts = list(ring.coords)
        body.append(f"X{fmt(pts[0][0])}Y{fmt(pts[0][1])}D02*")
        body += [f"X{fmt(x)}Y{fmt(y)}D01*" for x, y in pts[1:]]
    files.append(write("Edge_Cuts.gm1", "Profile,NP", body))
    drills = {}
    for h in board.drill_list():                          # (the footprints' own bare holes too: USB-C pegs)
        drills.setdefault(("PTH" if h["plated"] else "NPTH", round(h["d"], 3)), []).append(h)
    for kind in ("PTH", "NPTH"):
        tools = sorted(d for (k, d) in drills if k == kind)
        if not tools:
            continue
        lines = ["M48", "METRIC,TZ", f"; {kind}"] + [f"T{i + 1}C{d:.3f}" for i, d in enumerate(tools)] + ["%", "G05"]
        for i, d in enumerate(tools):
            lines.append(f"T{i + 1}")
            for h in drills[(kind, d)]:                   # (a slot: routed from end to end, G85)
                (x, y), (a, b) = h["at"], h["slot"] or (None, None)
                lines.append(f"X{a[0]:.3f}Y{a[1]:.3f}G85X{b[0]:.3f}Y{b[1]:.3f}" if a else f"X{x:.3f}Y{y:.3f}")
        lines.append("M30")
        path = os.path.join(out_dir, f"{board.name}-{kind}.drl")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        files.append(path)
    return files


def _fractured(geom) -> List[List[Tuple[float, float]]]:
    """each polygon of ``geom`` as one ring, its holes joined to the outline by zero-width slits (KiCad stores zone
    fills so): holes taken leftmost first, each bridged from its leftmost vertex straight left to the nearest edge
    of the ring so far (the outline and the holes already joined). The ring is a linked list over numpy arrays, so
    a fill with a thousand holes is joined in a moment"""
    import numpy as np
    from shapely.geometry.polygon import orient
    out = []
    for p in (getattr(geom, "geoms", None) or [geom]):
        if p.is_empty or not hasattr(p, "exterior") or p.area < 1e-6:
            continue
        p = orient(p, 1.0)                                    # outline ccw, holes cw
        ext = np.asarray(p.exterior.coords)[:-1]
        holes = [np.asarray(h.coords)[:-1] for h in p.interiors]
        cap = len(ext) + sum(len(h) + 3 for h in holes)
        vx, vy = np.empty(cap), np.empty(cap)
        nxt = np.empty(cap, dtype=np.int64)
        n = len(ext)
        vx[:n], vy[:n] = ext[:, 0], ext[:, 1]
        nxt[:n] = np.roll(np.arange(n), -1)
        for h in sorted(holes, key=lambda h: h[:, 0].min()):
            k = int(np.lexsort((h[:, 1], h[:, 0]))[0])        # leftmost vertex (lowest y among equals)
            px, py = h[k]
            x1, y1 = vx[:n], vy[:n]
            x2, y2 = vx[nxt[:n]], vy[nxt[:n]]
            cross = ((y1 <= py) & (py < y2)) | ((y2 <= py) & (py < y1))
            if not cross.any():
                continue
            idx = np.nonzero(cross)[0]
            xs = x1[idx] + (py - y1[idx]) * (x2[idx] - x1[idx]) / (y2[idx] - y1[idx])
            left = xs <= px
            if not left.any():
                continue
            idx, xs = idx[left], xs[left]
            j = int(np.argmax(xs))
            i, x = int(idx[j]), float(xs[j])
            seq = [(x, py)] + [tuple(q) for q in np.roll(h, -k, axis=0)] + [(px, py), (x, py)]
            m = len(seq)
            vx[n:n + m] = [q[0] for q in seq]
            vy[n:n + m] = [q[1] for q in seq]
            nxt[n:n + m - 1] = np.arange(n + 1, n + m)
            nxt[n + m - 1] = nxt[i]
            nxt[i] = n
            n += m
        ring, v = [], 0
        for _ in range(n):
            ring.append((float(vx[v]), float(vy[v])))
            v = int(nxt[v])
            if v == 0:
                break
        out.append(ring)
    return out


_KICAD_LAYERS = [(0, "F.Cu", "signal"), (2, "B.Cu", "signal"), (9, "F.Adhes", "user", "F.Adhesive"),
                 (11, "B.Adhes", "user", "B.Adhesive"), (13, "F.Paste", "user"), (15, "B.Paste", "user"),
                 (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"), (1, "F.Mask", "user"),
                 (3, "B.Mask", "user"), (17, "Dwgs.User", "user", "User.Drawings"),
                 (19, "Cmts.User", "user", "User.Comments"), (21, "Eco1.User", "user", "User.Eco1"),
                 (23, "Eco2.User", "user", "User.Eco2"), (25, "Edge.Cuts", "user"), (27, "Margin", "user"),
                 (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"), (35, "F.Fab", "user"),
                 (33, "B.Fab", "user")]
# library-file tokens a footprint inside a board does not carry (KiCad 10's library is newer than the board format)
_FP_DROP = {"version", "generator", "generator_version", "tedit", "embedded_fonts", "embedded_files",
            "duplicate_pad_numbers_are_jumpers", "jumper_pad_groups", "component_classes",
            "point"}                                     # (KiCad 10's anchor points: KiCad 9 cannot read a board
                                                         #  that has one, and they carry no copper)


def place_refs(board: "Board", sizes: Sequence[float] = (0.8,), gap: float = 0.25) -> Dict[str, Optional[Tuple]]:
    """reference designators placed for the silkscreen as a fab expects them: each part's label next to its body,
    clear of every pad (silkscreen over exposed copper is clipped off by the fab), of the drills, of the board edge,
    of the other parts' bodies and of the other labels; 0.8 mm text (KiCad's and the usual fab's minimum), else
    none (the label stays on the assembly layer, as on a dense board). {ref: (x, y, angle, size) or None}, board mm.
    Memoised on the board's state (the Gerbers and the KiCad export both ask)."""
    key = (board._state_key(), tuple(sizes), gap,
           tuple(sorted((r, c.get("value", "")) for r, c in board.comps.items())))
    memo = board.__dict__.get("_refs_memo")
    if memo is not None and memo[0] == key:
        return dict(memo[1])
    got = _place_refs(board, sizes, gap)
    got.update({r: c["label"] for r, c in board.comps.items() if "label" in c})   # (where the designer put them)
    board._refs_memo = (key, got)
    return dict(got)


def _place_refs(board: "Board", sizes: Sequence[float], gap: float) -> Dict[str, Optional[Tuple]]:
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    side = lambda r: board.comps[r].get("side", "F")
    pads = {L: unary_union([g.buffer(gap, 8) for _, _, l, g, _ in board.pad_shapes() if l == L] or [Point(0, 0).buffer(0)])
            for L in "FB"}
    holes = unary_union([Point(x, y).buffer(d / 2 + gap, 16) for x, y, d in board.holes] +
                        [g.centroid.buffer(d / 2 + gap, 16) for _, _, _, g, d in board.pad_shapes() if d] or [Point(0, 0).buffer(0)])
    inside = board.outline.buffer(-0.5)
    crt = {r: board.courtyard(r) for r in board.comps}
    silk = {r: _fp_silk(board, r) for r in board.comps}
    taken = {"F": [], "B": []}
    for txt, (x, y), size, ang, *on in getattr(board, "silk_text", []):     # (the board's own text: a title)
        w, h = len(txt) * size * 0.78 + 0.2, size + 0.25
        w, h = (w, h) if abs(ang % 180) < 45 else (h, w)
        taken[on[0] if on else "F"].append(box(x - w / 2, y - h / 2, x + w / 2, y + h / 2).buffer(gap))
    out: Dict[str, Optional[Tuple]] = {}
    order = sorted(board.comps, key=lambda r: crt[r].area if crt[r] is not None else 0.0)
    for r in order:
        L = side(r)
        body = crt[r] if crt[r] is not None else Point(*board.comps[r]["at"]).buffer(1.0)
        bodies = unary_union([crt[o] for o in board.comps if o != r and crt[o] is not None and side(o) == L] or
                             [Point(0, 0).buffer(0)])
        strokes = unary_union([silk[o] for o in board.comps if o != r and side(o) == L] or [Point(0, 0).buffer(0)])
        x0, y0, x1, y1 = body.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        found = None
        for size in sizes:
            w, h = len(r) * size * 0.78 + 0.2, size + 0.25            # KiCad stroke font, 15 % stroke
            cands = []
            for a in (0.0, 90.0):
                tw, th = (w, h) if a == 0.0 else (h, w)
                for dx in (0.0, -tw / 2, tw / 2):
                    cands += [(cx + dx, y1 + gap + th / 2, a), (cx + dx, y0 - gap - th / 2, a)]
                for dy in (0.0, -th / 2, th / 2):
                    cands += [(x1 + gap + tw / 2, cy + dy, a), (x0 - gap - tw / 2, cy + dy, a)]
            def fits(x, y, a):
                tw, th = (w, h) if a == 0.0 else (h, w)
                bb = box(x - tw / 2, y - th / 2, x + tw / 2, y + th / 2)
                ok = (inside.contains(bb) and not bb.intersects(pads[L]) and not bb.intersects(holes)
                      and not bb.intersects(bodies) and not bb.intersects(body) and not bb.intersects(strokes)
                      and bb.distance(bodies) > bb.distance(body) + 0.3      # (read as its own part's, not a neighbour's)
                      and not any(bb.intersects(t) for t in taken[L]))
                return bb if ok else None
            hit = next(((x, y, a, bb) for x, y, a in cands for bb in [fits(x, y, a)] if bb), None)
            if hit is None:                              # (no slot on its sides: the nearest free spot round it,
                scan = sorted(((cx + i * 0.1, cy + j * 0.1, a) for i in range(-30, 31) for j in range(-30, 31)
                               for a in (0.0, 90.0)),     #  0.1 mm steps out to 3 mm, nearest first)
                              key=lambda t: (abs(t[0] - cx) - (x1 - x0) / 2) ** 2 + (abs(t[1] - cy) - (y1 - y0) / 2) ** 2)
                for x, y, a in scan:
                    tw, th = (w, h) if a == 0.0 else (h, w)
                    if box(x - tw / 2, y - th / 2, x + tw / 2, y + th / 2).distance(body) > 1.0:
                        continue
                    bb = fits(x, y, a)
                    if bb:
                        hit = (x, y, a, bb)
                        break
            if hit:
                x, y, a, bb = hit
                found = (round(x, 3), round(y, 3), a, size)
                taken[L].append(bb.buffer(gap))
            if found:
                break
        out[r] = found
    return out


def _kicad_footprint(board: "Board", ref: str, c: Dict, netnum: Dict[str, int], tx, ty, label="keep",
                     body_dir: Optional[str] = None) -> list:
    """one placed part as a board footprint: the library's own .kicad_mod (pads, silkscreen, courtyard, fab, model)
    at the part's place, turned, flipped to the bottom for side B the way KiCad flips (mirrored top to bottom and
    turned 180: the same copper as ``Board.place``'s left / right mirror), its pads on their nets"""
    lib, name = c["fp"].split(":")
    footprint(c["fp"])                                       # fetched into the cache
    tree = _sx_parse(_fp_file(c["fp"]).read_text())
    back = c.get("side", "F") == "B"
    rot = c["rot"] % 360.0
    rot_k = (rot + 180.0) % 360.0 if back else rot      # (KiCad's flip: mirrored top to bottom and turned 180, the
    pn = board.pad_net()                                 #  same copper as the harness's left / right mirror)
    flipL = lambda s: _Q(("B." + s[2:]) if s.startswith("F.") else ("F." + s[2:]) if s.startswith("B.") else s)

    def walk(node, owner):
        out = [node[0]]
        for v in node[1:]:
            if not isinstance(v, list) or not v:
                out.append(v)
                continue
            k = v[0]
            if k == "model":                             # (the library's model path, as the KiCad 9 file this
                out.append([k, _Q(re.sub(r"^\$\{KICAD\d+_3DMODEL_DIR\}/", "${KICAD9_3DMODEL_DIR}/", str(v[1])))]
                           + v[2:] if len(v) > 1 else v)     # is finds it: a newer library names KICAD10's)
            elif k == "at" and owner in ("pad", "property", "fp_text"):
                x, y = float(v[1]), float(v[2])
                a = float(v[3]) if len(v) > 3 else 0.0
                if back:
                    y, a = -y, -a
                at = ["at", x, y, (rot_k + a) % 360.0]           # pad and text angles are absolute in a board
                out.append(at + [w for w in v[4:]])
            elif k in ("at", "start", "end", "mid", "center", "xy") and len(v) >= 3:
                out.append([k, v[1], -float(v[2]) if back else v[2]] + v[3:])
            elif k == "angle" and back:
                out.append(["angle", -float(v[1])])
            elif k == "layer" and owner.startswith("fp_") and len(v) > 1 and str(v[1]) == "Edge.Cuts":
                out.append([k, _Q("Dwgs.User")])        # (a part's mark of where the board edge should run - a
            #                                              jack's notch: the board's outline is the script's, and a
            #                                              second, open outline is a malformed board to KiCad)
            elif k in ("layer", "layers") and back:
                out.append([k] + [flipL(s) if isinstance(s, _Q) else s for s in v[1:]])
            elif k == "effects" and back and owner in ("property", "fp_text"):
                e = walk(v, k)
                j = [w for w in e if isinstance(w, list) and w[0] == "justify"]
                if j:
                    j[0].append("mirror")
                else:
                    e.append(["justify", "mirror"])
                out.append(e)
            else:
                out.append(walk(v, k))
        return out

    body = [v for v in tree[2:] if not (isinstance(v, list) and v and v[0] in _FP_DROP)]
    fp = walk(["footprint", _Q(c["fp"])] + body, "footprint")
    pads_on = 0
    for v in fp[2:]:
        if not isinstance(v, list) or not v:
            continue
        if v[0] == "layer":
            v[1] = _Q("B.Cu" if back else "F.Cu")
        elif (v[0] == "property" and v[1] == "Reference") or (v[0] == "fp_text" and v[1] == "reference"):
            v[2] = _Q(ref)
            if label is None:                                 # no clean spot: off the silkscreen (fab layer keeps it)
                v[:] = [w for w in v if not (isinstance(w, list) and w and w[0] == "hide")] + [["hide", "yes"]]
            elif label != "keep":                             # the placed spot: board -> footprint frame
                lx, ly, ang, size = label
                dx, dy = lx - c["at"][0], ly - c["at"][1]
                a = math.radians(-rot_k)
                px, py = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
                for w in v:
                    if isinstance(w, list) and w and w[0] == "at":
                        w[:] = ["at", round(px, 4), round(-py, 4), ang]
                    if isinstance(w, list) and w and w[0] == "effects":
                        for f_ in _find(w, "font"):
                            f_[:] = ["font", ["size", size, size], ["thickness", max(0.15, round(size * 0.15, 3))]]   # (0.15: the fab's line)
        elif v[0] == "property" and v[1] == "Value" and c["value"]:
            v[2] = _Q(c["value"])
        elif v[0] == "fp_text" and v[1] == "value" and c["value"]:
            v[2] = _Q(c["value"])
        elif v[0] == "pad":
            if c.get("pads_key"):                         # pads the board changed (thermal vias drilled to the
                at_ = next((w for w in v if isinstance(w, list) and w and w[0] == "at"), None)   # fab's minimum):
                if at_ is not None:                       # the file carries the board's drill and size, not the
                    ax, ay = float(at_[1]), float(at_[2])  # library's
                    for p_ in c["pads"].get(str(v[1]), []):
                        px_, py_ = p_["at"]
                        if p_["drill"] and min(abs(px_ - sx) + abs(py_ - sy) for sx in (ax, -ax) for sy in (ay, -ay)) < 1e-3:
                            for w in v:
                                if isinstance(w, list) and w and w[0] == "drill" and len(w) > 1 and \
                                        w[1] != "oval":   # (a round drill; a slot is left as it is)
                                    w[1] = p_["drill"]
                                elif isinstance(w, list) and w and w[0] == "size":
                                    w[1], w[2] = p_["size"][0], p_["size"][1]
                            break
            n = pn.get((ref, str(v[1])))
            if n is not None:
                v.insert(next(i for i, w in enumerate(v) if isinstance(w, list) and w[0] == "layers") + 1,
                         ["net", netnum[n], _Q(n)])
            pads_on += 1
    # silkscreen strokes past the board edge (an edge connector's outline over the overhang) are dropped: the fab
    # would clip them anyway, and the board file stays clean for KiCad's DRC
    ca, sa = math.cos(math.radians(rot_k)), math.sin(math.radians(rot_k))
    on_board = board.outline.buffer(-0.15)

    def outside(g):
        from shapely.geometry import Point
        pts_ = [q for key in ("start", "end", "mid", "center") for q in _find(g, key)] + \
               (_find(_find(g, "pts")[0], "xy") if _find(g, "pts") else [])
        for q in pts_:
            lx_, ly_ = float(q[1]), -float(q[2])
            if not on_board.contains(Point(c["at"][0] + lx_ * ca - ly_ * sa, c["at"][1] + lx_ * sa + ly_ * ca)):
                return True
        return False
    fp = [w for w in fp if not (isinstance(w, list) and w and w[0] in ("fp_line", "fp_rect", "fp_poly", "fp_arc",
                                                                       "fp_circle")
                                and any(isinstance(q, list) and q[0] == "layer" and "SilkS" in str(q[1]) for q in w)
                                and outside(w))]
    i = next(i for i, w in enumerate(fp) if isinstance(w, list) and w[0] == "layer") + 1
    fp[i:i] = [["uuid", _uuid(board.name, ref)], ["at", tx(c["at"][0]), ty(c["at"][1]), rot_k]]
    extra = [("MPN", c["mpn"]), ("Note", c["note"])]
    fab = _Q("B.Fab" if back else "F.Fab")
    for k_, val in extra:
        if val:
            fp.append(["property", _Q(k_), _Q(val), ["at", 0, 0, rot_k], ["layer", fab], ["hide", "yes"],
                       ["uuid", _uuid(board.name, ref, k_)], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]])
    old = [w for w in fp if isinstance(w, list) and w and w[0] == "model"]
    if c.get("model") is not None or c.get("body") is not None:     # (neither given: the footprint's own model stays,
        fp = [w for w in fp if not (isinstance(w, list) and w and w[0] == "model")]          # model=False: none)
    if c.get("model"):                                       # the 3D model the harness renders, as KiCad would find it
        var = re.match(r"\$\{\w+\}/", str(old[0][1])).group(0) if old and re.match(r"\$\{\w+\}/", str(old[0][1])) \
            else "${KICAD9_3DMODEL_DIR}/"
        ox, oy, oz = c.get("model_offset") or (0.0, 0.0, 0.0)
        if old and c["model"] == footprint(c["fp"]).get("model") and not (ox or oy or oz):   # (the footprint's own:
            fp.append([old[0][0], _Q(var + c["model"])] + old[0][2:])                     #  its offset and turn kept)
        else:
            fp.append(["model", _Q(var + c["model"]), ["offset", ["xyz", ox, oy, oz]], ["scale", ["xyz", 1, 1, 1]],
                       ["rotate", ["xyz", 0, 0, 0]]])
    if c.get("body") is not None and body_dir:              # a part drawn in the program: its STEP, in place of the
        import build123d as bd                               # library model, or beside a ``model`` named as well (a
                                                             # module drawn on its library sockets)
        name = f"{re.sub(r'[^A-Za-z0-9_.-]', '_', c['fp'].split(':')[1])}_{_uuid('body', round(float(c['body'].volume), 3), *[round(v, 3) for v in c['body'].bounding_box().size])[:8]}.step"
        p_ = Path(body_dir) / name
        if not p_.exists():
            p_.parent.mkdir(parents=True, exist_ok=True)
            bd.export_step(c["body"], str(p_))
        fp.append(["model", _Q("${KIPRJMOD}/3d/" + name), ["offset", ["xyz", 0, 0, 0]], ["scale", ["xyz", 1, 1, 1]],
                   ["rotate", ["xyz", 0, 0, 0]]])
    return fp


def kicad_models(board: Board, model_dir: str) -> List[str]:
    """fetch the KiCad library 3D models the board's parts name (``model`` paths, into the parts cache) and lay
    them out under ``model_dir`` as KiCad's 3D library is laid out, for ``render_kicad``. Returns the paths it
    could not get"""
    import shutil
    from pathlib import Path
    from vendor_parts import CACHE
    missing = []
    for c in board.comps.values():
        path = c.get("model")
        if not path or not isinstance(path, str):
            continue
        dst = kicad_lib_cache("kicad", Path(path).name)
        if not dst.exists() or dst.stat().st_size < 4096:
            # (a footprint now and then names a model the library has since renamed: its own name, without the
            # thermal-via / mask variant, and the newer "P" for "Pitch", are tried next)
            fpn = c["fp"].split(":")[-1]
            base = re.sub(r"(_Mask[\dx.]+mm)?(_ThermalVias)?$", "", fpn)
            folder = str(Path(path).parent)
            cands = list(dict.fromkeys([path, path.replace("_Pitch", "_P"), f"{folder}/{fpn}.step",
                                        f"{folder}/{base}.step"]))
            got = None
            for cand in cands:
                try:
                    data, ref = pcb.kicad_lib_get("kicad-packages3D", cand, timeout=120)
                    if data.startswith(b"ISO-10303-21"):
                        got = data
                        break
                except Exception:
                    continue
            if got is None:
                missing.append(path)
                continue
            dst = CACHE / "kicad" / ref / Path(path).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(got)
        out = Path(model_dir) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            shutil.copy(dst, out)
    return sorted(set(missing))


def kicad_drc(pcb_path: str, refill: bool = False) -> Dict[str, list]:
    """KiCad's own design-rule check of an exported board (``kicad-cli``), on a copy: as exported, or with every
    zone refilled by KiCad first (``pcbnew.ZONE_FILLER``; kicad-cli has no refill switch), so the harness's fills
    and KiCad's are both judged. {"errors": [...], "unconnected": [...], "warnings": {type: count}}; the
    footprint-library-table warnings (the footprints are embedded) are left out"""
    import collections
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp()
    for f in os.listdir(os.path.dirname(os.path.abspath(pcb_path))):
        src = os.path.join(os.path.dirname(os.path.abspath(pcb_path)), f)
        if os.path.isfile(src):
            shutil.copy(src, tmp)
    path = os.path.join(tmp, os.path.basename(pcb_path))
    if refill:
        subprocess.run([_pcbnew_python(), "-c", "import sys, pcbnew\nb = pcbnew.LoadBoard(sys.argv[1])\n"
                        "pcbnew.ZONE_FILLER(b).Fill(b.Zones())\nb.Save(sys.argv[1])", path],
                       check=True, capture_output=True, timeout=1800)
    out = os.path.join(tmp, "drc.json")
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json", "-o", out, path],
                       capture_output=True, text=True, timeout=1800)
    if not os.path.exists(out):                          # (KiCad could not read the board: say so, not a missing file)
        raise RuntimeError(f"kicad-cli could not check {os.path.basename(pcb_path)} (exit {r.returncode}): "
                           f"{(r.stdout + r.stderr).strip()[-300:] or 'no output'}")
    d = json.load(open(out))
    desc = lambda v: f"{v['type']}: {v['description']} ({'; '.join(i['description'] for i in v['items'][:2])})"
    return {"errors": [desc(v) for v in d.get("violations", []) if v["severity"] == "error"],
            "unconnected": [desc(v) for v in d.get("unconnected_items", [])],
            "warnings": dict(collections.Counter(v["type"] for v in d.get("violations", [])
                                                 if v["severity"] != "error" and v["type"] != "lib_footprint_issues"))}


def render_kicad(pcb_path: str, out_dir: str, model_dir: Optional[str] = None, zoom: float = 2.9) -> List[str]:
    """the assembled board as KiCad renders it with its parts' 3D models: top, bottom and a perspective view
    (``assembled_top / bottom / iso.png``); ``model_dir`` is where ``kicad_models`` put the library models"""
    import os
    import subprocess
    env = dict(os.environ)
    if model_dir:
        for v in ("KICAD8_3DMODEL_DIR", "KICAD9_3DMODEL_DIR", "KICAD10_3DMODEL_DIR"):
            env[v] = model_dir
    outs = []
    for name, args in (("top", ["--side", "top", "-w", "3000", "-h", "1000", "--zoom", str(zoom)]),
                       ("bottom", ["--side", "bottom", "-w", "3000", "-h", "1000", "--zoom", str(zoom)]),
                       ("iso", ["--side", "top", "-w", "2600", "-h", "1400", "--perspective", "--rotate", "-50,0,-25",
                                "--zoom", "1.2"])):
        o = os.path.join(out_dir, f"assembled_{name}.png")
        subprocess.run(["kicad-cli", "pcb", "render", *args, "--quality", "high", "--background", "opaque",
                        "-o", o, pcb_path], env=env, capture_output=True, timeout=1800, check=True)
        outs.append(o)
    return outs


def pack(src_dir: str) -> str:
    """zip a build folder (``gerbers/``, ``kicad/``) into ``<folder>.zip`` beside it and remove the folder, so a board's
    generated text files reach git as one compressed file each. Entries are sorted and dated 1980-01-01, so the same
    files always give the same zip and a rebuild with no change leaves git clean."""
    import os
    import shutil
    import zipfile
    src_dir = os.path.normpath(src_dir)
    out = src_dir + ".zip"
    root = os.path.dirname(src_dir)
    names = sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(src_dir) for f in fs)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in names:
            info = zipfile.ZipInfo(n.replace(os.sep, "/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(root, n), "rb") as f:
                z.writestr(info, f.read(), compresslevel=9)
    shutil.rmtree(src_dir)
    return out


# KiCad 8's file format: what Altium's importer (File > Import Wizard > KiCad) reads. Its layer numbers, its version
# stamps, and none of KiCad 9's new tokens
_K8_LAYERS = {"F.Cu": 0, **{f"In{i}.Cu": i for i in range(1, 31)}, "B.Cu": 31, "B.Adhes": 32, "F.Adhes": 33,
              "B.Paste": 34, "F.Paste": 35, "B.SilkS": 36, "F.SilkS": 37, "B.Mask": 38, "F.Mask": 39, "Dwgs.User": 40,
              "Cmts.User": 41, "Eco1.User": 42, "Eco2.User": 43, "Edge.Cuts": 44, "Margin": 45, "B.CrtYd": 46,
              "F.CrtYd": 47, "B.Fab": 48, "F.Fab": 49, **{f"User.{i}": 49 + i for i in range(1, 10)}}
_K8_DROP = {"embedded_fonts", "embedded_files", "tenting", "placement"}   # (a rule area's placement: KiCad 9's)


def export_kicad8(src_dir: str, out_dir: str) -> List[str]:
    """the KiCad project in ``src_dir`` (as ``export_kicad`` writes it) again in KiCad 8's format, in ``out_dir``: for
    tools that read KiCad 8 files and not later ones (Altium's KiCad import). The paths written"""
    import os
    import shutil
    os.makedirs(out_dir, exist_ok=True)

    def clean(node):
        if not isinstance(node, list):
            return node
        node = [clean(x) for x in node if not (isinstance(x, list) and x and x[0] in _K8_DROP)]
        if node and node[0] == "sheet":                  # (a page's box: KiCad 9 gave it these flags)
            node = [x for x in node if not (isinstance(x, list) and x and x[0] in
                                            ("exclude_from_sim", "in_bom", "on_board", "dnp"))]
        if node and node[0] in ("pin_names", "pin_numbers", "pin") and ["hide", "yes"] in node:
            node = [x for x in node if x != ["hide", "yes"]] + ["hide"]            # (KiCad 8: a bare flag there; a
                                                                                   #  hidden pin's too, or the page fails)
        return node
    out = []
    for f in sorted(os.listdir(src_dir)):
        src, dst = os.path.join(src_dir, f), os.path.join(out_dir, f)
        if f.endswith(".kicad_pcb") or f.endswith(".kicad_sch"):
            tree = clean(_sx_parse(open(src).read()))
            for i, x in enumerate(tree):
                if isinstance(x, list) and x and x[0] == "version":
                    tree[i] = ["version", 20240108 if f.endswith(".kicad_pcb") else 20231120]
                elif isinstance(x, list) and x and x[0] == "generator_version":
                    tree[i] = ["generator_version", _Q("8.0")]
                elif isinstance(x, list) and x and x[0] == "layers" and f.endswith(".kicad_pcb"):
                    rows = [[_K8_LAYERS[str(r[1])]] + r[1:] for r in x[1:]]
                    tree[i] = ["layers"] + sorted(rows, key=lambda r: r[0])
            text = _sx_dump(tree)                        # (KiCad 8 reads the bare hide flag only after the offset)
            text = re.sub(r"\((pin_names|pin_numbers) hide((?: \([^()]*\))*)\)", r"(\1\2 hide)", text)
            with open(dst, "w") as fh:
                fh.write(text + "\n")
            out.append(dst)
        elif f.endswith(".kicad_pro"):
            shutil.copyfile(src, dst)
            out.append(dst)
    return out


def export_kicad(board: Board, out_dir: str, origin: Tuple[float, float] = (150.0, 100.0),
                 place_labels: bool = True, schematic: bool = True) -> List[str]:
    """``<name>.kicad_pcb``, ``<name>.kicad_pro`` and (``schematic``) ``<name>.kicad_sch`` on KiCad's own symbols
    (kicad_sch.py) - KiCad 9 format; KiCad 9 and later open it - for further work in
    KiCad: every part on its library footprint (with its 3D model, MPN and note), the nets, tracks, vias, the pours as
    filled zones (solid pad connection, islands kept, as the harness fills them), keep-outs as rule areas, mounting
    holes, the outline on Edge.Cuts and the legend on the silkscreen. The project file carries the board's clearance,
    so KiCad's DRC checks against the same rule. Reference designators are placed clear of pads, parts and
    each other (``place_refs``), or kept off the silkscreen where a dense area has no room; a part drawn in the
    program (``body=``) is written to ``3d/`` as STEP and used as its model. Board mm, y up, are moved so the board's centre sits at ``origin`` in
    KiCad's page (y down). Zones carry the harness's fill: KiCad refills them with its own rules on 'B'."""
    import json
    import os
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    os.makedirs(out_dir, exist_ok=True)
    x0, y0, x1, y1 = board.outline.bounds
    ox, oy = origin[0] - (x0 + x1) / 2, origin[1] + (y0 + y1) / 2
    tx, ty = (lambda x: round(x + ox, 6)), (lambda y: round(oy - y, 6))
    LN = {"F": _Q("F.Cu"), "B": _Q("B.Cu"), "1": _Q("In1.Cu"), "2": _Q("In2.Cu")}
    netnum = {n: i + 1 for i, n in enumerate(sorted(board.nets))}
    cu_mm, in_mm = round(board.oz * OZ_MM, 4), round(board.inner_oz * OZ_MM, 4)
    diel = lambda i, kind, t: ["layer", _Q(f"dielectric {i}"), ["type", _Q(kind)], ["thickness", round(t, 4)],
                               ["material", _Q("FR4")], ["epsilon_r", 4.5], ["loss_tangent", 0.02]]
    if len(board.layers) == 2:
        mid = [diel(1, "core", board.t - 2 * cu_mm - 0.02)]
    else:                                       # the common 4-layer build: prepreg / core / prepreg
        pp = 0.2104
        mid = [diel(1, "prepreg", pp), ["layer", _Q("In1.Cu"), ["type", _Q("copper")], ["thickness", in_mm]],
               diel(2, "core", board.t - 2 * cu_mm - 2 * in_mm - 2 * pp - 0.02),
               ["layer", _Q("In2.Cu"), ["type", _Q("copper")], ["thickness", in_mm]], diel(3, "prepreg", pp)]
    stack = ["stackup", ["layer", _Q("F.SilkS"), ["type", _Q("Top Silk Screen")]],
             ["layer", _Q("F.Paste"), ["type", _Q("Top Solder Paste")]],
             ["layer", _Q("F.Mask"), ["type", _Q("Top Solder Mask")], ["thickness", 0.01]],
             ["layer", _Q("F.Cu"), ["type", _Q("copper")], ["thickness", cu_mm]]] + mid + [
             ["layer", _Q("B.Cu"), ["type", _Q("copper")], ["thickness", cu_mm]],
             ["layer", _Q("B.Mask"), ["type", _Q("Bottom Solder Mask")], ["thickness", 0.01]],
             ["layer", _Q("B.Paste"), ["type", _Q("Bottom Solder Paste")]],
             ["layer", _Q("B.SilkS"), ["type", _Q("Bottom Silk Screen")]],
             ["copper_finish", _Q("None")], ["dielectric_constraints", "no"]]
    pcb_ = ["kicad_pcb", ["version", 20241229], ["generator", _Q("prometheus")], ["generator_version", _Q("9.0")],
            ["general", ["thickness", board.t], ["legacy_teardrops", "no"]], ["paper", _Q("A4")],
            ["layers"] + [[l[0], _Q(l[1]), l[2]] + ([_Q(l[3])] if len(l) > 3 else [])
                          for l in _KICAD_LAYERS[:1] + ([(4, "In1.Cu", "signal"), (6, "In2.Cu", "signal")]
                                                       if len(board.layers) == 4 else []) + _KICAD_LAYERS[1:]],
            ["setup", stack, ["pad_to_mask_clearance", 0], ["allow_soldermask_bridges_in_footprints", "no"],
             ["tenting", "front", "back"]],
            ["net", 0, _Q("")]] + [["net", i, _Q(n)] for n, i in netnum.items()]
    labels = place_refs(board) if place_labels else {}
    for ref in sorted(board.comps, key=lambda r: (re.sub(r"\d", "", r), r)):
        pcb_.append(_kicad_footprint(board, ref, board.comps[ref], netnum, tx, ty, labels.get(ref, "keep"),
                                     body_dir=os.path.join(out_dir, "3d")))
    hole_refs, k_h = [], 0                               # (H1, H2 ... past any part the script named so: two
    for _ in board.holes:                                #  footprints with one reference and KiCad's Specctra
        k_h += 1                                         #  export, FreeRouting's input, fails without a word)
        while f"H{k_h}" in board.comps:
            k_h += 1
        hole_refs.append(f"H{k_h}")
    for i, (x, y, d) in enumerate(board.holes):          # mounting holes: a bare (non-plated) hole each
        pcb_.append(["footprint", _Q(f"MountingHole:MountingHole_{d:g}mm"), ["layer", _Q("F.Cu")],
                     ["uuid", _uuid(board.name, "hole", i)], ["at", tx(x), ty(y), 0],
                     ["property", _Q("Reference"), _Q(hole_refs[i]), ["at", 0, -d / 2 - 1.0, 0], ["layer", _Q("F.Fab")],
                      ["uuid", _uuid(board.name, "hole", i, "ref")], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
                     ["property", _Q("Value"), _Q(f"hole {d:g} mm"), ["at", 0, d / 2 + 1.0, 0], ["layer", _Q("F.Fab")],
                      ["uuid", _uuid(board.name, "hole", i, "val")], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
                     ["attr", "exclude_from_pos_files", "exclude_from_bom"],
                     ["fp_circle", ["center", 0, 0], ["end", d / 2, 0], ["stroke", ["width", 0.05], ["type", "solid"]],
                      ["fill", "no"], ["layer", _Q("Cmts.User")], ["uuid", _uuid(board.name, "hole", i, "c")]],
                     ["pad", _Q(""), "np_thru_hole", "circle", ["at", 0, 0, 0], ["size", d, d], ["drill", d],
                      ["layers", _Q("*.Cu"), _Q("*.Mask")], ["uuid", _uuid(board.name, "hole", i, "pad")]]])
    for k_, ring in enumerate([board.outline.exterior] + list(board.outline.interiors)):   # (the edge, the cut-outs)
        pcb_.append(["gr_poly", ["pts"] + [["xy", tx(x), ty(y)] for x, y in list(ring.coords)[:-1]],
                     ["stroke", ["width", 0.1], ["type", "solid"]], ["fill", "no"], ["layer", _Q("Edge.Cuts")],
                     ["uuid", _uuid(board.name, "edge", *([k_] if k_ else []))]])
    for i, (txt, (x, y), size, ang, *on) in enumerate(getattr(board, "silk_text", [])):
        back = bool(on) and on[0] == "B"
        just = (on[1].split() if len(on) > 1 else []) + (["mirror"] if back else [])
        pcb_.append(["gr_text", _Q(txt), ["at", tx(x), ty(y), ang], ["layer", _Q("B.SilkS" if back else "F.SilkS")],
                     ["uuid", _uuid(board.name, "silk", i)],
                     ["effects", ["font", ["size", size, size], ["thickness", round(size * 0.15, 3)]]] +
                     ([["justify"] + just] if just else [])])
    for i, g in enumerate(getattr(board, "graphics", [])):       # (logos and drawings, as imported)
        xy = ["pts"] + [["xy", tx(x), ty(y)] for x, y in g["pts"]]
        st = ["stroke", ["width", g.get("w") or 0], ["type", "solid"]]
        if g.get("closed"):
            pcb_.append(["gr_poly", xy, st, ["fill", "yes" if g.get("fill") else "no"], ["layer", _Q(g["layer"])],
                         ["uuid", _uuid(board.name, "art", i)]])
        else:
            pcb_ += [["gr_line", ["start", tx(a[0]), ty(a[1])], ["end", tx(b[0]), ty(b[1])], st, ["layer", _Q(g["layer"])],
                      ["uuid", _uuid(board.name, "art", i, j)]] for j, (a, b) in enumerate(zip(g["pts"], g["pts"][1:]))]
    for i, t in enumerate(board.tracks):
        for j, (a, b) in enumerate(zip(t["pts"], t["pts"][1:])):
            if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9:
                continue
            pcb_.append(["segment", ["start", tx(a[0]), ty(a[1])], ["end", tx(b[0]), ty(b[1])], ["width", t["w"]],
                         ["layer", LN[t["layer"]]], ["net", netnum[t["net"]]], ["uuid", _uuid(board.name, "trk", i, j)]])
    for i, v in enumerate(board.vias):
        pcb_.append(["via", ["at", tx(v["at"][0]), ty(v["at"][1])], ["size", v["dia"]], ["drill", v["drill"]],
                     ["layers", _Q("F.Cu"), _Q("B.Cu")], ["net", netnum[v["net"]]], ["uuid", _uuid(board.name, "via", i)]])
    fills = {}
    ring = lambda r: ["polygon", ["pts"] + [["xy", tx(x), ty(y)] for x, y in list(r.coords)[:-1]]]
    cu_all = board.copper() if board.pours else None    # (one pass for every pour: a net's pour is carved only round
    for i, pz in enumerate(board.pours):                 # other nets' fixed copper, so it is the same as copper(only_
        key = (pz["net"], pz["layer"])                   # net=...) gives, without a pass per net)
        if key not in fills:                             # (the fill as KiCad will judge it: clear of every bare
            fills[key] = board.pour_fill(*key, cu=cu_all)  # hole by the hole clearance, a pad merged into it too)
            bare_ = [Point(x, y).buffer(d / 2 + HOLE_CLEAR + 0.01, 24) for x, y, d in board.all_holes()]
            if bare_ and fills[key] is not None and not fills[key].is_empty:
                fills[key] = fills[key].difference(unary_union(bare_))
        zp = Polygon(pz["poly"], pz.get("holes") or None)
        fill = _fractured(fills[key].intersection(zp))
        pcb_.append(["zone", ["net", netnum[pz["net"]]], ["net_name", _Q(pz["net"])], ["layer", LN[pz["layer"]]],
                     ["uuid", _uuid(board.name, "zone", i)], ["hatch", "edge", 0.5],
                     ["priority", 0 if pz.get("fill") else i + 1],   # (KiCad ranks overlapping zones: a background
                                                                     #  fill below the power pours it goes round)
                     ["connect_pads", "yes", ["clearance", board.clear]], ["min_thickness", MIN_FILL],
                     ["filled_areas_thickness", "no"],
                     ["fill", "yes", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5], ["island_removal_mode", 0]],
                     ring(zp.exterior)] + [ring(h) for h in zp.interiors] +   # (a zone's later polygons are its holes)
                    [["filled_polygon", ["layer", LN[pz["layer"]]],
                      ["pts"] + [["xy", tx(x), ty(y)] for x, y in f]] for f in fill])
    for i, k in enumerate(getattr(board, "keepouts", [])):
        pcb_.append(["zone", ["net", 0], ["net_name", _Q("")], ["layers"] + [LN[L] for L in board.layers if L in k["layers"]],
                     ["uuid", _uuid(board.name, "keepout", i)], ["name", _Q(k["why"] or f"keepout {i + 1}")],
                     ["hatch", "edge", 0.5], ["connect_pads", ["clearance", 0]], ["min_thickness", 0.25],
                     ["filled_areas_thickness", "no"],
                     ["keepout", ["tracks", "not_allowed"], ["vias", "not_allowed"], ["pads", "not_allowed"],
                      ["copperpour", "not_allowed"], ["footprints", "allowed"]],
                     ["fill", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5]],
                     ["polygon", ["pts"] + [["xy", tx(x), ty(y)] for x, y in k["poly"]]]])
    path = os.path.join(out_dir, f"{board.name}.kicad_pcb")
    with open(path, "w") as f:
        f.write(_sx_dump(pcb_) + "\n")
    w_min = min([t["w"] for t in board.tracks] or [0.2])
    v_min = min(board.vias, key=lambda v: v["dia"]) if board.vias else {"dia": 0.6, "drill": 0.3}
    d_min = min([v["drill"] for v in board.vias] + [d for *_, d in board.pad_shapes() if d] or [0.3])  # (a footprint's
                                                                                        # own thermal vias included)
    pro = {"meta": {"filename": f"{board.name}.kicad_pro", "version": 3},
           "board": {"design_settings": {"rules": {"min_clearance": board.clear, "min_track_width": round(w_min, 4),
                                                   "min_via_diameter": v_min["dia"], "min_through_hole_diameter": d_min,
                                                   "min_copper_edge_clearance": board.edge,
                                                   "min_hole_clearance": board.hole_clear}}},
           "net_settings": {"meta": {"version": 4},
                            "classes": [{"name": "Default", "clearance": board.clear, "track_width": round(w_min, 4),
                                         "via_diameter": v_min["dia"], "via_drill": v_min["drill"],
                                         "priority": 2147483647}]}}
    kr = getattr(board, "kicad_rules", None) or {}       # (an imported design's own rules and DRC severities kept)
    pro["board"]["design_settings"]["rules"] = {**kr.get("rules", {}), **pro["board"]["design_settings"]["rules"]}
    if kr.get("rule_severities"):
        pro["board"]["design_settings"]["rule_severities"] = kr["rule_severities"]
    # each net's routed width as a net class (KiCad's interactive router and FreeRouting, through KiCad's
    # Specctra export, then route each net as wide as the harness does)
    widths = {n: round(board.net_width(n), 4) for n in board.nets}
    for k, w in enumerate(sorted(set(widths.values()))):
        pro["net_settings"]["classes"].append({"name": f"W{w:.2f}mm", "clearance": board.clear, "track_width": w,
                                               "via_diameter": v_min["dia"], "via_drill": v_min["drill"],
                                               "priority": k})
    pro["net_settings"]["netclass_patterns"] = [{"netclass": f"W{w:.2f}mm", "pattern": n}
                                                for n, w in sorted(widths.items()) if "*" not in n and "?" not in n]
    pro["net_settings"]["netclass_assignments"] = {n: f"W{w:.2f}mm" for n, w in sorted(widths.items())}
    ppath = os.path.join(out_dir, f"{board.name}.kicad_pro")
    with open(ppath, "w") as f:
        json.dump(pro, f, indent=2)
    out = [path, ppath]
    if not schematic:
        return out
    try:                                                 # the schematic, on KiCad's own symbols (kicad_sch.py)
        import kicad_sch
        out.append(kicad_sch.write(board, out_dir))
    except Exception as e:                               # (no symbols offline: the board is still written)
        board.skip("the KiCad schematic", f"{type(e).__name__}: {str(e)[:200]}", "KiCad's symbols are fetched online")
    return out


NET_COLORS ={"GND": (40, 40, 40), "VBUS": (200, 40, 40), "VIN": (230, 110, 20), "VD": (180, 80, 160),
              "5V": (200, 120, 0), "3V3": (0, 120, 200)}


def schematic(board: Board, png: str, groups: Sequence[Tuple[str, Sequence[str]]],
              pin_names: Optional[Dict[str, Dict[str, str]]] = None, title: str = "", notes: Sequence[str] = ()):
    """A net-label schematic of the board, drawn from its netlist (so it cannot drift from the layout): one panel per
    functional block; ICs as boxes with their pin names, two-pin parts as symbols (R zigzag, C plates, L coil,
    D triangle, connectors as boxes); every pin's net written at the end of its stub, power nets in colour."""
    from PIL import Image, ImageDraw, ImageFont
    pin_names = pin_names or {}
    pn = board.pad_net()
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
        fsm = ImageFont.truetype("DejaVuSans.ttf", 11)
        fbig = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
        ftitle = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:
        font = fsm = fbig = ftitle = ImageFont.load_default()
    col = lambda n: NET_COLORS.get(n, (20, 90, 40))

    def pins_of(ref):
        c = board.comps[ref]
        out = []
        for num in c["pads"]:
            n = pn.get((ref, num))
            if n:
                out.append((num, n))
        return sorted(set(out), key=lambda p: (int(p[0]) if p[0].isdigit() else 999, p[0]))

    def comp_size(ref):
        ps = pins_of(ref)
        if len(ps) <= 2 and ref[0] in "RCLDQ" and ref not in pin_names:
            return 300, 46
        return 330, 30 + 18 * max(1, (len(ps) + 1) // 2)

    PANEL_W = 720
    cols = 3
    layout = []
    heights = []
    for gi, (gname, refs) in enumerate(groups):
        h = 40 + sum(comp_size(r)[1] + 14 for r in refs if r in board.comps)
        heights.append(h)
    rows = [heights[i:i + cols] for i in range(0, len(heights), cols)]
    row_h = [max(r) for r in rows]
    W = cols * (PANEL_W + 20) + 20
    H = 90 + sum(row_h) + 20 * len(rows) + 22 * len(notes) + 20
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    d.text((20, 18), title or f"{board.name} schematic", fill=(10, 10, 10), font=ftitle)
    y0 = 60
    for ri, rh in enumerate(row_h):
        for ci in range(cols):
            gi = ri * cols + ci
            if gi >= len(groups):
                break
            gname, refs = groups[gi]
            x = 20 + ci * (PANEL_W + 20)
            d.rounded_rectangle([x, y0, x + PANEL_W, y0 + rh], 10, outline=(150, 150, 140), width=2, fill=(255, 255, 255))
            d.text((x + 14, y0 + 10), gname, fill=(30, 30, 30), font=fbig)
            yy = y0 + 42
            for ref in refs:
                if ref not in board.comps:
                    continue
                c = board.comps[ref]
                w, h = comp_size(ref)
                cx = x + PANEL_W // 2
                ps = pins_of(ref)
                if h == 46:                                      # two-pin part, drawn as its symbol
                    a, b_ = (cx - 40, yy + 20), (cx + 40, yy + 20)
                    k = ref[0]
                    if k == "R":
                        pts = [a] + [(a[0] + 10 + 10 * i, yy + 20 + (8 if i % 2 else -8)) for i in range(6)] + [b_]
                        d.line(pts, fill=(0, 0, 0), width=2)
                    elif k == "C":
                        d.line([a, (cx - 5, yy + 20)], fill=(0, 0, 0), width=2)
                        d.line([(cx + 5, yy + 20), b_], fill=(0, 0, 0), width=2)
                        d.line([(cx - 5, yy + 6), (cx - 5, yy + 34)], fill=(0, 0, 0), width=3)
                        d.line([(cx + 5, yy + 6), (cx + 5, yy + 34)], fill=(0, 0, 0), width=3)
                    elif k == "L":
                        d.line([a, (cx - 30, yy + 20)], fill=(0, 0, 0), width=2)
                        for i in range(4):
                            d.arc([cx - 30 + 15 * i, yy + 12, cx - 15 + 15 * i, yy + 28], 180, 360, fill=(0, 0, 0), width=2)
                        d.line([(cx + 30, yy + 20), b_], fill=(0, 0, 0), width=2)
                    elif k == "D":
                        d.line([a, b_], fill=(0, 0, 0), width=2)
                        d.polygon([(cx + 10, yy + 20), (cx - 10, yy + 8), (cx - 10, yy + 32)], fill=(0, 0, 0))
                        d.line([(cx + 10, yy + 8), (cx + 10, yy + 32)], fill=(0, 0, 0), width=3)
                    else:
                        d.rectangle([cx - 30, yy + 8, cx + 30, yy + 32], outline=(0, 0, 0), width=2)
                    lab = f"{ref}  {c['value']}"
                    d.text((cx - d.textlength(lab, font=fsm) / 2, yy - 2), lab, fill=(0, 0, 0), font=fsm)
                    for (num, n), (px, py), side in zip(ps, (a, b_), (-1, 1)):
                        ex = px + side * 50
                        d.line([(px, py), (ex, py)], fill=col(n), width=3)
                        tx = ex + 6 if side > 0 else ex - 6 - d.textlength(n, font=font)
                        d.text((tx, py - 8), n, fill=col(n), font=font)
                else:                                            # an IC or connector: a box with named pins
                    bx0, bx1 = cx - 75, cx + 75
                    d.rectangle([bx0, yy, bx1, yy + h - 10], outline=(0, 0, 0), width=2, fill=(250, 248, 235))
                    lab = f"{ref}  {c['value']}"
                    d.text((cx - d.textlength(lab, font=font) / 2, yy + 4), lab, fill=(0, 0, 0), font=font)
                    half = (len(ps) + 1) // 2
                    for i, (num, n) in enumerate(ps):
                        left = i < half
                        py = yy + 30 + 18 * (i if left else i - half)
                        px = bx0 if left else bx1
                        ex = px - 45 if left else px + 45
                        d.line([(px, py), (ex, py)], fill=col(n), width=3)
                        name = pin_names.get(ref, {}).get(num, num)
                        tw = d.textlength(name, font=fsm)
                        d.text((px + 4 if left else px - 4 - tw, py - 7), name, fill=(60, 60, 60), font=fsm)
                        tx = ex - 6 - d.textlength(n, font=font) if left else ex + 6
                        d.text((tx, py - 8), n, fill=col(n), font=font)
                yy += h + 14
        y0 += rh + 20
    for i, t in enumerate(notes):
        d.text((24, y0 + 6 + 22 * i), t, fill=(40, 40, 40), font=font)
    img.save(png)
    return png
