# Reference

Everything the harness does, how far it has been taken, and how to work on it. Start with the [README](../README.md).

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

- **Parts** on KiCad's footprint library (fetched once and cached), plus project footprints in `src/pcb_footprints/`.
- **Placement**: give parts no position (`at=None`) and `place_and_route(board)` places them (short wire, courtyards
  apart, clear of holes and keep-outs, connectors you placed left alone) and routes the board, placing again if the
  routing is not clean; `autoplace(board)` places only.
- **Copper**: tracks, pours, vias, stitching, 2 or 4 layers (inner planes), an autorouter (`autoroute(board)` for the
  whole board in one call; `route`, `route_jobs` per net) with
  routes cached by board hash (`cached_route`, optionally stored in git).
- **Checks**: every net connected, no shorts, track and pour widths for the current (IPC-2221, external and internal;
  each track for the pins it serves, so a branch to a pin given `net(..., loads=)` or to a small decoupling
  capacitor may be narrow: `Board.branch_amps`),
  clearance, voltage drop, drill spacing, courtyards, dead copper.
- **Fab rules**: `Board(..., rules="jlcpcb-2")` makes the router and the check keep a house's clearance, drill gap
  and edge gap; `b.pair("D+", "D-", max_skew=0.15)` keeps a differential pair matched in length.
