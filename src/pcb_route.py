"""Routing: the built-in maze router (a wavefront per net, escapes from fine-pitch pads), the order nets are routed
in, the FreeRouting hand-off, length tuning of pairs, via repairs after routing, and the route cache (a design's
copper kept and reused, ``routes/`` in git). Split from ``pcb``; ``pcb.<name>`` still reaches every function here.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pcb
from pcb import (Board, EDGE, HOLE_CLEAR, HOLE_GAP, ROUTER_VERSION, _pad_poly, live, live_copper, severity)


def _cxy(geom, X, Y):
    """vectorised point-in-geometry on a prepared geometry (orders of magnitude faster on big unions); on the
    router's grid (X, Y from meshgrid(..., indexing="ij")) only the points inside the geometry's bounds are tested"""
    import numpy as np
    import shapely
    shapely.prepare(geom)
    if getattr(X, "ndim", 0) != 2 or geom.is_empty:
        return shapely.contains_xy(geom, X, Y) if not geom.is_empty else np.zeros(np.shape(X), bool)
    xs, ys = X[:, 0], Y[0, :]
    if not (np.all(np.diff(xs) > 0) and np.all(np.diff(ys) > 0)) or not np.all(X[0, :] == X[0, 0]):
        return shapely.contains_xy(geom, X, Y)
    x0, y0, x1, y1 = geom.bounds
    i0, i1 = np.searchsorted(xs, x0, "left"), np.searchsorted(xs, x1, "right")
    j0, j1 = np.searchsorted(ys, y0, "left"), np.searchsorted(ys, y1, "right")
    out = np.zeros(X.shape, bool)
    if i1 > i0 and j1 > j0:
        out[i0:i1, j0:j1] = shapely.contains_xy(geom, X[i0:i1, j0:j1], Y[i0:i1, j0:j1])
    return out


def _crowded(board: Board, ref: str, num: str, g, width: float, slack: float = 0.25) -> bool:
    """a track ``width`` wide on pad ``ref.num``'s axis would come closer than the clearance to a pad of another
    net on the same part (a USB-C receptacle's 0.6 mm VBUS pin between GND and CC at 0.5 mm pitch: a 3 A feed
    cannot run onto it at full width). ``slack``: how far off the pin's axis the router's grid may put the track"""
    c = board.comps[ref]
    pn = board.pad_net()
    mine = pn.get((ref, str(num)))
    w_pad = min(_mrr_sides(g))
    gap = (width + slack - w_pad) / 2 + board.clear - 1e-6
    if gap <= board.clear - 1e-6:
        return False
    for n2, ps in c["pads"].items():
        if n2 == str(num) or (mine and pn.get((ref, n2)) == mine) or not n2:
            continue
        for p2 in ps:
            if _pad_poly(p2, (c["at"][0], c["at"][1], c["rot"])).distance(g) < gap:
                return True
    return False


def _mrr_sides(g) -> Tuple[float, float]:
    mrr = g.minimum_rotated_rectangle.exterior.coords
    return math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2])


def _escape(board: Board, ref: str, num: str, reach: float = 0.5, width: Optional[float] = None):
    """for a pad narrower than a routing channel (fine-pitch SMD), the point just beyond its outer end along its long
    axis, away from the part's centre: route from there, with a straight stub out of the pad. With ``width`` (the
    track's), also a wider pad whose neighbours of other nets leave no room for that width beside it (a USB-C
    receptacle's VBUS pin under a 3 A feed): a short neck as wide as the pad, out to where the full width fits"""
    c = board.comps[ref]
    pad = c["pads"][str(num)][0]
    g = _pad_poly(pad, (c["at"][0], c["at"][1], c["rot"]))
    w, h = pad["size"]
    if pad.get("prims"):                                     # a custom pad: its real extent, in the pad's frame
        bx = _pad_poly({**pad, "rot": 0.0, "at": (0.0, 0.0), "mirror": False}, (0.0, 0.0, 0.0)).bounds
        w, h = bx[2] - bx[0], bx[3] - bx[1]
    if pad["drill"] or len(c["pads"]) <= 4:              # (a 2-pad passive leaves its pad anywhere)
        return None
    if min(w, h) >= 0.6:
        if not (width and min(w, h) < width + 0.25 and max(w, h) > min(w, h) * 1.3
                and _crowded(board, ref, num, g, width)):
            return None
        reach = max(reach, width / 2 + board.clear)      # (the full width starts clear of the pads' ends)
    ang = math.radians(c["rot"] - pad["rot"] + (0.0 if w >= h else 90.0))
    ux, uy = math.cos(ang), math.sin(ang)
    if min(w, h) >= 0.6:                                 # (and every other one in its row, counted from the row's
        place = (c["at"][0], c["at"][1], c["rot"])       #  nearer end, longer by a full width and a clearance, so
        cx0, cy0 = g.centroid.x, g.centroid.y            #  two such necks side by side, a VBUS pin's and a 3 A
        row = set()                                      #  GND's, do not end level: each one's full width starts
        for ps in c["pads"].values():                    #  past the other's end)
            for p2 in ps:
                if p2["drill"]:
                    continue
                q = _pad_poly(p2, place).centroid
                if abs((q.x - cx0) * ux + (q.y - cy0) * uy) < 0.05:
                    row.add(round((q.x - cx0) * -uy + (q.y - cy0) * ux, 2))
        row = sorted(row)
        i = row.index(min(row, key=abs))
        if min(i, len(row) - 1 - i) % 2 == 1:
            reach += width / 2 + board.clear
    cx, cy = g.centroid.x, g.centroid.y
    if (cx - c["at"][0]) * ux + (cy - c["at"][1]) * uy < 0:
        ux, uy = -ux, -uy
    half = max(w, h) / 2
    return (cx, cy), (cx + ux * (half + reach), cy + uy * (half + reach))


# ---- a small maze router (Lee wavefronts on a grid, two layers, vias) ---------------------------------------------------
def route(board: Board, net: str, *a, **kw) -> List[str]:
    """``_route_net`` (below), and the net's copper as the live run's "copper" event when it is done"""
    probs = _route_net(board, net, *a, **kw)
    if pcb.LIVE is not None:
        live_copper(board, net)
        live("net", net=net, ok=not probs, problems=probs[:3])
    return probs


def _route_net(board: Board, net: str, width: float = 0.3, grid: float = 0.25, via_cost: float = 8.0, layers: str = "FB",
          keepout=None, via: Tuple[float, float] = (0.3, 0.6), only: Optional[Sequence[Tuple[str, str]]] = None,
          amps: Optional[float] = None, track_keepout=None,
          seed_pins: Optional[Sequence[Tuple[str, str]]] = None) -> List[str]:
    """Connect the pads of ``net`` (see ``_route_once`` for how one attempt goes). When an attempt leaves a pad
    unreached or the net split, the net is torn back to where it was and tried again growing from a different
    root: from the net's pour (every pad joined to it), then from its biggest pad (not one boxed in by fine-pitch
    neighbours); the attempt with the fewest problems is kept. Returns that attempt's problems.
    A net with low-current pins (``Board.pin_loads``) is routed in two steps: the trunk between its other pins at
    ``width``, then each branch to a low-current pin at its own width (``Board.pin_width``), joined to the trunk."""
    low = board.pin_loads(net) if (net in board.nets and only is None and amps is None and seed_pins is None) else {}
    pins0 = [tuple(p) for p in board.nets[net]["pins"]] if low else []
    if low and any(p not in low for p in pins0):
        groups: Dict[float, list] = {}
        for p in pins0:
            if p in low:
                groups.setdefault(min(width, board.pin_width(net, p)), []).append(p)
        trunk = [p for p in pins0 if p not in low]
        args = dict(grid=grid, via_cost=via_cost, layers=layers, keepout=keepout, via=via,
                    track_keepout=track_keepout)
        d = board.nets[net]
        orig = d["pins"]
        try:                                             # (the pins not yet routed left off the net meanwhile: not
            d["pins"] = trunk                            #  taken for copper of the net that the trunk could join)
            probs = route(board, net, width=width, **args)
            done = list(trunk)
            for w_, ps in sorted(groups.items(), key=lambda kv: -kv[0]):   # (the wider branches first)
                done += ps
                d["pins"] = [p for p in pins0 if p in done]
                probs += route(board, net, width=w_, only=ps, **args)
        finally:
            d["pins"] = orig
        return probs
    kw = dict(width=width, grid=grid, via_cost=via_cost, layers=layers, keepout=keepout, via=via, only=only, amps=amps,
              track_keepout=track_keepout, seed_pins=seed_pins)
    forced = [(a, getattr(board, a)) for a in ("root_big", "pour_root") if hasattr(board, a)]
    if forced:                                           # (a caller's own choice: one attempt)
        return _join_islands(board, net, _route_once(board, net, **kw), kw, only)
    t0, v0, f0 = list(board.tracks), list(board.vias), set(getattr(board, "_fan", set()))
    best = None
    for rb, pr in ((False, False), (False, True), (True, True)):
        board.tracks, board.vias, board._fan = list(t0), list(v0), set(f0)
        board.root_big, board.pour_root = rb, pr
        try:
            probs = _route_once(board, net, **kw)
        finally:
            del board.root_big, board.pour_root
        split = len(board.islands(net)) - 1 if net in board.nets else 0
        # (a partial route, ``only``: the pads it names reached first; the net may be split until the rest is)
        score = (len(probs) + split, len(board.vias) - len(v0)) if not only else \
            (len(probs), split, len(board.vias) - len(v0))
        if best is None or score < best[0]:
            best = (score, probs, list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())))
        if score[0] == 0:
            break
    _, probs, board.tracks, board.vias, board._fan = best
    neck = max(0.15, board.width_for(amps if amps is not None else board.nets[net]["amps"], "F")) \
        if net in board.nets else 0.15
    left = [tuple(m.group(1).rsplit(".", 1)) for m in
            (re.match(rf"route {re.escape(net)}: could not reach (.+) \(blocked", p_) for p_ in probs) if m]
    if left and neck < width - 1e-9:                     # neck down: the pads still unreached (between fine-pitch
        board.root_big = board.pour_root = False         # neighbours a full-width track cannot pass), each joined
        try:                                             # to the net's copper so far by the narrowest track its
            kw2 = dict(kw, width=neck, only=left, seed_pins=None)   # current allows
            again = _route_once(board, net, **kw2)
        finally:
            del board.root_big, board.pour_root
        probs = [p_ for p_ in probs if not re.match(rf"route {re.escape(net)}: could not reach", p_)] + again
    return _join_islands(board, net, probs, kw, only)


