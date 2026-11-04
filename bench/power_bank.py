"""A one-cell LiPo power bank: USB-C in, a TP4056 linear charger with its charge and standby LEDs, a DW01A + FS8205A
protection pair in the battery's negative leg, an MT3608 boost to 5 V and a USB-A port out. 2 layers, 52 x 32 mm,
ground poured on the bottom. Tests current sizing: the battery and boost paths carry 2 A. Only the connectors and
the mounting holes are placed by hand; place_and_route does the rest. Pin maps are the makers' datasheets as
remembered (TP4056, DW01A, FS8205A, MT3608): verify before fab.

    pcb-harness bench/power_bank.py out/ --spec bench/specs/power_bank.json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pcb import Board  # noqa: E402

R06, C06, C08, C12 = ("Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric",
                      "Capacitor_SMD:C_0805_2012Metric", "Capacitor_SMD:C_1206_3216Metric")
W, H = 52.0, 32.0
OUT = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]


def build():
    b = Board("power_bank", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2")
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.5, H / 2), 270,
            "USB-C in", "GCT USB4105-GF-A", overhang=True)
    b.place("J2", "Connector_USB:USB_A_Molex_105057_Vertical", (39.5, 17.0), 0, "USB-A out", "Molex 105057-0001")
    b.place("J3", "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal", (16.0, 25.0), 180, "battery", "JST S2B-PH-K-S")
    for x, y in ((4.0, 4.0), (W - 4.0, 4.0), (4.0, H - 4.0), (W - 4.0, H - 4.0)):
        b.hole((x, y), 2.7)
    b.place("U1", "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm", None, 0, "TP4056", "NanJing Top Power TP4056")
    b.place("U2", "Package_TO_SOT_SMD:SOT-23-6", None, 0, "DW01A", "Fortune DW01A")
    b.place("Q1", "Package_SO:TSSOP-8_4.4x3mm_P0.65mm", None, 0, "FS8205A", "Fortune FS8205A")
    b.place("U3", "Package_TO_SOT_SMD:SOT-23-6", None, 0, "MT3608", "Aerosemi MT3608")
    b.place("L1", "Inductor_SMD:L_Bourns_SRN6045TA", None, 0, "22u", "Bourns SRN6045TA-220M")
    b.place("D3", "Diode_SMD:D_SOD-123", None, 0, "SS34", "SS34 Schottky")
    for ref, fp, val in (("C1", C08, "10u"), ("C2", C08, "10u"), ("C3", C06, "100n"), ("C4", C12, "22u"),
                         ("C5", C12, "22u"), ("R1", R06, "1.2k"), ("R2", R06, "1k"), ("R3", R06, "1k"), ("R4", R06, "100R"),
                         ("R5", R06, "2k"), ("R6", R06, "75k"), ("R7", R06, "10k"), ("R8", R06, "5.1k"), ("R9", R06, "5.1k")):
        b.place(ref, fp, None, 0, val)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "red", "charging")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", None, 0, "green", "charged")

    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U1", "4"), ("U1", "8"), ("C1", "1"),
                                                                  ("D1", "2"), ("D2", "2")], amps=1.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12")] + [("U1", "3"), ("U1", "9"), ("U1", "1"),
          ("C1", "2"), ("R1", "2"), ("Q1", "6"), ("Q1", "7"), ("R5", "2"), ("U3", "2"), ("R7", "2"), ("C4", "2"),
          ("C5", "2"), ("J2", "4"), ("R8", "2"), ("R9", "2")], amps=2.0)
    b.net("CC1", [("J1", "A5"), ("R8", "1")])
    b.net("CC2", [("J1", "B5"), ("R9", "1")])
    b.net("PROG", [("U1", "2"), ("R1", "1")])
    b.net("CHRG", [("U1", "7"), ("R2", "2")])
    b.net("CHRG_LED", [("R2", "1"), ("D1", "1")])
    b.net("STDBY", [("U1", "6"), ("R3", "2")])
    b.net("STDBY_LED", [("R3", "1"), ("D2", "1")])
    # the cell: + to the charger and the boost; - through the protection FETs to ground
    b.net("BAT", [("U1", "5"), ("C2", "1"), ("J3", "1"), ("R4", "1"), ("U3", "5"), ("U3", "4"), ("L1", "1"),
                  ("C4", "1")], amps=2.0)
    b.net("BATN", [("J3", "2"), ("U2", "6"), ("Q1", "2"), ("Q1", "3"), ("C3", "2"), ("C2", "2")], amps=2.0)
    b.net("DW_VCC", [("U2", "5"), ("R4", "2"), ("C3", "1")])
    b.net("OD", [("U2", "1"), ("Q1", "4")])
    b.net("OC", [("U2", "3"), ("Q1", "5")])
    b.net("CS", [("U2", "2"), ("R5", "1")])
    b.net("FET_D", [("Q1", "1"), ("Q1", "8")])                   # (the common drain, joined inside the package:
    # the cell's current flows S1 -> S2 within the FS8205A, so this tie carries none)
    # the boost: L1 from the cell to SW, D3 to the output, R6 / R7 set 5 V
    b.net("SW", [("U3", "1"), ("L1", "2"), ("D3", "2")], amps=2.0)
    b.net("VOUT", [("D3", "1"), ("C5", "1"), ("R6", "1"), ("J2", "1")], amps=1.5)
    b.net("FB", [("U3", "3"), ("R6", "2"), ("R7", "1")])
    b.net("USB_A_D", [("J2", "2"), ("J2", "3")])                 # (D+ tied to D-: a dedicated charging port)
    b.pour("GND", "B", OUT)
    return b
