"""The arm power distribution board's schematic, two A3 sheets as an engineer would draw them:
  1/2  power distribution - pack input and its protection as one group, the five fused motor feeds as a channel
       column off the 49 V bus, design notes;
  2/2  the 24 V buck in TI's application-circuit form (input caps, high / low FET, switch node, inductor, output caps,
       the controller beside it with its compensation) and the two fused GL40 feeds as their own column.
Frames and title blocks, ground and power-port symbols, off-sheet connectors between the sheets. Both sheets are
checked together against the board's netlist (schem.check_sheets) before they are saved."""

import datetime
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")]
import schemdraw.elements as elm
from schem import Sheet, check_sheets, save_sheets

TERM2 = {"1": "start", "2": "end"}
W, H = 46.0, 32.5                       # an A3 sheet (1 : 1.414), drawing units
BOARD = "PIONEER-ARM-PDB  (cad_bench/projects/arm_pdb.py)"


def conn(s, ref, pin2, label, label_dx=2.6, above=False):
    """a 2-pin connector, + (pin 2) above - (pin 1), pins on its left; its + pin at ``pin2``"""
    pins = [elm.IcPin(name="-", pin="1", side="left"), elm.IcPin(name="+", pin="2", side="left")]
    e = elm.Ic(pins=pins, size=(1.6, 1.2), pinspacing=0.6, edgepadW=0.3).right().anchor("pin2").at(pin2)
    s.add(ref, e, {"1": "pin1", "2": "pin2"})
    if above:
        s.text((pin2[0] + 0.3, pin2[1] + 1.3), label, size=7, halign="left")
    else:
        s.text((pin2[0] + label_dx, pin2[1] - 0.3), label, size=8, halign="left")
    return e


def leg(s, ref, e, x, y, label=None, pins=TERM2, gnd=True, length=2.0):
    """a two-terminal part from (x, y) down, its foot to ground"""
    s.add(ref, e.at((x, y)).down().length(length), pins, label=label, loc="bottom")
    if gnd:
        s.gnd(e.absanchors["end"])
    return e


def notes(s, x, y, lines, size=7):
    s.text((x, y), "NOTES", size=8, halign="left")
    for i, t in enumerate(lines):
        s.text((x, y - 0.55 * (i + 1)), t, size=size, halign="left")


