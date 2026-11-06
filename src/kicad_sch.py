"""The board's schematic as KiCad's own ``.kicad_sch``: every part on its KiCad library symbol (the one it was placed
with, else Device:R / C / L / D / LED ... by its kind), grouped into titled blocks by the signals they share (an IC
with the parts on its pins; a part on power nets only joins the block it shares most with), each pin wired by a
short stub to a net label (a GND symbol on ground, an X where a pin is left open), on the smallest ISO sheet that
holds it, with KiCad's title block. Opens in Eeschema; ``kicad-cli sch export svg`` renders it.

    import kicad_sch
    path = kicad_sch.write(board, "out/kicad")          # out/kicad/<name>.kicad_sch
    kicad_sch.check(board, path)                         # [] when KiCad's netlist of the sheet is the board's

Every part's room (its symbol, pins, stubs, labels and text) is reserved before it is placed, so nothing overlaps;
``overlaps()`` proves it on the layout written."""
from __future__ import annotations

import datetime
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import parts
import pcb
from pcb import _Q, _sx_dump, _sx_parse, _uuid

G = 2.54                                                 # (KiCad's connection grid)
STUB = 2 * G                                             # (pin end to label)
CH = 1.0                                                 # (a 1.27 mm character's width, KiCad's font, with room)
PAPER = [("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0), ("A0", 1189.0, 841.0)]
TITLE_BLOCK = (112.0, 36.0)                              # (KiCad's default title block, bottom right, with a margin)
MARGIN = 12.0


def _snap(v: float) -> float:
    return round(round(v / G) * G, 4)


# ---- which symbol a part goes on ----------------------------------------------------------------------------------
def symbol_for(ref: str, c: dict) -> str:
    """the KiCad symbol "Lib:Name" for a part: the one it was placed with, else the generic one for its kind"""
    if c.get("symbol"):
        return c["symbol"]["name"]
    import pcb_view
    kind = pcb_view.kind_of(ref, c["fp"])
    text = " ".join(str(c.get(k) or "") for k in ("value", "mpn", "note", "fp")).lower()
    n = len({p for p in c["pads"] if p})
    if ref.startswith("FB") or "ferrite" in text:
        return "Device:FerriteBead"
    if kind == "res":
        return "Device:R"
    if kind == "cap":
        return "Device:C"
    if kind == "electrolytic":
        return "Device:C_Polarized"
    if kind == "inductor":
        return "Device:L"
    if kind == "led":
        return "Device:LED"
    if kind == "diode" and n == 2:
        if "schottky" in text or re.search(r"\b(ss\d|b[1-5]\d0|pds|bat|mbr|stps)", text):
            return "Device:D_Schottky"
        if "zener" in text or re.search(r"\b(bz|mmsz|zm)", text):
            return "Device:D_Zener"
        if "tvs" in text or re.search(r"\b(sm[abcd]j|p[46]ke|\d\.\dsmdj)", text):
            return "Device:D_TVS"
        return "Device:D"
    if kind == "fuse":
        return "Device:Fuse"
    if kind == "pot" and n == 3:
        return "Device:R_Potentiometer"
    if kind == "crystal":
        return "Device:Crystal" if n == 2 else "Device:Crystal_GND24" if n == 4 else _generic(n)
    if "USB_C" in c["fp"] and n in (16, 17):
        return "Connector:USB_C_Receptacle_USB2.0_16P"
    if kind == "pad" and n == 1:
        return "Connector:TestPoint"
    if kind == "switch" and n == 2:
        return "Switch:SW_Push"
    return _generic(n)


def _generic(n: int) -> str:
    """a numbered pin row: every pad its own pin, by its number (a part the harness has no symbol for)"""
    return f"Connector_Generic:Conn_01x{max(1, n):02d}" if n <= 60 else f"Connector_Generic:Conn_02x{(n + 1) // 2:02d}_Odd_Even"


_BOX: Dict[str, Tuple[list, dict]] = {}


def box_symbol(ref: str, c: dict) -> str:
    """a box symbol made for a part from its own pads (KiCad has none for it, and its pads are not numbered 1..n,
    as a USB-C's A5 / B6): each pad a passive pin named by its number, half down each side; "Prometheus:<footprint>"
    """
    pads = sorted({p for p in c["pads"] if p}, key=lambda p: (re.sub(r"\d", "", p), int(re.sub(r"\D", "", p) or 0), p))
    fp = re.sub(r"[^A-Za-z0-9_.-]", "_", c["fp"].split(":")[-1])
    name = f"Prometheus:{fp}"
    if name in _BOX:
        return name
    half = (len(pads) + 1) // 2
    w = max(5.08, _snap(max(len(p) for p in pads) * 1.0 * 2 + 3.0))
    top = _snap((half - 1) * G / 2 + G)
    pins, nodes = [], []
    for i, n in enumerate(pads):
        left = i < half
        y = top - G - (i if left else i - half) * G
        x = -w / 2 - G if left else w / 2 + G
        rot = 0 if left else 180
        pins.append({"num": n, "name": n, "key": n, "type": "passive", "unit": 1, "at": (x, y), "rot": float(rot),
                     "length": G})
        eff = ["effects", ["font", ["size", 1.27, 1.27]]]
        nodes.append(["pin", "passive", "line", ["at", x, y, rot], ["length", G], ["name", _Q(n), eff],
                      ["number", _Q(n), eff]])
    bot = top - G * (half + 1)
    body = (-w / 2, top, w / 2, bot)
    prop = lambda k, v, y, hide=False: ["property", _Q(k), _Q(v), ["at", -w / 2, y, 0],
                                        ["effects", ["font", ["size", 1.27, 1.27]], ["justify", "left"]] +
                                        ([["hide", "yes"]] if hide else [])]
    node = ["symbol", _Q(name), ["pin_names", ["offset", 1.016]], ["exclude_from_sim", "no"], ["in_bom", "yes"],
            ["on_board", "yes"], prop("Reference", "U", top + 1.27), prop("Value", fp, bot - 1.27),
            prop("Footprint", c["fp"], bot - 3.81, True), prop("Datasheet", "~", bot - 6.35, True),
            ["symbol", _Q(fp + "_0_1"), ["rectangle", ["start", body[0], body[1]], ["end", body[2], body[3]],
                                         ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]],
            ["symbol", _Q(fp + "_1_1")] + nodes]
    _BOX[name] = (node, {"name": name, "lib": "Prometheus", "footprint": c["fp"], "datasheet": "", "description": "",
                         "value": fp, "pins": pins, "body": [body]})
    return name


KICAD9_LIB = "9.0.9.1"                                   # (KiCad 9's symbol library: the format the sheet is in)


def _symbol9(lib: str, sym: str) -> Optional[str]:
    """the symbol's text from KiCad 9's library (one file per library there), cut out once and cached; None when
    KiCad 9's library does not have it (a symbol added since)"""
    import urllib.parse
    import urllib.request
    from vendor_parts import CACHE
    one = CACHE / "kicad_sym9" / f"{lib}__{sym}.kicad_sym"
    if one.exists():
        return one.read_text() or None
    whole = CACHE / "kicad_sym9" / f"{lib}.kicad_sym"
    if not whole.exists():
        url = f"https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/{KICAD9_LIB}/{urllib.parse.quote(lib)}.kicad_sym"
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read().decode()
        whole.parent.mkdir(parents=True, exist_ok=True)
        whole.write_text(data)
    text = whole.read_text()
    m = re.search(r'\n\t\(symbol "' + re.escape(sym) + r'"\n', text)
    out = ""
    if m:                                                # (its parentheses matched to the end of the symbol)
        i, depth, q = m.start() + 2, 0, False
        for j in range(i, len(text)):
            ch = text[j]
            if ch == '"' and text[j - 1] != "\\":
                q = not q
            elif not q and ch == "(":
                depth += 1
            elif not q and ch == ")":
                depth -= 1
                if depth == 0:
                    out = text[i:j + 1]
                    break
    one.write_text(out)
    return out or None


def _lib_symbol(name: str) -> list:
    """the symbol's definition from KiCad's library, named "Lib:Name" as a schematic embeds it: KiCad 9's (so the
    sheet opens in KiCad 9 and later), else today's with its newer tokens taken out; an alias (``extends``)
    flattened onto its parent's drawing"""
    if name in _BOX:
        return _BOX[name][0]
    lib, sym = name.split(":", 1)
    own = any((Path(d) / f"{lib}.kicad_symdir" / f"{sym}.kicad_sym").exists() for d in parts.SYM_DIRS)
    try:
        t9 = None if own else _symbol9(lib, sym)         # (a project symbol: its own file, already KiCad 9's form)
    except Exception:                                    # (offline with nothing cached: today's library)
        t9 = None
    nums9 = set(re.findall(r'\(number "([^"]*)"', t9 or ""))     # (an alias has none: its parent's are checked)
    if nums9 and nums9 != {p["num"] for p in parts.symbol(name)["pins"]}:
        t9 = None                                        # (KiCad 9 numbered its pins otherwise, USB-C's S1 now SH:
    if t9:                                               #  the pads are matched to today's numbers, so today's)
        top = _sx_parse(t9)
    else:
        tree = _sx_parse(parts._fetch(f"{lib}.kicad_symdir/{sym}.kicad_sym"))
        top = next(s for s in tree if isinstance(s, list) and s[:1] == ["symbol"] and s[1] == sym)
    ext = next((x[1] for x in top if isinstance(x, list) and x[:1] == ["extends"]), None)
    if ext:
        base = _lib_symbol(f"{lib}:{ext}")
        own = {x[1]: x for x in top if isinstance(x, list) and x[:1] == ["property"]}
        body = []
        for x in base[2:]:
            if isinstance(x, list) and x[:1] == ["property"] and x[1] in own:
                x = own.pop(x[1])
            elif isinstance(x, list) and x[:1] == ["symbol"]:
                x = ["symbol", _Q(sym + x[1][len(ext):])] + x[2:]
            body.append(x)
        top = ["symbol", _Q(sym)] + body + list(own.values())

    def clean(node):                                     # (KiCad 10's own tokens out: KiCad 9 opens the file too)
        return [clean(x) if isinstance(x, list) else x for x in node
                if not (isinstance(x, list) and x[:1] in (["in_pos_files"], ["extends"], ["do_not_autoplace"],
                                                          ["duplicate_pin_numbers_are_jumpers"], ["show_name"]))]
    return ["symbol", _Q(name)] + clean(top)[2:]


# ---- the layout ---------------------------------------------------------------------------------------------------
DIRS = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}   # (a symbol's angle -> its direction on the sheet, y down)


def _rot(x: float, y: float, r: int) -> Tuple[float, float]:
    """symbol coordinates (y up) turned ``r`` degrees counter-clockwise"""
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[r]
    return x * c - y * s, x * s + y * c


_EXT: Dict[str, Tuple[float, float, float, float]] = {}


def _extent(lib: str) -> Tuple[float, float, float, float]:
    """the symbol's drawing (lines, arcs, circles, boxes, not its pins), symbol coordinates (y up)"""
    if lib not in _EXT:
        xs, ys = [], []

        def walk(n):
            for x in n:
                if not isinstance(x, list) or x[:1] == ["pin"] or x[:1] == ["property"]:
                    continue
                if x[:1] in (["xy"], ["start"], ["end"], ["mid"], ["center"]) and len(x) >= 3:
                    xs.append(float(x[1]))
                    ys.append(float(x[2]))
                    if x[0] == "center":
                        r = next((float(y[1]) for y in n if isinstance(y, list) and y[:1] == ["radius"]), 0.0)
                        xs.extend((float(x[1]) - r, float(x[1]) + r))
                        ys.extend((float(x[2]) - r, float(x[2]) + r))
                walk(x)
        walk(_lib_symbol(lib))
        _EXT[lib] = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    return _EXT[lib]


class _Part:
    """one symbol instance (a unit of a part) with every pin's stub, label or ground laid out round its origin; a
    two-pin part stands upright, ground at its foot (else its anode, else pin 1, on top)"""

    def __init__(self, ref: str, c: dict, lib: str, sym: dict, unit: int, pn: Dict[Tuple[str, str], str],
                 pad: float = 0.0, flat: Optional[str] = None):
        self.ref, self.c, self.lib, self.unit, self.pad, self.sym, self.pn = ref, c, lib, unit, pad, sym, pn
        self.off = None
        self.pins = [p for p in sym["pins"] if p["unit"] in (0, unit)]
        self.value = str(c.get("value") or sym.get("value") or "")
        net = {p["num"]: pn.get((ref, p["num"])) for p in self.pins}
        two = len(self.pins) == 2 and abs(abs(self.pins[0]["rot"] - self.pins[1]["rot"]) - 180) < 1   # (end to end)
        self.rot = 0
        if two:
            def score(r):
                (x1, y1), (x2, y2) = (_rot(*p["at"], r) for p in self.pins)
                if flat:                                 # (lying down, the net ``flat`` on its left: in a power chain)
                    if abs(y1 - y2) > 1e-6:
                        return None
                    return (net[(self.pins[0] if x1 < x2 else self.pins[1])["num"]] == flat, r == 0)
                if abs(x1 - x2) > 1e-6:
                    return None                          # (not upright)
                top = self.pins[0] if y1 > y2 else self.pins[1]
                bot = self.pins[1] if top is self.pins[0] else self.pins[0]
                return (net[bot["num"]] == "GND", net[top["num"]] != "GND", top["name"] in ("A", "+"),
                        top["num"] == "1", r == 0)
            ok = [r for r in (0, 90, 180, 270) if score(r)]
            self.rot = max(ok, key=score) if ok else 0
        sheet = lambda x, y: (lambda q: (q[0], -q[1]))(_rot(x, y, self.rot))
        ends = [sheet(*p["at"]) for p in self.pins]
        ex = _extent(lib)
        corners = [sheet(x, y) for x, y in ((ex[0], ex[1]), (ex[2], ex[3]))]
        self.body = _union([(x, y, x, y) for x, y in corners])
        if two and not flat:
            if self.body[2] - self.body[0] < 3.2:
                cx = (self.body[0] + self.body[2]) / 2
                self.body = (cx - 1.6, self.body[1], cx + 1.6, self.body[3])
            self.body = _union([self.body] + [(x, y, x, y) for x, y in ends])
        self.items, hard, soft = [], [self.body], []
        gnd: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
        done = set()
        for p, (x, y) in zip(self.pins, ends):
            if (round(x, 3), round(y, 3)) in done:       # (stacked pins: one stub serves them all)
                continue
            done.add((round(x, 3), round(y, 3)))
            dx, dy = DIRS[int((p["rot"] + self.rot + 180) % 360)]      # (outward: away from the body)
            ln = p["length"]
            hard.append(_grow((x, y, x + dx * ln, y + dy * ln), 0.3, 0.3))
            n = net[p["num"]]
            if n is None:
                self.items.append(("nc", (x, y)))
                continue
            e = (x + dx * STUB, y + dy * STUB)
            self.items.append(("wire", (x, y), e))
            hard.append((min(x, e[0]), min(y, e[1]), max(x, e[0]), max(y, e[1])))
            if n == "GND":
                gnd.setdefault((dx, dy), []).append(e)
                continue
            w = len(n) * CH + 0.6 + pad
            if dx == 0 and not two:                      # (an IC's top / bottom row: upright, clear of its neighbours)
                self.items.append(("label", e, 90 if dy < 0 else 270, n))
                soft.append((e[0] - 1.9, e[1] - w, e[0] + 0.3, e[1]) if dy < 0 else (e[0] - 0.3, e[1], e[0] + 1.9, e[1] + w))
            else:
                self.items.append(("label", e, 180 if dx < 0 else 0, n))
                soft.append((e[0] - w, e[1] - 1.9, e[0], e[1] + 0.3) if dx < 0 else (e[0], e[1] - 1.9, e[0] + w, e[1] + 0.3))
        for (dx, dy), es in gnd.items():                 # (one ground symbol per side, the stubs joined)
            if dx and not two:                           # (a side of a box: out past the side's labels first)
                reach = max([len(it[3]) * CH + 0.6 + pad for it in self.items
                             if it[0] == "label" and it[2] == (180 if dx < 0 else 0)] + [0.0])
                ext = math.ceil((reach + 2.6) / G) * G if reach else 0.0      # (the symbol's half width clear too)
                if ext:
                    for k, q in enumerate(es):
                        q2 = (q[0] + dx * ext, q[1])
                        self.items.append(("wire", q, q2))
                        hard.append(_union([(q[0], q[1], q[0], q[1]), (q2[0], q2[1], q2[0], q2[1])]))
                        es[k] = q2
            es.sort(key=lambda q: (q[1], q[0]))
            for a, b in zip(es, es[1:]):
                self.items.append(("wire", a, b))
                hard.append(_union([(a[0], a[1], a[0], a[1]), (b[0], b[1], b[0], b[1])]))
            if dy < 0:                                   # (pins on top: the symbol points up)
                at = es[0]
                self.items.append(("gnd", at, 180))
                soft.append((at[0] - 2.2, at[1] - 5.2, at[0] + 2.2, at[1]))
            else:
                at = max(es, key=lambda q: (q[1], -q[0]))
                self.items.append(("gnd", at, 0))
                soft.append((at[0] - 2.2, at[1], at[0] + 2.2, at[1] + 5.2))
            if len(es) > 1:                              # (where three wires meet: a junction)
                for q in es[1:-1] + [at]:
                    self.items.append(("junction", q))
        # reference and value: right of an upright part, else the first free place above / below the body
        b = self.body
        tw = max(len(ref), len(self.value)) * CH + 0.6
        if flat:                                         # (lying in a chain: its text above it, clear of the wires)
            cands = [((b[0], b[1] - 3.4), (b[0], b[1] - 1.4), (b[0], b[1] - 4.4, b[0] + tw, b[1] - 0.4))]
        elif two:
            m = (b[1] + b[3]) / 2
            cands = [((b[2] + 0.6, m - 1.0), (b[2] + 0.6, m + 1.0), (b[2], m - 2.0, b[2] + 0.6 + tw, m + 2.0))]
        else:
            allb = _union(hard + soft)
            cands = [((b[0], y - 2.9), (b[0], y - 0.9), (b[0], y - 3.9, b[0] + tw, y)) for y in (b[1], allb[1])]
            cands += [((b[0], y + 1.0), (b[0], y + 3.0), (b[0], y, b[0] + tw, y + 4.0)) for y in (b[3], allb[3])]
        for ra, va, box in cands:
            if not any(_hit(box, o) for o in (hard[1:] if two and not flat else hard) + soft if o is not self.body):
                break
        self.ref_at, self.val_at, self.justify = ra, va, "left"
        soft.append(box)
        self.soft, self.hard = soft, hard
        self._rebox()
        self.at = (0.0, 0.0)
        labels = [it for it in self.items if it[0] == "label"]
        grounds = [it for it in self.items if it[0] == "gnd"]
        # an upright two-pin part between one net on top and ground at its foot: it can stand in a bank on a rail
        self.rail = (labels[0][3] if two and len(labels) == 1 and len(grounds) == 1 and grounds[0][2] == 0
                     and labels[0][1][1] < grounds[0][1][1] else None)

    def _rebox(self):
        bb = _union(self.hard + self.soft)
        self.box = (bb[0] - 1.0, bb[1] - 1.0, bb[2] + 1.0, bb[3] + 1.0)

    @property
    def w(self):
        return self.box[2] - self.box[0]

    @property
    def h(self):
        return self.box[3] - self.box[1]

    def place(self, x: float, y: float):
        """put the part's room's top-left corner at about (x, y), its origin on the grid"""
        self.at = (_snap(x - self.box[0]), _snap(y - self.box[1]))

    def room(self):
        return (self.box[0] + self.at[0], self.box[1] + self.at[1], self.box[2] + self.at[0], self.box[3] + self.at[1])


def _grow(b, gx, gy):
    return (min(b[0], b[2]) - gx, min(b[1], b[3]) - gy, max(b[0], b[2]) + gx, max(b[1], b[3]) + gy)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _hit(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def blocks(board) -> List[Tuple[str, List[str]]]:
    """the parts in functional groups, as an engineer splits a sheet: each IC with the parts on its signals (reached
    through local nets - not ground, up to six pins - without passing another IC); parts joined only to each other
    (an LED and its resistor) a group of their own; a part on power nets only joins the group it shares most pins
    with. Each group in the order its parts hang off its IC's pins, titled by its IC"""
    import pcb_view
    pn = board.pad_net()
    refs = [r for r in sorted(board.comps) if any(pn.get((r, p)) for p in board.comps[r]["pads"])]
    nets: Dict[str, List[str]] = {}
    pins_of: Dict[str, List[str]] = {}
    for (r, p), n in sorted(pn.items(), key=lambda kv: (kv[0][0], len(kv[0][1]), kv[0][1])):
        if r in refs:
            if r not in nets.setdefault(n, []):
                nets[n].append(r)
            pins_of.setdefault(r, []).append(n)
    local = lambda n: n != "GND" and len(nets[n]) <= 6
    kind = {r: pcb_view.kind_of(r, board.comps[r]["fp"]) for r in refs}
    wide = lambda r: len({p for (rr, p) in pn if rr == r})
    is_ic = lambda r: (kind[r] == "ic" or (kind[r] not in ("connector", "pad") and len(board.comps[r]["pads"]) > 4)
                       or wide(r) > 10)               # (a module on headers - a Teensy, a dev board - is the MCU)
    ics = sorted([r for r in refs if is_ic(r)], key=lambda r: -len(board.comps[r]["pads"]))
    owner = {r: r for r in ics}
    order: Dict[str, List[str]] = {r: [r] for r in ics}
    frontier = list(ics)                                 # (all ICs at once, breadth first: each part to its nearest)
    while frontier:
        nxt = []
        for r in frontier:
            for n in pins_of[r]:
                if not local(n):
                    continue
                for o in nets[n]:
                    if o not in owner and not is_ic(o):
                        owner[o] = owner[r]
                        order[owner[r]].append(o)
                        nxt.append(o)
        frontier = nxt
    rest = [r for r in refs if r not in owner]           # (the parts no IC reaches: joined among themselves)
    up = {r: r for r in rest}

    def find(r):
        while up[r] != r:
            up[r] = up[up[r]]
            r = up[r]
        return r
    for n, rs in nets.items():
        rs = [r for r in rs if r in up]
        if local(n):
            for r in rs[1:]:
                up[find(r)] = find(rs[0])
    loose: Dict[str, List[str]] = {}
    for r in rest:
        loose.setdefault(find(r), []).append(r)
    groups = dict(order)
    for k, rs in loose.items():
        if len(rs) > 1:
            groups[k] = rs
    for (r,) in [v for v in loose.values() if len(v) == 1]:
        score = {g: sum(1 for n in pins_of[r] if n != "GND" for o in nets[n] if o in v) for g, v in groups.items()}
        g = max(score, key=lambda g: (score[g], len(groups[g]))) if score and max(score.values()) else None
        if g is None:
            groups.setdefault("_other", []).append(r)
        else:
            groups[g].append(r)
    out = []
    for k, rs in groups.items():
        if k in board.comps and is_ic(k):
            title = f"{board.comps[k].get('value') or k} ({k})"
        elif k == "_other":
            title = "Other"
        else:
            own = [n for n, v in nets.items() if local(n) and set(v) <= set(rs)]
            title = own[0] if own else ", ".join(rs[:3])
        out.append((title, rs))
    out.sort(key=lambda t: -len(t[1]))
    return out


BANK = 8                                                 # (parts on one rail, at most: a longer bank wraps)


def _chain(ps):
    """a regulator's output stage as an engineer draws it: the inductor lying on the rail, the parts on its switch node
    (the catch diode) standing on its left, the output capacitors on its right, all on one wire, one label each end:
    SW --+--[L]--+---+-- VOUT. [parts] in rail order, each with its offset; [] when the block has none"""
    for L in ps:
        if L.lib != "Device:L" or not L.c.get("pads"):
            continue
        nets = [L.pn.get((L.ref, q["num"])) for q in L.pins]
        if len(nets) != 2 or None in nets or "GND" in nets:
            continue
        a, b = nets
        left = [q for q in ps if q.rail == a and q is not L]
        right = [q for q in ps if q.rail == b and q is not L]
        if not right:
            a, b, left, right = b, a, right, left
        if not right:
            continue
        flat = _Part(L.ref, L.c, L.lib, L.sym, L.unit, L.pn, L.pad, flat=a)
        fl = [it[1] for it in flat.items if it[0] == "label"]
        if len(fl) != 2 or abs(fl[0][1] - fl[1][1]) > 1e-6:
            continue                                     # (its symbol does not lie down)
        chain = left + [flat] + right
        ends = {}                                        # (each part's points on the rail, its own frame)
        for q in chain:
            ls = [it for it in q.items if it[0] == "label"]
            ends[id(q)] = (min(ls, key=lambda it: it[1][0])[1], max(ls, key=lambda it: it[1][0])[1])
        x, right_edge = 0.0, None
        for q in chain:                                  # (side by side, each part's left point on the rail at y 0)
            lp = ends[id(q)][0]
            if right_edge is not None:
                x = max(x, right_edge + 1.0 + (lp[0] - q.box[0]))
            x = math.ceil(x / G - 1e-9) * G
            q.off = (x - lp[0], -lp[1])
            right_edge = q.off[0] + q.box[2]
            x = q.off[0] + ends[id(q)][1][0]
        for k, q in enumerate(chain):
            lp, rp = ends[id(q)]
            labels = [it for it in q.items if it[0] == "label"]
            at = lambda e: next(it[3] for it in labels if it[1] == e)                   # (the net at that point)
            keep = []
            if k == 0:
                keep.append(("label", lp, 180, at(lp)))
            if k == len(chain) - 1:
                keep.append(("label", rp, 0, at(rp)))
            q.items = [it for it in q.items if it[0] != "label"] + keep
            q.soft = [s_ for s_ in q.soft if not any(_hit(s_, (e[0] - .01, e[1] - .01, e[0] + .01, e[1] + .01))
                                                     for e in (lp, rp))]
            for it in keep:
                w = len(it[3]) * CH + 0.6 + q.pad
                e = it[1]
                q.soft.append((e[0] - w, e[1] - 1.9, e[0], e[1] + 0.3) if it[2] == 180 else (e[0], e[1] - 1.9, e[0] + w, e[1] + 0.3))
            if k < len(chain) - 1:                       # (on along the rail to the next part's left point)
                nxt = chain[k + 1]
                to = (nxt.off[0] + ends[id(nxt)][0][0] - q.off[0], rp[1])
                q.items.append(("wire", rp, to))
                if q is not flat and 0 < k:              # (its stub, the wire in, the wire on)
                    q.items.append(("junction", rp))
            q._rebox()
        for q, nxt in zip(chain, chain[1:]):             # (a room reaches the next one's: the rail wire is inside)
            q.box = (q.box[0], q.box[1], max(q.box[2], nxt.off[0] - q.off[0] + nxt.box[0] - 0.01), q.box[3])
        ps[ps.index(L)] = flat
        return chain
    return []


def _banks(ps):
    """the parts in placing order, each a list: a bank of upright two-pin parts on one symbol between one rail and
    ground (decoupling capacitors on 3V3) drawn as an engineer does - side by side on one pitch, the rail a wire across
    their tops with one label, their feet joined to one ground symbol - else the part alone"""
    chain = _chain(ps)
    out, seen = ([chain] if chain else []), {id(q) for q in chain}
    for p in ps:
        if id(p) in seen:
            continue
        mates = [q for q in ps if q.rail and p.rail and q.lib == p.lib and q.rail == p.rail and id(q) not in seen]
        for k in range(0, max(len(mates), 1), BANK):
            grp = mates[k:k + BANK] if p.rail else [p]
            seen.update(id(q) for q in grp)
            if len(grp) > 1:
                _rail(grp)
            out.append(grp)
    return out


def _rail(grp):
    """join a bank's tops and feet (each part's own frame; the parts then stand ``pitch`` apart)"""
    pitch = math.ceil(max(q.w for q in grp) / G) * G
    for k, q in enumerate(grp):
        q.pitch = pitch
        top = next(it for it in q.items if it[0] == "label")
        foot = next(it for it in q.items if it[0] == "gnd")
        et, ef = top[1], foot[1]
        q.items = [it for it in q.items if it is not top and it is not foot]
        q.soft = [s_ for s_ in q.soft if not (_hit(s_, (et[0] - 0.01, et[1] - 0.01, et[0] + 0.01, et[1] + 0.01))
                                            or _hit(s_, (ef[0] - 0.01, ef[1] + 0.01, ef[0] + 0.01, ef[1] + 0.02)))]
        if k == 0:                                       # (the rail's one label, outward to the left; one ground)
            n = top[3]
            q.items += [("label", et, 180, n), ("gnd", ef, 0)]
            w = len(n) * CH + 0.6 + q.pad
            q.soft += [(et[0] - w, et[1] - 1.9, et[0], et[1] + 0.3), (ef[0] - 2.2, ef[1], ef[0] + 2.2, ef[1] + 5.2)]
        if k < len(grp) - 1:                             # (on to the next part)
            q.items += [("wire", et, (et[0] + pitch, et[1])), ("wire", ef, (ef[0] + pitch, ef[1]))]
        if 0 < k < len(grp) - 1:                         # (three wires meet)
            q.items += [("junction", et), ("junction", ef)]
        if k == 0:
            q.items.append(("junction", ef))             # (the ground symbol, the stub, the rail)
        q._rebox()
    for q, nxt in zip(grp, grp[1:]):                     # (its room reaches the next part's: the wires are inside)
        q.box = (q.box[0], q.box[1], max(q.box[2], pitch + nxt.box[0] - 0.01), q.box[3])


def _blocks(board, pad: float = 0.0):
    """[[title, extent, [_Part]]]: each functional block laid out round its own origin (``pad``: the extra width of a
    label, a global label's arrow)"""
    pn = board.pad_net()
    syms: Dict[str, dict] = {}
    out = []
    for title, refs in blocks(board):
        ps = []
        for r in refs:
            lib = symbol_for(r, board.comps[r])
            sym = board.comps[r].get("symbol") if board.comps[r].get("symbol") else syms.get(lib) or parts.symbol(lib)
            used = {p for (rr, p) in pn if rr == r}
            if not board.comps[r].get("symbol") and not used <= {q["num"] for q in sym["pins"]}:
                lib = _generic(len({p for p in board.comps[r]["pads"] if p}))     # (the generic symbol misses a pad)
                sym = syms.get(lib) or parts.symbol(lib)
                if not used <= {q["num"] for q in sym["pins"]}:                   # (pads not numbered 1..n: its own box)
                    lib = box_symbol(r, board.comps[r])
                    sym = _BOX[lib][1]
            syms[lib] = sym
            units = sorted({p["unit"] for p in sym["pins"]} - {0}) or [1]
            ps += [_Part(r, board.comps[r], lib, sym, u, pn, pad) for u in units]
        # the IC on the left, its parts in rows to its right, about as tall as it
        head = ps[0] if ps and len(ps[0].pins) > 3 else None
        rest = ps[1:] if head else ps
        area = sum(p.w * p.h for p in rest)
        width = max([p.w for p in rest] + [((area / max(head.h, 40.0)) if head else (area * 1.6) ** 0.5) * 1.15])
        x0 = head.w + 4.0 if head else 0.0
        x, y, bottom = x0, 0.0, 0.0
        for grp in _banks(rest):                         # (rows by each part's room as snapped to the grid)
            p = grp[0]
            if p.off is not None:                        # (a power chain: placed whole, by its own box)
                l_ = min(q.off[0] + q.box[0] for q in grp)
                t_ = min(q.off[1] + q.box[1] for q in grp)
                gw = max(q.off[0] + q.box[2] for q in grp) - l_
                if x > x0 and x + gw > x0 + width:
                    x, y = x0, bottom + 1.0
                ox, oy = math.ceil((x - l_) / G - 1e-9) * G, math.ceil((y - t_) / G - 1e-9) * G
                for q in grp:
                    q.at = (round(ox + q.off[0], 4), round(oy + q.off[1], 4))    # (the origin on the grid, the
                                                                                     # parts on their exact offsets)
                x, bottom = max(q.room()[2] for q in grp) + 1.0, max([bottom] + [q.room()[3] for q in grp])
                continue
            gw = p.w + sum(q.pitch for q in grp[:-1]) if len(grp) > 1 else p.w
            if x > x0 and x + gw > x0 + width:
                x, y = x0, bottom + 1.0
            p.place(x, y)
            if p.room()[0] < x or p.room()[1] < y:       # (the snap went back: one grid step on)
                p.at = (p.at[0] + (G if p.room()[0] < x else 0), p.at[1] + (G if p.room()[1] < y else 0))
            for k, q in enumerate(grp[1:], 1):           # (a bank: on its pitch, the rail wires meeting)
                q.at = (p.at[0] + k * p.pitch, p.at[1])
            x, bottom = grp[-1].room()[2] + 1.0, max([bottom] + [q.room()[3] for q in grp])
        if head:
            head.place(0.0, 0.0)
        ps = ([head] if head else []) + rest              # (a chain's inductor is its lying-down twin now)
        out.append([title, _union([p.room() for p in ps]), ps])
    return out


START = (MARGIN, MARGIN + 6.0, 0.0)                      # (where the first block goes: x, y, the row's height)
GPAD = 2.6                                               # (a global label's arrow, beside a local label's width)


def _title_room(pw: float, ph: float):
    return (pw - MARGIN - TITLE_BLOCK[0], ph - MARGIN - TITLE_BLOCK[1], pw, ph)


def _size(blk):
    return blk[1][2] - blk[1][0] + 2 * G, blk[1][3] - blk[1][1] + 2 * G + 5.0


def _shelf(st, bw, bh, pw, ph, tb):
    """the next place on a sheet filled in rows for a block bw x bh: (frame, the sheet's state after) or (None, st)"""
    x, y, row = st
    for _ in range(2):
        if x > MARGIN and x + bw > pw - MARGIN:
            x, y, row = MARGIN, y + row + 6.0, 0.0
        frame = (x, y, x + bw, y + bh)
        if _hit(frame, tb) and x > MARGIN:
            x, y, row = MARGIN, y + row + 6.0, 0.0
            continue
        break
    frame = (x, y, x + bw, y + bh)
    if frame[2] > pw - MARGIN or frame[3] > ph - MARGIN or _hit(frame, tb):
        return None, st
    return frame, (x + bw + 6.0, y, max(row, bh))


def _settle(blk, frame):
    dx, dy = _snap(frame[0] + G - blk[1][0]), _snap(frame[1] + G + 5.0 - blk[1][1])   # (one shift for the block:
    for p in blk[2]:                                                                     # its parts keep their places)
        p.at = (round(p.at[0] + dx, 4), round(p.at[1] + dy, 4))
    blk[1] = frame


def layout(board, pad: float = 0.0):
    """[(title, frame, [_Part])], the paper and the title block's room: every block on the smallest sheet that holds
    them, in rows clear of the title block"""
    out = _blocks(board, pad)
    for name, pw, ph in PAPER:
        tb, st, placed = _title_room(pw, ph), START, []
        for blk in out:
            f, st = _shelf(st, *_size(blk), pw, ph, tb)
            if f is None:
                break
            placed.append(f)
        else:
            break
    for blk, f in zip(out, placed):
        _settle(blk, f)
    return out, (name, pw, ph), tb


def pages(board) -> list:
    """the sheets as an engineer splits a large design: one sheet when it fits A3, else A3 pages filled block by block
    (first fit; a block too big for A3 gets the smallest sheet that holds it), their nets joined by global labels.
    [layout] as ``layout`` returns it, one per page"""
    one = layout(board)
    if one[1][0] in ("A4", "A3"):
        return [one]
    name, pw, ph = PAPER[1]
    tb = _title_room(pw, ph)
    pgs = []                                             # ([blocks], paper, title room, row state)
    for blk in _blocks(board, GPAD):
        bw, bh = _size(blk)
        for pg in pgs:
            if pg[1][0] == name:
                f, st = _shelf(pg[3], bw, bh, pw, ph, tb)
                if f:
                    pg[3] = st
                    break
        else:
            for paper in PAPER[1:]:
                t_ = _title_room(*paper[1:])
                f, st = _shelf(START, bw, bh, paper[1], paper[2], t_)
                if f:
                    break
            pg = [[], paper, t_, st]
            pgs.append(pg)
        _settle(blk, f)
        pg[0].append(blk)
    return [(pg[0], pg[1], pg[2]) for pg in pgs]


def overlaps(lay) -> List[str]:
    """every pair of parts whose rooms overlap, and every block that runs into another or the title block"""
    blks, _, tb = lay
    out = []
    for _, _, ps in blks:                                # (inside a part: its labels, grounds and text apart)
        for p in ps:
            for k, a in enumerate(p.soft):
                for b in p.soft[k + 1:]:
                    if _hit(a, b):
                        out.append(f"{p.ref}: its labels or text overlap on the schematic")
    rooms = [(p.ref, p.room()) for _, _, ps in blks for p in ps]
    for i, (a, ra) in enumerate(rooms):
        for b, rb in rooms[i + 1:]:
            if _hit(ra, rb):
                out.append(f"{a} and {b} overlap on the schematic")
    for i, (ta, fa, _) in enumerate(blks):
        if _hit(fa, tb):
            out.append(f"block {ta} runs into the title block")
        for tb_, fb, _ in blks[i + 1:]:
            if _hit(fa, fb):
                out.append(f"blocks {ta} and {tb_} overlap")
    return out


# ---- the file -----------------------------------------------------------------------------------------------------
def _font(size=1.27, bold=False):
    f = ["font", ["size", size, size]]
    return f + [["thickness", round(size * 0.2, 3)], ["bold", "yes"]] if bold else f


def write(board, out_dir: str, title: Optional[str] = None, rev: str = "A", company: str = "") -> str:
    """``<out_dir>/<name>.kicad_sch`` (KiCad 9 format); returns its path. A design larger than A3 is a cover sheet
    (the title block, a box per page) over A3 pages ``<name>_<n>.kicad_sch``, its nets in global labels. Raises
    ValueError if a page's layout overlaps"""
    pgs = pages(board)
    for lay in pgs:
        bad = overlaps(lay)
        if bad:
            raise ValueError("; ".join(bad))
    multi = len(pgs) > 1
    root = _uuid("sch", board.name)
    n_pwr = [0]
    os.makedirs(out_dir, exist_ok=True)

    def head(paper, uid, blks, comment):
        libs = sorted({p.lib for _, _, ps in blks for p in ps} | ({"power:GND"} if "GND" in board.nets and blks else set()))
        return ["kicad_sch", ["version", 20250114], ["generator", _Q("eeschema")], ["generator_version", _Q("9.0")],
                ["uuid", uid], ["paper", _Q(paper)],
                ["title_block", ["title", _Q(title or board.name)], ["date", _Q(datetime.date.today().isoformat())],
                 ["rev", _Q(rev)], ["company", _Q(company)], ["comment", 1, _Q(comment)]],
                ["lib_symbols"] + [_lib_symbol(n) for n in libs]]

    def body(sch, blks, path):
        def inst(lib, at, rot, ref, value, uid, unit=1, props=(), pins=(), ref_at=None, val_at=None, hide_ref=False,
                 justify=None):
            fa = (-rot) % 360                          # (a field turns with its symbol: turned back, it reads level)
            if rot == 180:                               # (turned half round, KiCad reads a field at 0 level and
                fa = 0                                   # mirrors its justification: asked mirrored, it lands as meant)
                justify = {"left": "right", "right": "left"}.get(justify, justify)
            eff = lambda hide=False: ["effects", _font()] + ([["justify", justify]] if justify else []) + (
                [["hide", "yes"]] if hide else [])
            node = ["symbol", ["lib_id", _Q(lib)], ["at", at[0], at[1], rot], ["unit", unit],
                    ["exclude_from_sim", "no"], ["in_bom", "no" if hide_ref else "yes"],
                    ["on_board", "no" if hide_ref else "yes"], ["dnp", "no"], ["uuid", uid],
                    ["property", _Q("Reference"), _Q(ref), ["at", *(ref_at or at), fa], eff(hide_ref)],
                    ["property", _Q("Value"), _Q(value), ["at", *(val_at or at), fa], eff()]]
            for k, v in props:
                node.append(["property", _Q(k), _Q(v), ["at", at[0], at[1], 0], eff(True)])
            node += [["pin", _Q(n), ["uuid", _uuid("pin", uid, n)]] for n in pins]
            node.append(["instances", ["project", _Q(board.name), ["path", _Q(path), ["reference", _Q(ref)],
                                                                     ["unit", unit]]]])
            return node

        for tname, frame, ps in blks:
            ti = tname
            sch.append(["rectangle", ["start", frame[0], frame[1]], ["end", frame[2], frame[3]],
                        ["stroke", ["width", 0.254], ["type", "dash"]], ["fill", ["type", "none"]],
                        ["uuid", _uuid("blk", ti)]])
            sch.append(["text", _Q(tname), ["exclude_from_sim", "no"], ["at", frame[0] + G, frame[1] + 4.2, 0],
                        ["effects", _font(2.0, True), ["justify", "left", "bottom"]], ["uuid", _uuid("blkt", ti)]])
            for p in ps:
                X, Y = p.at
                c = p.c
                props = [("Footprint", c["fp"]), ("Datasheet", (c.get("symbol") or {}).get("datasheet") or "~")]
                props += [(k, str(c[f])) for k, f in (("MPN", "mpn"), ("LCSC", "lcsc"), ("Note", "note")) if c.get(f)]
                uid = _uuid("sym", p.ref, p.unit)
                sch.append(inst(p.lib, (X, Y), p.rot, p.ref, p.value, uid, p.unit, props, [q["num"] for q in p.pins],
                                (X + p.ref_at[0], Y + p.ref_at[1]), (X + p.val_at[0], Y + p.val_at[1]), justify=p.justify))
                for it in p.items:
                    if it[0] == "nc":
                        sch.append(["no_connect", ["at", X + it[1][0], Y + it[1][1]], ["uuid", _uuid("nc", p.ref, it[1])]])
                    elif it[0] == "wire":
                        (a, b) = it[1], it[2]
                        sch.append(["wire", ["pts", ["xy", X + a[0], Y + a[1]], ["xy", X + b[0], Y + b[1]]],
                                    ["stroke", ["width", 0.1524], ["type", "default"]], ["uuid", _uuid("w", p.ref, a, b)]])
                    elif it[0] == "junction":
                        q = it[1]
                        sch.append(["junction", ["at", X + q[0], Y + q[1]], ["diameter", 0], ["color", 0, 0, 0, 0],
                                    ["uuid", _uuid("j", p.ref, q)]])
                    elif it[0] == "gnd":
                        n_pwr[0] += 1
                        e, rot = it[1], it[2]
                        off = {0: (0, 3.81), 90: (3.81, 0), 180: (0, -3.81), 270: (-3.81, 0)}[rot]
                        sch.append(inst("power:GND", (X + e[0], Y + e[1]), rot, f"#PWR{n_pwr[0]:03d}", "GND",
                                        _uuid("pwr", p.ref, e), pins=["1"], hide_ref=True,
                                        val_at=(X + e[0] + off[0], Y + e[1] + off[1])))
                    elif multi:                          # (a net on several pages: one global label name joins them)
                        e, rot, net = it[1], it[2], it[3]
                        at = ["at", X + e[0], Y + e[1], rot]
                        sch.append(["global_label", _Q(net), ["shape", "bidirectional"], at, ["fields_autoplaced", "yes"],
                                    ["effects", _font(), ["justify", "right" if rot in (180, 270) else "left"]],
                                    ["uuid", _uuid("glbl", p.ref, e)],
                                    ["property", _Q("Intersheetrefs"), _Q("${INTERSHEET_REFS}"), at[:3] + [0],
                                     ["effects", _font(), ["hide", "yes"]]]])
                    else:
                        e, rot, net = it[1], it[2], it[3]
                        just = ["justify", "right" if rot in (180, 270) else "left", "bottom"]
                        sch.append(["label", _Q(net), ["at", X + e[0], Y + e[1], rot], ["fields_autoplaced", "yes"],
                                    ["effects", _font(), just], ["uuid", _uuid("lbl", p.ref, e)]])

    path = os.path.join(out_dir, f"{board.name}.kicad_sch")
    if not multi:
        blks, (paper, _, _), _ = pgs[0]
        sch = head(paper, root, blks, "generated from the layout netlist")
        body(sch, blks, "/" + root)
    else:                                                # (the cover: the title block, a box per page)
        sch = head("A4", root, [], f"generated from the layout netlist: {len(pgs)} pages")
        for i, (blks, (paper, _, _), _) in enumerate(pgs):
            sid, fname = _uuid("sheet", board.name, i), f"{board.name}_{i + 2}.kicad_sch"
            name = f"{i + 2} {blks[0][0]}"                    # (the page and its first block: also its SVG's name)
            x, y = 20.0 + (i % 2) * 135.0, 35.0 + (i // 2) * 40.0
            sch.append(["sheet", ["at", x, y], ["size", 120.0, 25.4], ["exclude_from_sim", "no"], ["in_bom", "yes"],
                        ["on_board", "yes"], ["dnp", "no"], ["fields_autoplaced", "yes"],
                        ["stroke", ["width", 0.1524], ["type", "solid"]], ["fill", ["color", 0, 0, 0, 0.0]],
                        ["uuid", sid],
                        ["property", _Q("Sheetname"), _Q(name), ["at", x, y - 0.7, 0],
                         ["effects", _font(), ["justify", "left", "bottom"]]],
                        ["property", _Q("Sheetfile"), _Q(fname), ["at", x, y + 26.0, 0],
                         ["effects", _font(), ["justify", "left", "top"]]],
                        ["instances", ["project", _Q(board.name), ["path", _Q("/" + root), ["page", _Q(str(i + 2))]]]]])
            sub = head(paper, _uuid("sheetfile", board.name, i), blks, f"page {i + 2} of {len(pgs) + 1}")
            body(sub, blks, f"/{root}/{sid}")
            sub.append(["embedded_fonts", "no"])
            with open(os.path.join(out_dir, fname), "w") as f:
                f.write(_sx_dump(sub) + "\n")
    sch += [["sheet_instances", ["path", _Q("/"), ["page", _Q("1")]]], ["embedded_fonts", "no"]]
    with open(path, "w") as f:
        f.write(_sx_dump(sch) + "\n")
    return path


# ---- KiCad's view of it -------------------------------------------------------------------------------------------
def netlist(path: str) -> Dict[str, set]:
    """KiCad's netlist of the sheet (``kicad-cli sch export netlist``): {net: {(ref, pin)}}"""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "n.net")
        r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "-o", out, path], capture_output=True, text=True,
                           timeout=300)
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError("kicad-cli could not read the schematic: " + (r.stderr or r.stdout)[-400:])
        tree = pcb._sx_parse(open(out).read())
    nets = {}
    for n in pcb._find(next(iter(pcb._find(tree, "nets")), []), "net"):
        name = next((x[1] for x in n if isinstance(x, list) and x[0] == "name"), "")
        nodes = {(x[1][1], x[2][1]) for x in pcb._find(n, "node")}
        nets[name.lstrip("/")] = nodes
    return nets


def check(board, path: str) -> List[str]:
    """[] when every net of the board is one net on the sheet, with the same pins; else what differs"""
    pn = board.pad_net()
    want: Dict[str, set] = {}
    for (r, p), n in pn.items():
        want.setdefault(n, set()).add((r, p))
    got = {k: v for k, v in netlist(path).items() if v}
    where = {pin: n for n, v in got.items() for pin in v}
    out = []
    for n, pins in sorted(want.items()):
        seen = {where.get(p) for p in pins}
        if seen != {n} and len(seen) == 1 and None not in seen:
            continue                                     # (one net, under another name: KiCad's choice of label)
        if None in seen:
            out.append(f"{n}: pins {sorted(p for p in pins if p not in where)} are not on the schematic")
        elif len(seen) > 1:
            out.append(f"{n}: split on the schematic into {sorted(s for s in seen if s)}")
        elif got[seen.pop()] != pins:
            out.append(f"{n}: the schematic joins it to other pins")
    return out


if __name__ == "__main__":                               # a board script -> its schematic, checked
    import sys
    import pcb_cli
    b, _ = pcb_cli.load(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    path = write(b, out)
    print(path)
    print("\n".join(check(b, path)) or "the schematic's netlist is the board's")
