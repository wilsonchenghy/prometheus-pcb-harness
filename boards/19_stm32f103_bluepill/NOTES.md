# 19 STM32F103 "Blue Pill": clean

The board is clean with FreeRouting through KiCad and KiCad 9's DRC.

Getting there took four design changes:
- **Board height 28 mm, up from 26.** With the original height, two 1x20 headers along the long edges left no room for
  ground to flow between the LQFP and the edges on two layers.
- **D+ pull-up R3 placed within 4 mm of PA12** (`near` in req.json). The placer had put it in the far corner, which
  made D+ 22 mm longer than D-.
- **VSS 47 and VSSA 8 tied inward to their own ground vias under the chip.** Each sits between two signal pins with no
  way out to the pour on its own side.
- **The HSE crystal's two ground pads (2 and 4) joined under its body.** The track runs between the signal pads
  with 0.34 mm to spare.
