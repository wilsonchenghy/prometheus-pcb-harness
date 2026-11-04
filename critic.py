"""A fast design review: the board against its requirements and against what a reviewer would flag on any board,
in milliseconds and before (or after) routing.

    import critic
    notes = critic.review(board, spec)          # spec: a dict, or critic.load_spec("spec.json"), or None
    for n in notes: print(n["severity"], n["text"])

Each note is {"severity": "error" | "warning" | "info", "kind", "text", "refs", "hint"}. An error is a broken
requirement (the board is not what was asked for); a warning is a likely problem a reviewer would send back; an
info is worth knowing. The requirements file (JSON) may give any of:

    {
      "size_mm": [60, 40],                   board no larger than this (either orientation)
      "layers": 2,                           exactly this many copper layers
      "house": "jlcpcb-2",                   the fab preset the board must be made to
      "nets": {"VBUS": {"amps": 2.0}},       at least this current on that net (sets the check's width)
      "pairs": [["USB_DP", "USB_DM", 0.5]],  differential pairs and their allowed length mismatch (mm)
      "near": [["C1", "U1", 3.0]],           this part within this distance (pad to pad, mm) of that one (or of
                                             one pin: "U1.9", "U1.VDDA")
      "edge": ["J1", "J2"],                  these parts at the board edge (within 2 mm)
      "holes": {"count": 4, "d": 3.2},       mounting holes
      "assembly": true,                      every part needs an LCSC number (JLCPCB assembly)
      "max_parts": 60
    }
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

POWER = re.compile(r"^(\+?\d+V\d*|\d+V\d+|VCC\w*|VDD\w*|AVDD|DVDD|IOVDD|V3V3|3V3|5V|VBUS|VBAT\w*|VIN\w*|VSYS)$", re.I)
GROUND = re.compile(r"^(GND\w*|AGND|DGND|PGND|VSS\w*|0V)$", re.I)
USB_PAIRS = [("USB_DP", "USB_DM"), ("DP", "DM"), ("D+", "D-"), ("USB_D+", "USB_D-"), ("USBDP", "USBDM")]


def load_spec(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _note(sev, kind, text, refs=(), hint=""):
    return {"severity": sev, "kind": kind, "text": text, "refs": list(refs), "hint": hint}


def _pad_points(board):
    """{(ref, num): [(x, y, polygon)]}, from the board's memoised pad shapes"""
    out: Dict[tuple, list] = {}
    for ref, num, L, g, d in board.pad_shapes():
        out.setdefault((ref, num), []).append(g)
    return out


def _is_ic(c) -> bool:
    return len([n for n in c["pads"] if n]) >= 6


