"""Cross-switch analysis: link discovery, access points, firewalls, summary figures."""
from __future__ import annotations
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

from .models import Switch
from .firewalls import detect_firewall
from .utils import canon_port, short_port, port_key, in_subnet, media_from_name, norm_mac, IPV4
from .layered import build as layered_build
from .stdconfig import compare as compare_std
from .findings import build_findings

AP_PLATFORM = re.compile(
    r"\b(AIR-[\w\-]+|C91\d\d\w*|AP-?\d{2,3}\w*|AP\d{3}\w*|UAP\S*|U6-\S+|UniFi|EAP\d+\S*|MR\d{2}\w*|Meraki MR|Ruckus|"
    r"IAP-\S+|Aironet|OAW-AP\S*|WAP\d+\S*|Aruba AP)\b", re.I)
AP_NAME = re.compile(r"^(?:AP|WAP|UAP|EAP)[-_ ]?[\w]*\d|\b(?:access[- ]?point|wifi|wi-fi|wlan)\b", re.I)
AP_DESC = re.compile(r"\bAP\b|\bAP[-_]\w+|\bAP\d+|\baccess[- ]?point\b|\bwi-?fi\b|\bwlan\b", re.I)


@dataclass
class Analysis:
    id: str
    switches: list
    aps: list = field(default_factory=list)
    firewalls: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    links: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    compliance: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)

    def component_rows(self):
        rows = []
        for sw in self.switches:
            for c in sw.components:
                rows.append({"kind": c.kind, "make": c.make or sw.vendor, "model": c.model or "Unknown", "serial": c.serial or "N/A",
                             "port": short_port(c.port), "description": c.descr, "switch_ip": sw.mgmt_ip or "N/A",
                             "switch_hostname": sw.hostname, "switch_id": sw.id})
        return rows

    def summary(self):
        sw_models = Counter((sw.vendor, m.model or "Unknown") for sw in self.switches for m in sw.members)
        ap_models = Counter((a["make"], a["model"] or "Unknown") for a in self.aps)
        fw_unique = {r["fw_id"]: (r["make"], r["model"] or "Unknown") for r in self.firewalls}
        fw_models = Counter(fw_unique.values())
        comp = Counter((r["kind"], r["model"]) for r in self.component_rows())
        return {
            "switch_count": len(self.switches), "member_count": sum(len(s.members) for s in self.switches),
            "stack_count": sum(1 for s in self.switches if s.is_stack), "ap_count": len(self.aps),
            "firewall_count": len(fw_unique), "port_channel_count": sum(len(s.port_channels) for s in self.switches), "component_count": sum(comp.values()),
            "link_count": len(self.links), "blocked_count": sum(1 for l in self.links if l["blocked"]),
            "vendors": dict(Counter(s.vendor for s in self.switches)),
            "switch_models": [{"make": k[0], "model": k[1], "count": v} for k, v in sw_models.most_common()],
            "ap_models": [{"make": k[0], "model": k[1], "count": v} for k, v in ap_models.most_common()],
            "firewall_models": [{"make": k[0], "model": k[1], "count": v} for k, v in fw_models.most_common()],
            "component_models": [{"kind": k[0], "model": k[1], "count": v} for k, v in comp.most_common()],
        }

    def to_dict(self):
        return {"session_id": self.id, "switches": [s.to_dict() for s in self.switches], "aps": self.aps, "firewalls": self.firewalls,
                "components": self.component_rows(), "nodes": self.nodes, "links": self.links, "summary": self.summary(),
                "compliance": self.compliance, "findings": self.findings, "skipped": self.skipped}


# ---------------------------------------------------------------- helpers
def _caps(n):
    return {t.lower() for t in re.split(r"[,\s]+", n.capabilities or "") if t}


def _is_ap(n) -> bool:
    caps = _caps(n)
    blob = f"{n.platform} {n.description}"
    cdp_bridge = n.source == "cdp" and "t" in caps and not caps & {"s", "switch", "r", "router"}   # CDP code T = Trans-Bridge
    return ("w" in caps or "trans-bridge" in caps or cdp_bridge or "wlan" in blob.lower()
            or bool(AP_PLATFORM.search(blob)) or bool(AP_NAME.search(n.remote_name or "")))


