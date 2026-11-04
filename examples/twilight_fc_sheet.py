"""The Twilight flight computer's schematic: four A3 sheets as an engineer draws them.
  1/4  POWER - battery input, fuse, reverse-polarity FET, TVS, current shunt + INA226; the 5 V buck and the 6 V servo
       buck in TI's datasheet form; the BEC inlet (Mode B); VSERVO and its sense divider; the rail test points.
  2/4  COMPUTE + I/O - the Teensy 4.1 (its two pin rows), the connectors in Pixhawk order (TELEM1, GPS1, RC IN, CAN,
       Qwiic, expansion), the LoRa module and its regulator, LEDs, buzzer, button.
  3/4  SENSORS - the analog 3.3 V supply (ferrite + LDO) and the six sensors with their decoupling, bus pins named.
  4/4  PYRO + SERVO - the pyro supply and the arming chain (keyed switch, ARM_EN FET, ARM LED), the four fire
       channels with continuity sensing, the servo buffer and the output block.
Power and analog circuits are wired; bus pins (SPI, I2C, UART) carry net names, as is usual between an MCU and its
peripherals. All four sheets are checked together against the board's netlist before they are saved."""

import datetime
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")]
import schemdraw.elements as elm
from schem import Sheet, check_sheets, save_sheets, TERM2, REV2, ic, nets_out, leg, tp, tp_on, conn, notes
W, H = 46.0, 32.5
BOARD = "TWILIGHT-FC REV A  (cad_bench/projects/twilight_fc.py)"


def buck(s, b, U, at, ref_parts, vout_net, title, values):
    """a TPS54x60B buck in TI's datasheet form: CIN, the controller, CBOOT, catch diode, inductor, COUT, divider,
    compensation, EN divider, RT"""
    cin, cboot, d, l, couts, ren1, ren2, rt, rfb1, rfb2, rc, cc, cc2 = ref_parts
    x0, y0 = at
    a = ic(s, U, (x0, y0), [("VIN", "2"), ("EN", "3"), ("RT/CLK", "4"), ("COMP", "6")],
           [("BOOT", "1"), ("SW", "8"), ("GND", "7"), ("FB", "5")], f"{U}\n{values['part']}", w=4.2, sp=0.8,
           bot=[("PAD", "9")])
    s.gnd(a("9"), lead=0.4)
    s.wire(a("7"), (a("7")[0] + 0.6, a("7")[1]))
    s.gnd((a("7")[0] + 0.6, a("7")[1]))
    # input: VIN port, CIN to ground, EN divider
    xi = a("2")[0] - 4.2
    yv = a("2")[1] + 1.6
    s.wire(a("2"), (a("2")[0] - 0.6, a("2")[1]), (a("2")[0] - 0.6, yv))
    s.wire((xi - 1.0, yv), (a("2")[0] - 0.6, yv))
    s.port((xi - 1.0, yv), "VIN")
    for i, c in enumerate(cin):
        x = xi + 1.6 * i
        s.dot((x, yv))
        leg(s, c, elm.Capacitor(), x, yv, f"{c}\n{values['cin']}", pins=TERM2 if values["cin_pin1_top"][i] else REV2,
            length=1.2)
    xe = a("3")[0] - 1.0
    s.wire(a("3"), (xe, a("3")[1]))
    s.dot((xe, a("3")[1]))
    r1 = s.add(ren1, elm.Resistor().at((xe, a("3")[1])).up().length(1.2), REV2, label=f"{ren1} {values['ren1']}",
               loc="bottom")
    s.wire(r1.absanchors["end"], (xe, yv))
    s.dot((xe, yv))
    r2 = s.add(ren2, elm.Resistor().at((xe, a("3")[1])).left().length(1.2), TERM2, label=f"{ren2} {values['ren2']}",
               loc="top")
    s.gnd(r2.absanchors["end"])
    r3 = s.add(rt, elm.Resistor().at(a("4")).left().length(1.6), TERM2, label=f"{rt} {values['rt']}", loc="top")
    s.gnd(r3.absanchors["end"])
    # compensation: RC + CC to ground, CC2 in parallel
    xc = a("6")[0] - 1.4
    s.wire(a("6"), (xc, a("6")[1]))
    s.dot((xc, a("6")[1]))
    r4 = s.add(rc, elm.Resistor().at((xc, a("6")[1])).down().length(1.0), TERM2, label=f"{rc} {values['rc']}",
               loc="bottom")
    c4 = s.add(cc, elm.Capacitor().at(r4.absanchors["end"]).down().length(1.0), TERM2, label=f"{cc} {values['cc']}",
               loc="bottom")
    s.gnd(c4.absanchors["end"])
    xc2 = xc - 1.6                                       # (CC2 down from a stub, clear of RT above it)
    s.wire((xc, a("6")[1]), (xc2, a("6")[1]))
    c5 = s.add(cc2, elm.Capacitor().at((xc2, a("6")[1])).down().length(1.2), TERM2, label=f"{cc2} {values['cc2']}",
               loc="top")
    s.gnd(c5.absanchors["end"])
    # boot, switch node, catch diode, inductor, output
    xs = a("8")[0] + 1.4
    ysw = a("8")[1]
    s.wire(a("8"), (xs, ysw))
    cb = s.add(cboot, elm.Capacitor().at(a("1")).right().length(1.4), TERM2, label=f"{cboot} 100n", loc="top")
    s.wire(cb.absanchors["end"], (xs, a("1")[1]), (xs, ysw))
    s.dot((xs, ysw))
    dd = s.add(d, elm.Schottky().at((xs, ysw)).down().length(1.6).reverse(), {"1": "start", "2": "end"})
    s.gnd(dd.absanchors["end"])
    s.text((xs + 0.3, ysw - 1.1), f"{d}\n{values['d']}", size=6, halign="left")
    s.netname((xs, ysw), values["sw"], dx=0.2, dy=0.3)
    ll = s.add(l, elm.Inductor2().at((xs, ysw)).right().length(2.4), TERM2, label=f"{l} {values['l']}", loc="top")
    vo = ll.absanchors["end"]
    xo = vo[0]
    for i, c in enumerate(couts):
        x = xo + 0.6 + 1.6 * i
        s.dot((x, vo[1]))
        leg(s, c, elm.Capacitor(), x, vo[1], f"{c}\n{values['cout']}",
            pins=TERM2 if values["cout_pin1_top"][i] else REV2, length=1.2)
    xf = xo + 0.6 + 1.6 * len(couts) + 0.4
    s.wire(vo, (xf + 1.6, vo[1]))
    s.port((xf + 1.6, vo[1]), vout_net)
    # feedback divider from the output; its tap runs back to FB under the power stage
    yfb = a("5")[1]
    ylo = ysw - 3.0
    rf1 = s.add(rfb1, elm.Resistor().at((xf, vo[1])).down().length(vo[1] - ylo), TERM2 if values["rfb1_pin1_top"] else REV2)
    s.dot((xf, vo[1]))
    s.wire(a("5"), (a("5")[0] + 0.3, yfb), (a("5")[0] + 0.3, ylo), (xf, ylo))
    s.dot((xf, ylo))
    rf2 = s.add(rfb2, elm.Resistor().at((xf, ylo)).down().length(1.0), TERM2)
    s.gnd(rf2.absanchors["end"])
    s.text((xf + 0.3, (vo[1] + ylo) / 2), f"{rfb1}\n{values['rfb1']}", size=6, halign="left")
    s.text((xf + 0.3, ylo - 0.5), f"{rfb2} {values['rfb2']}", size=6, halign="left")
    s.text((x0 - 5.5, a("2")[1] + 3.0), title, size=8, halign="left")
    return a, vo


