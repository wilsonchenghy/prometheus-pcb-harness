# 23 FE1.1s 4-port USB hub: not clean (one pair skew)

The last full run used the merged harness, FreeRouting through KiCad and KiCad 9's DRC. It gave `clean: false` with
one finding, which the harness check reports and KiCad's DRC does not:

- **The upstream pair UDP / UDM is 7.4 mm out of match.** The tracks are 23.6 mm and 16.2 mm long; the limit is
  1.25 mm. The serpentine tuner could not fit enough length on the shorter net.

**This board is sensitive.** It was clean once, on KiCad 10's footprints (an earlier commit of this file has that run). Since the harness moved to KiCad 9's footprints (#103), the USB-C shield tabs are pad `S1`, not `SH`. The
script's hand-drawn ground ties to those tabs had silently been skipped before; they now land. Every small change
since has moved the one remaining problem somewhere else:
- a 0.08 mm GND/VBUS slip by the USB-C;
- then a downstream pair left unrouted;
- now the upstream pair's skew.

**What the design has:**
- a ground pour on the top round the SSOP, (31, 22)–(47, 37), declared at 0.3 A because it carries only the
  chip's own return;
- the chip's VDD5 behind a ferrite (FB1), drawn by hand into its cap;
- each hand-drawn VBUS feed going straight up 2 mm and then out to two 0.4 mm vias;
- the USB pairs matched within 1.25 mm (50 mil) in the requirements.

**To finish:** route the upstream pair by hand side by side from J1 to U1 (their pins face each other), or move U1
a few mm toward J1 so the pair runs straight.
