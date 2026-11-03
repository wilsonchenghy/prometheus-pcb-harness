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
   connectors, buttons and mounting holes yourself; give other parts `at=None` for the placer.
3. Run `pcb-harness board.py out/ --json`. Read `clean` and `findings` (kind, pins, at, hint) in the JSON.
4. Fix each finding by changing the design (positions, widths, outline), never by loosening rules; run again.
5. Done only when `clean` is true. Say which router ran (`router`) and whether KiCad's DRC ran (`kicad_drc.run`);
   name any pin numbers you assumed. The order files are in `out/`: `gerbers.zip`, `bom_jlc.csv`, `cpl.csv`,
   `cart/`, `report.html`, `kicad/`.

Details: `AGENTS.md`, `docs/getting-started.md`, `docs/ordering.md`, `docs/setup.md` in the pcb-harness repository.
