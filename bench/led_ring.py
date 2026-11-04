"""An addressable LED ring: twelve WS2812B LEDs round a 50 mm round board, each with its 100 nF, driven by an ATtiny85
through a 330 R series resistor, a button, a 2x03 ISP header and a JST-XH power / data connector. The LEDs and their
capacitors are placed on the ring by hand (the design's look); the rest by place_and_route. 2 layers, ground poured
on the bottom; the 5 V net carries the LEDs' 0.8 A. Pin maps are the WS2812B and ATtiny85 datasheets as remembered:
verify before fab.

    pcb-harness bench/led_ring.py out/ --spec bench/specs/led_ring.json
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pcb import Board  # noqa: E402

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
R_BOARD, N = 25.0, 12
OUT = [(R_BOARD * math.cos(2 * math.pi * k / 72), R_BOARD * math.sin(2 * math.pi * k / 72)) for k in range(72)]


def build():
    b = Board("led_ring", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=0.0)
    for k in range(N):                                           # the ring: LED k at 30 degrees apart, facing out
        a = 2 * math.pi * k / N
        b.place(f"LED{k + 1}", "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
                (round(20.0 * math.cos(a), 3), round(20.0 * math.sin(a), 3)), round(math.degrees(a) + 90, 2) % 360,
                "WS2812B", "Worldsemi WS2812B")
        a2 = a + math.pi / N
        b.place(f"C{k + 1}", C06, (round(14.8 * math.cos(a2), 3), round(14.8 * math.sin(a2), 3)),
                round(math.degrees(a2), 2) % 360, "100n")
    b.place("J1", "Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical", (-2.5, -8.5), 0, "5V / DIN / GND",
            "JST B3B-XH-A")
    b.hole((0.0, 0.0), 3.2)
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", None, 0, "ATtiny85", "Microchip ATTINY85-20SU")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical", None, 0, "ISP", "")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_TL3342", None, 0, "MODE", "E-Switch TL3342F160QG")
    for ref, fp, val in (("C13", C08, "10u"), ("C14", C06, "100n"), ("R1", R06, "330R"), ("R2", R06, "10k")):
        b.place(ref, fp, None, 0, val)

    b.net("5V", [("J1", "1"), ("C13", "1"), ("C14", "1"), ("U1", "8"), ("J2", "2"), ("R2", "1")] +
          [(f"LED{k + 1}", "1") for k in range(N)] + [(f"C{k + 1}", "1") for k in range(N)], amps=0.8)
    b.net("GND", [("J1", "3"), ("C13", "2"), ("C14", "2"), ("U1", "4"), ("J2", "6"), ("SW1", "2")] +
          [(f"LED{k + 1}", "3") for k in range(N)] + [(f"C{k + 1}", "2") for k in range(N)], amps=0.8)
    b.net("DATA", [("U1", "3"), ("R1", "1")])                     # PB4
    b.net("DIN1", [("R1", "2"), ("LED1", "4"), ("J1", "2")])
    for k in range(N - 1):                                       # the chain: each LED's out to the next one's in
        b.net(f"D{k + 2}", [(f"LED{k + 1}", "2"), (f"LED{k + 2}", "4")])
    b.net("BUTTON", [("U1", "2"), ("SW1", "1")])                  # PB3
    b.net("MOSI", [("U1", "5"), ("J2", "4")])                     # PB0
    b.net("MISO", [("U1", "6"), ("J2", "1")])                     # PB1
    b.net("SCK", [("U1", "7"), ("J2", "3")])                      # PB2
    b.net("RESET", [("U1", "1"), ("J2", "5"), ("R2", "2")])       # PB5
    b.pour("GND", "B", OUT)
    return b
