"""ATtiny85 USB dev board (V-USB, the Micronucleus bootloader's wiring): USB-C (USB 2.0 full speed lines used at
low speed), D+ on PB4 and D- on PB3 through 68 ohm with 3.6 V zeners to GND, 1.5k pull-up on D- (low-speed device),
a LED on PB1, VIN 7-12 V through an LM78M05 to 5 V, USB VBUS through an SS14 Schottky (so the two supplies can be
joined), headers P0-P5 and VIN / GND / 5V. Difficulty 3/5. 2 layers, 32 x 26 mm, JLCPCB.

Wiring is the Digispark's (open hardware) and Micronucleus's default (D+ = PB4, D- = PB3). Zeners BZT52C3V6 hold
the data lines at USB levels while the chip runs at 5 V. ATtiny85 pins from KiCad's ATtiny85-20S symbol (SOIC-8
5.3 mm). LM78M05 TO-252: 1 IN, 2 GND (tab), 3 OUT; 0.33 uF in / 0.1 uF out per the 78M05 datasheet's typical
application (as remembered).
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
W, H = 32.0, 26.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("attiny85_vusb", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, H / 2), 270, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (W - 2.6, H - 2.6), 0,
            "P0-P5", "XFCN PZ254V-11-06P")
    b.place("J3", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (8.0, 2.6), 270, "VIN GND 5V",
            "XFCN PZ254V-11-03P")
    b.place("U1", None, (17.0, 14.5), 0, "ATtiny85", "Microchip ATTINY85-20SU", symbol="MCU_Microchip_ATtiny:ATtiny85-20S",
            lcsc="C89852")
    b.place("U2", "Package_TO_SOT_SMD:TO-252-2", (24.5, 4.0), 0, "LM78M05", "TI LM78M05CDT", lcsc="C58069")
    for ref, fp, val, note in (("R1", R06, "68", "D+"), ("R2", R06, "68", "D-"), ("R3", R06, "1.5k", "D- pull-up"),
                               ("R4", R06, "5.1k", "CC1"), ("R5", R06, "5.1k", "CC2"), ("R6", R06, "1k", "LED"),
                               ("C1", C06, "100n", "VCC"), ("C2", C08, "10u", "5V bulk"), ("C3", C06, "330n", "78M05 in"),
                               ("C4", C06, "100n", "78M05 out")):
        b.place(ref, fp, ((23.2, 16.4) if ref == "C1" else None), 90 if ref == "C1" else 0, val, note=note)
    b.place("D1", "Diode_SMD:D_MiniMELF", None, 0, "3V6", "Nexperia BZT52C3V6 (MiniMELF: ZMM3V6)")
    b.place("D2", "Diode_SMD:D_MiniMELF", None, 0, "3V6", "Nexperia BZT52C3V6 (MiniMELF: ZMM3V6)")
    b.place("D3", "Diode_SMD:D_SMA", None, 0, "SS14", "MDD SS14")
    b.place("D4", "LED_SMD:LED_0603_1608Metric", None, 0, "red")

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("D3", "2")], amps=0.5, volts=5.0)
    b.net("VIN", [("J3", "1"), ("U2", "1"), ("C3", "1")], amps=0.5, volts=12.0)
    b.net("5V", [("D3", "1"), ("U2", "3"), ("C4", "1"), ("C2", "1"), ("C1", "1"), u("VCC"), ("R3", "1"), ("J3", "3")],
          amps=0.5, volts=5.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [("J3", "2"), ("U2", "2"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), u("GND"), ("D1", "2"),
           ("D2", "2"), ("D4", "1"), ("R4", "2"), ("R5", "2")], amps=0.5)
    b.net("CC1", [("J1", "A5"), ("R4", "1")])
    b.net("CC2", [("J1", "B5"), ("R5", "1")])
    b.net("D+", [("J1", "A6"), ("J1", "B6"), ("R1", "1")])
    b.net("D-", [("J1", "A7"), ("J1", "B7"), ("R2", "1")])
    b.net("P4", [("R1", "2"), ("D1", "1"), u("XTAL2/PB4"), ("J2", "5")])
    b.net("P3", [("R2", "2"), ("D2", "1"), ("R3", "2"), u("XTAL1/PB3"), ("J2", "4")])
    b.net("P0", [u("AREF/PB0"), ("J2", "1")])
    b.net("P1", [u("PB1"), ("J2", "2"), ("R6", "1")])
    b.net("P2", [u("PB2"), ("J2", "3")])
    b.net("P5", [u("nRESET/PB5"), ("J2", "6")])
    b.net("LED", [("R6", "2"), ("D4", "2")])
    b.pair("D+", "D-", max_skew=1.0)
    b.nc("J1", "SBU1", "SBU2")
    b.pour("GND", "B", OUT)
    return b
