# Changelog

## v0.1.0 (2026-11-05)

The first release of Prometheus, a printed circuit board harness for engineers and the AI agents that work with them.
Describe a board in Python (or start from a KiCad netlist), and it places, routes, checks and writes everything a
board house needs to make and assemble it - explaining each finding, so the engineer stays in charge of the design.

**Design**
- Parts on real KiCad footprints and symbols (fetched on first use), wired by pin name and checked pin to pad both
  ways with an ERC; nets with their current, pours, keep-outs, mounting holes, 2 or 4 layers, any outline; a KiCad
  netlist (`.net`) as a starting point, or a finished KiCad layout for a revision (`prometheus import`: its
  logos, cut-outs, slots, designators and rules kept): moving parts re-routes only their nets.
- Placement for the parts you leave without a position (decoupling held by its pins, crystals by their chip, the
  requirements' "within d mm" kept); routing tries other placements when it is not clean, in parallel and repeatably.
- Footprints from a package drawing (`prometheus footprint`): IPC-7351B lands, checked, and a review page to compare
  with the drawing.

**Routing**
- FreeRouting through KiCad's own Specctra export and import when KiCad is installed, with fan-out for fine-pitch
  parts; the harness's own router otherwise. Track widths from each net's current (IPC-2221); differential pairs
  length-tuned.

**Checks**
- The design check (every net joined, shorts, clearance, current, voltage drop, drills, courtyards), the board
  house's rules (JLCPCB, PCBWay, OSH Park) and KiCad's own DRC.
- A design critic against the engineer's requirements (`--spec`: size, currents, distances, pairs, connectors at
  the edge, holes) and the usual review points; a board far larger than its parts is flagged when no size is given.
- Every finding names its parts, pins and place, what to change, and why the rule matters.

**Outputs**
- Gerbers and drill, pick-and-place, the JLCPCB assembly BOM with LCSC numbers looked up from JLCPCB's catalogue,
  parts carts for DigiKey, Mouser and LCSC, a design summary, a STEP of the assembled board.
- A KiCad project with the board and its schematic on KiCad's own symbols: functional blocks, decoupling drawn as
  banks on one rail, a regulator's power stage wired, large designs on A3 pages joined by global labels; KiCad reads
  its netlist back equal to the board's.
- The same project in KiCad 8's format (`out/kicad8.zip`), for Altium Designer's KiCad import and older KiCad.

**The viewer** (`prometheus view out/`, opened by every run): the layout with its layers and nets, KiCad's 3D models,
KiCad's schematic page by page, each part's symbol beside its land pattern, the findings, and the run's progress.

**For agents and engineers**
- `prometheus board.py out/ --json`: one JSON result; `--stage critic | place | route | check | export` runs one
  step from the last run's saved board. `prometheus doctor` and `prometheus setup`.
- `AGENTS.md` and a Claude Code skill (`install.sh --skill`) that work with the engineer: their requirements first,
  a stop at each decision, each fix explained.

**Boards** (`bench/`, `examples/`): bench boards placed and routed from scratch and clean, KiCad DRC included, and
example boards from robot projects, among them a 199-part, 4-layer flight computer.
