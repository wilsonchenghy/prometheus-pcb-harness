"""FreeRouting as the router: the board goes out as a Specctra design (.dsn), FreeRouting routes it, its session
(.ses) comes back in as tracks and vias, and the harness's own checks judge the result. FreeRouting is the
open-source router KiCad users route with (KiCad itself has none built in): a shape-based router with rip-up and
re-route and optimising passes, where the harness's own router is a grid maze router.

    import freerouting
    probs = freerouting.route(board)      # [] when every net is joined; then board.check() as always

Needs Java 17+ and the FreeRouting jar: ``$FREEROUTING_JAR``, else ``~/.cache/prometheus/freerouting/*.jar``
(``freerouting.find_jar()``). The nets a pour carries (ground) are left to the harness's router afterwards, which
drops their pins into the pour; FreeRouting routes every other net.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

UM = 1000.0                                              # .dsn units: micrometres (mm x 1000)
PAD_GROW = 0.0                                           # mm each pad is grown by in the .dsn


def find_jar() -> Optional[str]:
    """the FreeRouting jar: $FREEROUTING_JAR, else the newest in ~/.cache/prometheus/freerouting, else None"""
    j = os.environ.get("FREEROUTING_JAR")
    if j and os.path.exists(j):
        return j
    found = sorted(glob.glob(os.path.expanduser("~/.cache/prometheus/freerouting/*.jar")))
    return found[-1] if found else None


def available() -> bool:
    return bool(find_jar() and shutil.which("java"))


def _q(s: str) -> str:
    return '"' + str(s).replace('"', "'") + '"'


def _pts(coords, ox=0.0, oy=0.0) -> str:
    return " ".join(f"{(x - ox) * UM:.1f} {(y - oy) * UM:.1f}" for x, y in coords)


def _layer_name(L: str) -> str:
    return {"F": "F.Cu", "B": "B.Cu", "1": "In1.Cu", "2": "In2.Cu"}[L]


def export_dsn(board, path: str, skip_nets: Sequence[str] = (), via: Tuple[float, float] = (0.3, 0.6)) -> str:
    """the board as a Specctra design for FreeRouting: the outline (inset by the edge gap), the layers, every pad
    with its real copper shape, the nets (``skip_nets`` left out: their pads stay as obstacles), each net's track
    width, the clearance, one via, the keep-outs and mounting holes, and the tracks and vias already drawn (kept:
    FreeRouting routes round them). Coordinates are the board's, in micrometres"""
    from shapely.geometry import Point, Polygon
    pn = board.pad_net()
    skip = set(skip_nets)
    L_ = list(board.layers)
    lines = [f"(pcb {_q(os.path.basename(path))}",
             '  (parser (string_quote ") (space_in_quoted_tokens on) (host_cad "pcb-harness") (host_version "1"))',
             "  (resolution um 10)", "  (unit um)", "  (structure"]
    for i, L in enumerate(L_):
        lines.append(f"    (layer {_layer_name(L)} (type signal) (property (index {i})))")
    inner = board.outline.buffer(-max(board.edge - board.clear, 0.0), join_style=2)
    ring = list(inner.exterior.coords)
    lines.append(f"    (boundary (path pcb 0 {_pts(ring)}))")
    for k in getattr(board, "keepouts", []):
        for L in L_:
            if L in k["layers"] or (L not in "FB" and "F" in k["layers"] and "B" in k["layers"]):
                c = list(Polygon(k["poly"]).exterior.coords)
                lines.append(f"    (keepout (polygon {_layer_name(L)} 0 {_pts(c)}))")
    hko = getattr(board, "_hole_ko", {})
    for x, y, d in board.holes:
        r = max(hko.get((x, y), 0.0), d / 2)
        lines.append(f"    (keepout (circle signal {2 * r * UM:.1f} {x * UM:.1f} {y * UM:.1f}))")
    vname = f"Via[0-{len(L_) - 1}]_{via[1] * UM:.0f}:{via[0] * UM:.0f}_um"
    lines.append(f"    (via {_q(vname)})")
    clr = board.clear * UM + 10.0                        # (+10 um: FreeRouting's arcs are polygons too; a track
    smd = board.clear * UM + 40.0                        # keeps 40 um more from a pad, whose shape FreeRouting
    lines.append(f"    (rule (width 200) (clearance {clr:.1f}) (clearance {smd:.1f} (type default_smd)) "  # reads
                 f"(clearance {board.clear * UM:.1f} (type smd_smd)))")                                 # coarser)
    lines.append("  )")
    # parts: one image per part, its pads in board orientation round the part's origin, placed unturned (so a
    # bottom part's pads keep the layer the harness gave them)
    pads = board.pad_shapes()
    by_ref: Dict[str, list] = {}
    for ref, num, L, g, d in pads:
        by_ref.setdefault(ref, []).append((num, L, g, d))
    stacks: Dict[str, str] = {}
    images, place = [], []
    pin_of: Dict[Tuple[str, str], List[str]] = {}
    for ref, lst in by_ref.items():
        ox, oy = board.comps[ref]["at"]
        groups: Dict[Tuple[str, Tuple[float, float]], dict] = {}
        for num, L, g, d in lst:                         # one pin per pad: its layers' shapes together
            c = g.centroid
            key = (num, (round(c.x, 4), round(c.y, 4)))
            groups.setdefault(key, {"layers": {}, "drill": d, "c": (c.x, c.y)})["layers"][L] = g
        seen: Dict[str, int] = {}
        pins = []
        for (num, _), p in groups.items():
            cx, cy = p["c"]
            shapes = []
            for L, g in sorted(p["layers"].items()):
                q = g if g.geom_type == "Polygon" else max(getattr(g, "geoms", [g]), key=lambda h: h.area)
                q = q.buffer(PAD_GROW, join_style=2).simplify(0.002)   # (FreeRouting's own pad geometry is not
                # quite the harness's: a little more room keeps its routes clear by the harness's measure too)
                shapes.append(f"(shape (polygon {_layer_name(L)} 0 {_pts(list(q.exterior.coords)[:-1], cx, cy)}))")
            sname = f"P{len(stacks)}"
            body = " ".join(shapes)
            for k_, v_ in stacks.items():             # (identical pads share one padstack)
                if v_ == body:
                    sname = k_
                    break
            else:
                stacks[sname] = body
            base = num if num else "MP"
            n_ = seen.get(base, 0)
            seen[base] = n_ + 1
            pid = base if n_ == 0 else f"{base}@{n_}"
            pins.append(f"      (pin {sname} {_q(pid)} {(cx - ox) * UM:.1f} {(cy - oy) * UM:.1f})")
            if num:
                pin_of.setdefault((ref, num), []).append(pid)
        images.append(f"    (image {_q('IMG_' + ref)}\n" + "\n".join(pins) + "\n    )")
        place.append(f"    (component {_q('IMG_' + ref)} (place {_q(ref)} {ox * UM:.1f} {oy * UM:.1f} front 0))")
    lines.append("  (placement")
    lines += place
    lines.append("  )")
    lines.append("  (library")
    lines += images
    for name, body in stacks.items():
        lines.append(f"    (padstack {name} {body} (attach off))")
    lines.append(f"    (padstack {_q(vname)} " + " ".join(f"(shape (circle {_layer_name(L)} {via[1] * UM:.0f}))"
                                                          for L in L_) + " (attach off))")
    lines.append("  )")
    # nets: every pad of each net (a net's pin may be several pads), each net its own class for its width
    nets: Dict[str, List[str]] = {}
    for (ref, num), n in pn.items():
        if n in board.nets and n not in skip:
            for pid in pin_of.get((ref, num), []):
                nets.setdefault(n, []).append(f"{_q(ref)}-{_q(pid)}")
    lines.append("  (network")
    for n, pins in nets.items():
        lines.append(f"    (net {_q(n)} (pins {' '.join(sorted(set(pins)))}))")
    for n in nets:
        w = board.net_width(n) * UM
        lines.append(f"    (class {_q('C_' + n)} {_q(n)} (circuit (use_via {_q(vname)})) "
                     f"(rule (width {w:.1f}) (clearance {clr:.1f})))")
    lines.append("  )")
    lines.append("  (wiring")                            # copper already drawn: kept, FreeRouting routes round it
    for t in board.tracks:
        lines.append(f"    (wire (path {_layer_name(t['layer'])} {t['w'] * UM:.1f} {_pts(t['pts'])}) "
                     f"(net {_q(t['net'])}) (type protect))")
    for v in board.vias:
        lines.append(f"    (via {_q(vname)} {v['at'][0] * UM:.1f} {v['at'][1] * UM:.1f} (net {_q(v['net'])}) "
                     f"(type protect))")
    lines.append("  )")
    lines.append(")")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _sexpr(text: str):
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack, cur = [], []
    for t in toks:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1] if t.startswith('"') else t)
    return cur[0] if cur else []


