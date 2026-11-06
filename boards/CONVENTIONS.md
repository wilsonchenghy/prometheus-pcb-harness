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
- KiCad 9, Java and FreeRouting are installed: runs route with FreeRouting (or reuse `routes/`) and KiCad's DRC
  judges every board (`kicad_drc.run: true`). "via neck" warnings are tolerated; errors are not. A board is done
  only when `clean` is true.
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
- Coordinates are y-up (the footprint frame is KiCad's mirrored in y): a 1xN pin header's pins run toward -y, and a
  Phoenix MKDS horizontal terminal's wire entry faces -y at rot 0, -x at rot 270, +x at rot 90 (pads 5 mm apart).
- `req.json` `holes` is `{"count": N, "d": 3.2}`, not a number (a bare number crashes the critic).
- `width=` on a net does not change the design key: a cached route in `routes/` keeps the old widths. Delete the
  `routes/*.json.gz` file and run again (`PCB_REROUTE=1` routes afresh but writes nothing to `routes/`).
- There is no via current check: the built-in router drops power nets to the bottom through single 0.3 mm vias.
  Look at `out/board_state.pkl` tracks/vias per power net; draw the power path yourself (`b.track`, `b.via(net,
  at, 0.4, 0.8)` in threes) when it does.
- Hand-drawn tracks of a net the router later fails near are torn up with the net in its rip-up passes; set
  `b.tracks[-1]["fan"] = True` after `b.track(...)` to keep them. The placer does not see them either: place by
  hand the parts round them, or they land on the copper (shorts).
- With every part placed and any `b.track` in the script, the CLI takes the board as routed by the script and routes
  nothing (everything "split"): leave one small part at `at=None`. Such a board is also never saved to `routes/`.
- A net's `amps` sets its router width everywhere: a 3.5 mm GND track at 8 A shorted against neighbours, and 1.0 mm
  tracks into 0.65 mm-pitch TSSOP pins broke clearance. Give a poured net's ties `width=0.4`, and a net that meets
  fine-pitch pins a width those pins can take (0.6 mm at TSSOP-8).
- Two pours of different nets on one layer must not touch (abutting polygons are a short): leave a gap or use
  `holes=`. A small top ground pad under a FET source plus vias carries its current to the bottom pour.
- An SMD ground pad joins the bottom pour only through a via: a ground fill on top with few vias shows up as a
  current "neck" on the top pour; put a 0.4 mm via beside each power capacitor's ground pad.
- A board whose parts are ALL fixed and which has a `b.track()` is not routed at all (pcb_cli routes only when a
  part is unplaced or there are no tracks): leave one passive to the placer (`at=None` + req.json `near`), as
  30_rfm95_lora does for its hand-drawn RF feed.
- The built-in router puts a 0.25-0.5 mm jog in a wide (1.2 mm) pad-to-pad line even when both pads share a y:
  draw a short RF feed by hand (`b.track(net, "F", [b.pad(a), b.pad(b)], w)`).
- Edge-mount SMA (Samtec SMA-J-P-H-ST-EM1): origin at x = W - 2.6 (its "PCB Edge" mark), rot 0 on a right edge,
  `overhang=True`. 50 ohm on 1.6 mm FR4 with the 0.2 mm pour clearance as the CPWG gap: 1.2 mm track.
- An RF module wants no top copper under its body: `b.keepout(body, layers="F")` (it also answers the critic's
  antenna warning for a module whose antenna is off-board).
- JST-SH SM04B horizontal (Qwiic): rot 0 opens toward -y (bottom edge); its MP pads reach the edge if the body is
  flush: origin >= 3.2 mm in from the edge, `overhang=True` for the courtyard.
- WS2812B grids: turn the LEDs 270 / 90 deg on alternate columns so DOUT -> DIN is a short diagonal; 5V as the F
  pour and GND as the B pour (the harness drops each GND pad's via itself); keep the cap column >= 2 mm off the
  edge or the 5 V pour gets a current neck there.
- An SOT-23-5 LDO with EN tied to VIN can wall in its own GND pin (pin 2) when its caps are fixed close by: let the
  placer place the in / out caps (near 3 mm) or turn the LDO so pins 1-3 face open board.
- Footprint pad coordinates from `pcb.footprint(fp)["pads"]` are already in the harness frame (y up, KiCad's y
  flipped): pin 1 of a QFP is at (-x, +y). Compute decoupling spots from them; `near` in req.json takes a pin
  (`["C1", "U1.4", 3.0]`).
- A part KiCad lacks (ESP32-C3-MINI-1): put a `.kicad_mod` in the board's `footprints/<Lib>.pretty/` and run
  `symgen.main([spec])` with `pcb.FP_DIRS` holding that folder (`prometheus symbol spec.json` alone does not look in
  the board's `footprints/` and fails on a local footprint). The courtyard is used as one bounding box: a radio
  module whose KiCad courtyard includes the antenna keep-out (a T, 43 mm wide) blocks everything beside it; cut the
  courtyard to the body and declare the keep-out with `b.keepout(...)`.
- The built-in fan-out ignores keep-outs: it runs stubs from a module's top-row ground pads into the antenna keep-out
  (a `copper inside the keep-out` finding). Pre-marking those pads in `b._fan` skips the fan-out (it runs only when
  `_fan` is empty); a 0.8 mm pitch module routes fine without it.
- Never add tracks (or call `b.fanout()`) in `build()`: a board with tracks is treated as already routed (no routing,
  no `routes/` cache).
- The built-in router routes signal nets most-local-first, so a long USB pair comes last and gets walled in. Put the
  USB connector near the MCU's D+/D- pins (a ~10 mm pair) and keep a free corner between them; it then routes clean.
- Set `PCB_WORKERS=2` on the shared machine: place_and_route otherwise forks up to 4 tries at once.

## Fixed in the harness on this branch (no workaround needed any more)

- A script that draws copper with `b.track()` is cached in `routes/`. `--stage check/export/critic` start from its
  saved board, and with every part placed the run still routes the rest. Its copper is left exactly as drawn: no
  spur trim, chamfer or rip-up. The placer keeps parts off it.
- A mounting hole on a pad, or inside a courtyard, is a finding. Courtyards are KiCad's shapes, and any overlap
  counts.
- KiCad 10 `(point ...)` items and footprint Edge.Cuts marks no longer break the exported board.
- `req.json` `"holes": 4` (just the count) works.
- The fan-out keeps out of keep-outs.
- Mounting-hole references never collide with parts named H1, H2 ...
- A 4-layer board without a preset is house-checked against jlcpcb-4.
