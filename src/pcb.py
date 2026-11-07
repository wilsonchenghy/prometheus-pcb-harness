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

KICAD_FP_RAW = "https://gitlab.com/kicad/libraries/kicad-footprints/-/raw/master/"
LOCAL_FP = Path(__file__).parent / "pcb_footprints"   # project footprints KiCad's library lacks: <Lib>.pretty/<Name>.kicad_mod
CU_RHO = 1.72e-5            # ohm mm (copper, 20 C)
ROUTER_VERSION = 8          # in route_jobs' checkpoint key: bump when the router changes what it would produce
HOLE_CLEAR = 0.25           # copper to a bare (non-plated) hole's wall, mm: KiCad's default hole clearance
HOLE_GAP = 0.25             # mm between drill walls (a common fab minimum)
EDGE = 0.25                 # mm from copper (tracks, vias) to the board edge
MIN_FILL = 0.1              # mm: a pour's narrowest copper (KiCad's zone min_thickness)
OZ_MM = 0.0347              # 1 oz/ft^2 copper thickness
_FP: Dict[str, Dict] = {}


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
    from vendor_parts import CACHE
    return CACHE / "kicad_fp" / f"{fp}.kicad_mod"


class FootprintNotFound(ValueError):
    """a footprint name KiCad's library does not have; ``suggestions`` the closest names it does"""

    def __init__(self, name: str, suggestions: Sequence[str] = ()):
        self.name, self.suggestions = name, list(suggestions)
        lib = name.split(":")[0]
        hint = f"; did you mean: {', '.join(lib + ':' + s_ for s_ in self.suggestions)}" if self.suggestions else ""
        super().__init__(f"footprint {name} is not in KiCad's library{hint} (the library: "
                         f"https://gitlab.com/kicad/libraries/kicad-footprints/-/tree/master/{lib}.pretty)")


def _close_footprints(lib: str, fp: str, n: int = 5) -> List[str]:
    """the names in KiCad's footprint library ``lib`` closest to ``fp`` (its file list, fetched once and cached)"""
    import difflib
    import json
    import urllib.parse
    import urllib.request
    from vendor_parts import CACHE
    cache = CACHE / "kicad_fp_lists" / f"{lib}.json"
    names = None
    if cache.exists():
        names = json.loads(cache.read_text())
    else:
        try:
            names, page = [], 1
            while page < 30:
                q = urllib.parse.urlencode({"path": f"{lib}.pretty", "per_page": 100, "page": page})
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
    """fetch footprint "Lib:Name" from KiCad's library into the cache (the project library needs no fetch)"""
    lib, fp = name.split(":")
    dst = _fp_file(name)
    if not dst.exists() or dst.stat().st_size < 200:
        import time
        import urllib.error
        import urllib.request
        dst.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):                         # (GitLab answers 429 to a burst: wait and try again)
            try:
                with urllib.request.urlopen(f"{KICAD_FP_RAW}{lib}.pretty/{fp}.kicad_mod", timeout=60) as r:
                    data = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:                        # (a name the library does not have: say so, with the
                    raise FootprintNotFound(name, _close_footprints(lib, fp)) from None   # nearest it does)
                if e.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                time.sleep(float(e.headers.get("Retry-After") or 0) or 1.5 * 2 ** attempt)
        if not data.lstrip().startswith(b"(footprint") and not data.lstrip().startswith(b"(module"):
            raise FileNotFoundError(f"no KiCad footprint {name}")
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
_FP_NAME = re.compile(r'["\']([A-Za-z0-9_.\-]+:[A-Za-z0-9_.,+\-]+)["\']')


