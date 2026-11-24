"""Cable routing for a diagram whose switches were placed by hand (the user dragged one in any direction).

The tiered layout (layered.py) can only route cables between rows of switches. Once a switch sits anywhere the user dropped it,
cables are routed here instead, on an orthogonal grid around the switches:

  * every cable leaves its switch straight out of the edge that faces the other end, through its own anchor point;
  * cables never pass through a switch box (vertices inside a box plus a small clearance are blocked);
  * no two cables share a stretch of line or a corner: each grid edge carries one cable, and a grid point can only be
    shared by two cables that cross it straight at a right angle;
  * shortest cables are routed first, with a penalty per bend, so short links stay simple.
"""
from __future__ import annotations
import heapq
import math
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict

from .layered import NODE_W as W, NODE_H as H

APPROACH = 2               # grid steps beyond a stub kept free of other cables' bends and runs
STUB = 26                  # straight run out of a switch edge before a cable may turn (room for the port label)
CLEAR = 10                 # cables keep at least this far from any switch box
LANES = (16, 26, 40, 54)   # parallel cable lanes around every switch
GAP_DROP = 110             # ... and the switch the user just dropped at least this far
GAP = 2 * STUB + 12        # switches are kept at least this far apart in one direction (the automatic layout uses 70)
MERGE = 6                  # grid lines closer than this are merged
LANE_STEP = 10             # spacing of the extra lanes added in wide gaps
MAX_FILL = 14               # at most this many extra lanes in one gap
WEIGHT = 1.6               # >1 makes the search head for the target sooner (much faster, routes stay near-shortest)
BEND = 40                  # cost of one bend, in diagram units
PENALTY = 4000             # cost of sharing grid with another cable (used only when no clean route exists)
TIME_LIMIT = 3.0           # seconds; after this, remaining cables take their cheapest route without re-routing others
MAX_EXPAND = 80_000        # search budget per cable
ANCHOR_GAP = 16            # minimum spacing of cables along one switch edge
CAP = {e: int((W if e in "tb" else H) // ANCHOR_GAP) - 1 for e in "tblr"}
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))     # 0 +x, 1 -x, 2 +y, 3 -y
OUT = {"r": 0, "l": 1, "b": 2, "t": 3}         # direction leaving each switch edge


