"""The Pioneer Cube main board's schematic: one A3 sheet, checked against the board's netlist before it is saved.
Power is wired (XT30 -> fuse -> the battery rail, the bulk capacitor, the 5 V buck to the DevKit's VIN); the modules'
logic pins carry net names, as is usual between an MCU and its drivers and sensors.

    python cube_board_sheet.py [out_dir]       (default boards/cube_main)
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", ".."), HERE]
import schemdraw.elements as elm  # noqa: E402
from schem import Sheet, check_sheets, save_sheets, TERM2, REV2, ic, nets_out, conn, notes  # noqa: E402

W, H = 46.0, 32.5
BOARD = "PIONEER CUBE MAIN r1  (cad_bench/projects/balancing_cube/cube_board.py)"


def draw(b, out_dir=None):
    import cube_board as CB
    s = Sheet(b)
    s.frame(0, 0, W, H, "PIONEER CUBE - MAIN BOARD", "1/1", BOARD, rev="A", date=str(datetime.date.today()),
            note="3S LiPo, ESP32 DevKit V1, 3 x SimpleFOC Mini v2.3, GY-521")
    # ---- power: XT30 -> 15 A fuse -> VBAT; bulk cap; 5 V buck -> DevKit VIN
    j1 = conn(s, "J1", (1.5, 27.0), [("+", "2"), ("-", "1")], "J1 XT30\nbattery", side="right", w=1.6)
    yv = j1("2")[1]
    f1 = s.add("F1", elm.Fuse().at((j1("2")[0] + 0.8, yv)).right().length(2.4), TERM2, label="F1 15 A", loc="top")
    s.wire(j1("2"), f1.absanchors["start"])
    s.netname(f1.absanchors["start"], "VBAT_IN", dx=-0.1, dy=-0.55)
    xr = f1.absanchors["end"][0] + 1.2
    s.wire(f1.absanchors["end"], (xr + 6.0, yv))
    s.netname((xr + 6.0, yv), "VBAT", dx=0.2, dy=0.25)
    s.port((xr + 6.0, yv), "VBAT", lead=0.6)
    c1 = s.add("C1", elm.Capacitor(polar=True).at((xr, yv)).down().length(1.6), TERM2, label="C1 470u 25V", loc="bottom")
    s.gnd(c1.absanchors["end"])
    s.wire(j1("1"), (j1("1")[0] + 0.6, j1("1")[1]))
    s.gnd((j1("1")[0] + 0.6, j1("1")[1]))
    u1 = ic(s, "U1", (xr + 2.6, yv - 5.4), [("VIN", "3"), ("SHDN", "2"), ("PG", "1")], [("VOUT", "5"), ("GND", "4")],
            "", w=3.0, sp=0.7)
    s.text((xr + 4.1, yv - 5.4 + 4 * 0.7 + 0.35), "U1 D24V10F5 (5 V 1 A buck)", size=7, halign="center")
    s.wire(u1("3"), (u1("3")[0] - 0.8, u1("3")[1]), (u1("3")[0] - 0.8, yv - 1.0), (xr + 5.0, yv - 1.0), (xr + 5.0, yv))
    for p in ("2", "1"):
        q = u1(p)
        s.text((q[0] - 0.25, q[1]), "x", size=8, halign="center", valign="center")
    s.wire(u1("4"), (u1("4")[0] + 0.6, u1("4")[1]))
    s.gnd((u1("4")[0] + 0.6, u1("4")[1]))
    s.flag(u1("5"), "5V")
    # ---- ESP32 DevKit V1 on two 15-way sockets
    h1n = CB.PIN_NAMES["H1"]
    h2n = CB.PIN_NAMES["H2"]
    a2 = ic(s, "H2", (4.6, 2.0), [(h2n[str(i)], str(i)) for i in range(1, 16)], [], "", w=3.0, sp=0.6)
    nets_out(s, b, "H2", a2, [str(i) for i in range(1, 16)], "left", gnd_flag=True)
    a1 = ic(s, "H1", (11.0, 2.0), [], [(h1n[str(i)], str(i)) for i in range(1, 16)], "", w=3.0, sp=0.6)
    nets_out(s, b, "H1", a1, [str(i) for i in range(1, 16)], "right", gnd_flag=True)
    s.text((4.6, 12.3), "H2 / H1: ESP32 DevKit V1 (DOIT, 30 pin) on 2 x 15 sockets", size=7, halign="left")
    # ---- the three drivers, their lead connectors
    for i, k in enumerate("XYZ"):
        x0 = 24.6 + 7.6 * i
        dh = ic(s, f"D{k}H", (x0, 18.6), [(n, str(2 * j + 2)) for j, n in enumerate(CB.MINI_PINS["A"])],
                [(n, str(2 * j + 1)) for j, n in enumerate(CB.MINI_PINS["B"])], "", w=2.2, sp=0.6)
        s.text((x0 + 1.1, 23.1), f"D{k}H Mini {k}", size=7, halign="center")
        nets_out(s, b, f"D{k}H", dh, [str(2 * j + 2) for j in range(6)], "left")
        nets_out(s, b, f"D{k}H", dh, [str(2 * j + 1) for j in range(5)], "right")
        dm = conn(s, f"D{k}M", (x0, 14.0), [("M1", "1"), ("M2", "2"), ("M3", "3")], f"D{k}M motor", side="right", w=1.9)
        nets_out(s, b, f"D{k}M", dm, ["1", "2", "3"], "right")
        p2 = conn(s, f"D{k}P2", (x0, 11.0), [("VM+", "1")], f"D{k}P2", side="right", w=1.9)
        s.flag(p2("1"), "VBAT")
        p1 = conn(s, f"D{k}P1", (x0, 8.4), [("VM-", "1")], f"D{k}P1", side="right", w=1.9)
        s.wire(p1("1"), (p1("1")[0] + 0.6, p1("1")[1]))
        s.gnd((p1("1")[0] + 0.6, p1("1")[1]))
        jm = conn(s, f"JM{k}", (x0 + 0.4, 25.4), [("A", "1"), ("B", "2"), ("C", "3")], f"JM{k} MR30", side="left", w=1.9)
        nets_out(s, b, f"JM{k}", jm, ["1", "2", "3"], "left")
    # encoders and the IMU
    for i, k in enumerate("XYZ"):
        je = conn(s, f"JE{k}", (2.0 + 6.2 * i, 14.2), [(n, str(j + 1)) for j, n in enumerate("3V3 GND SCK MISO MOSI CS".split())],
                  f"JE{k} encoder", side="right", w=1.9)
        nets_out(s, b, f"JE{k}", je, [str(j) for j in range(1, 7)], "right", gnd_flag=True)
    u2 = conn(s, "U2", (19.8, 6.6), [(n, str(j + 1)) for j, n in enumerate("VCC GND SCL SDA XDA XCL AD0 INT".split())],
              "U2 GY-521\nMPU-6050", side="right", w=1.9)
    nets_out(s, b, "U2", u2, [str(j) for j in range(1, 9)], "right", gnd_flag=True)
    notes(s, 19.8, 5.4, ["Drivers: SimpleFOC Mini v2.3 on their own headers;",
                         "their CS1-3 and 3V3 out are not used.",
                         "Encoder pinout is this board's (GL30 lead ASSUMED",
                         "SPI, AS5048A-class). J1 pin 2 is + (AMASS)."])
    s.autoname()
    probs = check_sheets(b, [s])
    probs += s.layout_problems() + s.dot_problems() + s.label_problems()
    if out_dir and not probs:
        os.makedirs(out_dir, exist_ok=True)
        save_sheets([s], out_dir)
    return probs, s


if __name__ == "__main__":
    import cube_board as CB
    b, _ = CB.routed(store=os.path.join(HERE, "..", "routes"))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "boards", "cube_main")
    probs, s = draw(b, out)
    print(len(probs), "problems")
    for p in probs[:60]:
        print(" ", p)
    if probs:
        s.save(os.path.join(out, "schematic_draft.png"))
