"""Random designs through the whole flow, looking for crashes (not for clean boards: a random netlist may be
unroutable, and saying so is the right answer). Each case: a random board (size, round or rectangular, 2 layers),
a random mix of real footprints, random nets between their pins with random currents, a ground pour, sometimes a
fixed connector, a mounting hole, a pair or a requirement; then place_and_route, check, the critic, the house check
and every export.

    python bench/fuzz.py 20            # 20 cases, seeds 0..19
    python bench/fuzz.py 5 --seed 100  # 5 cases from seed 100
    python bench/fuzz.py 20 --kicad    # and KiCad's DRC on each: a board the harness calls clean that KiCad does
                                       # not is a disagreement, printed with KiCad's findings (exit 1)
"""
import math
import os
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import numpy as np  # noqa: E402

import critic  # noqa: E402
import fab  # noqa: E402
import pcb  # noqa: E402

PARTS = [("R", "Resistor_SMD:R_0603_1608Metric", 2), ("R", "Resistor_SMD:R_0402_1005Metric", 2),
         ("C", "Capacitor_SMD:C_0603_1608Metric", 2), ("C", "Capacitor_SMD:C_0805_2012Metric", 2),
         ("D", "LED_SMD:LED_0603_1608Metric", 2), ("U", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", 8),
         ("U", "Package_TO_SOT_SMD:SOT-23-5", 5), ("U", "Package_TO_SOT_SMD:SOT-23", 3),
         ("U", "Package_SO:TSSOP-14_4.4x5mm_P0.65mm", 14), ("U", "Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm", 21),
         ("Q", "Package_TO_SOT_SMD:SOT-23", 3), ("SW", "Button_Switch_SMD:SW_SPST_TL3342", 4)]
CONN = ("J", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", 4)


def make(seed: int):
    rng = np.random.default_rng(seed)
    w, h = float(rng.uniform(25, 60)), float(rng.uniform(20, 45))
    if rng.random() < 0.2:                               # a round board
        r = min(w, h) / 2
        out = [(r * math.cos(2 * math.pi * k / 64), r * math.sin(2 * math.pi * k / 64)) for k in range(64)]
        cx, cy, w, h = 0.0, 0.0, 2 * r, 2 * r
    else:
        out = [(0, 0), (w, 0), (w, h), (0, h)]
        cx, cy = w / 2, h / 2
    b = pcb.Board(f"fuzz{seed}", out, rules="jlcpcb-2")
    pins = []
    if rng.random() < 0.7:                               # a connector placed by hand, at the edge
        b.place("J1", CONN[1], (cx - w / 2 + 3.0, cy), 0, "conn")
        pins += [("J1", str(i + 1)) for i in range(CONN[2])]
    if rng.random() < 0.5 and min(w, h) > 26:
        b.hole((cx + w / 2 - 4.0, cy + h / 2 - 4.0) if len(out) == 4 else (cx, cy), 2.7)
    count = {}
    for _ in range(int(rng.integers(3, 14))):
        pre, fp, n = PARTS[int(rng.integers(len(PARTS)))]
        count[pre] = count.get(pre, 0) + 1
        ref = f"{pre}{count[pre]}"
        b.place(ref, fp, None, 0, "x")
        pins += [(ref, str(i + 1)) for i in range(n)]
    order = list(rng.permutation(len(pins)))
    pins = [pins[i] for i in order]
    k, nets = 0, 0
    gnd = pins[:max(2, len(pins) // 6)]
    b.net("GND", gnd, amps=float(rng.choice([0.1, 0.5, 2.0])))
    k = len(gnd)
    while k + 1 < len(pins):
        m = int(rng.integers(2, 5))
        grp = pins[k:k + m]
        if len(grp) >= 2:
            nets += 1
            b.net(f"N{nets}", grp, amps=float(rng.choice([0.05, 0.1, 0.1, 0.5, 1.5])))
        k += m
    b.pour("GND", "B", out)
    spec = {"size_mm": [round(w, 1), round(h, 1)], "layers": 2}
    if nets >= 2 and rng.random() < 0.3:
        b.pair("N1", "N2", 2.0)
        spec["pairs"] = [["N1", "N2", 2.0]]
    caps = [r for r in b.comps if r.startswith("C")]
    ics = [r for r in b.comps if r.startswith("U")]
    if caps and ics and rng.random() < 0.5:
        b.near = [(caps[0], ics[0], 4.0)]
        spec["near"] = [[caps[0], ics[0], 4.0]]
    return b, spec


def case(seed: int, router: str, kicad: bool = False) -> dict:
    b, spec = make(seed)
    t = time.perf_counter()
    out = tempfile.mkdtemp()
    probs = pcb.place_and_route(b, router=router, tries=2, workers=1)
    chk = b.check()
    notes = critic.review(b, spec)
    fchk = fab.fab_check(b, "jlcpcb-2")
    fab.order(b, os.path.join(out, "order"))
    paths = pcb.export_kicad(b, os.path.join(out, "kicad"))
    r = {"seed": seed, "parts": len(b.comps), "nets": len(b.nets), "s": round(time.perf_counter() - t, 1),
         "clean": not probs and not chk, "problems": len(probs), "check": len(chk), "fab": len(fchk),
         "critic": len(notes)}
    if kicad:                                            # (the same board judged by KiCad's own DRC)
        d = pcb.kicad_drc(next(p_ for p_ in paths if p_.endswith(".kicad_pcb")))
        r["kicad"] = d["errors"] + d["unconnected"]
        r["disagree"] = r["clean"] and not fchk and bool(r["kicad"])
    return r


def main(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 10
    s0 = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 0
    router = argv[argv.index("--router") + 1] if "--router" in argv else "freerouting"
    kicad = "--kicad" in argv
    crashed = disagree = 0
    for seed in range(s0, s0 + n):
        try:
            r = case(seed, router, kicad)
            print(f"seed {seed}: {r['parts']} parts, {r['nets']} nets, {r['s']} s, "
                  f"{'clean' if r['clean'] else 'not clean'} ({r['problems']} routing, {r['check']} check, "
                  f"{r['fab']} house, {r['critic']} critic"
                  + (f", {len(r['kicad'])} KiCad DRC" if kicad else "") + ")", flush=True)
            if r.get("disagree"):
                disagree += 1
                print("  DISAGREE: clean to the harness, not to KiCad:", *r["kicad"][:8], sep="\n    ", flush=True)
        except Exception:
            crashed += 1
            print(f"seed {seed}: CRASH\n" + traceback.format_exc(), flush=True)
    print(f"{n} cases, {crashed} crashed" + (f", {disagree} where KiCad's DRC disagreed" if kicad else ""))
    return 1 if crashed or disagree else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
