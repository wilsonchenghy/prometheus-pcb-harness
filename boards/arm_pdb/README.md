# Arm power distribution board (Pioneer humanoid, one arm)

One board per arm: the 12S pack in, five fused 49 V feeds for the CubeMars AK motors, and an on-board 24 V buck with
two fused 24 V feeds for the GL40 II wrist and gripper motors. Source: `Backend/cad_bench/projects/arm_pdb.py`
(run it to regenerate everything here). 150 x 96 mm, 2 layers, 2 oz copper, 1.6 mm FR4, 4 x M3.

Files:
- `TLDR.md`: the one-page version.
- `schematic_1.png` / `schematic_2.png` (and `.svg`): two A3 sheets with frames and title blocks: power
  distribution, then the 24 V converter and GL40 feeds. Checked together against the board's netlist.
- `assembled_top/bottom/iso.png`: KiCad renders with every part's 3D model.
- `layers.png`: the copper plot.
- `bom.csv`.
- `gerbers.zip`: RS-274X + Excellon.
- `kicad.zip`: `arm_pdb.kicad_pcb` + `.kicad_pro`, KiCad 9, editable. The `3d/` folder holds the models of parts KiCad's
  library lacks.

## Inputs this is designed against

| Motor (site table) | Joint | Rated | Peak | Feed |
|---|---|---|---|---|
| CubeMars AK10-9 V3.0 KV60 | shoulder 1, 2 (J2, J3) | 10.7 A, 18 Nm, 442 W | 31.9 A, 53 Nm | 49 V, 40 A fuse |
| CubeMars AK80-9 V3.0 KV100 | elbow 3, 4, 5 (J4, J5, J6) | 12 A, 9 Nm | 28 A, 22 Nm | 49 V, 40 A fuse |
| CubeMars GL40 II KV70 | wrist 6 (J7), gripper 7 (J8) | ~1.6 A, 0.25 Nm | 5.22 A, 0.73 Nm | 24 V, 10 A fuse |

- **Bus:** the 12S7P EVE 40PL pack: 36.0 V practical cut-off to 49.0 V charge ceiling. The chopper switches on at
  49.8 V, worst-case BMS OVP is 50.4 V, and the CubeMars driver limit is 52 V. Every bus part is rated at least 63 V.
- **GL40 IIs:** 24 V class, per the team's Battery Protection System doc ("we will need a 24V buck converter for the
  GL40 ii motors"). They must never see the pack bus.

## Current budget: what each path is sized for

All checked by IPC-2221 (external layers, 2 oz) at no more than a 20 °C rise.

