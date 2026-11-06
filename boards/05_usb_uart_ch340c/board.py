"""USB-UART adapter: USB-C (USB 2.0) -> CH340C (crystal-less) -> 6-pin FTDI-style header (GND CTS VCC TX RX DTR),
3.3 V / 5 V I/O selected by a solder-jumper-free 3-pin header (VCC_SEL), TX/RX activity LEDs, DTR auto-reset cap.
Difficulty: 2/5. 2 layers, 36 x 18 mm, JLCPCB.

    prometheus boards/05_usb_uart_ch340c/board.py out/ --spec boards/05_usb_uart_ch340c/req.json
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
W, H = 36.0, 18.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
FIXED = {"C2": ((29.3, 14.2), 0)}              # by U1.16 (VCC): its decoupling within 2 mm of the pin


def build():
    b = Board("usb_uart_ch340c", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, H / 2), 270, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (W - 2.6, H / 2 + 6.35), 0, "FTDI",
            "XFCN PZ254V-11-06P", lcsc="C492405")
    b.place("U1", None, (26.6, 8.0), 0, "CH340C", "WCH CH340C", symbol="Interface_USB:CH340C", lcsc="C84681")
    ds = "https://datasheet.lcsc.com/szlcsc/Jiangsu-Qin-Heng-CH340C_C84681.pdf"
    for ref, fp, val, note in (("C1", C08, "10u", "VBUS bulk"), ("C2", C06, "100n", "VCC decoupling"),
                               ("C3", C06, "100n", "V3 (only bypass when VCC=5V)"), ("C4", C06, "100n", "DTR reset"),
                               ("R1", R06, "5.1k", "CC1"), ("R2", R06, "5.1k", "CC2"),
                               ("R3", R06, "1k", "TX LED"), ("R4", R06, "1k", "RX LED"), ("R5", R06, "1k", "PWR LED")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.cite("C2", "VCC decoupling", "100n", 4, "0.1uF", url=ds)
    for ref, col in (("D1", "green"), ("D2", "orange"), ("D3", "red")):
        b.place(ref, "LED_SMD:LED_0603_1608Metric", None, 0, col)

    u = lambda n: ("U1", n)
    vbus = [("J1", p) for p in ("A4", "A9", "B4", "B9")]
    b.net("VBUS", vbus + [u("VCC"), ("C1", "1"), ("C2", "1"), ("C3", "1"), u("V3"), ("J2", "3"), ("R5", "1"),
                          ("R3", "1"), ("R4", "1")], amps=0.5, volts=5.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [u("GND"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("D3", "1"), ("J2", "1"), ("R1", "2"), ("R2", "2")], amps=0.5)
    b.net("D+", [("J1", "A6"), ("J1", "B6"), u("UD+")], width=0.25)
    b.net("D-", [("J1", "A7"), ("J1", "B7"), u("UD-")], width=0.25)
    b.pair("D+", "D-", max_skew=0.5)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("TXD", [u("TXD"), ("J2", "4"), ("D1", "1")])
    b.net("RXD", [u("RXD"), ("J2", "5"), ("D2", "1")])
    b.net("TXL", [("R3", "2"), ("D1", "2")])
    b.net("RXL", [("R4", "2"), ("D2", "2")])
    b.net("PWRL", [("R5", "2"), ("D3", "2")])
    b.net("CTS", [u("nCTS"), ("J2", "2")])
    b.net("DTR", [u("nDTR"), ("C4", "1")])
    b.net("DTRC", [("C4", "2"), ("J2", "6")])
    b.nc("U1", "nDSR", "nRI", "nDCD", "nRTS", "R232", "NC")
    b.nc("J1", "SBU1", "SBU2")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
