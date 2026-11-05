# DESIGN SPEC: Pioneer humanoid - power distribution board for ONE arm (6 DOF + gripper, 7 motors).
# Board frame: mm, y up, origin at the board centre; 2 layers, 2 oz copper, 1.6 mm FR4.
#
# Motors (the humanoid project site, "6 DOF Arm + 1 DOF Gripper - Motor Selection"; CubeMars spec tables):
#   J2, J3  shoulder 1-2  CubeMars AK10-9 V3.0 KV60   48 V, rated 10.7 A / 18 Nm (442 W), peak 31.9 A / 53 Nm
#   J4..J6  elbow 3-4-5   CubeMars AK80-9 V3.0 KV100  48 V, rated 12 A / 9 Nm, peak 28 A / 22 Nm
#   J7, J8  wrist 6, gripper 7  CubeMars GL40 II KV70 (own driver, 24 V class; max 5.22 A each, team's Battery
#           Protection System doc) - fed from an on-board 24 V buck, never from the pack bus
# Bus: the 12S7P EVE 40PL pack (pack design doc): 36.0 V practical LVC .. 49.0 V charge ceiling; regen chopper on at
#   49.8 V; BMS OVP 50.4 V worst case; CubeMars drivers 52 V absolute. Every part on the bus is rated >= 63 V.
#
# Current budget (what each copper path is sized for, at <= 20 C rise by IPC-2221 on 2 oz external copper):
#   AK10-9 branch: 32 A (its peak current) treated as if continuous - its true continuous draw is lower:
#       rated power 442 W / 0.85 / 36 V = 14.4 A at the bottom of the pack
#   AK80-9 branch: 28 A (its peak) as if continuous
#   24 V rail: 9 A (the LM5145 valley current limit with RILIM 499 R and 13 mOhm FETs, ~8 A + ripple / 2)
#   Trunk (XT90 -> fuses, and the ground return): 60 A continuous. All five AK motors at rated power together at
#       36 V is 5 x ~13 A = 66 A; the team's estimate for an arm in use is ~10 A, and the pack doc's coincident peak
#       factor (0.3) on the arm's 154 A sum of peaks is 46 A. Peaks above 60 A last well under a second.
# Fuses (on board, replaceable SMD bricks): Eaton CB61F40A (40 A, 63 VDC, 500 A DC breaking) on each AK feed (the
#   team's 40 A choice; ATO PCB holders were rejected: 30 A max / 22.5 A continuous, 32 V); CB61F10A on each 24 V
#   feed. The 500 A breaking capacity is below the prospective short-circuit current of the pack at the board
#   (~1 kA): the pack's Class T fuse is the backstop for a bolted short - see out/README.md.

import math
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."), os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), os.path.dirname(os.path.abspath(__file__))]
from pcb import Board, route, cached_route
from pcb_bodies import xt30pw_body, xt90pw_body, brick_fuse_body

V_BUS = 49.0
BX, BY = 75.0, 48.0                     # half sizes: 150 x 96 mm
TOP_X = (-26.0, -7.0, 12.0, 31.0)      # top-edge motor connectors (J2..J5), pin 1 x
BOT_X = -20.0                           # bottom-edge motor connector (J6)
PIN_Y = BY - 14.55 + 0.6                # XT30PW pins from the edge: the mating face 0.6 mm proud of it
FUSE_Y = 17.4                           # 2512-size fuse centres: pad 1 on the bus (y 14.5), pad 2 on the branch
BAND = 15.0                             # the bus pour: |y| <= 15 (30 mm: 60 A at < 20 C on 2 oz)
XR = BX - 14.55 + 0.6                   # right-edge 24 V connectors (J7, J8), pin x
XT90_X = -BX + 25.1 - 0.6               # XT90 pins from the left edge

