# Ordering boards: from `fab.order()` to a parcel

`python -m pcb_cli my_board.py out/ --boards 10` (or `fab.order(b, "out/", boards=10)`) writes everything a board
house and the parts shops need. What each file is for, and where it goes:

| File | What it is | Where it goes |
|---|---|---|
| `gerbers.zip` | copper, solder mask, paste, silkscreen and outline Gerbers + Excellon drills | the house's "add Gerber file" upload (JLCPCB, PCBWay, OSH Park all take the zip as is) |
| `bom_jlc.csv` | the assembly BOM in JLCPCB's columns (Comment, Designator, Footprint, LCSC Part #) | JLCPCB's PCB Assembly step, "BOM file" |
| `cpl.csv` | pick-and-place: each part's centre, rotation and side | JLCPCB's PCB Assembly step, "CPL file" |
| `cart/digikey.csv`, `cart/mouser.csv`, `cart/lcsc.csv` | the parts, quantities for your boards plus spares | that shop's BOM upload tool (DigiKey "BOM Manager", Mouser "BOM Tool", LCSC "BOM Tool"): the cart fills in one go |
| `cart/buy.html` | every line with a search link at each shop | open it in a browser when a line needs choosing by hand |
| `report.html` | the board picture, the design check, the house check, the parts | read it before you pay |
| `kicad/` (CLI) | the board as a KiCad project | open in KiCad to look, run KiCad's own DRC, or keep editing |

## Before you pay

1. `report.html` says **clean** under both checks. The design check is this harness's (connections, clearance,
   current, voltage drop, drills, courtyards); the house check is the house's published standard process.
2. Open `kicad/<name>.kicad_pcb` in KiCad 8 and run its DRC if you have KiCad: an independent second opinion.
3. Look at the Gerbers in the house's own viewer after upload: it is what they will make.
4. **Pinouts**: the harness checks that copper joins the pins you named; it cannot know that you named the right
   pins. Check every IC's net list against its datasheet.

## Assembly at JLCPCB

- Parts need an LCSC number (`b.place(..., lcsc="C25804")`, or an `LCSC` field in the KiCad schematic). Lines
  without one are left for you to fit, or to pick in JLCPCB's parts search during the order.
- JLCPCB "basic" parts carry no setup fee; "extended" parts carry a fee per part type. Prefer basic passives.
- **Rotations**: `cpl.csv` gives KiCad's rotations. JLCPCB's placement preview shows each part on its pads: fix any
  that are turned (often SOT-23 / SOT-223 / some ICs by 90 or 180 degrees) in that preview before confirming.
- The fiducials and tooling rails JLCPCB adds for assembly are added by them; you need not draw them.

## Which preset

`Board(..., rules=...)` and `fab_check(b, rules)` take one of `fab.RULES`:

| Preset | Track / space | Min drill | Notes |
|---|---|---|---|
| `jlcpcb-2` | 0.127 mm | 0.3 mm | JLCPCB 2-layer standard |
| `jlcpcb-4` | 0.09 mm | 0.3 mm | JLCPCB 4-layer (smaller vias possible at extra cost; kept conservative here) |
| `pcbway-std` | 0.1 mm | 0.2 mm | PCBWay standard: 4 mil tracks for fine-pitch parts on 2 layers |
| `oshpark-2` | 0.152 mm | 0.254 mm | OSH Park 2-layer |

The presets are conservative readings of each house's standard capabilities; houses revise them, so check the
house's capability page for your order.
