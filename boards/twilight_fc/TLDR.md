# Twilight flight computer, Rev A: TL;DR

165 x 50 mm, 4 layers, Teensy 4.1 on sockets. Built from the "Twilight Flight Computer" concept (Rev A). KiCad 9
DRC: **0 errors, 0 unconnected**.

## What the concept asked for, and what the board does

| Concept says | Board |
|---|---|
| "165 × 50 mm, 4-layer — fits a 54 mm airframe and up" | 165 x 50 mm, 4 layers |
| "The midspan pair is the difference between a rigid board and a tuning fork" | six M3 holes, 76.5 x 38 mm pitch (corners + midspan) |
| "Current flows only when the switch is closed AND ARM_EN is high AND the channel gate is driven" | yes: keyed switch J15 in series, ARM_EN high-side FET, a low-side fire FET per channel |
| "The 10k gate pulldown is mandatory: it holds the FET off through reset, brownout and firmware upload" | 10k on every fire gate, plus 100R series |
| "ARM is driven from the pyro rail itself, never from a GPIO" | ARM LED runs from VPYRO_ARMED |
| "continuity sense at ~330 uA" | ~0.26 mA per match disarmed, one LED + ADC per channel |
| "you can put 100 mm of copper between the switching regulators and the accelerometers" | bucks at x < 60 mm, IMUs at x 122-141 mm |
| "The analog rail gets its own LDO behind a ferrite bead" | 3V3A: ferrite + TPS7A2033, all six sensors on it |
| "Never populate Mode A and B at the same time" | servo rail from the 6 V buck (Mode A) or a BEC on its own XT30 (Mode B) |
| Teensy 4.1 recommended | Teensy 4.1; all 42 edge pins used |

## Where it differs, and why

- **1 oz copper, not "2 oz outer copper".** 2 oz can't etch the 0.15 mm lines that the fine-pitch sensors and the 42 Teensy lines need. The power copper is resized for 1 oz.
- **One solid ground plane, not "Three ground pours tied at a single star point".** On 4 layers, a continuous return plane is quieter. Noisy currents stay at the power end because of where the parts are placed.
- **6 V servo buck is a TPS54560B (5 A), not a TPS56637 (6 A).** There's no reliable footprint for the TPS56637. Above 5 A, use Mode B.
- **Pin budget** (the Teensy has 42 edge pins and every one is used):
  - barometers on I2C rather than SPI;
  - SD and FAULT indicators as RGB LED pixels;
  - a 2x5 expansion header, not 2x10;
  - the NOR flash on the Teensy's own QSPI pads.
- **Not fitted:**
  - BTN2 reset: the Teensy brings no reset pin to its edge;
  - USB-C: the Teensy's micro-USB is used instead;
  - the SBUS inverter: the Teensy inverts in hardware.
- **Pyro bulk is 470 uF, not 1000 uF**, for room. The firing current comes from the pack, and the 5 V rail rides through the sag.
- **Fixed part numbers:** SQ4407EY and 0456015 don't exist, so the board uses SQ4401EY and 0451015.MRL. The TCAN332 becomes the TCAN332G, the CAN-FD-capable version.

## Why trust it

- **KiCad's own DRC is clean** on the exported pours and after KiCad refills them itself.
- **The harness check is clean.** It covers clearance on all layers, drill gaps, edge clearance, current per track and pour, and connectivity by KiCad's rules.
- **The schematic is checked pin for pin against the board's netlist.**
- **Every part is a real orderable part.**

## Before ordering

- Confirm the two drawn footprints (BMP390, MS5607) and the buzzer's + pad against the makers' drawings.
- Nothing is simulated; the copper sizes are IPC-2221 estimates.
- The fire FETs are 30 V parts: fine on 6S, but use 40 V parts if the wiring sees big transients.
- Bench-test the pyro channels with dummy loads only.

Full detail (pin map, power table, every deviation): `README.md`.
