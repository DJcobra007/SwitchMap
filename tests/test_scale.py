"""Layout / cable-routing checks on awkward topologies (rings, ring-of-rings, dual-homed campus, 150-switch tree, random meshes).
A diagram FAILS if (1) two cables share any stretch of line, (2) a cable passes through a switch, or (3) two switches overlap."""
import os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
from core.layered import build, NODE_W, NODE_H
from core.freeroute import resolve, route


def mk(names, edges, l3=(), fw=()):
    nodes = [{"id": n, "kind": "firewall" if n in fw else "switch", "l3": n in l3, "is_root": False} for n in names]
    links = [{"id": f"l{i}", "a": a, "b": b} for i, (a, b) in enumerate(edges)]
    return nodes, links


def segments(pts):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if abs(x1 - x2) < 0.01 and abs(y1 - y2) < 0.01:
            continue
        assert abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01, ("cable is not axis-aligned", (x1, y1, x2, y2))
        yield ("v", x1, min(y1, y2), max(y1, y2)) if abs(x1 - x2) < 0.01 else ("h", y1, min(x1, x2), max(x1, x2))


def check(name, nodes, links, lay=None):
    lay = lay or build(nodes, links)
    pos, routes = lay["pos"], lay["routes"]
    assert set(pos) == {n["id"] for n in nodes}, name
    assert set(routes) == {l["id"] for l in links}, (name, "missing routes")
    ids = list(pos)
    for i in range(len(ids)):                                              # switches must not overlap
        for j in range(i + 1, len(ids)):
            (ax, ay), (bx, by) = pos[ids[i]], pos[ids[j]]
            assert abs(ax - bx) >= NODE_W - 1 or abs(ay - by) >= NODE_H - 1, (name, "switches overlap", ids[i], ids[j])
    buckets, segs_all = {}, []
    for lid, r in routes.items():
        for kind, c, lo, hi in segments(r["pts"]):
            buckets.setdefault((kind, round(c / 0.5)), []).append((lo, hi, lid))
            segs_all.append((lid, kind, c, lo, hi))
    overlaps = 0
    for key, items in buckets.items():                                     # (1) shared stretches of line
        items.sort()
        for (lo1, hi1, l1), (lo2, hi2, l2) in zip(items, items[1:]):
            if l1 != l2 and min(hi1, hi2) - max(lo1, lo2) > 1.0:
                overlaps += 1
    through = 0
    for lid, kind, c, lo, hi in segs_all:                                  # (2) cable through a switch (interior, shrunk by 2px)
        for nid, (nx, ny) in pos.items():
            x0, x1, y0, y1 = nx + 2, nx + NODE_W - 2, ny + 2, ny + NODE_H - 2
            if kind == "v" and x0 < c < x1 and lo < y1 and hi > y0:
                through += 1
            if kind == "h" and y0 < c < y1 and lo < x1 and hi > x0:
                through += 1
    vs = [(c, lo, hi, lid) for lid, k, c, lo, hi in segs_all if k == "v"]
    hs = [(c, lo, hi, lid) for lid, k, c, lo, hi in segs_all if k == "h"]
    crossings = sum(1 for y, a, b, l1 in hs for x, c, d, l2 in vs if l1 != l2 and a < x < b and c < y < d)
    print(f"{name:34} {len(nodes):4} switches {len(links):4} cables | overlapping cables: {overlaps} | cables through switches: {through} | crossings: {crossings}")
    assert overlaps == 0 and through == 0, name
    return crossings


random.seed(7)
# 1. plain tree, 150 switches
names = ["core1", "core2"] + [f"d{i}" for i in range(10)] + [f"a{i}" for i in range(130)]
edges = [("core1", "core2")] + [(c, f"d{i}") for i in range(10) for c in ("core1", "core2")]
edges += [(f"d{i % 10}", f"a{i}") for i in range(130)]
check("tree, 150 switches", *mk(names, edges, l3={"core1", "core2"}))
# 2. one big ring of 12 hanging off a core, plus a firewall
ring = [f"r{i}" for i in range(12)]
check("ring of 12 + core + firewall", *mk(["core", "fw"] + ring, [("fw", "core"), ("core", "r0"), ("core", "r11")] + [(ring[i], ring[i + 1]) for i in range(11)], l3={"core"}, fw={"fw"}))
# 3. ring of rings: 5 rings of 6 switches, each ring closed on the core
names, edges = ["core"], []
for k in range(5):
    r = [f"R{k}-{i}" for i in range(6)]
    names += r
    edges += [("core", r[0]), ("core", r[5])] + [(r[i], r[i + 1]) for i in range(5)]
