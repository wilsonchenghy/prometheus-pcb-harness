# Using Prometheus as an agent

The harness does the geometry; the agent and the engineer do the design. The agent writes the board script from the
engineer's requirements, runs it, explains the findings and fixes them with the engineer until the run is clean.

## Working with an engineer

The harness is there to make an engineer faster and better at board design, not to take the board out of their
hands. Unless they ask you to just build it, work with them:

1. **Their requirements first.** Before writing parts, ask for what only they know: input voltage and current, the
   output and its load, the board's size or enclosure, connectors and where they go, layers, the board house, and
   anything that must be near something else. Write the answers into `req.json` and show it to them: it is their
   spec, and every check is measured against it.
2. **Stop at the decisions.** Show the placement (`--stage place`, the viewer) before routing; show the findings
   before fixing them. A part swapped for stock, a value chosen from a datasheet's equations, a pin number read off
   a drawing, an LCSC number the run looked up (`lcsc_auto`): say each one, why, and the alternative, and let them
   decide.
3. **Explain each fix.** Every finding and critic note carries `why`: the engineering reason the rule exists. When
   you change the design for one, say what you changed and that reason, so the next board they draw has it
   already.
4. **Leave them the controls.** Prefer edits they can read in `board.py` (a position, a width, a requirement) over
   clever code; open the result in KiCad (`out/kicad/<name>.kicad_pro`) when they want to adjust by hand, and rerun
   from their edits.

## The loop

0. **Check the machine once**: `prometheus doctor`. Missing KiCad, Java or FreeRouting? `prometheus setup` adds
   FreeRouting (no root); for the rest show the user `docs/setup.md` and install only with their approval. Each is
   optional: without them the harness uses its own router and checks (see the table in `docs/setup.md`).
1. **Write `board.py`** with a `build()` that returns a `pcb.Board`: the outline, the parts (`place(ref,
   "Lib:Footprint", at, rot, value, mpn, lcsc=...)`; `at=None` lets the placer choose), the nets with their
   current (`net(name, pins, amps=...)`), the pours. Or start from a KiCad netlist (`.net`).
2. **Run** `prometheus board.py out/ --json`. It places what has no position, routes, runs the design check and
   the house check, and writes the order files. Stdout is one JSON object; it is also saved as `out/result.json`.
3. **Read `clean`.** If true, stop: `out/` holds the Gerbers, assembly files, parts cart and `report.html`.
4. **Otherwise read `findings`.** Each has `kind`, `pins`, `at` (mm) and a `hint`:

   | kind | usual edit |
   |---|---|
   | `unreached`, `split` | move the named parts (or fix their position in `placement` from the result), or route that net earlier |
   | `short`, `clearance` | move or re-route one of the two nets |
   | `current`, `voltage drop` | set `width=` / `amps=` on the net, or widen the pour |
   | `drill`, `edge` | move vias or holes; larger drills with `min_drill=` |
   | `placement` | fix a position or a pin name (`check_placement()` is instant: run it first) |

5. **Edit and run again.** Routes are cached per design, so a run after a small edit only re-routes what moved.

## LCSC numbers

A full run looks up an LCSC number (JLCPCB assembly) for every part without one: the MPN itself, else a resistor's
exact value at 1 % or a capacitor's value at no less than its voltage, in the footprint's size; basic parts first,
always in stock. It prints what it found and `result.json` lists it under `lcsc_auto`; a part it could not match
stays blank (the critic lists it): give that one an MPN or `lcsc=`. `--no-lcsc` skips it (it is off when `CI` is set).

## Parts by symbol: pins by name, checked both ways, and an ERC

Place a chip with its KiCad symbol and wire it by pin name, not by remembered pin numbers:
`b.place("U1", None, None, 0, "DRV8833", symbol="Driver_Motor:DRV8833PWP")` (the footprint is the symbol's own
unless you give one), then `b.net("AIN2", [("U1", "AIN2"), ...])`; a name used by several pins (GND) takes them all.
`prometheus part Driver_Motor:DRV8833PWP` prints the pin table, the datasheet link and the footprint. The
instant placement check then matches every symbol pin to a pad and every pad to a pin, and the critic runs an ERC
on the pins' electrical types (an input left open, a power pin unconnected or unsupplied, two outputs tied, a
no-connect pin wired). Prefer this for every IC: a wrong pin number is the one mistake the other checks cannot see.

## Requirements and one stage at a time