def _prefetch_callers():
    """on the first footprint the cache lacks, fetch every footprint named in the running program's source files
    (the main script and the modules it imported from beside it) in parallel, so a cold board waits once, not once
    per footprint"""
    import sys
    files = []
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None) or ""
        if f.endswith(".py") and "site-packages" not in f and f not in _SCANNED and Path(f).name != "pcb.py":
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
class Board:
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

    def loose_ends(self, cu=None, pads=None, pn=None, only: Optional[Sequence[int]] = None):
        """[(track index, end point)] for each track end that lies inside no copper of its net (a pad, a via,
        another track, its pour): KiCad joins items by their end points, so a track that only overlaps a pad's edge
        or rounded corner reads as unconnected there, and one ending on nothing is a dangling stub. ``only``: the
        tracks to test"""
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
        cu = self.copper() if cu is None else cu
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        import numpy as np
        import shapely
        items = [(L, pn.get((r, n)), g, i) for i, (r, n, L, g, d) in enumerate(pads)] + \
                [(L, v["net"], Point(v["at"]).buffer(v["dia"] / 2, 16), ("v", k))
                 for k, v in enumerate(self.vias) for L in self.layers] + \
                [(t["layer"], t["net"], self._item_shape(t), ("t", k)) for k, t in enumerate(self.tracks)]
        poured = {(pz["net"], pz["layer"]) for pz in self.pours}
        zones = {key: g for key, g in cu.items() if key in poured and g is not None and not g.is_empty}
        thru = {i for i, (r, n, L, g, d) in enumerate(pads) if d}
        ks = list(range(len(self.tracks)) if only is None else only)
        ends = [(k, e) for k in ks for e in (self.tracks[k]["pts"][0], self.tracks[k]["pts"][-1])]
        if not ends:
            return []
        good = np.zeros(len(ends), bool)
        pts = shapely.points(np.array([e for _, e in ends], float))
        if items:                                        # pads, vias, other tracks: one query for every end
            st = STRtree([g for _, _, g, _ in items])
            ia, ib = st.query(pts, predicate="dwithin", distance=0.005)
            for a, b in zip(ia.tolist(), ib.tolist()):
                if good[a]:
                    continue
                k = ends[a][0]
                t = self.tracks[k]
                L, n, _, tag = items[b]
                if n == t["net"] and tag != ("t", k) and (L == t["layer"] or tag in thru):
                    good[a] = True
        for (n, L), g in zones.items():                  # its pour (prepared: a big polygon, many ends)
            sel = np.array([not good[i] and self.tracks[k]["net"] == n and self.tracks[k]["layer"] == L
                            for i, (k, _) in enumerate(ends)])
            if sel.any():
                shapely.prepare(g)
                good[sel] |= shapely.dwithin(g, pts[sel], 0.005)
        return [(k, tuple(e)) for (k, e), ok in zip(ends, good) if not ok]

    VIA_AMPS_PER_MM = 1.0 / 0.3      # A per mm of drill: a 0.3 mm via, 25 um of plating, ~1 A (IPC-2152, a short
    #                                  barrel; a conservative figure, as the house calculators give for 10-20 C)

    def via_current(self, min_amps: float = 1.0) -> List[Tuple[str, List[Tuple[float, float]], float, float]]:
        """where a net carrying current changes layer through too few vias: for each net of at least ``min_amps``
        and no pour (a plane spreads it over its many drops), the vias that are the only way between two parts of
        its copper each holding a part on the net's power path (a connector, fuse, inductor, transistor, diode,
        relay or battery: a branch to a chip, a resistor or a capacitor draws its own share, unknown, and is not
        judged), whose drills together carry less than the net's current (``VIA_AMPS_PER_MM`` per mm of drill). [(net, via centres, their amps, the
        net's)]"""
        from shapely.geometry import Point
        out = []
        pads = self.pad_shapes()
        pn = self.pad_net()
        vias0 = list(self.vias)
        try:
            self.vias = []                               # (each layer's copper of each net without its vias, no
            cu = self.copper(pours=False)                #  pours: poured nets are not judged; the memo is keyed by
        finally:                                         #  the vias as well, so the board's own stays)
            self.vias = vias0
        poured = {pz["net"] for pz in self.pours}
        for net, d in self.nets.items():
            amps = d.get("amps") or 0.0
            vs = [v for v in self.vias if v["net"] == net]
            if amps < min_amps or not vs or net in poured:   # (a plane spreads the current over its many drops:
                continue                                      #  each carries its own pin's share, not the net's)
            isl = []                                     # (layer, polygon) islands of the net
            for L in self.layers:
                g = cu.get((net, L))
                if g is not None and not g.is_empty:
                    isl += [(L, q) for q in getattr(g, "geoms", [g])]
            if len(isl) < 2:
                continue
            parent = list(range(len(isl)))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def at(pt, L=None):
                return [k for k, (L_, q) in enumerate(isl) if (L is None or L_ == L) and q.intersects(pt)]
            for r, n, L, g, dr in pads:                  # (a plated hole joins the layers: no via needed there)
                if dr and pn.get((r, n)) == net:
                    ks = at(g.centroid)
                    for a, b in zip(ks, ks[1:]):
                        parent[find(a)] = find(b)
            links = {}                                   # (the vias between each pair of island groups: by the via's
            for v in vs:                                 #  ring, as copper joins - its centre may be just off a track)
                ks = at(Point(v["at"]).buffer(v["dia"] / 2 - 0.01, 12))
                for a, b in zip(ks, ks[1:]):
                    links.setdefault(tuple(sorted((find(a), find(b)))), []).append(v)
            pins = {}                                    # (the current-carrying pins on each island group)
            low_ = self.pin_loads(net)                   # (a pin given a small load draws no more than that)
            for r, n, L, g, dr in pads:
                if pn.get((r, n)) == net and not r.upper().startswith("C") and (r, n) not in low_:
                    for k in at(g.centroid, None if dr else L):
                        pins.setdefault(find(k), set()).add((r, n))
            for (a, b), lst in links.items():
                if a == b:
                    continue
                par = {k: k for k in set(find(k) for k in range(len(isl)))}   # (the net joined without these vias)

                def f2(x):
                    while par[x] != x:
                        x = par[x]
                    return x
                for (c, e), l2 in links.items():
                    if l2 is not lst and c != e:
                        par[f2(c)] = f2(e)
                if f2(a) == f2(b):
                    continue                             # (another way round: not the only way)
                side_a = {k for k in par if f2(k) == f2(a)}
                pa_ = set().union(*[pins.get(k, set()) for k in side_a])
                pb_ = set().union(*[pins.get(k, set()) for k in par if k not in side_a])
                power = lambda ps: any(re.match(r"(J|P|F|L|Q|K|BT|D)\d", r_.upper())
                                       and "LED" not in self.comps[r_]["fp"].upper() for r_, _ in ps)
                if not power(pa_) or not power(pb_):     # (the path of the net's current runs between connectors,
                    continue                             #  fuses, inductors, switches, diodes: a branch to a chip or a
                    #                                      resistor draws its own share, unknown, not judged here)
                cap = sum(v["drill"] * self.VIA_AMPS_PER_MM for v in lst)
                if cap + 1e-9 < amps:
                    out.append((net, [tuple(v["at"]) for v in lst], round(cap, 2), amps))
        return out

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

    # checks
    def islands(self, net: str, cu=None, pads=None, pn=None) -> List[List[Tuple[str, str]]]:
        """the pins of ``net`` grouped by the copper that joins them (each layer's pieces, joined at vias and plated
        holes), largest group first: one group when the net is whole"""
        from shapely.geometry import Point
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        if cu is None and not any(pz["net"] == net for pz in self.pours):
            from shapely.ops import unary_union         # (no pour: its own copper is all there is, no need to carve
            own: Dict[str, list] = {}                    # the whole board's pours: the router asks after every net)
            for ref, num, L, g, _ in pads:
                if pn.get((ref, num)) == net:
                    own.setdefault(L, []).append(g)
            for t in self.tracks:
                if t["net"] == net:
                    own.setdefault(t["layer"], []).append(self._item_shape(t))
            for v in self.vias:
                if v["net"] == net:
                    for L in self.layers:
                        own.setdefault(L, []).append(self._item_shape(v))
            cu = {(net, L): unary_union(gs) for L, gs in own.items()}
        cu = self.copper() if cu is None else cu
        comps = []                                       # (layer, geometry) islands
        for L in self.layers:
            g = cu.get((net, L))
            if g is None or g.is_empty:
                continue
            for part in (getattr(g, "geoms", None) or [g]):
                comps.append((L, part))
        parent = list(range(len(comps)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        links = [Point(v["at"]) for v in self.vias if v["net"] == net] + \
                [g.centroid for ref, num, L, g, drill in pads if drill and pn.get((ref, num)) == net]
        from shapely.strtree import STRtree
        tree = STRtree([g for _, g in comps])            # (a tree query within 10 um, not a buffer of each pour per
        for pt in links:                                 # point: that was nine tenths of the check's time)
            hit = sorted(int(i) for i in tree.query(pt, predicate="dwithin", distance=0.01))
            for i in hit[1:]:
                parent[root(i)] = root(hit[0])
        where = {}
        for ref, num, L, g, _ in pads:
            if pn.get((ref, num)) != net:
                continue
            rp = g.representative_point()
            for i in tree.query(rp, predicate="intersects"):
                if comps[int(i)][0] == L:
                    where.setdefault((ref, num), set()).add(root(int(i)))
        groups, seen = {}, {}
        for rs in where.values():
            for r_ in rs:
                seen[r_] = seen.get(r_, 0) + 1
        for pin, rs in where.items():                   # a pin with several pads (a switch, a shield) joins through any
            groups.setdefault(max(rs, key=lambda r_: (seen[r_], -r_)), []).append(pin)
        return sorted(groups.values(), key=len, reverse=True)

    NECK_LEN = 1.5      # mm: a narrower track this short entering a pad smaller than the full width is a pin entry

    def _pin_neck(self, t, need, pads, pn) -> bool:
        """A short track narrower than its current needs, ending in a pad of its own net that is itself narrower than
        that width (a 2 A trace necking down into a 0.4 mm TSSOP pin): the pad limits the current there anyway, as in
        KiCad's and every router's pin entry, so it is not a narrow track."""
        from shapely.geometry import Point
        if t.get("neck"):                                 # (a via drop as wide as its pad: drop() marks it)
            return True
        L = sum(math.dist(a, b) for a, b in zip(t["pts"], t["pts"][1:]))
        if L > self.NECK_LEN:
            return False
        for end in (t["pts"][0], t["pts"][-1]):
            p = Point(end)
            for ref, num, lay, pg, _ in pads:
                if lay != t["layer"] or pn.get((ref, num)) != t["net"] or not pg.buffer(1e-3).contains(p):
                    continue
                mrr = pg.minimum_rotated_rectangle
                xs, ys = mrr.exterior.coords.xy
                sides = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(2)]
                if min(sides) < need - 1e-6:
                    return True
        return False

    def check(self) -> List[str]:
        import shapely
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import unary_union
        probs: List[str] = []
        cu = self.copper()
        pn = self.pad_net()
        pads = self.pad_shapes()
        for k in getattr(self, "keepouts", []):            # nothing copper in a keep-out
            z = Polygon(k["poly"])
            hit = sorted({n for (n, L), g in cu.items() if L in k["layers"] and g.intersection(z).area > 0.01})
            if hit:
                probs.append(f"copper of {', '.join(hit)} inside the keep-out{' (' + k['why'] + ')' if k['why'] else ''}")
        for n, d in self.nets.items():                      # every pin exists
            for ref, num in d["pins"]:
                if ref not in self.comps or num not in self.comps[ref]["pads"]:
                    probs.append(f"net {n}: {ref} has no pad {num!r} on its footprint {self.comps.get(ref, {}).get('fp')}")
        # connectivity: per net, union per layer, layers joined at vias and plated holes
        # (and again by KiCad's rule, which joins a track only where its end lies inside the other item)
        for n in self.nets:
            gs = self.islands(n, cu, pads, pn)
            how = ""
            if len(gs) <= 1:
                gs = self.anchor_groups(n, cu, pads, pn)
                how = " where track ends land (KiCad's rule; the copper itself overlaps)"
            if len(gs) > 1:
                gs = sorted(gs, key=len)
                probs.append(f"net {n} is split in {len(gs)}{how}: " +
                             " | ".join(", ".join(f"{r}.{p}" for r, p in g[:6]) for g in gs[:3]))
        # shorts / clearance between different nets on a layer (pad to pad inside one footprint is the maker's land
        # pattern: exempt)
        from shapely.geometry import LineString as _LS
        for L in self.layers:
            routed: Dict[str, list] = {}
            padg: Dict[str, Dict[str, list]] = {}
            pourg: Dict[str, list] = {}
            for t in self.tracks:
                if t["layer"] == L:
                    routed.setdefault(t["net"], []).append(self._item_shape(t))
            for v in self.vias:
                routed.setdefault(v["net"], []).append(self._item_shape(v))
            pcu = cu
            for pz in self.pours:
                if pz["layer"] == L:
                    g = pcu.get((pz["net"], L))
                    if g is not None:
                        pourg.setdefault(pz["net"], []).append(g.intersection(Polygon(pz["poly"], pz.get("holes") or None)))
            for ref, num, l, g, _ in pads:
                if l == L:
                    padg.setdefault(pn.get((ref, num), f"~{ref}.{num}"), {}).setdefault(ref, []).append(g)
            # every pair of copper items of different nets within the clearance, by bulk queries of STR trees (pad to
            # pad of one part exempt; a pour enters as its outline's segments, cheap to measure, plus a test for an
            # item lying wholly inside it); then each net pair's nearest offence is reported
            from shapely.strtree import STRtree
            import numpy as _npc
            el_g, el_n, el_r = [], [], []
            for n, lst in routed.items():
                for g in lst:
                    el_g.append(g), el_n.append(n), el_r.append(None)
            for n, by in padg.items():
                for ref, lst in by.items():
                    for g in lst:
                        el_g.append(g), el_n.append(n), el_r.append(ref)
            pieces = [(n, q) for n, lst in pourg.items() for g in lst for q in (getattr(g, "geoms", None) or [g])
                      if not q.is_empty and q.geom_type == "Polygon"]
            names = sorted(set(el_n) | {n for n, _ in pieces})
            ids = {n: i for i, n in enumerate(names)}
            unn = _npc.array([n.startswith("~") for n in names] or [False])
            g_s = _npc.array(el_g, dtype=object)
            n_s = _npc.array([ids[n] for n in el_n], dtype=int)
            rmap = {r: i for i, r in enumerate(sorted({r for r in el_r if r is not None}))}
            r_s = _npc.array([rmap[r] if r is not None else -1 for r in el_r], dtype=int)
            segs, n_seg = [], []
            for n, q in pieces:
                for ring in [q.exterior, *q.interiors]:
                    c = _npc.asarray(ring.coords)
                    if len(c) >= 2:
                        segs.append(shapely.linestrings(_npc.stack([c[:-1], c[1:]], axis=1)))
                        n_seg.append(_npc.full(len(c) - 1, ids[n]))
            g_p = _npc.concatenate(segs) if segs else _npc.array([], dtype=object)
            n_p = _npc.concatenate(n_seg) if n_seg else _npc.array([], dtype=int)
            worst: Dict[Tuple[str, str], Tuple[float, object, object]] = {}

            def note(ga_, gb_, na_, nb_):
                ok = (na_ != nb_) & ~(unn[na_] & unn[nb_])
                if not ok.any():
                    return
                ga_, gb_, na_, nb_ = ga_[ok], gb_[ok], na_[ok], nb_[ok]
                dd = shapely.distance(ga_, gb_)
                for k in _npc.argsort(dd).tolist():
                    key = (min(names[na_[k]], names[nb_[k]]), max(names[na_[k]], names[nb_[k]]))
                    if key not in worst:
                        worst[key] = (float(dd[k]), ga_[k], gb_[k])
                    elif dd[k] < worst[key][0]:
                        worst[key] = (float(dd[k]), ga_[k], gb_[k])
            reach = self.clear - 2e-3
            if len(g_s) >= 2:                            # item to item
                ia, ib = STRtree(g_s).query(g_s, predicate="dwithin", distance=reach)
                k_ = (ia < ib) & ~((r_s[ia] >= 0) & (r_s[ia] == r_s[ib]))
                note(g_s[ia[k_]], g_s[ib[k_]], n_s[ia[k_]], n_s[ib[k_]])
            if len(g_s) and len(g_p):                    # item to a pour's outline
                ia, ib = STRtree(g_p).query(g_s, predicate="dwithin", distance=reach)
                note(g_s[ia], g_p[ib], n_s[ia], n_p[ib])
                stree = STRtree(g_s)                      # item wholly inside another net's pour
                for n, q in pieces:
                    cand = stree.query(q, predicate="intersects")
                    cand = cand[(n_s[cand] != ids[n])] if len(cand) else cand
                    if len(cand):
                        note(_npc.array([q] * len(cand), dtype=object), g_s[cand],
                             _npc.full(len(cand), ids[n]), n_s[cand])
            box_ = [q.bounds for _, q in pieces]          # pour to pour of another net, where they come near
            for a in range(len(pieces)):
                for b2 in range(a + 1, len(pieces)):
                    (na, qa), (nb, qb) = pieces[a], pieces[b2]
                    ba, bb = box_[a], box_[b2]
                    if na == nb or ba[0] > bb[2] + reach or bb[0] > ba[2] + reach or ba[1] > bb[3] + reach or \
                            bb[1] > ba[3] + reach:
                        continue
                    if qa.intersects(qb):
                        note(_npc.array([qa], dtype=object), _npc.array([qb], dtype=object),
                             _npc.array([ids[na]]), _npc.array([ids[nb]]))
                    elif shapely.dwithin(qa, qb, reach):
                        note(_npc.array([qa], dtype=object), _npc.array([qb], dtype=object),
                             _npc.array([ids[na]]), _npc.array([ids[nb]]))
            from shapely.ops import nearest_points as _np2
            for (na, nb), (dist, ga, gb) in sorted(worst.items(), key=lambda kv: kv[0]):
                if dist < 1e-6:
                    x_, y_ = _np2(ga, gb)[0].coords[0] if not ga.intersects(gb) else \
                        ga.intersection(gb).representative_point().coords[0]
                else:
                    pa_, pb_ = _np2(ga, gb)              # (where they come nearest)
                    x_, y_ = (pa_.x + pb_.x) / 2, (pa_.y + pb_.y) / 2
                what = "short" if dist < 1e-6 else f"{dist:.2f} mm apart (clearance {self.clear})"
                probs.append(f"{L}.Cu: {na} and {nb} {what} at ({x_:.1f}, {y_:.1f})")
        for p_, n_, skew in getattr(self, "pairs", []):       # differential pairs kept matched in length
            lp, ln = self.net_length(p_), self.net_length(n_)
            if abs(lp - ln) > skew:
                probs.append(f"pair {p_} / {n_}: tracks {lp:.2f} and {ln:.2f} mm long, {abs(lp - ln):.2f} mm apart "
                             f"(keep within {skew} mm: lengthen the shorter, or route them side by side)")
        # current: tracks (each by the pins it serves: a branch to a low-current pin, its share) and pour necks
        branch: Dict[int, float] = {}
        for n in {t["net"] for t in self.tracks if t["amps"] is None}:
            if n in self.nets and self.pin_loads(n):
                branch.update(self.branch_amps(n, pads, pn, cu))
        for i_t, t in enumerate(self.tracks):
            d = self.nets.get(t["net"])
            if not d:
                probs.append(f"track on unknown net {t['net']}")
                continue
            amps = t["amps"] if t["amps"] is not None else branch.get(i_t, d["amps"])
            need = self.width_for(amps, t["layer"])
            if t["w"] + 1e-6 < need and not self._pin_neck(t, need, pads, pn):
                probs.append(f"track {t['net']} ({t['layer']}.Cu, {t['w']} mm) is too narrow for {amps} A on "
                             f"{self.oz_of(t['layer'])} oz copper: needs {need:.2f} mm for a {self.max_rise:g} C rise "
                             f"(IPC-2221)")
            L = sum(math.dist(a, b) for a, b in zip(t["pts"], t["pts"][1:]))
            drop = amps * CU_RHO * L / (t["w"] * self.oz_of(t["layer"]) * OZ_MM)
            if d["volts"] > 0 and drop > self.max_drop * d["volts"]:      # (a ground net has no voltage to drop)
                probs.append(f"track {t['net']} ({L:.0f} mm) drops {drop * 1000:.0f} mV at {amps} A, over "
                             f"{self.max_drop * 100:.0f} % of {d['volts']} V")
        for pz in self.pours:
            d = self.nets.get(pz["net"])
            g = cu.get((pz["net"], pz["layer"]))
            if not d or g is None or g.is_empty:
                continue
            from shapely.geometry import Polygon as _P
            g = g.intersection(_P(pz["poly"], pz.get("holes") or None))                # this zone's own fill
            amps = pz.get("amps") if pz.get("amps") is not None else d["amps"]
            need = self.width_for(amps, pz["layer"])
            if need <= 0.5:
                continue
            core = g.buffer(-need / 2, 8)                     # what survives when every neck narrower than need closes
            pins = [pg for ref, num, L, pg, _ in pads if L == pz["layer"] and pn.get((ref, num)) == pz["net"]]
            # the pieces each pad reaches, joined through the pad (one pad between two scraps is no neck)
            cs_ = getattr(core, "geoms", None) or [core]
            par_ = list(range(len(cs_)))

            def _rt(i):
                while par_[i] != i:
                    i = par_[i]
                return i
            touched = set()
            import numpy as _np_
            live = [k for k, part in enumerate(cs_) if not part.is_empty]
            hits_ = {}
            if live and pins:                            # (within reach, by distance: one query for every pin)
                from shapely.strtree import STRtree as _STn
                pi_, ci_ = _STn([cs_[k] for k in live]).query(pins, predicate="dwithin", distance=need / 2 + 0.5)
                for a_i, c_i in zip(pi_, ci_):
                    hits_.setdefault(int(a_i), []).append(live[int(c_i)])
            for ip_ in range(len(pins)):
                hit = sorted(hits_.get(ip_, []))
                for i in hit[1:]:
                    par_[_rt(i)] = _rt(hit[0])
                touched |= set(hit)
            if len({_rt(i) for i in touched}) > 1:
                from shapely.ops import nearest_points as _np
                big = {}
                for i in touched:                                            # each group's main piece
                    if _rt(i) not in big or cs_[i].area > cs_[big[_rt(i)]].area:
                        big[_rt(i)] = i
                a_, b_ = sorted(big.values(), key=lambda i: -cs_[i].area)[:2]  # (the neck: where the two main
                p_, q_ = _np(cs_[a_], cs_[b_])                                 # pieces come nearest)
                probs.append(f"pour {pz['net']} on {pz['layer']}.Cu near ({(p_.x + q_.x) / 2:.1f}, "
                             f"{(p_.y + q_.y) / 2:.1f}) has a neck narrower than {need:.1f} mm between its pads "
                             f"({amps} A, {self.oz_of(pz['layer'])} oz): widen it or add the other layer in parallel")
        # drills keep the hole gap apart, wall to wall (a via drilled into a pin's hole, or a web too thin to survive)
        from shapely.geometry import Point as _Pt
        drl = self.drill_list()                          # (each hole as its shape: a slot is not a round hole)
        holes_ = [(h["name"], _Pt(h["at"]), h["geom"], h) for h in drl]
        tight = []
        from shapely.strtree import STRtree as _STRh       # (the pairs within reach only: every pair was 0.4 M
        _ht = _STRh([g for _, _, g, _ in holes_]) if holes_ else None    # distances on a 56-part board)
        for i in range(len(holes_)):
            for j in sorted(int(j) for j in _ht.query(holes_[i][2], predicate="dwithin", distance=self.hole_gap)):
                if j <= i:
                    continue
                (na, pa, ga, ha), (nb, pb, gb, hb) = holes_[i], holes_[j]
                if not ha["slot"] and not hb["slot"]:    # (round ones exactly; a slot by its shape)
                    gap = pa.distance(pb) - ha["d"] / 2 - hb["d"] / 2
                else:
                    gap = ga.distance(gb) - (ga.intersection(gb).area ** 0.5 if ga.intersects(gb) else 0.0)
                if gap < self.hole_gap - 1e-6 and not (na == nb and pa.distance(pb) < 1e-6):
                    tight.append(f"{na} and {nb} drills {gap:.2f} mm apart at ({pa.x:.1f}, {pa.y:.1f}) "
                                 f"(keep {self.hole_gap} mm)")
        probs += tight[:20] + ([f"... and {len(tight) - 20} more drills too close"] if len(tight) > 20 else [])
        hc = max(self.clear, self.hole_clear)                 # copper keeps clear of every bare hole (KiCad's "hole
        bare = [h for h in drl if not h["plated"]]            # clearance"): a track over a peg hole is cut by the drill
        if bare and (self.tracks or self.vias):
            from shapely.strtree import STRtree as _STh
            items_ = self.tracks + self.vias
            shp = [self._item_shape(it) for it in items_]
            th = _STh(shp)
            for h in bare:
                (x, y), hg = h["at"], h["geom"]
                for i in th.query(hg, predicate="dwithin", distance=hc - 1e-6):
                    it = items_[int(i)]
                    gap = shp[int(i)].distance(hg)
                    probs.append(f"{'track' if 'pts' in it else 'via'} {it['net']} {max(gap, 0):.2f} mm from the bare "
                                 f"hole at ({x:.1f}, {y:.1f}) (keep {hc:.2f} mm)")
        if bare:                                              # and so does every pad: a mounting hole on a part's
            from shapely.strtree import STRtree as _STp           # pad cuts it (a footprint's own pegs beside its own
            pth = _STp([g for *_, g, _ in pads])                  # pads are the maker's design, not checked here)
            hit = set()
            for h in bare:
                (x, y), hg = h["at"], h["geom"]
                for i in pth.query(hg, predicate="dwithin", distance=hc - 1e-6):
                    ref, num, L, g, _ = pads[int(i)]
                    if ref == h.get("ref") or (ref, num, (x, y)) in hit:
                        continue
                    hit.add((ref, num, (x, y)))
                    probs.append(f"{ref} pad {num or '(mount)'} {max(g.distance(hg), 0):.2f} mm from the bare hole at "
                                 f"({x:.1f}, {y:.1f}) (keep {hc:.2f} mm)")
        inner = self.outline.buffer(-self.edge)               # copper keeps the edge gap from the routed edge
        for ref, num, L, g, _ in pads:
            if not inner.contains(g):
                probs.append(f"{ref} pad {num or '(mount)'} ({L}.Cu) reaches the board edge: copper keeps {self.edge} mm inside")
                break
        from shapely.geometry import LineString as _LS
        items = self.tracks + self.vias                  # (by distance to the edge, not a buffer per item)
        if items:
            import numpy as _npe
            axes = _npe.array([_LS(it["pts"]) if "pts" in it else _Pt(it["at"]) for it in items], dtype=object)
            rad = _npe.array([it["w"] / 2 if "pts" in it else it["dia"] / 2 for it in items])
            ok_ = shapely.contains(self.outline, axes) & ~shapely.dwithin(axes, self.outline.boundary,
                                                                          rad + self.edge - 1e-6)
        for it, ok1 in zip(items, ok_ if items else []):
            if not ok1:
                xy = it["pts"][0] if "pts" in it else it["at"]
                probs.append(f"{'track' if 'pts' in it else 'via'} {it['net']} near ({xy[0]:.1f}, {xy[1]:.1f}) comes "
                             f"within {self.edge} mm of the board edge")
        # every track end lands inside copper of its net (KiCad joins items by their end points)
        loose_ = [f"track {self.tracks[k]['net']} ({self.tracks[k]['layer']}.Cu) ends at ({e[0]:.2f}, {e[1]:.2f}) on "
                  f"nothing of its net (dangling, or only touching a pad's edge: KiCad reads it unconnected)"
                  for k, e in self.loose_ends(cu, pads, pn)]
        probs += loose_[:20] + ([f"... and {len(loose_) - 20} more loose track ends"] if len(loose_) > 20 else [])
        return probs + self.check_placement()

    def check_placement(self) -> List[str]:
        """the placement alone, in a moment, before routing: every courtyard on the board (unless the part is
        placed to overhang, like a USB connector at the edge), no two courtyards on one side overlapping, and every
        pin a net names on a part that exists with that pad. check() includes it"""
        from shapely.strtree import STRtree
        probs: List[str] = []
        for n, d in self.nets.items():
            for ref, num in d["pins"]:
                if ref not in self.comps:
                    probs.append(f"net {n}: no part {ref}")
                elif num not in self.comps[ref]["pads"]:
                    probs.append(f"net {n}: {ref} ({self.comps[ref]['fp'].split(':')[1]}) has no pad {num}")
        cy = {}
        for ref, c in self.comps.items():
            if c.get("unplaced"):
                probs.append(f"{ref} has no position yet (autoplace(board) or place_and_route(board) places it)")
                continue
            g = self.courtyard(ref)
            if g is None:
                continue
            cy[ref] = g
            if not self.outline.contains(g) and not c["overhang"]:
                probs.append(f"{ref} ({c['fp'].split(':')[1]}) hangs off the board outline")
        refs = list(cy)
        tree = STRtree([cy[r] for r in refs]) if refs else None
        for i in range(len(refs)):
            for j in sorted(int(j) for j in tree.query(cy[refs[i]])):
                if j <= i:
                    continue
                if self.comps[refs[i]].get("side", "F") != self.comps[refs[j]].get("side", "F"):
                    continue                                 # top and bottom parts may overlap in plan
                if cy[refs[i]].intersection(cy[refs[j]]).area > 1e-3:     # (KiCad's DRC flags any overlap: a
                    probs.append(f"courtyards of {refs[i]} and {refs[j]} overlap")   # 0.03 mm strip is one)
        if tree is not None:                                 # a mounting hole is drilled through whatever is there
            from shapely.geometry import Point as _Ph
            for x, y, d in self.holes:
                hg = _Ph(x, y).buffer(d / 2, 16)
                for j in sorted(int(j) for j in tree.query(hg)):
                    if cy[refs[j]].intersection(hg).area > 0.05:
                        probs.append(f"the mounting hole at ({x:.1f}, {y:.1f}) is inside the courtyard of {refs[j]}")
        if any(c.get("symbol") for c in self.comps.values()):  # (every symbol pin has a pad, every pad a pin)
            import parts
            probs += parts.pin_check(self)
        return probs

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


def _cxy(geom, X, Y):
    """vectorised point-in-geometry on a prepared geometry (orders of magnitude faster on big unions); on the
    router's grid (X, Y from meshgrid(..., indexing="ij")) only the points inside the geometry's bounds are tested"""
    import numpy as np
    import shapely
    shapely.prepare(geom)
    if getattr(X, "ndim", 0) != 2 or geom.is_empty:
        return shapely.contains_xy(geom, X, Y) if not geom.is_empty else np.zeros(np.shape(X), bool)
    xs, ys = X[:, 0], Y[0, :]
    if not (np.all(np.diff(xs) > 0) and np.all(np.diff(ys) > 0)) or not np.all(X[0, :] == X[0, 0]):
        return shapely.contains_xy(geom, X, Y)
    x0, y0, x1, y1 = geom.bounds
    i0, i1 = np.searchsorted(xs, x0, "left"), np.searchsorted(xs, x1, "right")
    j0, j1 = np.searchsorted(ys, y0, "left"), np.searchsorted(ys, y1, "right")
    out = np.zeros(X.shape, bool)
    if i1 > i0 and j1 > j0:
        out[i0:i1, j0:j1] = shapely.contains_xy(geom, X[i0:i1, j0:j1], Y[i0:i1, j0:j1])
    return out


def _escape(board: Board, ref: str, num: str, reach: float = 0.5):
    """for a pad narrower than a routing channel (fine-pitch SMD), the point just beyond its outer end along its long
    axis, away from the part's centre: route from there, with a straight stub out of the pad"""
    c = board.comps[ref]
    pad = c["pads"][str(num)][0]
    g = _pad_poly(pad, (c["at"][0], c["at"][1], c["rot"]))
    w, h = pad["size"]
    if pad.get("prims"):                                     # a custom pad: its real extent, in the pad's frame
        bx = _pad_poly({**pad, "rot": 0.0, "at": (0.0, 0.0), "mirror": False}, (0.0, 0.0, 0.0)).bounds
        w, h = bx[2] - bx[0], bx[3] - bx[1]
    if min(w, h) >= 0.6 or pad["drill"] or len(c["pads"]) <= 4:    # (a 2-pad passive leaves its pad anywhere)
        return None
    ang = math.radians(c["rot"] - pad["rot"] + (0.0 if w >= h else 90.0))
    ux, uy = math.cos(ang), math.sin(ang)
    cx, cy = g.centroid.x, g.centroid.y
    if (cx - c["at"][0]) * ux + (cy - c["at"][1]) * uy < 0:
        ux, uy = -ux, -uy
    half = max(w, h) / 2
    return (cx, cy), (cx + ux * (half + reach), cy + uy * (half + reach))


# ---- a small maze router (Lee wavefronts on a grid, two layers, vias) ---------------------------------------------------
def route(board: Board, net: str, width: float = 0.3, grid: float = 0.25, via_cost: float = 8.0, layers: str = "FB",
          keepout=None, via: Tuple[float, float] = (0.3, 0.6), only: Optional[Sequence[Tuple[str, str]]] = None,
          amps: Optional[float] = None, track_keepout=None,
          seed_pins: Optional[Sequence[Tuple[str, str]]] = None) -> List[str]:
    """Connect the pads of ``net`` (see ``_route_once`` for how one attempt goes). When an attempt leaves a pad
    unreached or the net split, the net is torn back to where it was and tried again growing from a different
    root: from the net's pour (every pad joined to it), then from its biggest pad (not one boxed in by fine-pitch
    neighbours); the attempt with the fewest problems is kept. Returns that attempt's problems.
    A net with low-current pins (``Board.pin_loads``) is routed in two steps: the trunk between its other pins at
    ``width``, then each branch to a low-current pin at its own width (``Board.pin_width``), joined to the trunk."""
    low = board.pin_loads(net) if (net in board.nets and only is None and amps is None and seed_pins is None) else {}
    pins0 = [tuple(p) for p in board.nets[net]["pins"]] if low else []
    if low and any(p not in low for p in pins0):
        groups: Dict[float, list] = {}
        for p in pins0:
            if p in low:
                groups.setdefault(min(width, board.pin_width(net, p)), []).append(p)
        trunk = [p for p in pins0 if p not in low]
        args = dict(grid=grid, via_cost=via_cost, layers=layers, keepout=keepout, via=via,
                    track_keepout=track_keepout)
        d = board.nets[net]
        orig = d["pins"]
        try:                                             # (the pins not yet routed left off the net meanwhile: not
            d["pins"] = trunk                            #  taken for copper of the net that the trunk could join)
            probs = route(board, net, width=width, **args)
            done = list(trunk)
            for w_, ps in sorted(groups.items(), key=lambda kv: -kv[0]):   # (the wider branches first)
                done += ps
                d["pins"] = [p for p in pins0 if p in done]
                probs += route(board, net, width=w_, only=ps, **args)
        finally:
            d["pins"] = orig
        return probs
    kw = dict(width=width, grid=grid, via_cost=via_cost, layers=layers, keepout=keepout, via=via, only=only, amps=amps,
              track_keepout=track_keepout, seed_pins=seed_pins)
    forced = [(a, getattr(board, a)) for a in ("root_big", "pour_root") if hasattr(board, a)]
    if forced:                                           # (a caller's own choice: one attempt)
        return _join_islands(board, net, _route_once(board, net, **kw), kw, only)
    t0, v0, f0 = list(board.tracks), list(board.vias), set(getattr(board, "_fan", set()))
    best = None
    for rb, pr in ((False, False), (False, True), (True, True)):
        board.tracks, board.vias, board._fan = list(t0), list(v0), set(f0)
        board.root_big, board.pour_root = rb, pr
        try:
            probs = _route_once(board, net, **kw)
        finally:
            del board.root_big, board.pour_root
        split = len(board.islands(net)) - 1 if net in board.nets else 0
        # (a partial route, ``only``: the pads it names reached first; the net may be split until the rest is)
        score = (len(probs) + split, len(board.vias) - len(v0)) if not only else \
            (len(probs), split, len(board.vias) - len(v0))
        if best is None or score < best[0]:
            best = (score, probs, list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())))
        if score[0] == 0:
            break
    _, probs, board.tracks, board.vias, board._fan = best
    neck = max(0.15, board.width_for(amps if amps is not None else board.nets[net]["amps"], "F")) \
        if net in board.nets else 0.15
    left = [tuple(m.group(1).rsplit(".", 1)) for m in
            (re.match(rf"route {re.escape(net)}: could not reach (.+) \(blocked", p_) for p_ in probs) if m]
    if left and neck < width - 1e-9:                     # neck down: the pads still unreached (between fine-pitch
        board.root_big = board.pour_root = False         # neighbours a full-width track cannot pass), each joined
        try:                                             # to the net's copper so far by the narrowest track its
            kw2 = dict(kw, width=neck, only=left, seed_pins=None)   # current allows
            again = _route_once(board, net, **kw2)
        finally:
            del board.root_big, board.pour_root
        probs = [p_ for p_ in probs if not re.match(rf"route {re.escape(net)}: could not reach", p_)] + again
    return _join_islands(board, net, probs, kw, only)


