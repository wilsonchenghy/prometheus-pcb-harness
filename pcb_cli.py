"""The harness from the command line: build a board from a script, route it if it is not routed, check it, and write
everything to order it.

    python -m pcb_cli my_board.py out/                 # route (if needed), check, report, order files
    python -m pcb_cli my_board.py out/ --boards 10 --rules jlcpcb-4 --step

The script makes the board: it defines ``build()`` returning a Board (or a (Board, problems) pair, routed), or a
module-level ``board`` / ``b`` / ``PCB``. Parts placed without a position (``at=None``) are placed and the board
routed with ``pcb.place_and_route``; a placed board with nets but no tracks is routed with ``pcb.autoroute``.
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
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    b, probs = load(a.script)
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
