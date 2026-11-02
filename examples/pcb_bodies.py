"""3D bodies (build123d, footprint frame: board top z = 0) for parts KiCad's model library lacks, shared by the
board programs. Outlines from the footprints' courtyards and the makers' published sizes; approximate in the details."""

def _xt_body(width, height, length, y0, pitch, cx, male, scale=1.0):
    """an AMASS XT horizontal PCB connector: the D-profile nylon housing from the pins forward to the mating face,
    the two contacts showing in it"""
    import build123d as bd
    yellow, gold = bd.Color(0.93, 0.76, 0.12), bd.Color(0.85, 0.68, 0.30)
    housing = bd.Pos(cx, y0 + length / 2, height / 2) * bd.Box(width, length, height)
    housing = bd.chamfer(housing.edges().filter_by(bd.Axis.Y).sort_by(bd.Axis.Z)[-2:], 0.22 * height)
    r_hole, depth = 0.18 * width, length * 0.6
    for dx in (-pitch / 2, pitch / 2):                                         # the contact bores in the mating face
        housing -= bd.Pos(cx + dx, y0 + length - depth / 2, height * 0.48) * bd.Rot(90, 0, 0) * bd.Cylinder(r_hole, depth)
    housing.color = yellow
    contacts = [bd.Pos(cx + dx, y0 + length - depth / 2 - (0.0 if male else 1.0), height * 0.48) * bd.Rot(90, 0, 0) *
                bd.Cylinder(r_hole * (0.55 if male else 0.85), depth - (0.4 if male else 2.0)) for dx in (-pitch / 2, pitch / 2)]
    for c_ in contacts:
        c_.color = gold
    return bd.Compound(children=[housing] + contacts)


def xt30pw_body(male):
    # XT30PW: 10.2 x 5.2 mm housing, 5.0 mm contact pitch; the F footprint's pin 2 is at +5, the M's at -5
    return _xt_body(10.2, 5.2, 14.0, 0.4, 5.0, -2.5 if male else 2.5, male)


def xt90pw_body():
    # XT90PW: 21 x 9.6 mm housing, 10.9 mm contact pitch, 24 mm from the pins to the mating face
    return _xt_body(21.0, 9.6, 24.0, 0.8, 10.9, 5.45, True)


def brick_fuse_body():
    # Eaton CB61F 2410 brick: 6.1 x 2.57 x 2.57 mm ceramic body, silver end terminations
    import build123d as bd
    body = bd.Pos(0, 0, 1.285) * bd.Box(4.1, 2.57, 2.57)
    body.color = bd.Color(0.92, 0.90, 0.84)
    ends = [bd.Pos(sx * 2.55, 0, 1.285) * bd.Box(1.0, 2.6, 2.6) for sx in (-1, 1)]
    for e in ends:
        e.color = bd.Color(0.78, 0.79, 0.80)
    return bd.Compound(children=[body] + ends)




def _box(x0, y0, z0, x1, y1, z1, color):
    import build123d as bd
    b = bd.Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * bd.Box(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0))
    b.color = bd.Color(*color)
    return b


GREEN, BLACK, SILVER, GOLD, WHITE = (0.10, 0.42, 0.22), (0.08, 0.08, 0.09), (0.78, 0.79, 0.80), (0.85, 0.68, 0.30), \
    (0.93, 0.93, 0.90)