def route_board(board: Board, router: str = "freerouting", **kw) -> List[str]:
    """route the whole board (``_route_board``), then length-match the differential pairs (``tune_pairs``)"""
    probs = _route_board(board, router, **kw)
    unpaired = lambda: {p_ for p_ in board.check() if not p_.startswith("pair ")}   # (pairs: tuned just below)
    before = unpaired()                                  # (clean corners: no stub past a bend, no right angles;
    t0 = [dict(t, pts=list(t["pts"])) for t in board.tracks]   # kept only when the check finds nothing new)
    board.drop_loops()                                   # (a second run of a net's track, a loop: not needed)
    board.trim_overshoots()
    board.chamfer()
    if unpaired() - before:
        board.tracks = t0
        board.__dict__.pop("_cu_memo", None)
    if getattr(board, "pairs", None):
        tune_pairs(board)
        probs = [p_ for p_ in probs if not p_.startswith("pair ")] + \
            [p_ for p_ in board.check() if p_.startswith("pair ")]
    if any(" drills " in p_ for p_ in board.check()):  # (a via the passes since the nudge left against a drill:
        nudge_vias(board)                                #  parted once more, as the check holds it)
        neck_vias(board)
        probs = [p_ for p_ in probs if " drills " not in p_] + [p_ for p_ in board.check() if " drills " in p_]
    return probs


def mark_user_copper(board: Board) -> None:
    """the copper on the board before any routing is the script's own (a hand-drawn feed): tagged ``user``, so the
    clean-up passes (spurs, loops, overshoots, chamfers) and the rip-up leave it as drawn"""
    if getattr(board, "_user_marked", False) or any(it.get("user") for it in board.tracks + board.vias):
        board._user_marked = True                        # (once - later calls find the router's copper there too -
        return                                           #  or already tagged as the script drew it: pcb_cli.load)
    board._user_marked = True
    for it in board.tracks + board.vias:
        if not it.get("fan"):
            it.setdefault("user", True)


def tie_to_exposed_pad(board: Board) -> int:
    """a QFN's (or any chip's) pin on the same net as its exposed pad, tied to it by a short track straight in
    from the pin, as wide as the pin, as a designer draws it: from inside the pin ring it never has to find a way
    out between fine-pitch neighbours to reach the pour (a TESTEN or GND pin beside the paddle). Returns how many
    it drew; marked ``tie`` (and ``neck``: a pin entry), and drawn once"""
    from shapely.geometry import LineString, Point
    pn = board.pad_net()
    n = 0
    for ref, c in board.comps.items():
        pads = [(num, L, g) for r, num, L, g, d in board.pad_shapes() if r == ref and num and not d]
        if len(pads) < 8:
            continue
        ox, oy = c["at"]
        centre = Point(ox, oy)
        eps = [(num, L, g) for num, L, g in pads if g.buffer(1e-3).contains(centre)]
        if not eps:
            continue
        e_num, e_L, e_g = max(eps, key=lambda p_: p_[2].area)
        net = pn.get((ref, e_num))
        if not net or net not in board.nets:
            continue
        others = [g for num, L, g in pads if L == e_L and pn.get((ref, num)) != net]
        for num, L, g in pads:
            if num == e_num or L != e_L or pn.get((ref, num)) != net or g.area >= e_g.area * 0.5:
                continue
            x0_, y0_, x1_, y1_ = g.bounds
            if max(x1_ - x0_, y1_ - y0_) < 1.5 * min(x1_ - x0_, y1_ - y0_):
                continue                                 # (a pin: long and narrow)
            cx, cy = g.centroid.x, g.centroid.y
            w = round(min(x1_ - x0_, y1_ - y0_), 3)
            ex0, ey0, ex1, ey1 = e_g.bounds
            if (x1_ - x0_) > (y1_ - y0_):                # (along the pin's long axis, 0.05 mm into the paddle)
                if not (ey0 + w / 2 <= cy <= ey1 - w / 2):
                    continue                             # (a pin past the paddle's end: no straight way in)
                q = (round(ex1 - 0.05 if cx > ox else ex0 + 0.05, 4), round(cy, 4))
            else:
                if not (ex0 + w / 2 <= cx <= ex1 - w / 2):
                    continue
                q = (round(cx, 4), round(ey1 - 0.05 if cy > oy else ey0 + 0.05, 4))
            pts = [(round(cx, 4), round(cy, 4)), q]
            band = LineString(pts).buffer(w / 2 + board.clear - 1e-3)
            if any(band.intersects(o) for o in others):
                continue                                 # (too close to a neighbour's pad: left to the router)
            if any(t.get("tie") and t["net"] == net and t["pts"] == pts for t in board.tracks):
                continue
            board.track(net, L, pts, w)
            board.tracks[-1]["tie"] = True
            board.tracks[-1]["neck"] = True
            n += 1
    if n:
        board.__dict__.pop("_cu_memo", None)
    return n


def _route_board(board: Board, router: str = "freerouting", **kw) -> List[str]:
    """route the whole board with ``router``: "freerouting" (FreeRouting through KiCad, as KiCad users route:
    ``freerouting.route_kicad``; needs KiCad, Java and the FreeRouting jar, and says which is missing) or "harness"
    (``autoroute``, this file's grid router, chosen explicitly). Sets ``board.router_used``"""
    mark_user_copper(board)                              # (first routing call: what is there, the script drew)
    tie_to_exposed_pad(board)                            # (a chip's pins on its exposed pad's net: tied straight in)
    if router == "harness":
        board.router_used = "harness"
        probs = autoroute(board, **kw)
        neck_vias(board)                                 # (a via its route left against a pad of its own net moved a
        return probs                                     #  neck away, as after FreeRouting: solder wicks down it)
    if router != "freerouting":
        raise ValueError("router: freerouting or harness")
    import freerouting
    missing = [w for w, ok in (("KiCad (its Python API, pcbnew)", freerouting.kicad_available()),
                               ("Java 17+ and the FreeRouting jar (prometheus setup)", freerouting.available())) if not ok]
    if missing:
        raise RuntimeError("FreeRouting needs " + " and ".join(missing) + ": see docs/setup.md, or route with the "
                           "harness's own router (--router harness)")
    freerouting.escape_fine_pitch(board)                 # (fine-pitch pins' first stretch drawn by the harness)
    board.router_used = "freerouting (through KiCad)"    # (FreeRouting does not know the drill gap: its vias are
    probs = freerouting.route_kicad(board, **kw)         #  parted from the others after)
    probs = _second_pass(board, probs, lambda: freerouting.route_kicad(board, **kw)) + nudge_vias(board)
    neck_vias(board)                                     # (one it cannot move is the critic's "via neck" warning:
    return probs                                         #  a solder risk to weigh, not a board that is not joined)


