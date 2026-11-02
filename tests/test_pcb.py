"""The PCB harness's tests (pcb.py, schem.py): routing, checks, Gerbers, KiCad export, four layers, stitching,
schematic sheets. Some fetch KiCad footprints on first run (cached in ~/.cache/prometheus/parts)."""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_pcb_board_routes_and_checks():
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
        pcb.footprint("Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical")
    except Exception as e:                                      # the footprints come from KiCad's library online
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("t", [(0, 0), (40, 0), (40, 30), (0, 30)], copper_oz=1.0, clearance=0.2)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (8, 8))
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (30, 22), rot=90)
    b.place("R3", "Resistor_SMD:R_0603_1608Metric", (20, 15))   # in the way, on another net
    b.place("J1", "Connector_AMASS:AMASS_XT30UPB-F_1x02_P5.0mm_Vertical", (8, 22))
    b.net("A", [("R1", "2"), ("R2", "1")], amps=0.1)
    b.net("B", [("R3", "1"), ("R3", "2")], amps=0.1)
    b.net("P", [("J1", "1"), ("R1", "1")], amps=8.0)
    assert not pcb.route(b, "A", width=0.25) and not pcb.route(b, "B", width=0.25)
    b.track("P", "F", [b.pad("J1", "1"), (8, 14), b.pad("R1", "1")], width=0.3)      # too thin for 8 A
    probs = b.check()
    assert not any("split" in p or "short" in p or "apart" in p for p in probs), probs
    assert any("too narrow for 8.0 A" in p for p in probs)
    assert pcb.ipc2221_width(8.0, 1.0) > 2.0
    b.track("A", "F", [b.pad("R3", "1"), b.pad("R1", "2")], width=0.25)              # a short through R3's pad
    assert any("short" in p for p in b.check())


def test_pcb_keepout_routes_round_and_is_checked():
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    from shapely.geometry import Polygon
    b = pcb.Board("k", [(0, 0), (40, 0), (40, 30), (0, 30)], copper_oz=1.0, clearance=0.2)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 15))
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (35, 15))
    b.net("A", [("R1", "2"), ("R2", "1")])
    b.net("GND", [("R1", "1"), ("R2", "2")])
    zone = [(15, 2), (25, 2), (25, 28), (15, 28)]                 # an antenna's keep-out across the middle
    b.keepout(zone, "FB", "antenna")
    b.pour("GND", "B", [(0, 0), (40, 0), (40, 30), (0, 30)])
    assert not pcb.route(b, "A", width=0.25)
    cu = b.copper()
    assert all(g.intersection(Polygon(zone)).area < 0.01 for g in cu.values())   # round it, pour carved
    assert not any("keep-out" in p for p in b.check())
    b.track("A", "F", [(10, 15), (30, 15)], width=0.25)
    assert any("keep-out (antenna)" in p for p in b.check())


def test_pcb_gerbers_round_trip(tmp_path):
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("g", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 5))
    b.net("GND", [("R1", "1"), ("R1", "2")])
    b.pour("GND", "B", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.via("GND", (9, 5))
    b.hole((16, 5), 2.2)
    files = pcb.export_gerbers(b, str(tmp_path))
    names = sorted(p.split("/")[-1] for p in files)
    assert "g-F_Cu.gtl" in names and "g-B_Cu.gbl" in names and "g-PTH.drl" in names and "g-NPTH.drl" in names
    txt = open(tmp_path / "g-B_Cu.gbl").read()
    assert txt.startswith("%FSLAX46Y46*%") and "G36*" in txt and txt.rstrip().endswith("M02*")


def test_pcb_gerber_keeps_pads_inside_pour_clearances(tmp_path):
    """copper inside another net's pour clearance (a pad in its hole) survives in the Gerber: the file, read back
    independently (dark regions added, clear regions erased, in file order), gives exactly the board's copper"""
    import re
    import pytest
    pytest.importorskip("shapely")
    import pcb
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    b = pcb.Board("h", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.2)
    for i, x in enumerate((6, 12, 18, 24)):                          # pads of other nets inside the GND pour's holes
        b.place(f"R{i}", "Resistor_SMD:R_1206_3216Metric", (x, 10), side="B")
        b.net(f"N{i}", [(f"R{i}", "1")])
        b.net(f"M{i}", [(f"R{i}", "2")])
    b.place("R9", "Resistor_SMD:R_1206_3216Metric", (15, 3), side="B")
    b.net("GND", [("R9", "1"), ("R9", "2")])
    b.pour("GND", "B", [(0, 0), (30, 0), (30, 20), (0, 20)])
    import shapely.ops
    from shapely.geometry import MultiPolygon
    union = shapely.ops.unary_union

    def islands_first(geoms):                                       # (the order that lost copper on the arm PDB)
        g = union(geoms)
        return MultiPolygon(sorted(g.geoms, key=lambda q: q.area)) if hasattr(g, "geoms") else g
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shapely.ops, "unary_union", islands_first)
        pcb.export_gerbers(b, str(tmp_path))
    txt = open(tmp_path / "h-B_Cu.gbl").read()
    got, pol = Polygon(), "D"
    for m in re.finditer(r"%LP([DC])\*%|G36\*(.*?)G37\*", txt, re.S):
        if m.group(1):
            pol = m.group(1)
            continue
        ring = Polygon([(int(x) / 1e6, int(y) / 1e6) for x, y in re.findall(r"X(-?\d+)Y(-?\d+)", m.group(2))])
        got = got.union(ring) if pol == "D" else got.difference(ring)
    want = unary_union([g for (n, l), g in b.copper().items() if l == "B"])
    assert want.area > 0 and got.symmetric_difference(want).area < 1e-4