def teensy41_body(z0=11.0):
    """PJRC Teensy 4.1 as it sits in a pair of 1x24 sockets (frame of the socket with its pin 1 at the origin, pins
    along -y, the other row at x = +15.24): its header strips on the sockets, the 61 x 17.8 mm board ``z0`` up, the
    i.MX RT1062, the flash, the micro-USB at the pin-1 end and the microSD socket at the other"""
    import build123d as bd
    xa, xb = -1.27, 16.51                      # board edges (rows at x = 0 and 15.24)
    ya, yb = 1.27 - 60.96, 1.27                # far end, USB end
    parts = [_box(-1.27, ya + 0.0, z0 - 2.5, 1.27, yb - 0.6, z0, BLACK),          # header plastic, both rows
             _box(15.24 - 1.27, ya + 0.0, z0 - 2.5, 15.24 + 1.27, yb - 0.6, z0, BLACK),
             _box(xa, ya, z0, xb, yb, z0 + 1.6, GREEN),                            # the board
             _box(7.62 - 5.0, -24.0, z0 + 1.6, 7.62 + 5.0, -14.0, z0 + 2.8, BLACK),  # i.MX RT1062 (10 x 10 BGA)
             _box(7.62 - 2.6, -33.0, z0 + 1.6, 7.62 + 2.6, -27.6, z0 + 2.8, BLACK),  # W25Q64 boot flash
             _box(7.62 - 3.9, yb - 5.0, z0 + 1.6, 7.62 + 3.9, yb + 0.7, z0 + 4.4, SILVER),  # micro-USB B
             _box(7.62 - 6.0, ya - 1.5, z0 + 1.6, 7.62 + 6.0, ya + 11.0, z0 + 3.1, SILVER),  # microSD socket
             _box(7.62 - 1.5, -8.0, z0 + 1.6, 7.62 + 1.5, -5.0, z0 + 2.3, WHITE)]     # program button
    pins = []
    for row in (0.0, 15.24):
        for k in range(24):
            p = bd.Pos(row, -2.54 * k, z0 + 1.6 + 0.6) * bd.Box(0.64, 0.64, 1.2)
            p.color = bd.Color(*GOLD)
            pins.append(p)
    return bd.Compound(children=parts + pins)


def rfm95w_body():
    """HopeRF RFM95W: 16 x 16 mm green module with its can"""
    return bd_compound([_box(-8.0, -8.0, 0.0, 8.0, 8.0, 0.8, GREEN), _box(-6.5, -6.0, 0.8, 6.0, 6.5, 2.2, SILVER)])


def ws2812_2020_body():
    return bd_compound([_box(-1.0, -1.0, 0.0, 1.0, 1.0, 0.6, WHITE), _box(-0.8, -0.8, 0.6, 0.8, 0.8, 0.84, (0.85, 0.88, 0.95))])


def buzzer_body():
    """CUI CMT-8504-100-SMT: 8.5 x 8.5 x 3 mm black square, round sound port on top"""
    import build123d as bd
    b = _box(-4.25, -4.25, 0.0, 4.25, 4.25, 3.0, BLACK)
    b = b - bd.Pos(0, 0, 2.6) * bd.Cylinder(1.0, 1.0)
    b.color = bd.Color(*BLACK)
    return b


def bmp390_body():
    """Bosch BMP390: 2.0 x 2.0 x 0.75 mm metal-lid LGA with its port hole"""
    import build123d as bd
    base = _box(-1.0, -1.0, 0.0, 1.0, 1.0, 0.25, (0.85, 0.82, 0.70))
    lid = _box(-0.92, -0.92, 0.25, 0.92, 0.92, 0.75, SILVER) - bd.Pos(0.4, 0.4, 0.7) * bd.Cylinder(0.125, 0.2)
    lid.color = bd.Color(*SILVER)
    return bd_compound([base, lid])


def ms5607_body():
    """TE MS5607-02BA03: 3.0 x 5.0 mm ceramic base, stainless cap (1.0 mm tall)"""
    import build123d as bd
    cap = bd.Pos(0, 0, 0.6) * bd.Box(2.7, 4.7, 0.8)
    cap = cap - bd.Pos(0, 0, 0.9) * bd.Cylinder(0.25, 0.4)
    cap.color = bd.Color(*SILVER)
    return bd_compound([_box(-1.5, -2.5, 0.0, 1.5, 2.5, 0.2, WHITE), cap])


def servo_block_body():
    """3 x 6 pin header (2.54 mm): black spacer and the gold posts (pad 1 at the origin, columns +x, rows -y)"""
    import build123d as bd
    parts = [_box(-1.27, -6.35, 0.0, 13.97, 1.27, 2.54, BLACK)]
    for c in range(6):
        for r in range(3):
            p = bd.Pos(2.54 * c, -2.54 * r, 2.54 + 3.0) * bd.Box(0.64, 0.64, 6.0)
            p.color = bd.Color(*GOLD)
            parts.append(p)
    return bd_compound(parts)


def bd_compound(children):
    import build123d as bd
    return bd.Compound(children=children)
