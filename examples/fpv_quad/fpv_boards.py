"""The 5-inch FPV quadcopter's own boards: a 30.5 mm 4-in-1 ESC and an STM32F405 flight controller.

Taken from the quadcopter's design (its frame, motors and wiring are not part of this example); the board code is as
there. ASSUMED pinouts are from the makers' datasheets as remembered: verify before fab.

    python fpv_boards.py [out_dir]      # writes out/fpv_esc and out/fpv_fc
"""
import math
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")]
from build123d import *
from pcb import Board, route, cached_route, route_jobs, repair
from vendor_parts import kicad

SP_PINS = {"SIG": "13", "S_A": "14", "S_B": "17", "S_C": "20", "S_V": "23", "GND": "18", "GND2": "33",
           "VDD": "34", "VREG": "35", "VM": "36",
           "OUT_A": "48", "LS_A": "47", "HS_A": "46", "BOOT_A": "45", "OUT_B": "44", "LS_B": "43", "HS_B": "42",
           "BOOT_B": "41", "OUT_C": "40", "LS_C": "39", "HS_C": "38", "BOOT_C": "37", "EP": "49"}

CH_MOTOR = {0: 4, 1: 3, 2: 1, 3: 2}     # channel k on side k (k = 0: +y side, 90 deg steps ccw): FL, RL, RR, FR

COLS = (-1.5, 4.0, 9.5)                  # the three half-bridges of a channel along its side

Y_FET, Y_PAD = 16.6, 21.2

UX, UY = 4.0, 5.0                        # the channel's controller

LANE_Y = 13.3                            # where the lanes from the half-bridges drop to the bottom fan

def rot(p, k):
    x, y = p
    a = math.radians(90 * k)
    return (round(x * math.cos(a) - y * math.sin(a), 4), round(x * math.sin(a) + y * math.cos(a), 4))

def rpoly(pts, k):
    return [rot(p, k) for p in pts]

