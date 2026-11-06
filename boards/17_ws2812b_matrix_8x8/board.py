"""8 x 8 WS2812B (5050) RGB LED matrix: 64 LEDs on an 8 mm pitch, each with its own 100 nF decoupling capacitor,
the data line chained column by column in a serpentine (column 0 bottom -> top, column 1 top -> bottom, ...,
column 7 ends at the bottom right next to the output header). A 330 ohm series resistor at the data input, a
1000 uF / 10 V bulk electrolytic across the input, a 2-pin 5.08 mm screw terminal J3 for the full-brightness feed,
3-pin 2.54 mm input / output headers J1 / J2 (1 5V, 2 DIN / DOUT, 3 GND) to chain boards.
Difficulty: 4/5. 2 layers, 1 oz, 68 x 80 mm (the 68 x 67 mm LED field and a 13 mm connector strip below it),
JLCPCB. 5 V is the top-layer pour, GND the bottom-layer pour (each LED's and cap's GND pad drops through its own via).

Current: 64 x 60 mA (all three colours full on) = 3.84 A on 5V and GND. The pours are the whole board on each
layer (68 mm wide 1 oz copper: a few mV of drop and no heating at 3.84 A). The 2.54 mm headers are rated about 3 A
per pin: feed full white through J3 (MKDS 1.5, 13.5 A); the headers are for chaining data and moderate brightness.

Choices and assumptions:
- LED pin map from KiCad's LED:WS2812B symbol, which matches the Worldsemi datasheet (1 VDD, 2 DOUT, 3 VSS, 4 DIN).
- 100 nF per LED: the datasheet's typical application circuit (p.5: "104" at each LED's VDD), cited.
- 330 ohm series data resistor: the usual Adafruit / community recommendation to damp ringing on the first DIN
  (not from the datasheet). Data level: VIH = 0.7 VDD (p.3) = 3.5 V at 5 V: a 3.3 V MCU needs a level shifter
  (e.g. 74AHCT125) or a supply of ~4.3 V for reliable input; not fitted here.
- 1000 uF bulk across the input: the usual Adafruit NeoPixel guidance (protects the first pixel from the inrush
  spike), not from the datasheet. Nichicon UVR1A102MPD, 10 x 16 mm, 5 mm lead pitch.
- LED rotation: columns going up are turned 270 deg (DIN bottom right, DOUT top left), going down 90 deg, so each
  DOUT -> DIN hop is a 4.5 mm diagonal through the gap between two LEDs.
- Size: ~66 x 66 was asked; at 8 mm pitch the right-hand column's caps (4 mm right of each LED) sat on the edge of a
  66 mm board and pinched the 5 V pour (a current finding), so the board is 68 mm wide and 1 mm taller.
- No LCSC number given for the LEDs or the bulk cap (the run's lookup found none by MPN): JLCPCB stocks the
  WS2812B-B/W (likely C2761795, not verified here): confirm and add lcsc= before ordering assembly.
- No mounting holes (the LED field fills the board; the strip holds the connectors): mount by the strip or with
  adhesive standoffs.

    prometheus boards/17_ws2812b_matrix_8x8/board.py out/ --spec boards/17_ws2812b_matrix_8x8/req.json
"""
from pcb import Board

C06, R06 = "Capacitor_SMD:C_0603_1608Metric", "Resistor_SMD:R_0603_1608Metric"
LED = "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm"
DS = "https://cdn-shop.adafruit.com/datasheets/WS2812B.pdf"
W, H = 68.0, 80.0
STRIP = 13.0                                   # connector strip at the bottom
PITCH = 8.0
X0, Y0 = 4.5, STRIP + 5.0                      # centre of the bottom-left LED
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def chain():
    """the LEDs in data order: (ref, cap ref, x, y, rot)"""
    out = []
    for col in range(8):
        rows = range(8) if col % 2 == 0 else range(7, -1, -1)
        for row in rows:
            k = len(out) + 1
            out.append((f"D{k}", f"C{k}", X0 + col * PITCH, Y0 + row * PITCH, 270 if col % 2 == 0 else 90))
    return out


def build():
    b = Board("ws2812b_matrix_8x8", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J3", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
            (5.0, 5.4), 0, "5V GND", "Phoenix 1729128")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (17.0, 3.0), 90, "IN 5V DIN GND",
            "XFCN PZ254V-11-03P", lcsc="C2829860")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (57.9, 3.0), 90, "OUT 5V DOUT GND",
            "XFCN PZ254V-11-03P", lcsc="C2829860")
    b.place("C65", "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm", (32.0, 6.5), 0, "1000u 10V", "Nichicon UVR1A102MPD",
            note="bulk at the input")
    b.place("R1", R06, (22.5, 8.5), 0, "330R", note="DIN series")
    leds = chain()
    gnd, vdd = [("J1", "3"), ("J2", "3"), ("J3", "2"), ("C65", "2")], [("J1", "1"), ("J2", "1"), ("J3", "1"), ("C65", "1")]
    for i, (d, c, x, y, rot) in enumerate(leds):
        b.place(d, LED, (x, y), rot, "WS2812B", "Worldsemi WS2812B-B/W", symbol="LED:WS2812B")
        b.place(c, C06, (x + 4.0, y), 90, "100n", note=f"{d} decoupling")
        gnd += [(d, "VSS"), (c, "2")]
        vdd += [(d, "VDD"), (c, "1")]
    b.cite("C1", "decoupling per LED", "100n", 5, "104", url=DS)
    b.net("5V", vdd, amps=3.84, volts=5.0)
    b.net("GND", gnd, amps=3.84)
    b.net("DIN", [("J1", "2"), ("R1", "1")])
    b.net("D0", [("R1", "2"), (leds[0][0], "DIN")])
    for i in range(len(leds) - 1):
        b.net(f"D{i + 1}", [(leds[i][0], "DOUT"), (leds[i + 1][0], "DIN")])
    b.net("DOUT", [(leds[-1][0], "DOUT"), ("J2", "2")])
    b.pour("5V", "F", OUT)
    b.pour("GND", "B", OUT)
    return b
