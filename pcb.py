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

KICAD_FP_RAW = "https://gitlab.com/kicad/libraries/kicad-footprints/-/raw/master/"
LOCAL_FP = Path(__file__).parent / "pcb_footprints"   # project footprints KiCad's library lacks: <Lib>.pretty/<Name>.kicad_mod
CU_RHO = 1.72e-5            # ohm mm (copper, 20 C)
HOLE_GAP = 0.25             # mm between drill walls (a common fab minimum)
EDGE = 0.25                 # mm from copper (tracks, vias) to the board edge
MIN_FILL = 0.1              # mm: a pour's narrowest copper (KiCad's zone min_thickness)
OZ_MM = 0.0347              # 1 oz/ft^2 copper thickness
_FP: Dict[str, Dict] = {}


# ---- footprints ------------------------------------------------------------------------------------------------------
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


def _find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _fp_file(name: str) -> Path:
    """where footprint "Lib:Name" lives: the project library (``LOCAL_FP``) first, else KiCad's, cached"""
    lib, fp = name.split(":")
    local = LOCAL_FP / f"{lib}.pretty" / f"{fp}.kicad_mod"
    if local.exists():
        return local
    from vendor_parts import CACHE
    return CACHE / "kicad_fp" / f"{fp}.kicad_mod"


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
    tree = _sexpr(dst.read_text())
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
    _FP[name] = {"pads": pads, "courtyard": crt, "ref": ref, "silk": silk, "model": model}
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


def ipc2221_width(amps: float, oz: float, rise_c: float = 20.0, external: bool = True) -> float:
    """track width (mm) carrying ``amps`` with a ``rise_c`` temperature rise on ``oz`` copper (IPC-2221)"""
    k = 0.048 if external else 0.024
    area_mil2 = (amps / (k * rise_c ** 0.44)) ** (1 / 0.725)
    return area_mil2 / (oz * 1.378) * 0.0254


