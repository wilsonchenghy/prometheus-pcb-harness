"""Single-cell Li-ion / LiPo charger with protection: USB-C (5 V, sink by 5.1k CC pull-downs) -> TP4056 linear
charger at 1 A (R_PROG = 1.2k) with red CHRG and green STDBY LEDs -> JST-PH 2-pin cell; DW01A + FS8205A protect the
cell in its negative leg (over-charge, over-discharge, over-current, short). The load is taken from a 2-pin 2.54 mm
header on the bottom edge (pin 1 OUT+ = cell +, pin 2 OUT- = protected negative, the board's ground).
Difficulty: 2/5. 2 layers, 34 x 22 mm, JLCPCB, ground poured on both sides.

Choices and assumptions (no questions asked: an unattended build):
- R_PROG 1.2k: TP4056 datasheet table "RPROG 1.2k -> IBAT 1000 mA" (I_BAT = 1200 V / R_PROG).
- TEMP (pin 1) tied to GND: the thermistor input disabled, as the datasheet allows; CE (pin 8) tied to VCC.
- LEDs: anode to VCC through 1k, cathode to the open-collector nCHRG / nSTDBY pins (datasheet typical circuit).
- DW01A supply: 100R from the cell + and 100 nF to the cell -; CS through 1k to the protected negative (OUT-): the
  standard DW01A application circuit. OD drives the discharge FET gate (G1), OC the charge FET gate (G2).
- FS8205A pin map: the Prometheus symbol (8205A family TSSOP-8: 1,8 drain; 2,3 S1; 4 G1; 5 G2; 6,7 S2): check it
  against the maker's TSSOP-8 drawing before fab. S1 = cell -, S2 = OUT- / charger ground.
- The charger and the load share the protected ground (OUT-): the protection then sees the charge current too.
  C2 (the BAT capacitor) goes to that ground, as in the TP4056 datasheet's circuit.
- Currents: VBUS / charge path 1 A; the cell and load path 2 A (the DW01A + FS8205A trip near 3 A).
- Layout: the 2 A copper (BAT -> C2 -> cell + and output header, cell - -> Q1 S1) and OD are drawn by hand on top
  (1.0 / 0.6 mm), so the cell current never passes a 0.3 mm via; the parts round them are placed by hand. No mounting
  holes (a 34 x 22 mm module, meant to be soldered or taped in).
- LCSC: TP4056 C16581, JST B2B-PH-K-S C131337, green LED C72043 given; the rest found by the run (lcsc_auto).

    cd boards/06_tp4056_lipo_charger && prometheus board.py out/ --spec req.json --json --no-view
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
W, H = 34.0, 22.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
TP_DS = "https://www.lcsc.com/datasheet/lcsc_datasheet_2410121619_TOPPOWER-Nanjing-Extension-Microelectronics-TP4056-42-ESOP8_C16581.pdf"


def build():
    b = Board("tp4056_lipo_charger", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, H / 2), 270, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", (W - 3.5, 6.0), 270, "BAT",
            "JST B2B-PH-K-S", lcsc="C131337")
    b.place("J3", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (15.0, 2.2), 90, "OUT",
            "XFCN PZ254V-11-02P")
    b.place("U1", None, (13.0, 11.0), 0, "TP4056", "TOPPOWER TP4056-42-ESOP8",
            symbol="Battery_Management:TP4056-42-ESOP8", lcsc="C16581")
    b.place("U2", None, (24.5, 9.0), 0, "DW01A", "Fortune DW01A", symbol="Battery_Management:DW01A")
    b.place("Q1", None, (23.3, 4.0), 180, "FS8205A", "Fortune FS8205A", symbol="Prometheus:FS8205A")
    # the parts placed by hand: the placer does not see the hand-drawn power tracks below
    b.place("C2", C08, (17.8, 6.6), 270, "10u", note="BAT output")
    for ref, fp, val, at, rot, note in (
            ("C1", C08, "10u", (9.6, 6.4), 270, "VCC input"), ("C3", C06, "100n", (25.0, 11.45), 0, "DW01A VCC"),
            ("R1", R06, "1.2k", (11.0, 15.2), 0, "PROG: 1 A"), ("R2", R06, "1k", (14.5, 17.2), 180, "CHRG LED"),
            ("R3", R06, "1k", (19.5, 17.2), 180, "STDBY LED"), ("R4", R06, "100", (21.6, 11.8), 270, "DW01A supply"),
            ("R5", R06, "1k", (20.9, 9.0), 180, "DW01A CS"), ("R6", R06, "5.1k", (5.0, 17.6), 0, "CC1")):
        b.place(ref, fp, at, rot, val, note=note)
    b.place("R7", R06, None, 0, "5.1k", note="CC2")     # (one part left to the placer: with every part fixed and
    # tracks drawn, the harness takes the board as routed by the script and routes nothing)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (14.5, 19.5), 0, "red", note="charging")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", (19.5, 19.5), 0, "green", "Hubei KENTO KT-0603G", note="charged",
            lcsc="C72043")
    b.cite("R1", "PROG resistor for 1 A", "1.2k", 4, "1.2k", url=TP_DS)

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] +
          [u("V_{CC}"), u("CE"), ("C1", "1"), ("R2", "1"), ("R3", "1")], amps=1.0, volts=5.0, width=0.8)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [u("GND"), u("EPAD"), u("TEMP"), ("C1", "2"), ("R1", "2"), ("R6", "2"), ("R7", "2"), ("C2", "2"),
           ("Q1", "S2"), ("R5", "2"), ("J3", "2")], amps=2.0)
    b.net("CC1", [("J1", "A5"), ("R6", "1")])
    b.net("CC2", [("J1", "B5"), ("R7", "1")])
    b.net("PROG", [u("PROG"), ("R1", "1")])
    b.net("CHRG", [u("nCHRG"), ("D1", "1")])
    b.net("CHRG_LED", [("R2", "2"), ("D1", "2")])
    b.net("STDBY", [u("nSTDBY"), ("D2", "1")])
    b.net("STDBY_LED", [("R3", "2"), ("D2", "2")])
    b.net("BAT+", [u("BAT"), ("C2", "1"), ("J2", "1"), ("J3", "1"), ("R4", "1")], amps=2.0, volts=3.7, width=1.0)
    b.net("BAT-", [("J2", "2"), ("Q1", "S1"), ("U2", "GND"), ("C3", "2")], amps=2.0, volts=3.7, width=0.6)
    b.net("DW_VCC", [("U2", "VCC"), ("R4", "2"), ("C3", "1")])
    b.net("OD", [("U2", "OD"), ("Q1", "G1")])
    b.net("OC", [("U2", "OC"), ("Q1", "G2")])
    b.net("CS", [("U2", "CS"), ("R5", "1")])
    b.net("FET_D", [("Q1", "1"), ("Q1", "8")])
    b.nc("U2", "TD")
    b.nc("J1", "SBU1", "SBU2", "A6", "A7", "B6", "B7")
    # the 2 A paths drawn by hand on top (marked so the router's rip-up leaves them): BAT -> C2 -> over U2 to the cell, BAT straight down to the output header; the cell's negative straight into Q1's S1 pins (S2 meets
    # the output's ground pin through the top ground fill)
    p5, c2 = b.pad("U1", "5"), b.pad("C2", "1")
    j2p, j2n, j3p = b.pad("J2", "1"), b.pad("J2", "2"), b.pad("J3", "1")
    s1 = b.pad("Q1", "2")
    for net, pts, w in (("BAT+", [p5, (c2[0], p5[1]), c2], 1.0),
                        ("BAT+", [(c2[0], p5[1]), (18.8, 10.1), (18.8, 12.8), (29.6, 12.8), (29.6, 7.6), j2p], 1.0),
                        ("BAT+", [p5, (j3p[0], p5[1] - 0.5), j3p], 1.0),
                        ("BAT-", [j2n, (s1[0], j2n[1])], 0.6)):
        b.track(net, "F", pts, w)
        b.tracks[-1]["fan"] = True
    # OD (U2.1 -> Q1 G1) by hand as well: G1 sits boxed in by the cell's negative, so it leaves through a via and
    # runs under U2 on the bottom
    g1, od = b.pad("Q1", "4"), b.pad("U2", "1")
    for L, pts in (("F", [g1, (27.2, g1[1]), (27.6, 5.4)]), ("B", [(27.6, 5.4), (27.6, 10.75), (22.9, 10.75)]),
                   ("F", [(22.9, 10.75), od])):
        b.track("OD", L, pts, 0.25)
        b.tracks[-1]["fan"] = True
    b.via("OD", (27.6, 5.4))
    b.via("OD", (22.9, 10.75))
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
