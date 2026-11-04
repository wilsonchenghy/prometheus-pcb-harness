"""An RP2040 board from a parts list: the QFN-56 (0.4 mm pitch) with its QSPI flash, 12 MHz crystal, USB-C with
its series resistors and CC pull-downs, a 3.3 V LDO, the decoupling, BOOTSEL and RUN buttons, a status LED and
two 1x15 GPIO headers along the long edges. 2 layers, 56 x 30 mm, ground poured on both sides. Only the USB
connector and the headers are placed by hand; place_and_route does the rest under JLCPCB's 2-layer rules: a test
of the placer and router on a modern microcontroller board. Pin maps are the makers' datasheets as remembered
(RP2040 datasheet ch. 1.4, W25Q128JV, AP2112K): verify before fab.

    python bench/rp2040_board.py [out_dir]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pcb  # noqa: E402
from pcb import Board  # noqa: E402

R04, C04 = "Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric"
C06 = "Capacitor_SMD:C_0603_1608Metric"
W, H = 56.0, 30.0
OUT = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x15_P2.54mm_Vertical"
# RP2040 QFN-56 pins
IOVDD = ["1", "10", "22", "33", "42", "49"]
DVDD = ["23", "50"]
GPIO = {i: str(p) for i, p in zip(range(16), [2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18])}
GPIO.update({16: "27", 17: "28", 18: "29", 19: "30", 20: "31", 21: "32", 22: "34", 23: "35", 24: "36", 25: "37",
             26: "38", 27: "39", 28: "40", 29: "41"})


def build():
    b = Board("rp2040_board", OUT, thickness=1.6, copper_oz=1.0, clearance=0.15, rules="jlcpcb-2")
    # the hand-placed parts: the USB-C at the left edge, the headers along the long edges
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.5, H / 2), 270,
            "USB-C", "GCT USB4105-GF-A", overhang=True)
    b.place("J2", HDR, (10.0, 2.0), 90, "GPIO 0-14", "")       # along the bottom edge, pin 1 at the left
    b.place("J3", HDR, (10.0, H - 2.0), 90, "GPIO 15-28", "")  # along the top edge
    # the rest: no position, place_and_route places them
    b.place("U1", "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm", None, 0, "RP2040", "Raspberry Pi RP2040",
            lcsc="C2040")
    b.place("U2", "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm", None, 0, "W25Q128JV", "Winbond W25Q128JVSIQ")
    b.place("U3", "Package_TO_SOT_SMD:SOT-23-5", None, 0, "AP2112K-3.3", "Diodes Inc AP2112K-3.3TRG1")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", None, 0, "12 MHz", "Abracon ABM8-272-T3")
    for k in range(1, 11):                                   # decoupling: 6 x IOVDD, 2 x DVDD, USB_VDD, ADC_AVDD
        b.place(f"C{k}", C04, None, 0, "100n")
    b.place("C11", C06, None, 0, "1u")                       # VREG_VIN
    b.place("C12", C06, None, 0, "1u")                       # VREG_VOUT (DVDD)
    b.place("C13", C04, None, 0, "15p")                      # crystal loads
    b.place("C14", C04, None, 0, "15p")
    b.place("C15", C06, None, 0, "10u")                      # LDO in
    b.place("C16", C06, None, 0, "10u")                      # LDO out
    b.place("R1", R04, None, 0, "1k")                        # crystal series
    b.place("R2", R04, None, 0, "27R")                       # USB D-
    b.place("R3", R04, None, 0, "27R")                       # USB D+
    b.place("R4", R04, None, 0, "5.1k")                      # CC1
    b.place("R5", R04, None, 0, "5.1k")                      # CC2
    b.place("R6", R04, None, 0, "10k")                       # flash CS pull-up
    b.place("R7", R04, None, 0, "1k")                        # BOOTSEL
    b.place("R8", R04, None, 0, "330")                       # LED
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "green")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_TL3342", None, 0, "BOOTSEL", "E-Switch TL3342F160QG")
    b.place("SW2", "Button_Switch_SMD:SW_SPST_TL3342", None, 0, "RUN", "E-Switch TL3342F160QG")

    gnd = [("J1", p) for p in ("A1", "A12", "B1", "B12")] + [("U1", "57"), ("U2", "4"), ("U3", "2"), ("Y1", "2"),
           ("Y1", "4"), ("C13", "2"), ("C14", "2"), ("C15", "2"), ("C16", "2"), ("C11", "2"), ("C12", "2"),
           ("R4", "2"), ("R5", "2"), ("D1", "1"), ("SW1", "2"), ("SW2", "2"), ("J2", "15"), ("J3", "15")] + \
        [(f"C{k}", "2") for k in range(1, 11)]
    b.net("GND", gnd, amps=0.5)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U3", "1"), ("U3", "3"), ("C15", "1"),
                                                                    ("J3", "14")], amps=0.5)
    b.net("3V3", [("U3", "5"), ("C16", "1"), ("U2", "8"), ("R6", "1"), ("U1", "43"), ("U1", "44"), ("U1", "48"),
                  ("C11", "1"), ("J3", "13")] + [("U1", p) for p in IOVDD] + [(f"C{k}", "1") for k in range(1, 7)] +
          [("C9", "1"), ("C10", "1")], amps=0.3)
    b.net("DVDD", [("U1", "45"), ("C12", "1"), ("C7", "1"), ("C8", "1")] + [("U1", p) for p in DVDD], amps=0.1)
    b.net("XIN", [("U1", "20"), ("Y1", "1"), ("C13", "1")])
    b.net("XOUT", [("U1", "21"), ("R1", "1")])
    b.net("XR", [("R1", "2"), ("Y1", "3"), ("C14", "1")])
    b.net("USB_DM", [("U1", "46"), ("R2", "1")])
    b.net("USB_DP", [("U1", "47"), ("R3", "1")])
    b.net("DM", [("R2", "2"), ("J1", "A7"), ("J1", "B7")])
    b.net("DP", [("R3", "2"), ("J1", "A6"), ("J1", "B6")])
    b.net("CC1", [("J1", "A5"), ("R4", "1")])
    b.net("CC2", [("J1", "B5"), ("R5", "1")])
    b.net("QSPI_SS", [("U1", "56"), ("U2", "1"), ("R6", "2"), ("R7", "1")])
    b.net("BOOT", [("R7", "2"), ("SW1", "1")])
    b.net("QSPI_SD1", [("U1", "55"), ("U2", "2")])
    b.net("QSPI_SD2", [("U1", "54"), ("U2", "3")])
    b.net("QSPI_SD0", [("U1", "53"), ("U2", "5")])
    b.net("QSPI_SCLK", [("U1", "52"), ("U2", "6")])
    b.net("QSPI_SD3", [("U1", "51"), ("U2", "7")])
    b.net("RUN", [("U1", "26"), ("SW2", "1"), ("J3", "12")])
    b.net("LED", [("U1", GPIO[25]), ("R8", "1")])
    b.net("LEDK", [("R8", "2"), ("D1", "2")])
    for i in range(15):                                      # GPIO 0-14 on J2 pins 1-14 (and 15: GND)
        if i < 14:
            b.net(f"GP{i}", [("U1", GPIO[i]), ("J2", str(i + 1))])
    for n, i in enumerate((15, 16, 17, 18, 19, 20, 21, 22, 26, 27, 28)):   # GPIO 15-28 on J3 pins 1-11
        b.net(f"GP{i}", [("U1", GPIO[i]), ("J3", str(n + 1))])
    for n, d in b.nets.items():                          # signals at 0.15 mm (out of a 0.4 mm pitch QFN)
        if d["amps"] <= 0.1 and n not in ("GND", "DVDD"):
            d["width"] = 0.15
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b


if __name__ == "__main__":
    t0 = time.perf_counter()
    b = build()
    probs = pcb.place_and_route(b)
    print(f"placed and routed in {time.perf_counter() - t0:.1f} s: {probs or 'clean'}")
    import fab
    print("house check:", fab.fab_check(b) or "clean")
    if len(sys.argv) > 1:
        fab.order(b, sys.argv[1])
        b.plot(os.path.join(sys.argv[1], "layers.png"))
