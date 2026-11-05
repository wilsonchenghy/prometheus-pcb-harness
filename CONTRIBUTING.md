# Contributing

Thank you for helping. Issues and pull requests are welcome: a bug with the board script that shows it, a part or
footprint the harness mishandles, a board house's rules, a faster or better router step.

## Set up

```
git clone https://github.com/wilsonchenghy/prometheus-pcb-harness && cd prometheus-pcb-harness
pip install -e ".[dev]"
prometheus setup          # FreeRouting (needs Java); KiCad 9 is optional, see docs/setup.md
pytest -q                  # about 30 s
```

## Before a pull request

- `pytest -q` passes.
- A change to placement or routing: run the bench boards from scratch and say what changed (time, clean or not):

  ```
  PCB_REROUTE=1 python bench/run.py led_ring power_bank motor_driver can_node     # a few minutes
  PCB_REROUTE=1 python bench/run.py                                              # all of them, longer
  python bench/fuzz.py 10                                                        # random designs: no crashes
  ```

  `PCB_REROUTE=1` ignores the routing caches, so the numbers are honest.
- A new rule in the design check or the critic: a test in `tests/test_pcb.py` that shows it firing and not firing.
- A new example or bench board: its pin numbers from the datasheet, and anything assumed marked as such.

## Principles

- **Fix the design, not the rules.** A check that fails is reported as it is; a fix that loosens a rule to get a
  clean run is not one.
- **Close to KiCad.** Where KiCad has a way (its DRC, its Specctra round trip, its footprints, its 3D models), the
  harness uses it rather than its own.
- **Optional extras stay optional.** Java, FreeRouting, KiCad, build123d: without each, the harness still runs and
  says what is missing and how to add it.
- **The same input, the same board.** Anything that depends on set order or the clock is a bug.
- **Findings are data.** Every problem names what, where (pins, mm) and, where it can, what to change.

## Code

Plain Python 3.10+, no build step. The main modules: `pcb.py` (the board, placement, routing, checks, exports),
`freerouting.py` (FreeRouting and the KiCad round trip), `fab.py` (house rules, order files, carts), `critic.py`
(the review against requirements), `pcb_cli.py` (the command line), `schem.py` (schematics). Keep lines under 120
characters and comments about why, not what.

## Licence

By contributing you agree that your contribution is licensed under the Apache License 2.0, as the project is.
