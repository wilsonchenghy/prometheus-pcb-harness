"""BNO085 IMU breakout: USB-C 5 V in, AP2112K 3.3 V LDO, BNO085 in I2C mode (PS0 = PS1 = 0, SA0 low -> 0x4A),
32.768 kHz crystal, 6-pin 2.54 mm header (3V3, GND, SDA, SCL, INT, RST) to the host MCU. 2 layers, GND on both.

The BNO085's pad numbers are checked against its symbol, Prometheus:BNO085 (src/pcb_symbols), read from CEVA's
BNO08X datasheet (figure 1-6, pins 1-28); it sits on KiCad's LGA-28 5.2 x 3.8 mm footprint.

    prometheus examples/imu_bno085/imu_bno085.py out/ --spec examples/imu_bno085/req.json
"""
from pcb import Board

R04, C04 = "Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric"
W, H = 18.0, 22.0
OUT = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]

# BNO08x LGA-28 pad numbers (CEVA BNO08X datasheet, figure 1-6)
PIN = {"GND": ["2", "25"], "VDD": "3", "BOOTN": "4", "PS1": "5", "PS0": "6", "CAP": "9", "CLKSEL0": "10",
       "NRST": "11", "H_INTN": "14", "SA0": "17", "H_CSN": "18", "SCL": "19", "SDA": "20", "XOUT32": "26",
       "XIN32": "27", "VDDIO": "28"}


# hand-placed by the pins they serve: (at, rotation)
FIXED = {"C2": ((4.9, 14.6), 0), "C3": ((15.6, 13.1), 180), "C4": ((15.6, 11.7), 0), "C5": ((13.0, 16.6), 0),
         "R3": ((15.8, 15.1), 0)}


def build():
    b = Board("imu_bno085", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.0,
              clearance=0.15)                                          # 0.5 mm LGA: 0.2 mm cannot clear its corners
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (W / 2, 3.2), 0, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (2.65, H - 1.9), 90, "I2C",
            "XFCN PZ254V-11-06P", lcsc="C492405")
    b.place("U1", "Package_LGA:LGA-28_5.2x3.8mm_P0.5mm", (11.5, 13.6), 180, "BNO085", "CEVA BNO085", symbol="Prometheus:BNO085")
    b.place("U2", "Package_TO_SOT_SMD:SOT-23-5", (3.6, 12.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1",
            symbol="Regulator_Linear:AP2112K-3.3")
    b.place("Y1", "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", (14.3, 9.7), 0, "32.768k", "Epson Q13FC13500004 (FC-135, 12.5 pF)",
            lcsc="C32346")
    for ref, fp, val, note in (("C1", C04, "1u", "LDO in"), ("C2", C04, "1u", "LDO out"),
                               ("C3", C04, "100n", "VDD"), ("C4", C04, "100n", "VDDIO"), ("C5", C04, "100n", "CAP"),
                               ("C6", C04, "22p", "XIN32"), ("C7", C04, "22p", "XOUT32"),
                               ("R1", R04, "5k1", "CC1"), ("R2", R04, "5k1", "CC2"),
                               ("R3", R04, "10k", "BOOTN up"), ("R4", R04, "10k", "NRST up"),
                               ("R5", R04, "4k7", "SDA up"), ("R6", R04, "4k7", "SCL up"),
                               ("R7", R04, "10k", "H_CSN up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)

    u = lambda n: ("U1", PIN[n])
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U2", "VIN"), ("U2", "EN"), ("C1", "1")],
          amps=0.1, volts=5.0)
    b.net("3V3", [("U2", "VOUT"), ("C2", "1"), u("VDD"), u("VDDIO"), ("C3", "1"), ("C4", "1"), ("J2", "1"),
                  ("R3", "1"), ("R4", "1"), ("R5", "1"), ("R6", "1"), ("R7", "1")], amps=0.05, volts=3.3,
          width=0.15)                                                    # 0.15 mm: clears the LGA corner pads
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [("U1", p) for p in PIN["GND"]] + [u("PS0"), u("PS1"), u("SA0"), u("CLKSEL0"), ("U2", "GND"), ("J2", "2")] +
          [(c, "2") for c in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "R1", "R2")], amps=0.1)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("SDA", [u("SDA"), ("R5", "2"), ("J2", "3")])
    b.net("SCL", [u("SCL"), ("R6", "2"), ("J2", "4")])
    b.net("INT", [u("H_INTN"), ("J2", "5")], width=0.15)
    b.net("NRST", [u("NRST"), ("R4", "2"), ("J2", "6")])
    b.net("BOOTN", [u("BOOTN"), ("R3", "2")])
    b.net("H_CSN", [u("H_CSN"), ("R7", "2")])
    b.net("CAP", [u("CAP"), ("C5", "1")])
    b.net("XIN32", [u("XIN32"), ("Y1", "1"), ("C6", "1")])
    b.net("XOUT32", [u("XOUT32"), ("Y1", "2"), ("C7", "1")])
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
