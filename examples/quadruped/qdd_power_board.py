"""The QDD quadruped's power / CAN board: a hot-swap switched 6S input, a 24 -> 5 V buck, an ESP32 on sockets, CAN
and an IMU, an XT30 + JST-GH pair per motor for twelve motors (the board Ripple's is cut down from).

Taken from the quadruped's design (its legs, frame and wiring are not part of this example); the board code is as
there. ASSUMED pinouts are from the makers' datasheets as remembered: verify before fab.

    python qdd_power_board.py [out_dir]      # writes out/
"""
import math
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src")]
from build123d import *
from pcb import Board, route, cached_route
from vendor_parts import kicad

C = (Align.CENTER, Align.CENTER, Align.MIN)

LEGS = {"fl": (1, 1), "fr": (-1, 1), "rl": (1, -1), "rr": (-1, -1)}   # name: (s = side +left, e = end +front)

def at(loc, p):
    q = (loc * Location(p)).position
    return (q.X, q.Y, q.Z)

PCB_Y = (-56.0, 102.0)

POSTS = [(x, y) for x in (-58.0, 58.0) for y in (-52.0, 97.0)]   # clear of the abad screws

XT30_H = 22.0                                      # XT30 pair, vertical, mated: body + plug height above the board

XT_Y = {1: [69.0, 82.0, 95.0], -1: [-19.0, -32.0, -45.0]}

XT = {(leg, k): (e * 78.0, XT_Y[s][k]) for leg, (s, e) in LEGS.items() for k in range(3)}   # k: 0 abad, 1 hip, 2 knee

SW_X = 30.0

AUX_XT, UART_GH = (-78.0, 30.0), (-48.0, 8.0)                       # r5: J26 (24 V out), J27 (Pi UART)

CHG_P1, CHG_D = (-49.5, -24.0), (-47.0, -31.5)                      # r6: J28's + pin (dock charge in), D4

XT60 = (-48.0, -42.0)                              # battery connector (pin 1 at XT60, turned 180: its body to -x)

SW_Y = PCB_Y[1] - 2.5                              # its mounting pads 0.7 mm inside the board edge

_x60 = kicad("Connector_AMASS.3dshapes/AMASS_XT60-F_1x02_P7.2mm_Vertical.step").solids()

_good = [s_ for s_ in _x60[1:] if s_.is_valid][0]            # one contact in KiCad's model does not tessellate closed:

XT60_BODY = Compound(children=[_x60[0]] + [Pos(c.center().X - _good.center().X, 0, 0) * _good for c in _x60[1:]])

def _xt30_mated():
    """AMASS XT30U, mated (no maker 3D model; drawn from its datasheet outline): the D-profile female socket on the
    board (10.2 x 5.2, chamfered on one long side), a seam, the male plug on top with its grip grooves"""
    def d_prof(i):
        hx, hy, ch = 5.1 - i, 2.6 - i, 1.2
        return Polygon((-hx, -hy), (hx, -hy), (hx, hy - ch), (hx - ch, hy), (-hx + ch, hy), (-hx, hy - ch), align=None)
    body = extrude(d_prof(0.0), 8.0) + Pos(0, 0, 7.99) * extrude(d_prof(0.35), 0.62) + \
        Pos(0, 0, 8.6) * extrude(d_prof(0.1), XT30_H - 8.6)
    for z in (12.5, 14.0, 15.5):                                   # grip grooves across the plug's long faces
        body = body - Pos(0, 0, z) * Box(12.0, 6.0, 0.5, align=C) + Pos(0, 0, z) * extrude(d_prof(0.35), 0.5)
    return Pos(2.5, 0, 0) * body

XT30_BODY = _xt30_mated()                          # XT30 pair, mated

