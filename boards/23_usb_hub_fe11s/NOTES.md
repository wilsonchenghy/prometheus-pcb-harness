# 23 FE1.1s 4-port USB hub: one clearance slip left

Last full run (FreeRouting through KiCad, KiCad 9 DRC): `clean: false`, with **one** finding, which the harness and
KiCad's DRC both report:

- A **GND track 0.1875 mm from the VDD5 track** at (40.1, 33.8), rule 0.2 mm, beside the chip's pins 19 and 20.
  The router's full-width (0.52 mm, 2 A) ground track starts at a grid point just past the pin row. No grid cell
  there was clear at that width, so the router took the nearest one anyway.

What this board's runs fixed, in the harness:
- the fan-out stub direction at the ends of an SSOP row;
- a stub out of a fine-pitch pin no wider than the pin;
- courtyard shapes;
- neck_vias track widths and track ends;
- merging two vias of one net drilled into each other.

What was changed in the design:
- The chip's VDD5 now has its own net behind a ferrite (FB1), so the 2 A never reaches the SSOP.
- VDD5 is drawn by hand straight into its cap.
- Each hand-drawn VBUS feed ends in two 0.4 mm vias.
- The USB pairs are matched within 1.25 mm (50 mil).

To finish:
- Give GND a top-layer pour around U1, so its pins drop into copper instead of routed ties.
- Or nudge U1 by half a grid step so the router has a clear cell past pins 19 and 20.
