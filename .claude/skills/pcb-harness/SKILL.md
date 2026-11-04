---
name: pcb-harness
description: Design a printed circuit board with pcb-harness - parts on KiCad footprints, auto-placement, routing (FreeRouting through KiCad when installed), the design check, the board house's rules and KiCad's DRC, then Gerbers, BOM, pick-and-place and a parts cart to order it. Use for any PCB, circuit board, schematic-to-board, routing or fab-ordering task.
---

# pcb-harness

1. Once per machine: `pcb-harness doctor`. If KiCad, Java or FreeRouting is missing, run `pcb-harness setup`
   (FreeRouting, no root) and show the user `docs/setup.md` for the rest; install system software only with their
   approval. Everything is optional; the harness falls back to its own router and checks.
2. Write `board.py` with a `build()` returning a `pcb.Board` (outline, parts with real KiCad footprints and pin
   numbers from the datasheet, nets with their current, pours), or start from a KiCad netlist (`.net`). Place
   every IC with its KiCad symbol (`symbol="Lib:Name"`; `pcb-harness part Lib:Name` shows it) and wire its pins by
   name: pins are then checked against pads both ways and an ERC runs. Place
   connectors, buttons and mounting holes yourself; give other parts `at=None` for the placer.
3. Put what the board must meet in `req.json` (size, nets' current, `near`, `pairs`, `edge`, holes: AGENTS.md) and
   run `pcb-harness board.py out/ --spec req.json --json`. Read `clean`, `findings` (kind, pins, at, hint) and
   `critic` in the JSON. `--stage critic` reviews in milliseconds; `--stage check` / `export` reuse the last
   placement and routing (`out/board_state.pkl`).
   For a person watching, `pcb-harness view out/` (or `--view` on the run) opens the board in their browser:
   layout, 3D, schematic, findings, and live progress with the time left; give them the address it prints.
4. Fix each finding by changing the design (positions, widths, outline), never by loosening rules; run again.
5. Done only when `clean` is true. Say which router ran (`router`) and whether KiCad's DRC ran (`kicad_drc.run`);
   name any pin numbers you assumed. The order files are in `out/`: `gerbers.zip`, `bom_jlc.csv`, `cpl.csv`,
   `cart/`, `report.html`, `kicad/`.

Details: `AGENTS.md`, `docs/getting-started.md`, `docs/ordering.md`, `docs/setup.md` in the pcb-harness repository.
