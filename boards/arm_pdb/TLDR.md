# Pioneer arm PDB: TL;DR

One board per arm (150 x 96 mm, 2 layers, 2 oz copper).
- **In:** 12S pack (36–49 V) on an XT90.
- **49 V out:** five 40 A-fused XT30 sockets for the AK motors (2 x AK10-9, 3 x AK80-9).
- **24 V out:** an on-board TI LM5145 converter feeds two 10 A-fused XT30 plugs for the GL40 IIs.

## How current was sized

**Rule:** copper is sized by continuous current, to a ≤ 20 °C rise (IPC-2221). Peaks are checked for duration,
because copper takes seconds to heat.

**Motor ratings are phase currents.** The trace carries bus current = power ÷ voltage. That is far lower at high
torque and low speed (a stall barely loads the bus), so peak phase current is a ceiling on bus current.

| Path | Sized for | Why | Copper | Rise |
|---|---|---|---|---|
| Motor branch | 32 A (AK10-9), 28 A (AK80-9) | the motor's **peak**, as if held forever: extra margin the board had room for (true continuous ≈ 14 A) | 8.6 mm, both layers | 6.9 °C |
| Trunk (shared, XT90 → fuses) and its ground | 60 A | all five AK motors at rated power at 36 V (≈ 66 A); **not** the 154 A sum of peaks, which never coincide (pack doc: 0.3 × peaks = 46 A; team estimate ≈ 10 A) | 30 mm band each | 17.8 °C |
| Ground near each connector | one motor's 32 A, half per layer | top and bottom stitched in parallel | — | < 20 °C |
| 24 V rail (shared by the GL40s) | 9 A | the converter's current limit, a hard ceiling | 7 mm | 2.6 °C |

The trunk drops 42 mV at 60 A. It stays 30 mm wide even where the current tapers past each tap: the space was there,
and the band doubles as the ground and heat-spreading plane.

## Other decisions

- **Fuses:** Eaton CB61F 2410 SMD bricks: 40 A / 63 VDC per AK feed (the team's 40 A), 10 A per 24 V feed. PCB ATO
  holders were rejected (30 A max, 22.5 A continuous, 32 V).
- **24 V for the GL40s:** they must never see the pack. The team doc called for a 24 V buck.
- **Converter:**
  - It is TI's published 48 → 24 V circuit, unchanged, so its compensation is TI-tested.
  - Rated 5 A continuous; above that the high-side FET runs too hot. Peaks go to about 9 A.
  - It runs in **forced PWM**, so the GL40s' regen energy flows back to the pack instead of pumping up the 24 V rail.
- **Polarity:** + is pin 2, the pad the AMASS footprints mark "+". KiCad confirms it pin by pin.
- **Mis-plug proofing:** the 24 V outputs are XT30 **male**, so a GL40 lead can't reach a 49 V socket and an AK lead
  can't reach a 24 V one.
- **Input protection:**
  - A 5 kW, 54 V TVS (above the 50.4 V BMS trip) for disconnect spikes. Slow overvoltage is the brake chopper's job.
  - 2 x 1200 µF bulk caps and 2 x 1 µF ceramics.
  - A bleed resistor and a red BUS LIVE LED.
- **Layout:** the switch node is kept free of signal traces, the gate drive is short, and the power grounds are
  stitched to the plane.

## Why trust it

- **Two independent checks are clean.** The harness `check()` and KiCad 9's DRC both report 0 errors and 0
  unconnected items, including after KiCad refills the pours itself. The harness check covers every net connected,
  no shorts, 0.2 mm clearance, width and heating of every track and pour, drill spacing, courtyards and edge
  clearance.
- **The schematic is traced against the board's netlist**, so it can't disagree with the board.
- **Parts and numbers are real.** Every part is a KiCad library footprint. Currents come from CubeMars' tables;
  voltages and fuses come from the team's pack and protection docs.

## Limits (check before ordering)

- **XT90:** rated 45 A continuous, below the copper's 60 A. For sustained loads above 45 A, change the connector.
- **XT30:** rated 30 A burst, just under the AK10-9's 31.9 A peak (the motors' own connector choice).
- **Fuse breaking capacity:** the board fuses break 500 A, but a dead short at a socket can draw about 1 kA from the
  pack. The pack's Class T fuse is the backstop.
- **24 V:** 5 A continuous; two GL40s at rated torque draw about 3.3 A. Limit GL40 current in firmware if both may
  hold at stall.
- **Not simulated:** the copper numbers are IPC estimates, which are conservative. Confirm the CB61F pad layout
  against Eaton's datasheet.
- **First power-up:** check every lead's polarity, and use a current-limited supply.

Files: `schematic_1.png` (distribution) and `schematic_2.png` (24 V converter), also as .svg; `assembled_*.png`;
`kicad.zip` (KiCad 9+); `gerbers.zip`; `bom.csv`; `README.md` (full notes).
