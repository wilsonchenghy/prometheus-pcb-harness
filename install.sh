#!/usr/bin/env bash
# Prometheus: install the harness, fetch FreeRouting, and report what this machine has.
#   bash install.sh            (from a clone)
#   curl -fsSL https://raw.githubusercontent.com/wilsonchenghy/pcb-harness/main/install.sh | bash
# Installs no system software: KiCad and Java are listed by `prometheus doctor` with the commands to add them
# (docs/setup.md), for you to run or approve.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
PY="${PYTHON:-python3}"
if [ -n "$HERE" ] && [ -f "$HERE/pyproject.toml" ]; then
  "$PY" -m pip install --quiet -e "$HERE[3d,schematic]" || "$PY" -m pip install --quiet -e "$HERE"
else
  "$PY" -m pip install --quiet "prometheus-pcb[3d,schematic] @ git+https://github.com/wilsonchenghy/pcb-harness" \
    || "$PY" -m pip install --quiet "prometheus-pcb @ git+https://github.com/wilsonchenghy/pcb-harness"
fi
if command -v java >/dev/null 2>&1; then
  prometheus setup
else
  echo "Java not found: FreeRouting skipped (the built-in router is used). Install Java 17+ to add it: docs/setup.md"
  prometheus doctor
fi
echo
echo "Try it:  prometheus <your_board.py> out/      (or a KiCad netlist: prometheus design.net out/ --size 50x40)"
