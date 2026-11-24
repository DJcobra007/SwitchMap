"""Common parser scaffolding: sectioning, member bookkeeping and management-IP selection."""
from __future__ import annotations
import os
import re
from collections import Counter

from ..parts import scan_inventory
from ..models import Switch, StackMember, Vlan, Interface
from ..utils import mask_secrets, MAC_ANY, norm_mac, split_sections, get_section, network_of, canon_port, in_subnet

MGMT_WORDS = re.compile(r"mgmt|management|manage|\boob\b|\bmgt\b", re.I)


class BaseParser:
    vendor = "Unknown"
    os_family = ""
    steps: tuple = ()
    scan_skip: tuple = ()

    def __init__(self, text: str, filename: str, sid: str):
        self.text = text
        self.cfg, self.shows = split_sections(text)
        self.stem = os.path.splitext(os.path.basename(filename))[0]
        self.sw = Switch(id=sid, filename=filename, vendor=self.vendor, os_family=self.os_family)
        self.vlan_names: dict[int, str] = {}
        self.mgmt_hints: Counter = Counter()     # vlan id (str) -> weight
        self.ifaces: dict[str, Interface] = {}   # canonical name -> Interface
        self.members: dict[int, StackMember] = {}
        self.lags: dict[str, dict] = {}          # canonical port-channel name -> record

    # --------------------------------------------------------------- helpers
    def sec(self, cmd: str, exact: bool = False) -> str:
        return get_section(self.shows, cmd, exact)

    def member(self, idx: int) -> StackMember:
        if idx not in self.members:
            self.members[idx] = StackMember(index=idx)
        return self.members[idx]

    def iface(self, name: str) -> Interface:
        key = canon_port(name)
        if key not in self.ifaces:
            self.ifaces[key] = Interface(name=key)
        return self.ifaces[key]

    def lag(self, name: str, member: str = "", mode: str = "", protocol: str = "", flags: str = "", description: str = "", flag: str = ""):
        """Register a port-channel / LAG / trunk group and (optionally) one member port."""
        key = canon_port(name)
        rec = self.lags.setdefault(key, {"name": key, "protocol": "", "flags": "", "description": "", "members": []})
        rec["protocol"] = rec["protocol"] or protocol or {"active": "LACP", "passive": "LACP", "desirable": "PAgP", "auto": "PAgP", "on": "static"}.get(mode.lower(), "")
        rec["flags"] = rec["flags"] or flags
        rec["description"] = rec["description"] or description
        if member:
            m = canon_port(member)
            ex = next((x for x in rec["members"] if x["port"] == m), None)
            if ex is None:
                rec["members"].append({"port": m, "flag": flag})
            elif flag:
                ex["flag"] = flag
            self.iface(m).channel_group = key
        return rec

    @staticmethod
    def block_text(header: str, body: str, limit: int = 80) -> str:
        """An interface's configuration as readable text: its header line plus its lines, secrets masked."""
        lines = [header.strip()] + [l.rstrip() for l in body.split("\n") if l.strip() and l.strip() not in ("exit", "!", "#")]
        extra = len(lines) - limit
        return mask_secrets("\n".join(lines[:limit]) + (f"\n... (+{extra} more lines)" if extra > 0 else ""))

    flat_ports = False        # True for CLIs that configure a port with single-line commands (no interface blocks)

    def flat_config(self):
        """For flat-command CLIs: gather the configuration lines that mention a port id such as 1/1/1."""
        lines = [l.strip() for l in self.cfg.split("\n") if l.strip() and not l.strip().startswith(("!", "#"))]
        for i in self.ifaces.values():
            if i.config or "/" not in i.name:
                continue
            rx = re.compile(r"(?<![\w/.])" + re.escape(i.name) + r"(?![\w/.])")
            hit = [l for l in lines if rx.search(l)]
            if hit:
                i.config = mask_secrets("\n".join(hit[:40]))

    def warn(self, msg: str):
        if msg not in self.sw.warnings:
            self.sw.warnings.append(msg)

    # ------------------------------------------------------------------ run
    def parse(self) -> Switch:
        for step in self.steps:
            try:
                getattr(self, step)()
            except Exception as exc:  # keep going; a bad section must not kill the file
                self.warn(f"Could not fully parse '{step.replace('parse_', '')}': {exc}")
        return self.finalize()

    def finalize(self) -> Switch:
        sw = self.sw
        if not sw.hostname:
            m = re.search(r"^\s*(?:<)?([\w.\-]+)[#>]\s*(?:show|display)\s", self.text, re.M)
            sw.hostname = m.group(1) if m else self.stem
            self.warn("Hostname not found in the backup; used the prompt/file name instead.")
        sw.members = [self.members[k] for k in sorted(self.members)]
        if not sw.members:
            sw.members = [StackMember(index=1)]
        if len(sw.members) == 1:
            sw.members[0].role = "Standalone"
        elif not any(m.role == "Active" for m in sw.members):
            sw.members[0].role = "Active"
        self.fallback_ids()
        self.scan_parts()
        if self.flat_ports:
            self.flat_config()
        for m in sw.members:
            if not m.serial:
                self.warn(f"Serial number for member {m.index} not found (needs 'show version' / 'show inventory' style output).")
            if not m.mac:
                self.warn(f"MAC address for member {m.index} not found.")
        sw.vlans = [Vlan(i, n) for i, n in sorted(self.vlan_names.items())]
        for i in self.ifaces.values():
            if i.status == "unknown" and i.admin_down:
                i.status = "down"
        sw.interfaces = sorted(self.ifaces.values(), key=_port_sort)
        sw.port_channels = sorted(self.lags.values(), key=lambda r: [int(x) for x in re.findall(r"\d+", r["name"])] or [0])
        self.choose_mgmt()
        if not sw.stp_version:
            sw.stp_version = sw.stp_mode or "Not in backup"
        if not sw.firmware:
            sw.firmware = "Not in backup"
        return sw

    def scan_parts(self):
        """Vendor-neutral sweep over inventory-like show/display output (modules, transceivers, PSUs, fans, member MACs)."""
        known = {m.serial for m in self.members.values() if m.serial} | {c.serial for c in self.sw.components if c.serial}
        shows = {k: v for k, v in self.shows.items() if not k.startswith(self.scan_skip)} if self.scan_skip else self.shows
        comps, macs = scan_inventory(shows, known, self.sw.vendor)
        kept = []
        for c in comps:      # a row that describes the switch chassis itself belongs to the Switches sheet, not the parts list
            slot = re.fullmatch(r"(?:\w+ )?(\d+)", (c.port or "").strip())
            if slot and re.search(r"\b(?:switch|chassis)\b", c.descr or "", re.I) and not re.search(r"module|transceiver|sfp|fan|power", c.descr, re.I):
                mem = self.member(int(slot.group(1)))
                mem.serial, mem.model = mem.serial or c.serial, mem.model or c.model
                continue
            kept.append(c)
        comps = kept
        self.sw.components += comps
        for idx, mac in macs.items():
            mem = self.members.get(idx)
            if mem is not None and not mem.mac:
                mem.mac = mac
        if comps:
            self.sw.info.append(f"Generic inventory scan found {len(comps)} additional part(s).")

    def fallback_ids(self):
        """Last resort for a single-chassis switch: look for any 'MAC address' / 'serial number' line in the whole backup."""
        mem = self.members[min(self.members)] if self.members else None
        if mem is None or len(self.members) > 1:
            return
        if not mem.mac:
            for m in re.finditer(r"(?im)^[^\n]*\b(?:base|system|chassis|bridge|burned[- ]in|ethernet)?\s*mac(?:\s*addr(?:ess)?)?\b[^\n]*?[:=\-]\s*(" + MAC_ANY.pattern + ")", self.text):
                mem.mac = norm_mac(m.group(1))
                break
        if not mem.serial:
            m = re.search(r"(?im)^[^\n]*\bserial\s*(?:number|no\.?|#)?\s*[:=\-]+\s*([A-Z0-9]{6,})\s*$", self.text)
            if m:
                mem.serial = m.group(1)

    # ------------------------------------------------------ management IP
    def choose_mgmt(self):
        sw = self.sw
        cands = {k: v for k, v in sw.svis.items() if v.get("ip")}
        if not cands:
            self.warn("No management IP address found in the backup.")
            return
        pick = None
        # 1. interface used as a source for management traffic (SNMP, syslog, NTP, AAA ...)
        for vid, _ in self.mgmt_hints.most_common():
            if vid in cands:
                pick = vid
                break
        # 2. interface or VLAN that is named like a management network
        if pick is None:
            for k, v in cands.items():
                name = self.vlan_names.get(int(k), "") if k.isdigit() else ""
                if MGMT_WORDS.search(f"{name} {v.get('desc', '')} {k}"):
                    pick = k
                    break
        # 3. the SVI whose subnet holds the default gateway
        if pick is None and sw.gateway:
            for k, v in cands.items():
                if in_subnet(sw.gateway, v["ip"], v["mask"]):
                    pick = k
                    break
        # 4. VLAN 1 / lowest VLAN id / first candidate
        if pick is None:
            vlan_ids = sorted((int(k) for k in cands if k.isdigit()))
            pick = str(vlan_ids[0]) if vlan_ids else next(iter(cands))
        c = cands[pick]
        sw.mgmt_ip, sw.subnet = c["ip"], c["mask"]
        sw.prefix = network_of(c["ip"], c["mask"])
        sw.mgmt_vlan = int(pick) if pick.isdigit() else None
        if not sw.gateway:
            self.warn("Default gateway not found in the backup.")


def _port_sort(i: Interface):
    nums = [int(x) for x in re.findall(r"\d+", i.name)]
    return (0 if not i.name.lower().startswith(("port-channel", "po", "lag", "trk", "bridge", "linkagg")) else 1,
            re.sub(r"[\d/.:]+", "", i.name), nums)
