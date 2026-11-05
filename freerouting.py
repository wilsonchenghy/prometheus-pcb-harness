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
import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

UM = 1000.0                                              # .dsn units: micrometres (mm x 1000)
FR_MARGIN = 0.0      # mm added to every clearance FreeRouting is given in route_kicad (0.06 tried: it then left
#                      0.4 mm pitch pins unrouted and still fell short in places)
PAD_EXTEND = 0.0     # mm a fine-pitch pin is lengthened outwards in the .dsn (off: FreeRouting then ends tracks on
#                      the made-up part, off the real pad)


def find_jar() -> Optional[str]:
    """the FreeRouting jar: $FREEROUTING_JAR, else the newest in ~/.cache/prometheus/freerouting, else None"""
    j = os.environ.get("FREEROUTING_JAR")
    if j and os.path.exists(j):
        return j
    found = sorted(glob.glob(os.path.expanduser("~/.cache/prometheus/freerouting/*.jar")))
    return found[-1] if found else None


_JAVA: List = []


def java_version(exe: str) -> int:
    """a Java's major version (``java -version``: "1.8.0_x" is 8, "17.0.2" is 17), 0 when it does not run"""
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0
    m = re.search(r'version "(\d+)(?:\.(\d+))?', r.stderr + r.stdout)
    if not m:
        return 0
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def find_java(least: int = 17) -> Optional[Tuple[str, int]]:
    """a Java FreeRouting runs on (17+): $PROMETHEUS_JAVA, macOS's java_home, $JAVA_HOME, the PATH's, then the usual
    installs (Homebrew, /usr/lib/jvm) - the first new enough, so a machine whose default Java is older (kept for
    something else) still routes; None when there is none"""
    if _JAVA:
        return _JAVA[0]
    cands = [os.environ.get("PROMETHEUS_JAVA") or os.environ.get("ZEUS_JAVA")]   # (its name before: still read)
    if os.path.exists("/usr/libexec/java_home"):
        try:
            home = subprocess.run(["/usr/libexec/java_home", "-v", f"{least}+"], capture_output=True, text=True,
                                  timeout=30).stdout.strip()
            cands.append(os.path.join(home, "bin", "java") if home else None)
        except (OSError, subprocess.SubprocessError):
            pass
    if os.environ.get("JAVA_HOME"):
        cands.append(os.path.join(os.environ["JAVA_HOME"], "bin", "java"))
    cands += [shutil.which("java"), "/opt/homebrew/opt/openjdk/bin/java", "/usr/local/opt/openjdk/bin/java"]
    cands += sorted(glob.glob("/opt/homebrew/opt/openjdk@*/bin/java"), reverse=True)
    cands += sorted(glob.glob("/usr/lib/jvm/*/bin/java"), reverse=True)
    seen = set()
    for c in cands:
        if not c or c in seen or not os.path.exists(c):
            continue
        seen.add(c)
        v = java_version(c)
        if v >= least:
            _JAVA.append((c, v))
            return _JAVA[0]
    return None


def available() -> bool:
    return bool(find_jar() and find_java())


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
             '  (parser (string_quote ") (space_in_quoted_tokens on) (host_cad "Prometheus") (host_version "1"))',
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
    for x, y, d in board.all_holes():
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
    import numpy as np
    from shapely import affinity
    for ref, lst in by_ref.items():
        ox, oy = board.comps[ref]["at"]
        cs = np.array([(g.centroid.x, g.centroid.y) for num, L, g, d in lst if num])
        fine = False
        if len(cs) >= 6:                                  # a fine-pitch part (pins closer than 0.8 mm)
            dd = np.hypot(*(cs[:, None, :] - cs[None, :, :]).transpose(2, 0, 1))
            np.fill_diagonal(dd, np.inf)
            fine = bool(dd.min() < 0.8)
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
                if PAD_EXTEND and fine and num and not p["drill"]:   # a fine-pitch pin lengthened outwards: FreeRouting turns
                    dx, dy = cx - ox, cy - oy             # 45 degrees right off a pin's end and clips the next
                    step = (PAD_EXTEND * (1 if dx > 0 else -1), 0.0) if abs(dx) > abs(dy) else \
                        (0.0, PAD_EXTEND * (1 if dy > 0 else -1))         # pin's corner; from further out it
                    q = q.union(affinity.translate(q, *step)).convex_hull  # clears it (the pin gaps unchanged)
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
                board.tracks[-1]["fr"] = True            # (FreeRouting's: torn up again if it breaks a clearance)
                nt += 1
        for v in [c for c in net if isinstance(c, list) and c and c[0] == "via"]:
            x, y = float(v[2]) * scale, float(v[3]) * scale
            key = (name, (round(x, 3), round(y, 3)))
            if key in have_v:
                continue
            board.via(name, (round(x, 4), round(y, 4)), via[0], via[1])
            board.vias[-1]["fr"] = True
            nv += 1
    return nt, nv