def route_board(board: Board, router: str = "freerouting", **kw) -> List[str]:
    """route the whole board (``_route_board``), then length-match the differential pairs (``tune_pairs``)"""
    probs = _route_board(board, router, **kw)
    unpaired = lambda: {p_ for p_ in board.check() if not p_.startswith("pair ")}   # (pairs: tuned just below)
    before = unpaired()                                  # (clean corners: no stub past a bend, no right angles;
    t0 = [dict(t, pts=list(t["pts"])) for t in board.tracks]   # kept only when the check finds nothing new)
    board.drop_loops()                                   # (a second run of a net's track, a loop: not needed)
    board.trim_overshoots()
    board.chamfer()
    if unpaired() - before:
        board.tracks = t0
        board.__dict__.pop("_cu_memo", None)
    if getattr(board, "pairs", None):
        tune_pairs(board)
        probs = [p_ for p_ in probs if not p_.startswith("pair ")] + \
            [p_ for p_ in board.check() if p_.startswith("pair ")]
    return probs


def mark_user_copper(board: Board) -> None:
    """the copper on the board before any routing is the script's own (a hand-drawn feed): tagged ``user``, so the
    clean-up passes (spurs, loops, overshoots, chamfers) and the rip-up leave it as drawn"""
    if getattr(board, "_user_marked", False) or any(it.get("user") for it in board.tracks + board.vias):
        board._user_marked = True                        # (once - later calls find the router's copper there too -
        return                                           #  or already tagged as the script drew it: pcb_cli.load)
    board._user_marked = True
    for it in board.tracks + board.vias:
        if not it.get("fan"):
            it.setdefault("user", True)


def _route_board(board: Board, router: str = "freerouting", **kw) -> List[str]:
    """route the whole board with ``router``: "freerouting" (FreeRouting through KiCad, as KiCad users route:
    ``freerouting.route_kicad``; needs KiCad, Java and the FreeRouting jar, and says which is missing) or "harness"
    (``autoroute``, this file's grid router, chosen explicitly). Sets ``board.router_used``"""
    mark_user_copper(board)                              # (first routing call: what is there, the script drew)
    if router == "harness":
        board.router_used = "harness"
        probs = autoroute(board, **kw)
        neck_vias(board)                                 # (a via its route left against a pad of its own net moved a
        return probs                                     #  neck away, as after FreeRouting: solder wicks down it)
    if router != "freerouting":
        raise ValueError("router: freerouting or harness")
    import freerouting
    missing = [w for w, ok in (("KiCad (its Python API, pcbnew)", freerouting.kicad_available()),
                               ("Java 17+ and the FreeRouting jar (prometheus setup)", freerouting.available())) if not ok]
    if missing:
        raise RuntimeError("FreeRouting needs " + " and ".join(missing) + ": see docs/setup.md, or route with the "
                           "harness's own router (--router harness)")
    freerouting.escape_fine_pitch(board)                 # (fine-pitch pins' first stretch drawn by the harness)
    board.router_used = "freerouting (through KiCad)"    # (FreeRouting does not know the drill gap: its vias are
    probs = freerouting.route_kicad(board, **kw)         #  parted from the others after)
    probs = _second_pass(board, probs, lambda: freerouting.route_kicad(board, **kw)) + nudge_vias(board)
    neck_vias(board)                                     # (one it cannot move is the critic's "via neck" warning:
    return probs                                         #  a solder risk to weigh, not a board that is not joined)


def tune_pairs(board: Board, tries_per_seg: int = 6) -> List[str]:
    """length-match each differential pair whose skew is over its limit (``Board.pair``) the way KiCad's length
    tuner does: a serpentine on a straight stretch of the shorter net, its legs a clearance apart, kept only when
    the design check finds nothing new (no short, clearance slip or cut pour). Returns what is still over"""
    left = []
    for p_, n_, skew in getattr(board, "pairs", []):
        lp, ln = board.net_length(p_), board.net_length(n_)
        if abs(lp - ln) <= skew:
            continue
        short = p_ if lp < ln else n_
        want = abs(lp - ln) - skew / 3                    # (aim inside the limit, not at its edge)
        base = set(board.check())
        segs = []
        for i, t in enumerate(board.tracks):
            if t["net"] != short:
                continue
            for k, (a, b) in enumerate(zip(t["pts"], t["pts"][1:])):
                if math.dist(a, b) >= 2.0:
                    segs.append((math.dist(a, b), i, k))
        done = False
        for S, i, k in sorted(segs, reverse=True):
            t = board.tracks[i]
            a, b = t["pts"][k], t["pts"][k + 1]
            ux, uy = (b[0] - a[0]) / S, (b[1] - a[1]) / S
            per = 2 * (t["w"] + board.clear + 0.05)       # one bump: up, across, down, across
            m = per / 5                                   # (its corners mitred at 45 degrees, as a professional
            nmax = int((S - 1.0) // per)                  #  tuner draws them: each mitre is 0.586 m shorter)
            for nb in sorted({max(1, nmax), max(1, nmax // 2), max(1, nmax // 4)}, reverse=True)[:3]:
                A = want / (2 * nb) + 2 * (2 - math.sqrt(2)) * m   # (a bump adds 2A less 2.343 m)
                if A < max(t["w"], 2.5 * m) or A > 3.0:
                    continue
                for sign in (1, -1):
                    nx, ny = -uy * sign, ux * sign
                    s0 = (S - nb * per) / 2
                    pts = [a]
                    for j in range(nb):
                        for d_, h in ((-m, 0), (0, m), (0, A - m), (m, A), (per / 2 - m, A), (per / 2, A - m),
                                      (per / 2, m), (per / 2 + m, 0)):
                            o = s0 + j * per + d_
                            pts.append((round(a[0] + ux * o + nx * h, 4), round(a[1] + uy * o + ny * h, 4)))
                    pts.append(b)
                    old = t["pts"]
                    t["pts"] = old[:k] + pts + old[k + 2:]
                    board.__dict__.pop("_cu_memo", None)
                    now = set(board.check())
                    new = {m for m in now - base if not m.startswith(f"pair {p_} / {n_}")}
                    if not new:
                        done = abs(board.net_length(p_) - board.net_length(n_)) <= skew
                        base = now
                        break
                    t["pts"] = old
                    board.__dict__.pop("_cu_memo", None)
                else:
                    continue
                break
            if done:
                break
            lp, ln = board.net_length(p_), board.net_length(n_)
            want = abs(lp - ln) - skew / 3
            if abs(lp - ln) <= skew:
                done = True
                break
        if not done:
            lp, ln = board.net_length(p_), board.net_length(n_)
            left.append(f"pair {p_} / {n_}: still {abs(lp - ln):.2f} mm apart after length tuning")
    return left


def _second_pass(board: Board, probs: List[str], again) -> List[str]:
    """nets still unreached or split after FreeRouting and the finish: torn up whole and given to FreeRouting once
    more with the rest of the copper in place (it finds ways the grid router does not); kept only when better"""
    def unreached(ps):
        return {m.group(1) for m in (re.match(r"route (\S+): could not reach", p_) or
                                    re.match(r"net (\S+) is split", p_) for p_ in ps) if m}
    nets = {n for n in unreached(probs) if n in board.nets and n not in {p["net"] for p in board.pours}}
    if not nets:
        return probs
    before = (list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())), probs,
              severity(probs) + severity(board.check()))
    board.tracks = [t for t in board.tracks if t["net"] not in nets or t.get("user")]   # (the script's own copper
    board.vias = [v for v in board.vias if v["net"] not in nets or v.get("user")]       #  stays as it drew it)
    board.__dict__.pop("_cu_memo", None)
    try:
        probs2 = again()
    except (RuntimeError, OSError):
        probs2 = None
    if probs2 is None or severity(probs2) + severity(board.check()) >= before[4]:
        board.tracks, board.vias, board._fan, probs2 = before[0], before[1], before[2], before[3]
        board.__dict__.pop("_cu_memo", None)
    return probs2


def auto_grid(board: Board) -> float:
    """the routing grid for a board: 0.25 mm, or 0.2 mm when a part's pins sit at a pitch the 0.25 grid misses and
    0.2 hits (0.4 mm, 0.8 mm: an RP2040's QFN-56), so each pin's centre is a grid point"""
    import numpy as np
    pitches = []
    for c in board.comps.values():
        cs = [(sum(p["at"][0] for p in l) / len(l), sum(p["at"][1] for p in l) / len(l))
              for n, l in c["pads"].items() if l and n]
        if len(cs) >= 6:
            a = np.asarray(cs)
            d = np.hypot(*(a[:, None, :] - a[None, :, :]).transpose(2, 0, 1))
            np.fill_diagonal(d, np.inf)
            pitches.append(float(d.min()))
    off = lambda p, g: abs(p / g - round(p / g))
    return 0.2 if any(p < 0.8 and off(p, 0.25) > 0.05 and off(p, 0.2) < 0.05 for p in pitches) else 0.25


def drop_pours(board: Board, nets: Optional[Sequence[str]] = None) -> None:
    """each surface pad of a net poured on another layer drops through a via of its own beside it (the ground pins
    of a two-layer board, a plane's), first, while there is room round a fine-pitch part"""
    poured = {p["net"] for p in board.pours}
    for net in (nets if nets is not None else sorted(poured, key=lambda n: board.nets[n]["amps"])):
        if net not in board.nets:
            continue
        layers = {p["layer"] for p in board.pours if p["net"] == net}
        pins = [tuple(p) for p in board.nets[net]["pins"]
                if any(L_ not in layers and not d for r_, n_, L_, g, d in board.pad_shapes() if (r_, n_) == tuple(p))]
        if pins:
            board.drop(net, pins, width=min(0.3, board.net_width(net)))


def autoroute(board: Board, passes: int = 3, drop: bool = True, **kw) -> List[str]:
    """route the whole board in one call: fine-pitch fan-outs first, a via beside each surface pad of a poured net
    (``drop``: the ground pins of a two-layer board drop to its ground pour), then the signal nets (the most local first),
    then the power nets (the least current first), the nets with a pour last, each at ``Board.net_width`` (a net's
    own width, else what its current needs), in negotiated passes (``route_jobs``: a net that fails is routed
    first on the next pass), and finally ``repair`` (a net a later one split joined back). ``kw`` goes to every
    ``route`` call (layers, grid, keepout, via...). Returns the problems ([] = every pad reached)"""
    import math
    poured = {p["net"] for p in board.pours}
    pads = {(r, n): g for r, n, L, g, d in board.pad_shapes()}

    def spread(net):
        pts = [pads[tuple(p)].centroid for p in board.nets[net]["pins"] if tuple(p) in pads]
        if len(pts) < 2:
            return 0.0
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if "grid" not in kw:                                 # a grid the finest pitch sits on: 0.4 mm pads on 0.2
        kw["grid"] = auto_grid(board)
    nets = [n for n, d in board.nets.items() if len(d["pins"]) >= 2 or n in poured]
    sig = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] <= 0.3], key=spread)
    pwr = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] > 0.3],
                 key=lambda n: (board.nets[n]["amps"], spread(n)))
    last = sorted([n for n in nets if n in poured], key=lambda n: board.nets[n]["amps"])
    if not getattr(board, "_fan", None):
        board.fanout(width=0.2)
    if drop:
        drop_pours(board, last)
    jobs = [(n, board.net_width(n), None) for n in sig + pwr + last]
    if ("~freerouting", "") in getattr(board, "_fan", set()):     # (finishing another router's work: the nets it
        jobs = [j for j in jobs if j[0] in poured or len(board.islands(j[0])) > 1   # left whole are not routed
                or len(board.anchor_groups(j[0])) > 1]                             # again, net by net: a stub's
        board.prune_spurs()                              # tip past where FreeRouting joined it is trimmed)
    probs = route_jobs(board, jobs, kw=(lambda net: dict(kw)) if kw else None, passes=passes)
    return probs + repair(board, **kw)


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


def _decap_target(c: dict, ref: str, num: str):
    """a pin a decoupling capacitor belongs by: a chip's supply pin (a symbol's power input). A connector's pin is
    None: it takes its turn in the deal (so the chip's pins get as many as before) but holds no capacitor; a
    symbol's other pins are False, out of the deal"""
    if re.match(r"^(J|P|CN|CON)\d", ref, re.I):
        return None
    sym = c.get("symbol")
    if sym and not sym.get("auto"):                      # (a generic symbol says nothing of a chip's supply pins)
        return any(q["num"] == num and q["type"] == "power_in" for q in sym["pins"])
    return True