def sheet1(b, date):
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "POWER DISTRIBUTION", "1 / 2", BOARD, date=date,
            note="drawn by the harness; checked against the layout netlist")
    # ---- pack input and protection -------------------------------------------------------------------------------
    y = 26.0
    s.text((2.0, 29.6), "PACK INPUT  -  12S Li-ion, 36.0-49.0 V (BMS OVP 50.4 V, drivers 52 V abs. max)", size=9,
           halign="left")
    j1 = elm.Ic(pins=[elm.IcPin(name="-", pin="1", side="right"), elm.IcPin(name="+", pin="2", side="right")],
                size=(1.6, 1.2), pinspacing=0.6, edgepadW=0.3).right().anchor("pin2").at((4.0, y))
    s.add("J1", j1, {"1": "pin1", "2": "pin2"})
    s.text((1.4, y + 1.2), "J1  XT90PW-M", size=8, halign="left")
    s.wire(j1.absanchors["pin1"], (4.6, j1.absanchors["pin1"][1]))
    s.gnd((4.6, j1.absanchors["pin1"][1]))
    xbus = 24.0
    s.wire((4.0, y), (xbus, y))
    s.text((5.0, y + 0.3), "VBAT", size=8, halign="left")
    taps = [6.5, 9.0, 11.5, 14.0, 16.5, 19.0, 21.5]
    leg(s, "D1", elm.DiodeTVS(), taps[0], y, "D1\n5.0SMDJ54A\n5 kW TVS")
    leg(s, "C1", elm.Capacitor(polar=True), taps[1], y, "C1\n1200u\n63V")
    leg(s, "C2", elm.Capacitor(polar=True), taps[2], y, "C2\n1200u\n63V")
    leg(s, "C3", elm.Capacitor(), taps[3], y, "C3\n1u\n100V")
    leg(s, "C4", elm.Capacitor(), taps[4], y, "C4\n1u\n100V")
    leg(s, "R1", elm.Resistor(), taps[5], y, "R1\n22k\nbleed")
    r2 = leg(s, "R2", elm.Resistor(), taps[6], y, "R2\n22k", gnd=False, length=1.8)
    leg(s, "D2", elm.LED(), taps[6], r2.absanchors["end"][1], "D2 red\nBUS LIVE", pins={"2": "start", "1": "end"},
        length=1.8)
    for x in taps:
        s.dot((x, y))
    # ---- motor feeds: one channel per row off the bus ---------------------------------------------------------------
    feeds = [("F1", "J2", "SHOULDER 1   CubeMars AK10-9 V3.0   (peak 31.9 A)"),
             ("F2", "J3", "SHOULDER 2   CubeMars AK10-9 V3.0   (peak 31.9 A)"),
             ("F3", "J4", "ELBOW 3   CubeMars AK80-9 V3.0   (peak 28 A)"),
             ("F4", "J5", "ELBOW 4   CubeMars AK80-9 V3.0   (peak 28 A)"),
             ("F5", "J6", "ELBOW 5   CubeMars AK80-9 V3.0   (peak 28 A)")]
    s.text((xbus + 0.6, 29.6), "MOTOR FEEDS  -  49 V, one 40 A fuse per motor", size=9, halign="left")
    rows = [24.0 - 3.4 * i for i in range(len(feeds))]
    s.wire((xbus, y), (xbus, rows[-1] - 2.4))
    for (f, j, label), yr in zip(feeds, rows):
        s.dot((xbus, yr))
        fe = s.add(f, elm.Fuse().at((xbus, yr)).right().length(2.6), TERM2, label=f"{f}  40A", loc="top")
        s.wire(fe.absanchors["end"], (xbus + 4.0, yr))
        jc = conn(s, j, (xbus + 4.0, yr), f"{j}  XT30PW-F\n{label}")
        p1 = jc.absanchors["pin1"]
        s.wire(p1, (p1[0] - 0.6, p1[1]))
        s.gnd((p1[0] - 0.6, p1[1]))
    s.offpage((xbus, rows[-1] - 2.4), "VBAT", side="right", note="to sheet 2: 24 V converter input")
    notes(s, 1.5, 15.5, [
        "1.  Every part on VBAT is rated >= 63 V.",
        "2.  F1-F5: Eaton CB61F40A-TR1, 40 A, 63 VDC, 500 A DC breaking. The pack's",
        "     Class T fuse backs them up for a bolted short (prospective ~1 kA).",
        "3.  AMASS connectors: pin 2 = +, as their footprints mark it. Motor and pack",
        "     sides female / male as AMASS intends.",
        "4.  Copper (IPC-2221, 2 oz, <= 20 C rise): VBAT band and its ground 60 A;",
        "     each motor branch its motor's peak current (32 A) on both layers.",
        "5.  D1 clamps disconnect spikes only. Slow overvoltage is the brake",
        "     chopper's job (on at 49.8 V).",
        "6.  R1 + R2/D2 bleed the bus below 5 V in about a minute; D2 lit = bus live.",
    ])
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


