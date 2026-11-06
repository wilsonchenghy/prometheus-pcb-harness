"""The PCB harness's tests (pcb.py, schem.py): routing, checks, Gerbers, KiCad export, four layers, stitching,
schematic sheets. Some fetch KiCad footprints on first run (cached in ~/.cache/prometheus/parts)."""
import json
import math
import os
import sys
from pathlib import Path

import pytest

os.environ["CI"] = "1"   # (no viewer tabs from the test runs)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


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
    """a pad sitting in a piece of its net's pour that nothing joins to the rest is joined back to the net's main
    group: by route() itself now (it checks the net's islands after routing), or with seed_pins named"""
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
    b2 = pcb.Board("i", [(0, 0), (30, 0), (30, 20), (0, 20)], clearance=0.15)
    b2.comps, b2.nets, b2.pours = dict(b.comps), dict(b.nets), list(b.pours)
    assert pcb.route(b, "A", width=0.2, only=[("R2", "1")]) == []
    assert len(b.islands("A")) == 1                                # (the island found and joined)
    assert pcb.route(b2, "A", width=0.2, only=[("R2", "1")], seed_pins=[("R1", "2")]) == []
    assert len(b2.islands("A")) == 1


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
    pcb_path, pro_path = pcb.export_kicad(b, str(tmp_path), schematic=False)
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
    for kind in ("top copper", "top mask", "top paste", "top silk", "bottom copper", "mechanical outline"):
        assert kind in names, names
    out = gerbonara.GerberFile.open(str(tmp_path / "g-Edge_Cuts.gm1")).bounding_box()
    assert abs(out[1][0] - 30.0) < 0.2 and abs(out[1][1] - 20.0) < 0.2


def test_parts_take_their_footprints_3d_model_and_export_step(tmp_path):
    """a part placed without a model gets the 3D model its KiCad footprint names; export_step writes the board"""
    pytest.importorskip("build123d")
    import os
    import pcb
    b = pcb.Board("s", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (10.0, 5.0), 0, "1k")
    assert b.comps["R1"]["model"] == "Resistor_SMD.3dshapes/R_0603_1608Metric.step"
    f = pcb.export_step(b, str(tmp_path / "s.step"), models=False)
    assert os.path.getsize(f) > 1000


def test_autoroute_routes_a_board_in_one_call():
    """pcb.autoroute: every net of a small board routed (signals, then power, then the poured ground) with no
    per-net tuning; a net's own width is used"""
    import uuid
    import pcb
    out = [(0, 0), (30, 0), (30, 20), (0, 20)]
    b = pcb.Board(f"a{uuid.uuid4().hex[:8]}", out)                  # (a fresh name: no route checkpoint reused)
    for k, x in enumerate((6, 14, 22)):
        b.place(f"R{k + 1}", "Resistor_SMD:R_0603_1608Metric", (x, 10.0), 90, "1k")
    b.net("VCC", [("R1", "2"), ("R2", "2"), ("R3", "2")], amps=0.5, width=0.4)
    b.net("S", [("R1", "1"), ("R2", "1")])
    b.net("GND", [("R3", "1")])
    b.pour("GND", "B", out)
    assert pcb.autoroute(b) == [] and b.check() == []
    assert {t["w"] for t in b.tracks if t["net"] == "VCC"} == {0.4}


def test_kicad_board_reads_back_with_kiutils(tmp_path):
    """the exported .kicad_pcb parses with an independent KiCad file reader (kiutils): every part, every net,
    every segment and via, the zone"""
    kiutils_board = pytest.importorskip("kiutils.board")
    import pcb
    b = pcb.Board("k", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (8.0, 10.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (22.0, 10.0), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    b.net("GND", [("R1", "1"), ("R2", "2")])
    b.pour("GND", "B", [(0, 0), (30, 0), (30, 20), (0, 20)])
    assert not pcb.route(b, "A", width=0.25) and not pcb.route(b, "GND", width=0.3)
    path = pcb.export_kicad(b, str(tmp_path))[0]
    kb = kiutils_board.Board.from_file(path)
    assert len(kb.footprints) == 2 and len(kb.zones) == 1
    assert {n.name for n in kb.nets} >= {"A", "GND"}
    assert len(kb.traceItems) == sum(len(t["pts"]) - 1 for t in b.tracks) + len(b.vias)


def test_thermal_vias_meet_the_fab_drill_and_stale_routes_are_not_reused():
    """with a fab preset, an exposed pad's 0.2 mm thermal vias are drilled to the house minimum (a pin's hole is
    left alone), and a routing saved before the change does not come back with a via now too close to them"""
    import fab
    import pcb
    out = [(0, 0), (40, 0), (40, 40), (0, 40)]
    b = pcb.Board("t", out, rules="jlcpcb-2")
    b.place("U1", "RF_Module:ESP32-WROOM-32E", (20.0, 22.0), 0, "ESP32")
    assert all(p["drill"] in (None, 0.3) for p in b.comps["U1"]["pads"]["39"])
    assert not [p for p in fab.fab_check(b) if "drill" in p]
    old = {"placement": {"U1": ("RF_Module:ESP32-WROOM-32E", (20.0, 22.0), 0, "F")}, "tracks": [], "vias": []}
    assert "U1" in {r for r in pcb.placement(b) if pcb.placement(b)[r] != old["placement"].get(r)}
    plain = pcb.Board("t2", out)
    plain.place("U1", "RF_Module:ESP32-WROOM-32E", (20.0, 22.0), 0, "ESP32")
    assert any(p["drill"] == 0.2 for p in plain.comps["U1"]["pads"]["39"])
    assert pcb.placement(plain) == old["placement"]                # (no preset: footprint as KiCad has it)


def test_autoplace_then_route_a_parts_list():
    """parts given no position (at=None) are placed on the board, courtyards apart and clear of a fixed connector
    and a mounting hole, and the board then routes clean and passes the fab preset"""
    import uuid
    import fab
    import pcb
    out = [(0, 0), (35, 0), (35, 25), (0, 25)]
    b = pcb.Board(f"ap{uuid.uuid4().hex[:8]}", out, rules="jlcpcb-2")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (4.0, 11.0), 0, "5V in")
    b.hole((30.0, 20.0), 3.2)
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", None, 0, "NE555")
    for r in ("R1", "R2", "R3"):
        b.place(r, "Resistor_SMD:R_0603_1608Metric", None, 0, "1k")
    for r in ("C1", "C2"):
        b.place(r, "Capacitor_SMD:C_0603_1608Metric", None, 0, "100n")
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "red")
    assert any("no position" in p for p in b.check_placement())
    b.net("VCC", [("J1", "1"), ("U1", "8"), ("U1", "4"), ("R1", "1"), ("C2", "1")])
    b.net("GND", [("J1", "2"), ("U1", "1"), ("C1", "2"), ("C2", "2"), ("D1", "1")])
    b.net("DIS", [("U1", "7"), ("R1", "2"), ("R2", "1")])
    b.net("THR", [("U1", "6"), ("U1", "2"), ("R2", "2"), ("C1", "1")])
    b.net("OUT", [("U1", "3"), ("R3", "1")])
    b.net("LED", [("R3", "2"), ("D1", "2")])
    b.pour("GND", "B", out)
    assert pcb.place_and_route(b, router="harness") == []
    assert b.comps["J1"]["at"] == (4.0, 11.0) and b.check_placement() == []
    from shapely.geometry import Point
    assert all(not b.courtyard(r).intersects(Point(30, 20).buffer(1.6)) for r in b.comps)
    assert b.check() == [] and fab.fab_check(b) == []


def test_kicad_netlist_to_a_clean_board():
    """a KiCad netlist (.net) becomes a board: footprints, values, MPN and LCSC fields, nets of two or more pins
    (hierarchical '/' stripped, unconnected pins dropped), a part with no footprint noted and left out, GND poured;
    then placed and routed clean"""
    import uuid
    import fab
    import pcb
    net = Path(__file__).parent.parent / "examples" / "netlist" / "blinker_sch.net"
    b = pcb.from_netlist(str(net), (35, 25), at={"J1": (4, 12.5)}, rules="jlcpcb-2", name=f"nl{uuid.uuid4().hex[:8]}")
    assert set(b.comps) == {"J1", "U1", "R1", "R2", "R3", "C1", "C2", "D1"}
    assert any("TP1" in n for n in b.import_notes)
    assert set(b.nets) == {"+5V", "GND", "DIS", "THR", "Net-(U1-Q)", "Net-(D1-A)"}
    assert b.comps["U1"]["mpn"] == "TI NE555DR" and b.comps["R1"]["lcsc"] == "C21190"
    assert [p["net"] for p in b.pours] == ["GND"]
    assert pcb.place_and_route(b, router="harness") == [] and b.check() == [] and fab.fab_check(b) == []


def test_clearance_between_pours_tracks_and_pads():
    """the clearance check finds a track too near another net's pad, two pours of different nets overlapping, and
    two pours too close (the pour outlines are measured as segments, so each case is worth a test)"""
    import pcb
    out = [(0, 0), (30, 0), (30, 20), (0, 20)]
    b = pcb.Board("c1", out)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (10, 10), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (20, 10), 0, "1k")
    b.net("A", [("R1", "1"), ("R2", "1")])
    b.net("B", [("R1", "2"), ("R2", "2")])
    x, y = b.pad("R1", "2")
    b.track("A", "F", [(x - 1.5, y + 0.75), (x + 3, y + 0.75)], 0.2)
    assert any(p.startswith("F.Cu: A and B 0.18 mm apart") for p in b.check())
    b = pcb.Board("c2", out)
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (10, 10), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (20, 10), 0, "1k")
    b.net("A", [("R1", "1")])
    b.net("B", [("R2", "2")])
    b.pour("A", "F", [(0, 0), (16, 0), (16, 20), (0, 20)])
    b.pour("B", "F", [(15, 0), (30, 0), (30, 20), (15, 20)])
    assert any(p.startswith("F.Cu: A and B short") for p in b.check())
    b.pours[1]["poly"] = [(16.1, 0), (30, 0), (30, 20), (16.1, 20)]
    assert any(p.startswith("F.Cu: A and B 0.10 mm apart") for p in b.check())


