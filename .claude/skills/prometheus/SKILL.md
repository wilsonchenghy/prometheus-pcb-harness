---
name: prometheus
description: Design a printed circuit board with Prometheus - parts on KiCad footprints, auto-placement, routing (FreeRouting through KiCad when installed), the design check, the board house's rules and KiCad's DRC, then Gerbers, BOM, pick-and-place and a parts cart to order it. Use for any PCB, circuit board, schematic-to-board, routing or fab-ordering task.
---

# Prometheus

Work with the engineer, not around them (AGENTS.md, "Working with an engineer"): ask for their requirements and
write them into `req.json`; stop at decisions (placement, part swaps, values, assumed pins, looked-up LCSC numbers)
and let them choose; explain each fix with its finding's `why`. Build end to end only when they ask for that.

1. At the start of a session: `prometheus update` (the newest harness and this skill), then `prometheus doctor`. If KiCad, Java or FreeRouting is missing, run `prometheus setup`
   (FreeRouting, no root) and show the user the setup guide (`docs/setup.md`, https://github.com/wilsonchenghy/prometheus-pcb-harness/blob/main/docs/setup.md) for the rest; install system software only with their
   approval: ask first, recommending they be installed. Only if they cannot be, or nobody answers, go on: a run still gives a board (the
   built-in router) and lists what it skipped in `result.json` (`skipped`); tell the user.
2. Write `board.py` with a `build()` returning a `pcb.Board` (outline, parts with real KiCad footprints and pin
   numbers from the datasheet, each assumed pin and chosen value cited with `cite={"page", "quote"}` (AGENTS.md),
   nets with their current, pours), or start from a KiCad netlist (`.net`); for a revision of a finished KiCad
   board, `prometheus import design.kicad_pcb rev2/` writes that board.py (AGENTS.md). Place
   every IC with its KiCad symbol (`symbol="Lib:Name"`; `prometheus part Lib:Name` shows it) and wire its pins by
   name: pins are then checked against pads both ways and an ERC runs. Place
   connectors, buttons and mounting holes yourself; give other parts `at=None` for the placer.
3. On a new board, show the schematic first: `prometheus board.py out/ --stage schematic` writes it and opens it in
   the viewer, then stops. Wait for the engineer's go or changes before placing and routing.
4. Put what the board must meet in `req.json` (size, nets' current, `near`, `pairs`, `edge`, holes: AGENTS.md, https://github.com/wilsonchenghy/prometheus-pcb-harness/blob/main/AGENTS.md) and
   run `prometheus board.py out/ --spec req.json --json`. Read `clean`, `findings` (kind, pins, at, hint) and
   `critic` in the JSON. `--stage critic` reviews in milliseconds; `--stage check` / `export` reuse the last
   placement and routing (`out/board_state.pkl`).
   For a person watching, `prometheus view out/` opens the board in their browser:
   layout, 3D, schematic, findings, and live progress with the time left; give them the address it prints.
5. Fix each finding by changing the design (positions, widths, outline), never by loosening rules; run again.
6. Done only when `clean` is true. Say which router ran (`router`) and whether KiCad's DRC ran (`kicad_drc.run`);
   name any pin numbers you assumed. The order files are in `out/`: `gerbers.zip`, `bom_jlc.csv`, `cpl.csv`,
   `cart/`, `report.html`, `kicad/`.

Details: `AGENTS.md`, `docs/getting-started.md`, `docs/ordering.md`, `docs/setup.md` in the Prometheus repository
(https://github.com/wilsonchenghy/prometheus-pcb-harness/blob/main/; read them there when Prometheus was installed with pip, not cloned).
