# DESIGN SPEC: Twilight flight computer, Rev A (the "Twilight Flight Computer" concept document, Aug 2026).
# One slender board for a high-power rocket or an RC aircraft: Teensy 4.1 (i.MX RT1062), redundant inertial sensing,
# 4 pyro channels behind an arming chain, 6 buffered servo outputs, LoRa beacon + telemetry radio port, CAN-FD.
# Board frame: mm, y up, origin at the bottom-left corner; 165 x 50 mm, 4 layers (F, In1 = GND plane, In2 = power
# planes: VIN at the power end, +5 V elsewhere, B), 1 oz copper, 1.6 mm FR4, 0.15 mm tracks and clearance. Six M3 holes on a
# 76.5 x 38 mm pitch (corners + midspan).
#
# Floorplan, left to right: POWER (x 0-60: XT30 in, fuse, reverse FET, TVS, shunt + INA226; 5 V and 6 V bucks; BEC
# inlet), the TEENSY 4.1 on two 24-pin sockets (x 60-121) with the PYRO block above it and the SERVO block below it, low
# parts under it (it sits ~11 mm up on sockets + its headers), the INERTIAL island (x 122-140) and RF / I-O (x 140-165).
#
# Power tree (input 2S-6S, 6.5-26 V):
#   J1 XT30 -> F1 15 A (Littelfuse 0451015.MRL) -> Q1 P-FET reverse protection (SQM40031EL, 3 mOhm) -> D1 SMBJ33A
#   -> RS1 2 mOhm shunt (INA226 at 0x40 on I2C) -> VIN.
#   VIN -> U2 TPS54360B -> +5 V, 3.5 A (TI's 600 kHz design: MCU, radios, GPS, CAN, servo buffer, LEDs).
#   +5 V -> FB1 -> U4 TPS7A2033 -> 3V3A (IMUs, baros, magnetometer: their own quiet rail).
#   +5 V -> U12 TPS7A2033 -> 3V3R (LoRa module). The Teensy's own 3.3 V output -> 3V3 (CAN, INA226, I2C pull-ups).
#   VIN -> U3 TPS54560B -> VSERVO 6.0 V, 5 A (Mode A); or J2 XT30 external BEC -> Q2 P-FET -> VSERVO (Mode B, 12 A
#   continuous through the board; leave L2 off so the BEC cannot back-feed the buck).
#   VIN -> F2 7 A -> D2 Schottky -> VPYRO (470 uF) -> external arm switch (JA) -> Q3 P-FET (ARM_EN) -> VPYRO_ARMED.
#
# Pin map (Teensy 4.1, every one of the 42 edge pins used; see out/README.md):
#   0/1 TELEM1 (Serial1)   2,3,4,5,6,9 servo 1-6 (FlexPWM)   7/8 GPS (Serial2)   10 CS ICM-42688   11/12/13 SPI0
#   14 expansion CS   15 ARM_EN   16 ICM-42688 INT1   17 BTN   18/19 I2C (SDA/SCL)   20/21/40/41 pyro continuity ADC
#   22/23 CS BMI088 accel/gyro   24 CS ADXL375   25 VSERVO sense ADC   26/27/39 SPI1 (MOSI1/SCK1/MISO1)   38 CS LoRa
#   28/29/32/33 FIRE 1-4   30/31 CAN3 (CAN-FD)   34/35 RC (Serial8: SBUS / CRSF, RX inverted in the LPUART)
#   36 buzzer   37 WS2812 chain (STATUS, SD, FAULT)

import math
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."), os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
                os.path.dirname(os.path.abspath(__file__))]
from pcb import Board, route, cached_route, route_jobs, nudge_vias
import pcb_bodies as bodies

W, H = 165.0, 50.0
HOLES = [(x, y) for x in (5.75, 82.25, 158.75) for y in (6.0, 44.0)]
# Teensy 4.1 on two 1x24 sockets, USB end at +x: pin position k (1 = USB end) at x = TX1 - 2.54 (k - 1)
TX1, TY = 119.634, 24.892                     # on the 0.254 mm routing grid: every gap between
#                                               two socket pins takes a track
ROW_T, ROW_B = TY + 7.62, TY - 7.62           # the module's "left" row (GND, 0-12, 3.3V, 24-32) / "right" row
TOP_ROW = ["GND"] + [str(i) for i in range(13)] + ["3V3"] + [str(i) for i in range(24, 33)]
BOT_ROW = ["VIN", "GND", "3V3"] + [str(i) for i in range(23, 12, -1)] + ["GND"] + [str(i) for i in range(41, 32, -1)]
DATUM = (130.0, 25.0)                         # sensor datum: the ICM-42688-P centre (silkscreened)

R06, C06 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
R12 = "Resistor_SMD:R_1206_3216Metric"
C08, C1210 = "Capacitor_SMD:C_0805_2012Metric", "Capacitor_SMD:C_1210_3225Metric"
LED06 = "LED_SMD:LED_0603_1608Metric"
TP, TPL = "TestPoint:TestPoint_Pad_D1.5mm", "TestPoint:TestPoint_Keystone_5005-5009_Compact"
XT30M = "Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal"
NANO2 = "Fuse:Fuse_Littelfuse-NANO2-451_453"
D2PAK = "Package_TO_SOT_SMD:TO-263-2"
HSOP8 = "Package_SO:Texas_R-PDSO-G8_EP2.95x4.9mm_Mask2.4x3.1mm_ThermalVias"
PDI5 = "Diode_SMD:D_PowerDI-5"
XAL = "Inductor_SMD:L_Coilcraft_XAL6060-XXX"
VSON = "Package_SON:VSONP-8-1EP_5x6_P1.27mm"
SOT23, SOT235 = "Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23-5"
TERM = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal"
GH4 = "Connector_JST:JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical"
GH6 = "Connector_JST:JST_GH_BM06B-GHS-TBT_1x06-1MP_P1.25mm_Vertical"


def m3d(fp):
    """the KiCad 3D model path of a library footprint"""
    lib, name = fp.split(":")
    return f"{lib}.3dshapes/{name}.step"


def tpin(pin):
    """a Teensy pin name ('0'..'41', 'VIN', '3V3', 'GND') -> [(socket ref, pad)]"""
    out = [("JT1", str(k + 1)) for k, n in enumerate(TOP_ROW) if n == pin]
    out += [("JT2", str(k + 1)) for k, n in enumerate(BOT_ROW) if n == pin]
    return out


def T(pin):
    return tpin(str(pin))[0]


def tx(k):
    return TX1 - 2.54 * (k - 1)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---- placement ---------------------------------------------------------------------------------------------------------
# (ref, footprint, (x, y), rot, value, mpn, note)
PARTS = []


def P(ref, fp, at, rot=0.0, value="", mpn="", note="", model=True, **kw):
    PARTS.append((ref, fp, at, rot, value, mpn, note, model, kw))


# power entry (top-left): XT30 male (the pack lead is the female side), pin 2 (round pad) +
P("J1", XT30M, (13.95, 35.8), 90, "XT30PW-M", "AMASS XT30PW-M", "battery in, 2S-6S; pin 2 +", model=False, overhang=True)
P("F1", NANO2, (20.0, 30.8), 0, "15A", "Littelfuse 0451015.MRL", "input fuse (65 V)")
P("Q1", D2PAK, (31.6, 30.8), 180, "SQM40031EL", "Vishay SQM40031EL-GE3", "reverse-polarity P-FET, 3 mOhm")
P("DZ1", "Diode_SMD:D_SOD-123", (43.1, 30.8), -90, "12V", "Nexperia BZT52C12", "Q1 gate clamp")
P("R1", R06, (43.5, 27.0), 0, "47k", "RC0603FR-0747KL", "Q1 gate pull-down")
P("D1", "Diode_SMD:D_SMB", (49.2, 28.0), 0, "SMBJ33A", "Littelfuse SMBJ33A", "input TVS")
P("RS1", "Resistor_SMD:R_2512_6332Metric", (48.4, 33.34), 0, "2m", "Bourns CSS2H-2512K-2L00F", "current shunt")
P("U1", "Package_SO:MSOP-10_3x3mm_P0.5mm", (48.4, 32.8), 0, "INA226", "TI INA226AIDGSR",
  "V / I monitor, I2C 0x40; under the shunt (bottom): Kelvin vias into each shunt pad's pour", side="B")
P("C1", C06, (44.0, 30.6), 90, "100n", "GRM188R71H104KA93D", "INA226 VS", side="B")
# 5 V buck (row along the top-left): TI TPS54360B datasheet design, 600 kHz
P("U2", HSOP8, (30.0, 44.0), 0, "TPS54360B", "TI TPS54360BDDAR", "5 V / 3.5 A buck",
  min_drill=0.3)                       # (its thermal vias at the board house's 0.3 mm minimum)