def test_cart_merges_one_part_used_under_two_values():
    """two buttons of one part number labelled EN and BOOT are one line to buy, with both refs and both values"""
    import fab
    import pcb
    b = pcb.Board("cart2", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("SW1", "Button_Switch_SMD:SW_SPST_TL3342", (8, 10), 0, "EN", "E-Switch TL3342F160QG")
    b.place("SW2", "Button_Switch_SMD:SW_SPST_TL3342", (20, 10), 0, "BOOT", "E-Switch TL3342F160QG")
    ls = fab.lines(b, boards=5)
    assert len(ls) == 1 and ls[0]["refs"] == ["SW1", "SW2"] and ls[0]["need"] == 10 and ls[0]["value"] == "EN / BOOT"


def test_microstrip_impedance_and_width():
    """IPC-2141: a wider track is a lower impedance, a pair below its single-ended double, and width_for_z0 inverts"""
    import pcb
    assert pcb.microstrip_z0(0.2, 0.2) > pcb.microstrip_z0(0.4, 0.2)
    assert 45 < pcb.microstrip_z0(0.33, 0.2) < 55
    w = pcb.width_for_z0(90, 0.2, gap=0.15)
    assert abs(pcb.microstrip_z0(w, 0.2, gap=0.15) - 90) < 0.5
    assert pcb.microstrip_z0(w, 0.2, gap=0.15) < 2 * pcb.microstrip_z0(w, 0.2)


def test_cli_findings_are_data_for_an_agent():
    """each finding comes back with its kind, the pins and place it names, and a hint"""
    import pcb_cli
    f = pcb_cli.finding("routing", "route GND: could not reach RB2V.2 (blocked; move parts or widen a channel)")
    assert f["kind"] == "unreached" and f["pins"] == ["RB2V.2"] and f["hint"]
    f = pcb_cli.finding("design", "F.Cu: A and B 0.18 mm apart (clearance 0.2) at (10.6, 10.6)")
    assert f["kind"] == "clearance" and f["at"] == [10.6, 10.6]
    assert pcb_cli.finding("design", "net GP0 is split in 2: J2.1 | U1.2")["pins"] == ["J2.1", "U1.2"]


def test_freerouting_through_kicad(tmp_path, monkeypatch):
    """FreeRouting gets KiCad's design without the poured nets (their pads and wires stay as obstacles); a missing
    KiCad or FreeRouting stops the routing with what to install, no other router taken quietly; with them, a small
    board routes clean through KiCad"""
    import freerouting
    import pcb
    dsn = ('(pcb x (network (net A (pins R1-2 R2-1)) (net GND (pins R1-1 R2-2)) (class W A GND (rule (width 300))))'
           ' (wiring (wire (path F.Cu 200 0 0 1 1) (net GND) (type route))))')
    got = freerouting._drop_nets(dsn, {"GND"})
    assert "(net GND (pins" not in got and "(net A (pins R1-2 R2-1))" in got and "(class W A (rule" in got
    assert "(net GND) (type route)" in got                              # (the ground wire stays, an obstacle)
    out = [(0, 0), (30, 0), (30, 20), (0, 20)]
    b = pcb.Board("frt", out)
    for r, x in (("R1", 8.0), ("R2", 22.0)):
        b.place(r, "Resistor_SMD:R_0603_1608Metric", (x, 10.0), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    b.net("GND", [("R1", "1"), ("R2", "2")])
    b.pour("GND", "B", out)
    real = freerouting.kicad_available()
    monkeypatch.setattr(freerouting, "kicad_available", lambda: False)
    with pytest.raises(RuntimeError, match="KiCad"):
        pcb.route_board(b)
    monkeypatch.setattr(freerouting, "kicad_available", lambda: real)
    if real and freerouting.available():
        assert pcb.route_board(b) == [] and b.check() == [] and b.router_used == "freerouting (through KiCad)"


def test_footprint_bare_holes_are_obstacles_and_drilled():
    """a USB-C's locating pegs (non-plated holes inside its footprint) are found, the check flags copper over them,
    and the drill file drills them (KiCad's DRC caught a route across them before the harness knew of them)"""
    import pcb
    b = pcb.Board("peg", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (4.0, 10.0), 270,
            "USB-C", overhang=True)
    holes = b.npth()
    assert len(holes) == 2 and all(abs(d - 0.65) < 1e-9 for _, _, d in holes)
    x, y, _ = holes[0]
    b.net("VBUS", [("J1", "A4"), ("J1", "A9")])
    b.track("VBUS", "B", [(x - 2, y), (x + 2, y)], 0.3)
    assert any("bare hole" in p for p in b.check())
    import tempfile
    out = tempfile.mkdtemp()
    pcb.export_gerbers(b, out)
    import glob
    npth = open([f for f in glob.glob(out + "/*") if "NPTH" in f][0]).read()
    assert "C0.650" in npth


def test_doctor_reports_each_optional_piece():
    """pcb-harness doctor names KiCad, its Python, Java and FreeRouting, each with what it gives and the fallback"""
    import pcb_cli
    rows = pcb_cli.doctor()
    assert {"kicad", "kicad_python", "java", "freerouting"} <= set(rows)
    assert all(r["gives"] and r["without"] for r in rows.values())


def test_critic_flags_requirements_and_review_points():
    """the critic finds a broken requirement (size, layers, current, a part too far) and the usual review points
    (decoupling far from a supply pin, an undeclared USB pair, no ground pour) in a moment"""
    import time
    import critic
    import pcb
    b = pcb.Board("crit", [(0, 0), (40, 0), (40, 30), (0, 30)])
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (10, 15), 0, "MCU")
    b.place("C1", "Capacitor_SMD:C_0603_1608Metric", (35, 25), 0, "100n")
    b.net("3V3", [("U1", "8"), ("C1", "1")], amps=0.2)
    b.net("GND", [("U1", "4"), ("C1", "2")])
    b.net("USB_DP", [("U1", "2")])
    b.net("USB_DM", [("U1", "3")])
    t = time.perf_counter()
    notes = critic.review(b, {"size_mm": [30, 30], "layers": 4, "nets": {"3V3": {"amps": 1.0}}, "near": [["C1", "U1", 3]]})
    assert time.perf_counter() - t < 1.0
    kinds = {(n["severity"], n["kind"]) for n in notes}
    assert {("error", "size"), ("error", "layers"), ("error", "current"), ("error", "near")} <= kinds
    assert {("warning", "decoupling"), ("warning", "pair"), ("warning", "ground")} <= kinds
    assert notes[0]["severity"] == "error"


def test_cli_stages(tmp_path):
    """--stage critic and --stage place run without routing or writing order files; all writes them"""
    import json
    import pcb_cli
    here = Path(__file__).parent.parent
    rc = pcb_cli.main([str(here / "bench" / "blinker.py"), str(tmp_path / "c"), "--stage", "critic", "--kicad-drc", "off"])
    r = json.load(open(tmp_path / "c" / "result.json"))
    assert r["stage"] == "critic" and r["files"] == {} and "critic" in r and rc in (0, 1)
    pcb_cli.main([str(here / "bench" / "blinker.py"), str(tmp_path / "a"), "--kicad-drc", "off", "--router", "harness"])
    r = json.load(open(tmp_path / "a" / "result.json"))
    assert r["stage"] == "all" and "gerbers" in r["files"] and r["parts"] == 8
    assert not [c for c in r["critic"] if c["kind"] == "size"]   # (the blinker: 24 x 15 mm, no more than it needs)


def test_critic_fails_a_board_far_larger_than_its_parts():
    """no size_mm in the requirements: a board its parts barely cover is an error (the agent shrinks it), with the size
    that would do; with size_mm given the enclosure decides and the check is the maximum only"""
    import critic
    from shapely.geometry import Polygon
    sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
    import blinker
    b = blinker.build()
    b.outline = Polygon([(0, 0), (60, 0), (60, 40), (0, 40)])
    size = [n for n in critic.review(b) if n["kind"] == "size"]
    assert size and size[0]["severity"] == "error" and "would do" in size[0]["text"]
    assert not [n for n in critic.review(b, {"size_mm": [60, 40]}) if n["kind"] == "size"]


def test_tune_pairs_lengthens_the_shorter_net():
    """a pair over its skew gets a serpentine on the shorter net's straight stretch, within the skew after, and the
    check finds nothing new"""
    import pcb
    b = pcb.Board("tp", [(0, 0), (40, 0), (40, 20), (0, 20)])
    b.track("DP", "F", [(5.0, 5.0), (30.0, 5.0)], 0.2)
    b.track("DN", "F", [(5.0, 12.0), (25.0, 12.0)], 0.2)
    b.pair("DP", "DN", max_skew=0.5)
    before = set(b.check())
    assert pcb.tune_pairs(b) == []
    assert abs(b.net_length("DP") - b.net_length("DN")) <= 0.5
    assert len(b.tracks[1]["pts"]) > 2 and not (set(b.check()) - before)


def test_placer_holds_required_parts_near_and_is_repeatable():
    """board.near (a requirement's "within d mm") is met by autoplace, and the same seed gives the same placement"""
    import pcb

    def make():
        out = [(0, 0), (40, 0), (40, 30), (0, 30)]
        b = pcb.Board("nr", out, rules="jlcpcb-2")
        b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", None, 0, "IC")
        for r in ("R1", "R2", "R3", "R4"):
            b.place(r, "Resistor_SMD:R_0603_1608Metric", None, 0, "1k")
        b.place("C1", "Capacitor_SMD:C_0603_1608Metric", None, 0, "100n")
        b.net("VCC", [("U1", "8"), ("C1", "1"), ("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1")])
        b.net("GND", [("U1", "4"), ("C1", "2")])
        for k in range(4):
            b.net(f"S{k}", [("U1", str(k + 1 if k < 3 else 5)), (f"R{k + 1}", "2")])
        b.near = [("C1", "U1", 2.0)]
        return b
    a, c = make(), make()
    pcb.autoplace(a, seed=3)
    pcb.autoplace(c, seed=3)
    assert pcb.near_misses(a) == []
    assert {r: v["at"] for r, v in a.comps.items()} == {r: v["at"] for r, v in c.comps.items()}


def test_stage_check_starts_from_the_saved_board(tmp_path, capsys):
    """a route run saves the board; --stage check then reuses its placement and copper without routing"""
    import json
    import pcb_cli
    here = Path(__file__).parent.parent
    out = str(tmp_path / "o")
    pcb_cli.main([str(here / "bench" / "blinker.py"), out, "--stage", "route", "--router", "harness", "--kicad-drc", "off"])
    assert (tmp_path / "o" / "board_state.pkl").exists()
    capsys.readouterr()
    pcb_cli.main([str(here / "bench" / "blinker.py"), out, "--stage", "check", "--kicad-drc", "off"])
    said = capsys.readouterr().out
    r = json.load(open(tmp_path / "o" / "result.json"))
    assert "from the last run" in said and r["clean"] and "route" not in r["timings"]


def test_viewer_serves_a_run_and_its_progress(tmp_path):
    """a run writes progress.json (stages, tries, an estimate of what is left) and result.json naming its script;
    the viewer serves the page, three.js, the board (parts, pads, copper), the result and the progress"""
    import json
    import urllib.request
    import pcb_cli
    import pcb_view
    here = Path(__file__).parent.parent
    out = tmp_path / "o"
    pcb_cli.main([str(here / "bench" / "blinker.py"), str(out), "--router", "harness", "--kicad-drc", "off"])
    pr = json.load(open(out / "progress.json"))
    assert pr["done"] and pr["eta"] == 0.0 and [s["name"] for s in pr["stages"]][-1] == "done"
    assert json.load(open(out / "result.json"))["script"].endswith("blinker.py")
    srv, url = pcb_view.serve(str(out), open_browser=False, block=False)
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        get = lambda p: op.open(url + p, timeout=120).read()
        assert b"Prometheus" in get("")
        assert len(get("three.min.js")) > 100000
        b = json.loads(get("api/board"))
        assert b["name"] == "blinker" and len(b["comps"]) == 8 and b["tracks"] and b["copper"]
        assert all(c["pads"] for c in b["comps"]) and b["outline"]
        assert json.loads(get("api/result"))["board"] == "blinker"
        assert json.loads(get("api/progress"))["done"]
    finally:
        srv.shutdown()


def test_progress_estimate_counts_the_tries_left(tmp_path):
    import pcb_view
    p = pcb_view.Progress(str(tmp_path), "board.py", parts=20)
    p.stage("place+route")
    p.event({"kind": "tries", "tries": 6, "workers": 4})
    p.event({"kind": "try", "k": 0, "seconds": 10.0, "clean": False, "line": "try 1"})
    export = 5.0 + 0.15 * 20                                 # (the export: 5 s and 0.15 s a part, as measured)
    assert abs(p.eta() - (2 * 10.0 + export)) < 1e-6       # (5 tries left, 4 at a time: two batches, then export)
    p.event({"kind": "try", "k": 1, "seconds": 10.0, "clean": True, "line": "try 2"})
    assert p.d["tries"] == 2 and abs(p.eta() - export) < 1e-6
    q = pcb_view.Progress(str(tmp_path / "q"), "board.py", parts=20)   # a first try past its guess: unknown, not a
    q.stage("place+route")                                              # number that stops counting
    q.d["stages"][-1]["start"] -= 3600
    assert q.eta() is None


def test_parts_symbol_pin_names_pin_check_and_erc():
    """a part placed with its KiCad symbol: nets name its pins by name (all of a name's pads), the check matches
    pins to pads both ways, and the ERC finds an open input, an unconnected power pin and two outputs tied"""
    import parts
    import pcb
    s = parts.symbol("Driver_Motor:DRV8833PWP")
    assert {p["key"]: p["num"] for p in s["pins"]}["AIN2"] == "15" and s["datasheet"].endswith(".pdf")
    b = pcb.Board("ps", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("U1", None, (15, 10), 0, symbol="Driver_Motor:DRV8833PWP")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (4, 10), 0)
    b.net("GND", [("U1", "GND"), ("J1", "1")])
    b.net("VM", [("U1", "VM"), ("J1", "2")])
    b.net("AIN2", [("U1", "AIN2"), ("J1", "3")])
    b.net("OUT", [("U1", "AOUT1"), ("U1", "BOUT1")])
    assert sorted(b.nets["GND"]["pins"]) == [("J1", "1"), ("U1", "13"), ("U1", "17")]
    assert parts.pin_check(b) == []
    text = [n["text"] for n in parts.erc(b) if n["severity"] == "error"]
    assert any("U1.16 (AIN1, input) is left open" in t for t in text), text
    assert any("U1.14 (VINT, power in) is not connected" in t for t in text), text
    assert any("net OUT: outputs" in t for t in text), text
    w = pcb.Board("pw", [(0, 0), (30, 0), (30, 20), (0, 20)])            # (the wrong footprint for the symbol)
    w.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (15, 10), 0, symbol="Driver_Motor:DRV8833PWP")
    assert any("symbol pin 16 (AIN1) has no pad" in p for p in w.check_placement())


def test_datasheet_pin_table_is_held_against_the_symbol():
    """the datasheet's pin table (its text, as pypdf reads it) confirms each symbol pin by name and number, and a
    pin the table numbers otherwise is reported with the table's numbers"""
    import parts
    page = ("5 Pin Configuration and Functions Pin Functions PIN NAME WQFN HTSSOP\\n"
            "Both the GND pin and device PowerPADGND 13 —PPAD has PowerPAD.\\nVINT 12 14 — Internal supply\\n"
            "VM 10 12 — Device power supply\\nX7RVCP 9 11 IO High-side gate drive\\nAIN1 14 16 I Bridge A input 1\\n"
            "AIN2 13 15 I Bridge A input 2\\nBIN1 7 9 I\\nBIN2 8 10 I\\nnSLEEP 15 1 I Sleep mode input\\n"
            "nFAULT 16 8 OD Fault output\\nAISEN 2 3 IO\\nBISEN 5 6 IO\\nAOUT1 1 2 O\\nAOUT2 3 4 O\\nBOUT1 6 7 O\\nBOUT2 4 5 O\\n")
    r = parts.datasheet_check("Driver_Motor:DRV8833PWP", pages=["contents", page.replace("\\\\n", "\\n")])
    assert r["agree"] == r["total"] == 17 and r["page"] == 2, [p for p in r["pins"] if p["status"] != "agrees"]
    ain2 = next(p for p in r["pins"] if p["name"] == "AIN2")       # (where each pin was read, and its words there)
    assert ain2["page"] == 2 and ain2["quote"] == "AIN2 13 15", ain2
    bad = parts.datasheet_check("Driver_Motor:DRV8833PWP", pages=[page.replace("AIN1 14 16", "AIN1 14 99")])
    assert [(p["name"], p["status"]) for p in bad["pins"] if p["status"] != "agrees"] == [("AIN1", "differs")]


def test_datasheet_numbered_list_and_other_names():
    """a datasheet that lists its pins number first ("2- VOUT") under another name than the symbol's (VO), and one
    that spells a pin in other case (RS for Rs), still confirms the symbol; a swapped pair is still caught"""
    import parts
    ams = "PIN CONNECTIONS\n3 PIN FIXED/ADJUSTABLE VERSION\n1- Ground/Adjust\n2- VOUT\n3- VIN\nSOT-223 Top View"
    r = parts.datasheet_check("Regulator_Linear:AMS1117-3.3", pages=["front page", ams])
    assert r["agree"] == r["total"] == 3 and r["page"] == 2, r["pins"]
    assert {p["name"]: p["quote"] for p in r["pins"]}["VO"] == "2- VOUT"
    bad = parts.datasheet_check("Regulator_Linear:AMS1117-3.3", pages=[ams.replace("2- VOUT\n3- VIN", "2- VIN\n3- VOUT")])
    assert sorted(p["name"] for p in bad["pins"] if p["status"] == "differs") == ["VI", "VO"], bad["pins"]
    can = ("Pin Functions\n1D 8  RS \n2GND 7  CANH\n3VCC 6  CANL\n4R 5  Vref\nD 1 I Driver input\nGND 2 GND\n"
           "VCC 3 Supply\nR 4 O Receiver output\nVref 5 O\nCANL 6 I/O\nCANH 7 I/O\nRS\n8\nI Mode select")
    r = parts.datasheet_check("Interface_CAN_LIN:SN65HVD230", pages=[can])
    assert r["agree"] == r["total"] == 8, [p for p in r["pins"] if p["status"] != "agrees"]
    # no pin table (a pinout drawing only): nothing is called wrong
    r = parts.datasheet_check("Interface_CAN_LIN:SN65HVD230", pages=["Features\nD 1 GND 2 R 4\nTypical application"])
    assert r["page"] is None and {p["status"] for p in r["pins"]} == {"not checked"}