def test_pcb_drills_keep_apart_and_stitch_avoids_pads():
    """a via drilled into a connector pin's hole is flagged; stitching never puts one there (or in any pad)"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        fp = "Connector_AMASS:AMASS_XT30PW-F_1x02_P2.50mm_Horizontal"
        pcb.footprint(fp)
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    from shapely.geometry import Point
    b = pcb.Board("d", [(0, 0), (30, 0), (30, 30), (0, 30)])
    b.place("J1", fp, (10, 10))
    b.net("GND", [("J1", "1")])
    b.net("P", [("J1", "2")])
    for L in "FB":
        b.pour("GND", L, [(1, 1), (29, 1), (29, 29), (1, 29)])
    assert b.stitch("GND", [(1, 1), (29, 1), (29, 29), (1, 29)], pitch=1.0) > 0
    pin = Point(b.pad("J1", "1"))
    assert all(Point(v["at"]).distance(pin) >= 1.9 / 2 + 0.3 / 2 + pcb.HOLE_GAP - 1e-6 for v in b.vias)
    assert not any("drills" in p for p in b.check())
    b.via("GND", (10.4, 10.0))                                      # into J1 pin 1's 1.9 mm hole
    assert any("drills" in p and "J1 pad 1" in p for p in b.check())


def test_pcb_labels_courtyard_circles_dead_copper():
    """silkscreen labels land clear of every pad; a radial cap's circular courtyard is read; a pour island touching
    nothing of its net is cut away"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
        cp = pcb.footprint("Capacitor_THT:CP_Radial_D12.5mm_P5.00mm")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    from shapely.geometry import box
    assert cp["courtyard"] and cp["courtyard"][2] - cp["courtyard"][0] > 12.5          # the circle, not None
    b = pcb.Board("l", [(0, 0), (30, 0), (30, 20), (0, 20)])
    for i in range(4):
        b.place(f"R{i + 1}", "Resistor_SMD:R_0603_1608Metric", (8 + 4 * i, 10))
    b.net("A", [("R1", "1"), ("R2", "1")])
    labels = pcb.place_refs(b)
    pads = [g for _, _, _, g, _ in b.pad_shapes()]
    for ref, v in labels.items():
        assert v, ref
        x, y, a, size = v
        w, h = len(ref) * size * 0.78 + 0.2, size + 0.25
        bb = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2) if a == 0 else box(x - h / 2, y - w / 2, x + h / 2, y + w / 2)
        assert not any(bb.intersects(g) for g in pads), ref
    b.pour("A", "B", [(1, 1), (6, 1), (6, 19), (1, 19)])                  # on the bottom, touching no A copper
    assert ("A", "B") not in b.copper() or b.copper()[("A", "B")].area < 1e-6


def test_schematic_layout_check_and_junction_dots():
    """a sheet's layout is checked as a reader sees it: overlapping text, a wire through a label, anything in the
    title block or off the sheet is reported; junction dots land on every T and on no corner"""
    import pytest
    pytest.importorskip("shapely")
    pytest.importorskip("schemdraw")
    pytest.importorskip("cairosvg")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    import schemdraw.elements as elm
    from schem import Sheet
    b = pcb.Board("s", [(0, 0), (20, 0), (20, 10), (0, 10)])
    for r, x in (("R1", 5), ("R2", 10), ("R3", 15)):
        b.place(r, "Resistor_SMD:R_0603_1608Metric", (x, 5))
    b.net("A", [("R1", "1"), ("R2", "1"), ("R3", "1")])
    b.net("GND", [("R1", "2"), ("R2", "2"), ("R3", "2")])

    def sheet(bad):
        s = Sheet(b, unit=2.0, fontsize=8)
        s.frame(0, 0, 40, 28, "TEST", "1 / 1", "board", date="today")
        s.port((4, 20), "A")
        for i, r in enumerate(("R1", "R2", "R3")):              # three resistors hung off one rail
            x = 4 + 3 * i
            if i:
                s.wire((x - 3, 20), (x, 20))
            e = s.add(r, elm.Resistor().at((x, 20)).down(), {"1": "start", "2": "end"})
            s.gnd(e.absanchors["end"])
        s.text((12, 23), "RAIL A", size=8, halign="left")
        if bad:
            s.text((12.2, 23), "RAIL A again", size=8, halign="left")   # on top of the first
            s.text((5.5, 20.1), "ACROSS", size=8, halign="center", valign="center")   # on the rail wire
            s.text((33, 2.5), "in the title block", size=8, halign="left")
        return s
    good, bad = sheet(False), sheet(True)
    assert good.layout_problems() == [] and good.dot_problems() == []
    probs = bad.layout_problems()
    assert any("overlaps" in p for p in probs)
    assert any("runs through text 'ACROSS'" in p for p in probs)
    assert any("title block" in p for p in probs)
    assert (7.0, 20.0) in good.dots and (4.0, 20.0) in good.dots and (10.0, 20.0) not in good.dots


