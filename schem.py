"""Conventional schematics for the boards designed with pcb.Board, drawn by hand with schemdraw (standard symbols,
wires, junction dots, ground and power-port symbols) and checked against the board's netlist, so the sheet cannot
disagree with the layout.

    from schem import Sheet
    s = Sheet(board, unit=2.5)
    r = s.add("R1", elm.Resistor().at((0, 0)).down(), {"1": "start", "2": "end"}, label="22k")
    s.wire((0, 0), (4, 0))                         # a wire; ends and T-junctions join what they touch
    s.gnd(r.end)                                   # ground symbol = net "GND"; s.port(xy, "VBAT") a named power port
    print(s.check())                               # [] when every drawn connection matches the board's nets
    s.save("schematic.svg"); s.save("schematic.png")

Connectivity is by geometry, as on paper: a pin joins every wire end, wire, pin or symbol at its point, and a wire
end landing on another wire joins it (draw the junction dot with ``s.dot``)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import schemdraw
import schemdraw.elements as elm


EPS = 1e-3


def _on(p, a, b) -> bool:
    """``p`` strictly inside segment a-b"""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) > EPS * max(1.0, abs(bx - ax) + abs(by - ay)):
        return False
    t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / (((bx - ax) ** 2 + (by - ay) ** 2) or 1.0)
    return EPS < t < 1 - EPS


def _k(p) -> Tuple[float, float]:
    return (round(float(p[0]), 3), round(float(p[1]), 3))


class Sheet:
    def __init__(self, board, unit: float = 2.5, fontsize: float = 9):
        self.board = board
        self.d = schemdraw.Drawing(show=False, canvas="svg")   # (the SVG backend even where matplotlib is installed:
        #  the layout checks read this SVG)
        self.d.config(unit=unit, fontsize=fontsize, lw=1.1)
        self.pins: Dict[Tuple[str, str], Tuple[float, float]] = {}      # (ref, pin) -> point
        self.wires: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        self.ports: List[Tuple[Tuple[float, float], str]] = []          # (point, net) from ground / power symbols
        self.refs: List[str] = []

    # drawing
    def add(self, ref: str, e, pins: Dict[str, str], label: Optional[str] = None, loc: str = "top"):
        """place element ``e`` (already positioned) as part ``ref``; ``pins`` maps the board's pad numbers to the
        element's anchors"""
        if label is not None:                            # (before it is drawn: schemdraw draws on adding)
            e.label(label, loc=loc)
        self.d += e
        for num, anchor in pins.items():
            self.pins[(ref, str(num))] = _k(e.absanchors[anchor])
        self.refs.append(ref)
        self._elems = getattr(self, "_elems", {})
        self._elems.setdefault(ref, []).append(e)
        return e

    def wire(self, *pts, dot_ends: bool = False):
        """a wire through the points (anchors or xy)"""
        pts = [_k(p) for p in pts]
        for a, b in zip(pts, pts[1:]):
            if a != b:
                self.d += elm.Line().at(a).to(b)
                self.wires.append((a, b))
        return pts[-1]

    def dot(self, p):
        """(kept for the drawing code: the dots themselves are placed by ``_finish``, at every junction)"""

    def _finish(self):
        """junction dots wherever three or more wires / pins / symbols meet, and nowhere else (drawn once, before
        the sheet is rendered)"""
        drawn = getattr(self, "dots", [])
        for p in sorted({q for w in self.wires for q in w}):
            if p not in drawn and self._branches(p) >= 3:
                self.d += elm.Dot().at(p)
                drawn.append(p)
        self.dots = drawn

    def gnd(self, p, lead: float = 0.0):
        p = _k(p)
        if lead:
            q = (p[0], p[1] - lead)
            self.wire(p, q)
            p = q
        self.d += elm.Ground().at(p)
        self.ports.append((p, "GND"))
        return p

    def port(self, p, net: str, up: bool = True, lead: float = 0.0):
        """a named power port (the rail symbol with its net name): joins every port of the same name"""
        p = _k(p)
        if lead:
            q = (p[0], p[1] + (lead if up else -lead))
            self.wire(p, q)
            p = q
        self.d += (elm.Vdd() if up else elm.Vss()).at(p).label(net)
        self.ports.append((p, net))
        return p

    def flag(self, p, net: str, side: str = "right"):
        """a net label: a short stub ending in the net's name (joins every flag / port of that name)"""
        p = _k(p)
        q = (p[0] + (0.5 if side == "right" else -0.5), p[1])
        self.wire(p, q)
        self.d += elm.Label().at(q).label(net, loc="right" if side == "right" else "left", fontsize=8,
                                          halign="left" if side == "right" else "right", valign="center")
        self.ports.append((q, net))
        return q

    def _poly(self, pts, close=True, lw=None):
        """drawing lines that are not wires (symbol outlines, the frame)"""
        pts = [_k(p) for p in pts] + ([_k(pts[0])] if close else [])
        for a, b in zip(pts, pts[1:]):
            e = elm.Line().at(a).to(b)
            if lw:
                e.linewidth(lw)
            self.d += e

    def offpage(self, p, net: str, side: str = "right", note: str = ""):
        """an off-sheet connector: the arrow-ended tag with the net's name (joins the same name on any page),
        ``note`` the sheets it goes to"""
        p = _k(p)
        s = 1 if side == "right" else -1
        w = 0.32 * len(net) + 0.5
        x0, y0 = p
        self._poly([(x0, y0), (x0 + s * 0.3, y0 + 0.3), (x0 + s * (w + 0.3), y0 + 0.3),
                    (x0 + s * (w + 0.6), y0), (x0 + s * (w + 0.3), y0 - 0.3), (x0 + s * 0.3, y0 - 0.3)])
        self.d += elm.Label().at((x0 + s * (w / 2 + 0.3), y0)).label(net, fontsize=8, halign="center", valign="center")
        if note:
            self.d += elm.Label().at((x0 + s * (w + 0.8), y0)).label(note, fontsize=7,
                                                                    halign="left" if s > 0 else "right", valign="center")
        self.ports.append((p, net))
        return p

    def frame(self, x0, y0, x1, y1, title: str, sheet: str, board_name: str, rev: str = "A", date: str = "",
              note: str = "", size: str = "A3"):
        """the drawing frame and the title block (bottom right), as on any engineering sheet"""
        self._poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], lw=1.6)
        self._poly([(x0 + 0.4, y0 + 0.4), (x1 - 0.4, y0 + 0.4), (x1 - 0.4, y1 - 0.4), (x0 + 0.4, y1 - 0.4)])
        bx0, by1 = x1 - 0.4 - 17.0, y0 + 0.4 + 4.2
        self._frame, self._tblock = (x0, y0, x1, y1), (bx0, y0 + 0.4, x1 - 0.4, by1)
        self._frame_text = {title, board_name, note, f"SHEET  {sheet}", f"REV  {rev}", f"DATE  {date}", f"SIZE  {size}"}
        self._poly([(bx0, y0 + 0.4), (x1 - 0.4, y0 + 0.4), (x1 - 0.4, by1), (bx0, by1)])
        for yy in (by1 - 1.6, by1 - 2.6, by1 - 3.4):
            self._poly([(bx0, yy), (x1 - 0.4, yy)], close=False)
        self._poly([(bx0 + 8.5, by1 - 2.6), (bx0 + 8.5, y0 + 0.4)], close=False)
        lab = lambda xy, t, size, **kw: self.d.add(elm.Label().at(xy).label(t, fontsize=size, halign="left", **kw))
        lab((bx0 + 0.3, by1 - 0.55), title, 10)
        lab((bx0 + 0.3, by1 - 1.15), board_name, 7)
        lab((bx0 + 0.3, by1 - 2.1), note, 7)
        lab((bx0 + 0.3, by1 - 3.0), f"SHEET  {sheet}", 8)
        lab((bx0 + 8.8, by1 - 3.0), f"REV  {rev}", 8)
        lab((bx0 + 0.3, y0 + 0.75), f"DATE  {date}", 7)
        lab((bx0 + 8.8, y0 + 0.75), f"SIZE  {size}", 7)

    def netname(self, p, net: str, dx: float = 0.3, dy: float = -0.5):
        """name the net at point ``p`` (on a wire or pin) with a small label, as a schematic names a node"""
        p = _k(p)
        self.d += elm.Label().at((p[0] + dx, p[1] + dy)).label(net, fontsize=7, halign="left")
        self.ports.append((p, net))
        self._names = getattr(self, "_names", set()) | {(p, net)}      # (a name on a node: not a branch)

    def text(self, p, s: str, size: float = 9, **kw):
        self.d += elm.Label().at(_k(p)).label(s, fontsize=size, **kw)

    def save(self, path: str, scale: float = 4.0):
        """SVG as drawn; PNG through cairosvg (white background)"""
        if not path.endswith(".png"):
            self._finish()
            self.d.save(path)
            return
        import cairosvg
        self._finish()
        svg = self.d.get_imagedata("svg")
        cairosvg.svg2png(bytestring=svg if isinstance(svg, bytes) else svg.encode(), write_to=path, scale=scale,
                         background_color="white")

    # layout: what a reader sees
    def _render(self, char_w: float = 0.58):
        """the sheet as drawn: its SVG's text lines (a box each: font size, ``char_w`` em per character) and line
        segments, with the SVG <-> sheet-unit mapping taken off the frame's outer rectangle"""
        import re
        from shapely.geometry import box
        if not hasattr(self, "_frame"):
            return None
        self._finish()
        svg = self.d.get_imagedata("svg")
        svg = svg.decode() if isinstance(svg, bytes) else svg
        num = lambda v: float(v)
        segs, boxes, outer = [], [], []
        for m in re.finditer(r'<path d="([^"]+)"[^>]*?stroke-width:([0-9.]+)', svg):
            pts = [(num(a), num(b)) for a, b in re.findall(r"(-?[0-9.]+),\s*(-?[0-9.]+)", m.group(1))]
            segs += [(a, b) for a, b in zip(pts, pts[1:]) if a != b]
            if abs(num(m.group(2)) - 1.6) < 1e-6:        # (the frame's outer rectangle, drawn 1.6 wide)
                outer += pts
        for m in re.finditer(r'<polygon points="([^"]+)"', svg):
            pts = [(num(a), num(b)) for a, b in re.findall(r"(-?[0-9.]+),(-?[0-9.]+)", m.group(1))]
            segs += [(a, b) for a, b in zip(pts, pts[1:] + pts[:1]) if a != b]
        for m in re.finditer(r'<text x="([^"]+)" y="([^"]+)"[^>]*font-size="([0-9.]+)"[^>]*text-anchor="(\w+)">(.*?)</text>',
                             svg, re.S):
            x, y, fs, anc = num(m.group(1)), num(m.group(2)), num(m.group(3)), m.group(4)
            dy = 0.0
            for dyv, t in re.findall(r'<tspan[^>]*dy="([-0-9.]+)"[^>]*>([^<]*)</tspan>', m.group(5)):
                dy += num(dyv)
                t = t.strip()
                if not t:
                    continue
                w = fs * sum(0.66 if c.isupper() or c.isdigit() else 0.52 if c.islower() else 0.3
                             for c in t) * char_w / 0.58         # (sans: capitals and digits wider)
                x0_ = x if anc == "start" else x - w / 2 if anc == "middle" else x - w
                cy = y + dy                               # (as rendered: dominant-baseline central, one dy down)
                boxes.append((t, box(x0_, cy - fs * 0.38, x0_ + w, cy + fs * 0.38), fs))   # (cap height)
        fx0, fy0, fx1, fy1 = self._frame
        xs = [p[0] for p in outer] or [p[0] for sg in segs for p in sg]
        ys = [p[1] for p in outer] or [p[1] for sg in segs for p in sg]
        k = (max(xs) - min(xs)) / (fx1 - fx0)            # (sheet units -> SVG points, off the outer frame)
        sx0, sy0 = min(xs), max(ys)
        sheet_box = lambda g: box(fx0 + (g.bounds[0] - sx0) / k, fy0 + (sy0 - g.bounds[3]) / k,
                                  fx0 + (g.bounds[2] - sx0) / k, fy0 + (sy0 - g.bounds[1]) / k)
        return {"segs": segs, "boxes": boxes, "outer": outer, "k": k, "sx0": sx0, "sy0": sy0,
                "texts_sheet": [(t, sheet_box(g)) for t, g, _ in boxes]}

    def layout_problems(self, char_w: float = 0.58) -> List[str]:
        """text that overlaps other text, text that a wire or symbol line runs through, and anything drawn in the
        title block or off the sheet, read off the rendered sheet. Positions are reported in sheet units"""
        from shapely.geometry import LineString, box
        from shapely.strtree import STRtree
        r = self._render(char_w)
        if r is None:
            return []
        segs, boxes, outer, k, sx0, sy0 = r["segs"], r["boxes"], r["outer"], r["k"], r["sx0"], r["sy0"]
        fx0, fy0, fx1, fy1 = self._frame
        to_sheet = lambda X, Y: (round(fx0 + (X - sx0) / k, 1), round(fy0 + (sy0 - Y) / k, 1))
        tb = self._tblock
        tbox = box(sx0 + (tb[0] - fx0) * k, sy0 - (tb[3] - fy0) * k, sx0 + (tb[2] - fx0) * k, sy0 - (tb[1] - fy0) * k)
        probs = []
        # text on text
        st = STRtree([g for _, g, _ in boxes])
        for i, (t, g, _) in enumerate(boxes):
            for j in st.query(g):
                j = int(j)
                if j <= i:
                    continue
                t2, g2, _ = boxes[j]
                ov = g.intersection(g2).area
                if ov > 0.02 * min(g.area, g2.area):
                    c = g.intersection(g2).centroid
                    probs.append(f"text {t!r} overlaps {t2!r} at {to_sheet(c.x, c.y)}")
        # lines through text (a box shrunk a little: a pin number may sit on its pin line)
        lines = [LineString(sg) for sg in segs]
        lt = STRtree(lines)
        for t, g, fs in boxes:                           # (through its middle: a pin number sits on its pin line)
            if t in getattr(self, "_frame_text", set()):
                continue
            gx0, gy0, gx1, gy1 = g.bounds
            cy_ = (gy0 + gy1) / 2
            core = box(gx0 + (gx1 - gx0) * 0.1, cy_ - fs * 0.16, gx1 - (gx1 - gx0) * 0.1, cy_ + fs * 0.16)
            if any(lines[int(j)].intersects(core) for j in lt.query(core)):
                c = g.centroid
                probs.append(f"a line runs through text {t!r} at {to_sheet(c.x, c.y)}")
        # everything inside the frame's inner border
        fr = box(sx0 + 0.4 * k, sy0 - (fy1 - fy0 - 0.4) * k, sx0 + (fx1 - fx0 - 0.4) * k, sy0 - 0.4 * k).buffer(1.0)
        for t, g, _ in boxes:
            if not fr.contains(g):
                probs.append(f"text {t!r} runs off the sheet at {to_sheet(g.centroid.x, g.centroid.y)}")
        for sg in segs:
            if not fr.contains(LineString(sg)) and not (outer and set(sg) <= set(outer)):
                probs.append(f"a line runs off the sheet at {to_sheet(*LineString(sg).centroid.coords[0])}")
                break
        # the title block holds the title block only
        inner = tbox.buffer(-2.0)
        for t, g, _ in boxes:
            if t not in getattr(self, "_frame_text", set()) and g.intersects(inner):
                probs.append(f"text {t!r} is in the title block")
        tw = tbox.bounds[2] - tbox.bounds[0]
        for sg in segs:                                  # (its own rules: full-width rows, the column divider)
            L = LineString(sg)
            if not L.intersects(inner):
                continue
            (ax, ay), (bx, by) = sg
            row = abs(ay - by) < 0.5 and abs(ax - bx) > 0.45 * tw
            col = abs(ax - bx) < 0.5 and abs(ax - (tbox.bounds[0] + 8.5 * k)) < 1.0
            if not (row or col):
                probs.append(f"a line is drawn in the title block at {to_sheet(*L.centroid.coords[0])}")
        return list(dict.fromkeys(probs))

    def label_problems(self, near: float = 1.2) -> List[str]:
        """every part named beside its own symbol, as an engineer reads a sheet: a text holding its reference
        within ``near`` of the symbol, and for a passive (R, C, L, FB, F) its value in that text or right beside
        it, not in a shared caption across the sheet. Range references ("RP1-6") count for each part"""
        import re
        from shapely.geometry import box
        r = self._render()
        if r is None:
            return []
        texts = r["texts_sheet"]                        # [(text, sheet-unit box)]

        def refs_in(t):
            out = set()
            for tok in re.split(r"[\s/,()]+", t):
                m = re.fullmatch(r"([A-Z]+)(\d+)-(?:[A-Z]+)?(\d+)", tok)       # ("RP1-6", "C20-C26")
                if m:
                    out |= {f"{m.group(1)}{k}" for k in range(int(m.group(2)), int(m.group(3)) + 1)}
                elif re.fullmatch(r"[A-Z]+\d+", tok) or tok in known:       # (or any placed ref: "JMX", "DXP1")
                    out.add(tok)
            return out
        known = set(self.refs)
        val = lambda r_: (self.board.comps.get(r_) or {}).get("value")
        # (a caption naming several parts holds only when they are alike: "C4/C5 47u", not "C37 1u  C38 10u")
        holders = [(t, g, rs) for t, g in texts for rs in [refs_in(t) & set(self.refs)]
                   if len({val(r_) for r_ in rs}) <= 1]
        probs = []
        for ref in dict.fromkeys(self.refs):
            c = self.board.comps.get(ref)
            bbs = [e.get_bbox(transform=True) for e in self._elems.get(ref, [])]
            if not bbs:
                continue
            shapes = [box(bb.xmin, bb.ymin, bb.xmax, bb.ymax) for bb in bbs]
            mine = [(t, g) for t, g, rs in holders if ref in rs and min(sh.distance(g) for sh in shapes) <= near]
            if not mine:
                probs.append(f"{ref} has no label beside it")
                continue
            passive = re.match(r"(R|C|L|FB|F)\d", ref) and c and c.get("value")
            if passive:
                v = c["value"].split()[0]
                ok = any(v in re.split(r"[\s/,@]+", t) for t, _ in mine) or \
                    any(v in re.split(r"[\s/,@]+", t2) and g.distance(g2) <= 0.6 for _, g in mine for t2, g2 in texts)
                if not ok:
                    probs.append(f"{ref}'s value {c['value']} is not beside it")
        return probs

    def autoname(self, size: float = 6, near: float = 1.2):
        """name every part ``label_problems`` finds unnamed: its reference and value on two lines beside its
        symbol, on the first side (right, left, above, below, then the corners) clear of wires, symbols and other
        text. Call it once the sheet is otherwise drawn"""
        import re
        from shapely.geometry import LineString, box
        from shapely.ops import unary_union
        r = self._render()
        if r is None:
            return
        k, sx0, sy0 = r["k"], r["sx0"], r["sy0"]
        fx0, fy0, _, _ = self._frame
        to_sheet = lambda X, Y: (fx0 + (X - sx0) / k, fy0 + (sy0 - Y) / k)
        obst = [LineString([to_sheet(*a), to_sheet(*b)]).buffer(0.06) for a, b in r["segs"]] + \
               [g.buffer(0.06) for _, g in r["texts_sheet"]]
        if hasattr(self, "_tblock"):
            obst.append(box(*self._tblock))
        todo = []
        for p in self.label_problems(near):
            m = re.match(r"(\S+?)(?:'s value .*| has no label beside it)$", p)
            if m and m.group(1) not in todo:
                todo.append(m.group(1))
        ch, lh = size * 0.62 / k, size * 1.25 / k        # (one character's width, one line's height, sheet units)
        for ref in todo:
            v = (self.board.comps.get(ref) or {}).get("value") or ""
            lines = [ref] + ([v] if v else [])
            w, h = max(len(t) for t in lines) * ch, len(lines) * lh
            bb = [e.get_bbox(transform=True) for e in self._elems[ref]][0]
            cx, cy = (bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2
            g = 0.15
            cands = [(bb.xmax + g, cy - h / 2), (bb.xmin - g - w, cy - h / 2), (cx - w / 2, bb.ymax + g),
                     (cx - w / 2, bb.ymin - g - h), (bb.xmax + g, bb.ymax - h), (bb.xmax + g, bb.ymin),
                     (bb.xmin - g - w, bb.ymax - h), (bb.xmin - g - w, bb.ymin),
                     (bb.xmax + g, bb.ymax + g), (bb.xmin - g - w, bb.ymin - g - h)]
            allo = unary_union(obst)
            best = min(cands, key=lambda c: (box(c[0], c[1], c[0] + w, c[1] + h).intersection(allo).area,
                                             cands.index(c)))
            x0, y0 = best
            for i, t in enumerate(lines):
                self.text((x0, y0 + h - lh * (i + 0.5)), t, size=size, halign="left", valign="center")
            obst.append(box(x0, y0, x0 + w, y0 + h).buffer(0.06))

    def dot_problems(self) -> List[str]:
        """the junction convention: a dot wherever three or more wires / pins / symbols meet (a T), and none where
        only two do (a corner or a wire into a pin)"""
        self._finish()
        branches = self._branches
        pts = {q for w in self.wires for q in w}
        dots = set(getattr(self, "dots", []))
        probs = [f"junction at {p} has {branches(p)} branches but no dot" for p in sorted(pts - dots)
                 if branches(p) >= 3]
        probs += [f"dot at {p} where only {branches(p)} things meet" for p in sorted(dots) if branches(p) < 3]
        return probs

    def _branches(self, p):
        """how many wires, pins and symbols meet at ``p`` (a wire passing through counts twice)"""
        n = 0
        for a, b in self.wires:
            if p in (a, b):
                n += 1
            elif _on(p, a, b):
                n += 2                                # (a wire passing through: two branches)
        n += len({r for (r, _), q in self.pins.items() if q == p})    # (a part's pads on one pin: one branch)
        n += len({net for q, net in self.ports if q == p and (q, net) not in getattr(self, "_names", set())})
        return n

    # checking
    def nets(self) -> List[set]:
        """the sheet's connectivity: groups of (ref, pin) and port names"""
        pts = set(self.pins.values()) | {p for w in self.wires for p in w} | {p for p, _ in self.ports}
        pts = sorted(pts)
        idx = {p: i for i, p in enumerate(pts)}
        parent = list(range(len(pts)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def join(a, b):
            parent[root(idx[a])] = root(idx[b])

        def on(p, a, b):
            (x, y), (x1, y1), (x2, y2) = p, a, b
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if abs(cross) > EPS * max(1.0, abs(x2 - x1) + abs(y2 - y1)):
                return False
            return min(x1, x2) - EPS <= x <= max(x1, x2) + EPS and min(y1, y2) - EPS <= y <= max(y1, y2) + EPS

        for a, b in self.wires:
            join(a, b)
            for p in pts:                                # anything sitting on the wire (a T, a pin, a port)
                if p != a and p != b and on(p, a, b):
                    join(p, a)
        by_name: Dict[str, Tuple[float, float]] = {}
        for p, n in self.ports:
            if n in by_name:
                join(p, by_name[n])
            by_name[n] = p
        groups: Dict[int, set] = {}
        for pin, p in self.pins.items():
            groups.setdefault(root(idx[p]), set()).add(pin)
        for p, n in self.ports:
            groups.setdefault(root(idx[p]), set()).add(("PORT", n))
        return list(groups.values())

    def check(self, skip_refs: Sequence[str] = ()) -> List[str]:
        """every board connection drawn, and nothing drawn the board lacks"""
        probs: List[str] = []
        pn = {k: v for k, v in self.board.pad_net().items() if k[0] not in skip_refs}
        drawn = {}
        for g in self.nets():
            pins_ = [p for p in g if p[0] != "PORT"]
            names = {n for t, n in g if t == "PORT"}
            board_nets = {pn[p] for p in pins_ if p in pn}
            if len(board_nets) > 1:
                probs.append(f"sheet joins nets {sorted(board_nets)} ({sorted(pins_)[:6]})")
            for n in names:
                if board_nets and n not in board_nets:
                    probs.append(f"port {n} on net {sorted(board_nets)}")
            for p in pins_:
                drawn[p] = (frozenset(pins_), next(iter(board_nets)) if len(board_nets) == 1 else None)
        for ref in {r for r, _ in pn}:
            if ref not in self.refs:
                probs.append(f"{ref} is on the board but not on the sheet")
        for net in sorted(set(pn.values())):
            members = [p for p, n in pn.items() if n == net]
            missing = [p for p in members if p not in drawn]
            if missing:
                probs.append(f"net {net}: pins not drawn {missing[:6]}")
            groups = {drawn[p][0] for p in members if p in drawn}
            if len(groups) > 1:
                probs.append(f"net {net} is split on the sheet into {len(groups)} pieces")
        for p in drawn:
            if p not in pn and p[0] not in skip_refs:
                n_others = len(drawn[p][0]) - 1
                if n_others:
                    probs.append(f"{p[0]}.{p[1]} is wired on the sheet but on no net on the board")
        return probs


def check_sheets(board, sheets: Sequence["Sheet"], skip_refs: Sequence[str] = ()) -> List[str]:
    """check a multi-page schematic against the board: the pages' wires stay on their own page, ports and
    off-sheet connectors of one name join across pages"""
    m = Sheet.__new__(Sheet)
    m.board, m.pins, m.wires, m.ports, m.refs = board, {}, [], [], []
    for i, s in enumerate(sheets):
        off = lambda p: (p[0] + 100000.0 * i, p[1])
        for k, p in s.pins.items():
            if k in m.pins:
                return [f"{k[0]}.{k[1]} is drawn on two pages"]
            m.pins[k] = off(p)
        m.wires += [(off(a), off(b)) for a, b in s.wires]
        m.ports += [(off(p), n) for p, n in s.ports]
        m.refs += s.refs
    return m.check(skip_refs)


TERM2 = {"1": "start", "2": "end"}       # a two-terminal part's pads on its anchors
REV2 = {"2": "start", "1": "end"}


# ---- drawing helpers (shared by every board's sheets) ---------------------------------------------------------------------------------------------------
def ic(s, ref, at, left, right, label, w=4.0, sp=0.6, top=(), bot=(), anchor=None):
    """an IC box with its lower-left corner at ``at`` (or pad ``anchor`` there); ``left`` / ``right`` [(name, pad)]
    listed top to bottom; returns pad -> point"""
    pins = [elm.IcPin(name=n, pin=p, side="left") for n, p in reversed(left)] + \
        [elm.IcPin(name=n, pin=p, side="right") for n, p in reversed(right)] + \
        [elm.IcPin(name=n, pin=p, side="top") for n, p in top] + [elm.IcPin(name=n, pin=p, side="bot") for n, p in bot]
    w = max(w, (max(len(top), len(bot)) + 1) * sp)      # (top / bottom pins sp apart: the box widens to hold them)
    h = (max(len(left), len(right)) + 1) * sp
    e = elm.Ic(pins=pins, size=(w, h), pinspacing=sp, label=label, lblsize=8, lsize=11, plblsize=9,
               lofst=0.22).right()                       # (pin names clear of the pin numbers on every side)
    e = e.anchor(f"pin{anchor}").at(at) if anchor else e.at(at)
    s.add(ref, e, {p: f"pin{p}" for _, p in list(left) + list(right) + list(top) + list(bot)})
    return lambda p: e.absanchors[f"pin{p}"]


def nets_out(s, b, ref, a, pads, side, skip=(), gnd_flag=False):
    """name each listed pad's net at its pin: a ground symbol for GND (a GND flag on a tight pin row), a net flag
    otherwise, an X if unconnected"""
    pn = b.pad_net()
    grounds = []
    for p in pads:
        if p in skip:
            continue
        n = pn.get((ref, p))
        q = a(p)
        if n is None:
            s.text((q[0] + (0.25 if side == "right" else -0.25), q[1]), "x", size=8, halign="center", valign="center")
        elif n == "GND" and not gnd_flag:
            grounds.append(q)
        else:
            s.flag(q, n, side=side)
    if grounds:                                          # the ground pins of one side tied, one ground symbol
        d = 1.0 if side == "right" else -0.6           # (right: clear of an unconnected pin's "x")
        x = grounds[0][0] + d
        for q in grounds:
            s.wire(q, (x, q[1]))
        ys = sorted(q[1] for q in grounds)
        if len(ys) > 1:
            s.wire((x, ys[-1]), (x, ys[0]))
            for y in ys[1:]:
                s.dot((x, y))
        s.gnd((x, ys[0]))


def leg(s, ref, e, x, y, label=None, pins=TERM2, gnd=True, length=1.6, loc="bottom"):
    """a two-terminal part from (x, y) down, its foot to ground"""
    s.add(ref, e.at((x, y)).down().length(length), pins, label=label, loc=loc)
    if gnd:
        s.gnd(e.absanchors["end"])
    return e


def tp(s, ref, p, name, below=False):
    """a test point: an open circle on the node, its name above it (or below, off a stub that hangs down)"""
    s.add(ref, elm.Dot(open=True).at(p), {"1": "start"})
    s.text((p[0], p[1] + (-0.45 if below else 0.45)), ref, size=6, halign="center")


def tp_on(s, ref, p, name, dx=0.0, dy=0.8):
    """a test point on a short stub from node ``p``, named on the far side from the stub"""
    q = (p[0] + dx, p[1] + dy)
    s.wire(p, q)
    s.dot(p)
    tp(s, ref, q, name, below=dy < 0)


def conn(s, ref, at, pins, title, side="left", w=1.8, sp=0.6):
    """a connector box, pins [(name, pad)] top to bottom on ``side``"""
    if side == "left":
        a = ic(s, ref, at, pins, [], "", w=w, sp=sp)
    else:
        a = ic(s, ref, at, [], pins, "", w=w, sp=sp)
    top = at[1] + (len(pins) + 1) * sp                   # (its name above the box, clear of the pin names)
    s.text((at[0] + w / 2, top + 0.3 + 0.25 * title.count("\n")), title, size=7, halign="center")
    return a


def notes(s, x, y, lines, size=7):
    s.text((x, y), "NOTES", size=8, halign="left")
    for i, t in enumerate(lines):
        s.text((x, y - 0.5 * (i + 1)), t, size=size, halign="left")


def save_sheets(sheets: Sequence["Sheet"], out_dir: str, stem: str = "schematic"):
    """each sheet as ``<stem>_<n>.png`` (~9000 px), ``.svg`` and vector ``.pdf``, and all of them in ``<stem>.pdf``"""
    import os
    import cairosvg
    from pypdf import PdfWriter
    w = PdfWriter()
    for i, s in enumerate(sheets, 1):
        base = os.path.join(out_dir, f"{stem}_{i}")
        s.save(base + ".png")
        s.save(base + ".svg")
        cairosvg.svg2pdf(url=base + ".svg", write_to=base + ".pdf", background_color="white")
        w.append(base + ".pdf")
    w.write(os.path.join(out_dir, f"{stem}.pdf"))
