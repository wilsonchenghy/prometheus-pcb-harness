# Branch `claude/pcb-collection`: what to review

This branch has two parts:
1. **A collection of boards** in `boards/`, from difficulty 1 to 4.
2. **Harness fixes**, found by building those boards and then testing the harness against an independent judge:
   KiCad 9's own DRC, on every board, on the repo's examples, and on random boards.

## 1. The boards (`boards/README.md`)

- 26 designs. 24 are clean: routing, design check, JLCPCB house check, critic, KiCad 9 DRC and the schematic read
  back by KiCad. Each `out/result.json` says so (`clean`, `kicad_drc.run`).
- 19 (STM32 Blue Pill) and 23 (FE1.1s USB hub) are not clean yet. Each has a `NOTES.md` saying what remains and what
  to change.
- Every `board.py` docstring lists what was assumed. Check those against the datasheets before ordering;
  `boards/README.md` lists the ones that matter most:
  - hand-assigned pins: relay contacts, LED polarity, the battery holder, the barrel jack;
  - the generated PAM8403 symbol and the generated ESP32-C3-MINI-1 footprint;
  - LCSC numbers that were looked up automatically.

## 2. The harness changes (`CHANGELOG.md`, "Unreleased")

Every fix has a regression test that fails on the code before it and passes after. The suite went from 106 to 133
passing tests, with one skipped (it needs KiCad 8). The ones to look at first, by how much they matter:

| What was wrong | Who it hit |
|---|---|
| Exported boards KiCad 9 could not open (KiCad 10 `point` items in today's footprint library) | any board with LED_THT / TO-220 / DIP parts |
| Specctra export failing silently (mounting holes named H1, H2 like the script's parts) | FreeRouting on any such board |
| A mounting hole drilled through a pad passed the design check | found by KiCad on board 26 |
| `--stage check/export/critic` ignored the saved routing of a board whose script draws copper | every hand-routed board |
| The router and clean-up passes reshaped, tore up or duplicated hand-drawn copper; the placer put parts on it | same |
| Vias placed against USB-C shell slots (slot treated as a round hole) | USB-C boards |
| FreeRouting fan-out stubs ran sideways into neighbour pins at the ends of SSOP rows | fine-pitch SSOPs |
| Schematic shorted facing pins (a dual pot's units) | KiCad netlist differed from the board |
| Courtyards were bounding boxes; overlaps under 0.05 mm² passed | disagreed with KiCad's DRC both ways |
| neck_vias: 0.2 mm ties on power nets, and tracks left dangling | current findings, dangling tracks |
| FreeRouting runs not repeatable (KiCad's import order) | same input, different Gerbers |
| 4-layer boards house-checked against the 2-layer preset | Twilight FC example |
| A wide net's way out of a fine-pitch pin drawn at full width, into the neighbouring pins | board 23 |
| A saved routing missing the script's own copper kept being reused | board 16 |

Also new:
- `critic`: a "via current" warning, for a power path that changes layer through too few vias.
- `bench/fuzz.py --kicad`: random boards with KiCad's DRC as a second judge. 48 seeds run (36 with FreeRouting,
  12 with the built-in router): no crashes, and no board the harness called clean that KiCad did not.
- `prometheus import`: keeps the edge overhang, the symbols and the no-connect pins on a round trip.

## Known and not fixed

- `bench/rp2040_board.py` does not come out clean with FreeRouting here: one QSPI net is left unreached. It fails
  the same way on the code before this branch.
- The via-current check is a heuristic. It judges only nets without a pour, and only where both sides hold a part
  on the power path (connector, fuse, inductor, transistor, diode). A branch to a single chip is not judged.
- The design-key change means the boards with hand-drawn copper route once more on their first run (their
  `routes/` files are refreshed in this branch).

## Environment

KiCad 9.0.9 (kicad-cli, pcbnew, symbols, footprints), Java 21 and FreeRouting 2.0.1 were installed in the session's
container, so every result above was judged by KiCad. A machine without them gets the built-in router and no
KiCad DRC, and each run says so in `skipped`.