MOTORS = {  # ref: (label, peak A, connector at, side)
    "J2": ("shoulder 1 AK10-9", 32.0, TOP_X[0], "T"), "J3": ("shoulder 2 AK10-9", 32.0, TOP_X[1], "T"),
    "J4": ("elbow 3 AK80-9", 28.0, TOP_X[2], "T"), "J5": ("elbow 4 AK80-9", 28.0, TOP_X[3], "T"),
    "J6": ("elbow 5 AK80-9", 28.0, BOT_X, "B"),
}
R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
r06m, c06m = "Resistor_SMD.3dshapes/R_0603_1608Metric.step", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"
C1210, c1210m = "Capacitor_SMD:C_1210_3225Metric", "Capacitor_SMD.3dshapes/C_1210_3225Metric.step"
FUSE = "Fuse:Fuse_2512_6332Metric"     # KiCad's 2512 land for the 2410 brick (check against Eaton's TD 4377 pads)
XT30F = "Connector_AMASS:AMASS_XT30PW-F_1x02_P2.50mm_Horizontal"
XT30M = "Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal"
FET, fetm = "Package_SON:VSONP-8-1EP_5x6_P1.27mm", "Package_TO_SOT_SMD.3dshapes/TDSON-8-1.step"  # (same 5 x 6 package)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def branch(x, side):
    """a motor feed: fuse pad 2 to the connector's + pin (at x), away from its - pin (8.6 mm wide, both layers)"""
    if side == "T":
        return rect(x - 2.6, FUSE_Y - 0.8, x + 6.0, PIN_Y + 2.5)
    return rect(x - 6.0, -(PIN_Y + 2.5), x + 2.6, -(FUSE_Y - 0.8))


# ---- the 24 V buck (TI LM5145 datasheet SNVSAI4B, application circuit 3: 48 V -> 24 V, 440 kHz) -------------------
QX = 2.0                                # FET column: Q1 (high side) over Q2 (low side), drains up
Q1Y, Q2Y = -25.5, -35.0
U1 = (12.0, -40.0)                      # LM5145 RGY, turned 180: gate drive (LO, HO, BST, SW) faces the FETs
L1 = (13.5, -24.5)                      # Coilcraft XAL1010-153, pad 1 on the switch node right of the FETs
COUT_X = [26.6 + 3.4 * i for i in range(7)]
COUT_Y = -33.5
CIN_X = (-3.4, -7.0, -10.6)
CIN_Y = -22.4
SMALL_X, SMALL_Y = (20.4, 24.6, 28.8), -39.6   # the controller's small parts: three columns, 2.4 mm rows


SW_POUR = [(-0.5, -34.0), (6.0, -34.0), (6.0, -36.4), (11.4, -36.4), (11.4, -19.8), (8.9, -19.8), (8.9, -27.8),
           (-0.5, -27.8)]


def sw_keepout():
    """signals stay off the top of the switch node (it would be cut in two), except at Q1's gate, which drops
    straight through a via to the bottom"""
    from shapely.geometry import Point, Polygon
    g = Polygon(SW_POUR).buffer(0.3, join_style=2).difference(Point(QX + 1.905, Q1Y - 2.8).buffer(1.4))
    return {"F": [list(p.exterior.coords)[:-1] for p in (getattr(g, "geoms", None) or [g])]}


