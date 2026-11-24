"""Layered (Sugiyama-style) layout with orthogonal cable routing.

Guarantees, by construction:
  * no two cables share the same stretch of line (every horizontal run gets its own track, every cable end its own anchor point);
  * no cable passes through a switch (cables only run in the gaps between tiers, or in a private lane for cables that skip a tier);
  * switches that hang off the same parent stay together, rings close with a short side link or a clean U under the tier.

Steps: tiers by hop distance from the core -> lanes (dummy nodes) for cables that skip tiers -> order each tier to reduce crossings ->
x positions by iterative straightening -> anchor points on each switch edge -> track assignment per gap -> real y positions -> polylines.
"""
from __future__ import annotations
import math
import re
from collections import defaultdict, deque

NODE_W, NODE_H = 210, 72
LANE = 30            # width reserved for a cable that passes through a tier
GAP_NODE = 70        # minimum gap between two switches in a tier
GAP_LANE = 26
TRACK = 10           # vertical distance between two horizontal cable runs
LABEL_ZONE = 84      # room above / below a switch for its port labels
GRID_COLS = 8
SUB_GAP = 70


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def _pav(desired, seps):
    """Order-preserving positions closest to `desired` with at least seps[i] between item i and i+1 (isotonic regression)."""
    n = len(desired)
    off = [0.0] * n
    for i in range(1, n):
        off[i] = off[i - 1] + seps[i - 1]
    blocks = []
    for v in (desired[i] - off[i] for i in range(n)):
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    flat = []
    for s, c in blocks:
        flat += [s / c] * c
    return [flat[i] + off[i] for i in range(n)]


