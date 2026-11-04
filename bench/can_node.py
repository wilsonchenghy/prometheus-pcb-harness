"""A CAN-bus node: an STM32F103C8 (LQFP-48, 0.5 mm pitch) with its 8 MHz crystal, an SN65HVD230 transceiver and a
switchable 120 R terminator, an AMS1117 regulator from the bus's 12 V, two JST-XH bus connectors (daisy chain), a
Cortex 10-pin SWD header, a reset and a status LED. 2 layers, 50 x 36 mm, ground poured on both sides; CANH / CANL
declared as a pair. Pin maps are ST's and TI's datasheets as remembered: verify before fab.

    pcb-harness bench/can_node.py out/ --spec bench/specs/can_node.json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pcb import Board  # noqa: E402

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
XH4 = "Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical"
W, H = 50.0, 36.0
OUT = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
# STM32F103C8 LQFP-48
VDD = ["24", "36", "48"]
VSS = ["23", "35", "47"]


def build():
    b = Board("can_node", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2")
    b.place("J1", XH4, (5.0, 4.5), 0, "CAN in", "JST B4B-XH-A")
    b.place("J2", XH4, (W - 15.5, 4.5), 0, "CAN out", "JST B4B-XH-A")
    b.place("J3", "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical", (4.5, H - 9.0), 0, "SWD", "Cortex debug")
    for x, y in ((W - 3.5, H - 3.5), (W - 3.5, 14.0)):
        b.hole((x, y), 2.7)
    b.place("U1", "Package_QFP:LQFP-48_7x7mm_P0.5mm", None, 0, "STM32F103C8T6", "ST STM32F103C8T6", lcsc="C8734")
    b.place("U2", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", None, 0, "SN65HVD230", "TI SN65HVD230DR")
    b.place("U3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", None, 0, "AMS1117-3.3", "AMS AMS1117-3.3")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", None, 0, "8 MHz", "Abracon ABM8-8.000MHZ")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_TL3342", None, 0, "RESET", "E-Switch TL3342F160QG")
    for k in range(1, 6):
        b.place(f"C{k}", C06, None, 0, "100n")                  # C1-C3 VDD, C4 VDDA, C5 transceiver
    for ref, fp, val in (("C6", C06, "1u"), ("C7", C08, "10u"), ("C8", C08, "10u"), ("C9", C06, "20p"),
                         ("C10", C06, "20p"), ("C11", C06, "100n"), ("R1", R06, "120R"), ("R2", R06, "10k"),
                         ("R3", R06, "1k"), ("R4", R06, "10k")):
        b.place(ref, fp, None, 0, val)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "blue", "status")

    b.net("VIN", [("J1", "1"), ("J2", "1"), ("U3", "3"), ("C8", "1")], amps=0.3, volts=12.0)
    b.net("3V3", [("U3", "2"), ("C7", "1"), ("U1", "1"), ("U1", "9"), ("C1", "1"), ("C2", "1"), ("C3", "1"),
                  ("C4", "1"), ("C6", "1"), ("U2", "3"), ("C5", "1"), ("J3", "1"), ("R4", "1")] +
          [("U1", p) for p in VDD], amps=0.2, volts=3.3)
    b.net("GND", [("J1", "4"), ("J2", "4"), ("U3", "1"), ("C7", "2"), ("C8", "2"), ("U1", "8"), ("C1", "2"),
                  ("C2", "2"), ("C3", "2"), ("C4", "2"), ("C6", "2"), ("U2", "2"), ("U2", "8"), ("C5", "2"),
                  ("Y1", "2"), ("Y1", "4"), ("C9", "2"), ("C10", "2"), ("J3", "3"), ("J3", "5"), ("J3", "9"),
                  ("R2", "2"), ("SW1", "2"), ("C11", "2"), ("D1", "1")] + [("U1", p) for p in VSS], amps=0.3)
    b.net("CANH", [("U2", "7"), ("J1", "2"), ("J2", "2"), ("R1", "1")])
    b.net("CANL", [("U2", "6"), ("J1", "3"), ("J2", "3"), ("R1", "2")])
    b.pair("CANH", "CANL", max_skew=2.0)
    b.net("CAN_TX", [("U1", "33"), ("U2", "1")])                  # PA12
    b.net("CAN_RX", [("U1", "32"), ("U2", "4")])                  # PA11
    b.net("OSC_IN", [("U1", "5"), ("Y1", "1"), ("C9", "1")])
    b.net("OSC_OUT", [("U1", "6"), ("Y1", "3"), ("C10", "1")])
    b.net("NRST", [("U1", "7"), ("SW1", "1"), ("C11", "1"), ("J3", "10"), ("R4", "2")])
    b.net("SWDIO", [("U1", "34"), ("J3", "2")])                   # PA13
    b.net("SWCLK", [("U1", "37"), ("J3", "4")])                   # PA14
    b.net("SWO", [("U1", "39"), ("J3", "6")])                     # PB3
    b.net("BOOT0", [("U1", "44"), ("R2", "1")])
    b.net("LED", [("U1", "2"), ("R3", "1")])                      # PC13
    b.net("LEDK", [("R3", "2"), ("D1", "2")])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
