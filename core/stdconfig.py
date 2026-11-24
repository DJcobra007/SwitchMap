"""Pull the 'standard configuration' facts (AAA, NTP, SNMP, DNS, domain name) out of a config, then compare them across switches.

Community strings are never stored in clear: only their access level and a short fingerprint, so two switches can be
compared without exposing the secret.
"""
from __future__ import annotations
import hashlib
import re
from collections import Counter

from .utils import IPV4, iter_blocks

SUPPORTED = {"cisco", "aruba_os", "aruba_cx", "alcatel", "tplink", "comware", "quantum", "cisco_sb"}
CATEGORIES = [("aaa", "AAA"), ("ntp", "NTP"), ("snmp", "SNMP"), ("dns", "DNS"), ("domain", "Domain name")]
HOST = rf"(?:{IPV4}|[A-Za-z][\w.\-]+)"


def _fp(secret: str) -> str:
    return hashlib.sha1(secret.encode()).hexdigest()[:6]


def _uniq(seq):
    return sorted(dict.fromkeys(seq))


def extract_std(cfg: str, key: str) -> dict:
    d = {"supported": key in SUPPORTED and bool(cfg.strip()), "aaa": {}, "ntp": {}, "snmp": {}, "dns": {}, "domain": {}}
    if not d["supported"]:          # unknown vendor, or no running-config in the backup: unknown, not "missing"
        return d

    def find(rx, flags=re.M | re.I):
        return re.findall(rx, cfg, flags)

    # ---------------------------------------------------------------- AAA
    authn = find(r"^aaa authentication (.+?)\s*$")
    authz = find(r"^aaa authorization (.+?)\s*$")
    acct = find(r"^aaa accounting (.+?)\s*$")
    servers, kinds = [], []
    for m, body in iter_blocks(cfg, r"(?i)^(tacacs|radius) server\s+\S+\s*$"):
        a = re.search(rf"address (?:ipv4 )?({HOST})", body)
        if a:
            servers.append(a.group(1)); kinds.append(m.group(1).lower())
    for kind, host in find(r"^(tacacs|radius)-server host\s+({0})".format(HOST)):
        servers.append(host); kinds.append(kind.lower())
    for host in find(r"^\s*primary authentication\s+(" + HOST + ")"):        # Comware HWTACACS / RADIUS scheme
        servers.append(host)
        kinds.append("aaa scheme")
    enabled = bool(find(r"^aaa new-model\s*$") or authn or authz or acct or servers)
    d["aaa"] = {"present": enabled, "authn": _uniq(authn), "authz": _uniq(authz), "acct": _uniq(acct), "servers": _uniq(servers), "types": _uniq(kinds)}

    # ---------------------------------------------------------------- NTP
    ntp = (find(r"^ntp server\s+(?:vrf\s+\S+\s+)?(" + HOST + ")") + find(r"^ntp-service unicast-server\s+(" + HOST + ")")
           + find(r"^sntp server\s+(?:priority\s+\d+\s+)?(" + HOST + ")") + find(r"^system-time ntp\s+(" + HOST + ")")
           + find(r"^ntp unicast-server\s+(" + HOST + ")") + find(r'^ntp server\s+"?(' + HOST + ')"?'))
    d["ntp"] = {"present": bool(ntp), "servers": _uniq(ntp)}

    # --------------------------------------------------------------- SNMP
    comms = []
    for name, acc in find(r'^snmp-server community\s+"?(\S+?)"?(?:\s+(RO|RW|operator|manager|restricted|unrestricted))?(?:\s|$)'):
        level = "RW" if (acc or "").lower() in ("rw", "manager", "unrestricted") else "RO"
        comms.append(f"{level}:{_fp(name)}")
    for acc, name in find(r"^snmp-agent community\s+(read|write)\s+(?:simple\s+|cipher\s+)?(\S+)"):
        comms.append(f"{'RO' if acc == 'read' else 'RW'}:{_fp(name)}")
    hosts = (find(r"^snmp-server host\s+(" + HOST + ")") + find(r"^snmp-agent target-host trap address udp-domain\s+(" + HOST + ")"))
    v3 = bool(find(r"^snmp-server (?:user|group)\s+\S+") or find(r"^snmp-agent usm-user"))
    d["snmp"] = {"present": bool(comms or hosts or v3 or find(r"^snmp-server (?:enable|location|contact)")), "communities": _uniq(comms),
                 "hosts": _uniq(hosts), "v3": v3, "location": bool(find(r"^snmp-server location\s+\S+")), "contact": bool(find(r"^snmp-server contact\s+\S+"))}

    # ---------------------------------------------------------------- DNS
    dns = []
    for line in find(r"^ip name-server\s+(?:vrf\s+\S+\s+)?(.+?)\s*$"):
        dns += re.findall(IPV4, line)
    dns += find(r"^ip dns server-address\s+(?:priority\s+\d+\s+)?(" + IPV4 + ")")
    dns += find(r"^dns server\s+(" + IPV4 + ")")
    dns += find(r"^ip domain-name-server\s+(" + IPV4 + ")")
    d["dns"] = {"present": bool(dns), "servers": _uniq(dns), "lookup": not bool(find(r"^no ip domain[- ]lookup"))}

    # -------------------------------------------------------- domain name
    dom = (find(r"^ip domain[- ]name\s+(?:vrf\s+\S+\s+)?(\S+)") + find(r'^ip dns domain-name\s+"?([^"\s]+)') + find(r"^dns domain\s+(\S+)"))
    d["domain"] = {"present": bool(dom), "name": dom[0].lower() if dom else ""}
    return d