def tune_pairs(board: Board, tries_per_seg: int = 6) -> List[str]:
    """length-match each differential pair whose skew is over its limit (``Board.pair``) the way KiCad's length
    tuner does: a serpentine on a straight stretch of the shorter net, its legs a clearance apart, kept only when
    the design check finds nothing new (no short, clearance slip or cut pour). Returns what is still over"""
    left = []
    for p_, n_, skew in getattr(board, "pairs", []):
        lp, ln = board.net_length(p_), board.net_length(n_)
        if abs(lp - ln) <= skew:
            continue
        short = p_ if lp < ln else n_
        want = abs(lp - ln) - skew / 3                    # (aim inside the limit, not at its edge)
        base = set(board.check())
        segs = []
        for i, t in enumerate(board.tracks):
            if t["net"] != short:
                continue
            for k, (a, b) in enumerate(zip(t["pts"], t["pts"][1:])):
                if math.dist(a, b) >= 2.0:
                    segs.append((math.dist(a, b), i, k))
        done = False
        for S, i, k in sorted(segs, reverse=True):
            t = board.tracks[i]
            a, b = t["pts"][k], t["pts"][k + 1]
            ux, uy = (b[0] - a[0]) / S, (b[1] - a[1]) / S
            per = 2 * (t["w"] + board.clear + 0.05)       # one bump: up, across, down, across
            m = per / 5                                   # (its corners mitred at 45 degrees, as a professional
            nmax = int((S - 1.0) // per)                  #  tuner draws them: each mitre is 0.586 m shorter)
            for nb in sorted({max(1, nmax), max(1, nmax // 2), max(1, nmax // 4)}, reverse=True)[:3]:
                A = want / (2 * nb) + 2 * (2 - math.sqrt(2)) * m   # (a bump adds 2A less 2.343 m)
                if A < max(t["w"], 2.5 * m) or A > 3.0:
                    continue
                for sign in (1, -1):
                    nx, ny = -uy * sign, ux * sign
                    s0 = (S - nb * per) / 2
                    pts = [a]
                    for j in range(nb):
                        for d_, h in ((-m, 0), (0, m), (0, A - m), (m, A), (per / 2 - m, A), (per / 2, A - m),
                                      (per / 2, m), (per / 2 + m, 0)):
                            o = s0 + j * per + d_
                            pts.append((round(a[0] + ux * o + nx * h, 4), round(a[1] + uy * o + ny * h, 4)))
                    pts.append(b)
                    old = t["pts"]
                    t["pts"] = old[:k] + pts + old[k + 2:]
                    board.__dict__.pop("_cu_memo", None)
                    now = set(board.check())
                    new = {m for m in now - base if not m.startswith(f"pair {p_} / {n_}")}
                    if not new:
                        done = abs(board.net_length(p_) - board.net_length(n_)) <= skew
                        base = now
                        break
                    t["pts"] = old
                    board.__dict__.pop("_cu_memo", None)
                else:
                    continue
                break
            if done:
                break
            lp, ln = board.net_length(p_), board.net_length(n_)
            want = abs(lp - ln) - skew / 3
            if abs(lp - ln) <= skew:
                done = True
                break
        if not done:
            lp, ln = board.net_length(p_), board.net_length(n_)
            left.append(f"pair {p_} / {n_}: still {abs(lp - ln):.2f} mm apart after length tuning")
    return left


def _second_pass(board: Board, probs: List[str], again) -> List[str]:
    """nets still unreached or split after FreeRouting and the finish: torn up whole and given to FreeRouting once
    more with the rest of the copper in place (it finds ways the grid router does not); kept only when better"""
    def unreached(ps):
        return {m.group(1) for m in (re.match(r"route (\S+): could not reach", p_) or
                                    re.match(r"net (\S+) is split", p_) for p_ in ps) if m}
    nets = {n for n in unreached(probs) if n in board.nets and n not in {p["net"] for p in board.pours}}
    if not nets:
        return probs
    before = (list(board.tracks), list(board.vias), set(getattr(board, "_fan", set())), probs,
              severity(probs) + severity(board.check()))
    board.tracks = [t for t in board.tracks if t["net"] not in nets or t.get("user")]   # (the script's own copper
    board.vias = [v for v in board.vias if v["net"] not in nets or v.get("user")]       #  stays as it drew it)
    board.__dict__.pop("_cu_memo", None)
    try:
        probs2 = again()
    except (RuntimeError, OSError):
        probs2 = None
    if probs2 is None or severity(probs2) + severity(board.check()) >= before[4]:
        board.tracks, board.vias, board._fan, probs2 = before[0], before[1], before[2], before[3]
        board.__dict__.pop("_cu_memo", None)
    return probs2


def auto_grid(board: Board) -> float:
    """the routing grid for a board: 0.25 mm, or 0.2 mm when a part's pins sit at a pitch the 0.25 grid misses and
    0.2 hits (0.4 mm, 0.8 mm: an RP2040's QFN-56), so each pin's centre is a grid point"""
    import numpy as np
    pitches = []
    for c in board.comps.values():
        cs = [(sum(p["at"][0] for p in l) / len(l), sum(p["at"][1] for p in l) / len(l))
              for n, l in c["pads"].items() if l and n]
        if len(cs) >= 6:
            a = np.asarray(cs)
            d = np.hypot(*(a[:, None, :] - a[None, :, :]).transpose(2, 0, 1))
            np.fill_diagonal(d, np.inf)
            pitches.append(float(d.min()))
    off = lambda p, g: abs(p / g - round(p / g))
    return 0.2 if any(p < 0.8 and off(p, 0.25) > 0.05 and off(p, 0.2) < 0.05 for p in pitches) else 0.25


def drop_pours(board: Board, nets: Optional[Sequence[str]] = None) -> None:
    """each surface pad of a net poured on another layer drops through a via of its own beside it (the ground pins
    of a two-layer board, a plane's), first, while there is room round a fine-pitch part"""
    poured = {p["net"] for p in board.pours}
    for net in (nets if nets is not None else sorted(poured, key=lambda n: board.nets[n]["amps"])):
        if net not in board.nets:
            continue
        layers = {p["layer"] for p in board.pours if p["net"] == net}
        pins = [tuple(p) for p in board.nets[net]["pins"]
                if any(L_ not in layers and not d for r_, n_, L_, g, d in board.pad_shapes() if (r_, n_) == tuple(p))]
        if pins:
            board.drop(net, pins, width=min(0.3, board.net_width(net)))


def autoroute(board: Board, passes: int = 3, drop: bool = True, **kw) -> List[str]:
    """route the whole board in one call: fine-pitch fan-outs first, a via beside each surface pad of a poured net
    (``drop``: the ground pins of a two-layer board drop to its ground pour), then the signal nets (the most local first),
    then the power nets (the least current first), the nets with a pour last, each at ``Board.net_width`` (a net's
    own width, else what its current needs), in negotiated passes (``route_jobs``: a net that fails is routed
    first on the next pass), and finally ``repair`` (a net a later one split joined back). ``kw`` goes to every
    ``route`` call (layers, grid, keepout, via...). Returns the problems ([] = every pad reached)"""
    import math
    poured = {p["net"] for p in board.pours}
    pads = {(r, n): g for r, n, L, g, d in board.pad_shapes()}

    def spread(net):
        pts = [pads[tuple(p)].centroid for p in board.nets[net]["pins"] if tuple(p) in pads]
        if len(pts) < 2:
            return 0.0
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if "grid" not in kw:                                 # a grid the finest pitch sits on: 0.4 mm pads on 0.2
        kw["grid"] = auto_grid(board)
    nets = [n for n, d in board.nets.items() if len(d["pins"]) >= 2 or n in poured]
    sig = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] <= 0.3], key=spread)
    pwr = sorted([n for n in nets if n not in poured and board.nets[n]["amps"] > 0.3],
                 key=lambda n: (board.nets[n]["amps"], spread(n)))
    last = sorted([n for n in nets if n in poured], key=lambda n: board.nets[n]["amps"])
    if not getattr(board, "_fan", None):
        board.fanout(width=0.2)
    if drop:
        drop_pours(board, last)
    jobs = [(n, board.net_width(n), None) for n in sig + pwr + last]
    if ("~freerouting", "") in getattr(board, "_fan", set()):     # (finishing another router's work: the nets it
        jobs = [j for j in jobs if j[0] in poured or len(board.islands(j[0])) > 1   # left whole are not routed
                or len(board.anchor_groups(j[0])) > 1]                             # again, net by net: a stub's
        board.prune_spurs()                              # tip past where FreeRouting joined it is trimmed)
    probs = route_jobs(board, jobs, kw=(lambda net: dict(kw)) if kw else None, passes=passes)
    return probs + repair(board, **kw)


def _drill_gap(at, drill: float, w: dict) -> float:
    """drill wall to drill wall, a via's hole at ``at`` to ``w`` (a via, or a part's hole: an oval slot by its
    shape, not its centre)"""
    import math
    if w.get("geom") is not None:
        from shapely.geometry import Point
        return Point(at).buffer(drill / 2, 16).distance(w["geom"])
    return math.hypot(at[0] - w["at"][0], at[1] - w["at"][1]) - drill / 2 - w["drill"] / 2


def nudge_vias(board: Board, gap: Optional[float] = None, reach: float = 0.3) -> List[str]:
    """vias closer than ``gap`` (default: the board's ``hole_gap``; a house's rule, e.g. 0.5 mm for JLCPCB, tightens
    it) to another via of another net or to a part's own drill (a pin's hole, an exposed pad's thermal via, a bare
    hole), as a router that does not know the drill gap leaves them: the via (the one with fewer tracks on it, of
    two vias) moves away by the shortfall (at most ``reach``), the ends of its tracks with it, when every clearance
    still holds where it lands. Returns the pairs it could not part."""
    import math
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    gap = board.hole_gap if gap is None else gap
    same = gap                                           # (one net's drills too: the check holds every pair of drills
    #                                                      to the hole gap - the web between them, whatever their nets)
    left: List[str] = []

    def ends(v):
        return [(t, i) for t in board.tracks if t["net"] == v["net"] for i in (0, -1)
                if math.hypot(t["pts"][i][0] - v["at"][0], t["pts"][i][1] - v["at"][1]) < 0.02]

    def short(a, b):
        d = _drill_gap(a["at"], a["drill"], b)
        return d < (gap if a["net"] != b["net"] else same) - 1e-6, d
    pn = board.pad_net()                                 # (the parts' own drills: they do not move)
    pinned = [{"net": pn.get(tuple(h["name"].split(" pad "))) if " pad " in h["name"] else None, "at": h["at"],
               "drill": h["d"], "geom": h["geom"], "fixed": True} for h in board.drill_list()
              if not h["name"].startswith("via ")]          # (a slot too: by its shape)
    for _ in range(2):
        bad = [(a, b) for i, a in enumerate(board.vias) for b in board.vias[i + 1:] + pinned if short(a, b)[0]]
        if not bad:
            break
        fixed = board.copper(pours=False)
        for a, b in bad:
            if not short(a, b)[0]:
                continue
            moved = False
            for v, o in sorted(((a, b),) if b.get("fixed") else ((a, b), (b, a)), key=lambda p: len(ends(p[0]))):
                if v.get("user") or any(t.get("user") for t, _ in ends(v)):
                    continue                             # (the script's via, or one its tracks end at: as drawn)
                others = {L: unary_union([g for (nt, l), g in fixed.items() if l == L and nt != v["net"]] or [Point(0, 0).buffer(0)])
                          for L in board.layers}

                def lands(at):                           # every clearance and every drill gap holds there
                    if any(Point(at).buffer(v["dia"] / 2).distance(others[L]) < board.clear - 1e-6
                           for L in board.layers if not others[L].is_empty):   # (an empty layer's distance: nan)
                        return False
                    for t, i in ends(v):
                        pts = list(t["pts"]); pts[i] = at
                        seg = LineString(pts[-2:] if i == -1 else pts[:2]).buffer(t["w"] / 2)
                        if seg.distance(others[t["layer"]]) < board.clear - 1e-6:
                            return False
                    return all(_drill_gap(at, v["drill"], w)
                               >= (gap if w["net"] != v["net"] else same) - 1e-6
                               for w in board.vias + pinned if w is not v)
                dx, dy = v["at"][0] - o["at"][0], v["at"][1] - o["at"][1]
                a0 = math.atan2(dy, dx)                  # straight away first, then turning off it, nearest first
                tries = [(round(v["at"][0] + r * math.cos(a0 + da), 4), round(v["at"][1] + r * math.sin(a0 + da), 4))
                         for r in [reach * k / 8 for k in range(1, 9)]
                         for da in [math.radians(t) for t in (0, 30, -30, 60, -60, 90, -90, 120, -120)]]
                at = next((p_ for p_ in tries if lands(p_)), None)
                ok = at is not None
                if ok:
                    for t, i in ends(v):
                        t["pts"] = list(t["pts"]); t["pts"][i] = at
                    v["at"] = at
                    moved = True
                    board.__dict__.pop("_cu_memo", None)
                    fixed = board.copper(pours=False)
                    break
            if not moved:
                left.append(f"vias {a['net']} at {a['at']} and {b['net']} at {b['at']}: no room to part them")
    return left


def neck_vias(board: Board, neck: Optional[float] = None, width: float = 0.2, reach: float = 1.0) -> List[str]:
    """vias against an SMD pad of their own net (closer than ``neck``, default fab.VIA_PAD_NECK, to it, and not a via-in-pad: the ring wholly on
    the pad), as a router leaves them: solder wicks down the hole and a small part tombstones. The via moves off the
    pad until ``neck`` of track lies between them (at most ``reach``), its tracks' ends with it and a track from the pad
    to it, where every clearance and drill gap still holds. Returns the vias it could not move."""
    import math
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    from fab import VIA_PAD_NECK                         # (the critic's own: what it warns of, this keeps clear)
    neck = VIA_PAD_NECK if neck is None else neck
    pn = board.pad_net()
    smd = [(r, n, L, g) for r, n, L, g, d in board.pad_shapes() if not d and L in ("F", "B")]
    holes = [{"at": h["at"], "drill": h["d"], "net": pn.get(tuple(h["name"].split(" pad "))) if " pad " in h["name"]
              else None, "geom": h["geom"]} for h in board.drill_list() if not h["name"].startswith("via ")]
    left: List[str] = []

    def against(v, at):                                  # the own-net SMD pads too near a via at ``at``
        ring = Point(at).buffer(v["dia"] / 2, 16)
        return [(r, n, L, g) for r, n, L, g in smd if pn.get((r, n)) == v["net"] and not g.contains(ring)
                and ring.distance(g) < neck - 1e-6]

    for v in list(board.vias):
        if v.get("user"):                                # (the script's own via: where it drew it)
            continue
        near = against(v, v["at"])
        if not near:
            continue
        r0, n0, L0, g0 = near[0]
        fixed = board.copper(pours=False)
        others = {L: unary_union([g for (nt, l), g in fixed.items() if l == L and nt != v["net"]] or [Point(0, 0).buffer(0)])
                  for L in board.layers}
        ends = [(t, i) for t in board.tracks if t["net"] == v["net"] for i in (0, -1)   # (every end the via's ring
                if math.hypot(t["pts"][i][0] - v["at"][0], t["pts"][i][1] - v["at"][1]) < v["dia"] / 2]  # holds:
        #                                                    FreeRouting ends a track off the via's centre too)
        if any(t.get("user") for t, _ in ends):          # (a hand-drawn track ends there: not dragged along)
            left.append(f"via {v['net']} at {v['at']}: against {r0}.{n0}, the script's own track ends in it")
            continue
        pad_at = g0.representative_point().coords[0]
        mrr = g0.minimum_rotated_rectangle.exterior.coords   # (the track off the pad: as wide as the net needs,
        minor = min(math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2]))   # up to the pad's own width - a 0.2 mm
        wide = round(max(width, min(board.net_width(v["net"]), minor)), 3)   # tie on a 1.5 A ground failed the
        #                                                    current check - else ``width`` where only that fits)
        w_try = [width]

        def lands(at):
            width = w_try[0]
            if against(v, at) or any(Point(at).buffer(v["dia"] / 2).distance(others[L]) < board.clear - 1e-6
                                     for L in board.layers if not others[L].is_empty):
                return False
            segs = [(L0, LineString([pad_at, at]), width)]
            for t, i in ends:
                pts = list(t["pts"]); pts[i] = at
                segs.append((t["layer"], LineString(pts[-2:] if i == -1 else pts[:2]), t["w"]))
            if any(not others[L].is_empty and sg.buffer(w / 2).distance(others[L]) < board.clear - 1e-6
                   for L, sg, w in segs):
                return False
            return all(_drill_gap(at, v["drill"], w)
                       >= board.hole_gap - 1e-6
                       for w in board.vias + holes if w is not v)
        a0 = math.atan2(v["at"][1] - pad_at[1], v["at"][0] - pad_at[0])   # straight off the pad first, then turning
        tries = [(round(v["at"][0] + r * math.cos(a0 + da), 4), round(v["at"][1] + r * math.sin(a0 + da), 4))
                 for r in [reach * k / 20 for k in range(1, 21)]
                 for da in [math.radians(t) for t in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180)]]
        at = None
        for w_ in dict.fromkeys([wide, width]):
            w_try[0] = w_
            at = next((p_ for p_ in tries if lands(p_)), None)
            if at is not None:
                break
        if at is None:
            left.append(f"via {v['net']} at {v['at']}: against {r0}.{n0}, no room to move it a neck away")
            continue
        for t, i in ends:
            t["pts"] = list(t["pts"]); t["pts"][i] = at
        board.tracks.append({"net": v["net"], "layer": L0, "pts": [tuple(round(c, 4) for c in pad_at), at], "w": w_try[0],
                             "amps": None, **({"neck": True} if w_try[0] < board.net_width(v["net"]) - 1e-6
                                              and minor < board.net_width(v["net"]) - 1e-6 else {})})
        v["at"] = at
        board.__dict__.pop("_cu_memo", None)
    return left