def esc_board():
    HX, HY = 23.0, 23.0                 # the 46 mm square round the stack, a 6 mm battery tab at the rear, 2 mm at the front
    b = Board("esc", [(-29.0, -HY), (HX + 2.0, -HY), (HX + 2.0, HY), (-29.0, HY)], thickness=1.6, copper_oz=3.0,
              clearance=0.15, max_rise_c=40.0, corner_r=3.0)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.hole((sx * 15.25, sy * 15.25), 4.0, keepout=3.4)        # M3 in a soft grommet (4 mm hole)
    FET = "Package_TO_SOT_SMD:LFPAK33"
    C04, c04m = "Capacitor_SMD:C_0402_1005Metric", "Capacitor_SMD.3dshapes/C_0402_1005Metric.step"
    R04, r04m = "Resistor_SMD:R_0402_1005Metric", "Resistor_SMD.3dshapes/R_0402_1005Metric.step"
    PAD25 = "TestPoint:TestPoint_Pad_2.5x2.5mm"
    QFN48 = "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm"
    pours = []
    for k in range(4):
        m = CH_MOTOR[k]
        a = 90 * k
        for X, ph in zip(COLS, "ABC"):
            # low side on top (sources inward onto the top ground), high side under it (drain inward onto the bottom
            # VBAT ring); both drains / sources outward onto the phase copper and the motor pad
            b.place(f"Q{m}{ph}L", FET, rot((X, Y_FET), k), a + 90, "40V 1m6", "Nexperia PSMN1R0-40YLD (LFPAK33)",
                    model="Package_TO_SOT_SMD.3dshapes/LFPAK33.step", note=f"motor {m} phase {ph} low side")
            b.place(f"Q{m}{ph}H", FET, rot((X, Y_FET), k), a + 90, "40V 1m6", "Nexperia PSMN1R0-40YLD (LFPAK33)",
                    model="Package_TO_SOT_SMD.3dshapes/LFPAK33.step", note=f"motor {m} phase {ph} high side", side="B")
            b.place(f"P{m}{ph}", PAD25, rot((X, Y_PAD), k), a, "pad", "motor wire pad", note=f"motor {m} phase {ph}")
            pours.append((f"M{m}{ph}", "F", rpoly([(X - 2.0, 16.05), (X + 2.0, 16.05), (X + 2.0, 24.0), (X - 2.0, 24.0)], k)))
            pours.append((f"M{m}{ph}", "B", rpoly([(X - 2.0, 17.45), (X + 2.0, 17.45), (X + 2.0, 24.0), (X - 2.0, 24.0)], k)))
            for v in ((X - 0.6, 19.2), (X - 1.6, 19.2), (X - 1.65, 18.2), (X - 1.7, 21.2), (X + 1.7, 21.2)):
                b.via(f"M{m}{ph}", rot(v, k), 0.4, 0.7)
        # the channel's controller: STM32F031 + 3-phase gate driver + 12 V / 3.3 V regulators in one package
        b.place(f"U{m}", QFN48, rot((UX, UY), k), a, "STSPIN32F0A", "ST STSPIN32F0ATR",
                model="Package_DFN_QFN.3dshapes/QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm.step", note=f"motor {m} controller")
        for i, ph in enumerate("ABC"):                               # bootstrap caps over their OUT and BOOT vias
            b.place(f"C{m}{ph}B", C04, rot((2.0 + 2.0 * i, 11.15), k), a, "1u 25V", "X7R 0402", model=c04m, note="bootstrap")
        # back-EMF and bus sensing (ST 6-step: phase / 2 against the bus / 2 on the ADC): a 22k / 2k2 divider per phase
        # and one for VM, each a vertical chain under the controller
        for j, ph in enumerate("ABCV"):
            x = 2.5 + 1.5 * j
            b.place(f"RT{m}{ph}", R04, rot((x, 5.85), k), a + 90, "22k", "RC0402FR-0722KL", model=r04m, side="B")
            b.place(f"RB{m}{ph}", R04, rot((x, 3.75), k), a + 90, "2k2", "RC0402FR-072K2L", model=r04m, side="B")
        b.place(f"C{m}R", C04, rot((10.7, 5.5), k), a, "1u 25V", "X7R 0402", model=c04m, note="12 V regulator")
        b.place(f"C{m}D", C04, rot((10.7, 4.1), k), a, "1u", "X7R 0402", model=c04m, note="3.3 V")
        b.place(f"C{m}VM", "Capacitor_SMD:C_0603_1608Metric", rot((9.6, 8.6), k), a + 90, "100n 50V", "X7R 0603",
                model="Capacitor_SMD.3dshapes/C_0603_1608Metric.step", note="VM decoupling", side="B")
        kat, krot = (rot((-8.4, 13.4), k), a + 90) if k != 3 else ((15.5, 6.1), 270)   # (FR's: on its ring piece's end)
        b.place(f"C{m}K", "Capacitor_SMD:C_1210_3225Metric", kat, krot, "10u 50V", "GRM32ER71H106KA12L",
                model="Capacitor_SMD.3dshapes/C_1210_3225Metric.step", note="half-bridge bulk", side="B")
        x0 = -4.9 if k == 3 else -16.4      # (the front-right stays open: the FC signal lines come in there)
        pours.append(("VBAT", "B", rpoly([(x0, 13.85), (16.4, 13.85), (16.4, 17.2), (x0, 17.2)], k)))
    b.place("J1", "TestPoint:TestPoint_Pad_4.0x4.0mm", (-25.8, -6.0), 0, "VBAT", "battery + pad (12 AWG + 1000 uF cap)")
    b.place("J2", "TestPoint:TestPoint_Pad_4.0x4.0mm", (-25.8, -12.6), 0, "GND", "battery - pad")
    b.place("D1", "Diode_SMD:D_SMA", (-24.5, -19.5), 0, "SMAJ26A", "Littelfuse SMAJ26A", model="Diode_SMD.3dshapes/D_SMA.step")
    b.place("J3", "Connector_JST:JST_SH_BM08B-SRSS-TB_1x08-1MP_P1.00mm_Vertical", (22.3, 9.4), 90, "SH 8p",
            "JST BM08B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_BM08B-SRSS-TB_1x08-1MP_P1.00mm_Vertical.step",
            note="to the flight controller")
    pours.append(("VBAT", "B", [(-28.4, -9.3), (-13.85, -9.3), (-13.85, -4.0), (-28.4, -4.0)]))     # the battery feed
    pours.append(("GND", "F", [(-15.85, -15.85), (15.85, -15.85), (15.85, 15.85), (-15.85, 15.85)]))  # the top ground
    pours.append(("GND", "F", [(-28.4, -16.4), (-20.0, -16.4), (-20.0, -11.6), (-15.6, -11.6), (-15.6, -9.8),
                               (-28.4, -9.8)]))                                             # out to the battery pad
    pours.append(("GND", "F", [(15.5, 3.9), (24.6, 3.9), (24.6, 13.6), (15.5, 13.6)]))         # round the FC connector
    b.track("GND", "F", [(23.625, 5.9), (24.2, 4.9)], 0.3, amps=0.1)               # the FC connector's ground pin
    b.via("GND", (24.2, 4.9), 0.3, 0.6)
    for v in ((-26.8, -5.0), (-24.8, -5.0), (-26.8, -7.0), (-24.8, -7.0), (-25.8, -6.0)):
        b.via("VBAT", v, 0.4, 0.7)
    for v in ((-26.8, -11.6), (-24.8, -11.6), (-26.8, -13.6), (-24.8, -13.6), (-21.2, -15.8), (-27.9, -15.8),
              (-23.7, -15.8)):
        b.via("GND", v, 0.4, 0.7)
    from shapely.geometry import Polygon, Point
    from shapely.ops import unary_union
    # the bottom VBAT ring: the four side pieces lose their corners to the grommet keep-outs, so they join round the
    # outside of each hole (an annulus clear of the phase pours and the fan-in)
    ring = [p for p in pours if p[0] == "VBAT" and p[1] == "B"]
    pours = [p for p in pours if not (p[0] == "VBAT" and p[1] == "B")]
    no = unary_union([Polygon(p[2]).buffer(0.3, join_style=2) for p in pours if p[1] == "B" and p[0].startswith("M")] +
                     [Polygon(rpoly([(-4.3, 9.0), (12.4, 9.0), (12.4, 13.75), (-4.3, 13.75)], k)) for k in range(4)])
    ann = unary_union([Point(sx * 15.25, sy * 15.25).buffer(6.2, 48).difference(Point(sx * 15.25, sy * 15.25).buffer(3.75, 48))
                       for sx, sy in ((-1, 1), (-1, -1), (1, -1))])   # (open at the front-right: the FC lines pass)
    vb = unary_union([Polygon(p[2]) for p in ring] + [ann]).difference(no).intersection(
        Polygon([(-28.6, -22.6), (24.6, -22.6), (24.6, 22.6), (-28.6, 22.6)]))
    vb = max(getattr(vb, "geoms", None) or [vb], key=lambda q: q.area)
    pours.append(("VBAT", "B", [tuple(c) for c in list(vb.exterior.coords)[:-1]],
                  [[tuple(c) for c in list(r.coords)[:-1]] for r in vb.interiors]))
    other_b = unary_union([Polygon(p[2], p[3] if len(p) > 3 else None).buffer(0.25, join_style=2) for p in pours if p[1] == "B"])
    g = Polygon([(-29.5, -24.0), (26.0, -24.0), (26.0, 24.0), (-29.5, 24.0)]).difference(other_b)
    for q in (getattr(g, "geoms", None) or [g]):          # (inside the ring, and the corners outside it)
        if q.area > 4.0:
            pours.append(("GND", "B", [tuple(c) for c in list(q.exterior.coords)[:-1]],
                          [[tuple(c) for c in list(r.coords)[:-1]] for r in q.interiors]))
    return b, pours

