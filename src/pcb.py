"""Your own PCBs, designed rather than drawn as a green slab: parts on real KiCad footprints, nets, copper, checks.

    from pcb import Board
    b = Board("power", [(x, y), ...], thickness=1.6, copper_oz=2.0)       # outline in board mm (y up), 2 layers
    b.place("U1", "Package_SO:MSOP-10_3x3mm_P0.5mm", (40, 20), rot=90, value="LM5069", mpn="LM5069MM-2/NOPB",
            model="Package_SO.3dshapes/MSOP-10_3x3mm_P0.5mm.step")         # pads come from the .kicad_mod file
    b.net("VBUS", [("Q1", "5"), ("J2", "1"), ...], amps=60)                # every pad a net reaches
    b.track("VBUS", "F", [(x, y), ...], width=3.0)                          # copper: tracks, pours, vias
    b.pour("VBUS", "B", [(x, y), ...]); b.via("VBUS", (x, y))
    problems = b.check()            # connectivity, IPC-2221 width for the current, clearance, voltage drop
    parts = b.parts(z0)             # substrate (with holes), copper per layer, mask; the components' KiCad models
    b.plot(png); b.bom()            # layer plot, bill of materials

Footprints come from KiCad's footprint library (gitlab.com/kicad/libraries/kicad-footprints, CC-BY-SA 4.0 with the
KiCad library exception), fetched once into the parts cache. Board coordinates are mm with y up; KiCad's footprint
files have y down, so pads are flipped on load (the 3D models already match the board frame).

What the checks mean (a first-order design review, not a fab DRC):
  * connectivity: every pad listed in a net is joined to every other through that net's copper (tracks, pours,
    pads, vias / plated through-holes between layers); a pad on no net that copper of a net touches is a short.
  * width: each track (and each pour's narrowest neck between the net's pads) carries the net's current with a
    rise under ``max_rise_c`` by IPC-2221 (external layers: I = 0.048 dT^0.44 A^0.725, A in mil^2).
  * clearance: copper of different nets keeps ``clearance`` mm (IPC-2221 B4 external, < 50 V: 0.13; default 0.2).
  * drop: a track's IR drop at the net's current, against ``max_drop`` of the net's voltage.
  * drills: vias, plated pad holes and mounting holes keep ``HOLE_GAP`` (0.25 mm) wall to wall.

4 layers (``Board(..., layers=4)``): the inner layers "1" and "2" (In1.Cu / In2.Cu) carry planes as pours, carved round
every other net's via and plated hole; tracks stay outside; ``b.drop(net)`` gives each pad of a plane's net a via
beside it; checks run on every layer (inner widths by IPC-2221's internal rule); Gerbers and the KiCad export
(prepreg / core / prepreg stackup) carry all four.
The harness in modules: this one is the board (footprints, parts, nets, copper) and the live run's state; the board's
checks are ``pcb_check`` (inherited), ``pcb_place`` places, ``pcb_route`` routes (and keeps routes), ``pcb_export`` writes Gerbers, STEP and KiCad. Every
function of theirs is reached here too (``pcb.autoplace``, ``from pcb import route``).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


def _kicad_on_path():
    """put kicad-cli on the PATH from KiCad's usual install place when it is not there already (the macOS app and
    the Windows installer do not add it), so the DRC and the viewer's 3D parts work without setup"""
    import glob
    import os
    import shutil
    if shutil.which("kicad-cli"):
        return
    for d in ["/Applications/KiCad/KiCad.app/Contents/MacOS",
              *sorted(glob.glob(r"C:\Program Files\KiCad\*\bin"), reverse=True)]:
        if os.path.exists(os.path.join(d, "kicad-cli")) or os.path.exists(os.path.join(d, "kicad-cli.exe")):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return


_kicad_on_path()

KICAD_LIB_URL = "https://gitlab.com/kicad/libraries/"   # (kicad-footprints, kicad-symbols, kicad-packages3D)
KICAD_LIB_TAGS = {9: "9.0.9.1", 10: "10.0.8"}           # (each KiCad major's newest library release; GitLab keeps
KICAD_LIB_DEFAULT = 9                                    #  them as tags, there is no 9.0 branch. master tracks the
                                                         #  next KiCad, whose new tokens the installed one refuses)


PAD_RENAMED = {"SH": "S1", "S1": "SH"}                  # (pad numbers KiCad's library changed between releases: a
                                                         #  USB-C shield is S1 in KiCad 9's, SH in KiCad 10's; a
                                                         #  script written against either finds its pad in both)


def kicad_lib_ref() -> str:
    """the KiCad library release footprints, symbols and 3D models are read from: the one of the installed
    ``kicad-cli``'s major (KiCad 9 -> "9.0.9.1"), KiCad 9's when there is no KiCad (the format the export writes),
    master for a KiCad newer than any listed; ``$PROMETHEUS_KICAD_LIB`` names another tag or branch"""
    import os
    env = os.environ.get("PROMETHEUS_KICAD_LIB", "").strip()
    if env:
        return env
    return _kicad_lib_ref_installed()


@__import__("functools").lru_cache(maxsize=None)        # (kicad-cli takes 0.2 s to answer: asked once a run)
def _kicad_lib_ref_installed() -> str:
    m = re.match(r"(\d+)\.", kicad_version() or "")
    major = int(m.group(1)) if m else KICAD_LIB_DEFAULT
    if major in KICAD_LIB_TAGS:
        return KICAD_LIB_TAGS[major]
    return "master" if major > max(KICAD_LIB_TAGS) else KICAD_LIB_TAGS[KICAD_LIB_DEFAULT]


def kicad_lib_get(repo: str, path: str, timeout: float = 60) -> Tuple[bytes, str]:
    """a file of KiCad's library ``repo`` ("kicad-footprints", ...) at ``kicad_lib_ref()``, else at master when that
    release lacks it (a part added since): (its bytes, the ref it came from). A 404 at both raises urllib's
    HTTPError; GitLab's 429 to a burst is waited out"""
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    ref = kicad_lib_ref()
    for r in dict.fromkeys([ref, "master"]):
        url = f"{KICAD_LIB_URL}{repo}/-/raw/{urllib.parse.quote(r)}/{urllib.parse.quote(path)}"
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as f:
                    return f.read(), r
            except urllib.error.HTTPError as e:
                if e.code == 404 and r != "master":
                    break                                # (not in this release: master's)
                if e.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                time.sleep(float(e.headers.get("Retry-After") or 0) or 1.5 * 2 ** attempt)
    raise AssertionError("unreachable")


def kicad_lib_cache(kind: str, name: str) -> Path:
    """where a file of KiCad's library is cached: ``<parts cache>/<kind>/<ref>/<name>``, the release's own, else
    master's when the release lacked it and master's was fetched (a cache of one release is never read as another)"""
    from vendor_parts import CACHE
    own = CACHE / kind / kicad_lib_ref() / name
    if not own.exists():
        fb = CACHE / kind / "master" / name
        if fb.exists():
            return fb
    return own


LOCAL_FP = Path(__file__).parent / "pcb_footprints"   # project footprints KiCad's library lacks: <Lib>.pretty/<Name>.kicad_mod
CU_RHO = 1.72e-5            # ohm mm (copper, 20 C)
ROUTER_VERSION = 9          # in route_jobs' checkpoint key: bump when the router changes what it would produce
HOLE_CLEAR = 0.25           # copper to a bare (non-plated) hole's wall, mm: KiCad's default hole clearance
HOLE_GAP = 0.25             # mm between drill walls (a common fab minimum)
EDGE = 0.25                 # mm from copper (tracks, vias) to the board edge
MIN_FILL = 0.1              # mm: a pour's narrowest copper (KiCad's zone min_thickness)
OZ_MM = 0.0347              # 1 oz/ft^2 copper thickness
_FP: Dict[str, Dict] = {}


from pcb_check import CheckMixin   # (the design check: Board's check, check_placement, islands, ...)

# ---- footprints ------------------------------------------------------------------------------------------------------
def _find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


FP_DIRS: List[Path] = [LOCAL_FP]                         # (project footprint folders, searched in order; a board
                                                         #  script's own footprints/ goes first: pcb_cli.load)


def _fp_file(name: str) -> Path:
    """where footprint "Lib:Name" lives: a project library (``FP_DIRS``: the board's footprints/, then the
    harness's own) first, else KiCad's, cached"""
    lib, fp = name.split(":")
    for d in FP_DIRS:
        local = Path(d) / f"{lib}.pretty" / f"{fp}.kicad_mod"
        if local.exists():
            return local
    return kicad_lib_cache("kicad_fp", f"{fp}.kicad_mod")


class FootprintNotFound(ValueError):
    """a footprint name KiCad's library does not have; ``suggestions`` the closest names it does"""

    def __init__(self, name: str, suggestions: Sequence[str] = ()):
        self.name, self.suggestions = name, list(suggestions)
        lib = name.split(":")[0]
        hint = f"; did you mean: {', '.join(lib + ':' + s_ for s_ in self.suggestions)}" if self.suggestions else ""
        super().__init__(f"footprint {name} is not in KiCad's library{hint} (the library: "
                         f"{KICAD_LIB_URL}kicad-footprints/-/tree/{kicad_lib_ref()}/{lib}.pretty)")


def _close_footprints(lib: str, fp: str, n: int = 5) -> List[str]:
    """the names in KiCad's footprint library ``lib`` closest to ``fp`` (its file list, fetched once and cached)"""
    import difflib
    import json
    import urllib.parse
    import urllib.request
    from vendor_parts import CACHE
    ref = kicad_lib_ref()
    cache = CACHE / "kicad_fp_lists" / ref / f"{lib}.json"
    names = None
    if cache.exists():
        names = json.loads(cache.read_text())
    else:
        try:
            names, page = [], 1
            while page < 30:
                q = urllib.parse.urlencode({"path": f"{lib}.pretty", "ref": ref, "per_page": 100, "page": page})
                url = f"https://gitlab.com/api/v4/projects/kicad%2Flibraries%2Fkicad-footprints/repository/tree?{q}"
                with urllib.request.urlopen(url, timeout=30) as r:
                    got = json.loads(r.read())
                names += [g["name"][:-10] for g in got if g["name"].endswith(".kicad_mod")]
                if len(got) < 100:
                    break
                page += 1
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(names))
        except Exception:                                # (offline: no suggestions, the error still names it)
            return []
    if not names:
        return []
    low = {n_.lower(): n_ for n_ in names}
    hits = difflib.get_close_matches(fp.lower(), list(low), n=n, cutoff=0.5)
    key = re.split(r"[_\-]", fp)                         # (and the names sharing its maker / series word)
    more = [n_ for n_ in names if any(k and len(k) > 3 and k.lower() in n_.lower() for k in key[1:3])]
    out = list(dict.fromkeys([low[h] for h in hits] + more))
    return out[:n]


def _download_fp(name: str) -> Path:
    """fetch footprint "Lib:Name" from KiCad's library (``kicad_lib_ref()``'s release, else master) into the cache
    (the project library needs no fetch)"""
    lib, fp = name.split(":")
    dst = _fp_file(name)
    if not dst.exists() or dst.stat().st_size < 200:
        import urllib.error
        try:
            data, ref = kicad_lib_get("kicad-footprints", f"{lib}.pretty/{fp}.kicad_mod")
        except urllib.error.HTTPError as e:
            if e.code == 404:                            # (a name the library does not have: say so, with the
                raise FootprintNotFound(name, _close_footprints(lib, fp)) from None   # nearest it does)
            raise
        if not data.lstrip().startswith(b"(footprint") and not data.lstrip().startswith(b"(module"):
            raise FileNotFoundError(f"no KiCad footprint {name}")
        from vendor_parts import CACHE
        dst = CACHE / "kicad_fp" / ref / f"{fp}.kicad_mod"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    return dst


def prefetch(names: Sequence[str], workers: int = 8) -> List[str]:
    """download every footprint in ``names`` not yet cached, in parallel (a cold board otherwise waits on one
    download per footprint, a few seconds each). Returns the names that could not be fetched."""
    from concurrent.futures import ThreadPoolExecutor
    todo = sorted({n for n in names if n not in _FP})
    bad = []

    def one(n):
        try:
            _download_fp(n)
        except Exception:
            bad.append(n)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo) or 1))) as ex:
        list(ex.map(one, todo))
    return bad


_SCANNED: set = set()
_OWN_FILES = {"pcb.py", "pcb_route.py", "pcb_place.py", "pcb_export.py",
              "pcb_check.py"}   # (the harness's own, not a board's)
_FP_NAME = re.compile(r'["\']([A-Za-z0-9_.\-]+:[A-Za-z0-9_.,+\-]+)["\']')


def _prefetch_callers():
    """on the first footprint the cache lacks, fetch every footprint named in the running program's source files
    (the main script and the modules it imported from beside it) in parallel, so a cold board waits once, not once
    per footprint"""
    import sys
    files = []
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None) or ""
        if f.endswith(".py") and "site-packages" not in f and f not in _SCANNED and Path(f).name not in _OWN_FILES:
            files.append(f)
    names = set()
    for f in files:
        _SCANNED.add(f)
        try:
            names |= {n for n in _FP_NAME.findall(Path(f).read_text(errors="ignore")) if not n.startswith(("http", "C:"))
                      and n.split(":")[0][:1].isupper()}
        except OSError:
            pass
    if names:
        prefetch(sorted(names))



def _arc_points(p0, pm, p1, step: float = 0.1):
    """an arc through three points as a polyline every ``step`` mm (a round courtyard drawn as arcs: its three
    points alone make a diamond of 64 % of the circle)"""
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
    return [p0] + [(ux + r * math.cos(a0 + sweep * k / n), uy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n)] + [p1]


