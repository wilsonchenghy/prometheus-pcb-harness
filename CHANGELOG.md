# Changelog

## Unreleased

**Added: the run, live in the viewer**
- The run writes each step as it happens to `out/live.jsonl`: each part the placer puts down (and a few frames a
  second of its annealing), each net's copper as the router finishes it, each try's start and end, the counts at
  each checkpoint (parts placed, nets joined, the length left to route as a ratsnest, tracks, vias, problems), each
  check as it starts and ends, and every line the run prints, tagged (`route`, `place`, `try`, `drc`, ...). Tries in
  their own processes write to it too. It is written while a viewer is open, or with `PCB_LIVE=1`.
- The viewer plays it:
  - The board fills in as the run goes: parts slide into place (those not placed yet wait beside the board),
    airwires show the nets still open, and the copper lands net by net with the newest net lit.
  - The AI's cursor goes where the run is working, its tag saying what: it rides in with each part placed, follows
    the router to the end of each net, and goes to a check as it starts.
  - The stage is on the board (`02 · PLACE & ROUTE · TRY 3/6 · ROUTING`).
  - The run log runs under the board, filterable and copyable; parts and nets in it are links.
  - The Live tab has the numbered steps with their times, a chip per try (click one to see its board), bars for
    parts placed and nets routed, the length left to route with its curve, and the checks as they run.
  - "Replay the run" plays a finished run again at 1x, 4x or 16x.
- `GET /api/events?since=` serves the events after a byte offset (whole lines only).
- A try that is not clean is said on the board before the next one starts. The notice reads like "try 1: 6 problems,
  route XTAL1: could not reach U1.7. Tearing it up and placing again with another seed.", the pins it failed at
  are ringed, its copper fades out as it is torn up, and only then are the parts placed again. When the parts moved
  after routing, it looked like the page had changed its mind. The try that is kept is said too.
- The AI's cursor and the parts move together. One animation loop draws the board and puts the cursor in the same
  frame (the cursor had a CSS easing of its own, so it ran ahead of or behind the part). Each part placed is a move
  of its own: the cursor reaches where the part waits, then carries it to its place, the part drawn under it. Events
  after a placement wait until the parts are down, so copper never lands before the parts it joins. A page that
  goes live after the placer finished plays what it missed. The agent's words (`prometheus say`) are a callout and
  no longer take the cursor away from the work.
- The verdict and the header say what the run is doing ("Placing.", "Checking.", "Writing the order files."), where
  they said "Routing." whatever the stage.
- `prometheus say out/ "text" --at U1` (or `pcb.say(text, ref=...)` in a board script): the agent's word to the engineer
  watching. It is a line in the run log tagged `ai`, and on the board the AI's cursor takes it to the part during a
  run; after the run it is a callout pointing at the part.
- Fixed on the way: a pan or zoom frame queued as the view was redrawn threw `Cannot read properties of null`.
- The viewer served the run's board only after the board for live edits was built: the background watcher rebuilt it
  from the script under a lock each time the run saved its state, and the page's first draw waited on that lock
  (13 s on the 133-part LED matrix). The run's own `board.json` is now served without the lock, and the watcher
  leaves the board alone while a run is going. A run with `PCB_LIVE=1` and no viewer of its own also writes the
  routed board for a viewer opened beside it.

Found by building 26 boards (on branch claude/pcb-collection) and judging every one with KiCad 9's DRC, a random-board fuzz with KiCad as the
second judge (`bench/fuzz.py --kicad`), and an export/import round trip.

**Fixed**
- `nudge_vias` let two drills of one net sit 0.254 mm apart while the design check holds every pair of drills to the
  house's hole gap (0.5 mm at JLCPCB): a GND via 0.33 mm from a GND USB-C shell's slot was left for the check to
  flag. One net's drills are now parted to the hole gap too, and a via may move up to 0.3 mm (was 0.15) to get
  there, wherever every clearance still holds. `neck_vias` lands a via it moves at the same gap, and `route_board`
  parts any via the later passes left against a drill once more.
