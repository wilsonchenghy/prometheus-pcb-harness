"""I2C environment sensor breakout: Bosch BME280 (pressure / humidity / temperature, LGA-8 2.5 x 2.5) and Sensirion
SHT40 (humidity / temperature, DFN-4 1.5 x 1.5) on one I2C bus, an AP2112K-3.3 LDO from VIN, 10 k SDA / SCL
pull-ups, two STEMMA QT / Qwiic JST-SH 4-pin connectors (J1 in at the bottom edge, J2 out at the top edge, each
1 GND, 2 3V3, 3 SDA, 4 SCL: the SparkFun / Adafruit pinout) and a 1x05 2.54 mm header J3 (1 VIN, 2 3V3, 3 GND,
4 SCL, 5 SDA). A 1 mm routed slot (b.cutout) between the power side and the sensor side keeps the LDO's and the
connectors' heat (and the host's, through the cable copper) away from the sensors; only the two bridges above and
below it carry the four nets and the pours across.
Difficulty: 2/5. 2 layers, 1 oz, 24.5 x 17.5 mm, JLCPCB, GND poured on both layers.

I2C addresses: BME280 0x76 (SDO to GND, datasheet p.32, cited), SHT40-AD1B 0x44 (fixed by the part number).
BME280 in I2C mode: CSB tied to VDDIO (p.32).

Choices and assumptions:
- Pin maps from KiCad's Sensor:BME280 (the harness read it against the datasheet's pin table, p.38) and
  Sensor_Humidity:SHT4x symbols (1 SDA, 2 SCL, 3 VDD, 4 VSS; the Sensirion datasheet could not be fetched here:
  assumed from KiCad's symbol, check before fab).
- BME280 VDD and VDDIO each with 100 nF (datasheet p.39: "The recommended value for C1, C2 is 100 nF", cited).
  SHT40 VDD with 100 nF (Sensirion's usual recommendation; not cited, datasheet not fetched).
- 10 k pull-ups: fine for 100 / 400 kHz on a short Qwiic chain; with several boards on one bus each adds its
  pull-ups in parallel (cut-trace jumpers not fitted to keep the board small).
- AP2112K-3.3: 1 uF in and out (AP2112 datasheet p.2, cited), EN tied to VIN. VIN 3.5 - 6 V on J3.1. The Qwiic 3V3
  is the LDO's output: power the board from either the Qwiic host or VIN, not from a 3.3 V host and a VIN supply at
  once (the AP2112 output is then back-driven; Adafruit's breakouts share the same trade-off).
- Sensors and the slot: the sensors sit 4 - 9 mm right of the slot, the LDO ~6 mm left of it.

    prometheus boards/31_env_sensor_bme280_sht40/board.py out/ --spec boards/31_env_sensor_bme280_sht40/req.json
"""
from pcb import Board

R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
SH4 = "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"
DS_BME = "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf"
DS_LDO = "https://www.diodes.com/assets/Datasheets/AP2112.pdf"
W, H = 24.5, 17.5
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
SLOT = [(15.5, 3.5), (16.5, 3.5), (16.5, H - 3.5), (15.5, H - 3.5)]  # thermal slot, bridges above and below
U1X, U1Y = 19.5, 6.5                                  # BME280; the SHT40 above it
U2X, U2Y = 20.0, 12.0
FIXED = {"C3": ((U1X - 1.6, U1Y - 3.4), 90),   # 100n at BME280 VDD (pin 8, bottom row left)
         "C4": ((U1X + 1.6, U1Y - 3.4), 90),    # 100n at BME280 VDDIO (pin 6, bottom row)
         "C5": ((U2X + 1.0, U2Y - 2.7), 0),     # 100n at SHT40 VDD (pin 3)
         "C1": ((6.2, H / 2 - 1.25), 180),      # LDO in 1u, pad 1 toward U3 pin 1 (VIN)
         "C2": ((6.2, H / 2 + 1.25), 180),      # LDO out 1u, pad 1 toward U3 pin 5 (VOUT)
         }


def build():
    b = Board("env_sensor_bme280_sht40", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.cutout(SLOT)
    b.place("J1", SH4, (10.0, 3.2), 0, "Qwiic IN", "JST SM04B-SRSS-TB(LF)(SN)", overhang=True, lcsc="C160390")
    b.place("J2", SH4, (10.0, H - 3.2), 180, "Qwiic OUT", "JST SM04B-SRSS-TB(LF)(SN)", overhang=True,
            lcsc="C160390")
    b.place("J3", "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical", (2.5, H - 2.4), 0,
            "VIN 3V3 GND SCL SDA", "XFCN PZ254V-11-05P", lcsc="C2829862")
    b.place("U3", None, (9.6, H / 2), 90, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1", symbol="Regulator_Linear:AP2112K-3.3",
            lcsc="C51118")
    b.place("U1", None, (U1X, U1Y), 0, "BME280", "Bosch BME280", symbol="Sensor:BME280", lcsc="C92489")
    b.place("U2", None, (U2X, U2Y), 0, "SHT40", "Sensirion SHT40-AD1B-R2", symbol="Sensor_Humidity:SHT4x")
    for ref, fp, val, note in (("C1", C06, "1u", "LDO in"), ("C2", C06, "1u", "LDO out"),
                               ("C3", C06, "100n", "BME280 VDD"), ("C4", C06, "100n", "BME280 VDDIO"),
                               ("C5", C06, "100n", "SHT40 VDD"),
                               ("R1", R06, "10k", "SDA pull-up"), ("R2", R06, "10k", "SCL pull-up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    for c in ("C3", "C4"):
        b.cite(c, "BME280 VDD / VDDIO decoupling", "100n", 39, "The recommended value for C1, C2 is 100 nF", url=DS_BME)
    for c in ("C1", "C2"):
        b.cite(c, "LDO input / output capacitor", "1u", 2, "1.0µF ceramic capacitor is selected as input/output",
               url=DS_LDO)
    b.cite("U1", "SDO to GND: address 0x76", "GND", 32, "Connecting SDO to GND results in slave address", url=DS_BME)

    u, s, v = (lambda n: ("U1", n)), (lambda n: ("U2", n)), (lambda n: ("U3", n))
    b.net("VIN", [("J3", "1"), v("VIN"), v("EN"), ("C1", "1")], amps=0.1, volts=5.0)
    b.net("3V3", [("J3", "2"), ("J1", "2"), ("J2", "2"), v("VOUT"), ("C2", "1"), u("VDD"), u("VDDIO"), u("CSB"),
                  ("C3", "1"), ("C4", "1"), s("VDD"), ("C5", "1"), ("R1", "1"), ("R2", "1")], amps=0.1, volts=3.3)
    b.net("GND", [("J3", "3"), ("J1", "1"), ("J2", "1"), ("J1", "MP"), ("J2", "MP"), v("GND"), u("GND"), u("SDO"),
                  s("VSS"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), ("C5", "2")], amps=0.1)
    b.net("SDA", [("J3", "5"), ("J1", "3"), ("J2", "3"), u("SDI"), s("SDA"), ("R1", "2")])
    b.net("SCL", [("J3", "4"), ("J1", "4"), ("J2", "4"), u("SCK"), s("SCL"), ("R2", "2")])
    b.nc("U3", "NC")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
