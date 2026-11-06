"""(A copy of boards/03_usbc_power_breakout/board.py, the board that showed the via-to-slot bug: a test fixture.)

USB-C 5 V power breakout: a USB-C receptacle as a 5 V / up to 2 A sink (5.1k on CC1 and CC2 -> the source offers
its default current), a 2 A resettable fuse (1812), an SMBJ5.0A TVS across VBUS, a 22 uF bulk cap, a power LED, and
the 5 V out on a 2-pin 5.08 mm screw terminal and a 1x4 2.54 mm header (5V, 5V, GND, GND). Difficulty 1/5.
2 layers, 36 x 22 mm, JLCPCB.

The 2 A is what the copper is sized for (IPC-2221, 20 C rise): 3 A copper does not fit beside the receptacle's
0.5 mm-pitch pins on a 2-layer board. A USB-C source without PD gives 0.5-3 A as its CC advertisement says (Type-C
spec, Rd = 5.1 k). Fuse hold current 2 A (Bourns MF-MSMF200; assumed from its series table). VBUS is drawn by hand
from the receptacle's pins into the fuse.
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_1206_3216Metric"
W, H = 36.0, 22.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("usbc_power_breakout", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, H / 2), 270, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
            (W - 4.0, 6.0), 90, "5V OUT", "Phoenix 1729128", overhang=True)
    b.place("J3", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (W - 6.0, H - 2.6), 270, "5V",
            "XFCN PZ254V-11-04P")
    b.place("F1", "Fuse:Fuse_1812_4532Metric", (14.0, 10.0), 0, "2A hold", "Bourns MF-MSMF200-2")
    b.place("D1", "Diode_SMD:D_SMB", (20.0, 6.5), 90, "SMBJ5.0A", "Littelfuse SMBJ5.0A")
    b.place("C1", C08, (20.0, 13.5), 90, "22u 10V", "Samsung CL31A226KPHNNNE")
    for ref, fp, val, note in (("R1", R06, "5.1k", "CC1 Rd"), ("R2", R06, "5.1k", "CC2 Rd"), ("R3", R06, "1k", "LED")):
        b.place(ref, fp, None, 0, val, note=note)
    b.place("D2", "LED_SMD:LED_0603_1608Metric", None, 0, "green")
    b.hole((13.0, 3.6), d=3.2)                           # (clear of the terminal block's body)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("F1", "1")], amps=2.0, volts=5.0)
    b.net("5V", [("F1", "2"), ("D1", "1"), ("C1", "1"), ("J2", "1"), ("J3", "1"), ("J3", "2"), ("R3", "1")],
          amps=2.0, volts=5.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [("D1", "2"), ("C1", "2"), ("J2", "2"), ("J3", "3"), ("J3", "4"), ("R1", "2"), ("R2", "2"), ("D2", "1")],
          amps=2.0)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("LED", [("R3", "2"), ("D2", "2")])
    b.nc("J1", "SBU1", "SBU2", "D+", "D-")
    b.pour("GND", "B", OUT)
    b.pour("5V", "F", [(15.6, 1.0), (W - 1.0, 1.0), (W - 1.0, 14.0), (15.6, 17.0)])
    # VBUS by hand: a 0.52 mm track off each of the receptacle's two VBUS pad pairs (0.3 mm pins: the pin limits the
    # current there), then 0.9 mm into a 1.5 mm bar to the fuse
    (xa, ya), (xb, yb) = b.pad("J1", "A4"), b.pad("J1", "A9")
    xf, yf = b.pad("F1", "1")
    bar = xf - 1.8
    for (x, y) in ((xa, ya), (xb, yb)):
        b.track("VBUS", "F", [(x, y), (x + 1.4, y)], 0.52)
        b.track("VBUS", "F", [(x + 1.4, y), (bar, y)], 0.9)
    b.track("VBUS", "F", [(bar, ya), (bar, yb)], 1.5)
    b.track("VBUS", "F", [(bar, yf), (xf, yf)], 1.5)
    # the GND pins straight into the shell's nearer front tab (plated, GND, through to the bottom pour)
    tabs = [g.centroid for r, n, L, g, d in b.pad_shapes() if r == "J1" and n == "SH" and L == "F"]
    for pin in ("A1", "A12"):
        x, y = b.pad("J1", pin)
        t = min(tabs, key=lambda q: (q.x - x) ** 2 + (q.y - y) ** 2)
        b.track("GND", "F", [(x, y), (t.x, t.y)], 0.52)
    return b
