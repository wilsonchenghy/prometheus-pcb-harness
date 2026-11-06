"""Synchronous buck converter, 7-24 V in -> 5.0 V at up to 3 A out: TI LMR33630ADDA (3.8-36 V, 3 A, 400 kHz,
integrated high- and low-side FETs, HSOP-8 with exposed pad), 10 uH shielded inductor, ceramic input and output
capacitors, 5.0 mm screw terminals in and out, a green power LED on the output and the power-good flag (pulled up
to VOUT) on a test pad.
Difficulty: 3/5. 2 layers, 50 x 30 mm, JLCPCB, ground poured on both sides.

Values from the LMR33630 datasheet (SNVSAN3F, https://www.ti.com/lit/ds/symlink/lmr33630.pdf):
- Feedback (Eq. 3, p. 21): RFBB = RFBT / (VOUT / VREF - 1), VREF = 1 V, RFBT = 100k (recommended) -> RFBB = 25.0k,
  E96 24.9k -> VOUT = 1 V x (1 + 100k / 24.9k) = 5.02 V (the datasheet's own 5 V example, Table 9-2).
- Inductor (Eq. 4, p. 21, K = 0.3 ripple at 3 A, 400 kHz): 8.1 uH at 12 V in, 11 uH at 24 V in: 10 uH chosen (ripple
  27 % at 12 V, 36 % at 24 V, inside the 20-40 % the datasheet asks). Isat must exceed ISC (5.05 A max, p. 7):
  Coilcraft XAL6060-103MEB (10 uH, Isat well above 5 A).
- CIN: 10 uF 50 V X7R 1210 + 220 nF 50 V X7R 0603 right at VIN / GND (pins 2 / 1, adjacent: the hot loop), plus a
  47 uF 35 V electrolytic to damp the input leads (p. 23). COUT: 4 x 22 uF 16 V X7R 1210 (Table 9-2, 400 kHz, 5 V).
  CBOOT 100 nF, CVCC 1 uF 16 V (p. 23). CFF: open (Table 9-2).
- EN tied to VIN ("Can be connected directly to VIN", p. 5); PG pulled up to VOUT through 100k (p. 12).
Assumptions: no input reverse-polarity or TVS protection (not asked; add an SMBJ28A for automotive inputs). 1 oz
copper (the datasheet prefers 2 oz for heat at 3 A: order 2 oz for continuous full load at high ambient).
Currents: VIN 2.5 A (3 A x 5 V / 7 V / 0.85), SW, VOUT and GND 3 A.
Layout: hot loop = C2 (220 nF) straddling pins 2 / 1, C1 right behind it, all on top; VIN (terminal -> C5 / C1 -> C2
-> pin 2) and VOUT (L1 -> J2 straight, a bus down the four COUT pads) drawn by hand on top, 1.2-1.5 mm, no vias in
the power path (the built-in router had taken VIN under the board through 0.3 mm vias); each COUT ground pad has a
0.4 mm via to the bottom ground right beside it. Terminals: pin 1 = + on both (J1 VIN, J2 5 V).
L1 (Coilcraft XAL6060) has no LCSC stock match: hand-place or substitute a 10 uH, >= 5.5 A Isat shielded part.

    cd boards/12_buck_12v_to_5v && prometheus board.py out/ --spec req.json --json --no-view
"""
from pcb import Board

R06, C06, C12 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_1210_3225Metric"
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal"
W, H = 50.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
DS = "https://www.ti.com/lit/ds/symlink/lmr33630.pdf"
UX, UY = 22.0, 14.0                                   # U1, the regulator