def test_schematic_every_part_named_beside_it():
    """each part needs its reference (and a passive its value) beside its own symbol: a caption across the sheet,
    or one listing parts of different values together, does not count; autoname() names what is missing"""
    import pytest
    pytest.importorskip("shapely")
    pytest.importorskip("schemdraw")
    pytest.importorskip("cairosvg")
    try:
        import pcb
        pcb.footprint("Capacitor_SMD:C_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    import schemdraw.elements as elm
    from schem import Sheet
    b = pcb.Board("n", [(0, 0), (20, 0), (20, 10), (0, 10)])
    for r, x, v in (("C1", 5, "100n"), ("C2", 10, "10u")):
        b.place(r, "Capacitor_SMD:C_0603_1608Metric", (x, 5), value=v)
    b.net("A", [("C1", "1"), ("C2", "1")])
    b.net("GND", [("C1", "2"), ("C2", "2")])
    s = Sheet(b, unit=2.0, fontsize=8)
    s.frame(0, 0, 40, 28, "TEST", "1 / 1", "board", date="today")
    s.port((4, 20), "A")
    s.wire((4, 20), (8, 20))
    for r, x in (("C1", 4), ("C2", 8)):
        e = s.add(r, elm.Capacitor().at((x, 20)).down().length(1.4), {"1": "start", "2": "end"})
        s.gnd(e.absanchors["end"])
    s.text((5.0, 17.0), "C1 100n  C2 10u", size=6, halign="left")      # a shared caption of unlike parts
    assert sorted(s.label_problems()) == ["C1 has no label beside it", "C2 has no label beside it"]
    s.autoname()
    assert s.label_problems() == [] and s.layout_problems() == []


def test_schematic_sheet_is_checked_against_the_board():
    """a drawn sheet whose wires match the netlist passes; one wire to the wrong pin is caught"""
    import pytest
    pytest.importorskip("shapely")
    pytest.importorskip("schemdraw")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    import schemdraw.elements as elm
    from schem import Sheet
    b = pcb.Board("s", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 5))
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (12, 5))
    b.net("VIN", [("R1", "1")])
    b.net("MID", [("R1", "2"), ("R2", "1")])
    b.net("GND", [("R2", "2")])

    def sheet(wrong):
        s = Sheet(b)
        s.port((0, 0), "VIN")
        r1 = s.add("R1", elm.Resistor().at((0, 0)).down(), {"1": "start", "2": "end"})
        r2 = s.add("R2", elm.Resistor().at((3, 0)).down(), {"1": "start", "2": "end"})
        s.wire(r1.absanchors["end"], r2.absanchors["end" if wrong else "start"])
        s.gnd(r2.absanchors["start" if wrong else "end"])
        return s.check()
    assert sheet(False) == []
    assert sheet(True)
    from schem import check_sheets                     # two pages joined by an off-sheet connector
    p1, p2 = Sheet(b), Sheet(b)
    p1.port((0, 0), "VIN")
    r1 = p1.add("R1", elm.Resistor().at((0, 0)).down(), {"1": "start", "2": "end"})
    p1.offpage(r1.absanchors["end"], "MID")
    r2 = p2.add("R2", elm.Resistor().at((0, 0)).down(), {"1": "start", "2": "end"})
    p2.offpage(r2.absanchors["start"], "MID", side="left")
    p2.gnd(r2.absanchors["end"])
    assert check_sheets(b, [p1, p2]) == []


