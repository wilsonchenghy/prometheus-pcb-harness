"""HopeRF RFM95W LoRa module breakout (868 MHz variant; the 915 MHz RFM95W-915S2 is pin- and footprint-identical):
the module on KiCad's RF_Module:HOPERF_RFM9XW_SMD land pattern, an edge-mount SMA jack (Samtec SMA-J-P-H-ST-EM1)
on the right edge fed by a 1.6 mm long 50 ohm grounded coplanar line from the module's ANT pin, an AP2112K-3.3
LDO (600 mA) from 5 V, and a 1x10 2.54 mm header J1 (pin 1 at the top): 1 DIO1, 2 DIO0, 3 MISO, 4 MOSI, 5 SCK,
6 NSS, 7 RESET, 8 3V3, 9 GND, 10 5V (order follows the module's own pin order so the SPI lines run straight).
Difficulty: 3/5. 2 layers, 1 oz, 1.6 mm FR4, 32 x 30 mm, JLCPCB. GND poured on both layers.

RF feed: grounded coplanar waveguide (CPWG), 1.2 mm track, 0.2 mm gap to the top GND pour (the board clearance),
solid GND on the bottom layer underneath, 1.5 mm FR4 (er ~4.5) under it: ~50 ohm by the conformal-mapping CPWG
formula (computed for this board, not from a datasheet). It is ~1.6 mm long (lambda/4 at 868 MHz in FR4 is ~45 mm),
so its exact impedance hardly matters; the module's ANT pad sits on the same y as the SMA's centre pin so the
line is straight: drawn by hand with b.track (the built-in router put a 0.5 mm jog in it). So this board is
never cached in routes/ (the harness caches only boards it routed from nothing), and C4 is left to the placer
(near U1.13): a run with every part fixed and a hand-drawn track skips routing altogether. GND stitching vias flank the line. There is no copper keep-out: the antenna is off-board (on the
SMA), and the module wants a solid ground under it (pin 1 / 8 / 10 GND pads drop to the bottom pour).

Choices and assumptions:
- Module pin map from KiCad's RF_Module:RFM95W-868S2 symbol, checked against the HopeRF RFM95/96/97/98(W)
  datasheet's pin description (p.11, cited for ANT). KiCad's symbol names no footprint: HOPERF_RFM9XW_SMD given.
- Power: 120 mA at +20 dBm on PA_BOOST (datasheet p.13, cited): AP2112K-3.3 (600 mA, 250 mV dropout).
  1 uF X7R in and out (AP2112 datasheet p.2 typical application, cited) plus 10 uF + 100 nF at the module's
  3.3V pin 13 (usual practice for the TX current step; not a datasheet value). LDO EN tied to VIN.
- RESET has the module's own pull-up; left to the host (open-drain drive, per HopeRF / Semtech usage), no part.
- DIO2-DIO5 not brought out (FSK / CAD extras; LoRa needs only DIO0 TxDone/RxDone, DIO1 RxTimeout).
- 3V3 is on the header so a 3.3 V host can power the module directly (leave 5V open) or use the LDO's output.

    prometheus boards/30_rfm95_lora/board.py out/ --spec boards/30_rfm95_lora/req.json
"""
from pcb import Board

C06, C08 = "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
DS_RF = "https://cdn.sparkfun.com/assets/learn_tutorials/8/0/4/RFM95_96_97_98W.pdf"
DS_LDO = "https://www.diodes.com/assets/Datasheets/AP2112.pdf"
W, H = 32.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = W - 14.5, 17.5                         # module centre: its ANT pad (9) at (UX + 7.53, UY - 7)
FY = UY - 7.0                                   # the feed's y
FIXED = {"C3": ((UX + 10.6, UY + 1.0), 90),     # 100n at module pin 13 (3.3V)
         "C1": ((7.6, 3.4), 90), "C2": ((13.6, 3.4), 90)}   # LDO in / out


def build():
    b = Board("rfm95_lora", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("U1", "RF_Module:HOPERF_RFM9XW_SMD", (UX, UY), 0, "RFM95W-868S2", "HopeRF RFM95W-868S2",
            symbol="RF_Module:RFM95W-868S2",
            cite={"page": 11, "quote": "RF signal output/input", "what": "pin 9 ANT", "url": DS_RF})
    b.place("J2", "Connector_Coaxial:SMA_Samtec_SMA-J-P-H-ST-EM1_EdgeMount", (W - 2.6, FY), 0, "SMA",
            "Samtec SMA-J-P-H-ST-EM1", overhang=True)
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical", (2.5, 26.43), 0,
            "DIO1 DIO0 MISO MOSI SCK NSS RST 3V3 GND 5V", "XFCN PZ254V-11-10P", lcsc="C2829867")
    b.place("U2", None, (10.6, 3.6), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1", symbol="Regulator_Linear:AP2112K-3.3",
            lcsc="C51118")
    for ref, fp, val, note in (("C1", C06, "1u", "LDO in"), ("C2", C06, "1u", "LDO out"),
                               ("C3", C06, "100n", "module 3.3V"), ("C4", C08, "10u", "module 3.3V bulk")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    for c in ("C1", "C2"):
        b.cite(c, "LDO input / output capacitor", "1u", 2, "1.0µF ceramic capacitor is selected as input/output",
               url=DS_LDO)
    b.cite("U2", "module TX current at +20 dBm", "120 mA", 13, "RFOP = +20 dBm, on PA_BOOST", url=DS_RF)
    b.keepout([(UX - 5.9, UY - 8.1), (UX + 5.9, UY - 8.1), (UX + 5.9, UY + 8.1), (UX - 5.9, UY + 8.1)], layers="F",
              why="no top copper under the module's body: only its edge pads touch the board")
    for dx in (0.4,):                          # GND stitching beside the feed (centres 1.5 mm off the line)
        for dy in (-1.5, 1.5):
            b.via("GND", (W - 5.0 + dx, FY + dy), drill=0.3, dia=0.6)

    u, v = (lambda n: ("U1", n)), (lambda n: ("U2", n))
    b.net("5V", [("J1", "10"), v("VIN"), v("EN"), ("C1", "1")], amps=0.2, volts=5.0)
    b.net("3V3", [("J1", "8"), v("VOUT"), ("C2", "1"), u("3.3V"), ("C3", "1"), ("C4", "1")], amps=0.2, volts=3.3)
    b.net("GND", [("J1", "9"), v("GND"), u("GND"), ("J2", "2"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2")],
          amps=0.2)
    b.net("ANT", [u("ANT"), ("J2", "1")], width=1.2)
    b.track("ANT", "F", [b.pad("U1", "9"), b.pad("J2", "1")], 1.2)     # the feed, straight, drawn by hand
    b.net("DIO1", [u("DIO1"), ("J1", "1")])
    b.net("DIO0", [u("DIO0"), ("J1", "2")])
    b.net("MISO", [u("MISO"), ("J1", "3")])
    b.net("MOSI", [u("MOSI"), ("J1", "4")])
    b.net("SCK", [u("SCK"), ("J1", "5")])
    b.net("NSS", [u("NSS"), ("J1", "6")])
    b.net("RESET", [u("RESET"), ("J1", "7")])
    b.nc("U1", "DIO2", "DIO3", "DIO4", "DIO5")
    b.nc("U2", "NC")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