def _is_network_gear(n) -> bool:
    """Only neighbours that positively report switch/router/bridge capability. Hosts, phones, printers, servers are ignored."""
    caps = _caps(n)
    if n.source == "lldp":                      # LLDP: S = station only (a host), so it must not count
        return bool(caps & {"b", "r", "bridge", "router"})
    return bool(caps & {"s", "b", "r", "switch", "router", "bridge", "source-route-bridge"})


def _ap_make(text: str) -> str:
    t = text.lower()
    if re.search(r"aironet|air-|c91\d\d|cisco", t):
        return "Cisco"
    if re.search(r"aruba|iap-|\bap-?\d{3}|instant", t):
        return "Aruba"
    if re.search(r"unifi|uap|u6-|ubiquiti", t):
        return "Ubiquiti"
    if re.search(r"\beap\d|tp-link|omada", t):
        return "TP-Link"
    if re.search(r"meraki|\bmr\d{2}", t):
        return "Cisco Meraki"
    if "ruckus" in t:
        return "Ruckus"
    return "Unknown"


def _clean_name(n: str) -> str:
    n = (n or "").strip()
    if re.fullmatch(IPV4, n):
        return n
    return re.split(r"[.(]", n, 1)[0].strip().lower()


class _Index:
    def __init__(self, switches):
        self.by_name, self.by_ip, self.by_mac = {}, {}, {}
        for sw in switches:
            self.by_name[sw.hostname.lower()] = sw
            self.by_name.setdefault(re.sub(r"\.\w+$", "", sw.filename).lower(), sw)
            if sw.mgmt_ip:
                self.by_ip[sw.mgmt_ip] = sw
            for v in sw.svis.values():
                self.by_ip.setdefault(v["ip"], sw)
            for m in sw.members:
                if m.mac:
                    self.by_mac[norm_mac(m.mac)] = sw

    def find(self, n, me):
        sw = (self.by_name.get(_clean_name(n.remote_name)) or (self.by_ip.get(n.remote_ip) if n.remote_ip else None)
              or (self.by_mac.get(norm_mac(n.remote_mac)) if n.remote_mac else None))
        return sw if sw is not None and sw is not me else None

    def find_in_text(self, text, me):
        best = None
        for name, sw in self.by_name.items():
            if sw is me or len(name) < 3:
                continue
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text or "", re.I):
                if best is None or len(name) > len(best[0]):
                    best = (name, sw)
        return best[1] if best else None


