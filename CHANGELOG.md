# Changelog

## Unreleased

- Renamed **Zeus — AI PCB Harness**: the command is `zeus` (`pcb-harness` still works), the package `zeus-pcb`.
- `pcb-harness view out/` and `--view`: the board in a browser - layout with layers to switch, an interactive 3D
  view (three.js, shipped with the page; KiCad's photoreal render when KiCad is installed), a net-label schematic,
  the findings (click one: the board zooms to it) - and, during a run, its stage, the try it is on and about how
  long is left.
- Parts from KiCad's symbol library: wire a chip by pin name, every pin matched to a pad and every pad to a pin, an
  ERC on the pin types in the critic, `pcb-harness part`, and a Parts view drawing each pin's thread to its pad.
- The viewer: KiCad's own 3D export with every part's real model; a professional shell; light-drawn copper with
  current flowing on a focused net; a build-up sweep, a cursor light, a 4x loupe, parts that drop in and explode.
- Found by building boards as a new user: a mistyped footprint is named with the closest names KiCad has; a
  "within d mm" requirement is measured pad to pad; parts that do not fit stop the run at once instead of being
  routed round; via drops keep the board's edge gap; FreeRouting's edge slips are repaired; the critic stage no
  longer reports routing it has not done.
- The datasheet check reads more datasheets: numbered pin lists ("2- VOUT"), "4 GND" rows, the names datasheets give
  a pin otherwise (VOUT for KiCad's VO), other letter case (RS for Rs), a table continued on the next page; a pinout
  drawing is no longer taken for the table. Checked on DRV8833, SN65HVD230, AMS1117, AP63205, NE555 and LM75B.
- The CAN node is wired from KiCad's symbols (STM32F103, SN65HVD230, AMS1117) by pin name.
- Decoupling capacitors are held to a chip's supply pins (a symbol's power inputs), never to a connector's pin.
- The ERC lists a chip's unused pins in one note, not one note a pin.
- The release video is Zeus, with a scene of the viewer (74 s).

## v0.1.0 (2026-11-04)

The first release: a printed circuit board harness for people and AI agents. Describe a board in Python (or start
from a KiCad netlist), and it places, routes, checks and writes everything a board house needs to make and
assemble it.

**Design**
- Parts on real KiCad footprints (fetched on first use), nets with their current, pours, keep-outs, mounting
  holes, 2 or 4 layers, any board outline; a KiCad netlist (`.net`) as a starting point.
- Placement: `place_and_route` places the parts you leave without a position (short wire, decoupling held by its
  pins, crystals by their chip, the requirements' "within d mm" kept), routes, and tries other placements when the
  routing is not clean; tries run in parallel, and the same input always gives the same board.

**Routing**
- FreeRouting, the router KiCad users use, through KiCad's own Specctra export and import when KiCad is
  installed, with fan-out stubs for fine-pitch parts (0.4 mm QFN) and a clearance repair after it; the harness's
  own grid router when FreeRouting is not installed, and to finish the poured nets.
- Track widths from each net's current (IPC-2221), necking down only at a pin narrower than the track.
- Differential pairs length-tuned (a serpentine on the shorter net).

**Checks**
- The design check: every net joined (by KiCad's rules), no shorts, clearance, current, voltage drop, drill gaps,
  bare holes, courtyards, dangling copper.
- The board house's rules (JLCPCB 2 / 4 layer, PCBWay, OSH Park).
- KiCad 9's own DRC on the exported board, when KiCad is installed.
- A design critic against a requirements file (`--spec`: size, currents, distances, pairs, connectors at the edge,
  holes) and the usual review points, in milliseconds.

**Outputs**
- Gerbers and drill, pick-and-place, the JLCPCB assembly BOM, parts carts for DigiKey, Mouser and LCSC, a KiCad
  project, a review page, a STEP of the assembled board, schematics drawn from the netlist.

**For agents**
- `pcb-harness board.py out/ --json`: one JSON result with each finding's kind, pins, location and a hint;
  `--stage critic | place | route | check | export` runs one step from the last run's saved board.
- `pcb-harness doctor` and `pcb-harness setup`; a Claude Code skill and `AGENTS.md` in the repository.

**Boards** (`bench/`, `examples/`): eight bench boards placed and routed from scratch and clean, KiCad DRC
included (a blinker, a USB LDO, an ESP32 sensor, an addressable LED ring, a LiPo power bank, a dual motor driver,
a CAN node, an RP2040 board), and eight example boards from robot projects. Known open: the FPV 4-in-1 ESC's
ground is in three pieces, and the game controller's mounting holes sit 0.3 mm from its shell's cut-outs.

**Release video**: `media/release-video/` (a 67 s film drawn from the RP2040 board's real routing, with a
synthesised soundtrack); the rendered film is attached to this release.
