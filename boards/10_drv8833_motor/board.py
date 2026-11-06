"""DRV8833 dual brushed-DC motor driver carrier.

VM 2.7-10.8 V in on a 2-pin 5.08 mm screw terminal (J1), two motor outputs on 2-pin screw terminals (J2 = A, J3 = B),
a 1x08 2.54 mm logic header (J4: VCC GND AIN1 AIN2 BIN1 BIN2 nSLEEP nFAULT), DRV8833PWP (HTSSOP-16 with exposed pad
and thermal vias), VM bulk + decoupling, VCP charge-pump and VINT capacitors, nSLEEP pulled up to the logic VCC (so
the driver is awake by default), and a red fault LED that lights when the open-drain nFAULT pulls low.
Difficulty: 3/5. 2 layers, 2 oz, 40 x 30 mm, JLCPCB, GND poured on both sides, two M2.5 (2.7 mm) holes.

Engineering choices / assumptions (not cited from a page read in this run: the datasheet could not be fetched):
- Pin map from KiCad's Driver_Motor:DRV8833PWP symbol (the harness matched it to the datasheet's pin table, 17/17).
- External parts per TI DRV8833 (SLVSAR1) "External components" table, from memory: VM 10 uF bulk (here a 25 V
  1206 X5R) + 0.1 uF ceramic; VCP 0.01 uF 16 V X7R to VM; VINT 2.2 uF 6.3 V to GND.
- AISEN / BISEN tied straight to GND: no current regulation (a sense resistor could be fitted in a revision).
- nSLEEP pull-up (10 k) goes to the logic VCC pin of the header, not to VM: nSLEEP's absolute maximum is 7 V and VM
  can reach 10.8 V. The logic VCC (1.8-5.5 V) must therefore be supplied on J4 for the default-awake behaviour.
- nFAULT: 10 k pull-up to VCC plus LED + 1 k from VCC, so the host still reads a clean logic level.
- Current: 1.5 A RMS per bridge, 3 A total on VM/GND; no on-board fuse or reverse-polarity protection.
- J3 is wired BOUT2 -> pin 1, BOUT1 -> pin 2 (J2: AOUT1 -> 1, AOUT2 -> 2) so the output tracks do not cross.
- The power side of U1 (VM bus, VCP, VINT, the AIN/BIN escapes, GND pins into the exposed pad) is drawn by hand with
  b.track(): the built-in router could not fit a 3 A VM feed and the cap connections between 0.65 mm pins. The
  harness saves to routes/ only boards with no hand-drawn tracks, so routes/ stays empty and each run re-routes the
  rest (about a minute).

    prometheus boards/10_drv8833_motor/board.py out/ --spec boards/10_drv8833_motor/req.json
"""
from pcb import Board

R06, C06, C12 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_1206_3216Metric"
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
W, H = 40.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
UX, UY = 23.0, 14.0                            # U1 turned 180: outputs face the motor terminals on the right
VMY = UY + 0.325                               # y of pin 12 (VM): the VM bus runs straight in along it on F
FIXED = {"C1": ((13.3, VMY - 1.475), 270),     # VM bulk: + pad on the VM bus
         "C2": ((15.9, VMY - 0.775), 270),     # VM 100n: + pad on the bus, - pad below, 2.9 mm from pin 12
         "C3": ((17.45, VMY + 0.775), 90),      # VCP 10n: VM pad on the bus, VCP pad above, by pin 11
         "C4": ((17.6, UY - 1.9), 270)}       # VINT 2.2u: + pad level with pin 14 (VINT)