P("L1", XAL, (39.5, 45.5), 0, "6u8", "Coilcraft XAL6060-682MEB", "6.8 uH, 8.6 A sat")
P("D5", PDI5, (39.5, 39.5), 0, "PDS540", "Diodes PDS540-13", "5 V buck catch diode")
P("C2", C1210, (20.5, 44.0), 90, "4u7 50V", "GRM32ER71H475KA88L", "5 V buck input")
P("C3", C1210, (23.9, 44.0), 90, "4u7 50V", "GRM32ER71H475KA88L", "5 V buck input")
P("C4", C1210, (45.5, 45.5), 90, "47u 10V", "GRM32ER61A476ME20L", "5 V output")
P("C5", C1210, (48.9, 45.5), 90, "47u 10V", "GRM32ER61A476ME20L", "5 V output")
P("C6", C06, (30.0, 48.3), 0, "100n", "GRM188R71H104KA93D", "U2 boot")
for i, (ref, fp, val, mpn) in enumerate([
        ("R2", R06, "147k", "RC0603FR-07147KL"), ("R3", R06, "35k7", "RC0603FR-0735K7L"),     # EN: on at 6.0 V
        ("R4", R06, "162k", "RC0603FR-07162KL"), ("R5", R06, "53k6", "RC0603FR-0753K6L"),     # RT 600 kHz, FB top
        ("R6", R06, "10k2", "RC0603FR-0710K2L"), ("R7", R06, "13k", "RC0603FR-0713KL"),       # FB bottom, RCOMP
        ("C7", C06, "6n8", "GRM188R71H682KA01D"), ("C8", C06, "39p", "GRM1885C1H390JA01D")]):  # CCOMP, CCOMP2
    P(ref, fp, (18.6 + 3.1 * (i % 6), 38.3 if i < 6 else 40.2), 0, val, mpn)
# BEC inlet (bottom-left) and the 6 V servo buck (TPS54560B, 500 kHz)
P("J2", XT30M, (13.95, 20.0), 90, "XT30PW-M", "AMASS XT30PW-M", "servo BEC in (Mode B), up to 8.4 V; pin 2 +",
  model=False, overhang=True)
P("D3", "Diode_SMD:D_SMB", (20.6, 8.6), 0, "SMBJ10A", "Littelfuse SMBJ10A", "BEC inlet TVS")
P("Q2", D2PAK, (31.6, 15.0), 180, "SQM40031EL", "Vishay SQM40031EL-GE3", "BEC reverse-polarity P-FET")
P("R8", R06, (40.0, 8.4), 0, "10k", "RC0603FR-0710KL", "Q2 gate pull-down")
P("U3", HSOP8, (50.0, 21.0), 180, "TPS54560B", "TI TPS54560BDDAR", "6.0 V / 5 A servo buck (Mode A)",
  min_drill=0.3)                       # (its thermal vias at the board house's 0.3 mm minimum)
P("D6", PDI5, (46.5, 15.5), 180, "PDS540", "Diodes PDS540-13", "6 V buck catch diode")
P("L2", XAL, (47.5, 8.5), 0, "6u8", "Coilcraft XAL6060-682MEB", "6.8 uH; leave off for Mode B")
P("C9", C06, (52.3, 16.4), 90, "100n", "GRM188R71H104KA93D", "U3 boot")
P("C10", C1210, (55.9, 21.5), 90, "4u7 50V", "GRM32ER71H475KA88L", "6 V buck input")
P("C11", C1210, (55.9, 16.5), 90, "4u7 50V", "GRM32ER71H475KA88L", "6 V buck input")
for i, x in enumerate((53.0, 56.4)):
    P(f"C{12 + i}", C1210, (x, 8.5), 90, "47u 16V", "GRM32ER61C476ME15L", "6 V output")
P("C14", C1210, (54.0, 2.6), 0, "47u 16V", "GRM32ER61C476ME15L", "6 V output")
for i, (ref, val, mpn) in enumerate([
        ("R12", "66k5", "RC0603FR-0766K5L"), ("R13", "10k2", "RC0603FR-0710K2L"), ("R14", "18k7", "RC0603FR-0718K7L"),
        ("R9", "147k", "RC0603FR-07147KL"), ("R10", "35k7", "RC0603FR-0735K7L"), ("R11", "196k", "RC0603FR-07196KL")]):
    P(ref, R06, (44.0 + 3.0 * i, 24.6), 0, val, mpn)
P("C15", C06, (53.0, 12.5), 0, "5n6", "GRM188R71H562KA01D")
P("C16", C06, (56.1, 12.5), 0, "33p", "GRM1885C1H330JA01D")
# pyro supply and arming: F2 + D2 feed VPYRO; JA the external keyed arm switch; Q3 the ARM_EN high-side FET
P("F2", NANO2, (55.3, 39.3), 0, "7A", "Littelfuse 0451007.MRL", "pyro supply fuse")
P("D2", PDI5, (63.6, 36.7), 180, "PDS540", "Diodes PDS540-13", "pyro supply: the pyro bulk never feeds the logic")
P("J15", TERM, (57.0, 44.8), 180, "ARM SW", "Phoenix 1984617", "external keyed arm switch, in series")
P("C17", "Capacitor_SMD:CP_Elec_10x10.5", (73.0, 42.7), 90, "470u 35V", "Panasonic EEE-FK1V471P", "VPYRO bulk")
P("Q3", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (63.5, 45.5), 90, "SQ4401EY", "Vishay SQ4401EY-T1_GE3",
  "arm FET (high side)")
P("D7", LED06, (61.3, 40.4), 0, "red", "Kingbright APTD1608SURCK", "ARM: lit from VPYRO_ARMED itself")
P("R15", R12, (65.2, 40.4), 180, "4k7", "RC1206FR-074K7L", "ARM LED (6 mA at 26 V)")

# compute: the Teensy 4.1 on two 1x24 sockets (standard 8.5 mm female headers: with the module's own pin headers the
# Teensy sits ~11 mm up, so low parts (<= 6.5 mm) go under it), USB end toward the sensors
SOCK = "Connector_PinSocket_2.54mm:PinSocket_1x24_P2.54mm_Vertical"
P("JT1", SOCK, (TX1, ROW_T), -90, "Teensy 4.1 L", "Samtec SSW-124-01-T-S", "Teensy 4.1 GND,0-12,3.3V,24-32",
  model="Connector_PinSocket_2.54mm.3dshapes/PinSocket_1x24_P2.54mm_Vertical.step")   # (the Teensy drawn on it)
P("JT2", SOCK, (TX1, ROW_B), -90, "Teensy 4.1 R", "Samtec SSW-124-01-T-S", "Teensy 4.1 VIN,GND,3.3V,23-13,GND,41-33")
P("D9", "Diode_SMD:D_SMA", (116.5, 21.5), 0, "B140", "Diodes B140-13-F", "+5 V -> Teensy VIN (USB cannot back-feed +5 V)")
# pyro: four e-match terminals along the top edge, a low-side FET under each, its continuity LED beside it
for n in range(4):
    xp = 92.0 + 8.5 * n
    P(f"J{11 + n}", TERM, (xp, 44.8), 180, "E-MATCH", "Phoenix 1984617", f"pyro {n + 1}: pin 1 VPYRO_ARMED, pin 2 FET")
    P(f"QF{n + 1}", VSON, (xp - 1.75, 37.8), 90, "CSD17573Q5B", "TI CSD17573Q5B", f"pyro {n + 1} fire FET (30 V)",
      model="Package_TO_SOT_SMD.3dshapes/TDSON-8-1.step")                      # (same 5 x 6 package)
    P(f"DC{n + 1}", LED06, (xp + 2.5, 37.8), 90, "green", "Kingbright APTD1608LCGCK", f"pyro {n + 1} continuity")
    xc = xp - 1.75                 # (the channel's small parts on the bottom side, under its FET)
    P(f"RG{n + 1}", R06, (xc + 2.8, 35.8), 0, "100R", "RC0603FR-07100RL", "gate series", side="B")
    P(f"RD{n + 1}", R06, (xc + 2.8, 37.6), 0, "10k", "RC0603FR-0710KL", "gate pull-down: holds the FET off through reset",
      side="B")
    P(f"RA{n + 1}", R06, (xc - 1.75, 41.9), 90, "100k", "RC0603FR-07100KL", "continuity divider top", side="B")
    P(f"RB{n + 1}", R06, (xc - 1.75, 38.9), 90, "12k", "RC0603FR-0712KL", "continuity divider bottom", side="B")
    P(f"RL{n + 1}", R12, (xc + 0.6, 40.8), 90, "3k3", "RC1206FR-073K3L", "continuity LED", side="B")
# arming: Q4 pulls Q3's gate down when ARM_EN is high; BAT54 + 330R inject the continuity current from 3V3
# (bottom side, beside the arm FET, between the VPYRO and VPYRO_ARMED copper)
P("Q4", SOT23, (73.5, 36.8), 0, "2N7002", "Nexperia 2N7002,215", "ARM_EN -> arm FET gate", side="B")
P("R16", R06, (77.0, 36.0), 0, "100k", "RC0603FR-07100KL", "ARM_EN pull-down", side="B")
P("R17", R06, (72.2, 39.8), 0, "10k", "RC0603FR-0710KL", "arm FET gate drive", side="B")
P("R18", R06, (72.2, 44.6), 0, "100k", "RC0603FR-07100KL", "arm FET gate pull-up (off)", side="B")
P("DZ2", "Diode_SMD:D_SOD-123", (72.8, 42.2), 0, "12V", "Nexperia BZT52C12", "arm FET gate clamp", side="B")
P("D8", "Diode_SMD:D_SOD-323", (77.0, 44.6), 0, "BAT54", "Diodes BAT54WS-7-F", "continuity injection (blocks VPYRO)",
  side="B")
