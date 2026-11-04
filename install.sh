#!/usr/bin/env bash
# pcb-harness: install the harness, fetch FreeRouting, and report what this machine has.
#   bash install.sh            (from a clone)
#   curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/pcb-harness/main/install.sh | bash
# Installs no system software: KiCad and Java are listed by `pcb-harness doctor` with the commands to add them
# (docs/setup.md), for you to run or approve.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
PY="${PYTHON:-python3}"
if [ -n "$HERE" ] && [ -f "$HERE/pyproject.toml" ]; then
  "$PY" -m pip install --quiet -e "$HERE[3d,schematic]" || "$PY" -m pip install --quiet -e "$HERE"
else
  "$PY" -m pip install --quiet "pcb-harness[3d,schematic] @ git+https://github.com/wilsonchenghy/pcb-harness" \
    || "$PY" -m pip install --quiet "pcb-harness @ git+https://github.com/wilsonchenghy/pcb-harness"
fi
if command -v java >/dev/null 2>&1; then
  pcb-harness setup
else
  echo "Java not found: FreeRouting skipped (the built-in router is used). Install Java 17+ to add it: docs/setup.md"
  pcb-harness doctor
fi
echo
echo "Try it:  pcb-harness <your_board.py> out/      (or a KiCad netlist: pcb-harness design.net out/ --size 50x40)"