def run(dsn: str, ses: str, passes: int = 100, timeout: int = 1800, threads: int = 1) -> str:
    """FreeRouting on a .dsn, headless, writing the .ses; returns its log"""
    jar = find_jar()
    if not jar:
        raise RuntimeError("FreeRouting jar not found: set FREEROUTING_JAR or put it in ~/.cache/prometheus/freerouting")
    cmd = [find_java()[0], "-Djava.awt.headless=true", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes),
           "-mt", str(threads), "--gui.enabled=false"]
    log = ""
    for attempt in range(3):                            # (run alongside other copies, FreeRouting now and then
        if os.path.exists(ses):                         # routes and exits leaving its session empty: a run that
            os.remove(ses)                              # gives no session is run again)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        log += r.stdout + r.stderr
        if os.path.exists(ses) and "(session" in open(ses, errors="replace").read(4096):
            return log
    raise RuntimeError("FreeRouting wrote no session (3 runs):\n" + log[-3000:])


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
    t0, v0 = len(board.tracks), len(board.vias)
    import_ses(board, ses, via=via)
    fr_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"]))) for t in board.tracks[t0:]}
    fr_v = {(v["net"], tuple(v["at"])) for v in board.vias[v0:]}
    board.__dict__.pop("_cu_memo", None)
    if not finish:
        return []
    board._fan = set(getattr(board, "_fan", set())) | {("~freerouting", "")}   # (no fan-out stubs: FreeRouting
    probs = pcb.autoroute(board, **kw)                                        # already left every pad its way):
    return _repair(board, fr_t, fr_v, probs, kw)


def _repair(board, fr_t, fr_v, probs, kw):
    """after FreeRouting and the harness's finish: tear up and re-route the FreeRouting copper of any net in a short
    or a clearance slip (kept only when that does better)"""
    import pcb
    # the harness's router then does the poured nets and anything FreeRouting left open. FreeRouting's own
    # clearance test misses a 45-degree turn just off a fine-pitch pin clipping the next pin's corner (by ~0.04
    # mm); measured exactly here, the FreeRouting copper of each net in such a pair is torn up and the harness's
    # router, which measures the real shapes, routes those few nets again (what a KiCad user does by hand
    # after FreeRouting, at KiCad's DRC)
    score = lambda ps: pcb.severity(ps) + pcb.severity(board.check())
    for _ in range(3):
        before = (list(board.tracks), list(board.vias), probs, score(probs))
        bad = set()
        for msg in board.check():
            m = re.match(r"\S+\.Cu: (.+?) and (.+?) (short|[\d.]+ mm apart)", msg) or \
                re.match(r"via (\S+) and via (\S+) drills", msg) or \
                re.match(r"via (\S+) and (hole) drills", msg) or \
                re.match(r"track (\S+) near .* comes within .* of the (board) edge", msg)   # (no hole-to-hole
            # rule in FreeRouting, and its edge clearance is not the house's)
            if m:
                bad |= {n for n in (m.group(1), m.group(2)) if n in board.nets}
        is_fr = lambda t: (t["net"], t["layer"], tuple(map(tuple, t["pts"]))) in fr_t   # (by shape: a cached
        torn = {n for n in bad if any(is_fr(t) and t["net"] == n for t in board.tracks)}  # routing drops flags)
        mine = False
        if not torn:                                     # (the harness's own finish made it: its tracks of those
            poured = {p["net"] for p in board.pours}     # nets, not the pours' via drops, are routed again)
            torn = {n for n in bad if n not in poured}
            mine = True
            if not torn or len(torn) > 4:                # (a few nets; many is a placement to change, not repair)
                break
        board.tracks = [t for t in board.tracks if not ((mine or is_fr(t)) and t["net"] in torn)]
        board.vias = [v for v in board.vias if not ((mine or (v["net"], tuple(v["at"])) in fr_v)
                                                    and v["net"] in torn)]
        board.__dict__.pop("_cu_memo", None)
        probs = pcb.autoroute(board, **kw)
        if score(probs) >= before[3]:                   # (kept only when it does better)
            board.tracks, board.vias, probs = before[0], before[1], before[2]
            board.__dict__.pop("_cu_memo", None)
            break
    return probs