- **Output**: a STEP of the assembled board (`export_step`, every part's KiCad 3D model by default) for the
  enclosure; Gerbers (copper, mask, paste for the stencil, silkscreen with the reference designators, outline) + Excellon drill, a KiCad project (`export_kicad`, then KiCad's own DRC with `kicad_drc` when
  KiCad is installed), 3D boards with the components' KiCad models (`parts`, needs `build123d`), BOM, layer plots.
- **Footprints from a package drawing** (`fpgen.py`, `prometheus footprint spec.json --drawing d.png`): the drawing's
  dimensions in, the land pattern from IPC-7351B out (SOIC / SOP / SOT, QFP, QFN / DFN with exposed pad, BGA,
  through-hole grids, or the maker's own land table), checked and shown beside the drawing for a person to confirm.
  Within 0.1 mm of KiCad's own footprints for the same packages.
- **Schematics**: every run writes a KiCad schematic (`kicad_sch.py`, `out/kicad/<name>.kicad_sch`) on KiCad's own
  symbols, grouped into a block per IC, every pin wired to a net label, nothing overlapping; KiCad reads it back and
  its netlist must be the board's. Hand-drawn sheets in an IC's datasheet form: `schem.py` (needs `schemdraw`).
- **Altium**: every run also writes the KiCad project in KiCad 8's format (`out/kicad8/`, `out/kicad8.zip`), the
  format Altium Designer's KiCad import reads (File > Import Wizard > KiCad Design Files); KiCad 8 opens it clean.
- **Fab and parts** (`fab.py`): design-rule presets for JLCPCB, PCBWay and OSH Park and a check against them
  (`fab_check`); the pick-and-place file and the JLCPCB assembly BOM; a parts cart (BOM-upload CSVs for DigiKey,
  Mouser and LCSC, and a `buy.html` with a search link per line at each shop); `fab.order()` writes it all.

- **Requirements and stages**: `--spec req.json` (size, currents, `near`, `pairs`, `edge`, holes) is what the
  critic reviews the board against in milliseconds and what the placer holds parts to; `--stage critic | place |
  route | check | export` runs one step, starting from the last run's saved board.

- **Parts and ERC**: `place(..., symbol="Driver_Motor:DRV8833PWP")` takes a part's KiCad symbol: nets name its
  pins by name, every pin is matched to a pad and every pad to a pin, and an ERC runs on the pin types;
  `prometheus part Lib:Symbol` shows a symbol's pins and footprint and holds them against the maker's datasheet's pin
  table (`--datasheets` does it for every part of a run). The motor driver, CAN node, LED ring and blinker in
  `bench/` are wired this way.
- **Viewer**: every run opens the board in your browser (the stage, the try it is on, about how long is left)
  and leaves it open after, one tab per output folder (`--no-view`, or `CI` set, turns it off); `prometheus view out/`
  opens a finished run.
  Layout with layers to switch, an interactive 3D view (and KiCad's photoreal render when KiCad is installed), a
  the KiCad schematic as KiCad draws it, and every finding: click one and the board zooms to it.

From the command line, one script to an order folder (route if unrouted, check, house check, Gerbers zip, CPL, BOM,
parts cart, KiCad project, report; `--step` adds the 3D board; exit 0 when everything is clean):

```
python -m pcb_cli bench/esp32_sensor.py out/ --boards 10 --rules jlcpcb-2
prometheus bench/esp32_sensor.py out/          # the same, as installed by pip
```

Already have a KiCad schematic? Export its netlist (Eeschema: File > Export > Netlist) and give the board's size;
the parts are placed, routed to the fab's rules and written out for ordering (`pcb.from_netlist` in Python):

```
python -m pcb_cli examples/netlist/blinker_sch.net out/ --size 35x25 --at J1=4,12.5
```

Setting up a machine or an agent's environment (KiCad, Java, FreeRouting, and what happens without them)?
`docs/setup.md`; `prometheus doctor` says what a machine has. Driving it from an agent? `AGENTS.md` gives the loop (write the script, run with `--json`, fix the findings).
New here? `docs/getting-started.md` walks a board from nothing to an order; `docs/ordering.md` says which file
goes where when you order. `python bench/run.py` builds and times
every example board.

## Status

What a clean run means: the board's own design check and the fab house's rules both pass. Measured on this
repository's boards (`python bench/run.py`; `PCB_REROUTE=1` routes from scratch instead of loading the committed
routes):

| Board | Parts | Layers | From scratch | Result |
|---|---|---|---|---|
| blinker (555 + LED) | 8 | 2 | under 1 s, also placed automatically | clean (JLCPCB) |
| usb_ldo (USB-C to 3.3 V) | 10 | 2 | about 1 s, also placed automatically | clean (JLCPCB) |
| esp32_sensor | 21 | 2 | 4 s; 8 s placed automatically | clean (JLCPCB) |
| led_ring (12 WS2812B, round) | 31 | 2 | 14 s, placed, FreeRouting | clean, KiCad DRC clean |
| motor_driver (DRV8833, 2 oz) | 15 | 2 | 14 s, placed, FreeRouting | clean, KiCad DRC clean, its spec met |
| power_bank (LiPo, 2 A paths) | 25 | 2 | 13 s, placed, FreeRouting | clean, KiCad DRC clean, its spec met |
| can_node (STM32F103, CAN) | 24 | 2 | about 40 s, placed, FreeRouting, pair length-tuned | clean, KiCad DRC clean, its spec met |
| rp2040_board (0.4 mm QFN-56) | 34 | 2 | about 3.5 min, placed (clean on the 6th placement tried; tries run 4 at a time), FreeRouting through KiCad (without KiCad: not clean, 2 nets left) | clean, KiCad DRC clean |
| arm_pdb (60 A power) | 56 | 2 | about 80 s | clean (JLCPCB) |
| cube_main | 25 | 2 | about 60 s | clean (JLCPCB) |
| ripple_main | 67 | 2 | about 50 s | clean (JLCPCB) |
| qdd_main | 70 | 2 | about 55 s | clean (JLCPCB) |
| controller_main | 47 | 2 | about 55 s | clean (JLCPCB), KiCad DRC clean; its two rear screw holes at x = ±20.75, 0.25 mm inboard of where the shell had them, to keep 0.5 mm from the 7.2 mm cut-outs (move the shell's bosses to match) |
| twilight_fc | 199 | 4 | committed routes | clean (JLCPCB 4-layer), KiCad DRC clean |

KiCad's DRC finds no errors and no unconnected items on any of them; it still warns of a few dangling track ends
(three on the ESC, two on the FC: overshot stubs lying on tracks of their own net) and silkscreen overlaps. Not
yet: CPL rotations are KiCad's (check them in the house's placement preview), and placement is for small boards:
place big or dense boards yourself.

## Install

**One command** (Python 3.10+):

```
pip install "prometheus-pcb @ git+https://github.com/wilsonchenghy/prometheus-pcb-harness"
prometheus setup        # adds FreeRouting (needs Java), then reports what the machine has
```

or `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/prometheus-pcb-harness/main/install.sh | bash`, which does
both. KiCad 9 (for KiCad's own DRC) is a system install: `prometheus doctor` says whether it is there, and
`docs/setup.md` has the commands per OS. Everything beyond Python is optional; see the table there for what you
get without it.

**With Claude Code or another AI agent** (on your machine or in a cloud session): give it the link and the job.

> Install https://github.com/wilsonchenghy/prometheus-pcb-harness (follow its docs/setup.md), then design a board that ...

In a Claude Code cloud session, add the repository to the session (or let the agent clone it). The repository
carries a Claude Code skill (`.claude/skills/prometheus`) and `AGENTS.md`, so the agent follows the harness's
loop: check the machine, write the board script, run, fix the findings until clean. The agent will ask before
installing KiCad (it needs root); approve it, or in auto mode switch to *Accept edits* first.

### Try it in Claude Code on your machine

1. Install Prometheus and its skill (Python 3.10+; Java 17+, FreeRouting and KiCad 9 to route and run KiCad's DRC):

   ```
   git clone https://github.com/wilsonchenghy/prometheus-pcb-harness && cd prometheus-pcb-harness
   bash install.sh --skill       # the package, FreeRouting, and the skill in ~/.claude/skills/prometheus
   prometheus doctor                   # what the machine has, and what each missing piece would add
   ```

   Without a clone: `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/prometheus-pcb-harness/main/install.sh | bash -s -- --skill`.
   Inside a clone the skill is there anyway (`.claude/skills/prometheus`); `--skill` makes it work in any folder.
2. Start Claude Code in an empty folder (`mkdir myboard && cd myboard && claude`) and ask for a board. To test:

   > Make me a 12 V to 5 V, 2 A buck converter board with an AP63205, screw terminals in and out, and two M3
   > holes. Run it with prometheus, open the viewer, and fix whatever it finds until it is clean.

   > Here is my KiCad netlist (design.net): place and route it on a 50 x 40 mm two-layer board for JLCPCB.

   > Check the pins of Driver_Motor:DRV8833PWP against its datasheet.

   Claude writes `board.py`, runs `prometheus board.py out/` (the board opens in your browser and shows the run,
   its checks and the time left), reads `out/result.json` and changes the design until every check is clean.
3. What you get in `out/`: Gerbers and drill (`gerbers.zip`), the BOM and pick-and-place for JLCPCB assembly
   (`bom_jlc.csv`, `cpl.csv`), a parts cart (`cart/buy.html`), a KiCad project (`kicad/`), a review page
   (`report.html`) and `result.json`.
   `prometheus view out/` opens a finished run again.

Claude Code asks before it installs KiCad (it needs root): approve it, or run the commands from `docs/setup.md`
yourself. Everything works without it; only KiCad's own DRC is skipped, and the run says so.

**For a cloud or container environment** that every session starts from: put `bash install.sh` (and the KiCad
commands from `docs/setup.md`) in its setup script.

**From a clone, for development:**

```
pip install -e ".[dev]"          # + build123d (3D), schemdraw / cairosvg / pypdf (schematics), pytest, gerbonara, kiutils
pytest -q
```

KiCad footprints and 3D models are downloaded on first use into `~/.cache/prometheus/parts` (override with
`PROMETHEUS_PARTS_CACHE`).

## Example boards

Each board has its own folder in `examples/`: its script (and schematic sheet), its routed copper (`routes/`, kept in
git so it rebuilds without re-routing) and what it builds (`out/`: layer plot, BOM, Gerbers, KiCad project, renders).
Run the script to rebuild `out/`, or give it another folder. The KiCad DRC and renders at the end need KiCad 8+
(`kicad-cli` on the path).

| Folder | Script | |
|---|---|---|
| `examples/arm_pdb/` | `arm_pdb.py` (+ `arm_pdb_sheet.py` schematic) | a robot arm's power distribution board, 60 A, hot-swap |
| `examples/twilight_fc/` | `twilight_fc.py` (+ `twilight_fc_sheet.py`) | a 4-layer flight controller around a Teensy 4.1 |
| `examples/balancing_cube/` | `cube_board.py` (+ sheet) | a balancing cube's main board: three motor channels, encoders |
| `examples/social_wheelbot/` | `ripple_pcb.py` | a wheeled robot's power / CAN board, six motors |
| `examples/quadruped/` | `qdd_power_board.py` | a quadruped's power / CAN board, twelve motors |
| `examples/game_controller/` | `controller_pcb.py` | a game controller's board in its shell's outline (`controller_board.json`) |
| `examples/imu_bno085/` | `imu_bno085.py` (+ `req.json`, `routes/`; run with `prometheus`) | an 18 x 22 mm BNO085 IMU breakout: USB-C, 3.3 V LDO, I2C header |
| `examples/netlist/` | `blinker_sch.net` | a KiCad netlist to start a board from |

`examples/pcb_bodies.py` (3D bodies for parts KiCad has no model of) and `examples/export.py` are shared by the
scripts. Pinouts marked ASSUMED in the scripts are from datasheets as remembered: verify before you order boards.

## Contributing

`CONTRIBUTING.md` (set up, the tests and benches to run, the principles); `CHANGELOG.md` for what each release
holds.

## Licence

Apache-2.0 (`LICENSE`). KiCad's footprint and 3D-model libraries, which this downloads, are CC-BY-SA 4.0 with the
KiCad library exception: designs that use them are not bound by the share-alike terms.
