# 23 FE1.1s 4-port USB hub: not clean yet

Last full run (FreeRouting through KiCad, KiCad 9 DRC): `clean: false`.

What remains (result.json; KiCad's DRC agrees with the harness):
- **Clearance slips (0.17-0.18 mm, rule 0.2) around the chip's pins 18-20** (VBUSM / GND / VDD5). Those pins carry
  the 2 A VBUS and GND, at 0.635 mm pitch: the rails' 0.51 mm copper does not fit beside the neighbouring pins.
- **VBUS left split** at C3 (the VDD5 decoupling cap).
- Pair skews on one downstream port.

Why: a bus-powered 4-port hub carries 2 A of VBUS and GND across a 2-layer board and past a fine-pitch SSOP.

What to change (next pass):
- Give the chip's VDD5 pin its own low-current net (VDD5, through a 0 ohm or ferrite from VBUS), as 13 does for
  its sense pin. The 2 A then stays on the connector-fuse-ports path and never has to reach the SSOP pin.
- Make GND a pour on both layers and drop the chip's GND pins with short necks.
- For a strict high-speed design (90 ohm pairs), move to 4 layers.

The harness fixes this board's runs turned up are in the branch's history: the fan-out direction at SSOP row ends,
courtyard shapes, neck_vias widths and track ends, and merging same-net vias.