def autoplace(board: Board, move: Optional[Sequence[str]] = None, gap: float = 1.0, seed: int = 0,
              effort: float = 1.0, rotations: Sequence[int] = (0, 90, 180, 270)) -> List[str]:
    """place parts for you: a short total of wire (each net's half-perimeter, ground and other poured nets weighted
    low, high-current nets high) with every courtyard on the board, ``gap`` mm clear of the others on its side, the
    mounting holes and the keep-outs. ``move``: the parts to place (default: every part placed without a position,
    ``at=None``; if there are none, every part not placed to overhang the edge). The rest stay where they are:
    put connectors, buttons and mounting holes where the enclosure needs them first, then call this. It places the
    parts one at a time, most connected first, each where it adds the least wire, then improves the whole by
    annealing (moves, turns, swaps; ``effort`` scales the time spent). Returns the parts it found no room for.
    Then route: ``autoroute(board)``."""
    import numpy as np
    import shapely
    from shapely.geometry import box
    rng = np.random.default_rng(seed)
    if move is None:
        move = [r for r, c in board.comps.items() if c.get("unplaced")] or \
               [r for r, c in board.comps.items() if not c["overhang"]]
    move = [r for r in move if r in board.comps]
    mset = set(move)
    rots = [int(r) % 360 for r in rotations] or [0]
    inner = board.outline.buffer(-0.05)
    shapely.prepare(inner)

    def rect(ref, rot):                                  # courtyard box (footprint frame, side applied), turned
        c = board.comps[ref]
        crt = footprint(c["fp"])["courtyard"]
        if not crt:
            xs = [p["at"][0] for l in c["pads"].values() for p in l] or [0.0]
            ys = [p["at"][1] for l in c["pads"].values() for p in l] or [0.0]
            crt = (min(xs) - 0.5, min(ys) - 0.5, max(xs) + 0.5, max(ys) + 0.5)
        if c.get("side", "F") == "B":
            crt = (-crt[2], crt[1], -crt[0], crt[3])
        x0, y0, x1, y1 = crt
        cs = [(sum(p["at"][0] for p in l) / len(l), sum(p["at"][1] for p in l) / len(l)) for l in c["pads"].values() if l]
        if len(cs) >= 6:                                 # a fine-pitch part: room round it to fan out
            a_ = np.asarray(cs)
            d_ = np.hypot(*(a_[:, None, :] - a_[None, :, :]).transpose(2, 0, 1))
            np.fill_diagonal(d_, np.inf)
            if d_.min() < 0.8:                           # (more pins, more tracks to fan out: a QFN-56 ~1.9 mm)
                m_ = min(2.0, 0.75 + 0.02 * len(cs))
                x0, y0, x1, y1 = x0 - m_, y0 - m_, x1 + m_, y1 + m_
        corners = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        t = math.radians(rot)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        q = corners @ R.T
        return q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()

    def pads_at(ref, rot):                               # {pad number: centre offset} turned
        t = math.radians(rot)
        ct, st = math.cos(t), math.sin(t)
        return {n: (sum(p["at"][0] for p in l) / len(l) * ct - sum(p["at"][1] for p in l) / len(l) * st,
                    sum(p["at"][0] for p in l) / len(l) * st + sum(p["at"][1] for p in l) / len(l) * ct)
                for n, l in board.comps[ref]["pads"].items() if l}
    R_ = {(r, a): rect(r, a) for r in move for a in rots}
    P_ = {(r, a): pads_at(r, a) for r in board.comps for a in (rots if r in move else [board.comps[r]["rot"] % 360])}
    poured = {p["net"] for p in board.pours}
    # decoupling: a two-pin capacitor between a supply and a poured (ground) net belongs by one of that supply's IC
    # pins; the netlist does not say which, so the caps are dealt out over those pins in turn and each is held to its
    # pin by a stiff spring (a pseudo-net of the two pins)
    NETS = dict(board.nets)
    pnet = board.pad_net()
    many = {r for r, c in board.comps.items() if len(c["pads"]) >= 6}
    deal: Dict[str, int] = {}
    told = {x.partition(".")[0] for a_, b_, _ in getattr(board, "near", []) for x in (a_, b_)}    # (a requirement says where it goes)
    for r in sorted(mset, key=lambda r: (len(r), r)):
        c = board.comps[r]
        if r in told or not r.upper().startswith("C") or len([n for n in c["pads"] if n]) != 2:
            continue
        ns = {num: pnet.get((r, num)) for num in c["pads"] if num}
        sup = [(num, n) for num, n in ns.items() if n and n not in poured]
        if len(sup) != 1 or not any(n in poured for n in ns.values()):
            continue
        num, n = sup[0]
        targets = [tuple(p_) for p_ in NETS[n]["pins"] if p_[0] in many and _decap_target(board.comps[p_[0]], p_[0], p_[1])
                   is not False]
        if not targets:
            continue
        k = deal.get(n, 0)
        deal[n] = k + 1
        tgt = targets[k % len(targets)]
        if _decap_target(board.comps[tgt[0]], *tgt):    # (one dealt to a connector's pin is left free: the chip's
            NETS[f"~decap {r}"] = {"pins": [(r, num), tgt], "amps": 5.0, "limit": 3.0}   # own stay as many)
    for r in sorted(mset):                               # a crystal held to its IC's oscillator pins (short, quiet
        if not re.match(r"^(Y|X)\d", r):                 # traces), its load capacitors following through their nets
            continue
        for num in board.comps[r]["pads"]:
            n = pnet.get((r, num))
            if not n or n in poured or n not in board.nets:
                continue
            ic_pins = [tuple(p_) for p_ in board.nets[n]["pins"] if p_[0] in many and p_[0] != r]
            if ic_pins:
                NETS[f"~xtal {r}.{num}"] = {"pins": [(r, num), ic_pins[0]], "amps": 5.0, "limit": 5.0}
    for a0, b0, dmax in getattr(board, "near", []):     # the requirements' "within d mm of": a spring that turns
        ta, tb = near_target(board, a0), near_target(board, b0)   # stiff past d (pins on a shared net, else the
        if not ta or not tb:                                      # centres; "U1.9": that pin)
            continue
        a_, b_ = ta[0], tb[0]
        pas = ta[1] or list(board.comps[a_]["pads"])
        pbs = tb[1] or list(board.comps[b_]["pads"])
        shared = [(pa, pb) for pa in pas for pb in pbs
                  if pa and pb and pnet.get((a_, pa)) and pnet.get((a_, pa)) == pnet.get((b_, pb))]
        if not shared and (ta[1] or tb[1]):              # (a pin named: it, whatever its net)
            shared = [(pas[0], pbs[0])]
        shared.sort(key=lambda q: pnet.get((a_, q[0])) in poured)
        pa, pb = shared[0] if shared else (None, None)
        def _half(ref, num):                             # (the pad's smaller half-size: centre to its edge)
            ps = board.comps[ref]["pads"].get(num) or []
            return min(min(p_["size"]) for p_ in ps) / 2 if ps else 0.0
        # the requirement is pad to pad; the spring measures pin centres: the pads' half-sizes are allowed for
        lim_ = dmax + _half(a_, pa) + _half(b_, pb) - 0.15
        NETS[f"~near {a0} {b0}"] = {"pins": [(a_, pa), (b_, pb)], "amps": 5.0, "limit": max(0.5, lim_)}
    wt = {n: (0.15 if n in poured else 1.0) * (1.0 + min(d.get("amps", 0.1), 5.0) / 2.0) for n, d in NETS.items()}
    nets_of: Dict[str, List[str]] = {}
    for n, d in NETS.items():
        for r, _ in d["pins"]:
            nets_of.setdefault(r, [])
            if n not in nets_of[r]:
                nets_of[r].append(n)
    pos = {r: (c["at"][0], c["at"][1], c["rot"] % 360) for r, c in board.comps.items()}
    placed = {r for r in board.comps if r not in move}
    side = {r: board.comps[r].get("side", "F") for r in board.comps}
    # what a moved part keeps clear of: the fixed parts' courtyards, the holes, the keep-outs
    fixed_obs = {"F": [], "B": []}
    for r in placed:
        g = board.courtyard(r)
        if g is not None:
            fixed_obs[side[r]].append(g.bounds)
    for x, y, d in board.holes:
        k = getattr(board, "_hole_ko", {}).get((x, y), d / 2 + 0.6)
        for L in "FB":
            fixed_obs[L].append((x - k, y - k, x + k, y + k))
    for k in getattr(board, "keepouts", []):
        bx = shapely.geometry.Polygon(k["poly"]).bounds
        for L in "FB":
            if L in k["layers"]:
                fixed_obs[L].append(bx)
    for t in board.tracks:                               # and the copper the script drew (a hand-drawn feed): a
        if t.get("fan") or t["layer"] not in fixed_obs:  # part placed on it would short to it; as small boxes
            continue                                     # along each segment (a diagonal is no one box)
        h_ = t["w"] / 2
        for (xa, ya), (xb, yb) in zip(t["pts"], t["pts"][1:]):
            n_ = max(1, int(math.ceil(math.dist((xa, ya), (xb, yb)) / 0.5)))
            for i_ in range(n_):
                x0_, y0_ = xa + (xb - xa) * i_ / n_, ya + (yb - ya) * i_ / n_
                x1_, y1_ = xa + (xb - xa) * (i_ + 1) / n_, ya + (yb - ya) * (i_ + 1) / n_
                fixed_obs[t["layer"]].append((min(x0_, x1_) - h_, min(y0_, y1_) - h_,
                                              max(x0_, x1_) + h_, max(y0_, y1_) + h_))

    def box_of(r, x, y, a):
        x0, y0, x1, y1 = R_[(r, a)]
        return x + x0, y + y0, x + x1, y + y1

    def clear(r, b_, others):
        g = gap - 1e-6
        for o in others:
            if b_[0] < o[2] + g and o[0] < b_[2] + g and b_[1] < o[3] + g and o[1] < b_[3] + g:
                return False
        return True

    convex = inner.convex_hull.area - inner.area < 1e-6
    rect_ = convex and abs(box(*inner.bounds).area - inner.area) < 1e-6
    ib = inner.bounds

    def inside(b_):                                       # (a convex board holds a box when it holds its corners)
        if rect_:
            return ib[0] <= b_[0] and ib[1] <= b_[1] and b_[2] <= ib[2] and b_[3] <= ib[3]
        if convex:
            return bool(shapely.contains_xy(inner, [b_[0], b_[2], b_[2], b_[0]], [b_[1], b_[1], b_[3], b_[3]]).all())
        return inner.contains(box(*b_))

    def legal(r, x, y, a, skip=()):
        b_ = box_of(r, x, y, a)
        if not inside(b_):
            return False
        others = fixed_obs[side[r]] + [box_of(o, *pos[o]) for o in placed if o in mset and o != r and o not in skip
                                         and side[o] == side[r]]
        return clear(r, b_, others)

    def pin_xy(r, num):
        x, y, a = pos[r]
        dx, dy = P_[(r, a)].get(num, (0.0, 0.0))
        return x + dx, y + dy

    def net_cost(n, only_placed=True):
        pts = [pin_xy(r, num) for r, num in NETS[n]["pins"] if r in board.comps and (r in placed or not only_placed)]
        if len(pts) < 2:
            return 0.0
        a = np.asarray(pts)
        hp = a[:, 0].max() - a[:, 0].min() + a[:, 1].max() - a[:, 1].min()
        if len(a) <= 40:                                 # + each pin's way to its nearest pin: a decoupling cap
            d = np.abs(a[:, None, :] - a[None, :, :]).sum(-1)    # sits by a pin, not just inside the net's box
            np.fill_diagonal(d, np.inf)
            hp += 0.5 * d.min(1).sum()
        lim = NETS[n].get("limit")
        if lim is not None and len(a) == 2:              # (a "within d mm" spring: stiff past its limit)
            hp += 30.0 * max(0.0, float(np.hypot(*(a[0] - a[1]))) - lim)
        return wt[n] * hp

    # 1. one at a time, most connected first, each where it adds the least wire
    minx, miny, maxx, maxy = board.outline.bounds
    step = max(0.25, min(maxx - minx, maxy - miny) / 60)
    gx, gy = np.meshgrid(np.arange(minx, maxx + 1e-9, step), np.arange(miny, maxy + 1e-9, step))
    gx, gy = gx.ravel(), gy.ravel()
    cx, cy = board.outline.centroid.x, board.outline.centroid.y
    order = sorted(move, key=lambda r: -sum(len(NETS[n]["pins"]) * wt[n] for n in nets_of.get(r, []))
                   - 0.5 * len(board.comps[r]["pads"]))
    partners: Dict[str, List[str]] = {}                  # (a part held near another goes right after it: a
    for m, dm in NETS.items():                           # cluster, placed before the room round its chip fills)
        if dm.get("limit") is not None and len(dm["pins"]) == 2:
            (ra_, _), (rb_, _) = dm["pins"]
            partners.setdefault(rb_, []).append(ra_)
    seq, seen_ = [], set()

    def _emit(r):
        if r in seen_ or r not in mset:
            return
        seen_.add(r)
        seq.append(r)
        for q in sorted(partners.get(r, []), key=lambda q: -len(board.comps[q]["pads"])):
            _emit(q)
    for r in order:
        _emit(r)
    for r in list(partners):                             # (a fixed chip's partners come first of all)
        if r not in mset:
            for q in partners[r]:
                if q in mset and q not in seen_:
                    seq.insert(0, q)
                    seen_.add(q)
    order = seq + [r for r in order if r not in seen_]
    failed = []
    for r in order:
        best = None
        for gap_try, a in [(gt, a) for gt in dict.fromkeys((gap, min(gap, 0.3))) for a in rots]:
            if best is not None and gap_try != gap:
                break                                    # (only a part with no room at the full gap squeezes in)
            x0, y0, x1, y1 = R_[(r, a)]
            bxs = np.stack([gx + x0, gy + y0, gx + x1, gy + y1], axis=1)
            ok = shapely.contains(inner, shapely.box(bxs[:, 0], bxs[:, 1], bxs[:, 2], bxs[:, 3]))
            others = np.array(fixed_obs[side[r]] + [box_of(o, *pos[o]) for o in placed if o in mset and side[o] == side[r]])
            if len(others):
                g = gap_try - 1e-6
                hit = ((bxs[:, None, 0] < others[None, :, 2] + g) & (others[None, :, 0] < bxs[:, None, 2] + g) &
                       (bxs[:, None, 1] < others[None, :, 3] + g) & (others[None, :, 1] < bxs[:, None, 3] + g)).any(1)
                ok &= ~hit
            if not ok.any():
                continue
            cost = 0.002 * (np.abs(gx - cx) + np.abs(gy - cy))     # (with nothing to pull it, the middle)
            for n in nets_of.get(r, []):
                mine = [P_[(r, a)][num] for rr, num in NETS[n]["pins"] if rr == r and num in P_[(r, a)]]
                oth = [pin_xy(rr, num) for rr, num in NETS[n]["pins"] if rr != r and rr in placed]
                if not mine:
                    continue
                mx = np.array([m[0] for m in mine])
                my = np.array([m[1] for m in mine])
                lo_x = gx + mx.min()
                hi_x = gx + mx.max()
                lo_y = gy + my.min()
                hi_y = gy + my.max()
                if oth:
                    ox, oy = [o[0] for o in oth], [o[1] for o in oth]
                    lo_x, hi_x = np.minimum(lo_x, min(ox)), np.maximum(hi_x, max(ox))
                    lo_y, hi_y = np.minimum(lo_y, min(oy)), np.maximum(hi_y, max(oy))
                cost = cost + wt[n] * (hi_x - lo_x + hi_y - lo_y)
            cost = np.where(ok, cost, np.inf)
            i = int(np.argmin(cost))
            if best is None or cost[i] < best[0]:
                best = (cost[i], gx[i], gy[i], a)
        if best is None:
            failed.append(r)
            continue
        pos[r] = (float(best[1]), float(best[2]), best[3])
        placed.add(r)
    # 2. anneal: move, turn or swap a part; keep what shortens the wire (and, early on, some of what does not)
    mov = [r for r in move if r in placed]
    if len(mov) >= 1:
        total = sum(net_cost(n) for n in NETS)
        iters = int(effort * 400 * len(mov) * max(1, len(rots)))
        T0 = max(total / max(len(NETS), 1) * 0.3, 0.1)
        span = max(maxx - minx, maxy - miny)
        for k in range(iters):
            frac = k / max(iters, 1)
            T = T0 * (1 - frac) ** 2 + 1e-4
            r = mov[int(rng.integers(len(mov)))]
            kind = rng.random()
            old = {r: pos[r]}
            if kind < 0.15 and len(mov) > 1:              # swap with a part of a like size
                o = mov[int(rng.integers(len(mov)))]
                if o == r or side[o] != side[r]:
                    continue
                old[o] = pos[o]
                pos[r], pos[o] = (old[o][0], old[o][1], old[r][2]), (old[r][0], old[r][1], old[o][2])
            elif kind < 0.3:                              # turn
                pos[r] = (old[r][0], old[r][1], rots[int(rng.integers(len(rots)))])
            else:                                         # move, less far as it cools
                sc = span * (0.3 * (1 - frac) + 0.02)
                pos[r] = (round((old[r][0] + rng.normal(0, sc)) / 0.05) * 0.05,
                          round((old[r][1] + rng.normal(0, sc)) / 0.05) * 0.05, old[r][2])
            if not all(legal(q, *pos[q], skip=()) for q in old):
                pos.update(old)
                continue
            touched = sorted({n for q in old for n in nets_of.get(q, [])})   # (sorted: the same sum every run)
            new_c = sum(net_cost(n) for n in touched)
            saved = dict(pos)
            pos.update(old)
            old_c = sum(net_cost(n) for n in touched)
            pos.update(saved)
            d = new_c - old_c
            if d > 0 and rng.random() >= math.exp(-d / T):
                pos.update(old)
    # 3. settle: a part still further than its "within d mm" (a requirement, a decoupling cap) goes to the
    # cheapest legal spot round its partner's pin within that distance, if there is one
    mov_set = set(mov) if len(mov) >= 1 else set()
    gap0, gap = gap, min(gap, 0.5)                       # (legal() reads it: a held part may sit closer)
    for n, d_ in NETS.items():
        lim = d_.get("limit")
        if lim is None or len(d_["pins"]) != 2:
            continue
        (ra, pa), (rb, pb) = d_["pins"]
        if ra not in mov_set or rb not in placed:
            continue
        if math.dist(pin_xy(ra, pa), pin_xy(rb, pb)) <= lim:
            continue
        tx, ty = pin_xy(rb, pb)
        best_ = None
        here = pos[ra]
        for a in rots:
            ox_, oy_ = P_[(ra, a)].get(pa, (0.0, 0.0))
            for rr in np.arange(0.0, lim + 1e-9, 0.25):
                for th in np.linspace(0, 2 * math.pi, max(8, int(rr * 12)), endpoint=False):
                    x, y = round((tx + rr * math.cos(th) - ox_) / 0.05) * 0.05, round((ty + rr * math.sin(th) - oy_) / 0.05) * 0.05
                    if not legal(ra, x, y, a):
                        continue
                    pos[ra] = (x, y, a)
                    c_ = sum(net_cost(m) for m in nets_of.get(ra, []))
                    if best_ is None or c_ < best_[0]:
                        best_ = (c_, pos[ra])
                    pos[ra] = here
        if best_ is None:                                # no room: take a spot from parts no rule holds, and
            held = {q for m, dm in NETS.items() if dm.get("limit") is not None for q, _ in dm["pins"]}
            ox_, oy_ = P_[(ra, here[2])].get(pa, (0.0, 0.0))     # place them again where they cost the least
            for rr in np.arange(0.0, lim + 1e-9, 0.25):
                for th in np.linspace(0, 2 * math.pi, max(8, int(rr * 12)), endpoint=False):
                    x, y = round((tx + rr * math.cos(th) - ox_) / 0.05) * 0.05, round((ty + rr * math.sin(th) - oy_) / 0.05) * 0.05
                    b_ = box_of(ra, x, y, here[2])
                    if not inside(b_) or not clear(ra, b_, fixed_obs[side[ra]]):
                        continue
                    g_ = gap - 1e-6
                    blk = [o for o in sorted(placed) if o in mset and o != ra and side[o] == side[ra] and
                           (lambda o_: b_[0] < o_[2] + g_ and o_[0] < b_[2] + g_ and b_[1] < o_[3] + g_ and
                            o_[1] < b_[3] + g_)(box_of(o, *pos[o]))]
                    if not blk or len(blk) > 2 or any(o in held for o in blk):
                        continue
                    keep_ = {o: pos[o] for o in blk}
                    pos[ra] = (x, y, here[2])
                    for o in blk:
                        pos[o] = (1e6, 1e6, pos[o][2])   # (off the board while it looks for a spot)
                    ok_ = True
                    for o in blk:
                        bo = None
                        for a in rots:
                            for j in range(0, len(gx), 2):
                                if not legal(o, gx[j], gy[j], a):
                                    continue
                                pos[o] = (float(gx[j]), float(gy[j]), a)
                                c_ = sum(net_cost(m) for m in nets_of.get(o, []))
                                if bo is None or c_ < bo[0]:
                                    bo = (c_, pos[o])
                                pos[o] = (1e6, 1e6, a)
                        if bo is None:
                            ok_ = False
                            break
                        pos[o] = bo[1]
                    if ok_:
                        best_ = (0.0, pos[ra])
                        break
                    pos.update(keep_)
                    pos[ra] = here
                if best_ is not None:
                    break
        if best_ is not None:
            pos[ra] = best_[1]
    gap = gap0                                           # (lined up at the full gap: the routing's room kept)
    _align(board, [r for r in mov if r not in many], pos, rots, legal, net_cost, nets_of)
    for r in move:
        if r in placed:
            c = board.comps[r]
            c["at"], c["rot"] = (round(pos[r][0], 3), round(pos[r][1], 3)), pos[r][2]
            c.pop("unplaced", None)
    board.__dict__.pop("_pads_memo", None)
    return failed