def test_pcb_four_layers_planes_and_drop(tmp_path):
    """a 4-layer board: pads reach their inner planes through dropped vias, the planes clear the other nets' vias,
    check() is clean, and the Gerbers / KiCad file carry all four layers"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("q", [(0, 0), (30, 0), (30, 20), (0, 20)], layers=4, inner_oz=0.5)
    for i, x in enumerate((8, 15, 22)):
        b.place(f"R{i + 1}", "Resistor_SMD:R_0603_1608Metric", (x, 10))
    b.net("GND", [("R1", "1"), ("R2", "1"), ("R3", "1")], amps=0.5, volts=0)
    b.net("VCC", [("R1", "2"), ("R2", "2"), ("R3", "2")], amps=0.5, volts=3.3)
    full = [(0, 0), (30, 0), (30, 20), (0, 20)]
    b.pour("GND", "1", full)
    b.pour("VCC", "2", full)
    assert b.drop("GND") == [] and b.drop("VCC") == []
    assert b.check() == []
    cu = b.copper()
    vcc_vias = [v for v in b.vias if v["net"] == "VCC"]
    from shapely.geometry import Point
    assert all(not cu[("GND", "1")].contains(Point(v["at"])) for v in vcc_vias)       # antipads in the GND plane
    names = [p.split("/")[-1] for p in pcb.export_gerbers(b, str(tmp_path / "g"))]
    assert "q-In1_Cu.g2" in names and "q-In2_Cu.g3" in names
    txt = open(pcb.export_kicad(b, str(tmp_path / "k"))[0]).read()
    assert '"In1.Cu"' in txt and '"In2.Cu"' in txt


def test_pcb_pack_is_reproducible(tmp_path):
    """``pack`` zips a build folder beside itself and removes it; the same files give a byte-identical zip, so a
    rebuild with no change leaves git clean"""
    import zipfile
    import pcb
    zips = []
    for run in ("a", "b"):
        d = tmp_path / run / "kicad"
        (d / "3d").mkdir(parents=True)
        (d / "x.kicad_pcb").write_text("(kicad_pcb)")
        (d / "3d" / "m.step").write_text("ISO-10303-21;")
        z = pcb.pack(str(d))
        assert not d.exists() and z == str(d) + ".zip"
        zips.append(open(z, "rb").read())
    assert zips[0] == zips[1]
    assert sorted(zipfile.ZipFile(z).namelist()) == ["kicad/3d/m.step", "kicad/x.kicad_pcb"]


def test_pcb_cached_route_store_round_trip(tmp_path, monkeypatch):
    """``cached_route(store=)`` keeps the routing as gzipped JSON in a committed folder: a fresh machine (empty cache)
    loads the same tracks, vias and fan-outs from it instead of re-routing, and a new design replaces the old file"""
    import pcb
    import vendor_parts

    def board():
        b = pcb.Board("rt", [(0, 0), (20, 0), (20, 10), (0, 10)], clearance=0.2)
        b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 5))
        b.place("R2", "Resistor_SMD:R_0603_1608Metric", (15, 5))
        b.net("A", [("R1", "2"), ("R2", "1")], amps=0.1)
        return b

    calls = []

    def do_route(b):
        calls.append(1)
        b._fan = {("R1", "2")}
        return pcb.route(b, "A", width=0.25)

    store = tmp_path / "routes"
    b1 = board()                                                    # (footprints loaded before the cache moves)
    monkeypatch.setattr(vendor_parts, "CACHE", tmp_path / "cache")
    p1 = pcb.cached_route(b1, do_route, tag="t", store=str(store))
    assert calls == [1] and len(list(store.glob("rt_*.json.gz"))) == 1
    (tmp_path / "cache").rename(tmp_path / "gone")                  # a fresh clone: only the committed store
    b2 = board()
    p2 = pcb.cached_route(b2, do_route, tag="t", store=str(store))
    assert calls == [1] and p2 == p1
    assert b2.tracks == b1.tracks and b2.vias == b1.vias and b2._fan == b1._fan
    b3 = board()
    pcb.cached_route(b3, do_route, tag="t2", store=str(store))     # a changed design re-routes, replaces the file
    assert calls == [1, 1] and len(list(store.glob("rt_*.json.gz"))) == 1


def test_pcb_stitch_clears_tracks_on_every_layer():
    """a stitching via goes through all four layers: it keeps clear of another net's track on a layer it does not
    tie (here a track on In2 under ground fills on F, In1 and B)"""
    import pytest
    pytest.importorskip("shapely")
    import pcb
    from shapely.geometry import LineString, Point
    full = [(0, 0), (20, 0), (20, 10), (0, 10)]
    b = pcb.Board("s", full, layers=4, clearance=0.15)
    b.net("GND", [], volts=0)
    b.net("SIG", [])
    for L in ("F", "1", "B"):
        b.pour("GND", L, full)
    b.track("SIG", "2", [(1, 5), (19, 5)], 0.3)
    b.via("GND", (1.0, 1.0), 0.3, 0.6)                 # (a pour keeps only the islands touching its net's copper)
    assert b.stitch("GND", full, pitch=1.0, layers="F1B") > 10
    line = LineString([(1, 5), (19, 5)])
    assert all(Point(v["at"]).distance(line) >= 0.15 + 0.3 + 0.15 - 1e-6 for v in b.vias)


def test_pcb_stitch_pierces_a_fill_but_not_a_power_pour():
    """stitching vias for one net may pass through another net's background fill (the fill clears round them) but
    never perforate another net's power pour on an outer layer"""
    import pytest
    pytest.importorskip("shapely")
    import pcb
    from shapely.geometry import Point, Polygon
    full = [(0, 0), (20, 0), (20, 10), (0, 10)]
    left, right = [(0, 0), (10, 0), (10, 10), (0, 10)], [(10, 0), (20, 0), (20, 10), (10, 10)]
    b = pcb.Board("f", full, layers=4, clearance=0.15)
    b.net("VIN", [], amps=5.0, volts=12)
    b.net("GND", [], volts=0)
    b.net("VBAT", [], amps=5.0, volts=12)
    b.pour("VIN", "F", full)
    b.pour("VIN", "2", full)
    b.pour("GND", "B", left, fill=True)                # a ground fill: may be pierced
    b.pour("VBAT", "B", right)                         # a power pour: kept whole
    b.via("VIN", (0.8, 0.8), 0.3, 0.6)
    b.via("GND", (2.0, 9.0), 0.3, 0.6)
    b.via("VBAT", (19.0, 9.0), 0.3, 0.6)
    assert b.stitch("VIN", full, pitch=1.0, layers="F2") > 10
    vin = [Point(v["at"]) for v in b.vias if v["net"] == "VIN"]
    assert any(Polygon(left).contains(p) and p.x < 9 for p in vin)
    assert not any(Polygon(right).buffer(0.3).contains(p) for p in vin)


