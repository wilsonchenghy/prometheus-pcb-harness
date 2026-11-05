"""Getting a board made and its parts bought: assembly files for a fab, and a parts cart for the distributors.

    import fab
    fab.cpl(b, "out/cpl.csv")                    # pick-and-place: every part's centre, side and rotation
    fab.jlc_bom(b, "out/bom_jlc.csv")            # JLCPCB / LCSC assembly BOM (Comment, Designator, Footprint, LCSC Part #)
    fab.cart(b, "out/cart", boards=5)            # BOM-upload CSVs for DigiKey, Mouser and LCSC, and buy.html with a
                                                 # search link per line at each, quantities for 5 boards plus spares
    fab.report(b, "out/review")                  # one page to review: the copper, both checks, the parts
    fab.order(b, "out/order", boards=5)          # all of the above, the Gerbers and their zip, in one folder

Nothing here logs in or adds to a cart on your behalf: the distributors' BOM tools take the CSVs (DigiKey: myLists /
BOM Manager "upload"; Mouser: BOM Tool "upload a spreadsheet"; LCSC: BOM Tool "upload BOM"), and buy.html links each
line to a search at each shop. Prices and stock change by the hour: the links search live.

Part numbers: ``Board.place(..., mpn="Maker PART-NUMBER", lcsc="C12345")``. An ``mpn`` of more than one word is read
as the maker then the part number (its last word); a part with no ``mpn`` (a generic resistor or capacitor) is
searched by its value and package, e.g. "10k 0603 resistor".
"""
from __future__ import annotations

import csv
import html
import math
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus

SHOPS = {
    "DigiKey": "https://www.digikey.com/en/products/result?keywords={q}",
    "Mouser": "https://www.mouser.com/c/?q={q}",
    "LCSC": "https://www.lcsc.com/search?q={q}",
    "Octopart": "https://octopart.com/search?q={q}",
}
_KIND = {"R": "resistor", "C": "capacitor", "L": "inductor", "D": "diode", "LED": "LED", "F": "fuse", "Y": "crystal",
         "Q": "transistor", "U": "", "J": "connector", "SW": "switch", "FB": "ferrite bead"}


def split_mpn(mpn: str):
    """"TI LM5069MM-2/NOPB" -> ("TI", "LM5069MM-2/NOPB"); one word -> ("", word)"""
    w = (mpn or "").split()
    if len(w) > 1:
        return " ".join(w[:-1]), w[-1]
    return "", (w[0] if w else "")


def _package(footprint: str) -> str:
    m = re.search(r"(?<![0-9])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])", footprint)
    if m:
        return m.group(1)
    return footprint.split("_")[0]


def _kind(refs: List[str]) -> str:
    p = re.sub(r"\d+$", "", refs[0]) if refs else ""
    return _KIND.get(p, _KIND.get(p[:1], ""))


def lines(board, boards: int = 1, spare: float = 0.1, min_spare: int = 0) -> List[Dict]:
    """the board's BOM as order lines: per line the maker, part number, LCSC number, a search query, and the
    quantity for ``boards`` boards plus ``spare`` (a fraction, at least ``min_spare`` parts; passives get at least
    10% extra since they are lost in assembly by the tape)"""
    out = []
    rows: Dict[tuple, Dict] = {}                         # one line per part to buy: the same part number (or LCSC
    for r in board.bom():                                # number) on parts of different values merges into one
        key = ("mpn", r["mpn"].lower(), r["footprint"]) if r["mpn"] else \
            (("lcsc", r["lcsc"]) if r["lcsc"] else ("row", r["value"], r["footprint"], r["refs"][0]))
        if key in rows:
            m = rows[key]
            m["refs"] = m["refs"] + r["refs"]
            m["qty"] += r["qty"]
            if r["value"] not in m["value"].split(" / "):
                m["value"] = f"{m['value']} / {r['value']}"
        else:
            rows[key] = dict(r)
    for r in rows.values():
        maker, pn = split_mpn(r["mpn"])
        need = r["qty"] * boards
        extra = max(min_spare, math.ceil(need * spare))
        fp = r["footprint"]
        hdr = re.match(r"Pin(Header|Socket)_(\d+x\d+)_P([\d.]+)mm", fp)
        if hdr:                                          # a generic header / socket: its shape is the part
            query = f"{hdr.group(2)} {hdr.group(3)}mm pin {hdr.group(1).lower()}"
        else:
            kind = "LED" if fp.startswith("LED") else _kind(r["refs"])
            query = " ".join(x for x in (r["value"], _package(fp), kind) if x)
        query = pn or query
        out.append({**r, "maker": maker, "pn": pn, "query": query, "need": need, "order": need + extra,
                    "generic": not pn})
    return out


