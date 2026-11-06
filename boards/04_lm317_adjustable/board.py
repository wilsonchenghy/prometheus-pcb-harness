"""Adjustable linear regulator, 1.25-12 V out at up to 1.5 A: LM317 (TO-220, vertical, room for a clip-on heatsink)
set by a 2k multi-turn trimpot, fed from a 5.5 x 2.1 mm DC barrel jack through a 1N5822 Schottky (reverse-polarity
protection), input and output electrolytics plus ceramics, a 10 uF ADJ bypass with the datasheet's two protection
diodes, a 5.0 mm screw terminal out and a red power LED on the protected input. All through-hole: hand-solderable.
Difficulty: 2/5. 2 layers, 56 x 40 mm, JLCPCB, ground poured on both sides, 4 x M3 holes.

Values (LM317 datasheet, TI SLVS044, https://www.ti.com/lit/ds/symlink/lm317.pdf):
- VOUT = 1.25 V x (1 + R2 / R1) + IADJ x R2, R1 = 220R (5.7 mA through it: above the ~3.5 mA minimum load),
  R2 = the 2k trimpot (rheostat: its wiper tied to one end): 1.25 V at 0 ohm to 12.6 V at 2k (+0.1 V from IADJ).
- CIN 100 nF ceramic at the input pin plus 100 uF 35 V bulk; COUT 47 uF 25 V + 100 nF; CADJ 10 uF 25 V (ripple
  rejection). D2 (1N4002, output to input) and D3 (1N4002, ADJ to output) are the datasheet's protection diodes:
  they discharge COUT and CADJ when the input or output is shorted.
- Input: 15-30 V for the full 12 V out (dropout ~2.5 V + the Schottky's ~0.5 V); max 35 V (LM317's input rating
  less the margin). Barrel jack pin 1 = centre (+), pins 2 and 3 = sleeve and its switch, both to ground (assumed
  from KiCad's BarrelJack_Horizontal: check against the jack bought).
- Dissipation (VIN - VOUT) x I: over ~1.5 W the TO-220 needs a heatsink (no-heatsink RthJA ~ 50 C/W).
- Power LED on the protected input: 4.7k (2.7 mA at 15 V, 6 mA / 0.17 W at 30 V).
Currents: the input, regulator and output paths 1.5 A; LED and ADJ milliamps.

    cd boards/04_lm317_adjustable && prometheus board.py out/ --spec req.json --json --no-view
"""
from pcb import Board

RAX = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"
CDISC = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"
D41 = "Diode_THT:D_DO-41_SOD81_P7.62mm_Horizontal"
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal"
W, H = 56.0, 40.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
DS = "https://www.ti.com/lit/ds/symlink/lm317.pdf"


def build():
    b = Board("lm317_adjustable", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", "Connector_BarrelJack:BarrelJack_Horizontal", (15.0, 20.0), 0, "DC 15-30V", "CUI PJ-002A",
            symbol="Connector:Barrel_Jack_Switch")
    b.place("J2", TB, (W - 5.3, 17.5), 90, "VOUT", "Phoenix 1729128")
    for x, y in ((3.5, 3.5), (W - 3.5, 3.5), (3.5, H - 3.5), (W - 3.5, H - 3.5)):
        b.hole((x, y), 3.2)
    b.place("U1", None, (30.0, 22.0), 0, "LM317", "TI LM317T", symbol="Regulator_Linear:LM317_TO-220")
    b.place("D1", "Diode_THT:D_DO-201AD_P5.08mm_Vertical_AnodeUp", None, 0, "1N5822", "MDD 1N5822",
            note="reverse polarity")
    b.place("D2", D41, None, 0, "1N4002", "MDD 1N4002", note="VOUT -> VIN protection")
    b.place("D3", D41, None, 0, "1N4002", "MDD 1N4002", note="ADJ -> VOUT protection")
    b.place("RV1", "Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical", None, 0, "2k", "Bourns 3296W-1-202LF",
            note="VOUT set")
    b.place("R1", RAX, None, 0, "220 1%", "Yageo MFR-25FBF52-220R", note="VO to ADJ")
    b.place("R2", RAX, None, 0, "4.7k", "Yageo MFR-25FBF52-4K7", note="power LED")
    b.place("C1", "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm", None, 0, "100u 35V", "Panasonic EEU-FR1V101",
            note="input bulk")
    b.place("C2", CDISC, None, 0, "100n 50V", "Kemet C320C104K5R5TA", note="VI bypass")
    b.place("C3", "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm", None, 0, "10u 25V", "Panasonic EEU-FR1E100",
            note="CADJ")
    b.place("C4", "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm", None, 0, "47u 25V", "Panasonic EEU-FR1E470",
            note="output")
    b.place("C5", CDISC, None, 0, "100n 50V", "Kemet C320C104K5R5TA", note="output ceramic")
    b.place("D4", "LED_THT:LED_D3.0mm", None, 0, "red", "Kingbright L-934ID", note="power")
    b.cite("R1", "VOUT = VREF (1 + R2 / R1) + IADJ R2", "220", 16, "VO = VREF (1 + R2 / R1) + (IADJ × R2)", url=DS)

    u = lambda n: ("U1", n)
    b.net("VRAW", [("J1", "1"), ("D1", "2")], amps=1.5, volts=24.0, width=1.0)
    b.net("VIN", [("D1", "1"), u("VI"), ("C1", "1"), ("C2", "1"), ("D2", "1"), ("R2", "1")], amps=1.5, volts=24.0, width=1.0)
    b.net("GND", [("J1", "2"), ("J1", "3"), ("J2", "2"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"),
                  ("C5", "2"), ("RV1", "2"), ("RV1", "3"), ("D4", "1")], amps=1.5)
    b.net("VOUT", [u("VO"), ("R1", "1"), ("C4", "1"), ("C5", "1"), ("D2", "2"), ("D3", "1"), ("J2", "1")],
          amps=1.5, volts=12.0, width=1.0)
    b.net("ADJ", [u("ADJ"), ("R1", "2"), ("RV1", "1"), ("C3", "1"), ("D3", "2")])
    b.net("LED", [("R2", "2"), ("D4", "2")])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT, fill=True)
    return b
