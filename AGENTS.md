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
4. **Surface, ask, then act.** A choice with a cost or a trade-off (installing KiCad or Java, a part swapped for
   stock, the built-in router instead of FreeRouting, an output skipped) is put to the engineer as a question, with
   your recommendation, before you do it. Only when nobody answers (an unattended run) take the default that keeps
   the core working, and say afterwards what you chose and why (the run's `skipped` list says the same).
5. **Leave them the controls.** Prefer edits they can read in `board.py` (a position, a width, a requirement) over
   clever code; open the result in KiCad (`out/kicad/<name>.kicad_pro`) when they want to adjust by hand, and rerun
   from their edits.

## The loop

0. **Update, then check the machine**: `prometheus update` at the start of each session (the harness is fixed and
   improved often; every run also says when GitHub has a newer one), then `prometheus doctor`. Missing KiCad, Java or FreeRouting? `prometheus setup` adds
   FreeRouting (no root); for the rest show the user `docs/setup.md` and install only with their approval. Each is
   worth having: ask the user to install them (or to approve your doing it: `docs/setup.md` has the commands).
   Only when they cannot be installed, or nobody answers, go on without: a run then routes with the built-in router and lists that under
   `skipped` in `result.json` (why, how to add it), as it lists anything else it leaves out. Tell the user.
1. **Write `board.py`** with a `build()` that returns a `pcb.Board`: the outline, the parts (`place(ref,
   "Lib:Footprint", at, rot, value, mpn, lcsc=...)`; `at=None` lets the placer choose), the nets with their
   current (`net(name, pins, amps=...)`), the pours. Or start from a KiCad netlist (`.net`), or from a finished
   KiCad layout for a revision (below).
2. **Schematic first.** On a new board, run `prometheus board.py out/ --stage schematic` (seconds): it writes the
   schematic and shows it in the viewer, then stops. Show it to the engineer and wait for their go or their
   changes before any layout: a wrong part or net costs nothing to fix here.
3. **Run** `prometheus board.py out/ --json`. It places what has no position, routes, runs the design check and
   the house check, and writes the order files. Stdout is one JSON object; it is also saved as `out/result.json`.
4. **Read `clean`.** If true, stop: `out/` holds the Gerbers, assembly files, parts cart and `report.html`.
5. **Otherwise read `findings`.** Each has `kind`, `pins`, `at` (mm) and a `hint`:

   | kind | usual edit |
   |---|---|
   | `unreached`, `split` | move the named parts (or fix their position in `placement` from the result), or route that net earlier |
   | `short`, `clearance` | move or re-route one of the two nets |
   | `current`, `voltage drop` | set `width=` / `amps=` on the net, or widen the pour |
   | `drill`, `edge` | move vias or holes; larger drills with `min_drill=` |
   | `placement` | fix a position or a pin name (`check_placement()` is instant: run it first) |

6. **Edit and run again.** Routes are cached per design, so a run after a small edit only re-routes what moved.

## LCSC numbers

A full run looks up an LCSC number (JLCPCB assembly) for every part without one: the MPN itself, else a resistor's
exact value at 1 % or a capacitor's value at no less than its voltage, in the footprint's size; basic parts first,
always in stock. It prints what it found and `result.json` lists it under `lcsc_auto`; a part it could not match
stays blank (the critic lists it): give that one an MPN or `lcsc=`. For a pin header or a crystal it still could not match, the run then
searches by footprint and value ("1x6P 2.54mm pin header", "32.768kHz 3215") and lists the likeliest part under
`lcsc_suggested` (and as a critic note): never put in the BOM, since a search that loose can find a near miss. Say
each one to the engineer, and give `lcsc=` only for the ones they confirm. `--no-lcsc` skips it (it is off when `CI` is set).

## Parts by symbol: pins by name, checked both ways, and an ERC

Place a chip with its KiCad symbol and wire it by pin name, not by remembered pin numbers:
`b.place("U1", None, None, 0, "DRV8833", symbol="Driver_Motor:DRV8833PWP")` (the footprint is the symbol's own
unless you give one), then `b.net("AIN2", [("U1", "AIN2"), ...])`; a name used by several pins (GND) takes them all.
`prometheus part Driver_Motor:DRV8833PWP` prints the pin table, the datasheet link and the footprint. The
instant placement check then matches every symbol pin to a pad and every pad to a pin, and the critic runs an ERC
on the pins' electrical types (an input left open, a power pin unconnected or unsupplied, two outputs tied, a
no-connect pin wired). Prefer this for every IC: a wrong pin number is the one mistake the other checks cannot see.
A chip placed without its symbol is a critic error that names KiCad's symbol when one fits its pads exactly
(`parts.suggest_symbol`): add it. When KiCad has none, write the datasheet's pin table into a spec and run
`prometheus symbol spec.json` (symgen.py: the symbol in the board's `symbols/`, laid out as KiCad's are, and KiCad's
drawing of it); show the drawing to the engineer beside the datasheet before the board uses it. The harness ships the
ones made so far in `src/pcb_symbols/` (library `Prometheus`, each with its spec and the datasheet table it came from). A pin left open on purpose is `b.nc("U1", "SWCLK")` (KiCad's no-connect flag); a conflict the design has
on purpose, such as two tri-state outputs on one SPI line, is `b.erc_allow("MISO", "why")`, with the reason kept.

## Requirements and one stage at a time

Write what the board must meet in a requirements file and pass `--spec req.json`: `size_mm`, `layers`, `house`,
`nets` (`{"VBAT": {"amps": 2}}`), `pairs` (`[["CANH", "CANL", 2.0]]`: matched in length), `near`
(`[["C1", "U1", 3.0]]`: this part within that distance of that one, pad to pad), `edge` (connectors at the edge),
`holes`, `assembly`, `max_parts`. The critic reviews the board against it, and against the usual review rules
(decoupling by its pins, crystal by its chip, connectors at the edge, USB pairs, antenna keep-out, and, when `size_mm`
is not given, a board far larger than its parts: an error, shrink the outline to the size it names), in
milliseconds; an `error` makes the run not clean. The placer honours `near` too: each part held near another is
placed right after it and moved within its distance if it ends up further.

**Keep the routing in git:** make a `routes/` folder beside the board script. A clean run then writes its placement
and copper there (`<board>_<design>.json.gz`, the board's older one replaced), and a later run of the same design,
on any machine, loads it instead of placing and routing (seconds, the same copper). Commit it with the script;
`PCB_REROUTE=1` ignores it and routes from scratch.

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

## A revision of a KiCad layout

`prometheus import design.kicad_pcb rev2/` turns a finished KiCad board (KiCad 6 to 10, 2 or 4 layers) into
`rev2/board.py`: every part at its place on the design's own footprints (`rev2/footprints/`) with its designator
where the designer put it, the nets, the zones as pours (higher-priority zones carved out), keep-outs, mounting holes
and slots, the outline with its cut-outs, the silkscreen text on both sides. The routed copper is in
`rev2/routes.json`; the logos and the silkscreen and mask drawings (into the Gerbers too) and the project's own rules
and DRC severities are in `rev2/artwork.json`. Run `prometheus rev2/board.py rev2/out/ --json` first, unchanged: it shows the
design as it stands, and its findings are the existing board's, to talk through with the engineer before changing
anything. Then make the change they ask for in `board.py` (move a part, add one, change a net's width) and run
again: nets whose parts did not move keep their copper exactly, the nets of moved parts are routed again. Each run
also writes the design back revised, `out/revised/<name>.kicad_pcb` (KiCad's own pcbnew; the import keeps the
original in `rev2/source/`): the designer's own file with only the moved, added or removed parts and the re-routed
nets changed, the rest (arcs, zones, net classes, each footprint's link to the schematic) theirs. Their top
sheet is revised with it: a part added is drawn there (a net label on each pin; a net the sheet does not name gets
a label at one of its pins too), a part taken away goes with the wires that led only to it, and KiCad's netlist of
the revised sheet is checked against the board (what still differs is printed, e.g. a net only on a sub-sheet: wire
it by hand). Their other sheets are copied unchanged. The
import prints what it left out (drawings on layers the board house does not make; boards over 4 layers are refused);
keep the `.kicad_pro` beside the board, or its rules are unknown.

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
- Cite the datasheet for every pin you assume and every value you choose from it (a decoupling capacitor, a
  pull-up, a feedback divider): `place(..., cite={"page": 12, "quote": "Place a 0.1 µF capacitor"})`,
  `net(..., cite=...)` or `b.cite(ref, what, value, page, quote, url=None)`. The quote is the datasheet's own
  words on that (1-based) page. With `--datasheets` the run checks each quote is on its page (a warning if not);
  result.json's `evidence` lists them and the viewer opens the datasheet there. A value with no page to cite is
  an assumption: say so.
- Fix a finding by changing the design (positions, widths, the outline), not by loosening the rules.
- Place connectors, buttons and mounting holes yourself (the enclosure decides them); let the placer do the rest.
  For a dense fine-pitch board (0.4 mm QFN), place the chip and its decoupling yourself too.