def _align(board: Board, small: Sequence[str], pos: dict, rots, legal, net_cost, nets_of, grid: float = 0.5,
           tol: float = 1.5) -> None:
    """autoplace's last step, a tidy board: the small parts (``small``) of one footprint turned the same way, each
    on a ``grid`` mm grid, and a run of them side by side lined up in a row (or a column) at even gaps; each change
    kept only where it is legal and costs at most ``tol`` mm of wire a part"""
    def cost(rs):
        return sum(net_cost(n) for n in sorted({n for r in rs for n in nets_of.get(r, [])}))

    def take(ch):                                        # {ref: (x, y, a)}: kept if legal and nearly free
        old = {r: pos[r] for r in ch}
        c0 = cost(ch)
        pos.update(ch)
        if all(legal(r, *pos[r]) for r in ch) and cost(ch) <= c0 + tol * len(ch) + 1e-9:
            return True
        pos.update(old)
        return False
    g = lambda v: round(v / grid) * grid
    side = lambda r: board.comps[r].get("side", "F")
    groups: Dict[tuple, List[str]] = {}
    for r in sorted(small):
        groups.setdefault((board.comps[r]["fp"], side(r)), []).append(r)
    for rs in groups.values():
        # 1. one way round: the group's usual axis, each part turned to it where it is, else a grid step or a few off
        axis = int(sum(pos[r][2] % 180 == 0 for r in rs) * 2 < len(rs)) * 90
        for r in rs:
            if pos[r][2] % 180 != axis:
                x, y, _ = pos[r]
                for p in sorted(((g(x) + i * grid, g(y) + j * grid) for i in range(-4, 5) for j in range(-4, 5)),
                                key=lambda p: (math.hypot(p[0] - x, p[1] - y), p)):
                    if any(take({r: (p[0], p[1], a)}) for a in rots if a % 180 == axis):
                        break
        # 2. on the grid: the nearest grid point, else the others of its cell
        for r in rs:
            x, y, a = pos[r]
            for p in sorted({(gx_, gy_) for gx_ in (g(x), g(x) + grid, g(x) - grid) for gy_ in (g(y), g(y) + grid, g(y) - grid)},
                            key=lambda p: (abs(p[0] - x) + abs(p[1] - y), p)):
                if abs(p[0] - x) <= grid and abs(p[1] - y) <= grid and take({r: (p[0], p[1], a)}):
                    break
        # 3. rows and columns: neighbours (one apart a little across, a few mm along) on one line, evenly spaced
        for k in (1, 0):                                 # (k: the coordinate shared; 1 a row, 0 a column)
            runs, seen = [], set()
            for r in rs:
                if r in seen:
                    continue
                run, todo = [], [r]
                while todo:
                    q = todo.pop()
                    if q in seen:
                        continue
                    seen.add(q)
                    run.append(q)
                    todo += [o for o in rs if o not in seen and pos[o][2] % 180 == pos[q][2] % 180 and
                             abs(pos[o][k] - pos[q][k]) <= 1.5 and abs(pos[o][1 - k] - pos[q][1 - k]) <= 6.0]
                if len(run) >= 2:
                    runs.append(sorted(run, key=lambda q: (pos[q][1 - k], q)))
            for run in runs:
                line = g(sorted(pos[q][k] for q in run)[len(run) // 2])
                at = lambda q, v, u: (v, u, pos[q][2]) if k == 1 else (u, v, pos[q][2])
                if not take({q: at(q, pos[q][1 - k], line) for q in run}):
                    for q in run:
                        take({q: at(q, pos[q][1 - k], line)})
                on = [q for q in run if abs(pos[q][k] - line) < 1e-6]
                if len(on) >= 3:
                    lo, hi = pos[on[0]][1 - k], pos[on[-1]][1 - k]
                    step = g((hi - lo) / (len(on) - 1))
                    take({q: at(q, lo + i * step, line) for i, q in enumerate(on)})


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


def near_target(board: Board, name: str):
    """a near-rule's end: a part ("U1") -> ("U1", None), or one of its pins ("U1.9", "U1.VDDA", by number or by its
    symbol's name) -> ("U1", [pad numbers]); None when the board has no such part or pin"""
    if name in board.comps:
        return name, None
    ref, _, pin = name.partition(".")
    if not pin or ref not in board.comps:
        return None
    import parts
    nums = parts.resolve(board.comps[ref], pin)
    return (ref, nums) if nums else None


def near_gaps(board: Board, near=None) -> List[tuple]:
    """(a, b, dmax, gap) for each near-rule (``board.near``, or the ones given): the gap pad to pad, mm, between
    the parts or pins it names; a rule naming what the board lacks is left out"""
    from shapely.ops import unary_union
    near = getattr(board, "near", []) if near is None else near
    if not near:
        return []
    by, pad_by = {}, {}
    for ref, num, L, g, _ in board.pad_shapes():
        by.setdefault(ref, []).append(g)
        pad_by.setdefault((ref, num), []).append(g)

    def shape(t):
        return unary_union(by[t[0]] if t[1] is None else [g for n in t[1] for g in pad_by.get((t[0], n), [])])
    out = []
    for a, b, dmax in near:
        ta, tb = near_target(board, a), near_target(board, b)
        if ta and tb and ta[0] in by and tb[0] in by:
            out.append((a, b, dmax, shape(ta).distance(shape(tb))))
    return out


def near_misses(board: Board) -> List[str]:
    """the parts (or pins) further apart than ``board.near`` asks (the requirements' "within d mm", pad to pad)"""
    return [f"{a} is {d:.1f} mm from {b} (within {dmax})" for a, b, dmax, d in near_gaps(board) if d > dmax]


PROGRESS = None    # a callable given {"kind": "tries", ...} and {"kind": "try", ...} as place_and_route goes


def _progress(**ev):
    if PROGRESS is not None:
        try:
            PROGRESS(ev)
        except Exception:                                # (a viewer's trouble never stops a run)
            pass


def _one_try(board: Board, move, k: int, gap: float, route_kw, router: str, kw) -> tuple:
    """one placement and routing of ``place_and_route``: (score, problems, placement, tracks, vias, fan, line,
    clean, router, seconds)"""
    import time as _t
    t0_ = _t.perf_counter()
    left = autoplace(board, move=move, gap=gap + 0.25 * min(k, 3), seed=k, **kw)   # (later tries: other seeds at
    if left:                                             # the same room, not ever more than fits; a part that
        probs = [f"{r}: no room to place it (a bigger board, smaller parts, or both sides)" for r in left]
    else:                                                # found no room is not routed round: said at once)
        probs = route_board(board, router, **(route_kw or {}))
        probs += [p for p in board.check() if p not in probs]
    miss = near_misses(board)
    score = severity(probs) + 50 * len(left) + 5 * len(miss)   # (a part left off the board is worse than any miss;
    # a short worse than a slip; a requirement's "within d mm" missed counts)
    line = f"place and route, try {k + 1}: " + ("clean" if not probs and not miss and not left else
           "; ".join(([f"{len(probs)} problems ({probs[0][:70]})"] if probs else []) + miss[:2] +
                     ([f"no room for {', '.join(left)}"] if left else [])))
    return (score, probs, {r: (board.comps[r]["at"], board.comps[r]["rot"]) for r in move},
            list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())), line,
            not probs and not miss, getattr(board, "router_used", None), round(_t.perf_counter() - t0_, 2))


def _try_worker(blob: bytes, move, k, gap, route_kw, router, kw) -> tuple:
    import pickle
    global _NO_LATEST
    _NO_LATEST = True                                    # (tries in parallel: no routing reused from another try)
    return _one_try(pickle.loads(blob), move, k, gap, route_kw, router, kw)


_NO_LATEST = False


def place_and_route(board: Board, move: Optional[Sequence[str]] = None, tries: int = 6, gap: float = 1.0,
                    route_kw: Optional[dict] = None, router: str = "freerouting", workers: Optional[int] = None,
                    **kw) -> List[str]:
    """``autoplace`` then ``autoroute``, and when the routing is not clean, place again (another seed) and route
    again, up to ``tries`` times; keeps the placement and routing with the fewest problems (``kw`` go to
    ``autoplace``, ``route_kw`` to ``autoroute``, e.g. ``{"grid": 0.2}``). ``workers``: tries run that many at a
    time in separate processes (default: one per CPU, at most 4; ``PCB_WORKERS`` overrides); the first clean try,
    in order, is kept, so the result is the same as one at a time. Returns that routing's problems (empty: placed,
    routed, and check() clean)"""
    import os
    import pickle
    move = list(move) if move is not None else None
    if move is None:
        move = [r for r, c in board.comps.items() if c.get("unplaced")] or \
               [r for r, c in board.comps.items() if not c["overhang"]]
    mark_user_copper(board)                              # (the script's own copper, before any try routes)
    tracks0, vias0, fan0 = list(board.tracks), list(board.vias), set(getattr(board, "_fan", set()))
    tries = max(1, tries)
    if workers is None:
        workers = int(os.environ.get("PCB_WORKERS") or min(4, os.cpu_count() or 1))
    workers = max(1, min(workers, tries))
    best, results = None, {}
    _progress(kind="tries", tries=tries, workers=workers)

    def _done(k):
        print(results[k][6], flush=True)
        _progress(kind="try", k=k, seconds=results[k][9], clean=results[k][7], line=results[k][6])
    board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)   # (the first try alone: most
    results[0] = _one_try(board, move, 0, gap, route_kw, router, kw)              # boards are clean on it)
    _done(0)
    if results[0][7] or tries == 1:
        workers = 0
    if workers > 1:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
        board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)
        blob = pickle.dumps(board)
        for k0 in range(1, tries, workers):              # (each try in a process of its own, forked afresh: a
            wave = range(k0, min(tries, k0 + workers))  # pool's worker carried its memos from one try into the
            exs = {k: ProcessPoolExecutor(max_workers=1, mp_context=ctx) for k in wave}   # next, so which try
            try:                                         # ran where changed the boards)
                futs = {k: exs[k].submit(_try_worker, blob, move, k, gap, route_kw, router, kw) for k in wave}
                for k in sorted(futs):
                    results[k] = futs[k].result()
                    _done(k)
            finally:
                for ex in exs.values():
                    ex.shutdown(wait=True)
            if any(results[k][7] for k in wave):
                break
    elif workers == 1:
        for k in range(1, tries):
            board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)   # (each try afresh)
            results[k] = _one_try(board, move, k, gap, route_kw, router, kw)
            _done(k)
            if results[k][7]:
                break
    for k in sorted(results):                            # (the first clean try, else the fewest problems)
        if results[k][7]:
            best = results[k]
            break
        if best is None or results[k][0] < best[0]:
            best = results[k]
    for r, (at, rot) in best[2].items():
        board.comps[r]["at"], board.comps[r]["rot"] = at, rot
        board.comps[r].pop("unplaced", None)
    board.tracks, board.vias, board._fan = best[3], best[4], best[5]
    board.router_used = best[8] or "not routed: parts without room"
    board.__dict__.pop("_pads_memo", None)
    board.__dict__.pop("_cu_memo", None)
    return best[1]


def _drill_gap(at, drill: float, w: dict) -> float:
    """drill wall to drill wall, a via's hole at ``at`` to ``w`` (a via, or a part's hole: an oval slot by its
    shape, not its centre)"""
    import math
    if w.get("geom") is not None:
        from shapely.geometry import Point
        return Point(at).buffer(drill / 2, 16).distance(w["geom"])
    return math.hypot(at[0] - w["at"][0], at[1] - w["at"][1]) - drill / 2 - w["drill"] / 2


def nudge_vias(board: Board, gap: Optional[float] = None, reach: float = 0.15) -> List[str]:
    """vias closer than ``gap`` (default: the board's ``hole_gap``; a house's rule, e.g. 0.5 mm for JLCPCB, tightens
    it) to another via of another net or to a part's own drill (a pin's hole, an exposed pad's thermal via, a bare
    hole), as a router that does not know the drill gap leaves them: the via (the one with fewer tracks on it, of
    two vias) moves away by the shortfall (at most ``reach``), the ends of its tracks with it, when every clearance
    still holds where it lands. Returns the pairs it could not part."""
    import math
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    gap = board.hole_gap if gap is None else gap
    same = 0.254                                         # (one net's drills: the houses' same-net gap)
    left: List[str] = []

    def ends(v):
        return [(t, i) for t in board.tracks if t["net"] == v["net"] for i in (0, -1)
                if math.hypot(t["pts"][i][0] - v["at"][0], t["pts"][i][1] - v["at"][1]) < 0.02]

    def short(a, b):
        d = _drill_gap(a["at"], a["drill"], b)
        return d < (gap if a["net"] != b["net"] else same) - 1e-6, d
    pn = board.pad_net()                                 # (the parts' own drills: they do not move)
    pinned = [{"net": pn.get(tuple(h["name"].split(" pad "))) if " pad " in h["name"] else None, "at": h["at"],
               "drill": h["d"], "geom": h["geom"], "fixed": True} for h in board.drill_list()
              if not h["name"].startswith("via ")]          # (a slot too: by its shape)
    for _ in range(2):
        bad = [(a, b) for i, a in enumerate(board.vias) for b in board.vias[i + 1:] + pinned if short(a, b)[0]]
        if not bad:
            break
        fixed = board.copper(pours=False)
        for a, b in bad:
            if not short(a, b)[0]:
                continue
            moved = False
            for v, o in sorted(((a, b),) if b.get("fixed") else ((a, b), (b, a)), key=lambda p: len(ends(p[0]))):
                if v.get("user") or any(t.get("user") for t, _ in ends(v)):
                    continue                             # (the script's via, or one its tracks end at: as drawn)
                others = {L: unary_union([g for (nt, l), g in fixed.items() if l == L and nt != v["net"]] or [Point(0, 0).buffer(0)])
                          for L in board.layers}

                def lands(at):                           # every clearance and every drill gap holds there
                    if any(Point(at).buffer(v["dia"] / 2).distance(others[L]) < board.clear - 1e-6
                           for L in board.layers if not others[L].is_empty):   # (an empty layer's distance: nan)
                        return False
                    for t, i in ends(v):
                        pts = list(t["pts"]); pts[i] = at
                        seg = LineString(pts[-2:] if i == -1 else pts[:2]).buffer(t["w"] / 2)
                        if seg.distance(others[t["layer"]]) < board.clear - 1e-6:
                            return False
                    return all(_drill_gap(at, v["drill"], w)
                               >= (gap if w["net"] != v["net"] else same) - 1e-6
                               for w in board.vias + pinned if w is not v)
                dx, dy = v["at"][0] - o["at"][0], v["at"][1] - o["at"][1]
                a0 = math.atan2(dy, dx)                  # straight away first, then turning off it, nearest first
                tries = [(round(v["at"][0] + r * math.cos(a0 + da), 4), round(v["at"][1] + r * math.sin(a0 + da), 4))
                         for r in [reach * k / 8 for k in range(1, 9)]
                         for da in [math.radians(t) for t in (0, 30, -30, 60, -60, 90, -90, 120, -120)]]
                at = next((p_ for p_ in tries if lands(p_)), None)
                ok = at is not None
                if ok:
                    for t, i in ends(v):
                        t["pts"] = list(t["pts"]); t["pts"][i] = at
                    v["at"] = at
                    moved = True
                    board.__dict__.pop("_cu_memo", None)
                    fixed = board.copper(pours=False)
                    break
            if not moved:
                left.append(f"vias {a['net']} at {a['at']} and {b['net']} at {b['at']}: no room to part them")
    return left