def test_pcb_route_changes_layer_in_a_plated_hole_without_a_via():
    """a route that changes layer at a through-hole pin of its own net uses the plated hole (no via drilled into
    the pin), and no via it places comes nearer a drill than HOLE_GAP"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("h", [(0, 0), (20, 0), (20, 12), (0, 12)], clearance=0.15)
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", (4, 8.54))
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (15, 6), side="B")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (15, 9))
    b.net("A", [("J1", "2"), ("R1", "1"), ("R2", "1")])
    b.net("B", [("J1", "1"), ("J1", "3"), ("R1", "2"), ("R2", "2")])
    assert pcb.route(b, "A", width=0.2, grid=0.254) == []
    assert pcb.route(b, "B", width=0.2, grid=0.254) == []
    assert not [p for p in b.check() if "drill" in p or "split" in p or "short" in p]


def test_pcb_project_footprint_library():
    """footprints KiCad's library lacks come from pcb_footprints/ (no download): the BMP390's ten pads on
    Bosch's land, the servo block's 18 plated holes, numbered column by column"""
    import pytest
    pytest.importorskip("shapely")
    import pcb
    bmp = pcb.footprint("Prometheus_Sensor:Bosch_BMP390_LGA-10_2x2mm_P0.5mm")
    assert sorted(bmp["pads"], key=int) == [str(i) for i in range(1, 11)]
    assert bmp["pads"]["1"][0]["at"] == (0.25, 0.7625) and bmp["pads"]["4"][0]["at"] == (-0.7625, 0.0)
    ms = pcb.footprint("Prometheus_Sensor:TE_MS5607-02BA03_QFN-8_5x3mm_P1.25mm")
    assert ms["pads"]["1"][0]["at"] == (-1.1, 1.875) and ms["pads"]["8"][0]["at"] == (1.1, 1.875)
    blk = pcb.footprint("Prometheus_Connector:PinHeader_3x06_P2.54mm_Vertical")
    assert len(blk["pads"]) == 18 and all(p[0]["drill"] for p in blk["pads"].values())
    assert blk["pads"]["4"][0]["at"] == (2.54, 0.0) and blk["pads"]["3"][0]["at"] == (0.0, -5.08)


def test_pcb_route_through_an_inner_layer():
    """with both outer layers walled off, route() crosses in an inner layer when it is offered one (two vias), and
    the board checks clean"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("w", [(0, 0), (30, 0), (30, 20), (0, 20)], layers=4, clearance=0.15)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 10))
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (25, 10))
    b.net("A", [("R1", "2"), ("R2", "1")], amps=0.1)
    b.net("G", [("R1", "1"), ("R2", "2")], amps=0.1, volts=0)
    for L in "FB":                                       # a wall across the board on both outer layers
        b.track("G", L, [(15, 1), (15, 19)], 3.0)
    assert pcb.route(b, "A", width=0.15, grid=0.254, layers="FB")       # outer layers only: no way across
    assert pcb.route(b, "A", width=0.15, grid=0.254, layers="F2B") == []
    assert "2" in {t["layer"] for t in b.tracks if t["net"] == "A"}
    assert sum(v["net"] == "A" for v in b.vias) >= 2
    assert not [p for p in b.check() if "net A" in p or " A " in p]


def test_pcb_route_seed_pins_joins_an_island_to_the_main_group():
    """a pad sitting in a piece of its net's pour that nothing joins to the rest passes for routed, unless route()
    is told which pads are the main group (seed_pins): then it is routed back to them"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("i", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.15)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 10))
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (25, 10))
    b.net("A", [("R1", "2"), ("R2", "1")], amps=0.1)
    b.net("G", [("R1", "1"), ("R2", "2")], amps=0.1, volts=0)
    b.pour("A", "F", [(20, 2), (29, 2), (29, 18), (20, 18)])        # an island of A's pour round R2 only
    assert pcb.route(b, "A", width=0.2, only=[("R2", "1")]) == []
    assert len(b.islands("A")) == 2                                # (taken as reached: nothing was routed)
    assert pcb.route(b, "A", width=0.2, only=[("R2", "1")], seed_pins=[("R1", "2")]) == []
    assert len(b.islands("A")) == 1


def test_pcb_check_finds_a_pour_neck_and_says_where():
    """a 5 A pour joining two pads through a 0.6 mm neck is flagged, at the neck; the same pour with a wide
    bridge is not"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")

    def board(neck):
        b = pcb.Board("n", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.15)
        b.place("R1", "Resistor_SMD:R_0603_1608Metric", (6, 10))
        b.place("R2", "Resistor_SMD:R_0603_1608Metric", (24, 10))
        b.net("P", [("R1", "2"), ("R2", "1")], amps=5.0, volts=12)
        b.net("G", [], volts=0)
        h = neck / 2
        b.pour("P", "F", [(2, 4), (12, 4), (12, 10 - h), (18, 10 - h), (18, 4), (28, 4), (28, 16), (18, 16),
                          (18, 10 + h), (12, 10 + h), (12, 16), (2, 16)])
        return [p for p in b.check() if "neck" in p]
    necks = board(0.6)
    assert len(necks) == 1 and "near (1" in necks[0]
    x = float(necks[0].split("near (")[1].split(",")[0])
    assert 11 <= x <= 19
    assert board(8.0) == []