def _walk(node, key):
    if isinstance(node, list):
        if node and node[0] == key:
            yield node
        for c in node:
            yield from _walk(c, key)


def import_ses(board, path: str, via: Tuple[float, float] = (0.3, 0.6)) -> Tuple[int, int]:
    """the routes of a FreeRouting session (.ses) added to the board as tracks and vias (the ones already on the
    board, which went out as protected wiring, are not added twice). Returns (tracks, vias) added"""
    tree = _sexpr(open(path).read())
    scale = 1.0 / UM                                     # (resolution um N: coordinates in um / N)
    for r in _walk(tree, "resolution"):
        if len(r) >= 3 and r[1] == "um":
            scale = 1.0 / (UM * float(r[2]))
            break
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"]))) for t in board.tracks}
    have_v = {(v["net"], tuple(round(c, 3) for c in v["at"])) for v in board.vias}
    back = {"F.Cu": "F", "B.Cu": "B", "In1.Cu": "1", "In2.Cu": "2"}
    nt = nv = 0
    for net in _walk(tree, "net"):
        name = net[1]
        if name not in board.nets:
            continue
        for w in [c for c in net if isinstance(c, list) and c and c[0] == "wire"]:
            for p in _walk(w, "path"):
                L, wd = back.get(p[1]), float(p[2]) * scale
                xs = [float(v) * scale for v in p[3:] if not isinstance(v, list)]
                pts = [(round(xs[i], 4), round(xs[i + 1], 4)) for i in range(0, len(xs) - 1, 2)]
                if L is None or len(pts) < 2:
                    continue
                if (name, L, tuple(pts)) in have_t:
                    continue
                board.track(name, L, pts, round(wd, 4))
                nt += 1
        for v in [c for c in net if isinstance(c, list) and c and c[0] == "via"]:
            x, y = float(v[2]) * scale, float(v[3]) * scale
            key = (name, (round(x, 3), round(y, 3)))
            if key in have_v:
                continue
            board.via(name, (round(x, 4), round(y, 4)), via[0], via[1])
            nv += 1
    return nt, nv