def repair(board: Board, width: float = 0.25, nets: Optional[Sequence[str]] = None, **kw) -> List[str]:
    """the last step of a routing: every net (or those in ``nets``) that a later net's tracks split (a pour cut in
    two, a pad left on a scrap of it) gets its stray groups joined back to its main group. Returns what could not be
    joined"""
    probs = []
    for net in (nets or list(board.nets)):
        if len(board.islands(net)) > 1:
            kw2 = dict(width=width, grid=0.25, via_cost=8.0, layers="".join(L for L in board.layers if L in "FB"),
                       keepout=None, via=(0.3, 0.6), only=None, amps=None, track_keepout=None, seed_pins=None)
            kw2.update(kw)
            probs += _join_islands(board, net, [], kw2)
    return probs


def _join_islands(board: Board, net: str, probs: List[str], kw: dict, only=None) -> List[str]:
    """after routing: a pin the route took as reached that sits on copper cut off from the rest (a scrap of pour
    between tracks) is joined to the net's main group with seed_pins (``only``: just those pins)"""
    if net not in board.nets or any(re.match(rf"route {re.escape(net)}: could not reach", p_) for p_ in probs):
        return probs
    groups = board.islands(net)
    if len(groups) < 2:                                  # whole as copper; KiCad may still read it split, joining
        groups = board.anchor_groups(net)                # items only where a track END lands (a paired pin)
        if len(groups) < 2:
            return probs
    want = set(map(tuple, only)) if only else None
    main = [tuple(p_) for p_ in groups[0]]
    for g in groups[1:]:
        g = [tuple(p_) for p_ in g if want is None or tuple(p_) in want]
        if not g:
            continue
        kw2 = dict(kw, only=g[:1], seed_pins=main)
        board.root_big = board.pour_root = False
        try:
            more = _route_once(board, net, **kw2)
        finally:
            del board.root_big, board.pour_root
        probs = probs + more
        if not more:
            main += g
    return probs