def test_a_cited_choice_is_held_against_its_datasheet_page():
    """place(cite=), net(cite=) and cite() collect the datasheet's page and words for a choice; each is found (or
    not) on that page of the datasheet's text, its link the part's symbol's unless given, and a quote not on its page
    is warned of"""
    import parts
    import pcb
    import pytest
    b = pcb.Board("cite", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.place("C1", "Capacitor_SMD:C_0603_1608Metric", (5, 5), 0, "100n",
            cite={"what": "decoupling", "page": 2, "quote": "Place a 0.1 µF  capacitor"})
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (9, 5), 0, "10k")
    b.net("SDA", [("R1", "1"), ("C1", "1")], cite={"page": 3, "quote": "pull-up of 10 kΩ"})
    b.cite("R1", "pull-up", "10k", 3, "4.7 kΩ", url="https://x/ds.pdf")          # (page 1 says it, not 3)
    with pytest.raises(ValueError):
        b.cite("R1", "pull-up", "10k", 0, "")
    b.comps["C1"]["symbol"] = {"datasheet": "https://x/ds.pdf"}
    pages = ["Use 4.7kΩ pull-ups", "Placea0.1µF capacitor\nnear each pin", "SDA: pull-up of 10 kΩ"]
    ev = parts.evidence(b, read=lambda u: pages)
    assert [(e["ref"], e["what"], e["found"]) for e in ev] == [("C1", "decoupling", True), ("R1", "net SDA", None),
                                                                ("R1", "pull-up", False)]
    assert ev[0]["url"] == "https://x/ds.pdf" and ev[1]["url"] is None and ev[0]["quote"] == "Place a 0.1 µF capacitor"


def test_datasheet_rows_with_a_number_per_package_and_a_slash_name():
    """a pin table with one number column per package, read as printed rows ("2 2 6, 7 GND"), confirms the symbol;
    a pinout drawing's row ("4 4 VCP 13 13") gives no number; an active-low name printed with a slash ("7 /HOLD")"""
    import parts
    ap = ("Pin Descriptions\nPin Number Pin Name Function\nSOT25 SOT89-5 SO-8\n1 4 8 VIN Input Voltage\n"
          "2 2 6, 7 GND GND\n3 5 EN Chip Enable\n— 2, 3, 4 NC No Connection\n5 5 1 VOUT Output Voltage")
    r = parts.datasheet_check("Regulator_Linear:AP2112K-3.3", pages=[ap])
    assert r["agree"] == r["total"] == 5, r["pins"]
    assert {p["name"]: p["quote"] for p in r["pins"]}["GND"] == "2 2 6, 7 GND"
    assert "4" not in parts._numbers({"VCP"}, "4 4 VCP 13 13", 0)          # (the left side's pins, not VCP's)
    assert parts._numbers({"HOLD"}, "7 /HOLD or /RESET (IO3) I/O Hold", 0) == ["7"]


def test_drop_loops_takes_away_a_second_run_of_a_net():
    """two parallel tracks joining the same two pads: one goes, the pads stay joined; a net's only track stays"""
    import pcb
    b = pcb.Board("loops", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 10), 0, "10k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (15, 10), 0, "10k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    b.net("B", [("R1", "1"), ("R2", "2")])
    a, c = (5.825, 10.0), (14.175, 10.0)
    b.tracks += [{"net": "A", "layer": "F", "pts": [a, c], "w": 0.2, "amps": 0.1},
                 {"net": "A", "layer": "F", "pts": [a, (6.5, 11.0), (13.5, 11.0), c], "w": 0.2, "amps": 0.1},
                 {"net": "B", "layer": "F", "pts": [(4.175, 10.0), (4.175, 14.0), (15.825, 14.0), (15.825, 10.0)],
                  "w": 0.2, "amps": 0.1}]
    assert b.drop_loops() == 1
    assert [t["net"] for t in b.tracks] == ["A", "B"] and len(b.islands("A")) == 1 and b.check() == []


def test_live_edit_moves_a_part_reroutes_its_nets_and_undoes(tmp_path):
    """the viewer's /api/move: the part moves, only its nets are routed again (the rest keep their copper), the
    run's state and moves.json record it; a part off the board or a flip is refused; /api/undo puts it back"""
    import pcb
    import pcb_cli
    import pcb_view
    script = tmp_path / "b.py"
    script.write_text("import pcb\nb = pcb.Board('live', [(0, 0), (30, 0), (30, 20), (0, 20)])\n"
                      "b.place('R1', 'Resistor_SMD:R_0603_1608Metric', (8, 10), 0, '1k')\n"
                      "b.place('R2', 'Resistor_SMD:R_0603_1608Metric', (22, 10), 0, '1k')\n"
                      "b.place('R3', 'Resistor_SMD:R_0603_1608Metric', (15, 4), 0, '1k')\n"
                      "b.net('A', [('R1', '2'), ('R2', '1')])\nb.net('B', [('R2', '2'), ('R3', '2')])\nPCB = b\n")
    b, _ = pcb_cli.load(str(script))
    pcb.route_jobs(b, [(n, b.net_width(n), None) for n in b.nets])
    assert b.check() == []
    out = tmp_path / "out"
    out.mkdir()
    pcb_cli.save_state(b, str(out), pcb_cli.design_key(b))
    (out / "result.json").write_text(json.dumps({"script": str(script)}))
    cache = pcb_view._Cache(str(out))
    a_before = sorted(tuple(map(tuple, t["pts"])) for t in b.tracks if t["net"] == "A")
    got = cache.move("R3", (16.0, 5.0))
    assert got["ok"] and got["rerouted"] == ["B"] and got["check"] == [] and got["unrouted"] == []
    assert sorted(tuple(map(tuple, t["pts"])) for t in cache.live.tracks if t["net"] == "A") == a_before
    assert json.loads((out / "moves.json").read_text())[0]["at"] == [16.0, 5.0]
    c, _ = pcb_cli.load(str(script))
    assert pcb_cli.load_state(c, str(out), pcb_cli.design_key(c)) and c.comps["R3"]["at"] == (16.0, 5.0)
    assert not cache.move("R3", (-5, 5))["ok"] and not cache.move("R3", (16, 5), side="B")["ok"]
    assert not cache.move("R9", (5, 5))["ok"]
    back = cache.undo_move()
    assert back["ok"] and cache.live.comps["R3"]["at"] == (15, 4) and json.loads((out / "moves.json").read_text()) == []
    # the script saved with R3 elsewhere: shown with only B routed again, A's copper kept; an outline change refused
    cache.live = None
    cache._board()
    a_kept = sorted(tuple(map(tuple, t["pts"])) for t in cache.live.tracks if t["net"] == "A")
    script.write_text(script.read_text().replace("(15, 4)", "(14, 6)"))
    got = cache.refresh()
    assert got["ok"] and got["rerouted"] == ["B"] and got["check"] == [] and cache.rev == 1
    assert cache.live.comps["R3"]["at"] == (14, 6)
    assert sorted(tuple(map(tuple, t["pts"])) for t in cache.live.tracks if t["net"] == "A") == a_kept
    c2, _ = pcb_cli.load(str(script))                  # (the live edit is keyed as the next CLI run keys the script)
    assert cache.live_key == pcb_cli.design_key(c2)
    script.write_text(script.read_text().replace("(30, 0), (30, 20)", "(32, 0), (32, 20)"))
    assert not cache.refresh()["ok"] and cache.rev == 1
    cache.undo.append((None, None, None, [], []))                     # (a "route properly" undone: its copper only)
    assert cache.undo_move()["ok"] and cache.live.tracks == [] and cache.live.comps["R3"]["at"] == (14, 6)


def test_nudge_vias_parts_two_nets_drills():
    """two nets' vias 0.45 mm apart (drill wall to wall) under a 0.5 mm rule: the one with the fewer tracks moves away
    by the shortfall, its track's end with it, and nothing else changes"""
    import pcb
    b = pcb.Board("nudge", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.via("A", (10.0, 10.0), 0.3, 0.6)
    b.via("B", (10.75, 10.0), 0.3, 0.6)
    b.tracks.append({"net": "B", "layer": "F", "pts": [(10.75, 10.0), (14.0, 10.0)], "w": 0.2, "amps": 0.1})
    b.tracks.append({"net": "A", "layer": "F", "pts": [(10.0, 10.0), (10.0, 6.0)], "w": 0.2, "amps": 0.1})
    b.tracks.append({"net": "A", "layer": "B", "pts": [(10.0, 10.0), (6.0, 10.0)], "w": 0.2, "amps": 0.1})
    assert pcb.nudge_vias(b, gap=0.5) == []
    a, v = b.vias
    assert a["at"] == (10.0, 10.0)                                   # (two tracks on it: it stays)
    gapnow = math.hypot(v["at"][0] - a["at"][0], v["at"][1] - a["at"][1]) - 0.3
    assert 0.5 <= gapnow < 0.53 and b.tracks[0]["pts"][0] == v["at"]


def test_neck_vias_moves_a_via_off_its_own_pad_and_joins_it_by_a_track():
    """a GND via whose ring touches its own 0603 pad (solder would wick down it) moves a neck away, a track from the
    pad to it; a via-in-pad (the ring all on the pad) stays"""
    import pcb
    from shapely.geometry import Point
    b = pcb.Board("neck", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.place("C1", "Capacitor_SMD:C_0603_1608Metric", (10, 10), 0, "100n")
    b.place("C2", "Capacitor_SMD:C_1206_3216Metric", (10, 4), 0, "10u")
    b.net("GND", [("C1", "2"), ("C2", "2")])
    b.net("V", [("C1", "1"), ("C2", "1")])
    pad = next(g for r, n, L, g, d in b.pad_shapes() if (r, n) == ("C1", "2"))
    big = next(g for r, n, L, g, d in b.pad_shapes() if (r, n) == ("C2", "2"))
    b.via("GND", (pad.bounds[2] + 0.2, 10.0), 0.3, 0.6)            # (ring over the pad's edge)
    b.via("GND", big.centroid.coords[0], 0.2, 0.4)                  # (a via-in-pad on the 1206's pad)
    before = len(b.islands("GND"))
    assert pcb.neck_vias(b) == []
    v, vip = b.vias
    assert Point(v["at"]).buffer(0.3).distance(pad) >= 0.2 - 1e-6 and vip["at"] == big.centroid.coords[0]
    assert b.tracks[-1]["net"] == "GND" and b.tracks[-1]["pts"][-1] == v["at"] and len(b.islands("GND")) == before


def test_nudge_vias_parts_a_via_from_an_oval_slot_by_its_shape():
    """a via 0.14 mm from a USB-C shield's oval slot (its centre much further off) under a 0.5 mm rule moves away
    until the slot's own wall is 0.5 mm off"""
    import pcb
    try:
        pcb.footprint("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("slot", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (15, 6), 0, "USB")
    b.net("GND", [("J1", "A1")])
    slot = next(h for h in b.drill_list() if h["slot"])
    from shapely.geometry import Point
    g = slot["geom"]
    x = g.bounds[0] - 0.14 - 0.15                                    # (a 0.3 mm drill, its wall 0.14 mm off the slot's)
    b.via("GND", (round(x, 3), round(g.centroid.y, 3)), 0.3, 0.6)
    assert Point(b.vias[0]["at"]).buffer(0.15).distance(g) < 0.2
    pcb.nudge_vias(b, gap=0.5, reach=0.6)
    assert Point(b.vias[0]["at"]).buffer(0.15, 16).distance(g) >= 0.5 - 1e-3


def test_tidy_takes_away_a_via_that_joins_nothing():
    """a via with a track on one layer only joins nothing and goes; a via joining two layers' tracks stays; tracks
    meeting in a dead via still meet"""
    import pcb
    b = pcb.Board("tidy", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.via("A", (5.0, 5.0), 0.3, 0.6)                                 # dead: F only, two tracks meeting in it
    b.tracks.append({"net": "A", "layer": "F", "pts": [(2.0, 5.0), (5.0, 5.0)], "w": 0.2, "amps": 0.1})
    b.tracks.append({"net": "A", "layer": "F", "pts": [(5.1, 5.0), (8.0, 5.0)], "w": 0.2, "amps": 0.1})
    b.via("B", (12.0, 12.0), 0.3, 0.6)                               # live: F and B
    b.tracks.append({"net": "B", "layer": "F", "pts": [(9.0, 12.0), (12.0, 12.0)], "w": 0.2, "amps": 0.1})
    b.tracks.append({"net": "B", "layer": "B", "pts": [(12.0, 12.0), (15.0, 12.0)], "w": 0.2, "amps": 0.1})
    assert b.tidy() == 1
    assert [v["net"] for v in b.vias] == ["B"]
    assert any(t["pts"] == [(5.1, 5.0), (5.0, 5.0)] for t in b.tracks)   # (the end brought to where it met)


@pytest.mark.parametrize("script", ["bench/blinker.py", "bench/usb_ldo.py", "bench/can_node.py",
                                    "bench/power_bank.py", "bench/rp2040_board.py"])
def test_kicad_schematic_is_the_board_and_nothing_overlaps(tmp_path, script):
    """the KiCad schematic: every part on a symbol (its own, a generic one, or a box made from its pads), nothing on
    the sheet overlapping, and - read back by KiCad - its netlist the board's"""
    import kicad_sch
    import pcb
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / script))
    lay = kicad_sch.layout(b)
    assert kicad_sch.overlaps(lay) == []
    placed = {p.ref for _, _, ps in lay[0] for p in ps}
    pn = b.pad_net()
    assert placed == {r for (r, _) in pn}                # (every part on a net is on the sheet)
    path = kicad_sch.write(b, str(tmp_path))
    if not pcb.kicad_version():
        pytest.skip("KiCad is not installed: the netlist read-back needs kicad-cli")
    assert kicad_sch.check(b, path) == []


KICAD_TWINS = [  # (KiCad's footprint, the same package's drawing dimensions: JEDEC / the maker's datasheet)
    ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", {"family": "soic", "pins": 8, "pitch": 1.27, "body": [[4.8, 5.0], [3.8, 4.0]],
     "span": [5.8, 6.2], "lead_length": [0.4, 1.27], "lead_width": [0.31, 0.51]}),
    ("Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm", {"family": "soic", "pins": 20, "pitch": 0.65, "body": [[6.4, 6.6], [4.3, 4.5]],
     "span": [6.2, 6.6], "lead_length": [0.45, 0.75], "lead_width": [0.19, 0.30]}),
    ("Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", {"family": "qfn", "pins": 16, "pitch": 0.5, "body": [3, 3],
     "lead_length": [0.3, 0.5], "lead_width": [0.18, 0.30], "ep": [1.7, 1.7]}),
    ("Package_TO_SOT_SMD:SOT-23", {"family": "soic", "pins": 3, "pitch": 0.95, "body": [[2.8, 3.04], [1.2, 1.4]],
     "span": [2.1, 2.64], "lead_length": [0.3, 0.6], "lead_width": [0.3, 0.5],
     "rows": {"left": ["1", None, "2"], "right": [None, "3", None]}}),
    ("Package_QFP:LQFP-48_7x7mm_P0.5mm", {"family": "qfp", "pins": 48, "pitch": 0.5, "body": [7, 7], "span": [8.8, 9.2],
     "lead_length": [0.45, 0.75], "lead_width": [0.17, 0.27]}),
    ("Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", {"family": "tht", "grid": [5, 2], "pitch": 2.54,
     "lead": [0.64, 0.64]}),
]


