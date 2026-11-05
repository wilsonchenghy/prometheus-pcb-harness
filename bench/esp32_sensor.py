"""An ESP32 sensor board: an ESP32-WROOM-32E module, USB-C power through an AMS1117-3.3, EN / BOOT buttons with
the auto-reset RC, a BME280 on I2C with its pull-ups, a status LED, a UART header. 2 layers, 70 x 60 mm, ground
poured on the bottom, the antenna's keep-out clear of copper. A typical small product board: ~25 parts, a module, a
fine-pitch connector and an LGA sensor. Pin maps are the makers' datasheets as remembered: verify before fab."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from pcb import Board  # noqa: E402

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
OUT = [(0.0, 0.0), (70.0, 0.0), (70.0, 60.0), (0.0, 60.0)]
MOD = (35.0, 30.0)                                   # the module's origin; its antenna reaches up to the top edge


def build():
    b = Board("esp32_sensor", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2")
    b.place("U1", "RF_Module:ESP32-WROOM-32E", MOD, 0, "ESP32-WROOM-32E", "Espressif ESP32-WROOM-32E-N4")
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (4.0, 9.0), 270,
            "USB-C", "GCT USB4105-GF-A", overhang=True)
    b.place("R1", R06, (12.0, 13.5), 0, "5.1k")
    b.place("R2", R06, (12.0, 4.5), 0, "5.1k")
    b.place("U2", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", (21.0, 9.0), 0, "AMS1117-3.3", "AMS AMS1117-3.3")
    b.place("C1", C08, (15.5, 9.0), 90, "10u")
    b.place("C2", C08, (27.0, 12.5), 90, "22u")
    b.place("C3", C06, (27.0, 5.5), 90, "100n")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_TL3342", (40.0, 7.0), 0, "EN", "E-Switch TL3342F160QG")
    b.place("SW2", "Button_Switch_SMD:SW_SPST_TL3342", (52.0, 7.0), 0, "BOOT", "E-Switch TL3342F160QG")
    b.place("R5", R06, (33.0, 12.5), 0, "10k")
    b.place("C5", C06, (33.0, 4.0), 0, "1u")
    b.place("U3", "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering", (64.0, 30.0), 0, "BME280",
            "Bosch BME280")
    b.place("C4", C06, (64.0, 25.0), 0, "100n")
    b.place("R3", R06, (64.0, 36.0), 0, "4.7k")
    b.place("R4", R06, (64.0, 39.0), 0, "4.7k")
    b.place("R6", R06, (61.0, 4.0), 0, "1k")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (65.5, 4.0), 0, "blue")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (5.0, 26.0), 0, "UART")
    b.place("C6", C08, (7.5, 36.0), 0, "10u")
    b.place("C7", C06, (7.5, 33.0), 0, "100n")
    b.net("VBUS", [("J1", "A4"), ("J1", "A9"), ("C1", "1"), ("U2", "3")], amps=0.6)
    b.net("3V3", [("U2", "2"), ("C2", "1"), ("C3", "1"), ("U1", "2"), ("C6", "1"), ("C7", "1"), ("R5", "1"),
                  ("R3", "1"), ("R4", "1"), ("U3", "6"), ("U3", "8"), ("U3", "2"), ("C4", "1"), ("J2", "1")], amps=0.5)
    b.net("GND", [("J1", "A1"), ("J1", "A12"), ("C1", "2"), ("U2", "1"), ("C2", "2"), ("C3", "2"), ("U1", "1"),
                  ("U1", "15"), ("U1", "38"), ("U1", "39"), ("C6", "2"), ("C7", "2"), ("C5", "2"), ("SW1", "2"),
                  ("SW2", "2"), ("U3", "1"), ("U3", "7"), ("U3", "5"), ("C4", "2"), ("D1", "1"), ("J2", "4")], amps=0.6)
    b.net("EN", [("U1", "3"), ("R5", "2"), ("C5", "1"), ("SW1", "1")])
    b.net("IO0", [("U1", "25"), ("SW2", "1")])
    b.net("SDA", [("U1", "33"), ("U3", "3"), ("R3", "2")])
    b.net("SCL", [("U1", "36"), ("U3", "4"), ("R4", "2")])
    b.net("TX", [("U1", "35"), ("J2", "2")])
    b.net("RX", [("U1", "34"), ("J2", "3")])
    b.net("LED", [("U1", "24"), ("R6", "1")])
    b.net("LEDK", [("R6", "2"), ("D1", "2")])
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("CCG", [("R1", "2"), ("R2", "2")])
    ant = [(MOD[0] - 10.0, MOD[1] + 12.0), (MOD[0] + 10.0, MOD[1] + 12.0), (MOD[0] + 10.0, 60.0), (MOD[0] - 10.0, 60.0)]
    b.keepout(ant, "FB", "antenna")
    b.pour("GND", "B", OUT)
    return b


JOBS = [("CC1", 0.2, None), ("CC2", 0.2, None), ("CCG", 0.25, None), ("SDA", 0.2, None), ("SCL", 0.2, None),
        ("TX", 0.2, None), ("RX", 0.2, None), ("EN", 0.25, None), ("IO0", 0.25, None), ("LED", 0.25, None),
        ("LEDK", 0.25, None), ("VBUS", 0.4, None), ("3V3", 0.4, None), ("GND", 0.4, None)]


def do_route(b):
    """negotiated passes (pcb.route_jobs): a net that fails is routed first on the next pass"""
    from pcb import route_jobs
    b.fanout(width=0.2)
    return route_jobs(b, JOBS, passes=4)


if __name__ == "__main__":
    import time
    t = time.perf_counter()
    b = build()
    p = do_route(b)
    print("route:", p or "clean", "| check:", b.check() or "clean", f"| {time.perf_counter() - t:.1f} s")
