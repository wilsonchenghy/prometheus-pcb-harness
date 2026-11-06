# How the boards in this folder are made

Each board is `boards/NN_name/` with:

- `board.py`: a `build()` returning a `pcb.Board`; docstring says what the board is, its difficulty (1-5), layers and
  size, and every pin number or value that was assumed rather than read from a symbol/datasheet.
- `req.json`: the requirements (`size_mm`, `layers`, `house`, `nets` amps, `near` decoupling, `pairs`, `edge`).
- `routes/`: the cached placement and copper (written by a clean run; create the folder before the run).
- `out/`: only `gerbers.zip`, `bom_jlc.csv`, `cpl.csv`, `layers.png`, `result.json` are kept in git.

Run: `cd boards/NN_name && mkdir -p routes && prometheus board.py out/ --spec req.json --json --no-view > /dev/null 2> err.txt`
then read `out/result.json` (`clean`, `findings`, `critic`). Fast review after each edit: `--stage critic`.

Rules learned so far:

- Every IC is placed with `symbol="Lib:Name"` and wired by pin name; check it with `prometheus part Lib:Name`.
- `b.net(name, ...)` called twice with the same name REPLACES the net: list every pin of a net in one call.
- Unused IC pins: `b.nc(ref, "PIN", ...)`. USB-C SBU pins: `b.nc("J1", "SBU1", "SBU2")`.
- Place connectors, switches, holes and the chip itself; give decoupling caps a fixed spot by their supply pin
  (the critic wants a cap within 4 mm of each supply pin). Let the placer do the rest (`at=None`).
- KiCad is not installed here: the built-in router runs (`router: harness`) and there is no KiCad DRC. "via neck"
  warnings from the built-in router are tolerated; errors are not. A board is done only when `clean` is true.
- Pours: GND on B (and F) over the outline.
- To find a KiCad symbol's exact name, list its library on GitLab:
  `curl -s "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2Fkicad-symbols/repository/tree?path=Relay.kicad_symdir&per_page=100&page=1"`
  (footprints: project `kicad%2Flibraries%2Fkicad-footprints`, path `Relay_THT.pretty`).
- A run reuses `out/board_state.pkl` for nets it thinks unchanged: after hand-drawn copper changes, delete it and
  run with `PCB_REROUTE=1`.
- USB-C receptacle at more than ~2 A: copper wider than ~0.52 mm does not fit beside its 0.5 mm-pitch pins; draw
  VBUS by hand (see 03_usbc_power_breakout) or rate the board lower.
- A board with any `b.track()` in `build()` is never written to `routes/` (pcb_cli only caches boards it routed
  from nothing: `store = ... if not b.tracks`); say so in the docstring and leave `routes/` empty.
- 0.65 mm-pitch power ICs (DRV8833 HTSSOP): the built-in router cannot bring a multi-amp feed or the pump/VINT caps
  in between the pins. Draw the power side by hand: the feed straight along the pin's own y, wide, necking to 0.4 mm
  for its last <1.5 mm (`NECK_LEN`, the pin-entry exemption), caps with one pad sitting on the bus, short tracks
  from the GND/ISEN pins into the exposed pad (also <1.5 mm long, or they count as too narrow for the net's amps),
  and signal escapes as short stubs that clear the caps; the router finishes from the stubs.
- A low-current pin on a high-current net (INA219 IN+ on the shunt's 3 A side) gets routed at the net's full width
  and fails: give it its own net through the part that belongs there anyway (the 10 ohm input-filter resistor), and
  make the high-current nets F pours (`b.pour("VIN+", "F", rect)`) with the GND F pour kept to the other half.
- Terminal-block courtyards are deep (MKDS 5.08: ~5.7 mm behind the pads, PT 3.5: ~5 mm): check them with
  `b.courtyard(ref).bounds` before placing parts beside them; on a left edge (rot 270) the PT 3.5 needs x >= 5.2.
- Pin order decides crossings: pick the header pin order and which terminal pin is which from the IC's pin order
  (e.g. motor B wired OUT2 -> pin 1) and say so in the docstring.