P("R19", R06, (77.0, 42.4), 0, "330R", "RC0603FR-07330RL", "continuity injection", side="B")
# servo outputs: 74HCT244 on +5 V under the module, 100R per channel, the 3 x 6 block on the bottom edge
P("U14", "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm", (80.0, 23.0), 0, "74HCT244", "Nexperia 74HCT244PW,118",
  "servo buffer, 3.3 V in -> 5 V out")
for i in range(6):       # (pull-downs beside the buffer pins they hold: 1A0-1A3 on its left, 2A0 / 2A1 on its right)
    P(f"RP{i + 1}", R06, (71.5, 26.0 - 1.8 * i) if i < 4 else (86.4, 24.2 - 1.8 * (i - 4)), 180 if i < 4 else 0, "100k",
      "RC0603FR-07100KL", "servo input pull-down (reset)")
    P(f"RO{i + 1}", R06, (61.6 + 3.1 * i, 13.9), 0, "100R", "RC0603FR-07100RL", f"servo {i + 1} series")
P("C23", C06, (85.6, 26.0), 0, "100n", "GRM188R71H104KA93D", "U14")
P("J3", "Prometheus_Connector:PinHeader_3x06_P2.54mm_Vertical", (62.484, 10.414), 0, "SERVO 1-6", "3 x 6 2.54 mm header",
  "servo outputs: signal / VSERVO / GND per column", model=False)
P("C18", "Capacitor_SMD:CP_Elec_8x10", (31.0, 4.9), 0, "220u 25V", "Panasonic EEH-ZA1E221P", "VSERVO bulk")
P("C19", "Capacitor_SMD:CP_Elec_8x10", (91.0, 5.9), 90, "220u 25V", "Panasonic EEH-ZA1E221P", "VSERVO bulk")
P("R25", R06, (80.0, 11.5), 90, "100k", "RC0603FR-07100KL", "VSERVO sense top")
P("R26", R06, (81.6, 11.5), 90, "27k", "RC0603FR-0727KL", "VSERVO sense bottom")
# buzzer (magnetic: kept 30 mm from the magnetometer), its driver, the expansion header
P("BZ1", "Buzzer_Beeper:MagneticBuzzer_CUI_CMT-8504-100-SMT", (101.0, 5.6), 0, "85dB", "CUI CMT-8504-100-SMT-TR",
  "buzzer, pad 1 +", model=False)
P("Q5", SOT23, (110.0, 9.3), 0, "2N7002", "Nexperia 2N7002,215", "buzzer driver")
P("D10", "Diode_SMD:D_SOD-323", (114.0, 10.3), 0, "1N4148WS", "Diodes 1N4148WS-7-F", "buzzer flyback")
P("R20", R06, (106.9, 9.8), 90, "100k", "RC0603FR-07100KL", "buzzer gate pull-down")
P("J9", "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", (108.712, 2.286), 90, "EXPANSION",
  "2x5 2.54 mm header", "5V 3V3 GND GND SDA SCL SCK1 MOSI1 MISO1 CS")
# inertial island: ICM-42688-P at the sensor datum, BMI088 and ADXL375 beside it, magnetometer, the baro pair
P("U5", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", DATUM, 0, "ICM-42688-P", "TDK ICM-42688-P",
  "primary IMU (SPI0); its centre is the sensor datum")
P("U6", "Package_LGA:Bosch_LGA-16_4.5x3mm_P0.5mm_LayoutBorder7x1y_ClockwisePinNumbering", (130.0, 33.0), 0, "BMI088",
  "Bosch BMI088", "redundant IMU (SPI0)")
P("U7", "Package_LGA:LGA-14_3x5mm_P0.8mm_LayoutBorder1x6y", (124.6, 25.0), 0, "ADXL375", "ADI ADXL375BCCZ",
  "+-200 g accelerometer (SPI0)")
P("U10", "Package_LGA:LGA-16_3x3mm_P0.5mm", (135.8, 25.0), 0, "MMC5983MA", "MEMSIC MMC5983MA", "magnetometer (I2C)")
P("U8", "Prometheus_Sensor:Bosch_BMP390_LGA-10_2x2mm_P0.5mm", (126.0, 38.0), 0, "BMP390", "Bosch BMP390",
  "barometer 1 (I2C 0x77)", model=False)
P("U9", "Prometheus_Sensor:TE_MS5607-02BA03_QFN-8_5x3mm_P1.25mm", (133.5, 38.0), 90, "MS5607", "TE MS560702BA03-50",
  "barometer 2 (I2C 0x76)", model=False)
for ref, fp, at, rot, val, mpn in [
        ("C20", C06, (127.5, 21.0), 0, "100n", "GRM188R71H104KA93D"),
        ("C21", C06, (130.6, 21.0), 0, "2u2", "GRM188R61A225KE34D"),
        ("C22", C06, (133.7, 21.0), 0, "10n", "GRM188R71H103KA01D"),
        ("C24", C06, (129.5, 36.4), 90, "100n", "GRM188R71H104KA93D"),
        ("C25", C06, (127.0, 30.3), 0, "100n", "GRM188R71H104KA93D"),
        ("C26", C06, (123.4, 20.75), 90, "1u", "GRM188R71A105KA61D"),
        ("C27", C06, (125.1, 20.75), 90, "100n", "GRM188R71H104KA93D"),
        ("C28", C06, (133.3, 27.6), 90, "1u", "GRM188R71A105KA61D"),
        ("C29", C06, (139.25, 24.75), 0, "10u", "GRM188R61A106KE69D"),
        ("C30", C06, (126.0, 41.0), 0, "100n", "GRM188R71H104KA93D"),
        ("C31", C06, (130.0, 41.0), 0, "100n", "GRM188R71H104KA93D"),
        ("R22", R06, (138.8, 21.5), 90, "2k2", "RC0603FR-072K2L"),
        ("R23", R06, (138.8, 17.0), 90, "2k2", "RC0603FR-072K2L"),
]:
    P(ref, fp, at, rot, val, mpn)
# analog 3.3 V: ferrite + its own LDO, at the island
P("FB1", "Inductor_SMD:L_0603_1608Metric", (129.5, 15.8), 0, "220R", "Murata BLM18PG221SN1D", "+5 V -> 3V3A filter")
P("C32", C06, (129.5, 13.9), 0, "1u", "GRM188R61E105KA12D", "U4 input")
P("U4", SOT235, (125.0, 15.0), 0, "TPS7A2033", "TI TPS7A2033PDBVR", "3V3A: IMUs, baros, magnetometer")
P("C33", C06, (125.0, 18.5), 0, "2u2", "GRM188R61A225KE34D", "U4 output")
P("C34", C08, (128.8, 18.5), 0, "10u", "GRM21BR61A106KE19L", "3V3A bulk")
# CAN-FD: TCAN332G on CAN3 (the Teensy's only FD port), 120R terminator behind a solder jumper
P("U13", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (126.5, 10.2), 0, "TCAN332G", "TI TCAN332GDR", "CAN-FD transceiver")
P("C35", C06, (136.0, 11.0), 0, "100n", "GRM188R71H104KA93D", "U13")
P("R21", R06, (132.2, 11.6), 0, "120R", "RC0603FR-07120RL", "CAN termination")
P("JP1", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm", (132.6, 8.6), 0, "TERM", "", "bridge to terminate",
  model=False)
# I-O connectors (JST-GH, Pixhawk pin order), RC power jumper, LoRa, LEDs, button
P("J4", GH4, (127.25, 3.6), 0, "CAN", "JST BM04B-GHS-TBT", "CAN: 5V CANH CANL GND")
P("J5", "Connector_JST:JST_SH_BM04B-SRSS-TB_1x04-1MP_P1.00mm_Vertical", (136.4, 3.0), 0, "QWIIC", "JST BM04B-SRSS-TB",
  "Qwiic: GND 3V3 SDA SCL")
P("J6", GH4, (127.25, 46.5), 180, "RC IN", "JST BM04B-GHS-TBT", "RC: V RX TX GND (SBUS / CRSF / PPM)")
P("JP2", "Jumper:SolderJumper-3_P1.3mm_Open_RoundedPad1.0x1.5mm", (127.25, 41.8), 0, "RC 5V|3V3", "",
  "RC power: bridge 1-2 for 5 V, 2-3 for 3.3 V (bottom side)", model=False, side="B")
P("J7", GH6, (147.0, 46.5), 180, "TELEM1", "JST BM06B-GHS-TBT", "TELEM1: 5V TX RX CTS RTS GND (RFD900x)")
P("C36", "Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D", (147.0, 39.5), 0, "220u 10V", "KEMET T520D227M010ATE015",
  "TELEM1 bulk: the radio's 1 A transmit bursts")
