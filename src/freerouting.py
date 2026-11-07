"""FreeRouting as the router, the way KiCad users run it: the board goes to KiCad, KiCad writes the Specctra design
(.dsn), FreeRouting routes it, KiCad reads the session (.ses) back, and the harness's own checks judge the result.
FreeRouting is a shape-based router with rip-up and re-route and optimising passes, where the harness's own router
is a grid maze router (it still finishes the poured nets and joins what FreeRouting leaves).

    import freerouting
    probs = freerouting.route_kicad(board)      # [] when every net is joined; then board.check() as always

Needs KiCad's Python API (pcbnew), Java 17+ and the FreeRouting jar: ``$FREEROUTING_JAR``, else
``~/.cache/prometheus/freerouting/*.jar`` (``freerouting.find_jar()``). The nets a pour carries (ground) are left to
the harness's router afterwards, which drops their pins into the pour; FreeRouting routes every other net.
"""
from __future__ import annotations

import glob
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Dict, List, Optional, Sequence, Tuple

FR_MARGIN = 0.0      # mm added to every clearance FreeRouting is given in route_kicad (0.06 tried: it then left
#                      0.4 mm pitch pins unrouted and still fell short in places)


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
        import pcb
        t0 = time.time()
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in pr.stdout:                           # (each pass as it ends, to the page, as KiCad shows it)
            log += line
            m = re.search(r"pass #(\d+)", line)
            if m:
                pcb._progress(kind="router", line=f"FreeRouting pass {m.group(1)}")
            if time.time() - t0 > timeout:
                pr.kill()
                raise RuntimeError(f"FreeRouting ran past {timeout} s")
        pr.wait()
        if os.path.exists(ses) and "(session" in open(ses, errors="replace").read(4096):
            return log
    raise RuntimeError("FreeRouting wrote no session (3 runs):\n" + log[-3000:])


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


@__import__("functools").lru_cache(maxsize=None)        # (asked once a run: it starts KiCad's Python)
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
    tree = pcb._sx_parse(open(path).read())
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


def _drop_pins(dsn: str, pins) -> str:
    """a Specctra design with ``pins`` ({net: {"REF-PIN", ...}}) out of their nets' pin lists (their pads stay, as
    obstacles): FreeRouting then leaves those branches to the harness"""
    pins = {n: ps for n, ps in pins.items() if ps}
    if not pins:
        return dsn

    def fix(m):
        drop = pins.get(m.group(1).strip('"'))
        if not drop:
            return m.group(0)
        keep = [t for t in m.group(3).split() if t.replace('"', "") not in drop]
        return f"(net {m.group(1)}{m.group(2)}{' '.join(keep)})"
    i, end = dsn.index("(network"), dsn.find("(wiring") % (len(dsn) + 1)
    body = re.sub(r'\(net ("[^"]*"|\S+)(\s*\(pins\s+)([^()]*)\)', fix, dsn[i:end])
    return dsn[:i] + body + dsn[end:]


