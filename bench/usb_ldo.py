"""A USB-C to 3.3 V board: a USB-C receptacle (5.1 k CC pull-downs), an AMS1117-3.3 LDO with its caps, a power LED, a
4-pin header out. 2 layers, 38 x 24 mm, ground poured on both sides. Exercises a fine-pitch connector's fan-out."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pcb import Board, route  # noqa: E402

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
OUT = [(0.0, 0.0), (38.0, 0.0), (38.0, 24.0), (0.0, 24.0)]


def build():
    b = Board("usb_ldo", OUT, thickness=1.6, copper_oz=1.0)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (4.0, 12.0), 270,
            "USB-C", "GCT USB4105-GF-A", lcsc="C3020560", overhang=True)
    b.place("R1", R06, (12.0, 17.5), 0, "5.1k", lcsc="C23186")
    b.place("R2", R06, (12.0, 6.5), 0, "5.1k", lcsc="C23186")
    b.place("U1", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", (22.0, 12.0), 0, "AMS1117-3.3", "AMS AMS1117-3.3",
            lcsc="C6186")
    b.place("C1", C08, (15.5, 12.0), 90, "10u", lcsc="C15850")
    b.place("C2", C08, (28.5, 17.0), 90, "22u", lcsc="C45783")
    b.place("C3", C06, (28.5, 7.0), 90, "100n", lcsc="C14663")
    b.place("R3", R06, (20.0, 3.0), 0, "1k", lcsc="C21190")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (24.0, 3.0), 0, "green", lcsc="C72043")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (34.0, 16.0), 0, "out", "")
    b.net("VBUS", [("J1", "A4"), ("J1", "A9"), ("J1", "B4"), ("J1", "B9"), ("C1", "1"), ("U1", "3"), ("J2", "1")],
          amps=1.0)
    b.net("3V3", [("U1", "2"), ("C2", "1"), ("C3", "1"), ("R3", "1"), ("J2", "2")], amps=0.8)
    b.net("GND", [("J1", "A1"), ("J1", "A12"), ("J1", "B1"), ("J1", "B12"), ("C1", "2"), ("U1", "1"), ("C2", "2"),
                  ("C3", "2"), ("D1", "1"), ("J2", "4")], amps=1.0)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("CCG", [("R1", "2"), ("R2", "2")])
    b.net("LED", [("R3", "2"), ("D1", "2")])
    b.net("DP", [("J1", "A6"), ("J1", "B6"), ("J2", "3")])
    b.pour("GND", "B", OUT)
    return b


def do_route(b):
    probs = []
    b.fanout(width=0.2)
    for n, w in (("CC1", 0.2), ("CC2", 0.2), ("DP", 0.2), ("VBUS", 0.3), ("3V3", 0.4), ("LED", 0.25), ("CCG", 0.25)):
        probs += route(b, n, width=w)
    probs += route(b, "GND", width=0.4)
    return probs


if __name__ == "__main__":
    import time
    t = time.perf_counter()
    b = build()
    p = do_route(b)
    print("route:", p or "clean", "| check:", b.check() or "clean", f"| {time.perf_counter() - t:.1f} s")
