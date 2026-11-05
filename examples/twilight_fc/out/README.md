# Twilight flight computer, Rev A

The board described in the "Twilight Flight Computer" concept (Rev A, Aug 2026), designed in the harness:
`../twilight_fc.py` (layout), `twilight_fc_sheet.py` (schematic). Run the first to regenerate
everything here. **165 x 50 mm, 4 layers, 1 oz copper, 0.15 mm tracks and clearance, 1.6 mm FR4, six M3 holes on a
76.5 x 38 mm pattern (corners + midspan).**

Files:
- `TLDR.md`: the one-page version.
- `schematic_1..4.pdf` (vector, zoom freely), `.png` (~9000 px wide) and `.svg`: four A3 sheets (power; compute + I/O; sensors; pyro + servo), checked together
  against the board's netlist.
- **Two-sided assembly:** the INA226 and the pyro / arming resistors sit on the bottom; everything tall is on top.
- `assembled_top/bottom/iso.png`: KiCad renders with the parts' 3D models (drawn bodies for the Teensy 4.1, RFM95W,
  XT30s, buzzer, barometers, WS2812s and the servo block, which KiCad's library lacks).
- `layers.png`: copper plot. `bom.csv`. `gerbers.zip`. `kicad.zip` (KiCad 9, editable, with `3d/`; unzip to open).

## Floorplan (x along the board, mm)

| x | Island | What is there |
|---|---|---|
| 0-60 | POWER | XT30 battery in, 15 A fuse, reverse-polarity P-FET, TVS, 2 mOhm shunt (INA226 under it, bottom side); 5 V buck; 6 V servo buck; XT30 BEC inlet (Mode B) |
| 51-80, top | ARMING | 7 A pyro fuse + Schottky, 470 uF pyro bulk, the keyed arm-switch terminal, the ARM_EN FET, the ARM LED; its gate parts and the continuity injection on the bottom |
| 60-121 | COMPUTE | Teensy 4.1 on two 1x24 sockets; low parts under it (it sits ~11 mm up) |
| 86-121, top | PYRO | four e-match terminals, a fire FET under each, continuity LEDs; each channel's resistors on the bottom, under its FET |
| 60-121, bottom | SERVO / I-O | 3 x 6 servo block, servo bulk, buzzer, 2x5 expansion, pyro test points |
| 122-141 | INERTIAL | ICM-42688-P (sensor datum), BMI088, ADXL375, MMC5983MA, BMP390 + MS5607, their own 3.3 V LDO |
| 141-165 | RF / I-O | RFM95W + u.FL, TELEM1 / GPS1 / RC / CAN / Qwiic, WS2812 x3, PWR LED, button |

**Sensor datum: the ICM-42688-P centre, X = 130.0, Y = 25.0 mm** from the bottom-left corner (silkscreened). Enter the
IMU-to-CG lever arm from it.

## Power

| Rail | Source | Rating | Loads |
|---|---|---|---|
| VIN | XT30 -> F1 15 A -> Q1 SQM40031EL (3 mOhm) -> RS1 2 mOhm | 6.5-26 V (2S-6S), 15 A fuse | everything |
| +5 V | U2 TPS54360B, TI's 600 kHz datasheet design | 3.5 A | Teensy (via D9), TELEM1 (RFD900x 1 A bursts, 220 uF at the connector), GPS, CAN, RC, servo buffer, LEDs, buzzer, the two LDOs |
| 3V3 | the Teensy's own regulator | 250 mA | INA226, TCAN332G, I2C pull-ups, Qwiic, expansion, PWR LED, continuity injection |
| 3V3A | +5 V -> FB1 ferrite -> U4 TPS7A2033 | 300 mA | all six sensors |
| 3V3R | +5 V -> U12 TPS7A2033 | 300 mA | RFM95W (120 mA at +20 dBm) |
| VSERVO | Mode A: U3 TPS54560B 6.0 V; Mode B: J2 XT30 -> Q2 P-FET | 5 A (A) / 12 A (B) | six servos |
| VPYRO | VIN -> F2 7 A -> D2 PDS540 | 7 A | the arming chain |

