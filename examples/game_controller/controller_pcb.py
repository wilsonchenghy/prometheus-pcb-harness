"""The controller's main board: parts on KiCad footprints, the netlist, routed copper (pcb.py).

Board coordinates are the controller's world x, y (mm); the board's underside is at z = 2.0. Two layers: the
bottom is a ground pour, signals route on both. The USB-C is for charging only (CC pull-downs, VBUS to the charger and
to the module's VBUS sense): its data pins stay unconnected, firmware updates go over Bluetooth (DFU). Every button and stick click goes to a GPIO with the nRF52840's
internal pull-up (the switch shorts it to ground), so a button needs no resistor.

What is real here: every footprint and 3D model is KiCad's; the parts are the ones named in the BOM. What is
ASSUMED: the Raytac MDBT50Q pad each signal lands on (chosen for routing; the module's datasheet pin table must be
checked: analog signals must land on AIN-capable pins, USB on the module's D+ / D- / VBUS pads); the stick
module's pin positions (the stick module is drawn from its outline, not a STEP).
"""
import os

from pcb import Board, cached_route, route

R04, C04, C06 = "Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric", "Capacitor_SMD:C_0805_2012Metric"
R04M, C04M, C06M = ("Resistor_SMD.3dshapes/R_0402_1005Metric.step", "Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
                    "Capacitor_SMD.3dshapes/C_0805_2012Metric.step")
TACT = "Button_Switch_SMD:SW_SPST_TL3342"
TACT_M = "Button_Switch_SMD.3dshapes/SW_SPST_TL3342.step"
SIDE = "Button_Switch_SMD:Panasonic_EVQPUJ_EVQPUA"
SIDE_M = "Button_Switch_SMD.3dshapes/Panasonic_EVQPUJ_EVQPUA.step"
SOT5, SOT3 = "Package_TO_SOT_SMD:SOT-23-5", "Package_TO_SOT_SMD:SOT-23"
SOT5M, SOT3M = "Package_TO_SOT_SMD.3dshapes/SOT-23-5.step", "Package_TO_SOT_SMD.3dshapes/SOT-23.step"
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
HDR2 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
MODULE = "RF_Module:Raytac_MDBT50Q"

# the module's pads (ASSUMED assignment, see the docstring): outer pads only, for routing
PAD = {"GND": ["15", "33", "30"], "VDD": "2",
       "LX": "4", "LY": "6", "RX": "41", "RY": "39", "LT": "8", "RT": "44", "VSENSE": "10",
       "BTN_A": "46", "BTN_B": "48", "BTN_X": "31", "BTN_Y": "32", "BTN_LB": "3", "BTN_RB": "37",
       "DP_U": "12", "DP_D": "16", "DP_L": "17", "DP_R": "18", "BTN_HOME": "35", "BTN_VIEW": "20", "BTN_MENU": "34",
       "LS": "22", "RS": "24", "RUM_L": "26", "RUM_R": "28", "VBUS": "55"}


