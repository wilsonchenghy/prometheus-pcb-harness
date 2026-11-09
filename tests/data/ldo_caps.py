from pcb import Board
def build():
    b = Board("prox_ldo", [(0, 0), (40, 0), (40, 25), (0, 25)], rules="jlcpcb-2")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (2.5, 12), 0, "VIN")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (37.5, 8), 0, "OUT")
    b.place("U1", None, None, 0, "AP2112K-3.3", symbol="Regulator_Linear:AP2112K-3.3")
    b.place("U2", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", None, 0, "load chip")
    for r, v in (("C1", "1u"), ("C2", "1u"), ("C3", "100n")):
        b.place(r, "Capacitor_SMD:C_0603_1608Metric", None, 0, v)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", None, 0, "10k")
    b.net("VIN", [("J1", "1"), ("U1", "VIN"), ("U1", "EN"), ("C1", "1")])
    b.net("3V3", [("U1", "VOUT"), ("C2", "1"), ("U2", "8"), ("C3", "1"), ("J2", "1"), ("R1", "1")])
    b.net("GND", [("J1", "2"), ("U1", "GND"), ("C1", "2"), ("C2", "2"), ("U2", "4"), ("C3", "2"), ("J2", "4")])
    for k, (a, p) in enumerate([("U2", "2"), ("U2", "3")]):
        b.net(f"S{k}", [(a, p), ("J2", str(k + 2))])
    b.net("PU", [("R1", "2"), ("U2", "1")])
    b.pour("GND", "B", [(0, 0), (40, 0), (40, 25), (0, 25)])
    return b