def test_pcb_reuse_routes_keeps_what_a_move_does_not_disturb():
    """after one part moves, the earlier routing goes back for the nets it does not touch; the moved part's nets,
    and a net whose track the part now sits on, are left to route again"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")

    def board(r3_at):
        b = pcb.Board("u", [(0, 0), (40, 0), (40, 20), (0, 20)], clearance=0.15)
        for ref, at in (("R1", (5, 5)), ("R2", (35, 5)), ("R3", r3_at), ("R4", (5, 15)), ("R5", (35, 15))):
            b.place(ref, "Resistor_SMD:R_0603_1608Metric", at)
        b.net("A", [("R1", "2"), ("R2", "1")])
        b.net("B", [("R4", "2"), ("R5", "1")])
        b.net("C", [("R3", "1"), ("R3", "2")])
        return b
    b = board((20, 10))
    for n in "ABC":
        assert pcb.route(b, n, width=0.2, layers="F") == []
    old = {"placement": pcb.placement(b), "tracks": list(b.tracks), "vias": list(b.vias)}
    b2 = board((20, 10))                                   # nothing moved: all of it comes back
    assert pcb.reuse_routes(b2, old) == set() and len(b2.tracks) == len(old["tracks"])
    ya = b.pad("R1", "2")[1]
    b3 = board((20, ya))                                   # R3 moved onto net A's track
    redo = pcb.reuse_routes(b3, old)
    assert redo == {"A", "C"}
    assert {t["net"] for t in b3.tracks} == {"B"}
    b4 = board((20, 10))                                   # nothing moved, but B torn up round a failed pad
    assert pcb.reuse_routes(b4, old, rip=[(20, 15, 2.0)]) == {"B"}


def test_pcb_track_ending_on_a_pad_corner_is_a_loose_end():
    """a track that reaches a rounded pad only at its corner overlaps the copper but does not end inside the pad:
    KiCad reads it unconnected, and so do loose_ends() and check(); one ending inside the pad is joined"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("c", [(0, 0), (20, 0), (20, 10), (0, 10)], clearance=0.15)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (10, 5))
    b.net("A", [("R1", "1")])
    x0, y0, x1, y1 = pcb._pad_poly(b.comps["R1"]["pads"]["1"][0], (10, 5, 0)).bounds
    b.track("A", "F", [(x0 - 2, y1 + 1), (x0, y1)], 0.2)                    # onto the box corner (rounded off)
    assert [k for k, _ in b.loose_ends()] == [0, 0]                         # (its far end dangles too)
    assert any("ends at" in p for p in b.check())
    b.tracks = []
    b.track("A", "F", [(x0 - 2, (y0 + y1) / 2), ((x0 + x1) / 2, (y0 + y1) / 2)], 0.2)
    assert [e for _, e in b.loose_ends()] == [(x0 - 2, (y0 + y1) / 2)]


def test_pcb_anchor_groups_follow_kicads_rules():
    """KiCad joins a pad to the tracks it overlaps, a track to another only where an end lies in (or on the edge
    of) it, and counts a switch's two pads numbered 1 as two pads: wired to one of them the net is split, though
    islands() (which knows pins by number) calls it whole"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
        pcb.footprint("Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("a", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.15)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 10))
    b.place("SW1", "Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92", (20, 10))
    b.net("A", [("R1", "2"), ("SW1", "1")])
    xa, ya = b.pad("R1", "2")
    sw = [pcb._pad_poly(p, (20, 10, 0)).centroid for p in b.comps["SW1"]["pads"]["1"]]
    left = min(sw, key=lambda q: q.x)
    b.track("A", "F", [(xa, ya), (xa, left.y)], 0.3)
    b.track("A", "F", [(xa - 0.15, left.y), (left.x, left.y)], 0.3)      # starts on the first track's edge
    assert len(b.islands("A")) == 1
    gs = b.anchor_groups("A")
    assert len(gs) == 2 and ("SW1", "1") in gs[0] and ("SW1", "1") in gs[1]
    right = max(sw, key=lambda q: q.x)
    b.track("A", "F", [(left.x, left.y), (right.x, right.y)], 0.3)       # the switch's pads wired together
    assert len(b.anchor_groups("A")) == 1


def test_pcb_custom_pad_circle_primitives():
    """a solder jumper's pads are a rectangle plus two half-circles: the pad is the whole 1.0 x 1.5 mm shape"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        fp = pcb.footprint("Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    x0, y0, x1, y1 = pcb._pad_poly(fp["pads"]["1"][0], (0, 0, 0)).bounds
    assert abs((x1 - x0) - 1.0) < 0.02 and abs((y1 - y0) - 1.5) < 0.02