def build():
    b = Board("drv8833_motor", OUT, thickness=1.6, copper_oz=2.0, rules="jlcpcb-2", corner_r=1.0)
    b.place("J1", TB, (5.2, VMY), 270, "VM in", "Phoenix 1729128")
    b.place("J2", TB, (W - 5.2, 4.3), 90, "motor A", "Phoenix 1729128")
    b.place("J3", TB, (W - 5.2, 16.2), 90, "motor B", "Phoenix 1729128")
    b.place("J4", "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical", (4.5, 2.5), 90, "logic",
            "XFCN PZ254V-11-08P", lcsc="C2829863")
    b.hole((3.5, H - 3.5), 2.7)
    b.hole((W - 3.5, H - 3.5), 2.7)
    b.place("U1", None, (UX, UY), 180, "DRV8833", "TI DRV8833PWPR", symbol="Driver_Motor:DRV8833PWP", lcsc="C50506")
    for ref, fp, val, mpn, note in (
            ("C1", C12, "10u 25V", "Samsung CL31A106KAHNNNE", "VM bulk"),
            ("C2", C06, "100n 25V", "", "VM decoupling"),
            ("C3", C06, "10n 25V", "", "VCP charge pump, to VM"),
            ("C4", C06, "2.2u 10V", "", "VINT"),
            ("R1", R06, "10k", "", "nSLEEP pull-up to VCC"),
            ("R2", R06, "10k", "", "nFAULT pull-up to VCC"),
            ("R3", R06, "1k", "", "fault LED")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, mpn, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "red", "Hubei KENTO KT-0603R", note="fault (on = nFAULT low)")

    u = lambda n: ("U1", n)
    b.net("VM", [("J1", "1"), u("VM"), ("C1", "1"), ("C2", "1"), ("C3", "1")], amps=3.0, volts=10.8)
    b.net("GND", [("J1", "2"), u("GND"), u("AISEN"), u("BISEN"), ("C1", "2"), ("C2", "2"), ("C4", "2"),
                  ("J4", "2")], amps=3.0)
    b.net("VCP", [u("VCP"), ("C3", "2")])
    b.net("VINT", [u("VINT"), ("C4", "1")])
    b.net("VCC", [("J4", "1"), ("R1", "1"), ("R2", "1"), ("R3", "1")], amps=0.05, volts=5.0)
    # J3 is wired BOUT2 -> pin 1, BOUT1 -> pin 2 so the two output tracks do not cross (direction is set in software)
    for out, pin, j in (("AOUT1", "1", "J2"), ("AOUT2", "2", "J2"), ("BOUT1", "2", "J3"), ("BOUT2", "1", "J3")):
        b.net(out, [u(out), (j, pin)], amps=1.5, volts=10.8)
    for sig, hdr in (("AIN1", "3"), ("AIN2", "4"), ("BIN1", "5"), ("BIN2", "6")):
        b.net(sig, [u(sig), ("J4", hdr)])
    b.net("nSLEEP", [u("nSLEEP"), ("R1", "2"), ("J4", "7")])
    b.net("nFAULT", [u("nFAULT"), ("R2", "2"), ("D1", "1"), ("J4", "8")])
    b.net("LEDA", [("R3", "2"), ("D1", "2")])
    # The power side of U1 (pins 9-16) is laid out by hand, the rest is left to the router:
    # VM: 3 A, 0.6 mm (2 oz) on F straight from J1.1 over C1+, C2+ and C3 to pin 12, necking to 0.4 mm for its last
    # 1.4 mm (the 0.65 mm-pitch pin is 0.4 mm wide; the pin limits the current there anyway).
    b.track("VM", "F", [b.pad("J1", "1"), (UX - 4.8, VMY)], width=0.6)
    b.track("VM", "F", [(UX - 4.8, VMY), (UX - 3.4, VMY)], width=0.4)
    xl = UX - 2.86                                 # left pin centres; their outer ends are at xl - 0.79
    b.track("VCP", "F", [(xl - 0.5, UY + 0.975), (UX - 4.4, UY + 0.975), b.pad("C3", "2")], width=0.25)
    b.track("VINT", "F", [(xl - 0.5, UY - 0.975), b.pad("C4", "1")], width=0.25)
    b.track("BIN2", "F", [(xl - 0.5, UY + 1.625), (UX - 4.4, UY + 1.625), (UX - 4.4, UY + 3.6)], width=0.25)
    b.track("BIN1", "F", [(xl - 0.5, UY + 2.275), (UX - 3.8, UY + 2.275), (UX - 3.8, UY + 3.6)], width=0.25)
    b.track("AIN2", "F", [(xl - 0.5, UY - 1.625), (UX - 4.4, UY - 1.625), (UX - 4.4, UY - 3.6)], width=0.25)
    b.track("AIN1", "F", [(xl - 0.5, UY - 2.275), (UX - 3.8, UY - 2.275), (UX - 3.8, UY - 3.6)], width=0.25)
    # GND pin 13, AISEN (3) and BISEN (6) straight into the exposed pad (GND) beside them
    b.track("GND", "F", [(xl + 0.3, UY - 0.325), (UX - 1.4, UY - 0.325)], width=0.3)
    for y in (UY - 0.975, UY + 0.975):
        b.track("GND", "F", [(UX + 2.86 - 0.3, y), (UX + 1.4, y)], width=0.3)
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
