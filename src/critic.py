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
      "holes": {"count": 4, "d": 3.2},       mounting holes (or just the count: "holes": 4)
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


# why each rule matters, in a sentence: a finding teaches the engineer reading it, it does not only order a fix
WHY = {
    "decoupling": "A supply pin draws current in fast edges that its capacitor supplies; the farther the capacitor, the "
                  "larger the loop those edges flow round, and that loop's inductance becomes supply noise and EMI.",
    "near": "Parts that work together (a crystal and its chip, a sense resistor and its amplifier) work best with short "
            "connections; you set this distance in the requirements.",
    "crystal": "A crystal's traces are high impedance and pick up noise, and their length adds capacitance that pulls "
               "its frequency: keep it beside its chip, over unbroken ground.",
    "edge": "A connector a cable plugs into has to reach the board edge; copper too close to the edge is cut or exposed "
            "when the board is routed out of its panel.",
    "antenna": "Copper near an antenna detunes it and absorbs its power: the module's keep-out is what keeps its range.",
    "ground": "A solid ground plane gives every signal a return path right under it, keeping current loops small: less "
              "noise and less EMI.",
    "pair": "A differential pair's halves must match in length so both edges arrive together; skew turns into noise "
            "and bit errors at speed.",
    "current": "A trace too narrow for its current heats up (IPC-2221 relates width, copper weight and temperature "
               "rise); too hot, it degrades or lifts.",
    "voltage drop": "Every trace has resistance: at high current the drop along it lowers the voltage the load sees.",
    "size": "Board area costs money and empty board means long traces: size the board to its parts unless an enclosure "
            "decides it.",
    "holes": "Mounting holes hold the board without stressing solder joints; they are easiest to plan before parts "
             "crowd the corners.",
    "house": "Every fab has a smallest track, gap and drill it can make; below them the board cannot be built or costs "
             "more.",
    "layers": "You set the layer count: more layers cost more but give room for planes and routing.",
    "assembly": "JLCPCB assembles only the parts it stocks, named by their LCSC number.",
    "via current": "A via's plated barrel is a few hundredths of a square millimetre of copper: one 0.3 mm via carries "
                   "about an amp before it heats like an undersized track, and a power path that changes layer through "
                   "one is as weak as that via.",
    "via neck": "A via right against a pad of its own net drinks the pad's solder in reflow: the joint starves and a "
                 "small part can tombstone. A short neck of track between them keeps the solder on the pad.",
    "lcsc suggested": "A part found by its value and package, not its exact part number, can be a near miss (another "
                      "load capacitance, another height): only you can say it is the part you designed for.",
    "clearance": "Copper of two nets too close can short in manufacturing or arc at high voltage; the fab's minimum "
                 "gap is the floor.",
    "short": "Two nets touch: the circuit cannot work and may be damaged at power-up.",
    "unreached": "A net whose pads are not all joined by copper is an open circuit: the part on the missing pad never "
                 "gets its signal.",
    "split": "A net in pieces is an open circuit between them.",
    "dangling": "A track or via that leads nowhere does nothing but add an antenna and take room.",
    "drill": "Holes below the fab's smallest drill cannot be made, and holes too close break into each other.",
    "symbol": "A chip drawn as a box of pad numbers has no pin names: nothing checks that pin 15 really is AIN2, and the "
              "ERC cannot see a power pin left open. Its KiCad symbol (or one drawn from the datasheet's pin table) "
              "makes the schematic readable and every pin checked by name.",
    "erc": "Each pin's electrical type says how it must be driven: an input left open or two outputs tied together are "
           "faults no copper check can see.",
    "datasheet": "A symbol's pin numbers must match the manufacturer's: a wrong pin is the one mistake the board checks "
                 "cannot catch.",
    "label": "An assembler and anyone debugging the board find a part by the reference printed beside it; one left "
             "off the silkscreen has to be looked up in the drawings.",
    "evidence": "A choice cited to a datasheet must say what that page says: a quote not there is a value remembered, "
                "not read.",
}


