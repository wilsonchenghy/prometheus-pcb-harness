"""A simple benchmark board: a 555 astable LED blinker on a 2-pin power header, 2 layers, 35 x 25 mm.
Times every stage: footprints, placement, routing, the check, plot, Gerbers, KiCad export.

    python bench/blinker.py [out_dir]
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pcb  # noqa: E402
from pcb import Board, route  # noqa: E402

T = {}


def stage(name):
    class _S:
        def __enter__(self):
            self.t = time.perf_counter()

        def __exit__(self, *a):
            T[name] = time.perf_counter() - self.t
    return _S()


R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
OUT = [(0.0, 0.0), (35.0, 0.0), (35.0, 25.0), (0.0, 25.0)]


FPS = ["Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", R06, C06,
       "LED_SMD:LED_0603_1608Metric"]


def build():
    b = Board("blinker", OUT, thickness=1.6, copper_oz=1.0)
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (4.0, 11.0), 0, "5V in", "header")
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (17.0, 12.5), 0, "NE555", "TI NE555DR")
    b.place("R1", R06, (11.0, 19.0), 0, "1k", "")
    b.place("R2", R06, (17.0, 19.5), 0, "47k", "")
    b.place("C1", C06, (23.0, 19.0), 90, "10u", "")
    b.place("C2", C06, (11.0, 6.0), 90, "100n", "")
    b.place("R3", R06, (25.0, 9.0), 0, "330", "")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (30.0, 9.0), 0, "red", "")
    b.net("VCC", [("J1", "1"), ("U1", "8"), ("U1", "4"), ("R1", "1"), ("C2", "1")], amps=0.1)
    b.net("GND", [("J1", "2"), ("U1", "1"), ("C1", "2"), ("C2", "2"), ("D1", "1")], amps=0.1)
    b.net("DIS", [("U1", "7"), ("R1", "2"), ("R2", "1")])
    b.net("THR", [("U1", "6"), ("U1", "2"), ("R2", "2"), ("C1", "1")])
    b.net("OUT", [("U1", "3"), ("R3", "1")])
    b.net("LED", [("R3", "2"), ("D1", "2")])
    b.pour("GND", "B", OUT)
    return b


def main(out):
    t0 = time.perf_counter()
    with stage("build (footprints + place + nets)"):
        b = build()
    probs = []
    with stage("route"):
        for n in ("VCC", "DIS", "THR", "OUT", "LED", "GND"):
            probs += route(b, n, width=0.3)
    with stage("check"):
        chk = b.check()
    os.makedirs(out, exist_ok=True)
    with stage("plot"):
        b.plot(os.path.join(out, "layers.png"))
    with stage("gerbers"):
        pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    with stage("kicad export"):
        pcb.export_kicad(b, os.path.join(out, "kicad"))
    T["total"] = time.perf_counter() - t0
    print("route problems:", probs or "none", "| check:", chk or "clean")
    for k, v in T.items():
        print(f"  {k:36s} {v:7.2f} s")
    return probs, chk


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
