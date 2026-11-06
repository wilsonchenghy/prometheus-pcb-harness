# Changelog

## Unreleased

Found by building 26 boards (on branch claude/pcb-collection) and judging every one with KiCad 9's DRC, a random-board fuzz with KiCad as the
second judge (`bench/fuzz.py --kicad`), and an export/import round trip.

**Fixed**
- Exported boards that KiCad 9 could not open: KiCad's library now puts KiCad 10 `(point ...)` anchors into some
  footprints (LED_THT, TO-220, DIP). These are now left out, and model paths are written with
  `${KICAD9_3DMODEL_DIR}`.
- A footprint's own Edge.Cuts marks (a jack's notch, a panel pot's edge line) made KiCad call the outline malformed:
  they go to Dwgs.User, and the critic warns when such a mark is off the board's edge.
- KiCad's Specctra export (FreeRouting's input) failed without a word when the script had named parts H1, H2:
  mounting holes now take references no part has.
- `kicad_drc` on a board KiCad cannot read is a clear finding, not a missing drc.json.
- Scripts that draw some copper themselves:
  - `--stage check / export / critic` checked only the hand-drawn copper. They now start from the saved board.
  - Such a board is now kept in `routes/`.
  - With every part placed, the run now routes the rest.
  - The copper stays as drawn: the spur trim, chamfer, loop removal and rip-up no longer reshape it.
  - The placer keeps parts off it.
  - The earlier routing is no longer restored beside it.
- Vias:
  - The router's vias keep the hole gap from slots by their shape (USB-C shells).
  - neck_vias makes its track as wide as the net needs and brings along every track end in the via's ring.
  - The built-in router now runs neck_vias too.
  - tidy merges two vias of one net drilled into each other.
- FreeRouting fan-out stubs follow each pad's long axis; at the ends of an SSOP row they ran into the next pin.
- The built-in router's way out of a fine-pitch pin is no wider than the pin.
- FreeRouting runs are repeatable: the same input gives the same tracks and Gerbers.
- A saved routing (routes/, board_state.pkl) lacking the script's own copper is not reused.
- Viewer: no negative-width SVG on load.
- The fan-out keeps out of keep-outs.
- The schematic shorted facing pins (a dual pot's units end to end) and joined ground stubs across other pins.
- The house check of a 4-layer board without a preset uses jlcpcb-4.
- `req.json` `"holes": N` crashed the critic.
- The update notice was shown to a clone that is ahead of GitHub's main.
- `prometheus symbol` drew with a macOS path on Linux and missed the board's `footprints/`.

**Checks**
- A pad drilled by a mounting hole, and a mounting hole inside a courtyard, are now findings.
- Courtyards are KiCad's own shapes, and any overlap counts, as in KiCad's DRC.
- New critic warning, via current: a power path that changes layer through too few vias for its current.

**Import**
- Keeps an edge connector's overhang, each part's symbol (from the project's schematic) and the pins the schematic
  flags no-connect.

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