def cpl(board, path: str, origin=(0.0, 0.0)) -> str:
    """the pick-and-place (centroid) file as JLCPCB and most assembly houses take it: Designator, Mid X, Mid Y,
    Layer, Rotation (mm, the board's frame less ``origin``; degrees ccw from above, as KiCad's). Some houses rotate
    some packages differently from KiCad: check the placement preview the house shows before you pay."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for ref, c in sorted(board.comps.items()):
            x, y = c["at"][0] - origin[0], c["at"][1] - origin[1]
            w.writerow([ref, f"{x:.3f}mm", f"{y:.3f}mm", "Bottom" if c.get("side", "F") == "B" else "Top",
                        f"{c['rot'] % 360:.1f}"])
    return path


def jlc_bom(board, path: str) -> str:
    """the assembly BOM JLCPCB takes: Comment, Designator, Footprint, LCSC Part # (lines with no LCSC number are
    left for the house to match, or to you)"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for r in board.bom():
            w.writerow([r["value"] or split_mpn(r["mpn"])[1], ",".join(r["refs"]), r["footprint"], r.get("lcsc", "")])
    return path


def cart(board, out_dir: str, boards: int = 1, spare: float = 0.1) -> Dict[str, str]:
    """a parts cart for ``boards`` boards: digikey.csv, mouser.csv and lcsc.csv to upload to each shop's BOM tool,
    and buy.html with every line, its quantity and a search link at each shop. Returns the files"""
    os.makedirs(out_dir, exist_ok=True)
    ls = lines(board, boards, spare)
    files = {}
    p = os.path.join(out_dir, "digikey.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Quantity", "Manufacturer Part Number", "Manufacturer", "Customer Reference", "Description"])
        for l_ in ls:
            w.writerow([l_["order"], l_["pn"] or l_["query"], l_["maker"], ",".join(l_["refs"]),
                        f"{l_['value']} {l_['footprint']}".strip()])
    files["digikey"] = p
    p = os.path.join(out_dir, "mouser.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Mfr Part Number", "Manufacturer", "Quantity", "Customer Part Number", "Description"])
        for l_ in ls:
            w.writerow([l_["pn"] or l_["query"], l_["maker"], l_["order"], ",".join(l_["refs"]),
                        f"{l_['value']} {l_['footprint']}".strip()])
    files["mouser"] = p
    p = os.path.join(out_dir, "lcsc.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Quantity", "Manufacture Part Number", "Manufacturer", "LCSC Part Number", "Customer Part Number"])
        for l_ in ls:
            w.writerow([l_["order"], l_["pn"] or l_["query"], l_["maker"], l_.get("lcsc", ""), ",".join(l_["refs"])])
    files["lcsc"] = p
    files["html"] = _buy_html(board, ls, os.path.join(out_dir, "buy.html"), boards)
    return files