def build(outline, at, holes, keepouts=()):
    """the board. ``at``: positions of the parts the mechanics fix (switch centres, stick axes, the USB mouth,
    the bumper switches, the hall sensors, the connectors); ``holes``: (x, y, d) mounting holes"""
    b = Board("controller_main", outline, thickness=1.6, copper_oz=1.0, clearance=0.15)
    for h in holes:
        x, y, d = h[:3]
        b.hole((x, y), d, keepout=h[3] if len(h) > 3 else d / 2 + 0.6)
    # ---- controls: tact switches under every cap
    sw = {}
    for name, (x, y) in at["tact"].items():
        ref = "SW_" + name
        b.place(ref, TACT, (x, y), 0, "TL3342", "TE 1-1437565-1 (TL3342F160QG)", model=TACT_M, note=name)
        sw[name] = ref
    for name, (x, y) in at["side"].items():          # bumpers: the actuator faces the board's front edge (+y)
        ref = "SW_" + name
        b.place(ref, SIDE, (x, y), 0, "EVQ-PUJ", "Panasonic EVQ-PUJ02K", model=SIDE_M, note=name, overhang=True)
        sw[name] = ref
    # ---- sticks: the module's two pots and its push switch on 2.54 mm pins (ASSUMED), four frame legs
    for nm, (x, y) in at["sticks"].items():
        b.place(f"RV{nm}X", HDR, (x + 8.1, y + 2.54), 0, "pot X", "ALPS RKJXV1224005 (X pot)", note="stick module, X axis")
        b.place(f"RV{nm}Y", HDR, (x - 2.54, y - 8.1), 90, "pot Y", "ALPS RKJXV1224005 (Y pot)", note="stick module, Y axis")
        b.place(f"SW_{nm}S", HDR2, (x - 7.4, y + 1.27), 0, "click", "ALPS RKJXV1224005 (push)", note="stick click")
        for sx in (-1, 1):
            for sy in (-1, 1):
                b.hole((x + 6.0 * sx, y + 6.0 * sy), 1.6)
    # ---- radio + MCU
    mx, my = at["mcu"]
    b.place("U1", MODULE, (mx, my), 0, "MDBT50Q", "Raytac MDBT50Q-1MV2 (nRF52840)",
            model="RF_Module.3dshapes/Raytac_MDBT50Q.step")
    b.place("C10", C06, (mx - 3.0, my - 11.6), 0, "10u", "GRM21BR61A106KE19", model=C06M)
    b.place("C11", C04, (mx + 0.5, my - 11.6), 0, "100n", "X7R 0402", model=C04M)
    # ---- USB-C, charger, LDO, battery
    ux, uy = at["usb"]
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (ux, uy), 180, "USB-C",
            "GCT USB4105-GF-A", model="Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step",
            overhang=True)
    b.place("R1", R04, (ux - 6.5, uy - 8.0), 0, "5k1", "RC0402FR-075K1L", model=R04M, note="CC1", side="B")
    b.place("R2", R04, (ux + 3.5, uy - 8.6), 0, "5k1", "RC0402FR-075K1L", model=R04M, note="CC2", side="B")
    px, py = at["power"]
    b.place("U2", SOT5, (px, py), 0, "MCP73831", "Microchip MCP73831T-2ACI/OT", model=SOT5M, note="LiPo charger 500 mA")
    b.place("R4", R04, (px - 3.2, py - 2.6), 0, "2k", "RC0402FR-072KL", model=R04M, note="charge current 500 mA")
    b.place("C1", C06, (px - 4.6, py + 1.6), 90, "4u7", "GRM21BR61E475KA12", model=C06M, note="VBUS")
    b.place("C2", C06, (px + 3.6, py + 1.2), 90, "4u7", "GRM21BR61E475KA12", model=C06M, note="VBAT")
    b.place("D3", "LED_SMD:LED_0603_1608Metric", (px - 1.0, py + 3.8), 0, "red", "Kingbright APT1608EC",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step", note="charging")
    b.place("R3", R04, (px + 1.8, py + 3.8), 0, "1k", "RC0402FR-071KL", model=R04M)
    b.place("U3", SOT5, (px + 6.5, py - 4.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1", model=SOT5M, note="3.3 V LDO")
    b.place("C3", C04, (px + 9.6, py - 3.0), 90, "1u", "X5R 0402", model=C04M)
    b.place("C4", C04, (px + 3.4, py - 5.0), 90, "1u", "X5R 0402", model=C04M)
    b.place("R5", R04, (px + 3.0, py - 1.0), 0, "100k", "RC0402FR-07100KL", model=R04M, note="battery sense")
    b.place("R6", R04, (px + 3.0, py - 2.4), 0, "100k", "RC0402FR-07100KL", model=R04M, note="battery sense")
    jx, jy = at["bat"]
    b.place("J2", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", (jx, jy), 90, "PH 2p", "JST B2B-PH-K-S",
            model="Connector_JST.3dshapes/JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical.step", side="B", note="battery")
    # ---- triggers: hall sensors under the magnets (bottom side)
    for nm, (x, y) in at["hall"].items():
        b.place(f"U{nm}", SOT3, (x, y), 90, "DRV5055A1", "TI DRV5055A1QDBZR", model=SOT3M, side="B", note="trigger")
        b.place(f"C{nm}", C04, (x, y - 3.2), 0, "100n", "X7R 0402", model=C04M, side="B")
    # ---- rumble: a low-side MOSFET + flyback diode per motor, a JST-SH socket for its lead (bottom side)
    for nm, (x, y) in at["rumble"].items():
        b.place(f"J{nm}", "Connector_JST:JST_SH_BM02B-SRSS-TB_1x02-1MP_P1.00mm_Vertical", (x, y), 0, "SH 2p",
                "JST BM02B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_BM02B-SRSS-TB_1x02-1MP_P1.00mm_Vertical.step",
                side="B", note="rumble motor")
        b.place(f"Q{nm}", SOT3, (x, y + 5.0), 0, "AO3400A", "AOS AO3400A", model=SOT3M, note="rumble driver")
        b.place(f"D{nm}", "Diode_SMD:D_SOD-323", (x + 3.6, y + 5.0), 90, "B5819WS", "B5819WS",
                model="Diode_SMD.3dshapes/D_SOD-323.step", note="flyback")
    for poly, why in keepouts:
        b.keepout(poly, "FB", why)
    b.pour("GND", "B", list(b.outline.exterior.coords)[:-1])          # the bottom layer: ground
    b.pour("GND", "F", list(b.outline.exterior.coords)[:-1])          # and the top's free copper (carved round the rest)

    # ---- nets
    gnd = [("U1", n) for n in PAD["GND"]] + [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")]
    gnd += [("U2", "2"), ("U3", "2"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), ("C10", "2"), ("C11", "2"),
            ("R1", "2"), ("R2", "2"), ("R4", "2"), ("R6", "2"), ("J2", "2")]
    v33 = [("U1", PAD["VDD"]), ("U3", "5"), ("C3", "1"), ("C10", "1"), ("C11", "1")]
    vbat = [("U2", "3"), ("U3", "1"), ("U3", "3"), ("C2", "1"), ("C4", "1"), ("R5", "1"), ("J2", "1")]
    for name, ref in sw.items():
        b.net(name, [(ref, "1"), ("U1", PAD[name])], amps=0.01, volts=3.3)
        gnd.append((ref, "2"))
    for nm in at["sticks"]:
        for ax in "XY":
            b.net(f"{nm}{ax}", [(f"RV{nm}{ax}", "2"), ("U1", PAD[f"{nm}{ax}"])], amps=0.01, volts=3.3)
            gnd.append((f"RV{nm}{ax}", "1"))
            v33.append((f"RV{nm}{ax}", "3"))
        b.net(f"{nm}S", [(f"SW_{nm}S", "1"), ("U1", PAD[f"{nm}S"])], amps=0.01, volts=3.3)
        gnd.append((f"SW_{nm}S", "2"))
    for nm in at["hall"]:
        b.net(nm, [(f"U{nm}", "2"), ("U1", PAD[nm])], amps=0.01, volts=3.3)
        v33 += [(f"U{nm}", "1"), (f"C{nm}", "1")]
        gnd += [(f"U{nm}", "3"), (f"C{nm}", "2")]
    for nm in at["rumble"]:
        b.net(f"{nm}_PWM", [(f"Q{nm}", "1"), ("U1", PAD[nm])], amps=0.01, volts=3.3)
        b.net(f"{nm}_M", [(f"Q{nm}", "3"), (f"D{nm}", "2"), (f"J{nm}", "2")], amps=0.15, volts=4.2)
        vbat += [(f"D{nm}", "1"), (f"J{nm}", "1")]
        gnd.append((f"Q{nm}", "2"))
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] + [("U2", "4"), ("C1", "1"), ("R3", "1"), ("U1", PAD["VBUS"])],
          amps=0.6, volts=5.0)
    b.net("CC1", [("J1", "A5"), ("R1", "1")], amps=0.01, volts=5.0)
    b.net("CC2", [("J1", "B5"), ("R2", "1")], amps=0.01, volts=5.0)
    b.net("STAT", [("U2", "1"), ("D3", "1")], amps=0.01, volts=5.0)
    b.net("LED_A", [("D3", "2"), ("R3", "2")], amps=0.01, volts=5.0)
    b.net("PROG", [("U2", "5"), ("R4", "1")], amps=0.01, volts=5.0)
    b.net("VSENSE", [("R5", "2"), ("R6", "1"), ("U1", PAD["VSENSE"])], amps=0.001, volts=3.3)
    b.net("VBAT", vbat, amps=0.6, volts=4.2)
    b.net("3V3", v33, amps=0.1, volts=3.3)
    b.net("GND", gnd, amps=0.6, volts=4.2)          # (the return of the 4.2 V rail: its drop counts against that)
    return b


def do_route(b):
    """route signals, then power, then ground into the bottom pour"""
    probs = []
    # the USB-C's two VBUS pads sit between its signal pads: each drops through a via behind it, and VBUS joins them
    # on the bottom layer (the router cannot leave the right-hand one between its neighbours on the top)
    for num in ("A4", "A9"):
        x, y = b.pad("J1", num)
        b.track("VBUS", "F", [(x, y), (x, y - 1.6)], width=0.35)
        b.via("VBUS", (x, y - 1.6))
    for num in ("A1", "A12"):                                   # its ground pads: straight down into the pour
        x, y = b.pad("J1", num)
        xo = x + (0.8 if x > b.comps["J1"]["at"][0] else -0.8)
        b.track("GND", "F", [(x, y), (xo, y - 1.9)], width=0.35)
        b.via("GND", (xo, y - 1.9))
    x, y = b.pad("U2", "2")                                     # the charger's ground pin, between its neighbours
    b.track("GND", "F", [(x, y), (x - 1.5, y)], width=0.3)
    b.via("GND", (x - 1.5, y))
    for ref, num, dx, dy in (("U1", "15", -0.9, -1.1), ("U1", "33", 0.9, -1.1),   # ground pads boxed in by their
                             ("R4", "2", 0.0, -1.1), ("C3", "2", 1.1, 0.0),     # neighbours: a via each
                             ("C10", "2", 0.0, -1.2)):
        x, y = b.pad(ref, num)
        b.track("GND", "F", [(x, y), (x + dx, y + dy)], width=0.3)
        b.via("GND", (x + dx, y + dy))
    b.fanout(width=0.15)
    first = ["CC1", "CC2"]                                     # the USB-C's crowded row before anything else
    for n in first:
        probs += route(b, n, width=0.2)
    for n in ("VBUS", "VBAT", "3V3"):                           # power next, while there is room for wide tracks
        probs += route(b, n, width=0.4 if n != "3V3" else 0.3)
    for n in [n for n in b.nets if n not in ("GND", "VBAT", "3V3", "VBUS", *first)]:
        probs += route(b, n, width=0.2)
    probs += route(b, "GND", width=0.3)
    return probs


def routed(outline, at, holes, keepouts=()):
    b = build(outline, at, holes, keepouts)
    store = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "routes")    # routed copper, kept in git
    probs = cached_route(b, do_route, tag="ctl_v4", store=store)   # (bump the tag when do_route changes: the cache
    return b, probs                                                 # keys the placement, not this code)


if __name__ == "__main__":
    # python controller_pcb.py [out_dir]: the board in the controller's shell, its outline, part positions, holes and
    # the antenna keep-out as the controller's design gives them (controller_board.json), routed and exported
    import json
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:0] = [os.path.join(here, ".."), os.path.join(here, "..", "..")]
    from export import export
    g = json.load(open(os.path.join(here, "controller_board.json")))
    keep = [([tuple(p) for p in pts], why) for pts, why in g["keepouts"]]
    b, probs = routed([tuple(p) for p in g["outline"]], g["at"], [tuple(h) for h in g["holes"]], keep)
    export(b, probs, sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "..", "boards", "controller_main"))
