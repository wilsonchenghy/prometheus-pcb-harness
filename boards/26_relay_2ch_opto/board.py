"""2-channel relay module: two 5 V Sanyou SRD relays, each driven by an S8050 NPN through a PC817 optocoupler (the
logic side isolated from the coil side, as the common "JD-VCC" modules do), a flyback diode across each coil, an LED
per channel, a 4-pin logic header (VCC, IN1, IN2, GND; active low, as common modules), a 3-pin coil-supply header
(JD-VCC, VCC, GND: jumper JD-VCC to VCC for one supply) and a 3-way 5.08 mm screw terminal per relay (NO, COM, NC).
Difficulty 2/5. 2 layers, 56 x 46 mm, JLCPCB.

For low-voltage loads only (SELV, <= 30 V DC, 5 A): no mains creepage slots are cut. Relay pins from KiCad's
Relay:SANYOU_SRD_Form_C symbol: 2 and 5 coil, 1 COM; 3 drawn as the normally-open contact and 4 as the normally
closed one (assumed from the symbol's drawing: check against Sanyou's SRD drawing). PC817: 1 anode, 2 cathode,
3 emitter, 4 collector (Sharp PC817 datasheet). S8050 SOT-23: 1 B, 2 E, 3 C.
Values: opto LED 1k from VCC (5 V, ~3 mA, CTR >= 50 % -> >= 1.5 mA collector), base 1k, coil 70 mA (SRD-05VDC 71 ohm).
"""
from pcb import Board

R06 = "Resistor_SMD:R_0603_1608Metric"
TB3 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal"
W, H = 56.0, 46.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("relay_2ch_opto", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (2.6, 13.0), 0, "VCC IN1 IN2 GND",
            "XFCN PZ254V-11-04P")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (2.6, 32.0), 0, "JD-VCC VCC GND",
            "XFCN PZ254V-11-03P")
    for ch, y in ((1, 33.0), (2, 12.0)):
        b.place(f"K{ch}", "Relay_THT:Relay_SPDT_SANYOU_SRD_Series_Form_C", (21.0, y), 0, "SRD-05VDC-SL-C",
                "Songle SRD-05VDC-SL-C", symbol="Relay:SANYOU_SRD_Form_C")
        b.place(f"J{ch + 2}", TB3, (W - 4.0, y - 3.0), 90, "NO COM NC", "Phoenix 1729131", overhang=True)
        b.place(f"U{ch}", "Package_DIP:SMDIP-4_W7.62mm", (11.0, y), 0, "PC817", "Sharp PC817XNNSZ0F",
                symbol="Isolator:PC817")
        b.place(f"Q{ch}", "Package_TO_SOT_SMD:SOT-23", None, 0, "S8050", "CJ S8050 J3Y", lcsc="C2146")
        b.place(f"D{ch}", "Diode_SMD:D_SOD-123", None, 0, "1N4148W", "Diodes 1N4148W-7-F")
        b.place(f"D{ch + 2}", "LED_SMD:LED_0603_1608Metric", None, 0, "red")
        for k, (val, note) in enumerate((("1k", "opto LED"), ("1k", "base"), ("10k", "base pull-down"),
                                         ("1k", "channel LED"))):
            b.place(f"R{ch}{k + 1}", R06, None, 0, val, note=note)
    b.hole((44.0, H - 3.0), d=3.2)
    b.hole((3.5, H - 3.5), d=3.2)
    b.hole((42.5, 22.8), d=3.2)                          # (between the relays: clear of K2's coil pad)

    b.net("VCC", [("J1", "1"), ("J2", "2"), ("R11", "1"), ("R21", "1"), ("R14", "1"), ("R24", "1")], amps=0.1)
    b.net("GND", [("J1", "4")], amps=0.1)                    # logic ground: only the header (opto LEDs return to INx)
    b.net("JDVCC", [("J2", "1"), ("K1", "5"), ("K2", "5"), ("D1", "1"), ("D2", "1"), ("U1", "4"), ("U2", "4")],
          amps=0.3, volts=5.0)
    b.net("JDGND", [("J2", "3"), ("Q1", "2"), ("Q2", "2"), ("R13", "2"), ("R23", "2")], amps=0.3)
    for ch in (1, 2):
        b.net(f"A{ch}", [(f"R{ch}1", "2"), (f"U{ch}", "1")])
        b.net(f"IN{ch}", [(f"J1", str(ch + 1)), (f"U{ch}", "2"), (f"D{ch + 2}", "1")])
        b.net(f"LA{ch}", [(f"R{ch}4", "2"), (f"D{ch + 2}", "2")])
        b.net(f"E{ch}", [(f"U{ch}", "3"), (f"R{ch}2", "1")])
        b.net(f"B{ch}", [(f"R{ch}2", "2"), (f"R{ch}3", "1"), (f"Q{ch}", "1")])
        b.net(f"COIL{ch}", [(f"Q{ch}", "3"), (f"K{ch}", "2"), (f"D{ch}", "2")], amps=0.1)
        b.net(f"NO{ch}", [(f"K{ch}", "3"), (f"J{ch + 2}", "1")], amps=5.0, volts=30.0)
        b.net(f"COM{ch}", [(f"K{ch}", "1"), (f"J{ch + 2}", "2")], amps=5.0, volts=30.0)
        b.net(f"NC{ch}", [(f"K{ch}", "4"), (f"J{ch + 2}", "3")], amps=5.0, volts=30.0)
    # the contact copper by hand, 2 mm (5 A, IPC-2221 20 C rise needs 1.82 mm) on the top layer
    for ch in (1, 2):
        (xc, yc), (xno, yno), (xnc, ync) = (b.pad(f"K{ch}", p) for p in ("1", "3", "4"))
        (_, y1), (xt, y2), (_, y3) = (b.pad(f"J{ch + 2}", p) for p in ("1", "2", "3"))
        b.track(f"NO{ch}", "F", [(xno, yno), (xt - 9.0, yno), (xt - 6.0, y1), (xt, y1)], 2.0)
        b.track(f"COM{ch}", "F", [(xc, yc), (xt - 12.0, yc), (xt - 10.0, y2), (xt, y2)], 2.0)
        b.track(f"NC{ch}", "F", [(xnc, ync), (xt - 9.0, ync), (xt - 6.0, y3), (xt, y3)], 2.0)
    b.erc_allow("GND", "the logic ground only reaches the header: the opto LEDs return through the INx pins")
    b.pour("JDGND", "B", [(0, 0), (40.0, 0), (40.0, H), (0, H)])
    return b
