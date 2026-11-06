"""The harness from the command line: build a board from a script, route it if it is not routed, check it, and write
everything to order it.

    python -m pcb_cli doctor                           # what this machine has: KiCad, Java, FreeRouting...
    python -m pcb_cli setup                            # download FreeRouting (checksum-checked)
    python -m pcb_cli update                           # the newest harness from GitHub (and the Claude Code skill)
    python -m pcb_cli my_board.py out/                 # route (if needed), check, report, order files
    python -m pcb_cli my_board.py out/ --boards 10 --rules jlcpcb-4 --step
    python -m pcb_cli my_schematic.net out/ --size 50x40 --at J1=4,20,90 --amps VBUS=2   # a KiCad netlist
    python -m pcb_cli import design.kicad_pcb rev2/    # a finished KiCad layout as rev2/board.py, for a revision

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
from pathlib import Path
import sys
import time


def load(path: str):
    """the Board a script makes, and the routing problems it reports (None when it did not route)"""
    import pcb
    sys.path[:0] = [os.path.dirname(os.path.abspath(path))]
    own = Path(os.path.dirname(os.path.abspath(path))) / "footprints"   # (footprints made for this board: fpgen.py)
    if own.is_dir() and own not in pcb.FP_DIRS:
        pcb.FP_DIRS.insert(0, own)
    import parts
    syms = own.parent / "symbols"                        # (symbols made for this board: symgen.py)
    if syms.is_dir() and syms not in parts.SYM_DIRS:
        parts.SYM_DIRS.insert(0, syms)
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
    """a finding as data: its stage, kind, the parts / pins and the place it names, what to change and why it matters"""
    import re
    import critic
    kind, hint = "other", "read the text"
    for k, rx, h in HINTS:
        if re.search(rx, text):
            kind, hint = k, h
            break
    at = re.search(r"\((-?[\d.]+), (-?[\d.]+)\)", text)
    pins = sorted(set(re.findall(r"\b([A-Z][A-Z0-9_]*\d+[A-Z]?\.[A-Za-z0-9]+)\b", text)))
    out = {"stage": stage, "kind": kind, "text": text, "pins": pins, "hint": hint, "why": critic.WHY.get(kind, "")}
    if at:
        out["at"] = [float(at.group(1)), float(at.group(2))]
    return out


FREEROUTING_URL = "https://github.com/freerouting/freerouting/releases/download/v2.0.1/freerouting-2.0.1.jar"
FREEROUTING_SHA256 = "d7fd0f63f52e6d74b0fad6715f87ca9f0ffd7109d66b2a584638000270592ecf"


REPO = "wilsonchenghy/prometheus-pcb-harness"


def installed() -> tuple:
    """how this copy of the harness is installed and the commit it is at: ("clone", its folder, sha) for a git clone,
    ("pip", None, sha) for an install from GitHub (sha None when pip did not record one)"""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    if (root / ".git").exists():
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        return "clone", root, sha.strip() or None
    import importlib.metadata
    import json
    du = json.loads(importlib.metadata.distribution("prometheus-pcb").read_text("direct_url.json") or "{}")
    return "pip", None, (du.get("vcs_info") or {}).get("commit_id")


def latest() -> str:
    """the newest commit on GitHub's main"""
    import json
    import urllib.request
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/commits/main", timeout=5) as r:
        return json.loads(r.read())["sha"]


def update_notice() -> None:
    """once a day, a line on stderr when GitHub has a newer harness than this one (``prometheus update`` takes it)"""
    import json
    from vendor_parts import CACHE
    f = CACHE / "update_check.json"
    try:
        seen = json.loads(f.read_text()) if f.exists() else {}
        if time.time() - seen.get("at", 0) > 86400:
            seen = {"at": time.time(), "latest": latest()}
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(seen))
        have = installed()[2]
    except (OSError, ValueError, KeyError):             # (offline, or GitHub's rate limit: no notice, the run goes on)
        return
    if have and seen.get("latest") and have != seen["latest"]:
        print(f"Prometheus: a newer version is on GitHub ({have[:7]} -> {seen['latest'][:7]}): prometheus update",
              file=sys.stderr)


