"""LED chaser: an NE555 astable clocks a CD4017 decade counter whose ten outputs light ten LEDs in a ring, one at a
time; a 100k trimmer sets the speed (~0.7-30 Hz). 5-12 V in on a 2-pin screw terminal, a reverse-polarity diode.
Difficulty 2/5. 2 layers, 56 x 56 mm, JLCPCB.

Values: f = 1.44 / ((R1 + 2 (R2 + RV1)) C1) with R1 = 1k, R2 = 1k, RV1 = 0-100k, C1 = 1 uF -> ~0.7 Hz to ~480 Hz
(NE555 datasheet, astable). One LED is on at a time, so all ten share one cathode resistor R3 (470 ohm: ~10-20 mA
at 9-12 V, CD4017 output current limited, a few mA at 5 V). CD4017 pin map: KiCad's 4xxx:4017 symbol (CD4017B).
"""
import math
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
TB2 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
W, H = 56.0, 56.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
CX, CY, RING = W / 2, H / 2, 19.0


def build():
    b = Board("led_chaser_4017", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=3.0)
    b.place("J1", TB2, (4.5, 4.0), 0, "5-12V", "Phoenix 1729128", overhang=True)
    b.place("U1", None, (CX - 6.0, CY + 1.0), 90, "NE555", "TI NE555DR", symbol="Timer:NE555D", lcsc="C7593")
    b.place("U2", "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", (CX + 5.0, CY), 0, "CD4017", "TI CD4017BM96",
            symbol="4xxx:4017", lcsc="C7437")
    b.place("RV1", "Potentiometer_SMD:Potentiometer_Bourns_3314J_Vertical", (CX - 6.0, CY - 8.5), 0, "100k",
            "Bourns 3314J-1-104E")
    for ref, fp, val, note in (("R1", R06, "1k", "555 R1"), ("R2", R06, "1k", "555 R2"), ("R3", R06, "470", "LED common"),
                               ("C1", C08, "1u", "timing"), ("C2", C06, "10n", "CONT"), ("C3", C06, "100n", "555 VCC"),
                               ("C4", C06, "100n", "4017 VDD"), ("C5", C08, "10u 25V", "bulk")):
        b.place(ref, fp, None, 0, val, note=note)
    b.place("D11", "Diode_SMD:D_SMA", None, 0, "SS14", "MDD SS14")
    # ten LEDs round the edge, Q0 at the top, clockwise
    for i in range(10):
        a = math.pi / 2 - 2 * math.pi * i / 10
        b.place(f"D{i + 1}", "LED_SMD:LED_0805_2012Metric", (round(CX + RING * math.cos(a), 2),
                round(CY + RING * math.sin(a), 2)), round(math.degrees(a)) % 360, "red")
    b.hole((4.0, H - 4.0), d=3.2)
    b.hole((W - 4.0, H - 4.0), d=3.2)
    b.hole((W - 4.0, 4.0), d=3.2)

    u, c = (lambda n: ("U1", n)), (lambda n: ("U2", n))
    b.net("VIN", [("J1", "1"), ("D11", "2")], amps=0.2, volts=12.0)
    b.net("VCC", [("D11", "1"), u("VCC"), u("nRST"), c("VDD"), ("R1", "1"), ("C3", "1"), ("C4", "1"), ("C5", "1")],
          amps=0.2, volts=12.0)
    b.net("GND", [("J1", "2"), u("GND"), c("VSS"), c("CKEN"), c("Reset"), ("C1", "2"), ("C2", "2"), ("C3", "2"),
                  ("C4", "2"), ("C5", "2"), ("R3", "1")], amps=0.2)
    b.net("DIS", [u("DISCH"), ("R1", "2"), ("R2", "1")])
    b.net("POT", [("R2", "2"), ("RV1", "1"), ("RV1", "2")])          # the trimmer as a variable resistor
    b.net("TIM", [u("TRIG"), u("THRES"), ("C1", "1"), ("RV1", "3")])
    b.net("CV", [u("CONT"), ("C2", "1")])
    b.net("CLK", [u("OUT"), c("CLK")])
    for i in range(10):
        b.net(f"Q{i}", [c(f"Q{i}"), (f"D{i + 1}", "2")])
    b.net("LEDK", [(f"D{i + 1}", "1") for i in range(10)] + [("R3", "2")])
    b.nc("U2", "Cout")
    b.pour("GND", "B", OUT)
    return b