def _note(sev, kind, text, refs=(), hint=""):
    return {"severity": sev, "kind": kind, "text": text, "refs": list(refs), "hint": hint, "why": WHY.get(kind, "")}


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
    if placed and board.comps:                          # no more board than the parts need
        cys = {r: board.courtyard(r) for r in board.comps}
        cys = {r: g for r, g in cys.items() if g is not None}
        used = sum(g.area for g in cys.values()) / board.outline.area
        k = (used / 0.35) ** 0.5
        if "size_mm" not in spec and used < 0.15:      # (the bench boards run 26-57 %)
            notes.append(_note("error", "size", f"the parts cover {used:.0%} of the {w:.0f} x {h:.0f} mm board; "
                               f"about {w * k:.0f} x {h * k:.0f} mm would do", hint="shrink the outline, or give size_mm "
                               "in the requirements when the enclosure sets it"))
        elif used < 0.25:                              # (size_mm is the most the enclosure allows, not a size to fill)
            notes.append(_note("warning", "size", f"the parts cover {used:.0%} of the {w:.0f} x {h:.0f} mm board; "
                               f"about {w * k:.0f} x {h * k:.0f} mm would do", hint="shrink the outline: size_mm is "
                               "the most the board may be, not a size to fill"))
        # bare board past the parts at an edge (mounting holes go with the outline, so they are left out)
        own = [g for r, g in cys.items() if not str(board.comps[r].get("fp", "")).startswith("MountingHole")]
        if own:
            bx0 = min(g.bounds[0] for g in own); by0 = min(g.bounds[1] for g in own)
            bx1 = max(g.bounds[2] for g in own); by1 = max(g.bounds[3] for g in own)
            gaps = {"left": (bx0 - x0, w), "right": (x1 - bx1, w), "top": (by0 - y0, h), "bottom": (y1 - by1, h)}
            spare = {s_: g_ for s_, (g_, d_) in gaps.items() if g_ > max(4.0, 0.12 * d_)}
            if spare:
                dw = sum(g_ - 1.5 for s_, g_ in spare.items() if s_ in ("left", "right"))
                dh = sum(g_ - 1.5 for s_, g_ in spare.items() if s_ in ("top", "bottom"))
                notes.append(_note("warning", "size", "bare board past the parts: " + ", ".join(
                    f"{g_:.1f} mm on the {s_}" for s_, g_ in spare.items()) + f"; about {w - dw:.0f} x {h - dh:.0f} mm "
                    "would hold them", hint="shrink the outline (and move the mounting holes with it)"))
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
        if isinstance(want, (int, float)) and not isinstance(want, bool):   # ("holes": 4, the count alone)
            want = {"count": int(want)}
        elif isinstance(want, (list, tuple)):            # ("holes": [[x, y, d], ...]: where they must be)
            want = {"count": len(want)}
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
    # a part whose footprint marks where the board edge should run (a jack's notch, a panel-mount pot's edge line):
    # the outline is the board's own, so a mark away from it is a part not where its maker meant it
    import math as _m
    import pcb as _pcb
    from shapely.geometry import LineString as _LSe
    for r, c in board.comps.items():
        if c.get("unplaced"):                            # (not placed yet: nowhere to measure from)
            continue
        try:
            segs = _pcb.footprint(c["fp"]).get("edge_marks") or []
        except (OSError, ValueError):
            continue
        if not segs:
            continue
        a_ = _m.radians(c["rot"])
        mir = -1.0 if c.get("side", "F") == "B" else 1.0
        put = lambda q: (c["at"][0] + mir * q[0] * _m.cos(a_) - q[1] * _m.sin(a_),
                         c["at"][1] + mir * q[0] * _m.sin(a_) + q[1] * _m.cos(a_))
        edge_ = board.outline.exterior
        off = min(edge_.distance(_LSe([put(q) for q in sg])) for sg in segs)     # (a notch: one side on it)
        if off > 0.5:
            notes.append(_note("warning", "edge", f"{r}'s footprint ({c['fp'].split(':')[1]}) marks where the board "
                               f"edge should run, {off:.1f} mm from the outline (the KiCad file keeps the mark on "
                               "Dwgs.User): the part sits off the edge its maker drew it for", [r],
                               hint=f"move {r} so its edge mark lies on the outline, or shape the outline to it"))
    # a ground plane on a board with a processor
    if ics and not any(GROUND.match(p["net"]) for p in board.pours):
        notes.append(_note("warning", "ground", "no ground pour: return currents and decoupling want one",
                           hint="b.pour(\"GND\", \"B\", outline)"))
    if board.vias and placed:                           # (a power net through too few vias)
        try:
            weak = board.via_current()
        except Exception:                                # (a check that cannot run says nothing)
            weak = []
        for net, ats, cap, amps in weak:
            notes.append(_note("warning", "via current", f"{net} ({amps:g} A) changes layer through {len(ats)} "
                               f"via{'s' if len(ats) > 1 else ''} near ({ats[0][0]:.1f}, {ats[0][1]:.1f}), good for "
                               f"about {cap:g} A: the only way between its parts there", [net],
                               hint=f"add vias there (each 0.3 mm drill ~1 A), or keep {net} on one layer"))
    if board.vias:                                       # (a via against an SMD pad of its own net: solder wicks)
        from shapely.geometry import Point
        from fab import VIA_PAD_NECK
        smd = [(r, n, g) for r, n, L, g, d in board.pad_shapes() if not d]
        for v in board.vias:
            ring = Point(v["at"]).buffer(v["dia"] / 2, 16)
            for r, n, g in smd:
                if pn.get((r, n)) != v["net"] or g.contains(ring):     # (another net, or a via-in-pad: all on the pad)
                    continue
                gap = ring.distance(g)
                if gap < VIA_PAD_NECK - 1e-6:
                    notes.append(_note("warning", "via neck", f"via {v['net']} at ({v['at'][0]:.2f}, {v['at'][1]:.2f}) is "
                                       f"{gap:.2f} mm from {r}.{n} (keep {VIA_PAD_NECK} mm of track between them)", [r],
                                       hint="move the via off the pad, or route a short neck to it"))
    if not board.holes and not spec.get("holes"):
        notes.append(_note("info", "holes", "no mounting holes", hint="b.hole((x, y), d=3.2)"))
    if not spec.get("assembly"):
        missing = [r for r, c in board.comps.items() if not c.get("lcsc")]
        if missing and len(missing) < len(board.comps):
            notes.append(_note("info", "assembly", f"{len(missing)} of {len(board.comps)} parts have no LCSC number "
                               "(JLCPCB assembly)", missing))
    for r, c in board.comps.items():                     # (parts.suggest_lcsc's finds: for the engineer to adopt)
        g = c.get("lcsc_suggested")
        if g and not c.get("lcsc"):
            notes.append(_note("info", "lcsc suggested", f"{r}: no LCSC number; JLCPCB has {g['lcsc']} ({g['part']}, by "
                               f"'{g['query']}'): add lcsc= if it is the right part", [r], hint=f"lcsc=\"{g['lcsc']}\""))
    if placed:                                           # (a reference with no room beside its part)
        import pcb
        off = sorted(r for r, v in pcb.place_refs(board).items() if v is None)
        if off:
            notes.append(_note("warning", "label", f"no room on the silkscreen for {', '.join(off)}: left on the "
                               "assembly layer", off, hint="move the part (or its neighbours) to leave a label's "
                               "height clear beside it; autoplace keeps that room for the parts it places"))
    order = {"error": 0, "warning": 1, "info": 2}
    for r, c in board.comps.items():                     # (a chip without its symbol: drawn as pad numbers, unchecked)
        if _is_ic(c) and not re.match(r"(J|P|CN|CON)\d", r) and not c["fp"].startswith("Connector") \
                and not (c.get("symbol") and not c["symbol"].get("auto")):
            import parts
            try:
                found = parts.suggest_symbol(c)
            except (OSError, ValueError):                # (no KiCad library here: the warning still stands)
                found = None
            notes.append(_note("error", "symbol", f"{r} ({c['value']}) has no KiCad symbol: the schematic draws its "
                               "pad numbers and its pins are not checked by name" +
                               (f"; KiCad has {found}" if found else "; KiCad has none that fits its pads"),
                               refs=[r], hint=f'place(..., symbol="{found}")' if found else
                               "draw its symbol from the datasheet's pin table (symbols/), and check it against the datasheet"))
    if any(cc.get("symbol") for cc in board.comps.values()):   # (the ERC, from the parts' symbols' pin types)
        import parts
        notes += parts.erc(board)
    for n in notes:                                      # (the ERC's and the datasheet's notes too)
        n.setdefault("why", WHY.get(n["kind"], ""))
    return sorted(notes, key=lambda n: order[n["severity"]])


def _part_gap(pads, a, b) -> Optional[float]:
    ga = [g for (r, _), gs in pads.items() if r == a for g in gs]
    gb = [g for (r, _), gs in pads.items() if r == b for g in gs]
    if not ga or not gb:
        return None
    from shapely.ops import unary_union
    return unary_union(ga).distance(unary_union(gb))