@pytest.mark.parametrize("kicad,spec", KICAD_TWINS, ids=[k.split(":")[1] for k, _ in KICAD_TWINS])
def test_fpgen_lands_match_kicads_own_footprint(kicad, spec):
    """a footprint made from a package's drawing dimensions (IPC-7351B) lands where KiCad's own does, within 0.1 mm"""
    import fpgen
    import pcb
    ref = pcb.footprint(kicad)["pads"]
    fp = fpgen.generate(spec)
    assert {p["num"] for p in fp["pads"]} == {n for n in ref if n}
    for p in fp["pads"]:
        k = max(ref[p["num"]], key=lambda q: q["size"][0] * q["size"][1])
        assert abs(p["at"][0] - k["at"][0]) <= 0.1 and abs(p["at"][1] + k["at"][1]) <= 0.1, p["num"]
        assert abs(p["size"][0] - k["size"][0]) <= 0.1 and abs(p["size"][1] - k["size"][1]) <= 0.1, p["num"]


@pytest.mark.parametrize("family", ["soic", "sot23", "qfp", "qfn", "dfn", "bga", "tht", "pads"])
def test_fpgen_every_family_passes_its_checks_and_reads_back(tmp_path, family):
    import fpgen
    r = fpgen.build(fpgen.TEMPLATES[family], str(tmp_path / "footprints"))
    assert r["ok"], [c for c in r["checks"] if c["severity"] != "ok"]
    assert Path(r["review"]).exists()


def test_fpgen_a_board_uses_a_generated_footprint_from_its_own_folder(tmp_path):
    """a footprint made for a board, in footprints/ beside its script, places, routes and checks like any other"""
    import fpgen
    import pcb_cli
    r = fpgen.build(fpgen.TEMPLATES["soic"], str(tmp_path / "footprints"), lib="Mine")
    (tmp_path / "board.py").write_text(f'''
from pcb import Board
def build():
    b = Board("gen", [(0, 0), (20, 0), (20, 14), (0, 14)], rules="jlcpcb-2")
    b.place("U1", "{r['footprint']}", (10, 7), 0, "IC")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (3, 7), 90, "10k")
    b.net("A", [("U1", "1"), ("R1", "1")])
    b.net("B", [("U1", "8"), ("R1", "2")])
    return b
''')
    b, _ = pcb_cli.load(str(tmp_path / "board.py"))
    assert len(b.comps["U1"]["pads"]) == 8
    import pcb
    assert pcb.autoroute(b) == [] and b.check() == []


def test_lcsc_match_takes_the_mpn_else_the_exact_value_in_stock():
    """the catalogue entry is the part's MPN; else a resistor's exact value at 1 % / a capacitor's value at no less than
    its voltage, in its footprint's size; basic parts first; never short of stock; nothing rather than a near miss"""
    import parts
    row = lambda n, mfr, pkg, d, stock=10000, basic=False: {"lcsc": n, "mfr": mfr, "package": pkg, "description": d,
                                                             "stock": stock, "is_basic": basic}
    r = {"fp": "Resistor_SMD:R_0603_1608Metric", "value": "10k2", "mpn": ""}
    found = [row(1, "a", "0603", "100mW 2.2kΩ ±1% 0603"), row(2, "b", "0603", "100mW 10.2kΩ ±1% 0603", stock=5),
             row(3, "c", "0603", "100mW 10.2kΩ ±1% 0603"), row(4, "d", "0402", "62mW 10.2kΩ ±1% 0402", basic=True)]
    assert parts.lcsc_match(r, found, need=10)["lcsc"] == 3
    c = {"fp": "Capacitor_SMD:C_1210_3225Metric", "value": "4u7 50V", "mpn": ""}
    found = [row(5, "e", "1210", "4.7uF 25V X7R ±10% 1210", basic=True), row(6, "f", "1210", "4.7uF 50V X7R ±10% 1210")]
    assert parts.lcsc_match(c, found)["lcsc"] == 6
    assert parts.lcsc_match(c, found[:1]) is None
    u = {"fp": "Package_SO:SOIC-8", "value": "TPS54360", "mpn": "TI TPS54360BDDAR"}
    assert parts.lcsc_match(u, [row(7, "TPS54360DDAR", "SOIC-8", ""), row(8, "TPS54360BDDAR", "SOIC-8", "")])["lcsc"] == 8
    assert parts.lcsc_query(r) == ["10.2kΩ 0603 1%"] and parts.lcsc_query(c) == ["4.7uF 50V 1210"]


def test_compact_glb_merges_primitives_of_one_material(tmp_path):
    """KiCad's glTF has a primitive per copper shape; compacted, a mesh has one per material, the same triangles"""
    import struct
    import numpy as np
    import pcb_view
    tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    blob, views, accs, prims = bytearray(), [], [], []
    for k, mat in enumerate((0, 0, 1)):
        for arr, tgt in ((tri + k, 34962), (np.array([0, 0, 1] * 3, np.float32).reshape(3, 3), 34962),
                         (np.array([0, 1, 2], np.uint16), 34963)):
            while len(blob) % 4:
                blob.append(0)
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": tgt})
            blob += arr.tobytes()
            accs.append({"bufferView": len(views) - 1, "componentType": 5126 if arr.dtype == np.float32 else 5123,
                         "count": 3, "type": "VEC3" if arr.ndim == 2 else "SCALAR"})
        prims.append({"attributes": {"POSITION": len(accs) - 3, "NORMAL": len(accs) - 2}, "indices": len(accs) - 1,
                      "material": mat})
    j = {"asset": {"version": "2.0"}, "meshes": [{"primitives": prims}], "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}],
         "scene": 0, "materials": [{}, {}], "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(j).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob)
    p = tmp_path / "b.glb"
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    pcb_view.compact_glb(p)
    raw = p.read_bytes()
    out = json.loads(raw[20:20 + struct.unpack_from("<I", raw, 12)[0]])
    ps = out["meshes"][0]["primitives"]
    assert sorted(q["material"] for q in ps) == [0, 1]
    m0 = next(q for q in ps if q["material"] == 0)
    assert out["accessors"][m0["attributes"]["POSITION"]]["count"] == 6 and out["accessors"][m0["indices"]]["count"] == 6
    assert out["accessors"][m0["attributes"]["POSITION"]]["max"] == [2.0, 2.0, 1.0]
    pcb_view.compact_glb(p)                               # (twice: a no-op)
    assert p.read_bytes() == raw


def test_schematic_draws_decoupling_as_a_bank_on_one_rail():
    """capacitors between one rail and ground stand side by side on one pitch: one rail label, one ground symbol, the
    tops and feet joined by wires (as an engineer draws them), not a label and a ground on each"""
    import kicad_sch
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "bench" / "rp2040_board.py"))
    lay = kicad_sch.layout(b)
    banks = {}
    for _, _, ps in lay[0]:
        for p in ps:
            if getattr(p, "pitch", 0):
                banks.setdefault((p.lib, p.rail, p.at[1]), []).append(p)
    big = max(banks.values(), key=len)
    assert len(big) >= 3
    assert sum(1 for p in big for it in p.items if it[0] == "label") == 1
    assert sum(1 for p in big for it in p.items if it[0] == "gnd") == 1
    xs = sorted(p.at[0] for p in big)
    assert len({round(b_ - a, 3) for a, b_ in zip(xs, xs[1:])}) == 1          # (one pitch)
    assert kicad_sch.overlaps(lay) == []


def test_schematic_draws_pull_ups_as_a_bank_on_their_rail():
    """resistors from one rail to nets of their own (I2C and strap pull-ups on 3V3) stand on one wire across their
    tops with one rail label, each foot keeping its own net's label"""
    import kicad_sch
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "examples" / "imu_bno085" / "imu_bno085.py"))
    lay = kicad_sch.layout(b)
    ups = [p for _, _, ps in lay[0] for p in ps if getattr(p, "pitch", 0) and p.foot == "label"]
    assert len(ups) >= 3 and {p.rail for p in ups} == {"3V3"}
    labels = [it[3] for p in ups for it in p.items if it[0] == "label"]
    assert labels.count("3V3") == 1 and {"SCL", "SDA"} <= set(labels)
    assert kicad_sch.overlaps(lay) == []


def test_schematic_of_a_large_design_is_a3_pages_joined_by_global_labels(tmp_path):
    """a design that needs more than A3 (the flight controller) is A3 pages under a cover sheet, every part on one
    page, nothing overlapping, and - read back by KiCad - one netlist, the board's"""
    import kicad_sch
    import pcb
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "examples" / "twilight_fc" / "twilight_fc.py"))
    pgs = kicad_sch.pages(b)
    assert len(pgs) > 1 and {pg[1][0] for pg in pgs} == {"A3"}
    assert all(kicad_sch.overlaps(pg) == [] for pg in pgs)
    placed = [p.ref for pg in pgs for _, _, ps in pg[0] for p in ps]
    assert sorted(set(placed)) == sorted({r for (r, _) in b.pad_net()})
    path = kicad_sch.write(b, str(tmp_path))
    assert len(list(tmp_path.glob("twilight_fc_*.kicad_sch"))) == len(pgs)
    if not pcb.kicad_version():
        pytest.skip("KiCad is not installed: the netlist read-back needs kicad-cli")
    assert kicad_sch.check(b, path) == []


def test_schematic_draws_a_regulator_power_stage_as_one_wired_chain():
    """a regulator's power stage as an engineer draws it - the inductor lying on the rail, the capacitors on its far
    node standing on it (a buck: SW -+-[L]-+--+- VOUT) - one wire, one label at each end, nothing overlapping"""
    import kicad_sch
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "bench" / "power_bank.py"))
    lay = kicad_sch.layout(b)
    chain = [p for _, _, ps in lay[0] for p in ps if p.off is not None]
    assert [p.ref for p in chain] == ["L1", "C4"]
    ends = [p.at[1] + it[1][1] for p in chain for it in p.items
            if it[0] == "label" or (it[0] == "wire" and abs(it[1][1] - it[2][1]) < 1e-6)]   # (the rail: all level)
    assert max(ends) - min(ends) < 1e-6                       # (one straight rail)
    assert len([it for p in chain for it in p.items if it[0] == "label"]) == 2
    assert kicad_sch.overlaps(lay) == []


def test_every_rule_says_why_it_matters():
    """each kind of critic note and finding carries the engineering reason behind it, so a finding teaches the
    engineer reading it"""
    import re
    import critic
    import pcb_cli
    here = Path(__file__).resolve().parent.parent
    kinds = set(re.findall(r'_note\("\w+", "([\w ]+)"', (here / "src" / "critic.py").read_text()))
    kinds |= {k for k, _, _ in pcb_cli.HINTS}
    missing = sorted(k for k in kinds if not critic.WHY.get(k) and k not in ("parts", "net", "placement"))
    assert missing == []
    f = pcb_cli.finding("design", "F.Cu: A and B 0.18 mm apart (clearance 0.2) at (10.6, 10.6)")
    assert f["kind"] == "clearance" and "short" in f["why"]


def test_kicad8_copy_opens_in_kicad8(tmp_path):
    """every run also writes the KiCad project in KiCad 8's format (out/kicad8/, zipped), the format Altium's KiCad
    import reads: with KiCad 8 installed, KiCad 8 opens the board and passes its DRC, and reads the schematic's
    netlist back equal to the board's"""
    import os
    import subprocess
    import zipfile
    import kicad_sch
    import pcb
    import pcb_cli
    here = Path(__file__).parent.parent
    out = tmp_path / "o"
    pcb_cli.main([str(here / "bench" / "blinker.py"), str(out), "--router", "harness", "--kicad-drc", "off"])
    k8 = out / "kicad8"
    assert (k8 / "blinker.kicad_pcb").read_text().splitlines()[1].strip() == "(version 20240108)"
    assert "(31 \"B.Cu\" signal)" in (k8 / "blinker.kicad_pcb").read_text()
    assert (k8 / "blinker.kicad_sch").read_text().splitlines()[1].strip() == "(version 20231120)"
    assert sorted(zipfile.ZipFile(out / "kicad8.zip").namelist()) == ["blinker.kicad_pcb", "blinker.kicad_pro",
                                                                       "blinker.kicad_sch"]
    ver = pcb.kicad_version() or ""
    if not ver.startswith("8."):
        pytest.skip("KiCad 8 is not the installed KiCad: the KiCad 8 read-back runs in CI's kicad8 job")
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(tmp_path / "drc.json"),
                        str(k8 / "blinker.kicad_pcb")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    drc = json.load(open(tmp_path / "drc.json"))
    assert drc["unconnected_items"] == []
    assert [v for v in drc["violations"] if v["severity"] == "error"] == []
    b, _ = pcb_cli.load(str(here / "bench" / "blinker.py"))
    assert kicad_sch.check(b, str(k8 / "blinker.kicad_sch")) == []
    fc, _ = pcb_cli.load(str(here / "examples" / "twilight_fc" / "twilight_fc.py"))      # (a multi-page sheet: its page boxes too)
    kicad_sch.write(fc, str(tmp_path / "fc9"))
    pcb.export_kicad8(str(tmp_path / "fc9"), str(tmp_path / "fc8"))
    assert kicad_sch.check(fc, str(tmp_path / "fc8" / "twilight_fc.kicad_sch")) == []