def esc_nets(b):
    V = 25.2
    P = SP_PINS
    gnd = [("J2", "1"), ("D1", "2"), ("J3", "1")]
    vbat = [("J1", "1"), ("D1", "1"), ("J3", "2")]
    for k, m in CH_MOTOR.items():
        U = f"U{m}"
        for i, ph in enumerate("ABC"):
            H, L = f"Q{m}{ph}H", f"Q{m}{ph}L"
            vbat += [(H, "5")]
            gnd += [(L, "1"), (L, "2"), (L, "3"), (f"RB{m}{ph}", "2")]
            b.net(f"M{m}{ph}", [(H, "1"), (H, "2"), (H, "3"), (L, "5"), (f"P{m}{ph}", "1"), (U, P[f"OUT_{ph}"]),
                                (f"C{m}{ph}B", "1"), (f"RT{m}{ph}", "1")], amps=15.0, volts=V)
            b.net(f"HG{m}{ph}", [(U, P[f"HS_{ph}"]), (H, "4")], amps=0.2, volts=V)
            b.net(f"LG{m}{ph}", [(U, P[f"LS_{ph}"]), (L, "4")], amps=0.2, volts=12.0)
            b.net(f"VB{m}{ph}", [(U, P[f"BOOT_{ph}"]), (f"C{m}{ph}B", "2")], amps=0.1, volts=V)
        for ph in "ABCV":       # (bottom-side 0402s: pad 1 is the upper one)
            b.net(f"S{m}{ph}", [(f"RT{m}{ph}", "2"), (f"RB{m}{ph}", "1"), (U, P[f"S_{ph}"])], amps=0.01, volts=3.3)
        gnd += [(f"RB{m}V", "2")]
        vbat += [(f"RT{m}V", "1")]
        b.net(f"SIG{m}", [(U, P["SIG"]), ("J3", str(4 + m))], amps=0.01, volts=3.3)
        b.net(f"VREG{m}", [(U, P["VREG"]), (f"C{m}R", "1")], amps=0.05, volts=12.0)
        b.net(f"VDD{m}", [(U, P["VDD"]), (f"C{m}D", "1")], amps=0.05, volts=3.3)
        gnd += [(U, P["GND"]), (U, P["GND2"]), (U, P["EP"]), (f"C{m}K", "2"), (f"C{m}R", "2"), (f"C{m}D", "2"),
                (f"C{m}VM", "2")]
        vbat += [(f"C{m}K", "1"), (U, P["VM"]), (f"C{m}VM", "1")]
    b.net("VBAT", vbat, amps=60.0, volts=V)
    b.net("GND", gnd, amps=60.0, volts=V)