def _courtyard_poly(tree):
    """a footprint's courtyard as KiCad draws it (F.CrtYd lines, rectangles, polygons, arcs and circles closed into
    one shape, footprint frame, y up), or None when its strokes close nothing (the box of them is used then)"""
    from shapely.geometry import LineString, Point, Polygon, box
    from shapely.ops import polygonize, unary_union
    on = lambda ln: any(isinstance(c, list) and c[0] == "layer" and "CrtYd" in str(c[1]) for c in ln)
    xy = lambda q: (float(q[1]), -float(q[2]))
    lines, areas = [], []
    for ln in _find(tree, "fp_line"):
        if on(ln) and _find(ln, "start") and _find(ln, "end"):
            lines.append(LineString([xy(_find(ln, "start")[0]), xy(_find(ln, "end")[0])]))
    for ln in _find(tree, "fp_arc"):
        if on(ln) and _find(ln, "start") and _find(ln, "mid") and _find(ln, "end"):
            lines.append(LineString(_arc_points(*[xy(_find(ln, k)[0]) for k in ("start", "mid", "end")])))
    for ln in _find(tree, "fp_rect"):
        if on(ln) and _find(ln, "start") and _find(ln, "end"):
            (x0, y0), (x1, y1) = xy(_find(ln, "start")[0]), xy(_find(ln, "end")[0])
            areas.append(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    for ln in _find(tree, "fp_poly"):
        if on(ln) and _find(ln, "pts"):
            pts = [xy(q) for q in _find(_find(ln, "pts")[0], "xy")]
            if len(pts) >= 3:
                areas.append(Polygon(pts).buffer(0))
    for ln in _find(tree, "fp_circle"):
        if on(ln) and _find(ln, "center") and _find(ln, "end"):
            c_, e_ = xy(_find(ln, "center")[0]), xy(_find(ln, "end")[0])
            areas.append(Point(c_).buffer(math.dist(c_, e_), 32))
    if lines:
        areas += list(polygonize(unary_union(lines)))
    g = unary_union(areas) if areas else None
    if g is None or g.is_empty or g.area < 1e-6:
        return None
    if g.geom_type == "MultiPolygon":                    # (pieces: their outer shape, holes filled)
        g = unary_union([Polygon(p.exterior) for p in g.geoms])
    elif g.geom_type == "Polygon":
        g = Polygon(g.exterior)
    return g

def footprint(name: str) -> Dict:
    """KiCad footprint "Lib:Name" -> {"pads": {number: [pad, ...]}, "courtyard": (x0, y0, x1, y1)}; a pad is
    {"at": (x, y) board frame (y up), "size": (w, h), "rot": deg, "shape": rect|roundrect|circle|oval|...,
    "drill": dia or None, "layers": "F" | "B" | "FB"}. Fetched once into the parts cache."""
    if name in _FP:
        return _FP[name]
    lib, fp = name.split(":")
    f0 = _fp_file(name)
    if not f0.exists() or f0.stat().st_size < 200:
        _prefetch_callers()
    dst = _download_fp(name)
    tree = _sx_parse(dst.read_text())
    pads: Dict[str, List[Dict]] = {}
    for p in _find(tree, "pad"):
        num, kind, shape = p[1], p[2], p[3]
        at = _find(p, "at")[0]
        x, y = float(at[1]), -float(at[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        size = _find(p, "size")[0]
        dr = _find(p, "drill")
        drill = None
        if dr:
            vals = [v for v in dr[0][1:] if not isinstance(v, list) and v != "oval"]
            drill = float(vals[0]) if vals else None
            slot = (float(vals[0]), float(vals[1])) if "oval" in dr[0] and len(vals) > 1 else None
        else:
            slot = None
        lay = " ".join(str(v) for v in (_find(p, "layers")[0][1:] if _find(p, "layers") else []))
        side = ("F" if ("F.Cu" in lay or "*.Cu" in lay) else "") + ("B" if ("B.Cu" in lay or "*.Cu" in lay) else "")
        if kind == "np_thru_hole":                           # (paste-only apertures and bare holes carry no copper)
            side = ""
        prims = []                                           # a custom pad's primitives (QFN corner pins, LFPAK tabs)
        if shape == "custom":
            for pr in _find(p, "primitives"):
                for gp in _find(pr, "gr_poly"):
                    pts = [(float(q[1]), -float(q[2])) for q in _find(_find(gp, "pts")[0], "xy")]
                    wd = _find(gp, "width")
                    if not wd and _find(gp, "stroke"):
                        wd = _find(_find(gp, "stroke")[0], "width")
                    prims.append((pts, float(wd[0][1]) if wd else 0.0))
                for gc in _find(pr, "gr_circle"):                # (a solder jumper's rounded ends)
                    c_, e_ = _find(gc, "center")[0], _find(gc, "end")[0]
                    cx, cy = float(c_[1]), -float(c_[2])
                    r_ = math.hypot(float(e_[1]) - cx, -float(e_[2]) - cy)
                    prims.append(([(cx + r_ * math.cos(k * math.pi / 16), cy + r_ * math.sin(k * math.pi / 16))
                                   for k in range(32)], 0.0))
                for gr in _find(pr, "gr_rect"):
                    s_, e_ = _find(gr, "start")[0], _find(gr, "end")[0]
                    x0_, y0_, x1_, y1_ = float(s_[1]), -float(s_[2]), float(e_[1]), -float(e_[2])
                    prims.append(([(x0_, y0_), (x1_, y0_), (x1_, y1_), (x0_, y1_)], 0.0))
        rr = _find(p, "roundrect_rratio")
        anchor = _find(_find(p, "options")[0], "anchor") if _find(p, "options") else []
        pads.setdefault(num, []).append({"at": (x, y), "size": (float(size[1]), float(size[2])), "rot": rot,
                                         "shape": shape, "drill": drill, "slot": slot, "layers": side, "kind": kind,
                                         "prims": prims, "anchor": str(anchor[0][1]) if anchor else "rect",
                                         "rratio": float(rr[0][1]) if rr else 0.25})
    xs, ys = [], []
    for ln in _find(tree, "fp_line") + _find(tree, "fp_rect"):
        if any(isinstance(c, list) and c[0] == "layer" and "CrtYd" in str(c[1]) for c in ln):
            for key in ("start", "end"):
                s = _find(ln, key)
                if s:
                    xs.append(float(s[0][1]))
                    ys.append(-float(s[0][2]))
    for ln in _find(tree, "fp_circle") + _find(tree, "fp_poly") + _find(tree, "fp_arc"):   # (a big radial cap's
        if not any(isinstance(c, list) and c[0] == "layer" and "CrtYd" in str(c[1]) for c in ln):   # is a circle)
            continue
        if ln[0] == "fp_circle":
            cx_, cy_ = float(_find(ln, "center")[0][1]), -float(_find(ln, "center")[0][2])
            e = _find(ln, "end")[0]
            r_ = math.hypot(float(e[1]) - cx_, -float(e[2]) - cy_)
            xs += [cx_ - r_, cx_ + r_]
            ys += [cy_ - r_, cy_ + r_]
        else:
            pts_ = [q for key in ("start", "mid", "end") for q in _find(ln, key)] + \
                   (_find(_find(ln, "pts")[0], "xy") if _find(ln, "pts") else [])
            xs += [float(q[1]) for q in pts_]
            ys += [-float(q[2]) for q in pts_]
    crt = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    edge_marks = [[(float(st[0][1]), -float(st[0][2])), (float(en[0][1]), -float(en[0][2]))]   # (Edge.Cuts lines
                  for ln in _find(tree, "fp_line")                                         #  of its own: where the
                  if any(isinstance(c, list) and c[0] == "layer" and str(c[1]) == "Edge.Cuts" for c in ln)   # board
                  for st, en in [(_find(ln, "start"), _find(ln, "end"))] if st and en]     #  edge should run)
    crt_poly = _courtyard_poly(tree) if crt else None   # (its real shape, as KiCad's DRC judges overlaps: an SOIC's
    #                                                     is narrower over the body than across its pins)
    # silkscreen, for renders: the reference designator's place and the outline strokes on F.SilkS
    ref = None
    for t in _find(tree, "fp_text") + _find(tree, "property"):
        if len(t) > 2 and t[1] in ("reference", "Reference") and _find(t, "at"):
            a = _find(t, "at")[0]
            sz = [_find(f, "size") for f in _find(_find(t, "effects")[0], "font")] if _find(t, "effects") else []
            ref = (float(a[1]), -float(a[2]), float(a[3]) if len(a) > 3 else 0.0,
                   float(sz[0][0][1]) if sz and sz[0] else 1.0)
            break
    silk = []
    for ln in _find(tree, "fp_line") + _find(tree, "fp_rect"):
        if not any(isinstance(c, list) and c[0] == "layer" and c[1] == "F.SilkS" for c in ln):
            continue
        st_, en_ = _find(ln, "start"), _find(ln, "end")
        w = _find(ln, "stroke")
        w = float(_find(w[0], "width")[0][1]) if w and _find(w[0], "width") else (float(_find(ln, "width")[0][1]) if _find(ln, "width") else 0.12)
        if not (st_ and en_):
            continue
        x1, y1, x2, y2 = float(st_[0][1]), -float(st_[0][2]), float(en_[0][1]), -float(en_[0][2])
        if ln[0] == "fp_rect":
            silk += [(x1, y1, x2, y1, w), (x2, y1, x2, y2, w), (x2, y2, x1, y2, w), (x1, y2, x1, y1, w)]
        else:
            silk.append((x1, y1, x2, y2, w))
    mod = _find(tree, "model")                          # the 3D model KiCad's footprint names (its .step, by the
    model = None                                         # library path vendor_parts.kicad() fetches)
    if mod and isinstance(mod[0][1], str):
        m_ = re.sub(r"^\$\{[^}]*\}/", "", mod[0][1])
        model = re.sub(r"\.wrl$", ".step", m_) if "3dshapes/" in m_ else None
    _FP[name] = {"pads": pads, "courtyard": crt, "courtyard_poly": crt_poly, "edge_marks": edge_marks, "ref": ref,
                 "silk": silk, "model": model}
    return _FP[name]


def _pad_poly(pad: Dict, place: Tuple[float, float, float]):
    """a pad's copper outline (shapely) in board coordinates"""
    from shapely.geometry import Point, box
    from shapely import affinity
    w, h = pad["size"]
    if pad.get("prims"):                                     # custom: the anchor plus its polygons (pad frame)
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        anc = Point(0, 0).buffer(w / 2, 16) if pad.get("anchor") == "circle" else box(-w / 2, -h / 2, w / 2, h / 2)
        parts_ = [anc] + [Polygon(pts).buffer(wd / 2, 8) if wd > 0 else Polygon(pts) for pts, wd in pad["prims"]
                          if len(pts) >= 3]
        g = unary_union(parts_)
        if g.geom_type != "Polygon":
            g = g.convex_hull if g.geom_type != "MultiPolygon" else max(g.geoms, key=lambda q: q.area).union(g).convex_hull
    elif pad["shape"] == "circle":
        g = Point(0, 0).buffer(w / 2, 24)
    elif pad["shape"] == "oval":
        r = min(w, h) / 2
        g = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, 16) if abs(w - h) > 1e-6 else Point(0, 0).buffer(r, 24)
    elif pad["shape"] == "roundrect":                        # (corners rounded by rratio x the short side)
        r = min(pad.get("rratio", 0.25), 0.5) * min(w, h)
        g = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, 8) if r > 1e-6 else box(-w / 2, -h / 2, w / 2, h / 2)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    if pad.get("mirror"):                                    # a bottom-side part's pad: its shape flipped too
        g = affinity.scale(g, -1.0, 1.0, origin=(0, 0))
    g = affinity.rotate(g, -pad["rot"], origin=(0, 0))          # KiCad pad rotation is clockwise in y-down = ccw here
    g = affinity.translate(g, *pad["at"])
    px, py, prot = place
    g = affinity.rotate(g, prot, origin=(0, 0))
    return affinity.translate(g, px, py)


def microstrip_z0(width: float, height: float, er: float = 4.5, t: float = 0.035, gap: Optional[float] = None) -> float:
    """characteristic impedance (ohm) of an outer-layer track over a plane (IPC-2141 microstrip): ``width`` the
    track, ``height`` the dielectric to the plane below (1.6 mm 2-layer: ~1.5 mm; JLCPCB 4-layer 1.6 mm: ~0.2 mm to
    In1), ``er`` FR4 ~4.5 (JLCPCB's 7628 prepreg: 4.4), ``t`` copper thickness (1 oz = 0.035 mm), mm. With ``gap``
    (edge to edge to its partner): the differential impedance of the pair. A first-order figure, valid for
    0.1 < w/h < 2.0 and 1 < er < 15; the house's own calculator decides the order"""
    z0 = 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * height / (0.8 * width + t))
    return 2.0 * z0 * (1.0 - 0.48 * math.exp(-0.96 * gap / height)) if gap is not None else z0


def width_for_z0(z0: float, height: float, er: float = 4.5, t: float = 0.035, gap: Optional[float] = None) -> float:
    """the track width (mm) giving impedance ``z0`` (single-ended, or differential with ``gap``): e.g. USB's 90 ohm
    pair, ``width_for_z0(90, 0.2, gap=0.15)`` on a 4-layer board. Bisects ``microstrip_z0``"""
    lo, hi = 0.01, 20.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if microstrip_z0(mid, height, er, t, gap) > z0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def ipc2221_width(amps: float, oz: float, rise_c: float = 20.0, external: bool = True) -> float:
    """track width (mm) carrying ``amps`` with a ``rise_c`` temperature rise on ``oz`` copper (IPC-2221)"""
    k = 0.048 if external else 0.024
    area_mil2 = (amps / (k * rise_c ** 0.44)) ** (1 / 0.725)
    return area_mil2 / (oz * 1.378) * 0.0254