| Path | Design current | Copper | Rise at design current |
|---|---|---|---|
| Trunk: XT90 to the fuses (top), and its return (bottom) | 60 A continuous | 30 mm pour band, each layer | 17.8 °C (7.1 °C at 40 A) |
| Each AK branch: fuse to XT30 + | 32 A (the motor's peak), treated as continuous | 8.6 mm on both layers, 48 stitching vias | 6.9 °C (1.1 °C at 14.4 A) |
| Each motor's return round its connector | 32 A | top and bottom ground in parallel, stitched | within the 20 °C rule |
| 24 V rail: L1 to the fuses | 9 A (the converter's current limit) | 7 mm pour | 2.6 °C |
| 24 V branches | 10 A (the fuse) | 4.8 mm, both layers | within the 20 °C rule |

**Why 60 A for the trunk:**
- All five AK motors at rated power at the bottom of the pack draw 5 x ~13 A = 66 A.
  - AK10-9: 442 W / 0.85 / 36 V = 14.4 A.
- The team expects about 10 A for an arm in use.
- The pack doc's coincident-peak factor of 0.3 on this arm's 154 A sum of peaks gives 46 A.
- Peaks above 60 A last well under a second.

**Trunk resistance:** 0.7 mΩ, a 42 mV drop at 60 A.

**Connector limits, not the copper:**
- The XT90 is rated 45 A continuous (90 A burst). It is the input's continuous limit; the copper is good for 60 A.
- The XT30s are rated 15 A continuous (30 A burst). That is the motors' own connector choice (CubeMars XT30 2+2).
  The AK10-9's 31.9 A peak is slightly above the XT30's burst rating.

## Protection

**Fuses: Eaton CB61F brick, 2410 SMD, replaceable**
- CB61F40A (40 A, 63 VDC, 500 A DC breaking capacity) on each AK feed. This is the team's 40 A choice.
- CB61F10A on each 24 V feed.
- PCB ATO blade holders were rejected: the Littelfuse FLR is rated 30 A max, 22.5 A continuous, 32 V.
- The board uses KiCad's 2512 land pattern. **Check it against Eaton's CB61F recommended pads (Technical Data
  4377).** I could not download the datasheet here.

**Breaking capacity:** a bolted short at a motor connector can draw about 1 kA from the pack (49 V across ~45 mΩ),
which is above the brick's 500 A rating. The pack's Class T fuse (300–350 A) is the backstop for that case. The
board fuses clear the faults they are for: a motor or driver failing short behind its lead.

**TVS on the bus:** D1, 5.0SMDJ54A.
- 54 V standoff (above the 50.4 V OVP), 87 V clamp.
- It absorbs disconnect and wire-inductance spikes.
- It does **not** protect the motors from a slow rise above 52 V. That is the brake chopper's job.

**Bulk capacitance:** C1, C2, 2 x 1200 µF 63 V (the team's Rubycon ZLJ choice, 12.5 x 40 mm, tall).
- C3, C4: 1 µF 100 V ceramics for HF decoupling.

**Bleed:** R1, 22 kΩ, plus the BUS LIVE LED path (R2, D2).
- About 11 kΩ in total across 2.4 mF plus the drivers' own capacitance.
- The bus falls below 5 V in about a minute, and the red LED stays lit while it is charged.

**Polarity:** AMASS footprints mark **pin 2 (round pad) as +**, and the board follows that.
- Motor and pack connectors are female/male as AMASS intends: the board's XT30 sockets are female.
- The 24 V outputs are XT30 **male**, so a GL40 lead cannot be plugged into a 49 V socket, nor an AK lead into a
  24 V one.
- Still, check every lead's polarity before first power.

## 24 V buck

TI LM5145 synchronous buck, **application circuit 3 of the datasheet (SNVSAI4B)**, unchanged. That circuit is
48 V to 24 V at 440 kHz, validated by TI. Using it unchanged keeps TI's compensation valid.

| Part | Value |
|---|---|
| Q1, Q2 | CSD19534Q5A (100 V, 13 mΩ) |
| L1 | Coilcraft XAL1010-153 (15 µH, 15.5 A sat) |
| CIN | 3 x 4.7 µF 100 V |
| COUT | 7 x 10 µF 50 V |
| Compensation | RC1 41.2 k, CC1 1 nF, CC2 18 pF, CC3 390 pF, RC2 1 k |
| Feedback | 71.5 k / 2.49 k → **23.8 V** (TI's "72.3 k" is not an E96 value) |
| RT | 22.6 k (440 kHz) |
| Soft start | 47 nF (~3.8 ms) |
| UVLO | 200 k / 8.87 k: on at 28.3 V, off at 26.3 V |

- **Rating: 5 A continuous** (TI's tested rating), with about 9 A peaks.
  - The valley current limit, RILIM 499 Ω on the low-side FET's RDS(on), lets roughly 8–9 A out.
  - Above 5 A continuous, the high-side FET's switching loss (about 2.5 W at 9 A) needs more copper than this board
    has.
  - Two GL40 IIs at rated torque draw about 3.3 A. A gripper holding at full stall current can draw about 4 A per
    motor at 24 V. **Limit the GL40 current in firmware** if both may sit at stall together.
- **Forced PWM** (SYNCIN tied to VCC): the buck runs in both directions, so energy the GL40s regenerate flows back
  into the pack bus instead of pumping up the 24 V rail. This addresses the team doc's concern that a buck blocks
  regen.
- D3 (SMBJ28A) clamps the 24 V rail. A green LED shows 24 V is on.
- **Layout:** the switch node is a pour kept free of signal traces (the high-side gate drops through a via at Q1).
  The input caps and Q2's source have their own ground islands stitched to the bottom plane right under the loop.
  The controller sits on its gate-drive side with the compensation network directly off its pins.

## Checks run

- **Harness `Board.check()`: clean.**
  - Every net is connected, with no shorts and 0.2 mm clearance.
  - IPC-2221 width holds for every track and every pour neck, at the current each path carries.
  - IR drop, edge clearance, courtyards (including the big caps' circular ones) and 0.25 mm drill spacing pass.
- **KiCad 9.0.9 DRC on the exported board:** 0 errors, 0 warnings except "footprint library not configured", both as
  exported and after KiCad refills the zones itself.
  - No silkscreen overlaps and no silkscreen on copper: the reference designators are placed clear of pads, parts
    and each other. Two dense compensation resistors (R32, R34) keep theirs on the assembly layer.
  - No dead copper islands.
- **Schematic:** its drawn wires were traced and match the board's netlist exactly (`schem.Sheet.check`).
- **Polarity:** read from the footprints' own "+" marks, and checked pin by pin in KiCad (pin 2 = motor net).

## Not done / check before ordering

- **No circuit simulation of the buck, and no thermal simulation.** The current ratings above are IPC-2221 estimates.
- **CB61F land pattern:** confirm it against Eaton's datasheet (see above).
- **GL40 II details:** its supply range and connector were not confirmed from a CubeMars datasheet. The team doc's
  5.22 A and 24 V are used.
- **CAN:** this board carries power only. The motors' CAN wiring is separate.