def neck_vias(board: Board, neck: Optional[float] = None, width: float = 0.2, reach: float = 1.0) -> List[str]:
    """vias against an SMD pad of their own net (closer than ``neck``, default fab.VIA_PAD_NECK, to it, and not a via-in-pad: the ring wholly on
    the pad), as a router leaves them: solder wicks down the hole and a small part tombstones. The via moves off the
    pad until ``neck`` of track lies between them (at most ``reach``), its tracks' ends with it and a track from the pad
    to it, where every clearance and drill gap still holds. Returns the vias it could not move."""
    import math
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    from fab import VIA_PAD_NECK                         # (the critic's own: what it warns of, this keeps clear)
    neck = VIA_PAD_NECK if neck is None else neck
    pn = board.pad_net()
    smd = [(r, n, L, g) for r, n, L, g, d in board.pad_shapes() if not d and L in ("F", "B")]
    holes = [{"at": h["at"], "drill": h["d"], "net": pn.get(tuple(h["name"].split(" pad "))) if " pad " in h["name"]
              else None, "geom": h["geom"]} for h in board.drill_list() if not h["name"].startswith("via ")]
    left: List[str] = []

    def against(v, at):                                  # the own-net SMD pads too near a via at ``at``
        ring = Point(at).buffer(v["dia"] / 2, 16)
        return [(r, n, L, g) for r, n, L, g in smd if pn.get((r, n)) == v["net"] and not g.contains(ring)
                and ring.distance(g) < neck - 1e-6]

    for v in list(board.vias):
        if v.get("user"):                                # (the script's own via: where it drew it)
            continue
        near = against(v, v["at"])
        if not near:
            continue
        r0, n0, L0, g0 = near[0]
        fixed = board.copper(pours=False)
        others = {L: unary_union([g for (nt, l), g in fixed.items() if l == L and nt != v["net"]] or [Point(0, 0).buffer(0)])
                  for L in board.layers}
        ends = [(t, i) for t in board.tracks if t["net"] == v["net"] for i in (0, -1)   # (every end the via's ring
                if math.hypot(t["pts"][i][0] - v["at"][0], t["pts"][i][1] - v["at"][1]) < v["dia"] / 2]  # holds:
        #                                                    FreeRouting ends a track off the via's centre too)
        if any(t.get("user") for t, _ in ends):          # (a hand-drawn track ends there: not dragged along)
            left.append(f"via {v['net']} at {v['at']}: against {r0}.{n0}, the script's own track ends in it")
            continue
        pad_at = g0.representative_point().coords[0]
        mrr = g0.minimum_rotated_rectangle.exterior.coords   # (the track off the pad: as wide as the net needs,
        minor = min(math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2]))   # up to the pad's own width - a 0.2 mm
        wide = round(max(width, min(board.net_width(v["net"]), minor)), 3)   # tie on a 1.5 A ground failed the
        #                                                    current check - else ``width`` where only that fits)
        w_try = [width]

        def lands(at):
            width = w_try[0]
            if against(v, at) or any(Point(at).buffer(v["dia"] / 2).distance(others[L]) < board.clear - 1e-6
                                     for L in board.layers if not others[L].is_empty):
                return False
            segs = [(L0, LineString([pad_at, at]), width)]
            for t, i in ends:
                pts = list(t["pts"]); pts[i] = at
                segs.append((t["layer"], LineString(pts[-2:] if i == -1 else pts[:2]), t["w"]))
            if any(not others[L].is_empty and sg.buffer(w / 2).distance(others[L]) < board.clear - 1e-6
                   for L, sg, w in segs):
                return False
            return all(_drill_gap(at, v["drill"], w)
                       >= (board.hole_gap if w["net"] != v["net"] else 0.254) - 1e-6
                       for w in board.vias + holes if w is not v)
        a0 = math.atan2(v["at"][1] - pad_at[1], v["at"][0] - pad_at[0])   # straight off the pad first, then turning
        tries = [(round(v["at"][0] + r * math.cos(a0 + da), 4), round(v["at"][1] + r * math.sin(a0 + da), 4))
                 for r in [reach * k / 20 for k in range(1, 21)]
                 for da in [math.radians(t) for t in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180)]]
        at = None
        for w_ in dict.fromkeys([wide, width]):
            w_try[0] = w_
            at = next((p_ for p_ in tries if lands(p_)), None)
            if at is not None:
                break
        if at is None:
            left.append(f"via {v['net']} at {v['at']}: against {r0}.{n0}, no room to move it a neck away")
            continue
        for t, i in ends:
            t["pts"] = list(t["pts"]); t["pts"][i] = at
        board.tracks.append({"net": v["net"], "layer": L0, "pts": [tuple(round(c, 4) for c in pad_at), at], "w": w_try[0],
                             "amps": None, **({"neck": True} if w_try[0] < board.net_width(v["net"]) - 1e-6
                                              and minor < board.net_width(v["net"]) - 1e-6 else {})})
        v["at"] = at
        board.__dict__.pop("_cu_memo", None)
    return left


def repair(board: Board, width: float = 0.25, nets: Optional[Sequence[str]] = None, **kw) -> List[str]:
    """the last step of a routing: every net (or those in ``nets``) that a later net's tracks split (a pour cut in
    two, a pad left on a scrap of it) gets its stray groups joined back to its main group. Returns what could not be
    joined"""
    probs = []
    for net in (nets or list(board.nets)):
        if len(board.islands(net)) > 1:
            kw2 = dict(width=width, grid=0.25, via_cost=8.0, layers="".join(L for L in board.layers if L in "FB"),
                       keepout=None, via=(0.3, 0.6), only=None, amps=None, track_keepout=None, seed_pins=None)
            kw2.update(kw)
            probs += _join_islands(board, net, [], kw2)
    return probs


def _join_islands(board: Board, net: str, probs: List[str], kw: dict, only=None) -> List[str]:
    """after routing: a pin the route took as reached that sits on copper cut off from the rest (a scrap of pour
    between tracks) is joined to the net's main group with seed_pins (``only``: just those pins)"""
    if net not in board.nets or any(re.match(rf"route {re.escape(net)}: could not reach", p_) for p_ in probs):
        return probs
    groups = board.islands(net)
    if len(groups) < 2:                                  # whole as copper; KiCad may still read it split, joining
        groups = board.anchor_groups(net)                # items only where a track END lands (a paired pin)
        if len(groups) < 2:
            return probs
    want = set(map(tuple, only)) if only else None
    main = [tuple(p_) for p_ in groups[0]]
    for g in groups[1:]:
        g = [tuple(p_) for p_ in g if want is None or tuple(p_) in want]
        if not g:
            continue
        kw2 = dict(kw, only=g[:1], seed_pins=main)
        board.root_big = board.pour_root = False
        try:
            more = _route_once(board, net, **kw2)
        finally:
            del board.root_big, board.pour_root
        probs = probs + more
        if not more:
            main += g
    return probs


