"""Placement: the parts the netlist says belong together (``implied_near``), the auto-placer, the requirements'
"within d mm", and ``place_and_route`` (several placements tried, in parallel, each routed and checked). Split
from ``pcb``; ``pcb.<name>`` still reaches every function here.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence

import pcb
from pcb import (Board, GROUND_NET, _progress, footprint, live, live_copper, live_due, live_metrics, severity)
from pcb_route import (_emit, mark_user_copper, route_board)


def _chips(board) -> set:
    """the parts a capacitor, crystal, inductor or divider is held to: a chip (six pads or more), and a small one (a
    U or IC with three pads or more: an LDO in SOT-23-5, a reference, a load switch)"""
    return {r for r, c in board.comps.items()
            if len(c["pads"]) >= 6 or (re.match(r"^(U|IC)\d", r, re.I) and len([n for n in c["pads"] if n]) >= 3)}


def _pin_name(c: dict, num: str) -> str:
    sym = c.get("symbol")
    if not sym:
        return ""
    return next((q.get("name", "") for q in sym.get("pins", []) if q["num"] == num), "")


def implied_near(board, refs=None) -> List[dict]:
    """the parts the netlist says belong together, though no requirement names them: each a dict of ``ref`` and its
    ``pin`` held within ``limit`` mm of ``to`` (a chip's pin), with the ``kind`` and ``why``. The placer holds them by
    springs and the critic checks them:

    - decoupling: a two-pin capacitor between a supply and ground belongs by one of that supply's chip pins (a
      symbol's power input, or a regulator's output); the netlist does not say which, so the capacitors are dealt
      over those pins in turn, 3 mm;
    - crystal: a crystal by its chip's oscillator pin, 5 mm (its load capacitors follow as decoupling of that net);
    - switch node: a switching regulator's inductor by its SW / LX pin, 4 mm (the loop that radiates);
    - feedback: the divider's resistor by the FB pin, 4 mm (the node that picks up noise).

    ``refs``: only these parts (the placer's moving ones); a part a requirement places (``board.near``) is left to it"""
    pnet = board.pad_net()
    poured = {pz["net"] for pz in board.pours}
    ground = lambda n: bool(n) and (n in poured or bool(GROUND_NET.match(n)))
    chips = _chips(board)
    told = {x.partition(".")[0] for a_, b_, _ in getattr(board, "near", []) for x in (a_, b_)}
    pool = set(board.comps) if refs is None else set(refs)
    out: List[dict] = []
    deal: Dict[str, int] = {}
    two = lambda c: len([n for n in c["pads"] if n]) == 2
    for r in sorted(pool, key=lambda r: (len(r), r)):
        c = board.comps[r]
        if r in told or not r.upper().startswith("C") or not two(c):
            continue
        ns = {num: pnet.get((r, num)) for num in c["pads"] if num}
        sup = [(num, n) for num, n in ns.items() if n and not ground(n)]
        if len(sup) != 1 or not any(ground(n) for n in ns.values()) or sup[0][1] not in board.nets:
            continue
        num, n = sup[0]
        targets = [tuple(p_) for p_ in board.nets[n]["pins"] if p_[0] in chips and p_[0] != r
                   and _decap_target(board.comps[p_[0]], p_[0], p_[1]) is not False]
        if not targets:
            continue
        k = deal.get(n, 0)
        deal[n] = k + 1
        tgt = targets[k % len(targets)]
        tht = any(p_.get("drill") for p_ in board.comps[tgt[0]]["pads"].get(tgt[1], []))
        if _decap_target(board.comps[tgt[0]], *tgt):    # (one dealt to a connector's pin is left free: the chip's
            out.append({"ref": r, "pin": num, "to": tgt, "limit": 5.0 if tht else 3.0, "kind": "decoupling",   # own stay as many;
            #                                              a through-hole pin's (a TO-220's) pads stand further apart)
                        "why": f"{r} decouples {tgt[0]}.{tgt[1]} ({n})"})
    for r in sorted(pool):
        c = board.comps[r]
        if r in told:
            continue
        if re.match(r"^(Y|X)\d", r):                     # a crystal held to its IC's oscillator pins
            for num in c["pads"]:
                n = pnet.get((r, num))
                if not n or ground(n) or n not in board.nets:
                    continue
                ic_pins = [tuple(p_) for p_ in board.nets[n]["pins"] if p_[0] in chips and p_[0] != r]
                if ic_pins:
                    out.append({"ref": r, "pin": num, "to": ic_pins[0], "limit": 5.0, "kind": "crystal",
                                "why": f"crystal {r} by {ic_pins[0][0]}'s oscillator pin ({n})"})
        elif re.match(r"^L\d", r, re.I) and two(c):     # an inductor by the switch pin it is on
            for num in c["pads"]:
                n = pnet.get((r, num))
                if not n or n not in board.nets:
                    continue
                sw = [tuple(p_) for p_ in board.nets[n]["pins"] if p_[0] in chips and p_[0] != r
                      and re.match(r"^(SW|LX|PH|PHASE|SWITCH|L)\d*$", _pin_name(board.comps[p_[0]], p_[1]), re.I)]
                if sw:
                    out.append({"ref": r, "pin": num, "to": sw[0], "limit": 4.0, "kind": "switch node",
                                "why": f"inductor {r} by {sw[0][0]}'s switch pin ({n})"})
                    break
        elif re.match(r"^R\d", r, re.I) and two(c):     # a divider's resistor by the FB pin it sets
            for num in c["pads"]:
                n = pnet.get((r, num))
                if not n or n not in board.nets:
                    continue
                fb = [tuple(p_) for p_ in board.nets[n]["pins"] if p_[0] in chips and p_[0] != r
                      and re.match(r"^(V?FB|ADJ|FEEDBACK|VSENSE)\d*$", _pin_name(board.comps[p_[0]], p_[1]), re.I)]
                if fb:
                    out.append({"ref": r, "pin": num, "to": fb[0], "limit": 4.0, "kind": "feedback",
                                "why": f"{r} sets {fb[0][0]}'s feedback ({n})"})
                    break
    return out


def _decap_target(c: dict, ref: str, num: str):
    """a pin a decoupling capacitor belongs by: a chip's supply pin (a symbol's power input). A connector's pin is
    None: it takes its turn in the deal (so the chip's pins get as many as before) but holds no capacitor; a
    symbol's other pins are False, out of the deal"""
    if re.match(r"^(J|P|CN|CON)\d", ref, re.I):
        return None
    sym = c.get("symbol")
    if sym and not sym.get("auto"):                      # (a generic symbol says nothing of a chip's supply pins)
        return any(q["num"] == num and q["type"] in ("power_in", "power_out") for q in sym["pins"])
    return True


def autoplace(board: Board, move: Optional[Sequence[str]] = None, gap: float = 1.0, seed: int = 0,
              effort: float = 1.0, rotations: Sequence[int] = (0, 90, 180, 270)) -> List[str]:
    """place parts for you: a short total of wire (each net's half-perimeter, ground and other poured nets weighted
    low, high-current nets high) with every courtyard on the board, ``gap`` mm clear of the others on its side, the
    mounting holes and the keep-outs. ``move``: the parts to place (default: every part placed without a position,
    ``at=None``; if there are none, every part not placed to overhang the edge). The rest stay where they are:
    put connectors, buttons and mounting holes where the enclosure needs them first, then call this. It places the
    parts one at a time, most connected first, each where it adds the least wire, then improves the whole by
    annealing (moves, turns, swaps; ``effort`` scales the time spent). Returns the parts it found no room for.
    Then route: ``autoroute(board)``."""
    import numpy as np
    import shapely
    from shapely.geometry import box
    rng = np.random.default_rng(seed)
    if move is None:
        move = [r for r, c in board.comps.items() if c.get("unplaced")] or \
               [r for r, c in board.comps.items() if not c["overhang"]]
    move = [r for r in move if r in board.comps]
    mset = set(move)
    rots = [int(r) % 360 for r in rotations] or [0]
    inner = board.outline.buffer(-0.05)
    shapely.prepare(inner)

    def rect(ref, rot):                                  # courtyard box (footprint frame, side applied), turned
        c = board.comps[ref]
        crt = footprint(c["fp"])["courtyard"]
        if not crt:
            xs = [p["at"][0] for l in c["pads"].values() for p in l] or [0.0]
            ys = [p["at"][1] for l in c["pads"].values() for p in l] or [0.0]
            crt = (min(xs) - 0.5, min(ys) - 0.5, max(xs) + 0.5, max(ys) + 0.5)
        if c.get("side", "F") == "B":
            crt = (-crt[2], crt[1], -crt[0], crt[3])
        x0, y0, x1, y1 = crt
        cs = [(sum(p["at"][0] for p in l) / len(l), sum(p["at"][1] for p in l) / len(l)) for l in c["pads"].values() if l]
        if len(cs) >= 6:                                 # a fine-pitch part: room round it to fan out
            a_ = np.asarray(cs)
            d_ = np.hypot(*(a_[:, None, :] - a_[None, :, :]).transpose(2, 0, 1))
            np.fill_diagonal(d_, np.inf)
            if d_.min() < 0.8:                           # (more pins, more tracks to fan out: a QFN-56 ~1.9 mm)
                m_ = min(2.0, 0.75 + 0.02 * len(cs))
                x0, y0, x1, y1 = x0 - m_, y0 - m_, x1 + m_, y1 + m_
        corners = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        t = math.radians(rot)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        q = corners @ R.T
        return q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()

    def pads_at(ref, rot):                               # {pad number: centre offset} turned
        t = math.radians(rot)
        ct, st = math.cos(t), math.sin(t)
        return {n: (sum(p["at"][0] for p in l) / len(l) * ct - sum(p["at"][1] for p in l) / len(l) * st,
                    sum(p["at"][0] for p in l) / len(l) * st + sum(p["at"][1] for p in l) / len(l) * ct)
                for n, l in board.comps[ref]["pads"].items() if l}
    R_ = {(r, a): rect(r, a) for r in move for a in rots}
    P_ = {(r, a): pads_at(r, a) for r in board.comps for a in (rots if r in move else [board.comps[r]["rot"] % 360])}
    poured = {p["net"] for p in board.pours}
    # decoupling: a two-pin capacitor between a supply and a poured (ground) net belongs by one of that supply's IC
    # pins; the netlist does not say which, so the caps are dealt out over those pins in turn and each is held to its
    # pin by a stiff spring (a pseudo-net of the two pins)
    NETS = dict(board.nets)
    pnet = board.pad_net()
    many = {r for r, c in board.comps.items() if len(c["pads"]) >= 6}
    for it in implied_near(board, refs=mset):           # the parts the netlist says belong together (decoupling by its
        NETS[f"~{it['kind']} {it['ref']}.{it['pin']}"] = {   # pin, a crystal by its chip, an inductor by the switch pin,
            "pins": [(it["ref"], it["pin"]), it["to"]], "amps": 5.0, "limit": it["limit"]}   # a divider by FB): springs
    for a0, b0, dmax in getattr(board, "near", []):     # the requirements' "within d mm of": a spring that turns
        ta, tb = near_target(board, a0), near_target(board, b0)   # stiff past d (pins on a shared net, else the
        if not ta or not tb:                                      # centres; "U1.9": that pin)
            continue
        a_, b_ = ta[0], tb[0]
        pas = ta[1] or list(board.comps[a_]["pads"])
        pbs = tb[1] or list(board.comps[b_]["pads"])
        shared = [(pa, pb) for pa in pas for pb in pbs
                  if pa and pb and pnet.get((a_, pa)) and pnet.get((a_, pa)) == pnet.get((b_, pb))]
        if not shared and (ta[1] or tb[1]):              # (a pin named: it, whatever its net)
            shared = [(pas[0], pbs[0])]
        shared.sort(key=lambda q: pnet.get((a_, q[0])) in poured)
        pa, pb = shared[0] if shared else (None, None)
        def _half(ref, num):                             # (the pad's smaller half-size: centre to its edge)
            ps = board.comps[ref]["pads"].get(num) or []
            return min(min(p_["size"]) for p_ in ps) / 2 if ps else 0.0
        # the requirement is pad to pad; the spring measures pin centres: the pads' half-sizes are allowed for
        lim_ = dmax + _half(a_, pa) + _half(b_, pb) - 0.15
        NETS[f"~near {a0} {b0}"] = {"pins": [(a_, pa), (b_, pb)], "amps": 5.0, "limit": max(0.5, lim_)}
    wt = {n: (0.15 if n in poured else 1.0) * (1.0 + min(d.get("amps", 0.1), 5.0) / 2.0) for n, d in NETS.items()}
    nets_of: Dict[str, List[str]] = {}
    for n, d in NETS.items():
        for r, _ in d["pins"]:
            nets_of.setdefault(r, [])
            if n not in nets_of[r]:
                nets_of[r].append(n)
    pos = {r: (c["at"][0], c["at"][1], c["rot"] % 360) for r, c in board.comps.items()}
    placed = {r for r in board.comps if r not in move}
    side = {r: board.comps[r].get("side", "F") for r in board.comps}
    # what a moved part keeps clear of: the fixed parts' courtyards, the holes, the keep-outs
    fixed_obs = {"F": [], "B": []}
    for r in placed:
        g = board.courtyard(r)
        if g is not None:
            fixed_obs[side[r]].append(g.bounds)
    for x, y, d in board.holes:
        k = getattr(board, "_hole_ko", {}).get((x, y), d / 2 + 0.6)
        for L in "FB":
            fixed_obs[L].append((x - k, y - k, x + k, y + k))
    for k in getattr(board, "keepouts", []):
        bx = shapely.geometry.Polygon(k["poly"]).bounds
        for L in "FB":
            if L in k["layers"]:
                fixed_obs[L].append(bx)
    for t in board.tracks:                               # and the copper the script drew (a hand-drawn feed): a
        if t.get("fan") or t["layer"] not in fixed_obs:  # part placed on it would short to it; as small boxes
            continue                                     # along each segment (a diagonal is no one box)
        h_ = t["w"] / 2
        for (xa, ya), (xb, yb) in zip(t["pts"], t["pts"][1:]):
            n_ = max(1, int(math.ceil(math.dist((xa, ya), (xb, yb)) / 0.5)))
            for i_ in range(n_):
                x0_, y0_ = xa + (xb - xa) * i_ / n_, ya + (yb - ya) * i_ / n_
                x1_, y1_ = xa + (xb - xa) * (i_ + 1) / n_, ya + (yb - ya) * (i_ + 1) / n_
                fixed_obs[t["layer"]].append((min(x0_, x1_) - h_, min(y0_, y1_) - h_,
                                              max(x0_, x1_) + h_, max(y0_, y1_) + h_))

    def box_of(r, x, y, a):
        x0, y0, x1, y1 = R_[(r, a)]
        return x + x0, y + y0, x + x1, y + y1

    def clear(r, b_, others):
        g = gap - 1e-6
        for o in others:
            if b_[0] < o[2] + g and o[0] < b_[2] + g and b_[1] < o[3] + g and o[1] < b_[3] + g:
                return False
        return True

    convex = inner.convex_hull.area - inner.area < 1e-6
    rect_ = convex and abs(box(*inner.bounds).area - inner.area) < 1e-6
    ib = inner.bounds

    def inside(b_):                                       # (a convex board holds a box when it holds its corners)
        if rect_:
            return ib[0] <= b_[0] and ib[1] <= b_[1] and b_[2] <= ib[2] and b_[3] <= ib[3]
        if convex:
            return bool(shapely.contains_xy(inner, [b_[0], b_[2], b_[2], b_[0]], [b_[1], b_[1], b_[3], b_[3]]).all())
        return inner.contains(box(*b_))

    def legal(r, x, y, a, skip=()):
        b_ = box_of(r, x, y, a)
        if not inside(b_):
            return False
        others = fixed_obs[side[r]] + [box_of(o, *pos[o]) for o in placed if o in mset and o != r and o not in skip
                                         and side[o] == side[r]]
        return clear(r, b_, others)

    def pin_xy(r, num):
        x, y, a = pos[r]
        dx, dy = P_[(r, a)].get(num, (0.0, 0.0))
        return x + dx, y + dy

    def net_cost(n, only_placed=True):
        pts = [pin_xy(r, num) for r, num in NETS[n]["pins"] if r in board.comps and (r in placed or not only_placed)]
        if len(pts) < 2:
            return 0.0
        a = np.asarray(pts)
        hp = a[:, 0].max() - a[:, 0].min() + a[:, 1].max() - a[:, 1].min()
        if len(a) <= 40:                                 # + each pin's way to its nearest pin: a decoupling cap
            d = np.abs(a[:, None, :] - a[None, :, :]).sum(-1)    # sits by a pin, not just inside the net's box
            np.fill_diagonal(d, np.inf)
            hp += 0.5 * d.min(1).sum()
        lim = NETS[n].get("limit")
        if lim is not None and len(a) == 2:              # (a "within d mm" spring: stiff past its limit)
            hp += 30.0 * max(0.0, float(np.hypot(*(a[0] - a[1]))) - lim)
        return wt[n] * hp

    # 1. one at a time, most connected first, each where it adds the least wire
    minx, miny, maxx, maxy = board.outline.bounds
    step = max(0.25, min(maxx - minx, maxy - miny) / 60)
    gx, gy = np.meshgrid(np.arange(minx, maxx + 1e-9, step), np.arange(miny, maxy + 1e-9, step))
    gx, gy = gx.ravel(), gy.ravel()
    cx, cy = board.outline.centroid.x, board.outline.centroid.y
    order = sorted(move, key=lambda r: -sum(len(NETS[n]["pins"]) * wt[n] for n in nets_of.get(r, []))
                   - 0.5 * len(board.comps[r]["pads"]))
    partners: Dict[str, List[str]] = {}                  # (a part held near another goes right after it: a
    for m, dm in NETS.items():                           # cluster, placed before the room round its chip fills)
        if dm.get("limit") is not None and len(dm["pins"]) == 2:
            (ra_, _), (rb_, _) = dm["pins"]
            partners.setdefault(rb_, []).append(ra_)
    seq, seen_ = [], set()

    def _emit(r):
        if r in seen_ or r not in mset:
            return
        seen_.add(r)
        seq.append(r)
        for q in sorted(partners.get(r, []), key=lambda q: -len(board.comps[q]["pads"])):
            _emit(q)
    for r in order:
        _emit(r)
    for r in list(partners):                             # (a fixed chip's partners come first of all)
        if r not in mset:
            for q in partners[r]:
                if q in mset and q not in seen_:
                    seq.insert(0, q)
                    seen_.add(q)
    order = seq + [r for r in order if r not in seen_]
    failed = []
    for r in order:
        best = None
        for gap_try, a in [(gt, a) for gt in dict.fromkeys((gap, min(gap, 0.3))) for a in rots]:
            if best is not None and gap_try != gap:
                break                                    # (only a part with no room at the full gap squeezes in)
            x0, y0, x1, y1 = R_[(r, a)]
            bxs = np.stack([gx + x0, gy + y0, gx + x1, gy + y1], axis=1)
            ok = shapely.contains(inner, shapely.box(bxs[:, 0], bxs[:, 1], bxs[:, 2], bxs[:, 3]))
            others = np.array(fixed_obs[side[r]] + [box_of(o, *pos[o]) for o in placed if o in mset and side[o] == side[r]])
            if len(others):
                g = gap_try - 1e-6
                hit = ((bxs[:, None, 0] < others[None, :, 2] + g) & (others[None, :, 0] < bxs[:, None, 2] + g) &
                       (bxs[:, None, 1] < others[None, :, 3] + g) & (others[None, :, 1] < bxs[:, None, 3] + g)).any(1)
                ok &= ~hit
            if not ok.any():
                continue
            cost = 0.002 * (np.abs(gx - cx) + np.abs(gy - cy))     # (with nothing to pull it, the middle)
            for n in nets_of.get(r, []):
                mine = [P_[(r, a)][num] for rr, num in NETS[n]["pins"] if rr == r and num in P_[(r, a)]]
                oth = [pin_xy(rr, num) for rr, num in NETS[n]["pins"] if rr != r and rr in placed]
                if not mine:
                    continue
                mx = np.array([m[0] for m in mine])
                my = np.array([m[1] for m in mine])
                lo_x = gx + mx.min()
                hi_x = gx + mx.max()
                lo_y = gy + my.min()
                hi_y = gy + my.max()
                if oth:
                    ox, oy = [o[0] for o in oth], [o[1] for o in oth]
                    lo_x, hi_x = np.minimum(lo_x, min(ox)), np.maximum(hi_x, max(ox))
                    lo_y, hi_y = np.minimum(lo_y, min(oy)), np.maximum(hi_y, max(oy))
                cost = cost + wt[n] * (hi_x - lo_x + hi_y - lo_y)
            cost = np.where(ok, cost, np.inf)
            i = int(np.argmin(cost))
            if best is None or cost[i] < best[0]:
                best = (cost[i], gx[i], gy[i], a)
        if best is None:
            failed.append(r)
            continue
        pos[r] = (float(best[1]), float(best[2]), best[3])
        placed.add(r)
        live("place", ref=r, at=[round(pos[r][0], 3), round(pos[r][1], 3)], rot=pos[r][2], side=side[r])
    # 2. anneal: move, turn or swap a part; keep what shortens the wire (and, early on, some of what does not)
    mov = [r for r in move if r in placed]
    if len(mov) >= 1:
        total = sum(net_cost(n) for n in NETS)
        iters = int(effort * 400 * len(mov) * max(1, len(rots)))
        T0 = max(total / max(len(NETS), 1) * 0.3, 0.1)
        span = max(maxx - minx, maxy - miny)
        for k in range(iters):
            frac = k / max(iters, 1)
            T = T0 * (1 - frac) ** 2 + 1e-4
            r = mov[int(rng.integers(len(mov)))]
            kind = rng.random()
            old = {r: pos[r]}
            if kind < 0.15 and len(mov) > 1:              # swap with a part of a like size
                o = mov[int(rng.integers(len(mov)))]
                if o == r or side[o] != side[r]:
                    continue
                old[o] = pos[o]
                pos[r], pos[o] = (old[o][0], old[o][1], old[r][2]), (old[r][0], old[r][1], old[o][2])
            elif kind < 0.3:                              # turn
                pos[r] = (old[r][0], old[r][1], rots[int(rng.integers(len(rots)))])
            else:                                         # move, less far as it cools
                sc = span * (0.3 * (1 - frac) + 0.02)
                pos[r] = (round((old[r][0] + rng.normal(0, sc)) / 0.05) * 0.05,
                          round((old[r][1] + rng.normal(0, sc)) / 0.05) * 0.05, old[r][2])
            if not all(legal(q, *pos[q], skip=()) for q in old):
                pos.update(old)
                continue
            touched = sorted({n for q in old for n in nets_of.get(q, [])})   # (sorted: the same sum every run)
            new_c = sum(net_cost(n) for n in touched)
            saved = dict(pos)
            pos.update(old)
            old_c = sum(net_cost(n) for n in touched)
            pos.update(saved)
            d = new_c - old_c
            if d > 0 and rng.random() >= math.exp(-d / T):
                pos.update(old)
            if live_due("anneal", 0.25):                 # (a few frames a second of the annealer at work)
                live("places", phase="anneal", frac=round(frac, 3),
                     places={q: [round(pos[q][0], 3), round(pos[q][1], 3), pos[q][2], side[q]] for q in mov})
    # 3. settle: a part still further than its "within d mm" (a requirement, a decoupling cap) goes to the
    # cheapest legal spot round its partner's pin within that distance, if there is one
    mov_set = set(mov) if len(mov) >= 1 else set()
    gap0, gap = gap, min(gap, 0.5)                       # (legal() reads it: a held part may sit closer)
    for n, d_ in NETS.items():
        lim = d_.get("limit")
        if lim is None or len(d_["pins"]) != 2:
            continue
        (ra, pa), (rb, pb) = d_["pins"]
        if ra not in mov_set or rb not in placed:
            continue
        if math.dist(pin_xy(ra, pa), pin_xy(rb, pb)) <= lim:
            continue
        tx, ty = pin_xy(rb, pb)
        best_ = None
        here = pos[ra]
        for a in rots:
            ox_, oy_ = P_[(ra, a)].get(pa, (0.0, 0.0))
            for rr in np.arange(0.0, lim + 1e-9, 0.25):
                for th in np.linspace(0, 2 * math.pi, max(8, int(rr * 12)), endpoint=False):
                    x, y = round((tx + rr * math.cos(th) - ox_) / 0.05) * 0.05, round((ty + rr * math.sin(th) - oy_) / 0.05) * 0.05
                    if not legal(ra, x, y, a):
                        continue
                    pos[ra] = (x, y, a)
                    c_ = sum(net_cost(m) for m in nets_of.get(ra, []))
                    if best_ is None or c_ < best_[0]:
                        best_ = (c_, pos[ra])
                    pos[ra] = here
        if best_ is None:                                # no room: take a spot from parts no rule holds, and
            held = {q for m, dm in NETS.items() if dm.get("limit") is not None for q, _ in dm["pins"]}
            ox_, oy_ = P_[(ra, here[2])].get(pa, (0.0, 0.0))     # place them again where they cost the least
            for rr in np.arange(0.0, lim + 1e-9, 0.25):
                for th in np.linspace(0, 2 * math.pi, max(8, int(rr * 12)), endpoint=False):
                    x, y = round((tx + rr * math.cos(th) - ox_) / 0.05) * 0.05, round((ty + rr * math.sin(th) - oy_) / 0.05) * 0.05
                    b_ = box_of(ra, x, y, here[2])
                    if not inside(b_) or not clear(ra, b_, fixed_obs[side[ra]]):
                        continue
                    g_ = gap - 1e-6
                    blk = [o for o in sorted(placed) if o in mset and o != ra and side[o] == side[ra] and
                           (lambda o_: b_[0] < o_[2] + g_ and o_[0] < b_[2] + g_ and b_[1] < o_[3] + g_ and
                            o_[1] < b_[3] + g_)(box_of(o, *pos[o]))]
                    if not blk or len(blk) > 2 or any(o in held for o in blk):
                        continue
                    keep_ = {o: pos[o] for o in blk}
                    pos[ra] = (x, y, here[2])
                    for o in blk:
                        pos[o] = (1e6, 1e6, pos[o][2])   # (off the board while it looks for a spot)
                    ok_ = True
                    for o in blk:
                        bo = None
                        for a in rots:
                            for j in range(0, len(gx), 2):
                                if not legal(o, gx[j], gy[j], a):
                                    continue
                                pos[o] = (float(gx[j]), float(gy[j]), a)
                                c_ = sum(net_cost(m) for m in nets_of.get(o, []))
                                if bo is None or c_ < bo[0]:
                                    bo = (c_, pos[o])
                                pos[o] = (1e6, 1e6, a)
                        if bo is None:
                            ok_ = False
                            break
                        pos[o] = bo[1]
                    if ok_:
                        best_ = (0.0, pos[ra])
                        break
                    pos.update(keep_)
                    pos[ra] = here
                if best_ is not None:
                    break
        if best_ is not None:
            pos[ra] = best_[1]
    gap = gap0                                           # (lined up at the full gap: the routing's room kept)
    _align(board, [r for r in mov if r not in many], pos, rots, legal, net_cost, nets_of)
    for r in move:
        if r in placed:
            c = board.comps[r]
            c["at"], c["rot"] = (round(pos[r][0], 3), round(pos[r][1], 3)), pos[r][2]
            c.pop("unplaced", None)
    board.__dict__.pop("_pads_memo", None)
    live("places", phase="final", places={r: [board.comps[r]["at"][0], board.comps[r]["at"][1], board.comps[r]["rot"],
                                              board.comps[r].get("side", "F")] for r in move if r in placed},
         failed=failed)
    return failed


def _align(board: Board, small: Sequence[str], pos: dict, rots, legal, net_cost, nets_of, grid: float = 0.5,
           tol: float = 1.5) -> None:
    """autoplace's last step, a tidy board: the small parts (``small``) of one footprint turned the same way, each
    on a ``grid`` mm grid, and a run of them side by side lined up in a row (or a column) at even gaps; each change
    kept only where it is legal and costs at most ``tol`` mm of wire a part"""
    def cost(rs):
        return sum(net_cost(n) for n in sorted({n for r in rs for n in nets_of.get(r, [])}))

    def take(ch):                                        # {ref: (x, y, a)}: kept if legal and nearly free
        old = {r: pos[r] for r in ch}
        c0 = cost(ch)
        pos.update(ch)
        if all(legal(r, *pos[r]) for r in ch) and cost(ch) <= c0 + tol * len(ch) + 1e-9:
            return True
        pos.update(old)
        return False
    g = lambda v: round(v / grid) * grid
    side = lambda r: board.comps[r].get("side", "F")
    groups: Dict[tuple, List[str]] = {}
    for r in sorted(small):
        groups.setdefault((board.comps[r]["fp"], side(r)), []).append(r)
    for rs in groups.values():
        # 1. one way round: the group's usual axis, each part turned to it where it is, else a grid step or a few off
        axis = int(sum(pos[r][2] % 180 == 0 for r in rs) * 2 < len(rs)) * 90
        for r in rs:
            if pos[r][2] % 180 != axis:
                x, y, _ = pos[r]
                for p in sorted(((g(x) + i * grid, g(y) + j * grid) for i in range(-4, 5) for j in range(-4, 5)),
                                key=lambda p: (math.hypot(p[0] - x, p[1] - y), p)):
                    if any(take({r: (p[0], p[1], a)}) for a in rots if a % 180 == axis):
                        break
        # 2. on the grid: the nearest grid point, else the others of its cell
        for r in rs:
            x, y, a = pos[r]
            for p in sorted({(gx_, gy_) for gx_ in (g(x), g(x) + grid, g(x) - grid) for gy_ in (g(y), g(y) + grid, g(y) - grid)},
                            key=lambda p: (abs(p[0] - x) + abs(p[1] - y), p)):
                if abs(p[0] - x) <= grid and abs(p[1] - y) <= grid and take({r: (p[0], p[1], a)}):
                    break
        # 3. rows and columns: neighbours (one apart a little across, a few mm along) on one line, evenly spaced
        for k in (1, 0):                                 # (k: the coordinate shared; 1 a row, 0 a column)
            runs, seen = [], set()
            for r in rs:
                if r in seen:
                    continue
                run, todo = [], [r]
                while todo:
                    q = todo.pop()
                    if q in seen:
                        continue
                    seen.add(q)
                    run.append(q)
                    todo += [o for o in rs if o not in seen and pos[o][2] % 180 == pos[q][2] % 180 and
                             abs(pos[o][k] - pos[q][k]) <= 1.5 and abs(pos[o][1 - k] - pos[q][1 - k]) <= 6.0]
                if len(run) >= 2:
                    runs.append(sorted(run, key=lambda q: (pos[q][1 - k], q)))
            for run in runs:
                line = g(sorted(pos[q][k] for q in run)[len(run) // 2])
                at = lambda q, v, u: (v, u, pos[q][2]) if k == 1 else (u, v, pos[q][2])
                if not take({q: at(q, pos[q][1 - k], line) for q in run}):
                    for q in run:
                        take({q: at(q, pos[q][1 - k], line)})
                on = [q for q in run if abs(pos[q][k] - line) < 1e-6]
                if len(on) >= 3:
                    lo, hi = pos[on[0]][1 - k], pos[on[-1]][1 - k]
                    step = g((hi - lo) / (len(on) - 1))
                    take({q: at(q, lo + i * step, line) for i, q in enumerate(on)})


def near_target(board: Board, name: str):
    """a near-rule's end: a part ("U1") -> ("U1", None), or one of its pins ("U1.9", "U1.VDDA", by number or by its
    symbol's name) -> ("U1", [pad numbers]); None when the board has no such part or pin"""
    if name in board.comps:
        return name, None
    ref, _, pin = name.partition(".")
    if not pin or ref not in board.comps:
        return None
    import parts
    nums = parts.resolve(board.comps[ref], pin)
    return (ref, nums) if nums else None


def near_gaps(board: Board, near=None) -> List[tuple]:
    """(a, b, dmax, gap) for each near-rule (``board.near``, or the ones given): the gap pad to pad, mm, between
    the parts or pins it names; a rule naming what the board lacks is left out"""
    from shapely.ops import unary_union
    near = getattr(board, "near", []) if near is None else near
    if not near:
        return []
    by, pad_by = {}, {}
    for ref, num, L, g, _ in board.pad_shapes():
        by.setdefault(ref, []).append(g)
        pad_by.setdefault((ref, num), []).append(g)

    def shape(t):
        return unary_union(by[t[0]] if t[1] is None else [g for n in t[1] for g in pad_by.get((t[0], n), [])])
    out = []
    for a, b, dmax in near:
        ta, tb = near_target(board, a), near_target(board, b)
        if ta and tb and ta[0] in by and tb[0] in by:
            out.append((a, b, dmax, shape(ta).distance(shape(tb))))
    return out


def near_misses(board: Board) -> List[str]:
    """the parts (or pins) further apart than ``board.near`` asks (the requirements' "within d mm", pad to pad)"""
    return [f"{a} is {d:.1f} mm from {b} (within {dmax})" for a, b, dmax, d in near_gaps(board) if d > dmax]


def _one_try(board: Board, move, k: int, gap: float, route_kw, router: str, kw) -> tuple:
    """one placement and routing of ``place_and_route``: (score, problems, placement, tracks, vias, fan, line,
    clean, router, seconds)"""
    import time as _t
    pcb._LIVE_TRY = k
    t0_ = _t.perf_counter()
    live("try", state="start", seed=k)
    left = autoplace(board, move=move, gap=gap + 0.25 * min(k, 3), seed=k, **kw)   # (later tries: other seeds at
    live_metrics(board, note="placed")
    if left:                                             # the same room, not ever more than fits; a part that
        probs = [f"{r}: no room to place it (a bigger board, smaller parts, or both sides)" for r in left]
    else:                                                # found no room is not routed round: said at once)
        live("phase", name="route", router=router)
        probs = route_board(board, router, **(route_kw or {}))
        live_copper(board)
        live("phase", name="check")
        probs += [p for p in board.check() if p not in probs]
    miss = near_misses(board)
    live_metrics(board, probs + miss, note="checked")
    score = severity(probs) + 50 * len(left) + 5 * len(miss)   # (a part left off the board is worse than any miss;
    # a short worse than a slip; a requirement's "within d mm" missed counts)
    line = f"place and route, try {k + 1}: " + ("clean" if not probs and not miss and not left else
           "; ".join(([f"{len(probs)} problems ({probs[0][:70]})"] if probs else []) + miss[:2] +
                     ([f"no room for {', '.join(left)}"] if left else [])))
    secs = round(_t.perf_counter() - t0_, 2)
    live("try", state="end", clean=not probs and not miss, problems=(probs + miss)[:12], n=len(probs) + len(miss),
         seconds=secs, line=line)
    pcb._LIVE_TRY = None
    return (score, probs, {r: (board.comps[r]["at"], board.comps[r]["rot"]) for r in move},
            list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())), line,
            not probs and not miss, getattr(board, "router_used", None), secs)


def _try_worker(blob: bytes, move, k, gap, route_kw, router, kw) -> tuple:
    import pickle
    pcb._NO_LATEST = True                                    # (tries in parallel: no routing reused from another try)
    return _one_try(pickle.loads(blob), move, k, gap, route_kw, router, kw)


def place_and_route(board: Board, move: Optional[Sequence[str]] = None, tries: int = 6, gap: float = 1.0,
                    route_kw: Optional[dict] = None, router: str = "freerouting", workers: Optional[int] = None,
                    **kw) -> List[str]:
    """``autoplace`` then ``autoroute``, and when the routing is not clean, place again (another seed) and route
    again, up to ``tries`` times; keeps the placement and routing with the fewest problems (``kw`` go to
    ``autoplace``, ``route_kw`` to ``autoroute``, e.g. ``{"grid": 0.2}``). ``workers``: tries run that many at a
    time in separate processes (default: one per CPU, at most 4; ``PCB_WORKERS`` overrides); the first clean try,
    in order, is kept, so the result is the same as one at a time. Returns that routing's problems (empty: placed,
    routed, and check() clean)"""
    import os
    import pickle
    move = list(move) if move is not None else None
    if move is None:
        move = [r for r, c in board.comps.items() if c.get("unplaced")] or \
               [r for r, c in board.comps.items() if not c["overhang"]]
    mark_user_copper(board)                              # (the script's own copper, before any try routes)
    tracks0, vias0, fan0 = list(board.tracks), list(board.vias), set(getattr(board, "_fan", set()))
    tries = max(1, tries)
    if workers is None:
        workers = int(os.environ.get("PCB_WORKERS") or min(4, os.cpu_count() or 1))
    workers = max(1, min(workers, tries))
    best, results = None, {}
    _progress(kind="tries", tries=tries, workers=workers)

    def _done(k):
        print(results[k][6], flush=True)
        _progress(kind="try", k=k, seconds=results[k][9], clean=results[k][7], line=results[k][6])
    board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)   # (the first try alone: most
    results[0] = _one_try(board, move, 0, gap, route_kw, router, kw)              # boards are clean on it)
    _done(0)
    if results[0][7] or tries == 1:
        workers = 0
    if workers > 1:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
        board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)
        blob = pickle.dumps(board)
        for k0 in range(1, tries, workers):              # (each try in a process of its own, forked afresh: a
            wave = range(k0, min(tries, k0 + workers))  # pool's worker carried its memos from one try into the
            exs = {k: ProcessPoolExecutor(max_workers=1, mp_context=ctx) for k in wave}   # next, so which try
            try:                                         # ran where changed the boards)
                futs = {k: exs[k].submit(_try_worker, blob, move, k, gap, route_kw, router, kw) for k in wave}
                for k in sorted(futs):
                    results[k] = futs[k].result()
                    _done(k)
            finally:
                for ex in exs.values():
                    ex.shutdown(wait=True)
            if any(results[k][7] for k in wave):
                break
    elif workers == 1:
        for k in range(1, tries):
            board.tracks, board.vias, board._fan = list(tracks0), list(vias0), set(fan0)   # (each try afresh)
            results[k] = _one_try(board, move, k, gap, route_kw, router, kw)
            _done(k)
            if results[k][7]:
                break
    for k in sorted(results):                            # (the first clean try, else the fewest problems)
        if results[k][7]:
            best = results[k]
            break
        if best is None or results[k][0] < best[0]:
            best = results[k]
    for r, (at, rot) in best[2].items():
        board.comps[r]["at"], board.comps[r]["rot"] = at, rot
        board.comps[r].pop("unplaced", None)
    board.tracks, board.vias, board._fan = best[3], best[4], best[5]
    board.router_used = best[8] or "not routed: parts without room"
    live("kept", k=next(k for k in results if results[k] is best), clean=best[7], n=len(best[1]),
         places={r: [round(at[0], 3), round(at[1], 3), rot, board.comps[r].get("side", "F")]
                 for r, (at, rot) in best[2].items()})
    board.__dict__.pop("_pads_memo", None)
    board.__dict__.pop("_cu_memo", None)
    return best[1]
