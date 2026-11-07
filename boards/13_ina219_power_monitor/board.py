"""INA219 high-side current / power monitor breakout.

The load current flows in at J1 pin 2 (VIN+, from the supply), through the 0.1 ohm 2512 shunt R1 and out at J1 pin 1
(VIN-, to the load): 5.08 mm screw terminal, up to 3.2 A (the INA219's +-320 mV range on 0.1 ohm), bus up to 26 V.
INA219AIDR (SOIC-8) measures the shunt through the datasheet's optional input filter (10 ohm in each lead, 100 nF
across), and the bus voltage at IN-. 1x04 2.54 mm header J2: VCC (3.0-5.5 V), GND, SDA, SCL; 10 k pull-ups on SDA /
SCL to VCC; A0 and A1 strapped to GND by 0 ohm resistors R4 / R5: I2C address 0x40 (lift a strap and wire the pin to
VCC, SDA or SCL for the other 15 addresses). The load's ground must be common with the header's GND.
Difficulty: 3/5. 2 layers, 1 oz, 36 x 13 mm, JLCPCB. VIN+ / VIN- are copper pours on F (3.2 A), GND on B and on F's
left half.

Assumptions (datasheet not fetched in this run, values from memory of TI SBOS448):
- Pin map from KiCad's Sensor_Energy:INA219AxD symbol (harness matched 7 of 8 pins to the datasheet's table; pin 7
  "IN-" is only spelt differently there).
- Input filter 10 ohm + 100 nF (the datasheet's "Input filtering" section: series resistors of 10 ohm or less);
  VS decoupling 100 nF.
- Shunt: Bourns CRA2512-FZ-R100ELF (0.1 ohm, 1 %, 2512, 3 W): 1.0 W dissipated at 3.2 A.
- J1 pin 2 is VIN+ (supply side) so the shunt and the INA219's IN+ / IN- pins line up without crossing.

    prometheus boards/13_ina219_power_monitor/board.py out/ --spec boards/13_ina219_power_monitor/req.json
"""
from pcb import Board

R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
W, H = 36.0, 13.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
XS = 22.5                                       # shunt column
XP = 18.3                                       # left edge of the VIN+ / VIN- pours
FIXED = {"R6": ((XP, 8.4), 180),         # 10 R filter, VIN+ -> IN+ (pad 1 in the VIN+ pour)
         "R7": ((XP, 5.0), 180),          # 10 R filter, VIN- -> IN-
         "C1": ((15.0, 2.1), 270)}               # VS 100n by pin 5


def build():
    b = Board("ina219_power_monitor", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.0)
    b.place("J1", TB, (W - 5.2, 3.9), 90, "1 VIN- / 2 VIN+", "Phoenix 1729128")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (2.5, 10.2), 0, "I2C",
            "XFCN PZ254V-11-04P", lcsc="C2829861")
    b.place("U1", None, (12.5, 6.4), 0, "INA219", "TI INA219AIDR", symbol="Sensor_Energy:INA219AxD", lcsc="C138706")
    b.place("R1", "Resistor_SMD:R_2512_6332Metric", (XS, 6.4), 270, "0R1 1% 3W", "Bourns CRA2512-FZ-R100ELF",
            note="current shunt")
    for ref, fp, val, note in (("C1", C06, "100n", "VS decoupling"), ("C2", C06, "100n", "input filter, IN+ to IN-"),
                               ("R2", R06, "10k", "SDA pull-up"), ("R3", R06, "10k", "SCL pull-up"),
                               ("R4", R06, "0R", "A0 strap to GND"), ("R5", R06, "0R", "A1 strap to GND"),
                               ("R6", R06, "10R", "input filter IN+"), ("R7", R06, "10R", "input filter IN-")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)

    u = lambda n: ("U1", n)
    b.net("VIN+", [("J1", "2"), ("R1", "1"), ("R6", "1")], amps=3.2, volts=26.0)
    b.net("VIN-", [("J1", "1"), ("R1", "2"), ("R7", "1")], amps=3.2, volts=26.0)
    b.net("IN+", [("R6", "2"), u("IN+"), ("C2", "1")])
    b.net("IN-", [("R7", "2"), u("IN-"), ("C2", "2")])
    b.net("VCC", [("J2", "1"), u("VS"), ("C1", "1"), ("R2", "1"), ("R3", "1")], amps=0.05)
    b.net("GND", [("J2", "2"), u("GND"), ("C1", "2"), ("R4", "2"), ("R5", "2")], amps=0.1)
    b.net("SDA", [("J2", "3"), u("SDA"), ("R2", "2")])
    b.net("SCL", [("J2", "4"), u("SCL"), ("R3", "2")])
    b.net("A0", [u("A0"), ("R4", "1")])
    b.net("A1", [u("A1"), ("R5", "1")])
    b.pour("VIN+", "F", [(XP, 6.7), (W, 6.7), (W, H), (XP, H)])
    b.pour("VIN-", "F", [(XP, 0), (W, 0), (W, 6.1), (XP, 6.1)])
    b.pour("GND", "F", [(0, 0), (XP - 1.6, 0), (XP - 1.6, H), (0, H)])
    b.pour("GND", "B", OUT)
    return b
