"""The Pioneer Cube's main board: a hand-solderable carrier (through-hole only) for an ESP32 DevKit V1 (30 pin, on
sockets), three SimpleFOC Mini v2.3 drivers (DRV8316, soldered on by their own pin headers), a GY-521 (MPU-6050) IMU
and a Pololu D24V10F5 5 V buck, with the battery input (XT30, 15 A mini blade fuse, 470 uF bulk), an MR30 phase
connector and a JST-XH encoder connector per wheel motor. Two layers, 2 oz: the bottom is a ground pour, the battery
rail and the phases are wide tracks, the logic routed on both layers (pcb.py).

Board coordinates are the cube's world x, y (mm); the board's underside sits at z = PCB_Z0 (cube.py).

Pin map (ESP32 DevKit V1, DOIT 30 pin; H1 = EN VP VN D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND VIN from the antenna
end, H2 = D23 D22 TX0 RX0 D21 D19 D18 D5 TX2 RX2 D4 D2 D15 GND 3V3):
  wheel x  IN1-3 = D25 D26 D27      wheel y  IN1-3 = D32 D33 D14      wheel z  IN1-3 = D13 D4 D16
  drivers' EN (all three) = D17      SPI (encoders): SCK D18, MISO D19, MOSI D23, CS x D5 / y D15 / z D2
  IMU I2C: SDA D21, SCL D22, INT D34  battery sense: driver x's VCC x 0.1 output -> D35 (ADC1)
  strapping pins: D12 unused (must be low at boot); D2 / D5 / D15 carry chip selects (idle high: boots normally;
  hold BOOT to flash as usual).
Encoder connector (JST-XH 6, ours): 1 3V3, 2 GND, 3 SCK, 4 MISO, 5 MOSI, 6 CS. Phase connector (MR30): 1 A, 2 B, 3 C.

ASSUMED (check against the parts you buy before ordering the board): the SimpleFOC Mini v2.3 pin positions (read off
its published dimensioned layout, +-0.15 mm: the headers are on one 2.54 mm lattice, so only the offset between its
rows and the supply holes can be off), the GY-521's pin order (VCC GND SCL SDA XDA XCL AD0 INT, the common one), the
D24V10F5's pin order (PG SHDN VIN GND VOUT as Pololu lists them), the GL30 encoder's interface (SPI, AS5048A-class).
"""
import math
import os

from pcb import Board, cached_route, route, route_jobs

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x{:02d}_P2.54mm_Vertical"
HDR_M = "Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x{:02d}_P2.54mm_Vertical.step"
SOCK = "Connector_PinSocket_2.54mm:PinSocket_1x15_P2.54mm_Vertical"
HDR2 = "Connector_PinHeader_2.54mm:PinHeader_2x06_P2.54mm_Vertical"
MR30 = "Connector_AMASS:AMASS_MR30PW-M_1x03_P3.50mm_Horizontal"
XH = "Connector_JST:JST_XH_S6B-XH-A_1x06_P2.50mm_Horizontal"
XT30 = "Connector_AMASS:AMASS_XT30UPB-M_1x02_P5.0mm_Vertical"
FUSE = "Fuse:Fuseholder_Blade_Mini_Keystone_3568"
CAP = "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm"

OUTLINE = [(-62.0, -48.0), (50.0, -48.0), (50.0, 50.0), (-62.0, 50.0)]
HOLES = [(-56.0, -20.0), (44.0, -44.0), (46.0, 30.0), (-56.0, 46.0)]      # M3 on 6 mm brass standoffs
GRID = 0.254                                          # routing grid: divides the 2.54 mm header pitch
DEV = (-60.086, 12.012)                               # ESP32 DevKit's (0, 0) corner, USB toward -X (its pins on GRID)
ANTENNA = [(DEV[0] + 45.0, DEV[1] - 10.0), (DEV[0] + 66.5, DEV[1] - 10.0), (DEV[0] + 66.5, 50.0), (DEV[0] + 45.0, 50.0)]
# drivers: (origin of the module's own frame, turn): motor pads toward the board edge their lead comes in at
DRV = {"x": ((36.0, -34.0), 90.0), "y": ((36.0, 34.0), 180.0), "z": ((-50.0, -30.0), 0.0)}
IMU = ((-24.0, -16.0), 0.0)
BUCK = ((14.0, -47.0), 0.0)
# lead connectors: MR30 pin 1, XH pin 1, turn (mouth out of the board edge)
LEADS = {"x": ((38.5, -15.0), (43.5, -6.0), 270.0, 90.0),
         "y": ((14.0, 38.5), (42.0, 44.5), 0.0, 180.0),
         "z": ((-26.0, -36.5), (-53.5, -44.0), 180.0, 0.0)}
