"""Misconfiguration findings: per-switch audit results + checks that need several switches (links, port-channels, STP, VLAN names, firmware)
+ deviations from the fleet's standard configuration. Every finding names the switch and says what evidence it rests on.
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict

from .utils import canon_port, port_key, short_port

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}
STD_SEVERITY = {"aaa": ("High", "Medium"), "ntp": ("Medium", "Low"), "snmp": ("Medium", "Low"), "dns": ("Low", "Low"), "domain": ("Low", "Low")}


def _speed(v: str):
    v = (v or "").lower().replace("a-", "")
    m = re.fullmatch(r"(\d+)(g?)", v)
    return int(m.group(1)) * (1000 if m.group(2) else 1) if m else None


def _duplex(v: str):
    v = (v or "").lower().replace("a-", "")
    return v if v in ("full", "half") else None


def _stp_family(mode: str) -> str:
    m = (mode or "").lower()
    return "MST" if "mst" in m else ("Rapid-PVST/RSTP" if m in ("rapid-pvst", "rstp") else ("PVST" if "pvst" in m else m))


def build_findings(an) -> dict:
    sws = {s.id: s for s in an.switches}
    out = []

    def add(sw, sev, cat, title, detail="", where=""):
        out.append({"severity": sev, "category": cat, "switch_id": sw.id if sw else "", "hostname": sw.hostname if sw else "(several switches)",
                    "title": title, "detail": detail, "where": where})

    for s in an.switches:                                   # 1. per-switch audit
        for f in s.audit:
            out.append({**f, "switch_id": s.id, "hostname": s.hostname})

    # 2. identity clashes
    for label, getter in (("management IP", lambda s: s.mgmt_ip), ("hostname", lambda s: s.hostname.lower())):
        seen = defaultdict(list)
        for s in an.switches:
            if getter(s):
                seen[getter(s)].append(s)
        for val, group in seen.items():
            if len(group) > 1:
                for s in group:
                    add(s, "High", "Management", f"Duplicate {label}", f"{val} is also used by " + ", ".join(g.hostname for g in group if g is not s))

    # 3. links between uploaded switches
    ifc = {s.id: {port_key(i.name): i for i in s.interfaces} for s in an.switches}
    pcs = {s.id: {port_key(p["name"]): p for p in s.port_channels} for s in an.switches}
    for l in an.links:
        a, b = sws.get(l["a"]), sws.get(l["b"])
        if not a or not b:
            continue
        if l.get("lag_a") and l.get("lag_b"):
            pa, pb = pcs[a.id].get(port_key(l["lag_a"])), pcs[b.id].get(port_key(l["lag_b"]))
            if pa and pb and pa["protocol"] and pb["protocol"] and pa["protocol"] != pb["protocol"]:
                add(a, "High", "Port-channel", "Port-channel protocol mismatch",
                    f"{a.hostname} {short_port(l['lag_a'])} is {pa['protocol']} but {b.hostname} {short_port(l['lag_b'])} is {pb['protocol']}; the bundle will not form.", short_port(l["lag_a"]))
        elif l.get("lag_a") or l.get("lag_b"):
            one, other = (a, b) if l.get("lag_a") else (b, a)
            lag = l["lag_a"] if l.get("lag_a") else l["lag_b"]
            add(one, "High", "Port-channel", "Port-channel configured on one side only",
                f"{one.hostname} bundles these ports into {short_port(lag)} but {other.hostname} has them as individual ports.", short_port(lag))
        for m in l["members"]:
            ia, ib = ifc[a.id].get(port_key(canon_port(m["a_port"]))), ifc[b.id].get(port_key(canon_port(m["b_port"])))
            if not ia or not ib:
                continue
            where = f"{a.hostname} {m['a_port']} <-> {b.hostname} {m['b_port']}"
            if ia.mode in ("trunk", "access") and ib.mode in ("trunk", "access") and ia.mode != ib.mode:
                add(a, "High", "Links", "Trunk on one end, access on the other", f"{where}: {ia.mode} vs {ib.mode}.", m["a_port"])
            if ia.mode == ib.mode == "trunk" and ia.native_vlan and ib.native_vlan and ia.native_vlan != ib.native_vlan:
                add(a, "High", "Links", "Native VLAN mismatch on trunk", f"{where}: native VLAN {ia.native_vlan} vs {ib.native_vlan}.", m["a_port"])
            da, db = _duplex(ia.duplex), _duplex(ib.duplex)
            if da and db and da != db:
                add(a, "High", "Links", "Duplex mismatch", f"{where}: {da} vs {db}.", m["a_port"])
            sa, sb = _speed(ia.speed), _speed(ib.speed)
            if sa and sb and sa != sb:
                add(a, "Medium", "Links", "Speed mismatch", f"{where}: {sa} Mb/s vs {sb} Mb/s.", m["a_port"])
        if l["blocked"]:
            for node in l.get("blocked_on", []):
                s = sws.get(node)
                add(s, "Info", "Spanning tree", "A port is blocked by spanning tree",
                    f"{a.hostname} <-> {b.hostname} ({l['a_port']} / {l['b_port']}): normal in a ring, but this link carries no traffic.", "")

    # 4. port-channel health on every switch
    for s in an.switches:
        for pc in s.port_channels:
            bad = [(m["port"], m["flag"]) for m in pc["members"] if m["flag"] and m["flag"] not in ("P",)]
            for port, flag in bad:
                sev = "High" if flag in ("I", "s", "w") else "Medium"
                meaning = {"D": "down", "I": "stand-alone (not bundled)", "s": "suspended", "H": "hot-standby", "w": "waiting", "U": "in use"}.get(flag, flag)
                add(s, sev, "Port-channel", f"{short_port(pc['name'])} member {short_port(port)} is not active in the bundle", f"Flag ({flag}): {meaning}.", short_port(port))
            if len(pc["members"]) == 1:
                add(s, "Low", "Port-channel", f"{short_port(pc['name'])} has only one member port", "A single-member port-channel gives no extra bandwidth or redundancy.", short_port(pc["name"]))

    # 5. spanning tree consistency
    modes = {s.id: _stp_family(s.stp_mode) for s in an.switches if s.stp_mode}
    if len(set(modes.values())) > 1:
        common = Counter(modes.values()).most_common(1)[0][0]
        for sid, m in modes.items():
            if m != common:
                add(sws[sid], "Medium", "Spanning tree", "STP mode differs from the rest of the network", f"{m} here, {common} on most switches.")
    nodes = {n["id"]: n for n in an.nodes}
    has_core = any(n.get("l3") for n in nodes.values() if n["kind"] == "switch")
    for n in nodes.values():
        if n["kind"] == "switch" and n.get("is_root") and has_core and not n.get("l3"):
            add(sws[n["id"]], "Medium", "Spanning tree", "Root bridge is not a core / L3 switch", "An access switch is the STP root; set a lower priority on the core.")
    comp = _components(an)
    for members in comp:
        roots = {}
        for sid in members:
            r = sws[sid].stp_roots
            if r:
                k = "1" if "1" in r else sorted(r)[0]
                roots[sid] = r[k]["mac"]
        if len(set(roots.values())) > 1:
            for sid, mac in roots.items():
                add(sws[sid], "Medium", "Spanning tree", "Switches in the same network disagree about the root bridge", f"This switch sees root {mac}; others see a different one.")

    # 6. VLAN naming and firmware consistency
    names = defaultdict(lambda: defaultdict(list))
    for s in an.switches:
        for v in s.vlans:
            if v.name and not re.fullmatch(r"VLAN0*\d+|default", v.name, re.I):
                names[v.id][v.name.lower()].append(s.hostname)
    for vid, byname in sorted(names.items()):
        if len(byname) > 1:
            add(None, "Low", "VLAN", f"VLAN {vid} has different names on different switches", "; ".join(f"'{n}' on {len(h)}" for n, h in byname.items()), f"VLAN {vid}")
    fw = defaultdict(lambda: defaultdict(list))
    for s in an.switches:
        if s.firmware and s.firmware != "Not in backup":
            fw[s.primary.model][s.firmware].append(s.hostname)
    for model, versions in fw.items():
        if len(versions) > 1 and model:
            add(None, "Low", "Firmware", f"{model} switches run different software versions", "; ".join(f"{v} on {len(h)}" for v, h in versions.items()), model)

    # 7. deviations from the standard configuration
    for cat in an.compliance.get("categories", []):
        miss_sev, diff_sev = STD_SEVERITY[cat["key"]]
        for r in cat["rows"]:
            if r["status"] == "match":
                continue
            if r["status"] == "missing":
                add(sws[r["switch_id"]], miss_sev, "Standard config", f"{cat['label']} is not configured", f"Standard: {cat['baseline']}", cat["label"])
            else:
                add(sws[r["switch_id"]], diff_sev, "Standard config", f"{cat['label']} differs from the standard", f"Has: {r['value']}  |  Standard: {cat['baseline']}", cat["label"])

    out.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["hostname"].lower(), f["category"], f["title"]))
    counts = Counter(f["severity"] for f in out)
    return {"items": out, "counts": {k: counts.get(k, 0) for k in SEV_ORDER}, "categories": sorted({f["category"] for f in out})}


def _components(an):
    adj = defaultdict(set)
    ids = {s.id for s in an.switches}
    for l in an.links:
        if l["a"] in ids and l["b"] in ids:
            adj[l["a"]].add(l["b"]); adj[l["b"]].add(l["a"])
    seen, comps = set(), []
    for s in an.switches:
        if s.id in seen:
            continue
        stack, comp = [s.id], []
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n); comp.append(n); stack += list(adj[n] - seen)
        comps.append(comp)
    return comps
