"""ESP32-C3-MINI-1 dev board: USB-C (USB 2.0) straight to the C3's native USB-Serial/JTAG (IO18 = D-, IO19 = D+,
declared a differential pair), AP2112K-3.3 LDO (600 mA) from VBUS, RESET (EN) and BOOT (IO9) buttons, EN RC delay
(10k / 1 uF), strapping pull-ups on IO2 / IO8 / IO9, a status LED on IO10, two 1x9 headers with every free GPIO, UART0,
EN, 5V, 3V3 and GND (J2 left: 3V3 EN IO2 IO3 TX RX IO0 IO1 GND; J3 right: 5V IO4-IO10 GND). The USB-C sits on the
right edge just below the module so the D+/D- pair is ~10 mm long. The module sits at the top edge with its PCB antenna flush with the board edge and a copper
keep-out (both layers) over the full board width under the antenna.
Difficulty: 4/5. 2 layers, 30 x 56 mm, JLCPCB.

Engineering choices / assumptions (no engineer in the loop):
- KiCad 10 has neither a symbol nor a footprint for the ESP32-C3-MINI-1. The footprint
  (footprints/RF_Module.pretty/ESP32-C3-MINI-1.kicad_mod) is KiCad's ESP32-C6-MINI-1 land pattern: the same
  13.2 x 16.6 mm, 53-pad package (0.8 mm pitch, 3x3 centre ground pads = pin 49, corner pads 50-53). Its courtyard
  is cut back to the module body; KiCad's T-shaped courtyard that drew the antenna keep-out is replaced by the
  board's own keep-out (b.keepout, full width, from the antenna's start to the edge).
- The symbol (symbols/ESP32C3.kicad_symdir, spec ESP32-C3-MINI-1.json, made with `prometheus symbol`) is the
  ESP32-C3-MINI-1 datasheet's pin table AS REMEMBERED: GND 1, 2, 11, 14, 36-53; 3V3 3; EN 8; IO2 5, IO3 6, IO0 12,
  IO1 13, IO10 16, IO4-IO9 18-23, IO18 26, IO19 27, RXD0 30, TXD0 31; the rest NC. Verify against the datasheet
  before fab.
- 3V3 decoupling at module pin 3: 10 uF + 100 nF (Espressif's reference design values, not cited to a page).
  AP2112K: 10 uF in and out (the datasheet asks for >= 1 uF ceramic), EN tied to VIN.
- EN: 10k pull-up + 1 uF to GND (Espressif's recommended RC delay), RESET button EN -> GND.
- Strapping: IO9 10k pull-up + BOOT button to GND (download mode when held at reset); IO8 and IO2 10k pull-ups
  (both must be high for a serial/USB download boot). The status LED therefore sits on IO10 (active high, 1k), not IO8.
- CC1/CC2 5.1k pull-downs (a USB-C sink). No ESD array on D+/D- (a dev board; add a USBLC6-2SC6 for a product).
- The built-in fan-out is skipped (b._fan pre-marked): it would run stubs from the top-row ground pads into the
  antenna keep-out. The board routes clean without it.
- No series resistors on D+/D-: the C3's USB PHY needs none at full speed.

    prometheus boards/18_esp32c3_devboard/board.py out/ --spec boards/18_esp32c3_devboard/req.json
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
HDR9 = "Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical"
W, H = 30.0, 56.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = 15.0, H - 11.4                              # module origin: body from UY-5.6 to UY+11 (top = board edge)
ANT_Y = UY + 5.6                                     # where the antenna starts
JY = 32.5                                            # USB-C centre on the right edge, just below the module
FIXED = {"C1": ((6.6, UY + 2.4), 180),              # 100n at 3V3 pin 3 (left side, y +2.4)
         "C2": ((6.3, UY - 0.2), 180),              # 10u bulk by the same pin
         "R3": ((6.0, UY - 3.1), 0), "C5": ((6.0, UY - 5.2), 0),   # EN RC by EN (pin 8, left side)
         "R6": ((3.2, UY + 1.4), 90),               # IO2 strap pull-up (pin 5, left side)
         "R4": ((23.5, 9.0), 0), "R5": ((23.5, 13.0), 0),   # IO9 / IO8 pull-ups by J3 (clear of the USB corner)
         "R7": ((13.0, UY - 9.1), 270),             # status LED off IO10
         "C3": ((14.4, 25.0), 90), "C4": ((7.6, 25.0), 90)}   # LDO in / out
# the corner between the module's right side (D-/D+ on pins 26/27) and the USB-C's pads is left free for the pair


def build():
    b = Board("esp32c3_devboard", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("U1", None, (UX, UY), 0, "ESP32-C3-MINI-1", "Espressif ESP32-C3-MINI-1-N4",
            symbol="ESP32C3:ESP32-C3-MINI-1", lcsc="C2838502")
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (W - 3.2, JY), 90, "USB-C",
            "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", HDR9, (2.0, UY - 8.1), 0, "3V3 EN IO2 IO3 TX RX IO0 IO1 GND", "XFCN PZ254V-11-09P")
    b.place("J3", HDR9, (W - 2.0, 24.0), 0, "5V IO4-IO10 GND", "XFCN PZ254V-11-09P")
    b.place("SW1", "Button_Switch_SMD:SW_SPST_PTS810", (8.0, 4.0), 0, "RESET", "C&K PTS810SJM250SMTRLFS")
    b.place("SW2", "Button_Switch_SMD:SW_SPST_PTS810", (17.0, 4.0), 0, "BOOT", "C&K PTS810SJM250SMTRLFS")
    b.place("U2", None, (11.0, 25.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1",
            symbol="Regulator_Linear:AP2112K-3.3", lcsc="C51118")
    for ref, fp, val, note in (("C1", C06, "100n", "3V3 pin 3"), ("C2", C08, "10u", "3V3 bulk at module"),
                               ("C3", C08, "10u", "LDO in"), ("C4", C08, "10u", "LDO out"),
                               ("C5", C06, "1u", "EN delay"),
                               ("R1", R06, "5.1k", "CC1"), ("R2", R06, "5.1k", "CC2"),
                               ("R3", R06, "10k", "EN pull-up"), ("R4", R06, "10k", "IO9 strap pull-up"),
                               ("R5", R06, "10k", "IO8 strap pull-up"), ("R6", R06, "10k", "IO2 strap pull-up"),
                               ("R7", R06, "1k", "status LED")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (13.0, UY - 12.2), 90, "blue", note="status, IO10", lcsc="C72041")

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U2", "VIN"), ("U2", "EN"), ("C3", "1"),
                                                                   ("J3", "1")], amps=0.5, volts=5.0)
    b.net("3V3", [u("3V3"), ("U2", "VOUT"), ("C1", "1"), ("C2", "1"), ("C4", "1"), ("R3", "1"), ("R4", "1"),
                  ("R5", "1"), ("R6", "1"), ("J2", "1")], amps=0.5, volts=3.3)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] + [u("GND"), ("U2", "GND")] +
          [(c, "2") for c in ("C1", "C2", "C3", "C4", "C5", "R1", "R2", "SW1", "SW2")] +
          [("D1", "1"), ("J2", "9"), ("J3", "9")], amps=0.5)
    b.net("D+", [("J1", "A6"), ("J1", "B6"), u("IO19")], width=0.25)
    b.net("D-", [("J1", "A7"), ("J1", "B7"), u("IO18")], width=0.25)
    b.pair("D+", "D-", max_skew=0.5)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("EN", [u("EN"), ("R3", "2"), ("C5", "1"), ("SW1", "1"), ("J2", "2")])
    b.net("IO9", [u("IO9"), ("R4", "2"), ("SW2", "1"), ("J3", "7")])
    b.net("IO8", [u("IO8"), ("R5", "2"), ("J3", "6")])
    b.net("IO2", [u("IO2"), ("R6", "2"), ("J2", "3")])
    b.net("IO10", [u("IO10"), ("R7", "1"), ("J3", "8")])
    b.net("LEDA", [("R7", "2"), ("D1", "2")])
    b.net("TXD0", [u("TXD0"), ("J2", "5")])
    b.net("RXD0", [u("RXD0"), ("J2", "6")])
    b.net("IO3", [u("IO3"), ("J2", "4")])
    for io, pin in (("IO0", "7"), ("IO1", "8")):
        b.net(io, [u(io), ("J2", pin)])
    for io, pin in (("IO4", "2"), ("IO5", "3"), ("IO6", "4"), ("IO7", "5")):
        b.net(io, [u(io), ("J3", pin)])
    b.nc("J1", "SBU1", "SBU2")
    b.keepout([(0, ANT_Y), (W, ANT_Y), (W, H), (0, H)], "FB", "antenna")
    pour = [(0, 0), (W, 0), (W, ANT_Y), (0, ANT_Y)]
    b.pour("GND", "B", pour)
    b.pour("GND", "F", pour)
    # no fan-out stubs: the built-in fan-out would run one from each of the module's top-row ground pads (36-48,
    # 50, 53) straight up into the antenna keep-out. Marking them done skips the fan-out (it runs only on a board
    # with none marked); at 0.8 mm pitch the module's pads need none, and those ground pads join the pour below.
    b._fan = {("U1", str(n)) for n in list(range(36, 49)) + [50, 53]}
    return b