check("ring of rings (5 x 6)", *mk(names, edges, l3={"core"}))
# 4. dual-homed campus: stacked cores with 4 parallel links, dist pairs interlinked, access dual-homed + daisy chains, 2 HA firewalls
names = ["CORE-A", "CORE-B", "FW1", "FW2"] + [f"D{i}" for i in range(8)] + [f"A{i}" for i in range(40)]
edges = [("CORE-A", "CORE-B")] * 4 + [(f, c) for f in ("FW1", "FW2") for c in ("CORE-A", "CORE-B")]
for i in range(8):
    edges += [("CORE-A", f"D{i}"), ("CORE-B", f"D{i}")] + ([(f"D{i}", f"D{i + 1}")] if i % 2 == 0 else [])
for i in range(40):
    edges += [(f"D{i % 8}", f"A{i}"), (f"D{(i + 1) % 8}", f"A{i}")] + ([(f"A{i}", f"A{i + 1}")] if i % 9 == 0 and i < 39 else [])
check("dual-homed campus (48 switches)", *mk(names, edges, l3={"CORE-A", "CORE-B"}, fw={"FW1", "FW2"}))
# 5. random meshes
for n_nodes, n_edges in ((30, 50), (60, 120), (100, 190)):
    names = [f"m{i}" for i in range(n_nodes)]
    edges = {(names[i], names[random.randrange(i)]) for i in range(1, n_nodes)}
    while len(edges) < n_edges:
        a, b = random.sample(names, 2)
        edges.add((a, b))
    check(f"random mesh {n_nodes}/{n_edges}", *mk(names, sorted(edges)))
# 6. unlinked switches + two separate networks
check("two networks + 5 unlinked", *mk([f"x{i}" for i in range(6)] + [f"y{i}" for i in range(4)] + [f"u{i}" for i in range(5)],
      [(f"x{i}", f"x{i + 1}") for i in range(5)] + [("y0", "y1"), ("y1", "y2"), ("y2", "y3"), ("y3", "y0")]))

# 7. hand-placed switches (the user dragged switches in any direction): cables are routed around wherever the switches are
def free(name, nodes, links, moves):
    pos = dict(build(nodes, links)["pos"])
    rnd = random.Random(len(nodes))
    span_x = max(x for x, _ in pos.values()) + NODE_W
    span_y = max(y for _, y in pos.values()) + NODE_H
    picks = list(pos) if moves == "all" else rnd.sample(list(pos), moves)
    for n in picks:                      # one switch dragged at a time, as in the browser
        pos[n] = (rnd.uniform(0, span_x), rnd.uniform(0, span_y))
        pos = resolve(pos, n)
    t = time.time()
    routes = route(nodes, links, pos)
    check(f"{name} [{time.time() - t:.1f}s]", nodes, links, {"pos": pos, "routes": routes})

free("free: ring of 12, 3 moved", *mk(["core", "fw"] + ring, [("fw", "core"), ("core", "r0"), ("core", "r11")] + [(ring[i], ring[i + 1]) for i in range(11)], l3={"core"}, fw={"fw"}), 3)
free("free: ring of 12, all scattered", *mk(["core", "fw"] + ring, [("fw", "core"), ("core", "r0"), ("core", "r11")] + [(ring[i], ring[i + 1]) for i in range(11)], l3={"core"}, fw={"fw"}), "all")
names = ["CORE-A", "CORE-B", "FW1", "FW2"] + [f"D{i}" for i in range(8)] + [f"A{i}" for i in range(40)]
edges = [("CORE-A", "CORE-B")] * 4 + [(f, c) for f in ("FW1", "FW2") for c in ("CORE-A", "CORE-B")]
for i in range(8):
    edges += [("CORE-A", f"D{i}"), ("CORE-B", f"D{i}")] + ([(f"D{i}", f"D{i + 1}")] if i % 2 == 0 else [])
for i in range(40):
    edges += [(f"D{i % 8}", f"A{i}"), (f"D{(i + 1) % 8}", f"A{i}")] + ([(f"A{i}", f"A{i + 1}")] if i % 9 == 0 and i < 39 else [])
campus = mk(names, edges, l3={"CORE-A", "CORE-B"}, fw={"FW1", "FW2"})
free("free: campus, 1 moved", *campus, 1)
free("free: campus, 8 moved", *campus, 8)
free("free: campus, all scattered", *campus, "all")
names = ["core1", "core2"] + [f"d{i}" for i in range(10)] + [f"a{i}" for i in range(130)]
edges = [("core1", "core2")] + [(c, f"d{i}") for i in range(10) for c in ("core1", "core2")] + [(f"d{i % 10}", f"a{i}") for i in range(130)]
free("free: tree 142, 10 moved", *mk(names, edges, l3={"core1", "core2"}), 10)
random.seed(11)
names = [f"m{i}" for i in range(100)]
edges = {(names[i], names[random.randrange(i)]) for i in range(1, 100)}
while len(edges) < 190:
    edges.add(tuple(random.sample(names, 2)))
free("free: random mesh 100/190, 15 moved", *mk(names, sorted(edges)), 15)
print("all layout checks passed")