def run(dsn: str, ses: str, passes: int = 100, timeout: int = 1800, threads: int = 1) -> str:
    """FreeRouting on a .dsn, headless, writing the .ses; returns its log"""
    jar = find_jar()
    if not jar:
        raise RuntimeError("FreeRouting jar not found: set FREEROUTING_JAR or put it in ~/.cache/prometheus/freerouting")
    cmd = ["java", "-Djava.awt.headless=true", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes),
           "-mt", str(threads), "--gui.enabled=false"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not os.path.exists(ses):
        raise RuntimeError("FreeRouting wrote no session:\n" + (r.stdout + r.stderr)[-3000:])
    return r.stdout + r.stderr


def route(board, passes: int = 100, finish: bool = True, keep: Optional[str] = None, **kw) -> List[str]:
    """route the board with FreeRouting: every net but the poured ones goes to FreeRouting; then (``finish``) the
    harness's own router drops the poured nets' pins into their pours and joins whatever is still apart. ``keep``:
    a folder to keep the .dsn / .ses / log in. Returns the problems left ([] = every pad reached)"""
    import pcb
    poured = sorted({p["net"] for p in board.pours})
    work = keep or tempfile.mkdtemp()
    os.makedirs(work, exist_ok=True)
    via = kw.get("via", (0.3, 0.6))
    dsn = export_dsn(board, os.path.join(work, f"{board.name}.dsn"), skip_nets=poured, via=via)
    ses = os.path.join(work, f"{board.name}.ses")
    log = run(dsn, ses, passes=passes)
    with open(os.path.join(work, "freerouting.log"), "w") as f:
        f.write(log)
    import_ses(board, ses, via=via)
    board.__dict__.pop("_cu_memo", None)
    if not finish:
        return []
    board._fan = set(getattr(board, "_fan", set())) | {("~freerouting", "")}   # (no fan-out stubs: FreeRouting
    return pcb.autoroute(board, **kw)                                         # already left every pad its way):
    # the harness's router then does the poured nets and anything FreeRouting left open
