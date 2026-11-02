# Pioneer Cube main board, r1

The carrier on the Pioneer Cube's -Z plate (`Backend/cad_bench/projects/balancing_cube/cube_board.py`, schematic
`cube_board_sheet.py`; run each from `Backend/occ_services` to regenerate this folder). **112 x 98 mm, 2 layers,
2 oz, 1.6 mm FR4, 0.2 mm clearance, every part through-hole (hand-solderable), 4 x M3 holes.**

## TL;DR

| Needs (from the cube) | The board |
|---|---|
| 3S LiPo in, protected | XT30UPB-M (pin 2 +) -> 15 A ATM fuse (Keystone 3568) -> VBAT; 470 uF 25 V bulk |
| Three BLDC drivers, 7 A bursts | 3 x SimpleFOC Mini v2.3 (DRV8316, 8 A) on their own headers; VBAT on 2 oz tracks sized by IPC-2221 at 20 C (8 A); each driver's return on its own wide track to J1 (a star: motor current stays off the logic ground) |
| Motor phases out | 3 x AMASS MR30PW-M at the board's edges, phase tracks sized for 7 A |
| Encoders (SPI, 3 on one bus) | 3 x JST S6B-XH-A (3V3 GND SCK MISO MOSI CS), CS on D5 / D15 / D2 |
| Brain | ESP32 DevKit V1 (DOIT 30 pin) on 2 x 15 sockets, USB at the -X edge (through the -X plate); copper keep-out under its antenna |
| IMU | GY-521 (MPU-6050) on I2C (D21 / D22), INT on D34 |
| 5 V | Pololu D24V10F5 (1 A) -> the DevKit's VIN; its 3.3 V feeds the IMU and encoders |
| Battery sense | driver X's VCC x 0.1 output -> D35 (ADC1) |

Why to trust it: the harness's board check (connectivity, shorts, clearance, IPC-2221 widths, pour necks, courtyards,
edge clearance) is clean, and **KiCad 9.0.9's own DRC finds 0 errors and 0 unconnected items** both on the exported
fill and after KiCad refills every zone (13 warnings: silkscreen overlaps). The schematic (`schematic.pdf`) is checked
against the board's netlist and for readability (no overlaps, every part labelled).

Check before ordering:
- **The SimpleFOC Mini v2.3's pin positions** were read off its published dimensioned layout (+-0.15 mm). Its two
  header rows and motor pads are on one 2.54 mm lattice (so they fit together); the offset to its two supply holes is
  the least certain: hold a real Mini over the printed `layers.png` at 1:1.
- The GY-521's pin order (VCC GND SCL SDA XDA XCL AD0 INT is the common one) and the D24V10F5's (PG SHDN VIN GND
  VOUT as Pololu lists them): the silkscreen names each pin.
- The GL30 encoder's lead: this board's XH pinout is its own; wire the motor's plug to it.

Files: `gerbers.zip` (RS-274X + Excellon), `kicad.zip` (KiCad 9 project), `bom.csv`, `layers.png`,
`assembled_top/bottom/iso.png` (KiCad renders), `schematic.pdf` / `.png` / `.svg`.