P("J8", GH6, (147.0, 3.6), 0, "GPS1", "JST BM06B-GHS-TBT", "GPS1: 5V TX RX SCL SDA GND")
P("U11", "RF_Module:HOPERF_RFM9XW_SMD", (150.0, 25.0), 0, "RFM95W", "HopeRF RFM95W-915S2", "LoRa 915 MHz beacon (SPI1)",
  model=False)
P("J10", "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical", (162.3, 18.0), 0, "u.FL", "Hirose U.FL-R-SMT-1(10)",
  "LoRa antenna")
P("U12", SOT235, (145.0, 11.5), 0, "TPS7A2033", "TI TPS7A2033PDBVR", "3V3R: LoRa module")
P("C37", C06, (145.0, 14.6), 0, "1u", "GRM188R61E105KA12D", "U12 input")
P("C38", C08, (149.5, 11.5), 90, "10u", "GRM21BR61A106KE19L", "3V3R")
P("C39", C06, (152.5, 11.5), 90, "100n", "GRM188R71H104KA93D", "RFM95W")
for i in range(3):
    P(f"LED{i + 1}", "LED_SMD:LED_WS2812B-2020_PLCC4_2.0x2.0mm", (162.5, 23.0 + 3.0 * i), 0, "WS2812B-2020",
      "Worldsemi WS2812B-2020", ["STATUS", "SD", "FAULT"][i], model=False)
    P(f"C{40 + i}", C06, (160.0, 23.0 + 3.0 * i), 90, "100n", "GRM188R71H104KA93D", f"LED{i + 1}")
P("D11", LED06, (162.5, 32.0), 90, "green", "Kingbright APTD1608LCGCK", "PWR (3V3)")
P("R27", R06, (160.0, 32.0), 90, "1k", "RC0603FR-071KL", "PWR LED")
P("SW1", "Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92", (156.5, 37.0), 0, "BTN", "C&K PTS645SM43SMTR92 LFS",
  "self-test / calibrate / log dump")
P("R24", R06, (154.0, 42.0), 0, "10k", "RC0603FR-0710KL", "BTN pull-up")
# test points: bank A (rails) bottom-left + by the sensors, bank B (pyro) along the bottom edge, bank C (digital)
TPS = {  # ref: (net, at, loop)
    "TP1": ("VBAT_RAW", (11.0, 2.5)), "TP2": ("VIN_P", (13.8, 2.5)), "TP9": ("VIN", (16.6, 2.5)),
    "TP4": ("+5V", (19.4, 2.5)), "TP3": ("VSERVO", (22.2, 2.5)),
    "TP10": ("GND", (12.5, 46.5)), "TP11": ("GND", (12.0, 7.5)),
    "TP7": ("VPYRO", (87.5, 14.0)), "TP8": ("VPYRO_ARMED", (90.3, 14.0)),
    "TP12": ("GATE1", (93.1, 14.0)), "TP13": ("GATE2", (95.9, 14.0)), "TP14": ("GATE3", (98.7, 14.0)),
    "TP15": ("GATE4", (101.5, 14.0)), "TP16": ("CONT1", (104.3, 14.0)), "TP17": ("CONT2", (107.1, 14.0)),
    "TP18": ("CONT3", (109.9, 14.0)), "TP19": ("CONT4", (112.7, 14.0)), "TP20": ("ARM_EN", (115.5, 14.0)),
    "TP21": ("GND", (118.5, 9.0)),
    "TP22": ("SCK", (133.7, 42.0)), "TP23": ("MOSI", (136.5, 42.0)), "TP24": ("MISO", (139.3, 42.0)),
    "TP25": ("SDA", (133.7, 44.8)), "TP26": ("SCL", (136.5, 44.8)), "TP27": ("IMU_INT", (139.3, 44.8)),
    "TP28": ("VIN_T", (133.7, 47.6)), "TP5": ("3V3", (136.5, 47.6)), "TP6": ("3V3A", (139.3, 47.6)),
    "TP29": ("GND", (135.0, 16.5)),
}
for ref, (net, at) in TPS.items():
    loop = net == "GND"
    P(ref, TPL if loop else TP, at, 0, net, "Keystone 5005" if loop else "", "scope ground loop" if loop else "",
      model=loop)



BODIES = {  # parts KiCad has no 3D model of: drawn (pcb_bodies)
    "J1": lambda: bodies.xt30pw_body(True), "J2": lambda: bodies.xt30pw_body(True), "JT1": bodies.teensy41_body,
    "U11": bodies.rfm95w_body, "BZ1": bodies.buzzer_body, "U8": bodies.bmp390_body, "U9": bodies.ms5607_body,
    "J3": bodies.servo_block_body, **{f"LED{i + 1}": bodies.ws2812_2020_body for i in range(3)}}


def place_all(b, solids=True):
    import importlib.util
    if solids and importlib.util.find_spec("build123d") is None:     # (the drawn bodies are looks only: the board
        print("build123d not installed: parts without a KiCad 3D model show as pads only", file=sys.stderr)   # runs)
        solids = False
    for ref, fp, at, rot, value, mpn, note, model, kw in PARTS:
        body = BODIES[ref]() if solids and ref in BODIES else None
        mdl = model if isinstance(model, str) else (m3d(fp) if model else None)
        b.place(ref, fp, at, rot, value, mpn, model=mdl, note=note, body=body, **kw)


# ---- nets --------------------------------------------------------------------------------------------------------------
SERVO_PINS = [2, 3, 4, 5, 6, 9]                       # Teensy pins (FlexPWM)
BUF_IN = ["2", "4", "6", "8", "17", "15"]             # 74HCT244 1A0-1A3, 2A0, 2A1
BUF_OUT = ["18", "16", "14", "12", "3", "5"]          # 1Y0-1Y3, 2Y0, 2Y1
FIRE_PINS, CONT_PINS = [28, 29, 32, 33], [24, 25, 20, 21]


