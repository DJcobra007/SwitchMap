"""The ONE renderer of the topology diagram (SVG). The on-screen diagram and the SVG/PNG downloads both come from here,
so what you see is exactly what you download.
"""
from __future__ import annotations
from html import escape

from .layered import build, NODE_W, NODE_H
from .freeroute import clean_positions, resolve, route
from .utils import short_port

FIBER, UTP, BLOCKED = "#F2C100", "#1F6FEB", "#F28C28"     # yellow / blue / orange
INK, ACCENT, MUTED = "#12212E", "#0B5563", "#6B7B86"
FW_STROKE, FW_FILL = "#B3261E", "#FDECEA"
FONT = "Segoe UI, Arial, sans-serif"
MONO = "Consolas, 'Cascadia Mono', monospace"


def _end_lines(l, is_a):
    """Text printed at one end of a link: the physical port, or for a bundle the port-channel name followed by every member port."""
    ports = [p for p in ([m["a_port"] if is_a else m["b_port"] for m in l["members"]]) if p]
    if l.get("lag"):
        lag = l["lag_a"] if is_a else l["lag_b"]
        return [short_port(lag) if lag else "Po?"] + ports[:4] + ([f"+{len(ports) - 4} more"] if len(ports) > 4 else [])
    return [ports[0]] if ports else []


def side_counts(routes):
    out = {}
    for r in routes.values():
        for e in r["ends"]:
            out[(e["node"], e["side"])] = out.get((e["node"], e["side"]), 0) + 1
    return out