# ---- the board -------------------------------------------------------------------------------------------------------
class Board:
    hole_gap = HOLE_GAP                                  # drill wall to drill wall, mm
    edge = EDGE                                          # copper to the board edge, mm

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
    def place(self, ref: str, fp: str, at: Tuple[float, float], rot: float = 0.0, value: str = "", mpn: str = "",
              model: Optional[str] = None, model_offset=(0.0, 0.0, 0.0), body=None, note: str = "",
              overhang: bool = False, side: str = "F", lcsc: str = "", min_drill: Optional[float] = None):
        """a part: footprint "Lib:Name", its origin at ``at`` turned ``rot`` deg ccw (seen from above); ``model`` a
        KiCad 3D model path (vendor_parts.kicad; default: the one the footprint names) or ``body`` a build123d shape in
        the footprint frame for one KiCad lacks. ``side="B"`` puts it on the bottom: the footprint is flipped about its y axis (KiCad's flip), its SMD
        pads go to B.Cu, its model hangs under the board. ``lcsc``: the LCSC / JLCPCB part number (C-number) for
        assembly and the cart (``fab``). ``min_drill``: the thermal vias in an exposed pad (a plated hole whose pad
        number also has SMD copper) drilled smaller than this are enlarged to it, keeping their copper ring (default:
        the board's fab preset's plated-drill minimum; a part's pin holes are never changed: its leads must fit)"""
        pads = footprint(fp)["pads"]
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
        self.comps[ref] = {"fp": fp, "at": tuple(at), "rot": rot, "value": value, "mpn": mpn, "model": model,
                           "model_offset": model_offset, "body": body, "note": note, "overhang": overhang,
                           "pads": pads, "side": side, "lcsc": lcsc,
                           "pads_key": f"{min_drill}" if min_drill else ""}    # (the pads differ from the footprint's)

    def courtyard(self, ref: str):
        """a part's courtyard outline in board coordinates (flipped for a bottom-side part), or None"""
        from shapely import affinity
        from shapely.geometry import box
        c = self.comps[ref]
        crt = footprint(c["fp"])["courtyard"]
        if not crt:
            return None
        if c.get("side", "F") == "B":
            crt = (-crt[2], crt[1], -crt[0], crt[3])
        return affinity.translate(affinity.rotate(box(*crt), c["rot"], origin=(0, 0)), *c["at"])

    def net(self, name: str, pins: Sequence[Tuple[str, str]], amps: float = 0.1, volts: float = 5.0,
            width: Optional[float] = None):
        """a net: its pins (ref, pad number), the current it carries (sets how wide its copper must be) and its
        voltage (what its drop is measured against); ``width``: its track width when routed (default: as wide as
        its current needs, at least 0.2 mm)"""
        self.nets[name] = {"pins": [(r, str(p)) for r, p in pins], "amps": amps, "volts": volts}
        if width is not None:
            self.nets[name]["width"] = width

    def net_width(self, name: str) -> float:
        """the width ``name`` is routed at: its own, else what its current needs on the outer layers (min 0.2 mm)"""
        d = self.nets[name]
        return d.get("width") or max(0.2, round(self.width_for(d["amps"], "F") + 0.005, 2))

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
                 [(Point(x, y_), d) for x, y_, d in self.holes]
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
        drills = unary_union([Point(x, y).buffer(d / 2 + self.hole_gap + via[0] / 2, 12) for x, y, d in self.holes] +
                             [g.centroid.buffer(d / 2 + self.hole_gap + via[0] / 2, 12)
                              for _, _, _, g, d in self.pad_shapes() if d] +
                             [Point(v["at"]).buffer(v["drill"] / 2 + self.hole_gap + via[0] / 2, 12) for v in self.vias]
                             or [Point(0, 0).buffer(0)])
        inside = self.outline.buffer(-(via[1] / 2 + 0.3))
        kz = unary_union([__import__("shapely.geometry", fromlist=["Polygon"]).Polygon(k["poly"])
                          for k in getattr(self, "keepouts", [])] or [Point(0, 0).buffer(0)])
        shapes = {(r, n_, L): g for r, n_, L, g, _ in self.pad_shapes()}
        import shapely
        for g_ in list(others.values()) + [drills, inside, kz]:   # (tested thousands of times per call: prepared)
            shapely.prepare(g_)
        for ref, num in pins:
            c = self.comps[ref]
            L = "B" if c.get("side", "F") == "B" else "F"
            g = shapes.get((ref, num, L))
            if g is None:                                    # a plated through-hole already reaches the plane
                continue
            p0 = g.centroid
            r0 = max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2
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
                    trk = LineString([(p0.x, p0.y), (q.x, q.y)]).buffer(width / 2 + self.clear + 0.04, 8)
                    if trk.intersects(others[L]):
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
                                "w": width, "amps": amps})
            self.vias.append({"net": net, "at": (round(best.x, 4), round(best.y, 4)), "drill": via[0], "dia": via[1]})
            drills = drills.union(best.buffer(via[0] + self.hole_gap, 12))
            shapely.prepare(drills)
        return probs

    def hole(self, at: Tuple[float, float], d: float = 3.2, keepout: Optional[float] = None):
        """a mounting hole; ``keepout`` the copper-free radius round it (the standoff's or the screw head's)"""
        self.holes.append((at[0], at[1], d))
        self._hole_ko = getattr(self, "_hole_ko", {})
        self._hole_ko[(at[0], at[1])] = keepout if keepout is not None else d / 2 + 0.6

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
        self._fan = getattr(self, "_fan", set())
        pn = self.pad_net()
        for (ref, num), n in pn.items():
            if (ref, num) in self._fan or ref not in self.comps or num not in self.comps[ref]["pads"]:
                continue
            e = _escape(self, ref, num, reach)
            if e:
                self.track(n, "B" if self.comps[ref].get("side", "F") == "B" else "F", [e[0], e[1]], width, 0.01)
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
                             for x, y, d in self.holes] or [Polygon()])
        inner = self.outline.buffer(-0.3)
        clear_of: Dict[Tuple[str, str], object] = {}
        kz_of: Dict[str, object] = {}
        for pz in (self.pours if pours else []):
            if only_net is not None and pz["net"] != only_net:
                continue
            L = pz["layer"]
            if (pz["net"], L) not in clear_of:
                clear_of[(pz["net"], L)] = unary_union(
                    [g for (n, l), g in fixed.items() if l == L and n != pz["net"]] or [Polygon()]
                ).buffer(self.clear + 0.05, 8)
            if L not in kz_of:
                kz_of[L] = unary_union([Polygon(k) for k in self.keepout_zones(L)] or [Polygon()])
            area = Polygon(pz["poly"], pz.get("holes") or None).intersection(inner).difference(
                clear_of[(pz["net"], L)]).difference(holes).difference(kz_of[L])
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
        items = [(L, pn.get((r, n)), g, i) for i, (r, n, L, g, d) in enumerate(pads)] + \
                [(L, v["net"], Point(v["at"]).buffer(v["dia"] / 2, 16), ("v", k))
                 for k, v in enumerate(self.vias) for L in self.layers] + \
                [(t["layer"], t["net"], LineString(t["pts"]).buffer(t["w"] / 2, 12), ("t", k))
                 for k, t in enumerate(self.tracks)] + \
                [(L, n, g, ("p", n, L)) for (n, L), g in cu.items()
                 if any(pz["net"] == n and pz["layer"] == L for pz in self.pours)]
        thru = {i for i, (r, n, L, g, d) in enumerate(pads) if d}
        st = STRtree([g for _, _, g, _ in items])
        out = []
        for k in (range(len(self.tracks)) if only is None else only):
            t = self.tracks[k]
            for end in (t["pts"][0], t["pts"][-1]):
                pt = Point(end)
                if not any(n == t["net"] and tag != ("t", k) and (L == t["layer"] or tag in thru)
                           for L, n, g, tag in (items[int(j)] for j in
                                                st.query(pt, predicate="dwithin", distance=0.005))):
                    out.append((k, tuple(end)))
        return out

    def prune_spurs(self, tracks: Optional[Sequence[int]] = None, rounds: int = 4) -> int:
        """trim the dangling ends of ``tracks`` (default: all) back to where they last lie inside other copper of
        their net, and drop tracks that reach nothing past their other end: a fine-pitch pad's way-out stub that the
        route joined part-way along, or a closing segment that overshot, otherwise leaves a spur KiCad reads as
        unconnected. Never splits a net (only the dead end is cut). Returns how many track ends it changed"""
        from shapely.geometry import LineString, Point
        from shapely.ops import substring, unary_union
        changed = 0
        idx = set(range(len(self.tracks)) if tracks is None else tracks)
        for _ in range(rounds):
            loose = [(k, e) for k, e in self.loose_ends(only=sorted(i for i in idx if i < len(self.tracks)))]
            if not loose:
                break
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
                else:
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
                    if nodes[j][0] in ("zone", "track") and nodes[j][1] & Ls and \
                            nodes[j][2].intersection(g).area > 1e-4:
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
        cu = self.copper() if cu is None else cu
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
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
            for t in self.tracks:
                if t["layer"] == L:
                    routed.setdefault(t["net"], []).append(_LS(t["pts"]).buffer(t["w"] / 2, 8))
            for v in self.vias:
                routed.setdefault(v["net"], []).append(Point(v["at"]).buffer(v["dia"] / 2, 12))
            pcu = cu
            for pz in self.pours:
                if pz["layer"] == L:
                    g = pcu.get((pz["net"], L))
                    if g is not None:
                        routed.setdefault(pz["net"], []).append(g.intersection(Polygon(pz["poly"], pz.get("holes") or None)))
            for ref, num, l, g, _ in pads:
                if l == L:
                    padg.setdefault(pn.get((ref, num), f"~{ref}.{num}"), {}).setdefault(ref, []).append(g)
            nets_ = sorted(set(routed) | set(padg))
            R_ = {n: unary_union(routed.get(n, [])) if routed.get(n) else None for n in nets_}
            A_ = {n: unary_union([g for lst in padg.get(n, {}).values() for g in lst] + ([R_[n]] if R_[n] is not None else []))
                  for n in nets_}
            P_ = {n: {r: unary_union(lst) for r, lst in padg.get(n, {}).items()} for n in nets_}
            # only the pairs whose extents come within the clearance (an STR tree over the nets' bounding boxes)
            from shapely.strtree import STRtree
            from shapely.geometry import box as _box
            env = [_box(*A_[n].bounds).buffer(self.clear, join_style=2) if not A_[n].is_empty else _box(0, 0, 0, 0)
                   for n in nets_]
            near = STRtree(env)
            cand = sorted({(min(i, int(j)), max(i, int(j))) for i, e in enumerate(env) for j in near.query(e)
                           if int(j) != i})
            for i, j in cand:
                na, nb = nets_[i], nets_[j]
                if na.startswith("~") and nb.startswith("~"):
                    continue
                if not shapely.dwithin(A_[na], A_[nb], self.clear - 2e-3):     # (stops at the first close pair:
                    continue                                                # a full distance is far dearer)
                ds = []
                if R_[na] is not None:
                    ds.append((R_[na].distance(A_[nb]), R_[na], A_[nb]))
                if R_[nb] is not None:
                    ds.append((R_[nb].distance(A_[na]), R_[nb], A_[na]))
                for ra, ga in P_[na].items():
                    for rb, gb in P_[nb].items():
                        if ra != rb:
                            ds.append((ga.distance(gb), ga, gb))
                bad = [d_ for d_ in ds if d_[0] < self.clear - 2e-3]
                if bad:
                    dist, ga, gb = min(bad, key=lambda d_: d_[0])
                    from shapely.ops import nearest_points as _np2
                    pa_, pb_ = _np2(ga, gb)                           # (where they come nearest)
                    at = __import__("shapely.geometry", fromlist=["Point"]).Point((pa_.x + pb_.x) / 2, (pa_.y + pb_.y) / 2)
                    what = "short" if dist < 1e-6 else f"{dist:.2f} mm apart (clearance {self.clear})"
                    probs.append(f"{L}.Cu: {na} and {nb} {what} at ({at.x:.1f}, {at.y:.1f})")
        for p_, n_, skew in getattr(self, "pairs", []):       # differential pairs kept matched in length
            lp, ln = self.net_length(p_), self.net_length(n_)
            if abs(lp - ln) > skew:
                probs.append(f"pair {p_} / {n_}: tracks {lp:.2f} and {ln:.2f} mm long, {abs(lp - ln):.2f} mm apart "
                             f"(keep within {skew} mm: lengthen the shorter, or route them side by side)")
        # current: tracks and pour necks
        for t in self.tracks:
            d = self.nets.get(t["net"])
            if not d:
                probs.append(f"track on unknown net {t['net']}")
                continue
            amps = t["amps"] if t["amps"] is not None else d["amps"]
            need = self.width_for(amps, t["layer"])
            if t["w"] + 1e-6 < need:
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
            cs_arr = _np_.array(cs_, dtype=object)
            live = _np_.array([not part.is_empty for part in cs_])
            for pg in pins:                              # (within reach, by distance: no buffer per piece per pin)
                hit = [int(i) for i in _np_.nonzero(live & shapely.dwithin(cs_arr, pg, need / 2 + 0.5))[0]]
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
        holes_ = [(f"via {v['net']}", _Pt(v["at"]), v["drill"]) for v in self.vias] + \
                 [(f"{ref} pad {num}", g.centroid, d) for ref, num, L, g, d in pads if d and L == "F"] + \
                 [("hole", _Pt(x, y), d) for x, y, d in self.holes]
        tight = []
        from shapely.strtree import STRtree as _STRh       # (the pairs within reach only: every pair was 0.4 M
        _ht = _STRh([p for _, p, _ in holes_]) if holes_ else None    # distances on a 56-part board)
        _reach = self.hole_gap + max((d for _, _, d in holes_), default=0.0)
        for i in range(len(holes_)):
            for j in sorted(int(j) for j in _ht.query(holes_[i][1], predicate="dwithin", distance=_reach)):
                if j <= i:
                    continue
                (na, pa, da), (nb, pb, db) = holes_[i], holes_[j]
                gap = pa.distance(pb) - da / 2 - db / 2
                if gap < self.hole_gap - 1e-6 and not (na == nb and pa.distance(pb) < 1e-6):
                    tight.append(f"{na} and {nb} drills {gap:.2f} mm apart at ({pa.x:.1f}, {pa.y:.1f}) "
                                 f"(keep {self.hole_gap} mm)")
        probs += tight[:20] + ([f"... and {len(tight) - 20} more drills too close"] if len(tight) > 20 else [])
        inner = self.outline.buffer(-self.edge)               # copper keeps the edge gap from the routed edge
        for ref, num, L, g, _ in pads:
            if not inner.contains(g):
                probs.append(f"{ref} pad {num or '(mount)'} ({L}.Cu) reaches the board edge: copper keeps {self.edge} mm inside")
                break
        from shapely.geometry import LineString as _LS
        for it in self.tracks + self.vias:
            g = _LS(it["pts"]).buffer(it["w"] / 2, 8) if "pts" in it else _Pt(it["at"]).buffer(it["dia"] / 2, 8)
            if not inner.contains(g):
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
                if cy[refs[i]].intersection(cy[refs[j]]).area > 0.05:
                    probs.append(f"courtyards of {refs[i]} and {refs[j]} overlap")
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
                    except Exception:
                        m = None
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
    neighbours); the attempt with the fewest problems is kept. Returns that attempt's problems."""
    kw = dict(width=width, grid=grid, via_cost=via_cost, layers=layers, keepout=keepout, via=via, only=only, amps=amps,
              track_keepout=track_keepout, seed_pins=seed_pins)
    forced = [(a, getattr(board, a)) for a in ("root_big", "pour_root") if hasattr(board, a)]
    if forced or only:                                   # (a caller's own choice, or a partial route: one attempt)
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
        score = (len(probs) + split, len(board.vias) - len(v0))
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
            kw2 = dict(kw, width=neck, only=left)        # current allows
            again = _route_once(board, net, **kw2)
        finally:
            del board.root_big, board.pour_root
        probs = [p_ for p_ in probs if not re.match(rf"route {re.escape(net)}: could not reach", p_)] + again
    return _join_islands(board, net, probs, kw, only)


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
    nets = [n for n, d in board.nets.items() if len(d["pins"]) >= 2 or n in poured]
    sig = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] <= 0.3], key=spread)
    pwr = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] > 0.3],
                 key=lambda n: (board.nets[n]["amps"], spread(n)))
    last = sorted([n for n in nets if n in poured], key=lambda n: board.nets[n]["amps"])
    if not getattr(board, "_fan", None):
        board.fanout(width=0.2)
    if drop:                                             # each surface pad of a net poured on another layer drops
        pn = board.pad_net()                             # through a via of its own beside it, first, while there is
        for net in last:                                 # room (the ground pins of a two-layer board, a plane's)
            layers = {p["layer"] for p in board.pours if p["net"] == net}
            pins = [tuple(p) for p in board.nets[net]["pins"]
                    if any(L_ not in layers and not d for r_, n_, L_, g, d in board.pad_shapes() if (r_, n_) == tuple(p))]
            if pins:
                board.drop(net, pins, width=min(0.3, board.net_width(net)))
    jobs = [(n, board.net_width(n), None) for n in sig + pwr + last]
    probs = route_jobs(board, jobs, kw=(lambda net: dict(kw)) if kw else None, passes=passes)
    return probs + repair(board, **kw)


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
    holes = unary_union([Point(x, y).buffer(d / 2 + 0.3, 16) for x, y, d in board.holes] or [Polygon()])
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
        drl = [(g.centroid, d) for r_, n_, L_, g, d in board.pad_shapes() if d and L_ == "F"] + \
              [(Point(v_["at"]), v_["drill"]) for v_ in board.vias]
        if drl:
            vblk = vblk | _cxy(unary_union([c.buffer(d / 2 + board.hole_gap + via[0] / 2 + 0.02, 12) for c, d in drl]), XX, YY)
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
    if not only and getattr(board, "root_big", True):    # grow the net from its biggest pad (a regulator's tab, a
        big = max((p_ for p_ in pins if p_ in pads),     # capacitor), not from a pad boxed in by fine-pitch
                  key=lambda p_: (pads[p_][0].area, -pins.index(p_)), default=None)   # neighbours: everything
        if big is not None:                              # else would have to reach into its box
            pins = [big] + [p_ for p_ in pins if p_ != big]
    todo = [tuple(p) for p in only] if only else pins[1:]
    first = pins[0] if pins else None

    fanned = getattr(board, "_fan", set())

    def stub_of(pin):
        e = _escape(board, *pin) if "~" not in pin[1] else None
        if e is None:
            return None
        if pin not in fanned:
            board.track(net, pads[pin][1], [e[0], e[1]], width, amps)
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
    for pin in todo:
        if pin not in pads:
            probs.append(f"route {net}: no pad {pin}")
            continue
        g_pin, L_pin = pads[pin]
        if L_pin not in Ls:
            probs.append(f"route {net}: pad {pin} is not on a routing layer")
            continue
        tg = {L: unary_union(tree[L]) if tree[L] else Polygon() for L in LT}
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
        if best:
            ci, cj = best[1], best[2]
        blk[L_pin][ci, cj] = False
        goal = {k: (_cxy(tg[L].buffer(grid * 0.6), XX, YY) & ~blk[L]) if not tg[L].is_empty else np.zeros_like(blk[L])
                for k, L in enumerate(Ls)}
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
            board.track(net, L_pin, ([e[1]] if pin in fanned else [e[0], e[1]]) + [(float(xs[ci]), float(ys[cj]))],
                        width, amps)
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
    re-routing, which is slow and need not give the same copper with other library versions"""
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
    if f.exists():
        d = pickle.loads(f.read_bytes())
    elif kept is not None and kept.exists():
        d = _routes_load(kept.read_bytes())
    else:
        d = None
    if d is None:
        probs = do_route(board)
        d = {"tracks": board.tracks, "vias": board.vias, "fan": getattr(board, "_fan", set()), "probs": probs}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(pickle.dumps(d))
    board.tracks, board.vias, board._fan = d["tracks"], d["vias"], d.get("fan", set())
    if kept is not None and not kept.exists() and not d["probs"]:      # (only clean routing goes into git)
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
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) for t in board.tracks}
    have_v = {(v["net"], tuple(v["at"])) for v in board.vias}
    have_v_list = list(board.vias)
    old_t = [t for t in old["tracks"] if (t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) not in have_t
             and not t.get("fan")]                       # (escape stubs are laid afresh each build, not reused)
    old_v = [v for v in old["vias"] if (v["net"], tuple(v["at"])) not in have_v]

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
             [(Point(x, y), d) for x, y, d in board.holes] + [(Point(v["at"]), v["drill"]) for v in board.vias]
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


def _text_poly(txt: str, x: float, y: float, size: float, angle: float = 0.0):
    """``txt`` as filled outline polygons (a sans font from matplotlib), ``size`` mm cap height, centred on (x, y),
    turned ``angle`` degrees: silkscreen text a fab can print"""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    from shapely import affinity
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    tp = TextPath((0, 0), txt, size=size * 1.38, prop=FontProperties(family="DejaVu Sans", weight="bold"))
    shape = None
    for ring in tp.to_polygons():                        # rings alternate outline / hole: even-odd fill
        if len(ring) < 3:
            continue
        p_ = Polygon(ring).buffer(0)
        shape = p_ if shape is None else shape.symmetric_difference(p_)
    if shape is None or shape.is_empty:
        return Polygon()
    c = shape.envelope.centroid
    shape = affinity.translate(shape, x - c.x, y - c.y)
    return affinity.rotate(shape, angle, origin=(x, y)) if angle else shape


def silkscreen(board: Board, layer: str = "F"):
    """the silkscreen of one side as shapely geometry: each part's footprint silk lines (as KiCad draws them) and its
    reference designator where ``place_refs`` finds room, the board's own ``silk_text``; clipped clear of the pads
    and the drills (a fab clips silk off exposed copper)"""
    import math
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    geoms = []
    for ref, c in board.comps.items():
        side = c.get("side", "F")
        if side != layer:
            continue
        ax, ay = c["at"]
        a = math.radians(c["rot"])
        ca, sa = math.cos(a), math.sin(a)
        mx = -1.0 if side == "B" else 1.0

        def tf(px, py):
            px *= mx
            return (ax + px * ca - py * sa, ay + px * sa + py * ca)
        for x1, y1, x2, y2, w in footprint(c["fp"]).get("silk", []):
            geoms.append(LineString([tf(x1, y1), tf(x2, y2)]).buffer(max(w, 0.1) / 2, 4))
    for ref, at in place_refs(board).items():
        if at and board.comps[ref].get("side", "F") == layer:
            x, y, ang, size = at
            geoms.append(_text_poly(ref, x, y, size, ang))
    if layer == "F":
        for txt, (x, y), size, ang in getattr(board, "silk_text", []):
            geoms.append(_text_poly(txt, x, y, size, ang))
    silk = unary_union([g for g in geoms if not g.is_empty]) if geoms else None
    if silk is None or silk.is_empty:
        return silk
    keep_off = unary_union([g.buffer(0.1) for _, _, L, g, d in board.pad_shapes() if L == layer or d] +
                           [Point(x, y).buffer(d / 2 + 0.2) for x, y, d in board.holes] +
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
        pads = unary_union([g.buffer(0.05) for r, n, l, g, d in board.pad_shapes() if l == L] or [])
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
    pts = list(board.outline.exterior.coords)
    body.append(f"X{fmt(pts[0][0])}Y{fmt(pts[0][1])}D02*")
    body += [f"X{fmt(x)}Y{fmt(y)}D01*" for x, y in pts[1:]]
    files.append(write("Edge_Cuts.gm1", "Profile,NP", body))
    drills = {}
    for r, n, l, g, d in board.pad_shapes():
        if d and l == "F":
            drills.setdefault(("PTH", d), []).append((g.centroid.x, g.centroid.y))
    for v in board.vias:
        drills.setdefault(("PTH", v["drill"]), []).append(v["at"])
    for x, y, d in board.holes:
        drills.setdefault(("NPTH", d), []).append((x, y))
    for kind in ("PTH", "NPTH"):
        tools = sorted(d for (k, d) in drills if k == kind)
        if not tools:
            continue
        lines = ["M48", "METRIC,TZ", f"; {kind}"] + [f"T{i + 1}C{d:.3f}" for i, d in enumerate(tools)] + ["%", "G05"]
        for i, d in enumerate(tools):
            lines.append(f"T{i + 1}")
            lines += [f"X{x:.3f}Y{y:.3f}" for x, y in drills[(kind, d)]]
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
            cur.append(_Q(re.sub(r'\\(.)', r'\1', t[1:-1])) if t.startswith('"') else t)
    return cur[0] if cur else []


def _sx_atom(v) -> str:
    if isinstance(v, _Q):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
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
    fills so): holes taken leftmost first, each bridged from its leftmost vertex straight left to the nearest edge"""
    from shapely.geometry.polygon import orient
    out = []
    for p in (getattr(geom, "geoms", None) or [geom]):
        if p.is_empty or not hasattr(p, "exterior") or p.area < 1e-6:
            continue
        p = orient(p, 1.0)                                    # outline ccw, holes cw
        ring = list(p.exterior.coords)[:-1]
        holes = [list(h.coords)[:-1] for h in p.interiors]
        for h in sorted(holes, key=lambda h: min(x for x, y in h)):
            k = min(range(len(h)), key=lambda i: (h[i][0], h[i][1]))
            px, py = h[k]
            best = None                                       # the nearest edge crossed going left from (px, py)
            for i in range(len(ring)):
                (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
                if (y1 <= py < y2) or (y2 <= py < y1):
                    x = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                    if x <= px and (best is None or x > best[0]):
                        best = (x, i)
            if best is None:
                continue
            x, i = best
            hole = h[k:] + h[:k] + [h[k]]
            ring = ring[:i + 1] + [(x, py)] + hole + [(x, py)] + ring[i + 1:]
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
            "duplicate_pad_numbers_are_jumpers", "jumper_pad_groups", "component_classes"}