def fc_nets(b):
    N = b.net
    gnd = tpin("GND") + [("J1", "1"), ("J2", "1"), ("R1", "2"), ("D1", "2"), ("U1", "1"), ("U1", "2"), ("U1", "7"),
                         ("C1", "2"), ("C2", "1"), ("C3", "1"), ("U2", "7"), ("U2", "9"), ("R3", "2"), ("R4", "2"),
                         ("R6", "2"), ("C7", "2"), ("C8", "2"), ("D5", "2"), ("C4", "1"), ("C5", "1"),
                         ("D3", "2"), ("R8", "2"), ("U3", "7"), ("U3", "9"), ("R10", "2"), ("R11", "2"), ("R13", "2"),
                         ("C15", "2"), ("C16", "2"), ("D6", "2"), ("C10", "2"), ("C11", "1"), ("C12", "2"), ("C13", "2"),
                         ("C14", "2"), ("C18", "2"), ("C19", "2"), ("R26", "2"), ("C17", "2"), ("D7", "1"),
                         ("Q4", "2"), ("R16", "2"), ("U14", "10"), ("U14", "1"), ("U14", "19"), ("U14", "11"),
                         ("U14", "13"), ("C23", "2"), ("R20", "2"), ("Q5", "2"),
                         ("U5", "6"), ("U5", "7"), ("U5", "9"), ("U6", "2"), ("U6", "4"), ("U6", "6"), ("U6", "7"),
                         ("U7", "2"), ("U7", "4"), ("U7", "5"), ("U7", "11"), ("U8", "3"), ("U8", "8"), ("U8", "9"),
                         ("U9", "3"), ("U10", "9"), ("U10", "11"), ("U4", "2"), ("U12", "2"), ("U13", "2"),
                         ("U11", "1"), ("U11", "8"), ("U11", "10"), ("J10", "2"), ("J4", "4"), ("J5", "1"), ("J6", "4"),
                         ("J7", "6"), ("J8", "6"), ("J9", "3"), ("J9", "4"), ("SW1", "2"), ("D11", "1")] + \
        [(f"C{i}", "2") for i in (20, 21, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
                                   42)] + \
        [("J3", str(3 * i + 3)) for i in range(6)] + [(f"RP{i + 1}", "2") for i in range(6)] + \
        [(f"QF{n + 1}", "1") for n in range(4)] + [(f"RD{n + 1}", "2") for n in range(4)] + \
        [(f"RB{n + 1}", "2") for n in range(4)] + [(f"DC{n + 1}", "1") for n in range(4)] + \
        [(f"LED{i + 1}", "2") for i in range(3)] + [(r, "1") for r, (n, _) in TPS.items() if n == "GND"]
    N("GND", gnd, amps=15.0, volts=0.0)
    # input and protection
    N("VBAT_RAW", [("J1", "2"), ("F1", "1"), ("TP1", "1")], amps=15.0, volts=26.0)
    N("VBAT_F", [("F1", "2"), ("Q1", "2")], amps=15.0, volts=26.0)
    N("VIN_P", [("Q1", "3"), ("RS1", "1"), ("D1", "1"), ("DZ1", "1"), ("U1", "10"), ("TP2", "1")], amps=15.0, volts=26.0)
    N("Q1_G", [("Q1", "1"), ("DZ1", "2"), ("R1", "1")], amps=0.01, volts=26.0)
    N("VIN", [("RS1", "2"), ("U1", "9"), ("U1", "8"), ("C2", "2"), ("C3", "2"), ("U2", "2"), ("R2", "1"), ("C10", "1"),
              ("C11", "2"), ("U3", "2"), ("R9", "1"), ("F2", "1"), ("TP9", "1")], amps=15.0, volts=26.0)
    # 5 V buck
    N("BOOT5", [("U2", "1"), ("C6", "1")], amps=0.1, volts=30.0)
    N("SW5", [("U2", "8"), ("L1", "1"), ("D5", "1"), ("C6", "2")], amps=3.5, volts=26.0)
    N("EN5", [("U2", "3"), ("R2", "2"), ("R3", "1")], amps=0.01, volts=2.0)
    N("RT5", [("U2", "4"), ("R4", "1")], amps=0.01, volts=1.0)
    N("FB5", [("U2", "5"), ("R5", "2"), ("R6", "1")], amps=0.01, volts=1.0)
    N("COMP5", [("U2", "6"), ("R7", "1"), ("C8", "1")], amps=0.01, volts=2.0)
    N("CMP5", [("R7", "2"), ("C7", "1")], amps=0.01, volts=2.0)
    p5 = [("L1", "2"), ("C4", "2"), ("C5", "2"), ("R5", "1"), ("C23", "1"), ("U14", "20"), ("D9", "2"), ("FB1", "1"),
          ("U12", "1"), ("U12", "3"), ("C37", "1"), ("J4", "1"), ("J7", "1"), ("C36", "1"), ("J8", "1"), ("J9", "1"),
          ("JP2", "1"), ("BZ1", "1"), ("D10", "1"), ("TP4", "1")] + [(f"LED{i + 1}", "4") for i in range(3)] + \
        [(f"C{40 + i}", "1") for i in range(3)]
    N("+5V", p5, amps=3.5, volts=5.0)
    # 6 V servo buck, BEC inlet, VSERVO
    N("BOOT6", [("U3", "1"), ("C9", "1")], amps=0.1, volts=30.0)
    N("SW6", [("U3", "8"), ("L2", "1"), ("D6", "1"), ("C9", "2")], amps=5.5, volts=26.0)
    N("EN6", [("U3", "3"), ("R9", "2"), ("R10", "1")], amps=0.01, volts=2.0)
    N("RT6", [("U3", "4"), ("R11", "1")], amps=0.01, volts=1.0)
    N("FB6", [("U3", "5"), ("R12", "2"), ("R13", "1")], amps=0.01, volts=1.0)
    N("COMP6", [("U3", "6"), ("R14", "1"), ("C16", "1")], amps=0.01, volts=2.0)
    N("CMP6", [("R14", "2"), ("C15", "1")], amps=0.01, volts=2.0)
    N("VBEC", [("J2", "2"), ("D3", "1"), ("Q2", "2")], amps=12.0, volts=8.4)
    N("Q2_G", [("Q2", "1"), ("R8", "1")], amps=0.01, volts=8.4)
    N("VSERVO", [("L2", "2"), ("C12", "1"), ("C13", "1"), ("C14", "1"), ("Q2", "3"), ("R12", "1"), ("C18", "1"),
                 ("C19", "1"), ("R25", "1"), ("TP3", "1")] + [("J3", str(3 * i + 2)) for i in range(6)],
          amps=12.0, volts=6.0)
    N("VS_SENSE", [("R25", "2"), ("R26", "1"), T(40)], amps=0.01, volts=1.5)
    # servo signal chain
    for i, pin in enumerate(SERVO_PINS):
        N(f"SV{i + 1}", [T(pin), (f"RP{i + 1}", "1"), ("U14", BUF_IN[i])], amps=0.02, volts=3.3)
        N(f"SB{i + 1}", [("U14", BUF_OUT[i]), (f"RO{i + 1}", "1")], amps=0.05, volts=5.0)
        N(f"SO{i + 1}", [(f"RO{i + 1}", "2"), ("J3", str(3 * i + 1))], amps=0.05, volts=5.0)
    # pyro supply, arming
    N("PYRO_F", [("F2", "2"), ("D2", "2")], amps=7.0, volts=26.0)
    N("VPYRO", [("D2", "1"), ("C17", "1"), ("J15", "2"), ("TP7", "1")], amps=7.0, volts=26.0)
    N("VPYRO_SW", [("J15", "1"), ("Q3", "1"), ("Q3", "2"), ("Q3", "3"), ("R18", "1"), ("DZ2", "1")], amps=7.0, volts=26.0)
    N("ARM_G", [("Q3", "4"), ("R18", "2"), ("DZ2", "2"), ("R17", "1")], amps=0.01, volts=26.0)
    N("ARM_PD", [("R17", "2"), ("Q4", "3")], amps=0.01, volts=26.0)
    N("ARM_EN", [T(15), ("Q4", "1"), ("R16", "1"), ("TP20", "1")], amps=0.01, volts=3.3)
    N("VPYRO_ARMED", [("Q3", str(k)) for k in (5, 6, 7, 8)] + [(f"J{11 + n}", "1") for n in range(4)] +
      [("R15", "1"), ("R19", "2"), ("TP8", "1")], amps=7.0, volts=26.0)
    N("ARM_LED", [("R15", "2"), ("D7", "2")], amps=0.01, volts=26.0)
    N("INJ", [("D8", "1"), ("R19", "1")], amps=0.01, volts=3.3)
    for n in range(4):
        N(f"PYRO{n + 1}", [(f"J{11 + n}", "2"), (f"QF{n + 1}", "3"), (f"RL{n + 1}", "1"), (f"RA{n + 1}", "1")],
          amps=7.0, volts=26.0)
        N(f"GATE{n + 1}", [(f"QF{n + 1}", "2"), (f"RG{n + 1}", "2"), (f"RD{n + 1}", "1"), (f"TP{12 + n}", "1")],
          amps=0.02, volts=3.3)
        N(f"FIRE{n + 1}", [T(FIRE_PINS[n]), (f"RG{n + 1}", "1")], amps=0.02, volts=3.3)
        N(f"CONT{n + 1}", [(f"RA{n + 1}", "2"), (f"RB{n + 1}", "1"), T(CONT_PINS[n]), (f"TP{16 + n}", "1")],
          amps=0.01, volts=3.3)
        N(f"CL{n + 1}", [(f"RL{n + 1}", "2"), (f"DC{n + 1}", "2")], amps=0.01, volts=26.0)
    # Teensy supply, 3V3 (the module's regulator), the quiet 3V3A
    N("VIN_T", [tpin("VIN")[0], ("D9", "1"), ("TP28", "1")], amps=0.5, volts=5.0)
    N("3V3", tpin("3V3") + [("U1", "6"), ("C1", "1"), ("R22", "1"), ("R23", "1"), ("U13", "3"), ("C35", "1"),
                            ("J5", "2"), ("J9", "2"), ("JP2", "3"), ("R24", "1"), ("R27", "1"), ("D8", "2"), ("TP5", "1")],
      amps=0.25, volts=3.3)
    N("5VA", [("FB1", "2"), ("C32", "1"), ("U4", "1"), ("U4", "3")], amps=0.1, volts=5.0)
    N("3V3A", [("U4", "5"), ("C33", "1"), ("C34", "1"), ("U5", "5"), ("U5", "8"), ("C20", "1"), ("C21", "1"),
               ("C22", "1"), ("U6", "3"), ("U6", "11"), ("C24", "1"), ("C25", "1"), ("U7", "1"), ("U7", "3"),
               ("U7", "6"), ("C26", "1"), ("C27", "1"), ("U8", "1"), ("U8", "5"), ("U8", "6"), ("U8", "10"),
               ("C30", "1"), ("U9", "1"), ("U9", "2"), ("U9", "4"), ("U9", "5"), ("C31", "1"), ("U10", "2"),
               ("U10", "4"), ("U10", "13"), ("C28", "1"), ("TP6", "1")], amps=0.05, volts=3.3)
    N("MAG_CAP", [("U10", "10"), ("C29", "1")], amps=0.05, volts=3.3)
    # SPI0 (sensors), I2C (baros, magnetometer, INA226, GPS, Qwiic, expansion)
    N("SCK", [T(13), ("U5", "13"), ("U6", "8"), ("U7", "14"), ("TP22", "1")], amps=0.01, volts=3.3)
    N("MOSI", [T(11), ("U5", "14"), ("U6", "9"), ("U7", "13"), ("TP23", "1")], amps=0.01, volts=3.3)
    N("MISO", [T(12), ("U5", "1"), ("U6", "10"), ("U6", "15"), ("U7", "12"), ("TP24", "1")], amps=0.01, volts=3.3)
    N("CS_ICM", [T(10), ("U5", "12")], amps=0.01, volts=3.3)
    N("CS_BMIA", [T(22), ("U6", "14")], amps=0.01, volts=3.3)
    N("CS_BMIG", [T(23), ("U6", "5")], amps=0.01, volts=3.3)
    N("CS_ADXL", [T(41), ("U7", "7")], amps=0.01, volts=3.3)
    N("IMU_INT", [T(16), ("U5", "4"), ("TP27", "1")], amps=0.01, volts=3.3)
    N("SDA", [T(18), ("U1", "4"), ("U8", "4"), ("U9", "7"), ("U10", "16"), ("J8", "5"), ("J5", "3"), ("J9", "5"),
              ("R22", "2"), ("TP25", "1")], amps=0.01, volts=3.3)
    N("SCL", [T(19), ("U1", "5"), ("U8", "2"), ("U9", "8"), ("U10", "1"), ("J8", "4"), ("J5", "4"), ("J9", "6"),
              ("R23", "2"), ("TP26", "1")], amps=0.01, volts=3.3)
    # SPI1 (LoRa, expansion), LoRa supply and antenna
    N("MOSI1", [T(26), ("U11", "3"), ("J9", "8")], amps=0.01, volts=3.3)
    N("SCK1", [T(27), ("U11", "4"), ("J9", "7")], amps=0.01, volts=3.3)
    N("MISO1", [T(39), ("U11", "2"), ("J9", "9")], amps=0.01, volts=3.3)
    N("CS_LORA", [T(38), ("U11", "5")], amps=0.01, volts=3.3)
    N("EXP_CS", [T(14), ("J9", "10")], amps=0.01, volts=3.3)
    N("3V3R", [("U12", "5"), ("C38", "1"), ("C39", "1"), ("U11", "13")], amps=0.15, volts=3.3)
    N("ANT", [("U11", "9"), ("J10", "1")], amps=0.1, volts=3.3)
    # CAN-FD, RC, telemetry, GPS
    N("CAN_TX", [T(31), ("U13", "1")], amps=0.01, volts=3.3)
    N("CAN_RX", [T(30), ("U13", "4")], amps=0.01, volts=3.3)
    N("CANH", [("U13", "7"), ("J4", "2"), ("JP1", "1")], amps=0.05, volts=3.3)
    N("CANL", [("U13", "6"), ("J4", "3"), ("R21", "2")], amps=0.05, volts=3.3)
    N("CAN_T", [("JP1", "2"), ("R21", "1")], amps=0.05, volts=3.3)
    N("RC_RX", [T(34), ("J6", "2")], amps=0.01, volts=3.3)
    N("RC_TX", [T(35), ("J6", "3")], amps=0.01, volts=3.3)
    N("VRC", [("J6", "1"), ("JP2", "2")], amps=0.3, volts=5.0)
    N("TEL_TX", [T(1), ("J7", "2")], amps=0.01, volts=3.3)
    N("TEL_RX", [T(0), ("J7", "3")], amps=0.01, volts=3.3)
    N("GPS_TX", [T(8), ("J8", "2")], amps=0.01, volts=3.3)
    N("GPS_RX", [T(7), ("J8", "3")], amps=0.01, volts=3.3)
    # UI
    N("BTN", [T(17), ("SW1", "1"), ("R24", "2")], amps=0.01, volts=3.3)
    N("BUZ", [T(36), ("Q5", "1"), ("R20", "1")], amps=0.01, volts=3.3)
    N("BZ_N", [("Q5", "3"), ("BZ1", "2"), ("D10", "2")], amps=0.1, volts=5.0)
    N("LED_DIN", [T(37), ("LED1", "3")], amps=0.01, volts=5.0)
    N("LED_D2", [("LED1", "1"), ("LED2", "3")], amps=0.01, volts=5.0)
    N("LED_D3", [("LED2", "1"), ("LED3", "3")], amps=0.01, volts=5.0)
    N("PWR_A", [("R27", "2"), ("D11", "2")], amps=0.01, volts=3.3)