J_BAT = (-12.0, -28.0)
F_BAT = (-7.0, -42.0)
C_BULK = (2.0, -30.0)

MINI_PINS = {"A": ["IN1", "IN2", "IN3", "EN", "GND", "GND2"], "B": ["VSENSE", "CS1", "CS2", "CS3", "3V3"],
             "M": ["M1", "M2", "M3"]}
MINI_P0 = {"A": (7.67, 21.7), "B": (7.67, 19.16), "M": (12.75, 1.38)}
MINI_SUPPLY = {"VM-": (2.5, 14.2), "VM+": (2.5, 9.1)}
IMU_P0 = (1.71, 1.5)
BUCK_P0 = (3.81, 1.27)


def mr30_body():
    """AMASS MR30PW-M (right-angle male, PCB) in its footprint frame, from AMASS's drawing (ASSUMED +-0.3): a 15.6 x
    6.6 nylon housing from just behind the pins to the mouth 13.2 mm out (+Y), its three pins down into the board"""
    import build123d as bd
    body = bd.Pos(3.5, 5.85, 0.0) * bd.Box(15.6, 14.7, 6.6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    body = body - bd.Pos(3.5, 11.6, 0.9) * bd.Box(13.6, 3.4, 4.8, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    for x in (0.0, 3.5, 7.0):
        body = body + bd.Pos(x, 0.0, -3.0) * bd.Cylinder(0.5, 3.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    return body


def xt30_mated_body():
    """AMASS XT30UPB-M (vertical male) with the battery's XT30U-F plugged in, in its footprint frame (pins at x 0 and
    5): the D-shaped 10.2 x 5.2 nylon pair, 16 mm tall over the board (ASSUMED from AMASS's drawings, +-0.5)"""
    import build123d as bd
    sk = bd.Pos(2.5, 0) * bd.Rectangle(10.2, 5.2)
    body = bd.extrude(sk, 16.0)
    edges = body.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[:2]           # the D: chamfers on the minus end
    body = bd.chamfer(edges, 1.6)
    for x in (0.0, 5.0):
        body = body + bd.Pos(x, 0.0, -3.0) * bd.Cylinder(0.55, 3.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    return body


XT30_H = 16.0


def snap(v):
    return round(v / GRID) * GRID


def on_grid(origin, rot, p0):
    """the module origin moved (< 0.13 mm) so that its pin p0 lands on the routing lattice"""
    q = _xf(origin, rot, p0)
    return (origin[0] + snap(q[0]) - q[0], origin[1] + snap(q[1]) - q[1])


def _xf(origin, rot, p):
    a = math.radians(rot)
    return (origin[0] + p[0] * math.cos(a) - p[1] * math.sin(a), origin[1] + p[0] * math.sin(a) + p[1] * math.cos(a))


DRV = {k: (on_grid(o, r, (7.67, 19.16)), r) for k, (o, r) in DRV.items()}
IMU = (on_grid(IMU[0], IMU[1], (1.71, 1.5)), IMU[1])
BUCK = (on_grid(BUCK[0], BUCK[1], (3.81, 1.27)), BUCK[1])


def build():
    b = Board("cube_main", OUTLINE, thickness=1.6, copper_oz=2.0, clearance=0.2, max_rise_c=20.0, corner_r=3.0)
    for h in HOLES:
        b.hole(h, 3.2, keepout=3.4)
    b.keepout(ANTENNA, "FB", "ESP32 antenna (its keep-out reaches this board under the DevKit's sockets)")
    # power entry
    b.place("J1", XT30, J_BAT, 0, "XT30-M", "AMASS XT30UPB-M", body=xt30_mated_body(),
            note="battery in (drawn with the battery's plug in)")
    b.place("F1", FUSE, F_BAT, 0, "15 A ATM", "Keystone 3568 + 15 A mini blade fuse",
            model="Fuse.3dshapes/Fuseholder_Blade_Mini_Keystone_3568.step")
    b.place("C1", CAP, C_BULK, 0, "470u 25V", "Panasonic EEU-FR1E471", model="Capacitor_THT.3dshapes/CP_Radial_D10.0mm_P5.00mm.step")
    # ESP32 sockets (DevKit pins measured from its STEP: rows 25.4 apart, x = 7.0 + 2.54 k, pin 1 at the antenna end)
    # (their 3D bodies: the ESP32 catalogue model's 8.5 mm header envelope, not a second model here)
    b.place("H1", SOCK, (DEV[0] + 42.56, DEV[1] + 26.85), 270, "socket 1x15", "Samtec SSW-115-01-T-S")
    b.place("H2", SOCK, (DEV[0] + 42.56, DEV[1] + 1.45), 270, "socket 1x15", "Samtec SSW-115-01-T-S")
    # drivers: their own male headers solder through this board (rows A, B, the motor pads M, the two supply pins)
    for k, (o, r) in DRV.items():
        # rows A (IN1 IN2 IN3 EN GND GND) and B (VSENSE CS1 CS2 CS3 3V3) are one 2 x 6 header: odd pins = row B,
        # even = row A (pin 11 has no pin on the Mini)
        b.place(f"D{k.upper()}H", HDR2, _xf(o, r, MINI_P0["B"]), 90 + r, "Mini 2x6", "SimpleFOC Mini v2.3 input header",
                note=f"driver {k} input header")
        b.place(f"D{k.upper()}M", HDR.format(3), _xf(o, r, MINI_P0["M"]), 90 + r, "Mini M", "SimpleFOC Mini v2.3 M1/M2/M3",
                note=f"driver {k} motor pads")
        for i, (nm, p) in enumerate(MINI_SUPPLY.items()):
            b.place(f"D{k.upper()}P{i + 1}", HDR.format(1), _xf(o, r, p), 0, nm, f"SimpleFOC Mini v2.3 {nm} (pin in its supply hole)",
                    note=f"driver {k} {nm}")
    (o, r) = IMU
    b.place("U2", HDR.format(8), _xf(o, r, IMU_P0), 90 + r, "GY-521", "GY-521 MPU-6050 breakout (its 8-pin header)")
    (o, r) = BUCK
    b.place("U1", HDR.format(5), _xf(o, r, BUCK_P0), 90 + r, "D24V10F5", "Pololu D24V10F5 (#2831)")
    for k, (mr, xh, rm, rx) in LEADS.items():
        b.place(f"JM{k.upper()}", MR30, mr, rm, "MR30-M", "AMASS MR30PW-M",
                body=mr30_body(), note=f"wheel {k} phases",
                overhang=True)                                   # its mouth stands proud of the edge
        b.place(f"JE{k.upper()}", XH, xh, rx, "S6B-XH-A", "JST S6B-XH-A",
                model="Connector_JST.3dshapes/JST_XH_S6B-XH-A_1x06_P2.50mm_Horizontal.step", note=f"wheel {k} encoder",
                overhang=True)

    # ---- nets
    VB = 12.6
    D = {k: f"D{k.upper()}" for k in "xyz"}
    b.net("VBAT_IN", [("J1", "2"), ("F1", "1")], amps=15.0, volts=VB)
    b.net("VBAT", [("F1", "2"), ("C1", "1"), ("U1", "3")] + [(f"{D[k]}P2", "1") for k in "xyz"], amps=15.0, volts=VB)
    b.net("GND", [("J1", "1"), ("C1", "2"), ("U1", "4"), ("H1", "14"), ("H2", "14"), ("U2", "2")]
          + [(f"{D[k]}P1", "1") for k in "xyz"] + [(f"{D[k]}H", "10") for k in "xyz"] + [(f"{D[k]}H", "12") for k in "xyz"]
          + [(f"JE{k.upper()}", "2") for k in "xyz"], amps=15.0, volts=VB)
    b.net("5V", [("U1", "5"), ("H1", "15")], amps=0.6, volts=5.0)
    b.net("3V3", [("H2", "15"), ("U2", "1")] + [(f"JE{k.upper()}", "1") for k in "xyz"], amps=0.15, volts=3.3)
    pwm = {"x": [("H1", "8"), ("H1", "9"), ("H1", "10")], "y": [("H1", "6"), ("H1", "7"), ("H1", "11")],
           "z": [("H1", "13"), ("H2", "11"), ("H2", "10")]}
    for k, pins in pwm.items():
        for i, p in enumerate(pins):
            b.net(f"PWM_{k.upper()}{i + 1}", [p, (f"{D[k]}H", str(2 * i + 2))], amps=0.01, volts=3.3)
    b.net("DRV_EN", [("H2", "9")] + [(f"{D[k]}H", "8") for k in "xyz"], amps=0.01, volts=3.3)
    b.net("SCK", [("H2", "7")] + [(f"JE{k.upper()}", "3") for k in "xyz"], amps=0.01, volts=3.3)
    b.net("MISO", [("H2", "6")] + [(f"JE{k.upper()}", "4") for k in "xyz"], amps=0.01, volts=3.3)
    b.net("MOSI", [("H2", "1")] + [(f"JE{k.upper()}", "5") for k in "xyz"], amps=0.01, volts=3.3)
    for k, p in (("x", "8"), ("y", "13"), ("z", "12")):
        b.net(f"CS_{k.upper()}", [("H2", p), (f"JE{k.upper()}", "6")], amps=0.01, volts=3.3)
    b.net("SDA", [("H2", "5"), ("U2", "4")], amps=0.01, volts=3.3)
    b.net("SCL", [("H2", "2"), ("U2", "3")], amps=0.01, volts=3.3)
    b.net("IMU_INT", [("H1", "4"), ("U2", "8")], amps=0.01, volts=3.3)
    b.net("VSENSE", [("DXH", "1"), ("H1", "5")], amps=0.01, volts=1.3)
    for k in "xyz":
        for i in range(3):
            b.net(f"PH_{k.upper()}{'ABC'[i]}", [(f"{D[k]}M", str(i + 1)), (f"JM{k.upper()}", str(i + 1))],
                  amps=7.0, volts=VB)
    b.silk_text = [("PIONEER CUBE", (-36.0, -26.0), 3.0, 0.0), ("main board r1  ·  3S  ·  3 x GL30", (-36.0, -31.0), 1.8, 0.0),
                   ("X", (47.0, -25.0), 2.4, 0.0), ("Y", (8.0, 47.0), 2.4, 0.0), ("Z", (-36.0, -45.5), 2.4, 0.0),
                   ("+", (J_BAT[0] + 5.0, J_BAT[1] + 4.5), 2.0, 0.0)]
    return b


def do_route(b):
    probs = []
    # the battery rail and the phases first, wide
    probs += route(b, "VBAT_IN", width=b.width_for(15.0, "F"), amps=15.0, layers="F")
    probs += route(b, "VBAT", width=b.width_for(8.0, "F"), amps=8.0, layers="F")
    # each driver's supply return straight back to the battery connector (a star: motor current stays off the
    # logic ground), as wide as its supply
    probs += route(b, "GND", width=b.width_for(8.0, "F"), amps=8.0, layers="F",
                   only=[("J1", "1"), ("C1", "2")] + [(f"D{k}P1", "1") for k in "XYZ"])
    for k in "xyz":
        for c in "ABC":
            probs += route(b, f"PH_{k.upper()}{c}", width=b.width_for(7.0, "F"), amps=7.0)
    probs += route(b, "5V", width=0.6)
    probs += route(b, "3V3", width=0.5)
    # the logic's ground pins tied in before the signals can wall them off
    probs += route(b, "GND", width=0.5, amps=0.5, only=[("U2", "2"), ("H1", "14"), ("H2", "14"), ("U1", "4")],
                   grid=GRID)
    sig = ["SDA", "SCL", "IMU_INT", "VSENSE"] + [n for n in b.nets if n.startswith(("PWM_", "CS_"))] + \
        ["DRV_EN", "SCK", "MISO", "MOSI"]
    # negotiated passes on the header lattice; vias cost a lot, so signals keep to the top where they can and the
    # bottom stays an unbroken ground
    probs += route_jobs(b, [(n, 0.3, None) for n in sig], kw=lambda n: {"grid": GRID, "via_cost": 25.0}, passes=4)
    # the ground fill on top (declared before routing, in routed()) tied to the bottom pour by stitching vias
    b.stitch("GND", OUTLINE, pitch=4.0, drill=0.4, dia=0.8)
    groups = b.islands("GND")                          # a pin boxed in by tracks on both layers: join it back
    if len(groups) > 1:
        main = max(groups, key=len)
        for g in groups:
            if g is not main:
                probs += route(b, "GND", width=0.4, amps=0.3, only=g, seed_pins=main, grid=GRID)
    return probs


def pours(b):
    """the copper zones (part of the design, not of the cached routing): the bottom a ground pour for the logic return
    (the drivers' returns have their own tracks), the top a ground fill round everything"""
    b.pour("GND", "B", OUTLINE, amps=1.0)
    b.pour("GND", "F", OUTLINE, amps=1.0, fill=True)


def routed(store=None):
    b = build()
    pours(b)
    probs = cached_route(b, do_route, tag="cube-main-r9", store=store)
    return b, probs


PIN_NAMES = {
    "H1": dict(zip([str(i) for i in range(1, 16)], "EN VP VN D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND VIN".split())),
    "H2": dict(zip([str(i) for i in range(1, 16)], "D23 D22 TX0 RX0 D21 D19 D18 D5 TX2 RX2 D4 D2 D15 GND 3V3".split())),
    "J1": {"1": "-", "2": "+"}, "C1": {"1": "+", "2": "-"},
    "U1": dict(zip("12345", "PG SHDN VIN GND VOUT".split())),
    "U2": dict(zip("12345678", "VCC GND SCL SDA XDA XCL AD0 INT".split())),
    **{f"D{k}H": {**{str(2 * i + 2): n for i, n in enumerate(MINI_PINS["A"])},
                  **{str(2 * i + 1): n for i, n in enumerate(MINI_PINS["B"])}} for k in "XYZ"},
    **{f"D{k}M": {"1": "M1", "2": "M2", "3": "M3"} for k in "XYZ"},
    **{f"JE{k}": dict(zip("123456", "3V3 GND SCK MISO MOSI CS".split())) for k in "XYZ"},
    **{f"JM{k}": {"1": "A", "2": "B", "3": "C"} for k in "XYZ"},
}


def export(out):
    """everything to order and assemble the board: copper plot, BOM, Gerbers + drill, the KiCad project, KiCad's own
    DRC on our fill and on KiCad's refill (both must be clean), KiCad renders, the zips"""
    import csv
    import tempfile
    import pcb
    b, probs = routed(store=os.path.join(HERE, "routes"))
    chk = b.check()
    print("route:", probs or "clean", "| check:", chk or "clean")
    assert not probs and not chk
    os.makedirs(out, exist_ok=True)
    b.plot(os.path.join(out, "layers.png"))
    with open(os.path.join(out, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "mpn", "footprint"])
        for r in b.bom():
            w.writerow([" ".join(r["refs"]), r["qty"], r["value"], r["mpn"], r["footprint"]])
    pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    kpcb = pcb.export_kicad(b, os.path.join(out, "kicad"))[0]
    for refill in (False, True):
        d = pcb.kicad_drc(kpcb, refill=refill)
        print(f"KiCad DRC ({'KiCad refill' if refill else 'as exported'}):", len(d["errors"]), "errors,",
              len(d["unconnected"]), "unconnected,", d["warnings"] or "no warnings")
        for p in (d["errors"] + d["unconnected"])[:30]:
            print("  ", p)
    mdir = os.path.join(tempfile.gettempdir(), "kicad_3d")
    print("3D models not found:", pcb.kicad_models(b, mdir) or "none")
    pcb.render_kicad(kpcb, out, mdir)
    for d in ("gerbers", "kicad"):
        pcb.pack(os.path.join(out, d))
    return b


if __name__ == "__main__":
    import sys
    export(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out"))