# --------------------------------------------------------------- comparison
def _sig(cat: str, f: dict):
    if not f.get("present"):
        return None
    if cat == "aaa":
        return (bool(f["authn"]), bool(f["authz"]), bool(f["acct"]), tuple(f["servers"]))
    if cat == "ntp" or cat == "dns":
        return tuple(f["servers"]) or None
    if cat == "snmp":
        return (tuple(f["communities"]), f["v3"], tuple(f["hosts"]), f["location"])
    if cat == "domain":
        return f["name"] or None


def _text(cat: str, f: dict) -> str:
    if not f.get("present"):
        return "not configured"
    if cat == "aaa":
        parts = [x for x, on in (("authentication", f["authn"]), ("authorization", f["authz"]), ("accounting", f["acct"])) if on]
        s = " + ".join(parts) or "enabled"
        return s + (f" | servers: {', '.join(f['servers'])}" if f["servers"] else "") + (f" ({'/'.join(f['types'])})" if f["types"] else "")
    if cat == "ntp":
        return "servers: " + ", ".join(f["servers"])
    if cat == "dns":
        return "servers: " + ", ".join(f["servers"])
    if cat == "snmp":
        bits = []
        if f["communities"]:
            bits.append("communities: " + ", ".join(f["communities"]))
        if f["v3"]:
            bits.append("SNMPv3 users/groups")
        if f["hosts"]:
            bits.append("trap hosts: " + ", ".join(f["hosts"]))
        bits.append("location set" if f["location"] else "no location")
        return " | ".join(bits)
    return f["name"]


def _items(cat: str, f: dict):
    """The baseline as readable (label, value) pairs for the dashboard."""
    if not f.get("present"):
        return []
    if cat == "aaa":
        return ([("Authentication", x) for x in f["authn"]] + [("Authorization", x) for x in f["authz"]] + [("Accounting", x) for x in f["acct"]]
                + [(f"Server ({'/'.join(f['types'])})" if f["types"] else "Server", x) for x in f["servers"]])
    if cat in ("ntp", "dns"):
        return [("Server", x) for x in f["servers"]]
    if cat == "domain":
        return [("Domain name", f["name"])]
    items = [("Community (secret hidden)", c) for c in f["communities"]] + [("Trap host", h) for h in f["hosts"]]
    if f["v3"]:
        items.append(("SNMPv3", "users / groups configured"))
    items.append(("Location", "configured" if f["location"] else "not set"))
    return items


def compare(switches) -> dict:
    """Baseline = the most common configuration across all switches that can be read; report who matches, differs or lacks it."""
    out = {"categories": [], "matrix": []}
    readable = [s for s in switches if s.std.get("supported")]
    per_cat = {}
    for key, label in CATEGORIES:
        sigs = {s.id: _sig(key, s.std[key]) for s in readable}
        base = Counter(v for v in sigs.values() if v is not None).most_common(1)
        base_sig = base[0][0] if base else None
        base_sw = next((s for s in readable if sigs[s.id] == base_sig and base_sig is not None), None)
        rows, counts = [], Counter()
        for s in readable:
            st = "missing" if sigs[s.id] is None else ("match" if sigs[s.id] == base_sig else "differs")
            counts[st] += 1
            rows.append({"switch_id": s.id, "hostname": s.hostname, "status": st, "value": _text(key, s.std[key])})
        per_cat[key] = {r["switch_id"]: r["status"] for r in rows}
        out["categories"].append({"key": key, "label": label, "baseline": _text(key, base_sw.std[key]) if base_sw else "not configured on any switch",
                                  "items": [{"label": a, "value": b} for a, b in _items(key, base_sw.std[key])] if base_sw else [],
                                  "total": len(readable), "match": counts["match"], "differs": counts["differs"], "missing": counts["missing"], "rows": rows})
    for s in switches:
        if s.std.get("supported"):
            out["matrix"].append({"switch_id": s.id, "hostname": s.hostname, "status": {k: per_cat[k][s.id] for k, _ in CATEGORIES}})
        else:
            out["matrix"].append({"switch_id": s.id, "hostname": s.hostname, "status": {k: "unknown" for k, _ in CATEGORIES}})
    total = sum(c["total"] for c in out["categories"])
    out["score"] = round(100 * sum(c["match"] for c in out["categories"]) / total) if total else 0
    out["unsupported"] = [s.hostname for s in switches if not s.std.get("supported")]
    return out