- A chip's pin on the same net as its exposed pad (an RP2040's TESTEN beside its ground paddle) had to find its way
  out between 0.4 mm-pitch neighbours to reach the pour, and was sometimes left unreached (#99). Before routing,
  such a pin is now tied straight into the paddle by a track as wide as the pin (`pcb.tie_to_exposed_pad`, marked
  `tie`), when that track clears every other pad by the clearance.
- FreeRouting's prep no longer draws its fine-pitch escape stub out of a pin the script's own copper (or such a
  tie) already leaves: the extra stub beside the drawn copper only crowded the neighbouring pins.
- FreeRouting's prep drew its straight stub out of a fine-pitch pin into a keep-out (an ESP32-C3 module's GND pins
  into its own antenna keep-out, once KiCad 9's footprint made them read as fine-pitch pins). A stub that would
  cross a keep-out is left to FreeRouting.
- Footprints, symbols and 3D models were read from KiCad's library at `master`, which tracks KiCad 10 and keeps
  adding tokens KiCad 9 refuses (#98). They now come from the library release of the installed `kicad-cli`'s
  major (KiCad 9: the `9.0.9.1` tag; GitLab has no `9.0` branch), KiCad 9's when KiCad is not installed, and
  from master only for a file that release lacks. Each is cached under its release (`kicad_fp/9.0.9.1/...`), so
  a cache filled from master is not read as KiCad 9's; `PROMETHEUS_KICAD_LIB` names another tag. KiCad 9's
  symbol library has one file per library: a symbol is cut out of it. KiCad 9's USB-C parts number the shield
  `S1` where KiCad 10's say `SH`: a script wired to either name finds the pad. The 3D models some footprints
  lack (listed in #98) are still missing.
- `--stage route` placed the board again instead of routing the placement `--stage place` saved and the
  engineer saw. It now routes that placement when the design is unchanged (#100).
- `--datasheets`: a pin table that misspells a pin by one typing slip ("SHND" for SHDN, a swap counts) confirms
  that pin when the misspelling carries its number; the note quotes the datasheet's spelling. Another pin's name
  (BIN1 for AIN1) never counts (#100).
- USB-C VBUS above about 2 A: a feed wider than the receptacle's 0.6 mm VBUS pins, between GND and CC at 0.5 mm
  pitch, could not reach them, and the feed ended up narrow and failing the current check (#100). The built-in
  router (also FreeRouting's finisher) now leaves such a pin by a neck as wide as the pin, out past the pin row
  (every other one in the row longer, so two side by side do not end level), and runs the full width from
  there; the current check reads the neck as a pin entry. The first pin's escape is now also kept clear at the
  width it is drawn, not the feed's.
- Built-in router: when no grid cell past a fine-pitch pin's escape stub was clear at the net's full width, the
  router started from the nearest one anyway and the full-width track slipped under the clearance (a 2 A ground
  tie out of an SSOP pin, 0.19 mm from its neighbour; #96). That first step now stays at the stub's width, as a
  neck, and the track goes on full width from the first clear cell. The FE1.1s hub board is clean with it.
- The critic's board-size review ran only when the requirements gave no `size_mm`, so a board well inside its
  maximum but mostly empty passed unremarked. It now also warns, with `size_mm` given, when the parts cover under
  20 % of the board, and on any board when an edge has more than about 4 mm of bare board past the parts (with
  the size that would hold them). The error for a board far larger than its parts, without `size_mm`, is as before.
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

**A net's current no longer applies to every branch** (#97)
- `net(name, pins, amps=3, loads={("C1", "1"): 0.05, ("U2", "VS+"): 0.001})`: the most each named pin draws
  (by pad number or symbol pin name). Its branch is routed and checked for that load. The trunk between the other
  pins still takes the net's full `amps`. `None` for a pin puts it back to the full current.
- By default, a pin of a ceramic chip capacitor of size 0603 or smaller (ref `C..`, two pads, footprint `C_0603_...`)
  on a net of more than 0.5 A is a 0.5 A branch (`Board.CAP_BRANCH_AMPS`). Set it to `None` to turn this off.
  0805 and larger, or electrolytic, capacitors are not assumed: a regulator's input capacitor can carry amps of
  ripple.
- The current check works out each track's current from the pins it serves (`Board.branch_amps`):
  - A stretch of track whose removal splits the net carries at most what the smaller side can draw: the full current
    if that side has a full-current pin, else the sum of its pins' loads.
  - A stretch in a loop counts as carrying the full current, and so does every track of a net whose joints the graph
    cannot account for.
  - The voltage drop uses the same branch currents. The via-current warning no longer counts a pin with a load as
    an end of the power path.
- Routing:
  - The built-in router routes the trunk at the net's width first, then each low-current pin at its own width
    (`Board.pin_width`, at least 0.2 mm).
  - FreeRouting, which routes each net at one width, no longer gets the low-current pins. The harness routes them
    afterwards, fan-out stub included.
  - `ROUTER_VERSION` is now 8.

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
