"""STM32F103C8T6 "Blue Pill"-style board: LQFP-48 at 72 MHz from an 8 MHz HSE crystal, 32.768 kHz LSE crystal for
the RTC, USB-C (USB 2.0 full speed on PA11/PA12, 1.5k D+ pull-up to 3.3 V, CC 5.1k pull-downs), AP2112K-3.3 LDO,
BOOT0 3-pin jumper (3V3 / BOOT0 / GND, 10k pull-down so it boots from flash with no jumper), BOOT1 (PB2) 10k to
GND, reset button + 100 nF on NRST, 4-pin SWD header (3V3 SWDIO SWCLK GND), 100 nF on every VDD pin and on VBAT,
VDDA fed through a ferrite bead with 10 nF + 1 uF, 4.7 uF bulk, power LED and the PC13 LED, two 1x20 headers
along the long edges.
Difficulty: 4/5. 2 layers, 60 x 28 mm, JLCPCB.

Engineering choices / assumptions (no engineer in the loop):
- Pin numbers come from KiCad's MCU_ST_STM32F1:STM32F103C8Tx symbol (wired by name). The MCU is turned 180 deg so
  PA11/PA12 (USB) face the USB-C on the left edge.
- Decoupling: 100 nF per VDD pin (24, 36, 48) + 4.7 uF bulk, VDDA 10 nF + 1 uF behind a 220R@100MHz ferrite
  (Murata BLM18PG221SN1D): the usual ST application values (AN2586), not cited to a page here.
- HSE: 8 MHz 3225 crystal (Yangxing X322508MSB4SI, CL 20 pF), 20 pF load caps (assumed: 2 x (CL - ~5 pF stray)
  rounded down, as on most F103 boards). LSE: 32.768 kHz 3215 crystal, CL 7 pF (Seiko SC-32S class, ST AN2867 asks
  for a low-CL crystal on the F1's weak LSE driver), 10 pF load caps (assumed).
- PC14/PC15 (LSE) and PD0/PD1 (HSE) are not brought to the headers: a header stub on an oscillator pin stops it.
  PA11/PA12 (USB) are not on the headers either. VBAT is tied to 3V3 (100 nF).
- PC13 LED is active low (3V3 -> LED -> 1k -> PC13): PC13 may sink only ~3 mA.
- Headers (pin 1 at the left/USB end). J3 (top): 5V GND 3V3 PB15 PB14 PB13 PB12 PB11 PB10 PB2 PB1 PB0 PA7 PA6
  PA5 PA4 PA3 PA2 PA1 PA0. J2 (bottom): GND 3V3 PA8 PA9 PA10 PA15 PB3 PB4 PB5 PB6 PB7 PB8 PB9 PC13 NRST BOOT0 PA13
  PA14 3V3 GND.
- AP2112K-3.3: 10 uF in, 10 uF out, EN tied to VIN; the board draws well under its 600 mA.
- No ESD array on the USB lines and no series resistors (as on the Blue Pill); add a USBLC6-2SC6 for a product.

    prometheus boards/19_stm32f103_bluepill/board.py out/ --spec boards/19_stm32f103_bluepill/req.json
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
HDR20 = "Connector_PinHeader_2.54mm:PinHeader_1x20_P2.54mm_Vertical"
W, H = 60.0, 28.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = 30.0, 14.0                                  # the MCU, turned 180 deg
# fixed spots: decoupling by its supply pins (rot 180: VDD 36 left-bottom, VDD 48 bottom-right, VDD 24 top-left,
# VBAT 1 right-bottom, VDDA 9 right-top) and the oscillators by their pins
FIXED = {"C1": ((UX - 6.6, UY - 3.4), 90),          # VDD 36 (left, y -2.75) / VSS 35
         "C2": ((UX + 3.6, UY - 6.6), 0),           # VDD 48 (bottom, x +2.75) / VSS 47
         "C3": ((UX - 3.6, UY + 6.6), 0),           # VDD 24 (top, x -2.75) / VSS 23
         "C4": ((UX + 6.7, UY - 3.4), 90),          # VBAT 1 (right, y -2.75)
         "C5": ((UX + 6.7, UY + 2.6), 90),          # VDDA 9 (right, y +1.25), 10 nF
         "C6": ((UX + 8.6, UY + 3.4), 90),          # VDDA 1 uF
         "Y1": ((UX + 10.5, UY - 0.5), 0),          # HSE by PD0/PD1 (right, y -0.75 / -0.25)
         "Y2": ((UX + 10.0, UY - 6.0), 0)}          # LSE by PC14/PC15 (right, y -1.75 / -1.25)


def build():
    b = Board("stm32f103_bluepill", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("U1", None, (UX, UY), 180, "STM32F103C8T6", "ST STM32F103C8T6", symbol="MCU_ST_STM32F1:STM32F103C8Tx",
            lcsc="C8734")
    # connectors, buttons and jumpers: by hand
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, H / 2), 270, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", HDR20, (4.5, 2.2), 90, "GND 3V3 PA8-PA10 PA15 PB3-PB9 PC13 NRST BOOT0 PA13 PA14 3V3 GND",
            "XFCN PZ254V-11-20P")
    b.place("J3", HDR20, (4.5, H - 2.2), 90, "5V GND 3V3 PB15-PB10 PB2-PB0 PA7-PA0", "XFCN PZ254V-11-20P")
    b.place("J4", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (13.0, 20.6), 90, "SWD",
            "XFCN PZ254V-11-04P")
    b.place("JP1", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (50.5, 18.0), 0, "BOOT0",
            "XFCN PZ254V-11-03P")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_PTS810", (45.5, 20.8), 0, "RESET", "C&K PTS810SJM250SMTRLFS")
    b.place("U2", None, None, 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1", symbol="Regulator_Linear:AP2112K-3.3",
            lcsc="C51118")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", *FIXED["Y1"], "8MHz", "Yangxing X322508MSB4SI",
            lcsc="C9002")
    b.place("Y2", "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", *FIXED["Y2"], "32.768kHz", "Seiko SC-32S 32.768kHz 7pF")
    b.place("FB1", "Inductor_SMD:L_0603_1608Metric", None, 0, "220R@100MHz", "Murata BLM18PG221SN1D",
            note="VDDA ferrite")
    for ref, fp, val, note in (("C1", C06, "100n", "VDD 36"), ("C2", C06, "100n", "VDD 48"),
                               ("C3", C06, "100n", "VDD 24"), ("C4", C06, "100n", "VBAT"),
                               ("C5", C06, "10n", "VDDA"), ("C6", C06, "1u", "VDDA"),
                               ("C7", C08, "4.7u", "3V3 bulk"), ("C8", C06, "20p", "HSE load"),
                               ("C9", C06, "20p", "HSE load"), ("C10", C06, "10p", "LSE load"),
                               ("C11", C06, "10p", "LSE load"), ("C12", C06, "100n", "NRST"),
                               ("C13", C08, "10u", "LDO in"), ("C14", C08, "10u", "LDO out"),
                               ("R1", R06, "5.1k", "CC1"), ("R2", R06, "5.1k", "CC2"),
                               ("R3", R06, "1.5k", "USB D+ pull-up"), ("R4", R06, "10k", "BOOT0 pull-down"),
                               ("R5", R06, "10k", "BOOT1 (PB2) pull-down"), ("R6", R06, "10k", "NRST pull-up"),
                               ("R7", R06, "1k", "power LED"), ("R8", R06, "1k", "PC13 LED")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "red", note="power", lcsc="C2286")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", None, 0, "green", note="PC13", lcsc="C72043")

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U2", "VIN"), ("U2", "EN"), ("C13", "1"),
                                                                   ("J3", "1")], amps=0.5, volts=5.0)
    b.net("3V3", [u("VDD"), u("VBAT"), ("U2", "VOUT"), ("C14", "1"), ("C7", "1"), ("C1", "1"), ("C2", "1"),
                  ("C3", "1"), ("C4", "1"), ("FB1", "1"), ("R3", "1"), ("R6", "1"), ("R7", "1"), ("D2", "2"),
                  ("J4", "1"), ("JP1", "1"), ("J3", "3"), ("J2", "2"), ("J2", "19")], amps=0.3, volts=3.3)
    b.net("VDDA", [u("VDDA"), ("FB1", "2"), ("C5", "1"), ("C6", "1")], amps=0.05, volts=3.3)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] + [u("VSS"), u("VSSA"), ("U2", "GND")] +
          [(c, "2") for c in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12", "C13",
                              "C14", "R1", "R2", "R4", "R5", "SW1")] +
          [("D1", "1"), ("Y1", "2"), ("Y1", "4"), ("J4", "4"), ("JP1", "3"), ("J3", "2"), ("J2", "1"), ("J2", "20")], amps=0.3)
    b.net("D+", [("J1", "A6"), ("J1", "B6"), u("PA12"), ("R3", "2")], width=0.25)
    b.net("D-", [("J1", "A7"), ("J1", "B7"), u("PA11")], width=0.25)
    b.pair("D+", "D-", max_skew=0.5)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("HSE_IN", [u("PD0"), ("Y1", "1"), ("C8", "1")])
    b.net("HSE_OUT", [u("PD1"), ("Y1", "3"), ("C9", "1")])
    b.net("LSE_IN", [u("PC14"), ("Y2", "1"), ("C10", "1")])
    b.net("LSE_OUT", [u("PC15"), ("Y2", "2"), ("C11", "1")])
    b.net("NRST", [u("NRST"), ("C12", "1"), ("R6", "2"), ("SW1", "1"), ("J2", "15")])
    b.net("BOOT0", [u("BOOT0"), ("R4", "1"), ("JP1", "2"), ("J2", "16")])
    b.net("PB2", [u("PB2"), ("R5", "1"), ("J3", "10")])
    b.net("SWDIO", [u("PA13"), ("J4", "2"), ("J2", "17")])
    b.net("SWCLK", [u("PA14"), ("J4", "3"), ("J2", "18")])
    b.net("PWRL", [("R7", "2"), ("D1", "2")])
    b.net("PC13L", [("D2", "1"), ("R8", "1")])
    b.net("PC13", [u("PC13"), ("R8", "2"), ("J2", "14")])
    for i, p in enumerate(["PA8", "PA9", "PA10", "PA15", "PB3", "PB4", "PB5", "PB6", "PB7", "PB8", "PB9"]):
        b.net(p, [u(p), ("J2", str(i + 3))])
    top = ["PB15", "PB14", "PB13", "PB12", "PB11", "PB10", None, "PB1", "PB0", "PA7", "PA6", "PA5", "PA4", "PA3",
           "PA2", "PA1", "PA0"]
    for i, p in enumerate(top):
        if p:
            b.net(p, [u(p), ("J3", str(i + 4))])
    b.nc("J1", "SBU1", "SBU2")
    # VSS 47 sits between VDD 48 and PB9 with no way out to the pour on its own side: a short tie inward to a
    # ground via under the chip's body (the pour on the back is whole there)
    b.track("GND", "F", [(UX + 2.25, UY - 4.165), (UX + 2.25, UY - 2.4)], 0.25)
    b.via("GND", (UX + 2.25, UY - 2.4), 0.3, 0.6)
    # VSSA 8, between NRST 7 and VDDA 9, likewise: inward to its own via under the body
    b.track("GND", "F", [(UX + 4.16, UY + 0.75), (UX + 2.4, UY + 0.75)], 0.25)
    b.via("GND", (UX + 2.4, UY + 0.75), 0.3, 0.6)
    # the HSE crystal's two ground pads (2 and 4, on opposite corners) tied under its body, between the signal pads
    yx, yy = FIXED["Y1"][0]
    b.track("GND", "F", [(yx - 1.1, yy + 0.85), (yx + 1.1, yy - 0.85)], 0.2)
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