def _route_once(board: Board, net: str, width: float = 0.3, grid: float = 0.25, via_cost: float = 8.0, layers: str = "FB",
          keepout=None, via: Tuple[float, float] = (0.3, 0.6), only: Optional[Sequence[Tuple[str, str]]] = None,
          amps: Optional[float] = None, track_keepout=None,
          seed_pins: Optional[Sequence[Tuple[str, str]]] = None) -> List[str]:
    """Connect the pads of ``net`` as a tree: each pad in turn (or just the pads in ``only``) to the net's copper so
    far (its first pad, its pour, what is already routed), on a ``grid`` keeping ``width / 2 + clearance`` from other
    nets' fixed copper (their pours are carved round the track later) and out of ``keepout`` ({layer: [polygon]}:
    copper to keep off, vias passing that layer included), changing layer through a via where every layer is clear;
    ``track_keepout`` only bars tracks (where an inner layer may not carry signals, a via may still pass). ``layers``
    any of the board's ("F2B": the inner layer 2 too). ``seed_pins``: the pads that count as already on the net, in
    place of the net's pours and its other pads (join a stray island to the main group of ``Board.islands``, where
    a piece of plane cut off by tracks would otherwise pass for the net). Fine-pitch pads leave by a straight stub
    off their outer end.
    Adds tracks and vias to the board; returns the problems (a pad it could not reach)."""
    import numpy as np
    import shapely
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import nearest_points, unary_union
    probs: List[str] = []
    n_route0 = len(board.tracks)
    x0, y0, x1, y1 = board.outline.bounds
    nx, ny = int((x1 - x0) / grid) + 1, int((y1 - y0) / grid) + 1
    xs = x0 + np.arange(nx) * grid
    ys = y0 + np.arange(ny) * grid
    XX, YY = np.meshgrid(xs, ys, indexing="ij")
    keepout = keepout or {}                               # {layer: [polygon]}: copper to keep off (vias too)
    track_keepout = track_keepout or {}                   # {layer: [polygon]}: no tracks there (a via may pass)
    # bare holes as obstacles, sized so that, grown by the clearance like all copper, a track keeps the hole
    # clearance (KiCad's) from a peg's wall and a mounting hole's copper-free radius (its screw head)
    hko_ = getattr(board, "_hole_ko", {})
    holes = unary_union([Point(x, y).buffer(max(d / 2, max(hko_.get((x, y), 0.0), d / 2 + HOLE_CLEAR) - board.clear)
                                            + 0.02, 16) for x, y, d in board.all_holes()] or [Polygon()])
    cu_fixed = board.copper(pours=False)
    pn = board.pad_net()
    pads = {(r, n_): (g, L) for r, n_, L, g, _ in board.pad_shapes()}
    # a part with several pads of one number (a switch's paired pins, a shield's tabs): each is a pad of the net
    # to reach, "1~1", "1~2", ... after the first (KiCad wants copper to every one of them)
    twins: Dict[Tuple[str, str], list] = {}
    for r, n_, L, g, _ in board.pad_shapes():
        if pn.get((r, n_)) == net:
            lst = twins.setdefault((r, n_), [])
            if all(g.centroid.distance(q.centroid) > 0.3 for q, _ in lst):
                lst.append((g, L))
    extra = []
    for (r, n_), lst in twins.items():
        for k, (g, L) in enumerate(lst[1:], 1):
            pads[(r, f"{n_}~{k}")] = (g, L)
            pn[(r, f"{n_}~{k}")] = net
            extra.append((r, f"{n_}~{k}"))
        if len(lst) > 1:
            pads[(r, n_)] = lst[0]
    Ls = list(layers)
    by_layer: Dict[Tuple[str, str], Dict[str, object]] = {}
    for r, n_, L, g, _ in board.pad_shapes():
        by_layer.setdefault((r, n_), {}).setdefault(L, g)
    for key, (g, L) in list(pads.items()):              # a through-hole pad is on every layer: route it on one offered
        if L not in Ls and key in by_layer:              # (keyed by its last layer, a "F only" route refused it)
            L2 = next((l_ for l_ in Ls if l_ in by_layer[key]), None)
            if L2 is not None:
                pads[key] = (by_layer[key][L2], L2)
    corridors = []                                      # other nets' fine-pitch ways out, kept clear 1 mm past
    for t in board.tracks:
        if t.get("fan") and t["net"] != net:
            (ax, ay), (bx, by) = t["pts"][0], t["pts"][-1]
            dl = math.hypot(bx - ax, by - ay) or 1.0
            cl = getattr(board, "corridor", 1.0)            # how far past a stub its way out is kept clear
            corridors.append((t["layer"], LineString([(bx, by), (bx + (bx - ax) / dl * cl, by + (by - ay) / dl * cl)]).buffer(t["w"] / 2)))
    # a keep-out (a pour kept clear of signals) does not wall in this net's own pads: round each, room for its way
    # out and a via (a gate resistor sitting inside a power pour still has to be reached)
    own_zone = {L: unary_union([g.buffer(width + board.clear + via[1] + 0.3, 8) for (r_, n_), (g, l_) in pads.items()
                                if l_ == L and pn.get((r_, n_)) == net] or [Polygon()]) for L in board.layers}
    ko_ = lambda polys, L: [Polygon(k).difference(own_zone.get(L, Polygon())) for k in polys]
    blk0 = {}
    for L in Ls:
        other = unary_union([g for (n, l), g in cu_fixed.items() if l == L and n != net] + [holes] +
                            ko_(keepout.get(L, []) + track_keepout.get(L, []), L) +
                            [Polygon(k) for k in board.keepout_zones(L)] +
                            [c_ for l_, c_ in corridors if l_ == L])
        inside = _cxy(board.outline.buffer(-(width / 2 + max(0.3, board.edge + 0.04))), XX, YY)
        blk0[L] = (_cxy(other.buffer(board.clear + 0.04 + width / 2, 8), XX, YY) if not other.is_empty
                   else np.zeros_like(inside)) | ~inside       # 0.04 mm over the rule: grid snapping stays legal
    vblk, own_tht = None, Polygon()
    if len(Ls) >= 2:
        vb = []
        for L in board.layers:                          # (a via passes every layer: their copper and keep-outs)
            other = unary_union([g for (n, l), g in cu_fixed.items() if l == L and n != net] + [holes] +
                                [Polygon(k) for k in board.keepout_zones(L)] + ko_(keepout.get(L, []), L) +
                                [c_ for l_, c_ in corridors if l_ == L])
            vb.append(_cxy(other.buffer(board.clear + 0.04 + via[1] / 2, 8), XX, YY) if not other.is_empty
                      else np.zeros_like(blk0[Ls[0]]))
        vblk = vb[0] | ~_cxy(board.outline.buffer(-(via[1] / 2 + board.edge + 0.04)), XX, YY)    # (off the board edge)
        for v_ in vb[1:]:
            vblk = vblk | v_
        # a via keeps the hole gap from every drill, this net's own plated holes too, except that inside one of those
        # the layer change needs no via at all (the plated hole joins the layers)
        # (vias already on the board included: this net's own may be reused as they stand)
        drl = [h["geom"] for h in board.drill_list()]  # (each drill as its shape: a USB-C shell's slot is longer
        if drl:                                          #  than it is wide)
            vblk = vblk | _cxy(unary_union([g_.buffer(board.hole_gap + via[0] / 2 + 0.02, 12) for g_ in drl]), XX, YY)
        own_tht = unary_union([g for r_, n_, L_, g, d in board.pad_shapes()
                               if d and L_ == "F" and pn.get((r_, n_)) == net] +
                              [Point(v_["at"]).buffer(v_["dia"] / 2) for v_ in board.vias if v_["net"] == net]
                              or [Polygon()])
        if not own_tht.is_empty:
            vblk = vblk & ~_cxy(own_tht.buffer(-0.05), XX, YY)
    LT = list(dict.fromkeys(["F", "B"] + Ls))            # the layers this net's copper is tracked on
    tree = {L: [] for L in LT}                          # this net's connected copper so far (shapely, per layer)
    seeds = {L: [] for L in LT}                         # what the tree must hang off: pours, the first pin
    cu_net = None
    for L in LT:
        if any(p["net"] == net and p["layer"] == L for p in board.pours):
            if cu_net is None:
                cu_net = board.copper(only_net=net)        # (once for all the net's layers)
            g = board.pour_fill(net, L, cu_net)
            if not g.is_empty:
                tree[L].append(g)
                # the pour's main body is on the net; a sliver cut off by tracks is not, until something joins it
                if seed_pins is None:
                    seeds[L].append(max(getattr(g, "geoms", None) or [g], key=lambda q: q.area))
    fan_geo = {}
    for t in board.tracks:
        if t["net"] == net and t.get("fan"):            # a pad's way out joins the tree with its pad
            fan_geo[t["fan"]] = (t["layer"], LineString(t["pts"]).buffer(t["w"] / 2))
        elif t["net"] == net:
            tree[t["layer"]].append(LineString(t["pts"]).buffer(t["w"] / 2))
    for v in board.vias:
        if v["net"] == net:
            for L in LT:
                tree[L].append(Point(v["at"]).buffer(v["dia"] / 2))
    pins = [tuple(p) for p in board.nets[net]["pins"]]
    pins += [p for p in extra if (p[0], p[1].split("~")[0]) in pins]
    for p_ in [p_ for p_ in pins if p_ not in pads]:      # (a pin its footprint does not have: said, not routed)
        probs.append(f"route {net}: no pad {p_}")
    pins = [p_ for p_ in pins if p_ in pads]
    if not only and getattr(board, "root_big", True):    # grow the net from its biggest pad (a regulator's tab, a
        big = max((p_ for p_ in pins if p_ in pads),     # capacitor), not from a pad boxed in by fine-pitch
                  key=lambda p_: (pads[p_][0].area, -pins.index(p_)), default=None)   # neighbours: everything
        if big is not None:                              # else would have to reach into its box
            pins = [big] + [p_ for p_ in pins if p_ != big]
    todo = [tuple(p) for p in only] if only else pins[1:]
    first = pins[0] if pins else None

    fanned = getattr(board, "_fan", set())

    def stub_w(pin):                                     # a way out of a fine-pitch pad no wider than the pad: a
        mrr = pads[pin][0].minimum_rotated_rectangle.exterior.coords    # full-width track along it runs into the
        return min(width, min(math.dist(mrr[0], mrr[1]), math.dist(mrr[1], mrr[2])))   # next pins (a pin entry)

    def stub_track(pin, L, pts):
        w_ = stub_w(pin)
        board.track(net, L, pts, w_, amps)
        if w_ < width - 1e-6:
            board.tracks[-1]["neck"] = True

    def stub_of(pin):
        e = _escape(board, *pin, width=width) if "~" not in pin[1] else None
        if e is None:
            return None
        if pin not in fanned:
            stub_track(pin, pads[pin][1], [e[0], e[1]])
        return e[1]
    if not only and getattr(board, "pour_root", True) and any(tree[L] for L in LT):   # the net has a pour: the pour is the root, and every pad,
        todo = [p_ for p_ in pins]                       # the first too, is joined to it (a first pad taken as
        first = None                                     # already on the net may sit apart from the pour)
    if not only and first is not None:
        g0, L0 = pads[first]
        tree[L0].append(g0)
        if first in fan_geo:
            tree[fan_geo[first][0]].append(fan_geo[first][1])
        e = stub_of(first)
        if e is not None:
            tree[L0].append(LineString([g0.centroid.coords[0], e]).buffer(stub_w(first) / 2))   # (as wide as drawn)
        seeds[L0].append(g0)
    else:                                                # pins not being routed are taken as already on the net
        for p_ in pins:
            if p_ not in todo and p_ in pads:
                if seed_pins is None or p_ in set(map(tuple, seed_pins)):
                    seeds[pads[p_][1]].append(pads[p_][0])
                tree[pads[p_][1]].append(pads[p_][0])
    if any(seeds[L] for L in LT):
        # keep only the copper that hangs off a seed: a hand-drawn piece not yet joined to the net is not "reached"
        parts_ = [(L, q) for L in LT for q in (getattr(unary_union(tree[L]), "geoms", None) or
                                               ([unary_union(tree[L])] if tree[L] else [])) if not q.is_empty]
        par = list(range(len(parts_)))

        def _r(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        links_ = [Point(v["at"]) for v in board.vias if v["net"] == net] + \
                 [g_.centroid for (r_, n_), (g_, l_) in pads.items() if pn.get((r_, n_)) == net and
                  any(pd_["drill"] for pd_ in board.comps[r_]["pads"].get(n_, []))]
        from shapely.strtree import STRtree as _STR
        ptree = _STR([q for _, q in parts_])             # (a tree query within 10 um, not a buffer per part per
        for pt in links_:                                # point: on a ground net that was minutes per call)
            hit = sorted(int(i) for i in ptree.query(pt, predicate="dwithin", distance=0.01))
            for i in hit[1:]:
                par[_r(i)] = _r(hit[0])
        good = {_r(i) for i, (L, q) in enumerate(parts_) if any(q.intersects(sg) for sg in seeds[L])}
        tree = {L: [q for i, (l_, q) in enumerate(parts_) if l_ == L and _r(i) in good] for L in LT}
    own_pads = {L: unary_union([gg for (rr, nn), (gg, ll) in pads.items() if ll == L and pn.get((rr, nn)) == net] or [Polygon()])
                for L in Ls}
    own_free = {L: unary_union([g_.buffer(-width / 2) if not g_.buffer(-width / 2).is_empty
                                else g_.centroid.buffer(grid * 0.5)
                                for (rr, nn), (g_, ll) in pads.items() if ll == L and pn.get((rr, nn)) == net]
                               or [Polygon()]) for L in Ls}
    tg, gm_, seen_ = {}, {}, {}
    for pin in todo:
        if pin not in pads:
            probs.append(f"route {net}: no pad {pin}")
            continue
        g_pin, L_pin = pads[pin]
        if L_pin not in Ls:
            probs.append(f"route {net}: pad {pin} is not on a routing layer")
            continue
        for L in LT:                                     # (the net's copper so far, and the grid cells next to it,
            n0 = seen_.get(L, 0)                         # grown by what each pin's route added: copper only adds)
            if L not in tg or len(tree[L]) != n0:
                new_ = tree[L][n0:] if L in tg else tree[L]
                tg[L] = unary_union(([tg[L]] if L in tg and not tg[L].is_empty else []) + new_) if (new_ or L in tg) \
                    else Polygon()
                if L in Ls:
                    add_ = unary_union(new_).buffer(grid * 0.6) if new_ else Polygon()
                    gm_[L] = (gm_[L] if L in gm_ else np.zeros_like(blk0[L])) | \
                        (_cxy(add_, XX, YY) if not add_.is_empty else False)
                seen_[L] = len(tree[L])
        if not tg[L_pin].is_empty and tg[L_pin].intersects(g_pin.buffer(-0.01)):
            continue
        blk = {L: blk0[L].copy() for L in Ls}
        for L in Ls:                                     # this net's own pads are free where a track stays inside
            if not own_free[L].is_empty:                 # them (one centred at a pad's edge overhangs its neighbour)
                blk[L] &= ~_cxy(own_free[L], XX, YY)
        e = _escape(board, *pin, width=width) if "~" not in pin[1] else None
        sx, sy = e[1] if e else (g_pin.centroid.x, g_pin.centroid.y)
        ci, cj = int(round((sx - x0) / grid)), int(round((sy - y0) / grid))
        best = None                                      # the nearest clear cell to the way out, else force it;
        if e:                                            # off a fine-pitch pad, only in line with its stub
            ex, ey = e[1][0] - e[0][0], e[1][1] - e[0][1]
            en = math.hypot(ex, ey) or 1.0
            ex, ey = ex / en, ey / en
        for tol in ((0.02, grid * 0.55) if e else (1e9,)):    # exactly in line first, then nearly
            for di in range(-3, 4):
                for dj in range(-3, 4):
                    a_, b_ = ci + di, cj + dj
                    if 0 <= a_ < nx and 0 <= b_ < ny and not blk[L_pin][a_, b_]:
                        if e and (abs((xs[a_] - sx) * ey - (ys[b_] - sy) * ex) > tol or
                                  (xs[a_] - sx) * ex + (ys[b_] - sy) * ey < -1e-6):
                            continue
                        dd = math.hypot(xs[a_] - sx, ys[b_] - sy)
                        if best is None or dd < best[0]:
                            best = (dd, a_, b_)
            if best:
                break
        forced = best is None and e is not None and blk[L_pin][ci, cj]
        if best:
            ci, cj = best[1], best[2]
        blk[L_pin][ci, cj] = False
        goal = {k: (gm_[L] & ~blk[L]) if not tg[L].is_empty else np.zeros_like(blk[L]) for k, L in enumerate(Ls)}
        found, path = _wavefront_windowed(blk, vblk, goal, [(ci, cj, Ls.index(L_pin))], Ls, via_cost)
        if not found and len(Ls) > 1 and vblk is not None and not vblk[ci, cj] and \
                all(not blk[L][ci, cj] or L == L_pin for L in Ls):
            # boxed in on its own layer (a pad between fine-pitch neighbours): drop through a via where its way out
            # ends and go on from the other layers, as a designer would
            found, path = _wavefront_windowed(blk, vblk, goal, [(ci, cj, k) for k in range(len(Ls))], Ls, via_cost)
        if not found:
            probs.append(f"route {net}: could not reach {pin[0]}.{pin[1]} (blocked; move parts or widen a channel)")
            continue
        n_t, n_v = len(board.tracks), len(board.vias)
        if Ls[path[0][2]] != L_pin and not own_tht.contains(Point(float(xs[ci]), float(ys[cj]))):
            board.via(net, (round(float(xs[ci]), 4), round(float(ys[cj]), 4)), *via)   # (the escape via)
        if e:
            neck_ = [(float(xs[ci]), float(ys[cj]))]
            if forced and len(path) > 1 and path[1][2] == path[0][2]:
                # (no cell past the stub is clear at the full width: the forced one is left at the stub's width,
                # and the track goes on full width only from the first clear cell, as a neck)
                neck_.append((float(xs[path[1][0]]), float(ys[path[1][1]])))
                path = path[1:]
            stub_track(pin, L_pin, ([e[1]] if pin in fanned else [e[0], e[1]]) + neck_)
        else:
            board.track(net, L_pin, [(sx, sy), (float(xs[ci]), float(ys[cj]))], width, amps)
        seg = []
        for (i, j, k) in path:
            pt = (round(float(xs[i]), 4), round(float(ys[j]), 4))
            if seg and seg[-1][2] != k:
                _emit(board, net, Ls[seg[-1][2]], [q[:2] for q in seg], width, amps)
                if not own_tht.contains(Point(pt)):          # (in a plated hole or via of this net: no new via)
                    board.via(net, pt, *via)
                    vgap = Point(pt).buffer(via[0] + board.hole_gap + 0.02, 12)    # the next via keeps its drill gap
                    vblk = vblk | (_cxy(vgap, XX, YY) & ~_cxy(Point(pt).buffer(via[1] / 2 - 0.05), XX, YY))
                    own_tht = own_tht.union(Point(pt).buffer(via[1] / 2))
                seg = []
            seg.append((pt[0], pt[1], k))
        _emit(board, net, Ls[seg[-1][2]], [q[:2] for q in seg], width, amps)
        endL = Ls[path[-1][2]]                          # close the last gap onto the copper it reached
        endp = Point(seg[-1][0], seg[-1][1])
        if not tg[endL].is_empty and not tg[endL].buffer(-0.02).contains(endp):
            # (onto a point well inside what it reached: KiCad joins a track whose end lies inside the pad or track,
            # not one that only overlaps it at an edge or a rounded corner)
            near_ = min(getattr(tg[endL], "geoms", None) or [tg[endL]], key=lambda q_: q_.distance(endp))
            q0 = nearest_points(near_, endp)[0]
            q = None
            for inset in (0.1, 0.06, 0.03, 0.01):        # (as deep as the piece it reached allows, a step past)
                core_ = near_.buffer(-inset, 8)
                if not core_.is_empty:
                    c_ = nearest_points(core_, endp)[0]
                    if c_.distance(q0) <= inset + 0.05:
                        q = c_
                        break
            q = q if q is not None else q0
            if q.distance(endp) > 1e-3:
                board.track(net, endL, [(endp.x, endp.y), (q.x, q.y)], width, amps)
        for t in board.tracks[n_t:]:
            tree[t["layer"]].append(LineString(t["pts"]).buffer(t["w"] / 2))
        for v in board.vias[n_v:]:
            for L in LT:
                tree[L].append(Point(v["at"]).buffer(v["dia"] / 2))
        tree[L_pin].append(g_pin)
        if pin in fan_geo:
            tree[fan_geo[pin][0]].append(fan_geo[pin][1])
    if getattr(board, "prune", True):                    # the dead ends (a way-out stub or fan-out the route joined
        mine = [k for k, t in enumerate(board.tracks) if t["net"] == net]    # part-way along): the whole net's once
        if not probs and len(board.islands(net)) == 1:   # it is whole, else only this call's (a stub of a pin not
            board.prune_spurs(mine)                      # reached yet is its way out, kept for the next call)
        else:
            board.prune_spurs([k for k in mine if k >= n_route0])
    #                                                      board.prune = False keeps a deliberate stub (an antenna)
    return probs


def _emit(board, net, L, pts, width, amps=None):
    out = [pts[0]]
    for p in pts[1:]:
        if len(out) >= 2:
            (ax, ay), (bx, by) = out[-2], out[-1]
            if abs((bx - ax) * (p[1] - by) - (by - ay) * (p[0] - bx)) < 1e-6:
                out[-1] = p
                continue
        out.append(p)
    if len(out) >= 2:
        board.track(net, L, out, width, amps)


def _shift(a, di, dj):
    """out[i, j] = a[i - di, j - dj] (zeros where that falls off the grid)"""
    import numpy as np
    out = np.zeros_like(a)
    n, m = a.shape
    out[max(di, 0):n + min(di, 0), max(dj, 0):m + min(dj, 0)] = a[max(-di, 0):n + min(-di, 0), max(-dj, 0):m + min(-dj, 0)]
    return out


def _wavefront_windowed(blk, vblk, goal, starts, Ls, via_cost, margin: int = 48):
    """_wavefront in a window round the start and the nearest goal cell (``margin`` cells past both), and on the
    whole grid only when the window holds no way through: most connections are local, and the search costs grow
    with the area it floods"""
    import numpy as np
    k_any = [np.argwhere(goal[k]) for k in range(len(Ls))]
    pts = np.concatenate([g for g in k_any if len(g)] or [np.zeros((0, 2), int)])
    if not len(pts):
        return _wavefront(blk, vblk, goal, starts, Ls, via_cost)
    si, sj = starts[0][0], starts[0][1]
    near = pts[np.argmin((pts[:, 0] - si) ** 2 + (pts[:, 1] - sj) ** 2)]
    n, m = goal[0].shape
    i0, i1 = max(0, min(si, near[0]) - margin), min(n, max(si, near[0]) + margin + 1)
    j0, j1 = max(0, min(sj, near[1]) - margin), min(m, max(sj, near[1]) + margin + 1)
    if (i1 - i0) * (j1 - j0) >= 0.6 * n * m:             # (nearly the whole board anyway)
        return _wavefront(blk, vblk, goal, starts, Ls, via_cost)
    w = (slice(i0, i1), slice(j0, j1))
    hit, path = _wavefront({L: blk[L][w] for L in Ls}, vblk[w] if vblk is not None else None,
                           {k: g[w] for k, g in goal.items()}, [(i - i0, j - j0, k) for i, j, k in starts], Ls, via_cost)
    if hit:
        return (hit[0] + i0, hit[1] + j0, hit[2]), [(i + i0, j + j0, k) for i, j, k in path]
    return _wavefront(blk, vblk, goal, starts, Ls, via_cost)


def _wavefront(blk, vblk, goal, starts, Ls, via_cost):
    """Lee router: breadth-first wavefronts on both layers at once (numpy), 8-connected, a via costing ``via_cost``
    steps; returns (found, path [(i, j, k)] from the start to the goal)"""
    import numpy as np
    nL = len(Ls)
    free = [~blk[L] for L in Ls]
    INF = np.iinfo(np.int32).max
    dist = [np.full(free[0].shape, INF, np.int32) for _ in Ls]
    front = [np.zeros_like(free[0]) for _ in Ls]
    for i, j, k in starts:
        dist[k][i, j] = 0
        front[k][i, j] = True
    pending = {}                                        # step -> [(k, mask)] via arrivals
    vc = int(round(via_cost))
    hit = None
    n_, m_ = free[0].shape
    diag = []                                           # per layer: (where a diagonal step lands, where it comes
    for k in range(nL):                                 # from, its two corners clear) - fixed for the whole search
        fr = free[k]
        dl = []
        for di, dj in ((1, 1), (-1, -1), (1, -1), (-1, 1)):    # diagonals only past two clear corners
            dst = (slice(max(di, 0), n_ + min(di, 0)), slice(max(dj, 0), m_ + min(dj, 0)))
            src = (slice(max(-di, 0), n_ + min(-di, 0)), slice(max(-dj, 0), m_ + min(-dj, 0)))
            dl.append((dst, src, (_shift(fr, 0, dj) & _shift(fr, di, 0))[dst]))
        diag.append(dl)
    unvis = [d == INF for d in dist]                    # (kept up to date: no full compare against INF per step)
    nvblk = ~vblk if vblk is not None else None
    for step in range(1, 6000):
        new = []
        for k in range(nL):
            f = front[k]
            g = np.zeros_like(f)
            g[1:, :] |= f[:-1, :]
            g[:-1, :] |= f[1:, :]
            g[:, 1:] |= f[:, :-1]
            g[:, :-1] |= f[:, 1:]
            for dst, src, cm in diag[k]:
                g[dst] |= f[src] & cm
            g &= free[k] & unvis[k]
            new.append(g)
            if nvblk is not None and nL >= 2 and f.any():   # change layer here, arriving vc steps later
                fv = f & nvblk
                for k2 in range(nL):
                    if k2 != k:
                        pending.setdefault(step - 1 + vc, []).append((k2, fv))
        for k2, m in pending.pop(step, []):
            m = m & free[k2] & unvis[k2]
            new[k2] |= m
        any_new = False
        for k in range(nL):
            dist[k][new[k]] = step
            unvis[k] &= ~new[k]
            front[k] = new[k]
            any_new |= bool(new[k].any())
            gk = new[k] & goal[k]
            if gk.any():
                ii, jj = np.argwhere(gk)[0]
                hit = (int(ii), int(jj), k)
                break
        if hit or (not any_new and not pending):
            break
    if not hit:
        return None, []
    path = [hit]
    i, j, k = hit
    d = dist[k][i, j]
    nb = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    while d > 0:
        moved = False
        for di, dj in nb:
            a, b_ = i + di, j + dj
            if 0 <= a < dist[k].shape[0] and 0 <= b_ < dist[k].shape[1] and dist[k][a, b_] == d - 1 and \
                    (not (di and dj) or (free[k][a, j] and free[k][i, b_])):
                i, j, d = a, b_, d - 1
                path.append((i, j, k))
                moved = True
                break
        if not moved and nL >= 2:
            for k2 in range(nL):
                if k2 != k and dist[k2][i, j] == d - vc:
                    k, d = k2, d - vc
                    path.append((i, j, k))
                    moved = True
                    break
        if not moved:
            break
    path.reverse()
    return hit, path


def _routes_json(tracks, vias, fan, probs) -> bytes:
    import gzip
    import json
    return gzip.compress(json.dumps({"tracks": tracks, "vias": vias, "fan": sorted(map(list, fan)), "probs": probs},
                                    separators=(",", ":")).encode(), mtime=0)


def _routes_load(raw: bytes) -> dict:
    import gzip
    import json
    d = json.loads(gzip.decompress(raw))
    tup = lambda o: {k: [tuple(p) for p in v] if k == "pts" else tuple(v) if isinstance(v, list) else v
                     for k, v in o.items()}                     # (JSON lists back to the tuples routing wrote)
    return {"tracks": [tup(t) for t in d["tracks"]], "vias": [tup(v) for v in d["vias"]],
            "fan": {tuple(f) for f in d["fan"]}, "probs": d["probs"]}


def cached_route(board: Board, do_route, tag: str = "", store: Optional[str] = None) -> List[str]:
    """run ``do_route(board)`` (which adds tracks and vias and returns problems) once per design: the routed copper
    is cached by a hash of the placement, nets, pours and holes, so a program re-run per MOTION pose does not re-route.
    ``store``: a folder kept in git where the routed copper is also written (``<board>_<hash>.json.gz``, the board's
    older ones removed; only a routing with no problems), so a fresh clone loads the committed routing instead of
    re-routing, which is slow and need not give the same copper with other library versions. With the environment
    variable ``PCB_REROUTE=1`` every cache and the store are ignored (and nothing is written): routing from scratch"""
    import hashlib
    import json
    import os
    import pickle
    from vendor_parts import CACHE
    key = json.dumps({"tag": tag, "comps": {r: [c["fp"], c["at"], c["rot"]] + (["B"] if c.get("side", "F") == "B" else [])
                                         for r, c in sorted(board.comps.items())},
                      "nets": board.nets, "pours": board.pours, "holes": board.holes, "clear": board.clear,
                      "oz": board.oz, "tracks": board.tracks, "vias": board.vias,
                      "keepouts": getattr(board, "keepouts", []),
                      **({"gaps": [board.hole_gap, board.edge]} if (board.hole_gap, board.edge) != (HOLE_GAP, EDGE)
                         else {})}, sort_keys=True, default=str)
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    board._route_key = h
    f = CACHE / "pcb_routes" / f"{board.name}_{h}.pkl"
    kept = Path(store) / f"{board.name}_{h}.json.gz" if store else None
    fresh = bool(os.environ.get("PCB_REROUTE"))         # (route from scratch: a router change measured honestly)
    if fresh:
        d, kept = None, None
    elif f.exists():
        d = pickle.loads(f.read_bytes())
    elif kept is not None and kept.exists():
        d = _routes_load(kept.read_bytes())
    else:
        d = None
    if d is None:
        probs = do_route(board)
        d = {"tracks": board.tracks, "vias": board.vias, "fan": getattr(board, "_fan", set()), "probs": probs}
        if not fresh:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(pickle.dumps(d))
    board.tracks, board.vias, board._fan = d["tracks"], d["vias"], d.get("fan", set())
    clean = not d["probs"] and not any(("split" in p_ or re.match(r"\S+\.Cu: .+ (short|mm apart)", p_) or
                                        "bare hole" in p_) for p_ in board.check()) \
        if kept is not None and not kept.exists() else False
    if clean:                                            # (only clean routing goes into git: joined by KiCad's rule,
        # no shorts, clearance kept; a check finding about the design itself, a hole spacing, does not hold it back)
        kept.parent.mkdir(parents=True, exist_ok=True)
        for old in kept.parent.glob(f"{board.name}_*.json.gz"):
            os.remove(old)
        kept.write_bytes(_routes_json(board.tracks, board.vias, board._fan, d["probs"]))
    return d["probs"]






def placement(board: Board) -> dict:
    """each part's footprint, position, rotation and side: what a saved routing was routed round"""
    return {r: (c["fp"], tuple(c["at"]), c["rot"], c.get("side", "F")) + ((c["pads_key"],) if c.get("pads_key") else ())
            for r, c in board.comps.items()}                  # (pads_key: its pads differ from the footprint's)


def reuse_routes(board: Board, old: dict, rip: Sequence[Tuple[float, float, float]] = ()) -> set:
    """put back the routing of an earlier placement where it still holds, so moving a few parts re-routes only
    what they disturb. ``old``: {"placement": placement(board) then, "tracks": [...], "vias": [...]} (tracks and
    vias added by the routing, or all of them: ones already on the board are not added twice). A net's old copper
    goes back unless one of its parts moved or any of it now comes within the clearance of another net's pads or
    copper on the board, or (rip-up) within one of the circles ``rip`` ((x, y, r): round a pad that could not be
    reached, so the net that failed there can route first). Returns the nets left out (route them again), every
    net of a moved part included."""
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    now = placement(board)
    moved = {r for r in set(now) | set(old["placement"]) if now.get(r) != old["placement"].get(r)}
    pn = board.pad_net()
    bad = {n for (r, _), n in pn.items() if r in moved}
    for n, d in board.nets.items():                      # (a net that lost or gained a moved part's pin)
        if any(r in moved for r, _ in d["pins"]):
            bad.add(n)
    k_t = lambda t: (t["net"], t["layer"], tuple(tuple(round(c, 4) for c in p) for p in t["pts"]), round(t["w"], 4))
    k_v = lambda v: (v["net"], tuple(round(c, 4) for c in v["at"]))
    mine, theirs = {}, {}                                # (the copper the script draws, now and when it was routed:
    for it in board.tracks + board.vias:                 #  a net whose hand-drawn copper changed gets no old routing
        if it.get("user"):                               #  back - it ran beside the old copper - and the old hand-
            mine.setdefault(it["net"], set()).add(k_t(it) if "pts" in it else k_v(it))   # drawn copper itself never
    for it in old["tracks"] + old["vias"]:               #  comes back)
        if it.get("user"):
            theirs.setdefault(it["net"], set()).add(k_t(it) if "pts" in it else k_v(it))
    bad |= {n for n in set(mine) | set(theirs) if mine.get(n, set()) != theirs.get(n, set())}
    have_t = {(t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) for t in board.tracks}
    have_v = {(v["net"], tuple(v["at"])) for v in board.vias}
    have_v_list = list(board.vias)
    old_t = [t for t in old["tracks"] if (t["net"], t["layer"], tuple(map(tuple, t["pts"])), t["w"]) not in have_t
             and not t.get("fan") and not t.get("user")]  # (escape stubs are laid afresh each build, not reused)
    old_v = [v for v in old["vias"] if (v["net"], tuple(v["at"])) not in have_v and not v.get("user")]

    def shape(it):
        if "pts" in it:
            return it["layer"], LineString(it["pts"]).buffer(it["w"] / 2, 8)
        return None, Point(it["at"]).buffer(it["dia"] / 2, 8)     # (a via: every layer)
    # what is on the board now: every pad, and the tracks and vias already there
    obst = [(L, n, g, d) for r, num, L, g, d in board.pad_shapes() for n in [pn.get((r, num))]] + \
           [(L, it["net"], g, 0) for it in board.tracks + board.vias for L, g in [shape(it)]]
    tree = STRtree([g for _, _, g, _ in obst])
    circles = [Point(x, y).buffer(r, 16) for x, y, r in rip]
    inner = board.outline.buffer(-board.edge)
    drills = [(g.centroid, d) for *_, g, d in board.pad_shapes() if d] + \
             [(Point(x, y), d) for x, y, d in board.all_holes()] + [(Point(v["at"]), v["drill"]) for v in board.vias]
    dtree = STRtree([c for c, _ in drills]) if drills else None
    for it in old_t + old_v:
        n = it["net"]
        if n in bad:
            continue
        L, g = shape(it)
        if any(g.intersects(c) for c in circles) or not inner.contains(g):
            bad.add(n)
            continue
        if "pts" not in it and dtree is not None:        # a via keeps the drill gap (the rules may have changed)
            c0 = Point(it["at"])
            if any(c0.distance(drills[int(i)][0]) - (it["drill"] + drills[int(i)][1]) / 2 < board.hole_gap - 1e-6
                   for i in dtree.query(c0.buffer(it["drill"] / 2 + board.hole_gap + 3))):
                bad.add(n)
                continue
        for i in tree.query(g.buffer(board.clear)):
            Lo, no, go, dr = obst[int(i)]
            if no == n:
                continue
            if L is not None and Lo is not None and Lo != L and not dr:
                continue                                  # (another layer; a plated hole is on all of them)
            if g.distance(go) < board.clear - 1e-6:
                bad.add(n)
                break
    # and clear of each other: a short between two kept nets (an earlier routing's own fault) sends both back
    kept = [(it, n, L, g) for it in old_t + old_v if it["net"] not in bad for n in [it["net"]] for L, g in [shape(it)]]
    kt = STRtree([g for *_, g in kept])
    for i, (it, n, L, g) in enumerate(kept):
        for j in kt.query(g.buffer(board.clear)):
            _, n2, L2, g2 = kept[int(j)]
            if n2 == n or (L is not None and L2 is not None and L2 != L):
                continue
            if g.distance(g2) < board.clear - 1e-6:
                bad |= {n, n2}
    n0 = len(board.tracks)
    for t in old_t:
        if t["net"] not in bad:
            board.tracks.append(t)
    for v in old_v:
        if v["net"] not in bad:
            board.vias.append(v)
    # a kept track whose end no longer lands inside its net's copper (a pad reshaped, a stub gone) goes too, and
    # so does a net it left whole that KiCad's end-point rule splits
    loose = {board.tracks[k]["net"] for k, _ in board.loose_ends(only=range(n0, len(board.tracks)))}
    poured = {pz["net"] for pz in board.pours}
    cu_ = board.copper(pours=False)
    failed = {m.split()[1].rstrip(":") for m in old.get("got", []) if m.startswith("route ")}
    loose |= {n for n in {t["net"] for t in board.tracks[n0:]} - poured - bad - failed
              if n in board.nets and len(board.anchor_groups(n, cu_)) > 1}
    if loose:
        bad |= loose
        keep_t = board.tracks[:n0] + [t for t in board.tracks[n0:] if t["net"] not in loose]
        board.vias = [v for v in board.vias if v["net"] not in loose or v in have_v_list]
        board.tracks = keep_t
    return bad


def route_jobs(board: Board, jobs: Sequence[Tuple[str, float, Optional[Sequence[Tuple[str, str]]]]], kw=None,
               passes: int = 3, rip: float = 2.5) -> List[str]:
    """route a board's signal nets the way a big board needs, in one call: ``jobs`` [(net, width, only-pins or
    None)] in order (local nets first), ``kw(net)`` the rest of ``route()``'s arguments for that net (grid, layers,
    keep-outs, ...); a job may add a fourth item, a dict of ``route()`` arguments for that job alone (e.g.
    ``{"amps": 0.01}`` for a sense line off a power net; ``"rejoin": False`` leaves a net that later nets split to
    the caller, e.g. a ground joined afterwards by stitching vias).
    - Negotiated passes: a net that fails is routed first on the next pass; the best of ``passes`` is kept.
    - Checkpoints: the result is saved per design (``<name>_<route key>_signals.pkl``, so a failure in the steps
      after routing costs nothing) and as ``<name>_signals_latest.pkl``.
    - Incremental: with no checkpoint for this design but an earlier routing on file, ``reuse_routes`` puts back
      what the changed placement does not disturb, tears up nets within ``rip`` mm of each pad that failed last
      time, and only those nets, the failed ones first, route again: moving a few parts costs minutes, not the
      whole board.
    Leaves the routing on the board; returns its problems."""
    import pickle
    from vendor_parts import CACHE
    kw = kw or (lambda net: {})
    # (keyed by the design: cached_route's key, else a hash of the board as it stands - placement, nets, the copper
    # drawn so far - and the jobs, so a changed design never loads another design's routing)
    if getattr(board, "_route_key", None):
        rkey = board._route_key
    else:
        import hashlib
        rkey = hashlib.sha1((board._state_key() + repr(list(jobs)) + f"r{ROUTER_VERSION}").encode()).hexdigest()[:16]
    ckpt = CACHE / "pcb_routes" / f"{board.name}_{rkey}_signals.pkl"
    latest = CACHE / "pcb_routes" / f"{board.name}_signals_latest_r{ROUTER_VERSION}.pkl"
    jobs = list(jobs)
    import os
    fresh = bool(os.environ.get("PCB_REROUTE"))
    best = pickle.loads(ckpt.read_bytes()) if ckpt.exists() and not fresh else None
    if best is None and latest.exists() and not fresh and not pcb._NO_LATEST:
        old = pickle.loads(latest.read_bytes())
        failed0 = {m.split()[1].rstrip(":") for m in old["got"] if m.startswith("route ")}
        circles = []
        for m in old["got"]:
            if "reach " in m:
                ref, num = m.split("reach ")[1].split()[0].rsplit(".", 1)
                if ref in board.comps and num in board.comps[ref]["pads"]:
                    circles.append(board.pad(ref, num) + (rip,))
        redo = reuse_routes(board, old, rip=circles) | failed0
        redo |= {j[0] for j in jobs if j[0] in board.nets and len(board.islands(j[0])) > 1}   # (left split last time)
        jobs = [j for j in jobs if j[0] in failed0] + [j for j in jobs if j[0] in redo and j[0] not in failed0]
        print(f"reusing the last routing: {len(jobs)} nets to route again", flush=True)
    base = (list(board.tracks), list(board.vias))
    computed = best is None
    for _ in range(0 if best else passes):
        board.tracks, board.vias = list(base[0]), list(base[1])
        got, failed = [], []
        for job in jobs:
            net, w, pins = job[:3]
            ex = {k: v for k, v in (job[3] if len(job) > 3 else {}).items() if k != "rejoin"}
            p = route(board, net, width=w, only=pins, **{**kw(net), **ex})
            got += p
            if p:
                failed.append(job)
        for job in {j[0]: j for j in jobs}.values():     # a net a later one split (a pour cut in two by the nets
            net, w = job[0], job[1]                       # routed after it, now that failures go first) joined back
            if (job[3] if len(job) > 3 else {}).get("rejoin", True) is False:
                continue                                  # (a net joined later by the caller: stitching vias)
            if net in board.nets and job not in failed and (len(board.islands(net)) > 1 or
                                                            len(board.anchor_groups(net)) > 1):
                ex = {k: v for k, v in {**kw(net), **(job[3] if len(job) > 3 else {})}.items() if k != "rejoin"}
                more = repair(board, width=w, nets=[net], **{k: v for k, v in ex.items() if k != "only"})
                got += more
                if more:
                    failed.append(job)
        if best is None or len(got) < len(best[0]):
            best = (got, list(board.tracks), list(board.vias))
        if not failed:
            break
        jobs = failed + [j for j in jobs if j not in failed]
    from shapely.geometry import LineString, Point
    if computed and best is not None and best[0]:        # rip up and retry: the nets round each pad still
        for rr in (rip, 2 * rip):                        # unreached torn up, the failed nets routed first, the
            board.tracks, board.vias = list(best[1]), list(best[2])   # torn ones after; kept if it does better
            fails, circles = set(), []
            for m in best[0]:
                mm = re.match(r"route (\S+): could not reach (\S+) \(blocked", m)
                if mm:
                    fails.add(mm.group(1))
                    ref, num = mm.group(2).rsplit(".", 1)
                    if ref in board.comps and num in board.comps[ref]["pads"]:
                        circles.append(Point(*board.pad(ref, num)).buffer(rr))
            if not circles:
                break
            from shapely.ops import unary_union as _uu
            zone = _uu(circles)
            torn = {t["net"] for t in board.tracks if not t.get("fan") and t["net"] not in fails
                    and zone.intersects(LineString(t["pts"]))} | \
                   {v["net"] for v in board.vias if v["net"] not in fails and zone.contains(Point(v["at"]))}
            poured_ = {pz["net"] for pz in board.pours}
            torn -= poured_                              # (a ground's pins drop through vias: leave it be)
            redo = fails | torn
            board.tracks = [t for t in board.tracks if t["net"] not in redo or t.get("fan") or t.get("user")]
            board.vias = [v for v in board.vias if v["net"] not in redo or v["net"] in poured_ or v.get("user")]
            order = [j for j in jobs if j[0] in fails] + [j for j in jobs if j[0] in torn]
            got = []
            for job in order:
                ex = {k: v for k, v in (job[3] if len(job) > 3 else {}).items() if k != "rejoin"}
                got += route(board, job[0], width=job[1], only=job[2], **{**kw(job[0]), **ex})
            got = [m for m in best[0] if m.split(":")[0].split()[-1] not in redo] + got
            got += [f"route {n}: split after the rip-up" for n in sorted(redo)          # (a net left in pieces counts)
                    if n in board.nets and len(board.islands(n)) > 1 and not any(f"route {n}:" in m for m in got)]
            print(f"rip-up within {rr} mm of {len(circles)} pads ({len(torn)} nets torn up): "
                  f"{len(best[0])} problems before, {len(got)} after", flush=True)
            live_copper(board)                           # (the nets torn up and routed again: the whole board)
            if len(got) < len(best[0]):
                best = (got, list(board.tracks), list(board.vias))
            if not best[0]:
                break
    if best is not None and not best[0]:                 # every net whole: trim way-out stubs a route ran past
        board.tracks, board.vias = list(best[1]), list(best[2])
        if board.prune_spurs():
            best = (best[0], list(board.tracks), list(board.vias))
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(pickle.dumps(best))
    board.tracks, board.vias = best[1], best[2]
    latest.write_bytes(pickle.dumps({"placement": placement(board), "got": best[0], "tracks": best[1],
                                     "vias": best[2]}))
    return list(best[0])
