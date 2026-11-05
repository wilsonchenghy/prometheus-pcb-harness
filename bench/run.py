"""The benchmark: every example board (and the simple ones in bench/) built, routed (or loaded from its committed
routes), checked, checked against a fab preset, plotted, and exported to Gerbers, the KiCad project and the assembly
files, each stage timed. KiCad itself is not needed (its DRC is the examples' own export step).

    python bench/run.py                 # all boards
    python bench/run.py blinker cube    # some (led_ring, power_bank, motor_driver, can_node and rp2040 are placed
                                        # and routed from scratch against bench/specs/; rp2040 takes minutes)
    python bench/run.py --json out.json

A board passes when it routes with no problems, Board.check() is clean and fab.fab_check() is clean.
"""
import json
import os
import runpy
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "examples")]
os.environ.setdefault("ARM_PDB_SOLIDS", "0")
os.environ.setdefault("TWILIGHT_SOLIDS", "0")

import fab  # noqa: E402
import pcb  # noqa: E402


def _ex(path, **kw):
    return runpy.run_path(os.path.join(ROOT, "examples", path), run_name="bench", **kw)


def _blinker():
    g = runpy.run_path(os.path.join(HERE, "blinker.py"), run_name="bench")
    b = g["build"]()
    probs = []
    for n in ("VCC", "DIS", "THR", "OUT", "LED", "GND"):
        probs += pcb.route(b, n, width=0.3)
    return [(b, probs, "jlcpcb-2")]


def _usb_ldo():
    g = runpy.run_path(os.path.join(HERE, "usb_ldo.py"), run_name="bench")
    b = g["build"]()
    return [(b, g["do_route"](b), "jlcpcb-2")]


def _esp32():
    g = runpy.run_path(os.path.join(HERE, "esp32_sensor.py"), run_name="bench")
    b = g["build"]()
    return [(b, g["do_route"](b), "jlcpcb-2")]


def _controller():
    g = _ex("game_controller/controller_pcb.py")
    j = json.load(open(os.path.join(ROOT, "examples", "game_controller", "controller_board.json")))
    keep = [([tuple(p) for p in pts], why) for pts, why in j["keepouts"]]
    b, probs = g["routed"]([tuple(p) for p in j["outline"]], j["at"], [tuple(h) for h in j["holes"]], keep)
    return [(b, probs, "jlcpcb-2")]


def _fpv():
    g = _ex("fpv_quad/fpv_boards.py")
    # (0.1 mm tracks off 0.4-0.5 mm pitch parts: a 4 mil house, not JLCPCB's 2-layer 5 mil)
    return [(*g["esc_build"](), "pcbway-std"), (*g["fc_build"](), "pcbway-std")]


def _placed(script, fixed, rules="jlcpcb-2"):
    """a bench board with its parts' positions thrown away (all but the ones the enclosure fixes) and placed and
    routed from scratch by pcb.place_and_route: the whole flow from a parts list"""
    def go():
        g = runpy.run_path(os.path.join(HERE, script), run_name="bench")
        b = g["build"]()
        b.name += "_placed"
        if rules and b.rules is None:                # (the house's drill gap and clearance from the start)
            b.rules, b.hole_gap = rules, max(b.hole_gap, fab.RULES[rules]["hole_gap"])
            b.clear = max(b.clear, fab.RULES[rules]["space"])
        move = [r for r in b.comps if r not in fixed]
        return [(b, pcb.place_and_route(b, move=move), rules)]
    return go


def _specced(script):
    """a bench board placed and routed the way the command line does it (``place_and_route``: FreeRouting through
    KiCad, which this needs), against its requirements file in ``bench/specs/`` when there is one: the critic's errors
    count as problems"""
    def go():
        import critic
        g = runpy.run_path(os.path.join(HERE, script), run_name="bench")
        b = g["build"]()
        sp = os.path.join(HERE, "specs", script.replace(".py", ".json"))
        spec = critic.load_spec(sp) if os.path.exists(sp) else {}
        b.near = [tuple(x) for x in spec.get("near", [])]
        probs = pcb.place_and_route(b)
        probs += [n["text"] for n in critic.review(b, spec) if n["severity"] == "error"]
        return [(b, probs, spec.get("house") or b.rules or "jlcpcb-2")]
    return go