def review(board, spec: Optional[dict] = None) -> List[dict]:
    """the review's notes, most severe first"""
    spec = spec or {}
    notes: List[dict] = []
    pn = board.pad_net()
    pads = _pad_points(board)
    unplaced = [r for r, c in board.comps.items() if c.get("unplaced")]
    placed = not unplaced
    x0, y0, x1, y1 = board.outline.bounds
    w, h = x1 - x0, y1 - y0

    # --- the requirements ---
    if "size_mm" in spec:
        a, b = sorted(spec["size_mm"])
        if sorted((w, h))[0] > a + 1e-6 or sorted((w, h))[1] > b + 1e-6:
            notes.append(_note("error", "size", f"the board is {w:.1f} x {h:.1f} mm; the requirement is at most "
                               f"{spec['size_mm'][0]} x {spec['size_mm'][1]} mm", hint="shrink the outline"))
    if "layers" in spec and len(board.layers) != int(spec["layers"]):
        notes.append(_note("error", "layers", f"{len(board.layers)} copper layers; the requirement is {spec['layers']}",
                           hint="Board(..., layers=N)"))
    if "house" in spec and board.rules != spec["house"]:
        notes.append(_note("error", "house", f"the board is made to {board.rules or 'no fab preset'}; the requirement "
                           f"is {spec['house']}", hint=f"Board(..., rules=\"{spec['house']}\")"))
    if "max_parts" in spec and len(board.comps) > spec["max_parts"]:
        notes.append(_note("error", "parts", f"{len(board.comps)} parts; the requirement is at most {spec['max_parts']}"))
    for n, req in (spec.get("nets") or {}).items():
        if n not in board.nets:
            notes.append(_note("error", "net", f"net {n} the requirements name is not on the board", hint="check the name"))
        elif req.get("amps") and board.nets[n].get("amps", 0) + 1e-9 < req["amps"]:
            notes.append(_note("error", "current", f"net {n} is declared for {board.nets[n].get('amps', 0)} A; the "
                               f"requirement is {req['amps']} A", hint=f"net(\"{n}\", ..., amps={req['amps']})"))
    declared = {(p, q) for p, q, _ in getattr(board, "pairs", [])}
    for pr in spec.get("pairs") or []:
        p, q = pr[0], pr[1]
        if p not in board.nets or q not in board.nets:
            notes.append(_note("error", "pair", f"pair {p} / {q}: not both nets are on the board"))
        elif (p, q) not in declared and (q, p) not in declared:
            notes.append(_note("error", "pair", f"pair {p} / {q} is not declared, so its lengths are not checked",
                               hint=f"b.pair(\"{p}\", \"{q}\", max_skew={pr[2] if len(pr) > 2 else 0.5})"))
    if placed:
        import pcb as _pcb
        for a, b, dmax, d in _pcb.near_gaps(board, spec.get("near") or []):     # (a part, or one pin: "U1.9")
            if d > dmax:
                notes.append(_note("error", "near", f"{a} is {d:.1f} mm from {b}; the requirement is within {dmax} mm",
                                   [a.partition(".")[0], b.partition(".")[0]], hint=f"move {a} toward {b}"))
        for r in spec.get("edge") or []:
            if r in board.comps:
                g = board.courtyard(r)
                if g is not None and g.distance(board.outline.exterior) > 2.0 and not board.comps[r]["overhang"]:
                    notes.append(_note("error", "edge", f"{r} is {g.distance(board.outline.exterior):.1f} mm from the "
                                       "edge; the requirement is at the edge", [r], hint=f"move {r} to the edge"))
    if "holes" in spec:
        want = spec["holes"]
        have = board.holes
        if len(have) < want.get("count", 0):
            notes.append(_note("error", "holes", f"{len(have)} mounting holes; the requirement is {want['count']}",
                               hint=f"b.hole((x, y), d={want.get('d', 3.2)})"))
        elif want.get("d") and any(abs(d - want["d"]) > 0.05 for *_, d in have):
            notes.append(_note("error", "holes", f"mounting holes of {sorted({d for *_, d in have})} mm; the "
                               f"requirement is {want['d']} mm"))
    if spec.get("assembly"):
        missing = sorted(r for r, c in board.comps.items() if not c.get("lcsc"))
        if missing:
            notes.append(_note("error", "assembly", f"{len(missing)} parts have no LCSC number for assembly: "
                               f"{', '.join(missing[:12])}{' ...' if len(missing) > 12 else ''}", missing,
                               hint="place(..., lcsc=\"C...\")"))

    # --- what a reviewer flags on any board ---
    if unplaced:
        notes.append(_note("info", "placement", f"{len(unplaced)} parts have no position yet: the placement notes "
                           "wait for autoplace", unplaced))
    ics = [r for r, c in board.comps.items() if _is_ic(c) and not r.upper().startswith(("J", "SW", "H"))]
    caps = [r for r, c in board.comps.items() if r.upper().startswith("C") and len([n for n in c["pads"] if n]) == 2]
    if placed:
        # decoupling: each IC's supply pins with a capacitor of that supply close by
        for ic in ics:
            far = []
            sym = board.comps[ic].get("symbol")         # (with its symbol: its power inputs, not a reset tied high)
            supply = {q["num"] for q in sym["pins"] if q["type"] == "power_in"} if sym else None
            for num in board.comps[ic]["pads"]:
                n = pn.get((ic, num))
                if not n or not POWER.match(n) or (ic, num) not in pads or (supply is not None and num not in supply):
                    continue
                best = None
                for c in caps:
                    for cn in board.comps[c]["pads"]:
                        if pn.get((c, cn)) == n and (c, cn) in pads:
                            d = min(a.distance(b) for a in pads[(ic, num)] for b in pads[(c, cn)])
                            best = d if best is None else min(best, d)
                if best is None:
                    far.append(f"{ic}.{num} ({n}): no capacitor on it")
                elif best > 4.0:
                    far.append(f"{ic}.{num} ({n}): nearest capacitor {best:.1f} mm")
            if far:
                notes.append(_note("warning", "decoupling", f"{ic}: supply pins without close decoupling (keep a "
                                   f"capacitor within 4 mm): " + "; ".join(far[:6]) + (" ..." if len(far) > 6 else ""),
                                   [ic], hint="move the capacitors to those pins, or add one per pin"))
        # crystals by their IC
        for r, c in board.comps.items():
            if not re.match(r"^(Y|X)\d", r):
                continue
            nets_ = {pn.get((r, num)) for num in c["pads"]} - {None}
            nets_ = {n for n in nets_ if not GROUND.match(n)}
            for ic in ics:
                if any(pn.get((ic, num)) in nets_ for num in board.comps[ic]["pads"]):
                    d = _part_gap(pads, r, ic)
                    if d is not None and d > 6.0:
                        notes.append(_note("warning", "crystal", f"crystal {r} is {d:.1f} mm from {ic} (keep within "
                                           "6 mm: long oscillator traces pick up noise)", [r, ic],
                                           hint=f"move {r} next to {ic}'s oscillator pins"))
        # connectors at the edge
        for r, c in board.comps.items():
            if not r.upper().startswith("J") or c["overhang"]:
                continue
            g = board.courtyard(r)
            if g is not None and g.distance(board.outline.exterior) > 3.0 and "PinHeader" not in c["fp"] \
                    and "PinSocket" not in c["fp"]:
                notes.append(_note("warning", "edge", f"connector {r} sits {g.distance(board.outline.exterior):.1f} mm "
                                   "inside the board: a cable or plug usually needs it at the edge", [r]))
    # USB pairs, declared and close in length
    for p, q in USB_PAIRS:
        if p in board.nets and q in board.nets and (p, q) not in declared and (q, p) not in declared:
            notes.append(_note("warning", "pair", f"{p} / {q} look like a USB pair but are not declared: their lengths "
                               "are not matched", [p, q], hint=f"b.pair(\"{p}\", \"{q}\", max_skew=0.5)"))
    # radio modules want copper kept away from the antenna
    for r, c in board.comps.items():
        if re.search(r"RF_Module|ESP32|ESP8266|nRF52|WROOM|Antenna", c["fp"], re.I) and not getattr(board, "keepouts", []):
            notes.append(_note("warning", "antenna", f"{r} is a radio module but the board has no copper keep-out "
                               "under its antenna", [r], hint="b.keepout(polygon, \"FB\", \"antenna\")"))
    # a ground plane on a board with a processor
    if ics and not any(GROUND.match(p["net"]) for p in board.pours):
        notes.append(_note("warning", "ground", "no ground pour: return currents and decoupling want one",
                           hint="b.pour(\"GND\", \"B\", outline)"))
    if not board.holes and not spec.get("holes"):
        notes.append(_note("info", "holes", "no mounting holes", hint="b.hole((x, y), d=3.2)"))
    if not spec.get("assembly"):
        missing = [r for r, c in board.comps.items() if not c.get("lcsc")]
        if missing and len(missing) < len(board.comps):
            notes.append(_note("info", "assembly", f"{len(missing)} of {len(board.comps)} parts have no LCSC number "
                               "(JLCPCB assembly)", missing))
    order = {"error": 0, "warning": 1, "info": 2}
    if any(cc.get("symbol") for cc in board.comps.values()):   # (the ERC, from the parts' symbols' pin types)
        import parts
        notes += parts.erc(board)
    return sorted(notes, key=lambda n: order[n["severity"]])


def _part_gap(pads, a, b) -> Optional[float]:
    ga = [g for (r, _), gs in pads.items() if r == a for g in gs]
    gb = [g for (r, _), gs in pads.items() if r == b for g in gs]
    if not ga or not gb:
        return None
    from shapely.ops import unary_union
    return unary_union(ga).distance(unary_union(gb))