def test_kicad_layout_imports_as_a_board_script_and_a_revision_keeps_the_rest(tmp_path):
    """a .kicad_pcb comes back (prometheus import) with every pad where it was, a part on each side of the same
    footprint included, its copper clean; moving one part re-routes only that part's nets"""
    import pcb
    import pcb_cli
    import kicad_import
    try:
        pcb.footprint("Package_TO_SOT_SMD:SOT-23")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("imp", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("Q1", "Package_TO_SOT_SMD:SOT-23", (8.0, 10.0), 90, "Q")
    b.place("Q2", "Package_TO_SOT_SMD:SOT-23", (22.0, 10.0), 30, "Q", side="B")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (15.0, 4.0), 0, "1k")
    b.net("A", [("Q1", "1"), ("Q2", "1")])
    b.net("B", [("Q1", "3"), ("R1", "1")])
    b.net("GND", [("Q1", "2"), ("Q2", "2"), ("R1", "2")])
    b.pour("GND", "B", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.hole((3.0, 3.0), 3.2)
    assert not pcb.autoroute(b) and not b.check()
    path = pcb.export_kicad(b, str(tmp_path / "k"), schematic=False)[0]
    got = kicad_import.write(path, str(tmp_path / "rev"))
    segs = sum(1 for t in b.tracks for p, q in zip(t["pts"], t["pts"][1:]) if p != q)
    assert (got["parts"], got["tracks"], got["vias"], got["pours"]) == (3, segs, len(b.vias), 1)
    script = str(tmp_path / "rev" / "board.py")
    c, _ = pcb_cli.load(script)
    where = lambda x: sorted((r, n, L, round(g.centroid.x, 3), round(g.centroid.y, 3)) for r, n, L, g, _ in x.pad_shapes())
    assert where(c) == where(b) and c.holes == b.holes
    assert c.check() == []
    # a revision: Q2 moves, so its nets are routed again and R1's own net keeps its copper exactly
    s = open(script).read().replace("(22.0, 10.0)", "(23.0, 12.0)")
    open(script, "w").write(s)
    d, probs = pcb_cli.load(script)
    assert not probs and d.check() == []
    key = lambda x, n: sorted((t["layer"], tuple(map(tuple, t["pts"]))) for t in x.tracks if t["net"] == n)
    assert key(d, "B") == key(c, "B") and key(d, "A") != key(c, "A")
    # the design written back (KiCad's pcbnew): Q2 moved on its own footprint, B's copper the designer's own items
    import freerouting
    if not freerouting.kicad_available():
        return
    rv = kicad_import.revise(d, str(tmp_path / "revised"))
    assert rv["moved"] == ["Q2"] and "B" not in rv["rerouted"] and not rv["added"] + rv["removed"]
    e, _, _ = kicad_import.read(rv["file"], str(tmp_path / "fp2"))
    assert e.comps["Q2"]["at"] == (23.0, 12.0) and e.comps["Q2"]["side"] == "B" and e.comps["Q2"]["rot"] == 30.0
    uuids = lambda f: {str(pcb._find(g, "uuid")[0][1]) for g in pcb._sx_parse(open(f).read())
                       if isinstance(g, list) and g and g[0] in ("segment", "footprint") and pcb._find(g, "uuid")}
    assert len(uuids(path) & uuids(rv["file"])) >= 3 + len(key(c, "B"))    # (every footprint, B's tracks: the same items)
    assert pcb.kicad_drc(rv["file"])["errors"] == []


def test_a_revision_adds_and_takes_parts_off_the_designers_schematic_too(tmp_path):
    """an imported design with its schematic: a part added (R9), one taken away (R2), one moved (Q2); the revised
    schematic's own netlist (KiCad's) is the board's, R9's footprint links to its new symbol, and the sheet has no
    ERC finding the original did not"""
    import re
    import shutil
    import subprocess
    import pcb
    import pcb_cli
    import kicad_import
    import freerouting
    if not (freerouting.kicad_available() and shutil.which("kicad-cli")):
        pytest.skip("KiCad (pcbnew and kicad-cli) not installed")
    b = pcb.Board("syn", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("Q1", "Package_TO_SOT_SMD:SOT-23", (8.0, 10.0), 90, "Q")
    b.place("Q2", "Package_TO_SOT_SMD:SOT-23", (22.0, 10.0), 30, "Q", side="B")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (15.0, 4.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (15.0, 16.0), 0, "1k")
    b.net("A", [("Q1", "1"), ("Q2", "1")])
    b.net("B", [("Q1", "3"), ("R1", "1"), ("R2", "1")])
    b.net("GND", [("Q1", "2"), ("Q2", "2"), ("R1", "2"), ("R2", "2")])
    assert not pcb.autoroute(b) and not b.check()
    paths = pcb.export_kicad(b, str(tmp_path / "k"))
    kicad_import.write(paths[0], str(tmp_path / "rev"))
    script = tmp_path / "rev" / "board.py"
    s = script.read_text().replace("(22.0, 10.0)", "(23.0, 12.0)").replace("b.place('R2'", "# b.place('R2'")
    s = s.replace("b.net('B', [", "b.place('R9', 'Resistor_SMD:R_0603_1608Metric', (5.0, 16.0), 0, '4k7')\n"
                  "b.net('B', [('R9', '1'), ").replace("b.net('GND', [", "b.net('GND', [('R9', '2'), ")
    script.write_text(s.replace(", ('R2', '1')", "").replace(", ('R2', '2')", ""))
    d, probs = pcb_cli.load(str(script))
    assert not probs and d.check() == []
    rv = kicad_import.revise(d, str(tmp_path / "out"))
    assert (rv["moved"], rv["added"], rv["removed"], rv["schematic"]) == (["Q2"], ["R9"], ["R2"], [])
    sch = pcb._sx_parse((tmp_path / "out" / "rev.kicad_sch").read_text())
    r9 = next(x for x in sch if isinstance(x, list) and x[:1] == ["symbol"] and pcb._find(x, "lib_id")
              and any(p[1] == "Reference" and p[2] == "R9" for p in pcb._find(x, "property")))
    assert f'(path "/{pcb._find(r9, "uuid")[0][1]}")' in open(rv["file"]).read()     # (its footprint: linked to it)
    erc = lambda f: sorted(re.findall(r"^\[(\w+)\]", subprocess.run(["kicad-cli", "sch", "erc", "-o", str(tmp_path / "e.rpt"), f],
                                                                capture_output=True) and (tmp_path / "e.rpt").read_text(), re.M))
    assert erc(str(tmp_path / "out" / "rev.kicad_sch")) == erc(str(tmp_path / "rev" / "source" / "rev.kicad_sch"))


def test_import_carries_cutouts_slots_artwork_labels_and_rules(tmp_path):
    """what a revision must not lose: a cut-out (edge in the Gerber, both KiCad rings), a slot drilled as a slot
    (G85), a logo on the silkscreen and the mask, bottom text with its justify, each designator where it was put,
    the project's own hole clearance"""
    import pcb
    import pcb_cli
    import kicad_import
    try:
        pcb.footprint("Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12")
    except Exception as e:
        pytest.skip(f"KiCad footprints unavailable: {e}")
    b = pcb.Board("art", [(0, 0), (40, 0), (40, 30), (0, 30)])
    b.place("J1", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", (12.0, 22.0), 0, "USB")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (30.0, 8.0), 0, "1k", label=(30.0, 10.0, 0.0, 0.8))
    b.cutout([(20, 5), (25, 5), (25, 10), (20, 10)])
    b.graphics = [{"layer": "F.SilkS", "pts": [(32, 20), (36, 20), (36, 24), (32, 24)], "w": 0.0, "closed": True, "fill": True},
                  {"layer": "B.Mask", "pts": [(5, 5), (8, 5), (8, 8)], "w": 0.0, "closed": True, "fill": True}]
    b.silk_text = [("REV A\nTEST", (8.0, 12.0), 1.0, 90.0, "B", "left")]
    b.hole_clear = 0.2
    drills = pcb.export_gerbers(b, str(tmp_path / "g"))
    assert "G85" in open(next(f for f in drills if f.endswith("PTH.drl") and "NPTH" not in f)).read()
    edge = open(next(f for f in drills if "Edge_Cuts" in f)).read()
    assert edge.count("D02*") == 2                                   # the outline and the cut-out
    path = pcb.export_kicad(b, str(tmp_path / "k"), schematic=False)[0]
    pro = json.load(open(path.replace(".kicad_pcb", ".kicad_pro")))
    assert pro["board"]["design_settings"]["rules"]["min_hole_clearance"] == 0.2
    kicad_import.write(path, str(tmp_path / "rev"))
    c, _ = pcb_cli.load(str(tmp_path / "rev" / "board.py"))
    assert len(c.outline.interiors) == 1 and abs(c.outline.area - b.outline.area) < 1e-3
    assert sorted(g["layer"] for g in c.graphics) == ["B.Mask", "F.SilkS"]
    assert c.silk_text == [("REV A\nTEST", (8.0, 12.0), 1.0, 90.0, "B", "left")]
    assert all(abs(u - v) < 1e-3 for u, v in zip(c.comps["R1"]["label"], (30.0, 10.0, 0.0, 0.8)))
    assert c.hole_clear == 0.2 and c.kicad_rules["rules"]["min_hole_clearance"] == 0.2
    assert pcb.silkscreen(c, "F").intersects(__import__("shapely.geometry", fromlist=["Point"]).Point(34, 22))


def test_update_notice_says_when_github_is_ahead(tmp_path, monkeypatch, capsys):
    """a run says (once a day, on stderr) when GitHub has a newer harness than the one installed, and stays quiet
    when it is the same or when GitHub cannot be reached"""
    import pcb_cli
    import vendor_parts
    monkeypatch.setattr(vendor_parts, "CACHE", tmp_path)
    monkeypatch.setattr(pcb_cli, "installed", lambda: ("clone", tmp_path, "a" * 40))
    monkeypatch.setattr(pcb_cli, "latest", lambda: "b" * 40)
    pcb_cli.update_notice()
    assert "prometheus update" in capsys.readouterr().err
    (tmp_path / "update_check.json").unlink()
    monkeypatch.setattr(pcb_cli, "latest", lambda: "a" * 40)
    pcb_cli.update_notice()
    assert capsys.readouterr().err == ""
    (tmp_path / "update_check.json").unlink()

    def offline():
        raise OSError("no network")
    monkeypatch.setattr(pcb_cli, "latest", offline)
    pcb_cli.update_notice()
    assert capsys.readouterr().err == ""


def test_erc_no_connect_and_allowed_conflict_and_symbol_search():
    """a pin left open on purpose (Board.nc) is not an ERC error, an allowed conflict (Board.erc_allow) is not
    reported, and the symbol search finds KiCad's symbol whose pins are the footprint's pads"""
    import critic
    import parts
    import pcb
    b = pcb.Board("nc", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("U1", None, (15, 10), 0, symbol="Driver_Motor:DRV8833PWP")
    b.place("U2", None, (25, 10), 0, symbol="Driver_Motor:DRV8833PWP")
    erc = lambda: [n["text"] for n in critic.review(b) if n["kind"] == "erc" and n["severity"] == "error"]
    assert any("AIN1" in t for t in erc())
    b.nc("U1", "AIN1", "AIN2", "BIN1", "BIN2", "nSLEEP")
    b.nc("U2", "AIN1", "AIN2", "BIN1", "BIN2", "nSLEEP")
    assert not any("left open" in t for t in erc())
    b.net("OUT", [("U1", "AOUT1"), ("U2", "AOUT1")])
    assert any("drive each other" in t for t in erc())
    b.erc_allow("OUT", "test")
    assert not any("drive each other" in t for t in erc())
    try:
        found = parts.suggest_symbol({"value": "DRV8833", "mpn": "TI DRV8833PWPR", "pads": b.comps["U1"]["pads"]})
    except FileNotFoundError:
        pytest.skip("no KiCad symbol library here")
    assert found == "Driver_Motor:DRV8833PWP"


def test_a_run_without_kicad_still_gives_a_board_and_says_what_it_skipped(tmp_path, monkeypatch):
    """the core works with Python alone: no KiCad or Java means the built-in router, and the run lists that (what,
    why, how to add it) under skipped in result.json instead of hiding it or failing"""
    import freerouting
    import pcb_cli
    here = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(freerouting, "kicad_available", lambda: False)
    rc = pcb_cli.main([str(here / "bench" / "blinker.py"), str(tmp_path), "--kicad-drc", "off", "--no-lcsc"])
    r = json.loads((tmp_path / "result.json").read_text())
    assert rc == 0 and r["clean"] and r["router"] == "harness"
    assert [s["what"] for s in r["skipped"]] == ["FreeRouting"] and "docs/setup.md" in r["skipped"][0]["how"]


def test_schematic_symbol_pin_numbers_match_todays_library():
    # KiCad 9's USB-C symbol numbers its shield S1, today's SH (the footprint's pads): the sheet must use today's
    import kicad_sch
    text = str(kicad_sch._lib_symbol("Connector:USB_C_Receptacle_USB2.0_16P"))
    assert "'SH'" in text or '"SH"' in text, text[:300]
    assert '"S1"' not in text and "'S1'" not in text


def test_clean_corners_no_overshoot_no_right_angles():
    """a stub that runs past the point where its route turns off is cut back to that point, and a right-angle
    corner becomes two 45-degree bends, with the board checking as clean as before"""
    import math
    import pcb
    b = pcb.Board("cc", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (5, 5), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0603_1608Metric", (20, 15), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    x0, y0 = b.pad("R1", "2")
    x1, y1 = b.pad("R2", "1")
    b.track("A", "F", [(x0, y0), (11.6, y0)], 0.25)              # a stub 0.1 mm past the turn at 11.5: inside the
                                                                 # route's copper, so no check flags it (SCL's kink)
    b.track("A", "F", [(11.5, y0), (11.5, y1), (x1, y1)], 0.25)  # the route off it: a right angle at (11.5, y1)
    assert b.check() == []
    assert b.trim_overshoots() == 1 and max(p[0] for p in b.tracks[0]["pts"]) == 11.5
    assert b.chamfer() >= 1 and b.check() == []
    for t in b.tracks:
        for a, c, d in zip(t["pts"], t["pts"][1:], t["pts"][2:]):
            u, v = (c[0] - a[0], c[1] - a[1]), (d[0] - c[0], d[1] - c[1])
            assert abs(u[0] * v[0] + u[1] * v[1]) > 1e-3 * math.hypot(*u) * math.hypot(*v)   # no right angle left


def test_symbol_from_a_pin_table(tmp_path):
    """symgen: a pin table becomes a KiCad symbol laid out as KiCad's are (supplies on top, grounds at the bottom,
    inputs left), its pins checked against the footprint's pads, and the harness reads it back by name"""
    import parts
    import symgen
    spec = {"lib": "T", "name": "LMR51420", "footprint": "Package_TO_SOT_SMD:SOT-23-6",
            "pins": [[1, "GND", "power_in"], [2, "SW", "power_out"], [3, "VIN", "power_in"], [4, "FB", "input"],
                     [5, "EN", "input"], [6, "CB", "passive"]]}
    path = symgen.write(spec, str(tmp_path))
    placed, _ = symgen.layout([tuple(p) for p in spec["pins"]])
    side = {p["name"]: p["rot"] for p in placed}
    assert side["GND"] == 90 and side["VIN"] == 270 and side["FB"] == 0     # (bottom, top, left: pins point inward)
    parts.SYM_DIRS.insert(0, tmp_path)
    try:
        s = parts.symbol("T:LMR51420")
        assert {p["num"]: p["name"] for p in s["pins"]}["4"] == "FB" and path.exists()
    finally:
        parts.SYM_DIRS.remove(tmp_path)
    with pytest.raises(ValueError, match="pads"):
        symgen.write(dict(spec, pins=spec["pins"][:5]), str(tmp_path))


def test_lcsc_suggested_by_value_and_package_never_in_the_bom(monkeypatch):
    """a part whose MPN finds nothing gets a suggestion by footprint and value (basic first, the right package, no
    right-angle header for a vertical one), marked for the engineer and kept out of lcsc"""
    import critic
    import parts
    import pcb
    row = lambda n, mfr, pkg, d, stock=10000, basic=False: {"lcsc": n, "mfr": mfr, "package": pkg, "description": d,
                                                            "stock": stock, "is_basic": basic}
    cat = {"32.768kHz 3215": [row(1, "SC-32S", "SMD3215-2P", "32.768kHz 7pF SMD3215-2P", 99999),
                              row(2, "Q13FC13500004", "SMD3215-2P", "12.5pF 32.768kHz SMD3215-2P", basic=True),
                              row(3, "X32", "SMD3225-4P", "32.768kHz SMD3225-4P", basic=True)],
           "1x6P 2.54mm pin header": [row(4, "R-1x6P", "Push-Pull,P=2.54mm", "1x6P 2.54mm Right Angle", 99999),
                                      row(5, "PZ254V-11-06P", "Plugin,P=2.54mm", "1x6P 2.54mm Pin Header")]}
    monkeypatch.setattr(parts, "_jlc", lambda q: cat.get(q, []))
    b = pcb.Board("s", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("Y1", "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", (8, 10), 0, "32.768k", "Epson FC-135 32.768KA")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", (20, 4), 0, "I2C", "header")
    got = parts.suggest_lcsc(b)
    assert {r: g["lcsc"] for r, g in got.items()} == {"Y1": "C2", "J2": "C5"}
    assert not b.comps["Y1"].get("lcsc") and not b.comps["J2"].get("lcsc")
    notes = [n for n in critic.review(b) if n["kind"] == "lcsc suggested"]
    assert len(notes) == 2 and "add lcsc=" in notes[0]["text"] and notes[0]["why"]


def test_a_via_against_its_own_pad_is_warned_a_via_in_pad_or_a_neck_is_not():
    """solder wicking: a via touching an SMD pad of its net is a warning; one inside the pad (via-in-pad), one a neck
    away, or one against another net's pad (that is clearance's) is not"""
    import critic
    import fab
    import pcb
    b = pcb.Board("v", [(0, 0), (20, 0), (20, 10), (0, 10)])
    b.place("C1", "Capacitor_SMD:C_0402_1005Metric", (10, 5), 0, "100n")
    b.net("GND", [("C1", "2")])
    b.net("V", [("C1", "1")])
    x2 = b.pad("C1", "2")[0]
    neck = lambda: [n for n in critic.review(b) if n["kind"] == "via neck"]
    b.via("GND", (x2 + 0.55, 5), 0.3, 0.6)                    # ring touching the pad
    assert len(neck()) == 1 and neck()[0]["severity"] == "warning" and neck()[0]["why"]
    b.vias.clear()
    b.via("GND", (x2 + 0.3 + 0.3 + fab.VIA_PAD_NECK + 0.05, 5), 0.3, 0.6)     # a neck away
    b.via("GND", (x2, 5), 0.2, 0.4)                                            # via-in-pad
    assert neck() == []
    b.vias.clear()
    b.via("GND", (x2 + 0.2, 5), 0.3, 0.6)                     # centre on the pad, ring half off it: the worst case
    assert len(neck()) == 1


def test_a_routes_folder_beside_the_script_keeps_the_routing(tmp_path, monkeypatch):
    """a script with routes/ beside it: a clean run writes its placement and copper there, and the next run of the
    same design loads them instead of placing and routing"""
    import shutil
    import pcb_cli
    here = Path(__file__).resolve().parent.parent
    shutil.copy(here / "bench" / "blinker.py", tmp_path / "blinker.py")
    (tmp_path / "routes").mkdir()
    monkeypatch.delenv("PCB_REROUTE", raising=False)
    args = [str(tmp_path / "blinker.py"), str(tmp_path / "out"), "--router", "harness", "--kicad-drc", "off",
            "--no-lcsc", "--no-view"]
    assert pcb_cli.main(args) == 0
    kept = list((tmp_path / "routes").glob("*.json.gz"))
    assert len(kept) == 1
    first = json.loads((tmp_path / "out" / "result.json").read_text())
    assert pcb_cli.main(args) == 0
    again = json.loads((tmp_path / "out" / "result.json").read_text())
    assert again["clean"] and "routes/" in again["router"] and again["placement"] == first["placement"]


def test_the_viewer_serves_a_cited_datasheet_and_nothing_else(tmp_path, monkeypatch):
    """/api/datasheet.pdf gives a part's datasheet from the parts cache, and refuses a link the board does not cite"""
    import hashlib
    import pcb_view
    from vendor_parts import CACHE
    url = "https://example.com/u1.pdf"
    dst = CACHE / "datasheets" / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"%PDF-1.4" + b" " * 3000)

    class Cache:
        def board(self):
            return {"comps": [{"ref": "U1", "symbol": {"datasheet": url}}]}
    assert pcb_view._datasheet_pdf(Cache(), tmp_path, "U1", "") == dst
    assert pcb_view._datasheet_pdf(Cache(), tmp_path, "", url) == dst
    assert pcb_view._datasheet_pdf(Cache(), tmp_path, "", "https://example.com/other.pdf") is None
    dst.unlink()


def test_schematic_pin_marks_sit_on_the_wires_and_labels(tmp_path):
    """the viewer's schematic pin marks: every pin end of a part on the BNO085 sheet is on a wire end, a label, or a
    no-connect mark, so a mark is on its pin as KiCad draws it"""
    import re
    import kicad_sch
    import pcb_cli
    import pcb_view
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "examples" / "imu_bno085" / "imu_bno085.py"))
    sch = kicad_sch.write(b, str(tmp_path))
    text = Path(sch).read_text()
    ends = {(round(float(a), 2), round(float(b_), 2)) for a, b_ in
            re.findall(r'\((?:xy|at) ([\d.\-]+) ([\d.\-]+)', text)}
    pins = pcb_view._sch_pins(sch)
    assert len(pins) > 40 and {p["ref"] for p in pins} >= {"U1", "U2", "J1"}
    assert all((round(p["x"], 2), round(p["y"], 2)) in ends for p in pins)


def test_a_regulators_input_and_output_capacitors_are_drawn_with_it():
    """a capacitor from a supply to ground is drawn in the block of the nearest chip with a power pin on that supply:
    the BNO085 board's LDO keeps its 1 uF input (VBUS) and output (3V3), the IMU its 100 nF"""
    import kicad_sch
    import pcb_cli
    b, _ = pcb_cli.load(str(Path(__file__).resolve().parent.parent / "examples" / "imu_bno085" / "imu_bno085.py"))
    import pcb
    pcb.autoplace(b)
    grp = {t: set(rs) for t, rs in kicad_sch.blocks(b)}
    ldo = next(v for t, v in grp.items() if "(U2)" in t)
    imu = next(v for t, v in grp.items() if "(U1)" in t)
    assert {"C1", "C2"} <= ldo and {"C3", "C4"} <= imu


def test_a_long_led_chain_off_a_small_chip_is_drawn_on_a3_pages():
    """a group too large for one sheet (a 32-LED chain off a 5-pin buffer) is drawn as numbered blocks in rows a
    sheet holds, not one line metres long: every block fits A3, nothing overlaps"""
    import kicad_sch
    import pcb
    b = pcb.Board("chain", [(0, 0), (120, 0), (120, 80), (0, 80)])
    b.place("U1", "Package_TO_SOT_SMD:SOT-23-5", (5, 5), 0, "74AHCT1G125", symbol="74xGxx:74AHCT1G125")
    leds = [f"D{k + 1}" for k in range(32)]
    for k, r in enumerate(leds):
        b.place(r, "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm", (10 + (k % 8) * 12, 20 + (k // 8) * 12), 0, "WS2812B",
                symbol="LED:WS2812B")
    b.net("DIN", [("U1", "4"), ("D1", "DIN")])
    for a, c in zip(leds, leds[1:]):
        b.net(f"D_{a}", [(a, "DOUT"), (c, "DIN")])
    b.net("5V", [("U1", "5")] + [(r, "VDD") for r in leds])
    b.net("GND", [("U1", "3"), ("U1", "1")] + [(r, "VSS") for r in leds])
    titles = [t for t, _ in kicad_sch.blocks(b)]
    assert any(t.endswith("1/2") for t in titles)
    pgs = kicad_sch.pages(b)
    assert all(p[1][0] in ("A4", "A3") for p in pgs) and all(kicad_sch.overlaps(p) == [] for p in pgs)


def test_the_board_switcher_lists_remembered_boards_and_opens_only_those(tmp_path, monkeypatch):
    """each viewer remembers its run folder; the switcher lists the ones still holding a run, newest first, and the
    open request is refused for a folder it does not know"""
    import json as _json
    import pcb_view
    monkeypatch.setattr(pcb_view, "_boards_file", lambda: tmp_path / "boards.json")
    monkeypatch.setattr(pcb_view, "_throwaway", lambda out: False)
    a, b = tmp_path / "a" / "out", tmp_path / "b" / "out"
    for d, name in ((a, "alpha"), (b, "beta")):
        d.mkdir(parents=True)
        (d / "result.json").write_text(_json.dumps({"board": name, "clean": name == "alpha", "script": str(d.parent / "board.py")}))
        pcb_view.remember(d)
    pcb_view.remember(tmp_path / "gone" / "out")             # (a folder with no run: left out)
    got = pcb_view.boards(a.resolve())
    assert [g["name"] for g in got] == ["beta", "alpha"] or {g["name"] for g in got} == {"alpha", "beta"}
    assert next(g for g in got if g["name"] == "alpha")["current"] and len(got) == 2


def test_a_pad_on_a_bare_hole_is_a_finding():
    """a mounting hole drilled through a part's pad cuts it (KiCad's hole clearance): the design check says so, and
    the placement check flags the hole inside the part's courtyard before any routing"""
    import pcb
    b = pcb.Board("h", [(0, 0), (30, 0), (30, 20), (0, 20)], rules="jlcpcb-2")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (10.0, 10.0), 0, "")
    b.hole((11.9, 10.0), d=3.2)                          # (its wall 0.3 mm into pin 1's 1.7 mm pad, drills 0.6 apart)
    assert any("J1 pad 1" in p and "bare hole" in p for p in b.check())
    assert any("mounting hole" in p and "J1" in p for p in b.check_placement())
    c = pcb.Board("h2", [(0, 0), (30, 0), (30, 20), (0, 20)], rules="jlcpcb-2")
    c.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (10.0, 10.0), 0, "")
    c.hole((20.0, 10.0), d=3.2)
    assert not any("bare hole" in p for p in c.check()) and not any("mounting hole" in p for p in c.check_placement())


def test_a_via_keeps_the_hole_gap_from_a_slot_by_its_shape(monkeypatch):
    """the router's vias keep the hole gap from a USB-C shell's slot by the slot's shape (it is longer than it is
    wide: a circle at its centre let a ground via in 0.16 mm from it, boards/03 as it was first routed)"""
    import runpy
    import pcb
    monkeypatch.setenv("PCB_REROUTE", "1")
    monkeypatch.setenv("PCB_WORKERS", "1")
    here = Path(__file__).parent.parent
    b = runpy.run_path(str(here / "boards" / "03_usbc_power_breakout" / "board.py"))["build"]()
    pcb.place_and_route(b, router="harness")
    assert not any("drills" in p for p in b.check())


def test_export_drops_kicad10_footprint_items(tmp_path):
    """KiCad's library now gives some footprints KiCad 10's (point ...) anchors and ${KICAD10_3DMODEL_DIR} models:
    the exported board is KiCad 9's format, which cannot read a point, so they are left out and the model path
    written as KiCad 9 finds it"""
    import pcb
    b = pcb.Board("p", [(0, 0), (20, 0), (20, 12), (0, 12)], rules="jlcpcb-2")
    b.place("D1", "LED_THT:LED_D3.0mm", (8.0, 6.0), 0, "red")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (14.0, 6.0), 0, "1k")
    b.net("A", [("D1", "2"), ("R1", "1")])
    pcb.export_kicad(b, str(tmp_path / "k"))
    txt = next((tmp_path / "k").glob("*.kicad_pcb")).read_text()
    assert "(point" not in txt and "KICAD10_3DMODEL_DIR" not in txt
    import shutil
    if shutil.which("kicad-cli"):                         # (and KiCad opens it: its DRC writes a report)
        d = pcb.kicad_drc(str(next((tmp_path / "k").glob("*.kicad_pcb"))))
        assert "errors" in d


def test_kicad_drc_says_when_kicad_cannot_read_the_board(tmp_path):
    """a board file KiCad cannot open is a clear error, not a missing report file"""
    import shutil
    import pcb
    if not shutil.which("kicad-cli"):
        pytest.skip("no kicad-cli")
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("(kicad_pcb (version 20241229) (footprint \"x\" (layer \"F.Cu\") (point (at 0 0) (size 1))))")
    with pytest.raises(RuntimeError, match="could not check"):
        pcb.kicad_drc(str(bad))


def test_cli_finishes_and_keeps_a_board_whose_script_draws_some_copper(tmp_path, capsys):
    """a script that draws part of its copper (a hand-drawn power feed) with every part placed: the run routes the
    rest (not 'routed by the script'), keeps it in routes/, and --stage check starts from the saved board; a change
    to the hand-drawn copper is another design"""
    import json
    import pcb_cli
    d = tmp_path / "bd"
    (d / "routes").mkdir(parents=True)
    src = '''from pcb import Board
OUT = [(0, 0), (30, 0), (30, 20), (0, 20)]
W = %s
def build():
    b = Board("hand", OUT, rules="jlcpcb-2")
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (6.0, 10.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (24.0, 10.0), 0, "1k")
    b.place("R3", "Resistor_SMD:R_0805_2012Metric", (15.0, 15.0), 0, "1k")
    b.net("P", [("R1", "2"), ("R2", "1")], amps=1.0)
    b.net("Q", [("R1", "1"), ("R3", "1")])
    b.net("S", [("R2", "2"), ("R3", "2")])
    x1, y1 = b.pad("R1", "2"); x2, y2 = b.pad("R2", "1")
    b.track("P", "F", [(x1, y1), (x2, y2)], W)
    return b
'''
    (d / "board.py").write_text(src % "0.6")
    (d / "req.json").write_text('{"size_mm": [30, 20]}')
    out = str(d / "out")
    pcb_cli.main([str(d / "board.py"), out, "--spec", str(d / "req.json"), "--router", "harness", "--kicad-drc", "off",
                  "--no-lcsc"])
    r = json.load(open(d / "out" / "result.json"))
    assert r["clean"] and r["router"] == "harness" and list((d / "routes").glob("hand_*.json.gz"))
    capsys.readouterr()
    pcb_cli.main([str(d / "board.py"), out, "--spec", str(d / "req.json"), "--stage", "check", "--kicad-drc", "off"])
    assert "from the last run" in capsys.readouterr().out and json.load(open(d / "out" / "result.json"))["clean"]
    (d / "board.py").write_text(src % "0.8")                # (the hand-drawn feed widened: not the saved board)
    pcb_cli.main([str(d / "board.py"), out, "--stage", "check", "--kicad-drc", "off"])
    assert "another version of the design" in capsys.readouterr().out


def test_critic_takes_the_hole_count_alone():
    """req.json "holes": 4 (the count, no diameter) is read as {"count": 4}, not a crash"""
    import critic
    import pcb
    b = pcb.Board("c", [(0, 0), (20, 0), (20, 20), (0, 20)])
    b.hole((3, 3), d=3.2)
    notes = critic.review(b, {"holes": 2})
    assert any(n["kind"] == "holes" and n["severity"] == "error" for n in notes)
    assert not any(n["kind"] == "holes" and n["severity"] == "error" for n in critic.review(b, {"holes": 1}))


def test_fanout_keeps_out_of_a_keepout():
    """the fan-out's stubs stay out of a keep-out (an antenna's): a pad whose way out runs into one gets no stub;
    the others keep theirs"""
    import pcb
    b = pcb.Board("f", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("U1", "Package_SO:SSOP-28_3.9x9.9mm_P0.635mm", (15.0, 10.0), 90, "")   # (pin 1 at (10.9, 7.4), its
    b.net("A", [("U1", "1"), ("U1", "28")])                                          #  pad down to y 6.8)
    b.net("B", [("U1", "5"), ("U1", "24")])
    b.keepout([(9.0, 0), (12.0, 0), (12.0, 6.6), (9.0, 6.6)], "FB", "antenna")      # (just past pin 1's end)
    b.fanout()
    fanned = {t.get("fan") for t in b.tracks}
    assert ("U1", "1") not in fanned and ("U1", "5") in fanned
    assert not any("keep-out" in p for p in b.check())


def test_the_scripts_own_copper_is_left_as_drawn():
    """copper the script draws (a feed with right angles, its tip inside the pad) comes out of routing exactly as
    drawn: the spur trim, loop removal, overshoot cut and chamfer leave it be; the router finishes the rest (and a
    dangling end the script draws is the script's to fix: a finding, not trimmed away)"""
    import pcb
    b = pcb.Board("user_copper", [(0, 0), (30, 0), (30, 20), (0, 20)], rules="jlcpcb-2")
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (6.0, 6.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (24.0, 14.0), 0, "1k")
    b.place("R3", "Resistor_SMD:R_0805_2012Metric", None, 0, "1k")
    b.net("P", [("R1", "2"), ("R2", "1")], amps=0.5)
    b.net("Q", [("R1", "1"), ("R3", "1")])
    b.net("S", [("R2", "2"), ("R3", "2")])
    (x1, y1), (x2, y2) = b.pad("R1", "2"), b.pad("R2", "1")
    drawn = [(x1, y1), (x1 + 5.0, y1), (x1 + 5.0, y2), (x2, y2), (x2, y2 + 0.4)]   # (right angles; a tip in R2.1)
    b.track("P", "F", drawn, 0.5)
    probs = pcb.place_and_route(b, router="harness")
    assert not probs
    mine = [t for t in b.tracks if t["net"] == "P" and t["layer"] == "F" and t["w"] == 0.5]
    assert any([tuple(p) for p in t["pts"]] == drawn for t in mine) and all(t.get("user") for t in mine)


def test_schematic_keeps_facing_pins_apart(tmp_path):
    """a dual pot's units lie end to end, pin 3 facing pin 4: their stubs, and the wire joining the ground pins on
    one side, must not touch the other net (KiCad's netlist of the sheet had the left input shorted to GND)"""
    import shutil
    import kicad_sch
    import pcb
    b = pcb.Board("dp", [(0, 0), (40, 0), (40, 30), (0, 30)])
    b.place("RV1", "Potentiometer_THT:Potentiometer_Alps_RK097_Dual_Horizontal", (30.0, 10.0), 270, "10k",
            symbol="Device:R_Potentiometer_Dual")
    b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (5.0, 20.0), 0, "")
    b.net("GND", [("RV1", "1"), ("RV1", "4"), ("J1", "1")])
    b.net("AINL", [("RV1", "3"), ("J1", "2")])
    b.net("WL", [("RV1", "2"), ("J1", "3")])
    b.net("WR", [("RV1", "5"), ("RV1", "6"), ("J1", "4")])
    path = kicad_sch.write(b, str(tmp_path))
    if not shutil.which("kicad-cli"):
        pytest.skip("no kicad-cli to read the sheet back")
    assert kicad_sch.check(b, path) == []


def test_a_via_moved_off_its_pad_keeps_the_nets_width():
    """neck_vias moves a via off a pad of its own net with a track as wide as the net needs (up to the pad's
    width), not a 0.2 mm tie that then fails the current check"""
    import pcb
    b = pcb.Board("nv", [(0, 0), (20, 0), (20, 12), (0, 12)], rules="jlcpcb-2")
    b.place("C1", "Capacitor_SMD:C_0805_2012Metric", (8.0, 6.0), 0, "10u")
    b.place("C2", "Capacitor_SMD:C_0805_2012Metric", (14.0, 6.0), 0, "10u")
    b.net("GND", [("C1", "2"), ("C2", "2")], amps=1.5)
    b.net("V", [("C1", "1"), ("C2", "1")], amps=0.1)
    x, y = b.pad("C1", "2")
    b.via("GND", (x + 1.0, y))                            # (its ring right against the pad)
    assert not pcb.neck_vias(b)
    t = b.tracks[-1]
    assert t["net"] == "GND" and t["w"] >= b.net_width("GND") - 1e-6
    assert not any("too narrow" in p for p in b.check())


def test_autoplace_keeps_parts_off_the_scripts_copper():
    """a part the placer places does not land on a track the script drew (it would short to it)"""
    import pcb
    from shapely.geometry import LineString
    b = pcb.Board("pl", [(0, 0), (20, 0), (20, 10), (0, 10)], rules="jlcpcb-2")
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (3.0, 5.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (17.0, 5.0), 0, "1k")
    for i in range(3, 9):
        b.place(f"R{i}", "Resistor_SMD:R_0805_2012Metric", None, 0, "1k")
    b.net("P", [("R1", "2"), ("R2", "1")], amps=2.0)
    b.track("P", "F", [b.pad("R1", "2"), b.pad("R2", "1")], 1.0)       # (a wide feed across the middle)
    for i in range(3, 9, 2):
        b.net(f"N{i}", [(f"R{i}", "1"), (f"R{i + 1}", "1")])
    assert not pcb.autoplace(b)
    feed = LineString([b.pad("R1", "2"), b.pad("R2", "1")]).buffer(0.5)
    assert not [r for r in b.comps if r not in ("R1", "R2") and b.courtyard(r).intersects(feed)]


def test_fine_pitch_escapes_follow_the_pads_long_axis():
    """the stubs drawn out of a fine-pitch part's pins before FreeRouting run along each pad's long axis: the end
    pins of a long SSOP row lie further out along the row than across it, and a stub along the row ran into the
    next pin (0.155 mm at 0.635 mm pitch)"""
    import freerouting
    import pcb
    b = pcb.Board("e", [(0, 0), (30, 0), (30, 20), (0, 20)], rules="jlcpcb-2")
    b.place("U1", "Package_SO:SSOP-28_3.9x9.9mm_P0.635mm", (15.0, 10.0), 90, "")
    for i in range(1, 15):
        b.net(f"N{i}", [("U1", str(i)), ("U1", str(29 - i))])
    assert freerouting.escape_fine_pitch(b) == 28
    assert not [p for p in b.check() if "apart" in p or "short" in p]


def test_tidy_merges_two_vias_of_one_net_drilled_into_each_other():
    """two vias of one net closer than the hole gap (two routing passes' drops at one spot) become one, the tracks
    that met the second brought to the first"""
    import pcb
    b = pcb.Board("tv", [(0, 0), (20, 0), (20, 12), (0, 12)], rules="jlcpcb-2")
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (5.0, 6.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (15.0, 6.0), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    (x1, y1), (x2, y2) = b.pad("R1", "2"), b.pad("R2", "1")
    b.track("A", "F", [(x1, y1), (10.0, y1)], 0.3)
    b.via("A", (10.0, y1))
    b.via("A", (10.3, y1))                               # (drilled 0.0 mm from the first)
    b.track("A", "B", [(10.3, y1), (x2 - 1.5, y2)], 0.3)
    b.via("A", (x2 - 1.5, y2))                           # (and up again to R2.1)
    b.track("A", "F", [(x2 - 1.5, y2), (x2, y2)], 0.3)
    assert any("drills" in p for p in b.check())
    assert b.tidy() >= 1 and len(b.vias) == 2
    assert not any("drills" in p for p in b.check()) and len(b.islands("A")) == 1


def test_courtyards_are_kicads_shapes():
    """a courtyard is the footprint's own shape (an SOIC's is notched over the body, past its pins), not its box, and
    any overlap of two is one (KiCad's DRC flags a 0.03 mm strip): a part tucked into the notch is clear, a part
    0.03 mm over another is not"""
    import pcb
    b = pcb.Board("cy", [(0, 0), (20, 0), (20, 14), (0, 14)])
    b.place("U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", (8.0, 6.0), 0, "")
    b.place("R1", "Resistor_SMD:R_0603_1608Metric", (8.0 + 4.4, 6.0 + 3.23), 0, "")   # (in the corner notch)
    assert not any("overlap" in p for p in b.check_placement())
    assert b.courtyard("U1").area < 7.4 * 5.4 - 0.5
    c = pcb.Board("cy2", [(0, 0), (20, 0), (20, 14), (0, 14)])
    c.place("R1", "Resistor_SMD:R_0603_1608Metric", (5.0, 6.0), 0, "")
    c.place("R2", "Resistor_SMD:R_0603_1608Metric", (5.0, 6.0 + 1.46 - 0.03), 0, "")  # (0.03 mm over R1's)
    assert any("overlap" in p for p in c.check_placement())


def test_house_check_of_a_four_layer_board_without_a_preset():
    """a 4-layer board made with no house preset is checked against JLCPCB's 4-layer process, not refused by the
    2-layer one for its layer count"""
    import fab
    import pcb
    b = pcb.Board("l4", [(0, 0), (20, 0), (20, 20), (0, 20)], layers=4)
    assert not any("layers" in p for p in fab.fab_check(b))
    assert any("layers" in p for p in fab.fab_check(b, "jlcpcb-2"))


def test_a_via_moved_off_its_pad_takes_every_track_end_in_its_ring():
    """neck_vias brings along each track end its via's ring holds, not only one at its centre (FreeRouting ends a
    track 0.2 mm off a via's centre; left behind, the track dangled)"""
    import pcb
    b = pcb.Board("nv2", [(0, 0), (20, 0), (20, 12), (0, 12)], rules="jlcpcb-2")
    b.place("C1", "Capacitor_SMD:C_0805_2012Metric", (8.0, 6.0), 0, "10u")
    b.place("C2", "Capacitor_SMD:C_0805_2012Metric", (14.0, 6.0), 0, "10u")
    b.net("A", [("C1", "2"), ("C2", "2")])
    x, y = b.pad("C1", "2")
    (x2, y2) = b.pad("C2", "2")
    b.via("A", (x + 0.75, y))                             # (against C1.2: it moves ~0.3 mm off)
    b.track("A", "B", [(x + 0.75 - 0.15, y + 0.15), (x2 - 1.5, y2)], 0.25)   # (its end in the ring, off centre)
    b.via("A", (x2 - 1.5, y2))
    b.track("A", "F", [(x2 - 1.5, y2), (x2, y2)], 0.25)
    assert not pcb.neck_vias(b)
    assert not any("dangling" in p for p in b.check()) and len(b.islands("A")) == 1


def test_mounting_holes_take_references_no_part_has(tmp_path):
    """a script that names parts H1, H2 (headers): the exported mounting holes are numbered past them, so no two
    footprints share a reference (KiCad's Specctra export, FreeRouting's input, failed without a word)"""
    import re
    import pcb
    b = pcb.Board("hr", [(0, 0), (30, 0), (30, 20), (0, 20)])
    b.place("H1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (10.0, 10.0), 0, "")
    b.place("H2", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (20.0, 10.0), 0, "")
    b.hole((3.5, 3.5))
    b.hole((26.5, 16.5))
    txt = next(p for p in pcb.export_kicad(b, str(tmp_path)) if p.endswith(".kicad_pcb"))
    refs = re.findall(r'\(property "Reference" "([^"]*)"', open(txt).read())
    assert len(refs) == len(set(refs)) == 4


def test_via_current_warns_of_a_power_path_through_one_via():
    """a 3 A net that changes layer through one 0.3 mm via (~1 A) is a critic warning; three of them in parallel
    with a 0.4 mm drill (4 A) are not"""
    import critic
    import pcb
    def board(n_vias, drill):
        b = pcb.Board("vc", [(0, 0), (30, 0), (30, 12), (0, 12)], rules="jlcpcb-2")
        b.place("J1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (3.0, 7.0), 0, "")
        b.place("R1", "Resistor_SMD:R_2512_6332Metric", (24.0, 6.0), 0, "0.1")
        b.place("R2", "Resistor_SMD:R_2512_6332Metric", (24.0, 1.0), 0, "0.1")   # (closing the other end)
        b.place("F2", "Fuse:Fuse_1206_3216Metric", (20.0, 10.0), 0, "")           # (the path's far side: a fuse)
        b.net("P", [("J1", "1"), ("R1", "1"), ("F2", "1")], amps=3.0)
        b.net("Q", [("J1", "2"), ("R1", "2"), ("R2", "1"), ("R2", "2")], amps=0.1)
        (xj, yj), (xr, yr) = b.pad("J1", "1"), b.pad("R1", "1")
        b.track("P", "B", [(xj, yj), (14.0, yj)], 2.0)
        for k in range(n_vias):
            b.via("P", (14.0, yj - 0.9 + 0.9 * k), drill, drill + 0.4)
        b.track("P", "F", [(14.0, yj), (xr, yr)], 2.0)
        b.track("P", "F", [b.pad("F2", "1"), (xr, yr)], 0.6)
        return b
    weak = lambda b: [n for n in critic.review(b) if n["kind"] == "via current"]
    assert weak(board(1, 0.3)) and not weak(board(3, 0.4))


def test_import_keeps_overhang_symbols_and_no_connects(tmp_path):
    """a board exported and imported back (prometheus import) keeps an edge connector's overhang, each part's
    symbol (read from the project's schematic, used when its pins are the footprint's pads) and the pins the
    schematic flags no-connect"""
    import kicad_import
    import pcb
    b = pcb.Board("rt", [(0, 0), (30, 0), (30, 18), (0, 18)], rules="jlcpcb-2")
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (3.2, 9.0), 270, "USB-C",
            overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("U1", None, (18.0, 9.0), 0, "CH340C", symbol="Interface_USB:CH340C")
    b.net("D+", [("J1", "A6"), ("J1", "B6"), ("U1", "UD+")])
    b.net("D-", [("J1", "A7"), ("J1", "B7"), ("U1", "UD-")])
    b.nc("U1", "nRI", "nDCD")
    paths = pcb.export_kicad(b, str(tmp_path / "k"))
    kpcb = next(p for p in paths if p.endswith(".kicad_pcb"))
    kicad_import.write(kpcb, str(tmp_path / "rev"))
    src = (tmp_path / "rev" / "board.py").read_text()
    j1 = next(ln for ln in src.splitlines() if ln.startswith("b.place('J1'"))
    u1 = next(ln for ln in src.splitlines() if ln.startswith("b.place('U1'"))
    assert "overhang=True" in j1 and "symbol='Interface_USB:CH340C'" in u1
    nc = next(ln for ln in src.splitlines() if ln.startswith("b.nc('U1'"))
    assert "'11'" in nc and "'12'" in nc                 # (nRI, nDCD)


def test_freerouting_gives_the_same_tracks_twice(monkeypatch):
    """the same placed board routed by FreeRouting twice gives the same tracks in the same order (KiCad's session
    import hands segments back in no fixed order: different Gerbers for the same copper)"""
    import runpy
    import freerouting
    import pcb
    if not (freerouting.available() and freerouting.kicad_available()):
        pytest.skip("FreeRouting through KiCad is not installed")
    monkeypatch.setenv("PCB_REROUTE", "1")
    here = Path(__file__).parent.parent
    runs = []
    for _ in range(2):
        b = runpy.run_path(str(here / "bench" / "blinker.py"))["build"]()
        pcb.route_board(b, "freerouting")
        runs.append([(t["net"], t["layer"], [tuple(p) for p in t["pts"]], t["w"]) for t in b.tracks])
    assert runs[0] == runs[1]


def test_a_saved_routing_without_the_scripts_copper_is_not_reused(tmp_path):
    """routes/ and board_state.pkl are reused only when they still hold every track the script draws: an older
    harness that trimmed or dropped them saved a routing under the same design key"""
    import gzip
    import pcb
    import pcb_cli
    b = pcb.Board("sc", [(0, 0), (20, 0), (20, 12), (0, 12)])
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (5.0, 6.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (15.0, 6.0), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")])
    drawn = [b.pad("R1", "2"), b.pad("R2", "1")]
    b.track("A", "F", drawn, 0.5)
    key = pcb_cli.design_key(b)
    store = tmp_path / "routes"
    store.mkdir()
    def save(tracks):
        b2 = pcb.Board("sc", [(0, 0), (20, 0), (20, 12), (0, 12)])
        b2.comps = b.comps
        b2.tracks, b2.vias = tracks, []
        pcb_cli.save_store(b2, str(store), key)
    save([{"net": "A", "layer": "F", "pts": drawn, "w": 0.3, "amps": None}])          # (narrowed: not the script's)
    assert not pcb_cli.load_store(b, str(store), key)
    save([{"net": "A", "layer": "F", "pts": drawn, "w": 0.5, "amps": None}])
    assert pcb_cli.load_store(b, str(store), key)


def test_design_key_is_the_scripts_not_the_routers(tmp_path):
    """the design key covers the copper the script draws (tagged as load builds the board), not the routing added
    after: the key of a routed board is the key of its script (the viewer's live edit and the next CLI run agree)"""
    import pcb
    import pcb_cli
    (tmp_path / "s.py").write_text('''from pcb import Board
def build():
    b = Board("dk", [(0, 0), (24, 0), (24, 12), (0, 12)], rules="jlcpcb-2")
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (5.0, 6.0), 0, "1k")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (19.0, 6.0), 0, "1k")
    b.place("R3", "Resistor_SMD:R_0805_2012Metric", (12.0, 2.5), 0, "1k")
    b.net("A", [("R1", "2"), ("R2", "1")], amps=1.0)
    b.net("B", [("R1", "1"), ("R3", "1")])
    b.net("C", [("R2", "2"), ("R3", "2")])
    b.track("A", "F", [b.pad("R1", "2"), b.pad("R2", "1")], 0.6)
    return b
''')
    b, _ = pcb_cli.load(str(tmp_path / "s.py"))
    k0 = pcb_cli.design_key(b)
    pcb.route_board(b, "harness")
    assert len(b.tracks) > 1 and pcb_cli.design_key(b) == k0


def test_the_scripts_vias_stay_where_it_drew_them():
    """a via the script draws against a pad of its own net is not moved by neck_vias, nor merged away by tidy"""
    import pcb
    b = pcb.Board("uv", [(0, 0), (20, 0), (20, 12), (0, 12)], rules="jlcpcb-2")
    b.place("C1", "Capacitor_SMD:C_0805_2012Metric", (8.0, 6.0), 0, "")
    b.place("C2", "Capacitor_SMD:C_0805_2012Metric", (14.0, 6.0), 0, "")
    b.net("A", [("C1", "2"), ("C2", "2")])
    x, y = b.pad("C1", "2")
    b.via("A", (x + 0.75, y))
    pcb.mark_user_copper(b)
    pcb.neck_vias(b)
    b.tidy()
    assert [tuple(v["at"]) for v in b.vias] == [(x + 0.75, y)]


def test_a_round_courtyard_drawn_with_arcs_is_round():
    """a courtyard of two half-circle arcs is a circle (its area within 2 %), not the diamond of their end and mid
    points"""
    import pcb
    tree = pcb._sx_parse('(footprint "x" (fp_arc (start -5 0) (mid 0 -5) (end 5 0) (layer "F.CrtYd")) '
                         '(fp_arc (start 5 0) (mid 0 5) (end -5 0) (layer "F.CrtYd")))')
    g = pcb._courtyard_poly(tree)
    assert abs(g.area - math.pi * 25) < 0.02 * math.pi * 25


def test_edge_marks_wait_for_a_placed_part():
    """a part whose footprint marks the board edge is not judged against the edge before it is placed"""
    import critic
    import pcb
    b = pcb.Board("em", [(0, 0), (40, 0), (40, 30), (0, 30)])
    b.place("J1", "Connector_Audio:Jack_3.5mm_CUI_SJ1-3523N_Horizontal", None, 0, "")
    assert not any(n["kind"] == "edge" and "marks where" in n["text"] for n in critic.review(b))


def test_an_escape_stub_the_script_draws_is_cut_back_not_dropped():
    """a stub the script draws out of a pin (for the router to take on from) that the routing joins part-way along
    has its loose tip cut back to the join, as any spur; it stays the script's track, and a saved routing holding it
    so is still the script's (its copper check accepts it)"""
    import pcb
    import pcb_cli
    b = pcb.Board("es", [(0, 0), (20, 0), (20, 12), (0, 12)])
    b.place("R1", "Resistor_SMD:R_0805_2012Metric", (4.0, 6.0), 0, "")
    b.place("R2", "Resistor_SMD:R_0805_2012Metric", (16.0, 9.0), 0, "")
    b.net("A", [("R1", "2"), ("R2", "1")])
    x1, y1 = b.pad("R1", "2")
    b.track("A", "F", [(x1, y1), (x1 + 6.0, y1)], 0.3)          # (the stub, 6 mm out)
    pcb_cli._own(b)
    script = pcb.Board("es", [(0, 0), (20, 0), (20, 12), (0, 12)])
    script.tracks = [dict(t) for t in b.tracks]
    b.track("A", "F", [(x1 + 3.0, y1), (x1 + 3.0, 9.0), b.pad("R2", "1")], 0.3)   # (the route, from 3 mm along)
    b.prune_spurs()
    stub = [t for t in b.tracks if t.get("user")]
    assert len(stub) == 1 and abs(stub[0]["pts"][-1][0] - (x1 + 3.0)) < 0.2
    assert not any("dangling" in p for p in b.check())
    assert pcb_cli._keeps_script_copper(script, b.tracks, b.vias)


def test_a_wide_nets_way_out_of_a_fine_pitch_pin_is_no_wider_than_the_pin():
    """the built-in router's stub out of a fine-pitch pad, for a net whose current wants a track wider than the pad,
    is drawn as wide as the pad (a pin entry), not at the full width that runs into the neighbouring pins"""
    import pcb
    b = pcb.Board("sw", [(0, 0), (30, 0), (30, 20), (0, 20)], rules="jlcpcb-2")
    b.place("U1", "Package_SO:SSOP-28_3.9x9.9mm_P0.635mm", (12.0, 10.0), 90, "")
    b.place("R1", "Resistor_SMD:R_1206_3216Metric", (12.0, 2.0), 0, "")
    b.place("R2", "Resistor_SMD:R_1206_3216Metric", (24.0, 16.0), 0, "")
    b.net("P", [("U1", "5"), ("R1", "1")], amps=2.0)                  # (needs ~0.51 mm; the pin is 0.4 mm wide)
    b.net("Q", [("U1", "4"), ("R2", "1")])
    b.net("S", [("U1", "6"), ("R2", "2")])
    pcb.route(b, "P", width=b.net_width("P"))
    pcb.route(b, "Q", width=0.2)
    pcb.route(b, "S", width=0.2)
    x, y = b.pad("U1", "5")
    out = [t for t in b.tracks if t["net"] == "P" and any(math.dist(p, (x, y)) < 1e-3 for p in t["pts"])]
    assert out and max(t["w"] for t in out) <= 0.4 + 1e-6
    assert not [p for p in b.check() if "apart" in p or "short" in p]
