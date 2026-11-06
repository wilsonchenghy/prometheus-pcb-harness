"""Electret microphone preamp: an electret capsule on a 2-pin JST-PH (+, GND), biased through 2.2k from a filtered
supply, AC-coupled (1 uF) into an MCP6002 rail-to-rail op-amp as a non-inverting stage with gain 1 + 100k/1k =
101 (40 dB, ~16 kHz bandwidth at the MCP6002's 1 MHz GBW; 47 pF across the feedback limits it to ~34 kHz), biased
at mid-rail by the second op-amp buffering a 100k/100k divider. Output AC-coupled (10 uF) and also DC (mid-rail,
for an ADC) on a 4-pin header (VCC, GND, AC OUT, DC OUT). 2.7-5.5 V. Difficulty 2/5. 2 layers, 30 x 18 mm, JLCPCB.

MCP6002 SOIC-8 pins from KiCad's Amplifier_Operational:MCP6002-xSN: 1 OUTA, 2 -A, 3 +A, 4 V-, 5 +B, 6 -B, 7 OUTB,
8 V+ (wired by number: the symbol's units name pins only +/-). Values are the usual electret preamp (bias 2.2k as
CUI's measurement circuit, RL = 2.2 k); the gain is a choice, not a datasheet value.
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
W, H = 30.0, 18.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("mic_preamp_mcp6002", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", (4.6, H / 2 - 1.0), 270, "MIC",
            "JST B2B-PH-K-S")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (W - 2.6, H / 2 + 3.81), 0,
            "VCC GND AC DC", "XFCN PZ254V-11-04P")
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (15.0, 9.0), 0, "MCP6002", "Microchip MCP6002T-I/SN",
            symbol="Amplifier_Operational:MCP6002-xSN", lcsc="C7377")
    for ref, fp, val, note in (("R1", R06, "2.2k", "mic bias"), ("R2", R06, "100", "bias filter"),
                               ("C1", C08, "10u", "bias filter"), ("C2", C06, "1u", "mic coupling"),
                               ("R3", R06, "100k", "+A to VREF"), ("R4", R06, "1k", "gain to VREF"),
                               ("C3", C06, "10u", "gain-leg DC block"), ("R5", R06, "100k", "feedback"),
                               ("C4", C06, "47p", "feedback roll-off"), ("R6", R06, "100k", "divider top"),
                               ("R7", R06, "100k", "divider bottom"), ("C5", C06, "1u", "divider filter"),
                               ("C6", C06, "100n", "VDD decoupling"), ("C7", C08, "10u", "output coupling"),
                               ("R8", R06, "100", "output")):
        b.place(ref, fp, *(((19.9, 11.6), 90) if ref == "C6" else (None, 0)), val, note=note)

    b.net("VCC", [("J2", "1"), ("U1", "8"), ("C6", "1"), ("R2", "1"), ("R6", "1")], amps=0.05, volts=5.0)
    b.net("GND", [("J2", "2"), ("U1", "4"), ("C6", "2"), ("C1", "2"), ("C5", "2"), ("R7", "2"), ("J1", "2")])
    b.net("VB", [("R2", "2"), ("C1", "1"), ("R1", "1")])
    b.net("MIC", [("J1", "1"), ("R1", "2"), ("C2", "1")])
    b.net("INP", [("C2", "2"), ("R3", "1"), ("U1", "3")])
    b.net("FB", [("U1", "2"), ("R5", "1"), ("C4", "1"), ("R4", "1")])
    b.net("GLEG", [("R4", "2"), ("C3", "1")])
    b.net("OUT", [("U1", "1"), ("R5", "2"), ("C4", "2"), ("R8", "1"), ("J2", "4")])
    b.net("ROUT", [("R8", "2"), ("C7", "1")])
    b.net("AC", [("C7", "2"), ("J2", "3")])
    b.net("DIV", [("R6", "2"), ("R7", "1"), ("C5", "1"), ("U1", "5")])
    b.net("VREF", [("U1", "7"), ("U1", "6"), ("R3", "2"), ("C3", "2")])
    b.pour("GND", "B", OUT)
    return b
