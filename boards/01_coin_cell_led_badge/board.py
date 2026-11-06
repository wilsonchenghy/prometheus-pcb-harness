"""Coin-cell LED badge: a CR2032 (Keystone 3034 SMD holder, on the back) powers three LEDs (red, green, blue), each
with its own resistor, through a slide switch (C&K PCM12). Difficulty 1/5. 2 layers, 30 x 30 mm, JLCPCB.

Values: a CR2032 is ~3.0 V; red LED Vf ~1.9 V -> 100 ohm gives ~10 mA (less, the cell's ESR ~10-20 ohm limits it);
green/blue Vf ~2.7-2.9 V -> 10 ohm, current set mostly by the cell's own resistance (a few mA). Assumed, typical.
Battery holder: Keystone 3034 pad 1 = +, pad 2 = - (KiCad's footprint; check against Keystone's drawing).
"""
from pcb import Board

R06 = "Resistor_SMD:R_0603_1608Metric"
LED = "LED_SMD:LED_0805_2012Metric"
W, H = 30.0, 30.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]


def build():
    b = Board("coin_cell_led_badge", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=4.0)
    b.place("BT1", "Battery:BatteryHolder_Keystone_3034_1x20mm", (W / 2, H / 2 - 2), 0, "CR2032", "Keystone 3034",
            side="B")
    b.place("SW1", "Button_Switch_SMD:SW_SPDT_PCM12", (W / 2, H - 2.8), 0, "ON/OFF", "C&K PCM12SMTR",
            overhang=True)
    for i, (col, r) in enumerate((("red", "100"), ("green", "10"), ("blue", "10"))):
        x = 7.0 + i * 8.0
        b.place(f"D{i + 1}", LED, (x, 12.0), 90, col)
        b.place(f"R{i + 1}", R06, (x, 7.0), 90, r)
    b.hole((4.5, H - 4.5), d=2.2)                         # a lanyard / pin hole
    b.net("VBAT", [("BT1", "1"), ("SW1", "2")], amps=0.05, volts=3.0)
    b.net("VSW", [("SW1", "1")] + [(f"D{i}", "2") for i in (1, 2, 3)], amps=0.05, volts=3.0)
    for i in (1, 2, 3):
        b.net(f"K{i}", [(f"D{i}", "1"), (f"R{i}", "2")])
    b.net("GND", [("BT1", "2")] + [(f"R{i}", "1") for i in (1, 2, 3)], amps=0.05)
    b.nc("SW1", "3")
    b.pour("GND", "B", OUT)
    return b
