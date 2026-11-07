"""4-port USB 2.0 hub: a Terminus FE1.1s (SSOP-28, single-TT, high speed) between a USB-C upstream port and four
USB-A downstream ports (Molex 67643), bus powered (BUSJ low), the downstream VBUS through one 2 A resettable fuse
with 47 uF per port, a 12 MHz crystal (the FE1.1s has the load capacitance inside), REXT 2.7k 1 %, its 5 V -> 3.3 V
and 3.3 V -> 1.8 V regulators' 10 uF caps, its VDD5 fed from VBUS through a 600 ohm ferrite (FB1), a reset RC, a power LED. Difficulty 4/5. 2 layers, 78 x 46 mm, JLCPCB.

From the FE1.1s datasheet (Rev 1.0, pin description p.5): REXT 2.7k 1 % to VSS, 10 uF on VD33_O and on VD18_O,
VD33_O feeds VD33 and VD18_O feeds VD18, BUSJ 0 = bus powered, OVCJ active low (pulled up: no over-current
sensing), PWRJ (ganged power enable) unused, TESTJ has its own pull-up. VBUSM (upstream VBUS monitor) through a
10k / 20k divider (assumed: keeps it at 3.3 V logic; the datasheet does not give its level). The USB pairs are
length-matched within 1.25 mm (50 mil, the usual USB 2.0 high-speed layout guidance; req.json pairs), not impedance controlled: a 1.6 mm 2-layer board cannot give 90 ohm with
practical widths, so keep the board's runs short; for a strict design, use 4 layers.
A bus-powered hub can only share the upstream port's 0.5-0.9 A (3 A from a Type-C source advertising it).
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
C12 = "Capacitor_SMD:C_1206_3216Metric"
USBA = "Connector_USB:USB_A_Molex_67643_Horizontal"
DS = "https://cdn-shop.adafruit.com/product-files/2991/FE1.1s+Data+Sheet+(Rev.+1.0).pdf"
W, H = 78.0, 46.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
PX = [7.0, 25.5, 44.0, 62.5]
FIXED = {"C1": ((37.2, 34.7), 0),        # VD33_O (pin 21), over the unused pins 22-25
         "C3": ((39.9, 35.7), 90),       # VDD5 (pin 20)
         "C7": ((33.2, 34.6), 0),        # VD18 (pin 28), clear of the VBUS feed
         "C2": ((44.6, 24.4), 90),       # VD18_O (pin 12)
         "C6": ((42.6, 24.3), 90),       # VD33 (pin 13), right under it
         "R3": ((46.0, 27.6), 0)}        # REXT (pin 14)                     # the USB-A ports' pin-1 x, along the bottom edge


def build():
    b = Board("usb_hub_fe11s", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (W / 2, H - 3.2), 180,
            "USB-C UP", "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    for i, x in enumerate(PX):
        b.place(f"J{i + 2}", USBA, (x, 12.0), 0, f"PORT {i + 1}", "Molex 0676432910", overhang=True,
                symbol="Connector:USB_A")
    b.place("U1", None, (W / 2, 30.0), 90, "FE1.1s", "Terminus FE1.1s", symbol="Interface_USB:FE1.1s")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", None, 0, "12MHz", "Yangxing X322512MSB4SI")
    b.place("F1", "Fuse:Fuse_1812_4532Metric", None, 0, "2A hold", "Bourns MF-MSMF200-2")
    for ref, fp, val, note in (("R1", R06, "5.1k", "CC1"), ("R2", R06, "5.1k", "CC2"), ("R3", R06, "2.7k 1%", "REXT"),
                               ("R4", R06, "10k", "reset pull-up"), ("R5", R06, "10k", "VBUSM top"),
                               ("R6", R06, "20k", "VBUSM bottom"), ("R7", R06, "10k", "OVCJ pull-up"),
                               ("R8", R06, "1k", "PWR LED"),
                               ("C1", C08, "10u", "VD33_O"), ("C2", C08, "10u", "VD18_O"), ("C3", C06, "100n", "VDD5"),
                               ("C4", C08, "10u", "VBUS in"), ("C5", C06, "1u", "reset delay"),
                               ("C6", C06, "100n", "VD33"), ("C7", C06, "100n", "VD18")) + \
            tuple((f"C{10 + i}", C12, "47u", f"port {i + 1} VBUS") for i in range(4)):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "green")
    b.place("FB1", "Inductor_SMD:L_0603_1608Metric", None, 0, "600R@100MHz", "Murata BLM18KG601SN1D",
            note="VDD5 filter: the hub's own supply off VBUS")
    b.cite("R3", "REXT", "2.7k 1%", 5, "resister should be connected to VSS", url=DS)
    b.cite("C1", "VD33_O cap", "10u", 5, "decoupling capacitor is required", url=DS)
    for x, y in ((3.5, H - 3.5), (W - 3.5, H - 3.5)):
        b.hole((x, y), d=3.2)

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] +
          [("F1", "1"), ("FB1", "1"), ("C4", "1"), ("R5", "1"), ("R8", "1")], amps=2.0, volts=5.0)
    b.net("VDD5", [("FB1", "2"), u("VDD5"), ("C3", "1")], amps=0.2, volts=5.0)   # (the chip's own supply, tens
    #                                                     of mA: the 2 A stays on the connector-fuse-ports path)
    b.net("VBUSD", [("F1", "2")] + [(f"J{i + 2}", "VBUS") for i in range(4)] + [(f"C{10 + i}", "1") for i in range(4)],
          amps=2.0, volts=5.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [(f"J{i + 2}", n) for i in range(4) for n in ("GND", "Shield")] + [(f"C{10 + i}", "2") for i in range(4)] +
          [u("VSS"), u("BUSJ"), ("R1", "2"), ("R2", "2"), ("R3", "2"), ("R6", "2"), ("C1", "2"), ("C2", "2"),
           ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"), ("C7", "2"), ("D1", "1"), ("Y1", "2"), ("Y1", "4")],
          amps=2.0)
    b.net("3V3", [u("VD33_O"), u("VD33"), ("C1", "1"), ("C6", "1"), ("R4", "1"), ("R7", "1")], amps=0.1, volts=3.3)
    b.net("1V8", [u("VD18_O"), u("VD18"), ("C2", "1"), ("C7", "1")], amps=0.1, volts=1.8)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("UDP", [("J1", "A6"), ("J1", "B6"), u("DPU")])
    b.net("UDM", [("J1", "A7"), ("J1", "B7"), u("DMU")])
    b.pair("UDP", "UDM", max_skew=1.25)
    for i in range(4):
        b.net(f"DP{i + 1}", [u(f"DP{i + 1}"), (f"J{i + 2}", "D+")])
        b.net(f"DM{i + 1}", [u(f"DM{i + 1}"), (f"J{i + 2}", "D-")])
        b.pair(f"DP{i + 1}", f"DM{i + 1}", max_skew=1.25)
    b.net("XIN", [u("XIN"), ("Y1", "1")])
    b.net("XOUT", [u("XOUT"), ("Y1", "3")])
    b.net("REXT", [u("REXT"), ("R3", "1")])
    b.net("RST", [u("nXRSTJ"), ("R4", "2"), ("C5", "1")])
    b.net("VBM", [u("VBUSM"), ("R5", "2"), ("R6", "1")])
    b.net("OVC", [u("OVCJ"), ("R7", "2")])
    b.net("PLED", [("R8", "2"), ("D1", "2")])
    b.nc("U1", "PWRJ", "DRV", "LED1/EESCL", "LED2", "TESTJ/EESDA")
    b.nc("J1", "SBU1", "SBU2")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", [(31, 22), (47, 22), (47, 38), (31, 38)])   # (round the SSOP on top too: its ground pins drop
    #                                                      into copper)
    # VBUS and GND off the receptacle's 0.5 mm-pitch pins by hand at 0.52 mm (2 A), each VBUS pin pair outward
    # (the data and CC pins between them leave straight down)
    for pin, dx in (("A9", -2.2), ("A4", 2.2)):
        x, y = b.pad("J1", pin)
        b.track("VBUS", "F", [(x, y), (x, y - 1.2), (x + dx, y - 3.0)], 0.52)
        b.via("VBUS", (x + dx, y - 3.0), 0.4, 0.8)        # (the router takes it on from here, either layer:
        b.via("VBUS", (x + dx * 1.45, y - 3.0), 0.4, 0.8)  #  two 0.4 mm vias, ~2.7 A, for the 2 A; the second
        b.track("VBUS", "F", [(x + dx, y - 3.0), (x + dx * 1.45, y - 3.0)], 0.8)   #  outward, clear of C7,
        b.track("VBUS", "B", [(x + dx, y - 3.0), (x + dx * 1.45, y - 3.0)], 0.8)   #  joined on both layers)
    b.track("VDD5", "F", [b.pad("U1", "20"), b.pad("C3", "1")], 0.2)   # (the chip's 5 V pin straight up into its
    #                                                                     decoupling cap: 0.9 mm, by hand)
    tabs = [g.centroid for r, n, L, g, d in b.pad_shapes() if r == "J1" and n == "SH" and L == "F"]
    for pin in ("A1", "A12"):
        x, y = b.pad("J1", pin)
        t = min(tabs, key=lambda q: (q.x - x) ** 2 + (q.y - y) ** 2)
        b.track("GND", "F", [(x, y), (t.x, t.y)], 0.52)
    return b