# ---- sheet 1: power ----------------------------------------------------------------------------------------------------
def sheet1(b, date):
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "POWER", "1 / 4", BOARD, date=date, note="checked against the layout netlist")
    s.text((1.5, 30.6), "BATTERY INPUT  -  2S-6S (6.5-26 V), 15 A fused, reverse-polarity protected, V / I monitored",
           size=9, halign="left")
    y = 28.0
    j1 = conn(s, "J1", (1.4, y - 0.9), [("+", "2"), ("-", "1")], "J1\nXT30", side="right", w=1.4)
    s.wire(j1("1"), (j1("1")[0] + 0.6, j1("1")[1]))
    s.gnd((j1("1")[0] + 0.6, j1("1")[1]))
    p = j1("2")
    s.wire(p, (p[0] + 1.2, p[1]))
    tp_on(s, "TP1", (p[0] + 0.6, p[1]), "VBAT_RAW")
    f1 = s.add("F1", elm.Fuse().at((p[0] + 1.2, p[1])).right().length(2.0), TERM2, label="F1 15A", loc="top")
    xq = f1.absanchors["end"][0] + 0.8
    y = p[1]
    s.wire(f1.absanchors["end"], (xq, y))
    q1 = s.add("Q1", elm.PFet().down().anchor("drain").at((xq, y)), {"2": "drain", "3": "source", "1": "gate"},
               label="Q1 SQM40031EL", loc="top")
    src = q1.absanchors["source"]
    g = q1.absanchors["gate"]
    yb = g[1] - 1.0
    s.wire(g, (g[0], yb))
    s.dot((g[0], yb))
    dz = s.add("DZ1", elm.Zener().at((src[0] + 0.6, yb)).up().length(src[1] - yb), {"1": "end", "2": "start"})
    s.wire((g[0], yb), (src[0] + 0.6, yb))
    r1 = s.add("R1", elm.Resistor().at((g[0], yb)).down().length(1.2), TERM2)
    s.gnd(r1.absanchors["end"])
    s.text((g[0] - 0.3, yb - 0.8), "R1 47k", size=6, halign="right")
    s.text((src[0] + 0.9, yb + 0.4), "DZ1 12V", size=6, halign="left")
    xb = src[0] + 0.6
    s.wire(src, (xb + 2.4, src[1]))
    s.dot((xb, src[1]))
    s.netname((xb + 0.2, src[1]), "VIN_P", dx=0.0, dy=0.3)
    xt = xb + 1.6
    s.dot((xt, src[1]))
    leg(s, "D1", elm.DiodeTVS().reverse(), xt, src[1], None, pins={"1": "start", "2": "end"}, length=1.4)
    s.text((xt - 0.3, src[1] - 0.8), "D1 SMBJ33A", size=6, halign="right")      # (clear of the Kelvin line)
    tp_on(s, "TP2", (xb + 1.0, src[1]), "VIN_P", dy=0.8)
    rs = s.add("RS1", elm.Resistor().at((xb + 2.4, src[1])).right().length(2.4), TERM2, label="RS1 2m", loc="bottom")
    vin = rs.absanchors["end"]
    s.wire(vin, (vin[0] + 3.0, vin[1]))
    s.port((vin[0] + 3.0, vin[1]), "VIN")
    tp_on(s, "TP9", (vin[0] + 1.6, vin[1]), "VIN", dy=0.8)
    # INA226: sense pins on top, Kelvin lines straight down from the shunt's pads
    xk1, xk2 = rs.absanchors["start"][0], vin[0]
    a = ic(s, "U1", (xk1, src[1] - 3.2), [], [("VS", "6"), ("SDA", "4"), ("SCL", "5"), ("ALERT", "3")],
           "U1 INA226\n0x40", w=4.8, sp=1.2, top=[("IN+", "10"), ("VBUS", "8"), ("IN-", "9")],
           bot=[("A1", "1"), ("A0", "2"), ("GND", "7")], anchor="10")
    s.wire(rs.absanchors["start"], a("10"))
    s.dot(rs.absanchors["start"])
    s.wire(vin, (xk2, a("9")[1]))
    s.dot(vin)
    s.wire(a("8"), (a("8")[0], a("8")[1] + 0.5), (xk2, a("8")[1] + 0.5))
    s.dot((xk2, a("8")[1] + 0.5))
    for p_ in ("1", "2", "7"):
        s.gnd(a(p_), lead=0.4)
    s.flag(a("4"), "SDA")
    s.flag(a("5"), "SCL")
    s.text((a("3")[0] + 0.3, a("3")[1]), "x", size=8, halign="center", valign="center")
    vs = a("6")
    s.wire(vs, (vs[0] + 1.2, vs[1]))
    s.dot((vs[0] + 1.2, vs[1]))
    s.port((vs[0] + 1.2, vs[1]), "3V3")
    leg(s, "C1", elm.Capacitor(), vs[0] + 1.2, vs[1], None, pins=TERM2, length=1.0)
    s.text((vs[0] + 1.5, vs[1] - 0.5), "C1 100n", size=6, halign="left")
    # 5 V buck
    v5 = {"part": "TPS54360B", "cin": "4u7 50V", "cin_pin1_top": [False, False], "ren1": "147k", "ren2": "35k7",
          "rt": "162k", "rc": "13k", "cc": "6n8", "cc2": "39p", "d": "PDS540", "sw": "SW5", "l": "6u8",
          "cout": "47u 10V", "cout_pin1_top": [False, False], "rfb1": "53k6", "rfb2": "10k2", "rfb1_pin1_top": True}
    buck(s, b, "U2", (9.5, 12.0), (["C2", "C3"], "C6", "D5", "L1", ["C4", "C5"], "R2", "R3", "R4", "R5", "R6", "R7",
                                    "C7", "C8"),
         "+5V", "5 V / 3.5 A  -  TI TPS54360B datasheet design, 600 kHz", v5)
    s.text((24.6, 13.6), "+5 V feeds: MCU, radios, GPS, CAN,\nservo buffer, LEDs, 3V3 LDOs", size=6, halign="left")
    # 6 V servo buck
    v6 = {"part": "TPS54560B", "cin": "4u7 50V", "cin_pin1_top": [True, False], "ren1": "147k", "ren2": "35k7",
          "rt": "196k", "rc": "18k7", "cc": "5n6", "cc2": "33p", "d": "PDS540", "sw": "SW6", "l": "6u8",
          "cout": "47u 16V", "cout_pin1_top": [True, True, True], "rfb1": "66k5", "rfb2": "10k2", "rfb1_pin1_top": True}
    _, vo6 = buck(s, b, "U3", (9.5, 3.0), (["C10", "C11"], "C9", "D6", "L2", ["C12", "C13", "C14"], "R9", "R10", "R11",
                                           "R12", "R13", "R14", "C15", "C16"),
                  "VSERVO", "6.0 V / 5 A servo rail (Mode A)  -  TI TPS54560B, 500 kHz", v6)
    # BEC inlet (Mode B) -> VSERVO
    xj, yj = 31.0, 16.6
    s.text((xj - 0.5, yj + 2.6), "SERVO BEC INLET  (Mode B: 12 A, up to 8.4 V)", size=8, halign="left")
    j2 = conn(s, "J2", (xj, yj - 0.9), [("+", "2"), ("-", "1")], "J2\nXT30", side="right", w=1.4)
    s.wire(j2("1"), (j2("1")[0] + 0.6, j2("1")[1]))
    s.gnd((j2("1")[0] + 0.6, j2("1")[1]))
    p = j2("2")
    xq2 = p[0] + 3.6
    s.wire(p, (xq2, p[1]))
    s.dot((p[0] + 1.2, p[1]))
    leg(s, "D3", elm.DiodeTVS().reverse(), p[0] + 1.2, p[1], None, pins={"1": "start", "2": "end"}, length=1.4)
    s.text((p[0] + 1.5, p[1] - 0.8), "D3\nSMBJ10A", size=6, halign="left")
    q2 = s.add("Q2", elm.PFet().down().anchor("drain").at((xq2, p[1])), {"2": "drain", "3": "source", "1": "gate"})
    s.text((xq2 + 0.5, p[1] + 0.55), "Q2 SQM40031EL", size=6, halign="center")
    g2 = q2.absanchors["gate"]
    r8 = s.add("R8", elm.Resistor().at(g2).down().length(1.0), TERM2)
    s.gnd(r8.absanchors["end"])
    s.text((g2[0] - 0.3, g2[1] - 0.6), "R8 10k", size=6, halign="right")
    sv = q2.absanchors["source"]
    xs = sv[0] + 3.6
    s.wire(sv, (xs + 2.8, sv[1]))
    s.port((xs + 2.8, sv[1]), "VSERVO")
    for i, (ref, x) in enumerate((("C18", sv[0] + 1.0), ("C19", sv[0] + 2.2))):
        s.dot((x, sv[1]))
        leg(s, ref, elm.Capacitor(polar=True), x, sv[1], None, pins=TERM2, length=1.2)
    s.text((sv[0] + 0.7, sv[1] - 1.9), "C18/C19 220u 25V", size=6, halign="left")
    s.dot((xs, sv[1]))
    r25 = s.add("R25", elm.Resistor().at((xs, sv[1])).down().length(1.2), TERM2)
    s.dot(r25.absanchors["end"])
    r26 = s.add("R26", elm.Resistor().at(r25.absanchors["end"]).down().length(1.2), TERM2)
    s.gnd(r26.absanchors["end"])
    s.flag(r25.absanchors["end"], "VS_SENSE")
    s.text((xs - 0.3, sv[1] - 1.2), "R25 100k\nR26 27k", size=6, halign="right")
    tp_on(s, "TP3", (xs + 1.6, sv[1]), "VSERVO", dy=0.8)
    # rail test points and the ground loops
    s.text((31.0, 11.2), "RAIL TEST POINTS", size=8, halign="left")
    tp(s, "TP4", (31.5, 10.0), "+5V")
    s.port((31.5, 10.0), "+5V", up=False)
    for i, ref in enumerate(("TP10", "TP11")):
        x = 34.0 + 1.6 * i
        tp(s, ref, (x, 10.0), "GND")
        s.gnd((x, 10.0))
    notes(s, 30.5, 8.1, [
        "1.  Q1 / Q2 conduct only with the battery the right way round (body diode,",
        "     then the gate pulled low); DZ1 holds VGS inside +-20 V on 6S + transients.",
        "2.  RS1 -> INA226: 81.92 mV full scale = 41 A; Kelvin lines to the shunt pads.",
        "3.  Mode A: U3 fitted, nothing on J2. Mode B: leave L2 off, BEC on J2.",
        "4.  Both bucks: UVLO on at 6.0 V. The 5 V rail is sized for RFD900x bursts (1 A).",
        "5.  6 V compensation from TI's equations (fco 28 kHz, 85 uF derated COUT).",
    ])
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


