"""A simple benchmark board: a 555 astable LED blinker on a 2-pin power header, 2 layers, 24 x 15 mm.
Times every stage: footprints, placement, routing, the check, plot, Gerbers, KiCad export.

    python bench/blinker.py [out_dir]
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
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
OUT = [(0.0, 0.0), (24.0, 0.0), (24.0, 15.0), (0.0, 15.0)]


FPS = ["Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", R06, C06,
       "LED_SMD:LED_0603_1608Metric"]


def build():
    b = Board("blinker", OUT, thickness=1.6, copper_oz=1.0)
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (3.0, 6.2), 0, "5V in", "header")
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (11.0, 7.5), 0, "NE555", "TI NE555DR",
            symbol="Timer:NE555D")
    b.place("R1", R06, (8.0, 12.6), 0, "1k", "")
    b.place("R2", R06, (12.0, 12.6), 0, "47k", "")
    b.place("C1", C06, (16.2, 12.6), 0, "10u", "")
    b.place("C2", C06, (15.6, 9.6), 90, "100n", "")              # (by U1's VCC pin)
    b.place("R3", R06, (20.5, 4.0), 90, "330", "")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (20.5, 9.5), 90, "red", "")
    b.net("VCC", [("J1", "1"), ("U1", "VCC"), ("U1", "nRST"), ("R1", "1"), ("C2", "1")], amps=0.1)
    b.net("GND", [("J1", "2"), ("U1", "GND"), ("C1", "2"), ("C2", "2"), ("D1", "1")], amps=0.1)
    b.net("DIS", [("U1", "DISCH"), ("R1", "2"), ("R2", "1")])
    b.net("THR", [("U1", "THRES"), ("U1", "TRIG"), ("R2", "2"), ("C1", "1")])
    b.net("OUT", [("U1", "OUT"), ("R3", "1")])
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