# ---- copper: power pours, planes, ground fills -------------------------------------------------------------------------
def xps():
    return [92.0 + 8.5 * n for n in range(4)]


I_IN = 10.0     # input path, continuous: Mode A servos (5 A at 6 V) + the 5 V rail (3.5 A) draw ~7.5 A from a 2S pack
I_FIRE = 5.0    # a firing pulse (the concept's 5 A, 1 s); F2 (7 A) bounds it


def power_pours():
    """[(net, layer, polygon(s), amps)]: the current-carrying copper, each sized for what it carries (IPC-2221,
    <= 20 C rise on 1 oz outer copper)"""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    U = lambda *rs: unary_union([Polygon(r) for r in rs])
    P_ = [
        ("VBAT_RAW", "F", U(rect(11.4, 27.4, 18.9, 33.3)), I_IN),
        ("VBAT_F", "F", U(rect(20.6, 25.4, 35.2, 36.2)), I_IN),
        ("VIN_P", "F", U(rect(36.6, 31.4, 46.8, 36.6), rect(45.6, 26.6, 48.6, 31.4)), I_IN),
        ("VIN", "F", U(rect(49.8, 31.7, 56.2, 41.2)), I_IN),
        ("VIN", "F", U(rect(53.6, 16.9, 59.0, 21.1)), 6.0),
        ("VIN", "F", U(rect(52.0, 19.95, 53.7, 20.75)), 0.5),     # the last 1.6 mm into U3's VIN pin (the pin itself
                                                                    # is narrower): a pin entry, not a current path
        ("SW5", "F", U(rect(32.0, 45.3, 38.3, 47.3), rect(35.6, 37.6, 41.0, 45.3)), 3.5),
        ("+5V", "F", U(rect(41.2, 44.9, 50.6, 49.6)), 3.5),
        ("SW6", "F", U(rect(44.6, 5.0, 47.4, 11.5), rect(44.6, 11.5, 50.3, 19.5)), 5.5),
        ("VSERVO", "F", U(rect(48.4, 5.6, 59.4, 7.95), rect(48.4, 0.6, 52.3, 5.6)), 5.0),
        ("VSERVO", "F", U(rect(36.6, 16.2, 42.3, 19.2)), 6.0),                    # (half: the B pour under it)
        ("VSERVO", "F", U(rect(24.4, 1.0, 30.6, 9.0)), 2.0),
        ("VSERVO", "F", U(rect(87.4, 0.6, 94.6, 5.6)), 2.0),
        ("VSERVO", "B", U(rect(25.0, 0.6, 78.4, 12.0), rect(36.0, 12.0, 42.6, 19.4)), 12.0),
        ("VSERVO", "B", U(rect(78.4, 0.6, 94.6, 2.3), rect(87.4, 0.6, 94.6, 5.6)), 1.0),
        ("VBEC", "F", U(rect(11.4, 12.4, 35.2, 17.9), rect(16.9, 6.9, 20.2, 12.4)), 6.0),    # top + bottom in
        ("VBEC", "B", U(rect(11.4, 12.4, 25.0, 17.9)), 6.0),                                   # parallel: 12 A
        ("PYRO_F", "F", U(rect(56.7, 35.3, 61.7, 39.5)), I_FIRE),
        ("VPYRO", "F", U(rect(62.1, 34.8, 75.3, 39.2)), I_FIRE),
        ("VPYRO", "B", U(rect(51.8, 34.8, 55.2, 46.3), rect(51.8, 34.8, 70.0, 42.6)), I_FIRE),
        ("VPYRO_SW", "F", U(rect(55.6, 41.6, 64.6, 46.4)), I_FIRE),
        ("VPYRO_ARMED", "F", U(rect(59.2, 46.8, 70.5, 49.6)), I_FIRE),
        ("VPYRO_ARMED", "B", U(rect(60.6, 46.8, 121.0, 49.6), *[rect(x - 1.5, 43.3, x + 1.5, 46.8) for x in xps()]),
         I_FIRE),
    ]
    for n, xp in enumerate(xps()):
        xf = xp - 1.75
        P_.append((f"PYRO{n + 1}", "F", U(rect(xf - 2.6, 35.75, xf + 2.6, 41.0), rect(xf - 3.25, 41.0, xf - 0.25, 46.4)),
                   I_FIRE))
        P_.append(("GND", "F", U(rect(xf - 2.6, 33.75, xf + 1.2, 35.45)), I_FIRE))       # the fire FET's source
    return P_


IN2_VIN = [(16.0, 0.6), (59.3, 0.6), (59.3, 41.0), (35.4, 41.0), (35.4, 49.4), (16.0, 49.4)]
IN2_5V = [(35.8, 41.4), (59.7, 41.4), (59.7, 0.6), (164.4, 0.6), (164.4, 49.4), (35.8, 49.4)]