def _route_once(board: Board, net: str, width: float = 0.3, grid: float = 0.25, via_cost: float = 8.0, layers: str = "FB",
          keepout=None, via: Tuple[float, float] = (0.3, 0.6), only: Optional[Sequence[Tuple[str, str]]] = None,
          amps: Optional[float] = None, track_keepout=None,
          seed_pins: Optional[Sequence[Tuple[str, str]]] = None) -> List[str]:
    """Connect the pads of ``net`` as a tree: each pad in turn (or just the pads in ``only``) to the net's copper so
    far (its first pad, its pour, what is already routed), on a ``grid`` keeping ``width / 2 + clearance`` from other
    nets' fixed copper (their pours are carved round the track later) and out of ``keepout`` ({layer: [polygon]}:
    copper to keep off, vias passing that layer included), changing layer through a via where every layer is clear;
    ``track_keepout`` only bars tracks (where an inner layer may not carry signals, a via may still pass). ``layers``
    any of the board's ("F2B": the inner layer 2 too). ``seed_pins``: the pads that count as already on the net, in
    place of the net's pours and its other pads (join a stray island to the main group of ``Board.islands``, where
    a piece of plane cut off by tracks would otherwise pass for the net). Fine-pitch pads leave by a straight stub
    off their outer end.
    Adds tracks and vias to the board; returns the problems (a pad it could not reach)."""
    import numpy as np
    import shapely
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import nearest_points, unary_union
    probs: List[str] = []
    n_route0 = len(board.tracks)
    x0, y0, x1, y1 = board.outline.bounds
    nx, ny = int((x1 - x0) / grid) + 1, int((y1 - y0) / grid) + 1
    xs = x0 + np.arange(nx) * grid
    ys = y0 + np.arange(ny) * grid
    XX, YY = np.meshgrid(xs, ys, indexing="ij")
    keepout = keepout or {}                               # {layer: [polygon]}: copper to keep off (vias too)
    track_keepout = track_keepout or {}                   # {layer: [polygon]}: no tracks there (a via may pass)
    # bare holes as obstacles, sized so that, grown by the clearance like all copper, a track keeps the hole
    # clearance (KiCad's) from a peg's wall and a mounting hole's copper-free radius (its screw head)
    hko_ = getattr(board, "_hole_ko", {})
    holes = unary_union([Point(x, y).buffer(max(d / 2, max(hko_.get((x, y), 0.0), d / 2 + HOLE_CLEAR) - board.clear)
                                            + 0.02, 16) for x, y, d in board.all_holes()] or [Polygon()])
    cu_fixed = board.copper(pours=False)
    pn = board.pad_net()
    pads = {(r, n_): (g, L) for r, n_, L, g, _ in board.pad_shapes()}
    # a part with several pads of one number (a switch's paired pins, a shield's tabs): each is a pad of the net
    # to reach, "1~1", "1~2", ... after the first (KiCad wants copper to every one of them)
    twins: Dict[Tuple[str, str], list] = {}
    for r, n_, L, g, _ in board.pad_shapes():
        if pn.get((r, n_)) == net:
            lst = twins.setdefault((r, n_), [])
            if all(g.centroid.distance(q.centroid) > 0.3 for q, _ in lst):
                lst.append((g, L))
    extra = []
    for (r, n_), lst in twins.items():
        for k, (g, L) in enumerate(lst[1:], 1):
            pads[(r, f"{n_}~{k}")] = (g, L)
            pn[(r, f"{n_}~{k}")] = net
            extra.append((r, f"{n_}~{k}"))
        if len(lst) > 1:
            pads[(r, n_)] = lst[0]
    Ls = list(layers)
    by_layer: Dict[Tuple[str, str], Dict[str, object]] = {}
    for r, n_, L, g, _ in board.pad_shapes():
        by_layer.setdefault((r, n_), {}).setdefault(L, g)
    for key, (g, L) in list(pads.items()):              # a through-hole pad is on every layer: route it on one offered
        if L not in Ls and key in by_layer:              # (keyed by its last layer, a "F only" route refused it)
            L2 = next((l_ for l_ in Ls if l_ in by_layer[key]), None)
            if L2 is not None:
                pads[key] = (by_layer[key][L2], L2)
    corridors = []                                      # other nets' fine-pitch ways out, kept clear 1 mm past
    for t in board.tracks:
        if t.get("fan") and t["net"] != net:
            (ax, ay), (bx, by) = t["pts"][0], t["pts"][-1]
            dl = math.hypot(bx - ax, by - ay) or 1.0
            cl = getattr(board, "corridor", 1.0)            # how far past a stub its way out is kept clear
            corridors.append((t["layer"], LineString([(bx, by), (bx + (bx - ax) / dl * cl, by + (by - ay) / dl * cl)]).buffer(t["w"] / 2)))
    # a keep-out (a pour kept clear of signals) does not wall in this net's own pads: round each, room for its way
    # out and a via (a gate resistor sitting inside a power pour still has to be reached)
    own_zone = {L: unary_union([g.buffer(width + board.clear + via[1] + 0.3, 8) for (r_, n_), (g, l_) in pads.items()
                                if l_ == L and pn.get((r_, n_)) == net] or [Polygon()]) for L in board.layers}
    ko_ = lambda polys, L: [Polygon(k).difference(own_zone.get(L, Polygon())) for k in polys]
    blk0 = {}
    for L in Ls:
        other = unary_union([g for (n, l), g in cu_fixed.items() if l == L and n != net] + [holes] +
                            ko_(keepout.get(L, []) + track_keepout.get(L, []), L) +
                            [Polygon(k) for k in board.keepout_zones(L)] +
                            [c_ for l_, c_ in corridors if l_ == L])
        inside = _cxy(board.outline.buffer(-(width / 2 + max(0.3, board.edge + 0.04))), XX, YY)
        blk0[L] = (_cxy(other.buffer(board.clear + 0.04 + width / 2, 8), XX, YY) if not other.is_empty
                   else np.zeros_like(inside)) | ~inside       # 0.04 mm over the rule: grid snapping stays legal
    vblk, own_tht = None, Polygon()
    if len(Ls) >= 2:
        vb = []
        for L in board.layers:                          # (a via passes every layer: their copper and keep-outs)
            other = unary_union([g for (n, l), g in cu_fixed.items() if l == L and n != net] + [holes] +
                                [Polygon(k) for k in board.keepout_zones(L)] + ko_(keepout.get(L, []), L) +
                                [c_ for l_, c_ in corridors if l_ == L])
            vb.append(_cxy(other.buffer(board.clear + 0.04 + via[1] / 2, 8), XX, YY) if not other.is_empty
                      else np.zeros_like(blk0[Ls[0]]))
        vblk = vb[0] | ~_cxy(board.outline.buffer(-(via[1] / 2 + board.edge + 0.04)), XX, YY)    # (off the board edge)
        for v_ in vb[1:]:
            vblk = vblk | v_
        # a via keeps the hole gap from every drill, this net's own plated holes too, except that inside one of those
        # the layer change needs no via at all (the plated hole joins the layers)
        # (vias already on the board included: this net's own may be reused as they stand)
        drl = [h["geom"] for h in board.drill_list()]  # (each drill as its shape: a USB-C shell's slot is longer
        if drl:                                          #  than it is wide)
            vblk = vblk | _cxy(unary_union([g_.buffer(board.hole_gap + via[0] / 2 + 0.02, 12) for g_ in drl]), XX, YY)
        own_tht = unary_union([g for r_, n_, L_, g, d in board.pad_shapes()
                               if d and L_ == "F" and pn.get((r_, n_)) == net] +
                              [Point(v_["at"]).buffer(v_["dia"] / 2) for v_ in board.vias if v_["net"] == net]
                              or [Polygon()])
        if not own_tht.is_empty:
            vblk = vblk & ~_cxy(own_tht.buffer(-0.05), XX, YY)
    LT = list(dict.fromkeys(["F", "B"] + Ls))            # the layers this net's copper is tracked on
    tree = {L: [] for L in LT}                          # this net's connected copper so far (shapely, per layer)
    seeds = {L: [] for L in LT}                         # what the tree must hang off: pours, the first pin
    cu_net = None
    for L in LT:
        if any(p["net"] == net and p["layer"] == L for p in board.pours):
            if cu_net is None:
                cu_net = board.copper(only_net=net)        # (once for all the net's layers)
            g = board.pour_fill(net, L, cu_net)
            if not g.is_empty:
                tree[L].append(g)
                # the pour's main body is on the net; a sliver cut off by tracks is not, until something joins it
                if seed_pins is None:
                    seeds[L].append(max(getattr(g, "geoms", None) or [g], key=lambda q: q.area))
    fan_geo = {}
    for t in board.tracks:
        if t["net"] == net and t.get("fan"):            # a pad's way out joins the tree with its pad
            fan_geo[t["fan"]] = (t["layer"], LineString(t["pts"]).buffer(t["w"] / 2))
        elif t["net"] == net:
            tree[t["layer"]].append(LineString(t["pts"]).buffer(t["w"] / 2))
    for v in board.vias:
        if v["net"] == net:
            for L in LT:
                tree[L].append(Point(v["at"]).buffer(v["dia"] / 2))
    pins = [tuple(p) for p in board.nets[net]["pins"]]
    pins += [p for p in extra if (p[0], p[1].split("~")[0]) in pins]
    for p_ in [p_ for p_ in pins if p_ not in pads]:      # (a pin its footprint does not have: said, not routed)
        probs.append(f"route {net}: no pad {p_}")
    pins = [p_ for p_ in pins if p_ in pads]
    if not only and getattr(board, "root_big", True):    # grow the net from its biggest pad (a regulator's tab, a
        big = max((p_ for p_ in pins if p_ in pads),     # capacitor), not from a pad boxed in by fine-pitch
                  key=lambda p_: (pads[p_][0].area, -pins.index(p_)), default=None)   # neighbours: everything
        if big is not None:                              # else would have to reach into its box
            pins = [big] + [p_ for p_ in pins if p_ != big]
    todo = [tuple(p) for p in only] if only else pins[1:]
    first = pins[0] if pins else None

    fanned = getattr(board, "_fan", set())

    def stub_w(pin):                                     # a way out of a fine-pitch pad no wider than the pad: a
        mrr = pads[pin][0].minimum_rotated_rectangle.exterior.coords    # full-width track along it runs into the
        return min(width, min(math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2])))   # next pins (a pin entry)

    def stub_track(pin, L, pts):
        w_ = stub_w(pin)
        board.track(net, L, pts, w_, amps)
        if w_ < width - 1e-6:
            board.tracks[-1]["neck"] = True

    def stub_of(pin):
        e = _escape(board, *pin) if "~" not in pin[1] else None
        if e is None:
            return None
        if pin not in fanned:
            stub_track(pin, pads[pin][1], [e[0], e[1]])
        return e[1]
    if not only and getattr(board, "pour_root", True) and any(tree[L] for L in LT):   # the net has a pour: the pour is the root, and every pad,
        todo = [p_ for p_ in pins]                       # the first too, is joined to it (a first pad taken as
        first = None                                     # already on the net may sit apart from the pour)
    if not only and first is not None:
        g0, L0 = pads[first]
        tree[L0].append(g0)
        if first in fan_geo:
            tree[fan_geo[first][0]].append(fan_geo[first][1])
        e = stub_of(first)
        if e is not None:
            tree[L0].append(LineString([g0.centroid.coords[0], e]).buffer(width / 2))
        seeds[L0].append(g0)
    else:                                                # pins not being routed are taken as already on the net
        for p_ in pins:
            if p_ not in todo and p_ in pads:
                if seed_pins is None or p_ in set(map(tuple, seed_pins)):
                    seeds[pads[p_][1]].append(pads[p_][0])
                tree[pads[p_][1]].append(pads[p_][0])
    if any(seeds[L] for L in LT):
        # keep only the copper that hangs off a seed: a hand-drawn piece not yet joined to the net is not "reached"
        parts_ = [(L, q) for L in LT for q in (getattr(unary_union(tree[L]), "geoms", None) or
                                               ([unary_union(tree[L])] if tree[L] else [])) if not q.is_empty]
        par = list(range(len(parts_)))

        def _r(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        links_ = [Point(v["at"]) for v in board.vias if v["net"] == net] + \
                 [g_.centroid for (r_, n_), (g_, l_) in pads.items() if pn.get((r_, n_)) == net and
                  any(pd_["drill"] for pd_ in board.comps[r_]["pads"].get(n_, []))]
        from shapely.strtree import STRtree as _STR
        ptree = _STR([q for _, q in parts_])             # (a tree query within 10 um, not a buffer per part per
        for pt in links_:                                # point: on a ground net that was minutes per call)
            hit = sorted(int(i) for i in ptree.query(pt, predicate="dwithin", distance=0.01))
            for i in hit[1:]:
                par[_r(i)] = _r(hit[0])
        good = {_r(i) for i, (L, q) in enumerate(parts_) if any(q.intersects(sg) for sg in seeds[L])}
        tree = {L: [q for i, (l_, q) in enumerate(parts_) if l_ == L and _r(i) in good] for L in LT}
    own_pads = {L: unary_union([gg for (rr, nn), (gg, ll) in pads.items() if ll == L and pn.get((rr, nn)) == net] or [Polygon()])
                for L in Ls}
    own_free = {L: unary_union([g_.buffer(-width / 2) if not g_.buffer(-width / 2).is_empty
                                else g_.centroid.buffer(grid * 0.5)
                                for (rr, nn), (g_, ll) in pads.items() if ll == L and pn.get((rr, nn)) == net]
                               or [Polygon()]) for L in Ls}
    tg, gm_, seen_ = {}, {}, {}
    for pin in todo:
        if pin not in pads:
            probs.append(f"route {net}: no pad {pin}")
            continue
        g_pin, L_pin = pads[pin]
        if L_pin not in Ls:
            probs.append(f"route {net}: pad {pin} is not on a routing layer")
            continue
        for L in LT:                                     # (the net's copper so far, and the grid cells next to it,
            n0 = seen_.get(L, 0)                         # grown by what each pin's route added: copper only adds)
            if L not in tg or len(tree[L]) != n0:
                new_ = tree[L][n0:] if L in tg else tree[L]
                tg[L] = unary_union(([tg[L]] if L in tg and not tg[L].is_empty else []) + new_) if (new_ or L in tg) \
                    else Polygon()
                if L in Ls:
                    add_ = unary_union(new_).buffer(grid * 0.6) if new_ else Polygon()
                    gm_[L] = (gm_[L] if L in gm_ else np.zeros_like(blk0[L])) | \
                        (_cxy(add_, XX, YY) if not add_.is_empty else False)
                seen_[L] = len(tree[L])
        if not tg[L_pin].is_empty and tg[L_pin].intersects(g_pin.buffer(-0.01)):
            continue
        blk = {L: blk0[L].copy() for L in Ls}
        for L in Ls:                                     # this net's own pads are free where a track stays inside
            if not own_free[L].is_empty:                 # them (one centred at a pad's edge overhangs its neighbour)
                blk[L] &= ~_cxy(own_free[L], XX, YY)
        e = _escape(board, *pin) if "~" not in pin[1] else None
        sx, sy = e[1] if e else (g_pin.centroid.x, g_pin.centroid.y)
        ci, cj = int(round((sx - x0) / grid)), int(round((sy - y0) / grid))
        best = None                                      # the nearest clear cell to the way out, else force it;
        if e:                                            # off a fine-pitch pad, only in line with its stub
            ex, ey = e[1][0] - e[0][0], e[1][1] - e[0][1]
            en = math.hypot(ex, ey) or 1.0
            ex, ey = ex / en, ey / en
        for tol in ((0.02, grid * 0.55) if e else (1e9,)):    # exactly in line first, then nearly
            for di in range(-3, 4):
                for dj in range(-3, 4):
                    a_, b_ = ci + di, cj + dj
                    if 0 <= a_ < nx and 0 <= b_ < ny and not blk[L_pin][a_, b_]:
                        if e and (abs((xs[a_] - sx) * ey - (ys[b_] - sy) * ex) > tol or
                                  (xs[a_] - sx) * ex + (ys[b_] - sy) * ey < -1e-6):
                            continue
                        dd = math.hypot(xs[a_] - sx, ys[b_] - sy)
                        if best is None or dd < best[0]:
                            best = (dd, a_, b_)
            if best:
                break
        forced = best is None and e is not None and blk[L_pin][ci, cj]
        if best:
            ci, cj = best[1], best[2]
        blk[L_pin][ci, cj] = False
        goal = {k: (gm_[L] & ~blk[L]) if not tg[L].is_empty else np.zeros_like(blk[L]) for k, L in enumerate(Ls)}
        found, path = _wavefront_windowed(blk, vblk, goal, [(ci, cj, Ls.index(L_pin))], Ls, via_cost)
        if not found and len(Ls) > 1 and vblk is not None and not vblk[ci, cj] and \
                all(not blk[L][ci, cj] or L == L_pin for L in Ls):
            # boxed in on its own layer (a pad between fine-pitch neighbours): drop through a via where its way out
            # ends and go on from the other layers, as a designer would
            found, path = _wavefront_windowed(blk, vblk, goal, [(ci, cj, k) for k in range(len(Ls))], Ls, via_cost)
        if not found:
            probs.append(f"route {net}: could not reach {pin[0]}.{pin[1]} (blocked; move parts or widen a channel)")
            continue
        n_t, n_v = len(board.tracks), len(board.vias)
        if Ls[path[0][2]] != L_pin and not own_tht.contains(Point(float(xs[ci]), float(ys[cj]))):
            board.via(net, (round(float(xs[ci]), 4), round(float(ys[cj]), 4)), *via)   # (the escape via)
        if e:
            neck_ = [(float(xs[ci]), float(ys[cj]))]
            if forced and len(path) > 1 and path[1][2] == path[0][2]:
                # (no cell past the stub is clear at the full width: the forced one is left at the stub's width,
                # and the track goes on full width only from the first clear cell, as a neck)
                neck_.append((float(xs[path[1][0]]), float(ys[path[1][1]])))
                path = path[1:]
            stub_track(pin, L_pin, ([e[1]] if pin in fanned else [e[0], e[1]]) + neck_)
        else:
            board.track(net, L_pin, [(sx, sy), (float(xs[ci]), float(ys[cj]))], width, amps)
        seg = []
        for (i, j, k) in path:
            pt = (round(float(xs[i]), 4), round(float(ys[j]), 4))
            if seg and seg[-1][2] != k:
                _emit(board, net, Ls[seg[-1][2]], [q[:2] for q in seg], width, amps)
                if not own_tht.contains(Point(pt)):          # (in a plated hole or via of this net: no new via)
                    board.via(net, pt, *via)
                    vgap = Point(pt).buffer(via[0] + board.hole_gap + 0.02, 12)    # the next via keeps its drill gap
                    vblk = vblk | (_cxy(vgap, XX, YY) & ~_cxy(Point(pt).buffer(via[1] / 2 - 0.05), XX, YY))
                    own_tht = own_tht.union(Point(pt).buffer(via[1] / 2))
                seg = []
            seg.append((pt[0], pt[1], k))
        _emit(board, net, Ls[seg[-1][2]], [q[:2] for q in seg], width, amps)
        endL = Ls[path[-1][2]]                          # close the last gap onto the copper it reached
        endp = Point(seg[-1][0], seg[-1][1])
        if not tg[endL].is_empty and not tg[endL].buffer(-0.02).contains(endp):
            # (onto a point well inside what it reached: KiCad joins a track whose end lies inside the pad or track,
            # not one that only overlaps it at an edge or a rounded corner)
            near_ = min(getattr(tg[endL], "geoms", None) or [tg[endL]], key=lambda q_: q_.distance(endp))
            q0 = nearest_points(near_, endp)[0]
            q = None
            for inset in (0.1, 0.06, 0.03, 0.01):        # (as deep as the piece it reached allows, a step past)
                core_ = near_.buffer(-inset, 8)
                if not core_.is_empty:
                    c_ = nearest_points(core_, endp)[0]
                    if c_.distance(q0) <= inset + 0.05:
                        q = c_
                        break
            q = q if q is not None else q0
            if q.distance(endp) > 1e-3:
                board.track(net, endL, [(endp.x, endp.y), (q.x, q.y)], width, amps)
        for t in board.tracks[n_t:]:
            tree[t["layer"]].append(LineString(t["pts"]).buffer(t["w"] / 2))
        for v in board.vias[n_v:]:
            for L in LT:
                tree[L].append(Point(v["at"]).buffer(v["dia"] / 2))
        tree[L_pin].append(g_pin)
        if pin in fan_geo:
            tree[fan_geo[pin][0]].append(fan_geo[pin][1])
    if getattr(board, "prune", True):                    # the dead ends (a way-out stub or fan-out the route joined
        mine = [k for k, t in enumerate(board.tracks) if t["net"] == net]    # part-way along): the whole net's once
        if not probs and len(board.islands(net)) == 1:   # it is whole, else only this call's (a stub of a pin not
            board.prune_spurs(mine)                      # reached yet is its way out, kept for the next call)
        else:
            board.prune_spurs([k for k in mine if k >= n_route0])
    #                                                      board.prune = False keeps a deliberate stub (an antenna)
    return probs


def _emit(board, net, L, pts, width, amps=None):
    out = [pts[0]]
    for p in pts[1:]:
        if len(out) >= 2:
            (ax, ay), (bx, by) = out[-2], out[-1]
            if abs((bx - ax) * (p[1] - by) - (by - ay) * (p[0] - bx)) < 1e-6:
                out[-1] = p
                continue
        out.append(p)
    if len(out) >= 2:
        board.track(net, L, out, width, amps)


def _shift(a, di, dj):
    """out[i, j] = a[i - di, j - dj] (zeros where that falls off the grid)"""
    import numpy as np
    out = np.zeros_like(a)
    n, m = a.shape
    out[max(di, 0):n + min(di, 0), max(dj, 0):m + min(dj, 0)] = a[max(-di, 0):n + min(-di, 0), max(-dj, 0):m + min(-dj, 0)]
    return out


def _wavefront_windowed(blk, vblk, goal, starts, Ls, via_cost, margin: int = 48):
    """_wavefront in a window round the start and the nearest goal cell (``margin`` cells past both), and on the
    whole grid only when the window holds no way through: most connections are local, and the search costs grow
    with the area it floods"""
    import numpy as np
    k_any = [np.argwhere(goal[k]) for k in range(len(Ls))]
    pts = np.concatenate([g for g in k_any if len(g)] or [np.zeros((0, 2), int)])
    if not len(pts):
        return _wavefront(blk, vblk, goal, starts, Ls, via_cost)
    si, sj = starts[0][0], starts[0][1]
    near = pts[np.argmin((pts[:, 0] - si) ** 2 + (pts[:, 1] - sj) ** 2)]
    n, m = goal[0].shape
    i0, i1 = max(0, min(si, near[0]) - margin), min(n, max(si, near[0]) + margin + 1)
    j0, j1 = max(0, min(sj, near[1]) - margin), min(m, max(sj, near[1]) + margin + 1)
    if (i1 - i0) * (j1 - j0) >= 0.6 * n * m:             # (nearly the whole board anyway)
        return _wavefront(blk, vblk, goal, starts, Ls, via_cost)
    w = (slice(i0, i1), slice(j0, j1))
    hit, path = _wavefront({L: blk[L][w] for L in Ls}, vblk[w] if vblk is not None else None,
                           {k: g[w] for k, g in goal.items()}, [(i - i0, j - j0, k) for i, j, k in starts], Ls, via_cost)
    if hit:
        return (hit[0] + i0, hit[1] + j0, hit[2]), [(i + i0, j + j0, k) for i, j, k in path]
    return _wavefront(blk, vblk, goal, starts, Ls, via_cost)


def _wavefront(blk, vblk, goal, starts, Ls, via_cost):
    """Lee router: breadth-first wavefronts on both layers at once (numpy), 8-connected, a via costing ``via_cost``
    steps; returns (found, path [(i, j, k)] from the start to the goal)"""
    import numpy as np
    nL = len(Ls)
    free = [~blk[L] for L in Ls]
    INF = np.iinfo(np.int32).max
    dist = [np.full(free[0].shape, INF, np.int32) for _ in Ls]
    front = [np.zeros_like(free[0]) for _ in Ls]
    for i, j, k in starts:
        dist[k][i, j] = 0
        front[k][i, j] = True
    pending = {}                                        # step -> [(k, mask)] via arrivals
    vc = int(round(via_cost))
    hit = None
    n_, m_ = free[0].shape
    diag = []                                           # per layer: (where a diagonal step lands, where it comes
    for k in range(nL):                                 # from, its two corners clear) - fixed for the whole search
        fr = free[k]
        dl = []
        for di, dj in ((1, 1), (-1, -1), (1, -1), (-1, 1)):    # diagonals only past two clear corners
            dst = (slice(max(di, 0), n_ + min(di, 0)), slice(max(dj, 0), m_ + min(dj, 0)))
            src = (slice(max(-di, 0), n_ + min(-di, 0)), slice(max(-dj, 0), m_ + min(-dj, 0)))
            dl.append((dst, src, (_shift(fr, 0, dj) & _shift(fr, di, 0))[dst]))
        diag.append(dl)
    unvis = [d == INF for d in dist]                    # (kept up to date: no full compare against INF per step)
    nvblk = ~vblk if vblk is not None else None
    for step in range(1, 6000):
        new = []
        for k in range(nL):
            f = front[k]
            g = np.zeros_like(f)
            g[1:, :] |= f[:-1, :]
            g[:-1, :] |= f[1:, :]
            g[:, 1:] |= f[:, :-1]
            g[:, :-1] |= f[:, 1:]
            for dst, src, cm in diag[k]:
                g[dst] |= f[src] & cm
            g &= free[k] & unvis[k]
            new.append(g)
            if nvblk is not None and nL >= 2 and f.any():   # change layer here, arriving vc steps later
                fv = f & nvblk
                for k2 in range(nL):
                    if k2 != k:
                        pending.setdefault(step - 1 + vc, []).append((k2, fv))
        for k2, m in pending.pop(step, []):
            m = m & free[k2] & unvis[k2]
            new[k2] |= m
        any_new = False
        for k in range(nL):
            dist[k][new[k]] = step
            unvis[k] &= ~new[k]
            front[k] = new[k]
            any_new |= bool(new[k].any())
            gk = new[k] & goal[k]
            if gk.any():
                ii, jj = np.argwhere(gk)[0]
                hit = (int(ii), int(jj), k)
                break
        if hit or (not any_new and not pending):
            break
    if not hit:
        return None, []
    path = [hit]
    i, j, k = hit
    d = dist[k][i, j]
    nb = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    while d > 0:
        moved = False
        for di, dj in nb:
            a, b_ = i + di, j + dj
            if 0 <= a < dist[k].shape[0] and 0 <= b_ < dist[k].shape[1] and dist[k][a, b_] == d - 1 and \
                    (not (di and dj) or (free[k][a, j] and free[k][i, b_])):
                i, j, d = a, b_, d - 1
                path.append((i, j, k))
                moved = True
                break
        if not moved and nL >= 2:
            for k2 in range(nL):
                if k2 != k and dist[k2][i, j] == d - vc:
                    k, d = k2, d - vc
                    path.append((i, j, k))
                    moved = True
                    break
        if not moved:
            break
    path.reverse()
    return hit, path


def _routes_json(tracks, vias, fan, probs) -> bytes:
    import gzip
    import json
    return gzip.compress(json.dumps({"tracks": tracks, "vias": vias, "fan": sorted(map(list, fan)), "probs": probs},
                                    separators=(",", ":")).encode(), mtime=0)


def _routes_load(raw: bytes) -> dict:
    import gzip
    import json
    d = json.loads(gzip.decompress(raw))
    tup = lambda o: {k: [tuple(p) for p in v] if k == "pts" else tuple(v) if isinstance(v, list) else v
                     for k, v in o.items()}                     # (JSON lists back to the tuples routing wrote)
    return {"tracks": [tup(t) for t in d["tracks"]], "vias": [tup(v) for v in d["vias"]],
            "fan": {tuple(f) for f in d["fan"]}, "probs": d["probs"]}


def cached_route(board: Board, do_route, tag: str = "", store: Optional[str] = None) -> List[str]:
    """run ``do_route(board)`` (which adds tracks and vias and returns problems) once per design: the routed copper
    is cached by a hash of the placement, nets, pours and holes, so a program re-run per MOTION pose does not re-route.
    ``store``: a folder kept in git where the routed copper is also written (``<board>_<hash>.json.gz``, the board's
    older ones removed; only a routing with no problems), so a fresh clone loads the committed routing instead of
    re-routing, which is slow and need not give the same copper with other library versions. With the environment
    variable ``PCB_REROUTE=1`` every cache and the store are ignored (and nothing is written): routing from scratch"""
    import hashlib
    import json
    import os
    import pickle
    from vendor_parts import CACHE
    key = json.dumps({"tag": tag, "comps": {r: [c["fp"], c["at"], c["rot"]] + (["B"] if c.get("side", "F") == "B" else [])
                                         for r, c in sorted(board.comps.items())},
                      "nets": board.nets, "pours": board.pours, "holes": board.holes, "clear": board.clear,
                      "oz": board.oz, "tracks": board.tracks, "vias": board.vias,
                      "keepouts": getattr(board, "keepouts", []),
                      **({"gaps": [board.hole_gap, board.edge]} if (board.hole_gap, board.edge) != (HOLE_GAP, EDGE)
                         else {})}, sort_keys=True, default=str)
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    board._route_key = h
    f = CACHE / "pcb_routes" / f"{board.name}_{h}.pkl"
    kept = Path(store) / f"{board.name}_{h}.json.gz" if store else None
    fresh = bool(os.environ.get("PCB_REROUTE"))         # (route from scratch: a router change measured honestly)
    if fresh:
        d, kept = None, None
    elif f.exists():
        d = pickle.loads(f.read_bytes())
    elif kept is not None and kept.exists():
        d = _routes_load(kept.read_bytes())
    else:
        d = None
    if d is None:
        probs = do_route(board)
        d = {"tracks": board.tracks, "vias": board.vias, "fan": getattr(board, "_fan", set()), "probs": probs}
        if not fresh:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(pickle.dumps(d))
    board.tracks, board.vias, board._fan = d["tracks"], d["vias"], d.get("fan", set())
    clean = not d["probs"] and not any(("split" in p_ or re.match(r"\S+\.Cu: .+ (short|mm apart)", p_) or
                                        "bare hole" in p_) for p_ in board.check()) \
        if kept is not None and not kept.exists() else False
    if clean:                                            # (only clean routing goes into git: joined by KiCad's rule,
        # no shorts, clearance kept; a check finding about the design itself, a hole spacing, does not hold it back)
        kept.parent.mkdir(parents=True, exist_ok=True)
        for old in kept.parent.glob(f"{board.name}_*.json.gz"):
            os.remove(old)
        kept.write_bytes(_routes_json(board.tracks, board.vias, board._fan, d["probs"]))
    return d["probs"]






def placement(board: Board) -> dict:
    """each part's footprint, position, rotation and side: what a saved routing was routed round"""
    return {r: (c["fp"], tuple(c["at"]), c["rot"], c.get("side", "F")) + ((c["pads_key"],) if c.get("pads_key") else ())
            for r, c in board.comps.items()}                  # (pads_key: its pads differ from the footprint's)


def reuse_routes(board: Board, old: dict, rip: Sequence[Tuple[float, float, float]] = ()) -> set:
    """put back the routing of an earlier placement where it still holds, so moving a few parts re-routes only
    what they disturb. ``old``: {"placement": placement(board) then, "tracks": [...], "vias": [...]} (tracks and
    vias added by the routing, or all of them: ones already on the board are not added twice). A net's old copper
    goes back unless one of its parts moved or any of it now comes within the clearance of another net's pads or
    copper on the board, or (rip-up) within one of the circles ``rip`` ((x, y, r): round a pad that could not be
    reached, so the net that failed there can route first). Returns the nets left out (route them again), every
    net of a moved part included."""
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    now = placement(board)
    moved = {r for r in set(now) | set(old["placement"]) if now.get(r) != old["placement"].get(r)}
    pn = board.pad_net()
    bad = {n for (r, _), n in pn.items() if r in moved}
    for n, d in board.nets.items():                      # (a net that lost or gained a moved part's pin)
        if any(r in moved for r, _ in d["pins"]):
            bad.add(n)
    k_t = lambda t: (t["net"], t["layer"], tuple(tuple(round(c, 4) for c in p) for p in t["pts"]), round(t["w"], 4))
    k_v = lambda v: (v["net"], tuple(round(c, 4) for c in v["at"]))
    mine, theirs = {}, {}                                # (the copper the script draws, now and when it was routed:
    for it in board.tracks + board.vias:                 #  a net whose hand-drawn copper changed gets no old routing
        if it.get("user"):                               #  back - it ran beside the old copper - and the old hand-
            mine.setdefault(it["net"], set()).add(k_t(it) if "pts" in it else k_v(it))   # drawn copper itself never
    for it in old["tracks"] + old["vias"]:               #  comes back)
        if it.get("user"):
            theirs.setdefault(it["net"], set()).add(k_t(it) if "pts" in it else k_v(it))
    bad |= {n for n in set(mine) | set(theirs) if mine.get(n, set()) != theirs.get(n, set())}
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) for t in board.tracks}
    have_v = {(v["net"], tuple(v["at"])) for v in board.vias}
    have_v_list = list(board.vias)
    old_t = [t for t in old["tracks"] if (t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) not in have_t
             and not t.get("fan") and not t.get("user")]  # (escape stubs are laid afresh each build, not reused)
    old_v = [v for v in old["vias"] if (v["net"], tuple(v["at"])) not in have_v and not v.get("user")]

    def shape(it):
        if "pts" in it:
            return it["layer"], LineString(it["pts"]).buffer(it["w"] / 2, 8)
        return None, Point(it["at"]).buffer(it["dia"] / 2, 8)     # (a via: every layer)
    # what is on the board now: every pad, and the tracks and vias already there
    obst = [(L, n, g, d) for r, num, L, g, d in board.pad_shapes() for n in [pn.get((r, num))]] + \
           [(L, it["net"], g, 0) for it in board.tracks + board.vias for L, g in [shape(it)]]
    tree = STRtree([g for _, _, g, _ in obst])
    circles = [Point(x, y).buffer(r, 16) for x, y, r in rip]
    inner = board.outline.buffer(-board.edge)
    drills = [(g.centroid, d) for *_, g, d in board.pad_shapes() if d] + \
             [(Point(x, y), d) for x, y, d in board.all_holes()] + [(Point(v["at"]), v["drill"]) for v in board.vias]
    dtree = STRtree([c for c, _ in drills]) if drills else None
    for it in old_t + old_v:
        n = it["net"]
        if n in bad:
            continue
        L, g = shape(it)
        if any(g.intersects(c) for c in circles) or not inner.contains(g):
            bad.add(n)
            continue
        if "pts" not in it and dtree is not None:        # a via keeps the drill gap (the rules may have changed)
            c0 = Point(it["at"])
            if any(c0.distance(drills[int(i)][0]) - (it["drill"] + drills[int(i)][1]) / 2 < board.hole_gap - 1e-6
                   for i in dtree.query(c0.buffer(it["drill"] / 2 + board.hole_gap + 3))):
                bad.add(n)
                continue
        for i in tree.query(g.buffer(board.clear)):
            Lo, no, go, dr = obst[int(i)]
            if no == n:
                continue
            if L is not None and Lo is not None and Lo != L and not dr:
                continue                                  # (another layer; a plated hole is on all of them)
            if g.distance(go) < board.clear - 1e-6:
                bad.add(n)
                break
    # and clear of each other: a short between two kept nets (an earlier routing's own fault) sends both back
    kept = [(it, n, L, g) for it in old_t + old_v if it["net"] not in bad for n in [it["net"]] for L, g in [shape(it)]]
    kt = STRtree([g for *_, g in kept])
    for i, (it, n, L, g) in enumerate(kept):
        for j in kt.query(g.buffer(board.clear)):
            _, n2, L2, g2 = kept[int(j)]
            if n2 == n or (L is not None and L2 is not None and L2 != L):
                continue
            if g.distance(g2) < board.clear - 1e-6:
                bad |= {n, n2}
    n0 = len(board.tracks)
    for t in old_t:
        if t["net"] not in bad:
            board.tracks.append(t)
    for v in old_v:
        if v["net"] not in bad:
            board.vias.append(v)
    # a kept track whose end no longer lands inside its net's copper (a pad reshaped, a stub gone) goes too, and
    # so does a net it left whole that KiCad's end-point rule splits
    loose = {board.tracks[k]["net"] for k, _ in board.loose_ends(only=range(n0, len(board.tracks)))}
    poured = {pz["net"] for pz in board.pours}
    cu_ = board.copper(pours=False)
    failed = {m.split()[1].rstrip(":") for m in old.get("got", []) if m.startswith("route ")}
    loose |= {n for n in {t["net"] for t in board.tracks[n0:]} - poured - bad - failed
              if n in board.nets and len(board.anchor_groups(n, cu_)) > 1}
    if loose:
        bad |= loose
        keep_t = board.tracks[:n0] + [t for t in board.tracks[n0:] if t["net"] not in loose]
        board.vias = [v for v in board.vias if v["net"] not in loose or v in have_v_list]
        board.tracks = keep_t
    return bad


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


