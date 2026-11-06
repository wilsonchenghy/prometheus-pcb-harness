"""IR obstacle sensor: a 940 nm IR LED (5 mm) and a phototransistor (5 mm) side by side at the front edge, an LM393
comparator against a 10k trimmer threshold, an open-collector output (low = obstacle) with a 10k pull-up and an
indicator LED, a power LED, and a 3-pin header (VCC, GND, OUT). 3.3-5 V. Difficulty 1/5. 2 layers, 32 x 16 mm.

IR LED current: (5 V - 1.2 V) / 150 ohm = 25 mA (typical 5 mm IR LED, 50 mA max); at 3.3 V ~14 mA. Phototransistor
(Everlight PT333-3C) with 10k to VCC: more reflected light pulls its collector down below the trimmer's threshold
and the output goes low. LM393 SOIC-8 pins from KiCad's Comparator:LM393 (1 OUTA, 2 -A, 3 +A, 4 GND, 5 +B, 6 -B,
7 OUTB, 8 VCC): the unused B half has both inputs at GND and its output open. 5 mm parts: pad 1 is the cathode /
emitter (KiCad LED_D5.0mm: the flat side): check against the parts' drawings.
"""
from pcb import Board

R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
W, H = 32.0, 16.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("ir_obstacle_lm393", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("D1", "LED_THT:LED_D5.0mm", (3.6, 13.0), 270, "IR 940nm", "Everlight IR333-A")
    b.place("Q1", "LED_THT:LED_D5.0mm", (3.6, 5.2), 270, "PT333-3C", "Everlight PT333-3C")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (W - 2.6, H / 2 + 2.54), 0,
            "VCC GND OUT", "XFCN PZ254V-11-03P")
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (17.0, 8.0), 0, "LM393", "TI LM393DR",
            symbol="Comparator:LM393", lcsc="C7955")
    b.place("RV1", "Potentiometer_SMD:Potentiometer_Bourns_3314J_Vertical", (10.0, 8.0), 0, "10k",
            "Bourns 3314J-1-103E")
    for ref, val, note in (("R1", "150", "IR LED"), ("R2", "10k", "phototransistor"), ("R3", "10k", "OUT pull-up"),
                           ("R4", "1k", "OUT LED"), ("R5", "1k", "PWR LED")):
        b.place(ref, R06, None, 0, val, note=note)
    b.place("C1", C06, (21.6, 10.0), 90, "100n", note="VCC")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", None, 0, "green", note="obstacle")
    b.place("D3", "LED_SMD:LED_0603_1608Metric", None, 0, "red", note="power")

    b.net("VCC", [("J1", "1"), ("U1", "8"), ("C1", "1"), ("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1"),
                  ("R5", "1"), ("RV1", "3")], amps=0.05)
    b.net("GND", [("J1", "2"), ("U1", "4"), ("U1", "5"), ("U1", "6"), ("C1", "2"), ("D1", "1"), ("Q1", "1"),
                  ("RV1", "1"), ("D3", "1")], amps=0.05)
    b.net("IRA", [("R1", "2"), ("D1", "2")])
    b.net("SENSE", [("Q1", "2"), ("R2", "2"), ("U1", "3")])
    b.net("THR", [("RV1", "2"), ("U1", "2")])
    b.net("OUT", [("U1", "1"), ("R3", "2"), ("D2", "1"), ("J1", "3")])
    b.net("LEDA", [("R4", "2"), ("D2", "2")])
    b.net("PLED", [("R5", "2"), ("D3", "2")])
    b.nc("U1", "7")
    b.pour("GND", "B", OUT)
    return b