def update() -> int:
    """the newest harness from GitHub: git pull for a clone, pip's reinstall otherwise; the Claude Code skill too"""
    import shutil
    import subprocess
    kind, root, before = installed()
    if kind == "clone":
        subprocess.run(["git", "-C", str(root), "pull", "--ff-only"], check=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-e", str(root)], check=True)
        skill = root / ".claude" / "skills" / "prometheus" / "SKILL.md"
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall", "--no-deps",
                        f"prometheus-pcb @ git+https://github.com/{REPO}"], check=True)
        skill = None
    dest = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "skills" / "prometheus" / "SKILL.md"
    if dest.exists():                                    # (the skill, where install.sh --skill put it)
        if skill is not None:
            shutil.copyfile(skill, dest)
        else:
            import urllib.request
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/main/.claude/skills/prometheus/SKILL.md",
                                        timeout=30) as r:
                dest.write_bytes(r.read())
    after = installed()[2] if kind == "clone" else latest()
    print(f"Prometheus updated: {(before or 'unknown')[:7]} -> {after[:7]}" if before != after
          else f"Prometheus is up to date ({after[:7]})")
    return 0


def doctor() -> dict:
    """what this machine gives the harness: Python packages, KiCad (its DRC), Java and FreeRouting (the router),
    and what the harness falls back to without each"""
    import importlib.util
    import shutil
    import pcb
    import freerouting
    ver = pcb.kicad_version()
    major = int(ver.split(".")[0]) if ver and ver.split(".")[0].isdigit() else 0
    java = freerouting.find_java()
    old = shutil.which("java")                           # (the PATH's, when it is too old for FreeRouting)
    jar = freerouting.find_jar()
    rows = {
        "kicad": {"ok": major >= 9, "have": ver or "not installed",
                  "gives": "KiCad's own DRC on every run; FreeRouting through KiCad's Specctra export",
                  "without": "no routing (or --router harness) and no KiCad DRC"},
        "kicad_python": {"ok": freerouting.kicad_available(), "have": "pcbnew importable" if freerouting.kicad_available()
                         else "no", "gives": "KiCad writes FreeRouting's input and reads its result back",
                         "without": "no routing (or --router harness)"},
        "java": {"ok": bool(java), "have": f"Java {java[1]}: {java[0]}" if java else
                 (f"only Java {freerouting.java_version(old)} ({old}): FreeRouting needs 17+" if old else "not installed"),
                 "gives": "runs FreeRouting (any Java 17+ found, the default one may stay older; or set PROMETHEUS_JAVA)",
                 "without": "no routing (or --router harness, the built-in grid router)"},
        "freerouting": {"ok": bool(jar), "have": jar or "not found", "gives": "the router KiCad users use",
                        "without": "no routing (or --router harness: fine for simple boards, not 0.4 mm pitch chips)"},
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


STATE = "board_state.pkl"


def design_key(b) -> str:
    """the design the script describes (parts, nets, pours, holes, the parts it fixed in place): a saved board state
    is reused only for this same design"""
    import hashlib
    import json
    d = {"comps": {r: [c["fp"]] + ([] if c.get("unplaced") else [c["at"], c["rot"], c.get("side", "F")])
                   for r, c in sorted(b.comps.items())},
         "nets": b.nets, "pours": b.pours, "holes": b.holes, "outline": b.outline.wkt}
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def save_state(b, out, key):
    """the placed and routed board, so a later --stage check / export / critic starts from it"""
    import pickle
    st = {"key": key, "place": {r: {k: c[k] for k in ("at", "rot", "side") if k in c} for r, c in b.comps.items()},
          "tracks": b.tracks, "vias": b.vias, "fan": getattr(b, "_fan", set()),
          "router": getattr(b, "router_used", None)}
    with open(os.path.join(out, STATE), "wb") as f:
        pickle.dump(st, f)


def load_state(b, out, key) -> bool:
    import pickle
    p = os.path.join(out, STATE)
    if not os.path.exists(p):
        return False
    with open(p, "rb") as f:
        st = pickle.load(f)
    if st.get("key") != key:
        print(f"note: {p} is from another version of the design: not reused")
        return False
    for r, c in st["place"].items():
        if r in b.comps:
            b.comps[r].update(c)
            b.comps[r].pop("unplaced", None)
    b.tracks, b.vias, b._fan = st["tracks"], st["vias"], st["fan"]
    if st.get("router"):
        b.router_used = st["router"] + " (saved)"
    return True


def _repeatable(argv) -> None:
    """the same board for the same input: Python orders sets of names by a per-process hash, and the router's
    choices follow some of those orders, so a run started without PYTHONHASHSEED starts again with it fixed"""
    if os.environ.get("PYTHONHASHSEED") is None and not os.environ.get("PCB_NO_REEXEC"):
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, "-m", "pcb_cli", *argv])