def power_board():
    b = Board("qdd_main", [(-90.0, PCB_Y[0]), (90.0, PCB_Y[0]), (90.0, PCB_Y[1]), (-90.0, PCB_Y[1])],
              thickness=1.6, copper_oz=2.0, clearance=0.15, max_rise_c=20.0, corner_r=5.0)
    for p in POSTS:
        b.hole(p, 2.7, keepout=4.6)                       # M2.5 on the chassis posts (R4)
    P = "Package_TO_SOT_SMD.3dshapes/"
    # power entry and switch
    b.place("J1", "Connector_AMASS:AMASS_XT60-F_1x02_P7.20mm_Vertical", XT60, 180, "XT60-F", "AMASS XT60-F",
            body=XT60_BODY)
    b.place("D1", "Diode_SMD:D_SMB", (-38.0, -30.0), 0, "SMBJ28A", "Littelfuse SMBJ28A", model="Diode_SMD.3dshapes/D_SMB.step")
    b.place("RS1", "Resistor_SMD:R_2512_6332Metric", (-30.0, -42.0), 0, "1m 3W", "Vishay WSLP25121L000FEA",
            model="Resistor_SMD.3dshapes/R_2512_6332Metric.step")
    for ref, y in (("Q1", -47.0), ("Q2", -37.0)):
        b.place(ref, "Package_TO_SOT_SMD:TDSON-8-1", (-14.0, y), 180, "60V 1m45", "Infineon BSC014N06NS",
                model=P + "TDSON-8-1.step")
    b.place("U1", "Package_SO:MSOP-10_3x3mm_P0.5mm", (-28.0, -22.0), 0, "LM5069", "TI LM5069MM-2/NOPB",
            model="Package_SO.3dshapes/MSOP-10_3x3mm_P0.5mm.step", symbol="Power_Management:LM5069MM-1")
    R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
    r06m, c06m = "Resistor_SMD.3dshapes/R_0603_1608Metric.step", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"
    for ref, at, val, mpn in (("R1", (-37.0, -16.0), "102k", "RC0603FR-07102KL"), ("R2", (-37.0, -19.0), "6k65", "RC0603FR-076K65L"),
                              ("R3", (-37.0, -22.0), "10k", "RC0603FR-0710KL"), ("R10", (-19.0, -18.0), "10k", "RC0603FR-0710KL"),
                              ("RG1", (-5.0, -45.1), "10R", "RC0603FR-0710RL"), ("RG2", (-5.0, -35.1), "10R", "RC0603FR-0710RL"),
                              ("R7", (-40.0, 30.0), "100k", "RC0603FR-07100KL"), ("R8", (-40.0, 27.0), "10k", "RC0603FR-0710KL"),
                              ("R9", (-52.0, 30.0), "10k", "RC0603FR-0710KL"), ("R4", (12.0, 22.0), "100k", "RC0603FR-07100KL"),
                              ("R5", (12.0, 18.0), "13k7", "RC0603FR-0713K7L")):
        b.place(ref, R06, at, 0, val, mpn, model=r06m)
    b.place("R11", R06, (-33.0, -14.5), 0, "10R", "RC0603FR-0710RL", model=r06m)          # U1 supply filter
    b.place("C13", C06, (-29.5, -14.5), 0, "1u", "X7R 50 V", model=c06m)
    b.place("D2", "Package_TO_SOT_SMD:SOT-23", (-20.0, 80.5), 0, "PESD2CAN", "Nexperia PESD2CAN,215",
            model="Package_TO_SOT_SMD.3dshapes/SOT-23.step")                                   # CAN bus ESD clamp
    b.place("D3", "LED_SMD:LED_0603_1608Metric", (45.0, 28.0), 0, "green", "Kingbright APT1608SGC",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step")                                # 5 V on
    b.place("R12", R06, (45.0, 31.0), 0, "1k", "RC0603FR-071KL", model=r06m)
    for ref, at, val in (("C12", (-19.0, -24.0), "10n"), ("C11", (-36.0, 27.0), "100n"), ("C7", (20.0, 25.0), "100n"),
                         ("C8", (-37.0, 84.0), "100n"), ("C9", (21.0, 47.0), "100n"), ("C10", (21.0, 41.0), "100n")):
        b.place(ref, C06, at, 0, val, "X7R 50 V", model=c06m)
    for ref, x in (("CB1", 12.0), ("CB2", 32.0)):
        b.place(ref, "Capacitor_SMD:CP_Elec_10x10.5", (x, -40.0), 0, "330u 35V", "Panasonic EEE-FK1V331P",
                model="Capacitor_SMD.3dshapes/CP_Elec_10x10.5.step")
    b.place("SW1", "Button_Switch_SMD:SW_SPDT_PCM12", (SW_X, SW_Y), 180, "PCM12", "C&K PCM12SMTR",
            model="Button_Switch_SMD.3dshapes/SW_SPDT_PCM12.step", overhang=True)   # its lever reaches the wall
    # 5 V buck
    b.place("U2", "Package_TO_SOT_SMD:SOT-23-6", (20.0, 20.0), 180, "LMR51420", "TI LMR51420YDDCR", model=P + "SOT-23-6.step", symbol="Prometheus:LMR51420")
    b.place("L1", "Inductor_SMD:L_Bourns_SRN6045TA", (31.0, 20.0), 0, "10u", "Bourns SRN6045TA-100M",
            model="Inductor_SMD.3dshapes/L_Bourns_SRN6045TA.step")
    for ref, at in (("C3", (22.0, 12.0)), ("C4", (22.0, 8.0))):
        b.place(ref, "Capacitor_SMD:C_1210_3225Metric", at, 0, "10u 50V", "GRM32ER71H106KA12L",
                model="Capacitor_SMD.3dshapes/C_1210_3225Metric.step")
    for ref, at in (("C5", (38.0, 14.0)), ("C6", (38.0, 10.0))):
        b.place(ref, "Capacitor_SMD:C_0805_2012Metric", at, 0, "22u 10V", "GRM21BR61A226ME44L",
                model="Capacitor_SMD.3dshapes/C_0805_2012Metric.step")
    # logic: ESP32 sockets (DevKit V1 30-pin, rows 25.4 apart), CAN, IMU
    # ESP32 DevKit V1 (DOIT, 30 pin): pins measured from its STEP: rows 25.4 apart, pin 1 at the antenna end
    b.place("H1", "Connector_PinSocket_2.54mm:PinSocket_1x15_P2.54mm_Vertical", (-17.44, 66.85), 270, "socket 1x15",
            "Samtec SSW-115-01-T-S")             # (its 3D body: the ESP32 catalogue model's 8.5 mm header envelope)
    b.place("H2", "Connector_PinSocket_2.54mm:PinSocket_1x15_P2.54mm_Vertical", (-17.44, 41.45), 270, "socket 1x15",
            "Samtec SSW-115-01-T-S")
    b.place("U3", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (-25.0, 85.0), 0, "SN65HVD230", "TI SN65HVD230DR",
            model="Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step", symbol="Interface_CAN_LIN:SN65HVD230")
    b.place("RT", "Resistor_SMD:R_0805_2012Metric", (-16.0, 85.0), 90, "120R", "RC0805FR-07120RL",
            model="Resistor_SMD.3dshapes/R_0805_2012Metric.step")
    b.place("U4", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", (16.0, 44.0), 0, "ICM-42688-P", "TDK ICM-42688-P",
            model="Package_LGA.3dshapes/LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y.step", symbol="Prometheus:ICM-42688-P")
    # motor connectors
    for i, ((leg, k), (x, y)) in enumerate(XT.items()):
        e = LEGS[leg][1]
        b.place(f"J{2 + i}", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", (x, y - 2.5), 90, "XT30-F",
                "AMASS XT30UPB-F", body=XT30_BODY, note=f"motor {leg} {('abad', 'hip', 'knee')[k]} power")
        b.place(f"J{14 + i}", "Connector_JST:JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical", (x - e * 12.0, y), 90,
                "GH 4p", "JST BM04B-GHS-TBT", model="Connector_JST.3dshapes/JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical.step",
                note=f"motor {leg} {('abad', 'hip', 'knee')[k]} CAN")
    XT30S, GHS = [f"J{2 + i}" for i in range(12)], [f"J{14 + i}" for i in range(12)]
    # r5: 24 V out for the Raspberry Pi's 5 V BEC (on the left VBUS spine) and the Pi <-> ESP32 UART (UART2)
    b.place("J26", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", (AUX_XT[0], AUX_XT[1] - 2.5), 90, "XT30-F",
            "AMASS XT30UPB-F", body=XT30_BODY, note="24 V out to the Pi's 5 V BEC")
    b.place("J27", "Connector_JST:JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical", UART_GH, 90, "GH 4p",
            "JST BM04B-GHS-TBT", model="Connector_JST.3dshapes/JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical.step",
            note="UART to the Raspberry Pi")
    # r6: charge input from the belly pads (the dock's pogo pins): J28 -> D4 Schottky -> VIN, the battery side of the
    # hot-swap switch (the pack charges with the robot off); D4 also keeps the exposed pads dead when undocked
    b.place("J28", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", CHG_P1, 180, "XT30-F",
            "AMASS XT30UPB-F", body=XT30_BODY, note="charge in from the belly pads")
    b.place("D4", "Diode_SMD:D_PowerDI-5", CHG_D, 90, "45V 10A", "Diodes Inc PDS1045-13",
            model="Diode_SMD.3dshapes/D_PowerDI-5.step")
    # no copper on either layer under the DevKit's antenna (its keep-out, 15 mm round the module's antenna end,
    # reaches down to this board 8.5 mm below): the IMU moved out of it in r4
    b.keepout([(-15.0, 30.0), (6.5, 30.0), (6.5, 78.5), (-15.0, 78.5)], "FB", "ESP32 antenna")

    # ---- nets (pin numbers are the footprints'; ESP32 DevKit V1 (DOIT 30 pin), from the antenna end: H1 = EN VP VN
    # D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND VIN, H2 = D23 D22 TX0 RX0 D21 D19 D18 D5 TX2 RX2 D4 D2 D15 GND 3V3)
    V = 25.2
    b.net("VIN", [("J1", "1"), ("D1", "1"), ("RS1", "1"), ("R11", "1"), ("R1", "1"), ("D4", "1")], amps=30.0, volts=V)
    b.net("VCHG", [("J28", "1"), ("D4", "2")], amps=4.0, volts=V)
    b.net("VINF", [("R11", "2"), ("U1", "2"), ("C13", "1")], amps=0.01, volts=V)
    b.net("VD", [("RS1", "2"), ("Q1", "5"), ("Q2", "5"), ("U1", "1")], amps=30.0, volts=V)
    b.net("VBUS", [("Q1", s_) for s_ in "123"] + [("Q2", s_) for s_ in "123"] + [(j, "1") for j in XT30S] + [("J26", "1")]
          + [("U1", "9"), ("CB1", "1"), ("CB2", "1"), ("U2", "3"), ("U2", "5"), ("C3", "1"), ("C4", "1"), ("R7", "1")],
          amps=30.0, volts=V)
    b.net("GND", [("J1", "2"), ("D1", "2"), ("U1", "5"), ("R3", "2"), ("R10", "2"), ("C12", "2"), ("R8", "2"),
                  ("C11", "2"), ("CB1", "2"), ("CB2", "2"), ("SW1", "1"), ("U2", "1"), ("C3", "2"), ("C4", "2"),
                  ("C5", "2"), ("C6", "2"), ("R5", "2"), ("H1", "14"), ("H2", "14"), ("U3", "2"), ("U3", "8"),
                  ("C8", "2"), ("U4", "6"), ("C9", "2"), ("C10", "2"), ("C13", "2"), ("D2", "3"), ("D3", "1")] + [(j, "2") for j in XT30S] + [(j, "3") for j in GHS] + [("J26", "2"), ("J27", "3"), ("J28", "2")],
          amps=30.0, volts=V)
    b.net("GATE", [("U1", "10"), ("RG1", "1"), ("RG2", "1")], amps=0.05, volts=V)
    b.net("G1", [("RG1", "2"), ("Q1", "4")], amps=0.05, volts=V)
    b.net("G2", [("RG2", "2"), ("Q2", "4")], amps=0.05, volts=V)
    b.net("UVLO", [("U1", "3"), ("R1", "2"), ("R2", "1"), ("SW1", "2")], amps=0.01, volts=V)
    b.net("OVLO", [("U1", "4"), ("R2", "2"), ("R3", "1")], amps=0.01, volts=V)
    b.net("TIMER", [("U1", "6"), ("C12", "1")], amps=0.01, volts=V)
    b.net("PWR", [("U1", "7"), ("R10", "1")], amps=0.01, volts=V)
    b.net("PGD", [("U1", "8"), ("R9", "2"), ("H1", "4")], amps=0.01, volts=3.3)          # D34 (input only)
    b.net("VSENSE", [("R7", "2"), ("R8", "1"), ("C11", "1"), ("H1", "5")], amps=0.01, volts=3.3)   # D35 (ADC1_7)
    b.net("SW", [("U2", "2"), ("L1", "1"), ("C7", "2")], amps=1.5, volts=V)
    b.net("CB", [("U2", "6"), ("C7", "1")], amps=0.1, volts=V)
    b.net("FB", [("U2", "4"), ("R4", "2"), ("R5", "1")], amps=0.01, volts=1.0)
    b.net("5V", [("L1", "2"), ("C5", "1"), ("C6", "1"), ("R4", "1"), ("H1", "15"), ("R12", "1")], amps=1.5, volts=5.0)
    b.net("LED", [("R12", "2"), ("D3", "2")], amps=0.01, volts=5.0)
    b.net("3V3", [("H2", "15"), ("U3", "3"), ("C8", "1"), ("U4", "5"), ("U4", "8"), ("C9", "1"), ("C10", "1"), ("R9", "1")],
          amps=0.2, volts=3.3)
    b.net("CAN_TX", [("H2", "8"), ("U3", "1")], amps=0.01, volts=3.3)        # D5
    b.net("CAN_RX", [("H2", "11"), ("U3", "4")], amps=0.01, volts=3.3)       # D4
    b.net("CANH", [("U3", "7"), ("RT", "1"), ("D2", "2")] + [(j, "1") for j in GHS], amps=0.05, volts=3.3)
    b.net("CANL", [("U3", "6"), ("RT", "2"), ("D2", "1")] + [(j, "2") for j in GHS], amps=0.05, volts=3.3)
    b.net("SCK", [("H2", "7"), ("U4", "13")], amps=0.01, volts=3.3)          # D18 (VSPI)
    b.net("MOSI", [("H2", "1"), ("U4", "14")], amps=0.01, volts=3.3)         # D23
    b.net("MISO", [("H2", "6"), ("U4", "1")], amps=0.01, volts=3.3)          # D19
    b.net("IMU_CS", [("H2", "13"), ("U4", "12")], amps=0.01, volts=3.3)      # D15
    b.net("UART_TX", [("H2", "9"), ("J27", "1")], amps=0.01, volts=3.3)      # TX2 (GPIO17) -> Pi RXD
    b.net("UART_RX", [("H2", "10"), ("J27", "2")], amps=0.01, volts=3.3)     # RX2 (GPIO16) <- Pi TXD

    # ---- copper: the power path in pours (2 oz both layers: VIN, VD, VBUS on top; GND the whole bottom), signals routed
    top_pours = {
        "VIN": [(-51.0, -50.0), (-31.4, -50.0), (-31.4, -34.0), (-38.0, -34.0), (-38.0, -28.5), (-43.0, -28.5),
                (-43.0, -34.0), (-51.0, -34.0)],                      # D4's cathode tab overlaps its top edge
        "VD": [(-29.5, -50.0), (-13.2, -50.0), (-13.2, -34.0), (-29.5, -34.0)],
    }
    I3 = 13.5                                   # a spine feeds 3 motors: 3 x 4.5 A rated (the 60 A peak is seconds)
    vbus = [([(-12.0, -53.0), (58.0, -53.0), (58.0, -12.0), (-12.0, -12.0)], None),   # A: from the FET sources
            ([(-86.0, -13.0), (86.0, -13.0), (86.0, -3.0), (-86.0, -3.0)], 27.0),     # B: across, above the GH columns
            ([(-86.0, -53.0), (-72.0, -53.0), (-72.0, -3.0), (-86.0, -3.0)], I3),      # C: rr column
            ([(72.0, -53.0), (86.0, -53.0), (86.0, -3.0), (72.0, -3.0)], I3),         # D: fr column
            ([(72.0, -3.0), (86.0, -3.0), (86.0, 100.0), (72.0, 100.0)], I3),         # E: right spine (fl)
            ([(-86.0, -3.0), (-72.0, -3.0), (-72.0, 100.0), (-86.0, 100.0)], I3)]     # F: left spine (rl)
    b.silk_text = [("PROMETHEUS  QDD POWER / CAN", (30.0, 64.0), 3.2, 0.0), ("rev r6  ·  6S IN  ·  12 x AK60-6", (30.0, 58.5), 2.2, 0.0),
                   ("FL", (80.0, 59.0), 2.4, 0.0), ("FR", (80.0, -9.0), 2.4, 0.0), ("RL", (-80.0, 59.0), 2.4, 0.0),
                   ("RR", (-80.0, -9.0), 2.4, 0.0), ("CHG", (-49.5, -18.5), 1.8, 0.0)]   # legend (renders only)
    return b, top_pours, vbus

def board_pours(b, top_pours, vbus):
    x0, x1 = -90.0, 90.0
    y0, y1 = PCB_Y
    # pours first: VIN, VD, VBUS on top (2 oz), GND the whole bottom in current zones (the middle power band carries
    # the 30 A return, each corner / outer column three motors' returns, the logic area a few amps)
    for n, poly in top_pours.items():
        b.pour(n, "F", poly)
    for poly, amps in vbus:
        b.pour("VBUS", "F", poly, amps=amps)
    for poly, amps in (([(-62.0, y0), (62.0, y0), (62.0, -3.0), (-62.0, -3.0)], 30.0),
                       ([(x0, y0), (-62.0, y0), (-62.0, y1), (x0, y1)], 13.5),
                       ([(62.0, y0), (x1, y0), (x1, y1), (62.0, y1)], 13.5),
                       ([(-62.0, -3.0), (62.0, -3.0), (62.0, y1), (-62.0, y1)], 2.0)):
        b.pour("GND", "B", poly, amps=amps)
    # r6 VCHG (4 A): J28's + pin straight across to D4's anode pair, beside the VIN pour D4's cathode sits in
    ya = CHG_D[1] + 2.862
    b.track("VCHG", "F", [CHG_P1, (CHG_D[0], CHG_P1[1] - 2.5), (CHG_D[0], ya)], width=1.0)
    b.track("VCHG", "F", [(CHG_D[0] - 0.92, ya), (CHG_D[0] + 0.92, ya)], width=1.0)

def board_route(b, top_pours, vbus):
    probs = []
    widths = {"SW": 0.8, "5V": 0.8, "3V3": 0.25, "CANH": 0.3, "CANL": 0.3, "SCK": 0.15, "MOSI": 0.15, "MISO": 0.15,
              "IMU_CS": 0.15}
    b.fanout(width=0.15)
    ko_all = list(top_pours.values()) + [p for p, _ in vbus]
    # every small GND pin down to the bottom pour first (vias need room), then the signals, then the power pins' links
    gnd_small = [p for p in b.nets["GND"]["pins"] if not (p[0].startswith("J") and p[1] == "2" and p[0] != "J1")
                 and p[0] not in ("H1", "H2", "J1")]
    probs += route(b, "GND", width=0.3, amps=0.5, only=gnd_small, keepout={"F": ko_all})
    ko = lambda own: {"F": [p for p in ko_all if p not in own]}
    probs += route(b, "VBUS", width=0.25, amps=0.01, only=[("U1", "9")], keepout=ko([p for p, _ in vbus]))
    for n in ("UVLO", "OVLO", "PGD", "TIMER", "PWR", "GATE", "G1", "G2"):        # the hot-swap controller's pins first
        probs += route(b, n, width=0.25, keepout={"F": ko_all})
    probs += route(b, "VIN", width=0.25, amps=0.01, only=[("R11", "1"), ("R1", "1")], keepout=ko([top_pours["VIN"]]))
    probs += route(b, "VINF", width=0.25, keepout={"F": ko_all})
    probs += route(b, "VD", width=0.25, amps=0.01, only=[("U1", "1")], keepout=ko([top_pours["VD"]]))
    probs += route(b, "VBUS", width=0.6, amps=1.0, only=[("U2", "3"), ("U2", "5"), ("C3", "1"), ("C4", "1"), ("R7", "1")],
                   keepout=ko([p for p, _ in vbus]))
    for n in ("CB", "SW", "FB", "5V", "LED", "VSENSE", "CAN_TX", "CAN_RX", "SCK", "MOSI", "MISO", "IMU_CS", "UART_TX", "UART_RX", "3V3",
              "CANH", "CANL"):
        probs += route(b, n, width=widths.get(n, 0.25), keepout={"F": ko_all})
    from pcb import repair
    probs += repair(b, width=0.3, amps=0.5, keepout={"F": ko_all})   # (a pour scrap left by later nets joined back)
    return probs



def build():
    b, tp, vb = power_board()
    board_pours(b, tp, vb)
    store = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes")    # routed copper, kept in git
    return b, cached_route(b, lambda b_: board_route(b_, tp, vb), tag="qdd-main-r7", store=store)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from export import export
    b, probs = build()
    export(b, probs, sys.argv[1] if len(sys.argv) > 1 else
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
