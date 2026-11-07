# 23 FE1.1s 4-port USB hub: clean

Clean: FreeRouting through KiCad, KiCad 9's DRC.

Getting there took three passes.
- **The first pass left one finding:** a 2 A ground track ran 0.19 mm from VDD5 beside the SSOP's pins 19/20.
- **The fix was a ground pour on the top layer round the SSOP**, region (31, 22)–(47, 38). The chip's ground pins now
  drop into copper instead of needing full-width routed ties between 0.635 mm-pitch neighbours.
- **That pour is declared at 0.3 A.** It carries only the chip's own return. The ports' 2 A comes back through the bottom
  pour. Left at the net's 2 A, the pour-neck check asked for 0.5 mm necks it does not need.

What this board's runs fixed, in the harness:
- the fan-out stub direction at the ends of an SSOP row;
- a stub out of a fine-pitch pin no wider than the pin;
- courtyard shapes;
- neck_vias track widths and track ends;
- merging two vias of one net drilled into each other;
- a forced start cell kept to the stub's width (#96).

Design choices:
- The chip's VDD5 has its own net behind a ferrite (FB1), so the 2 A never reaches the SSOP.
- VDD5 is drawn by hand straight into its cap.
- Each hand-drawn VBUS feed ends in two 0.4 mm vias.
- The USB pairs are matched within 1.25 mm (50 mil).