Write what the board must meet in a requirements file and pass `--spec req.json`: `size_mm`, `layers`, `house`,
`nets` (`{"VBAT": {"amps": 2}}`), `pairs` (`[["CANH", "CANL", 2.0]]`: matched in length), `near`
(`[["C1", "U1", 3.0]]`: this part within that distance of that one, pad to pad), `edge` (connectors at the edge),
`holes`, `assembly`, `max_parts`. The critic reviews the board against it, and against the usual review rules
(decoupling by its pins, crystal by its chip, connectors at the edge, USB pairs, antenna keep-out, and, when `size_mm`
is not given, a board far larger than its parts: an error, shrink the outline to the size it names), in
milliseconds; an `error` makes the run not clean. The placer honours `near` too: each part held near another is
placed right after it and moved within its distance if it ends up further.

`--stage` runs one step: `critic` (the review alone, instant: run it after every edit to the script), `place`,
`route` (place and route, then the checks), `check`, `export` (order files and KiCad's DRC). Each run saves the
placed and routed board as `out/board_state.pkl`; `check`, `export` and `critic` start from it when the design is
unchanged, so they take seconds. A differential pair over its skew is length-tuned after routing (a serpentine on
the shorter net, kept only when the check finds nothing new).

## Showing the user

Every run opens a local page in the browser by itself (or run `prometheus view out/` after): the layout, a 3D view of
KiCad's part models, the schematic and the findings, and live progress with an estimate of the time left. It runs
in the background, so the command still returns; later runs into the same folder reuse the page. Give the user the
address it prints (`viewer: ...` on stderr). Pass `--no-view` in unattended runs (it is off when `CI` is set).

## KiCad's DRC

When KiCad 9+ is installed (`kicad-cli` on the PATH), every run also checks the exported board with KiCad's own
DRC and its findings come back with `stage: "kicad"`. When it is not, `result.json` says
`"kicad_drc": {"run": false, "skipped": ..., "install": ...}` with the install command for the system: tell the
user, do not install system software unasked. `--kicad-drc require` makes a missing KiCad a failure.

## A part KiCad has no footprint for: from its drawing

When the user pastes a package drawing (an image or a datasheet page), make the footprint with `fpgen.py`, never by
placing pads by hand:

1. If the datasheet has a recommended land pattern ("example board layout", "land pattern"), use it: family
   `pads`, each land's number, centre and size as drawn. The maker's land beats any formula.
2. Otherwise read the package outline into a spec (`prometheus footprint template:qfn` prints one; every family:
   soic, sot23, qfp, qfn, dfn, bga, tht, pads). Give each dimension as the drawing does, `[min, max]`: span (E),
   body (D x E1), lead length (L), lead width (b), pitch (e), exposed pad (D2 x E2). A BSC / nominal value is one
   number. The lands then come from IPC-7351B.
3. `prometheus footprint spec.json --drawing drawing.png --symbol Lib:Name` writes `footprints/<Lib>.pretty/<Name>.kicad_mod`
   beside the board script (the harness reads that folder first), checks it (pin count, the symbol's pins, pad gaps
   against the house, pitch, the land past the toe and inside the heel, the exposed pad's clearance, KiCad and the
   harness reading it back) and opens a review page: the drawing, the land pattern with its dimensions, KiCad's
   render, the checks.
4. Show the user the review page and ask them to compare it with the drawing before the board uses the footprint:
   the checks cannot see a dimension read wrong off the drawing. Say which dimensions you read and any you assumed.

## Altium and older KiCad

Every run also writes the KiCad project in KiCad 8's format: `out/kicad8/` and `out/kicad8.zip` (the board, the
schematic, the project). That is the format Altium Designer's KiCad import reads (File > Import Wizard > KiCad
Design Files); KiCad 8 itself opens it, passes its DRC and reads the schematic's netlist back equal to the board's
(CI's kicad8 job). The Gerbers, drill files, BOM, pick-and-place and STEP need no conversion in any tool.

## The schematic

Every run writes `out/kicad/<name>.kicad_sch`, the board's schematic on KiCad's own symbols, and (with KiCad) reads
it back: a `schematic` finding means its netlist differs from the board's. Place ICs with `symbol=` so they are drawn
with their real pin names; a part without one is drawn on a generic symbol or a box of its pads. A design larger than A3 is
split into A3 pages under a cover sheet (`<name>.kicad_sch`, pages `<name>_<n>.kicad_sch`), its nets joined by global labels;
decoupling capacitors on one rail are drawn as a bank (one rail label, one ground).

## Rules for the agent

- Never claim a board is done unless `clean` is true. Report the findings that remain, as they are.
- The checks prove the copper joins the pins you named; they cannot know you named the right pins. Take pin
  numbers from the datasheet and say when one is assumed.
- Fix a finding by changing the design (positions, widths, the outline), not by loosening the rules.
- Place connectors, buttons and mounting holes yourself (the enclosure decides them); let the placer do the rest.
  For a dense fine-pitch board (0.4 mm QFN), place the chip and its decoupling yourself too.
