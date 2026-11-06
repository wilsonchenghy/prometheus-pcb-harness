"""NE555 PWM LED dimmer: 12 V in (2-pin screw terminal), an NE555 astable at ~1 kHz whose duty cycle is set by a 100k
potentiometer with steering diodes (1N4148W) on charge and discharge, driving an AO3400A logic-level MOSFET that
switches an LED strip on a 2-pin screw terminal (up to 2 A). Difficulty 1/5. 2 layers, 40 x 30 mm, JLCPCB.

f = 1.44 / ((R1 + RV1) * C1) = 1.44 / ((1k + 100k) * 10n) ~ 1.4 kHz (NE555 datasheet, astable equation), duty
~1-99 %. The 555 is powered from 12 V directly (NE555 is 4.5-16 V). Values are the classic duty-cycle circuit.
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
TB2 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
W, H = 40.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("ne555_pwm_dimmer", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", TB2, (4.0, 10.0), 270, "12V IN", "Phoenix 1729128", overhang=True)
    b.place("J2", TB2, (W - 4.0, 18.0), 90, "LED OUT", "Phoenix 1729128", overhang=True)
    b.place("U1", None, (18.0, 15.0), 0, "NE555", "TI NE555DR", symbol="Timer:NE555D", lcsc="C7593")
    b.place("RV1", "Potentiometer_THT:Potentiometer_Bourns_3386P_Vertical", (17.0, 22.0), 0, "100k",
            "Bourns 3386P-1-104LF")
    b.place("Q1", "Package_TO_SOT_SMD:SOT-23", None, 0, "AO3400A", "AOS AO3400A", lcsc="C20917")
    for ref, fp, val, note in (("R1", R06, "1k", "to VCC"), ("R2", R06, "100", "gate"), ("R3", R06, "100k", "gate pull-down"),
                               ("R4", R06, "4.7k", "PWR LED"), ("C1", C06, "10n", "timing"), ("C2", C06, "10n", "CONT"),
                               ("C3", C06, "100n", "VCC decoupling"), ("C4", C08, "10u 25V", "bulk")):
        b.place(ref, fp, FIXED.get(ref), 0, val, note=note)
    b.place("D1", "Diode_SMD:D_SOD-123", None, 0, "1N4148W", "Diodes 1N4148W-7-F")
    b.place("D2", "Diode_SMD:D_SOD-123", None, 0, "1N4148W", "Diodes 1N4148W-7-F")
    b.place("D3", "LED_SMD:LED_0603_1608Metric", None, 0, "green")
    b.hole((3.5, H - 3.5), d=3.2)
    b.hole((W - 3.5, 3.5), d=3.2)

    u = lambda n: ("U1", n)
    b.net("12V", [("J1", "1"), u("VCC"), u("nRST"), ("R1", "1"), ("C3", "1"), ("C4", "1"), ("J2", "1"), ("R4", "1")],
          amps=2.0, volts=12.0)
    b.net("GND", [("J1", "2"), u("GND"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), ("R3", "2"), ("Q1", "2"),
                  ("D3", "1")], amps=2.0)
    b.net("DIS", [u("DISCH"), ("R1", "2"), ("RV1", "2")])          # the wiper
    b.net("CHG", [("RV1", "1"), ("D1", "2")])                       # D1 anode: charge path
    b.net("DSC", [("RV1", "3"), ("D2", "1")])                       # D2 cathode: discharge path
    b.net("TIM", [u("TRIG"), u("THRES"), ("C1", "1"), ("D1", "1"), ("D2", "2")])
    b.net("CV", [u("CONT"), ("C2", "1")])
    b.net("PWM", [u("OUT"), ("R2", "1")])
    b.net("GATE", [("R2", "2"), ("R3", "1"), ("Q1", "1")])
    b.net("LED-", [("Q1", "3"), ("J2", "2")], amps=2.0, volts=12.0)
    b.net("PLED", [("R4", "2"), ("D3", "2")])
    b.pour("GND", "B", OUT)
    return b


FIXED = {"C3": (21.5, 18.5)}       # by U1.8 VCC