def build():
    b = Board("buck_12v_to_5v", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.5)
    b.place("J1", TB, (5.3, 14.0), 270, "VIN 7-24V", "Phoenix 1729128")
    b.place("J2", TB, (W - 5.3, 18.0), 90, "5V OUT", "Phoenix 1729128")
    for x, y in ((3.5, 3.5), (W - 3.5, H - 3.5)):
        b.hole((x, y), 3.2)
    b.place("U1", None, (UX, UY), 0, "LMR33630ADDA", "TI LMR33630ADDAR", symbol="Regulator_Switching:LMR33630ADDA")
    # the hot loop: 220 nF across pins 2 (VIN) / 1 (GND), the 10 uF just outside it
    b.place("C2", C06, (UX - 4.8, UY + 1.25), 90, "220n 50V", "Samsung CL10B224KB8NNNC", note="CHF, at VIN/GND")
    b.place("C1", C12, (UX - 7.6, UY + 1.25), 90, "10u 50V", "Murata GRM32ER71H106KA12L", note="CIN")
    b.place("C3", C06, (UX + 4.6, UY + 1.25), 90, "100n", "Samsung CL10B104KB8NNNC", note="CBOOT, BOOT to SW")
    b.place("C4", C06, (UX + 4.6, UY - 1.8), 270, "1u 16V", "Samsung CL10B105KO8NNNC", note="CVCC")
    b.place("L1", "Inductor_SMD:L_Coilcraft_XAL6060-XXX", (UX + 9.5, UY + 4.0), 0, "10u", "Coilcraft XAL6060-103MEB")
    b.place("C5", "Capacitor_SMD:CP_Elec_6.3x7.7", (UX - 7.2, UY - 7.6), 270, "47u 35V", "Panasonic EEE-FK1V470P", note="input bulk")
    # COUT beside L1's output pad, ground pads toward U1, each with a via to the bottom ground right by it
    for ref, at in (("C6", (UX + 9.5, UY - 2.0)), ("C7", (UX + 9.5, UY - 5.4)), ("C8", (UX + 9.5, UY - 8.8)),
                    ("C9", (UX + 14.8, UY - 2.0))):
        b.place(ref, C12, at, 0 if ref == "C9" else 180, "22u 16V", "Murata GRM32ER71C226KE18L", note="COUT")
    b.place("R1", R06, None, 0, "100k 1%", note="RFBT")
    b.place("R2", R06, None, 0, "24.9k 1%", note="RFBB")
    b.place("R3", R06, None, 0, "100k", note="PG pull-up")
    b.place("R4", R06, None, 0, "1k", note="power LED")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "green", "Hubei KENTO KT-0603G", lcsc="C72043")
    b.place("TP1", "TestPoint:TestPoint_Pad_D1.5mm", None, 0, "PG")
    b.cite("R2", "RFBB for 5 V", "24.9k", 21, "For this 5-V example, RFBT = 100 kΩ and RFBB = 24.9 kΩ are chosen.", url=DS)
    b.cite("C3", "CBOOT", "100n", 23, "A high-quality ceramic capacitor of 100 nF and at least 10 V is required.", url=DS)
    b.cite("U1", "EN tied to VIN", "VIN", 5, "Can be connected directly to VIN", url=DS)

    u = lambda n: ("U1", n)
    b.net("VIN", [("J1", "1"), u("VIN"), u("EN"), ("C1", "1"), ("C2", "1"), ("C5", "1")], amps=2.5, volts=12.0, width=1.5)
    b.net("GND", [("J1", "2"), ("J2", "2"), ("U1", "1"), ("U1", "9"), ("C1", "2"), ("C2", "2"), ("C4", "2"),
                  ("C5", "2"), ("C6", "2"), ("C7", "2"), ("C8", "2"), ("C9", "2"), ("R2", "2"), ("D1", "1")],
          amps=3.0)
    b.net("SW", [u("SW"), ("L1", "1"), ("C3", "2")], amps=3.0, volts=12.0, width=1.2)
    b.net("BOOT", [u("BOOT"), ("C3", "1")])
    b.net("VCC", [u("VCC"), ("C4", "1")])
    b.net("VOUT", [("L1", "2"), ("C6", "1"), ("C7", "1"), ("C8", "1"), ("C9", "1"), ("J2", "1"), ("R1", "1"),
                   ("R3", "1"), ("R4", "1")], amps=3.0, volts=5.0, width=1.5)
    b.net("FB", [u("FB"), ("R1", "2"), ("R2", "1")])
    b.net("PG", [u("PG"), ("R3", "2"), ("TP1", "1")])
    b.net("LED", [("R4", "2"), ("D1", "2")])
    # the input drawn by hand on top (the router took it under the board through vias): terminal -> CIN -> CHF -> VIN
    c1, c5, c2, vin = b.pad("C1", "1"), b.pad("C5", "1"), b.pad("C2", "1"), b.pad("U1", "2")
    b.track("VIN", "F", [b.pad("J1", "1"), (c1[0], b.pad("J1", "1")[1]), c1], 1.5)
    b.track("VIN", "F", [c1, c5], 1.5)
    b.track("VIN", "F", [c1, (c2[0] - 1.0, c1[1]), c2], 1.2)
    b.track("VIN", "F", [c2, vin], 0.8)
    # the output drawn by hand on top too: L1 -> J2 straight, and a bus down the COUT pads
    l2, j2 = b.pad("L1", "2"), b.pad("J2", "1")
    c6, c7, c8, c9 = (b.pad(r, "1") for r in ("C6", "C7", "C8", "C9"))
    b.track("VOUT", "F", [l2, (j2[0], l2[1])], 1.5)
    b.track("VOUT", "F", [(l2[0], l2[1] - 2.0), (l2[0], c6[1]), c6, c8], 1.2)
    b.track("VOUT", "F", [c6, c9], 1.2)
    for ref in ("C6", "C7", "C8", "C9", "C1"):
        x, y = b.pad(ref, "2")
        b.via("GND", {"C1": (x, y + 1.6), "C9": (x + 1.45, y)}.get(ref, (x - 1.45, y)), 0.4, 0.8)
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT, fill=True)
    return b
