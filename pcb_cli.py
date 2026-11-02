"""The harness from the command line: build a board from a script, route it if it is not routed, check it, and write
everything to order it.

    python -m pcb_cli my_board.py out/                 # route (if needed), check, report, order files
    python -m pcb_cli my_board.py out/ --boards 10 --rules jlcpcb-4 --step
    python -m pcb_cli my_schematic.net out/ --size 50x40 --at J1=4,20,90 --amps VBUS=2   # a KiCad netlist

The script makes the board: it defines ``build()`` returning a Board (or a (Board, problems) pair, routed), or a
module-level ``board`` / ``b`` / ``PCB``. Parts placed without a position (``at=None``) are placed and the board
routed with ``pcb.place_and_route``; a placed board with nets but no tracks is routed with ``pcb.autoroute``.
A KiCad netlist (``.net``: Eeschema, File > Export > Netlist) becomes a board of ``--size``, its parts placed
(except those fixed with ``--at``) and routed to the ``--rules`` preset (default jlcpcb-2), GND poured on the bottom.
Exit status 0 when routing, the design check and the house check are all clean, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import runpy
import sys
import time


def load(path: str):
    """the Board a script makes, and the routing problems it reports (None when it did not route)"""
    import pcb
    sys.path[:0] = [os.path.dirname(os.path.abspath(path))]
    g = runpy.run_path(path, run_name="pcb_cli")
    if callable(g.get("build")):
        got = g["build"]()
        if isinstance(got, tuple):
            return got[0], list(got[1])
        return got, None
    for name in ("board", "b", "PCB"):
        if isinstance(g.get(name), pcb.Board):
            return g[name], g.get("PCB_ROUTE_PROBLEMS")
    raise SystemExit(f"{path}: defines no build() and no board / b / PCB")


def load_netlist(path: str, a):
    """a Board from a KiCad netlist and the command line's board size, fixed parts and fab preset"""
    import pcb
    if not a.size:
        raise SystemExit(f"{path}: a netlist needs the board size, --size WxH (mm)")
    w, h = (float(v) for v in a.size.lower().split("x"))
    at = {}
    for spec in a.at or []:
        ref, xy = spec.split("=")
        at[ref] = tuple(float(v) for v in xy.split(",")[:3]) + tuple(xy.split(",")[3:4])
    amps = {n: float(v) for n, v in (s_.split("=") for s_ in (a.amps or []))}
    return pcb.from_netlist(path, (w, h), at=at, amps=amps, rules=a.rules or "jlcpcb-2", layers=a.layers), None


def main(argv=None) -> int:
    import fab
    import pcb
    ap = argparse.ArgumentParser(prog="python -m pcb_cli", description=__doc__.split("\n\n")[0])
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--boards", type=int, default=5, help="how many boards to buy parts for (default 5)")
    ap.add_argument("--rules", default=None, help="fab preset for the house check (default: the board's, else jlcpcb-2)")
    ap.add_argument("--step", action="store_true", help="also write the assembled board as STEP (needs build123d)")
    ap.add_argument("--no-route", action="store_true", help="do not route an unrouted board")
    ap.add_argument("--size", help="a netlist's board size, WxH mm (e.g. 50x40)")
    ap.add_argument("--layers", type=int, default=2, help="a netlist's board: 2 or 4 layers (default 2)")
    ap.add_argument("--at", action="append", metavar="REF=X,Y[,ROT[,SIDE]]",
                    help="a netlist's part fixed in place, e.g. --at J1=4,12.5,90 (repeat for each)")
    ap.add_argument("--amps", action="append", metavar="NET=A", help="a netlist's net current, e.g. --amps VBUS=2")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    b, probs = load_netlist(a.script, a) if a.script.endswith(".net") else load(a.script)
    for note in getattr(b, "import_notes", []):
        print("note:", note)
    print(f"{b.name}: {len(b.comps)} parts, {len(b.nets)} nets, {len(b.layers)} layers")
    if any(c.get("unplaced") for c in b.comps.values()) and not a.no_route:
        t = time.perf_counter()
        probs = pcb.place_and_route(b)
        print(f"placed and routed: {'clean' if not probs else f'{len(probs)} problems'} ({time.perf_counter() - t:.1f} s)")
    place = b.check_placement()
    if place:
        print("placement:", *place, sep="\n  ")
    if probs is None and not b.tracks and not a.no_route:
        t = time.perf_counter()
        probs = pcb.autoroute(b)
        print(f"autoroute: {'clean' if not probs else f'{len(probs)} problems'} ({time.perf_counter() - t:.1f} s)")
    probs = probs or []
    chk = b.check()
    fchk = fab.fab_check(b, a.rules)
    files = fab.order(b, a.out, boards=a.boards)
    pcb.export_kicad(b, os.path.join(a.out, "kicad"))
    if a.step:
        files["step"] = pcb.export_step(b, os.path.join(a.out, f"{b.name}.step"))
    for name, ps in (("routing", probs), ("design check", chk), ("house check", fchk)):
        print(f"{name}: {'clean' if not ps else ''}")
        for p in ps[:20]:
            print("  ", p)
        if len(ps) > 20:
            print(f"   ... and {len(ps) - 20} more")
    print(f"wrote {a.out}: review {files['report']}, parts {files['html']} ({time.perf_counter() - t0:.1f} s)")
    return 0 if not (probs or chk or fchk) else 1


if __name__ == "__main__":
    sys.exit(main())
