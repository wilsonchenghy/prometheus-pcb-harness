"""A KiCad symbol from a chip's pin table, for a part KiCad's library has no symbol for.

    prometheus symbol spec.json            # symbols/<Lib>.kicad_symdir/<Name>.kicad_sym beside it, and its drawing

The spec: {"lib", "name", "ref" ("U"), "footprint", "datasheet", "description", "pins": [[number, name, type], ...]}
with the pin table as the datasheet gives it and each pin's electrical type as KiCad names them (power_in,
power_out, input, output, bidirectional, tri_state, passive, open_collector, open_emitter, no_connect, free,
unspecified). The symbol is laid out as KiCad's own are: supplies on top, grounds at the bottom, inputs on the left,
outputs and I/O on the right, on the 2.54 mm grid. With a footprint, every pin must be a pad and every pad a pin.
Check the drawing against the datasheet before the board uses it: nothing else can see a pin name read wrong.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

TYPES = {"power_in", "power_out", "input", "output", "bidirectional", "tri_state", "passive", "open_collector",
         "open_emitter", "no_connect", "free", "unspecified"}
GROUND = re.compile(r"^(A|D|P)?(GND|VSS|VEE)\w*$|^(EP|PAD|EPAD|THERMAL)$|^GND\w*$", re.I)
G = 2.54


def _side(name: str, kind: str) -> str:
    if GROUND.match(name):
        return "bottom"
    if kind in ("power_in", "power_out"):
        return "top"
    if kind in ("input",):
        return "left"
    return "right"


def layout(pins: Sequence[Sequence[str]]) -> List[dict]:
    """each pin's place: {"num", "name", "type", "at": (x, y), "rot"}, symbol mm (y up), pin tips on the grid"""
    sides = {"left": [], "right": [], "top": [], "bottom": []}
    for num, name, kind in pins:
        sides[_side(name, kind)].append((str(num), name, kind))
    while len(sides["right"]) > len(sides["left"]) + 1:      # (passives and I/O to the shorter side, by number)
        movable = [p for p in sides["right"] if p[2] in ("passive", "bidirectional", "unspecified", "free")]
        if not movable:
            break
        sides["right"].remove(movable[0])
        sides["left"].append(movable[0])
    num_key = lambda p: (p[2] == "no_connect", len(p[0]), p[0])   # (by number, the no-connect pins last)
    for k in sides:
        sides[k].sort(key=num_key)
    chars = lambda ps: max([len(p[1]) for p in ps] or [0])
    rows = max(len(sides["left"]), len(sides["right"]), 1)
    across = max(len(sides["top"]), len(sides["bottom"]), 1)
    w = max(4, -(-((chars(sides["left"]) + chars(sides["right"])) * 1.27 + 2 * G) // G), across + 1) * G
    h = max(rows + 1, -(-((chars(sides["top"]) + chars(sides["bottom"])) * 1.27 + 2 * G) // G), 3) * G
    w, h = w + w % (2 * G), h + h % (2 * G)                  # (even: the pins land on the grid each side)
    out = []
    for k, ps in sides.items():
        for i, (num, name, kind) in enumerate(ps):
            if k in ("left", "right"):
                y = (len(ps) - 1) / 2 * G - i * G
                at, rot = ((-w / 2 - G, y), 0) if k == "left" else ((w / 2 + G, y), 180)
            else:
                x = -(len(ps) - 1) / 2 * G + i * G
                at, rot = ((x, h / 2 + G), 270) if k == "top" else ((x, -h / 2 - G), 90)
            out.append({"num": num, "name": name, "type": kind, "at": (round(at[0], 3), round(at[1], 3)), "rot": rot})
    return out, (w, h)


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write(spec: dict, folder: str) -> Path:
    """the symbol file (KiCad 9's symbol format, one file per symbol as KiCad 10 keeps them) in the symbols
    ``folder`` (a board's symbols/, or the harness's own pcb_symbols/)"""
    name, lib = spec["name"], spec.get("lib", "Prometheus")
    pins = [tuple(p) for p in spec["pins"]]
    bad = [p for p in pins if p[2] not in TYPES]
    if bad:
        raise ValueError(f"{name}: unknown pin types {bad} (KiCad's: {', '.join(sorted(TYPES))})")
    nums = [str(p[0]) for p in pins]
    if len(set(nums)) != len(nums):
        raise ValueError(f"{name}: pin numbers repeated: {sorted({n for n in nums if nums.count(n) > 1})}")
    if spec.get("footprint"):
        import pcb
        pads = {n for n in pcb.footprint(spec["footprint"])["pads"] if n}
        if pads != set(nums):
            raise ValueError(f"{name}: the pins are not {spec['footprint']}'s pads: pins without a pad "
                             f"{sorted(set(nums) - pads)}, pads without a pin {sorted(pads - set(nums))}")
    placed, (w, h) = layout(pins)
    font = "(effects (font (size 1.27 1.27)))"
    hide = "(effects (font (size 1.27 1.27)) (hide yes))"
    prop = lambda k, v, x, y, e: f'\t\t(property {_q(k)} {_q(v)} (at {x} {y} 0) {e})'
    lines = ["(kicad_symbol_lib (version 20241209) (generator \"prometheus\") (generator_version \"9.0\")",
             f"\t(symbol {_q(name)} (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)",
             prop("Reference", spec.get("ref", "U"), round(-w / 2, 3), round(h / 2 + 1.27, 3), font),
             prop("Value", name, round(w / 2, 3), round(h / 2 + 1.27, 3), font),
             prop("Footprint", spec.get("footprint", ""), 0, 0, hide),
             prop("Datasheet", spec.get("datasheet", "~") or "~", 0, 0, hide),
             prop("Description", spec.get("description", ""), 0, 0, hide),
             f"\t\t(symbol {_q(name + '_0_1')} (rectangle (start {round(-w / 2, 3)} {round(h / 2, 3)}) "
             f"(end {round(w / 2, 3)} {round(-h / 2, 3)}) (stroke (width 0.254) (type default)) "
             "(fill (type background))))",
             f"\t\t(symbol {_q(name + '_1_1')}"]
    for p in placed:
        lines.append(f"\t\t\t(pin {p['type']} line (at {p['at'][0]} {p['at'][1]} {p['rot']}) (length {G}) "
                     f"(name {_q(p['name'])} {font}) (number {_q(p['num'])} {font}))")
    lines += ["\t\t)", "\t)", ")"]
    path = Path(folder) / f"{lib}.kicad_symdir" / f"{name}.kicad_sym"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def preview(path: Path) -> Path:
    """KiCad's own drawing of the symbol (kicad-cli sym export svg), for the engineer to compare with the datasheet"""
    import shutil
    exe = shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    out = path.parent.parent / "preview"
    r = subprocess.run([exe, "sym", "export", "svg", "-o", str(out), str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("KiCad could not draw the symbol: " + (r.stderr or r.stdout)[-300:])
    return next(out.glob("*.svg"))


def main(argv) -> int:
    if not argv or not argv[0].endswith(".json"):
        print(__doc__.split("\n\n")[1].strip())
        return 2
    spec = json.loads(Path(argv[0]).read_text())
    try:
        path = write(spec, str(Path(argv[0]).resolve().parent / "symbols"))
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{path}: {len(spec['pins'])} pins; use place(..., symbol=\"{spec.get('lib', 'Prometheus')}:{spec['name']}\")")
    try:
        print(f"drawing: {preview(path)}  (compare every pin with the datasheet's pin table)")
    except (RuntimeError, OSError, StopIteration) as e:
        print(f"drawing: not made ({e})")
    return 0