def sheet2(b, date):
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "24 V CONVERTER + GL40 FEEDS", "2 / 2", BOARD, date=date,
            note="TI LM5145 application circuit 3, unchanged")
    s.text((2.0, 29.6), "24 V BUCK  -  TI LM5145 (SNVSAI4B, application circuit 3: 48 V -> 24 V, 440 kHz, 5 A cont.)",
           size=9, halign="left")
    xq, y0 = 22.0, 21.0                          # Q1 drain
    q1 = elm.NFet().right().reverse().anchor("drain").at((xq, y0))
    s.add("Q1", q1, {"3": "drain", "1": "source", "2": "gate"}, label="Q1\nCSD19534Q5A", loc="right")
    yb = y0 + 2.6                                # the converter's input bus, over the controller
    s.wire((xq, y0), (xq, yb))
    s.wire((xq, yb), (xq + 6.5, yb))
    s.offpage((xq + 6.5, yb), "VBAT", side="right", note="from sheet 1")
    s.dot((xq, yb))
    for i, x in enumerate((xq + 2.0, xq + 3.5, xq + 5.0)):
        s.dot((x, yb))
        leg(s, f"C{10 + i}", elm.Capacitor(), x, yb)
    s.text((xq + 1.6, yb - 2.9), "C10-C12  4u7 100V X7S 1210", size=7, halign="left")
    g1 = q1.absanchors["gate"]
    pins = [("ILIM", "11", "right"), ("LO", "13", "right"), ("PGND", "12", "right"), ("VCC", "14", "right"),
            ("SW", "19", "right"), ("BST", "17", "right"), ("HO", "18", "right"),
            ("AGND", "6", "left"), ("FB", "5", "left"), ("COMP", "4", "left"), ("SYNCIN", "8", "left"), ("SS", "3", "left"),
            ("RT", "2", "left"), ("EN", "1", "top"), ("VIN", "20", "top"), ("EP", "21", "bot")]
    u1 = elm.Ic(pins=[elm.IcPin(name=n, pin=p, side=sd) for n, p, sd in pins], size=(4.0, 7.6), pinspacing=1.0,
                label="U1\nLM5145RGY", lsize=11, plblsize=9, lofst=0.22).right().anchor("pin18").at((xq - 6.0, g1[1]))
    s.add("U1", u1, {p: f"pin{p}" for _, p, _ in pins})
    a = lambda p: u1.absanchors[f"pin{p}"]
    ysw = a("19")[1]
    q2 = elm.NFet().right().reverse().anchor("gate").at((g1[0], a("13")[1]))
    s.add("Q2", q2, {"3": "drain", "1": "source", "2": "gate"}, label="Q2\nCSD19534Q5A", loc="right")
    s.wire(q1.absanchors["source"], q2.absanchors["drain"])
    s.gnd(q2.absanchors["source"])
    s.wire(a("18"), g1)
    s.wire(a("13"), q2.absanchors["gate"])
    s.wire(a("19"), (xq, ysw))
    s.dot((xq, ysw))
    s.netname((xq, ysw), "SW")
    l1 = s.add("L1", elm.Inductor2().at((xq, ysw)).right().length(3.0), TERM2, label="L1 15u XAL1010")
    v24 = l1.absanchors["end"]
    # bootstrap, VCC, power ground, current limit
    xb = a("17")[0] + 1.6
    s.wire(a("17"), (xb, a("17")[1]))
    s.add("C31", elm.Capacitor().at((xb, a("17")[1])).down().length(1.0), TERM2)
    s.text((xb + 0.3, a("17")[1] - 0.5), "C31 100n", size=7, halign="left")
    s.dot((xb, ysw))
    xv = a("14")[0] + 1.2
    s.wire(a("14"), (xv, a("14")[1]))
    s.add("C30", elm.Capacitor().at((xv, a("14")[1])).down().length(1.0), TERM2)
    s.text((xv + 0.3, a("14")[1] - 0.5), "C30 2u2", size=7, halign="left")
    s.wire(a("12"), (xv, a("12")[1]))
    s.gnd((a("12")[0] + 0.5, a("12")[1]))
    s.flag((xv, a("14")[1]), "VCC")
    s.wire(a("11"), (a("11")[0] + 1.0, a("11")[1]))
    r30 = s.add("R30", elm.Resistor().at((a("11")[0] + 1.0, a("11")[1])).right().length(2.2), TERM2, label="R30 499")
    s.flag(r30.absanchors["end"], "SW")
    c33 = s.add("C33", elm.Capacitor().at((a("11")[0] + 0.6, a("11")[1])).down().length(1.2), TERM2)
    s.text((a("11")[0] + 0.9, a("11")[1] - 0.7), "C33 10p", size=7, halign="left")
    s.dot((a("11")[0] + 0.6, a("11")[1]))
    s.gnd(c33.absanchors["end"])
    s.gnd(a("21"), lead=0.4)
    # top: VIN (decoupled), EN divider (UVLO on 28.3 V)
    vt = (a("20")[0], a("20")[1] + 1.0)
    s.wire(a("20"), vt, (vt[0], vt[1] + 0.8))
    s.port((vt[0], vt[1] + 0.8), "VBAT")
    c32 = s.add("C32", elm.Capacitor().at(vt).right().length(1.4), TERM2)
    s.dot(vt)
    s.gnd(c32.absanchors["end"])
    s.text((vt[0] + 0.2, vt[1] + 0.5), "C32 100n", size=7, halign="left")
    et = (a("1")[0], a("1")[1] + 1.0)
    s.wire(a("1"), et)
    r31 = s.add("R31", elm.Resistor().at(et).up().length(1.6), {"2": "start", "1": "end"})
    s.text((et[0] - 0.3, et[1] + 0.8), "R31 200k", size=7, halign="right")
    s.port(r31.absanchors["end"], "VBAT")
    r32 = s.add("R32", elm.Resistor().at(et).left().length(1.8), TERM2)
    s.text((et[0] - 0.9, et[1] - 0.6), "R32 8k87", size=7, halign="center")
    s.dot(et)
    s.gnd(r32.absanchors["end"])
    # left: RT, SS, SYNCIN (forced PWM), compensation (type III), feedback (23.8 V), AGND
    r33 = s.add("R33", elm.Resistor().at(a("2")).left().length(2.0), TERM2, label="R33 22k6")
    s.gnd(r33.absanchors["end"])
    c34 = s.add("C34", elm.Capacitor().at(a("3")).left().length(2.0), TERM2, label="C34 47n")
    s.gnd(c34.absanchors["end"])
    s.flag(a("8"), "VCC", side="left")
    s.wire(a("6"), (a("6")[0] - 0.6, a("6")[1]))
    s.gnd((a("6")[0] - 0.6, a("6")[1]))
    yc, yf = a("4")[1], a("5")[1]
    xa = a("4")[0] - 1.0
    s.wire(a("4"), (xa, yc))
    s.wire(a("5"), (xa, yf))
    s.add("C36", elm.Capacitor().at((xa, yc)).down().length(yc - yf), TERM2)
    s.text((xa + 0.2, (yc + yf) / 2), "C36\n18p", size=6, halign="left")
    s.dot((xa, yc))
    s.dot((xa, yf))
    r34 = s.add("R34", elm.Resistor().at((xa, yc)).left().length(2.0), TERM2, label="R34 41k2")
    c35 = s.add("C35", elm.Capacitor().at(r34.absanchors["end"]).left().length(1.6), TERM2, label="C35 1n")
    xc = c35.absanchors["end"][0]
    s.wire(c35.absanchors["end"], (xc, yf))
    s.wire((xa, yf), (xc - 1.4, yf))
    s.dot((xc, yf))
    r36 = s.add("R36", elm.Resistor().at((xc, yf)).down().length(1.6), TERM2)
    s.text((xc + 0.2, yf - 0.8), "R36 2k49", size=7, halign="left")
    s.gnd(r36.absanchors["end"])
    xg = xc - 1.4
    r35 = s.add("R35", elm.Resistor().at((xg, yf)).left().length(2.0), {"2": "start", "1": "end"}, label="R35 71k5")
    s.flag(r35.absanchors["end"], "V24", side="left")
    s.dot((xg, yf))
    r37 = s.add("R37", elm.Resistor().at((xg, yf)).down().length(1.6), {"2": "start", "1": "end"})
    s.text((xg + 0.3, yf - 0.8), "R37 1k", size=7, halign="left")
    c37 = s.add("C37", elm.Capacitor().at(r37.absanchors["end"]).left().length(1.6), {"2": "start", "1": "end"},
                label="C37 390p", loc="bottom")
    s.flag(c37.absanchors["end"], "V24", side="left")
    # 24 V rail: output caps, clamp, LED, then the GL40 feed column
    yv = v24[1]
    for i in range(7):
        x = v24[0] + 0.7 + 1.2 * i
        s.dot((x, yv))
        leg(s, f"C{20 + i}", elm.Capacitor(), x, yv)
    s.text((v24[0] + 0.5, yv - 2.9), "C20-C26  10u 50V X7R 1210", size=7, halign="left")
    s.netname(v24, "V24", dx=0.3, dy=0.35)
    s.text((v24[0] + 1.6, yv + 0.35), "23.8 V", size=7, halign="left")
    xd = v24[0] + 9.6
    s.dot((xd, yv))
    leg(s, "D3", elm.DiodeTVS().reverse(), xd, yv, "D3\nSMBJ28A")
    s.dot((xd + 1.8, yv))
    r3 = leg(s, "R3", elm.Resistor(), xd + 1.8, yv, "R3\n10k", gnd=False, length=1.6)
    leg(s, "D4", elm.LED(), xd + 1.8, r3.absanchors["end"][1], "D4 green\n24V ON", pins={"2": "start", "1": "end"},
        length=1.6)
    xcol = xd + 4.4
    s.wire(v24, (xcol, yv))
    s.text((xcol - 1.0, yv + 4.2), "GL40 FEEDS  -  24 V, 10 A fused", size=9, halign="left")
    rows = [yv, yv - 4.2]
    s.wire((xcol, yv), (xcol, rows[-1]))
    for (f, j, label), yr in zip((("F6", "J7", "WRIST 6  GL40 II"), ("F7", "J8", "GRIPPER 7  GL40 II")), rows):
        fe = s.add(f, elm.Fuse().at((xcol, yr)).right().length(2.6), TERM2, label=f"{f}  10A", loc="top")
        s.wire(fe.absanchors["end"], (xcol + 3.6, yr))
        jc = conn(s, j, (xcol + 3.6, yr), f"{j}  XT30PW-M\n{label}", above=True)
        p1 = jc.absanchors["pin1"]
        s.wire(p1, (p1[0] - 0.6, p1[1]))
        s.gnd((p1[0] - 0.6, p1[1]))
    s.dot((xcol, yv))
    notes(s, 1.5, 8.2, [
        "1.  Values exactly as TI's application circuit 3 (compensation is TI's); RFB1 71k5 (E96) sets 23.8 V.",
        "2.  SYNCIN tied to VCC: forced PWM, so GL40 regen flows back to VBAT instead of raising V24.",
        "3.  5 A continuous; the valley limit (R30 on Q2's RDS(on)) lets ~8-9 A out for peaks.",
        "4.  UVLO: R31 / R32 start the converter at 28.3 V, stop it at 26.3 V.",
        "5.  J7 / J8 are XT30 male: a GL40 lead cannot reach a 49 V socket, nor an AK lead a 24 V one.",
    ])
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


def draw(b, out_dir=None, stem="schematic"):
    """draw both sheets, check them together against the board; save <stem>_1/2.png and .svg"""
    date = datetime.date.today().isoformat()
    sheets = [sheet1(b, date), sheet2(b, date)]
    probs = check_sheets(b, sheets)
    for i, s in enumerate(sheets, 1):
        probs += [f"sheet {i}: {p}" for p in s.layout_problems() + s.dot_problems() + s.label_problems()]
    if out_dir:
        save_sheets(sheets, out_dir, stem)
    return probs


if __name__ == "__main__":
    import runpy
    os.environ.setdefault("ARM_PDB_SOLIDS", "0")
    g = runpy.run_path(os.path.join(os.path.dirname(__file__), "arm_pdb.py"), run_name="prog")
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/arm_sheet"
    os.makedirs(out, exist_ok=True)
    print(draw(g["PCB"], out))
