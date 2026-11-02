# Getting started: a board from nothing to an order, in one script

This walks through `bench/blinker.py`, a 555 LED blinker: eight parts on a 35 x 25 mm two-layer board. Run it first:

```
pip install -e ".[dev]"
python bench/blinker.py out/
```

It prints the routing problems and the check (both should be clean) and how long each stage took (under a second
once the footprints are cached; the first run downloads them, all at once, in a second or two).

## 1. The board and its parts

```python
from pcb import Board, route
OUT = [(0, 0), (35, 0), (35, 25), (0, 25)]
b = Board("blinker", OUT, thickness=1.6, copper_oz=1.0)        # mm, y up; corners rounded 3 mm by default
b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (17.0, 12.5), 0, "NE555", "TI NE555DR")
b.place("R1", "Resistor_SMD:R_0603_1608Metric", (11.0, 19.0), 0, "1k")
```

A footprint is KiCad's library name, `"Library:Footprint"`, exactly as KiCad shows it. `place(ref, footprint, (x, y),
rotation, value, mpn, lcsc=...)`: the manufacturer part number (`"Maker PART"`) and the LCSC number are what the
assembly BOM and the parts cart use. `side="B"` puts a part on the bottom.

Don't want to pick coordinates? Give a part no position, `b.place("R1", R06, None, 0, "1k")`, place what the
enclosure fixes (connectors, buttons, mounting holes) yourself, and after the nets call
`pcb.place_and_route(b)`: it places the rest (short wire, every courtyard on the board and apart, fine-pitch parts
given room to fan out), routes, and if the routing is not clean places again with more room, keeping the best. The
blinker from a bare parts list places and routes in about a second; the ESP32 board's 16 loose parts in under
ten seconds.

## 2. Nets

```python
b.net("VCC", [("J1", "1"), ("U1", "8"), ("U1", "4"), ("R1", "1"), ("C2", "1")], amps=0.1)
b.net("GND", [("J1", "2"), ("U1", "1"), ("C1", "2"), ("C2", "2"), ("D1", "1")])
b.pour("GND", "B", OUT)                                         # a ground plane on the bottom
```

Pins are `(ref, pad number)` as the footprint numbers them. `amps` sets how wide copper must be for the net (IPC-2221
at `max_rise_c`, default 20 C); `volts` sets what its voltage drop is measured against.

Before routing, `b.check_placement()` says in a moment whether every courtyard is on the board and clear of the
others, and whether every pin a net names exists - routing a misplaced board only wastes minutes.

## 3. Routing

```python
for n in ("VCC", "DIS", "THR", "OUT", "LED", "GND"):
    problems += route(b, n, width=0.3)
```

Or route the whole board in one call: `problems = pcb.autoroute(b)` fans out the fine-pitch pads, routes the
signal nets (most local first), then power, then the poured nets, each at its width (`b.net(..., width=0.4)`, else
what its current needs), in negotiated passes, and joins back any net a later one split. Route nets yourself when
you want control:

`route` connects a net's pads with tracks and vias on a grid (0.25 mm by default), keeping the clearance to every
other net. Route the nets with the least room first (fine-pitch signals), power next, ground last. A pad it cannot
reach is reported, never faked; a net that fails is retried from another starting point (the pour, the biggest pad),
and a pad boxed in by fine-pitch neighbours drops through a via. For a big board, `route_jobs` routes a list of nets
in negotiated passes, and `cached_route(b, do_route, tag, store="routes")` keeps the result (in git, if you like) so
a re-run does not route again.

You can also draw copper yourself: `b.track(net, "F", [(x, y), ...], width)`, `b.via(net, (x, y))`,
`b.pour(net, layer, polygon)`, `b.stitch(net, region)` (stitching vias), `b.hole((x, y), d=3.2)` (mounting holes).

## 4. Checks

```python
problems = b.check()                 # the design: every net joined, no shorts, clearance, current, voltage drop,
                                     # drill gaps, copper off the edge, courtyards, dangling track ends
import fab
problems += fab.fab_check(b, "jlcpcb-2")   # the house's process: track width, vias, drills, gaps, edge
```

Both return a list of plain sentences that say what and where; empty is clean. Better still, make the board
with the house's rules from the start, `Board("blinker", OUT, rules="jlcpcb-2")`: the router then keeps its
clearance, drill gap and edge gap, and `fab_check` uses them by default. For USB or CAN, `b.pair("DP", "DN",
max_skew=0.15)` has the check compare the pair's track lengths. The presets in `fab.RULES` are the
houses' standard capabilities, set conservatively; check the house's own page for your order.

## 5. Outputs

```python
b.plot("layers.png")                              # a picture of the copper
pcb.export_gerbers(b, "out/gerbers")              # what a fab needs: Gerbers + drill
pcb.export_kicad(b, "out/kicad")                  # a KiCad project, to open, inspect or keep editing in KiCad
fab.order(b, "out/order", boards=5)               # Gerbers zip, pick-and-place, JLCPCB BOM, and the parts cart
```

`fab.order` writes `cart/digikey.csv`, `cart/mouser.csv` and `cart/lcsc.csv` (upload one to that shop's BOM tool and
the cart fills in one go) and `cart/buy.html`, which lists every line with the quantity for your boards plus spares
and a search link at each shop. Nothing is bought or logged into for you.

With KiCad 8+ installed, `pcb.kicad_drc(path)` runs KiCad's own design-rule check on the exported board and
`pcb.render_kicad` renders it in 3D.

Or all of it in one command: `python -m pcb_cli bench/blinker.py out/` loads the board the script makes (its
`build()`, or a module-level `board` / `b`), routes it if it has no tracks, runs both checks, writes every file
above plus `report.html`, and exits 0 only when everything is clean - a natural CI step.

## From a KiCad schematic

`pcb.from_netlist("design.net", (50, 40), at={"J1": (4, 20, 90)}, amps={"VBUS": 2}, rules="jlcpcb-2")` reads the
netlist Eeschema exports (File > Export > Netlist): each part on the footprint the schematic gives it, with its
value, MPN (a field named MPN or Manufacturer_Part_Number, with Manufacturer) and LCSC number (a field named LCSC),
each net of two or more pins, GND poured on the bottom. Parts in `at` stay where you put them; then
`pcb.place_and_route(b)` does the rest. A part the schematic gives no footprint is listed in `b.import_notes`.

## 6. Schematics

`schem.py` draws schematic sheets from the board's netlist and checks them against the board; see
`examples/arm_pdb_sheet.py` for a full one.

## How fast

`python bench/run.py` builds every example board and times each stage. A small board (the blinker, the USB-C
regulator) goes from nothing to Gerbers in about a second; the 199-part four-layer flight controller (Twilight) in
under a minute with its routes cached.