# ----------------------------------------------------------------- analyse
def analyze(switches: list[Switch], skipped=None) -> Analysis:
    an = Analysis(id=uuid.uuid4().hex[:12], switches=switches, skipped=skipped or [])
    idx = _Index(switches)
    all_bindings, all_pools = {}, []
    for sw in switches:
        all_bindings.update(sw.dhcp_bindings)
        all_pools += sw.dhcp_pools
    halves, ext_nodes, fw_nodes, fw_rows = [], {}, {}, []
    iface_maps = {sw.id: {port_key(i.name): i for i in sw.interfaces} for sw in switches}
    lag_maps = {sw.id: {port_key(m["port"]): pc["name"] for pc in sw.port_channels for m in pc["members"]} for sw in switches}
    pc_members = {sw.id: {port_key(pc["name"]): [port_key(m["port"]) for m in pc["members"]] for pc in sw.port_channels} for sw in switches}

    def add_firewall(sw, local_port, name, ip, mac, remote_port, hit, vlan=""):
        key = _clean_name(name) or ip or mac or f"{hit['vendor']}@{sw.hostname}:{local_port}"
        node_id = "fw:" + key
        if node_id not in fw_nodes:
            fw_nodes[node_id] = {"id": node_id, "kind": "firewall", "fw_id": f"FW-{len(fw_nodes) + 1}", "hostname": name or hit["model"] or "Firewall",
                                 "ip": ip, "mac": norm_mac(mac) if mac else "", "vendor": hit["vendor"], "model": hit["model"],
                                 "stp_version": "n/a", "firmware": "n/a", "external": False, "is_stack": False, "members": 1,
                                 "source": hit["source"], "connections": []}
        node = fw_nodes[node_id]
        for fld, val in (("ip", ip), ("mac", norm_mac(mac) if mac else ""), ("model", hit["model"])):
            if val and not node[fld]:
                node[fld] = val
        if node["vendor"] == "Unknown" and hit["vendor"] != "Unknown":
            node["vendor"] = hit["vendor"]
        iface = iface_maps[sw.id].get(port_key(local_port))
        row = {"fw_id": node["fw_id"], "make": node["vendor"], "model": node["model"], "hostname": node["hostname"], "ip": node["ip"] or "N/A",
               "mac": node["mac"] or "N/A", "switch_port": short_port(local_port), "firewall_port": short_port(remote_port) if remote_port else "N/A",
               "vlan": (iface.vlan if iface else "") or "", "switch_ip": sw.mgmt_ip or "N/A", "switch_hostname": sw.hostname, "switch_id": sw.id,
               "detected_from": hit["source"]}
        fw_rows.append(row)
        node["connections"].append({"switch_hostname": sw.hostname, "switch_port": row["switch_port"], "firewall_port": row["firewall_port"]})
        halves.append((sw.id, local_port, node_id, remote_port))

    for sw in switches:
        used_ports = set()
        for n in sw.neighbors:
            pk = port_key(n.local_port)
            target = idx.find(n, sw)
            fw = None if target else detect_firewall(n.remote_name, n.platform, n.description)
            if target:
                halves.append((sw.id, n.local_port, target.id, n.remote_port))
                used_ports.add(pk)
            elif fw:
                add_firewall(sw, n.local_port, n.remote_name, n.remote_ip, n.remote_mac, n.remote_port, fw)
                used_ports.add(pk)
            elif _is_ap(n):
                an.aps.append(_ap_row(sw, n.local_port, n.remote_name, n.remote_ip, n.platform or n.description, n.remote_mac,
                                      iface_maps[sw.id].get(pk), all_bindings, all_pools))
                used_ports.add(pk)
            elif (fw := detect_firewall(n.remote_name, n.platform, n.description, n.remote_mac, use_oui=True)) and n.remote_mac:
                add_firewall(sw, n.local_port, n.remote_name, n.remote_ip, n.remote_mac, n.remote_port, fw)
                used_ports.add(pk)
            elif _is_network_gear(n):
                key = "ext:" + (_clean_name(n.remote_name) or n.remote_ip or n.remote_mac)
                if key != "ext:":
                    ext_nodes.setdefault(key, {"id": key, "kind": "external", "hostname": n.remote_name or n.remote_ip, "ip": n.remote_ip,
                                               "vendor": "Not uploaded", "model": n.platform, "stp_version": "n/a", "firmware": "n/a",
                                               "external": True, "is_stack": False, "members": 1})
                    halves.append((sw.id, n.local_port, key, n.remote_port))
                    used_ports.add(pk)
        # fall back to port descriptions ("to SW-2", "FortiGate-100F", "AP-lobby") for ports with no CDP/LLDP entry
        for i in sw.interfaces:
            pk = port_key(i.name)
            if pk in used_ports or not i.description:
                continue
            is_po = i.name.lower().startswith(("port-channel", "po", "lag", "trk", "bridge-agg")) or pk in pc_members[sw.id]
            if is_po and any(m in used_ports for m in pc_members[sw.id].get(pk, [])):
                continue                                   # its member ports already produced the link
            target = idx.find_in_text(i.description, sw)
            if is_po and not target:
                continue
            fw = None if target else detect_firewall(name=i.description)
            if target:
                halves.append((sw.id, i.name, target.id, None))
                used_ports.add(pk)
            elif fw:
                nm = f"{fw['vendor']} {fw['model']}".strip() if fw["model"] and fw["vendor"] != "Unknown" else i.description
                add_firewall(sw, i.name, nm, "", "", "", {**fw, "source": "port description"})
            elif AP_DESC.search(i.description):
                nm = re.search(r"\b(AP[-_ ]?[\w\-]*)", i.description, re.I)
                an.aps.append(_ap_row(sw, i.name, nm.group(1) if nm else i.description, "", "", "", i, all_bindings, all_pools))

    an.links = _merge_links(halves, iface_maps, {s.id: s for s in switches}, lag_maps)
    an.nodes = []
    for s in switches:
        root_text, is_root = _root_info(s, idx)
        an.nodes.append({"id": s.id, "kind": "switch", "hostname": s.hostname, "ip": s.mgmt_ip or "no IP", "vendor": s.vendor, "model": s.primary.model,
                         "stp_version": s.stp_version, "stp_root": root_text, "is_root": is_root, "firmware": s.firmware, "external": False,
                         "is_stack": s.is_stack, "members": len(s.members), "l3": bool(s.l3.get("is_l3"))})
    an.compliance = compare_std(switches)
    an.nodes += list(fw_nodes.values())
    used_ext = {l["a"] for l in an.links} | {l["b"] for l in an.links}
    an.nodes += [n for k, n in ext_nodes.items() if k in used_ext]
    pos = layered_build(an.nodes, an.links)["pos"]
    for n in an.nodes:
        n["x"], n["y"] = pos[n["id"]]
    an.aps.sort(key=lambda a: (a["switch_hostname"].lower(), a["port_sort"]))
    an.findings = build_findings(an)
    an.firewalls = sorted(fw_rows, key=lambda r: (r["switch_hostname"].lower(), [int(x) for x in re.findall(r"\d+", r["switch_port"])]))
    return an


