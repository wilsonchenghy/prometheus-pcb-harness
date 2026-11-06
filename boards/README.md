# Boards

Thirty-ish small designs built with the harness, from a coin-cell badge to a 4-port USB hub, each as a script you
can read and rerun: `board.py` (the design, with every assumed pin and value in its docstring), `req.json` (what it
must meet), `routes/` (its placement and copper, so a rerun takes seconds) and `out/` (Gerbers, JLCPCB BOM,
pick-and-place, a picture, `result.json`).

    prometheus boards/05_usb_uart_ch340c/board.py out/ --spec boards/05_usb_uart_ch340c/req.json

**Clean** below means `result.json` has `"clean": true` from a run with KiCad 9's DRC (`kicad_drc.run: true`), the
schematic read back by KiCad, the design check, the JLCPCB house check and the critic all passing. Routed by
`freerouting (through KiCad)` or the harness's own grid router; `(routes/)` means the run reused the cached routing.
Remaining critic warnings (mostly "via neck": a via close to a pad of its own net) are listed in each `result.json`.

| Board | Difficulty | Parts | Size (mm) | Status |
|---|---|---|---|---|
| [01 coin-cell LED badge](01_coin_cell_led_badge) | 1 | 8 | 30 x 30 | clean |
| [02 NE555 PWM LED dimmer](02_ne555_pwm_dimmer) | 1 | 16 | 40 x 30 | clean |
| [03 USB-C 5 V / 2 A power breakout](03_usbc_power_breakout) | 1 | 10 | 36 x 22 | clean |
| [33 IR obstacle sensor (LM393)](33_ir_obstacle_lm393) | 1 | 13 | 32 x 16 | clean |
| [04 LM317 adjustable supply (all THT)](04_lm317_adjustable) | 2 | 15 | 56 x 40 | clean |
| [05 USB-UART adapter (CH340C)](05_usb_uart_ch340c) | 2 | 15 | 36 x 18 | clean |
| [06 Li-ion charger + protection (TP4056, DW01A, FS8205A)](06_tp4056_lipo_charger) | 2 | 18 | 34 x 22 | clean |
| [07 LED chaser (NE555 + CD4017)](07_led_chaser_4017) | 2 | 23 | 56 x 56 | clean |
| [08 4-channel MOSFET low-side driver](08_mosfet_4ch_driver) | 2 | 31 | 67 x 40 | clean |
| [09 electret mic preamp (MCP6002)](09_mic_preamp_mcp6002) | 2 | 18 | 30 x 18 | clean |
| [26 2-channel opto-isolated relay module](26_relay_2ch_opto) | 2 | 22 | 56 x 46 | clean |
| [31 environment sensor (BME280 + SHT40, Qwiic)](31_env_sensor_bme280_sht40) | 2 | 13 | 30 x 22 | clean |
| [10 dual DC motor driver (DRV8833)](10_drv8833_motor) | 3 | 13 | 40 x 30 | clean |
| [11 minimal Arduino (ATmega328P)](11_atmega328p_minimal) | 3 | 20 | 46 x 28 | clean |
| [12 buck 7-24 V to 5 V / 3 A (LMR33630)](12_buck_12v_to_5v) | 3 | 19 | 50 x 30 | clean |
| [13 current / power monitor (INA219)](13_ina219_power_monitor) | 3 | 12 | 36 x 20 | clean |
| [14 RS-485 transceiver (SP3485)](14_rs485_max3485) | 3 | 11 | 32 x 22 | clean |
| [15 CAN node (MCP2515 + TJA1051)](15_can_mcp2515) | 3 | 14 | 44 x 26 | clean |
| [16 stereo class-D amp (PAM8403)](16_pam8403_stereo_amp) | 3 | 20 | 50 x 40 | clean |
| [25 thermocouple amp (MAX31855)](25_max31855_thermocouple) | 3 | 11 | 30 x 20 | clean |
| [30 LoRa breakout (RFM95W)](30_rfm95_lora) | 3 | 8 | 32 x 30 | clean |
| [32 ATtiny85 V-USB dev board](32_attiny85_vusb) | 3 | 19 | 32 x 26 | clean |
| [17 8 x 8 WS2812B matrix](17_ws2812b_matrix_8x8) | 4 | 133 | 68 x 80 | clean |
| [18 ESP32-C3-MINI-1 dev board](18_esp32c3_devboard) | 4 | 20 | 30 x 56 | clean |
| [19 STM32F103 "Blue Pill"](19_stm32f103_bluepill) | 4 | 35 | 60 x 26 | **not clean**: see its NOTES.md |
| [23 4-port USB 2.0 hub (FE1.1s)](23_usb_hub_fe11s) | 4 | 28 | 78 x 46 | **not clean**: see its NOTES.md |

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