# ---- sheet 2: compute + I/O --------------------------------------------------------------------------------------------
def sheet2(b, date):
    import twilight_fc as fc
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "COMPUTE + I/O", "2 / 4", BOARD, date=date, note="Teensy 4.1 pin numbers in the boxes")
    s.text((1.5, 30.6), "TEENSY 4.1  (i.MX RT1062, 600 MHz)  -  two 1x24 sockets; every edge pin used", size=9, halign="left")
    top = [(f"{n}" if n in ("GND", "3V3") else f"D{n}", str(k + 1)) for k, n in enumerate(fc.TOP_ROW)]
    bot = [(f"{n}" if n in ("GND", "3V3", "VIN") else f"D{n}", str(k + 1)) for k, n in enumerate(fc.BOT_ROW)]
    a1 = ic(s, "JT1", (5.0, 13.0), [], top, "JT1\nleft row", w=2.6, sp=0.62)
    a2 = ic(s, "JT2", (14.5, 13.0), [], bot, "JT2\nright row", w=2.6, sp=0.62)
    nets_out(s, b, "JT1", a1, [p for _, p in top], "right", gnd_flag=True)
    pn = b.pad_net()
    nets_out(s, b, "JT2", a2, [p for _, p in bot], "right", skip=("1",), gnd_flag=True)
    # Teensy VIN from +5 V through D9
    v = a2("1")
    s.wire(v, (v[0] + 1.0, v[1]))
    s.dot((v[0] + 1.0, v[1]))
    d9 = s.add("D9", elm.Schottky().at((v[0] + 1.0, v[1])).up().length(1.4).reverse(), {"1": "start", "2": "end"})
    s.port(d9.absanchors["end"], "+5V")
    s.text((v[0] + 1.3, v[1] + 0.7), "D9 B140", size=6, halign="left")
    tp_on(s, "TP28", (v[0] + 1.0, v[1]), "VIN_T", dx=1.0, dy=0.0)
    # connectors (Pixhawk JST-GH order)
    x = 24.0
    s.text((x, 30.4), "CONNECTORS", size=8, halign="left")
    rows = [("J7", [("5V", "1"), ("TX", "2"), ("RX", "3"), ("CTS", "4"), ("RTS", "5"), ("GND", "6")], "J7 TELEM1"),
            ("J8", [("5V", "1"), ("TX", "2"), ("RX", "3"), ("SCL", "4"), ("SDA", "5"), ("GND", "6")], "J8 GPS1"),
            ("J6", [("V", "1"), ("RX", "2"), ("TX", "3"), ("GND", "4")], "J6 RC IN"),
            ("J5", [("GND", "1"), ("3V3", "2"), ("SDA", "3"), ("SCL", "4")], "J5 QWIIC")]
    yy = 24.6
    for ref, pins, title in rows:
        a = conn(s, ref, (x + 2.2, yy), pins, title, side="left", w=2.0)
        nets_out(s, b, ref, a, [p for _, p in pins], "left", skip=("1",) if ref in ("J7", "J6") else ())
        if ref == "J7":
            q = a("1")
            s.wire(q, (q[0] - 1.2, q[1]))
            s.dot((q[0] - 1.2, q[1]))
            s.wire((q[0] - 1.2, q[1]), (q[0] - 1.2, q[1] + 0.8))
            s.port((q[0] - 1.2, q[1] + 0.8), "+5V")
            leg(s, "C36", elm.Capacitor(polar=True), q[0] - 2.2, q[1], None, length=1.0)
            s.wire((q[0] - 2.2, q[1]), (q[0] - 1.2, q[1]))
            s.text((q[0] - 2.4, q[1] + 0.4), "C36 220u", size=6, halign="right")
        if ref == "J6":
            q = a("1")
            jp = ic(s, "JP2", (q[0] - 5.4, q[1] - 0.6), [], [("5V", "1"), ("V", "2"), ("3V3", "3")], "JP2", w=2.0, sp=0.6)
            s.wire(jp("2"), q)
            s.wire(jp("1"), (jp("1")[0] + 0.5, jp("1")[1]))
            s.port((jp("1")[0] + 0.5, jp("1")[1]), "+5V")
            s.wire(jp("3"), (jp("3")[0] + 0.5, jp("3")[1]))
            s.port((jp("3")[0] + 0.5, jp("3")[1]), "3V3", up=False)
        yy -= len(pins) * 0.6 + 1.6
    a = conn(s, "J9", (x + 2.2, 1.6), [(str(k), str(k)) for k in range(1, 11)], "J9 EXPANSION", side="left", w=2.0, sp=0.7)
    nets_out(s, b, "J9", a, [str(k) for k in range(1, 11)], "left", gnd_flag=True)
    # CAN-FD: CANH / CANL straight to J4, the solder-jumpered 120R between them
    xc, yc = 30.2, 22.0
    s.text((xc, 29.2), "CAN-FD  (CAN3)", size=8, halign="left")
    u = ic(s, "U13", (xc + 1.6, yc), [("VCC", "3"), ("TXD", "1"), ("RXD", "4"), ("GND", "2")],
           [("CANH", "7"), ("NC", "5"), ("NC", "8"), ("CANL", "6")], "U13\nTCAN332G", w=2.6, sp=1.0)
    nets_out(s, b, "U13", u, ["1", "4", "2"], "left")
    nets_out(s, b, "U13", u, ["5", "8"], "right")
    s.wire(u("3"), (u("3")[0] - 1.2, u("3")[1]))           # (VCC on top: its rail and decoupling cross no pin)
    s.wire((u("3")[0] - 1.2, u("3")[1]), (u("3")[0] - 1.2, u("3")[1] + 0.6))
    s.port((u("3")[0] - 1.2, u("3")[1] + 0.6), "3V3")
    c35 = s.add("C35", elm.Capacitor().at((u("3")[0] - 1.2, u("3")[1])).left().length(1.0), TERM2)
    s.gnd(c35.absanchors["end"])
    xj = u("7")[0] + 3.6
    jc = ic(s, "J4", (xj, u("7")[1]), [("CANH", "2"), ("5V", "1"), ("GND", "4"), ("CANL", "3")], [], "J4\nCAN",
            w=1.8, sp=1.0, anchor="2")
    s.wire(u("7"), jc("2"))
    s.wire(u("6"), jc("3"))
    xt = u("7")[0] + 1.4
    s.dot((xt, u("7")[1]))
    jp1 = s.add("JP1", elm.Switch().at((xt, u("7")[1])).down().length(1.5), TERM2)
    r21 = s.add("R21", elm.Resistor().at(jp1.absanchors["end"]).down().length(u("7")[1] - u("6")[1] - 1.5), TERM2)
    s.dot((xt, u("6")[1]))
    s.text((xt + 0.3, u("7")[1] - 1.5), "JP1\nR21 120R", size=6, halign="left")
    s.flag(jc("1"), "+5V", side="left")
    s.wire(jc("4"), (jc("4")[0] - 0.5, jc("4")[1]))
    s.gnd((jc("4")[0] - 0.5, jc("4")[1]))
    # LoRa beacon
    xl0, yl = 31.0, 11.0
    s.text((xl0, 17.2), "LoRa BEACON  -  RFM95W 915 MHz (SPI1)", size=8, halign="left")
    rf = ic(s, "U11", (xl0 + 6.0, yl), [("MISO", "2"), ("MOSI", "3"), ("SCK", "4"), ("NSS", "5"), ("3.3V", "13"),
                                         ("GND", "1"), ("GND", "8"), ("GND", "10")],
            [("ANT", "9"), ("RESET", "6"), ("DIO0", "14"), ("DIO1", "15"), ("DIO2", "16"), ("DIO3", "11"),
             ("DIO4", "12"), ("DIO5", "7")], "U11\nRFM95W", w=3.0, sp=0.6)
    nets_out(s, b, "U11", rf, ["2", "3", "4", "5", "1", "8", "10"], "left")
    nets_out(s, b, "U11", rf, ["6", "14", "15", "16", "11", "12", "7"], "right")
    ant = rf("9")
    s.wire(ant, (ant[0] + 1.0, ant[1]))
    s.add("J10", elm.Antenna().at((ant[0] + 1.0, ant[1])), {"1": "start"})
    s.text((ant[0] + 1.3, ant[1] + 0.9), "J10 u.FL", size=6, halign="left")
    # the u.FL shell (pad 2) is grounded: shown as its own ground
    jsh = (round(ant[0] + 2.0, 3), round(ant[1] + 0.2, 3))     # (beside the antenna it belongs to)
    s.pins[("J10", "2")] = jsh
    s.gnd(jsh)
    s.text((jsh[0] + 0.3, jsh[1] - 0.35), "J10 shell", size=6, halign="left")
    v3 = rf("13")
    xr = v3[0] - 2.2
    s.wire(v3, (xr, v3[1]))
    u12 = ic(s, "U12", (xr - 4.2, v3[1] - 0.6), [("IN", "1"), ("EN", "3")], [("OUT", "5"), ("NC", "4")],
             "U12 TPS7A2033", w=2.6, sp=0.6, bot=[("GND", "2")])
    s.gnd(u12("2"), lead=0.3)
    s.wire(u12("5"), (xr, u12("5")[1]), (xr, v3[1]))
    s.text((u12("4")[0] + 0.25, u12("4")[1]), "x", size=8, halign="center", valign="center")
    c38 = s.add("C38", elm.Capacitor().at((xr, v3[1])).down().length(1.0), TERM2)
    s.gnd(c38.absanchors["end"])
    s.dot((xr, v3[1]))
    c39 = s.add("C39", elm.Capacitor().at((xr + 1.0, v3[1])).down().length(1.0), TERM2)
    s.gnd(c39.absanchors["end"])
    s.dot((xr + 1.0, v3[1]))
    s.text((xr + 0.2, v3[1] + 0.35), "3V3R", size=6, halign="left")
    xi = u12("1")[0] - 0.8
    s.wire(u12("1"), (xi, u12("1")[1]), (xi, u12("3")[1]), u12("3"))
    s.dot((xi, u12("1")[1]))
    s.wire((xi, u12("1")[1]), (xi, u12("1")[1] + 0.8))
    s.port((xi, u12("1")[1] + 0.8), "+5V")
    c37 = s.add("C37", elm.Capacitor().at((xi, u12("3")[1])).down().length(1.0), TERM2)
    s.gnd(c37.absanchors["end"])
    s.dot((xi, u12("3")[1]))
    # status: WS2812 chain, PWR LED, buzzer, button, I2C pull-ups
    xs, ys = 30.8, 7.8
    s.text((xs + 1.2, 11.4), "STATUS LEDS (WS2812B chain)", size=8, halign="left")
    prev = None
    for i in range(3):                                   # (supply on top, ground below, data through the sides)
        l = ic(s, f"LED{i + 1}", (xs + 0.4 + 3.8 * i, ys - 1.2), [("DI", "3")], [("DO", "1")],
               f"LED{i + 1}", w=2.4, sp=1.0, top=[("VDD", "4")], bot=[("GND", "2")])
        s.text((xs + 0.4 + 3.8 * i + 1.6, ys - 1.75), ["STATUS", "SD", "FAULT"][i], size=6, halign="left")
        s.gnd(l("2"), lead=0.2)
        if prev is None:
            s.flag(l("3"), "LED_DIN", side="left")
        else:
            s.wire(prev, l("3"))
        prev = l("1")
        vd = l("4")
        s.wire(vd, (vd[0], vd[1] + 0.5), (vd[0] + 1.5, vd[1] + 0.5))     # (its decoupling outside the box)
        s.port((vd[0], vd[1] + 0.5), "+5V")
        c = s.add(f"C{40 + i}", elm.Capacitor().at((vd[0] + 1.5, vd[1] + 0.5)).down().length(0.7), TERM2)
        s.gnd(c.absanchors["end"])
    s.text((prev[0] + 0.25, prev[1]), "x", size=8, halign="center", valign="center")
    # right column: buzzer, button, pull-ups, PWR LED
    xr2 = 44.6
    s.text((xr2 - 3.0, 29.2), "UI", size=8, halign="left")
    s.port((xr2, 27.6), "+5V")
    bzp = s.add("BZ1", elm.Inductor2().at((xr2, 27.6)).down().length(1.4), TERM2)
    s.text((xr2 + 0.3, 26.9), "BZ1\nbuzzer", size=6, halign="left")
    dfl = s.add("D10", elm.Diode().at((xr2 - 1.2, bzp.absanchors["end"][1])).up().length(1.4), REV2)
    s.wire((xr2 - 1.2, 27.6), (xr2, 27.6))
    s.wire((xr2 - 1.2, bzp.absanchors["end"][1]), bzp.absanchors["end"])
    s.dot((xr2, 27.6))
    s.dot(bzp.absanchors["end"])
    s.text((xr2 - 1.5, 26.9), "D10", size=6, halign="right")
    q5 = s.add("Q5", elm.NFet().right().reverse().anchor("drain").at(bzp.absanchors["end"]),
               {"3": "drain", "2": "source", "1": "gate"})
    s.gnd(q5.absanchors["source"])
    gq = q5.absanchors["gate"]
    s.wire(gq, (gq[0] - 1.0, gq[1]))
    s.dot((gq[0] - 1.0, gq[1]))
    r20 = s.add("R20", elm.Resistor().at((gq[0] - 1.0, gq[1])).down().length(1.0), TERM2)
    s.gnd(r20.absanchors["end"])
    s.flag((gq[0] - 1.0, gq[1]), "BUZ", side="left")
    s.text((gq[0] + 0.2, gq[1] - 0.9), "Q5 2N7002\nR20 100k", size=6, halign="left")
    # button + pull-up
    yb = 21.5
    s.port((xr2, yb + 1.4), "3V3")
    r24 = s.add("R24", elm.Resistor().at((xr2, yb + 1.4)).down().length(1.4), TERM2)
    s.dot((xr2, yb))
    sw = s.add("SW1", elm.Button().at((xr2, yb)).down().length(1.2), TERM2)
    s.gnd(sw.absanchors["end"])
    s.flag((xr2, yb), "BTN", side="left")
    s.text((xr2 + 0.3, yb + 0.7), "R24 10k", size=6, halign="left")
    s.text((xr2 - 0.5, yb - 0.6), "SW1", size=6, halign="right")
    # I2C pull-ups
    yp = 17.4
    s.text((xr2 - 3.0, yp + 2.4), "I2C PULL-UPS", size=7, halign="left")
    for i, (ref, net) in enumerate((("R22", "SDA"), ("R23", "SCL"))):
        xx = xr2 - 1.0 - 1.4 * i
        s.port((xx, yp + 1.2), "3V3")
        r = s.add(ref, elm.Resistor().at((xx, yp + 1.2)).down().length(1.2), TERM2)
        s.flag(r.absanchors["end"], net, side="left" if i else "right")
    s.text((xr2 - 1.7, yp + 0.2), "R22 / R23  2k2", size=6, halign="center")
    # PWR LED
    yl2 = 13.8
    s.port((xr2, yl2 + 1.2), "3V3")
    r27 = s.add("R27", elm.Resistor().at((xr2, yl2 + 1.2)).down().length(1.2), TERM2)
    d11 = s.add("D11", elm.LED().at(r27.absanchors["end"]).down().length(1.2), REV2)
    s.gnd(d11.absanchors["end"])
    s.text((xr2 - 0.4, yl2 + 0.6), "R27 1k", size=6, halign="right")
    s.text((xr2 - 0.4, yl2 - 0.6), "D11 PWR", size=6, halign="right")
    # digital test points
    s.text((xr2 - 1.8, 11.9), "TEST POINTS", size=7, halign="left")
    for i, (ref, net) in enumerate((("TP22", "SCK"), ("TP23", "MOSI"), ("TP24", "MISO"), ("TP25", "SDA"),
                                    ("TP26", "SCL"), ("TP27", "IMU_INT"))):
        xx, yy2 = xr2 - 2.6 + 1.6 * (i % 2), 10.6 - 0.9 * (i // 2)
        tp(s, ref, (xx, yy2), net)
        s.flag((xx, yy2), net)
    tp(s, "TP5", (xr2, 7.0), "3V3")
    s.port((xr2, 7.0), "3V3", up=False)
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


# ---- sheet 3: sensors --------------------------------------------------------------------------------------------------
def sheet3(b, date):
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "SENSORS", "3 / 4", BOARD, date=date, note="SPI0 inertial bus; I2C baros + magnetometer")
    s.text((1.5, 30.6), "ANALOG 3.3 V  -  ferrite + its own LDO, at the sensor island", size=9, halign="left")
    y = 27.0
    s.port((2.0, y), "+5V", up=True)
    fb = s.add("FB1", elm.Inductor2().at((2.0, y)).right().length(2.0), TERM2, label="FB1 220R@100M",
               loc="bottom")
    p = fb.absanchors["end"]
    u4 = ic(s, "U4", (p[0] + 2.2, y - 1.2), [("IN", "1"), ("EN", "3")], [("OUT", "5"), ("NC", "4")], "U4\nTPS7A2033",
            w=2.8, sp=0.6, bot=[("GND", "2")])
    s.gnd(u4("2"), lead=0.3)
    s.wire(p, (p[0] + 1.4, p[1]), (p[0] + 1.4, u4("1")[1]), u4("1"))
    s.wire((p[0] + 1.4, u4("1")[1]), (p[0] + 1.4, u4("3")[1]), u4("3"))
    s.dot((p[0] + 1.4, u4("1")[1]))
    c32 = s.add("C32", elm.Capacitor().at((p[0] + 0.5, p[1])).down().length(1.2), TERM2)
    s.gnd(c32.absanchors["end"])
    s.dot((p[0] + 0.5, p[1]))
    s.text((p[0] + 0.3, p[1] + 0.35), "5VA", size=6, halign="left")
    s.text((u4("4")[0] + 0.25, u4("4")[1]), "x", size=8, halign="center", valign="center")
    vo = u4("5")
    x = vo[0] + 1.0
    s.wire(vo, (x + 4.0, vo[1]))
    s.port((x + 4.0, vo[1]), "3V3A")
    for i, (ref, val) in enumerate((("C33", "2u2"), ("C34", "10u"))):
        xx = x + 1.0 * i
        s.dot((xx, vo[1]))
        c = s.add(ref, elm.Capacitor().at((xx, vo[1])).down().length(1.2), TERM2)
        s.gnd(c.absanchors["end"])
    tp_on(s, "TP6", (x + 2.6, vo[1]), "3V3A", dy=0.8)
    s.dot((x + 2.6, vo[1]))
    s.text((22.0, 28.8), "Sensor datum: U5 (ICM-42688-P) centre, board X 130.0 Y 25.0 mm (silkscreened).", size=7,
           halign="left")
    s.text((22.0, 28.0), "Every sensor on 3V3A; SPI0 at 24 MHz with one chip select each; I2C at 400 kHz.", size=7,
           halign="left")

    def sensor(ref, at, left, right, label, caps, w=4.4, sp=0.6, cap_pins=None):
        a = ic(s, ref, at, left, right, label, w=w, sp=sp)
        pn = b.pad_net()
        supply = [p for _, p in left if pn.get((ref, p)) in ("3V3A", "MAG_CAP")]
        nets_out(s, b, ref, a, [p for _, p in left if p not in supply], "left")
        nets_out(s, b, ref, a, [p for _, p in right], "right")
        # supply pins: tied to a 3V3A rail stub above the box with their decoupling caps
        xs = a(supply[0])[0] - 1.4 if supply else None
        for p in supply:
            q = a(p)
            n = pn[(ref, p)]
            s.wire(q, (xs, q[1]))
            if n == "3V3A":
                s.dot((xs, q[1]))
        if supply:
            ys = [a(p)[1] for p in supply if pn[(ref, p)] == "3V3A"]
            top = max(ys)
            if len(ys) > 1:
                s.wire((xs, min(ys)), (xs, top))
            s.wire((xs, top), (xs, top + 0.8))
            s.port((xs, top + 0.8), "3V3A")
            for i, (cref, val) in enumerate(caps):
                xc = xs - 1.0 - 1.0 * i
                s.wire((xc + 1.0, top + 0.5), (xc, top + 0.5)) if i == 0 else s.wire((xc + 1.0, top + 0.5), (xc, top + 0.5))
                c = s.add(cref, elm.Capacitor().at((xc, top + 0.5)).down().length(1.0), TERM2)
                s.gnd(c.absanchors["end"])
            if caps:
                s.dot((xs, top + 0.5))
        return a

    sensor("U5", (8.0, 16.5), [("VDD", "8"), ("VDDIO", "5"), ("AP_CS", "12"), ("AP_SCLK", "13"), ("AP_SDI", "14"),
                               ("AP_SDO", "1"), ("RESV", "7"), ("GND", "6")],
           [("INT1", "4"), ("INT2", "9"), ("RESV", "2"), ("RESV", "3"), ("RESV", "10"), ("RESV", "11")],
           "U5\nICM-42688-P\nprimary IMU", [("C20", "100n"), ("C21", "2u2"), ("C22", "10n")], w=5.4)
    sensor("U6", (8.0, 6.0), [("VDD", "3"), ("VDDIO", "11"), ("CSB1", "14"), ("CSB2", "5"), ("SCK", "8"), ("SDI", "9"),
                              ("SDO1", "15"), ("SDO2", "10"), ("PS", "7"), ("GNDA", "4"), ("GNDIO", "6"), ("NC", "2")],
           [("INT1", "16"), ("INT2", "1"), ("INT3", "12"), ("INT4", "13")], "U6\nBMI088\nredundant IMU",
           [("C24", "100n"), ("C25", "100n")], sp=0.55)
    sensor("U7", (24.0, 16.5), [("VS", "6"), ("VDDIO", "1"), ("RES", "3"), ("CS", "7"), ("SCLK", "14"), ("SDI", "13"),
                                ("SDO", "12"), ("GND", "2"), ("GND", "4"), ("GND", "5"), ("RES", "11")],
           [("INT1", "8"), ("INT2", "9"), ("NC", "10")], "U7\nADXL375\n+-200 g", [("C26", "1u"), ("C27", "100n")],
           sp=0.55)
    sensor("U10", (24.0, 6.0), [("VDD", "2"), ("VDDIO", "13"), ("CS", "4"), ("SCL", "1"), ("SDA", "16"), ("CAP", "10"),
                                ("GND", "9"), ("GND", "11")],
           [("SDO", "5"), ("INT", "15"), ("NC", "3"), ("NC", "6"), ("NC", "7"), ("NC", "8"), ("NC", "12"), ("NC", "14")],
           "U10\nMMC5983MA\nmagnetometer 0x30", [("C28", "1u")], sp=0.55)
    # the magnetometer's SET / RESET reservoir on CAP
    a10 = lambda p: s.pins[("U10", p)]
    q = a10("10")
    s.wire(q, (q[0] - 1.6, q[1]))
    c29 = s.add("C29", elm.Capacitor().at((q[0] - 1.6, q[1])).down().length(0.9), TERM2)
    s.gnd(c29.absanchors["end"])
    s.text((q[0] - 1.9, q[1] - 0.5), "C29 10u", size=6, halign="right")
    sensor("U8", (37.0, 16.5), [("VDD", "10"), ("VDDIO", "1"), ("CSB", "6"), ("SDO", "5"), ("SCK", "2"), ("SDI", "4"),
                                ("VSS", "3"), ("VSS", "8"), ("VSS", "9")],
           [("INT", "7")], "U8 BMP390\nbaro 1, I2C 0x77", [("C30", "100n")], w=4.4, sp=0.55)
    sensor("U9", (37.0, 6.0), [("VDD", "1"), ("PS", "2"), ("CSB", "4"), ("CSB", "5"), ("SCLK", "8"), ("SDA", "7"),
                               ("GND", "3")],
           [("SDO", "6")], "U9 MS5607\nbaro 2, I2C 0x76", [("C31", "100n")], w=4.4, sp=0.55)
    tp(s, "TP29", (44.0, 26.5), "GND")
    s.gnd((44.0, 26.5))
    s.text((43.0, 27.4), "scope GND", size=6, halign="right")
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


