"""Getting a board made and its parts bought: assembly files for a fab, and a parts cart for the distributors.

    import fab
    fab.cpl(b, "out/cpl.csv")                    # pick-and-place: every part's centre, side and rotation
    fab.jlc_bom(b, "out/bom_jlc.csv")            # JLCPCB / LCSC assembly BOM (Comment, Designator, Footprint, LCSC Part #)
    fab.cart(b, "out/cart", boards=5)            # BOM-upload CSVs for DigiKey, Mouser and LCSC, and buy.html with a
                                                 # search link per line at each, quantities for 5 boards plus spares
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
    for r in board.bom():
        maker, pn = split_mpn(r["mpn"])
        need = r["qty"] * boards
        extra = max(min_spare, math.ceil(need * spare))
        query = pn or " ".join(x for x in (r["value"], _package(r["footprint"]), _kind(r["refs"])) if x)
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
    return files