def route_jobs(board: Board, jobs: Sequence[Tuple[str, float, Optional[Sequence[Tuple[str, str]]]]], kw=None,
               passes: int = 3, rip: float = 2.5) -> List[str]:
    """route a board's signal nets the way a big board needs, in one call: ``jobs`` [(net, width, only-pins or
    None)] in order (local nets first), ``kw(net)`` the rest of ``route()``'s arguments for that net (grid, layers,
    keep-outs, ...); a job may add a fourth item, a dict of ``route()`` arguments for that job alone (e.g.
    ``{"amps": 0.01}`` for a sense line off a power net; ``"rejoin": False`` leaves a net that later nets split to
    the caller, e.g. a ground joined afterwards by stitching vias).
    - Negotiated passes: a net that fails is routed first on the next pass; the best of ``passes`` is kept.
    - Checkpoints: the result is saved per design (``<name>_<route key>_signals.pkl``, so a failure in the steps
      after routing costs nothing) and as ``<name>_signals_latest.pkl``.
    - Incremental: with no checkpoint for this design but an earlier routing on file, ``reuse_routes`` puts back
      what the changed placement does not disturb, tears up nets within ``rip`` mm of each pad that failed last
      time, and only those nets, the failed ones first, route again: moving a few parts costs minutes, not the
      whole board.
    Leaves the routing on the board; returns its problems."""
    import pickle
    from vendor_parts import CACHE
    kw = kw or (lambda net: {})
    # (keyed by the design: cached_route's key, else a hash of the board as it stands - placement, nets, the copper
    # drawn so far - and the jobs, so a changed design never loads another design's routing)
    if getattr(board, "_route_key", None):
        rkey = board._route_key
    else:
        import hashlib
        rkey = hashlib.sha1((board._state_key() + repr(list(jobs)) + f"r{ROUTER_VERSION}").encode()).hexdigest()[:16]
    ckpt = CACHE / "pcb_routes" / f"{board.name}_{rkey}_signals.pkl"
    latest = CACHE / "pcb_routes" / f"{board.name}_signals_latest_r{ROUTER_VERSION}.pkl"
    jobs = list(jobs)
    import os
    fresh = bool(os.environ.get("PCB_REROUTE"))
    best = pickle.loads(ckpt.read_bytes()) if ckpt.exists() and not fresh else None
    if best is None and latest.exists() and not fresh and not _NO_LATEST:
        old = pickle.loads(latest.read_bytes())
        failed0 = {m.split()[1].rstrip(":") for m in old["got"] if m.startswith("route ")}
        circles = []
        for m in old["got"]:
            if "reach " in m:
                ref, num = m.split("reach ")[1].split()[0].rsplit(".", 1)
                if ref in board.comps and num in board.comps[ref]["pads"]:
                    circles.append(board.pad(ref, num) + (rip,))
        redo = reuse_routes(board, old, rip=circles) | failed0
        redo |= {j[0] for j in jobs if j[0] in board.nets and len(board.islands(j[0])) > 1}   # (left split last time)
        jobs = [j for j in jobs if j[0] in failed0] + [j for j in jobs if j[0] in redo and j[0] not in failed0]
        print(f"reusing the last routing: {len(jobs)} nets to route again", flush=True)
    base = (list(board.tracks), list(board.vias))
    computed = best is None
    for _ in range(0 if best else passes):
        board.tracks, board.vias = list(base[0]), list(base[1])
        got, failed = [], []
        for job in jobs:
            net, w, pins = job[:3]
            ex = {k: v for k, v in (job[3] if len(job) > 3 else {}).items() if k != "rejoin"}
            p = route(board, net, width=w, only=pins, **{**kw(net), **ex})
            got += p
            if p:
                failed.append(job)
        for job in {j[0]: j for j in jobs}.values():     # a net a later one split (a pour cut in two by the nets
            net, w = job[0], job[1]                       # routed after it, now that failures go first) joined back
            if (job[3] if len(job) > 3 else {}).get("rejoin", True) is False:
                continue                                  # (a net joined later by the caller: stitching vias)
            if net in board.nets and job not in failed and (len(board.islands(net)) > 1 or
                                                            len(board.anchor_groups(net)) > 1):
                ex = {k: v for k, v in {**kw(net), **(job[3] if len(job) > 3 else {})}.items() if k != "rejoin"}
                more = repair(board, width=w, nets=[net], **{k: v for k, v in ex.items() if k != "only"})
                got += more
                if more:
                    failed.append(job)
        if best is None or len(got) < len(best[0]):
            best = (got, list(board.tracks), list(board.vias))
        if not failed:
            break
        jobs = failed + [j for j in jobs if j not in failed]
    from shapely.geometry import LineString, Point
    if computed and best is not None and best[0]:        # rip up and retry: the nets round each pad still
        for rr in (rip, 2 * rip):                        # unreached torn up, the failed nets routed first, the
            board.tracks, board.vias = list(best[1]), list(best[2])   # torn ones after; kept if it does better
            fails, circles = set(), []
            for m in best[0]:
                mm = re.match(r"route (\S+): could not reach (\S+) \(blocked", m)
                if mm:
                    fails.add(mm.group(1))
                    ref, num = mm.group(2).rsplit(".", 1)
                    if ref in board.comps and num in board.comps[ref]["pads"]:
                        circles.append(Point(*board.pad(ref, num)).buffer(rr))
            if not circles:
                break
            from shapely.ops import unary_union as _uu
            zone = _uu(circles)
            torn = {t["net"] for t in board.tracks if not t.get("fan") and t["net"] not in fails
                    and zone.intersects(LineString(t["pts"]))} | \
                   {v["net"] for v in board.vias if v["net"] not in fails and zone.contains(Point(v["at"]))}
            poured_ = {pz["net"] for pz in board.pours}
            torn -= poured_                              # (a ground's pins drop through vias: leave it be)
            redo = fails | torn
            board.tracks = [t for t in board.tracks if t["net"] not in redo or t.get("fan") or t.get("user")]
            board.vias = [v for v in board.vias if v["net"] not in redo or v["net"] in poured_ or v.get("user")]
            order = [j for j in jobs if j[0] in fails] + [j for j in jobs if j[0] in torn]
            got = []
            for job in order:
                ex = {k: v for k, v in (job[3] if len(job) > 3 else {}).items() if k != "rejoin"}
                got += route(board, job[0], width=job[1], only=job[2], **{**kw(job[0]), **ex})
            got = [m for m in best[0] if m.split(":")[0].split()[-1] not in redo] + got
            got += [f"route {n}: split after the rip-up" for n in sorted(redo)          # (a net left in pieces counts)
                    if n in board.nets and len(board.islands(n)) > 1 and not any(f"route {n}:" in m for m in got)]
            print(f"rip-up within {rr} mm of {len(circles)} pads ({len(torn)} nets torn up): "
                  f"{len(best[0])} problems before, {len(got)} after", flush=True)
            if len(got) < len(best[0]):
                best = (got, list(board.tracks), list(board.vias))
            if not best[0]:
                break
    if best is not None and not best[0]:                 # every net whole: trim way-out stubs a route ran past
        board.tracks, board.vias = list(best[1]), list(best[2])
        if board.prune_spurs():
            best = (best[0], list(board.tracks), list(board.vias))
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(pickle.dumps(best))
    board.tracks, board.vias = best[1], best[2]
    latest.write_bytes(pickle.dumps({"placement": placement(board), "got": best[0], "tracks": best[1],
                                     "vias": best[2]}))
    return list(best[0])


def kicad_models(board: Board, model_dir: str) -> List[str]:
    """fetch the KiCad library 3D models the board's parts name (``model`` paths, into the parts cache) and lay
    them out under ``model_dir`` as KiCad's 3D library is laid out, for ``render_kicad``. Returns the paths it
    could not get"""
    import shutil
    import urllib.request
    from pathlib import Path
    from vendor_parts import CACHE, KICAD_RAW
    missing = []
    for c in board.comps.values():
        path = c.get("model")
        if not path or not isinstance(path, str):
            continue
        dst = CACHE / "kicad" / Path(path).name
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
                    with urllib.request.urlopen(KICAD_RAW + cand, timeout=120) as r:
                        data = r.read()
                    if data.startswith(b"ISO-10303-21"):
                        got = data
                        break
                except Exception:
                    continue
            if got is None:
                missing.append(path)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(got)
        out = Path(model_dir) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            shutil.copy(dst, out)
    return sorted(set(missing))


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
