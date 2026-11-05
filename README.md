<img src="media/prometheus-icon.png" width="72" alt="Prometheus">

# Prometheus

An AI agent that helps you make professional-grade PCBs.

Use it to:

- **Source parts**: real KiCad footprints, LCSC numbers and carts for JLCPCB, DigiKey and Mouser
- **Prototype a board**: from your requirements to Gerbers, BOM and pick-and-place
- **Import a design**: start from your KiCad board or your schematic's netlist
- **Do a revision**: ask for a change, and every check runs again on the whole board

[![Prometheus: the release film](media/prometheus.gif)](https://github.com/wilsonchenghy/prometheus-pcb-harness/releases/download/v0.1.0/prometheus-v0.1.0.mp4)

## Install

Open [Claude Code](https://claude.com/claude-code) and type:

> Install https://github.com/wilsonchenghy/prometheus-pcb-harness

That's it. Without an agent: `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/prometheus-pcb-harness/main/install.sh | bash`

<sub>For the agent: run `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/prometheus-pcb-harness/main/install.sh | bash -s -- --skill`,
then `prometheus doctor`. KiCad and Java are optional; ask before installing them (`docs/setup.md`).</sub>

## Use

Tell the agent what you need, in your own words:

> Find parts for a 12 V to 5 V, 2 A buck converter that JLCPCB can assemble.

> Make me a prototype board for it with screw terminals and two M3 holes.

> Here is my KiCad board (design.kicad_pcb): import it so we can do a revision.

> Rev 2: make the USB power traces wider and move J1 to the left edge.

It asks for your requirements first, shows the board in your browser, and explains each finding. You make the calls.
Every run gives you what you need to order: Gerbers, BOM, pick-and-place, and a KiCad project (with a KiCad 8 copy
for Altium).

## More

- [Getting started](docs/getting-started.md): a board from nothing to an order
- [Ordering](docs/ordering.md): which file goes where
- [Setup](docs/setup.md): KiCad, Java, FreeRouting, cloud sessions
- [Reference](docs/reference.md): the Python API, the checks, the example boards, development
- [AGENTS.md](AGENTS.md): how an agent works with you

Apache-2.0. KiCad's libraries, which this downloads, are CC-BY-SA 4.0 with the KiCad library exception.