def esc_hand(b):
    """the hand-routed copper: lanes out of the half-bridges, the controller's via rows and the fan-in between them"""
    for k, m in CH_MOTOR.items():
        for X, ph in zip(COLS, "ABC"):
            # top lanes in the 1.5 mm gaps between the phase pours: the phase (M, off its via) on the column's left,
            # the high-side gate (through a via behind its bottom pin) on its right; the low-side gate straight in
            b.track(f"M{m}{ph}", "F", [rot(p, k) for p in ((X - 1.6, 19.2), (X - 2.3, 19.2), (X - 2.3, LANE_Y))], 0.15,
                    amps=0.01)
            b.via(f"HG{m}{ph}", rot((X + 0.97, 19.15), k), 0.3, 0.6)
            b.track(f"HG{m}{ph}", "F", [rot(p, k) for p in ((X + 0.97, 19.15), (X + 2.3, 19.15), (X + 2.3, LANE_Y))], 0.15)
            b.track(f"LG{m}{ph}", "F", [rot((X + 0.97, 14.15), k), rot((X + 0.97, LANE_Y), k)], 0.15)
            for nm, x in (("M", X - 2.3), ("HG", X + 2.3), ("LG", X + 0.97)):
                b.via(f"{nm}{m}{ph}", rot((x, LANE_Y), k), 0.3, 0.6)
        # the controller's gate-drive row (12 pins at 0.5 mm, all bound for the bottom): staggered vias off its stubs
        U = f"U{m}"
        for pin in range(37, 49):
            i = 48 - pin
            x = UX - 2.75 + 0.5 * i
            yv = UY + 4.65 if i % 2 == 0 else UY + 5.35
            net = b.pad_net()[(U, str(pin))]
            b.track(net, "F", [rot((x, UY + 4.375), k), rot((x, yv), k)], 0.1, amps=0.01 if net.startswith("M") else None)
            b.via(net, rot((x, yv), k), 0.3, 0.6)
        # the fan-in (bottom, between the via rows and the VBAT ring): nine traces in the same order at both ends;
        # rightward movers step up as they start further right, leftward ones as they start further left: none cross
        fan = [("M", "A", -3.8, 1.25, 10.95), ("LG", "A", -0.53, 1.75, 11.3), ("HG", "A", 0.8, 2.25, 11.65),
               ("M", "B", 1.7, 3.25, 12.0), ("LG", "B", 4.97, 3.75, 12.7), ("HG", "B", 6.3, 4.25, 12.35),
               ("M", "C", 7.2, 5.25, 12.0), ("LG", "C", 10.47, 5.75, 11.65), ("HG", "C", 11.8, 6.25, 11.3)]
        for kind, ph, xs, xt, yl in fan:
            yt = UY + 4.65 if kind != "LG" else UY + 5.35
            b.track(f"{kind}{m}{ph}", "B", [rot(p, k) for p in ((xs, LANE_Y), (xs, yl), (xt, yl), (xt, yt))],
                    0.1, amps=0.01 if kind == "M" else None)
        # regulator caps straight off their pins
        b.track(f"VREG{m}", "F", [rot(p, k) for p in ((8.375, 7.25), (9.55, 7.25), (9.9, 6.1), (10.22, 5.6))], 0.2)
        b.track(f"VDD{m}", "F", [rot(p, k) for p in ((8.375, 6.75), (9.25, 6.75), (9.25, 4.1), (10.1, 4.1))], 0.2)
        for i, ph in enumerate("ABC"):
            xo = 1.25 + 2.0 * i                        # OUT via at xo (lower row), BOOT via at xo + 1.5 (upper row)
            b.track(f"M{m}{ph}", "F", [rot(p, k) for p in ((xo, 9.65), (xo, 10.7), (xo + 0.27, 11.0))], 0.1, amps=0.01)
            b.track(f"VB{m}{ph}", "F", [rot(p, k) for p in ((xo + 1.5, 10.35), (xo + 1.23, 10.9), (xo + 1.23, 11.1))], 0.1)
            xcol = 2.5 + 1.5 * i                       # and down under the controller to its sense divider
            b.track(f"M{m}{ph}", "B", [rot(p, k) for p in ((xo, 9.65), (xo, 7.1), (xcol, 6.4))], 0.1, amps=0.01)
        b.track("VBAT", "B", [rot(p, k) for p in ((7.0, 6.45), (8.0, 7.45), (8.7, 9.0), (9.6, 9.25))], 0.15, amps=0.01)  # bus sense
        b.track(f"SIG{m}", "F", [rot((1.25, 0.625), k), rot((0.95, 0.85), k)], 0.1)   # the signal drops to the bottom
        b.via(f"SIG{m}", rot((0.95, 0.85), k), 0.3, 0.6)                                # in the middle of the board
        for j, ph in enumerate("ABCV"):                # each sense node: via off the controller's pin, up into its chain
            xp, xcol = 1.75 + 1.5 * j, 2.5 + 1.5 * j
            b.track(f"S{m}{ph}", "F", [rot((xp, 0.625), k), rot((xp, 0.45), k)], 0.1)
            b.via(f"S{m}{ph}", rot((xp, 0.45), k), 0.3, 0.6)
            b.track(f"S{m}{ph}", "B", [rot(p, k) for p in ((xp, 0.45), (xp, 4.8), (xcol, 4.8))], 0.1)
            b.track(f"S{m}{ph}", "B", [rot((xcol, 4.25), k), rot((xcol, 5.35), k)], 0.1)
            if ph != "V":                              # the divider's ground: a via up into the controller's pad
                b.via("GND", rot((xcol, 2.55), k), 0.3, 0.6)

