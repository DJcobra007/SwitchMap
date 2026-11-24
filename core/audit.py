"""Per-switch configuration checks (security hygiene and obvious misconfigurations) for IOS-style CLI backups."""
from __future__ import annotations
import re

from .utils import IPV4, iter_blocks, in_subnet, network_of

IOS_LIKE = {"cisco", "aruba_cx", "aruba_os", "tplink", "quantum", "cisco_sb"}
WELL_KNOWN_COMMUNITIES = {"public", "private", "cisco", "community", "snmp", "admin", "default", "secret", "manager"}
DEFAULT_NAMES = re.compile(r"^(switch|sw|router|hostname|catalyst|localhost|cisco|aruba|hp|unnamed|QN-[0-9a-f]{6})$", re.I)


def audit_config(cfg: str, key: str, sw) -> list:
    out = []

    def add(sev, cat, title, detail="", where=""):
        out.append({"severity": sev, "category": cat, "title": title, "detail": detail, "where": where})

    # ---- checks that only need parsed data (any vendor)
    if re.fullmatch(DEFAULT_NAMES, sw.hostname or ""):
        add("Low", "Management", "Default hostname", f"The switch is still called '{sw.hostname}', which makes it hard to identify.")
    if sw.mgmt_ip and sw.gateway and sw.subnet and not in_subnet(sw.gateway, sw.mgmt_ip, sw.subnet):
        add("Medium", "Management", "Default gateway is outside the management subnet", f"Gateway {sw.gateway} is not in {network_of(sw.mgmt_ip, sw.subnet)}.")
    if sw.mgmt_ip and not sw.gateway and not sw.l3.get("is_l3") and cfg.strip():     # no config = gateway unknown, not missing
        add("Medium", "Management", "No default gateway", "The switch cannot be managed from other subnets.")
    if not sw.mgmt_ip:
        add("High", "Management", "No management IP address found", "No SVI / management interface with an IP address was found in the backup.")
    if sw.mgmt_vlan == 1:
        add("Medium", "Management", "Management interface is in VLAN 1", "VLAN 1 is the default VLAN; use a dedicated management VLAN.")
    ids = {v.id for v in sw.vlans} | {1}
    if sw.vlans:
        bad = {}
        for i in sw.interfaces:
            if i.vlan.isdigit() and int(i.vlan) not in ids:
                bad.setdefault(i.vlan, []).append(i.name)
        for vid, ports in sorted(bad.items(), key=lambda t: int(t[0])):
            add("Medium", "VLAN", f"Ports assigned to VLAN {vid}, which does not exist on this switch", ", ".join(_short(p) for p in ports[:10]) + (" ..." if len(ports) > 10 else ""), f"VLAN {vid}")
    err = [i.name for i in sw.interfaces if "err" in (i.state_text or "")]
    if err:
        add("High", "Ports", f"{len(err)} error-disabled port(s)", ", ".join(_short(p) for p in err[:12]), ", ".join(_short(p) for p in err[:3]))
    for m in sw.members:
        if sw.is_stack and m.state and m.state.lower() not in ("ready", "ok"):
            add("High", "Stack", f"Stack member {m.index} is not Ready", f"State reported: {m.state}", f"member {m.index}")
    if sw.is_stack and not any(m.role == "Standby" for m in sw.members):
        add("Medium", "Stack", "Stack has no Standby member", "If the Active member fails there is no ready replacement.")
    versions = {m.version for m in sw.members if m.version}
    if sw.is_stack and len(versions) > 1:
        add("Medium", "Stack", "Stack members run different software versions", ", ".join(sorted(versions)))
    for a in sw.l3.get("acl_undefined", []):
        add("Medium", "Layer 3", f"ACL '{a['name']}' is applied but not defined", "Applied on: " + ", ".join(a["where"]), a["name"])
    for a in sw.l3.get("acls", []):
        if not a.get("applied_to"):
            add("Info", "Layer 3", f"ACL '{a['name']}' is defined but not applied to any interface or line", "It may still be used by NAT, route-maps or class-maps (not checked).", a["name"])
    nets = [network_of(s["ip"], s["mask"]) for s in sw.l3.get("svis", []) if s.get("ip")]
    for r in sw.l3.get("routes", []):
        if not r["vrf"] and re.fullmatch(IPV4, r["next_hop"]) and not any(in_subnet(r["next_hop"], s["ip"], s["mask"]) for s in sw.l3.get("svis", [])):
            add("Medium", "Layer 3", f"Static route {r['prefix']} has an unreachable next hop", f"{r['next_hop']} is not inside any connected subnet of this switch.", r["prefix"])
    if sw.l3.get("is_l3") and sw.l3.get("routes") is not None and not any(r["default"] for r in sw.l3.get("routes", [])) \
            and not any(p["protocol"] != "Static" for p in sw.l3.get("protocols", [])):
        add("Low", "Layer 3", "Layer-3 switch without a default route or routing protocol", "Traffic to other networks has no path.")

    if key not in IOS_LIKE or not cfg.strip():
        return out

    # ---- configuration text checks (IOS-style CLI)
    unrestricted = []
    for m, body in iter_blocks(cfg, r"^line vty.*$"):
        ti = re.search(r"^\s*transport input\s+(.+?)\s*$", body, re.M)
        if ti and re.search(r"\b(telnet|all)\b", ti.group(1)):
            add("High", "Security", "Telnet allowed on VTY lines", f"{m.group(0).strip()}: transport input {ti.group(1)} (Telnet sends passwords in clear text).", m.group(0).strip())
        if not re.search(r"^\s*access-class\b", body, re.M):
            unrestricted.append(m.group(0).strip())
    if unrestricted:
        add("Low", "Security", "VTY lines are not restricted by an access-class", ", ".join(unrestricted))
    if re.search(r"^ip http server\s*$", cfg, re.M) and not re.search(r"^no ip http server", cfg, re.M):
        add("Medium", "Security", "HTTP management server is enabled", "Unencrypted web management; use 'ip http secure-server' only.")
    # Quantum stores secrets hashed even with the 'password' keyword: 'password encrypted <sha1>' / 'password 7 <md5>'
    hashed = r"(?!encrypted\b)(?!7\s+[0-9a-fA-F]{32}\b)" if key == "quantum" else r"(?!encrypted\b)"
    if re.search(r"^enable password\s+(?:level\s+\d+\s+)?(?!level\b)" + hashed + r"\S+", cfg, re.M):
        add("High", "Security", "'enable password' is used instead of 'enable secret'", "The privileged password is stored reversibly or in clear text.")
    plain = [u for u, pw in re.findall(r"^username\s+(\S+)\s+(?:privilege\s+\d+\s+)?password\s+(?:0\s+)?" + hashed + r"(\S+)", cfg, re.M)]
    if plain:
        add("Medium", "Security", "Local user(s) stored with 'password' instead of 'secret'", "Users: " + ", ".join(sorted(set(plain))))
    if re.search(r"^\s+password\s+\S+", cfg, re.M) and not re.search(r"^service password-encryption", cfg, re.M):
        add("Medium", "Security", "Line passwords are stored in clear text", "Enable 'service password-encryption' or use AAA / local users with secrets.")
    for name, acc in re.findall(r"^snmp-server community\s+\"?(\S+?)\"?(?:\s+(RO|RW|operator|manager))?(?:\s|$)", cfg, re.M | re.I):
        if name.lower() in WELL_KNOWN_COMMUNITIES:
            add("High", "Security", "Well-known SNMP community string in use", f"A default/guessable community is configured ({(acc or 'RO').upper()} access).")
        if (acc or "").upper() in ("RW", "MANAGER"):
            add("Medium", "Security", "SNMP read-write community configured", "Write access over SNMPv1/v2c is rarely needed; prefer SNMPv3.")
    if not re.search(rf"^logging\s+(?:host\s+)?{IPV4}", cfg, re.M) and not re.search(r"^logging host", cfg, re.M):
        add("Low", "Security", "No syslog server configured", "Events are only kept locally.")
    return out


def _short(name: str) -> str:
    from .utils import short_port
    return short_port(name)
