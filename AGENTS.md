# Using pcb-harness as an agent

The harness does the geometry; the agent does the design. An agent's job is to write a board script, run it,
read the findings, and edit the script until the run is clean.

## The loop

0. **Check the machine once**: `pcb-harness doctor`. Missing KiCad, Java or FreeRouting? `pcb-harness setup` adds
   FreeRouting (no root); for the rest show the user `docs/setup.md` and install only with their approval. Each is
   optional: without them the harness uses its own router and checks (see the table in `docs/setup.md`).
1. **Write `board.py`** with a `build()` that returns a `pcb.Board`: the outline, the parts (`place(ref,
   "Lib:Footprint", at, rot, value, mpn, lcsc=...)`; `at=None` lets the placer choose), the nets with their
   current (`net(name, pins, amps=...)`), the pours. Or start from a KiCad netlist (`.net`).
2. **Run** `pcb-harness board.py out/ --json`. It places what has no position, routes, runs the design check and
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

## Requirements and one stage at a time

Write what the board must meet in a requirements file and pass `--spec req.json`: `size_mm`, `layers`, `house`,
`nets` (`{"VBAT": {"amps": 2}}`), `pairs` (`[["CANH", "CANL", 2.0]]`: matched in length), `near`
(`[["C1", "U1", 3.0]]`: this part within that distance of that one, pad to pad), `edge` (connectors at the edge),
`holes`, `assembly`, `max_parts`. The critic reviews the board against it, and against the usual review rules
(decoupling by its pins, crystal by its chip, connectors at the edge, USB pairs, antenna keep-out), in
milliseconds; an `error` makes the run not clean. The placer honours `near` too: each part held near another is
placed right after it and moved within its distance if it ends up further.

`--stage` runs one step: `critic` (the review alone, instant: run it after every edit to the script), `place`,
`route` (place and route, then the checks), `check`, `export` (order files and KiCad's DRC). Each run saves the
placed and routed board as `out/board_state.pkl`; `check`, `export` and `critic` start from it when the design is
unchanged, so they take seconds. A differential pair over its skew is length-tuned after routing (a serpentine on
the shorter net, kept only when the check finds nothing new).

## Showing the user

For a person following along, add `--view` to the run (or run `pcb-harness view out/` after): a local page with
the layout, a 3D view, the schematic and the findings, and live progress with an estimate of the time left. Give
them the address it prints. Leave it off in unattended runs: with `--view` the command waits for Ctrl-C at the end.

## KiCad's DRC

When KiCad 9+ is installed (`kicad-cli` on the PATH), every run also checks the exported board with KiCad's own
DRC and its findings come back with `stage: "kicad"`. When it is not, `result.json` says
`"kicad_drc": {"run": false, "skipped": ..., "install": ...}` with the install command for the system: tell the
user, do not install system software unasked. `--kicad-drc require` makes a missing KiCad a failure.

## Rules for the agent

- Never claim a board is done unless `clean` is true. Report the findings that remain, as they are.
- The checks prove the copper joins the pins you named; they cannot know you named the right pins. Take pin
  numbers from the datasheet and say when one is assumed.
- Fix a finding by changing the design (positions, widths, the outline), not by loosening the rules.
- Place connectors, buttons and mounting holes yourself (the enclosure decides them); let the placer do the rest.
  For a dense fine-pitch board (0.4 mm QFN), place the chip and its decoupling yourself too.
