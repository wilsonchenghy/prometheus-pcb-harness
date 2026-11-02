# Using pcb-harness as an agent

The harness does the geometry; the agent does the design. An agent's job is to write a board script, run it,
read the findings, and edit the script until the run is clean.

## The loop

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
