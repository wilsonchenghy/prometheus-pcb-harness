"""3.3 V RS-485 transceiver module: SP3485EN (MAX3485-compatible pinout, half duplex, 10 Mbps) with a 120 ohm
termination (in series with a bridged solder jumper JP1: cut it when the node is not at a bus end), 680 ohm fail-safe
bias (A up to 3V3, B down to GND), an SM712 asymmetric TVS (-7 / +12 V, the RS-485 common-mode range) on A and B,
a 3-pin 5.08 mm screw terminal J1 (1 A, 2 B, 3 GND) and a 1x05 2.54 mm UART header J2 (1 3V3, 2 GND, 3 RO, 4 DIR,
5 DI). DIR drives DE and /RE together: low (default, 10 k pull-down) = receive, high = transmit. RO has a 10 k
pull-up so the host's RX idles high while the receiver is off.
Difficulty: 3/5. 2 layers, 1 oz, 32 x 22 mm, JLCPCB, GND poured on both sides.

Assumptions (datasheets not fetched in this run):
- Pin map from KiCad's Interface_UART:SP3485EN symbol (same as MAX3485: RO nRE DE DI GND A B VCC).
- 100 nF VCC decoupling by pin 8 (the usual MaxLinear / Maxim recommendation, value not cited from a page).
- 680 ohm bias with 120 ohm termination gives > 200 mV idle differential at 3.3 V (an engineering choice: a bus
  with termination at both ends and only this node biasing gets about 3.3 * 60 / 1420 = 140 mV on a 60 ohm load;
  bias at one node per bus, fit 560 ohm for a long, doubly terminated bus).
- SM712: pins 1 / 2 to the lines, pin 3 (common) to GND, as KiCad's Diode:SM712_SOT23 symbol.

    prometheus boards/14_rs485_max3485/board.py out/ --spec boards/14_rs485_max3485/req.json
"""
from pcb import Board

R06, R08, C06 = "Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0805_2012Metric", "Capacitor_SMD:C_0603_1608Metric"
TB3 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal"
W, H = 32.0, 22.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
FIXED = {"C1": ((15.6, 15.2), 90)}              # VCC 100n by U1.8


def build():
    b = Board("rs485_max3485", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.0)
    b.place("J1", TB3, (W - 5.2, 4.5), 90, "1 A / 2 B / 3 GND", "Phoenix 1729131")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical", (2.5, 16.08), 0, "UART",
            "XFCN PZ254V-11-05P", lcsc="C2829862")
    b.place("U1", None, (13.0, 11.0), 0, "SP3485EN", "MaxLinear SP3485EN-L/TR", symbol="Interface_UART:SP3485EN",
            lcsc="C8963")
    b.place("D1", None, None, 0, "SM712", "Semtech SM712.TCT", symbol="Diode:SM712_SOT23", note="RS-485 TVS")
    b.place("JP1", "Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm", None, 0, "TERM",
            note="bridged: 120R termination on; cut to remove")
    for ref, fp, val, note in (("C1", C06, "100n", "VCC decoupling"), ("R1", R08, "120R", "termination"),
                               ("R2", R06, "680R", "A bias to 3V3"), ("R3", R06, "680R", "B bias to GND"),
                               ("R4", R06, "10k", "DIR pull-down (receive)"), ("R5", R06, "10k", "RO pull-up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)

    u = lambda n: ("U1", n)
    b.net("VCC", [("J2", "1"), u("VCC"), ("C1", "1"), ("R2", "1"), ("R5", "1")], amps=0.1, volts=3.3)
    b.net("GND", [("J2", "2"), ("J1", "3"), u("GND"), ("C1", "2"), ("R3", "2"), ("R4", "2"), ("D1", "3")], amps=0.1)
    b.net("RO", [u("RO"), ("J2", "3"), ("R5", "2")])
    b.net("DIR", [u("nRE"), u("DE"), ("J2", "4"), ("R4", "1")])
    b.net("DI", [u("DI"), ("J2", "5")])
    b.net("A", [u("A"), ("J1", "1"), ("D1", "1"), ("R2", "2"), ("R1", "1")], volts=12.0)
    b.net("B", [u("B"), ("J1", "2"), ("D1", "2"), ("R3", "1"), ("JP1", "2")], volts=12.0)
    b.net("TERM", [("R1", "2"), ("JP1", "1")])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