def esc_route(b, pours):
    probs = []
    top, bot = {}, {}
    for n, L, p, *h in pours:
        (top if L == "F" else bot).setdefault(n, []).append(p)

    def ko(own=()):
        return {"F": [p for n, ps in top.items() if n not in own and n != "GND" for p in ps],
                "B": [p for n, ps in bot.items() if n not in own and n != "GND" for p in ps]}
    b.corridor = 0.4
    b.fanout(width=0.1)
    esc_hand(b)
    # (RB2V's ground pad sits on a scrap of back-side pour that SIG1, S2C and S2V close in once routed: its via to the
    # front ground goes in first, small as the house allows, and the signals route round it)
    probs += b.drop("GND", [("RB2V", "2")], via=(0.2, 0.5), width=0.1, reach=1.5, amps=0.01)
    for n in ("SIG4", "SIG3", "SIG2", "SIG1"):
        probs += route(b, n, width=0.1, keepout=ko())
    for m in (4, 3, 1, 2):
        for ph in "ABC":
            for nm in ("HG", "LG", "VB"):
                probs += route(b, f"{nm}{m}{ph}", width=0.15, keepout=ko(tuple(f"M{m}{q}" for q in "ABC")))
            probs += route(b, f"M{m}{ph}", width=0.1, amps=0.01, keepout=ko((f"M{m}{ph}",)),
                           only=[(f"U{m}", SP_PINS[f"OUT_{ph}"]), (f"C{m}{ph}B", "1"), (f"RT{m}{ph}", "1")])
        for n in [f"S{m}{ph}" for ph in "ABCV"] + [f"VREG{m}", f"VDD{m}"]:
            probs += route(b, n, width=0.1, keepout=ko())
    probs += route(b, "VBAT", width=0.3, amps=0.3, keepout=ko(("VBAT",)),
                   only=[p for p in b.nets["VBAT"]["pins"] if p[0] in ("D1", "J3") or p[0].startswith("U")
                         or p[0].startswith("RT") or (p[0].startswith("C") and p[0].endswith("VM"))])
    b.stitch("GND", [(-15.85, -15.85), (15.85, -15.85), (15.85, 15.85), (-15.85, 15.85)], pitch=1.0)
    gnd = [p for p in b.nets["GND"]["pins"] if p[0] not in ("J2",) and not p[0].startswith("Q")]
    probs += route(b, "GND", width=0.25, amps=0.3, only=gnd, keepout=ko(("GND",)))
    b.stitch("GND", [(-15.85, -15.85), (15.85, -15.85), (15.85, 15.85), (-15.85, 15.85)], pitch=1.0)
    # (driver 4's logic ground - U4, its sense resistors and J3 - ends on a piece of pour the power copper fences off:
    # joined back by a thin track, as the logic ground it is)
    probs = [p for p in probs if "GND" not in p] + repair(b, width=0.1, nets=["GND"], amps=0.3)
    return probs

def pour_all(b, pours):
    for n, L, p, *h in pours:
        rear = min(x for x, y in p) < -20
        amps = 30.0 if (n in ("VBAT", "GND") and rear) else (10.0 if n.startswith("M") or n == "VBAT" else 2.0)
        b.pour(n, L, p, amps=amps, holes=h[0] if h else None)

def esc_build():
    b, pours = esc_board()
    esc_nets(b)
    pour_all(b, pours)
    rp = esc_route(b, pours)
    b.tidy()                                             # (the via rows' vias no route went on through)
    return b, rp

F4 = {"VBAT": "1", "PC14": "3", "PC15": "4", "OSC_IN": "5", "OSC_OUT": "6", "NRST": "7", "PC2": "10", "VSSA": "12",
      "VDDA": "13", "PA2": "16", "PA3": "17", "VSS1": "18", "VDD1": "19", "PA4": "20", "PA5": "21", "PA6": "22",
      "PA7": "23", "PC4": "24", "PB0": "26", "PB1": "27", "VCAP1": "31", "VDD2": "32", "PC6": "37", "PC7": "38",
      "PA9": "42", "PA10": "43", "PA11": "44", "PA12": "45", "VCAP2": "47", "VDD3": "48", "PA15": "50", "PC10": "51",
      "PC11": "52", "PC12": "53", "BOOT0": "60", "VSS2": "63", "VDD4": "64"}