def arm_board():
    global XT30F_BODY, XT30M_BODY, XT90_BODY, FUSE_BODY
    XT30F_BODY, XT30M_BODY, XT90_BODY, FUSE_BODY = xt30pw_body(False), xt30pw_body(True), xt90pw_body(), brick_fuse_body()
    b = Board("arm_pdb", rect(-BX, -BY, BX, BY), thickness=1.6, copper_oz=2.0, clearance=0.2, max_rise_c=20.0,
              corner_r=4.0)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.hole((sx * (BX - 6.0), sy * (BY - 6.0)), 3.2, keepout=3.6)       # M3 to the torso frame
    # power entry: XT90 male (the pack lead is the female, shrouded side), TVS, ceramics, bulk, bleed + live LED
    # AMASS footprints: pin 2 is + (the round pad; the footprint's own silkscreen marks it), pin 1 - (square)
    b.place("J1", "Connector_AMASS:AMASS_XT90PW-M_1x02_P10.90mm_Horizontal", (XT90_X, -5.45), 90, "XT90PW-M",
            "AMASS XT90PW-M", note="pack in, 12S (36-49 V); pin 2 +", overhang=True, body=XT90_BODY)
    b.place("D1", "Diode_SMD:D_SMC", (-33.0, -8.6), 270, "5.0SMDJ54A", "Littelfuse 5.0SMDJ54A",
            model="Diode_SMD.3dshapes/D_SMC.step", note="5 kW TVS: 54 V standoff (> 50.4 V OVP), clamps disconnect spikes")
    for ref, x in (("C3", -27.2), ("C4", -23.6)):
        b.place(ref, C1210, (x, -13.6), 270, "1u 100V", "TDK C3225X7R2A105K", model=c1210m, note="bus HF decoupling")
    for ref, at, rot in (("C1", (-41.0, 19.0), 90), ("C2", (-41.0, -19.0), 270)):
        b.place(ref, "Capacitor_THT:CP_Radial_D12.5mm_P5.00mm", at, rot, "1200u 63V", "Rubycon 63ZLJ1200M12.5X40",
                model="Capacitor_THT.3dshapes/CP_Radial_D12.5mm_P5.00mm.step",
                note="bulk at the arm: the motor leads' inductance (team's choice, 12.5 x 40 mm)")
    b.place("R1", "Resistor_SMD:R_1206_3216Metric", (-37.0, 9.0), 0, "22k", "Yageo RC1206FR-0722KL",
            model="Resistor_SMD.3dshapes/R_1206_3216Metric.step", note="bleed: 2.4 mF to < 5 V in ~2 min")
    b.place("R2", "Resistor_SMD:R_1206_3216Metric", (-38.8, -11.5), 0, "22k", "Yageo RC1206FR-0722KL",
            model="Resistor_SMD.3dshapes/R_1206_3216Metric.step", note="bus-live LED: 2 mA at 49 V, 0.1 W")
    b.place("D2", "LED_SMD:LED_0805_2012Metric", (-38.8, -8.2), 180, "red", "Kingbright APT2012SURCK",
            model="LED_SMD.3dshapes/LED_0805_2012Metric.step", note="BUS LIVE: lit until the caps are below ~5 V")
    # motor feeds: fuse, branch, XT30 female
    for k, (ref, (label, amps, x, side)) in enumerate(MOTORS.items()):
        f = f"F{k + 1}"
        if side == "T":
            b.place(f, FUSE, (x, FUSE_Y), 90, "40A", "Eaton CB61F40A-TR1", body=FUSE_BODY, note=f"{label} feed")
            b.place(ref, XT30F, (x - 5.0, PIN_Y), 0, "XT30PW-F", "AMASS XT30PW-F", note=f"{label}; pin 2 +",
                    overhang=True, body=XT30F_BODY)                                       # (its + pin, pin 2, at x)
        else:
            b.place(f, FUSE, (x, -FUSE_Y), 270, "40A", "Eaton CB61F40A-TR1", body=FUSE_BODY, note=f"{label} feed")
            b.place(ref, XT30F, (x + 5.0, -PIN_Y), 180, "XT30PW-F", "AMASS XT30PW-F", note=f"{label}; pin 2 +",
                    overhang=True, body=XT30F_BODY)
    # 24 V buck power stage
    for ref, y in (("Q1", Q1Y), ("Q2", Q2Y)):
        b.place(ref, FET, (QX, y), 90, "100V 13m", "TI CSD19534Q5A", model=fetm,
                note="high side" if ref == "Q1" else "low side (also the current sense: RDS(on))")
    b.place("L1", "Inductor_SMD:L_Coilcraft_XAL1010-XXX", L1, 0, "15u", "Coilcraft XAL1010-153ME",
            model="Inductor_SMD.3dshapes/L_Coilcraft_XAL1010-XXX.step", note="15 uH, 15.5 A sat, 18.6 mOhm")
    for i, x in enumerate(CIN_X):
        b.place(f"C{10 + i}", C1210, (x, CIN_Y), 270, "4u7 100V", "TDK C3225X7S2A475M", model=c1210m, note="buck input")
    for i, x in enumerate(COUT_X):
        b.place(f"C{20 + i}", C1210, (x, COUT_Y), 270, "10u 50V", "Murata GRM32ER71H106KA12L", model=c1210m,
                note="buck output")
    b.place("U1", "Package_DFN_QFN:Texas_RGY_R-PVQFN-N20_EP2.05x3.05mm", U1, 180, "LM5145", "TI LM5145RGYR",
            model="Package_DFN_QFN.3dshapes/Texas_RGY_R-PVQFN-N20_EP2.05x3.05mm.step", note="24 V buck controller")
    ux, uy = U1
    near = [  # by the gate-drive pins: VCC and BST caps between the FETs and U1, ILIM and VIN parts above / below
        ("C30", C06, (7.6, -40.1), 180, "2u2 25V", "GRM188R61E225KA12D"),       # VCC (pad 2 to the power ground)
        ("C31", C06, (7.6, -42.4), 0, "100n 25V", "GRM188R71E104KA01D"),        # BST - SW
        ("R30", R06, (12.0, -35.6), 180, "499R", "RC0603FR-07499RL"),           # ILIM - SW (RDS(on) sensing)
        ("C33", C06, (15.2, -35.6), 0, "10p 100V", "GRM1885C2A100JA01D"),       # ILIM - PGND
        ("C32", C06, (11.25, -44.6), 0, "100n 100V", "GRM188R72A104KA35D"),     # VIN pin
        ("C34", C06, (16.4, -42.6), 0, "47n", "GRM188R71H473KA61D"),            # SS: ~3.8 ms
        ("R33", R06, (16.4, -44.6), 0, "22k6", "RC0603FR-0722K6L"),             # RT: 440 kHz
        ("R31", R06, (8.2, -46.6), 0, "200k", "RC0603FR-07200KL"),              # UVLO top (on at 28.3 V)
        ("R32", R06, (12.4, -46.6), 0, "8k87", "RC0603FR-078K87L"),             # UVLO bottom
    ]
    grid = [  # ref, kind, value, mpn: two columns right of U1
        ("R35", R06, "71k5", "RC0603FR-0771K5L"),          # RFB1: 0.8 x (1 + 71.5 / 2.49) = 23.8 V
        ("R36", R06, "2k49", "RC0603FR-072K49L"),          # RFB2
        ("C36", C06, "18p", "GRM1885C1H180JA01D"),         # CC2
        ("R34", R06, "41k2", "RC0603FR-0741K2L"),          # RC1
        ("C35", C06, "1n", "GRM1885C1H102JA01D"),          # CC1
        ("C37", C06, "390p", "GRM1885C1H391JA01D"),        # CC3
        ("R37", R06, "1k", "RC0603FR-071KL"),              # RC2
    ]
    small = near + [(ref, fp, (SMALL_X[i % 3], SMALL_Y - 2.4 * (i // 3)), 0, val, mpn)
                    for i, (ref, fp, val, mpn) in enumerate(grid)]
    for ref, fp, at, rot, val, mpn in small:
        b.place(ref, fp, at, rot, val, mpn, model=r06m if fp == R06 else c06m)
    # 24 V outputs: TVS, LED, fuses, XT30 male (a GL40 lead cannot reach a 49 V socket, nor an AK lead a 24 V one)
    b.place("D3", "Diode_SMD:D_SMB", (52.5, -24.0), 0, "SMBJ28A", "Littelfuse SMBJ28A",
            model="Diode_SMD.3dshapes/D_SMB.step", note="24 V rail clamp (31 V min breakdown)")
    b.place("R3", "Resistor_SMD:R_0805_2012Metric", (52.5, -29.0), 0, "10k", "RC0805FR-0710KL",
            model="Resistor_SMD.3dshapes/R_0805_2012Metric.step")
    b.place("D4", "LED_SMD:LED_0805_2012Metric", (56.4, -29.0), 180, "green", "Kingbright APT2012SGC",
            model="LED_SMD.3dshapes/LED_0805_2012Metric.step", note="24 V on")
    for ref, f, y, label in (("J7", "F6", 8.0, "wrist 6 GL40 II"), ("J8", "F7", -12.0, "gripper 7 GL40 II")):
        b.place(f, FUSE, (49.0, y), 0, "10A", "Eaton CB61F10A-TR1", body=FUSE_BODY, note=f"{label} feed")
        b.place(ref, XT30M, (XR, y - 5.0), 270, "XT30PW-M", "AMASS XT30PW-M", note=f"{label}, 24 V; pin 2 +",
                overhang=True, body=XT30M_BODY)                                           # (its + pin, pin 2, at y)
    return b


def arm_nets(b):
    V, V24 = V_BUS, 24.0
    motors = list(MOTORS.items())
    b.net("VBAT", [("J1", "2"), ("D1", "1"), ("C3", "1"), ("C4", "1"), ("C1", "1"), ("C2", "1"), ("R1", "1"),
                   ("R2", "1"), ("U1", "20"), ("C32", "1"), ("R31", "1"), ("Q1", "3")]
          + [(f"F{k + 1}", "1") for k in range(len(motors))] + [(f"C{10 + i}", "1") for i in range(len(CIN_X))],
          amps=60.0, volts=V)
    b.net("GND", [("J1", "1"), ("D1", "2"), ("C3", "2"), ("C4", "2"), ("C1", "2"), ("C2", "2"), ("R1", "2"),
                  ("D2", "1"), ("Q2", "1"), ("U1", "6"), ("U1", "12"), ("U1", "21"), ("C30", "2"),
                  ("C32", "2"), ("C33", "2"), ("R32", "2"), ("R33", "2"), ("C34", "2"), ("R36", "2"), ("D3", "2"),
                  ("D4", "1"), ("J7", "1"), ("J8", "1")]
          + [(ref, "1") for ref, _ in motors] + [(f"C{10 + i}", "2") for i in range(len(CIN_X))]
          + [(f"C{20 + i}", "2") for i in range(len(COUT_X))], amps=60.0, volts=V)
    for k, (ref, (label, amps, x, side)) in enumerate(motors):
        b.net(f"M{k + 1}", [(f"F{k + 1}", "2"), (ref, "2")], amps=amps, volts=V)
    b.net("LED_A", [("R2", "2"), ("D2", "2")], amps=0.003, volts=V)
    b.net("SW", [("Q1", "1"), ("Q2", "3"), ("L1", "1"), ("U1", "19"), ("C31", "2"), ("R30", "2")], amps=9.0, volts=V)
    b.net("HO", [("U1", "18"), ("Q1", "2")], amps=0.5, volts=V)
    b.net("LO", [("U1", "13"), ("Q2", "2")], amps=0.5, volts=8.0)
    b.net("BST", [("U1", "17"), ("C31", "1")], amps=0.5, volts=V)
    b.net("VCC", [("U1", "14"), ("C30", "1"), ("U1", "8")], amps=0.05, volts=8.0)   # SYNCIN high: forced PWM
    b.net("ILIM", [("U1", "11"), ("R30", "1"), ("C33", "1")], amps=0.01, volts=V)
    b.net("EN", [("U1", "1"), ("R31", "2"), ("R32", "1")], amps=0.01, volts=2.0)
    b.net("RT", [("U1", "2"), ("R33", "1")], amps=0.01, volts=1.0)
    b.net("SS", [("U1", "3"), ("C34", "1")], amps=0.01, volts=1.0)
    b.net("COMP", [("U1", "4"), ("R34", "1"), ("C36", "1")], amps=0.01, volts=3.0)
    b.net("CMPRC", [("R34", "2"), ("C35", "1")], amps=0.01, volts=3.0)
    b.net("FB", [("U1", "5"), ("C35", "2"), ("C36", "2"), ("R35", "2"), ("R36", "1"), ("R37", "2")], amps=0.01, volts=1.0)
    b.net("FBC", [("C37", "2"), ("R37", "1")], amps=0.01, volts=V24)
    b.net("V24", [("L1", "2"), ("R35", "1"), ("C37", "1"), ("F6", "1"), ("F7", "1"), ("D3", "1"), ("R3", "1")]
          + [(f"C{20 + i}", "1") for i in range(len(COUT_X))], amps=9.0, volts=V24)
    b.net("LED_B", [("R3", "2"), ("D4", "2")], amps=0.003, volts=V24)
    b.net("W6", [("F6", "2"), ("J7", "2")], amps=10.0, volts=V24)
    b.net("G7", [("F7", "2"), ("J8", "2")], amps=10.0, volts=V24)


def arm_pours(b):
    """the power path in copper pours; the bottom layer is ground except under the motor branches"""
    E = BX - 0.5
    # the bus: a 30 mm band from the XT90 to the last fuse, lobes down to C1 / C2 and the buck's input
    vbat = [(-E, -BAND), (38.0, -BAND), (38.0, BAND), (-E, BAND)]
    b.pour("VBAT", "F", vbat, amps=60.0)
    b.pour("VBAT", "F", rect(-44.0, BAND - 1.0, -38.0, 21.6), amps=3.0)                      # C1 +
    b.pour("VBAT", "F", rect(-44.0, -21.6, -38.0, -BAND + 1.0), amps=3.0)                    # C2 +
    b.pour("VBAT", "F", [(-12.4, -23.3), (-0.9, -23.3), (-0.9, -27.45), (6.0, -27.45), (6.0, -BAND + 1.0),
                         (-12.4, -BAND + 1.0)], amps=9.0 * 24 / 36 / 0.9)                      # buck input
    for k, (ref, (label, amps, x, side)) in enumerate(MOTORS.items()):
        for L in "FB":
            b.pour(f"M{k + 1}", L, branch(x, side), amps=amps)
    # buck: switch node, its power ground, the 24 V rail out to the fuses and the right-edge connectors
    b.pour("SW", "F", SW_POUR, amps=9.0)
    b.pour("GND", "F", rect(-1.2, -42.0, 5.4, -37.3), amps=9.0)                               # Q2 source
    b.pour("GND", "F", rect(-12.2, -28.6, -1.7, -24.0), amps=9.0)                             # input caps
    b.pour("GND", "F", rect(COUT_X[0] - 2.0, -38.2, COUT_X[-1] + 2.0, -34.4), amps=9.0)      # output caps
    b.pour("V24", "F", [(15.4, -32.6), (50.5, -32.6), (50.5, -21.0), (47.0, -21.0), (47.0, 11.0), (40.0, 11.0),
                        (40.0, -18.0), (15.4, -18.0)], amps=9.0)
    for net, y in (("W6", 8.0), ("G7", -12.0)):
        for L in "FB":
            b.pour(net, L, rect(50.6, y - 2.4, E, y + 2.4), amps=10.0)
    b.pour("GND", "F", rect(54.4, -27.0, 58.5, -21.0), amps=1.0)                              # D3 anode
    # ground, by the current each region carries: the trunk return under the bus band (60 A), each motor's return
    # round its connector (one motor's peak, 32 A), the 24 V side; bottom everywhere but the branches, top where free
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    br = [Polygon(branch(x, side)) for (_, _, x, side) in MOTORS.values()]
    v24 = [Polygon(rect(50.6, y - 2.4, E, y + 2.4)) for y in (8.0, -12.0)]
    cut_b = unary_union([g.buffer(0.6, join_style=2) for g in br + v24])
    # (above the band the top ground runs in parallel with the bottom, stitched: each layer carries half)
    # (each region overlaps the band it drains into, so a connector's return is checked all the way to the trunk)
    regions_b = [(rect(-E, -16.0, 38.0, 16.0), 60.0), (rect(-E, -16.0, E, BY - 0.5), 16.0),
                 (rect(-E, -BY + 0.5, E, 16.0), 32.0)]
    lobes = [Polygon(rect(-44.0, BAND - 1.0, -38.0, 21.6)), Polygon(rect(-44.0, -21.6, -38.0, -BAND + 1.0))]
    cut_f = unary_union([g.buffer(0.8, join_style=2) for g in br + lobes] + [Polygon(vbat).buffer(0.8, join_style=2)])
    regions_f = [(rect(-E, BAND + 1.0, 38.0, PIN_Y + 3.3), 16.0), (rect(-E, PIN_Y + 3.3, 38.0, BY - 0.5), 16.0),
                 (rect(-E, -BY + 0.5, -23.4, -BAND - 1.0), 16.0)]   # (top: one strip per connector, then the edge)
    for L, regions, cut in (("B", regions_b, cut_b), ("F", regions_f, cut_f)):
        for poly, amps in regions:
            g = Polygon(poly).difference(cut)
            for p in (getattr(g, "geoms", None) or [g]):
                if p.area > 4.0:
                    b.pour("GND", L, list(p.exterior.coords)[:-1], amps=amps,
                           holes=[list(r.coords)[:-1] for r in p.interiors])


def arm_route(b):
    probs = []
    for k, (ref, (label, amps, x, side)) in enumerate(MOTORS.items()):          # tie each branch's two layers
        b.stitch(f"M{k + 1}", branch(x, side), pitch=1.6, drill=0.4, dia=0.8)
    for net, y in (("W6", 8.0), ("G7", -12.0)):
        b.stitch(net, rect(50.6, y - 2.4, 58.0, y + 2.4), pitch=1.6, drill=0.4, dia=0.8)
    for poly in (rect(-1.2, -42.0, 5.4, -37.3), rect(-12.2, -28.6, -1.7, -24.0),
                 rect(COUT_X[0] - 2.0, -38.2, COUT_X[-1] + 2.0, -34.4), rect(54.4, -27.0, 58.5, -21.0)):
        b.stitch("GND", poly, pitch=1.2, drill=0.3, dia=0.6)                     # the power grounds to the plane
    b.stitch("GND", rect(-BX, BAND + 1.0, 38.0, BY), pitch=2.5, drill=0.4, dia=0.8)          # top / bottom ground
    b.stitch("GND", rect(-BX, -BY, -23.4, -BAND - 1.0), pitch=2.5, drill=0.4, dia=0.8)       # in parallel
    b.fanout(width=0.2)
    ko = sw_keepout()
    for net, w in (("HO", 0.25), ("LO", 0.25), ("BST", 0.25), ("VCC", 0.25), ("ILIM", 0.2), ("FB", 0.2),
                   ("COMP", 0.2), ("CMPRC", 0.2), ("FBC", 0.2), ("SS", 0.2), ("RT", 0.2), ("EN", 0.2),
                   ("LED_A", 0.3), ("LED_B", 0.3)):
        probs += route(b, net, width=w, keepout=ko)
    probs += route(b, "SW", width=0.25, amps=0.5, only=[("U1", "19"), ("C31", "2"), ("R30", "2")])
    probs += route(b, "V24", width=0.3, amps=0.05, keepout=ko, only=[("R35", "1"), ("C37", "1"), ("R3", "1")])
    probs += route(b, "VBAT", width=0.3, amps=0.05, keepout=ko,
                   only=[("U1", "20"), ("C32", "1"), ("R31", "1"), ("R1", "1"), ("R2", "1")])
    probs += route(b, "GND", width=0.3, amps=0.05, keepout=ko, only=[p for p in b.nets["GND"]["pins"]
                                             if p[0] in ("U1", "C30", "C32", "C33", "R32", "R33", "C34", "R36",
                                                         "D2", "D4", "R1", "C3", "C4", "D1")])
    prune_stubs(b)
    return probs


def prune_stubs(b):
    """drop escape stubs the router did not use (their far end touches nothing else of the net): an unused stub
    on ILIM or RT is an antenna"""
    from shapely.geometry import LineString, Point
    keep = []
    pads = {(r, n): g for r, n, L, g, _ in b.pad_shapes()}
    for t in b.tracks:
        if t.get("fan"):
            stub = LineString(t["pts"]).buffer(t["w"] / 2).difference(pads.get(t["fan"], Point(t["pts"][0]).buffer(0.01)))
            used = any(o is not t and o["net"] == t["net"] and o["layer"] == t["layer"] and
                       LineString(o["pts"]).buffer(o["w"] / 2).intersects(stub) for o in b.tracks) or \
                any(v["net"] == t["net"] and Point(v["at"]).buffer(v["dia"] / 2).intersects(stub) for v in b.vias)
            if not used:
                continue
        keep.append(t)
    b.tracks = keep


ROUTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes")   # routed copper, kept in git


def build():
    b = arm_board()
    arm_nets(b)
    arm_pours(b)
    b.silk_text = [("PIONEER ARM PDB", (0.0, 4.0), 3.0, 0.0), ("12S 36-49 V IN  /  5 x 49 V 40 A  /  2 x 24 V", (0.0, -1.5), 1.6, 0.0)]
    probs = cached_route(b, arm_route, tag="arm-pdb-r18", store=ROUTES)
    return b, probs


PCB, PCB_ROUTE_PROBLEMS = build()
PCBS = {"arm_pdb": PCB}
parts = {}
if os.environ.get("ARM_PDB_SOLIDS", "1") == "1" and __name__ != "__main__":
    parts.update(PCB.parts(0.0))


if __name__ == "__main__":
    import csv
    import pcb
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    b, probs = PCB, PCB_ROUTE_PROBLEMS
    print("route:", probs)
    chk = b.check()
    print("check:", len(chk))
    for p in chk:
        print("  ", p)
    os.makedirs(out, exist_ok=True)
    b.plot(os.path.join(out, "layers.png"))
    from arm_pdb_sheet import draw                 # the conventional sheet, checked against the netlist
    sheet_probs = draw(b, out)                      # schematic_1 (distribution), schematic_2 (24 V converter)
    print("schematic vs board:", sheet_probs or "matches")
    assert not sheet_probs, sheet_probs
    with open(os.path.join(out, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "mpn", "footprint"])
        for r in b.bom():
            w.writerow([" ".join(r["refs"]), r["qty"], r["value"], r["mpn"], r["footprint"]])
    pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    kpcb = pcb.export_kicad(b, os.path.join(out, "kicad"))[0]
    for refill in (False, True):                     # KiCad's own DRC, on our fills and on KiCad's
        d = pcb.kicad_drc(kpcb, refill=refill)
        print(f"KiCad DRC ({'KiCad refill' if refill else 'as exported'}):", len(d["errors"]), "errors,",
              len(d["unconnected"]), "unconnected")
        assert not d["errors"] and not d["unconnected"], d["errors"][:5] + d["unconnected"][:5]
    import tempfile
    mdir = os.path.join(tempfile.gettempdir(), "kicad_3d")
    print("3D models not found:", pcb.kicad_models(b, mdir) or "none")
    pcb.render_kicad(kpcb, out, mdir, zoom=1.4)
    for d in ("gerbers", "kicad"):                  # one zip each in git, not thousands of generated lines
        pcb.pack(os.path.join(out, d))
    print("wrote", out)