def place_refs(board: "Board", sizes: Sequence[float] = (1.0, 0.8), gap: float = 0.25) -> Dict[str, Optional[Tuple]]:
    """reference designators placed for the silkscreen as a fab expects them: each part's label next to its body,
    clear of every pad (silkscreen over exposed copper is clipped off by the fab), of the drills, of the board edge,
    of the other parts' bodies and of the other labels; 1.0 mm text, else 0.8 mm (the usual fab minimum), else
    none (the label stays on the assembly layer, as on a dense board). {ref: (x, y, angle, size) or None}, board mm."""
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    side = lambda r: board.comps[r].get("side", "F")
    pads = {L: unary_union([g.buffer(gap, 8) for _, _, l, g, _ in board.pad_shapes() if l == L] or [Point(0, 0).buffer(0)])
            for L in "FB"}
    holes = unary_union([Point(x, y).buffer(d / 2 + gap, 16) for x, y, d in board.holes] +
                        [g.centroid.buffer(d / 2 + gap, 16) for _, _, _, g, d in board.pad_shapes() if d] or [Point(0, 0).buffer(0)])
    inside = board.outline.buffer(-0.5)
    crt = {r: board.courtyard(r) for r in board.comps}
    taken = {"F": [], "B": []}
    for txt, (x, y), size, ang in getattr(board, "silk_text", []):     # (the board's own text: a title)
        w, h = len(txt) * size * 0.78 + 0.2, size + 0.25
        w, h = (w, h) if abs(ang % 180) < 45 else (h, w)
        taken["F"].append(box(x - w / 2, y - h / 2, x + w / 2, y + h / 2).buffer(gap))
    out: Dict[str, Optional[Tuple]] = {}
    order = sorted(board.comps, key=lambda r: crt[r].area if crt[r] is not None else 0.0)
    for r in order:
        L = side(r)
        body = crt[r] if crt[r] is not None else Point(*board.comps[r]["at"]).buffer(1.0)
        bodies = unary_union([crt[o] for o in board.comps if o != r and crt[o] is not None and side(o) == L] or
                             [Point(0, 0).buffer(0)])
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
            for x, y, a in cands:
                tw, th = (w, h) if a == 0.0 else (h, w)
                bb = box(x - tw / 2, y - th / 2, x + tw / 2, y + th / 2)
                if (inside.contains(bb) and not bb.intersects(pads[L]) and not bb.intersects(holes)
                        and not bb.intersects(bodies) and not bb.intersects(body)
                        and not any(bb.intersects(t) for t in taken[L])):
                    found = (round(x, 3), round(y, 3), a, size)
                    taken[L].append(bb.buffer(gap))
                    break
            if found:
                break
        out[r] = found
    return out


