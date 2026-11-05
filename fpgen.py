"""Footprints for parts KiCad has none for, from the package drawing: the drawing's dimensions (read off it by a
person or an agent) go into a spec, the land pattern comes from IPC-7351B's equations - not from hand-placed pads -
and the result is checked before a board uses it, then shown next to the drawing for a person to confirm.

    zeus footprint spec.json --drawing drawing.png [--symbol Lib:Name]

writes ``footprints/<Lib>.pretty/<Name>.kicad_mod`` beside the board script (the harness reads that folder before
KiCad's library), and ``<Name>_review.html``: the drawing, the footprint with its dimensions, KiCad's own render of
it, and every check. A spec is JSON, in mm; a dimension is a number (nominal) or [min, max] as the drawing gives it:

    {"family": "soic", "pins": 8, "pitch": 1.27, "body": [4.9, 3.9],       body: [length along the rows, width]
     "span": [5.8, 6.2], "lead_length": [0.4, 1.27], "lead_width": [0.31, 0.51]}
    {"family": "qfn", "pins": 16, "pitch": 0.5, "body": [3, 3], "lead_length": [0.3, 0.5],
     "lead_width": [0.18, 0.3], "ep": [1.7, 1.7]}

Families: ``soic`` (any two-row gull-wing: SOIC, SOP, SSOP, TSSOP, MSOP, SOT-23 with ``rows``), ``qfp`` (four-side
gull-wing), ``qfn`` / ``dfn`` (four- / two-side no-lead, an exposed pad with ``ep``), ``bga``, ``tht`` (a pin grid:
headers, DIP) and ``pads`` (the drawing's own land table, for anything else). ``template(family)`` prints one.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# IPC-7351B, density level B (nominal): the solder fillet goals (toe, heel, side) in mm, and the courtyard excess
IPC = {"gullwing": dict(toe=0.35, heel=0.35, side=0.03, court=0.25),
       "gullwing_fine": dict(toe=0.35, heel=0.35, side=-0.02, court=0.25),      # (pitch <= 0.625 mm)
       "nolead": dict(toe=0.30, heel=0.00, side=-0.04, court=0.25),
       "bga": dict(court=1.0)}
FAB_TOL, PLACE_TOL = 0.10, 0.05                         # (the board's fabrication and the machine's placement)
EP_GAP = 0.2                                             # (an exposed pad kept this clear of the pins)
MIN_GAP = 0.2                                            # (pads kept this far apart when the formula would merge them)


# ---- dimensions ---------------------------------------------------------------------------------------------------
def _mm(v, tol: float = 0.0) -> Tuple[float, float]:
    """a drawing's dimension: a number (nominal: +-tol) or [min, max] (or [min, nom, max])"""
    if isinstance(v, (int, float)):
        return float(v) - tol, float(v) + tol
    v = [float(x) for x in v]
    return min(v), max(v)


def _nom(v) -> float:
    a, b = _mm(v)
    return (a + b) / 2


def _r(v: float, base: float = 0.01) -> float:
    return round(round(v / base) * base, 4)


def _up(v: float, base: float = 0.01) -> float:
    return round(math.ceil(v / base - 1e-9) * base, 4)


def lands(span, lead_length, lead_width, fillet: dict) -> Tuple[float, float, float]:
    """IPC-7351B for one row of terminals: (Z, G, X) - the lands' outer span, inner span and width - from the
    terminals' toe-to-toe span L, their length T and width W (each min / max) and the fillet goals"""
    Lmin, Lmax = _mm(span)
    Tmin, Tmax = _mm(lead_length)
    Wmin, Wmax = _mm(lead_width)
    Ltol, Ttol, Wtol = Lmax - Lmin, Tmax - Tmin, Wmax - Wmin
    Smin, Smax = Lmin - 2 * Tmax, Lmax - 2 * Tmin
    Stol = Smax - Smin
    Stol_rms = math.sqrt(Ltol ** 2 + 2 * Ttol ** 2)      # (the heel span's tolerance, root-sum-square, as IPC does)
    Smax_rms = Smax - (Stol - Stol_rms) / 2
    f = math.hypot(FAB_TOL, PLACE_TOL)
    Z = Lmin + 2 * fillet["toe"] + math.sqrt(Ltol ** 2 + f ** 2)
    G = Smax_rms - 2 * fillet["heel"] - math.sqrt(Stol_rms ** 2 + f ** 2)
    X = Wmin + 2 * fillet["side"] + math.sqrt(Wtol ** 2 + f ** 2)
    return Z, G, X


def _row_pads(Z, G, X, pitch) -> Tuple[float, float, float]:
    """a row's land: (centre distance from the part's centre, length, width), on KiCad's 0.01 mm grid"""
    X = min(X, pitch - MIN_GAP)
    length = _up((Z - G) / 2)
    return _r((Z + G) / 4), length, _up(X)


# ---- the families -------------------------------------------------------------------------------------------------
def _pad(num, x, y, w, h, shape="roundrect", drill=None, rot=0) -> dict:
    return {"num": str(num), "at": (_r(x, 0.001), _r(y, 0.001)), "size": (w, h), "shape": shape, "drill": drill,
            "rot": rot}


def _side_offsets(n: int, pitch: float) -> List[float]:
    return [(k - (n - 1) / 2) * pitch for k in range(n)]