def _buy_html(board, ls, path: str, boards: int) -> str:
    rows = []
    for l_ in ls:
        q = quote_plus(l_["lcsc"] if l_.get("lcsc") else l_["query"])
        qp = quote_plus(l_["query"])
        links = " ".join(f'<a href="{html.escape(u.format(q=(q if s == "LCSC" else qp)))}" target="_blank" '
                         f'rel="noopener">{s}</a>' for s, u in SHOPS.items())
        part = html.escape(" ".join(x for x in (l_["maker"], l_["pn"]) if x) or l_["query"])
        tag = ' <span class="g">generic: pick any that matches</span>' if l_["generic"] else ""
        rows.append(f"<tr><td>{html.escape(', '.join(l_['refs']))}</td><td>{part}{tag}</td>"
                    f"<td>{html.escape(l_['value'])}</td><td>{html.escape(l_['footprint'])}</td>"
                    f"<td class=n>{l_['need']}</td><td class=n><b>{l_['order']}</b></td><td>{links}</td></tr>")
    total = sum(l_["order"] for l_ in ls)
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(board.name)} parts</title>
<style>
:root {{ --bg:#fff; --fg:#1b1d21; --mute:#5f6670; --line:#e3e6ea; --acc:#0b6bcb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181c; --fg:#e8eaed; --mute:#9aa1aa; --line:#2b2f36; --acc:#6aaef5; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:20px; margin:0 0 4px; }} p {{ color:var(--mute); margin:0 0 16px; }}
.wrap {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--mute); font-weight:600; }} .n {{ text-align:right; font-variant-numeric:tabular-nums; }}
a {{ color:var(--acc); margin-right:8px; }} .g {{ color:var(--mute); font-size:12px; }}
</style></head><body><main>
<h1>{html.escape(board.name)}: parts for {boards} board{'s' if boards != 1 else ''}</h1>
<p>{len(ls)} lines, {total} parts with spares. Upload digikey.csv, mouser.csv or lcsc.csv to that shop's BOM tool to
fill a cart in one go, or follow a line's links to search it at each shop.</p>
<div class="wrap"><table><thead><tr><th>Refs</th><th>Part</th><th>Value</th><th>Footprint</th><th class=n>Need</th>
<th class=n>Order</th><th>Search</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></div></main></body></html>
"""
    with open(path, "w") as f:
        f.write(doc)
    return path


# Design-rule presets: each house's STANDARD capabilities (no extra charge), conservative, mm (hole_gap: drill wall to
# drill wall between nets; hole_gap_same: within one net, e.g. stitching vias; default the same). Houses revise these:
# check the house's capabilities page for your order (layer count, copper weight, thickness change them).
RULES = {
    "jlcpcb-2": dict(track=0.127, space=0.127, drill=0.3, via_dia=0.6, annular=0.13, hole_gap=0.5, hole_gap_same=0.254, edge=0.3,
                     pth_drill=0.3, max_layers=2, note="JLCPCB 2-layer, 1 oz outer"),
    "jlcpcb-4": dict(track=0.09, space=0.09, drill=0.3, via_dia=0.5, annular=0.1, hole_gap=0.5, hole_gap_same=0.254, edge=0.3,
                     pth_drill=0.3, max_layers=4, note="JLCPCB 4-layer, 1 oz outer, 0.5 oz inner"),
    "pcbway-std": dict(track=0.1, space=0.1, drill=0.2, via_dia=0.45, annular=0.15, hole_gap=0.4, edge=0.3,
                       pth_drill=0.2, max_layers=4, note="PCBWay standard"),
    "oshpark-2": dict(track=0.1524, space=0.1524, drill=0.254, via_dia=0.508, annular=0.127, hole_gap=0.5, edge=0.38,
                      pth_drill=0.254, max_layers=2, note="OSH Park 2-layer"),
}


def fab_check(board, rules=None) -> List[str]:
    """the board against a house's design rules (a preset name from RULES or a dict like one): track widths,
    the clearance it was checked at, vias (drill, pad, annular ring), plated and unplated drills, drill-to-drill
    gaps and copper to the board edge. Returns the problems, each saying where; [] = within the house's standard
    process. Run with Board.check(): that one checks the design, this one the fab's limits. Default: the rules the
    board was made with (``Board(..., rules=)``), else JLCPCB 2-layer"""
    from shapely.geometry import LineString, Point
    rules = rules if rules is not None else (getattr(board, "rules", None) or "jlcpcb-2")
    r = RULES[rules] if isinstance(rules, str) else rules
    out = []
    if len(board.layers) > r.get("max_layers", 99):
        out.append(f"{len(board.layers)} layers: the preset allows {r['max_layers']}")
    if board.clear < r["space"] - 1e-9:
        out.append(f"clearance {board.clear:.3f} mm is under the house's {r['space']:.3f} mm")
    edge = board.outline.boundary                        # (cut-outs are edges too)
    for k, t in enumerate(board.tracks):
        if t["w"] < r["track"] - 1e-9:
            out.append(f"track {t['net']} ({t['layer']}) {t['w']:.3f} mm wide at {tuple(round(c, 2) for c in t['pts'][0])}: "
                       f"the house's minimum is {r['track']:.3f} mm")
        d = LineString(t["pts"]).distance(edge) - t["w"] / 2 if len(t["pts"]) > 1 else Point(t["pts"][0]).distance(edge)
        if d < r["edge"] - 1e-6:
            out.append(f"track {t['net']} ({t['layer']}) {d:.2f} mm from the board edge (min {r['edge']:.2f})")
    holes = []
    for v in board.vias:
        ring = (v["dia"] - v["drill"]) / 2
        if v["drill"] < r["drill"] - 1e-9:
            out.append(f"via {v['net']} at {tuple(round(c, 2) for c in v['at'])}: drill {v['drill']:.2f} < {r['drill']:.2f} mm")
        if v["dia"] < r["via_dia"] - 1e-9 or ring < r["annular"] - 1e-9:
            out.append(f"via {v['net']} at {tuple(round(c, 2) for c in v['at'])}: pad {v['dia']:.2f} mm, ring "
                       f"{ring:.3f} mm (min pad {r['via_dia']:.2f}, ring {r['annular']:.3f})")
        if Point(v["at"]).distance(edge) - v["dia"] / 2 < r["edge"] - 1e-6:
            out.append(f"via {v['net']} at {tuple(round(c, 2) for c in v['at'])} is closer than {r['edge']:.2f} mm to the edge")
        holes.append((v["at"], v["drill"], f"via {v['net']}", v["net"]))
    for ref, c in board.comps.items():
        for num, lst in c["pads"].items():
            for p in lst:
                if p.get("drill"):
                    if p["drill"] < r["pth_drill"] - 1e-9:
                        out.append(f"{ref}.{num}: drill {p['drill']:.2f} < {r['pth_drill']:.2f} mm" +
                                   (" (a thermal via: Board(rules=...) or place(..., min_drill=...) enlarges it)"
                                    if any(q["kind"] == "smd" for q in lst) else ""))
    for (x, y, d) in board.all_holes():
        holes.append(((x, y), d, "mounting hole", None))
    if holes:                                            # drill wall to drill wall (vias and mounting holes):
        from shapely.strtree import STRtree              # only the pairs within reach, not every pair
        pts = [Point(a) for a, _, _, _ in holes]
        big = max(d for _, d, _, _ in holes)
        tree = STRtree(pts)
        for i, (a, da, na, ea) in enumerate(holes):
            for j in sorted(int(j) for j in tree.query(pts[i], predicate="dwithin", distance=r["hole_gap"] + big)):
                if j <= i:
                    continue
                (b, db, nb, eb) = holes[j]
                dist = pts[i].distance(pts[j])
                gap = dist - (da + db) / 2
                need = r.get("hole_gap_same", r["hole_gap"]) if ea is not None and ea == eb else r["hole_gap"]
                if dist > 1e-6 and gap < need - 1e-6:
                    out.append(f"{na} at {tuple(round(c, 2) for c in a)} and {nb} at {tuple(round(c, 2) for c in b)}: "
                               f"drills {gap:.2f} mm apart (min {need:.2f})")
    return list(dict.fromkeys(out))                       # (a pad number with several holes says it once)


def report(board, out_dir: str, rules="jlcpcb-2", boards: int = 5) -> str:
    """one page to review a board before ordering it: the copper plot, its size and stack, the design check and the
    house check (each problem in words, or "clean"), and the parts with their order quantities and search links.
    Writes out_dir/report.html (and layers.png beside it); returns the page's path"""
    import base64
    import io
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, "layers.png")
    board.plot(png, scale=14.0)
    img = base64.b64encode(open(png, "rb").read()).decode()
    chk, fchk = board.check(), fab_check(board, rules)
    x0, y0, x1, y1 = board.outline.bounds
    ls = lines(board, boards)
    esc = html.escape

    def plist(ps):
        if not ps:
            return '<p class="ok">clean</p>'
        return "<ul>" + "".join(f"<li>{esc(p)}</li>" for p in ps) + "</ul>"
    rows = "".join(f"<tr><td>{esc(', '.join(l_['refs']))}</td><td>{esc(' '.join(x for x in (l_['maker'], l_['pn']) if x) or l_['query'])}</td>"
                   f"<td>{esc(l_['value'])}</td><td>{esc(l_['footprint'])}</td><td class=n>{l_['order']}</td></tr>" for l_ in ls)
    rules = rules if rules is not None else (getattr(board, "rules", None) or "jlcpcb-2")
    r = RULES[rules] if isinstance(rules, str) else rules
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(board.name)} review</title>
<style>
:root {{ --bg:#fff; --fg:#1b1d21; --mute:#5f6670; --line:#e3e6ea; --ok:#1a7f37; --bad:#b42318; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181c; --fg:#e8eaed; --mute:#9aa1aa; --line:#2b2f36; --ok:#4cc26a; --bad:#f07167; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:1000px; margin:0 auto; }} h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:24px 0 8px; }}
.mute {{ color:var(--mute); }} img {{ max-width:100%; border-radius:6px; }} .ok {{ color:var(--ok); font-weight:600; }}
ul {{ color:var(--bad); padding-left:18px; }} .wrap {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }} th {{ color:var(--mute); }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
</style></head><body><main>
<h1>{esc(board.name)}</h1>
<p class="mute">{x1 - x0:.1f} x {y1 - y0:.1f} mm, {len(board.layers)} layers, {board.t} mm, {board.oz} oz outer copper,
{len(board.comps)} parts, {len(board.nets)} nets, {len(board.tracks)} tracks, {len(board.vias)} vias.</p>
<img alt="copper, top and bottom" src="data:image/png;base64,{img}">
<h2>Design check</h2>{plist(chk)}
<h2>House check: {esc(r.get('note', str(rules)))}</h2>{plist(fchk)}
<h2>Parts for {boards} boards</h2>
<div class="wrap"><table><thead><tr><th>Refs</th><th>Part</th><th>Value</th><th>Footprint</th><th class=n>Order</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="mute">The cart (cart/buy.html) has a search link per line at each shop; fab.order() writes it with the Gerbers.</p>
</main></body></html>
"""
    path = os.path.join(out_dir, "report.html")
    with open(path, "w") as f:
        f.write(doc)
    return path


def order(board, out_dir: str, boards: int = 5, spare: float = 0.1, origin=(0.0, 0.0)) -> Dict[str, str]:
    """everything to order a board made and assembled and its parts bought, in one folder: gerbers/ and
    gerbers.zip, cpl.csv, bom_jlc.csv, and the cart (cart/: the three shops' CSVs and buy.html)"""
    import pcb
    os.makedirs(out_dir, exist_ok=True)
    pcb.export_gerbers(board, os.path.join(out_dir, "gerbers"))
    files = {"gerbers": pcb.pack(os.path.join(out_dir, "gerbers")),
             "cpl": cpl(board, os.path.join(out_dir, "cpl.csv"), origin),
             "bom_jlc": jlc_bom(board, os.path.join(out_dir, "bom_jlc.csv"))}
    files.update(cart(board, os.path.join(out_dir, "cart"), boards, spare))
    files["report"] = report(board, out_dir, boards=boards)
    return files