def fc_pours(b):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    pp = power_pours()
    for net, L, g, amps in pp:
        for q in (getattr(g, "geoms", None) or [g]):
            b.pour(net, L, list(q.exterior.coords)[:-1], amps=amps)
    b.pour("GND", "1", rect(0, 0, W, H), amps=0.5, fill=True)       # In1: the ground plane, whole board (it spreads
                                                                     # the return currents; the large ones have their
                                                                     # own copper above)
    b.pour("VIN", "2", IN2_VIN, amps=6.0)                            # In2: VIN at the power end (feeds the bucks)
    b.pour("+5V", "2", IN2_5V, amps=3.5)                             # In2: +5 V everywhere else
    for L in "FB":                                                   # outer ground fills round the power copper
        other = unary_union([g.buffer(0.5, join_style=2) for net, l, g, _ in pp if l == L and net != "GND"])
        fill = Polygon(rect(0, 0, W, H)).difference(other)
        for q in (getattr(fill, "geoms", None) or [fill]):
            if q.area > 4.0:
                b.pour("GND", L, list(q.exterior.coords)[:-1], amps=0.5, fill=True,
                       holes=[list(r.coords)[:-1] for r in q.interiors])


# ---- routing -----------------------------------------------------------------------------------------------------------
LOCAL = [  # (net, width): short local nets first, while their neighbourhood is free
    ("3V3A", 0.3), ("MAG_CAP", 0.3),
    ("BOOT5", 0.3), ("EN5", 0.15), ("RT5", 0.15), ("FB5", 0.15), ("COMP5", 0.15), ("CMP5", 0.15),
    ("BOOT6", 0.3), ("EN6", 0.15), ("RT6", 0.15), ("FB6", 0.15), ("COMP6", 0.15), ("CMP6", 0.15),
    ("Q1_G", 0.3), ("Q2_G", 0.3), ("ARM_G", 0.25), ("ARM_PD", 0.25), ("ARM_LED", 0.25), ("INJ", 0.25)] + \
    [(f"GATE{n + 1}", 0.25) for n in range(4)] + [(f"CL{n + 1}", 0.25) for n in range(4)] + \
    [(f"SB{i + 1}", 0.15) for i in range(6)] + [(f"SO{i + 1}", 0.25) for i in range(6)] + \
    [(f"SV{i + 1}", 0.15) for i in range(6)] + [
    ("5VA", 0.3), ("3V3R", 0.4), ("ANT", 0.35), ("CANH", 0.25), ("CANL", 0.25), ("CAN_T", 0.25), ("VRC", 0.3),
    ("BZ_N", 0.3), ("LED_D2", 0.15), ("LED_D3", 0.15), ("PWR_A", 0.15), ("VIN_T", 0.4)]
BUSES = [  # then the MCU's lines, the sensor bus first
    ("3V3", 0.3), ("SCK", 0.15), ("MOSI", 0.15), ("MISO", 0.15), ("CS_ICM", 0.15), ("CS_BMIA", 0.15),
    ("CS_BMIG", 0.15), ("CS_ADXL", 0.15), ("IMU_INT", 0.15), ("SDA", 0.15), ("SCL", 0.15)] + \
    [(f"FIRE{n + 1}", 0.15) for n in range(4)] + [(f"CONT{n + 1}", 0.15) for n in range(4)] + [
    ("ARM_EN", 0.15), ("MOSI1", 0.15), ("SCK1", 0.15), ("MISO1", 0.15), ("CS_LORA", 0.15), ("EXP_CS", 0.15),
    ("CAN_TX", 0.15), ("CAN_RX", 0.15), ("TEL_TX", 0.15), ("TEL_RX", 0.15), ("GPS_TX", 0.15), ("GPS_RX", 0.15),
    ("RC_RX", 0.15), ("RC_TX", 0.15), ("BTN", 0.15), ("BUZ", 0.15), ("LED_DIN", 0.15), ("VS_SENSE", 0.15)]
PARTIAL = [  # nets fed by a pour: route only these pins to it (net, width, pins)
    ("VBAT_RAW", 0.3, [("TP1", "1")]), ("VIN_P", 0.25, [("U1", "10"), ("TP2", "1")]),
    ("VIN", 0.25, [("U1", "9"), ("U1", "8"), ("R2", "1"), ("R9", "1"), ("TP9", "1")]),
    ("SW5", 0.3, [("C6", "2")]), ("SW6", 0.3, [("C9", "2")]),
    ("VSERVO", 0.25, [("R12", "1"), ("R25", "1"), ("TP3", "1")]),
    ("VPYRO", 0.25, [("TP7", "1")]), ("VPYRO_SW", 0.25, [("R18", "1"), ("DZ2", "1")]),
    ("VPYRO_ARMED", 0.3, [("R15", "1"), ("R19", "2"), ("TP8", "1")]),
] + [(f"PYRO{n + 1}", 0.3, [(f"RL{n + 1}", "1"), (f"RA{n + 1}", "1")]) for n in range(4)]


RIP = 2.5            # (mm: an earlier routing's nets torn up round a pad that failed, so it routes first)
GRID = 0.254                                          # the 2.54 mm pin lattice / 10
PASSES = 3


def keepout_for(net):
    """the router stays off every other net's power copper (vias included)"""
    ko = {"F": [], "B": []}
    for n, L, g, _ in power_pours():
        if n != net and L in ko:
            for q in (getattr(g, "geoms", None) or [g]):
                ko[L].append(list(q.buffer(0.25, join_style=2).exterior.coords)[:-1])
    return ko


def in2_bar():
    """In2 may carry a signal only inside the +5 V plane's area (never under the VIN plane): tracks barred
    elsewhere, vias free to pass"""
    from shapely.geometry import Polygon
    return {"2": [list(Polygon(IN2_VIN).buffer(1.4, join_style=2).exterior.coords)[:-1], rect(-1, -1, 37.2, H + 1)]}


