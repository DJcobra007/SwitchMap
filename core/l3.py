"""Layer-3 view of a switch: SVIs, static routes and routing protocols, ACLs, SNMP traps, DHCP pools / relay.

Works on IOS-style CLI (Cisco, Aruba, TP-Link). Other vendors report {"supported": False}.
"""
from __future__ import annotations
import re

from .utils import IPV4, iter_blocks, to_netmask, network_of, canon_port

STYLE = {"cisco", "aruba_os", "aruba_cx", "tplink", "quantum", "cisco_sb"}
SX350 = {"quantum", "cisco_sb"}           # flat "... exit" blocks for ACLs and DHCP pools
MAX_ROUTES, MAX_ACE = 500, 300


def _ipmask(body: str):
    m = re.search(rf"^\s*ip address\s+({IPV4})\s+({IPV4})", body, re.M) or re.search(rf"^\s*ip address\s+({IPV4})/(\d+)", body, re.M)
    return (m.group(1), to_netmask(m.group(2))) if m else None


def _exit_blocks(cfg: str, header: str):
    """Sx350-style blocks (Quantum QN-SW) whose lines are NOT indented and end with a bare 'exit'."""
    for m in re.finditer(rf"(?m)^{header}\s*\n((?:(?!exit\s*$|interface\s|ip access-list|ip dhcp pool).*\n)*)", cfg):
        yield m, m.group(m.lastindex)


def _acl_uses(body: str):
    """(acl, direction) applied on an interface: Cisco 'ip access-group X in' and Quantum 'service-acl input X'."""
    out = [(a, dr) for a, dr in re.findall(r"^\s*ip access-group\s+(\S+)\s+(in|out)", body, re.M)]
    out += [(a, "in" if dr == "input" else "out") for dr, a in re.findall(r"^\s*service-acl\s+(input|output)\s+(\S+)", body, re.M)]
    return out