**Copper (IPC-2221, <= 20 C rise, 1 oz):**
- Input path (VBAT, VIN_P, VIN): 10 A continuous. The board's own worst case is ~7.5 A from a 2S pack (Mode A servos
  at 5 A / 6 V plus the 5 V rail at 3.5 A); F1 (15 A) is the fault limit, not a design current.
- BEC inlet: 12 A, top and bottom in parallel (6 A each). VSERVO: 12 A on the bottom layer from the inlet to the servo
  block.
- Pyro (VPYRO, VPYRO_ARMED, each drain and its FET's source): 5 A, the concept's firing pulse; F2 (7 A) bounds it.
- The bucks' switch nodes and outputs: their current limits. In1 is a solid ground plane; In2 carries VIN
under the power island and +5 V everywhere else; where the outer layers are full, a signal may cross in In2 inside
the +5 V area (never under the VIN plane; In1 stays unbroken). Every SMD ground, +5 V and VIN pad drops a via to its plane beside
it; the top and bottom ground fills are stitched to In1.

**Converters:**
- U2 (5 V): TI's TPS54360B design values: 600 kHz (RT 162k), 6.8 uH, 2 x 47 uF, RCOMP 13k / 6.8 nF / 39 pF,
  FB 53.6k / 10.2k. UVLO 147k / 35.7k: on at 6.0 V.
- U3 (6.0 V servo, Mode A): TPS54560B (the 5 A sibling of the TPS54360B, same HSOP-8 footprint). 500 kHz
  (RT 196k), 6.8 uH (Coilcraft XAL6060, 8.6 A sat), 3 x 47 uF 16 V, FB 66.5k / 10.2k (6.02 V). Compensation from TI's
  equations (section 8.2.2.11) for 85 uF derated output: fco 28 kHz, RCOMP 18.7k, CCOMP 5.6 nF, CPOLE 33 pF.
- Both catch diodes PDS540 (40 V, 5 A, PowerDI-5).
- **Below ~7 V input the 6 V rail cannot hold regulation at 5 A** (any buck's maximum duty): on a nearly flat 2S pack
  VSERVO sags toward the input. Logged through VS_SENSE (pin 25).

**Mode A / Mode B:** Mode A fits U3 and its parts and leaves J2 empty. Mode B (12 A servos from an external BEC or a
2S LiFe on J2) leaves **L2 off**, so the BEC cannot back-feed the buck. Never fit both.

## Pyro

Two independent conditions before any current reaches an e-match, as the concept requires:
1. **The external keyed switch** (J15) in series with the pyro supply: hardware only.
2. **ARM_EN** (pin 15) turns on Q3 (SQ4401EY high-side P-FET) through Q4. R18 holds Q3 off; R16 holds Q4 off through
   reset; DZ2 clamps Q3's gate.
Then each channel's low-side FET (CSD17573Q5B) is driven from its FIRE pin through 100R, with **a 10k pull-down that
holds the gate low through reset, brownout and firmware upload.** The ARM LED (D7) runs from VPYRO_ARMED itself.

**Continuity:** 3V3 -> BAT54 -> 330R injects into VPYRO_ARMED (the BAT54 blocks the pyro voltage). Through each match
it lights that channel's green LED (3.3k + LED to ground, ~0.26 mA disarmed) and appears at the ADC through
100k / 12k:
| State | ADC (CONTn) |
|---|---|
| match present, disarmed | ~0.28 V |
| no match | 0 V |
| armed, match present | VPYRO x 0.107 (2.8 V at 26 V) |
Every current stays orders of magnitude below an e-match's no-fire current (7 mA armed at 26 V, through the LED path).

**Pyro supply:** F2 (7 A) and D2 feed VPYRO from VIN; C17 470 uF sits on VPYRO. The 5 V converter regulates down to
~6 V input, so a firing pulse that sags the pack does not reset the MCU.

**CSD17573Q5B is a 30 V part**: fine on 6S (25.2 V) but the input TVS clamps at ~53 V, so a large input transient is
not clamped below its rating. Use a 40 V FET if the vehicle's wiring sees big transients.

## Compute, sensors, links

**Teensy 4.1** on two 1x24 sockets: every one of its 42 edge pins is used.

| Pins | Use | | Pins | Use |
|---|---|---|---|---|
| 0 / 1 | TELEM1 (Serial1) | | 22 / 23 | BMI088 accel / gyro CS |
| 2, 3, 4, 5, 6, 9 | servo 1-6 (FlexPWM) | | 41 | ADXL375 CS |
| 7 / 8 | GPS1 (Serial2) | | 40 | VSERVO sense (A16) |
| 10 | ICM-42688-P CS | | 26 / 27 / 39 / 38 | SPI1 MOSI / SCK / MISO, LoRa CS |
| 11 / 12 / 13 | SPI0 (sensors) | | 28, 29, 32, 33 | FIRE 1-4 |
| 14 | expansion CS | | 30 / 31 | CAN3 (CAN-FD) RX / TX |
| 15 | ARM_EN | | 34 / 35 | RC in (Serial8: SBUS / CRSF / PPM) |
| 16 | ICM-42688-P INT1 | | 36 | buzzer |
| 17 | button | | 37 | WS2812 chain |
| 18 / 19 | I2C (SDA / SCL) | | 24, 25, 20, 21 | pyro continuity ADC 1-4 (A10, A11, A6, A7) |

**Sensors:** ICM-42688-P, BMI088 and ADXL375 on SPI0 with a chip select each; BMP390 (0x77), MS5607 (0x76) and
MMC5983MA (0x30) on I2C with the INA226 (0x40), the GPS's I2C and Qwiic. All six sensors on 3V3A.
**Radio:** RFM95W on SPI1 (polled: DIO0 not wired), u.FL for the antenna. **CAN-FD:** TCAN332G on CAN3 (the Teensy's
only FD port), JST-GH 4, 120R behind solder jumper JP1. **RC:** JST-GH 4, supply 5 V or 3.3 V by JP2.
**Connectors** in Pixhawk JST-GH order (TELEM1: 5V TX RX CTS RTS GND; GPS1: 5V TX RX SCL SDA GND; CAN: 5V CANH
CANL GND).

## Where this differs from the concept, and why

| Concept | Here | Why |
|---|---|---|
| 2 oz outer copper | 1 oz, 0.15 mm tracks / clearance | 2 oz cannot etch the 0.15 mm lines a 0.5 mm-pitch LGA and 42 MCU lines between two socket rows need; the router could not fit the board at 2 oz rules (0.2 / 0.2). The power copper is re-sized for 1 oz (widths above), with the high-current paths doubled on both layers. |
| TPS56637 6 V / 6 A | TPS54560B 6 V / **5 A** | KiCad has no TPS56637 footprint and its RPA land pattern could not be taken reliably from TI's sheet. The TPS54560B shares the 5 V buck's footprint and design method. Above 5 A, Mode B is the concept's own answer. |
| W25Q128 on the board | on the Teensy's own bottom flash pads | No pin was left for its chip select; the Teensy 4.1 has a QSPI footprint for exactly this, supported by Teensyduino (LittleFS). For flight units solder the Teensy's headers to the board so an impact cannot unseat it. |
| BMP390 / MS5607 on SPI0 | on I2C | Pin budget. The baros are slow (<= 200 Hz); the inertial sensors keep their own CS lines on SPI0. |
| SD and FAULT discrete LEDs | WS2812 pixels 2 and 3 in the chain | No pins. PWR and ARM stay discrete and hard-wired, as the concept insists. |
| BTN2 reset | not fitted | The Teensy 4.1 brings no reset to its edge pins; it has its own program button. |
| CRSF without a TX line | full CRSF on Serial8 (34 / 35) | Kept bidirectional by polling the LoRa module instead of wiring its DIO0. |
| CAN1 at the RF end | CAN3 (FD) by the sensor island | CAN3 is the Teensy's only CAN-FD port. |
| TCAN332 | TCAN332**G** | The plain TCAN332 is a 1 Mbps classic-CAN part. |
| SQ4407EY / 0456015 | SQ4401EY / 0451015.MRL | The concept's part numbers do not exist; these are the real parts it meant. |
| 1000 uF pyro bulk | 470 uF | Room. The firing current comes through D2 from the pack; the 5 V converter rides through the sag. |
| 2x10 expansion | 2x5 (5V, 3V3, GND x2, I2C, SPI1, one CS) | Pin budget. |
| USB-C | the Teensy's micro-USB | Its device USB pins are not on the edge rows. |
| Sensor datum X 115 | X 130.0, Y 25.0 | The datum is where the IMU is. |
| Three star-tied ground pours | one solid ground plane (In1) | On a 4-layer board a continuous return plane beats split grounds; the noisy currents stay local by placement (power end, servo and pyro returns there). |
| DNP SBUS inverter | not fitted | The Teensy's LPUART inverts RX in hardware. |

## Checks run

All on the committed layout (`twilight_fc.py`, route tag `twilight-r24`):

- **Harness routing:** every net routed (0 pads unreached).
- **Harness `check()`: 0 problems.** It covers clearance on all four layers, drill-to-drill gaps, copper to the
  board edge, track width and voltage drop per net current (IPC-2221, 20 C rise), pour necks, every net connected
  both by its copper and by KiCad's own joining rule (track ends, pad overlap, paired pads), loose track ends,
  courtyards and keep-outs.
- **KiCad 9 DRC: 0 errors, 0 unconnected items**, both on the exported zone fills and after KiCad refills every
  zone itself (`pcbnew.ZONE_FILLER`). The only warnings are `lib_footprint_issues` (the project does not list
  KiCad's standard footprint libraries; the footprints are embedded in the board).
- **Schematic vs board:** the four sheets' connections equal the board's netlist, pin for pin.
- **PCB tests:** `pytest Backend/tests/test_cad_pipeline.py -k pcb` (the harness behaviour this board relies on).

Getting there: the harness first called the board clean while KiCad found seven nets unconnected, a via 0.21 mm
from the edge and a ground fill touching two solder jumpers. Each was the harness modelling something unlike
KiCad (sharp-cornered pads, a jumper's rounded ends missing, KiCad's end-point joining rule); all are fixed in
the harness and covered by tests, so its check now names what KiCad's DRC names.

## Not done / check before ordering

- **Not simulated:** no circuit simulation of the converters or the pyro channels; the copper figures are IPC-2221
  estimates.
- **Footprints to confirm:** the BMP390 and MS5607 land patterns are drawn from the makers' sheets (the MS5607's from
  its pin-identical MS5611 sibling); the CSD17573Q5B uses KiCad's Q5A-family VSON land; the CUI buzzer's + pad (pad 1)
  should be checked against CUI's drawing.
- **Teensy socket height:** the parts under the Teensy (the servo buffer and its resistors, D9, the 74HCT244) are under
  2 mm tall; it sits ~11 mm up on standard 8.5 mm sockets plus its own pin headers.
- **RF:** the antenna trace is short (u.FL beside the module) but not impedance-controlled.
- **Bring-up:** follow the concept's order: rails with no MCU, then the reverse-polarity test, then ripple on TP4 /
  TP6, then the MCU. Pyro channels with dummy loads only, twenty power cycles watching the gate test points.
