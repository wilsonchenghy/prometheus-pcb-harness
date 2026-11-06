"""MAX31855K K-type thermocouple amplifier breakout.

Thermocouple on a 2-pin 3.5 mm screw terminal J1 (pin 1 T-, pin 2 T+), each lead through a
ferrite bead (FB1, FB2) to the chip, 10 nF across T+ / T- right at the pins (the datasheet's differential filter);
MAX31855KASA+ (SOIC-8) on 3.3 V from an AP2112K-3.3 LDO fed with 5 V; 100 nF VCC decoupling; 10 k pull-up keeps /CS
deselected. 1x06 2.54 mm header J2: 1 SO, 2 /CS, 3 SCK, 4 3V3 (out, up to ~500 mA spare), 5 GND, 6 VIN (5 V).
SPI logic is 3.3 V: a 5 V host needs level shifting on SCK and /CS (the inputs are not 5 V tolerant).
Difficulty: 3/5. 2 layers, 1 oz, 30 x 20 mm, JLCPCB, GND poured on both sides (the GND pin is the chip's thermal
path to the board: the cold-junction sensor reads the board's temperature by the terminal, so U1 sits next to J1).

Thermocouple colours: ANSI K: yellow = T+ (chromel), red = T- (alumel). Wire yellow to J1 pin 2, red to J1 pin 1.

Assumptions (datasheets not fetched in this run, values from memory):
- Pin maps from KiCad's Sensor_Temperature:MAX31855KASA and Regulator_Linear:AP2112K-3.3 symbols (harness matched the
  AP2112's to its datasheet, 5 / 5).
- T- is NOT grounded (the MAX31855 biases T- itself; grounding it is a datasheet "do not"); 10 nF differential
  capacitor (Maxim's recommended 0.01 uF across T+ / T-); ferrites Murata BLM18PG221SN1D (220 ohm at 100 MHz, 0603)
  as series RF filtering, a choice beyond the datasheet's minimum.
- AP2112K-3.3: 1 uF in, 1 uF out (its datasheet's typical application); EN tied to VIN (always on).

    prometheus boards/25_max31855_thermocouple/board.py out/ --spec boards/25_max31855_thermocouple/req.json
"""
from pcb import Board

R06, C06, L06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Inductor_SMD:L_0603_1608Metric"
W, H = 30.0, 20.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = 17.5, 10.0
FIXED = {"FB2": ((10.4, UY + 0.9), 0),          # T- ferrite
         "FB1": ((10.4, UY - 0.9), 0),          # T+ ferrite
         "C2": ((13.0, UY), 90),                # 10 nF across T+ (pad 1, below) / T- (pad 2), by pins 2 / 3
         "C1": ((14.5, UY - 3.5), 0)}           # VCC 100 nF by pin 4


def build():
    b = Board("max31855_thermocouple", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.0)
    b.place("J1", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal",
            (5.2, UY + 1.75), 270, "1 T- / 2 T+", "Phoenix 1984617")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (W - 2.5, 16.35), 0, "SPI",
            "XFCN PZ254V-11-06P", lcsc="C492405")
    b.place("U1", None, (UX, UY), 0, "MAX31855K", "Analog Devices MAX31855KASA+T",
            symbol="Sensor_Temperature:MAX31855KASA", lcsc="C52028")
    b.place("U2", None, (23.0, 4.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1", symbol="Regulator_Linear:AP2112K-3.3",
            lcsc="C51118")
    for ref, fp, val, mpn, note in (
            ("FB1", L06, "220R@100MHz", "Murata BLM18PG221SN1D", "T+ ferrite"),
            ("FB2", L06, "220R@100MHz", "Murata BLM18PG221SN1D", "T- ferrite"),
            ("C2", C06, "10n", "", "thermocouple differential filter"),
            ("C1", C06, "100n", "", "U1 VCC decoupling"),
            ("C3", C06, "1u", "", "LDO input"), ("C4", C06, "1u", "", "LDO output"),
            ("R1", R06, "10k", "", "/CS pull-up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, mpn, note=note)

    u = lambda n: ("U1", n)
    b.net("TC-", [("J1", "1"), ("FB2", "1")])
    b.net("TC+", [("J1", "2"), ("FB1", "1")])
    b.net("T-", [("FB2", "2"), ("C2", "2"), u("T-")])
    b.net("T+", [("FB1", "2"), ("C2", "1"), u("T+")])
    b.net("VIN", [("J2", "6"), ("U2", "VIN"), ("U2", "EN"), ("C3", "1")], amps=0.1, volts=5.0)
    b.net("3V3", [("U2", "VOUT"), ("C4", "1"), u("V_{CC}"), ("C1", "1"), ("R1", "1"), ("J2", "4")], amps=0.1, volts=3.3)
    b.net("GND", [("J2", "5"), ("U2", "GND"), ("C3", "2"), ("C4", "2"), u("GND"), ("C1", "2")], amps=0.1)
    b.net("SCK", [u("SCK"), ("J2", "3")])
    b.net("CS", [u("nCS"), ("R1", "2"), ("J2", "2")])
    b.net("SO", [u("SO"), ("J2", "1")])
    b.nc("U1", "NC")
    b.nc("U2", "NC")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
