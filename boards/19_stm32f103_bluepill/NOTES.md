# 19 STM32F103 "Blue Pill": not clean yet

Last full run (FreeRouting through KiCad, KiCad 9 DRC): `clean: false`.

What remains (result.json):
- **GND split in 4**: U1.47, U2.2 and C4.2 each sit on a scrap of the ground pour that the signal tracks cut off,
  and no route reached them ("could not reach U1.47 / U2.2 / C4.2"). KiCad's DRC reports the same thing:
  unconnected GND zone islands on F.Cu.
- **USB pair D+ / D- skew**: 73.8 vs 21.0 mm. The pair goes the long way round because the USB-C sits on the left
  edge while the LQFP's PA11/PA12 come out on the side facing away from it.

Why: two 1x20 headers along both long edges leave a 60 x 26 mm board with no room for ground to flow between the
LQFP-48 and the edges on two layers.

What to change (next pass):
- Turn the MCU so PA11/PA12 face the USB-C, which shortens the pair to ~10 mm.
- Widen the board to ~30 mm, or drop the second header row to 1x16.
- Hand-place the LQFP's decoupling caps by their VDD pins, each with its own GND via, so ground never depends on
  the pour reaching between pins.