def end_labels(l, r, side_count, by_id):
    """Port labels at both ends of one cable, placed so neighbouring labels never overlap.
    Returns dicts: kind "text" (x, y baseline, anchor start|middle|end, text, bold) or kind "blk" (x, y = badge centre), each with its switch."""
    out = []
    for e in r["ends"]:
        lines = _end_lines(l, e["is_a"])
        blk = e["node"] in l.get("blocked_on", [])
        if e["side"] in ("l", "r"):                       # side link: text sits above the line, next to the switch edge
            ax, anchor = (e["x"] + 6, "start") if e["side"] == "r" else (e["x"] - 6, "end")
            for k, text in enumerate(lines):
                out.append({"kind": "text", "node": e["node"], "x": ax, "y": e["y"] - 5 - (len(lines) - 1 - k) * 12, "anchor": anchor,
                            "text": text, "bold": bool(l.get("lag") and k == 0)})
            if blk:
                out.append({"kind": "blk", "node": e["node"], "x": ax + (14 if anchor == "start" else -14), "y": e["y"] + 10})
            continue
        n_side = side_count[(e["node"], e["side"])]
        spacing = NODE_W / (n_side + 1)
        rows = min(5, max(1, -(-52 // int(spacing)))) if len(lines) == 1 else 1      # enough label rows that neighbouring port names never overlap
        stagger = (e["idx"] % rows) * 12 if rows > 1 else 0
        if e["side"] == "b" and by_id[e["node"]].get("is_stack"):
            stagger += 7
        for k, text in enumerate(lines):
            y = e["y"] + 13 + stagger + k * 12 if e["side"] == "b" else e["y"] - 8 - stagger - (len(lines) - 1 - k) * 12
            out.append({"kind": "text", "node": e["node"], "x": e["x"], "y": y, "anchor": "middle", "text": text, "bold": bool(l.get("lag") and k == 0)})
        if blk:
            y = e["y"] + (13 + stagger + len(lines) * 12 + 2 if e["side"] == "b" else -8 - stagger - len(lines) * 12 - 2)
            out.append({"kind": "blk", "node": e["node"], "x": e["x"], "y": y - 3})
    return out


def bounds(pos, routes, export):
    xs = [x for x, _ in pos.values()] + [p[0] for r in routes.values() for p in r["pts"]]
    ys = [y for _, y in pos.values()] + [p[1] for r in routes.values() for p in r["pts"]]
    minx, miny = min(xs) - 70, min(ys) - 60
    maxx, maxy = max(xs) + NODE_W + 70, max(ys) + NODE_H + (150 if export else 90)
    return minx, miny, maxx, maxy


def layout(nodes, links, positions=None, free=False, moved=None):
    """Switch positions + cable routes. Tiered layout by default; once the user has dragged a switch freely
    (free=True), every switch stays where it was put and the cables are routed around them (freeroute.py)."""
    ids = {n["id"] for n in nodes}
    given = clean_positions(positions, ids)
    if free and given:
        if len(given) < len(ids):
            auto = build(nodes, links)["pos"]
            given.update({k: v for k, v in auto.items() if k not in given})
        pos = resolve(given, moved if moved in ids else None)
        return pos, route(nodes, links, pos)
    lay = build(nodes, links, {k: v[0] + NODE_W / 2 for k, v in given.items()})
    return lay["pos"], lay["routes"]


def render_diagram(nodes, links, positions=None, export=False, free=False, moved=None):
    if not nodes:
        return {"svg": '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120"><text x="20" y="60" font-family="Arial">No switches to draw</text></svg>',
                "width": 400, "height": 120}
    pos, routes = layout(nodes, links, positions, free, moved)     # tiers or hand-placed switches, collision-free cable routes
    minx, miny, maxx, maxy = bounds(pos, routes, export)
    w, h = maxx - minx, maxy - miny
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.0f} {miny:.0f} {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" font-family="{FONT}">']
    if export:
        o.append(f'<rect x="{minx:.0f}" y="{miny:.0f}" width="{w:.0f}" height="{h:.0f}" fill="#FFFFFF"/>')
    labels, by_id = [], {n["id"]: n for n in nodes}
    side_count = side_counts(routes)
    for l in links:
        r = routes.get(l["id"])
        if not r:
            continue
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in r["pts"])
        col = FIBER if l["media"] == "fiber" else UTP
        kind = "fibre / SFP" if l["media"] == "fiber" else "UTP"
        if l.get("lag"):
            mem = ", ".join(f'{m["a_port"] or "?"} <-> {m["b_port"] or "?"}' for m in l["members"])
            tip = f'Port-channel {l["a_port"] or "?"} <-> {l["b_port"] or "?"} ({l["count"]} link{"s" if l["count"] != 1 else ""}, {kind}): {mem}'
        else:
            tip = f'{l["a_port"] or "?"} <-> {l["b_port"] or "?"} ({kind})'
        if l["blocked"]:
            tip += " - STP blocked"
        width = 8 if l.get("lag") else 3
        rail = f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{width}" stroke-linejoin="round"' + (' opacity=".4"' if l["blocked"] else "") + "/>"
        if l.get("lag") and not l["blocked"]:
            rail += f'<path d="{path}" fill="none" stroke="#FFFFFF" stroke-width="3.5" stroke-linejoin="round"/>'
        if l["blocked"]:
            rail += f'<path d="{path}" fill="none" stroke="{BLOCKED}" stroke-width="4" stroke-dasharray="1 7" stroke-linecap="round" stroke-linejoin="round"/>'
        o.append(f'<g class="link" data-id="{escape(l["id"])}"><title>{escape(tip)}</title>{rail}</g>')
        lab = []
        for it in end_labels(l, r, side_count, by_id):
            if it["kind"] == "text":
                bold = ' font-weight="700"' if it["bold"] else ""
                lab.append(f'<text x="{it["x"]:.1f}" y="{it["y"]:.1f}" text-anchor="{it["anchor"]}" font-size="10.5" font-family="{MONO}"{bold} fill="{INK}" '
                           f'stroke="#FFFFFF" stroke-width="4" paint-order="stroke">{escape(it["text"])}</text>')
            else:
                lab.append(f'<rect x="{it["x"] - 14:.1f}" y="{it["y"] - 6:.1f}" width="28" height="12" rx="6" fill="{BLOCKED}"/>'
                           f'<text x="{it["x"]:.1f}" y="{it["y"] + 3:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="#FFFFFF">BLK</text>')
        labels.append(f'<g class="lbl" data-id="{escape(l["id"])}">' + "".join(lab) + "</g>")
    o += labels
    for n in nodes:
        x, y = pos[n["id"]]
        fw = n.get("kind") == "firewall"
        dash = ' stroke-dasharray="6 4"' if n.get("external") else ""
        stroke = FW_STROKE if fw else (MUTED if n.get("external") else ACCENT)
        fill = FW_FILL if fw else "#FFFFFF"
        tip = (f'{n["hostname"]}\nFirewall: {n["vendor"]} {n.get("model") or ""}\nIP: {n["ip"] or "n/a"}' if fw
               else f'{n["hostname"]}\nSTP: {n["stp_version"]}\nRoot bridge: {n.get("stp_root", "unknown")}\nFirmware: {n["firmware"]}')
        sub = ((n["vendor"] if str(n.get("model") or "~").lower() in str(n["hostname"]).lower() else f'{n["vendor"]} {n.get("model") or ""}'.strip())
               if fw else str(n.get("model") or "")) + (f' - stack of {n["members"]}' if n.get("is_stack") else "")
        g = [f'<g class="node" data-id="{escape(n["id"])}"><title>{escape(tip)}</title>']
        if n.get("is_stack"):
            g.append(f'<rect x="{x + 6:.0f}" y="{y + 6:.0f}" width="{NODE_W}" height="{NODE_H}" rx="6" fill="#DCEBEE" stroke="{stroke}" stroke-width="1.5"/>')
        g.append(f'<rect class="box" x="{x:.0f}" y="{y:.0f}" width="{NODE_W}" height="{NODE_H}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        if fw:
            g.append(f'<text x="{x + 8:.0f}" y="{y + 13:.0f}" font-size="9" font-weight="700" fill="{FW_STROKE}">FIREWALL</text>')
        if n.get("is_root"):
            g.append(f'<rect x="{x + NODE_W - 50:.0f}" y="{y + 4:.0f}" width="46" height="14" rx="7" fill="#1E9E5A"/>'
                     f'<text x="{x + NODE_W - 27:.0f}" y="{y + 14:.0f}" text-anchor="middle" font-size="9" font-weight="700" fill="#FFFFFF">ROOT</text>')
        if n.get("l3") and not fw:
            g.append(f'<rect x="{x + 6:.0f}" y="{y + 4:.0f}" width="26" height="14" rx="7" fill="#7A3FF2"/>'
                     f'<text x="{x + 19:.0f}" y="{y + 14:.0f}" text-anchor="middle" font-size="9" font-weight="700" fill="#FFFFFF">L3</text>')
        g.append(f'<text x="{x + NODE_W / 2:.0f}" y="{y + 31:.0f}" text-anchor="middle" font-size="14" font-weight="700" fill="{INK}">{escape(str(n["hostname"])[:26])}</text>'
                 f'<text x="{x + NODE_W / 2:.0f}" y="{y + 48:.0f}" text-anchor="middle" font-size="12" font-family="{MONO}" fill="{INK}">{escape(str(n["ip"] or ""))}</text>'
                 f'<text x="{x + NODE_W / 2:.0f}" y="{y + 63:.0f}" text-anchor="middle" font-size="10" fill="{MUTED}">{escape(sub[:34])}</text></g>')
        o.append("".join(g))
    if export:
        lx, ly = minx + 30, maxy - 120
        o.append(f'<g font-size="12" fill="{INK}"><text x="{lx}" y="{ly}" font-weight="700">Legend</text>'
                 f'<line x1="{lx}" y1="{ly + 18}" x2="{lx + 40}" y2="{ly + 18}" stroke="{FIBER}" stroke-width="3"/><text x="{lx + 50}" y="{ly + 22}">Fibre / SFP</text>'
                 f'<line x1="{lx}" y1="{ly + 38}" x2="{lx + 40}" y2="{ly + 38}" stroke="{UTP}" stroke-width="3"/><text x="{lx + 50}" y="{ly + 42}">UTP copper</text>'
                 f'<line x1="{lx}" y1="{ly + 58}" x2="{lx + 40}" y2="{ly + 58}" stroke="{BLOCKED}" stroke-width="4" stroke-dasharray="1 7" stroke-linecap="round"/><text x="{lx + 50}" y="{ly + 62}">STP blocked port</text>'
                 f'<line x1="{lx + 190}" y1="{ly + 18}" x2="{lx + 224}" y2="{ly + 18}" stroke="{UTP}" stroke-width="8"/><line x1="{lx + 190}" y1="{ly + 18}" x2="{lx + 224}" y2="{ly + 18}" stroke="#FFFFFF" stroke-width="3.5"/>'
                 f'<text x="{lx + 232}" y="{ly + 22}">Port-channel (name + member ports)</text>'
                 f'<rect x="{lx + 190}" y="{ly + 32}" width="34" height="18" rx="3" fill="{FW_FILL}" stroke="{FW_STROKE}" stroke-width="2"/><text x="{lx + 232}" y="{ly + 46}">Firewall</text>'
                 f'<rect x="{lx + 190}" y="{ly + 58}" width="34" height="14" rx="7" fill="#1E9E5A"/><text x="{lx + 232}" y="{ly + 69}">STP root bridge</text></g>')
    o.append("</svg>")
    return {"svg": "".join(o), "width": round(w), "height": round(h), "positions": {k: [v[0], v[1]] for k, v in pos.items()}}


def render_svg(nodes, links, positions=None, free=False, moved=None) -> str:
    return render_diagram(nodes, links, positions, export=True, free=free, moved=moved)["svg"]
