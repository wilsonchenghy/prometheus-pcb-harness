"""The design check: every pad of a net joined through its copper, a short where another net's copper touches, IPC-2221
width for the current, clearance, voltage drop, drills, the board edge, the current through vias, a pour's neck, and
the instant placement check. ``Board`` inherits these (``board.check()``, ``board.check_placement()``); split from
``pcb``. This module does not import ``pcb`` at load time (``pcb`` imports it while it loads): ``check`` reaches it
when called.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple


class CheckMixin:
    """the checks of a ``pcb.Board``: they read the board's parts, nets and copper through ``self``"""

    def loose_ends(self, cu=None, pads=None, pn=None, only: Optional[Sequence[int]] = None):
        """[(track index, end point)] for each track end that lies inside no copper of its net (a pad, a via,
        another track, its pour): KiCad joins items by their end points, so a track that only overlaps a pad's edge
        or rounded corner reads as unconnected there, and one ending on nothing is a dangling stub. ``only``: the
        tracks to test"""
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
        cu = self.copper() if cu is None else cu
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        import numpy as np
        import shapely
        items = [(L, pn.get((r, n)), g, i) for i, (r, n, L, g, d) in enumerate(pads)] + \
                [(L, v["net"], Point(v["at"]).buffer(v["dia"] / 2, 16), ("v", k))
                 for k, v in enumerate(self.vias) for L in self.layers] + \
                [(t["layer"], t["net"], self._item_shape(t), ("t", k)) for k, t in enumerate(self.tracks)]
        poured = {(pz["net"], pz["layer"]) for pz in self.pours}
        zones = {key: g for key, g in cu.items() if key in poured and g is not None and not g.is_empty}
        thru = {i for i, (r, n, L, g, d) in enumerate(pads) if d}
        ks = list(range(len(self.tracks)) if only is None else only)
        ends = [(k, e) for k in ks for e in (self.tracks[k]["pts"][0], self.tracks[k]["pts"][-1])]
        if not ends:
            return []
        good = np.zeros(len(ends), bool)
        pts = shapely.points(np.array([e for _, e in ends], float))
        if items:                                        # pads, vias, other tracks: one query for every end
            st = STRtree([g for _, _, g, _ in items])
            ia, ib = st.query(pts, predicate="dwithin", distance=0.005)
            for a, b in zip(ia.tolist(), ib.tolist()):
                if good[a]:
                    continue
                k = ends[a][0]
                t = self.tracks[k]
                L, n, _, tag = items[b]
                if n == t["net"] and tag != ("t", k) and (L == t["layer"] or tag in thru):
                    good[a] = True
        for (n, L), g in zones.items():                  # its pour (prepared: a big polygon, many ends)
            sel = np.array([not good[i] and self.tracks[k]["net"] == n and self.tracks[k]["layer"] == L
                            for i, (k, _) in enumerate(ends)])
            if sel.any():
                shapely.prepare(g)
                good[sel] |= shapely.dwithin(g, pts[sel], 0.005)
        return [(k, tuple(e)) for (k, e), ok in zip(ends, good) if not ok]

    VIA_AMPS_PER_MM = 1.0 / 0.3      # A per mm of drill: a 0.3 mm via, 25 um of plating, ~1 A (IPC-2152, a short
    #                                  barrel; a conservative figure, as the house calculators give for 10-20 C)

    def via_current(self, min_amps: float = 1.0) -> List[Tuple[str, List[Tuple[float, float]], float, float]]:
        """where a net carrying current changes layer through too few vias: for each net of at least ``min_amps``
        and no pour (a plane spreads it over its many drops), the vias that are the only way between two parts of
        its copper each holding a part on the net's power path (a connector, fuse, inductor, transistor, diode,
        relay or battery: a branch to a chip, a resistor or a capacitor draws its own share, unknown, and is not
        judged), whose drills together carry less than the net's current (``VIA_AMPS_PER_MM`` per mm of drill). [(net, via centres, their amps, the
        net's)]"""
        from shapely.geometry import Point
        out = []
        pads = self.pad_shapes()
        pn = self.pad_net()
        vias0 = list(self.vias)
        try:
            self.vias = []                               # (each layer's copper of each net without its vias, no
            cu = self.copper(pours=False)                #  pours: poured nets are not judged; the memo is keyed by
        finally:                                         #  the vias as well, so the board's own stays)
            self.vias = vias0
        poured = {pz["net"] for pz in self.pours}
        for net, d in self.nets.items():
            amps = d.get("amps") or 0.0
            vs = [v for v in self.vias if v["net"] == net]
            if amps < min_amps or not vs or net in poured:   # (a plane spreads the current over its many drops:
                continue                                      #  each carries its own pin's share, not the net's)
            isl = []                                     # (layer, polygon) islands of the net
            for L in self.layers:
                g = cu.get((net, L))
                if g is not None and not g.is_empty:
                    isl += [(L, q) for q in getattr(g, "geoms", [g])]
            if len(isl) < 2:
                continue
            parent = list(range(len(isl)))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def at(pt, L=None):
                return [k for k, (L_, q) in enumerate(isl) if (L is None or L_ == L) and q.intersects(pt)]
            for r, n, L, g, dr in pads:                  # (a plated hole joins the layers: no via needed there)
                if dr and pn.get((r, n)) == net:
                    ks = at(g.centroid)
                    for a, b in zip(ks, ks[1:]):
                        parent[find(a)] = find(b)
            links = {}                                   # (the vias between each pair of island groups: by the via's
            for v in vs:                                 #  ring, as copper joins - its centre may be just off a track)
                ks = at(Point(v["at"]).buffer(v["dia"] / 2 - 0.01, 12))
                for a, b in zip(ks, ks[1:]):
                    links.setdefault(tuple(sorted((find(a), find(b)))), []).append(v)
            pins = {}                                    # (the current-carrying pins on each island group)
            low_ = self.pin_loads(net)                   # (a pin given a small load draws no more than that)
            for r, n, L, g, dr in pads:
                if pn.get((r, n)) == net and not r.upper().startswith("C") and (r, n) not in low_:
                    for k in at(g.centroid, None if dr else L):
                        pins.setdefault(find(k), set()).add((r, n))
            for (a, b), lst in links.items():
                if a == b:
                    continue
                par = {k: k for k in set(find(k) for k in range(len(isl)))}   # (the net joined without these vias)

                def f2(x):
                    while par[x] != x:
                        x = par[x]
                    return x
                for (c, e), l2 in links.items():
                    if l2 is not lst and c != e:
                        par[f2(c)] = f2(e)
                if f2(a) == f2(b):
                    continue                             # (another way round: not the only way)
                side_a = {k for k in par if f2(k) == f2(a)}
                pa_ = set().union(*[pins.get(k, set()) for k in side_a])
                pb_ = set().union(*[pins.get(k, set()) for k in par if k not in side_a])
                power = lambda ps: any(re.match(r"(J|P|F|L|Q|K|BT|D)\d", r_.upper())
                                       and "LED" not in self.comps[r_]["fp"].upper() for r_, _ in ps)
                if not power(pa_) or not power(pb_):     # (the path of the net's current runs between connectors,
                    continue                             #  fuses, inductors, switches, diodes: a branch to a chip or a
                    #                                      resistor draws its own share, unknown, not judged here)
                cap = sum(v["drill"] * self.VIA_AMPS_PER_MM for v in lst)
                if cap + 1e-9 < amps:
                    out.append((net, [tuple(v["at"]) for v in lst], round(cap, 2), amps))
        return out

    # checks
    def islands(self, net: str, cu=None, pads=None, pn=None) -> List[List[Tuple[str, str]]]:
        """the pins of ``net`` grouped by the copper that joins them (each layer's pieces, joined at vias and plated
        holes), largest group first: one group when the net is whole"""
        from shapely.geometry import Point
        pads = self.pad_shapes() if pads is None else pads
        pn = self.pad_net() if pn is None else pn
        if cu is None and not any(pz["net"] == net for pz in self.pours):
            from shapely.ops import unary_union         # (no pour: its own copper is all there is, no need to carve
            own: Dict[str, list] = {}                    # the whole board's pours: the router asks after every net)
            for ref, num, L, g, _ in pads:
                if pn.get((ref, num)) == net:
                    own.setdefault(L, []).append(g)
            for t in self.tracks:
                if t["net"] == net:
                    own.setdefault(t["layer"], []).append(self._item_shape(t))
            for v in self.vias:
                if v["net"] == net:
                    for L in self.layers:
                        own.setdefault(L, []).append(self._item_shape(v))
            cu = {(net, L): unary_union(gs) for L, gs in own.items()}
        cu = self.copper() if cu is None else cu
        comps = []                                       # (layer, geometry) islands
        for L in self.layers:
            g = cu.get((net, L))
            if g is None or g.is_empty:
                continue
            for part in (getattr(g, "geoms", None) or [g]):
                comps.append((L, part))
        parent = list(range(len(comps)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        links = [Point(v["at"]) for v in self.vias if v["net"] == net] + \
                [g.centroid for ref, num, L, g, drill in pads if drill and pn.get((ref, num)) == net]
        from shapely.strtree import STRtree
        tree = STRtree([g for _, g in comps])            # (a tree query within 10 um, not a buffer of each pour per
        for pt in links:                                 # point: that was nine tenths of the check's time)
            hit = sorted(int(i) for i in tree.query(pt, predicate="dwithin", distance=0.01))
            for i in hit[1:]:
                parent[root(i)] = root(hit[0])
        where = {}
        for ref, num, L, g, _ in pads:
            if pn.get((ref, num)) != net:
                continue
            rp = g.representative_point()
            for i in tree.query(rp, predicate="intersects"):
                if comps[int(i)][0] == L:
                    where.setdefault((ref, num), set()).add(root(int(i)))
        groups, seen = {}, {}
        for rs in where.values():
            for r_ in rs:
                seen[r_] = seen.get(r_, 0) + 1
        for pin, rs in where.items():                   # a pin with several pads (a switch, a shield) joins through any
            groups.setdefault(max(rs, key=lambda r_: (seen[r_], -r_)), []).append(pin)
        return sorted(groups.values(), key=len, reverse=True)

    NECK_LEN = 1.5      # mm: a narrower track this short entering a pad smaller than the full width is a pin entry

    def _pin_neck(self, t, need, pads, pn) -> bool:
        """A short track narrower than its current needs, ending in a pad of its own net that is itself narrower than
        that width (a 2 A trace necking down into a 0.4 mm TSSOP pin): the pad limits the current there anyway, as in
        KiCad's and every router's pin entry, so it is not a narrow track."""
        from shapely.geometry import Point
        if t.get("neck"):                                 # (a via drop as wide as its pad: drop() marks it)
            return True
        L = sum(math.dist(a, b) for a, b in zip(t["pts"], t["pts"][1:]))
        if L > self.NECK_LEN:
            return False
        for end in (t["pts"][0], t["pts"][-1]):
            p = Point(end)
            for ref, num, lay, pg, _ in pads:
                if lay != t["layer"] or pn.get((ref, num)) != t["net"] or not pg.buffer(1e-3).contains(p):
                    continue
                mrr = pg.minimum_rotated_rectangle
                xs, ys = mrr.exterior.coords.xy
                sides = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(2)]
                if min(sides) < need - 1e-6:
                    return True
        return False

    def check(self) -> List[str]:
        import shapely
        import pcb
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import unary_union
        probs: List[str] = []
        cu = self.copper()
        pn = self.pad_net()
        pads = self.pad_shapes()
        for k in getattr(self, "keepouts", []):            # nothing copper in a keep-out
            z = Polygon(k["poly"])
            hit = sorted({n for (n, L), g in cu.items() if L in k["layers"] and g.intersection(z).area > 0.01})
            if hit:
                probs.append(f"copper of {', '.join(hit)} inside the keep-out{' (' + k['why'] + ')' if k['why'] else ''}")
        for n, d in self.nets.items():                      # every pin exists
            for ref, num in d["pins"]:
                if ref not in self.comps or num not in self.comps[ref]["pads"]:
                    probs.append(f"net {n}: {ref} has no pad {num!r} on its footprint {self.comps.get(ref, {}).get('fp')}")
        # connectivity: per net, union per layer, layers joined at vias and plated holes
        # (and again by KiCad's rule, which joins a track only where its end lies inside the other item)
        for n in self.nets:
            gs = self.islands(n, cu, pads, pn)
            how = ""
            if len(gs) <= 1:
                gs = self.anchor_groups(n, cu, pads, pn)
                how = " where track ends land (KiCad's rule; the copper itself overlaps)"
            if len(gs) > 1:
                gs = sorted(gs, key=len)
                probs.append(f"net {n} is split in {len(gs)}{how}: " +
                             " | ".join(", ".join(f"{r}.{p}" for r, p in g[:6]) for g in gs[:3]))
        # shorts / clearance between different nets on a layer (pad to pad inside one footprint is the maker's land
        # pattern: exempt)
        from shapely.geometry import LineString as _LS
        for L in self.layers:
            routed: Dict[str, list] = {}
            padg: Dict[str, Dict[str, list]] = {}
            pourg: Dict[str, list] = {}
            for t in self.tracks:
                if t["layer"] == L:
                    routed.setdefault(t["net"], []).append(self._item_shape(t))
            for v in self.vias:
                routed.setdefault(v["net"], []).append(self._item_shape(v))
            pcu = cu
            for pz in self.pours:
                if pz["layer"] == L:
                    g = pcu.get((pz["net"], L))
                    if g is not None:
                        pourg.setdefault(pz["net"], []).append(g.intersection(Polygon(pz["poly"], pz.get("holes") or None)))
            for ref, num, l, g, _ in pads:
                if l == L:
                    padg.setdefault(pn.get((ref, num), f"~{ref}.{num}"), {}).setdefault(ref, []).append(g)
            # every pair of copper items of different nets within the clearance, by bulk queries of STR trees (pad to
            # pad of one part exempt; a pour enters as its outline's segments, cheap to measure, plus a test for an
            # item lying wholly inside it); then each net pair's nearest offence is reported
            from shapely.strtree import STRtree
            import numpy as _npc
            el_g, el_n, el_r = [], [], []
            for n, lst in routed.items():
                for g in lst:
                    el_g.append(g), el_n.append(n), el_r.append(None)
            for n, by in padg.items():
                for ref, lst in by.items():
                    for g in lst:
                        el_g.append(g), el_n.append(n), el_r.append(ref)
            pieces = [(n, q) for n, lst in pourg.items() for g in lst for q in (getattr(g, "geoms", None) or [g])
                      if not q.is_empty and q.geom_type == "Polygon"]
            names = sorted(set(el_n) | {n for n, _ in pieces})
            ids = {n: i for i, n in enumerate(names)}
            unn = _npc.array([n.startswith("~") for n in names] or [False])
            g_s = _npc.array(el_g, dtype=object)
            n_s = _npc.array([ids[n] for n in el_n], dtype=int)
            rmap = {r: i for i, r in enumerate(sorted({r for r in el_r if r is not None}))}
            r_s = _npc.array([rmap[r] if r is not None else -1 for r in el_r], dtype=int)
            segs, n_seg = [], []
            for n, q in pieces:
                for ring in [q.exterior, *q.interiors]:
                    c = _npc.asarray(ring.coords)
                    if len(c) >= 2:
                        segs.append(shapely.linestrings(_npc.stack([c[:-1], c[1:]], axis=1)))
                        n_seg.append(_npc.full(len(c) - 1, ids[n]))
            g_p = _npc.concatenate(segs) if segs else _npc.array([], dtype=object)
            n_p = _npc.concatenate(n_seg) if n_seg else _npc.array([], dtype=int)
            worst: Dict[Tuple[str, str], Tuple[float, object, object]] = {}

            def note(ga_, gb_, na_, nb_):
                ok = (na_ != nb_) & ~(unn[na_] & unn[nb_])
                if not ok.any():
                    return
                ga_, gb_, na_, nb_ = ga_[ok], gb_[ok], na_[ok], nb_[ok]
                dd = shapely.distance(ga_, gb_)
                for k in _npc.argsort(dd).tolist():
                    key = (min(names[na_[k]], names[nb_[k]]), max(names[na_[k]], names[nb_[k]]))
                    if key not in worst:
                        worst[key] = (float(dd[k]), ga_[k], gb_[k])
                    elif dd[k] < worst[key][0]:
                        worst[key] = (float(dd[k]), ga_[k], gb_[k])
            reach = self.clear - 2e-3
            if len(g_s) >= 2:                            # item to item
                ia, ib = STRtree(g_s).query(g_s, predicate="dwithin", distance=reach)
                k_ = (ia < ib) & ~((r_s[ia] >= 0) & (r_s[ia] == r_s[ib]))
                note(g_s[ia[k_]], g_s[ib[k_]], n_s[ia[k_]], n_s[ib[k_]])
            if len(g_s) and len(g_p):                    # item to a pour's outline
                ia, ib = STRtree(g_p).query(g_s, predicate="dwithin", distance=reach)
                note(g_s[ia], g_p[ib], n_s[ia], n_p[ib])
                stree = STRtree(g_s)                      # item wholly inside another net's pour
                for n, q in pieces:
                    cand = stree.query(q, predicate="intersects")
                    cand = cand[(n_s[cand] != ids[n])] if len(cand) else cand
                    if len(cand):
                        note(_npc.array([q] * len(cand), dtype=object), g_s[cand],
                             _npc.full(len(cand), ids[n]), n_s[cand])
            box_ = [q.bounds for _, q in pieces]          # pour to pour of another net, where they come near
            for a in range(len(pieces)):
                for b2 in range(a + 1, len(pieces)):
                    (na, qa), (nb, qb) = pieces[a], pieces[b2]
                    ba, bb = box_[a], box_[b2]
                    if na == nb or ba[0] > bb[2] + reach or bb[0] > ba[2] + reach or ba[1] > bb[3] + reach or \
                            bb[1] > ba[3] + reach:
                        continue
                    if qa.intersects(qb):
                        note(_npc.array([qa], dtype=object), _npc.array([qb], dtype=object),
                             _npc.array([ids[na]]), _npc.array([ids[nb]]))
                    elif shapely.dwithin(qa, qb, reach):
                        note(_npc.array([qa], dtype=object), _npc.array([qb], dtype=object),
                             _npc.array([ids[na]]), _npc.array([ids[nb]]))
            from shapely.ops import nearest_points as _np2
            for (na, nb), (dist, ga, gb) in sorted(worst.items(), key=lambda kv: kv[0]):
                if dist < 1e-6:
                    x_, y_ = _np2(ga, gb)[0].coords[0] if not ga.intersects(gb) else \
                        ga.intersection(gb).representative_point().coords[0]
                else:
                    pa_, pb_ = _np2(ga, gb)              # (where they come nearest)
                    x_, y_ = (pa_.x + pb_.x) / 2, (pa_.y + pb_.y) / 2
                what = "short" if dist < 1e-6 else f"{dist:.2f} mm apart (clearance {self.clear})"
                probs.append(f"{L}.Cu: {na} and {nb} {what} at ({x_:.1f}, {y_:.1f})")
        for p_, n_, skew in getattr(self, "pairs", []):       # differential pairs kept matched in length
            lp, ln = self.net_length(p_), self.net_length(n_)
            if abs(lp - ln) > skew:
                probs.append(f"pair {p_} / {n_}: tracks {lp:.2f} and {ln:.2f} mm long, {abs(lp - ln):.2f} mm apart "
                             f"(keep within {skew} mm: lengthen the shorter, or route them side by side)")
        # current: tracks (each by the pins it serves: a branch to a low-current pin, its share) and pour necks
        branch: Dict[int, float] = {}
        for n in {t["net"] for t in self.tracks if t["amps"] is None}:
            if n in self.nets and self.pin_loads(n):
                branch.update(self.branch_amps(n, pads, pn, cu))
        for i_t, t in enumerate(self.tracks):
            d = self.nets.get(t["net"])
            if not d:
                probs.append(f"track on unknown net {t['net']}")
                continue
            amps = t["amps"] if t["amps"] is not None else branch.get(i_t, d["amps"])
            need = self.width_for(amps, t["layer"])
            if t["w"] + 1e-6 < need and not self._pin_neck(t, need, pads, pn):
                probs.append(f"track {t['net']} ({t['layer']}.Cu, {t['w']} mm) is too narrow for {amps} A on "
                             f"{self.oz_of(t['layer'])} oz copper: needs {need:.2f} mm for a {self.max_rise:g} C rise "
                             f"(IPC-2221)")
            L = sum(math.dist(a, b) for a, b in zip(t["pts"], t["pts"][1:]))
            drop = amps * pcb.CU_RHO * L / (t["w"] * self.oz_of(t["layer"]) * pcb.OZ_MM)
            if d["volts"] > 0 and drop > self.max_drop * d["volts"]:      # (a ground net has no voltage to drop)
                probs.append(f"track {t['net']} ({L:.0f} mm) drops {drop * 1000:.0f} mV at {amps} A, over "
                             f"{self.max_drop * 100:.0f} % of {d['volts']} V")
        for pz in self.pours:
            d = self.nets.get(pz["net"])
            g = cu.get((pz["net"], pz["layer"]))
            if not d or g is None or g.is_empty:
                continue
            from shapely.geometry import Polygon as _P
            g = g.intersection(_P(pz["poly"], pz.get("holes") or None))                # this zone's own fill
            amps = pz.get("amps") if pz.get("amps") is not None else d["amps"]
            need = self.width_for(amps, pz["layer"])
            if need <= 0.5:
                continue
            core = g.buffer(-need / 2, 8)                     # what survives when every neck narrower than need closes
            pins = [pg for ref, num, L, pg, _ in pads if L == pz["layer"] and pn.get((ref, num)) == pz["net"]]
            # the pieces each pad reaches, joined through the pad (one pad between two scraps is no neck)
            cs_ = getattr(core, "geoms", None) or [core]
            par_ = list(range(len(cs_)))

            def _rt(i):
                while par_[i] != i:
                    i = par_[i]
                return i
            touched = set()
            import numpy as _np_
            live = [k for k, part in enumerate(cs_) if not part.is_empty]
            hits_ = {}
            if live and pins:                            # (within reach, by distance: one query for every pin)
                from shapely.strtree import STRtree as _STn
                pi_, ci_ = _STn([cs_[k] for k in live]).query(pins, predicate="dwithin", distance=need / 2 + 0.5)
                for a_i, c_i in zip(pi_, ci_):
                    hits_.setdefault(int(a_i), []).append(live[int(c_i)])
            for ip_ in range(len(pins)):
                hit = sorted(hits_.get(ip_, []))
                for i in hit[1:]:
                    par_[_rt(i)] = _rt(hit[0])
                touched |= set(hit)
            if len({_rt(i) for i in touched}) > 1:
                from shapely.ops import nearest_points as _np
                big = {}
                for i in touched:                                            # each group's main piece
                    if _rt(i) not in big or cs_[i].area > cs_[big[_rt(i)]].area:
                        big[_rt(i)] = i
                a_, b_ = sorted(big.values(), key=lambda i: -cs_[i].area)[:2]  # (the neck: where the two main
                p_, q_ = _np(cs_[a_], cs_[b_])                                 # pieces come nearest)
                probs.append(f"pour {pz['net']} on {pz['layer']}.Cu near ({(p_.x + q_.x) / 2:.1f}, "
                             f"{(p_.y + q_.y) / 2:.1f}) has a neck narrower than {need:.1f} mm between its pads "
                             f"({amps} A, {self.oz_of(pz['layer'])} oz): widen it or add the other layer in parallel")
        # drills keep the hole gap apart, wall to wall (a via drilled into a pin's hole, or a web too thin to survive)
        from shapely.geometry import Point as _Pt
        drl = self.drill_list()                          # (each hole as its shape: a slot is not a round hole)
        holes_ = [(h["name"], _Pt(h["at"]), h["geom"], h) for h in drl]
        tight = []
        from shapely.strtree import STRtree as _STRh       # (the pairs within reach only: every pair was 0.4 M
        _ht = _STRh([g for _, _, g, _ in holes_]) if holes_ else None    # distances on a 56-part board)
        for i in range(len(holes_)):
            for j in sorted(int(j) for j in _ht.query(holes_[i][2], predicate="dwithin", distance=self.hole_gap)):
                if j <= i:
                    continue
                (na, pa, ga, ha), (nb, pb, gb, hb) = holes_[i], holes_[j]
                if not ha["slot"] and not hb["slot"]:    # (round ones exactly; a slot by its shape)
                    gap = pa.distance(pb) - ha["d"] / 2 - hb["d"] / 2
                else:
                    gap = ga.distance(gb) - (ga.intersection(gb).area ** 0.5 if ga.intersects(gb) else 0.0)
                if gap < self.hole_gap - 1e-6 and not (na == nb and pa.distance(pb) < 1e-6):
                    tight.append(f"{na} and {nb} drills {gap:.2f} mm apart at ({pa.x:.1f}, {pa.y:.1f}) "
                                 f"(keep {self.hole_gap} mm)")
        probs += tight[:20] + ([f"... and {len(tight) - 20} more drills too close"] if len(tight) > 20 else [])
        hc = max(self.clear, self.hole_clear)                 # copper keeps clear of every bare hole (KiCad's "hole
        bare = [h for h in drl if not h["plated"]]            # clearance"): a track over a peg hole is cut by the drill
        if bare and (self.tracks or self.vias):
            from shapely.strtree import STRtree as _STh
            items_ = self.tracks + self.vias
            shp = [self._item_shape(it) for it in items_]
            th = _STh(shp)
            for h in bare:
                (x, y), hg = h["at"], h["geom"]
                for i in th.query(hg, predicate="dwithin", distance=hc - 1e-6):
                    it = items_[int(i)]
                    gap = shp[int(i)].distance(hg)
                    probs.append(f"{'track' if 'pts' in it else 'via'} {it['net']} {max(gap, 0):.2f} mm from the bare "
                                 f"hole at ({x:.1f}, {y:.1f}) (keep {hc:.2f} mm)")
        if bare:                                              # and so does every pad: a mounting hole on a part's
            from shapely.strtree import STRtree as _STp           # pad cuts it (a footprint's own pegs beside its own
            pth = _STp([g for *_, g, _ in pads])                  # pads are the maker's design, not checked here)
            hit = set()
            for h in bare:
                (x, y), hg = h["at"], h["geom"]
                for i in pth.query(hg, predicate="dwithin", distance=hc - 1e-6):
                    ref, num, L, g, _ = pads[int(i)]
                    if ref == h.get("ref") or (ref, num, (x, y)) in hit:
                        continue
                    hit.add((ref, num, (x, y)))
                    probs.append(f"{ref} pad {num or '(mount)'} {max(g.distance(hg), 0):.2f} mm from the bare hole at "
                                 f"({x:.1f}, {y:.1f}) (keep {hc:.2f} mm)")
        inner = self.outline.buffer(-self.edge)               # copper keeps the edge gap from the routed edge
        for ref, num, L, g, _ in pads:
            if not inner.contains(g):
                probs.append(f"{ref} pad {num or '(mount)'} ({L}.Cu) reaches the board edge: copper keeps {self.edge} mm inside")
                break
        from shapely.geometry import LineString as _LS
        items = self.tracks + self.vias                  # (by distance to the edge, not a buffer per item)
        if items:
            import numpy as _npe
            axes = _npe.array([_LS(it["pts"]) if "pts" in it else _Pt(it["at"]) for it in items], dtype=object)
            rad = _npe.array([it["w"] / 2 if "pts" in it else it["dia"] / 2 for it in items])
            ok_ = shapely.contains(self.outline, axes) & ~shapely.dwithin(axes, self.outline.boundary,
                                                                          rad + self.edge - 1e-6)
        for it, ok1 in zip(items, ok_ if items else []):
            if not ok1:
                xy = it["pts"][0] if "pts" in it else it["at"]
                probs.append(f"{'track' if 'pts' in it else 'via'} {it['net']} near ({xy[0]:.1f}, {xy[1]:.1f}) comes "
                             f"within {self.edge} mm of the board edge")
        # every track end lands inside copper of its net (KiCad joins items by their end points)
        loose_ = [f"track {self.tracks[k]['net']} ({self.tracks[k]['layer']}.Cu) ends at ({e[0]:.2f}, {e[1]:.2f}) on "
                  f"nothing of its net (dangling, or only touching a pad's edge: KiCad reads it unconnected)"
                  for k, e in self.loose_ends(cu, pads, pn)]
        probs += loose_[:20] + ([f"... and {len(loose_) - 20} more loose track ends"] if len(loose_) > 20 else [])
        return probs + self.check_placement()

    def check_placement(self) -> List[str]:
        """the placement alone, in a moment, before routing: every courtyard on the board (unless the part is
        placed to overhang, like a USB connector at the edge), no two courtyards on one side overlapping, and every
        pin a net names on a part that exists with that pad. check() includes it"""
        from shapely.strtree import STRtree
        probs: List[str] = []
        for n, d in self.nets.items():
            for ref, num in d["pins"]:
                if ref not in self.comps:
                    probs.append(f"net {n}: no part {ref}")
                elif num not in self.comps[ref]["pads"]:
                    probs.append(f"net {n}: {ref} ({self.comps[ref]['fp'].split(':')[1]}) has no pad {num}")
        cy = {}
        for ref, c in self.comps.items():
            if c.get("unplaced"):
                probs.append(f"{ref} has no position yet (autoplace(board) or place_and_route(board) places it)")
                continue
            g = self.courtyard(ref)
            if g is None:
                continue
            cy[ref] = g
            if not self.outline.contains(g) and not c["overhang"]:
                probs.append(f"{ref} ({c['fp'].split(':')[1]}) hangs off the board outline")
        refs = list(cy)
        tree = STRtree([cy[r] for r in refs]) if refs else None
        for i in range(len(refs)):
            for j in sorted(int(j) for j in tree.query(cy[refs[i]])):
                if j <= i:
                    continue
                if self.comps[refs[i]].get("side", "F") != self.comps[refs[j]].get("side", "F"):
                    continue                                 # top and bottom parts may overlap in plan
                if cy[refs[i]].intersection(cy[refs[j]]).area > 1e-3:     # (KiCad's DRC flags any overlap: a
                    probs.append(f"courtyards of {refs[i]} and {refs[j]} overlap")   # 0.03 mm strip is one)
        if tree is not None:                                 # a mounting hole is drilled through whatever is there
            from shapely.geometry import Point as _Ph
            for x, y, d in self.holes:
                hg = _Ph(x, y).buffer(d / 2, 16)
                for j in sorted(int(j) for j in tree.query(hg)):
                    if cy[refs[j]].intersection(hg).area > 0.05:
                        probs.append(f"the mounting hole at ({x:.1f}, {y:.1f}) is inside the courtyard of {refs[j]}")
        if any(c.get("symbol") for c in self.comps.values()):  # (every symbol pin has a pad, every pad a pin)
            import parts
            probs += parts.pin_check(self)
        return probs
