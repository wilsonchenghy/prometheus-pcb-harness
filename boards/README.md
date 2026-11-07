# Boards

Twenty-six small designs built with the harness, from a coin-cell badge to a 4-port USB hub, each as a script you
can read and rerun: `board.py` (the design, with every assumed pin and value in its docstring), `req.json` (what it
must meet), `routes/` (its placement and copper, so a rerun takes seconds) and `out/` (Gerbers, JLCPCB BOM,
pick-and-place, a picture of the copper, KiCad's 3D render `render_3d.jpg`, `result.json`).

    prometheus boards/05_usb_uart_ch340c/board.py out/ --spec boards/05_usb_uart_ch340c/req.json

**Clean** below means `result.json` has `"clean": true` from a run with KiCad 9's DRC (`kicad_drc.run: true`), the
schematic read back by KiCad, the design check, the JLCPCB house check and the critic all passing. Routed by
`freerouting (through KiCad)` or the harness's own grid router; `(routes/)` means the run reused the cached routing.
Remaining critic warnings (mostly "via neck": a via close to a pad of its own net) are listed in each `result.json`.

| Board | Difficulty | Parts | Size (mm) | Status | 3D |
|---|---|---|---|---|---|
| [01 coin-cell LED badge](01_coin_cell_led_badge) | 1 | 8 | 30 x 30 | clean | [render](01_coin_cell_led_badge/out/render_3d.jpg) |
| [02 NE555 PWM LED dimmer](02_ne555_pwm_dimmer) | 1 | 16 | 34 x 26 | clean | [render](02_ne555_pwm_dimmer/out/render_3d.jpg) |
| [03 USB-C 5 V / 2 A power breakout](03_usbc_power_breakout) | 1 | 10 | 36 x 22 | clean | [render](03_usbc_power_breakout/out/render_3d.jpg) |
| [33 IR obstacle sensor (LM393)](33_ir_obstacle_lm393) | 1 | 13 | 32 x 16 | clean | [render](33_ir_obstacle_lm393/out/render_3d.jpg) |
| [04 LM317 adjustable supply (all THT)](04_lm317_adjustable) | 2 | 15 | 44 x 34 | clean | [render](04_lm317_adjustable/out/render_3d.jpg) |
| [05 USB-UART adapter (CH340C)](05_usb_uart_ch340c) | 2 | 15 | 36 x 18 | clean | [render](05_usb_uart_ch340c/out/render_3d.jpg) |
| [06 Li-ion charger + protection (TP4056, DW01A, FS8205A)](06_tp4056_lipo_charger) | 2 | 18 | 34 x 17.5 | clean | [render](06_tp4056_lipo_charger/out/render_3d.jpg) |
| [07 LED chaser (NE555 + CD4017)](07_led_chaser_4017) | 2 | 23 | 32 x 35 | clean | [render](07_led_chaser_4017/out/render_3d.jpg) |
| [08 4-channel MOSFET low-side driver](08_mosfet_4ch_driver) | 2 | 31 | 67 x 30 | clean | [render](08_mosfet_4ch_driver/out/render_3d.jpg) |
| [09 electret mic preamp (MCP6002)](09_mic_preamp_mcp6002) | 2 | 18 | 26 x 16 | clean | [render](09_mic_preamp_mcp6002/out/render_3d.jpg) |
| [26 2-channel opto-isolated relay module](26_relay_2ch_opto) | 2 | 22 | 48 x 41.5 | clean | [render](26_relay_2ch_opto/out/render_3d.jpg) |
| [31 environment sensor (BME280 + SHT40, Qwiic)](31_env_sensor_bme280_sht40) | 2 | 13 | 24.5 x 17.5 | clean | [render](31_env_sensor_bme280_sht40/out/render_3d.jpg) |
| [10 dual DC motor driver (DRV8833)](10_drv8833_motor) | 3 | 13 | 39 x 29.5 | clean | [render](10_drv8833_motor/out/render_3d.jpg) |
| [11 minimal Arduino (ATmega328P)](11_atmega328p_minimal) | 3 | 20 | 46 x 28 | clean | [render](11_atmega328p_minimal/out/render_3d.jpg) |
| [12 buck 7-24 V to 5 V / 3 A (LMR33630)](12_buck_12v_to_5v) | 3 | 19 | 45.5 x 22.5 | clean | [render](12_buck_12v_to_5v/out/render_3d.jpg) |
| [13 current / power monitor (INA219)](13_ina219_power_monitor) | 3 | 12 | 36 x 13 | clean | [render](13_ina219_power_monitor/out/render_3d.jpg) |
| [14 RS-485 transceiver (SP3485)](14_rs485_max3485) | 3 | 11 | 31 x 18.5 | clean | [render](14_rs485_max3485/out/render_3d.jpg) |
| [15 CAN node (MCP2515 + TJA1051)](15_can_mcp2515) | 3 | 14 | 44 x 26 | clean | [render](15_can_mcp2515/out/render_3d.jpg) |
| [16 stereo class-D amp (PAM8403)](16_pam8403_stereo_amp) | 3 | 20 | 40 x 28.5 | clean | [render](16_pam8403_stereo_amp/out/render_3d.jpg) |
| [25 thermocouple amp (MAX31855)](25_max31855_thermocouple) | 3 | 11 | 25.5 x 17 | clean | [render](25_max31855_thermocouple/out/render_3d.jpg) |
| [30 LoRa breakout (RFM95W)](30_rfm95_lora) | 3 | 8 | 32 x 30 | clean | [render](30_rfm95_lora/out/render_3d.jpg) |
| [32 ATtiny85 V-USB dev board](32_attiny85_vusb) | 3 | 19 | 32 x 26 | clean | [render](32_attiny85_vusb/out/render_3d.jpg) |
| [17 8 x 8 WS2812B matrix](17_ws2812b_matrix_8x8) | 4 | 133 | 68 x 80 | clean | [render](17_ws2812b_matrix_8x8/out/render_3d.jpg) |
| [18 ESP32-C3-MINI-1 dev board](18_esp32c3_devboard) | 4 | 20 | 30 x 56 | clean | [render](18_esp32c3_devboard/out/render_3d.jpg) |
| [19 STM32F103 "Blue Pill"](19_stm32f103_bluepill) | 4 | 35 | 60 x 28 | clean | [render](19_stm32f103_bluepill/out/render_3d.jpg) |
| [23 4-port USB 2.0 hub (FE1.1s)](23_usb_hub_fe11s) | 4 | 29 | 78 x 46 | **not clean**: one USB pair 7.4 mm out of match, see its NOTES.md | [render](23_usb_hub_fe11s/out/render_3d.jpg) |

## Before ordering any of these

These were built without an engineer in the loop. Each `board.py` docstring lists what was assumed. Check those
items against the datasheets before you order:

- **Pins taken from KiCad symbols** were checked against the footprint both ways, and against the datasheet where
  the run could fetch it. Pins written by hand are named in the docstrings: relay contacts (26), the LED / photo
  transistor polarity (33), the battery holder (01), the barrel jack (04).
- **Generated parts:** 16's PAM8403 symbol (checked against Diodes' pin table) and 18's ESP32-C3-MINI-1 footprint
  (made by fpgen: compare it with Espressif's land pattern before ordering).
- **LCSC numbers looked up automatically** are listed under `lcsc_auto` in each `result.json`. Confirm them; parts
  without one are listed by the critic.
- **High-speed lines:** the USB pairs are length-matched, not impedance-controlled, since 2-layer 1.6 mm boards
  cannot give 90 ohm at practical widths. This is fine for full-speed USB (05, 18, 32); 23 is high-speed.

See [CONVENTIONS.md](CONVENTIONS.md) for how the boards are made and the harness lessons learned along the way.

## The 3D renders

`out/render_3d.jpg` is KiCad 9's raytraced render of the exported board with each part's library 3D model:
`pcb.kicad_models(board, model_dir)` fetches the models, then `kicad-cli pcb render --perspective` (as
`pcb.render_kicad` does) with `KICAD9_3DMODEL_DIR` set to that folder. A part whose footprint names a model KiCad's
library does not have is drawn as bare pads: the RFM95W, the SHT40, the PAM8403 board's dual pot and the
ESP32-C3 module.

## Sized to their parts

The outlines were first drawn generously, and the placer spreads parts over whatever outline it is given, so
several boards were mostly bare board. Fourteen were shrunk to their parts (02, 04, 06-10, 12-14, 16, 25, 26, 31):
parts' courtyards now cover 45-63 % of each board (36 % for the LED ring of 07, whose corners a ring leaves
empty), against 13-44 % before, each still clean. `size_mm` in `req.json` is the most the board may be; the critic
now warns when the parts cover under 20 % of it, or when an edge has more than about 4 mm of bare board.