def test_pcb_route_reaches_every_pad_of_one_number():
    """a switch with two pads numbered 1: route() takes copper to both (KiCad counts them as two pads), so the
    net comes out whole by KiCad's rule, not only by pin number"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
        pcb.footprint("Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("t", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.15)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 10))
    b.place("SW1", "Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92", (20, 10))
    b.net("A", [("R1", "2"), ("SW1", "1")])
    b.net("G", [("R1", "1"), ("SW1", "2")], volts=0)
    assert pcb.route(b, "A", width=0.25) == []
    assert len(b.anchor_groups("A")) == 1


def test_pcb_route_jobs_reroutes_only_what_a_move_disturbs(tmp_path, monkeypatch, capsys):
    """route_jobs() routes a board's nets and saves them; after one part moves, the next call puts back the
    undisturbed nets and routes only the moved part's nets again, and the board stays whole"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    import vendor_parts
    monkeypatch.setattr(vendor_parts, "CACHE", tmp_path)

    def board(r3_at):
        b = pcb.Board("rj", [(0, 0), (40, 0), (40, 20), (0, 20)], clearance=0.15)
        for ref, at in (("R1", (5, 5)), ("R2", (35, 5)), ("R3", r3_at), ("R4", (5, 15)), ("R5", (35, 15))):
            b.place(ref, "Resistor_SMD:R_0603_1608Metric", at)
        b.net("A", [("R1", "2"), ("R2", "1")])
        b.net("B", [("R4", "2"), ("R5", "1")])
        b.net("C", [("R3", "2"), ("R5", "2")])
        return b
    jobs = [("A", 0.2, None), ("B", 0.2, None), ("C", 0.2, None)]
    kw = lambda net: {"layers": "F"}
    b = board((20, 10))
    b._route_key = "first"
    assert pcb.route_jobs(b, jobs, kw) == []
    b2 = board((22, 9))                                   # R3 moved: only net C is R3's
    b2._route_key = "second"
    assert pcb.route_jobs(b2, jobs, kw) == []
    assert "1 nets to route again" in capsys.readouterr().out
    assert all(len(b2.anchor_groups(n)) == 1 for n in "ABC")


def test_pcb_kicad_board_file(tmp_path):
    """the .kicad_pcb reads back: parts on their footprints (a bottom one flipped), pads on nets, zones with holes"""
    import pytest
    pytest.importorskip("shapely")
    try:
        import pcb
        pcb.footprint("Resistor_SMD:R_0603_1608Metric")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("k", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 5), value="10k", mpn="RC0603")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (12, 5), rot=90, side="B")
    b.net("A", [("R1", "1"), ("R2", "1")])
    b.track("A", "F", [b.pad("R1", "1"), (8, 3)], 0.25)
    b.pour("GND", "B", [(0, 0), (20, 0), (20, 10), (0, 10)], holes=[[(15, 2), (18, 2), (18, 8), (15, 8)]])
    b.net("GND", [("R1", "2"), ("R2", "2")])
    b.via("A", (8, 3))
    b.hole((17, 5), 2.2)
    pcb_path, pro_path = pcb.export_kicad(b, str(tmp_path))
    tree = pcb._sx_parse(open(pcb_path).read())
    assert tree[0] == "kicad_pcb"
    fps = {next(p[2] for p in f if isinstance(p, list) and p[:2] == ["property", "Reference"]): f
           for f in tree if isinstance(f, list) and f[0] == "footprint"}
    assert set(fps) == {"R1", "R2", "H1"}
    layer = lambda f: next(p[1] for p in f if isinstance(p, list) and p[0] == "layer")
    assert layer(fps["R1"]) == "F.Cu" and layer(fps["R2"]) == "B.Cu"
    pads = lambda f: [p for p in f if isinstance(p, list) and p[0] == "pad"]
    assert all("B.Cu" in next(q for q in p if isinstance(q, list) and q[0] == "layers") for p in pads(fps["R2"]))
    nets = {p[1]: next(q[2] for q in p if isinstance(q, list) and q[0] == "net") for p in pads(fps["R1"])}
    assert nets == {"1": "A", "2": "GND"}
    zone = next(f for f in tree if isinstance(f, list) and f[0] == "zone")
    assert len([p for p in zone if isinstance(p, list) and p[0] == "polygon"]) == 2      # outline + its hole
    assert any(isinstance(f, list) and f[0] == "via" for f in tree)
    assert any(isinstance(f, list) and f[0] == "segment" for f in tree)
    import json
    assert json.load(open(pro_path))["net_settings"]["classes"][0]["clearance"] == b.clear