def _two_rows(spec, fillet) -> Tuple[List[dict], dict]:
    """two rows of terminals, left and right of the body (KiCad's frame: y down, pin 1 top left); ``rows`` gives
    each row's slots top to bottom ({"left": ["1", null, "2"], "right": [null, "3", null]}), else pins 1..n/2 down
    the left and the rest up the right"""
    pitch = float(spec["pitch"])
    span = spec.get("span") or spec["body"][1]           # (a no-lead part's terminals end at its body)
    c, ln, w = _row_pads(*lands(span, spec["lead_length"], spec["lead_width"], fillet), pitch)
    rows = spec.get("rows")
    if not rows:
        n = int(spec["pins"])
        rows = {"left": [str(k + 1) for k in range(n // 2)], "right": [str(n - k) for k in range(n // 2)]}
    pads = []
    for side, sx in (("left", -1), ("right", 1)):
        slots = rows[side]
        for num, y in zip(slots, _side_offsets(len(slots), pitch)):
            if num is not None:
                pads.append(_pad(num, sx * c, y, ln, w))
    return pads, {"centre": c, "pad": (ln, w), "pitch": pitch}


def _four_sides(spec, fillet) -> Tuple[List[dict], dict]:
    """terminals on four sides (QFP, QFN): pin 1 at the top of the left side, counter-clockwise; ``pins_x`` /
    ``pins_y`` per side when the part is not square (else pins / 4)"""
    pitch = float(spec["pitch"])
    n = int(spec["pins"])
    ny = int(spec.get("pins_y", n // 4))                 # (down each of the left and right sides)
    nx = int(spec.get("pins_x", (n - 2 * ny) // 2))      # (along the bottom and the top)
    body = spec["body"]
    span_x = spec.get("span_x") or spec.get("span") or body[1]
    span_y = spec.get("span_y") or spec.get("span") or body[0]
    cx, lx, wx = _row_pads(*lands(span_x, spec["lead_length"], spec["lead_width"], fillet), pitch)
    cy, ly, wy = _row_pads(*lands(span_y, spec["lead_length"], spec["lead_width"], fillet), pitch)
    pads, k = [], 1
    for y in _side_offsets(ny, pitch):                   # left, top to bottom
        pads.append(_pad(k, -cx, y, lx, wx)); k += 1
    for x in _side_offsets(nx, pitch):                   # bottom, left to right
        pads.append(_pad(k, x, cy, wy, ly)); k += 1
    for y in reversed(_side_offsets(ny, pitch)):         # right, bottom to top
        pads.append(_pad(k, cx, y, lx, wx)); k += 1
    for x in reversed(_side_offsets(nx, pitch)):         # top, right to left
        pads.append(_pad(k, x, -cy, wy, ly)); k += 1
    return pads, {"centre": cx, "centre_y": cy, "pad": (lx, wx), "pitch": pitch}


def _exposed_pad(spec, pads: List[dict], info: dict) -> None:
    """the exposed pad (``ep``: [length, width] as the drawing gives D2 x E2), numbered pins + 1 (or ``ep_num``),
    and every pin's inner end pulled back to keep EP_GAP from it"""
    if not spec.get("ep"):
        return
    ed, ee = _nom(spec["ep"][0]), _nom(spec["ep"][1])   # (D2 along the rows: y; E2 across: x)
    for p in pads:
        (x, y), (w, h) = p["at"], p["size"]
        if abs(x) >= abs(y) and w > 0:                   # (a left / right pin: its inner end in x)
            inner = abs(x) - w / 2
            need = ee / 2 + EP_GAP
            if inner < need:
                cut = _up(need - inner)
                p["size"] = (_r(w - cut), h)
                p["at"] = (_r(x + math.copysign(cut / 2, x), 0.001), y)
        else:
            inner = abs(y) - h / 2
            need = ed / 2 + EP_GAP
            if inner < need:
                cut = _up(need - inner)
                p["size"] = (w, _r(h - cut))
                p["at"] = (x, _r(y + math.copysign(cut / 2, y), 0.001))
    num = str(spec.get("ep_num", int(spec["pins"]) + 1))
    pads.append(_pad(num, 0, 0, _r(ee), _r(ed)) | {"ep": True})
    info["ep"] = (_r(ee), _r(ed))


def _bga(spec) -> Tuple[List[dict], dict]:
    """a ball grid: ``grid`` [rows, cols], ``pitch``, ``ball`` diameter; the land 80 % of the ball (IPC-7351B,
    collapsing balls, non-solder-mask-defined); balls named A1.. (JEDEC: rows skip I O Q S X Z); ``missing``"""
    rows, cols = int(spec["grid"][0]), int(spec["grid"][1])
    pitch, ball = float(spec["pitch"]), _nom(spec["ball"])
    d = _r(ball * 0.8)
    letters = [c for c in "ABCDEFGHJKLMNPRTUVWY"]
    names = letters + [a + b for a in letters for b in letters]
    gone = set(spec.get("missing", []))
    pads = []
    for i, y in enumerate(_side_offsets(rows, pitch)):
        for j, x in enumerate(_side_offsets(cols, pitch)):
            n = f"{names[i]}{j + 1}"
            if n not in gone:
                pads.append(_pad(n, x, y, d, d, "circle"))
    return pads, {"pad": (d, d), "pitch": pitch}


def _tht(spec) -> Tuple[List[dict], dict]:
    """a grid of through-hole pins: ``grid`` [rows, cols], ``pitch`` (or [along, across]), ``lead`` (a round lead's
    diameter, or [w, t] of a rectangular one); hole = the lead's diagonal + 0.15, rounded up to 0.05; ring 0.3;
    numbering ``rows`` (1, 2 across, as a 2-row header), ``dip`` (down one side, up the other) or ``cols``"""
    rows, cols = int(spec["grid"][0]), int(spec["grid"][1])
    p = spec["pitch"]
    pa, pc = (float(p), float(p)) if isinstance(p, (int, float)) else (float(p[0]), float(p[1]))
    lead = spec["lead"]
    diag = _mm(lead)[1] if isinstance(lead, (int, float)) else math.hypot(_mm(lead[0])[1], _mm(lead[1])[1])
    drill = _up(diag + 0.15, 0.05)
    pad = _up(drill + 2 * float(spec.get("ring", 0.3)), 0.05)
    order = spec.get("numbering", "rows")
    pads = []
    for r in range(rows):
        for c in range(cols):
            if order == "dip":
                num = r + 1 if c == 0 else rows * cols - r
            elif order == "cols":
                num = c * rows + r + 1
            else:
                num = r * cols + c + 1
            pads.append(_pad(num, c * pc, r * pa, pad, pad, "circle", drill))
    for q in pads:
        if q["num"] == "1":
            q["shape"] = "rect"
    return pads, {"pad": (pad, pad), "drill": drill, "pitch": pa}


def _manual(spec) -> Tuple[List[dict], dict]:
    """the drawing's own land table: ``pads`` [{"num", "at": [x, y], "size": [w, h], "shape", "drill", "rot"}],
    KiCad's frame (y down, mm)"""
    pads = [_pad(p["num"], p["at"][0], p["at"][1], float(p["size"][0]), float(p["size"][1]),
                 p.get("shape", "roundrect"), p.get("drill"), p.get("rot", 0)) for p in spec["pads"]]
    return pads, {}


# ---- the footprint ------------------------------------------------------------------------------------------------
def name_for(spec) -> str:
    """KiCad's naming: SOIC-8_3.9x4.9mm_P1.27mm, QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm ..."""
    if spec.get("name"):
        return re.sub(r"[^A-Za-z0-9_.,+-]", "_", spec["name"])
    fam = spec["family"]
    f = lambda v: f"{_nom(v):g}"
    if fam == "bga":
        n = int(spec["grid"][0]) * int(spec["grid"][1]) - len(spec.get("missing", []))
        return f"BGA-{n}_{f(spec['body'][1])}x{f(spec['body'][0])}mm_P{spec['pitch']}mm"
    if fam == "tht":
        return f"PinGrid_{spec['grid'][0]}x{spec['grid'][1]:02d}_P{spec['pitch'] if isinstance(spec['pitch'], (int, float)) else spec['pitch'][0]}mm"
    if fam == "pads":
        return f"{spec.get('package', 'Custom')}-{len(spec['pads'])}_{f(spec['body'][1])}x{f(spec['body'][0])}mm"
    tag = {"soic": "SOIC", "qfp": "QFP", "qfn": "QFN", "dfn": "DFN"}[fam]
    ep = "-1EP" if spec.get("ep") else ""
    s = f"{spec.get('package', tag)}-{spec['pins']}{ep}_{f(spec['body'][1])}x{f(spec['body'][0])}mm_P{spec['pitch']}mm"
    if spec.get("ep"):
        s += f"_EP{f(spec['ep'][1])}x{f(spec['ep'][0])}mm"
    return s


def generate(spec: dict) -> dict:
    """the land pattern: {"name", "pads", "body": (w, h), "courtyard": (x0, y0, x1, y1), "pin1": (x, y), "info"};
    KiCad's frame, mm"""
    fam = spec["family"]
    pitch = float(spec.get("pitch", 1.0)) if not isinstance(spec.get("pitch"), list) else float(spec["pitch"][0])
    gw = IPC["gullwing_fine" if pitch <= 0.625 else "gullwing"]
    if fam == "soic":                                    # (a PowerPAD / HSOP: its exposed pad too)
        pads, info = _two_rows(spec, gw)
        _exposed_pad(spec, pads, info)
    elif fam == "qfp":
        pads, info = _four_sides(spec, gw)
    elif fam == "qfn":
        pads, info = _four_sides(spec, IPC["nolead"])
        _exposed_pad(spec, pads, info)
    elif fam == "dfn":
        pads, info = _two_rows(spec, IPC["nolead"])
        _exposed_pad(spec, pads, info)
    elif fam == "bga":
        pads, info = _bga(spec)
    elif fam == "tht":
        pads, info = _tht(spec)
    elif fam == "pads":
        pads, info = _manual(spec)
    else:
        raise ValueError(f"unknown family {fam!r}: soic, qfp, qfn, dfn, bga, tht, pads")
    if "body" in spec:
        bw, bh = _nom(spec["body"][1]), _nom(spec["body"][0])     # (width across x, length along y)
    else:
        xs = [p["at"][0] for p in pads]
        ys = [p["at"][1] for p in pads]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    if fam == "tht":                                     # (pin 1 at the origin, as KiCad's headers: body round it)
        xs = [p["at"][0] for p in pads]
        ys = [p["at"][1] for p in pads]
        ox, oy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        body = (ox - bw / 2, oy - bh / 2, ox + bw / 2, oy + bh / 2) if "body" in spec else \
            (min(xs) - info["pad"][0] / 2 - 0.25, min(ys) - info["pad"][0] / 2 - 0.25,
             max(xs) + info["pad"][0] / 2 + 0.25, max(ys) + info["pad"][0] / 2 + 0.25)
    else:
        body = (-bw / 2, -bh / 2, bw / 2, bh / 2)
    p1 = next((p for p in pads if p["num"] in ("1", "A1")), pads[0])
    b1 = _pad_box(p1)                                    # pin 1's silk dot: beside its land's outer end, clear of
    dot = (b1[0] - 0.5, p1["at"][1]) if fam != "bga" else (body[0] - 0.5, body[1] - 0.5)   # it (0.2 + the line)
    ext = [_pad_box(p) for p in pads] + [body, (dot[0] - 0.1, dot[1] - 0.1, dot[0] + 0.1, dot[1] + 0.1)]
    court = IPC["bga"]["court"] if fam == "bga" else IPC["gullwing"]["court"]
    crt = (_r(min(b[0] for b in ext) - court, 0.01), _r(min(b[1] for b in ext) - court, 0.01),
           _r(max(b[2] for b in ext) + court, 0.01), _r(max(b[3] for b in ext) + court, 0.01))
    spec_out = dict(spec)
    spec_out.pop("drawing", None)
    return {"name": name_for(spec), "pads": pads, "body": body, "courtyard": crt, "pin1": p1["at"], "dot": dot, "info": info, "spec": spec_out,
            "smd": fam not in ("tht",)}


def _pad_box(p) -> Tuple[float, float, float, float]:
    (x, y), (w, h) = p["at"], p["size"]
    if p.get("rot") in (90, 270, -90):
        w, h = h, w
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _f(v) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if v != 0 else "0"


def kicad_mod(fp: dict, descr: str = "") -> str:
    """the footprint as KiCad's .kicad_mod (KiCad 9 format): pads, fabrication outline with pin 1's chamfer,
    silkscreen clear of the pads with a pin-1 mark, courtyard, reference and value"""
    from shapely.geometry import LineString, box
    from shapely.ops import unary_union
    x0, y0, x1, y1 = fp["body"]
    c = fp["courtyard"]
    out = [f'(footprint "{fp["name"]}"', '  (version 20241229)', '  (generator "zeus")', '  (layer "F.Cu")',
           f'  (descr "{descr or "generated by Zeus (fpgen.py) from the package drawing, IPC-7351B nominal"}")',
           f'  (attr {"smd" if fp["smd"] else "through_hole"})',
           f'  (property "Reference" "REF**" (at {_f((c[0] + c[2]) / 2)} {_f(c[1] - 1.0)} 0) (layer "F.SilkS") '
           '(effects (font (size 1 1) (thickness 0.15))))',
           f'  (property "Value" "{fp["name"]}" (at {_f((c[0] + c[2]) / 2)} {_f(c[3] + 1.0)} 0) (layer "F.Fab") '
           '(effects (font (size 1 1) (thickness 0.15))))',
           f'  (fp_text user "${{REFERENCE}}" (at {_f((x0 + x1) / 2)} {_f((y0 + y1) / 2)} 0) (layer "F.Fab") '
           '(effects (font (size 0.5 0.5) (thickness 0.08))))']             # (on the body: pin 1 is a header's origin)
    # fabrication outline, pin 1's corner chamfered
    ch = min(1.0, (x1 - x0) / 4, (y1 - y0) / 4)
    fab = [(x0 + ch, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + ch), (x0 + ch, y0)]
    out.append('  (fp_poly (pts ' + " ".join(f"(xy {_f(x)} {_f(y)})" for x, y in fab) + ') '
               '(stroke (width 0.1) (type solid)) (fill no) (layer "F.Fab"))')
    # silkscreen: the body's outline where it is clear of every pad (0.2 mm plus half the line)
    keep = unary_union([box(*_pad_box(p)).buffer(0.2 + 0.06, join_style=2) for p in fp["pads"]])
    g = 0.11                                              # (just outside the body)
    ring = [(x0 - g, y0 - g), (x1 + g, y0 - g), (x1 + g, y1 + g), (x0 - g, y1 + g), (x0 - g, y0 - g)]
    for a, b in zip(ring, ring[1:]):
        seg = LineString([a, b]).difference(keep)
        for s in getattr(seg, "geoms", [seg]):
            if s.is_empty or s.length < 0.3:
                continue
            (sx, sy), (ex, ey) = s.coords[0], s.coords[-1]
            out.append(f'  (fp_line (start {_f(sx)} {_f(sy)}) (end {_f(ex)} {_f(ey)}) '
                       '(stroke (width 0.12) (type solid)) (layer "F.SilkS"))')
    dot = fp["dot"]                                     # (pin 1's dot: placed with the pads, in generate())
    out.append(f'  (fp_circle (center {_f(dot[0])} {_f(dot[1])}) (end {_f(dot[0] + 0.1)} {_f(dot[1])}) '
               '(stroke (width 0.2) (type solid)) (fill yes) (layer "F.SilkS"))')
    out.append(f'  (fp_rect (start {_f(c[0])} {_f(c[1])}) (end {_f(c[2])} {_f(c[3])}) '
               '(stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))')
    for p in fp["pads"]:
        (x, y), (w, h) = p["at"], p["size"]
        rot = f' {_f(p["rot"])}' if p.get("rot") else ""
        if p.get("drill"):
            lay = '"*.Cu" "*.Mask"'
            out.append(f'  (pad "{p["num"]}" thru_hole {p["shape"]} (at {_f(x)} {_f(y)}{rot}) (size {_f(w)} {_f(h)}) '
                       f'(drill {_f(p["drill"])}) (layers {lay}))')
            continue
        extra = ""
        if p["shape"] == "roundrect":
            extra = f' (roundrect_rratio {_f(min(0.25, 0.25 / max(min(w, h), 1e-6)))})'
        if p.get("ep"):                                   # (an exposed pad: 64 % of it pasted, against floating)
            extra += " (solder_paste_margin_ratio -0.1)"
        out.append(f'  (pad "{p["num"]}" smd {p["shape"]} (at {_f(x)} {_f(y)}{rot}) (size {_f(w)} {_f(h)}) '
                   f'(layers "F.Cu" "F.Paste" "F.Mask"){extra})')
    out.append(")")
    return "\n".join(out) + "\n"


# ---- checks -------------------------------------------------------------------------------------------------------
def check(fp: dict, symbol: Optional[str] = None, house: str = "jlcpcb-2") -> List[dict]:
    """what could be wrong with it before a board uses it: [{"severity": error|warning|ok, "text"}]"""
    from shapely.geometry import box
    import fab
    out = []
    ok = lambda t: out.append({"severity": "ok", "text": t})
    bad = lambda t, sev="error": out.append({"severity": sev, "text": t})
    pads, spec = fp["pads"], fp["spec"]
    nums = [p["num"] for p in pads if not p.get("ep")]
    if "pins" in spec and spec["family"] not in ("bga", "tht", "pads"):
        n = int(spec["pins"])
        (ok if len(nums) == n else bad)(f"{len(nums)} pins for the drawing's {n}")
    if len(set(nums)) != len(nums):
        bad("pin numbers repeat: " + ", ".join(sorted({x for x in nums if nums.count(x) > 1})))
    if symbol:
        import parts
        sym = {q["num"] for q in parts.symbol(symbol)["pins"]}
        have = {p["num"] for p in pads}
        if sym == have:
            ok(f"every pin of {symbol} has a pad and every pad a pin")
        else:
            bad(f"{symbol}: pins without pads {sorted(sym - have)}, pads without pins {sorted(have - sym)}")
    space = fab.RULES[house]["space"]
    gaps = []
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            if a["num"] != b["num"]:
                gaps.append((box(*_pad_box(a)).distance(box(*_pad_box(b))), a["num"], b["num"]))
    if gaps:
        g, a, b = min(gaps)
        if g < space:
            bad(f"pads {a} and {b} are {g:.3f} mm apart: under {house}'s {space} mm")
        elif g < 0.1:
            bad(f"pads {a} and {b} are {g:.3f} mm apart: too little solder mask between them to stop a bridge",
                "warning")
        else:
            ok(f"closest pads {a} / {b}: {g:.3f} mm apart ({house} needs {space})")
    c = fp["crtyd"] if "crtyd" in fp else fp["courtyard"]
    inside = all(_pad_box(p)[0] >= c[0] - 1e-6 and _pad_box(p)[2] <= c[2] + 1e-6 and
                 _pad_box(p)[1] >= c[1] - 1e-6 and _pad_box(p)[3] <= c[3] + 1e-6 for p in pads)
    (ok if inside else bad)("every pad inside the courtyard" if inside else "a pad sticks out of the courtyard")
    xs = [p["at"][0] for p in pads]
    ys = [p["at"][1] for p in pads]
    if spec["family"] != "tht" and abs((min(xs) + max(xs)) / 2) + abs((min(ys) + max(ys)) / 2) > 0.01:
        bad("the pads are not centred on the origin: the pick-and-place centroid will be off", "warning")
    info = fp["info"]
    if "pitch" in info and spec["family"] in ("soic", "qfp", "qfn", "dfn"):
        rows: Dict[float, List[float]] = {}
        for p in pads:
            if not p.get("ep"):
                (x, y) = p["at"]
                key, along = (round(x, 3), y) if abs(x) > abs(y) or spec["family"] in ("soic", "dfn") else (round(y, 3), x)
                rows.setdefault(key, []).append(along)
        off = [k for k, v in rows.items() if len(v) > 1 and any(
            abs(round((b - a) / info["pitch"]) * info["pitch"] - (b - a)) > 0.001 for a, b in zip(sorted(v), sorted(v)[1:]))]
        (ok if not off else bad)(f"pitch {info['pitch']} mm on every row" if not off else "a row is off its pitch")
    if spec["family"] in ("soic", "qfp", "qfn", "dfn"):
        g_ = IPC["nolead" if spec["family"] in ("qfn", "dfn") else "gullwing"]
        Z, G, X = lands(spec.get("span") or spec.get("span_x") or spec["body"][1], spec["lead_length"],
                        spec["lead_width"], g_)
        L0, L1 = _mm(spec.get("span") or spec.get("span_x") or spec["body"][1])
        T0, T1 = _mm(spec["lead_length"])
        toe = (Z - L1) / 2
        heel = ((L0 - 2 * T1) - G) / 2
        (ok if toe > 0 and heel >= -0.05 else bad)(
            f"the land reaches {toe:.2f} mm past the longest toe and {heel:.2f} mm inside the shortest heel")
    if "ep" in info:
        ep = next(p for p in pads if p.get("ep"))
        eg = min(box(*_pad_box(ep)).distance(box(*_pad_box(p))) for p in pads if not p.get("ep"))
        (ok if eg >= EP_GAP - 1e-6 else bad)(f"exposed pad {eg:.2f} mm from the pins")
    if any(p.get("drill") for p in pads):
        d = min(p["drill"] for p in pads if p.get("drill"))
        ring = min((min(p["size"]) - p["drill"]) / 2 for p in pads if p.get("drill"))
        mind, minring = fab.RULES[house]["pth_drill"], fab.RULES[house]["annular"]
        (ok if d >= mind and ring >= minring else bad)(f"holes {d} mm, ring {ring:.2f} mm ({house}: {mind} / {minring})")
    return out


def kicad_check(path: str) -> Optional[str]:
    """None when KiCad reads the footprint (``kicad-cli fp export svg``), else why not; "" when KiCad is missing"""
    import subprocess
    import tempfile
    import pcb
    if not pcb.kicad_version():
        return ""
    lib = os.path.dirname(path)
    name = os.path.basename(path)[:-len(".kicad_mod")]
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(["kicad-cli", "fp", "export", "svg", "--fp", name, "-o", d, lib], capture_output=True,
                           text=True, timeout=120)
        svgs = list(Path(d).glob("*.svg"))
        if r.returncode != 0 or not svgs:
            return (r.stderr or r.stdout).strip()[-300:] or "kicad-cli wrote nothing"
        kicad_svg = svgs[0].read_text()
    _KSVG[path] = kicad_svg
    return None


_KSVG: Dict[str, str] = {}


# ---- the review page ----------------------------------------------------------------------------------------------
def figure(fp: dict) -> str:
    """the land pattern as an SVG, its dimensions outside the courtyard (nothing drawn over a pad or another label):
    the rows' centre-to-centre span above, the pitch to the left, the exposed pad's size on it when it fits"""
    c = fp["courtyard"]
    size = max(c[2] - c[0], c[3] - c[1])
    fs = size / 20                                       # (the labels' height)
    sw = size / 260
    ink, out = "#f2c14e", []
    pads = [p for p in fp["pads"] if not p.get("ep")]
    xs = [p["at"][0] for p in pads] or [0.0]
    ys = [p["at"][1] for p in pads] or [0.0]
    lw = lambda t: len(t) * fs * 0.62                    # (a label's width)

    def line(a, b, w=None, dash=""):
        out.append(f'<line x1="{_f(a[0])}" y1="{_f(a[1])}" x2="{_f(b[0])}" y2="{_f(b[1])}" stroke="{ink}" '
                   f'stroke-width="{_f(w or sw)}"{dash}/>')
    top, left = c[1], c[0]
    span = None
    if max(xs) - min(xs) > 1e-6:                         # the rows' centre-to-centre span, above the courtyard
        y = c[1] - 2.2 * fs
        span = f"{_f(max(xs) - min(xs))} c-c"
        for x in (min(xs), max(xs)):
            line((x, min(ys) - 0.2), (x, y - 0.4 * fs), dash=f' stroke-dasharray="{_f(sw*3)} {_f(sw*3)}"')
        line((min(xs), y), (max(xs), y))
        out.append(f'<text x="{_f((min(xs) + max(xs)) / 2)}" y="{_f(y - 0.5 * fs)}" font-size="{_f(fs)}" '
                   f'fill="{ink}" text-anchor="middle">{span}</text>')
        top = y - 1.6 * fs
    col = sorted({round(p["at"][1], 4) for p in pads if abs(p["at"][0] - min(xs)) < 1e-6})
    if len(col) >= 2:                                    # the pitch, left of the courtyard, between the first two
        x = c[0] - 1.2 * fs
        a, b = col[0], col[1]
        for yy in (a, b):
            line((min(xs) - 0.2, yy), (x - 0.4 * fs, yy), dash=f' stroke-dasharray="{_f(sw*3)} {_f(sw*3)}"')
        line((x, a), (x, b))
        t = f"{_f(b - a)}"
        out.append(f'<text x="{_f(x - 0.5 * fs)}" y="{_f((a + b) / 2 + 0.35 * fs)}" font-size="{_f(fs)}" '
                   f'fill="{ink}" text-anchor="end">{t}</text>')
        left = x - 0.5 * fs - lw(t) - fs
    m = 1.5 * fs
    vb = (min(left, c[0]) - m, min(top, c[1]) - m, 0.0, 0.0)
    vb = (vb[0], vb[1], c[2] + m - vb[0], c[3] + m - vb[1])
    px = 900 / vb[2]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(vb[0])} {_f(vb[1])} {_f(vb[2])} {_f(vb[3])}" '
         f'width="900" height="{round(vb[3] * px)}" font-family="IBM Plex Mono, Menlo, monospace">',
         f'<rect x="{_f(vb[0])}" y="{_f(vb[1])}" width="{_f(vb[2])}" height="{_f(vb[3])}" fill="#0c1220"/>',
         f'<rect x="{_f(c[0])}" y="{_f(c[1])}" width="{_f(c[2]-c[0])}" height="{_f(c[3]-c[1])}" fill="none" '
         f'stroke="#5a6478" stroke-width="{_f(sw)}" stroke-dasharray="{_f(sw*4)} {_f(sw*3)}"/>']
    x0, y0, x1, y1 = fp["body"]
    s.append(f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(x1-x0)}" height="{_f(y1-y0)}" fill="none" '
             f'stroke="#c9d0de" stroke-width="{_f(sw)}"/>')
    s += out                                             # (the dimensions under the pads: lands cover their leaders)
    for p in fp["pads"]:
        b = _pad_box(p)
        if p["shape"] == "circle":
            s.append(f'<circle cx="{_f(p["at"][0])}" cy="{_f(p["at"][1])}" r="{_f(p["size"][0]/2)}" fill="#d0584c"/>')
        else:
            rr = min(b[2] - b[0], b[3] - b[1]) * (0.25 if p["shape"] == "roundrect" else 0)
            s.append(f'<rect x="{_f(b[0])}" y="{_f(b[1])}" width="{_f(b[2]-b[0])}" height="{_f(b[3]-b[1])}" '
                     f'rx="{_f(min(rr, 0.25))}" fill="#d0584c"/>')
        if p.get("drill"):
            s.append(f'<circle cx="{_f(p["at"][0])}" cy="{_f(p["at"][1])}" r="{_f(p["drill"]/2)}" fill="#0c1220"/>')
    if pads:                                             # pin 1, named on its land when it fits, else beside it
        b = _pad_box(pads[0])
        t = pads[0]["num"]
        if lw(t) < (b[2] - b[0]) * 0.8 and fs * 0.9 < (b[3] - b[1]):
            s.append(f'<text x="{_f((b[0]+b[2])/2)}" y="{_f((b[1]+b[3])/2 + 0.32 * fs)}" font-size="{_f(fs * 0.9)}" '
                     f'fill="#ffffff" text-anchor="middle">{t}</text>')
    if "ep" in fp["info"]:
        w, h = fp["info"]["ep"]
        t = f"EP {_f(w)} x {_f(h)}"
        if lw(t) < w * 0.9:
            s.append(f'<text x="0" y="{_f(0.35 * fs)}" font-size="{_f(fs)}" fill="#0c1220" text-anchor="middle">{t}</text>')
    s.append("</svg>")
    return "\n".join(s)


def review(fp: dict, checks: List[dict], path: str, drawing: Optional[str] = None,
           kicad_svg: Optional[str] = None) -> str:
    """``path``: an HTML page with the drawing, the footprint with its dimensions, KiCad's render and the checks"""
    import base64
    import html
    img = ""
    if drawing:
        ext = Path(drawing).suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
                "webp": "image/webp", "pdf": "application/pdf"}.get(ext, "image/png")
        data = base64.b64encode(Path(drawing).read_bytes()).decode()
        img = (f'<embed src="data:{mime};base64,{data}" type="{mime}" style="width:100%;height:70vh">' if ext == "pdf"
               else f'<img src="data:{mime};base64,{data}" style="width:100%;height:auto">')
    sev = {"ok": "#2f7d4f", "warning": "#a5621a", "error": "#b2412f"}
    rows = "".join(f'<li style="color:{sev[c["severity"]]}"><b>{c["severity"].upper()}</b> {html.escape(c["text"])}</li>'
                   for c in checks)
    spec = html.escape(json.dumps(fp["spec"], indent=1))
    info = fp["info"]
    facts = [("land", f'{_f(info["pad"][0])} x {_f(info["pad"][1])} mm' if "pad" in info else "from the table"),
             ("pitch", f'{info.get("pitch", "-")} mm'), ("pins", str(len([p for p in fp["pads"] if not p.get("ep")])))]
    if "centre" in info:
        facts.append(("row centres", f'{_f(2 * info["centre"])} mm apart'))
    if "ep" in info:
        facts.append(("exposed pad", f'{_f(info["ep"][0])} x {_f(info["ep"][1])} mm, 64 % pasted'))
    if "drill" in info:
        facts.append(("hole", f'{info["drill"]} mm'))
    c = fp["courtyard"]
    facts.append(("courtyard", f'{_f(c[2]-c[0])} x {_f(c[3]-c[1])} mm'))
    ftab = "".join(f"<tr><td>{a}</td><td>{html.escape(b)}</td></tr>" for a, b in facts)
    verdict = "error" if any(c["severity"] == "error" for c in checks) else "ok"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(fp["name"])}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;background:#f4f1e9;color:#1d1b17;font:14px/1.5 -apple-system,'IBM Plex Sans',sans-serif}}
main{{max-width:1400px;margin:0 auto;padding:24px 16px}} h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#6d685d;margin-bottom:18px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #e3ddcf;border-radius:10px;padding:14px;overflow:hidden}}
.card h2{{font:600 11px 'IBM Plex Mono',monospace;letter-spacing:2px;color:#8a8578;margin:0 0 10px;text-transform:uppercase}}
.card svg{{max-width:100%;height:auto}} .kicad svg{{width:100%;background:#fff}} table{{border-collapse:collapse}} td{{padding:2px 12px 2px 0}}
ul{{padding-left:18px;margin:0}} pre{{font-size:11px;background:#f7f5ef;padding:8px;overflow:auto;margin:0}}
.v{{display:inline-block;padding:2px 10px;border-radius:4px;color:#fff;background:{sev[verdict]};font-weight:600}}</style></head>
<body><main><h1>{html.escape(fp["name"])} <span class="v">{"CHECKS PASS" if verdict == "ok" else "FIX BEFORE USE"}</span></h1>
<div class="sub">Compare the land pattern with the drawing: the checks cannot see a dimension read wrong off it.</div>
<div class="grid">
<div class="card"><h2>The drawing</h2>{img or "<i>no drawing given (--drawing)</i>"}</div>
<div class="card"><h2>The land pattern, mm</h2>{figure(fp)}<table style="margin-top:10px">{ftab}</table></div>
<div class="card kicad"><h2>KiCad's render</h2>{kicad_svg or "<i>KiCad is not installed</i>"}</div>
<div class="card"><h2>Checks</h2><ul>{rows}</ul><h2 style="margin-top:14px">The spec read off the drawing</h2><pre>{spec}</pre></div>
</div></main></body></html>"""
    Path(path).write_text(page)
    return path


# ---- the whole step -----------------------------------------------------------------------------------------------
def build(spec: dict, out_dir: str = "footprints", lib: str = "Zeus_Generated", drawing: Optional[str] = None,
          symbol: Optional[str] = None, house: str = "jlcpcb-2") -> dict:
    """spec -> the footprint written, checked, reviewed: {"footprint": "Lib:Name", "path", "review", "checks", "ok"}"""
    fp = generate(spec)
    d = Path(out_dir) / f"{lib}.pretty"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{fp['name']}.kicad_mod"
    descr = spec.get("descr") or (f"{spec.get('part', '')} from its package drawing; IPC-7351B nominal (zeus fpgen)").strip()
    path.write_text(kicad_mod(fp, descr))
    checks = check(fp, symbol, house)
    k = kicad_check(str(path))
    if k is None:
        checks.append({"severity": "ok", "text": "KiCad reads it (kicad-cli fp export)"})
    elif k:
        checks.append({"severity": "error", "text": "KiCad cannot read it: " + k})
    import pcb                                           # (and the harness reads it back to the same pads)
    if Path(out_dir).resolve() not in pcb.FP_DIRS:
        pcb.FP_DIRS.insert(0, Path(out_dir).resolve())
    pcb._FP.pop(f"{lib}:{fp['name']}", None)
    back = pcb.footprint(f"{lib}:{fp['name']}")
    n_back = sum(len(v) for v in back["pads"].values())
    checks.append({"severity": "ok" if n_back == len(fp["pads"]) else "error",
                   "text": f"the harness reads back {n_back} pads"})
    rpath = str(d / f"{fp['name']}_review.html")
    review(fp, checks, rpath, drawing, _KSVG.get(str(path)))
    return {"footprint": f"{lib}:{fp['name']}", "path": str(path), "review": rpath, "checks": checks,
            "ok": not any(c["severity"] == "error" for c in checks)}


TEMPLATES = {
    "soic": {"family": "soic", "part": "the part", "pins": 8, "pitch": 1.27, "body": [[4.8, 5.0], [3.8, 4.0]],
             "span": [5.8, 6.2], "lead_length": [0.4, 1.27], "lead_width": [0.31, 0.51]},
    "sot23": {"family": "soic", "package": "SOT-23", "pins": 3, "pitch": 0.95, "body": [2.9, 1.3],
              "span": [2.1, 2.64], "lead_length": [0.3, 0.6], "lead_width": [0.3, 0.5],
              "rows": {"left": ["1", None, "2"], "right": [None, "3", None]}},
    "qfp": {"family": "qfp", "package": "LQFP", "pins": 48, "pitch": 0.5, "body": [7, 7], "span": [8.8, 9.2],
            "lead_length": [0.45, 0.75], "lead_width": [0.17, 0.27]},
    "qfn": {"family": "qfn", "pins": 16, "pitch": 0.5, "body": [3, 3], "lead_length": [0.3, 0.5],
            "lead_width": [0.18, 0.3], "ep": [1.7, 1.7]},
    "dfn": {"family": "dfn", "pins": 8, "pitch": 0.5, "body": [2, 2], "lead_length": [0.25, 0.45],
            "lead_width": [0.18, 0.3], "ep": [1.2, 0.8]},
    "bga": {"family": "bga", "grid": [8, 8], "pitch": 0.8, "ball": [0.35, 0.45], "body": [7, 7], "missing": []},
    "tht": {"family": "tht", "grid": [2, 5], "pitch": 2.54, "lead": [0.64, 0.64], "numbering": "rows"},
    "pads": {"family": "pads", "pins": 2, "body": [2, 1.2],
             "pads": [{"num": "1", "at": [-0.8, 0], "size": [0.6, 0.9]}, {"num": "2", "at": [0.8, 0], "size": [0.6, 0.9]}]},
}


def main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="zeus footprint", description="a footprint from a package drawing's dimensions")
    ap.add_argument("spec", help="the spec (JSON) read off the drawing, or 'template:<family>' to print one")
    ap.add_argument("--drawing", help="the package drawing (PNG / JPG / PDF), shown beside the result")
    ap.add_argument("--symbol", help="the part's KiCad symbol: its pins must be the pads")
    ap.add_argument("--out", default="footprints", help="the project footprint folder (default ./footprints)")
    ap.add_argument("--lib", default="Zeus_Generated", help="the library name (default Zeus_Generated)")
    ap.add_argument("--house", default="jlcpcb-2")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-open", action="store_true", help="do not open the review page")
    a = ap.parse_args(argv)
    if a.spec.startswith("template:"):
        print(json.dumps(TEMPLATES[a.spec.split(":", 1)[1]], indent=1))
        return 0
    spec = json.loads(Path(a.spec).read_text())
    r = build(spec, a.out, a.lib, a.drawing or spec.get("drawing"), a.symbol or spec.get("symbol"), a.house)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"{r['footprint']}  ->  {r['path']}")
        for c in r["checks"]:
            print(f"  [{c['severity']}] {c['text']}")
        print(f"review: {r['review']}")
        print(f'use it: b.place("U1", "{r["footprint"]}", ...)  (keep {a.out}/ beside the board script)')
    if not a.no_open and not os.environ.get("CI"):
        import webbrowser
        webbrowser.open(Path(r["review"]).resolve().as_uri())
    return 0 if r["ok"] else 1