def _merge_links(halves, iface_maps, sw_by_id, lag_maps):
    links, by_ep = [], {}

    def ep(node, port):
        return (node, port_key(port)) if port else None

    for a, ap_, b, bp in halves:
        e1, e2 = ep(a, ap_), ep(b, bp)
        link = by_ep.get(e1) or (by_ep.get(e2) if e2 else None)
        if link is None:  # unmatched half-link whose far side is still unknown
            for l in links:
                if {l["a"], l["b"]} == {a, b} and l["ends"].get(a) is None:
                    link = l
                    break
        if link is None:
            link = {"a": a, "b": b, "ends": {a: ap_, b: bp}}
            links.append(link)
        else:
            if link["ends"].get(a) is None and ap_:
                link["ends"][a] = ap_
            if link["ends"].get(b) is None and bp:
                link["ends"][b] = bp
        for node in (a, b):
            if link["ends"].get(node):
                by_ep[ep(node, link["ends"][node])] = link
    out = []
    for n, l in enumerate(links, 1):
        a, b = l["a"], l["b"]
        pa, pb = l["ends"].get(a) or "", l["ends"].get(b) or ""
        medias, blocked_on, lags = [], [], {}
        for node, port in ((a, pa), (b, pb)):
            sw = sw_by_id.get(node)
            if not sw or not port:
                lags[node] = ""
                continue
            iface = iface_maps[sw.id].get(port_key(port))
            lag = lag_maps[sw.id].get(port_key(port), "") or (canon_port(port) if port_key(port) in {port_key(pc["name"]) for pc in sw.port_channels} else "")
            lags[node] = lag
            if iface and iface.media:
                medias.append(iface.media)
            bk = {port_key(p) for p in sw.blocked_ports}
            if port_key(port) in bk or (lag and port_key(lag) in bk):
                blocked_on.append(node)
        media = "fiber" if "fiber" in medias else ("utp" if "utp" in medias else media_from_name(pa or pb))
        out.append({"id": f"l{n}", "a": a, "b": b, "a_port": short_port(pa), "b_port": short_port(pb), "media": media,
                    "blocked": bool(blocked_on), "blocked_on": blocked_on, "lag_a": lags.get(a, ""), "lag_b": lags.get(b, "")})
    # members of the same port-channel between the same two switches become ONE drawn link
    bundles, final = {}, []
    for l in out:
        member = {"a_port": l["a_port"], "b_port": l["b_port"]}
        if l["lag_a"] or l["lag_b"]:
            key = (l["a"], l["b"], l["lag_a"], l["lag_b"])
            if key in bundles:
                bd = bundles[key]
                bd["members"].append(member)
                bd["blocked"] = bd["blocked"] or l["blocked"]
                bd["blocked_on"] = sorted(set(bd["blocked_on"]) | set(l["blocked_on"]))
                if l["media"] == "fiber":
                    bd["media"] = "fiber"
                continue
            l["members"] = [member]
            bundles[key] = l
        else:
            l["members"] = [member]
        final.append(l)
    for l in final:
        l["lag"] = bool(l["lag_a"] or l["lag_b"])
        l["count"] = len(l["members"])
        if l["lag"]:
            l["a_port"] = short_port(l["lag_a"]) if l["lag_a"] else "/".join(m["a_port"] for m in l["members"] if m["a_port"])
            l["b_port"] = short_port(l["lag_b"]) if l["lag_b"] else "/".join(m["b_port"] for m in l["members"] if m["b_port"])
    return final