def clean_positions(raw, ids):
    """Positions sent by the browser -> {id: (x, y)}; drops anything that is not a pair of finite numbers."""
    out = {}
    for k, v in (raw or {}).items():
        if k in ids and isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                x, y = float(v[0]), float(v[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                out[k] = (max(-1e6, min(1e6, x)), max(-1e6, min(1e6, y)))
    return out


def resolve(pos, last=None):
    """Keep switches from overlapping: a switch dropped onto another slides sideways until it is clear.
    `last` (the switch just dragged) is placed last, so it is the one that moves, not its neighbours."""
    order = sorted(pos, key=lambda n: (n == last, pos[n][1], pos[n][0]))
    placed, out = [], {}
    sx, sy = (W + GAP) / 2, (H + GAP) / 2
    steps = sorted(((i, j) for i in range(-60, 61) for j in range(-30, 31)), key=lambda t: ((t[0] * sx) ** 2 + (t[1] * sy) ** 2, abs(t[1])))
    clear = lambda x, y, g: not any(abs(p[0] - x) < W + g and abs(p[1] - y) < H + g for p in placed)
    for n in order:
        x0, y0 = pos[n]
        g = GAP_DROP if n == last else GAP          # the switch just dropped keeps a wider berth, so cables have room around it
        x, y = next(((x0 + i * sx, y0 + j * sy) for i, j in steps if clear(x0 + i * sx, y0 + j * sy, g)), (x0, y0))
        out[n] = (x, y)                  # nearest free spot to where it was dropped
        placed.append((x, y))
    return out


def _sides(pa, pb):
    """Which edges of switch a and switch b a cable between them uses."""
    ax, ay, bx, by = pa[0] + W / 2, pa[1] + H / 2, pb[0] + W / 2, pb[1] + H / 2
    vgap, hgap = abs(by - ay) - H, abs(bx - ax) - W
    room = 2 * STUB + 8
    if vgap >= room:                 # top / bottom whenever there is vertical room: cables then reach every anchor on its own column
        return ("b", "t") if by > ay else ("t", "b")
    if hgap >= room:
        return ("r", "l") if bx > ax else ("l", "r")
    return (("b", "t") if by > ay else ("t", "b")) if vgap >= hgap else (("r", "l") if bx > ax else ("l", "r"))


def route(nodes, links, pos):
    """pos: {id: (x, y)} top-left of every switch. Returns {link_id: {"pts", "ends", "kind"}} in layered.py's format."""
    pair = [l for l in links if l["a"] in pos and l["b"] in pos and l["a"] != l["b"]]
    if not pair:
        return {}
    centre = {n: (x + W / 2, y + H / 2) for n, (x, y) in pos.items()}
    link_of = {l["id"]: l for l in pair}

    # ---- anchors: the cables on one switch edge, spread along it in the order of where their other end is
    ends = defaultdict(list)                        # switch -> [(link id, other switch, preferred edge)]
    for l in pair:
        sa, sb = _sides(pos[l["a"]], pos[l["b"]])
        ends[l["a"]].append((l["id"], l["b"], sa))
        ends[l["b"]].append((l["id"], l["a"], sb))
    per_side = defaultdict(list)
    for n, items in ends.items():                   # an edge holds only so many cables; the rest use the next best edge
        c = centre[n]
        ranked = []
        for lid, o, first in items:
            dx, dy = centre[o][0] - c[0], centre[o][1] - c[1]
            score = {"r": dx, "l": -dx, "b": dy, "t": -dy}
            order = [first] + sorted((e for e in score if e != first), key=lambda e: -score[e])
            ranked.append((-(score[order[0]] - score[order[1]]), lid, o, order))
        used = defaultdict(int)
        for _, lid, o, order in sorted(ranked):
            e = next((e for e in order if used[e] < CAP[e]), None) or min(order, key=lambda e: used[e] / CAP[e])
            used[e] += 1
            per_side[(n, e)].append((lid, o))
    anchor = {}
    for (n, s), items in per_side.items():
        horiz = s in ("t", "b")
        items.sort(key=lambda t: (centre[t[1]][0] if horiz else centre[t[1]][1], t[0]))
        x0, y0 = pos[n]
        span = W if horiz else H
        stub = STUB
        if horiz:                                   # port labels sit above / below a top / bottom edge: the cable runs straight
            spacing = W / (len(items) + 1)          # out past them before it may turn (same label rows as diagram_export.end_labels)
            rows = min(5, max(1, -(-52 // int(spacing))))
            deepest = max(_label_lines(link_of[lid]) for lid, _ in items)
            depth = 13 + (rows - 1) * 12 if deepest == 1 else 13 + (deepest - 1) * 12
            stub = max(STUB, depth + 12)
        for k, (lid, _) in enumerate(items):
            off = span * (k + 1) / (len(items) + 1)
            ax, ay = {"t": (x0 + off, y0), "b": (x0 + off, y0 + H), "l": (x0, y0 + off), "r": (x0 + W, y0 + off)}[s]
            dx, dy = DIRS[OUT[s]]
            anchor[(lid, n)] = {"x": round(ax, 1), "y": round(ay, 1), "side": s, "idx": k,
                                "stub": (round(ax + dx * stub, 1), round(ay + dy * stub, 1)), "dir": OUT[s]}

    # ---- grid lines: switch edges (blocked, so no edge can jump across a box), lanes around them, anchor lines, gap midlines
    xs, ys = set(), set()
    for x, y in pos.values():
        xs.update((x, x + W))
        ys.update((y, y + H))
        for d in LANES:
            xs.update((x - d, x + W + d))
            ys.update((y - d, y + H + d))
    for a in anchor.values():
        xs.update((a["x"], a["stub"][0]))
        ys.update((a["y"], a["stub"][1]))
    xs.update((min(xs) - 90, max(xs) + 90))
    ys.update((min(ys) - 90, max(ys) + 90))
    xs, xmap = _lines(xs)
    ys, ymap = _lines(ys)
    for a in anchor.values():                       # anchors slide along their edge onto the merged grid line (a few units at most)
        if a["side"] in ("t", "b"):
            a["x"] = xmap[a["x"]]
        else:
            a["y"] = ymap[a["y"]]
        a["stub"] = (xmap[a["stub"][0]], ymap[a["stub"][1]])
    xi, yi = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}

    boxes = [(x - CLEAR, y - CLEAR, x + W + CLEAR, y + H + CLEAR) for x, y in pos.values()]
    blocked = set()
    for x0, y0, x1, y1 in boxes:                    # mark every grid point strictly inside a box (+ clearance)
        i0, i1 = bisect_right(xs, x0), bisect_left(xs, x1)
        j0, j1 = bisect_right(ys, y0), bisect_left(ys, y1)
        for i in range(i0, i1):
            for j in range(j0, j1):
                blocked.add((i, j))

    # ---- who uses what. Stubs are permanent; cable runs can be ripped up and re-routed.
    STUB_ID = "#stub"
    edge_by = {}                                    # grid edge -> link id (or STUB_ID)
    vt = defaultdict(dict)                          # grid point -> {link id: "h" | "v" | "x"}  ("x" = bend / end of a run)
    owner = {}                                      # stub turn point -> its link id
    runs = {}                                       # link id -> (path, sd, gd) of the committed run

    def ekey(u, v):
        return (u, v) if u < v else (v, u)

    # reserve every stub (anchor -> first turn point) before routing, so no cable runs along or turns on another's stub
    for (lid, n), a in anchor.items():
        s = (xi[a["stub"][0]], yi[a["stub"][1]])
        owner[s] = lid
        vt[s][STUB_ID + lid] = "x"
        axis = "h" if a["dir"] < 2 else "v"
        if axis == "h":
            ax, ay = bisect_left(xs, a["x"]), yi[a["y"]]
            cells = [(i, ay) for i in range(min(ax, s[0]), max(ax, s[0]) + 1)]
        else:
            ax, ay = xi[a["x"]], bisect_left(ys, a["y"])
            cells = [(ax, j) for j in range(min(ay, s[1]), max(ay, s[1]) + 1)]
        dx, dy = DIRS[a["dir"]]                     # keep a short approach lane open beyond the turn point, so no other cable
        ext = []                                    # can bend right in front of it and seal it off (others may still cross it)
        c = s
        for _ in range(APPROACH):
            c = (c[0] + dx, c[1] + dy)
            if not (0 <= c[0] < len(xs) and 0 <= c[1] < len(ys)) or c in blocked:
                break
            ext.append(c)
        line = sorted(set(cells + ext))
        for c, c2 in zip(line, line[1:]):
            edge_by.setdefault(ekey(c, c2), STUB_ID + lid)
        for c in line:
            if c != s and c not in blocked:
                vt[c].setdefault(STUB_ID + lid, axis)

    def through_ok(u, din, dout, lid):
        """May cable `lid` pass grid point u arriving along din and leaving along dout?"""
        others = [v for k, v in vt[u].items() if k != lid and k != STUB_ID + lid] if u in vt else []
        if not others:
            return True
        return din == dout and len(others) == 1 and others[0] == ("v" if din < 2 else "h")   # one clean right-angle crossing

    def conflicts(path, sd, gd, lid):
        """Link ids whose committed runs the path collides with (stubs are not counted: they cannot be moved)."""
        hit = set()
        dirs = [sd] + [_dir(path[k], path[k + 1]) for k in range(len(path) - 1)] + [gd]
        for k in range(len(path) - 1):
            o = edge_by.get(ekey(path[k], path[k + 1]))
            if o and o != lid and o != STUB_ID + lid:
                hit.add(o)
        for k, u in enumerate(path):
            if not through_ok(u, dirs[k], dirs[k + 1], lid):
                hit.update(o for o in vt[u] if o != lid and o != STUB_ID + lid)
        return hit

    def search(lid, s, sd, g, gd, soft):
        """A* from stub point s (arrived along sd) to stub point g, which must be left along gd (into the switch).
        soft 0: no sharing at all; 1: sharing allowed at a high price; 2: other cables ignored (switches still avoided)."""
        gx, gy = g
        tx, ty = xs[gx], ys[gy]
        wt = WEIGHT * (1, 1.6, 2.5)[soft]
        h = lambda p: wt * (abs(xs[p[0]] - tx) + abs(ys[p[1]] - ty))
        budget = MAX_EXPAND * (1, 1, 4)[soft]
        start = (s, sd)
        best = {start: 0}
        prev = {}
        heap = [(h(s), 0, start)]
        n_exp = 0
        while heap:
            f, c, st = heapq.heappop(heap)
            if best.get(st, math.inf) < c:
                continue
            u, din = st
            if u == g and (soft or through_ok(u, din, gd, lid)):
                path = [u]
                while st in prev:
                    st = prev[st]
                    path.append(st[0])
                return path[::-1]
            n_exp += 1
            if n_exp > budget:
                return None
            for dout, (dx, dy) in enumerate(DIRS):
                if dout ^ 1 == din:                 # no U-turns
                    continue
                v = (u[0] + dx, u[1] + dy)
                if not (0 <= v[0] < len(xs) and 0 <= v[1] < len(ys)) or v in blocked:
                    continue
                step = abs(xs[v[0]] - xs[u[0]]) + abs(ys[v[1]] - ys[u[1]]) + (BEND if dout != din else 0)
                if soft != 2:
                    o = edge_by.get(ekey(u, v))
                    if o is not None and o != lid and o != STUB_ID + lid:
                        if not soft:
                            continue
                        step += PENALTY * (4 if o.startswith(STUB_ID) else 1)
                    if u != s and not through_ok(u, din, dout, lid):
                        if not soft:
                            continue
                        step += PENALTY
                nc = c + step
                ns = (v, dout)
                if nc < best.get(ns, math.inf):
                    best[ns] = nc
                    prev[ns] = st
                    heapq.heappush(heap, (nc + h(v), nc, ns))
        return None

    def commit(lid, path, sd, gd):
        runs[lid] = (path, sd, gd)
        dirs = [sd] + [_dir(path[k], path[k + 1]) for k in range(len(path) - 1)] + [gd]
        for k in range(len(path) - 1):
            edge_by.setdefault(ekey(path[k], path[k + 1]), lid)
        for k, u in enumerate(path):
            din, dout = dirs[k], dirs[k + 1]
            vt[u][lid] = ("h" if din < 2 else "v") if (din == dout and k not in (0, len(path) - 1)) else "x"

    def uncommit(lid):
        path, _, _ = runs.pop(lid)
        for k in range(len(path) - 1):
            e = ekey(path[k], path[k + 1])
            if edge_by.get(e) == lid:
                del edge_by[e]
        for u in path:
            vt[u].pop(lid, None)

    link_by = {l["id"]: l for l in pair}
    ends_of = {}
    for l in pair:
        A, B = anchor[(l["id"], l["a"])], anchor[(l["id"], l["b"])]
        ends_of[l["id"]] = ((xi[A["stub"][0]], yi[A["stub"][1]]), A["dir"], (xi[B["stub"][0]], yi[B["stub"][1]]), B["dir"] ^ 1)
    queue = sorted(pair, key=lambda l: (abs(centre[l["a"]][0] - centre[l["b"]][0]) + abs(centre[l["a"]][1] - centre[l["b"]][1]), l["id"]))
    queue = [l["id"] for l in queue]
    rips, rip_budget, ripped = 0, 3 * len(pair), defaultdict(int)
    deadline = time.monotonic() + TIME_LIMIT
    while queue:
        lid = queue.pop(0)
        s0, sd, g0, gd = ends_of[lid]
        late = time.monotonic() > deadline          # out of time: no more ripping up, cheapest route straight away
        path = None if late else search(lid, s0, sd, g0, gd, 0)
        if path is None and late:
            path = search(lid, s0, sd, g0, gd, 1) or search(lid, s0, sd, g0, gd, 2)
            if path is not None:
                commit(lid, path, sd, gd)
            continue
        if path is None:                            # no clean route: take the cheapest one, then move the cables in its way
            path = search(lid, s0, sd, g0, gd, 1) or search(lid, s0, sd, g0, gd, 2)
            if path is None:
                continue
            hit = {o for o in conflicts(path, sd, gd, lid) if not o.startswith(STUB_ID) and o in runs}
            if hit and rips < rip_budget and ripped[lid] < 3:
                ripped[lid] += 1
                for o in hit:
                    uncommit(o)
                    queue.append(o)
                    rips += 1
        commit(lid, path, sd, gd)

    routes = {}
    for lid, l in link_by.items():
        A, B = anchor[(lid, l["a"])], anchor[(lid, l["b"])]
        if lid in runs:
            mid = [(xs[i], ys[j]) for i, j in runs[lid][0]]
        else:                                       # should not happen; a plain elbow keeps the cable visible
            mid = [A["stub"], (B["stub"][0], A["stub"][1]), B["stub"]]
        pts = _simplify([(A["x"], A["y"])] + mid + [(B["x"], B["y"])])
        routes[lid] = {"pts": pts, "kind": "free", "ends": [
            {"node": l["a"], "x": A["x"], "y": A["y"], "side": A["side"], "idx": A["idx"], "is_a": True},
            {"node": l["b"], "x": B["x"], "y": B["y"], "side": B["side"], "idx": B["idx"], "is_a": False}]}
    return routes


def _lines(raw):
    """Grid lines from the wanted coordinates: values closer than MERGE collapse onto one line (two cables a unit apart
    would look like one), and wide gaps get extra parallel lanes so busy corridors have room for several cables.
    Returns (sorted lines, {wanted value: line it was merged onto})."""
    vals = sorted(raw)
    lines, mapping = [], {}
    for v in vals:
        if lines and v - lines[-1] < MERGE:
            mapping[v] = lines[-1]
        else:
            lines.append(v)
            mapping[v] = v
    out = [lines[0]]
    for a, b in zip(lines, lines[1:]):
        gap = b - a
        if gap >= 3 * LANE_STEP:
            k = min(MAX_FILL, int(gap // LANE_STEP) - 1)
            mid = (a + b) / 2
            out += [round(mid + (t - (k - 1) / 2) * LANE_STEP, 1) for t in range(k)]
        out.append(b)
    return sorted(set(out)), mapping


def _label_lines(l):
    """Lines of port text at one end of a cable: one port, or for a port-channel its name plus up to 4 members (+ 'more')."""
    if not l.get("lag"):
        return 1
    n = len(l.get("members") or [])
    return 1 + min(n, 4) + (1 if n > 4 else 0)


def _dir(u, v):
    if v[0] > u[0]:
        return 0
    if v[0] < u[0]:
        return 1
    return 2 if v[1] > u[1] else 3


def _simplify(pts):
    out = []
    for p in pts:
        p = (round(p[0], 1), round(p[1], 1))
        if out and p == out[-1]:
            continue
        if len(out) >= 2 and ((out[-2][0] == out[-1][0] == p[0]) or (out[-2][1] == out[-1][1] == p[1])):
            out[-1] = p
        else:
            out.append(p)
    return out
