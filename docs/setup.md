# Setting up a machine (or an AI agent's environment) for the harness

The harness works with Python alone. KiCad and FreeRouting make it as good as it gets: KiCad's own design-rule
check judges every board, and FreeRouting (the router KiCad users use) routes the dense ones. Check what a
machine has at any time:

```
pcb-harness doctor
```

| Piece | What it gives | Without it |
|---|---|---|
| Python 3.10+ and `pip install pcb-harness` | everything: parts, placement, routing, the design check, the house check, Gerbers, KiCad project, BOM, cart | (required) |
| KiCad 9+ (`kicad-cli`, and its Python `pcbnew`) | KiCad's own DRC on every run; FreeRouting through KiCad's Specctra export | the harness's design check and house check only |
| Java 17+ and FreeRouting | the router KiCad users use; dense boards (0.4 mm pitch chips) route clean | the built-in grid router: fine for simple boards, dense fine-pitch boards leave pins unrouted |
| `pip install "pcb-harness[3d]"` (build123d) | STEP of the assembled board | no STEP |
| `pip install "pcb-harness[schematic]"` | schematic sheets | no sheets |

Each run's `result.json` says what it used (`"router"`, `"kicad_drc"`), so an agent knows what judged the board.

## Install

**FreeRouting** (any OS, needs Java):

```
pcb-harness setup          # downloads FreeRouting 2.0.1 into ~/.cache/prometheus/freerouting, checks its SHA-256
```

or put the jar anywhere and set `FREEROUTING_JAR=/path/to/freerouting.jar`.

**Java 17+**: Ubuntu / Debian `sudo apt install openjdk-21-jre-headless`; macOS `brew install openjdk`;
Windows: the Temurin installer from adoptium.net.

**KiCad 9+**:

- Ubuntu / Debian (the distribution's own package is often older than 9):
  ```
  sudo add-apt-repository ppa:kicad/kicad-9.0-releases && sudo apt update && sudo apt install kicad
  ```
  If `add-apt-repository` is missing or broken (minimal containers), add the source by hand:
  ```
  sudo curl -sS "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x245D5502FAD7A805" -o /etc/apt/keyrings/kicad.asc
  echo "deb [signed-by=/etc/apt/keyrings/kicad.asc] https://ppa.launchpadcontent.net/kicad/kicad-9.0-releases/ubuntu $(. /etc/os-release; echo $VERSION_CODENAME) main" | sudo tee /etc/apt/sources.list.d/kicad-9.list
  sudo apt update && sudo apt install --no-install-recommends kicad
  ```
- macOS: `brew install --cask kicad`, then put `kicad-cli` on the PATH
  (`/Applications/KiCad/KiCad.app/Contents/MacOS`).
- Windows: the installer from kicad.org; add KiCad's `bin` folder to the PATH.

KiCad's Python (`pcbnew`) is found automatically when it is importable from the Python running the harness, or
set `PCBNEW_PYTHON` to a Python that can import it (on Ubuntu, `/usr/bin/python3`).

## In an AI agent's environment (Claude Code and others)

The agent runs the same commands; what differs is whether it may install system software.

- **Tell the agent once**: "run `pcb-harness doctor`; install what is missing per docs/setup.md". Installing KiCad
  needs root and adds a package source, so many agents (and Claude Code's auto mode) ask first or refuse: that
  is the agent's safety check working, not a fault.
- **Claude Code**: approve the install when it asks. In a cloud session in auto mode the install is refused; switch
  the mode picker next to the prompt to *Accept edits* (then you approve each command), or add allow rules for
  the install commands to the repository's `.claude/settings.json`. FreeRouting needs no root:
  `pcb-harness setup` is enough.
- **Containers / CI**: bake KiCad 9, Java and the FreeRouting jar into the image, or run the commands above in the
  setup script, so every session starts complete.
- **No root at all**: the harness still works fully with its own checks and router; `pcb-harness setup` still adds
  FreeRouting if Java is there. Run KiCad's DRC later on any machine with KiCad, on the exported
  `out/kicad/<name>.kicad_pcb`.

An agent must never report a board as checked by KiCad unless `result.json` says `"kicad_drc": {"run": true}`.