def _kicad_footprint(board: "Board", ref: str, c: Dict, netnum: Dict[str, int], tx, ty, label="keep",
                     body_dir: Optional[str] = None) -> list:
    """one placed part as a board footprint: the library's own .kicad_mod (pads, silkscreen, courtyard, fab, model)
    at the part's place, turned, flipped to the bottom for side B (mirrored in x, KiCad's left / right flip, as
    ``Board.place`` does), its pads on their nets"""
    lib, name = c["fp"].split(":")
    footprint(c["fp"])                                       # fetched into the cache
    tree = _sx_parse(_fp_file(c["fp"]).read_text())
    back = c.get("side", "F") == "B"
    rot = c["rot"] % 360.0
    pn = board.pad_net()
    flipL = lambda s: _Q(("B." + s[2:]) if s.startswith("F.") else ("F." + s[2:]) if s.startswith("B.") else s)

    def walk(node, owner):
        out = [node[0]]
        for v in node[1:]:
            if not isinstance(v, list) or not v:
                out.append(v)
                continue
            k = v[0]
            if k == "model":
                out.append(v)
            elif k == "at" and owner in ("pad", "property", "fp_text"):
                x, y = float(v[1]), float(v[2])
                a = float(v[3]) if len(v) > 3 else 0.0
                if back:
                    x, a = -x, -a
                at = ["at", x, y, (rot + a) % 360.0]             # pad and text angles are absolute in a board
                out.append(at + [w for w in v[4:]])
            elif k in ("at", "start", "end", "mid", "center", "xy") and len(v) >= 3:
                out.append([k, -float(v[1]) if back else v[1], v[2]] + v[3:])
            elif k == "angle" and back:
                out.append(["angle", -float(v[1])])
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
                a = math.radians(-c["rot"])
                px, py = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
                for w in v:
                    if isinstance(w, list) and w and w[0] == "at":
                        w[:] = ["at", round(px, 4), round(-py, 4), ang]
                    if isinstance(w, list) and w and w[0] == "effects":
                        for f_ in _find(w, "font"):
                            f_[:] = ["font", ["size", size, size], ["thickness", round(size * 0.15, 3)]]
        elif v[0] == "property" and v[1] == "Value" and c["value"]:
            v[2] = _Q(c["value"])
        elif v[0] == "fp_text" and v[1] == "value" and c["value"]:
            v[2] = _Q(c["value"])
        elif v[0] == "pad":
            n = pn.get((ref, str(v[1])))
            if n is not None:
                v.insert(next(i for i, w in enumerate(v) if isinstance(w, list) and w[0] == "layers") + 1,
                         ["net", netnum[n], _Q(n)])
            pads_on += 1
    # silkscreen strokes past the board edge (an edge connector's outline over the overhang) are dropped: the fab
    # would clip them anyway, and the board file stays clean for KiCad's DRC
    ca, sa = math.cos(math.radians(c["rot"])), math.sin(math.radians(c["rot"]))
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
    fp[i:i] = [["uuid", _uuid(board.name, ref)], ["at", tx(c["at"][0]), ty(c["at"][1]), rot]]
    extra = [("MPN", c["mpn"]), ("Note", c["note"])]
    fab = _Q("B.Fab" if back else "F.Fab")
    for k_, val in extra:
        if val:
            fp.append(["property", _Q(k_), _Q(val), ["at", 0, 0, rot], ["layer", fab], ["hide", "yes"],
                       ["uuid", _uuid(board.name, ref, k_)], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]])
    old = [w for w in fp if isinstance(w, list) and w and w[0] == "model"]
    if c.get("model") is not None or c.get("body") is not None:     # (neither given: the footprint's own model stays,
        fp = [w for w in fp if not (isinstance(w, list) and w and w[0] == "model")]          # model=False: none)
    if c.get("model"):                                       # the 3D model the harness renders, as KiCad would find it
        var = re.match(r"\$\{\w+\}/", str(old[0][1])).group(0) if old and re.match(r"\$\{\w+\}/", str(old[0][1])) \
            else "${KICAD9_3DMODEL_DIR}/"
        ox, oy, oz = c.get("model_offset") or (0.0, 0.0, 0.0)
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
    keep-outs, ...).
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
        rkey = hashlib.sha1((board._state_key() + repr(list(jobs))).encode()).hexdigest()[:16]
    ckpt = CACHE / "pcb_routes" / f"{board.name}_{rkey}_signals.pkl"
    latest = CACHE / "pcb_routes" / f"{board.name}_signals_latest.pkl"
    jobs = list(jobs)
    best = pickle.loads(ckpt.read_bytes()) if ckpt.exists() else None
    if best is None and latest.exists():
        old = pickle.loads(latest.read_bytes())
        failed0 = {m.split()[1].rstrip(":") for m in old["got"] if m.startswith("route ")}
        circles = []
        for m in old["got"]:
            if "reach " in m:
                ref, num = m.split("reach ")[1].split()[0].rsplit(".", 1)
                if ref in board.comps and num in board.comps[ref]["pads"]:
                    circles.append(board.pad(ref, num) + (rip,))
        redo = reuse_routes(board, old, rip=circles) | failed0
        jobs = [j for j in jobs if j[0] in failed0] + [j for j in jobs if j[0] in redo and j[0] not in failed0]
        print(f"reusing the last routing: {len(jobs)} nets to route again", flush=True)
    base = (list(board.tracks), list(board.vias))
    for _ in range(0 if best else passes):
        board.tracks, board.vias = list(base[0]), list(base[1])
        got, failed = [], []
        for net, w, pins in jobs:
            p = route(board, net, width=w, only=pins, **kw(net))
            got += p
            if p:
                failed.append((net, w, pins))
        if best is None or len(got) < len(best[0]):
            best = (got, list(board.tracks), list(board.vias))
        if not failed:
            break
        jobs = failed + [j for j in jobs if j not in failed]
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
            try:
                with urllib.request.urlopen(KICAD_RAW + path, timeout=120) as r:
                    data = r.read()
                if not data.startswith(b"ISO-10303-21"):
                    raise ValueError("not a STEP file")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            except Exception:
                missing.append(path)
                continue
        out = Path(model_dir) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            shutil.copy(dst, out)
    return sorted(set(missing))