def fc_board():
    b = Board("fc", [(-18.0, -18.0), (18.0, -18.0), (18.0, 18.0), (-18.0, 18.0)], thickness=1.6, copper_oz=1.0,
              clearance=0.15, max_rise_c=20.0, corner_r=3.0)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.hole((sx * 15.25, sy * 15.25), 4.0, keepout=3.2)
    C04, c04m = "Capacitor_SMD:C_0402_1005Metric", "Capacitor_SMD.3dshapes/C_0402_1005Metric.step"
    R04, r04m = "Resistor_SMD:R_0402_1005Metric", "Resistor_SMD.3dshapes/R_0402_1005Metric.step"
    C06, c06m = "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"
    P = "Package_TO_SOT_SMD.3dshapes/"
    # MCU, gyro, crystal, flash
    b.place("U1", "Package_QFP:LQFP-64_10x10mm_P0.5mm", (0.0, 0.0), 180, "STM32F405RGT6", "ST STM32F405RGT6",
            model="Package_QFP.3dshapes/LQFP-64_10x10mm_P0.5mm.step")
    b.place("U2", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", (1.5, 10.75), 180, "ICM-42688-P", "TDK ICM-42688-P",
            model="Package_LGA.3dshapes/LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y.step", side="B")
    b.place("C20", C04, (-1.5, 11.5), 90, "100n", "X7R 0402", model=c04m, side="B")
    b.place("C21", C04, (4.0, 12.85), 0, "100n", "X7R 0402", model=c04m, side="B")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", (9.5, -1.5), 90, "8 MHz", "Abracon ABM8-8.000MHZ-B2-T",
            model="Crystal.3dshapes/Crystal_SMD_3225-4Pin_3.2x2.5mm.step")
    b.place("C22", C04, (9.5, 1.25), 0, "12p", "C0G 0402", model=c04m)
    b.place("C23", C04, (9.5, -4.25), 0, "12p", "C0G 0402", model=c04m)
    b.place("U3", "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm", (4.5, -11.0), 90, "W25Q128JV", "Winbond W25Q128JVSIQ",
            model="Package_SO.3dshapes/SOIC-8_5.3x5.3mm_P1.27mm.step", side="B", note="blackbox flash")
    b.place("C24", C04, (8.25, -11.0), 90, "100n", "X7R 0402", model=c04m, side="B")
    # MCU decoupling under it (bottom), VCAP, NRST, BOOT0
    for ref, at, rot_, val in (("C1", (2.75, 4.2), 90, "100n"), ("C2", (-4.0, 4.2), 90, "100n"), ("C3", (-4.2, -3.75), 0, "100n"),
                               ("C4", (3.25, -4.2), 90, "100n"), ("C5", (4.2, 2.25), 0, "1u"), ("C6", (-2.5, 4.2), 90, "2u2"),
                               ("C7", (-4.2, -2.25), 0, "2u2"), ("C8", (4.75, -2.75), 0, "100n")):
        b.place(ref, C04, at, rot_, val, "X7R 0402", model=c04m, side="B")
    b.place("R1", R04, (0.0, -7.6), 0, "10k", "RC0402FR-0710KL", model=r04m)           # BOOT0 pull-down
    b.place("SW1", "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2", (4.5, -13.5), 0, "BOOT", "C&K KMR211NGLFS",
            model="Button_Switch_SMD.3dshapes/SW_Push_1P1T_NO_CK_KMR2.step")
    # status LEDs
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (9.0, -8.5), 0, "blue", "Kingbright APT1608QBC/D",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", (9.0, -10.5), 0, "green", "Kingbright APT1608SGC",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step")
    b.place("R2", R04, (11.5, -8.5), 0, "1k", "RC0402FR-071KL", model=r04m)
    b.place("R3", R04, (11.5, -10.5), 0, "1k", "RC0402FR-071KL", model=r04m)
    # battery sense divider to PC2
    b.place("R4", R04, (12.75, -6.6), 0, "10k", "RC0402FR-0710KL", model=r04m)
    b.place("R5", R04, (12.75, -6.6), 0, "1k", "RC0402FR-071KL", model=r04m, side="B")
    # power: VBAT (from the ESC) -> 5 V buck (bottom) -> 3.3 V LDO; USB VBUS -> 5 V through a Schottky
    b.place("U4", "Package_TO_SOT_SMD:SOT-23-6", (8.5, 7.75), 90, "LMR51420", "TI LMR51420YDDCR",
            model=P + "SOT-23-6.step", side="B")
    b.place("L1", "Inductor_SMD:L_Coilcraft_XAL4020-XXX", (8.0, 12.5), 0, "6u8", "Coilcraft XAL4020-682",
            model="Inductor_SMD.3dshapes/L_Coilcraft_XAL4020-XXX.step", side="B")
    b.place("C9", "Capacitor_SMD:C_1206_3216Metric", (11.75, 8.75), 90, "4u7 50V", "GRM31CR71H475KA12L",
            model="Capacitor_SMD.3dshapes/C_1206_3216Metric.step", side="B")
    b.place("C10", C04, (6.25, 8.75), 90, "100n", "X7R 0402", model=c04m, side="B")
    b.place("C11", "Capacitor_SMD:C_0805_2012Metric", (6.5, 16.0), 0, "22u 10V", "GRM21BR61A226ME44L",
            model="Capacitor_SMD.3dshapes/C_0805_2012Metric.step", side="B")
    b.place("R6", R04, (5.5, 6.0), 0, "100k", "RC0402FR-07100KL", model=r04m, side="B")
    b.place("R7", R04, (5.5, 4.75), 0, "13k7", "RC0402FR-0713K7L", model=r04m, side="B")
    b.place("U5", "Package_TO_SOT_SMD:SOT-23-5", (-6.5, 11.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1",
            model=P + "SOT-23-5.step")
    b.place("C12", C06, (-9.5, 11.0), 90, "1u", "X7R 0603", model=c06m)
    b.place("C13", C06, (-3.5, 11.0), 90, "1u", "X7R 0603", model=c06m)
    b.place("D3", "Diode_SMD:D_SOD-323", (-6.0, -8.25), 0, "BAT60A", "Infineon BAT60A", model="Diode_SMD.3dshapes/D_SOD-323.step", side="B")
    # USB-C (data + power for setup), on the right-hand edge
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (-6.0, -14.6), 0, "USB-C",
            "GCT USB4105-GF-A", model="Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step",
            overhang=True)
    b.place("R8", R04, (-10.25, -9.25), 0, "5k1", "RC0402FR-075K1L", model=r04m)
    b.place("R9", R04, (0.25, -9.0), 0, "5k1", "RC0402FR-075K1L", model=r04m)
    # connectors: ESC (bottom, facing the 4-in-1 below), receiver and VTX
    b.place("J2", "Connector_JST:JST_SH_SM08B-SRSS-TB_1x08-1MP_P1.00mm_Horizontal", (14.6, 0.0), 90, "SH 8p",
            "JST SM08B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM08B-SRSS-TB_1x08-1MP_P1.00mm_Horizontal.step",
            note="to the 4-in-1 ESC (mouth forward, the harness loops down to the ESC)")
    b.place("J3", "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", (-14.7, -6.5), 270, "SH 4p",
            "JST SM04B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal.step",
            note="ELRS receiver")
    b.place("J4", "Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal", (-14.6, 6.0), 270, "SH 6p",
            "JST SM06B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal.step",
            note="HD VTX (power, MSP), mouth rearward")
    return b