def main(argv=None) -> int:
    if argv is None:                                     # (the command line, not a call from Python)
        _repeatable(sys.argv[1:])
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["doctor"]:
        return report()
    if argv[:1] == ["setup"]:
        return setup()
    if argv[:1] == ["update"]:
        return update()
    if not os.environ.get("CI"):                         # (a newer harness on GitHub: said once a day)
        update_notice()
    if argv[:1] == ["part"]:                             # (a part's symbol: pins, datasheet, footprint, the check)
        import parts
        if len(argv) < 2:
            print("usage: prometheus part Lib:Symbol   e.g. prometheus part Driver_Motor:DRV8833PWP", file=sys.stderr)
            return 2
        try:
            print(parts.describe(argv[1]))
        except parts.SymbolNotFound as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0
    if argv[:1] == ["footprint"]:
        import fpgen
        return fpgen.main(argv[1:])
    if argv[:1] == ["symbol"]:                           # (a symbol from a datasheet's pin table: symgen.py)
        import symgen
        return symgen.main(argv[1:])
    if argv[:1] == ["import"]:                           # (a KiCad layout as a board script, for a revision)
        import kicad_import
        return kicad_import.main(argv[1:])
    if argv[:1] == ["view"]:
        import pcb_view
        return pcb_view.main(argv[1:])
    import fab
    import pcb
    ap = argparse.ArgumentParser(prog="python -m pcb_cli", description=__doc__.split("\n\n")[0])
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--boards", type=int, default=5, help="how many boards to buy parts for (default 5)")
    ap.add_argument("--rules", default=None, help="fab preset for the house check (default: the board's, else jlcpcb-2)")
    ap.add_argument("--step", action="store_true", help="also write the assembled board as STEP (needs build123d)")
    ap.add_argument("--size", help="a netlist's board size, WxH mm (e.g. 50x40)")
    ap.add_argument("--layers", type=int, default=2, help="a netlist's board: 2 or 4 layers (default 2)")
    ap.add_argument("--at", action="append", metavar="REF=X,Y[,ROT[,SIDE]]",
                    help="a netlist's part fixed in place, e.g. --at J1=4,12.5,90 (repeat for each)")
    ap.add_argument("--amps", action="append", metavar="NET=A", help="a netlist's net current, e.g. --amps VBUS=2")
    ap.add_argument("--kicad-drc", choices=("auto", "off", "require"), default="auto",
                    help="KiCad's own DRC on the exported board: auto (when KiCad 9+ is installed; default), off, "
                         "or require (fail when KiCad is missing)")
    ap.add_argument("--router", choices=("freerouting", "harness"), default=None,
                    help="freerouting (FreeRouting through KiCad: needs KiCad, Java and the jar, and stops saying "
                         "which is missing) or harness (the built-in grid router). Default: FreeRouting when they are "
                         "installed, else the built-in router, and the run says so")
    ap.add_argument("--stage", choices=("schematic", "critic", "place", "route", "check", "export", "all"), default="all",
                    help="run one stage: schematic (the schematic, shown in the viewer, then stop: for the engineer's "
                         "go before any layout), critic (the design review, instant), place (place what has no position), "
                         "route (place and route, then the checks), check (the checks on the board as the script "
                         "left it), export (the order files and KiCad's DRC, no routing), all (default)")
    ap.add_argument("--spec", help="a requirements file (JSON: size_mm, layers, house, nets, pairs, near, edge, holes, "
                                   "assembly, max_parts) the critic checks the board against")
    ap.add_argument("--json", action="store_true", help="print the result as JSON (for an agent) instead of text")
    ap.add_argument("--datasheets", action="store_true", help="also read each symbol part's datasheet and check its "
                    "pins against the datasheet's pin table (fetched once, then cached)")
    ap.add_argument("--no-lcsc", action="store_true", help="do not look up LCSC numbers for parts without one "
                    "(JLCPCB's catalogue; also off when CI is set)")
    ap.add_argument("--no-view", action="store_true", help="do not open the viewer (also off when CI is set)")
    a = ap.parse_args(argv)
    if a.json:                                           # (the progress lines go to stderr, the JSON alone to stdout)
        real_stdout, sys.stdout = sys.stdout, sys.stderr
    t0 = time.perf_counter()
    import json
    import critic
    spec = critic.load_spec(a.spec) if a.spec else {}
    if a.size and "size_mm" not in spec:                 # (a size given on the command line is the user's: kept)
        spec["size_mm"] = [float(v) for v in a.size.lower().split("x")]
    os.makedirs(a.out, exist_ok=True)
    import pcb_view
    prog = pcb_view.Progress(a.out, a.script)
    pcb.PROGRESS = prog.event
    if not a.no_view and not os.environ.get("CI"):     # (the board in a browser, live, without holding the run)
        print(f"viewer: {pcb_view.launch(a.out)}", file=sys.stderr, flush=True)
    try:
        b, probs = load_netlist(a.script, a) if a.script.endswith(".net") else load(a.script)
    except pcb.FootprintNotFound as e:                   # (a mistyped footprint: one clear line, not a traceback)
        print(f"error: {e}", file=sys.stderr)
        if a.json:
            sys.stdout = real_stdout
            print(json.dumps({"clean": False, "error": "footprint", "footprint": e.name,
                              "suggestions": e.suggestions, "text": str(e)}, indent=1))
        return 2
    for note in getattr(b, "import_notes", []):
        print("note:", note)
    print(f"{b.name}: {len(b.comps)} parts, {len(b.nets)} nets, {len(b.layers)} layers  [stage: {a.stage}]")
    prog.d["parts"] = len(b.comps)
    if a.router is None:                                 # (the core works without the extras: said, not hidden)
        import freerouting
        a.router = "freerouting" if freerouting.available() and freerouting.kicad_available() else "harness"
        if a.router == "harness":
            b.skip("FreeRouting", "KiCad or Java is not installed: routed with the built-in router",
                   "install them for FreeRouting (docs/setup.md); dense 0.4 mm pitch chips need it")
    viewing = not a.no_view and not os.environ.get("CI")
    stage = a.stage
    if viewing or stage == "schematic":                  # (the schematic and the parts at once: the engineer reviews
        import kicad_sch                                 #  them while the layout is still being worked out)
        sch = kicad_sch.write(b, os.path.join(a.out, "kicad"))
        prog.snapshot(b, os.path.abspath(a.script))
    if stage == "schematic":                             # (and stops there, for the engineer's go)
        prog.finish(True)
        print(f"schematic: {sch}")
        if a.json:
            sys.stdout = real_stdout
            print(json.dumps({"stage": "schematic", "schematic": sch}, indent=1))
        return 0
    if spec.get("near"):                                 # (the placer holds these parts within their distance)
        b.near = list(getattr(b, "near", [])) + [tuple(x) for x in spec["near"]]
    key = design_key(b)
    if stage in ("critic", "check", "export") and not b.tracks and load_state(b, a.out, key):
        print(f"from the last run in {a.out}: {sum(1 for c in b.comps.values())} parts placed, "
              f"{len(b.tracks)} tracks, {len(b.vias)} vias ({b.router_used})")
    route_ok = stage in ("route", "all")
    timings = {}
    if stage in ("place", "route", "all") and any(c.get("unplaced") for c in b.comps.values()):
        t = time.perf_counter()
        prog.stage("place+route" if route_ok else "place")
        if route_ok:
            try:
                probs = pcb.place_and_route(b, router=a.router)
            except RuntimeError as e:                    # (a router that cannot run: one clear line, what to install)
                print(f"error: {e}", file=sys.stderr)
                return 2
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
        prog.stage("route")
        try:
            probs = pcb.route_board(b, a.router)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        timings["route"] = round(time.perf_counter() - t, 2)
        print(f"routed ({b.router_used}): {'clean' if not probs else f'{len(probs)} problems'} ({timings['route']} s)")
    probs = probs or []
    if b.tracks and ("route" in timings or "place+route" in timings):
        n_ = b.tidy()                                    # (dead vias the routing left: KiCad warns of them)
        if n_:
            print(f"tidied: {n_} via{'s' if n_ > 1 else ''} that joined nothing")
    if stage in ("place", "route", "all") and timings:
        save_state(b, a.out, key)
        if viewing:                                      # (the placed and routed board, before the checks and export)
            prog.snapshot(b, os.path.abspath(a.script))
    lcsc_auto = {}
    if stage in ("export", "all") and not a.no_lcsc and not os.environ.get("CI"):   # (JLCPCB assembly: a C-number each)
        import parts
        parts.JLC_DOWN = ""
        try:
            lcsc_auto = parts.fill_lcsc(b, a.boards)
        except OSError as e:
            parts.JLC_DOWN = str(e)
        if parts.JLC_DOWN:                               # (JLCPCB's search unreachable: the numbers not looked up)
            b.skip("LCSC numbers (JLCPCB assembly)", f"JLCPCB's search could not be reached ({parts.JLC_DOWN})",
                   "run again online, or give lcsc= on the parts")
        if lcsc_auto:
            print(f"LCSC numbers found for {len(lcsc_auto)} parts: " + ", ".join(f"{r} {n}" for r, n in lcsc_auto.items()))
    t = time.perf_counter()
    prog.stage("critic")
    notes = critic.review(b, spec)
    if a.datasheets:                                     # (the manufacturer's pin table, for each symbol part)
        import parts
        notes += parts.datasheet_notes(b)
        notes.sort(key=lambda n: {"error": 0, "warning": 1, "info": 2}[n["severity"]])
    timings["critic"] = round(time.perf_counter() - t, 3)
    crit_err = [n for n in notes if n["severity"] == "error"]
    chk, fchk, kchk, schk, kicad, files = [], [], [], [], {"run": False}, {}
    routed = bool(b.tracks) or stage in ("check", "export")
    if stage in ("place", "critic"):
        chk = place                                       # (no copper yet: the placement is what can be checked)
    else:
        t = time.perf_counter()
        prog.stage("check")
        chk = b.check()
        fchk = fab.fab_check(b, a.rules or spec.get("house"))
        timings["check"] = round(time.perf_counter() - t, 2)
    if stage in ("export", "all"):
        t = time.perf_counter()
        prog.stage("export")
        files = fab.order(b, a.out, boards=a.boards)
        kpaths = pcb.export_kicad(b, os.path.join(a.out, "kicad"))
        try:                                             # (KiCad 8's format too: what Altium's KiCad import reads)
            import shutil
            pcb.export_kicad8(os.path.join(a.out, "kicad"), os.path.join(a.out, "kicad8"))
            files["kicad8"] = shutil.make_archive(os.path.join(a.out, "kicad8"), "zip", os.path.join(a.out, "kicad8"))
        except Exception as e:
            b.skip("the KiCad 8 copy (for Altium)", f"{type(e).__name__}: {str(e)[:200]}")
        if a.kicad_drc != "off":                        # a third, independent judge: KiCad's own DRC
            ver = pcb.kicad_version()
            major = int(ver.split(".")[0]) if ver and ver.split(".")[0].isdigit() else 0
            if major >= 9:
                prog.stage("kicad drc")
                d = pcb.kicad_drc(kpaths[0])
                kchk = d["errors"] + d["unconnected"]
                kicad = {"run": True, "version": ver, "warnings": d["warnings"]}
                sch = [k for k in kpaths if k.endswith(".kicad_sch")]
                if sch:                                  # (the schematic, read back by KiCad: its nets are the board's)
                    import kicad_sch
                    try:
                        schk = kicad_sch.check(b, sch[0])
                    except RuntimeError as e:
                        schk = [str(e)]
                else:
                    schk = ["the KiCad schematic was not written (see the line above)"]
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
                ("house check", fchk)] + ([("KiCad DRC", kchk)] if kicad["run"] or kchk else []) + (
                [("schematic", schk)] if kicad["run"] else [])
    if stage in ("place", "critic"):
        sections = [s_ for s_ in sections if s_[0] != "house check"]
    if not b.tracks and not probs:                       # (nothing routed yet: no routing to call clean)
        sections = [s_ for s_ in sections if s_[0] != "routing"]
    for name, ps in sections:
        print(f"{name}: {'clean' if not ps else ''}")
        for p in ps[:20]:
            print("  ", p)
        if len(ps) > 20:
            print(f"   ... and {len(ps) - 20} more")
    print("critic: " + ("no notes" if not notes else ""))
    for n in notes[:20]:
        print(f"   [{n['severity']}] {n['text']}")
    for s_ in getattr(b, "skipped", []):
        print(f"skipped: {s_['what']}: {s_['why']}" + (f" ({s_['how']})" if s_["how"] else ""))
    if files:
        print(f"wrote {a.out}: review {files['report']}, parts {files['html']}")
    print(f"({time.perf_counter() - t0:.1f} s: " + ", ".join(f"{k} {v} s" for k, v in timings.items()) + ")")
    clean = not (probs or chk or fchk or kchk or schk or crit_err) and (routed or stage in ("place", "critic"))
    result = {"board": b.name, "script": os.path.abspath(a.script), "stage": stage, "clean": clean, "kicad_drc": kicad,
              "router": getattr(b, "router_used", "none (routed by the script)"),
              "skipped": getattr(b, "skipped", []),      # (what this run left out, why, and how to add it)
              "environment": {k: r["ok"] for k, r in doctor().items()},   # (what this machine had: docs/setup.md)
              "parts": len(b.comps), "nets": len(b.nets), "layers": len(b.layers),
              "seconds": round(time.perf_counter() - t0, 1), "timings": timings,
              "placement": {r: [c["at"][0], c["at"][1], c["rot"], c.get("side", "F")] for r, c in b.comps.items()},
              "findings": [finding(st, p) for st, ps in (("routing", probs), ("design", chk), ("house", fchk),
                                                          ("kicad", kchk), ("schematic", schk))
                           for p in ps],
              "critic": notes, "lcsc_auto": lcsc_auto,
              "files": {k: str(v) for k, v in files.items()}}
    with open(os.path.join(a.out, "result.json"), "w") as f:
        json.dump(result, f, indent=1)
    try:                                                 # (the page's board, so the viewer opens without rebuilding it)
        pcb_view.write_board(b, a.out, os.path.abspath(a.script))
    except Exception as e:
        b.skip("the viewer's board.json", f"{type(e).__name__}: {str(e)[:200]}", "prometheus view out/ rebuilds it")
    if not a.no_view and not os.environ.get("CI"):       # (KiCad's 3D model ready before the page asks for it)
        for f in (pcb_view._model, pcb_view._schematic):
            try:
                f(pcb_view.Path(a.out).resolve(), a.script)
            except Exception:
                pass
    prog.finish(result["clean"])
    pcb.PROGRESS = None
    if a.json:
        sys.stdout = real_stdout
        print(json.dumps(result, indent=1))
    return 0 if result["clean"] else 1

if __name__ == "__main__":
    sys.exit(main())
