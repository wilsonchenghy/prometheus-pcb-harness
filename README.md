# pcb-harness

Design printed circuit boards in Python, the way you would design them by hand: parts on real KiCad footprints, nets,
routed copper and pours, then a first-order design review against IPC-2221 before anything goes to the fab.

```python
from pcb import Board
b = Board("power", [(0, 0), (60, 0), (60, 40), (0, 40)], thickness=1.6, copper_oz=2.0)
b.place("U1", "Package_SO:MSOP-10_3x3mm_P0.5mm", (40, 20), rot=90, value="LM5069", mpn="LM5069MM-2/NOPB")
b.net("VBUS", [("Q1", "5"), ("J2", "1")], amps=20)
b.track("VBUS", "F", [(10, 10), (40, 10)], width=3.0)
b.pour("GND", "B", [(0, 0), (60, 0), (60, 40), (0, 40)])
problems = b.check()        # connectivity, IPC-2221 current width, clearance, IR drop, drill spacing
b.plot("layers.png"); b.bom()
```

What it does (`pcb.py`, its docstring has the details):

- **Parts** on KiCad's footprint library (fetched once and cached), plus project footprints in `pcb_footprints/`.
- **Placement**: give parts no position (`at=None`) and `place_and_route(board)` places them (short wire, courtyards
  apart, clear of holes and keep-outs, connectors you placed left alone) and routes the board, placing again if the
  routing is not clean; `autoplace(board)` places only.
- **Copper**: tracks, pours, vias, stitching, 2 or 4 layers (inner planes), an autorouter (`autoroute(board)` for the
  whole board in one call; `route`, `route_jobs` per net) with
  routes cached by board hash (`cached_route`, optionally stored in git).
- **Checks**: every net connected, no shorts, track and pour widths for the current (IPC-2221, external and internal),
  clearance, voltage drop, drill spacing, courtyards, dead copper.
- **Fab rules**: `Board(..., rules="jlcpcb-2")` makes the router and the check keep a house's clearance, drill gap
  and edge gap; `b.pair("D+", "D-", max_skew=0.15)` keeps a differential pair matched in length.
- **Output**: a STEP of the assembled board (`export_step`, every part's KiCad 3D model by default) for the
  enclosure; Gerbers (copper, mask, paste for the stencil, silkscreen with the reference designators, outline) + Excellon drill, a KiCad project (`export_kicad`, then KiCad's own DRC with `kicad_drc` when
  KiCad is installed), 3D boards with the components' KiCad models (`parts`, needs `build123d`), BOM, layer plots.
- **Schematics** (`schem.py`): sheets drawn from the board's netlist and checked against it (needs `schemdraw`).
- **Fab and parts** (`fab.py`): design-rule presets for JLCPCB, PCBWay and OSH Park and a check against them
  (`fab_check`); the pick-and-place file and the JLCPCB assembly BOM; a parts cart (BOM-upload CSVs for DigiKey,
  Mouser and LCSC, and a `buy.html` with a search link per line at each shop); `fab.order()` writes it all.

From the command line, one script to an order folder (route if unrouted, check, house check, Gerbers zip, CPL, BOM,
parts cart, KiCad project, report; `--step` adds the 3D board; exit 0 when everything is clean):

```
python -m pcb_cli bench/esp32_sensor.py out/ --boards 10 --rules jlcpcb-2
pcb-harness bench/esp32_sensor.py out/          # the same, as installed by pip
```

Already have a KiCad schematic? Export its netlist (Eeschema: File > Export > Netlist) and give the board's size;
the parts are placed, routed to the fab's rules and written out for ordering (`pcb.from_netlist` in Python):

```
python -m pcb_cli examples/netlist/blinker_sch.net out/ --size 35x25 --at J1=4,12.5
```

New here? `docs/getting-started.md` walks a board from nothing to an order. `python bench/run.py` builds and times
every example board.

## Install

```
pip install -e .                 # core: numpy, shapely, pillow
pip install -e ".[dev]"          # + build123d (3D), schemdraw / cairosvg / pypdf (schematics), pytest
pytest -q                        # the tests
```

KiCad footprints and 3D models are downloaded on first use into `~/.cache/prometheus/parts` (override with
`PROMETHEUS_PARTS_CACHE`).

## Example boards

Each writes its fabrication set (layer plot, BOM, Gerbers, KiCad project, zips) to `boards/<name>/` by default, or to
the directory given as its argument. The KiCad DRC and renders at the end need KiCad 8+ (`kicad-cli` on the path). The results are in `boards/`.

| Board | Script | |
|---|---|---|
| `arm_pdb` | `examples/arm_pdb.py` (+ `arm_pdb_sheet.py` schematic) | a robot arm's power distribution board, 60 A, hot-swap |
| `twilight_fc` | `examples/twilight_fc.py` (+ `twilight_fc_sheet.py`) | a 4-layer flight controller around a Teensy 4.1 |
| `cube_main` | `examples/balancing_cube/cube_board.py` (+ sheet) | a balancing cube's main board: three motor channels, encoders |
| `ripple_main` | `examples/social_wheelbot/ripple_pcb.py` | a wheeled robot's power / CAN board, six motors |
| `qdd_main` | `examples/quadruped/qdd_power_board.py` | a quadruped's power / CAN board, twelve motors |
| `fpv_esc`, `fpv_fc` | `examples/fpv_quad/fpv_boards.py` | a 5-inch quadcopter's 4-in-1 ESC and STM32F405 flight controller |
| `controller_main` | `examples/game_controller/controller_pcb.py` | a game controller's board in its shell's outline (`controller_board.json`) |

Routed copper for some boards is kept in `examples/routes/` so they rebuild without re-routing. Pinouts marked
ASSUMED in the scripts are from datasheets as remembered: verify before you order boards.

`viewer/PCB.tsx` is a React component for showing a board's layers in a web app (kept for reference; it expects the
host app's API).

## Licence

Apache-2.0 (`LICENSE`). KiCad's footprint and 3D-model libraries, which this downloads, are CC-BY-SA 4.0 with the
KiCad library exception: designs that use them are not bound by the share-alike terms.
