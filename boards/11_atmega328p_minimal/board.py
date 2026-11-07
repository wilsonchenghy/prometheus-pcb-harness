"""Minimal Arduino-compatible ATmega328P board (Pro-Mini style): ATmega328P-AU (TQFP-32) at 16 MHz from a 3225
crystal with 22 pF load caps, 10k reset pull-up + reset button, 6-pin AVR ISP header, 6-pin FTDI header (GND CTS VCC
TXD RXD DTR) with the 100 nF DTR auto-reset cap, power LED, D13 LED, 100 nF on each VCC/AVCC pin + 10 uF bulk,
100 nF on AREF, two 1x12 headers breaking out D0-D13, A0-A7, VCC, GND.
Difficulty: 3/5. 2 layers, 46 x 28 mm, JLCPCB.

Engineering choices / assumptions (no engineer in the loop):
- Powered at 5 V from the FTDI header's VCC pin (16 MHz needs 4.5-5.5 V per the ATmega328P speed grade); no
  on-board regulator. VCC net sized for 0.2 A.
- FTDI header pinout is the SparkFun/Arduino "FTDI Basic" convention: 1 GND, 2 CTS (tied to GND, as on the Pro Mini),
  3 VCC, 4 TXD (adapter out -> MCU RXD/PD0), 5 RXD (adapter in <- MCU TXD/PD1), 6 DTR (-> 100 nF -> RESET).
- ISP header is the AVR 6-pin standard: 1 MISO, 2 VCC, 3 SCK, 4 MOSI, 5 RESET, 6 GND.
- AVCC tied straight to VCC (no LC filter on a minimal board), 100 nF at the pin.
- Crystal: Yangxing X322516MLB4SI (16 MHz, CL 20 pF, 3225); load caps 22 pF (2*(CL - ~5 pF stray) ~ 30 pF,
  22 pF is the usual Arduino value and lands within the oscillator's pull range). Value assumed, not cited.
- Decoupling values (100 nF per supply pin, 10 uF bulk, 100 nF AREF) are the usual AVR practice, not cited
  to a datasheet page.
- LEDs: 1k series (about 3 mA at 5 V for a green/red 0603) - assumed.
- Pin numbers come from KiCad's MCU_Microchip_ATmega:ATmega328P-A symbol, wired by name.

    prometheus boards/11_atmega328p_minimal/board.py out/ --spec boards/11_atmega328p_minimal/req.json
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
HDR12 = "Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical"
W, H = 46.0, 28.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = 22.0, 14.0                                  # the MCU's centre
# fixed spots: the decoupling by its supply pins (pins 3-6 on the left side, 18/20/21 on the right)
FIXED = {"C1": ((UX - 6.7, UY + 0.8), 90),          # VCC pin 4 (y +0.4) / GND pin 3 (y +1.2)
         "C2": ((UX - 6.7, UY - 2.4), 270),         # VCC pin 6 (y -1.2) / GND pin 5 (y -0.4)
         "C3": ((UX + 6.7, UY - 2.6), 90),          # AVCC pin 18 (y -2.0)
         "C4": ((UX + 6.7, UY + 1.2), 90)}          # AREF pin 20 (y -0.4) / GND pin 21 (y +0.4)


def build():
    b = Board("atmega328p_minimal", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("U1", None, (UX, UY), 0, "ATmega328P-AU", "Microchip ATmega328P-AU",
            symbol="MCU_Microchip_ATmega:ATmega328P-A", lcsc="C14877")
    # connectors and the button: placed by hand (the enclosure decides them)
    b.place("J3", HDR12, (6.0, 2.2), 90, "D0-D11", "XFCN PZ254V-11-12P")
    b.place("J4", HDR12, (6.0, H - 2.2), 90, "D12-A7", "XFCN PZ254V-11-12P")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (W - 2.2, H / 2 + 6.35), 0, "FTDI",
            "XFCN PZ254V-11-06P", lcsc="C492405")
    b.place("J5", "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical", (2.2, H / 2 + 2.54), 0, "ISP",
            "XFCN PZ254V-12-6P")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_PTS810", (37.5, 7.5), 0, "RESET", "C&K PTS810SJM250SMTRLFS")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", (UX - 7.5, UY - 6.5), 0, "16MHz",
            "Yangxing X322516MLB4SI", lcsc="C13738")
    for ref, fp, val, note in (("C1", C06, "100n", "VCC pin 4"), ("C2", C06, "100n", "VCC pin 6"),
                               ("C3", C06, "100n", "AVCC"), ("C4", C06, "100n", "AREF"),
                               ("C5", C08, "10u", "VCC bulk"), ("C6", C06, "22p", "XTAL1 load"),
                               ("C7", C06, "22p", "XTAL2 load"), ("C8", C06, "100n", "DTR auto-reset"),
                               ("R1", R06, "10k", "RESET pull-up"), ("R2", R06, "1k", "PWR LED"),
                               ("R3", R06, "1k", "D13 LED")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "red", note="power")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", None, 0, "green", note="D13 (PB5)", lcsc="C72043")

    u = lambda n: ("U1", n)
    b.net("VCC", [u("VCC"), u("AVCC"), ("C1", "1"), ("C2", "1"), ("C3", "1"), ("C5", "1"), ("R1", "1"), ("R2", "1"),
                  ("J2", "3"), ("J5", "2"), ("J4", "11")], amps=0.2, volts=5.0)
    b.net("GND", [u("GND"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"),
                  ("C7", "2"), ("Y1", "2"), ("Y1", "4"), ("D1", "1"), ("D2", "1"), ("SW1", "2"), ("J2", "1"),
                  ("J2", "2"), ("J5", "6"), ("J4", "12")], amps=0.2)
    b.net("XTAL1", [u("XTAL1/PB6"), ("Y1", "1"), ("C6", "1")])
    b.net("XTAL2", [u("XTAL2/PB7"), ("Y1", "3"), ("C7", "1")])
    b.net("RESET", [u("nRESET/PC6"), ("R1", "2"), ("SW1", "1"), ("C8", "1"), ("J5", "5")])
    b.net("DTR", [("C8", "2"), ("J2", "6")])
    b.net("AREF", [u("AREF"), ("C4", "1")])
    b.net("PWRL", [("R2", "2"), ("D1", "2")])
    b.net("D13L", [("R3", "2"), ("D2", "2")])
    # D0-D11 on J3 pins 1-12; D0/D1 also to the FTDI header
    dpins = ["PD0", "PD1", "PD2", "PD3", "PD4", "PD5", "PD6", "PD7", "PB0", "PB1", "PB2", "PB3"]
    for i, p in enumerate(dpins):
        extra = []
        if i == 0:
            extra = [("J2", "4")]                    # adapter TXD -> MCU RXD
        if i == 1:
            extra = [("J2", "5")]                    # MCU TXD -> adapter RXD
        if p == "PB3":
            extra = [("J5", "4")]                    # MOSI
        b.net(f"D{i}", [u(p), ("J3", str(i + 1))] + extra)
    b.net("D12", [u("PB4"), ("J4", "1"), ("J5", "1")])               # MISO
    b.net("D13", [u("PB5"), ("J4", "2"), ("J5", "3"), ("R3", "1")])  # SCK + LED
    for i, p in enumerate(["PC0", "PC1", "PC2", "PC3", "PC4", "PC5", "ADC6", "ADC7"]):
        b.net(f"A{i}", [u(p), ("J4", str(i + 3))])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