_KICAD_ROUND_TRIP = r"""
import sys, pcbnew
mode, pcb, path = sys.argv[1:4]
b = pcbnew.LoadBoard(pcb)
if mode == "dsn":
    ok = pcbnew.ExportSpecctraDSN(b, path)
else:
    ok = pcbnew.ImportSpecctraSES(b, path)
    pcbnew.SaveBoard(sys.argv[4], b)
sys.exit(0 if ok else 3)
"""


def kicad_available() -> bool:
    """KiCad's Python API (pcbnew) can be loaded"""
    import pcb
    try:
        return subprocess.run([pcb._pcbnew_python(), "-c", "import pcbnew"], capture_output=True,
                              timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _read_kicad_copper(path: str, board) -> Tuple[list, list]:
    """the segments and vias of a .kicad_pcb, in the board's coordinates (the inverse of export_kicad's move)"""
    import pcb
    tree = pcb._sx_parse(open(path).read()) if hasattr(pcb, "_sx_parse") else _sexpr(open(path).read())
    x0, y0, x1, y1 = board.outline.bounds
    ox, oy = 150.0 - (x0 + x1) / 2, 100.0 + (y0 + y1) / 2
    names = {}
    for n in [c for c in tree if isinstance(c, list) and c and c[0] == "net"]:
        names[str(n[1])] = str(n[2]) if len(n) > 2 else ""
    back = {"F.Cu": "F", "B.Cu": "B", "In1.Cu": "1", "In2.Cu": "2"}
    segs, vias = [], []

    def val(node, key):
        f = [c for c in node if isinstance(c, list) and c and c[0] == key]
        return f[0] if f else None
    for c in tree:
        if not isinstance(c, list) or not c:
            continue
        if c[0] == "segment":
            s_, e_, w, L, n = val(c, "start"), val(c, "end"), val(c, "width"), val(c, "layer"), val(c, "net")
            net = names.get(str(n[1]), str(n[1])) if n else ""
            if L and str(L[1]) in back and net:
                segs.append((net, back[str(L[1])], [(round(float(s_[1]) - ox, 4), round(oy - float(s_[2]), 4)),
                                                     (round(float(e_[1]) - ox, 4), round(oy - float(e_[2]), 4))],
                             float(w[1])))
        elif c[0] == "via":
            at, size, drill, n = val(c, "at"), val(c, "size"), val(c, "drill"), val(c, "net")
            net = names.get(str(n[1]), str(n[1])) if n else ""
            if net:
                vias.append((net, (round(float(at[1]) - ox, 4), round(oy - float(at[2]), 4)), float(drill[1]),
                             float(size[1])))
    return segs, vias


def route_kicad(board, passes: int = 100, finish: bool = True, keep: Optional[str] = None, **kw) -> List[str]:
    """FreeRouting the way KiCad users run it: the board goes to KiCad (export_kicad), KiCad writes the Specctra
    design (its own pads, net classes and rules), FreeRouting routes it, KiCad imports the session, and the
    routed tracks and vias come back from KiCad's board. Needs KiCad's Python API besides Java and the jar.
    Then, as ``route``, the harness's router finishes the poured nets and anything left. Returns the problems"""
    import pcb
    work = keep or tempfile.mkdtemp()
    os.makedirs(work, exist_ok=True)
    kpcb = pcb.export_kicad(board, os.path.join(work, "kicad"), schematic=False)[0]
    script = os.path.join(work, "kicad_round_trip.py")
    with open(script, "w") as f:
        f.write(_KICAD_ROUND_TRIP)
    py = pcb._pcbnew_python()
    dsn, ses = os.path.join(work, f"{board.name}.dsn"), os.path.join(work, f"{board.name}.ses")
    r = subprocess.run([py, script, "dsn", kpcb, dsn], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not os.path.exists(dsn):
        raise RuntimeError("KiCad's Specctra export failed:\n" + (r.stdout + r.stderr)[-2000:])
    txt = open(dsn).read()                               # FreeRouting falls short of a clearance by up to ~50 um
    unit = re.search(r"\(unit (\w+)\)", txt)            # on its 45-degree segments: it is given a margin (what
    k = {"um": 1.0, "mm": 0.001, "mil": 1 / 25.4, "inch": 1 / 25400.0}.get(unit.group(1) if unit else "um", 1.0)
    txt = re.sub(r"\(clearance ([\d.]+)", lambda m: f"(clearance {float(m.group(1)) + FR_MARGIN * 1000 * k:.4f}",
                 txt)                                    # KiCad users do by hand), the real rule judged after
    with open(dsn, "w") as f:
        f.write(txt)
    log = run(dsn, ses, passes=passes)
    with open(os.path.join(work, "freerouting.log"), "w") as f:
        f.write(log)
    routed = os.path.join(work, f"{board.name}_routed.kicad_pcb")
    r = subprocess.run([py, script, "ses", kpcb, ses, routed], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not os.path.exists(routed):
        raise RuntimeError("KiCad's Specctra import failed:\n" + (r.stdout + r.stderr)[-2000:])
    segs, vias = _read_kicad_copper(routed, board)
    t0, v0 = len(board.tracks), len(board.vias)
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"]))) for t in board.tracks}
    have_v = {(v["net"], tuple(round(c, 3) for c in v["at"])) for v in board.vias}
    from shapely.geometry import LineString
    on = {}                                              # (KiCad hands back a stub it already had split into
    for t in board.tracks:                               # pieces: those are not new copper)
        on.setdefault((t["net"], t["layer"], round(t["w"], 3)), []).append(LineString(t["pts"]).buffer(2e-3))
    for net, L, pts, w in segs:
        if net not in board.nets or (net, L, tuple(pts)) in have_t or math.dist(pts[0], pts[-1]) < 1e-3:
            continue
        seg = LineString(pts)
        if any(g.contains(seg) for g in on.get((net, L, round(w, 3)), [])):
            continue
        board.track(net, L, pts, round(w, 4))
    for net, at, drill, dia in vias:
        if net in board.nets and (net, tuple(round(c, 3) for c in at)) not in have_v:
            board.via(net, at, drill, dia)
    board.__dict__.pop("_cu_memo", None)
    if not finish:
        return []
    fr_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"]))) for t in board.tracks[t0:]}
    fr_v = {(v["net"], tuple(v["at"])) for v in board.vias[v0:]}
    board._fan = set(getattr(board, "_fan", set())) | {("~freerouting", "")}
    return _repair(board, fr_t, fr_v, pcb.autoroute(board, **kw), kw)


def escape_fine_pitch(board, width: float = 0.15, short: float = 0.45, long: float = 1.05) -> int:
    """the first stretch out of each pin of a fine-pitch part (pins closer than 0.8 mm), drawn before FreeRouting:
    a straight track off the pin's outer end, alternately short and long, so every turn happens clear of the
    neighbouring pins (FreeRouting's 45-degree turn right at a 0.4 mm pitch pin clips the next one). Returns how
    many it drew"""
    import numpy as np
    pn = board.pad_net()
    poured = {p["net"] for p in board.pours}
    n = 0
    for ref, c in board.comps.items():
        if c.get("overhang") or ref.upper().startswith("J"):
            continue                                       # (a connector: its pins are not a chip's fan-out)
        pads = [(num, L, g) for r, num, L, g, d in board.pad_shapes() if r == ref and num and not d]
        if len(pads) < 6:
            continue
        def _pin(g):                                       # (a pin: long and narrow; an exposed pad, its pieces
            x0_, y0_, x1_, y1_ = g.bounds                  # or a BGA ball is not, and does not set the pitch)
            return max(x1_ - x0_, y1_ - y0_) >= 1.5 * min(x1_ - x0_, y1_ - y0_)
        pads = [p_ for p_ in pads if _pin(p_[2])]
        if len(pads) < 6:
            continue
        cs = np.array([(g.centroid.x, g.centroid.y) for _, _, g in pads])
        dd = np.hypot(*(cs[:, None, :] - cs[None, :, :]).transpose(2, 0, 1))
        np.fill_diagonal(dd, np.inf)
        if dd.min() >= 0.8:
            continue
        ox, oy = c["at"]
        side_rank: Dict[tuple, int] = {}
        order = sorted(range(len(pads)), key=lambda i: (round(np.arctan2(cs[i][1] - oy, cs[i][0] - ox), 3)))
        for k, i in enumerate(order):
            num, L, g = pads[i]
            net = pn.get((ref, num))
            if not net or net in poured or net not in board.nets:
                continue
            x0_, y0_, x1_, y1_ = g.bounds
            cx, cy = g.centroid.x, g.centroid.y
            dx, dy = cx - ox, cy - oy
            if max(x1_ - x0_, y1_ - y0_) < 1.5 * min(x1_ - x0_, y1_ - y0_):
                continue                                   # (a square pad: an exposed pad or a BGA ball, not a pin)
            reach = short if k % 2 == 0 else long
            if abs(dx) > abs(dy):                          # a pin on the left or right side
                ex = x1_ if dx > 0 else x0_
                pts = [(round(cx, 4), round(cy, 4)), (round(ex + (reach if dx > 0 else -reach), 4), round(cy, 4))]
            else:
                ey = y1_ if dy > 0 else y0_
                pts = [(round(cx, 4), round(cy, 4)), (round(cx, 4), round(ey + (reach if dy > 0 else -reach), 4))]
            need = max(width, board.net_width(net))       # as wide as the net needs, or as wide as fits between
            pitch = float(dd.min())                       # the neighbours (their stubs, or their full-width
            nbw = max([board.net_width(pn[(ref, pads[j][0])]) for j in np.nonzero(dd[i] < pitch * 1.2)[0]
                       if pn.get((ref, pads[j][0])) in board.nets] or [width])   # tracks past a shorter stub)
            room = pitch - board.clear - 0.01
            if need <= room:                              # (fits between the neighbours' pads at the net's width)
                board.track(net, L, pts, round(need, 3))
                n += 1
                continue
            if board.width_for(board.nets[net]["amps"], L) <= room:
                continue                                  # (a signal at a house's minimum: left to FreeRouting)
            fit = math.floor(min(room, 2 * room - nbw) * 100) / 100   # a power net necks down to the pin (marked,
            if fit < width:                               # so the current check reads it as a pin entry), and
                continue                                  # FreeRouting takes the full width on from the stub's end
            board.track(net, L, pts, round(fit, 3))
            board.tracks[-1]["neck"] = True
            n += 1
    board._fan = set(getattr(board, "_fan", set())) | {("~escape", "")}
    board.__dict__.pop("_cu_memo", None)
    return n
