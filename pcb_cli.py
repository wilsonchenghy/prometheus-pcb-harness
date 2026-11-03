"""The harness from the command line: build a board from a script, route it if it is not routed, check it, and write
everything to order it.

    python -m pcb_cli doctor                           # what this machine has: KiCad, Java, FreeRouting...
    python -m pcb_cli setup                            # download FreeRouting (checksum-checked)
    python -m pcb_cli my_board.py out/                 # route (if needed), check, report, order files
    python -m pcb_cli my_board.py out/ --boards 10 --rules jlcpcb-4 --step
    python -m pcb_cli my_schematic.net out/ --size 50x40 --at J1=4,20,90 --amps VBUS=2   # a KiCad netlist

The script makes the board: it defines ``build()`` returning a Board (or a (Board, problems) pair, routed), or a
module-level ``board`` / ``b`` / ``PCB``. Parts placed without a position (``at=None``) are placed and the board
routed with ``pcb.place_and_route``; a placed board with nets but no tracks is routed with ``pcb.autoroute``.
A KiCad netlist (``.net``: Eeschema, File > Export > Netlist) becomes a board of ``--size``, its parts placed
(except those fixed with ``--at``) and routed to the ``--rules`` preset (default jlcpcb-2), GND poured on the bottom.
Every run also writes ``out/result.json``: whether it is clean, each part's position, and every finding as data
(stage, kind, the pins and place it names, and what to change), for an agent's next edit; ``--json`` prints it.
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


# what an agent (or a person) can change for each kind of finding: the harness reports, the designer fixes
HINTS = [
    ("unreached", r"could not reach|no room to place", "move the parts named apart or nearer, widen a channel, or "
     "route that net earlier (it fails first on the next pass)"),
    ("split", r"is split", "the pins listed in different groups are not joined: route the net again or move parts"),
    ("short", r"\bshort\b", "two nets touch: move or re-route one of them"),
    ("clearance", r"mm apart \(clearance", "copper of two nets too close: move or re-route, or check the clearance"),
    ("current", r"too narrow for|neck narrower", "widen the track or pour, or set the net's width= / amps="),
    ("voltage drop", r"drops? ", "shorten or widen the path"),
    ("drill", r"drill", "move the vias or holes apart, or use a larger drill (min_drill=)"),
    ("dangling", r"dangling|loose track end", "remove the stub or end it on copper of its net"),
    ("placement", r"courtyard|hangs off|no position|no part|has no pad", "fix the part's position or the pin name"),
    ("edge", r"edge", "keep copper further inside the outline"),
]


def finding(stage: str, text: str) -> dict:
    """a finding as data: its stage, kind, the parts / pins and the place it names, and what to change"""
    import re
    kind, hint = "other", "read the text"
    for k, rx, h in HINTS:
        if re.search(rx, text):
            kind, hint = k, h
            break
    at = re.search(r"\((-?[\d.]+), (-?[\d.]+)\)", text)
    pins = sorted(set(re.findall(r"\b([A-Z][A-Z0-9_]*\d+[A-Z]?\.[A-Za-z0-9]+)\b", text)))
    out = {"stage": stage, "kind": kind, "text": text, "pins": pins, "hint": hint}
    if at:
        out["at"] = [float(at.group(1)), float(at.group(2))]
    return out


FREEROUTING_URL = "https://github.com/freerouting/freerouting/releases/download/v2.0.1/freerouting-2.0.1.jar"
FREEROUTING_SHA256 = "d7fd0f63f52e6d74b0fad6715f87ca9f0ffd7109d66b2a584638000270592ecf"


def doctor() -> dict:
    """what this machine gives the harness: Python packages, KiCad (its DRC), Java and FreeRouting (the router),
    and what the harness falls back to without each"""
    import importlib.util
    import shutil
    import pcb
    import freerouting
    ver = pcb.kicad_version()
    major = int(ver.split(".")[0]) if ver and ver.split(".")[0].isdigit() else 0
    java = shutil.which("java")
    jar = freerouting.find_jar()
    rows = {
        "kicad": {"ok": major >= 9, "have": ver or "not installed",
                  "gives": "KiCad's own DRC on every run; FreeRouting through KiCad's Specctra export",
                  "without": "the harness's own design check and house check only (still run every time)"},
        "kicad_python": {"ok": freerouting.kicad_available(), "have": "pcbnew importable" if freerouting.kicad_available()
                         else "no", "gives": "KiCad writes FreeRouting's input (exact pads, net classes)",
                         "without": "the harness writes the Specctra file itself"},
        "java": {"ok": bool(java), "have": java or "not installed", "gives": "runs FreeRouting",
                 "without": "no FreeRouting: the built-in grid router"},
        "freerouting": {"ok": bool(jar), "have": jar or "not found", "gives": "the router KiCad users use",
                        "without": "the built-in grid router (fine for simple boards; dense 0.4 mm pitch chips fail)"},
        "build123d": {"ok": importlib.util.find_spec("build123d") is not None, "have": "",
                      "gives": "STEP of the assembled board (--step)", "without": "no STEP"},
        "schemdraw": {"ok": importlib.util.find_spec("schemdraw") is not None, "have": "",
                      "gives": "schematic sheets (schem.py)", "without": "no schematic sheets"},
    }
    return rows


def setup() -> int:
    """download FreeRouting into the cache (checked against its published SHA-256) and say what else to install"""
    import hashlib
    import urllib.request
    path = os.path.expanduser(f"~/.cache/prometheus/freerouting/{os.path.basename(FREEROUTING_URL)}")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"downloading FreeRouting from {FREEROUTING_URL}")
        tmp = path + ".part"
        urllib.request.urlretrieve(FREEROUTING_URL, tmp)
        got = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        if got != FREEROUTING_SHA256:
            os.remove(tmp)
            print(f"FreeRouting's checksum does not match ({got}): not installed")
            return 1
        os.replace(tmp, path)
    print(f"FreeRouting: {path}")
    return report()


def report() -> int:
    rows = doctor()
    for k, r in rows.items():
        print(f"{'ok ' if r['ok'] else '-- '} {k:13s} {r['have'] or ('yes' if r['ok'] else 'no')}")
        print(f"     {'gives: ' + r['gives'] if r['ok'] else 'without it: ' + r['without']}")
    missing = [k for k, r in rows.items() if not r["ok"]]
    if missing:
        print("\nto install what is missing: docs/setup.md")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["doctor"]:
        return report()
    if argv[:1] == ["setup"]:
        return setup()
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
    ap.add_argument("--kicad-drc", choices=("auto", "off", "require"), default="auto",
                    help="KiCad's own DRC on the exported board: auto (when KiCad 9+ is installed; default), off, "
                         "or require (fail when KiCad is missing)")
    ap.add_argument("--router", choices=("auto", "freerouting", "harness"), default="auto",
                    help="auto (FreeRouting when Java and its jar are installed, else the built-in router; default), "
                         "freerouting, or harness")
    ap.add_argument("--stage", choices=("critic", "place", "route", "check", "export", "all"), default="all",
                    help="run one stage: critic (the design review, instant), place (place what has no position), "
                         "route (place and route, then the checks), check (the checks on the board as the script "
                         "left it), export (the order files and KiCad's DRC, no routing), all (default)")
    ap.add_argument("--spec", help="a requirements file (JSON: size_mm, layers, house, nets, pairs, near, edge, holes, "
                                   "assembly, max_parts) the critic checks the board against")
    ap.add_argument("--json", action="store_true", help="print the result as JSON (for an agent) instead of text")
    a = ap.parse_args(argv)
    if a.json:                                           # (the progress lines go to stderr, the JSON alone to stdout)
        real_stdout, sys.stdout = sys.stdout, sys.stderr
    t0 = time.perf_counter()
    import json
    import critic
    spec = critic.load_spec(a.spec) if a.spec else {}
    os.makedirs(a.out, exist_ok=True)
    b, probs = load_netlist(a.script, a) if a.script.endswith(".net") else load(a.script)
    for note in getattr(b, "import_notes", []):
        print("note:", note)
    print(f"{b.name}: {len(b.comps)} parts, {len(b.nets)} nets, {len(b.layers)} layers  [stage: {a.stage}]")
    stage = a.stage
    route_ok = stage in ("route", "all") and not a.no_route
    timings = {}
    if stage in ("place", "route", "all") and any(c.get("unplaced") for c in b.comps.values()) and not a.no_route:
        t = time.perf_counter()
        if route_ok:
            probs = pcb.place_and_route(b, router=a.router)
            print(f"placed and routed ({getattr(b, 'router_used', '')}): "
                  f"{'clean' if not probs else f'{len(probs)} problems'} ({time.perf_counter() - t:.1f} s)")
        else:
            left = pcb.autoplace(b)
            print(f"placed: {'every part' if not left else 'no room for ' + ', '.join(left)} ({time.perf_counter() - t:.1f} s)")
        timings["place" if not route_ok else "place+route"] = round(time.perf_counter() - t, 2)
    place = b.check_placement()
    nopos = [p_ for p_ in place if "has no position yet" in p_]
    if len(nopos) > 3:                                    # (one line for the parts the placer has not placed)
        place = [p_ for p_ in place if p_ not in nopos] + \
            [f"{len(nopos)} parts have no position yet ({', '.join(p_.split()[0] for p_ in nopos[:8])} ...): "
             "--stage place or all places them"]
    if place and stage not in ("place", "critic"):         # (those stages print it with their results)
        print("placement:", *place, sep="\n  ")
    if route_ok and probs is None and not b.tracks:
        t = time.perf_counter()
        probs = pcb.route_board(b, a.router)
        timings["route"] = round(time.perf_counter() - t, 2)
        print(f"routed ({b.router_used}): {'clean' if not probs else f'{len(probs)} problems'} ({timings['route']} s)")
    probs = probs or []
    t = time.perf_counter()
    notes = critic.review(b, spec)
    timings["critic"] = round(time.perf_counter() - t, 3)
    crit_err = [n for n in notes if n["severity"] == "error"]
    chk, fchk, kchk, kicad, files = [], [], [], {"run": False}, {}
    routed = bool(b.tracks) or stage in ("check", "export")
    if stage in ("place", "critic"):
        chk = place                                       # (no copper yet: the placement is what can be checked)
    else:
        t = time.perf_counter()
        chk = b.check()
        fchk = fab.fab_check(b, a.rules or spec.get("house"))
        timings["check"] = round(time.perf_counter() - t, 2)
    if stage in ("export", "all"):
        t = time.perf_counter()
        files = fab.order(b, a.out, boards=a.boards)
        kpaths = pcb.export_kicad(b, os.path.join(a.out, "kicad"))
        if a.kicad_drc != "off":                        # a third, independent judge: KiCad's own DRC
            ver = pcb.kicad_version()
            major = int(ver.split(".")[0]) if ver and ver.split(".")[0].isdigit() else 0
            if major >= 9:
                d = pcb.kicad_drc(kpaths[0])
                kchk = d["errors"] + d["unconnected"]
                kicad = {"run": True, "version": ver, "warnings": d["warnings"]}
            else:
                why = f"KiCad {ver} is too old (9+ needed: the board is KiCad 9 format)" if ver else "KiCad (kicad-cli) is not installed"
                how = pcb.KICAD_INSTALL.get(sys.platform, pcb.KICAD_INSTALL["linux"])
                kicad = {"run": False, "skipped": why, "install": how}
                print(f"KiCad DRC: skipped, {why}. To add it: {how}")
                if a.kicad_drc == "require":
                    kchk = [f"KiCad DRC required but {why}"]
        if a.step:
            files["step"] = pcb.export_step(b, os.path.join(a.out, f"{b.name}.step"))
        timings["export"] = round(time.perf_counter() - t, 2)
    sections = [("routing", probs), ("placement" if stage in ("place", "critic") else "design check", chk),
                ("house check", fchk)] + ([("KiCad DRC", kchk)] if kicad["run"] or kchk else [])
    if stage in ("place", "critic"):
        sections = [s_ for s_ in sections if s_[0] != "house check"]
    for name, ps in sections:
        print(f"{name}: {'clean' if not ps else ''}")
        for p in ps[:20]:
            print("  ", p)
        if len(ps) > 20:
            print(f"   ... and {len(ps) - 20} more")
    print("critic: " + ("no notes" if not notes else ""))
    for n in notes[:20]:
        print(f"   [{n['severity']}] {n['text']}")
    if files:
        print(f"wrote {a.out}: review {files['report']}, parts {files['html']}")
    print(f"({time.perf_counter() - t0:.1f} s: " + ", ".join(f"{k} {v} s" for k, v in timings.items()) + ")")
    clean = not (probs or chk or fchk or kchk or crit_err) and (routed or stage in ("place", "critic"))
    result = {"board": b.name, "stage": stage, "clean": clean, "kicad_drc": kicad,
              "router": getattr(b, "router_used", "none (routed by the script)"),
              "environment": {k: r["ok"] for k, r in doctor().items()},   # (what this machine had: docs/setup.md)
              "parts": len(b.comps), "nets": len(b.nets), "layers": len(b.layers),
              "seconds": round(time.perf_counter() - t0, 1), "timings": timings,
              "placement": {r: [c["at"][0], c["at"][1], c["rot"], c.get("side", "F")] for r, c in b.comps.items()},
              "findings": [finding(st, p) for st, ps in (("routing", probs), ("design", chk), ("house", fchk),
                                                          ("kicad", kchk))
                           for p in ps],
              "critic": notes,
              "files": {k: str(v) for k, v in files.items()}}
    with open(os.path.join(a.out, "result.json"), "w") as f:
        json.dump(result, f, indent=1)
    if a.json:
        sys.stdout = real_stdout
        print(json.dumps(result, indent=1))
    return 0 if result["clean"] else 1

if __name__ == "__main__":
    sys.exit(main())
