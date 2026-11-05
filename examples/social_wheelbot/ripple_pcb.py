"""Ripple's power / CAN board: the Pioneer QDD power board (quadruped_qdd.py, r6) cut down to six motors.

Same circuit as the dog's (r7: a Kobalt 24V MAX 6S drill pack in, was a 6S LiPo; through an LM5069 hot-swap switch with a 55 A breaker and UVLO / OVLO, a 24 -> 5 V
buck for an ESP32 DevKit V1 on sockets, an SN65HVD230 CAN transceiver and an ICM-42688-P IMU on SPI, a 24 V XT30 out
for the Raspberry Pi's 5 V BEC and a UART to it, the dock charge input J28 / D4), with one XT30 + JST-GH pair per
motor for the six AK60-6 (left leg: hip, knee, wheel on J2-J4 / J8-J10; right: J5-J7 / J11-J13). The board's IMU is
the balance controller's attitude sensor, and the Pi reads it over the UART for the face's fluid.
Board coordinates: x along the robot's x, y along its y (mm), the board's own origin; the program places it.
ASSUMED pinouts are from the makers' datasheets as remembered (as on the dog's board): verify before fab.
r7: the E-stop (normally closed, J29) in series with the UVLO divider; a TL431 regen brake (Q3, J30). ASSUMED: TL431 SOT-23
pinout 1 K / 2 REF / 3 A (TL432 swaps K and REF): check TI's DBZ drawing.
"""
import os

from build123d import *
from pcb import Board, route, cached_route
from vendor_parts import kicad