def fc_nets(b):
    U = "U1"
    n = lambda name, pins, amps=0.01, volts=3.3: b.net(name, pins, amps=amps, volts=volts)
    n("GND", [(U, F4["VSS1"]), (U, F4["VSS2"]), (U, F4["VSSA"]), ("U2", "6"), ("C20", "2"), ("C21", "2"), ("C22", "2"),
              ("C23", "2"), ("U3", "4"), ("C24", "2")] + [(c, "2") for c in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")]
      + [("R1", "2"), ("R5", "2"), ("U4", "2"), ("C9", "2"), ("C11", "2"), ("R7", "2"), ("U5", "2"), ("C12", "2"),
         ("C13", "2"), ("J1", "A1"), ("J1", "B12"), ("J1", "A12"), ("J1", "B1"), ("R8", "2"), ("R9", "2"), ("J2", "1"),
         ("J3", "1"), ("J4", "2"), ("J4", "5"), ("D1", "1"), ("D2", "1")], amps=1.5, volts=25.0)
    n("3V3", [(U, F4["VBAT"]), (U, F4["VDD1"]), (U, F4["VDD2"]), (U, F4["VDD3"]), (U, F4["VDD4"]), (U, F4["VDDA"]),
              ("U2", "5"), ("U2", "8"), ("C20", "1"), ("C21", "1"), ("U3", "8"), ("U3", "3"), ("U3", "7"), ("C24", "1"),
              ("C1", "1"), ("C2", "1"), ("C3", "1"), ("C4", "1"), ("C5", "1"), ("C8", "1"), ("U5", "5"), ("C13", "1"),
              ("SW1", "1"), ("R3", "1")], amps=0.3)                # (D2 through R3: the 3.3 V power LED)
    n("VBAT", [("J2", "2"), ("U4", "5"), ("U4", "4"), ("C9", "1"), ("R4", "1"), ("J4", "1")], amps=1.5, volts=25.2)
    n("5V", [("L1", "2"), ("C11", "1"), ("R6", "1"), ("U5", "1"), ("U5", "3"), ("C12", "1"), ("J3", "2"), ("D3", "1")],
      amps=1.5, volts=5.0)
    n("VBUS", [("J1", "A4"), ("J1", "B9"), ("J1", "B4"), ("J1", "A9"), ("D3", "2")], amps=0.5, volts=5.0)
    n("SW", [("U4", "6"), ("L1", "1"), ("C10", "2")], amps=1.5, volts=25.2)
    n("CB", [("U4", "1"), ("C10", "1")])
    n("FB", [("U4", "3"), ("R6", "2"), ("R7", "1")])
    n("VCAP1", [(U, F4["VCAP1"]), ("C6", "1")])
    n("VCAP2", [(U, F4["VCAP2"]), ("C7", "1")])
    n("BOOT0", [(U, F4["BOOT0"]), ("R1", "1"), ("SW1", "2")])
    n("OSC_IN", [(U, F4["OSC_IN"]), ("Y1", "1"), ("C22", "1")])
    n("OSC_OUT", [(U, F4["OSC_OUT"]), ("Y1", "3"), ("C23", "1")])
    n("GYRO_SCK", [(U, F4["PA5"]), ("U2", "13")])
    n("GYRO_MISO", [(U, F4["PA6"]), ("U2", "1")])
    n("GYRO_MOSI", [(U, F4["PA7"]), ("U2", "14")])
    n("GYRO_CS", [(U, F4["PA4"]), ("U2", "12")])
    n("GYRO_INT", [(U, F4["PC4"]), ("U2", "4")])
    n("FL_SCK", [(U, F4["PC10"]), ("U3", "6")])
    n("FL_MISO", [(U, F4["PC11"]), ("U3", "2")])
    n("FL_MOSI", [(U, F4["PC12"]), ("U3", "5")])
    n("FL_CS", [(U, F4["PA15"]), ("U3", "1")])
    n("USB_DM", [(U, F4["PA11"]), ("J1", "A7"), ("J1", "B7")])
    n("USB_DP", [(U, F4["PA12"]), ("J1", "A6"), ("J1", "B6")])
    n("CC1", [("J1", "A5"), ("R8", "1")])
    n("CC2", [("J1", "B5"), ("R9", "1")])
    n("VSENSE", [(U, F4["PC2"]), ("R4", "2"), ("R5", "1")])
    n("LED1", [(U, F4["PC14"]), ("R2", "1")])
    n("LED1K", [("R2", "2"), ("D1", "2")])
    n("LED2K", [("R3", "2"), ("D2", "2")])
    n("M1", [(U, F4["PB0"]), ("J2", "5")])
    n("M2", [(U, F4["PB1"]), ("J2", "6")])
    n("M3", [(U, F4["PA3"]), ("J2", "7")])
    n("M4", [(U, F4["PA2"]), ("J2", "8")])
    n("RX_TX", [(U, F4["PA9"]), ("J3", "4")])        # UART1: the ELRS receiver (CRSF)
    n("RX_RX", [(U, F4["PA10"]), ("J3", "3")])
    n("VTX_TX", [(U, F4["PC6"]), ("J4", "4")])       # UART6: the HD VTX (MSP DisplayPort OSD)
    n("VTX_RX", [(U, F4["PC7"]), ("J4", "3")])

def fc_route(b):
    probs = []
    b.corridor = 0.4
    b.fanout(width=0.1)
    b.via("GND", (10.4, 1.25), 0.3, 0.6)                      # the crystal's load caps to the ground plane
    b.via("GND", (10.4, -4.25), 0.3, 0.6)
    order = ["GYRO_SCK", "GYRO_MISO", "GYRO_MOSI", "GYRO_CS", "GYRO_INT", "M1", "M2", "M3", "M4", "OSC_IN", "OSC_OUT", "USB_DM", "USB_DP",
             "FL_SCK", "FL_MISO", "FL_MOSI", "FL_CS", "VCAP1", "VCAP2", "RX_TX", "RX_RX",
             "VTX_TX", "VTX_RX", "VSENSE", "LED1", "LED1K", "LED2K", "BOOT0", "CC1", "CC2", "CB", "FB"]
    # negotiated passes (pcb.route_jobs): a net that fails is routed first on the next pass, the best pass kept
    jobs = [(nm, 0.1, None) for nm in order[:9]] + [("3V3", 0.2, None)] + [(nm, 0.1, None) for nm in order[9:]] + \
        [("SW", 0.5, None), ("VBUS", 0.3, None),
         ("VBAT", 0.4, [p for p in b.nets["VBAT"]["pins"][1:] if p[0] != "R4"]),
         ("VBAT", 0.2, [("R4", "1")], {"amps": 0.01}),               # (the battery sense divider: no current)
         ("5V", 0.4, None), ("GND", 0.2, None, {"amps": 0.3})]
    probs += route_jobs(b, jobs, passes=4)
    b.stitch("GND", [(-17.5, -17.5), (17.5, -17.5), (17.5, 17.5), (-17.5, 17.5)], pitch=1.5)
    return probs

def fc_build():
    b = fc_board()
    fc_nets(b)
    b.pour("GND", "B", [(-18.5, -18.5), (18.5, -18.5), (18.5, 18.5), (-18.5, 18.5)], amps=1.5)
    b.pour("GND", "F", [(-18.5, -18.5), (18.5, -18.5), (18.5, 18.5), (-18.5, 18.5)], amps=1.5)
    rp = fc_route(b)
    b.tidy()
    return b, rp



if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from export import export
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    for name, make in (("fpv_esc", esc_build), ("fpv_fc", fc_build)):
        b, probs = make()
        export(b, probs, os.path.join(base, name))