def _pcbnew_python() -> str:
    """a Python that can import KiCad's pcbnew: $PCBNEW_PYTHON, else the first of python3, python3.12, ... that has it
    (KiCad's Ubuntu package builds pcbnew for the system Python, which need not be the one running the harness)"""
    import os
    import shutil
    import subprocess
    if os.environ.get("PCBNEW_PYTHON"):
        return os.environ["PCBNEW_PYTHON"]
    for exe in ("python3", "python3.12", "python3.11", "python3.10", "/usr/bin/python3.12", "/usr/bin/python3"):
        path = shutil.which(exe) or (exe if os.path.exists(exe) else None)
        if path and subprocess.run([path, "-c", "import pcbnew"], capture_output=True).returncode == 0:
            return path
    return "python3"


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
    subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json", "-o", out, path],
                   capture_output=True, timeout=1800)
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


def export_kicad(board: Board, out_dir: str, origin: Tuple[float, float] = (150.0, 100.0),
                 place_labels: bool = True) -> List[str]:
    """``<name>.kicad_pcb`` and ``<name>.kicad_pro`` (KiCad 9 format; KiCad 9 and later open it) for further work in
    KiCad: every part on its library footprint (with its 3D model, MPN and note), the nets, tracks, vias, the pours as
    filled zones (solid pad connection, islands kept, as the harness fills them), keep-outs as rule areas, mounting
    holes, the outline on Edge.Cuts and the legend on the silkscreen. The project file carries the board's clearance,
    so KiCad's DRC checks against the same rule. Reference designators are placed clear of pads, parts and
    each other (``place_refs``), or kept off the silkscreen where a dense area has no room; a part drawn in the
    program (``body=``) is written to ``3d/`` as STEP and used as its model. Board mm, y up, are moved so the board's centre sits at ``origin`` in
    KiCad's page (y down). Zones carry the harness's fill: KiCad refills them with its own rules on 'B'."""
    import json
    import os
    from shapely.geometry import Polygon
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
    for i, (x, y, d) in enumerate(board.holes):          # mounting holes: a bare (non-plated) hole each
        pcb_.append(["footprint", _Q(f"MountingHole:MountingHole_{d:g}mm"), ["layer", _Q("F.Cu")],
                     ["uuid", _uuid(board.name, "hole", i)], ["at", tx(x), ty(y), 0],
                     ["property", _Q("Reference"), _Q(f"H{i + 1}"), ["at", 0, -d / 2 - 1.0, 0], ["layer", _Q("F.Fab")],
                      ["uuid", _uuid(board.name, "hole", i, "ref")], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
                     ["property", _Q("Value"), _Q(f"hole {d:g} mm"), ["at", 0, d / 2 + 1.0, 0], ["layer", _Q("F.Fab")],
                      ["uuid", _uuid(board.name, "hole", i, "val")], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
                     ["attr", "exclude_from_pos_files", "exclude_from_bom"],
                     ["fp_circle", ["center", 0, 0], ["end", d / 2, 0], ["stroke", ["width", 0.05], ["type", "solid"]],
                      ["fill", "no"], ["layer", _Q("Cmts.User")], ["uuid", _uuid(board.name, "hole", i, "c")]],
                     ["pad", _Q(""), "np_thru_hole", "circle", ["at", 0, 0, 0], ["size", d, d], ["drill", d],
                      ["layers", _Q("*.Cu"), _Q("*.Mask")], ["uuid", _uuid(board.name, "hole", i, "pad")]]])
    pts = list(board.outline.exterior.coords)[:-1]
    pcb_.append(["gr_poly", ["pts"] + [["xy", tx(x), ty(y)] for x, y in pts], ["stroke", ["width", 0.1], ["type", "solid"]],
                 ["fill", "no"], ["layer", _Q("Edge.Cuts")], ["uuid", _uuid(board.name, "edge")]])
    for i, (txt, (x, y), size, ang) in enumerate(getattr(board, "silk_text", [])):
        pcb_.append(["gr_text", _Q(txt), ["at", tx(x), ty(y), ang], ["layer", _Q("F.SilkS")],
                     ["uuid", _uuid(board.name, "silk", i)],
                     ["effects", ["font", ["size", size, size], ["thickness", round(size * 0.15, 3)]]]])
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
        if key not in fills:
            fills[key] = board.pour_fill(*key, cu=cu_all)
        zp = Polygon(pz["poly"], pz.get("holes") or None)
        fill = _fractured(fills[key].intersection(zp))
        pcb_.append(["zone", ["net", netnum[pz["net"]]], ["net_name", _Q(pz["net"])], ["layer", LN[pz["layer"]]],
                     ["uuid", _uuid(board.name, "zone", i)], ["hatch", "edge", 0.5],
                     ["priority", i],                     # (KiCad wants overlapping zones ranked; the fills hold anyway)
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
                                                   "min_copper_edge_clearance": board.edge}}},
           "net_settings": {"meta": {"version": 4},
                            "classes": [{"name": "Default", "clearance": board.clear, "track_width": round(w_min, 4),
                                         "via_diameter": v_min["dia"], "via_drill": v_min["drill"],
                                         "priority": 2147483647}]}}
    ppath = os.path.join(out_dir, f"{board.name}.kicad_pro")
    with open(ppath, "w") as f:
        json.dump(pro, f, indent=2)
    return [path, ppath]


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
