"""A dual brushed-motor driver: a DRV8833 (HTSSOP-16 with its exposed pad and thermal vias) driving two motors at up
to 1.5 A each from a 2.7-10.8 V supply, a fuse and a power LED, screw terminals for power and motors and a 1x08
header for the logic. 2 layers, 2 oz copper, 42 x 32 mm, ground poured on both sides. Tests current sizing and an
exposed-pad power IC. Pin map is TI's DRV8833 PWP datasheet as remembered: verify before fab.

    pcb-harness bench/motor_driver.py out/ --spec bench/specs/motor_driver.json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pcb import Board  # noqa: E402

R06, C06, C08, C12 = ("Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric",
                      "Capacitor_SMD:C_0805_2012Metric", "Capacitor_SMD:C_1206_3216Metric")
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal"
W, H = 42.0, 32.0
OUT = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]


def build():
    b = Board("motor_driver", OUT, thickness=1.6, copper_oz=2.0, rules="jlcpcb-2")
    b.place("J1", TB, (5.2, 13.0), 270, "power in", "Phoenix 1729128")
    b.place("J2", TB, (W - 5.3, 8.0), 90, "motor A", "Phoenix 1729128")
    b.place("J3", TB, (W - 5.3, 22.0), 90, "motor B", "Phoenix 1729128")
    b.place("J4", "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical", (8.0, H - 3.0), 90, "logic", "")
    for x, y in ((3.5, H - 9.0), (W - 13.0, H - 3.5)):
        b.hole((x, y), 2.7)
    b.place("U1", "Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm_Mask3x3mm_ThermalVias", None, 0, "DRV8833",
            "TI DRV8833PWPR", symbol="Driver_Motor:DRV8833PWP")       # (its pins by name, from KiCad's symbol)
    b.place("F1", "Fuse:Fuse_1206_3216Metric", None, 0, "3A", "Bel Fuse 0685F3000-01")
    for ref, fp, val in (("C1", C06, "10n"), ("C2", C08, "2.2u"), ("C3", C12, "10u 25V"), ("C4", C06, "100n"),
                         ("R1", R06, "10k"), ("R2", R06, "10k"), ("R3", R06, "4.7k")):
        b.place(ref, fp, None, 0, val)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "green", "power")

    b.net("VM_IN", [("J1", "1"), ("F1", "1")], amps=3.0)
    b.net("VM", [("F1", "2"), ("U1", "VM"), ("C3", "1"), ("C4", "1"), ("C1", "2"), ("R3", "1")], amps=3.0)
    b.net("GND", [("J1", "2"), ("U1", "GND"), ("U1", "AISEN"), ("U1", "BISEN"), ("C2", "2"), ("C3", "2"),
                  ("C4", "2"), ("D1", "1"), ("J4", "7"), ("J4", "8")], amps=3.0)
    b.net("VCP", [("U1", "VCP"), ("C1", "1")])
    b.net("VINT", [("U1", "VINT"), ("C2", "1"), ("R1", "2"), ("R2", "2")])
    b.net("LED", [("R3", "2"), ("D1", "2")])
    for out, pins in (("AOUT1", [("U1", "AOUT1"), ("J2", "1")]), ("AOUT2", [("U1", "AOUT2"), ("J2", "2")]),
                      ("BOUT1", [("U1", "BOUT1"), ("J3", "1")]), ("BOUT2", [("U1", "BOUT2"), ("J3", "2")])):
        b.net(out, pins, amps=1.5)
    for sig, pin, hdr in (("AIN1", "AIN1", "1"), ("AIN2", "AIN2", "2"), ("BIN1", "BIN1", "3"), ("BIN2", "BIN2", "4")):
        b.net(sig, [("U1", pin), ("J4", hdr)])
    b.net("nSLEEP", [("U1", "nSLEEP"), ("R1", "1"), ("J4", "5")])
    b.net("nFAULT", [("U1", "nFAULT"), ("R2", "1"), ("J4", "6")])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