def _root_info(sw, idx):
    """Who is the spanning-tree root bridge, as seen from this switch. Returns (text, this_switch_is_root)."""
    if not sw.stp_roots:
        return "unknown (no 'show spanning-tree' output in the backup)", False
    groups, mine = {}, False
    for vlan, r in sw.stp_roots.items():
        owner = idx.by_mac.get(norm_mac(r["mac"])) if r.get("mac") else None
        is_me = bool(r.get("is_root")) or owner is sw
        mine = mine or is_me
        label = f"{sw.hostname} (this switch)" if is_me else (owner.hostname if owner else (r.get("mac") or "unknown"))
        groups.setdefault(label, {"vlans": [], "prio": r.get("priority", "")})["vlans"].append(vlan)
    if len(groups) == 1:
        label, g = next(iter(groups.items()))
        return label + (f" - priority {g['prio']}" if g["prio"] else ""), mine
    parts = [f"{label}: {'VLAN ' if all(v.isdigit() for v in g['vlans']) else ''}{', '.join(g['vlans'][:4])}{'...' if len(g['vlans']) > 4 else ''}"
             for label, g in groups.items()]
    return "per VLAN - " + "; ".join(parts[:4]), mine


def _ap_row(sw, port, name, ip, platform, mac, iface, bindings, pools):
    ip_mode = "Unknown"
    binding = bindings.get(ip) if ip else None
    if not ip and mac:
        for bip, b in bindings.items():
            if b.get("mac") and norm_mac(b["mac"]) == norm_mac(mac):
                ip, binding = bip, b
                break
    if ip:
        ip_mode = "DHCP" if (binding or any(in_subnet(ip, n, m) for n, m in pools)) else "Static"
    subnet = gateway = ""
    if ip:
        for v in sw.svis.values():
            if in_subnet(ip, v["ip"], v["mask"]):
                subnet = v["mask"]
                gateway = sw.gateway if sw.gateway and in_subnet(sw.gateway, v["ip"], v["mask"]) else v["ip"]
                break
    model = re.sub(r"^cisco\s+", "", platform or "", flags=re.I).strip()
    mm = re.search(r"MODEL:\s*(\w+)", model)           # e.g. Aruba LLDP: "ArubaOS (MODEL: 515), Version ..."
    if mm:
        model = ("AP-" + mm.group(1)) if mm.group(1).isdigit() else mm.group(1)
    if len(model) > 60:
        m = AP_PLATFORM.search(model)
        model = m.group(0) if m else model[:60]
    nums = [int(x) for x in re.findall(r"\d+", port)]
    return {"make": _ap_make(f"{platform} {name}"), "model": model or "Unknown", "hostname": name or "Unknown", "ip": ip,
            "ip_mode": ip_mode, "ip_display": ("DHCP" if ip_mode == "DHCP" else (ip or "Unknown")),
            "subnet": subnet or "N/A", "gateway": gateway or "N/A", "port": short_port(port), "port_sort": nums,
            "vlan": iface.vlan if iface else "", "switch_ip": sw.mgmt_ip or "N/A", "switch_hostname": sw.hostname, "switch_id": sw.id}