def _drop_nets(dsn: str, nets) -> str:
    """a Specctra design with ``nets`` out of its network (their pads and wires stay, as obstacles): FreeRouting
    then leaves them alone"""
    i, end = dsn.index("(network"), dsn.find("(wiring") % (len(dsn) + 1)
    out, k = [dsn[:i]], i
    while True:
        m = re.compile(r'\(net ("[^"]*"|\S+)').search(dsn, k, end)
        if not m:
            break
        depth, j = 0, m.start()
        while True:                                      # (the whole (net ...) block, nested pins included)
            depth += {"(": 1, ")": -1}.get(dsn[j], 0)
            j += 1
            if depth == 0:
                break
        out.append(dsn[k:m.start()])
        if m.group(1).strip('"') not in nets:
            out.append(dsn[m.start():j])
        k = j
    rest = dsn[k:]
    for n in nets:                                       # (and out of the net classes' lists)
        rest = re.sub(rf'(\(class [^()]*?)\s{re.escape(n)}(?=[\s)])', r"\1", rest)
    return "".join(out) + rest


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
    txt = _drop_pins(txt, {n: {f"{r}-{num}" for r, num in board.pin_loads(n)} for n in board.nets})
    #                                                    (a branch to a low-current pin is the harness's, at its own
    #                                                     width: FreeRouting routes every net at one width)
    txt = _drop_nets(txt, {p["net"] for p in board.pours})   # (poured nets left to the harness, and the copper
    txt = txt.replace("(type route)", "(type protect)")      #  already drawn kept as it is)
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
    # (KiCad's session import hands the segments back in no fixed order: each is put its lower end first and the
    #  lot sorted, so the same routing gives the same tracks, joined the same way, and the same Gerbers)
    segs = sorted(((n_, L_, list(p_) if tuple(p_[0]) <= tuple(p_[-1]) else list(p_)[::-1], w_)
                   for n_, L_, p_, w_ in segs), key=lambda s_: (s_[0], s_[1], [tuple(q) for q in s_[2]], s_[3]))
    vias = sorted(vias, key=lambda v_: (v_[0], tuple(v_[1]), v_[2], v_[3]))
    t0, v0 = len(board.tracks), len(board.vias)
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"]))) for t in board.tracks}
    have_v = {(v["net"], tuple(round(c, 3) for c in v["at"])) for v in board.vias}
    from shapely.geometry import LineString
    on = {}                                              # (KiCad hands back a stub it already had split into
    for t in board.tracks:                               # pieces: those are not new copper)
        on.setdefault((t["net"], t["layer"], round(t["w"], 3)), []).append(LineString(t["pts"]).buffer(2e-3))
    poured = {p["net"] for p in board.pours}            # (the poured nets are the harness's: nothing of theirs
    for net, L, pts, w in segs:                          #  is taken back from FreeRouting's session)
        if net not in board.nets or net in poured or (net, L, tuple(pts)) in have_t or math.dist(pts[0], pts[-1]) < 1e-3:
            continue
        seg = LineString(pts)
        if any(g.contains(seg) for g in on.get((net, L, round(w, 3)), [])):
            continue
        board.track(net, L, pts, round(w, 4))
    for net, at, drill, dia in vias:
        if net in board.nets and net not in poured and (net, tuple(round(c, 3) for c in at)) not in have_v:
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
    from shapely.geometry import LineString, Polygon
    pn = board.pad_net()
    poured = {p["net"] for p in board.pours}
    kz = {L: [Polygon(z) for z in board.keepout_zones(L)] for L in board.layers}
    low_: Dict[str, dict] = {}
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
            if not net or net not in board.nets:         # (a poured net's pin too: its way out to the pour is
                continue                                  #  kept, not left to whatever FreeRouting leaves free)
            if net not in low_:
                low_[net] = board.pin_loads(net)
            if (ref, num) in low_[net] and net not in poured:   # (a low-current branch: the harness's own router
                continue                                  #  draws it, way out and all, at its width)
            x0_, y0_, x1_, y1_ = g.bounds
            cx, cy = g.centroid.x, g.centroid.y
            dx, dy = cx - ox, cy - oy
            if max(x1_ - x0_, y1_ - y0_) < 1.5 * min(x1_ - x0_, y1_ - y0_):
                continue                                   # (a square pad: an exposed pad or a BGA ball, not a pin)
            reach = short if k % 2 == 0 else long
            if (x1_ - x0_) > (y1_ - y0_):                  # a pin on the left or right side: by the pad's long axis
                #                                            (not by where it lies from the centre: the end pins of
                #                                            a long SSOP row lie further out along the row than
                #                                            across it, and a stub along the row runs into the next)
                ex = x1_ if dx > 0 else x0_
                pts = [(round(cx, 4), round(cy, 4)), (round(ex + (reach if dx > 0 else -reach), 4), round(cy, 4))]
            else:
                ey = y1_ if dy > 0 else y0_
                pts = [(round(cx, 4), round(cy, 4)), (round(cx, 4), round(ey + (reach if dy > 0 else -reach), 4))]
            if any(LineString(pts).buffer(width / 2).intersects(z) for z in kz.get(L, [])):
                continue                                  # (into a keep-out, an antenna's: left to FreeRouting)
            if net in poured:                             # (into its pour, which carries the current: a pin
                board.track(net, L, pts, width)           #  entry, as narrow as the pins allow)
                board.tracks[-1]["neck"] = True
                n += 1
                continue
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