BOARDS = {
    "blinker": _blinker,
    "usb_ldo": _usb_ldo,
    "esp32_sensor": _esp32,
    "arm_pdb": lambda: [(*(lambda g: (g["PCB"], g["PCB_ROUTE_PROBLEMS"]))(_ex("arm_pdb/arm_pdb.py")), "jlcpcb-2")],
    "twilight": lambda: [(*(lambda g: (g["PCB"], g["PCB_ROUTE_PROBLEMS"]))(_ex("twilight_fc/twilight_fc.py")), "jlcpcb-4")],
    "cube": lambda: [(*_ex("balancing_cube/cube_board.py")["routed"](
        store=os.path.join(ROOT, "examples", "balancing_cube", "routes")), "jlcpcb-2")],
    "ripple": lambda: [(*_ex("social_wheelbot/ripple_pcb.py")["build"](), "jlcpcb-2")],
    "qdd": lambda: [(*_ex("quadruped/qdd_power_board.py")["build"](), "jlcpcb-2")],
    "fpv": _fpv,
    "controller": _controller,
    "blinker_placed": _placed("blinker.py", {"J1"}),
    "usb_ldo_placed": _placed("usb_ldo.py", {"J1", "J2"}),
    "esp32_placed": _placed("esp32_sensor.py", {"U1", "J1", "J2", "SW1", "SW2"}),
    "led_ring": _specced("led_ring.py"),
    "power_bank": _specced("power_bank.py"),
    "motor_driver": _specced("motor_driver.py"),
    "can_node": _specced("can_node.py"),
    "rp2040": _specced("rp2040_board.py"),
}


def run(name):
    rows = []
    t0 = time.perf_counter()
    boards = BOARDS[name]()
    t_build = time.perf_counter() - t0
    for b, probs, rules in boards:
        r = {"board": b.name, "set": name, "build+route s": round(t_build / len(boards), 2), "route problems": len(probs)}
        out = tempfile.mkdtemp()
        for stage, f in (("check", lambda: b.check()), ("fab_check", lambda: fab.fab_check(b, rules)),
                         ("plot", lambda: b.plot(os.path.join(out, "layers.png"))),
                         ("gerbers", lambda: pcb.export_gerbers(b, os.path.join(out, "gerbers"))),
                         ("kicad", lambda: pcb.export_kicad(b, os.path.join(out, "kicad"))),
                         ("assembly+cart", lambda: (fab.cpl(b, os.path.join(out, "cpl.csv")),
                                                    fab.jlc_bom(b, os.path.join(out, "bom.csv")),
                                                    fab.cart(b, os.path.join(out, "cart"))))):
            t = time.perf_counter()
            res = f()
            r[f"{stage} s"] = round(time.perf_counter() - t, 2)
            if stage in ("check", "fab_check"):
                r[stage] = len(res)
                r[f"{stage} first"] = res[:2]
        r["parts"] = len(b.comps)
        r["nets"] = len(b.nets)
        r["layers"] = len(b.layers)
        r["pass"] = r["route problems"] == 0 and r["check"] == 0 and r["fab_check"] == 0
        rows.append(r)
        print(json.dumps(r), flush=True)
    return rows


def main(argv):
    out_json = None
    if "--json" in argv:
        i = argv.index("--json")
        out_json = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    names = argv or list(BOARDS)
    rows = []
    for n in names:
        try:
            rows += run(n)
        except Exception as e:                            # a board that will not build is a result too
            rows.append({"set": n, "error": f"{type(e).__name__}: {str(e)[:200]}", "pass": False})
            print(json.dumps(rows[-1]), flush=True)
    print(f"\n{'board':20s} {'parts':>5s} {'L':>2s} {'build':>7s} {'check':>6s} {'gerb':>6s} {'kicad':>6s}  result")
    for r in rows:
        if "error" in r:
            print(f"{r['set']:20s} error: {r['error']}")
            continue
        res = "PASS" if r["pass"] else f"route {r['route problems']} / check {r['check']} / fab {r['fab_check']}"
        print(f"{r['board']:20s} {r['parts']:5d} {r['layers']:2d} {r['build+route s']:7.1f} {r['check s']:6.1f} "
              f"{r['gerbers s']:6.1f} {r['kicad s']:6.1f}  {res}")
    if out_json:
        json.dump(rows, open(out_json, "w"), indent=1)
    return rows


if __name__ == "__main__":
    main(sys.argv[1:])