def build(nodes, links, x_fixed=None):
    """nodes: dicts with id (+ kind, l3, is_root). links: dicts with id, a, b.
    x_fixed: optional {node_id: centre_x}; those switches keep that x (used after the user drags a switch sideways).
    Returns {"pos": {id: (x, y)} top-left, "routes": {link_id: {"pts": [...], "ends": [...], "kind": str}}}."""
    x_fixed = x_fixed or {}
    node_by = {n["id"]: n for n in nodes}
    pair = [l for l in links if l["a"] in node_by and l["b"] in node_by and l["a"] != l["b"]]
    adj = defaultdict(set)
    for l in pair:
        adj[l["a"]].add(l["b"])
        adj[l["b"]].add(l["a"])
    seen, comps = set(), []
    for n in sorted(node_by, key=_natural):
        if n in seen:
            continue
        comp, q = [], deque([n])
        seen.add(n)
        while q:
            cur = q.popleft()
            comp.append(cur)
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        comps.append(comp)
    linked = sorted((c for c in comps if len(c) > 1), key=lambda c: (-len(c), _natural(c[0])))
    singles = sorted((c[0] for c in comps if len(c) == 1), key=_natural)

    pos, routes, y_cursor = {}, {}, 60.0
    for comp in linked:
        members = set(comp)
        cl = [l for l in pair if l["a"] in members]
        p, r, height = _component(comp, cl, node_by, adj, x_fixed)
        min_x = min(x for x, _ in p.values())
        for n, (x, y) in p.items():
            pos[n] = (60 + x - min_x, y_cursor + y)
        for lid, rt in r.items():
            rt["pts"] = [(60 + x - min_x, y_cursor + y) for x, y in rt["pts"]]
            for e in rt["ends"]:
                e["x"] = 60 + e["x"] - min_x
                e["y"] = y_cursor + e["y"]
            routes[lid] = rt
        y_cursor += height + 160
    for i, n in enumerate(singles):
        pos[n] = (60 + (i % GRID_COLS) * (NODE_W + GAP_NODE), y_cursor + (i // GRID_COLS) * (NODE_H + SUB_GAP))
    return {"pos": pos, "routes": routes}


# --------------------------------------------------------------------------- one connected group
def _levels(comp, node_by, adj):
    fws = {n for n in comp if node_by[n].get("kind") == "firewall"}
    prefer = {n for n in comp if node_by[n].get("l3") or node_by[n].get("is_root")}
    non = [n for n in comp if n not in fws]

    def ecc(start):
        dist, q = {start: 0}, deque([start])
        while q:
            cur = q.popleft()
            for nb in adj[cur]:
                if nb not in dist and nb not in fws:
                    dist[nb] = dist[cur] + 1
                    q.append(nb)
        return max(dist.values())

    eccs = {n: ecc(n) for n in non} if len(non) <= 600 else {}
    level, seen = {}, set()
    for n in sorted(non, key=lambda k: (k not in prefer, eccs.get(k, 0), -len(adj[k]), _natural(k))):
        if n in seen:
            continue
        level[n] = 0
        q = deque([n])
        seen.add(n)
        grp = [n]
        while q:
            cur = q.popleft()
            for nb in sorted(adj[cur], key=lambda k: (-len(adj[k]), _natural(k))):
                if nb not in seen and nb not in fws:
                    seen.add(nb)
                    level[nb] = level[cur] + 1
                    q.append(nb)
                    grp.append(nb)
        for m in adj[n]:                      # a redundant core beside the top switch shares its tier, but only when the
            if (m in prefer and n in prefer and level.get(m) == 1   # configs show both as L3 / root switches: guessing from the
                    and len(adj[m] & adj[n]) >= max(2, (len(adj[n]) - 1) / 2)):   # link count promotes distribution switches by mistake
                level[m] = 0
    for f in sorted(fws, key=_natural):       # firewalls: one tier above their highest neighbour
        nb = [level[m] for m in adj[f] if m in level]
        level[f] = (min(nb) - 1) if nb else 0
    low = min(level.values())
    return {n: v - low for n, v in level.items()}


def _component(comp, links, node_by, adj, x_fixed):
    level = _levels(comp, node_by, adj)
    L = max(level.values())
    layers = [[] for _ in range(L + 1)]
    for n in sorted(comp, key=_natural):
        layers[level[n]].append(n)
    layer_of = dict(level)
    dummies = set()

    # ---- cables: between adjacent tiers (segments), through lanes (dummy nodes) when they skip tiers, or inside one tier
    segs, chains, same = [], {}, []
    for l in sorted(links, key=lambda k: _natural(k["id"])):
        a, b = l["a"], l["b"]
        if level[a] == level[b]:
            same.append(l)
            continue
        up, lo = (a, b) if level[a] < level[b] else (b, a)
        chain = [up]
        for t in range(level[up] + 1, level[lo]):
            d = f"~{l['id']}~{t}"
            dummies.add(d)
            layers[t].append(d)
            layer_of[d] = t
            chain.append(d)
        chain.append(lo)
        chains[l["id"]] = chain
        for i in range(len(chain) - 1):
            segs.append((l["id"], chain[i], chain[i + 1]))
    ups, downs = defaultdict(list), defaultdict(list)
    for _, u, v in segs:
        ups[v].append(u)
        downs[u].append(v)
    sides = defaultdict(list)
    for l in same:
        sides[l["a"]].append(l["b"])
        sides[l["b"]].append(l["a"])

    # ---- order inside each tier
    if x_fixed:
        key0 = {n: x_fixed[n] for n in comp if n in x_fixed}
        for lid, chain in chains.items():
            a, b = key0.get(chain[0]), key0.get(chain[-1])
            for t, d in enumerate(chain[1:-1], 1):
                key0[d] = (a + (b - a) * t / (len(chain) - 1)) if a is not None and b is not None else 0
        for layer in layers:
            layer.sort(key=lambda n: (key0.get(n, 1e9), _natural(n)))
    else:
        for t in range(1, L + 1):
            _sort_by(layers[t], layers, t, ups, sides)
        best, best_n = [list(l) for l in layers], _crossings(layers, downs)
        for it in range(16):                                   # barycentre sweep, then adjacent swaps; keep the best ordering seen
            if it % 2 == 0:
                for t in range(1, L + 1):
                    _sort_by(layers[t], layers, t, ups, sides)
            else:
                for t in range(L - 1, -1, -1):
                    _sort_by(layers[t], layers, t, downs, sides)
            _transpose(layers, downs)
            n = _crossings(layers, downs)
            if n < best_n:
                best, best_n = [list(l) for l in layers], n
        for t, l in enumerate(best):
            layers[t] = l

    # ---- x positions (centres)
    width = lambda n: LANE if n in dummies else NODE_W

    def seps(layer):
        return [(width(a) + width(b)) / 2 + (GAP_LANE if (a in dummies or b in dummies) else GAP_NODE) for a, b in zip(layer, layer[1:])]

    x = {}
    for layer in layers:
        sp, cur = seps(layer), 0.0
        for i, n in enumerate(layer):
            cur = cur + (sp[i - 1] if i else width(n) / 2)
            x[n] = cur
    pinned = {n for n in x_fixed if n in x}
    for n in pinned:
        x[n] = x_fixed[n]

    def relax(t, nbr):
        layer = layers[t]
        if not layer:
            return
        want = []
        for n in layer:
            if n in pinned:
                want.append(x_fixed[n])
            elif nbr[n]:
                want.append(sum(x[m] for m in nbr[n]) / len(nbr[n]))
            else:
                want.append(x[n])
        for n, v in zip(layer, _pav(want, seps(layer))):
            x[n] = v

    for _ in range(18):
        for t in range(1, L + 1):
            relax(t, ups)
        for t in range(L - 1, -1, -1):
            relax(t, downs)

    # ---- tier-internal cables: a short side link when the two switches are neighbours, otherwise a U under the tier
    side_links, u_links = [], []
    pair_count = defaultdict(int)
    for l in same:
        a, b = l["a"], l["b"]
        la = layers[level[a]]
        ia, ib = la.index(a), la.index(b)
        if abs(ia - ib) == 1:
            lft, rgt = (a, b) if ia < ib else (b, a)
            side_links.append((l["id"], lft, rgt, pair_count[(lft, rgt)]))
            pair_count[(lft, rgt)] += 1
        else:
            u_links.append((l["id"], a, b))

    # ---- anchor points on switch edges, ordered by where the other end is (fewest crossings)
    bottom, top = defaultdict(list), defaultdict(list)       # node -> [(sort_x, tie, item)]
    for lid, u, v in segs:
        bottom[u].append((x[v], lid, ("S", lid, u, v)))
        top[v].append((x[u], lid, ("S", lid, u, v)))
    for lid, a, b in u_links:
        bottom[a].append((x[b], lid, ("U", lid, a)))
        bottom[b].append((x[a], lid, ("U", lid, b)))
    anchor, anchor_idx = {}, {}
    for side, table in (("b", bottom), ("t", top)):
        for n, items in table.items():
            items.sort(key=lambda t_: (t_[0], _natural(t_[1])))
            m = len(items)
            for i, (_, _, it) in enumerate(items):
                ax = x[n] if n in dummies else x[n] - NODE_W / 2 + NODE_W * (i + 1) / (m + 1)
                anchor[(it[0], it[1], n, side)] = ax
                anchor_idx[(it[0], it[1], n, side)] = i

    # ---- keep cable ends in one gap apart: two verticals at the same x (one from above, one from below) would share a line
    def nudge(gap):
        tops, bots = [], []
        for lid, u, v in segs:
            if layer_of[u] == gap:
                tops.append((("S", lid, u, "b"), u))
                bots.append((("S", lid, v, "t"), v))
        for lid, a_, b_ in u_links:
            if level[a_] == gap:
                tops += [(("U", lid, a_, "b"), a_), (("U", lid, b_, "b"), b_)]
        for _ in range(8):
            moved = False
            for kb, nb in bots:
                for kt, nt in tops:
                    if abs(anchor[kb] - anchor[kt]) < 5:
                        k, n = (kb, nb) if nb not in dummies else ((kt, nt) if nt not in dummies else (None, None))
                        if k is None:
                            continue
                        step = 6 if anchor[k] + 6 < x[n] + NODE_W / 2 - 4 else -6
                        anchor[k] += step
                        moved = True
            if not moved:
                break

    for gap in range(L + 1):
        nudge(gap)

    # ---- tracks in each gap between two tiers: ordered so that cables cross each other as little as possible
    items_by_gap = defaultdict(list)
    for lid, u, v in segs:
        xt, xb = anchor[("S", lid, u, "b")], anchor[("S", lid, v, "t")]
        if abs(xt - xb) > 0.5:
            items_by_gap[layer_of[u]].append({"k": ("S", lid, u, v), "l": min(xt, xb), "r": max(xt, xb), "u": False, "tops": [xt], "bots": [xb]})
    for lid, a_, b_ in u_links:
        xa, xb = anchor[("U", lid, a_, "b")], anchor[("U", lid, b_, "b")]
        items_by_gap[level[a_]].append({"k": ("U", lid), "l": min(xa, xb), "r": max(xa, xb), "u": True, "tops": [xa, xb], "bots": []})
    track_of, n_tracks = {}, {}
    for gap, items in items_by_gap.items():
        n = len(items)
        preds = [set() for _ in range(n)]                 # preds[j] = items that must sit ABOVE item j to avoid a needless crossing
        for i, A in enumerate(items):
            for j, B in enumerate(items):
                if i == j:
                    continue
                if any(B["l"] + 1 < xx < B["r"] - 1 for xx in A["tops"]):
                    preds[j].add(i)                       # A's vertical drops through B's span: A above B
                if any(B["l"] + 1 < xx < B["r"] - 1 for xx in A["bots"]):
                    preds[i].add(j)                       # A's vertical rises through B's span: B above A
        remaining, track = set(range(n)), 0
        while remaining:
            ready = [i for i in remaining if not (preds[i] & remaining)]
            if not ready:                                 # a cycle: some crossing is unavoidable, break it at the least constrained item
                ready = [min(remaining, key=lambda i: (len(preds[i] & remaining), items[i]["l"]))]
            ready.sort(key=lambda i: (not items[i]["u"], items[i]["l"], str(items[i]["k"])))
            right, placed = -1e18, []
            for i in ready:
                if items[i]["l"] >= right + 12:
                    right = items[i]["r"]
                    placed.append(i)
                    track_of[(gap, items[i]["k"])] = track
            remaining -= set(placed)
            track += 1
        n_tracks[gap] = track

    # ---- real y positions
    layer_y = [0.0]
    for g in range(L):
        layer_y.append(layer_y[-1] + NODE_H + 2 * LABEL_ZONE + max(0, n_tracks.get(g, 0) - 1) * TRACK)

    def track_y(g, k):
        return layer_y[g] + NODE_H + LABEL_ZONE + track_of[(g, k)] * TRACK

    # ---- polylines
    routes = {}
    for lid, chain in chains.items():
        pts = []
        for i in range(len(chain) - 1):
            u, v = chain[i], chain[i + 1]
            g = layer_of[u]
            xt, xb = anchor[("S", lid, u, "b")], anchor[("S", lid, v, "t")]
            pts.append((xt, layer_y[g] + NODE_H))
            if abs(xt - xb) > 0.5:
                ty = track_y(g, ("S", lid, u, v))
                pts += [(xt, ty), (xb, ty)]
            pts.append((xb, layer_y[g + 1]))
            if v in dummies:
                pts.append((xb, layer_y[g + 1] + NODE_H))
        u, v = chain[0], chain[-1]
        routes[lid] = {"pts": _simplify(pts), "kind": "down", "ends": [
            {"node": u, "x": pts[0][0], "y": pts[0][1], "side": "b", "idx": anchor_idx[("S", lid, u, "b")], "is_a": False},
            {"node": v, "x": pts[-1][0], "y": pts[-1][1], "side": "t", "idx": anchor_idx[("S", lid, v, "t")], "is_a": False}]}
    for lid, a, b in u_links:
        g = level[a]
        xa, xb = anchor[("U", lid, a, "b")], anchor[("U", lid, b, "b")]
        ty = track_y(g, ("U", lid))
        y0 = layer_y[g] + NODE_H
        routes[lid] = {"pts": [(xa, y0), (xa, ty), (xb, ty), (xb, y0)], "kind": "row", "ends": [
            {"node": a, "x": xa, "y": y0, "side": "b", "idx": anchor_idx[("U", lid, a, "b")], "is_a": False},
            {"node": b, "x": xb, "y": y0, "side": "b", "idx": anchor_idx[("U", lid, b, "b")], "is_a": False}]}
    for lid, lft, rgt, k in side_links:
        cnt = pair_count[(lft, rgt)]
        yy = layer_y[level[lft]] + NODE_H / 2 + (k - (cnt - 1) / 2) * 14
        xl, xr = x[lft] + NODE_W / 2, x[rgt] - NODE_W / 2
        routes[lid] = {"pts": [(xl, yy), (xr, yy)], "kind": "side", "ends": [
            {"node": lft, "x": xl, "y": yy, "side": "r", "idx": k, "is_a": False},
            {"node": rgt, "x": xr, "y": yy, "side": "l", "idx": k, "is_a": False}]}
    link_by = {l["id"]: l for l in links}
    for lid, rt in routes.items():
        for e in rt["ends"]:
            e["is_a"] = e["node"] == link_by[lid]["a"]

    pos = {n: (x[n] - NODE_W / 2, layer_y[level[n]]) for n in comp}
    return pos, routes, layer_y[-1] + NODE_H


def _cross_pair(upper, lower, downs):
    """Crossings between two adjacent tiers, by counting inversions in the edge end positions."""
    ix = {n: i for i, n in enumerate(lower)}
    ends = []
    for i, u in enumerate(upper):
        ends += sorted(ix[v] for v in downs[u] if v in ix)
    total, seen = 0, []
    for e in ends:                                             # insertion-sort style inversion count (tiers are short)
        total += sum(1 for x in seen if x > e)
        seen.append(e)
    return total


def _crossings(layers, downs):
    return sum(_cross_pair(layers[t], layers[t + 1], downs) for t in range(len(layers) - 1))


def _transpose(layers, downs):
    """Swap neighbouring switches whenever that removes crossings (the second half of the classic ordering pass)."""
    improved = True
    rounds = 0
    while improved and rounds < 4:
        improved, rounds = False, rounds + 1
        for t in range(len(layers) - 1):
            upper, lower = layers[t], layers[t + 1]
            for i in range(len(upper) - 1):
                before = _cross_pair(upper, lower, downs)
                upper[i], upper[i + 1] = upper[i + 1], upper[i]
                if _cross_pair(upper, lower, downs) < before:
                    improved = True
                else:
                    upper[i], upper[i + 1] = upper[i + 1], upper[i]


def _sort_by(layer, layers, t, nbr, sides):
    """Reorder tier t by the average position of its neighbours in the adjacent tier (and of same-tier partners)."""
    if len(layer) < 2:
        return
    ix_all = {}
    for tt in (t - 1, t + 1):
        if 0 <= tt < len(layers):
            for i, n in enumerate(layers[tt]):
                ix_all[n] = i / max(1, len(layers[tt]) - 1)
    cur = {n: i / max(1, len(layer) - 1) for i, n in enumerate(layer)}

    def key(n):
        vals = [ix_all[m] for m in nbr[n] if m in ix_all]
        vals += [cur[m] for m in sides.get(n, []) if m in cur]
        return ((sum(vals) / len(vals)) if vals else cur[n], cur[n], _natural(n))

    layer.sort(key=key)


def _simplify(pts):
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - c[0]) < 0.01) or (abs(a[1] - b[1]) < 0.01 and abs(b[1] - c[1]) < 0.01):
            out.pop(i)
        else:
            i += 1
    return out