def test_fab_order_cpl_bom_and_cart(tmp_path):
    """fab: the pick-and-place file, the JLCPCB BOM and the cart (three shops' CSVs, buy.html) from one board"""
    import csv as _csv
    import fab
    import pcb
    b = pcb.Board("t", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5.0, 5.0), 90, "10k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (9.0, 5.0), 0, "10k")
    b.place("U1", "Package_TO_SOT_SMD:SOT-23", (15.0, 5.0), 0, "", "TI TLV431", side="B", lcsc="C125052")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (2.0, 8.0), 0, "out")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (12.0, 2.0), 0, "red")
    files = fab.order(b, str(tmp_path / "order"), boards=3)
    q = {l_["refs"][0]: l_["query"] for l_ in fab.lines(b)}
    assert q["J2"] == "1x04 2.54mm pin header" and q["D1"] == "red 0603 LED"
    assert "report" in files and "Design check" in open(files["report"]).read()
    rows = list(_csv.DictReader(open(files["cpl"])))
    rows = {r["Designator"]: r for r in rows}
    assert sorted(rows) == ["D1", "J2", "R1", "R2", "U1"]
    assert rows["R1"]["Rotation"] == "90.0" and rows["U1"]["Layer"] == "Bottom" and rows["R1"]["Mid X"] == "5.000mm"
    jlc = list(_csv.DictReader(open(files["bom_jlc"])))
    assert {r["Designator"]: r["LCSC Part #"] for r in jlc}["U1"] == "C125052"
    dk = list(_csv.DictReader(open(files["digikey"])))
    by = {r["Customer Reference"]: r for r in dk}
    assert by["U1"]["Manufacturer Part Number"] == "TLV431" and by["U1"]["Manufacturer"] == "TI"
    assert int(by["R1,R2"]["Quantity"]) == 7                        # 2 x 3 boards + 10 % spare, rounded up
    assert "10k 0603 resistor" in by["R1,R2"]["Manufacturer Part Number"]
    page = open(files["html"]).read()
    assert "digikey.com" in page and "lcsc.com/search?q=C125052" in page and files["gerbers"].endswith(".zip")


def test_fab_check_against_a_house_preset():
    """fab.fab_check: a thin track, a small via near the edge and two vias too close are each reported, with where"""
    import fab
    import pcb
    b = pcb.Board("t", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.1)
    b.track("A", "F", [(5.0, 10.0), (20.0, 10.0)], width=0.1)
    b.via("A", (0.4, 10.0), drill=0.2, dia=0.4)
    b.via("A", (10.0, 5.0)), b.via("A", (10.5, 5.0))
    probs = fab.fab_check(b, "jlcpcb-2")
    txt = "\n".join(probs)
    assert "0.100 mm wide" in txt and "clearance 0.100" in txt and "drill 0.20 < 0.30" in txt
    assert "closer than 0.30 mm to the edge" in txt and "drills 0.10 mm apart" in txt
    assert not [p for p in fab.fab_check(b, dict(fab.RULES["pcbway-std"], track=0.1, space=0.1, drill=0.2, via_dia=0.4,
                                                 annular=0.1, hole_gap=0.0, edge=0.0)) if "wide" in p]


def test_pair_skew_and_paste_layer(tmp_path):
    """a differential pair whose tracks differ in length is reported; Gerbers carry a paste layer for SMD pads"""
    import os
    import pcb
    b = pcb.Board("t", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5.0, 10.0), 0, "1k")
    b.track("DP", "F", [(5.0, 5.0), (25.0, 5.0)], 0.2)
    b.track("DN", "F", [(5.0, 6.0), (20.0, 6.0)], 0.2)
    b.pair("DP", "DN", max_skew=0.5)
    assert abs(b.net_length("DP") - 20.0) < 1e-9
    probs = b.check()
    assert any("pair DP / DN" in p and "5.00 mm apart" in p for p in probs), probs
    files = [os.path.basename(f) for f in pcb.export_gerbers(b, str(tmp_path))]
    assert "t-F_Paste.gtp" in files and "t-B_Paste.gbp" not in files


def test_gerbers_read_back_by_an_independent_parser(tmp_path):
    """gerbonara (an independent Gerber / Excellon reader) recognises every layer we write and their extents
    match the board"""
    gerbonara = pytest.importorskip("gerbonara")
    import pcb
    b = pcb.Board("g", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (10.0, 10.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (20.0, 10.0), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    assert not pcb.route(b, "A", width=0.25)
    b.via("A", (15.0, 15.0))
    pcb.export_gerbers(b, str(tmp_path))
    st = gerbonara.LayerStack.open(str(tmp_path))
    names = str(st)
    for kind in ("top copper", "top mask", "top paste", "bottom copper", "mechanical outline"):
        assert kind in names, names
    out = gerbonara.GerberFile.open(str(tmp_path / "g-Edge_Cuts.gm1")).bounding_box()
    assert abs(out[1][0] - 30.0) < 0.2 and abs(out[1][1] - 20.0) < 0.2