# ---- sheet 4: pyro + servo ---------------------------------------------------------------------------------------------
def sheet4(b, date):
    import twilight_fc as fc
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, W, H, "PYRO + SERVO", "4 / 4", BOARD, date=date, note="pyro: switch AND ARM_EN AND gate")
    s.text((1.5, 30.6), "PYRO SUPPLY + ARMING  -  current flows only with the keyed switch closed AND ARM_EN high AND the "
           "channel's gate driven", size=9, halign="left")
    y = 27.0
    s.port((1.6, y), "VIN")
    f2 = s.add("F2", elm.Fuse().at((1.6, y)).right().length(1.8), TERM2, label="F2 7A", loc="top")
    d2 = s.add("D2", elm.Schottky().at(f2.absanchors["end"]).right().length(1.8), REV2, label="D2 PDS540", loc="top")
    vp = d2.absanchors["end"]
    s.netname(vp, "VPYRO", dx=0.2, dy=0.3)
    s.dot((vp[0] + 0.8, y))
    leg(s, "C17", elm.Capacitor(polar=True), vp[0] + 0.8, y, None, length=1.4)
    s.text((vp[0] + 1.1, y - 0.8), "C17\n470u 35V", size=6, halign="left")
    tp_on(s, "TP7", (vp[0] + 2.0, y), "VPYRO")
    ja = s.add("J15", elm.Switch().at((vp[0] + 2.8, y)).right().length(2.2), {"2": "start", "1": "end"})
    s.wire(vp, ja.absanchors["start"])
    s.text((vp[0] + 3.9, y + 0.9), "J15  ARM SWITCH\n(external, keyed)", size=6, halign="center")
    xq = ja.absanchors["end"][0] + 4.0
    s.wire(ja.absanchors["end"], (xq, y))
    s.netname((ja.absanchors["end"][0] + 0.2, y), "VPYRO_SW", dx=0.0, dy=0.3)
    # Q3: source (pins 1-3) on the switch side, drain (5-8) to VPYRO_ARMED, gate below
    q3 = s.add("Q3", elm.PFet().up().reverse().anchor("source").at((xq, y)),
               {"1": "source", "2": "source", "3": "source", "4": "gate", "5": "drain", "6": "drain", "7": "drain",
                "8": "drain"})
    s.text((xq + 0.75, y + 0.6), "Q3 SQ4401EY", size=6, halign="center")
    gate = q3.absanchors["gate"]
    yg = y - 2.4
    xr, xz = xq - 1.2, xq - 2.4
    for ref, x_, e_, pins_ in (("R18", xr, elm.Resistor(), TERM2), ("DZ2", xz, elm.Zener().reverse(), {"1": "start", "2": "end"})):
        s.dot((x_, y))
        s.add(ref, e_.at((x_, y)).down().length(y - yg), pins_)
    s.wire((xz, yg), (gate[0], yg), gate)
    s.dot((xr, yg))
    s.dot((gate[0], yg))
    s.text((xz - 0.3, (y + yg) / 2), "DZ2\n12V", size=6, halign="right")
    s.text((xr + 0.3, yg - 0.4), "R18 100k", size=6, halign="left")
    r17 = s.add("R17", elm.Resistor().at((gate[0], yg)).down().length(1.2), TERM2)
    q4 = s.add("Q4", elm.NFet().right().reverse().anchor("drain").at(r17.absanchors["end"]),
               {"3": "drain", "2": "source", "1": "gate"})
    s.gnd(q4.absanchors["source"])
    g4 = q4.absanchors["gate"]
    xe = g4[0] - 1.2
    s.wire(g4, (xe, g4[1]))
    s.dot((xe, g4[1]))
    r16 = s.add("R16", elm.Resistor().at((xe, g4[1])).down().length(1.0), TERM2)
    s.gnd(r16.absanchors["end"])
    s.flag((xe, g4[1]), "ARM_EN", side="left")
    tp_on(s, "TP20", (xe, g4[1]), "ARM_EN", dx=0.0, dy=0.8)
    arm = q3.absanchors["drain"]
    xa = arm[0] + 2.2
    xbus = 26.0
    s.wire(arm, (xbus, arm[1]))
    s.netname((arm[0] + 0.3, arm[1]), "VPYRO_ARMED", dx=0.0, dy=0.3)
    tp_on(s, "TP8", (arm[0] + 1.0, arm[1]), "VPYRO_ARMED", dy=-0.8)
    s.dot((xa, arm[1]))
    r15 = s.add("R15", elm.Resistor().at((xa, arm[1])).down().length(1.2), TERM2)
    d7 = s.add("D7", elm.LED().at(r15.absanchors["end"]).down().length(1.2), REV2)
    s.gnd(d7.absanchors["end"])
    s.text((xa + 0.3, arm[1] - 1.2), "R15 4k7 1206\nD7 ARM (red)", size=6, halign="left")
    xi = xa + 3.0
    s.dot((xi, arm[1]))
    r19 = s.add("R19", elm.Resistor().at((xi, arm[1])).up().length(1.2), REV2)
    d8 = s.add("D8", elm.Schottky().at(r19.absanchors["end"]).up().length(1.2).reverse(), {"1": "start", "2": "end"})
    s.port(d8.absanchors["end"], "3V3")
    s.text((xi + 0.55, arm[1] + 2.6), "continuity injection", size=6, halign="left")
    # four fire channels, one row each, their e-match terminals on the VPYRO_ARMED bus
    s.text((1.5, 21.2), "FIRE CHANNELS  (x4)", size=8, halign="left")
    yrows = [18.6 - 4.2 * n for n in range(4)]
    for n, yr in enumerate(yrows):
        x0 = 3.6
        s.flag((x0, yr), f"FIRE{n + 1}", side="left")
        rg = s.add(f"RG{n + 1}", elm.Resistor().at((x0, yr)).right().length(1.6), TERM2)
        gnode = rg.absanchors["end"]
        s.dot(gnode)
        rd = s.add(f"RD{n + 1}", elm.Resistor().at(gnode).down().length(1.2), TERM2)
        s.gnd(rd.absanchors["end"])
        tp_on(s, f"TP{12 + n}", gnode, f"GATE{n + 1}", dy=0.7)
        q = elm.NFet().right().reverse().anchor("gate").at((gnode[0] + 1.6, gnode[1]))
        s.add(f"QF{n + 1}", q, {"3": "drain", "1": "source", "2": "gate"})
        s.wire(gnode, q.absanchors["gate"])
        s.gnd(q.absanchors["source"])
        dn = q.absanchors["drain"]
        yd = dn[1] + 0.5
        s.wire(dn, (dn[0], yd))
        s.netname((dn[0], yd), f"PYRO{n + 1}", dx=0.2, dy=0.25)
        s.text((gnode[0] - 1.0, yr + 0.35), f"RG{n + 1} 100R", size=6, halign="center")
        s.text((gnode[0] + 0.55, yr - 0.75), f"RD{n + 1}\n10k", size=6, halign="left")
        s.text((dn[0] - 0.35, yd + 0.25), f"QF{n + 1} CSD17573Q5B", size=6, halign="right")
        ra = s.add(f"RA{n + 1}", elm.Resistor().at((dn[0] + 1.2, yd)).down().length(1.0), TERM2)
        s.dot((dn[0] + 1.2, yd))
        cn = ra.absanchors["end"]
        rb = s.add(f"RB{n + 1}", elm.Resistor().at(cn).down().length(1.0), TERM2)
        s.gnd(rb.absanchors["end"])
        s.dot(cn)
        s.wire(cn, (cn[0] + 1.4, cn[1]))
        s.ports.append(((cn[0] + 1.4, cn[1]), f"CONT{n + 1}"))
        s.text((cn[0] + 1.5, cn[1]), f"CONT{n + 1}", size=6, halign="left", valign="center")
        tp_on(s, f"TP{16 + n}", (cn[0] + 0.7, cn[1]), f"CONT{n + 1}", dx=0.0, dy=-0.7)
        rl = s.add(f"RL{n + 1}", elm.Resistor().at((dn[0] + 5.6, yd)).down().length(1.0), TERM2)
        s.dot((dn[0] + 5.6, yd))
        dc = s.add(f"DC{n + 1}", elm.LED().at(rl.absanchors["end"]).down().length(1.0), REV2)
        s.gnd(dc.absanchors["end"])
        jt = ic(s, f"J{11 + n}", (xbus - 2.4, yd), [("", "2")], [("", "1")], f"J{11 + n}\nPYRO {n + 1}", w=1.4, sp=0.8,
                anchor="2")
        s.wire((dn[0], yd), jt("2"))
        s.wire(jt("1"), (xbus, jt("1")[1]))
        s.dot((xbus, jt("1")[1]))
    s.wire((xbus, arm[1]), (xbus, yrows[-1] + 0.5))
    s.text((xbus + 0.3, yrows[-1] + 1.4), "VPYRO_ARMED", size=6, halign="left")
    # servo outputs: inputs pulled down (defined through reset), buffered to 5 V, 100R, the 3 x 6 block
    xs0 = 29.6
    s.text((xs0 - 2.0, 29.4), "SERVO OUTPUTS  -  74HCT244 on +5 V", size=8, halign="left")
    sp = 2.0                                             # (a pull-down and its ground fit inside each row)
    u14 = ic(s, "U14", (xs0, 6.6), [("1A0", "2"), ("1A1", "4"), ("1A2", "6"), ("1A3", "8"), ("2A0", "17"), ("2A1", "15"),
                                     ("2A2", "13"), ("2A3", "11")],
             [("1Y0", "18"), ("1Y1", "16"), ("1Y2", "14"), ("1Y3", "12"), ("2Y0", "3"), ("2Y1", "5"), ("2Y2", "7"),
              ("2Y3", "9")], "U14\n74HCT244", w=5.2, sp=sp, top=[("VCC", "20")],
             bot=[("1OE", "1"), ("2OE", "19"), ("GND", "10")])
    for p in ("13", "11"):                               # (the two spare inputs held low)
        q = u14(p)
        s.wire(q, (q[0] - 0.4, q[1]))
    s.wire((u14("13")[0] - 0.4, u14("13")[1]), (u14("11")[0] - 0.4, u14("11")[1]))
    s.gnd((u14("11")[0] - 0.4, u14("11")[1]))
    for i in range(6):
        q = u14(fc.BUF_IN[i])
        node = (q[0] - 0.9, q[1])
        s.wire(q, node, (q[0] - 1.8, q[1]))
        s.dot(node)
        s.flag((q[0] - 1.8, q[1]), f"SV{i + 1}", side="left")
        rp = s.add(f"RP{i + 1}", elm.Resistor().at(node).down().length(1.0), TERM2)   # (with its ground, inside
        s.gnd(rp.absanchors["end"])                                                     # its own row)
    bots = [u14(p) for p in ("1", "19", "10")]
    s.wire(bots[0], (bots[0][0], bots[0][1] - 0.5), (bots[-1][0], bots[-1][1] - 0.5))
    for q in bots[1:]:
        s.wire(q, (q[0], q[1] - 0.5))
        if q != bots[-1]:
            s.dot((q[0], q[1] - 0.5))
    s.gnd((bots[-1][0], bots[-1][1] - 0.5))
    v = u14("20")
    s.wire(v, (v[0], v[1] + 0.8), (v[0] + 1.6, v[1] + 0.8))
    s.port((v[0] + 1.6, v[1] + 0.8), "+5V")
    c23 = s.add("C23", elm.Capacitor().at((v[0] + 0.8, v[1] + 0.8)).down().length(0.7), TERM2)
    s.dot((v[0] + 0.8, v[1] + 0.8))
    s.gnd(c23.absanchors["end"])
    s.text((v[0] + 1.1, v[1] + 0.2), "C23 100n", size=5, halign="left")
    for p in ("7", "9"):
        q = u14(p)
        s.text((q[0] + 0.25, q[1]), "x", size=8, halign="center", valign="center")
    # each servo: 100R, then its column of the 3 x 6 block (signal, VSERVO, GND)
    xb = u14("18")[0] + 2.6
    xv = xb + 4.0                                        # (clear of the J3 pin ends)
    vys, gys = [], []
    for i in range(6):
        q = u14(fc.BUF_OUT[i])
        ro = s.add(f"RO{i + 1}", elm.Resistor().at((q[0] + 0.4, q[1])).right().length(1.6), TERM2)
        s.wire(q, (q[0] + 0.4, q[1]))
        s.wire(ro.absanchors["end"], (xb, q[1]))
        j = ic(s, "J3", (xb, q[1]), [("S", str(3 * i + 1))], [("+", str(3 * i + 2)), ("-", str(3 * i + 3))],
               f"SERVO {i + 1}", w=2.6, sp=0.45, anchor=str(3 * i + 1))
        g = j(str(3 * i + 3))
        s.wire(g, (xv + 0.6, g[1]))                     # (every "-" to one ground bus right of the VSERVO bus)
        gys.append(g[1])
        s.wire(j(str(3 * i + 2)), (xv, j(str(3 * i + 2))[1]))
        vys.append(j(str(3 * i + 2))[1])
        if i < 5:                                        # (the bus runs on up to VSERVO: the bottom end is a corner)
            s.dot((xv, j(str(3 * i + 2))[1]))
    s.wire((xv + 0.6, max(gys)), (xv + 0.6, min(gys)))
    s.gnd((xv + 0.6, min(gys)))
    tops = [u14(fc.BUF_OUT[0]), u14(fc.BUF_OUT[5])]
    s.wire((xv, min(vys)), (xv, tops[0][1] + 2.0))
    s.port((xv, tops[0][1] + 2.0), "VSERVO")
    s.text((xb + 1.5, u14("18")[1] + 1.0), "J3  (3 x 6 block)", size=6, halign="center")
    tp(s, "TP21", (44.5, 29.0), "GND")
    s.gnd((44.5, 29.0))
    s.text((43.6, 29.9), "scope GND", size=6, halign="right")
    s.autoname()                                         # (each part its own name and value, beside it)
    return s


def draw(b, out_dir=None, stem="schematic"):
    date = datetime.date.today().isoformat()
    sheets = [sheet1(b, date), sheet2(b, date), sheet3(b, date), sheet4(b, date)]
    probs = check_sheets(b, sheets)
    for i, s in enumerate(sheets, 1):                    # (and as a reader sees it: nothing overlapping, dots right)
        probs += [f"sheet {i}: {p}" for p in s.layout_problems() + s.dot_problems() + s.label_problems()]
    if out_dir:
        save_sheets(sheets, out_dir, stem)
    return probs


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.environ["TWILIGHT_BUILD"] = "0"
    import twilight_fc as fc
    b, _ = fc.build(do_route=False)
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/twilight_sheet"
    os.makedirs(out, exist_ok=True)
    for p in draw(b, out):
        print(p)