def fc_route(b):
    probs = []
    # the power pours' layers tied together (top pour <-> bottom pour <-> inner plane)
    for net, L, g, amps in power_pours():
        for q in (getattr(g, "geoms", None) or [g]):
            reg = list(q.exterior.coords)[:-1]
            if net in ("VIN",) and L == "F":
                b.stitch(net, [(x, min(y, 34.6)) for x, y in reg] if q.bounds[1] > 30 else reg, pitch=1.2,
                         drill=0.3, dia=0.6, layers="F2")
            elif net == "+5V":
                b.stitch(net, reg, pitch=1.2, drill=0.3, dia=0.6, layers="F2")
            elif net in ("VSERVO", "VPYRO", "VPYRO_ARMED", "VBEC") and L == "F":
                b.stitch(net, reg, pitch=1.2, drill=0.3, dia=0.6, layers="FB")
            elif net == "GND":
                b.stitch(net, reg, pitch=1.0, drill=0.3, dia=0.6, layers="F1")
    # the magnetometer's CAP pin straight across to its capacitor, between the ground pins either side of it
    b.track("MAG_CAP", "F", [b.pad("U10", "10"), b.pad("C29", "1")], 0.2, 0.05)
    b._fan = getattr(b, "_fan", set()) | {("U10", "10")}   # (that is its way out: no escape stub beside it)
    # the button's paired pins (two pads numbered 1, two numbered 2) tied under its body: joined inside the
    # switch, but each pad is its own pad to KiCad and the router knows a pin by its number
    from pcb import _pad_poly
    for num, net in (("1", "BTN"), ("2", "GND")):
        c_ = b.comps["SW1"]
        ends = [_pad_poly(pd, (c_["at"][0], c_["at"][1], c_["rot"])).centroid for pd in c_["pads"][num]]
        b.track(net, "F", [(e.x, e.y) for e in ends], 0.3, 0.05)
    b.corridor = 0.4                                  # (a fine-pitch stub's way out kept clear 0.4 mm past it)
    b.fanout(width=0.15)
    # every Teensy signal pin gets its way out between the rows before anything routes, so no other net can run
    # along the row and seal it in
    pn = b.pad_net()
    for ref, inward in (("JT1", -1.0), ("JT2", 1.0)):
        for k in range(1, 25):
            n = pn.get((ref, str(k)))
            if n is None or n in ("GND", "3V3"):
                continue
            x, y = b.pad(ref, str(k))
            b.track(n, "F", [(x, y), (x, round(y + inward * 1.6, 4))], 0.15, 0.01)
            b.tracks[-1]["fan"] = (ref, str(k))
    # planes: every +5V / VIN / GND pad drops a via to its inner plane beside it (pads inside a pour of their
    # own net are stitched with it already)
    from shapely.geometry import Polygon
    own = {}
    for n, L, g, _ in power_pours():
        own.setdefault(n, []).append((L, g))
    side = lambda r: "B" if b.comps[r].get("side") == "B" else "F"
    shapes = {(r, n_): g for r, n_, L, g, _ in b.pad_shapes() if L == side(r)}     # (each pad on its part's side)
    def loose(net, pins):
        return [p for p in pins if p in shapes and not any(L == side(p[0]) and g.contains(shapes[p].centroid)
                                                           for L, g in own.get(net, []))
                and not any(pd["drill"] for pd in b.comps[p[0]]["pads"][p[1]])]
    # the ICs', their decoupling capacitors', the fire FETs', the continuity LEDs' and the bottom-side parts'
    # ground pins get their plane via first (a fine-pitch ground pin, or a small part between bigger ones, boxed in by signals later has no
    # room for one)
    ic_gnd = [p for p in b.nets["GND"]["pins"] if (p[0][0] in "UQC" or p[0].startswith("DC") or
                                                   b.comps[p[0]].get("side") == "B") and p in shapes and
              not any(pd["drill"] for pd in b.comps[p[0]]["pads"][p[1]])]
    ic_gnd = [p for p in ic_gnd if p[0][0] != "C" or loose("GND", [p])]   # (a capacitor in a ground pour has it)
    probs += b.drop("GND", ic_gnd, reach=2.5, width=0.25, amps=0.5)
    # the same for every +5 V pad over the +5 V plane (one boxed in by signals has no room for its via later)
    over5 = Polygon(IN2_5V)
    early5 = [p for p in loose("+5V", b.nets["+5V"]["pins"]) if over5.contains(shapes[p].centroid)]
    missed5 = b.drop("+5V", early5, reach=3.0, width=0.3, amps=1.0)
    early5 = [p for p in early5 if not any(f"{p[0]}.{p[1]}" in m.split()[-1] for m in missed5)]
    # the signals: negotiated passes, checkpointed, and incremental after a placement change (pcb.route_jobs)
    jobs = [(net, w, None) for net, w in LOCAL + BUSES] + [(net, w, pins) for net, w, pins in PARTIAL]
    partial = {net for net, _, _ in PARTIAL}             # (their few pins' own current, not the net's)
    probs += route_jobs(b, jobs, kw=lambda net: {"grid": GRID, "layers": "F2B", "keepout": keepout_for(net),
                                                 "track_keepout": in2_bar(), "amps": 0.05 if net in partial else None},
                        passes=PASSES, rip=RIP)
    # plane vias; a pad with no room for one (or a +5 V pad over the VIN plane) is routed to its net instead
    from shapely.geometry import Point
    p5 = [p for p in loose("+5V", b.nets["+5V"]["pins"]) if p not in early5]
    away = [p for p in p5 if not over5.contains(shapes[p].centroid)]
    for net, pins, reach, lay, w, a in (("+5V", [p for p in p5 if p not in away], 3.0, "F2B", 0.3, 1.0),
                                        ("VIN", [("C2", "2"), ("C3", "2"), ("U2", "2")], 3.0, "FB", 0.4, 1.5),
                                        ("GND", loose("GND", b.nets["GND"]["pins"]), 2.5, "FB", 0.3, 1.0)):
        missed = b.drop(net, pins, reach=reach, width=w, amps=a)       # (a pad's own current, not the net's)
        stuck = [p for p in pins if any(f"{p[0]}.{p[1]}" in m.split()[-1] for m in missed)]
        rest = stuck + (away if net == "+5V" else [])   # (all of one net's in one call: one board set-up)
        if rest:
            probs += route(b, net, width=w, grid=GRID, layers=lay, keepout=keepout_for(net),
                           track_keepout=in2_bar(), only=rest, amps=a)
    prune_stubs(b)
    # heal: a piece of a plane net cut off by the signals crossing In2 is routed back to the rest
    # (each stray group to the main one: a scrap of plane round the stray pads does not count as the net)
    for net, w, a, lay in (("+5V", 0.3, 0.5, "F2B"), ("GND", 0.3, 0.5, "FB")):
        grps = b.islands(net)
        for grp in grps[1:]:
            probs += route(b, net, width=w, grid=GRID, layers=lay, keepout=keepout_for(net),
                           track_keepout=in2_bar(), only=grp, amps=a, seed_pins=grps[0])
    prune_stubs(b)
    # ground fills top and bottom tied to the plane wherever they cover it
    b.stitch("GND", rect(0, 0, W, H), pitch=2.5, drill=0.3, dia=0.6, layers="F1B")
    b.stitch("GND", rect(0, 0, W, H), pitch=2.5, drill=0.3, dia=0.6, layers="F1")
    b.stitch("GND", rect(0, 0, W, H), pitch=2.5, drill=0.3, dia=0.6, layers="1B")
    return probs


def prune_stubs(b):
    """drop escape stubs the router did not use (their far end touches nothing else of the net)"""
    from shapely.geometry import LineString, Point
    keep = []
    pads = {(r, n): g for r, n, L, g, _ in b.pad_shapes()}
    pn_ = b.pad_net()
    for t in b.tracks:
        if t.get("fan"):
            stub = LineString(t["pts"]).buffer(t["w"] / 2).difference(pads.get(t["fan"],
                                                                                Point(t["pts"][0]).buffer(0.01)))
            used = any(o is not t and o["net"] == t["net"] and o["layer"] == t["layer"] and
                       LineString(o["pts"]).buffer(o["w"] / 2).intersects(stub) for o in b.tracks) or \
                any(v["net"] == t["net"] and Point(v["at"]).buffer(v["dia"] / 2).intersects(stub) for v in b.vias) or \
                any(k != t["fan"] and pn_.get(k) == t["net"] and g.intersects(stub) for k, g in pads.items())
            if not used:
                continue
            # used: cut back to the farthest point something joins it (a dangling tail past it is a DRC warning)
            line = LineString(t["pts"])
            band = line.buffer(t["w"] / 2)
            ends = [Point(e) for o in b.tracks if o is not t and o["net"] == t["net"] and o["layer"] == t["layer"]
                    for e in (o["pts"][0], o["pts"][-1]) if band.contains(Point(e))] + \
                   [Point(v["at"]) for v in b.vias if v["net"] == t["net"] and band.contains(Point(v["at"]))]
            far_pad = any(k != t["fan"] and pn_.get(k) == t["net"] and g.intersects(stub) for k, g in pads.items())
            if ends and not far_pad:
                d = max(line.project(e) for e in ends)
                if 1e-3 < d < line.length - 1e-3:
                    q = line.interpolate(d)
                    t = dict(t, pts=[t["pts"][0], (round(q.x, 4), round(q.y, 4))])
        keep.append(t)
    b.tracks = keep


ROUTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes")   # routed copper, kept in git


def build(do_route=True, solids=False):
    b = Board("twilight_fc", rect(0, 0, W, H), thickness=1.6, copper_oz=1.0, clearance=0.15, max_rise_c=20.0,
              corner_r=2.0, layers=4, inner_oz=1.0)
    for h in HOLES:
        b.hole(h, 3.2, keepout=3.5)
    place_all(b, solids)
    fc_nets(b)
    fc_pours(b)
    b.silk_text = [("TWILIGHT FC  REV A", (30.0, 22.0), 1.6, 0.0)]          # (clear of parts, not under the Teensy)
    probs = cached_route(b, fc_route, tag="twilight-r24", store=ROUTES) if do_route else []
    probs += nudge_vias(b, gap=0.5) if do_route else []     # (JLCPCB keeps 0.5 mm between two nets' drills)
    return b, probs


PCBS, parts = {}, {}
import importlib.util                                     # (the 3D solids need build123d: without it, the board alone)
SOLIDS = os.environ.get("TWILIGHT_SOLIDS", "1" if importlib.util.find_spec("build123d") else "0") == "1"
if os.environ.get("TWILIGHT_BUILD", "1") == "1":         # (0: only the definitions, for the schematic and tools)
    PCB, PCB_ROUTE_PROBLEMS = build(solids=SOLIDS)
    PCBS["twilight_fc"] = PCB
    if SOLIDS and __name__ != "__main__":
        parts.update(PCB.parts(0.0))


if __name__ == "__main__":
    import csv
    import pcb
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    b, probs = PCB, PCB_ROUTE_PROBLEMS
    print("route:", len(probs))
    for p in probs:
        print("  ", p)
    chk = b.check()
    print("check:", len(chk))
    for p in chk:
        print("  ", p)
    os.makedirs(out, exist_ok=True)
    b.plot(os.path.join(out, "layers.png"))
    from twilight_fc_sheet import draw              # four A3 sheets, checked against the netlist
    sheet_probs = draw(b, out)
    print("schematic vs board:", sheet_probs or "matches")
    assert not sheet_probs, sheet_probs
    with open(os.path.join(out, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "mpn", "footprint"])
        for r in b.bom():
            w.writerow([" ".join(r["refs"]), r["qty"], r["value"], r["mpn"], r["footprint"]])
    pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    kpcb = pcb.export_kicad(b, os.path.join(out, "kicad"))[0]
    for refill in (False, True):                     # KiCad's own DRC, on our fills and on KiCad's
        d = pcb.kicad_drc(kpcb, refill=refill)
        print(f"KiCad DRC ({'KiCad refill' if refill else 'as exported'}):", len(d["errors"]), "errors,",
              len(d["unconnected"]), "unconnected,", d["warnings"] or "no warnings")
        for p in (d["errors"] + d["unconnected"])[:20]:
            print("  ", p)
        assert not d["errors"] and not d["unconnected"]
    import tempfile
    mdir = os.path.join(tempfile.gettempdir(), "kicad_3d")
    print("3D models not found:", pcb.kicad_models(b, mdir) or "none")
    pcb.render_kicad(kpcb, out, mdir)
    for d in ("gerbers", "kicad"):                  # one zip each in git, not thousands of generated lines
        pcb.pack(os.path.join(out, d))
    print("wrote", out)