C = (Align.CENTER, Align.CENTER, Align.MIN)
ROUTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes")
PCB_Y = (-56.0, 102.0)
POSTS = [(x, y) for x in (-58.0, 58.0) for y in (-52.0, 97.0)]
XT30_H = 22.0                                      # XT30 pair, vertical, mated: body + plug height above the board
XT_Y = {1: [69.0, 82.0, 95.0], -1: [-19.0, -32.0, -45.0]}
BOARD_LEGS = {"l": (1, 1), "r": (-1, 1)}           # (s = side, e = the connector column: all on the +x edge)
MOTOR_KINDS = ("hip", "knee", "wheel")
XT = {(leg, k): (e * 78.0, XT_Y[s][k]) for leg, (s, e) in BOARD_LEGS.items() for k in range(3)}
SW_X = 30.0
AUX_XT, UART_GH = (-78.0, 30.0), (-48.0, 8.0)      # J26 (24 V out), J27 (Pi UART)
CHG_P1, CHG_D = (-49.5, -24.0), (-47.0, -31.5)     # J28's + pin (dock charge in), D4
CHG_XT = (CHG_P1[0] - 2.5, CHG_P1[1])
CU_T = 2 * 0.0347                                  # 2 oz outer copper
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
    b = Board("ripple_main", [(-90.0, PCB_Y[0]), (90.0, PCB_Y[0]), (90.0, PCB_Y[1]), (-90.0, PCB_Y[1])],
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
            model="Package_SO.3dshapes/MSOP-10_3x3mm_P0.5mm.step")
    R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
    r06m, c06m = "Resistor_SMD.3dshapes/R_0603_1608Metric.step", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"
    # UVLO off at 17.8 V (3.0 V / cell, 6S: the Kobalt 24V MAX drill pack, as the LiPo before it), OVLO 29.7 V
    # (LM5069: R1 = hysteresis / 21 uA, R2 + R3 = 2.5 R1 / (V_UVL - 2.5), R3 sets V_OVH)
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
    b.place("U2", "Package_TO_SOT_SMD:SOT-23-6", (20.0, 20.0), 0, "LMR51420", "TI LMR51420YDDCR", model=P + "SOT-23-6.step")
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
            model="Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step")
    b.place("RT", "Resistor_SMD:R_0805_2012Metric", (-16.0, 85.0), 90, "120R", "RC0805FR-07120RL",
            model="Resistor_SMD.3dshapes/R_0805_2012Metric.step")
    b.place("U4", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", (16.0, 44.0), 0, "ICM-42688-P", "TDK ICM-42688-P",
            model="Package_LGA.3dshapes/LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y.step")
    # motor connectors
    for i, ((leg, k), (x, y)) in enumerate(XT.items()):
        e = BOARD_LEGS[leg][1]
        b.place(f"J{2 + i}", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", (x, y - 2.5), 90, "XT30-F",
                "AMASS XT30UPB-F", body=XT30_BODY, note=f"motor {leg} {MOTOR_KINDS[k]} power")
        b.place(f"J{8 + i}", "Connector_JST:JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical", (x - e * 12.0, y), 90,
                "GH 4p", "JST BM04B-GHS-TBT", model="Connector_JST.3dshapes/JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical.step",
                note=f"motor {leg} {MOTOR_KINDS[k]} CAN")
    XT30S, GHS = [f"J{2 + i}" for i in range(6)], [f"J{8 + i}" for i in range(6)]
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
    # r7: the E-stop's normally closed contact in series with the UVLO divider's top: pressed, or a wire off, and
    # the hot-swap switch opens (a shorting plug runs the board without one)
    b.place("J29", "Connector_JST:JST_GH_BM02B-GHS-TBT_1x02-1MP_P1.25mm_Vertical", (-46.5, -17.0), 0, "GH 2p",
            "JST BM02B-GHS-TBT", model="Connector_JST.3dshapes/JST_GH_BM02B-GHS-TBT_1x02-1MP_P1.25mm_Vertical.step",
            note="E-stop (normally closed)")
    # r7: regen brake. If the pack's BMS refuses charge current, the motors' braking energy would lift the bus into
    # OVLO; above 26.3 V a TL431 turns Q3 (P-FET, high side) on and the bus dumps into a 10 R resistor on J30, off again
    # at 25.7 V (R17 hysteresis), above a full pack's 25.2 V so it never drains it
    b.place("U5", "Package_TO_SOT_SMD:SOT-23", (-46.0, 89.0), 0, "TL431", "TI TL431AIDBZR", model=P + "SOT-23.step")
    b.place("Q3", "Package_TO_SOT_SMD:TO-252-2", (-58.0, 76.0), 180, "-60V P-FET", "Vishay SUD50P06-15-GE3",
            model=P + "TO-252-2.step")
    for ref, at, val, mpn in (("R13", (-42.0, 96.0), "100k", "RC0603FR-07100KL"), ("R14", (-42.0, 92.0), "10k5", "RC0603FR-0710K5L"),
                              ("R17", (-42.0, 88.0), "3M9", "RC0603FR-073M9L"), ("R15", (-48.0, 78.0), "10k", "RC0603FR-0710KL"),
                              ("R16", (-48.0, 83.0), "10k", "RC0603FR-0710KL")):
        b.place(ref, R06, at, 0, val, mpn, model=r06m)
    b.place("J30", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", (-66.0, 80.0), 90, "XT30-F", "AMASS XT30UPB-F",
            body=XT30_BODY, note="brake resistor (10 R 50 W)")
    # no copper on either layer under the DevKit's antenna (its keep-out, 15 mm round the module's antenna end,
    # reaches down to this board 8.5 mm below): the IMU moved out of it in r4
    b.keepout([(-15.0, 30.0), (6.5, 30.0), (6.5, 78.5), (-15.0, 78.5)], "FB", "ESP32 antenna")

    # ---- nets (pin numbers are the footprints'; ESP32 DevKit V1 (DOIT 30 pin), from the antenna end: H1 = EN VP VN
    # D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND VIN, H2 = D23 D22 TX0 RX0 D21 D19 D18 D5 TX2 RX2 D4 D2 D15 GND 3V3)
    V = 25.2
    b.net("VIN", [("J1", "1"), ("D1", "1"), ("RS1", "1"), ("R11", "1"), ("J29", "1"), ("D4", "1")], amps=30.0, volts=V)
    b.net("ESTOP", [("J29", "2"), ("R1", "1")], amps=0.01, volts=V)
    b.net("BRK_REF", [("U5", "2"), ("R13", "2"), ("R14", "1"), ("R17", "1")], amps=0.01, volts=3.3)
    b.net("BRK_K", [("U5", "1"), ("R16", "2")], amps=0.01, volts=V)
    b.net("BRK_G", [("Q3", "1"), ("R15", "2"), ("R16", "1")], amps=0.01, volts=V)
    b.net("BRAKE", [("Q3", "2"), ("J30", "1"), ("R17", "2")], amps=3.0, volts=V)
    b.net("VCHG", [("J28", "1"), ("D4", "2")], amps=4.0, volts=V)
    b.net("VINF", [("R11", "2"), ("U1", "2"), ("C13", "1")], amps=0.01, volts=V)
    b.net("VD", [("RS1", "2"), ("Q1", "5"), ("Q2", "5"), ("U1", "1")], amps=30.0, volts=V)
    b.net("VBUS", [("Q1", s_) for s_ in "123"] + [("Q2", s_) for s_ in "123"] + [(j, "1") for j in XT30S] + [("J26", "1")] + [("Q3", "3"), ("R13", "1"), ("R15", "1")]
          + [("U1", "9"), ("CB1", "1"), ("CB2", "1"), ("U2", "5"), ("U2", "4"), ("C3", "1"), ("C4", "1"), ("R7", "1")],
          amps=30.0, volts=V)
    b.net("GND", [("U5", "3"), ("R14", "2"), ("J30", "2"), ("J1", "2"), ("D1", "2"), ("U1", "5"), ("R3", "2"), ("R10", "2"), ("C12", "2"), ("R8", "2"),
                  ("C11", "2"), ("CB1", "2"), ("CB2", "2"), ("SW1", "1"), ("U2", "2"), ("C3", "2"), ("C4", "2"),
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
    b.net("SW", [("U2", "6"), ("L1", "1"), ("C7", "2")], amps=1.5, volts=V)
    b.net("CB", [("U2", "1"), ("C7", "1")], amps=0.1, volts=V)
    b.net("FB", [("U2", "3"), ("R4", "2"), ("R5", "1")], amps=0.01, volts=1.0)
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
            ([(72.0, -53.0), (86.0, -53.0), (86.0, -3.0), (72.0, -3.0)], I3),         # D: right leg's column
            ([(72.0, -3.0), (86.0, -3.0), (86.0, 100.0), (72.0, 100.0)], I3),         # E: left leg's column
            ([(-86.0, -3.0), (-72.0, -3.0), (-72.0, 100.0), (-86.0, 100.0)], 5.0)]    # F: the Pi's 24 V out (J26)
    b.silk_text = [("PIONEER  RIPPLE POWER / CAN", (30.0, 64.0), 3.2, 0.0), ("rev r2  ·  6S (KOBALT 24V) IN  ·  6 x AK60-6", (30.0, 58.5), 2.2, 0.0),
                   ("L", (80.0, 59.0), 2.4, 0.0), ("R", (80.0, -9.0), 2.4, 0.0), ("PI 24V", (-80.0, 40.0), 1.8, 0.0),
                   ("CHG", (-56.0, -19.0), 1.8, 0.0), ("ESTOP", (-46.5, -12.8), 1.4, 0.0), ("BRAKE R", (-66.0, 74.0), 1.4, 0.0)]   # legend (renders only)
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
    ko = lambda own: {"F": [p for p in ko_all if p not in own]}
    # parts sitting in the FET-source VBUS pour (the gate resistors, the bulk caps' ground pins) route through it:
    # the pour clears round their copper (route() now frees only a pin's own pad, not the pour round it)
    in_a = ko([vbus[0][0]])
    for n in ("GATE", "G1", "G2"):
        probs += route(b, n, width=0.25, keepout=in_a)
    probs += route(b, "GND", width=0.3, amps=0.5, only=[("CB1", "2"), ("CB2", "2")], keepout=in_a)
    probs += route(b, "GND", width=0.3, amps=0.5, only=gnd_small, keepout={"F": ko_all})
    probs += route(b, "VBUS", width=0.25, amps=0.01, only=[("U1", "9")], keepout=ko([p for p, _ in vbus]))
    for n in ("UVLO", "OVLO", "PGD", "TIMER", "PWR"):                          # the hot-swap controller's pins
        probs += route(b, n, width=0.25, keepout={"F": ko_all})
    probs += route(b, "VIN", width=0.25, amps=0.01, only=[("R11", "1"), ("J29", "1")], keepout=ko([top_pours["VIN"]]))
    probs += route(b, "ESTOP", width=0.25, keepout={"F": ko_all})
    probs += route(b, "VBUS", width=1.0, amps=3.0, only=[("Q3", "3"), ("R13", "1"), ("R15", "1")],
                   keepout=ko([p for p, _ in vbus]))
    probs += route(b, "BRAKE", width=1.0, amps=3.0, keepout={"F": ko_all})
    for n in ("BRK_REF", "BRK_K", "BRK_G"):
        probs += route(b, n, width=0.25, keepout={"F": ko_all})
    probs += route(b, "GND", width=1.0, amps=3.0, only=[("J30", "2")], keepout={"F": ko_all})
    probs += route(b, "VINF", width=0.25, keepout={"F": ko_all})
    probs += route(b, "VD", width=0.25, amps=0.01, only=[("U1", "1")], keepout=ko([top_pours["VD"]]))
    probs += route(b, "VBUS", width=0.6, amps=1.0, only=[("U2", "5"), ("U2", "4"), ("C3", "1"), ("C4", "1"), ("R7", "1")],
                   keepout=ko([p for p, _ in vbus]))
    for n in ("CB", "SW", "FB", "5V", "LED", "VSENSE", "CAN_TX", "CAN_RX", "SCK", "MOSI", "MISO", "IMU_CS", "UART_TX", "UART_RX", "3V3",
              "CANH", "CANL"):
        probs += route(b, n, width=widths.get(n, 0.25), keepout={"F": ko_all})
    prune_stubs(b)
    # heal: a pin cut off from its net's pour by the signals is routed back to the main group
    for net in ("GND",):
        grps = b.islands(net)
        for grp in grps[1:]:
            probs += route(b, net, width=0.3, amps=0.5, only=grp, seed_pins=grps[0], keepout={"F": ko_all})
    prune_stubs(b)
    return probs


def prune_stubs(b):
    """drop escape stubs the router did not use (their far end touches nothing else of the net); as twilight_fc.py"""
    from shapely.geometry import LineString, Point
    keep = []
    pads = {(r, n): g for r, n, L, g, _ in b.pad_shapes()}
    pn_ = b.pad_net()
    for t in b.tracks:
        if t.get("fan"):
            stub = LineString(t["pts"]).buffer(t["w"] / 2).difference(pads.get(t["fan"],
                                                                                Point(t["pts"][0]).buffer(0.01)))
            used = any(o is not t and o["net"] == t["net"] and o["layer"] == t["layer"] and
                       LineString(o["pts"]).buffer(o["w"] / 2).intersects(stub) for o in b.tracks) or \
                any(v["net"] == t["net"] and Point(v["at"]).buffer(v["dia"] / 2).intersects(stub) for v in b.vias) or \
                any(k != t["fan"] and pn_.get(k) == t["net"] and g.intersects(stub) for k, g in pads.items())
            if not used:
                continue
            line = LineString(t["pts"])
            band = line.buffer(t["w"] / 2)
            ends = [Point(e) for o in b.tracks if o is not t and o["net"] == t["net"] and o["layer"] == t["layer"]
                    for e in (o["pts"][0], o["pts"][-1]) if band.contains(Point(e))] + \
                   [Point(v["at"]) for v in b.vias if v["net"] == t["net"] and band.contains(Point(v["at"]))]
            far_pad = any(k != t["fan"] and pn_.get(k) == t["net"] and g.intersects(stub) for k, g in pads.items())
            if ends and not far_pad:
                d = max(line.project(e) for e in ends)
                if 1e-3 < d < line.length - 1e-3:
                    q = line.interpolate(d)
                    t = dict(t, pts=[t["pts"][0], (round(q.x, 4), round(q.y, 4))])
        keep.append(t)
    b.tracks = keep


# for the schematic (pcb.schematic): pin functions from the makers' datasheets, and the board's functional blocks
PCB_PIN_NAMES = {
    "U1": dict(zip("1 2 3 4 5 6 7 8 9 10".split(), "SENSE VIN UVLO OVLO GND TIMER PWR PGD OUT GATE".split())),
    "U2": dict(zip("1 2 3 4 5 6".split(), "CB GND FB EN VIN SW".split())),
    "U3": dict(zip("1 2 3 4 5 6 7 8".split(), "D GND VCC R Vref CANL CANH Rs".split())),
    "U4": {"1": "SDO", "5": "VDDIO", "6": "GND", "8": "VDD", "12": "CS", "13": "SCLK", "14": "SDI"},
    "Q1": {"1": "S", "2": "S", "3": "S", "4": "G", "5": "D"}, "Q2": {"1": "S", "2": "S", "3": "S", "4": "G", "5": "D"},
    "J1": {"1": "+", "2": "-"}, "SW1": {"1": "A", "2": "COM", "3": "B"}, "D2": {"1": "CANL", "2": "CANH", "3": "GND"},
    "H1": dict(zip([str(i) for i in range(1, 16)], "EN VP VN D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND VIN".split())),
    "H2": dict(zip([str(i) for i in range(1, 16)], "D23 D22 TX0 RX0 D21 D19 D18 D5 TX2 RX2 D4 D2 D15 GND 3V3".split())),
    **{f"J{2 + i}": {"1": "+", "2": "-"} for i in range(6)},
    **{f"J{8 + i}": {"1": "CANH", "2": "CANL", "3": "GND"} for i in range(6)},
    "J26": {"1": "+", "2": "-"}, "J27": {"1": "TX", "2": "RX", "3": "GND", "4": "NC"}, "J28": {"1": "+", "2": "-"},
    "J29": {"1": "VIN", "2": "UVLO top"}, "J30": {"1": "BRAKE", "2": "-"},
    "U5": {"1": "K", "2": "REF", "3": "A"}, "Q3": {"1": "G", "2": "D", "3": "S"}, "D4": {"1": "K", "2": "A"}}
PCB_GROUPS = [("Battery in, protection, hot-swap switch", ["J1", "D1", "RS1", "Q1", "Q2", "RG1", "RG2"]),
              ("LM5069 hot-swap controller, E-stop", ["U1", "R11", "C13", "R1", "R2", "R3", "R10", "C12", "SW1", "J29"]),
              ("24 V bus: bulk caps, battery sense", ["CB1", "CB2", "R7", "R8", "C11", "R9"]),
              ("24 V -> 5 V buck, power LED", ["U2", "L1", "C3", "C4", "C5", "C6", "C7", "R4", "R5", "R12", "D3"]),
              ("ESP32 DevKit V1, 30 pin (on sockets)", ["H1", "H2"]),
              ("CAN bus + IMU", ["U3", "C8", "RT", "D2", "U4", "C9", "C10"]),
              ("Motor power (XT30) - left leg", [f"J{2 + i}" for i in range(3)]),
              ("Motor power (XT30) - right leg", [f"J{2 + i}" for i in range(3, 6)]),
              ("Motor CAN (JST-GH, daisy chain)", [f"J{8 + i}" for i in range(6)]),
              ("Raspberry Pi: 24 V out, UART", ["J26", "J27"]),
              ("Dock charge input", ["J28", "D4"]),
              ("Regen brake", ["U5", "Q3", "R13", "R14", "R15", "R16", "R17", "J30"])]



def build():
    """the board, its pours and routing (cached, the copper committed under projects/routes/)"""
    b, tp, vb = power_board()
    board_pours(b, tp, vb)
    probs = cached_route(b, lambda b_: board_route(b_, tp, vb), tag="ripple-main-r6", store=ROUTES_DIR)
    return b, probs


if __name__ == "__main__":
    # python ripple_pcb.py [out_dir]: the board's fabrication set (out/ by default), checked by the
    # harness and by KiCad's own DRC on our fills and on KiCad's
    import csv
    import sys
    import tempfile
    import pcb
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    b, probs = build()
    print("route:", probs)
    chk = b.check()
    print("check:", len(chk), chk[:10])
    os.makedirs(out, exist_ok=True)
    b.plot(os.path.join(out, "layers.png"))
    with open(os.path.join(out, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "mpn", "footprint"])
        for r in b.bom():
            w.writerow([" ".join(r["refs"]), r["qty"], r["value"], r["mpn"], r["footprint"]])
    pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    kpcb = pcb.export_kicad(b, os.path.join(out, "kicad"))[0]
    for refill in (False, True):
        d = pcb.kicad_drc(kpcb, refill=refill)
        print(f"KiCad DRC ({'KiCad refill' if refill else 'as exported'}):", len(d["errors"]), "errors,",
              len(d["unconnected"]), "unconnected", d["errors"][:8], d["unconnected"][:8])
    mdir = os.path.join(tempfile.gettempdir(), "kicad_3d")
    print("3D models not found:", pcb.kicad_models(b, mdir) or "none")
    pcb.render_kicad(kpcb, out, mdir, zoom=1.4)
    for d_ in ("gerbers", "kicad"):
        pcb.pack(os.path.join(out, d_))
    print("wrote", out)