def extract_l3(cfg: str, key: str, shows: dict | None = None) -> dict:
    if key not in STYLE:
        return {"supported": False}
    fwd = key in SX350 and bool(re.search(r"IP Forwarding:\s*enabled", "\n".join((shows or {}).values())))
    d = {"supported": True, "routing_enabled": bool(re.search(r"^ip routing\b", cfg, re.M)) or key == "aruba_cx" or fwd,
         "svis": [], "routes": [], "protocols": [], "acls": [], "snmp": {"traps_enabled": [], "hosts": []},
         "dhcp": {"pools": [], "helpers": [], "excluded": [], "snooping": bool(re.search(r"^ip dhcp snooping\s*$", cfg, re.M)),
                  "server_service": not bool(re.search(r"^no service dhcp", cfg, re.M))}}
    applied: dict[str, list] = {}

    def note(acl, where):
        if acl:
            applied.setdefault(acl, []).append(where)

    # ------------------------------------------------ interfaces (SVIs, routed ports, loopbacks) + ACL application
    seen_svi = {}
    for m, body in iter_blocks(cfg, r"^(?:interface\s+[Vv]lan\s*|vlan\s+)(\d+)\s*$"):
        ip = _ipmask(body)
        if not ip:
            continue
        vid = m.group(1)
        desc = (re.search(r'^\s*(?:description|name)\s+"?([^"\n]+?)"?\s*$', body, re.M) or [None, ""])[1]
        helpers = re.findall(rf"^\s*ip helper-address\s+(?:global\s+|vrf\s+\S+\s+)?({IPV4})", body, re.M)
        acls = _acl_uses(body)
        vip = re.findall(rf"^\s*(?:standby|vrrp)\s+\d+\s+(?:ip\s+)?({IPV4})", body, re.M) + re.findall(rf"^\s*active-gateway ip\s+({IPV4})", body, re.M)
        rec = {"name": f"Vlan{vid}", "vlan": vid, "type": "SVI", "description": desc, "ip": ip[0], "mask": ip[1], "helpers": helpers,
               "acl_in": next((a for a, dr in acls if dr == "in"), ""), "acl_out": next((a for a, dr in acls if dr == "out"), ""),
               "virtual_ip": vip, "shutdown": bool(re.search(r"^\s*shutdown\s*$", body, re.M)),
               "vrf": (re.search(r"^\s*vrf (?:forwarding|attach)\s+(\S+)", body, re.M) or [None, ""])[1]}
        seen_svi[vid] = rec
        for a, dr in acls:
            note(a, f"Vlan{vid} ({dr})")
        for h in helpers:
            d["dhcp"]["helpers"].append({"interface": f"Vlan{vid}", "address": h})
    d["svis"] = sorted(seen_svi.values(), key=lambda r: int(r["vlan"]))
    ospf_if = {}
    for m, body in iter_blocks(cfg, r"^interface\s+(\S+)\s*$"):
        name = m.group(1)
        if re.match(r"(?i)vlan", name):
            continue
        for a, dr in _acl_uses(body):
            note(a, f"{canon_port(name)} ({dr})")
        ip = _ipmask(body)
        if ip and (name.lower().startswith("loopback") or re.search(r"^\s*no switchport", body, re.M) or re.match(r"(?i)(port-channel|\d+/\d+/\d+)", name)):
            d["svis"].append({"name": canon_port(name), "vlan": "", "type": "Loopback" if name.lower().startswith("loopback") else "Routed port",
                              "description": (re.search(r"^\s*description\s+(.+?)\s*$", body, re.M) or [None, ""])[1], "ip": ip[0], "mask": ip[1],
                              "helpers": re.findall(rf"^\s*ip helper-address\s+({IPV4})", body, re.M), "acl_in": "", "acl_out": "", "virtual_ip": [],
                              "shutdown": bool(re.search(r"^\s*shutdown\s*$", body, re.M)), "vrf": ""})
        o = re.search(r"^\s*ip ospf\s+(\d+)\s+area\s+(\S+)", body, re.M)
        if o:
            ospf_if.setdefault(o.group(1), []).append(f"{canon_port(name)} -> area {o.group(2)}")
    for m, body in iter_blocks(cfg, r"^line vty.*$"):
        for a in re.findall(r"^\s*access-class\s+(\S+)", body, re.M):
            note(a, "VTY (management access)")

    # ---------------------------------------------------------------- static routes
    for m in re.finditer(rf"^ip route\s+(?:vrf\s+(\S+)\s+)?({IPV4})(?:/(\d+)|\s+({IPV4}))\s+(\S+)(.*)$", cfg, re.M):
        vrf, net, plen, mask, nh, rest = m.groups()
        mask = to_netmask(plen) if plen else mask
        ad = re.search(r"\b(\d{1,3})\b", rest or "")
        nm = re.search(r"name\s+(\S+)", rest or "")
        d["routes"].append({"prefix": network_of(net, mask) or f"{net}/{mask}", "next_hop": nh, "ad": ad.group(1) if ad else "1", "vrf": vrf or "",
                            "name": nm.group(1) if nm else "", "default": net == "0.0.0.0" and mask == "0.0.0.0"})
        if len(d["routes"]) >= MAX_ROUTES:
            break
    if key in SX350:            # Sx350-style CLI: 'ip default-gateway' is the default route, also in layer-3 mode
        for gw in re.findall(rf"^ip default-gateway\s+({IPV4})", cfg, re.M):
            d["routes"].append({"prefix": "0.0.0.0/0", "next_hop": gw, "ad": "1", "vrf": "", "name": "ip default-gateway", "default": True})
    if d["routes"]:
        d["protocols"].append({"protocol": "Static", "id": "", "router_id": "", "networks": [], "neighbors": [], "redistribute": [],
                               "notes": [f"{len(d['routes'])} static route(s), {sum(1 for r in d['routes'] if r['default'])} default"]})

    # --------------------------------------------------------- dynamic protocols
    for m, body in iter_blocks(cfg, r"^router (ospf|eigrp|bgp|rip|isis)\s*(\S*)(?:\s+vrf\s+(\S+))?\s*$"):
        proto, pid = m.group(1).upper(), m.group(2)
        rid = re.search(rf"^\s*(?:router-id|bgp router-id)\s+({IPV4})", body, re.M)
        nets = [" ".join(x.split()) for x in re.findall(r"^\s*network\s+(.+?)\s*$", body, re.M)]
        nets += [f"{a} area {ar}" for a, ar in re.findall(rf"^\s*area\s+(\S+)\s+(?:range|nssa|stub)\s*(\S*)", body, re.M)] if False else []
        nbrs = [f"{ip} AS {asn}" for ip, asn in re.findall(rf"^\s*neighbor\s+({IPV4})\s+remote-as\s+(\S+)", body, re.M)]
        red = re.findall(r"^\s*redistribute\s+(\S+)", body, re.M)
        notes = []
        if re.search(r"^\s*passive-interface default", body, re.M):
            notes.append("passive-interface default")
        if re.search(r"^\s*default-information originate", body, re.M):
            notes.append("default-information originate")
        if proto == "OSPF" and ospf_if.get(pid):
            nets += ospf_if[pid]
        d["protocols"].append({"protocol": proto, "id": pid, "router_id": rid.group(1) if rid else "", "networks": nets[:60],
                               "neighbors": nbrs, "redistribute": red, "notes": notes})
    if not d["routing_enabled"] and (d["protocols"] or len(d["svis"]) > 1) and key != "aruba_cx":
        d["routing_enabled"] = any(p["protocol"] != "Static" for p in d["protocols"]) or bool(re.search(r"^ip routing", cfg, re.M))

    # ------------------------------------------------------------------- ACLs
    acls = {}
    for m, body in iter_blocks(cfg, r"^(?:ip|ipv6)?\s*access-list\s+(?:(standard|extended|resequence)\s+)?(?:ip\s+)?(\S+)\s*$"):
        name = m.group(2)
        if name in ("extended", "standard"):
            continue
        aces = [" ".join(l.split()) for l in body.split("\n") if l.strip()]
        acls[name] = {"name": name, "type": (m.group(1) or "named").capitalize(), "entries": aces[:MAX_ACE], "count": len(aces)}
    if key in SX350:
        for m, body in _exit_blocks(cfg, r"ip access-list\s+(?:(extended|standard|ipv6)\s+)?(\S+)"):
            aces = [" ".join(l.split()) for l in body.split("\n") if l.strip()]
            acls[m.group(2)] = {"name": m.group(2), "type": (m.group(1) or "named").capitalize(), "entries": aces[:MAX_ACE], "count": len(aces)}
    for num, rest in re.findall(r"^access-list\s+(\d+)\s+(.+?)\s*$", cfg, re.M):
        a = acls.setdefault(num, {"name": num, "type": "Standard" if int(num) < 100 or 1300 <= int(num) < 2000 else "Extended", "entries": [], "count": 0})
        a["entries"].append(" ".join(rest.split()))
        a["count"] += 1
    for name, a in acls.items():
        a["applied_to"] = applied.get(name, [])
    d["acls"] = sorted(acls.values(), key=lambda a: a["name"])
    d["acl_undefined"] = [{"name": n, "where": w} for n, w in applied.items() if n not in acls]
    for a in re.findall(r"^snmp-server community\s+\S+\s+\S+\s+(\S+)\s*$", cfg, re.M):
        if a in acls:
            acls[a]["applied_to"].append("SNMP community")

    # ------------------------------------------------------------ SNMP traps
    for line in re.findall(r"^snmp-server enable traps\s*(.*)$", cfg, re.M):
        d["snmp"]["traps_enabled"].append(line.strip() or "all")
    for m in re.finditer(r"^snmp-server host\s+(\S+)(.*)$", cfg, re.M):
        rest = m.group(2)
        ver = re.search(r"version\s+(\S+)", rest)
        d["snmp"]["hosts"].append({"host": m.group(1), "type": "informs" if "informs" in rest else "traps", "version": ver.group(1) if ver else "",
                                   "source": (re.search(r"(?:trap-source|source-interface)\s+(\S+)", cfg) or [None, ""])[1]})
    src = re.search(r"^snmp-server (?:trap-source|source-interface(?: (?:traps|informs))?)\s+(\S+)", cfg, re.M)
    d["snmp"]["trap_source"] = src.group(1) if src else ""

    # -------------------------------------------------------------- DHCP pools
    for m, body in iter_blocks(cfg, r"^ip dhcp pool\s+(\S+)\s*$"):
        net = re.search(rf"^\s*network\s+({IPV4})\s*(?:/(\d+)|({IPV4}))?", body, re.M)
        mask = to_netmask(net.group(2)) if net and net.group(2) else (net.group(3) if net and net.group(3) else "")
        d["dhcp"]["pools"].append({
            "name": m.group(1), "network": network_of(net.group(1), mask) if net and mask else (net.group(1) if net else ""),
            "gateway": (re.search(rf"^\s*default-router\s+(.+?)\s*$", body, re.M) or [None, ""])[1],
            "dns": (re.search(rf"^\s*dns-server\s+(.+?)\s*$", body, re.M) or [None, ""])[1],
            "domain": (re.search(r"^\s*domain-name\s+(\S+)", body, re.M) or [None, ""])[1],
            "lease": " ".join((re.search(r"^\s*lease\s+(.+?)\s*$", body, re.M) or [None, ""])[1].split()) or "default"})
    if key in SX350:
        for m, body in _exit_blocks(cfg, r"ip dhcp pool network\s+(\S+)"):
            a = re.search(rf"^\s*address\s+(?:low\s+)?({IPV4})(?:\s+high\s+({IPV4}))?\s+({IPV4})", body, re.M)
            d["dhcp"]["pools"].append({
                "name": m.group(1), "network": network_of(a.group(1), a.group(3)) if a else "",
                "gateway": (re.search(r"^\s*default-router\s+(.+?)\s*$", body, re.M) or [None, ""])[1],
                "dns": (re.search(r"^\s*dns-server\s+(.+?)\s*$", body, re.M) or [None, ""])[1],
                "domain": (re.search(r"^\s*domain-name\s+(\S+)", body, re.M) or [None, ""])[1],
                "lease": " ".join((re.search(r"^\s*lease\s+(.+?)\s*$", body, re.M) or [None, ""])[1].split()) or "default"})
    d["dhcp"]["excluded"] = [" - ".join(x.split()) for x in re.findall(rf"^ip dhcp excluded-address\s+(?:vrf \S+\s+)?(.+?)\s*$", cfg, re.M)]
    d["is_l3"] = bool(d["routing_enabled"] and (len([s for s in d["svis"] if s["type"] == "SVI"]) > 1 or d["protocols"] or d["dhcp"]["pools"])) \
        or any(p["protocol"] not in ("Static",) for p in d["protocols"])
    return d
