"""4-channel low-side N-MOSFET driver: four AO3400A (30 V, logic-level: RDS(on) ~ 33 mOhm at VGS 4.5 V, ~ 50 mOhm
at 2.5 V) switch four loads (solenoids, relays, motors, LED strips) to ground from 3.3 V or 5 V logic. Per channel:
100R gate resistor, 100k gate pull-down (the load stays off while the MCU boots or the header is unplugged), SS34
flyback diode across the load (cathode to V+), and a green LED on the logic input. 5.0 mm screw terminals: V+ / GND
in (5-24 V, 8 A total) and one V+ / OUT pair per load (2 A each); a 1x6 2.54 mm logic header (GND, IN1-IN4, GND).
Difficulty: 2/5. 2 layers, 67 x 30 mm, JLCPCB, 4 x M3 holes; V+ poured on top along the terminal row, ground poured
on the bottom.

Choices and assumptions:
- AO3400A pins from KiCad's symbol (1 G, 2 S, 3 D; AOS SOT-23 drawing). 2 A x 50 mOhm = 0.2 W per FET in SOT-23:
  fine at 25 C; at 3.3 V drive the RDS(on) is near its 2.5 V figure.
- Flyback diode SS34 (3 A, 40 V Schottky, SMA): reverse voltage above V+ max, forward current above the 2 A load.
- Input LED from the logic input through 1k (1.3 mA at 3.3 V, 3 mA at 5 V): lights when the channel is commanded on,
  independent of V+.
- V+ bulk: 100 uF 35 V electrolytic across the input terminal (soaks up the inductive loads' turn-off energy that the
  flyback diodes return to V+).
- Currents: each OUT and its FET 2 A; V+ and GND 8 A (all four on).
- Layout: each channel's 2 A path is drawn by hand (drain -> flyback anode 1.0 mm on top, three 0.4 mm vias, 1.5 mm
  on the bottom under the V+ pour to the terminal); each FET source sits in a small top ground pad with three 0.4 mm
  vias to the bottom ground pour. GND tracks are 0.4 mm ties only (width=0.4): the pours carry the 8 A. Terminal
  pin 1 = V+ on all five (J1 pin 2 = GND, J2-J5 pin 2 = OUT1-4). Header J6: 1 GND, 2-5 IN1-IN4, 6 GND.

    cd boards/08_mosfet_4ch_driver && prometheus board.py out/ --spec req.json --json --no-view
"""
from pcb import Board

R06 = "Resistor_SMD:R_0603_1608Metric"
TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal"
W, H = 67.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
TY = 5.3                                              # the terminal row (wire entries face the bottom edge)
XS = [4.5 + 12.0 * i for i in range(5)]               # J1 (V+ in), J2..J5 (loads)


def build():
    b = Board("mosfet_4ch_driver", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", TB, (XS[0], TY), 0, "V+ IN", "Phoenix 1729128")
    for k in range(4):
        b.place(f"J{k + 2}", TB, (XS[k + 1], TY), 0, f"LOAD{k + 1}", "Phoenix 1729128")
    b.place("J6", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (W / 2 + 6.35, H - 2.6), 90, "LOGIC",
            "XFCN PZ254V-11-06P", lcsc="C492405")
    for x, y in ((3.0, 13.0), (W - 3.5, 3.5), (3.5, H - 3.5), (W - 3.5, H - 3.5)):
        b.hole((x, y), 3.2)
    b.place("C1", "Capacitor_SMD:CP_Elec_8x10", (9.5, 17.5), 90, "100u 35V", "Panasonic EEE-FK1V101P", note="V+ bulk")
    for k in range(4):
        n = k + 1
        b.place(f"Q{n}", None, (XS[k + 1] + 2.5, 18.5), 0, "AO3400A", "AOS AO3400A",
                symbol="Transistor_FET:AO3400A", lcsc="C20917")
        b.place(f"D{n}", "Diode_SMD:D_SMA", (XS[k + 1] + 7.0, 13.0), 180, "SS34", "MDD SS34", note="flyback")
        b.place(f"R{n}", R06, None, 0, "100", note="gate")
        b.place(f"R{n + 4}", R06, None, 0, "100k", note="gate pull-down")
        b.place(f"R{n + 8}", R06, None, 0, "1k", note="LED")
        b.place(f"D{n + 4}", "LED_SMD:LED_0603_1608Metric", None, 0, "green", "Hubei KENTO KT-0603G", lcsc="C72043")

    vplus = [("J1", "1"), ("C1", "1")] + [(f"J{k + 2}", "1") for k in range(4)] + [(f"D{k + 1}", "1") for k in range(4)]
    gnd = [("J1", "2"), ("C1", "2"), ("J6", "1"), ("J6", "6")] + \
          [(f"Q{n}", "S") for n in range(1, 5)] + [(f"R{n + 4}", "2") for n in range(1, 5)] + \
          [(f"D{n + 4}", "1") for n in range(1, 5)]
    b.net("V+", vplus, amps=8.0, volts=24.0)
    b.net("GND", gnd, amps=8.0, width=0.4)      # (its tracks only tie the small pins in: the pours carry the 8 A)
    for n in range(1, 5):
        b.net(f"OUT{n}", [(f"J{n + 1}", "2"), (f"Q{n}", "D"), (f"D{n}", "2")], amps=2.0, volts=24.0, width=1.0)
        b.net(f"IN{n}", [("J6", str(n + 1)), (f"R{n}", "1"), (f"R{n + 8}", "1")])
        b.net(f"G{n}", [(f"R{n}", "2"), (f"Q{n}", "G"), (f"R{n + 4}", "1")])
        b.net(f"LED{n}", [(f"R{n + 8}", "2"), (f"D{n + 4}", "2")])
    vp = [(0, 0), (W, 0), (W, 11.5)]                  # V+ along the terminal row, a tab up to each flyback cathode
    for x in reversed(XS[1:]):
        vp += [(x + 10.4, 11.5), (x + 10.4, 15.2), (x + 7.6, 15.2), (x + 7.6, 11.5)]
    vp += [(0, 11.5)]
    b.pour("V+", "F", vp)
    b.pour("GND", "B", OUT)
    for x in XS[1:]:                                  # the 2 A paths drawn by hand, not left to the router:
        b.track(f"OUT{XS.index(x)}", "F", [b.pad(f"Q{XS.index(x)}", "3"), (x + 5.0, 17.0), (x + 5.0, 13.0)], 1.0)
        for v in ((x + 4.3, 15.3), (x + 5.7, 15.3), (x + 5.0, 16.4)):
            b.via(f"OUT{XS.index(x)}", v, 0.4, 0.8)        # drain -> anode on top, three vias down,
        b.track(f"OUT{XS.index(x)}", "B", [(x + 5.0, 16.4), (x + 5.0, TY)], 1.5)   # under the V+ pour to the terminal
        b.pour("GND", "F", [(x - 0.6, 14.4), (x + 2.3, 14.4), (x + 2.3, 18.0), (x - 0.6, 18.0)])
        for v in ((x + 0.2, 15.1), (x + 1.6, 15.1), (x + 0.2, 16.5)):
            b.via("GND", v, 0.4, 0.8)                     # the source to the bottom ground through three vias
    return b
