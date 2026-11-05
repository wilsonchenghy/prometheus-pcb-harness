<img src="media/prometheus-icon.png" width="72" alt="Prometheus">

# Prometheus

You design. It places, routes and checks. You order.

[![Prometheus: the release film](media/prometheus.gif)](https://github.com/wilsonchenghy/pcb-harness/releases/download/v0.1.0/prometheus-v0.1.0.mp4)

*Click for the film with sound.*

## Install

Open [Claude Code](https://claude.com/claude-code) and type:

> Install https://github.com/wilsonchenghy/pcb-harness

That's it. Without an agent: `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/pcb-harness/main/install.sh | bash`

<sub>For the agent: run `curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/pcb-harness/main/install.sh | bash -s -- --skill`,
then `prometheus doctor`. KiCad and Java are optional; ask before installing them (`docs/setup.md`).</sub>

## Use

Ask for a board:

> Make me a 12 V to 5 V, 2 A buck converter board with screw terminals in and out and two M3 holes.

> Here is my KiCad netlist (design.net): place and route it for JLCPCB.

The agent asks for your requirements, then places, routes and checks the board. It opens the board in your browser
and explains each finding; you decide what changes. Out comes everything to order: Gerbers, BOM with LCSC numbers,
pick-and-place, a KiCad project (and a KiCad 8 copy for Altium).

## More

- [Getting started](docs/getting-started.md): a board from nothing to an order
- [Ordering](docs/ordering.md): which file goes where
- [Setup](docs/setup.md): KiCad, Java, FreeRouting, cloud sessions
- [Reference](docs/reference.md): the Python API, the checks, the example boards, development
- [AGENTS.md](AGENTS.md): how an agent works with you

Apache-2.0. KiCad's libraries, which this downloads, are CC-BY-SA 4.0 with the KiCad library exception.