# ---- the board -------------------------------------------------------------------------------------------------------
class Board(CheckMixin):
    hole_gap = HOLE_GAP                                  # drill wall to drill wall, mm
    hole_clear = HOLE_CLEAR                              # copper to a bare hole's wall, mm
    edge = EDGE                                          # copper to the board edge, mm
    CAP_BRANCH_AMPS = 0.5    # A: a small ceramic capacitor's branch off a net carrying more (its decoupling tie),
    #                          unless ``net(..., loads=)`` says otherwise; None: every pin takes the net's current

    def __init__(self, name: str, outline: Sequence[Tuple[float, float]], thickness: float = 1.6, copper_oz: float = 1.0,
                 clearance: float = 0.2, max_rise_c: float = 20.0, max_drop: float = 0.02, corner_r: float = 3.0,
                 layers: int = 2, inner_oz: float = 1.0, rules=None):
        """``layers`` 2 or 4: a 4-layer board has two inner layers ("1", "2": KiCad's In1.Cu / In2.Cu) for planes
        (pours); tracks stay on the outer layers, vias and plated holes reach every layer. ``rules``: a fab preset
        (``fab.RULES`` name, e.g. "jlcpcb-2", or a dict like one) the router and the check then keep to: its
        clearance (when larger), drill gap and copper-to-edge gap"""
        from shapely.geometry import Polygon
        self.name, self.t, self.oz, self.clear = name, thickness, copper_oz, clearance
        self.rules = None
        if rules is not None:
            import fab
            r = fab.RULES[rules] if isinstance(rules, str) else dict(rules)
            self.rules = rules
            self.clear = max(clearance, r.get("space", 0.0))
            self.hole_gap = max(HOLE_GAP, r.get("hole_gap", HOLE_GAP))
            self.edge = max(EDGE, r.get("edge", EDGE))
        if layers not in (2, 4):
            raise ValueError("layers: 2 or 4")
        self.layers = ("F", "B") if layers == 2 else ("F", "1", "2", "B")
        self.inner_oz = inner_oz
        self.max_rise, self.max_drop = max_rise_c, max_drop
        self.outline = Polygon(outline).buffer(-corner_r, join_style=2).buffer(corner_r, 16) if corner_r else Polygon(outline)
        self.comps: Dict[str, Dict] = {}
        self.nets: Dict[str, Dict] = {}
        self.tracks: List[Dict] = []
        self.pours: List[Dict] = []
        self.vias: List[Dict] = []
        self.holes: List[Tuple[float, float, float]] = []             # mounting holes (x, y, d)

    def oz_of(self, layer: str) -> float:
        return self.oz if layer in ("F", "B") else self.inner_oz

    def width_for(self, amps: float, layer: str) -> float:
        """IPC-2221 width for ``amps`` on ``layer`` (inner layers cool worse: k = 0.024)"""
        return ipc2221_width(amps, self.oz_of(layer), self.max_rise, external=layer in ("F", "B"))

    # declarations
    def place(self, ref: str, fp: str, at: Optional[Tuple[float, float]] = None, rot: float = 0.0, value: str = "", mpn: str = "",
              model: Optional[str] = None, model_offset=(0.0, 0.0, 0.0), body=None, note: str = "",
              overhang: bool = False, side: str = "F", lcsc: str = "", min_drill: Optional[float] = None,
              symbol: Optional[str] = None, label=None, cite=None):
        """a part: footprint "Lib:Name", its origin at ``at`` turned ``rot`` deg ccw (seen from above); ``model`` a
        KiCad 3D model path (vendor_parts.kicad; default: the one the footprint names) or ``body`` a build123d shape in
        the footprint frame for one KiCad lacks. ``side="B"`` puts it on the bottom: the footprint is flipped about its y axis (KiCad's flip), its SMD
        pads go to B.Cu, its model hangs under the board. ``lcsc``: the LCSC / JLCPCB part number (C-number) for
        assembly and the cart (``fab``). ``min_drill``: the thermal vias in an exposed pad (a plated hole whose pad
        number also has SMD copper) drilled smaller than this are enlarged to it, keeping their copper ring (default:
        the board's fab preset's plated-drill minimum; a part's pin holes are never changed: its leads must fit).
        ``symbol``: its KiCad symbol "Lib:Name" (``parts.symbol``): nets may then name its pins by name, the check
        matches every pin to a pad and the critic runs an ERC on its pin types; with ``fp=None`` the symbol's own
        footprint is used. ``label``: its reference designator's place on the silkscreen, (x, y, angle, size) board mm,
        or False to leave it off (default: placed clear of everything, ``place_refs``). ``cite``: the datasheet's word
        for the choice, {"page", "quote", "what"?, "value"?, "url"?} or a list of them (``cite``)"""
        sym = None
        if symbol:
            import parts
            sym = parts.symbol(symbol)
            fp = fp or sym["footprint"]
            if not fp:
                raise ValueError(f"{ref}: symbol {symbol} names no footprint: give one")
            if not value:
                value = sym["value"]
        pads = footprint(fp)["pads"]
        if sym is None:                                  # (no symbol given: KiCad's generic one for a passive or a
            try:                                         #  connector - Device:R, Device:C ... - when its pins are the
                import kicad_sch                         #  pads exactly; a chip needs its own: symbol=)
                import parts
                import pcb_view
                if pcb_view.kind_of(ref, fp) == "ic":
                    raise LookupError("a chip: no generic symbol")
                name = kicad_sch.symbol_for(ref, {"fp": fp, "pads": pads, "value": value, "mpn": mpn, "note": note})
                cand = parts.symbol(name)
                if {q["num"] for q in cand["pins"]} == {n for n in pads if n}:
                    sym = dict(cand, auto=True)
            except Exception:                            # (offline, or no such symbol: the part goes without one)
                sym = None
        if min_drill is None and self.rules is not None:
            import fab
            min_drill = (fab.RULES[self.rules] if isinstance(self.rules, str) else self.rules).get("pth_drill")
        if min_drill:
            def _thermal(lst):
                if not any(p["kind"] == "smd" for p in lst):
                    return lst
                out = []
                for p in lst:
                    if p["drill"] and not p["slot"] and p["drill"] < min_drill - 1e-9:
                        ring = max((min(p["size"]) - p["drill"]) / 2, 0.1)
                        sz = max(min(p["size"]), min_drill + 2 * ring)
                        p = {**p, "drill": min_drill, "size": (max(p["size"][0], sz), max(p["size"][1], sz))}
                    out.append(p)
                return out
            pads = {n: _thermal(lst) for n, lst in pads.items()}
        if side == "B":
            sw = {"F": "B", "B": "F", "FB": "FB", "": ""}
            pads = {n: [{**p, "at": (-p["at"][0], p["at"][1]), "rot": -p["rot"], "layers": sw.get(p["layers"], p["layers"]),
                         "mirror": True}
                        for p in lst] for n, lst in pads.items()}
        if model is None and body is None:               # the footprint's own 3D model, as KiCad would show it
            model = footprint(fp).get("model")
        self.comps[ref] = {"fp": fp, "at": tuple(at) if at is not None else (0.0, 0.0), "rot": rot, "value": value, "mpn": mpn, "model": model,
                           "model_offset": model_offset, "body": body, "note": note, "overhang": overhang,
                           "pads": pads, "side": side, "lcsc": lcsc,
                           "pads_key": f"{min_drill}" if min_drill else ""}    # (the pads differ from the footprint's)
        if at is None:                                   # (autoplace() puts it somewhere)
            self.comps[ref].update(at=(0.0, 0.0), unplaced=True)
        if label is not None:
            self.comps[ref]["label"] = tuple(label) if label else None
        if sym:
            self.comps[ref]["symbol"] = sym
        for c_ in ([cite] if isinstance(cite, dict) else cite or []):
            self.cite(**{"ref": ref, "what": "part", "value": value or mpn or fp, **c_})

    def courtyard(self, ref: str):
        """a part's courtyard outline in board coordinates (flipped for a bottom-side part), or None"""
        from shapely import affinity
        from shapely.geometry import box
        c = self.comps[ref]
        fp = footprint(c["fp"])
        crt = fp["courtyard"]
        if not crt:
            return None
        g = fp.get("courtyard_poly")
        if g is None:
            g = box(*crt)
        if c.get("side", "F") == "B":
            g = affinity.scale(g, -1.0, 1.0, origin=(0, 0))
        return affinity.translate(affinity.rotate(g, c["rot"], origin=(0, 0)), *c["at"])

    def net(self, name: str, pins: Sequence[Tuple[str, str]], amps: float = 0.1, volts: float = 5.0,
            width: Optional[float] = None, cite=None, loads=None):
        """a net: its pins (ref, pad number), the current it carries (sets how wide its copper must be) and its
        voltage (what its drop is measured against); ``width``: its track width when routed (default: as wide as
        its current needs, at least 0.2 mm). ``cite``: as ``place``'s, its ref the first pin's part unless given.
        ``loads``: {(ref, pin): amps} the most a pin draws when that is less than the net's ``amps`` (a sense pin,
        a decoupling capacitor, a chip's supply off a motor rail): its branch is routed and checked for that, the
        trunk between the other pins for the whole ``amps`` (``branch_amps``). ``None`` for a pin takes it back to
        the full current. A small ceramic capacitor's pin (ref C.., two pads, footprint C_0603_... or smaller) is a branch of
        ``Board.CAP_BRANCH_AMPS`` (0.5 A) by default; ``Board.CAP_BRANCH_AMPS = None`` turns that off"""
        for c_ in ([cite] if isinstance(cite, dict) else cite or []):
            self.cite(**{"ref": pins[0][0], "what": f"net {name}", "value": name, **c_})
        out = []
        for r, p in pins:                                # (a pin by name, through its part's symbol: "AIN2" -> 15)
            p, c = str(p), self.comps.get(r)
            if c is not None and p not in c["pads"] and PAD_RENAMED.get(p) in c["pads"]:
                p = PAD_RENAMED[p]                       # (a pad KiCad's libraries renamed between releases)
            if c is not None and p not in c["pads"] and c.get("symbol"):
                import parts
                nums = parts.resolve(c, p)
                out += [(r, n) for n in nums] if nums else [(r, p)]
            else:
                out.append((r, p))
        self.nets[name] = {"pins": list(dict.fromkeys(out)), "amps": amps, "volts": volts}
        if width is not None:
            self.nets[name]["width"] = width
        if loads:
            got = []
            for (r, p), a in dict(loads).items():
                p, c = str(p), self.comps.get(r)
                nums = None
                if c is not None and p not in c["pads"] and c.get("symbol"):   # (a pin by name, as in ``pins``)
                    import parts
                    nums = parts.resolve(c, p)
                for n_ in (nums or [p]):
                    if (r, n_) not in self.nets[name]["pins"]:
                        raise ValueError(f"net {name}: loads names {r}.{p}, which is not one of its pins")
                    got.append([r, n_, None if a is None else float(a)])
            self.nets[name]["loads"] = got            # (a list, not a dict on tuples: the route cache key is JSON)

    def cite(self, ref: str, what: str, value, page: int, quote: str, url: Optional[str] = None):
        """the datasheet's word for a choice made on the board (a pin, a capacitor's value, a pull-up): ``page``
        (1-based) and ``quote`` (its own words there) of ``url`` (default: ``ref``'s symbol's datasheet). Collected in
        result.json ("evidence"), checked against the datasheet's text, and opened at that page by the viewer"""
        if not (isinstance(page, int) and page >= 1 and str(quote).strip()):
            raise ValueError(f"cite {ref} {what}: a page (1-based) and the datasheet's own words are needed")
        self.__dict__.setdefault("evidence", []).append(
            {"ref": ref, "what": what, "value": str(value), "url": url, "page": page, "quote": " ".join(str(quote).split())})

    def skip(self, what: str, why: str, how: str = ""):
        """an output or a step left out of this run (no network, an extra not installed): said in the run's summary,
        its result.json ("skipped") and the viewer, never quietly, and the rest of the run goes on"""
        self.__dict__.setdefault("skipped", []).append({"what": what, "why": why, "how": how})

    def nc(self, ref: str, *pins: str):
        """pins left unconnected on purpose (an unused input, a debug pin), by number or by symbol name: the ERC no
        longer asks for them, as KiCad's no-connect flag does"""
        import parts
        c = self.comps[ref]
        c.setdefault("nc", set()).update(n for p in pins for n in (parts.resolve(c, str(p)) or [str(p)]))

    def erc_allow(self, net: str, why: str):
        """an ERC conflict the design has on purpose on ``net`` (two tri-state outputs on one SPI MISO line, each
        enabled by its own chip select): not reported, and ``why`` kept with the board, as KiCad's ERC exclusion"""
        self.__dict__.setdefault("erc_allowed", {})[net] = why

    def net_width(self, name: str) -> float:
        """the width ``name`` is routed at: its own, else what its current needs on the outer layers (min 0.2 mm)"""
        d = self.nets[name]
        return d.get("width") or max(0.2, round(self.width_for(d["amps"], "F") + 0.005, 2))

    def pin_loads(self, net: str) -> Dict[Tuple[str, str], float]:
        """{pin: amps} for the pins of ``net`` that draw less than its current (``net(..., loads=)``, and a small
        ceramic capacitor's ``CAP_BRANCH_AMPS`` by default): each is fed by a branch sized for that, not for the
        whole net. Every other pin may carry the net's full current"""
        d = self.nets.get(net) or {}
        A = d.get("amps") or 0.0
        given = {(r, n_): a for r, n_, a in d.get("loads", [])}
        out: Dict[Tuple[str, str], float] = {}
        cb = self.CAP_BRANCH_AMPS
        if cb is not None and cb < A:
            for pin in d.get("pins", []):
                pin = tuple(pin)
                c = self.comps.get(pin[0])
                if pin not in given and c is not None and re.match(r"C\d", pin[0].upper()) and \
                        len([k for k in c["pads"] if k]) == 2 and \
                        re.match(r"C_(01005|0201|0402|0603)_", c["fp"].split(":")[-1]):
                    out[pin] = cb                        # (a 0603 or smaller ceramic chip: a decoupling capacitor;
                    #                                      a bulk or a regulator's input capacitor, 0805 and up or an
                    #                                      electrolytic, may carry ripple of amps: not assumed small)
        on = {tuple(p) for p in d.get("pins", [])}
        for pin, a in given.items():
            if a is not None and a < A and pin in on:
                out[pin] = a
        return out

    def pin_width(self, net: str, pin: Tuple[str, str]) -> float:
        """the width a branch to ``pin`` is routed at: what its load (``pin_loads``) needs, at least 0.2 mm and at
        most the net's width; the net's width for a pin of the full current"""
        a = self.pin_loads(net).get(tuple(pin))
        w = self.net_width(net)
        return w if a is None else min(w, max(0.2, round(self.width_for(a, "F") + 0.005, 2)))

    def branch_amps(self, net: str, pads=None, pn=None, cu=None) -> Dict[int, float]:
        """the most current each track of ``net`` can carry, by the pins it serves: {index in ``tracks``: amps} for a
        net with ``pin_loads`` ({} otherwise: every track then takes the net's current). The net's copper is read as
        a graph: a node per pad, via, pour piece and track joint, an edge per stretch of track between two joints. A
        stretch whose removal parts the net (a bridge) carries no more than the smaller side can draw: the net's
        ``amps`` if that side holds a pin of the full current, else the sum of its pins' loads. A stretch in a loop
        takes the full current, a stretch wholly inside a pad or a via none, and every track of a net whose graph
        does not join all that its copper joins takes the full current (a joint the graph missed: read safely).
        A track drawn with its own ``amps`` keeps that (not in the result)"""
        loads = self.pin_loads(net)
        d = self.nets.get(net)
        if not loads or not d:
            return {}
        from shapely.geometry import LineString, Point
        from shapely.ops import nearest_points
        from shapely.strtree import STRtree
        A = d["amps"]
        tol = 0.005
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        par: List[int] = []

        def new():
            par.append(len(par))
            return len(par) - 1

        def find(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i

        def union(a, b):
            a, b = find(a), find(b)
            if a != b:
                par[b] = a
        segs = []                                        # (track index, layer, centre line) per straight stretch
        for i, t in enumerate(self.tracks):
            if t["net"] == net:
                for a, b in zip(t["pts"], t["pts"][1:]):
                    if math.dist(a, b) > 1e-6:
                        segs.append((i, t["layer"], LineString([a, b])))
        if not any(self.tracks[i]["amps"] is None for i, _, _ in segs):
            return {}                                    # (no track to judge: a net of pours and pads alone)
        ends = [(new(), new()) for _ in segs]
        att: List[list] = [[(0.0, e0), (1.0, e1)] for e0, e1 in ends]    # (where along each stretch things join)
        covered = [False] * len(segs)

        def attach(k, q, node):                          # join ``node`` to stretch k at the point q on it
            g = segs[k][2]
            u = g.project(Point(q), normalized=True)
            if u * g.length <= tol:
                union(ends[k][0], node)
            elif (1 - u) * g.length <= tol:
                union(ends[k][1], node)
            else:
                n_ = new()
                att[k].append((u, n_))
                union(n_, node)
        by_layer: Dict[str, List[int]] = {}
        for k, (_, L, _) in enumerate(segs):
            by_layer.setdefault(L, []).append(k)
        trees = {L: STRtree([segs[k][2] for k in ks]) for L, ks in by_layer.items()}
        for L, ks in by_layer.items():                   # track to track: where their centre lines meet
            ia, ib = trees[L].query([segs[k][2] for k in ks], predicate="dwithin", distance=tol)
            for a_, b_ in zip(ia, ib):
                if a_ >= b_:
                    continue
                ka, kb = ks[int(a_)], ks[int(b_)]
                ga, gb = segs[ka][2], segs[kb][2]
                if ga.intersects(gb):
                    x = ga.intersection(gb)
                    qs = [c for g_ in (getattr(x, "geoms", None) or [x]) if not g_.is_empty for c in
                          (list(g_.coords) if g_.geom_type == "Point" else [g_.coords[0], g_.coords[-1]])]
                    links = [(q, q) for q in qs]
                else:
                    pa, pb = nearest_points(ga, gb)
                    links = [((pa.x, pa.y), (pb.x, pb.y))]
                for qa, qb in links:
                    n_ = new()
                    attach(ka, qa, n_)
                    attach(kb, qb, n_)
        wmax = max([self.tracks[i]["w"] for i, _, _ in segs] or [0.0])
        for L, ks in by_layer.items():                   # a track's end inside another track's copper (KiCad's
            for k in ks:                                 # rule: where a router ends a branch on the trunk)
                for end, q in ((0, segs[k][2].coords[0]), (1, segs[k][2].coords[-1])):
                    pt = Point(q)
                    for j in trees[L].query(pt, predicate="dwithin", distance=wmax / 2 + tol):
                        kj = ks[int(j)]
                        if segs[kj][0] != segs[k][0] and \
                                segs[kj][2].distance(pt) <= self.tracks[segs[kj][0]]["w"] / 2 + tol:
                            attach(kj, q, ends[k][end])

        def hook(node, g, L, by_copper=False, cover=True):   # the stretches on layer L reaching shape g join node
            if L not in trees:                               # (by their centre line; ``by_copper``: by their copper)
                return
            for j in trees[L].query(g, predicate="dwithin", distance=(wmax / 2 if by_copper else 0.0) + tol):
                k = by_layer[L][int(j)]
                line = segs[k][2]
                r_ = (self.tracks[segs[k][0]]["w"] / 2 if by_copper else 0.0) + tol
                if line.distance(g) > r_:
                    continue
                ina = Point(line.coords[0]).distance(g) <= r_
                inb = Point(line.coords[-1]).distance(g) <= r_
                if ina:
                    union(ends[k][0], node)
                if inb:
                    union(ends[k][1], node)
                if ina and inb and cover:
                    covered[k] = True                    # (inside the pad or the via: they carry it)
                if not ina and not inb:
                    attach(k, nearest_points(line, g.centroid if cover else g)[0].coords[0], node)
        pin_node: Dict[Tuple[str, str], int] = {}
        own = [(r, n_, L, g) for r, n_, L, g, _ in pads if pn.get((r, n_)) == net]
        for r, n_, L, g in own:
            if (r, n_) not in pin_node:
                pin_node[(r, n_)] = new()
            hook(pin_node[(r, n_)], g, L)
        for L in self.layers:                            # pads of the net that overlap (a USB-C's A4 / B9): one
            lst = [(r, n_, g) for r, n_, L_, g in own if L_ == L]
            if len(lst) > 1:
                ia, ib = STRtree([g for _, _, g in lst]).query([g for _, _, g in lst], predicate="intersects")
                for a_, b_ in zip(ia, ib):
                    union(pin_node[lst[int(a_)][:2]], pin_node[lst[int(b_)][:2]])
        via_node = []
        for v in self.vias:
            if v["net"] != net:
                continue
            vn, disk = new(), Point(v["at"]).buffer(v["dia"] / 2, 12)
            via_node.append((vn, disk))
            for L in self.layers:
                hook(vn, disk, L)
            for r, n_, L, g in own:                      # (a via in a pad)
                if g.intersects(disk):
                    union(vn, pin_node[(r, n_)])
        poured = [L for L in self.layers if any(pz["net"] == net and pz["layer"] == L for pz in self.pours)]
        if poured:
            cu = self.copper() if cu is None else cu
            fixed = self.copper(pours=False)
            own_by = {L: [(r, n_) for r, n_, L_, _ in own if L_ == L] for L in poured}
            own_tree = {L: STRtree([g for _, _, L_, g in own if L_ == L]) for L in poured if own_by[L]}
            via_tree = STRtree([g for _, g in via_node]) if via_node else None
            for L in poured:                             # (the flood alone, the net's own pads and tracks taken
                fl = self.pour_fill(net, L, cu)          #  out: a track where the zone is carved away is no pour)
                if fixed.get((net, L)) is not None:
                    fl = fl.difference(fixed[(net, L)])
                for piece in (getattr(fl, "geoms", None) or ([fl] if not fl.is_empty else [])):
                    if piece.area < 0.01:
                        continue
                    zn = new()
                    hook(zn, piece, L, by_copper=True, cover=False)
                    for j in (own_tree[L].query(piece, predicate="dwithin", distance=tol) if L in own_tree else []):
                        union(zn, pin_node[own_by[L][int(j)]])
                    for j in (via_tree.query(piece, predicate="dwithin", distance=tol) if via_node else []):
                        union(zn, via_node[int(j)][0])
        edges = []                                       # (node, node, stretch): the track between two joints
        for k, lst in enumerate(att):
            lst = sorted(lst)
            for (_, n0), (_, n1) in zip(lst, lst[1:]):
                edges.append((find(n0), find(n1), k))
        weight: Dict[int, list] = {}                     # node: [pins of the full current, sum of the loads]
        pins = [tuple(p) for p in d["pins"] if tuple(p) in pin_node]
        for p in pins:
            w_ = weight.setdefault(find(pin_node[p]), [0, 0.0])
            if p in loads:
                w_[1] += loads[p]
            else:
                w_[0] += 1
        full = {i: A for i, t in enumerate(self.tracks) if t["net"] == net and t["amps"] is None}
        comp = {}                                        # (the graph's pieces, to compare with the copper's)

        def cf(x):
            comp.setdefault(x, x)
            while comp[x] != x:
                x = comp[x]
            return x
        for a, b, _ in edges:
            ra, rb = cf(a), cf(b)
            if ra != rb:
                comp[rb] = ra
        if len({cf(find(pin_node[p])) for p in pins}) > len(self.islands(net, cu, pads, pn)):
            return full
        adj: Dict[int, list] = {}
        for e, (a, b, _) in enumerate(edges):
            adj.setdefault(a, []).append((b, e))
            adj.setdefault(b, []).append((a, e))
        cur = [A] * len(edges)                           # (a stretch in a loop: the full current)
        cap = lambda f, l_: A if f else min(A, l_)
        tin: Dict[int, int] = {}
        low: Dict[int, int] = {}
        clock = 0
        for root in adj:                                 # bridges (Tarjan), iteratively, with each subtree's pins
            if root in tin:
                continue
            tin[root] = low[root] = clock
            clock += 1
            up = {root: (None, -1)}                      # node: (its parent, the edge to it)
            order, stack = [], [(root, iter(adj[root]))]
            while stack:
                x, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    stack.pop()
                    order.append(x)
                    p_ = up[x][0]
                    if p_ is not None:
                        low[p_] = min(low[p_], low[x])
                    continue
                y, e = nxt
                if e == up[x][1]:
                    continue
                if y in tin:
                    low[x] = min(low[x], tin[y])
                else:
                    tin[y] = low[y] = clock
                    clock += 1
                    up[y] = (x, e)
                    stack.append((y, iter(adj[y])))
            sub = {x: list(weight.get(x, [0, 0.0])) for x in order}
            for x in order:                              # (post-order: a child before its parent)
                p_ = up[x][0]
                if p_ is not None:
                    sub[p_][0] += sub[x][0]
                    sub[p_][1] += sub[x][1]
            tot = sub[root]
            for x in order:
                p_, e = up[x]
                if p_ is not None and low[x] > tin[p_]:
                    cur[e] = min(cap(*sub[x]), cap(tot[0] - sub[x][0], tot[1] - sub[x][1]))
        out: Dict[int, float] = {}
        for e, (_, _, k) in enumerate(edges):
            i = segs[k][0]
            if i in full:                                # (a stretch whose ends one conductor already joins - a pad,
                short_ = covered[k] or edges[e][0] == edges[e][1]   # a via, the copper of the track it ends
                out[i] = max(out.get(i, 0.0), 0.0 if short_ else cur[e])   # on - carries nothing of its own)
        return {i: round(out.get(i, A), 4) for i in full}

    def net_length(self, net: str) -> float:
        """the total length of ``net``'s tracks, mm (a branched net's branches all count)"""
        import math
        return sum(math.dist(a, b) for t in self.tracks if t["net"] == net for a, b in zip(t["pts"], t["pts"][1:]))

    def pair(self, p: str, n: str, max_skew: float = 0.5):
        """mark ``p`` / ``n`` as a differential pair (USB D+ / D-, CAN H / L): check() reports when their track
        lengths differ by more than ``max_skew`` mm (USB 2.0 full speed tolerates mm; high speed wants ~0.15)"""
        self.__dict__.setdefault("pairs", []).append((p, n, max_skew))

    def track(self, net: str, layer: str, pts: Sequence[Tuple[float, float]], width: float, amps: Optional[float] = None):
        """a track; ``amps`` when this branch carries less than the whole net (a regulator's feed off a bus)"""
        self.tracks.append({"net": net, "layer": layer, "pts": [tuple(p) for p in pts], "w": width, "amps": amps})

    def pad(self, ref: str, num: str) -> Tuple[float, float]:
        """a pad's centre in board coordinates (route tracks to it)"""
        c = self.comps[ref]
        g = _pad_poly(c["pads"][str(num)][0], (c["at"][0], c["at"][1], c["rot"]))
        return (round(g.centroid.x, 4), round(g.centroid.y, 4))

    def pour(self, net: str, layer: str, poly: Sequence[Tuple[float, float]], amps: Optional[float] = None,
             holes: Optional[Sequence[Sequence[Tuple[float, float]]]] = None, fill: bool = False):
        """a copper zone: filled inside ``poly`` (less any ``holes``: rings left bare, another net's zone) and the board,
        keeping ``clearance`` from other nets' copper; ``amps`` the current this zone carries (a branch feeding three
        motors), default the net's; ``fill`` marks a background fill (a ground fill round the power copper) that other
        nets' stitching vias may pierce, where a power pour is kept whole"""
        self.pours.append({"net": net, "layer": layer, "poly": [tuple(p) for p in poly], "amps": amps,
                           "holes": [[tuple(p) for p in h] for h in (holes or [])], "fill": fill})

    def via(self, net: str, at: Tuple[float, float], drill: float = 0.4, dia: float = 0.8):
        self.vias.append({"net": net, "at": tuple(at), "drill": drill, "dia": dia})

    def stitch(self, net: str, region: Sequence[Tuple[float, float]], pitch: float = 1.5, drill: float = 0.3,
               dia: float = 0.6, layers: str = "FB") -> int:
        """stitching vias for ``net`` on a ``pitch`` grid inside ``region``, wherever the net's copper (pours included,
        after routing) covers the whole via on both layers: ties each top island of a pour to the bottom one (a ground
        pour cut up by tracks). Returns how many it added."""
        from shapely.geometry import Point, Polygon
        cu = self.copper(prune=False)
        gs = [cu.get((net, L)) for L in layers]           # the layers it ties together ("FB", "F1", "12B", ...)
        if any(g is None for g in gs):
            return 0
        import shapely
        from shapely.ops import unary_union
        for g in gs:                                      # (tested point by point: a negative buffer of a whole
            shapely.prepare(g)                            # board's carved fill can defeat GEOS)
        # never in a pad (solder wicks down a via in an SMD pad) nor drilled into or beside a pad's or the board's hole
        pads = [g for _, _, _, g, _ in self.pad_shapes()]
        drills = [(g.centroid, d) for _, _, _, g, d in self.pad_shapes() if d] + \
                 [(Point(x, y_), d) for x, y_, d in self.all_holes()]
        # a via goes through every layer: clear of other nets' tracks, pads and vias on all of them, not only on
        # the layers it ties (an inner-layer track under a ground fill)
        # (and off other nets' outer-layer power pours: those are not to be perforated; a plane or a fill may be)
        fixed = self.copper(pours=False)
        foreign = [g.buffer(dia / 2 + self.clear + 0.02, 8) for (n_, L_), g in fixed.items() if n_ != net] + \
                  [Polygon(p["poly"]).intersection(cu[(p["net"], p["layer"])]).buffer(dia / 2 + self.clear + 0.02, 8)
                   for p in self.pours if p["net"] != net and p["layer"] in "FB" and not p.get("fill")
                   and (p["net"], p["layer"]) in cu]
        avoid = unary_union([g.buffer(dia / 2 + 0.2, 8) for g in pads] + foreign +
                            [c.buffer(d / 2 + drill / 2 + self.hole_gap, 12) for c, d in drills] or [Polygon()])
        shapely.prepare(avoid)
        reg = Polygon(region).intersection(self.outline.buffer(-(dia / 2 + self.edge)))    # (off the board edge)
        shapely.prepare(reg)
        others = [Point(v["at"]) for v in self.vias]
        x0, y0, x1, y1 = reg.bounds
        n = 0
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                p = Point(x, y)
                if reg.contains(p) and not avoid.contains(p):
                    disk = p.buffer(dia / 2 + 0.02, 8)
                    if all(g.contains(disk) for g in gs) and all(p.distance(q) >= dia + 0.2 for q in others):
                        self.vias.append({"net": net, "at": (round(x, 3), round(y, 3)), "drill": drill, "dia": dia})
                        others.append(p)
                        n += 1
                x += pitch
            y += pitch
        return n

    def drop(self, net: str, pins: Optional[Sequence[Tuple[str, str]]] = None, via: Tuple[float, float] = (0.3, 0.6),
             width: float = 0.3, reach: float = 3.0, amps: Optional[float] = None) -> List[str]:
        """connect each pad of ``net`` (or just ``pins``) to the net's plane on an inner layer the way a 4-layer
        board does: a via right beside the pad and a short track to it, the via placed where it clears every other
        net's copper on every layer, the drills and the keep-outs. Returns the pads it found no room for."""
        from shapely.geometry import LineString, Point
        from shapely.ops import unary_union
        probs: List[str] = []
        pn = self.pad_net()
        pins = list(pins) if pins is not None else [p for p in self.nets[net]["pins"]]
        fixed = self.copper(pours=False)
        others = {L: unary_union([g for (n, l), g in fixed.items() if l == L and n != net] or [Point(0, 0).buffer(0)])
                  for L in self.layers}
        drills = unary_union([Point(x, y).buffer(d / 2 + self.hole_gap + via[0] / 2, 12) for x, y, d in self.all_holes()] +
                             [h["geom"].buffer(self.hole_gap + via[0] / 2, 12)   # (each drill as its shape: a USB-C
                              for h in self.drill_list()]                        #  shell's slot is longer than wide)
                             or [Point(0, 0).buffer(0)])
        inside = self.outline.buffer(-(via[1] / 2 + 0.3))
        edge_in = self.outline.buffer(-(self.edge + 0.02))   # (the stub as well as its via keeps the edge gap)
        bare = unary_union([Point(x, y).buffer(d / 2, 12) for x, y, d in self.npth()] +
                           [Point(x, y).buffer(d / 2, 12) for x, y, d in self.holes] or [Point(0, 0).buffer(0)])
        kz = unary_union([__import__("shapely.geometry", fromlist=["Polygon"]).Polygon(k["poly"])
                          for k in getattr(self, "keepouts", [])] or [Point(0, 0).buffer(0)])
        shapes = {(r, n_, L): g for r, n_, L, g, _ in self.pad_shapes()}
        import shapely
        for g_ in list(others.values()) + [drills, inside, kz, bare]:   # (tested thousands of times per call: prepared)
            shapely.prepare(g_)
        for ref, num in pins:
            c = self.comps[ref]
            L = "B" if c.get("side", "F") == "B" else "F"
            g = shapes.get((ref, num, L))
            if g is None:                                    # a plated through-hole already reaches the plane
                continue
            p0 = g.centroid
            r0 = max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2
            mrr = g.minimum_rotated_rectangle.exterior.coords
            minor = min(math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2]))
            w_ = round(max(width, min(self.net_width(net), minor)), 3)   # (as wide as the net, up to the pad)
            best = None
            for k in range(int(reach / 0.25)):
                r = r0 + via[1] / 2 + self.clear + 0.05 + 0.25 * k
                for i in range(24):
                    a = 2 * math.pi * i / 24
                    q = Point(p0.x + r * math.cos(a), p0.y + r * math.sin(a))
                    disk = q.buffer(via[1] / 2 + self.clear + 0.04, 12)
                    if not inside.contains(q) or q.intersects(drills) or disk.intersects(kz):
                        continue
                    if any(disk.intersects(others[l]) for l in self.layers):
                        continue
                    trk = LineString([(p0.x, p0.y), (q.x, q.y)]).buffer(w_ / 2 + self.clear + 0.04, 8)
                    if trk.intersects(others[L]) or trk.buffer(HOLE_CLEAR - self.clear).intersects(bare) or \
                            not edge_in.contains(LineString([(p0.x, p0.y), (q.x, q.y)]).buffer(w_ / 2, 8)):
                        continue
                    best = q
                    break
                if best is not None:
                    break
            if best is None:
                probs.append(f"drop {net}: no room for a via beside {ref}.{num}")
                continue
            self.tracks.append({"net": net, "layer": L, "pts": [(round(p0.x, 4), round(p0.y, 4)),
                                                                (round(best.x, 4), round(best.y, 4))],
                                "w": w_, "amps": amps, **({"neck": True} if w_ < self.net_width(net) - 1e-6 else {})})
            self.vias.append({"net": net, "at": (round(best.x, 4), round(best.y, 4)), "drill": via[0], "dia": via[1]})
            drills = drills.union(best.buffer(via[0] + self.hole_gap, 12))
            shapely.prepare(drills)
        return probs

    def hole(self, at: Tuple[float, float], d: float = 3.2, keepout: Optional[float] = None):
        """a mounting hole; ``keepout`` the copper-free radius round it (the standoff's or the screw head's)"""
        self.holes.append((at[0], at[1], d))
        self._hole_ko = getattr(self, "_hole_ko", {})
        self._hole_ko[(at[0], at[1])] = keepout if keepout is not None else d / 2 + 0.6

    def npth(self) -> List[Tuple[float, float, float]]:
        """the non-plated holes the parts' footprints bring (a USB-C's locating pegs, a switch's posts): (x, y, d),
        board mm. Routed copper, vias and pours keep clear of them like mounting holes; the drill file drills them"""
        key = tuple((r, c["fp"], tuple(c["at"]), c["rot"], c.get("side", "F"), id(c["pads"]))
                    for r, c in self.comps.items())
        memo = self.__dict__.get("_npth_memo")
        if memo is not None and memo[0] == key:
            return list(memo[1])
        out = []
        for ref, c in self.comps.items():
            for lst in c["pads"].values():
                for pad in lst:
                    if pad["drill"] and (pad.get("kind") == "np_thru_hole" or not pad["layers"]):
                        g = _pad_poly(pad, (c["at"][0], c["at"][1], c["rot"]))
                        d = max(pad["slot"]) if pad.get("slot") else pad["drill"]   # (a slot: kept clear as its length)
                        out.append((round(g.centroid.x, 4), round(g.centroid.y, 4), float(d)))
        self._npth_memo = (key, out)
        return list(out)

    def all_holes(self) -> List[Tuple[float, float, float]]:
        """the mounting holes and the footprints' non-plated holes: every bare hole in the board"""
        return list(self.holes) + self.npth()

    def cutout(self, poly: Sequence[Tuple[float, float]]):
        """a hole cut through the board inside its outline (a window, a slot for a cable): routed like the edge"""
        from shapely.geometry import Polygon
        self.outline = self.outline.difference(Polygon(poly))

    def drill_list(self) -> List[Dict]:
        """every drill in the board: {"name", "at", "d", "plated", "slot", "geom"}; ``slot`` is None for a round hole,
        else its centre line ((x0, y0), (x1, y1)) with ``d`` its width; ``geom`` the hole's shape"""
        from shapely.geometry import LineString, Point
        out = [{"name": f"via {v['net']}", "at": tuple(v["at"]), "d": v["drill"], "plated": True, "slot": None}
               for v in self.vias] + \
              [{"name": "hole", "at": (x, y), "d": d, "plated": False, "slot": None} for x, y, d in self.holes]
        for ref, c in self.comps.items():
            for num, lst in c["pads"].items():
                for pad in lst:
                    if not pad["drill"]:
                        continue
                    place = (c["at"][0], c["at"][1], c["rot"])
                    plated = bool(pad["layers"]) and pad.get("kind") != "np_thru_hole"
                    d = dict(name=f"{ref} pad {num}" if plated else "hole", plated=plated, slot=None, ref=ref)
                    if pad.get("slot") and abs(pad["slot"][0] - pad["slot"][1]) > 1e-6:
                        g = _pad_poly({**pad, "shape": "oval", "size": pad["slot"], "prims": []}, place)
                        q = list(g.minimum_rotated_rectangle.exterior.coords)
                        a, b = max(((q[0], q[1]), (q[1], q[2])), key=lambda e: math.dist(*e))
                        L, w = math.dist(a, b), min(pad["slot"])
                        ux, uy = (b[0] - a[0]) / L * (L - w) / 2, (b[1] - a[1]) / L * (L - w) / 2
                        cx, cy = g.centroid.x, g.centroid.y
                        d.update(at=(cx, cy), d=w, slot=((cx - ux, cy - uy), (cx + ux, cy + uy)))
                    else:
                        g = _pad_poly(pad, place)
                        d.update(at=(g.centroid.x, g.centroid.y), d=float(pad["drill"]))
                    out.append(d)
        for d in out:
            d["geom"] = (LineString(d["slot"]) if d["slot"] else Point(d["at"])).buffer(d["d"] / 2, 16)
        return out

    def keepout(self, poly: Sequence[Tuple[float, float]], layers: str = "FB", why: str = ""):
        """a copper-free zone (under an antenna, a high-voltage gap): pours are carved round it, the router stays out
        and puts no via in it, and check() flags any pad or track inside"""
        self.keepouts = getattr(self, "keepouts", [])
        self.keepouts.append({"poly": [tuple(p) for p in poly], "layers": layers, "why": why})

    def keepout_zones(self, layer: str):
        return [k["poly"] for k in getattr(self, "keepouts", []) if layer in k["layers"]]

    def fanout(self, width: float = 0.2, reach: float = 0.5):
        """a straight stub off the outer end of every fine-pitch pad on a net, before any routing, so no route runs
        where another pad's way out must go"""
        from shapely.geometry import LineString, Polygon
        from pcb_route import _escape
        self._fan = getattr(self, "_fan", set())
        pn = self.pad_net()
        ko = {L: [Polygon(k) for k in self.keepout_zones(L)] for L in "FB"}
        for (ref, num), n in pn.items():
            if (ref, num) in self._fan or ref not in self.comps or num not in self.comps[ref]["pads"]:
                continue
            e = _escape(self, ref, num, reach)
            L = "B" if self.comps[ref].get("side", "F") == "B" else "F"
            if e and any(LineString([e[0], e[1]]).buffer(width / 2).intersects(k) for k in ko[L]):
                continue                                 # (no stub into a keep-out: an antenna's; the router
            if e:                                        #  leaves that pad another way)
                self.track(n, L, [e[0], e[1]], width, 0.01)
                self.tracks[-1]["fan"] = (ref, num)
                self._fan.add((ref, num))

    # geometry
    def pad_shapes(self):
        """[(ref, pad number, layer 'F'/'B', polygon, drill)] for every copper pad (memoised on the placement: the
        router asks for them on every net)"""
        key = (self.layers, tuple((r, c["fp"], tuple(c["at"]), c["rot"], c.get("side", "F"), id(c["pads"]))
                                  for r, c in self.comps.items()))
        memo = self.__dict__.get("_pads_memo")
        if memo is not None and memo[0] == key:
            return list(memo[1])
        out = self._pad_shapes()
        self._pads_memo = (key, out)
        return list(out)

    def _pad_shapes(self):
        out = []
        for ref, c in self.comps.items():
            place = (c["at"][0], c["at"][1], c["rot"])
            for num, lst in c["pads"].items():
                for pad in lst:
                    if not pad["layers"]:
                        continue
                    g = _pad_poly(pad, place)
                    for L in self.layers:
                        if L in pad["layers"] or (L not in ("F", "B") and pad["drill"] and pad["layers"] == "FB"):
                            out.append((ref, num, L, g, pad["drill"]))
        return out

    def pad_net(self) -> Dict[Tuple[str, str], str]:
        """{(ref, pad number): net}. A pad on no net that lies on a netted pad of the same part (a USB-C's A4 / B9,
        two numbers for one piece of copper) takes that pad's net: it is the same copper, and left out it would read
        as a foreign pad shorting the net"""
        pn = {pin: n for n, d in self.nets.items() for pin in d["pins"]}
        key = (tuple(sorted(pn.items())), self.__dict__.get("_pads_memo", (None,))[0])
        memo = self.__dict__.get("_pn_memo")
        if memo is not None and memo[0] == key and key[1] is not None:
            return dict(memo[1])
        by_ref: Dict[str, list] = {}
        for ref, num, L, g, d in self.pad_shapes():
            by_ref.setdefault(ref, []).append((num, L, g))
        for ref, lst in by_ref.items():
            netted = [(num, L, g) for num, L, g in lst if (ref, num) in pn]
            if not netted:
                continue
            for num, L, g in lst:
                if (ref, num) in pn or g.area <= 0:
                    continue
                for num2, L2, g2 in netted:
                    if L2 == L and g.intersection(g2).area > 0.5 * min(g.area, g2.area):
                        pn[(ref, num)] = pn[(ref, num2)]
                        break
        self._pn_memo = ((tuple(sorted({pin: n for n, d in self.nets.items() for pin in d["pins"]}.items())),
                          self.__dict__.get("_pads_memo", (None,))[0]), pn)
        return dict(pn)

    def pour_fill(self, net: str, layer: str, cu=None):
        """just the poured copper of ``net`` on ``layer`` (no pads, tracks or vias); ``cu`` a ``copper(only_net=
        net)`` already computed (the layers of one net share it)"""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        cu = self.copper(only_net=net) if cu is None else cu
        g = cu.get((net, layer))
        if g is None:
            return Polygon()
        zones = unary_union([Polygon(p["poly"], p.get("holes") or None) for p in self.pours if p["net"] == net and p["layer"] == layer] or [Polygon()])
        return g.intersection(zones)

    def _item_shape(self, it):
        """a track's or via's copper outline, cached on its geometry (the check asks for each on every layer)"""
        from shapely.geometry import LineString as _LS, Point as _Pt
        key = (tuple(map(tuple, it["pts"])), it["w"]) if "pts" in it else (tuple(it["at"]), it["dia"])
        cache = self.__dict__.setdefault("_shape_cache", {})
        g = cache.get(key)
        if g is None:
            g = _LS(it["pts"]).buffer(it["w"] / 2, 8) if "pts" in it else _Pt(it["at"]).buffer(it["dia"] / 2, 12)
            if len(cache) > 200000:
                cache.clear()
            cache[key] = g
        return g

    def _state_key(self) -> str:
        """a hash of everything copper() reads: an edit anywhere (a track moved, a via, a pour, a part) changes it"""
        import hashlib
        import pickle
        comps = sorted((r, c["fp"], tuple(c["at"]), c["rot"], c.get("side", "F"), c.get("pads_key", ""))
                       for r, c in self.comps.items())
        state = (comps, sorted((n, tuple(map(tuple, v["pins"]))) for n, v in self.nets.items()),
                 [(t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) for t in self.tracks],
                 [(v["net"], tuple(v["at"]), v["dia"]) for v in self.vias],
                 [(p["net"], p["layer"], repr(p["poly"]), repr(p.get("holes"))) for p in self.pours],
                 tuple(self.holes), self.clear, self.hole_gap, self.edge, self.layers, repr(getattr(self, "keepouts", [])),
                 repr(getattr(self, "_hole_ko", {})), self.outline.wkb)
        return hashlib.sha1(pickle.dumps(state)).hexdigest()

    def copper(self, pours: bool = True, only_net: Optional[str] = None, prune: bool = True) -> Dict[Tuple[str, str], object]:
        """{(net, layer): shapely geometry} with pours carved round other nets' copper; ``prune`` cuts away a pour
        island that touches nothing of its net (off for stitching: the vias are what will join it). Memoised on the
        board's state: the check, the plot and the Gerbers of an unchanged board share one computation"""
        memo = self.__dict__.setdefault("_cu_memo", {})
        k = (self._state_key(), pours, only_net, prune)
        if k not in memo:
            if len(memo) > 8:
                memo.clear()
            memo[k] = self._copper(pours, only_net, prune)
        return dict(memo[k])

    def _copper(self, pours: bool = True, only_net: Optional[str] = None, prune: bool = True) -> Dict[Tuple[str, str], object]:
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import unary_union
        pn = self.pad_net()
        raw: Dict[Tuple[str, str], list] = {}
        for ref, num, L, g, _ in self.pad_shapes():
            raw.setdefault((pn.get((ref, num), f"~{ref}.{num}"), L), []).append(g)
        for t in self.tracks:
            raw.setdefault((t["net"], t["layer"]), []).append(LineString(t["pts"]).buffer(t["w"] / 2, 12))
        for v in self.vias:
            for L in self.layers:
                raw.setdefault((v["net"], L), []).append(Point(v["at"]).buffer(v["dia"] / 2, 16))
        fixed = {k: unary_union(v) for k, v in raw.items()}
        cu = dict(fixed)
        # (what a pour keeps clear of, computed once per net and layer, not once per pour)
        hko = getattr(self, "_hole_ko", {})
        holes = unary_union([Point(x, y).buffer(max(hko.get((x, y), 0), d / 2 + self.clear + 0.3), 24)
                             for x, y, d in self.all_holes()] or [Polygon()])
        inner = self.outline.buffer(-(max(0.3, self.edge) + 0.005))   # (+5 um: a curved edge's chords come closer)
        clear_of: Dict[tuple, object] = {}
        kz_of: Dict[str, object] = {}
        import numpy as np
        import shapely
        from shapely.strtree import STRtree
        grown: Dict[str, tuple] = {}
        grown_tree: Dict[str, object] = {}
        for pz in (self.pours if pours else []):
            if only_net is not None and pz["net"] != only_net:
                continue
            L = pz["layer"]
            if L not in grown:                           # every item on the layer grown by the clearance, once
                its = [(n, g) for (n, l), lst in raw.items() if l == L for g in lst]
                ga = np.array([g for _, g in its], dtype=object)
                grown[L] = (np.array([n for n, _ in its], dtype=object), shapely.buffer(ga, self.clear + 0.05, quad_segs=8)
                            if len(ga) else ga)
                grown_tree[L] = STRtree(grown[L][1]) if len(ga) else None
            if L not in kz_of:
                kz_of[L] = unary_union([Polygon(k) for k in self.keepout_zones(L)] or [Polygon()])
            zone = Polygon(pz["poly"], pz.get("holes") or None).intersection(inner)
            if (pz["net"], L, zone.wkb) not in clear_of:  # (only the other nets' items reaching into this zone)
                nm, gg = grown[L]
                ci = grown_tree[L].query(zone, predicate="intersects") if grown_tree[L] is not None else []
                ci = [int(i) for i in ci if nm[int(i)] != pz["net"]]
                clear_of[(pz["net"], L, zone.wkb)] = shapely.union_all(gg[ci]) if ci else Polygon()
            area = zone.difference(clear_of[(pz["net"], L, zone.wkb)]).difference(holes).difference(kz_of[L])
            area = area.buffer(-MIN_FILL / 2, 8).buffer(MIN_FILL / 2, 8)   # (no sliver narrower than a fab etches)
            key = (pz["net"], L)
            cu[key] = unary_union([cu.get(key, Polygon()), area])
        for key in {(p["net"], p["layer"]) for p in (self.pours if pours and prune else [])}:   # dead copper: an island of
            g, f = cu.get(key), fixed.get(key)                                          # pour touching nothing of
            if g is None or g.is_empty:                                                 # its net is cut away, as a
                continue                                                                # fab flow removes it
            keep = [q for q in (getattr(g, "geoms", None) or [g]) if f is not None and q.intersects(f)]
            if keep:
                cu[key] = unary_union(keep)
            else:
                del cu[key]
        return cu

    def tidy(self) -> int:
        """dead vias taken away: a via that meets its own net's copper on fewer than two layers joins nothing (KiCad
        warns "via dangling"); the track ends meeting in it are brought to its centre first, so they still meet. And
        of two vias of one net drilled closer than the hole gap, one goes, its tracks brought to the other. Each kept
        only when check() finds nothing new. Returns how many vias it removed"""
        from shapely.geometry import Point
        before = set(self.check())
        vias0, tracks0 = list(self.vias), [dict(t, pts=list(t["pts"])) for t in self.tracks]
        merged = 0                                       # two vias of one net drilled into each other (two routing
        for v in list(self.vias):                        # passes' drops at one pad): one goes, the tracks that met
            if v not in self.vias:                       # it brought on to the other
                continue
            for w in [w for w in self.vias if w is not v and w["net"] == v["net"] and not w.get("user")]:
                if math.dist(v["at"], w["at"]) - (v["drill"] + w["drill"]) / 2 >= self.hole_gap - 1e-6:
                    continue
                for t in list(self.tracks):              # (a snapshot: the joins added here are not walked again)
                    if t["net"] == w["net"]:
                        for i in (0, -1):
                            if 1e-6 < math.dist(t["pts"][i], v["at"]) and math.dist(t["pts"][i], w["at"]) < w["dia"] / 2:
                                j_ = {k_: x_ for k_, x_ in t.items() if k_ not in ("fan", "user")}
                                self.tracks.append({**j_, "pts": [tuple(t["pts"][i]), tuple(v["at"])]})
                self.vias.remove(w)
                merged += 1
        if merged:
            self.__dict__.pop("_cu_memo", None)
            if set(self.check()) - before:               # (it broke something: as they were)
                self.vias, self.tracks = list(vias0), [dict(t, pts=list(t["pts"])) for t in tracks0]
                self.__dict__.pop("_cu_memo", None)
                merged = 0
            else:
                before = set(self.check())
                vias0, tracks0 = list(self.vias), [dict(t, pts=list(t["pts"])) for t in self.tracks]
        self.vias = []                                   # (each net's copper without its vias: what a via meets;
        self.__dict__.pop("_cu_memo", None)              # pour islands kept, the ones only a via joins too)
        cu = self.copper(prune=False)
        self.vias = list(vias0)
        self.__dict__.pop("_cu_memo", None)
        keep_v = []
        for v in self.vias:
            disk = Point(v["at"]).buffer(v["dia"] / 2 - 0.01, 16)
            touched = sum(1 for L in self.layers if (v["net"], L) in cu and cu[(v["net"], L)].intersects(disk))
            others = [w for w in self.vias if w is not v and w["net"] == v["net"]
                      and Point(w["at"]).distance(Point(v["at"])) < (w["dia"] + v["dia"]) / 2]
            if touched >= 2 or others or v.get("user"):  # (the script's own via stays as drawn)
                keep_v.append(v)
        for v in self.vias:                              # (a via the tracks of one layer meet at: their ends are
            if v in keep_v:                              # brought to its centre first, so they still meet)
                continue
            c = Point(v["at"])
            for t in list(self.tracks):
                if t["net"] != v["net"]:
                    continue
                for e in (t["pts"][0], t["pts"][-1]):
                    if 1e-6 < Point(e).distance(c) < v["dia"] / 2:
                        self.tracks.append({**t, "pts": [tuple(e), tuple(v["at"])]})
        changed = len(vias0) - len(keep_v)
        if not changed:
            return merged
        self.vias = keep_v
        self.__dict__.pop("_cu_memo", None)
        if set(self.check()) - before:                   # (it broke something: put it all back)
            self.vias, self.tracks = vias0, tracks0
            self.__dict__.pop("_cu_memo", None)
            return merged
        return changed + merged

    def drop_loops(self) -> int:
        """tracks a net does not need: one whose removal leaves every pin of its net joined as before (a second run
        beside the first, a loop, a stub left over), longest first, again until none goes. Nets with a pour or
        carrying an amp or more are left alone (parallel copper there may be on purpose). Kept only when check()
        finds nothing new. Returns how many tracks it removed"""
        import math
        poured = {pz["net"] for pz in self.pours}
        nets = [n for n, d in self.nets.items() if n not in poured and d.get("amps", 0.1) < 1.0]
        before, tracks0 = set(self.check()), list(self.tracks)
        length = lambda t: sum(math.dist(a, b) for a, b in zip(t["pts"], t["pts"][1:]))
        removed = 0
        def joined(n, want=None):                        # (by copper, then by where track ends land: KiCad's rule)
            g = len(self.islands(n))
            return (g, len(self.anchor_groups(n))) if want is None or g == want[0] else (g, None)
        for net in nets:
            groups = joined(net)
            again = True
            while again:
                again = False
                for t in sorted((t for t in self.tracks if t["net"] == net and length(t) >= 0.5
                                 and not t.get("user")), key=length,
                                reverse=True):          # (a run, not a corner's or a pin's escape piece)
                    i = next(k for k, u in enumerate(self.tracks) if u is t)
                    del self.tracks[i]
                    if joined(net, groups) == groups:
                        removed += 1
                        again = True
                        break
                    self.tracks.insert(i, t)
        self.__dict__.pop("_cu_memo", None)
        if removed and set(self.check()) - before:       # (it broke something: put it all back)
            self.tracks = tracks0
            self.__dict__.pop("_cu_memo", None)
            return 0
        return removed

    def trim_overshoots(self) -> int:
        """a track end that runs on past the point where the next track of its net leaves it (a route taken off a
        fine-pitch pin's stub part-way) and meets nothing there: cut back to that point, so no rounded end pokes out
        past the corner. Returns how many ends it cut"""
        import math
        from shapely.geometry import LineString, Point, Polygon
        pn = self.pad_net()
        pad_at = {}
        for r, n, L, g, d in self.pad_shapes():
            pad_at.setdefault((pn.get((r, n)), L if not d else "*"), []).append(g)
        cut, groups = 0, {}                              # (each net's pieces by KiCad's end rule, before any cut)
        for k, t in enumerate(self.tracks):
            if t.get("user"):                            # (the script's own copper: as it drew it)
                continue
            for end in (0, -1):
                if len(t["pts"]) < 2:                    # (cut to nothing by its other end)
                    break
                e = Point(t["pts"][end])
                if any(math.dist(v["at"], t["pts"][end]) < 1e-3 for v in self.vias if v["net"] == t["net"]) or \
                        any(g.buffer(1e-3).contains(e) for key in ((t["net"], t["layer"]), (t["net"], "*"))
                            for g in pad_at.get(key, [])):
                    continue                             # (it ends on a pad or a via: a real end)
                others = [u for j, u in enumerate(self.tracks) if j != k and u["net"] == t["net"]
                          and u["layer"] == t["layer"] and len(u["pts"]) >= 2]
                if any(math.dist(q, t["pts"][end]) < 1e-3 for u in others for q in (u["pts"][0], u["pts"][-1])) or \
                        any(LineString(u["pts"]).distance(e) < 1e-3 for u in others) or \
                        any(Polygon(pz["poly"], pz.get("holes") or None).contains(e) for pz in self.pours
                            if pz["net"] == t["net"] and pz["layer"] == t["layer"]):
                    continue                             # (a joint: another track's end or middle, or its pour)
                line = LineString(t["pts"] if end == -1 else t["pts"][::-1])   # (the loose end last)
                at = [line.project(Point(q)) for u in others for q in (u["pts"][0], u["pts"][-1])
                      if line.distance(Point(q)) < 1e-3 and 1e-3 < line.length - line.project(Point(q)) < 2.0
                      and line.project(Point(q)) > 1e-3]           # (all overshoot: prune_spurs' to drop)
                if not at:
                    continue
                from shapely.ops import substring
                if t["net"] not in groups:
                    groups[t["net"]] = len(self.anchor_groups(t["net"]))
                old = list(t["pts"])
                new = [tuple(c) for c in substring(line, 0.0, max(at)).coords]
                t["pts"] = new if end == -1 else new[::-1]
                self.__dict__.pop("_cu_memo", None)
                if len(self.anchor_groups(t["net"])) > groups[t["net"]]:
                    t["pts"] = old                       # (it was a joint after all: the net would come apart)
                    self.__dict__.pop("_cu_memo", None)
                    continue
                cut += 1
        self.tracks = [t for t in self.tracks if len(t["pts"]) >= 2 and LineString(t["pts"]).length > 1e-3]
        self.__dict__.pop("_cu_memo", None)
        return cut

    def chamfer(self) -> int:
        """each 90-degree corner of a track turned into two 45-degree bends (a chamfer about twice the track's width),
        where the chamfer keeps the clearance to every other net: two tracks of one net and width that meet end to
        end, with nothing else there, count as one. Returns how many corners it chamfered"""
        import math
        from shapely.geometry import LineString, Point
        ends = {}
        for k, t in enumerate(self.tracks):
            if t.get("user"):                            # (the script's own copper: as it drew it)
                continue
            for i in (0, -1):
                ends.setdefault((t["net"], t["layer"], tuple(round(c, 4) for c in t["pts"][i])), []).append((k, i))
        joined, gone = [], set()                         # (two segments end to end: one polyline)
        pad_pts = [g for *_, g, _ in self.pad_shapes()]
        for (net, L, p), lst in ends.items():
            if len(lst) != 2 or any(k in gone for k, _ in lst) or lst[0][0] == lst[1][0]:
                continue
            (a, ia), (b, ib) = lst
            ta, tb = self.tracks[a], self.tracks[b]
            if abs(ta["w"] - tb["w"]) > 1e-6 or any(math.dist(v["at"], p) < 1e-3 for v in self.vias) or \
                    any(g.contains(Point(p)) for g in pad_pts):
                continue
            pa = ta["pts"] if ia == -1 else ta["pts"][::-1]
            pb = tb["pts"] if ib == 0 else tb["pts"][::-1]
            joined.append({**ta, "pts": list(pa) + list(pb[1:])})
            gone |= {a, b}
        tracks = [t for k, t in enumerate(self.tracks) if k not in gone] + joined
        fixed = self.copper(pours=False)
        lands = {}                                       # (where each net's other copper ends: a corner one lands in
        for u in tracks:                                 #  keeps its copper)
            lands.setdefault((u["net"], u["layer"]), []).append(u["pts"][0])
            lands[(u["net"], u["layer"])].append(u["pts"][-1])
        n = 0
        for t in tracks:
            if t.get("user"):
                continue
            others = [g for (nt, l), g in fixed.items() if l == t["layer"] and nt != t["net"]]
            near = lands.get((t["net"], t["layer"]), []) + [v["at"] for v in self.vias if v["net"] == t["net"]] + \
                [(g.centroid.x, g.centroid.y) for g in pad_pts]
            pts, out = list(t["pts"]), [t["pts"][0]]
            for a, b, c in zip(pts, pts[1:], pts[2:]):
                u1 = (b[0] - a[0], b[1] - a[1])
                u2 = (c[0] - b[0], c[1] - b[1])
                l1, l2 = math.hypot(*u1), math.hypot(*u2)
                if l1 < 1e-6 or l2 < 1e-6 or abs(u1[0] * u2[0] + u1[1] * u2[1]) > 1e-3 * l1 * l2:
                    out.append(b)                        # (not a right angle)
                    continue
                d = min(l1 / 2, l2 / 2, 2 * t["w"])
                if any(math.dist(q, b) < d + t["w"] for q in near):
                    out.append(b)                        # (something of its net lands at the corner)
                    continue
                p1 = (round(b[0] - u1[0] / l1 * d, 4), round(b[1] - u1[1] / l1 * d, 4))
                p2 = (round(b[0] + u2[0] / l2 * d, 4), round(b[1] + u2[1] / l2 * d, 4))
                cut = LineString([p1, p2]).buffer(t["w"] / 2, 8)
                if all(cut.distance(g) >= self.clear - 1e-6 for g in others):
                    out += [p1, p2]
                    n += 1
                else:
                    out.append(b)
            out.append(pts[-1])
            t["pts"] = out
        self.tracks = tracks
        self.__dict__.pop("_cu_memo", None)
        return n

    def prune_spurs(self, tracks: Optional[Sequence[int]] = None, rounds: int = 4) -> int:
        """trim the dangling ends of ``tracks`` (default: all) back to where they last lie inside other copper of
        their net, and drop tracks that reach nothing past their other end: a fine-pitch pad's way-out stub that the
        route joined part-way along, or a closing segment that overshot, otherwise leaves a spur KiCad reads as
        unconnected. Never splits a net: a net whose copper would end up in more pieces keeps its tracks as they were.
        Returns how many track ends it changed"""
        from shapely.geometry import LineString, Point
        from shapely.ops import substring, unary_union
        changed = 0
        idx = set(range(len(self.tracks)) if tracks is None else tracks)
        orig, before = None, {}                          # (the script's own copper: a loose tip cut back to where
        #                                                  the routing joined it - an escape stub - never dropped)
        for _ in range(rounds):
            loose = [(k, e) for k, e in self.loose_ends(only=sorted(i for i in idx if i < len(self.tracks)))]
            if not loose:
                break
            if orig is None:                             # (to put a net back if trimming it cut it in two: a scrap
                orig = [dict(t) for t in self.tracks]    # of pour can hang on a stub that looks loose)
                before = {n: len(self.islands(n)) for n in {self.tracks[k]["net"] for k, _ in loose}
                          if n in self.nets}
            pads = self.pad_shapes()
            pn = self.pad_net()
            drop = set()
            for k, end in loose:
                if k in drop:
                    continue
                t = self.tracks[k]
                net, L = t["net"], t["layer"]
                other = [g for (r, n, L_, g, d) in pads if pn.get((r, n)) == net and (L_ == L or d)]
                other += [Point(v["at"]).buffer(v["dia"] / 2, 16) for v in self.vias if v["net"] == net]
                other += [LineString(u["pts"]).buffer(u["w"] / 2, 12) for j, u in enumerate(self.tracks)
                          if j != k and j not in drop and u["net"] == net and u["layer"] == L]
                other += [g for g in [self.pour_fill(net, L)] if g is not None and not g.is_empty] \
                    if any(pz["net"] == net and pz["layer"] == L for pz in self.pours) else []
                inner = unary_union(other).buffer(-0.02) if other else None
                pts = list(t["pts"])
                if tuple(pts[0]) == tuple(end):
                    pts = pts[::-1]                      # the loose end last
                line = LineString(pts)
                hit = line.intersection(inner) if inner is not None and not inner.is_empty else None
                far = 0.0
                for g in (getattr(hit, "geoms", None) or ([hit] if hit is not None and not hit.is_empty else [])):
                    for c in getattr(g, "coords", []):
                        far = max(far, line.project(Point(c)))
                if far <= 1e-3:
                    if t.get("user"):                    # (the script's own track: kept, whole)
                        continue
                    drop.add(k)                          # it lies inside nothing of its net: the whole spur goes
                    changed += 1
                    continue
                if far >= line.length - 1e-4:
                    continue
                cut = substring(line, 0.0, far)
                new = [tuple(c) for c in cut.coords]
                if len(new) >= 2 and cut.length > 1e-3:
                    t["pts"] = new if tuple(pts[0]) == tuple(t["pts"][0]) else new[::-1]
                    changed += 1
                elif not t.get("user"):
                    drop.add(k)
                    changed += 1
            if drop:
                remap = {}
                keep = [t for j, t in enumerate(self.tracks) if j not in drop]
                for j in range(len(self.tracks)):
                    if j not in drop:
                        remap[j] = len(remap)
                self.tracks = keep
                idx = {remap[j] for j in idx if j in remap}
        if changed and before:
            worse = {n for n, k0 in before.items() if len(self.islands(n)) > k0}
            if worse:
                self.tracks = [t for t in self.tracks if t["net"] not in worse] + \
                              [t for t in orig if t["net"] in worse]
        return changed

    def anchor_groups(self, net: str, cu=None, pads=None, pn=None) -> List[List[Tuple[str, str]]]:
        """the pins of ``net`` grouped the way KiCad joins copper: a pad joins the tracks and zones it overlaps, a
        track another track or a via only where an END point lies inside it, a via what its centre lies in; two
        tracks that merely overlap do not join, and two pads of one part with the same number are separate pads
        (a switch's paired pins must be wired together). Largest group first"""
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
        cu = self.copper() if cu is None else cu
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        nodes = []                                       # (kind, layers, geometry, key)
        anchor_at = {}                                   # (a pad's anchor: its centre, as KiCad places it)
        for r, n, L, g, d in pads:
            if pn.get((r, n)) == net:
                nodes.append(("pad", set(self.layers) if d else {L}, g, (r, n)))
                anchor_at[len(nodes) - 1] = g.centroid
        for v in self.vias:
            if v["net"] == net:
                nodes.append(("via", set(self.layers), Point(v["at"]).buffer(v["dia"] / 2, 16), v["at"]))
        tr = [t for t in self.tracks if t["net"] == net]
        for k, t in enumerate(tr):
            nodes.append(("track", {t["layer"]}, LineString(t["pts"]).buffer(t["w"] / 2, 12), k))
        for (n, L), g in cu.items():
            if n == net and any(pz["net"] == n and pz["layer"] == L for pz in self.pours):
                for q in (getattr(g, "geoms", None) or [g]):
                    import shapely as _shp
                    _shp.prepare(q)                      # (a big polygon, asked about many small things)
                    nodes.append(("zone", {L}, q, None))
        par = list(range(len(nodes)))

        def root(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i

        def join(i, j):
            par[root(i)] = root(j)
        st = STRtree([g for _, _, g, _ in nodes])
        import shapely
        inner_ = {}

        def inside(j, pt):                               # (on the edge counts, as in KiCad: 5 um over the polygon
            return shapely.dwithin(nodes[j][2], pt, 0.005)   # that stands for a round end)
        def _overlap(big, g):                            # the area they share (a zone clipped to the pad first)
            if not shapely.intersects(big, g):
                return 0.0
            if shapely.contains(big, g):
                return g.area
            return shapely.clip_by_rect(big, *g.bounds).intersection(g).area
        for i, (kind, Ls, g, key) in enumerate(nodes):
            if kind == "track":
                pts_ = [Point(tr[key]["pts"][0]), Point(tr[key]["pts"][-1])]
            elif kind == "via":
                pts_ = [Point(key)]
            elif kind == "pad":                          # (a track or zone over a pad's centre joins it)
                pts_ = [anchor_at[i]]
            else:
                pts_ = []
            for pt in pts_:
                for j in st.query(pt):
                    j = int(j)
                    if j != i and nodes[j][1] & Ls and inside(j, pt):
                        join(i, j)
            if kind == "pad":                            # a pad joins a track or zone of its net it overlaps
                for j in st.query(g):
                    j = int(j)
                    if nodes[j][0] in ("zone", "track") and nodes[j][1] & Ls and _overlap(nodes[j][2], g) > 1e-4:
                        join(i, j)
        groups = {}
        for i, (kind, _, _, key) in enumerate(nodes):
            if kind == "pad" and key not in groups.get(root(i), []):
                groups.setdefault(root(i), []).append(key)
        return sorted(groups.values(), key=len, reverse=True)

    # outputs
    def parts(self, z0: float, models: bool = True, mask_color: str = "#1d6b3a") -> Dict[str, object]:
        """build123d parts at board bottom z0: "<name>_pcb" (FR4 with drills and holes), "<name>_copper_top" /
        "_copper_bottom" (35 um x oz copper under the mask), "<name>_pads_top" (the exposed pads), "<name>_parts"
        (the components' 3D models)"""
        import build123d as bd
        from shapely.geometry.polygon import orient
        from vendor_parts import kicad, CACHE
        key = getattr(self, "_route_key", None)
        if key and models:                               # the custom bodies (a connector drawn in the program) count too
            import hashlib
            sig = [(r, round(float(c["body"].volume), 2), [round(v, 2) for v in c["body"].bounding_box().size])
                   for r, c in sorted(self.comps.items()) if c.get("body") is not None]
            key = f"{key}_{hashlib.sha1(repr(sig).encode()).hexdigest()[:8]}"
        cdir = CACHE / "pcb_solids" / f"{self.name}_{key}_{z0:.3f}_{int(models)}_v7" if key else None
        names = [f"{self.name}_pcb", f"{self.name}_copper_top", f"{self.name}_pads_top", f"{self.name}_copper_bottom"] + \
            ([f"{self.name}_parts"] if models else [])
        if cdir is not None and all((cdir / f"{n}.brep").exists() for n in names):
            return {n: bd.import_brep(str(cdir / f"{n}.brep")) for n in names}

        from OCP.BRepCheck import BRepCheck_Analyzer

        def face(p):
            ring = lambda r: bd.Wire.make_polygon([bd.Vector(x, y, 0) for x, y in list(r.coords)[:-1]], close=True)
            return bd.Face(ring(p.exterior), [ring(r) for r in p.interiors])

        def solid(g, z, h):
            sh = []
            polys = list(getattr(g, "geoms", None) or [g])
            pinched = any(polys[i].intersects(polys[j]) for i in range(len(polys)) for j in range(i + 1, len(polys)))
            for p in polys:
                rings = [p.exterior] + list(p.interiors)
                pinched = pinched or any(rings[i].intersects(rings[j]) for i in range(len(rings))
                                         for j in range(i + 1, len(rings)))
            if pinched:
                # copper touching itself at a point (two islands at a corner, a hole at the outline): valid as a
                # polygon, but a non-manifold solid that meshing, and so the fast gap checks, cannot take: shrink
                # this net's copper 1 um to open the points
                q = g.buffer(-0.003, 4).buffer(0.002, 4)
                polys = list(getattr(q, "geoms", None) or [q])
            for p in polys:
                if p.is_empty or p.area < 1e-4:
                    continue
                # cutting tracks against pads leaves micron-long edges that mesh into zero-area triangles: drop
                # them (5 um, far below any fab's resolution)
                p = p.simplify(0.005, preserve_topology=True)
                if p.is_empty or p.geom_type != "Polygon":
                    continue
                p = orient(p, 1.0)                      # counter-clockwise: the face looks +z, extrudes up
                try:
                    f_ = face(p)
                except Exception:
                    # a ring the kernel will not take (simplify left a hole of two or three near-collinear points):
                    # drop holes under 0.01 mm2 and open the rest 2 um, as below
                    p = p.buffer(-0.002, 4).buffer(0.002, 4)
                    if p.geom_type != "Polygon":          # (orient() takes no MultiPolygon in shapely < 2.1)
                        continue
                    p = orient(p, 1.0)
                    from shapely.geometry import Polygon as _P
                    p = orient(_P(p.exterior, [r for r in p.interiors if _P(r).area > 0.01]), 1.0)
                    try:
                        f_ = face(p)
                    except Exception:
                        continue
                s_ = bd.extrude(f_, h, dir=(0, 0, 1))
                if not BRepCheck_Analyzer(s_.wrapped).IsValid():
                    # a hole touching the outline at a point (valid in shapely, not as a face): part them by 2 um
                    for q in (getattr(p.buffer(-0.002, 4).buffer(0.002, 4), "geoms", None) or
                              [p.buffer(-0.002, 4).buffer(0.002, 4)]):
                        if not q.is_empty and q.area >= 1e-4:
                            sh.append(bd.Pos(0, 0, z) * bd.extrude(face(orient(q, 1.0)), h, dir=(0, 0, 1)))
                    continue
                sh.append(bd.Pos(0, 0, z) * s_)
            return sh
        t_cu = self.oz * OZ_MM
        sub = solid(self.outline, z0, self.t)[0]
        drills = [(g.centroid.x, g.centroid.y, drill) for ref, num, L, g, drill in self.pad_shapes() if drill and L == "F"]
        drills += [(v["at"][0], v["at"][1], v["drill"]) for v in self.vias] + list(self.holes)
        for c in self.comps.values():                         # non-plated pegs
            for lst in c["pads"].values():
                for pad in lst:
                    if not pad["layers"] and pad["drill"]:
                        from shapely.geometry import Point
                        g = _pad_poly(pad, (c["at"][0], c["at"][1], c["rot"]))
                        drills.append((g.centroid.x, g.centroid.y, pad["drill"]))
        for x, y, d in drills:
            sub -= bd.Pos(x, y, z0 - 0.1) * bd.Cylinder(d / 2, self.t + 0.2, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        from shapely.geometry import Point as _Pt, LineString as _LS
        from shapely.ops import unary_union as _uu
        slots = []                                            # oval drills (USB-C shell stakes): a slot along the pad
        for c in self.comps.values():
            for lst in c["pads"].values():
                for pad in lst:
                    if pad.get("slot") and pad["layers"]:
                        g = _pad_poly(pad, (c["at"][0], c["at"][1], c["rot"]))
                        q = list(g.minimum_rotated_rectangle.exterior.coords)
                        e1, e2 = (q[0], q[1]), (q[1], q[2])
                        ln = max((e1, e2), key=lambda e_: math.dist(*e_))
                        ux, uy = ln[1][0] - ln[0][0], ln[1][1] - ln[0][1]
                        L = math.hypot(ux, uy)
                        w, h = min(pad["slot"]), max(pad["slot"])
                        cx, cy = g.centroid.x, g.centroid.y
                        k = (h - w) / 2 / L                   # (square ends: the shell stakes are flat-ended tabs)
                        slots.append(_LS([(cx - ux * k, cy - uy * k), (cx + ux * k, cy + uy * k)]).buffer(w / 2, cap_style=3))
        for sl in slots:
            sub -= solid(sl, z0 - 0.1, self.t + 0.2)[0]
        cu = self.copper()
        bores = _uu([_Pt(x, y).buffer(d / 2, 16) for x, y, d in drills] + slots or [_Pt(0, 0).buffer(0)])
        pads_f = _uu([g for r, n, l, g, d in self.pad_shapes() if l == "F"] or [_Pt(0, 0).buffer(0)])
        top = [s for (n, L), g in cu.items() if L == "F" for s in solid(g.difference(bores).difference(pads_f),
                                                                           z0 + self.t, t_cu)]
        pads_top = [s for s in solid(pads_f.difference(bores), z0 + self.t, t_cu)]   # the mask's openings: ENIG
        bot = [s for (n, L), g in cu.items() if L == "B" for s in solid(g.difference(bores), z0 - t_cu, t_cu)]
        out = {f"{self.name}_pcb": sub,
               f"{self.name}_copper_top": bd.Compound(children=top),
               f"{self.name}_pads_top": bd.Compound(children=pads_top),
               f"{self.name}_copper_bottom": bd.Compound(children=bot)}
        if models:
            ms = []
            for ref, c in self.comps.items():
                m = None
                if c["model"]:
                    try:
                        m = kicad(c["model"])
                    except Exception as e:                # (the STEP without this part's body: said so)
                        m = None
                        self.skip(f"{ref}'s 3D model in the STEP", f"{type(e).__name__}: {str(e)[:120]}")
                if m is None and c["body"] is not None:
                    m = c["body"]
                if m is None:
                    continue
                ox, oy, oz = c["model_offset"]
                if c.get("side", "F") == "B":                 # flipped about the footprint's y axis, under the board
                    ms.append(bd.Pos(c["at"][0], c["at"][1], z0 - t_cu) * bd.Rot(0, 0, c["rot"]) * bd.Rot(0, 180, 0) *
                              bd.Pos(ox, oy, oz) * m)
                else:
                    ms.append(bd.Pos(c["at"][0], c["at"][1], z0 + self.t + t_cu) * bd.Rot(0, 0, c["rot"]) * bd.Pos(ox, oy, oz) * m)
            out[f"{self.name}_parts"] = bd.Compound(children=ms)
        if cdir is not None:
            cdir.mkdir(parents=True, exist_ok=True)
            for n, shp in out.items():
                bd.export_brep(shp, str(cdir / f"{n}.brep"))
        return out

    def bom(self) -> List[Dict]:
        rows: Dict[Tuple[str, str, str], List[str]] = {}
        for ref, c in self.comps.items():
            rows.setdefault((c["value"], c["mpn"], c["fp"].split(":")[1]), []).append(ref)
        return [{"refs": sorted(v, key=lambda r: (re.sub(r"\d", "", r), int(re.sub(r"\D", "", r) or 0))), "qty": len(v),
                 "value": k[0], "mpn": k[1], "footprint": k[2],
                 "lcsc": next((self.comps[r].get("lcsc", "") for r in v if self.comps[r].get("lcsc")), "")}
                for k, v in sorted(rows.items(), key=lambda kv: kv[1][0])]

    def plot(self, png: str, scale: float = 8.0, layers: Sequence[str] = ("F", "B")):
        """top copper (red), bottom copper (blue), outline, holes, pads and reference designators; ``layers=("1",)``
        plots an inner layer (green) instead"""
        from PIL import Image, ImageDraw
        x0, y0, x1, y1 = self.outline.bounds
        W, H = int((x1 - x0) * scale) + 40, int((y1 - y0) * scale) + 40
        img = Image.new("RGB", (W, H), (24, 28, 32))
        dr = ImageDraw.Draw(img, "RGBA")
        tf = lambda x, y: (20 + (x - x0) * scale, 20 + (y1 - y) * scale)

        def fill(g, col):
            mask = Image.new("L", (W, H), 0)                 # holes stay holes (what is under them shows through)
            md = ImageDraw.Draw(mask)
            for p in (getattr(g, "geoms", None) or [g]):
                if p.is_empty or not hasattr(p, "exterior"):
                    continue
                md.polygon([tf(x, y) for x, y in p.exterior.coords], fill=col[3] if len(col) > 3 else 255)
                for ring in p.interiors:
                    md.polygon([tf(x, y) for x, y in ring.coords], fill=0)
            img.paste(Image.new("RGB", (W, H), col[:3]), (0, 0), mask)
        fill(self.outline, (30, 90, 50, 255))
        cu = self.copper()
        for (n, L), g in sorted(cu.items(), key=lambda kv: kv[0][1] == "F"):
            if L in layers:
                fill(g, {"B": (70, 120, 230, 150), "F": (225, 70, 50, 170)}.get(L, (60, 190, 110, 170)))
        for ref, num, L, g, drill in self.pad_shapes():
            if L == "F":
                fill(g, (230, 190, 90, 230))
            elif not drill:
                fill(g, (150, 190, 240, 200))
            if drill:
                c = g.centroid
                r = drill / 2 * scale
                px, py = tf(c.x, c.y)
                dr.ellipse([px - r, py - r, px + r, py + r], fill=(10, 10, 10, 255))
        for v in self.vias:
            px, py = tf(*v["at"])
            r = v["dia"] / 2 * scale
            dr.ellipse([px - r, py - r, px + r, py + r], fill=(220, 220, 220, 255))
        for x, y, d in self.holes:
            px, py = tf(x, y)
            r = d / 2 * scale
            dr.ellipse([px - r, py - r, px + r, py + r], fill=(0, 0, 0, 255), outline=(200, 200, 200, 255))
        for ref, c in self.comps.items():
            px, py = tf(*c["at"])
            dr.text((px + 4, py - 12), ref, fill=(255, 255, 255, 255) if c.get("side", "F") == "F" else (160, 200, 255, 255))
        img.save(png)
        return png


def from_netlist(path: str, outline, name: Optional[str] = None, at: Optional[Dict[str, tuple]] = None,
                 amps: Optional[Dict[str, float]] = None, pour: Optional[str] = "auto", **board_kw) -> Board:
    """a Board from a KiCad schematic's netlist (Eeschema: File > Export > Netlist, KiCad format, ``.net``): every
    part on its footprint with its value, MPN and LCSC number (from fields named MPN / Manufacturer_Part_Number /
    Part Number, and LCSC / JLCPCB / LCSC Part), every net of two or more pins. ``outline``: the board's corners, or
    (w, h) for a rectangle from (0, 0). ``at``: {ref: (x, y) or (x, y, rot[, side])} for parts the enclosure fixes;
    the rest have no position yet, for ``place_and_route``. ``amps``: {net: amps} for nets that carry current.
    ``pour``: the net poured on the bottom ("auto": GND, if there is one; None: none). Parts with no footprint
    (power symbols aside, which are not in a netlist) are listed in ``board.import_notes`` and left out"""
    from pathlib import Path as _P
    tree = _sx_parse(_P(path).read_text(encoding="utf-8"))
    if isinstance(outline, (tuple, list)) and len(outline) == 2 and not isinstance(outline[0], (tuple, list)):
        w, h = outline
        outline = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    b = Board(name or _P(path).stem, outline, **board_kw)
    b.import_notes = []
    at, amps = dict(at or {}), dict(amps or {})

    def val(node, key):
        f = _find(node, key)
        return f[0][1] if f and len(f[0]) > 1 and isinstance(f[0][1], str) else ""

    def fields(comp):
        out = {}
        for fs in _find(comp, "fields"):
            for f in _find(fs, "field"):
                nm = val(f, "name")
                v = next((x for x in f[1:] if isinstance(x, str)), "")
                if nm:
                    out[nm.lower()] = v
        for pr in _find(comp, "property"):
            nm, v = val(pr, "name"), val(pr, "value")
            if nm:
                out.setdefault(nm.lower(), v)
        return out
    comps = [c for cs in _find(tree, "components") for c in _find(cs, "comp")]
    have = set()
    for c in comps:
        ref, value, fp = val(c, "ref"), val(c, "value"), val(c, "footprint")
        if not fp:
            b.import_notes.append(f"{ref} ({value}) has no footprint in the schematic: left out")
            continue
        fl = fields(c)
        mpn = next((fl[k] for k in ("mpn", "manufacturer_part_number", "part number", "manufacturer part number",
                                    "mfr part", "mfr. part #") if fl.get(k)), "")
        maker = next((fl[k] for k in ("manufacturer", "mfr", "mfr.", "manufacturer_name") if fl.get(k)), "")
        lcsc = next((fl[k] for k in ("lcsc", "lcsc part", "lcsc part #", "jlcpcb", "jlcpcb part #", "jlc") if fl.get(k)), "")
        p = at.get(ref)
        kw = {}
        if p is not None and len(p) > 3:
            kw["side"] = p[3]
        src = _find(c, "libsource")                      # (the symbol the schematic placed it with: its pins by name)
        lib_, part_ = (val(src[0], "lib"), val(src[0], "part")) if src else ("", "")
        if lib_ and part_:
            kw["symbol"] = f"{lib_}:{part_}"
        try:
            b.place(ref, fp, None if p is None else (p[0], p[1]), p[2] if p is not None and len(p) > 2 else 0.0, value,
                    f"{maker} {mpn}".strip() if mpn else "", lcsc=lcsc, **kw)
        except Exception as e:                           # (a symbol KiCad's library lacks now: the part without it,
            kw.pop("symbol", None)                       #  and the critic says so)
            b.import_notes.append(f"{ref}: its symbol {lib_}:{part_} not found ({type(e).__name__}): placed without it")
            b.place(ref, fp, None if p is None else (p[0], p[1]), p[2] if p is not None and len(p) > 2 else 0.0, value,
                    f"{maker} {mpn}".strip() if mpn else "", lcsc=lcsc, **kw)
        have.add(ref)
    for ns in _find(tree, "nets"):
        for n in _find(ns, "net"):
            nm = val(n, "name").lstrip("/")
            pins = [(val(x, "ref"), val(x, "pin")) for x in _find(n, "node")]
            pins = [(r, q) for r, q in pins if r in have]
            if len(pins) < 2 or nm.lower().startswith("unconnected-"):
                continue
            b.net(nm, pins, amps=amps.get(nm, 0.1))
    if pour == "auto":
        pour = next((n for n in ("GND", "GNDD", "DGND", "0") if n in b.nets), None)
    if pour:
        b.pour(pour, "B", [tuple(q) for q in b.outline.exterior.coords[:-1]])
    return b


GROUND_NET = re.compile(r"^(A|D|P|S)?GND\w*$|^VSS\w*$|^0V$|^GROUND$|^EARTH$", re.I)


def severity(probs: Sequence[str]) -> int:
    """a routing's problems weighed: a short 20, a clearance slip 5, a split net 5, anything else 1"""
    w = 0
    for p_ in probs:
        if re.match(r"\S+\.Cu: .+ short", p_):
            w += 20
        elif re.match(r"\S+\.Cu: .+ mm apart", p_) or "split" in p_:
            w += 5
        else:
            w += 1
    return w


PROGRESS = None    # a callable given {"kind": "tries", ...} and {"kind": "try", ...} as place_and_route goes

# ---- the live run: every step as it happens, for the viewer (out/live.jsonl, one JSON object a line) ----
LIVE: Optional[str] = None   # the file the run's events go to (pcb_view.Progress sets it); None: no events
_LIVE_TRY: Optional[int] = None   # the place-and-route try the events belong to (each try's own process too)
_LIVE_DUE: Dict[str, float] = {}


def live(kind: str, **ev) -> None:
    """one event of the live run, appended to ``LIVE``: {"t": unix time, "kind": ..., "try": k, ...}. Tries in
    their own processes write to the same file (a line at a time, appended); a viewer's trouble never stops a run"""
    if LIVE is None:
        return
    import json
    import time
    try:
        line = json.dumps({"t": round(time.time(), 3), "kind": kind, "try": _LIVE_TRY, **ev},
                          separators=(",", ":"), default=lambda o: float(o) if hasattr(o, "__float__") else str(o))
        with open(LIVE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def say(text: str, ref: Optional[str] = None, net: Optional[str] = None) -> None:
    """a word from whoever drives the run (the agent, or the board script) to the engineer watching the viewer: a line
    in the run log tagged "ai", and, with ``ref`` (a part) or ``net``, the AI's cursor or a callout at it on the board.
    ``prometheus say out/ "text" --at U1`` does the same from the command line, during a run or after it"""
    live("say", text=str(text)[:600], ref=ref, net=net)


def live_due(key: str, every: float) -> bool:
    """at most one event of ``key`` every ``every`` seconds (the annealer's thousands of moves: a few frames)"""
    import time
    if LIVE is None:
        return False
    now = time.time()
    if now - _LIVE_DUE.get(key, 0.0) < every:
        return False
    _LIVE_DUE[key] = now
    return True


def _live_cu(items) -> list:
    """tracks and vias, compact: [net, layer, width, points] and [net, x, y, diameter]"""
    return [[t["net"], t["layer"], round(t["w"], 3), [[round(p[0], 3), round(p[1], 3)] for p in t["pts"]]]
            if "pts" in t else [t["net"], round(t["at"][0], 3), round(t["at"][1], 3), round(t["dia"], 3)]
            for t in items]


def live_copper(board: "Board", net: Optional[str] = None) -> None:
    """the copper as it stands: one net's (after it is routed), or the whole board's"""
    if LIVE is None:
        return
    tr = [t for t in board.tracks if net is None or t["net"] == net]
    vi = [v for v in board.vias if net is None or v["net"] == net]
    live("copper", net=net, tracks=_live_cu(tr), vias=_live_cu(vi))


def live_state(board: "Board", note: str = "", probs: Optional[Sequence[str]] = None) -> None:
    """the whole board as it stands, for a viewer that joined late or a step that changed much at once: every part's
    place, all the copper, the metrics"""
    if LIVE is None:
        return
    live("places", phase=note or "state", places={r: [c["at"][0], c["at"][1], c["rot"], c.get("side", "F")]
                                                   for r, c in board.comps.items() if not c.get("unplaced")})
    live_copper(board)
    live_metrics(board, probs, note=note)


def live_metrics(board: "Board", probs: Optional[Sequence[str]] = None, note: str = "") -> dict:
    """where the board stands: parts placed, nets joined, the length still to route (each split net's pieces joined
    by their shortest pad-to-pad links, as a ratsnest), tracks and vias; sent as a "metrics" event"""
    import math
    m = {"parts": len(board.comps), "placed": sum(1 for c in board.comps.values() if not c.get("unplaced")),
         "tracks": len(board.tracks), "vias": len(board.vias), "nets": 0, "routed": 0, "ratsnest": 0.0,
         "unrouted": [], "note": note}
    if probs is not None:
        m["problems"] = len(probs)
    if LIVE is None:
        return m
    try:
        if m["placed"] == m["parts"]:
            pads, pn, cu = board.pad_shapes(), board.pad_net(), board.copper()
            at = {(r, n): (g.centroid.x, g.centroid.y) for r, n, L, g, d in pads}
            for net, d in board.nets.items():
                if len(d["pins"]) < 2:
                    continue
                m["nets"] += 1
                isl = board.islands(net, cu=cu, pads=pads, pn=pn)
                if len(isl) <= 1:
                    m["routed"] += 1
                    continue
                m["unrouted"].append(net)
                pts = [[at[tuple(p)] for p in g if tuple(p) in at] for g in isl]
                pts = [g for g in pts if g]
                done, tot = [pts[0]], 0.0                # (Prim's tree over the pieces, pad to nearest pad)
                rest = pts[1:]
                while rest:
                    best = min(((min(math.dist(a, b) for a in sum(done, []) for b in g), i) for i, g in enumerate(rest)))
                    tot += best[0]
                    done.append(rest.pop(best[1]))
                m["ratsnest"] += tot
            m["ratsnest"] = round(m["ratsnest"], 1)
    except Exception:
        pass
    live("metrics", **m)
    return m


def _progress(**ev):
    if PROGRESS is not None:
        try:
            PROGRESS(ev)
        except Exception:                                # (a viewer's trouble never stops a run)
            pass


_NO_LATEST = False


# ---- KiCad board file ------------------------------------------------------------------------------------------------
class _Q(str):
    """a quoted s-expression atom"""


def _sx_parse(text: str):
    """s-expression -> nested lists, keeping which atoms were quoted (so it can be written back the same)"""
    stack, cur = [], []
    for t in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text):
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(_Q(re.sub(r'\\(.)', lambda m: "\n" if m.group(1) == "n" else m.group(1), t[1:-1]))
                       if t.startswith('"') else t)
    return cur[0] if cur else []


def _sx_atom(v) -> str:
    if isinstance(v, _Q):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


def _sx_dump(node, ind: int = 0) -> str:
    if not isinstance(node, list):
        return _sx_atom(node)
    atoms = [v for v in node if not isinstance(v, list)]
    subs = [v for v in node if isinstance(v, list)]
    head = "(" + " ".join(_sx_atom(v) for v in atoms)
    if not subs:
        return head + ")"
    if all(not any(isinstance(w, list) for w in s) for s in subs) and len(subs) <= 3 and node[0] not in (
            "footprint", "pad", "zone", "kicad_pcb", "pts", "property"):
        return head + " " + " ".join(_sx_dump(s) for s in subs) + ")"      # (at 1 2) (size 3 4) on one line
    pad = "\t" * (ind + 1)
    return head + "\n" + "\n".join(pad + _sx_dump(s, ind + 1) for s in subs) + "\n" + "\t" * ind + ")"


def _uuid(*key) -> _Q:
    import uuid
    return _Q(str(uuid.uuid5(uuid.NAMESPACE_URL, "prometheus-pcb/" + "/".join(str(k) for k in key))))


@__import__("functools").lru_cache(maxsize=None)        # (probed once a run: it starts a Python each time)
def _pcbnew_python() -> str:
    """a Python that can import KiCad's pcbnew: $PCBNEW_PYTHON, else the first of python3, python3.12, ... that has it
    (KiCad's Ubuntu package builds pcbnew for the system Python, which need not be the one running the harness)"""
    import os
    import shutil
    import subprocess
    if os.environ.get("PCBNEW_PYTHON"):
        return os.environ["PCBNEW_PYTHON"]
    for exe in ("python3", "python3.12", "python3.11", "python3.10", "/usr/bin/python3.12", "/usr/bin/python3",
                "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"):
                                                         # (macOS: KiCad's own Python, inside the app)
        path = shutil.which(exe) or (exe if os.path.exists(exe) else None)
        if path and subprocess.run([path, "-c", "import pcbnew"], capture_output=True).returncode == 0:
            return path
    return "python3"


def kicad_version() -> Optional[str]:
    """the installed KiCad's version (``kicad-cli version``; 9 or newer reads the exported board), or None when there is none"""
    import shutil
    import subprocess
    exe = shutil.which("kicad-cli")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


KICAD_INSTALL = {"linux": "sudo add-apt-repository ppa:kicad/kicad-9.0-releases && sudo apt install kicad "
                          "(or your distribution's kicad package, version 9 or newer)",
                 "darwin": "brew install --cask kicad (found in /Applications/KiCad without a PATH entry)",
                 "win32": "the installer from kicad.org (found in Program Files\\KiCad without a PATH entry)"}


# ---- the rest of the harness, in its own modules: pcb.<name> reaches each (``pcb.autoplace``, ``from pcb import
# route``) as before. Looked up on first use, so pcb_route / pcb_place / pcb_export may be imported in any order ---
_MOVED = {
    **dict.fromkeys([
        '_crowded', '_cxy', '_drill_gap', '_emit', '_escape', '_join_islands', '_mrr_sides', '_route_board',
        '_route_net', '_route_once', '_routes_json', '_routes_load', '_second_pass', '_shift', '_wavefront',
        '_wavefront_windowed', 'auto_grid', 'autoroute', 'cached_route', 'drop_pours', 'mark_user_copper',
        'neck_vias', 'nudge_vias', 'placement', 'repair', 'reuse_routes', 'route', 'route_board', 'route_jobs',
        'tie_to_exposed_pad', 'tune_pairs'],
        "pcb_route"),
    **dict.fromkeys([
        '_align', '_chips', '_decap_target', '_one_try', '_pin_name', '_try_worker', 'autoplace', 'implied_near',
        'near_gaps', 'near_misses', 'near_target', 'place_and_route'],
        "pcb_place"),
    **dict.fromkeys([
        'NET_COLORS', '_FP_DROP', '_K8_DROP', '_K8_LAYERS', '_KICAD_LAYERS', '_fp_silk', '_fractured', '_graphic',
        '_kicad_footprint', '_place_refs', '_text_poly', 'export_gerbers', 'export_kicad', 'export_kicad8',
        'export_step', 'kicad_drc', 'kicad_models', 'pack', 'place_refs', 'render_kicad', 'schematic',
        'silkscreen'],
        "pcb_export"),
}


def __getattr__(name: str):
    mod = _MOVED.get(name)
    if mod is None:
        raise AttributeError(f"module 'pcb' has no attribute {name!r}")
    import importlib
    return getattr(importlib.import_module(mod), name)


def __dir__():
    return sorted([*globals(), *_MOVED])
