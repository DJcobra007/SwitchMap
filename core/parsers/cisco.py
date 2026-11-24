"""Cisco IOS / IOS-XE (Catalyst 2960/3560/3750/3850/9200/9300/9500 ...).

Reads the running-config and, when they are appended to the backup, the output of:
  show version | show switch | show inventory | show vlan brief | show interfaces status |
  show spanning-tree | show cdp neighbors [detail] | show lldp neighbors [detail] |
  show ip dhcp snooping binding | show ip dhcp binding
"""
from __future__ import annotations
import re
from collections import Counter

from ..models import Neighbor, Component
from ..utils import (IPV4, mac_or_blank, norm_mac, canon_port, media_from_type, iter_blocks, fixed_columns, to_netmask,
                     MAC_ANY, MAC_RX, stp_root_blocks)
from ..parts import classify_part
from .base import BaseParser

PORT_RX = r"[A-Za-z][A-Za-z\-]*\s?\d[\d/.:]*"


class CiscoParser(BaseParser):
    vendor = "Cisco"
    os_family = "IOS / IOS-XE"
    scan_skip = ("show inventory",)          # already parsed in detail
    steps = ("parse_config", "parse_version", "parse_stack", "parse_module", "parse_inventory", "parse_vlans",
             "parse_interface_status", "parse_etherchannel", "parse_stp", "parse_neighbors", "parse_dhcp")

    # ----------------------------------------------------------- running-config
    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r"^hostname\s+(\S+)", cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(rf"^ip default-gateway\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        elif (m := re.search(rf"^ip route\s+0\.0\.0\.0\s+0\.0\.0\.0\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        elif (m := re.search(rf"^ip route vrf \S+\s+0\.0\.0\.0\s+0\.0\.0\.0\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if (m := re.search(r"^spanning-tree mode\s+(\S+)", cfg, re.M)):
            sw.stp_mode = m.group(1)
        self.cfg_version = (re.search(r"^version\s+(\S+)", cfg, re.M) or [None, ""])[1]

        for m in re.finditer(r"^switch\s+(\d+)\s+provision\s+(\S+)", cfg, re.M):
            self.member(int(m.group(1))).model = m.group(2).upper()
        for m in re.finditer(r"^switch\s+(\d+)\s+priority\s+(\d+)", cfg, re.M):
            self.member(int(m.group(1))).priority = m.group(2)

        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):
            n = re.search(r"^\s*name\s+(.+?)\s*$", body, re.M)
            self.vlan_names[int(m.group(1))] = n.group(1) if n else f"VLAN{int(m.group(1)):04d}"

        # interfaces used as source for management traffic -> management SVI hint
        for vid in re.findall(r"(?:source-interface|trap-source|ntp source|update-source)\s+(?:(?:traps|informs)\s+)?Vlan(\d+)",
                              cfg, re.I):
            self.mgmt_hints[vid] += 1
        if (m := re.search(r"^ip http (?:client )?source-interface Vlan(\d+)", cfg, re.M)):
            self.mgmt_hints[m.group(1)] += 1

        for m, body in iter_blocks(cfg, r"^interface\s+(\S+)\s*$"):
            self._interface(m.group(1), body)

        for m, body in iter_blocks(cfg, r"^ip dhcp pool\s+\S+\s*$"):
            n = re.search(rf"^\s*network\s+({IPV4})\s+({IPV4}|/\d+)", body, re.M)
            if n:
                sw.dhcp_pools.append((n.group(1), to_netmask(n.group(2))))

    def _interface(self, name: str, body: str):
        low = name.lower()
        ip = re.search(rf"^\s*ip address\s+({IPV4})\s+({IPV4})", body, re.M)
        desc = (re.search(r"^\s*description\s+(.+?)\s*$", body, re.M) or [None, ""])[1]
        if low.startswith("vlan"):
            if ip:
                self.sw.svis[re.sub(r"\D", "", name)] = {"ip": ip.group(1), "mask": ip.group(2), "desc": desc}
            return
        if re.match(r"(loopback|null|tunnel|nve|bdi)", low):
            return
        i = self.iface(name)
        i.config = self.block_text(f"interface {name}", body)
        i.description = desc
        i.admin_down = bool(re.search(r"^\s*shutdown\s*$", body, re.M))
        mode = re.search(r"^\s*switchport mode\s+(\S+)", body, re.M)
        acc = re.search(r"^\s*switchport access vlan\s+(\d+)", body, re.M)
        nat = re.search(r"^\s*switchport trunk native vlan\s+(\d+)", body, re.M)
        if mode:
            i.mode = mode.group(1)
        elif re.search(r"^\s*no switchport\s*$", body, re.M):
            i.mode = "routed"
        i.vlan = "trunk" if i.mode == "trunk" else (acc.group(1) if acc else ("1" if i.mode != "routed" else ""))
        if i.mode == "trunk":
            i.native_vlan = nat.group(1) if nat else "1"
            if nat:
                i.vlan = f"trunk (native {nat.group(1)})"
        if (m := re.search(r"^\s*channel-group\s+(\d+)(?:\s+mode\s+(\S+))?", body, re.M)):
            self.lag(f"Port-channel{m.group(1)}", member=i.name, mode=m.group(2) or "")
        if low.startswith("port-channel"):
            self.lag(i.name, description=desc)
        if ip and ("mgmt" in body.lower() or re.search(r"^\s*vrf forwarding", body, re.M)):
            self.sw.svis[f"if:{i.name}"] = {"ip": ip.group(1), "mask": ip.group(2), "desc": "management port"}

    # ------------------------------------------------------------- show version
    def parse_version(self):
        txt, sw = self.sec("show version"), self.sw
        if not txt:
            if self.cfg_version:
                sw.firmware = self.cfg_version
                self.warn("No 'show version' output: firmware shown is the config-format version, not the running image.")
            return
        m = (re.search(r"Cisco IOS XE Software, Version\s+(\S+)", txt)
             or re.search(r"Cisco IOS Software.*?Version\s+([\w.()\-]+)", txt)
             or re.search(r"\bVersion\s+(\d[\w.()\-]*)", txt))
        if m:
            sw.firmware = m.group(1).rstrip(",")
        # model/version table
        for m in re.finditer(r"^\s*\*?\s*(\d+)\s+\d+\s+(\S+)\s+(\d[\w.()\-]*)\s+\S+\s+\S+", txt, re.M):
            mem = self.member(int(m.group(1)))
            mem.model, mem.version = mem.model or m.group(2), m.group(3)
        # per-switch blocks
        parts = re.split(r"^\s*Switch\s+0*(\d+)\s*\n\s*-{3,}\s*$", txt, flags=re.M)
        blocks = [(1, txt)] if len(parts) == 1 else [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
        for idx, body in blocks:
            mem = self.member(idx)
            if (m := re.search(r"(?im)^\s*model number\s*:\s*(\S+)", body)):
                mem.model = m.group(1)
            elif not mem.model and (m := re.search(r"(?i)^cisco\s+(\S+)\s+\(.*?\)\s+processor", body, re.M)):
                mem.model = m.group(1)
            if (m := re.search(r"(?im)^\s*system serial number\s*:\s*(\S+)", body)):
                mem.serial = m.group(1)
            elif not mem.serial and (m := re.search(r"(?i)processor board id\s+(\S+)", body)):
                mem.serial = m.group(1)
            if (m := re.search(r"(?im)^\s*base\s+ethernet\s+mac\s+address[\s.:]*(" + MAC_RX + ")", body)):
                mem.mac = norm_mac(m.group(1))

    # --------------------------------------------------------------- show switch
    def parse_stack(self):
        txt = self.sec("show switch", exact=False)
        for m in re.finditer(r"^\s*\*?\s*(\d+)\s+(Active|Standby|Member|Master|Slave)\s+(" + MAC_RX + r")\s+(\d+)"
                             r"\s+(\S+)\s+(\S+)", txt, re.M | re.I):
            mem = self.member(int(m.group(1)))
            mem.role = {"master": "Active", "slave": "Member"}.get(m.group(2).lower(), m.group(2).capitalize())
            mem.mac = norm_mac(m.group(3))
            mem.priority, mem.version, mem.state = m.group(4), m.group(5), m.group(6)

    # ------------------------------------------------------------ extra MAC sources
    def parse_module(self):
        """Older stacks / modular chassis: 'show module' lists a MAC range per member or slot."""
        for m in re.finditer(rf"^\s*(\d+)\s+({MAC_RX})\s+to\s+(?:{MAC_RX})\s+\S+\s+\S+\s+\S+\s+(\S+)", self.sec("show module"), re.M):
            mem = self.member(int(m.group(1)))
            mem.mac = mem.mac or norm_mac(m.group(2))
        # SVI hardware address = base MAC of the active member (only used if nothing better was found)
        if not any(x.mac for x in self.members.values()):
            hit = re.search(rf"(?s)\bVlan\d+ is .*?address is ({MAC_RX})", self.sec("show interfaces"))
            if hit:
                self.member(min(self.members) if self.members else 1).mac = norm_mac(hit.group(1))

    # ------------------------------------------------------------ show inventory
    def parse_inventory(self):
        """Every entry of 'show inventory' ends up in the workbook: chassis/stack members go to the Switches sheet,
        everything else (SFPs, DACs, stack cables/modules, uplink modules, power supplies, fans, line cards, other) to Connected Devices."""
        txt, sw = self.sec("show inventory"), self.sw
        if not txt.strip():
            self.warn("No 'show inventory' output: SFPs, stack modules/cables, power supplies and serial numbers cannot be listed.")
            return
        records = [r for r in re.split(r"(?m)^(?=\s*NAME:)", txt) if re.match(r"\s*NAME:", r)]
        seen, listed, chassis = set(), 0, 0
        for rec in records:
            nm = re.match(r'\s*NAME:\s*"(?P<name>.*?)"\s*,\s*DESCR:\s*(?P<rest>.*)', rec, re.S)
            pm = re.search(r"PID:\s*(?P<pid>[^,\n]*?)\s*,\s*VID:\s*(?P<vid>[^,\n]*?)\s*,\s*SN:\s*(?P<sn>[^\s,]*)", rec)
            if not nm or not pm:
                self.warn(f"An entry in 'show inventory' could not be read: {' '.join(rec.split())[:90]}")
                continue
            name = nm.group("name").strip()
            descr = " ".join(nm.group("rest").split("PID:")[0].replace('"', " ").split())
            pid, sn = pm.group("pid").strip(), pm.group("sn").strip()
            key = (name, pid, sn)
            if key in seen:
                continue
            seen.add(key)
            cm = re.match(r"^(?:Switch\s+)?(\d+)$", name) or (re.match(r"^chassis$", name, re.I) and re.match(r"(1)", "1"))
            if cm:                                   # chassis / stack member -> Switches sheet
                mem = self.member(int(cm.group(1)))
                mem.model = mem.model or pid
                mem.serial = mem.serial or sn
                chassis += 1
                continue
            if re.match(r"^c\d+\w*\s+stack$", name, re.I):   # aggregate 'stack' pseudo-entry
                continue
            kind = self._kind(name, descr, pid)
            port = canon_port(name) if re.match(PORT_RX + "$", name) else self._module_port(name)
            sw.components.append(Component(kind=kind, model=pid or "Unknown", serial=sn, port=port, make="Cisco", descr=descr))
            listed += 1
            if kind in ("SFP / Transceiver", "DAC / AOC Cable") and re.match(PORT_RX + "$", name) and not re.search(
                    r"GLC-T|GLC-TE|SFP-GE-T|1000BASE-?T", pid, re.I):
                self.iface(name).media = "fiber"
        sw.info.append(f"show inventory: {len(records)} entries found, {chassis} chassis/stack members + {listed} other parts listed")

    @staticmethod
    def _kind(name, descr, pid):
        return classify_part(name, descr, pid)

    @staticmethod
    def _module_port(name):
        m = re.match(r"^Switch\s+(\d+)\s*-\s*(.+)$", name)
        return f"Switch {m.group(1)}: {m.group(2)}" if m else name

    # --------------------------------------------------------------- show vlan
    def parse_vlans(self):
        txt = self.sec("show vlan")
        for m in re.finditer(r"^(\d+)\s+(.+?)\s+(active|act/unsup|suspend\w*|act/lshut)\b", txt, re.M):
            vid = int(m.group(1))
            if m.group(3) == "act/unsup" or 1002 <= vid <= 1005:
                continue
            self.vlan_names.setdefault(vid, m.group(2).strip())
            if self.vlan_names[vid].startswith("VLAN0") and m.group(2).strip():
                self.vlan_names[vid] = m.group(2).strip()

    # ------------------------------------------------------ show interfaces status
    def parse_interface_status(self):
        txt = self.sec("show interfaces status", exact=True)
        rows = fixed_columns(txt, "Port")
        if rows:
            for r in rows:
                port = r.get("port", "")
                if not port or not re.match(r"^[A-Za-z]", port):
                    continue
                i = self.iface(port)
                st = r.get("status", "").lower()
                i.status = "up" if st in ("connected", "monitoring", "up") else "down"
                i.admin_down = st == "disabled"
                i.state_text = st
                i.duplex = r.get("duplex", "")
                i.speed = r.get("speed", "")
                i.port_type = r.get("type", "")
                i.media = media_from_type(i.port_type) or i.media
                if r.get("name") and not i.description:
                    i.description = r["name"]
                if r.get("vlan") and not i.vlan:
                    i.vlan = r["vlan"]
            return
        # fallback: show ip interface brief
        brief = self.sec("show ip interface brief")
        for m in re.finditer(rf"^(\S+)\s+\S+\s+\w+\s+\w+\s+(up|down|administratively down)\s+(up|down)\s*$", brief, re.M):
            if m.group(1).lower().startswith(("vlan", "loopback")):
                continue
            i = self.iface(m.group(1))
            i.status = "up" if m.group(2) == "up" and m.group(3) == "up" else "down"
            i.admin_down = m.group(2).startswith("admin")
        if not rows and not brief:
            self.warn("No 'show interfaces status' output: port up/down colours need it (config-only shows shutdown ports).")

    # ------------------------------------------------------------ show etherchannel summary
    def parse_etherchannel(self):
        cur = None
        for line in self.sec("show etherchannel summary").split("\n"):
            m = re.match(r"^\s*(\d+)\s+(Po\d+)\((\w+)\)\s*(\S+)?(.*)$", line)
            if m:
                proto = m.group(4) if m.group(4) and not re.search(r"\(", m.group(4)) else ""
                rest = m.group(5) if proto else ((m.group(4) or "") + " " + m.group(5))
                cur = self.lag(m.group(2), protocol="" if proto in ("-", "NONE") else proto, flags=m.group(3))
            elif cur is None or not line.strip() or re.match(r"^\s*\d", line):
                continue
            else:
                rest = line
            for port, flag in re.findall(r"([A-Za-z]+\d[\d/.:]*)\((\w+)\)", rest):
                self.lag(cur["name"], member=port, flag=flag)

    # ------------------------------------------------------------ show spanning-tree
    def parse_stp(self):
        txt, sw = self.sec("show spanning-tree"), self.sw
        sw.stp_roots = stp_root_blocks(txt)
        proto = (re.search(r"Spanning tree enabled protocol\s+(\w+)", txt) or [None, ""])[1].lower()
        mode = proto or sw.stp_mode.lower()
        sw.stp_version = {"rstp": "RSTP (802.1w) - Rapid PVST+", "rapid-pvst": "RSTP (802.1w) - Rapid PVST+",
                          "ieee": "STP (802.1D) - PVST+", "pvst": "STP (802.1D) - PVST+",
                          "mstp": "MSTP (802.1s)", "mst": "MSTP (802.1s)"}.get(mode, sw.stp_mode or "")
        vlan = ""
        blocked, detail = set(), {}
        for line in txt.split("\n"):
            if (h := re.match(r"^\s*(?:VLAN|MST)(\d+)\s*$", line)):
                vlan = str(int(h.group(1)))
                continue
            m = re.match(r"^(\S+)\s+(Root|Desg|Altn|Back|Mstr|Dis)\s+(FWD|BLK|LRN|LIS|DIS|BKN\*?)\s+", line)
            if m and (m.group(3).startswith(("BLK", "BKN")) or m.group(2) in ("Altn", "Back")):
                p = canon_port(m.group(1))
                blocked.add(p)
                detail.setdefault(p, [])
                if vlan and vlan not in detail[p]:
                    detail[p].append(vlan)
        sw.blocked_ports = sorted(set(sw.blocked_ports) | blocked)
        sw.blocked_detail.update(detail)

    # ------------------------------------------------------------------ neighbours
    def parse_neighbors(self):
        sw = self.sw
        seen = set()

        def add(n: Neighbor):
            key = (canon_port(n.local_port).lower(), n.remote_name.lower(), canon_port(n.remote_port).lower())
            if key in seen:
                return
            seen.add(key)
            n.local_port, n.remote_port = canon_port(n.local_port), canon_port(n.remote_port)
            sw.neighbors.append(n)

        # CDP detail
        for blk in re.split(r"^-{5,}\s*$", self.sec("show cdp neighbors detail"), flags=re.M):
            dev = re.search(r"Device ID:\s*(\S+)", blk)
            ifs = re.search(r"Interface:\s*([^,\n]+),\s*Port ID \(outgoing port\):\s*(\S+)", blk)
            if not (dev and ifs):
                continue
            ip = re.search(rf"(?:IP address|IPv4 Address):\s*({IPV4})", blk)
            pl = re.search(r"Platform:\s*([^,\n]*),\s*Capabilities:\s*(.*)", blk)
            add(Neighbor(local_port=ifs.group(1).strip(), remote_name=dev.group(1), remote_port=ifs.group(2),
                         remote_ip=ip.group(1) if ip else "", platform=(pl.group(1).strip() if pl else ""),
                         capabilities=(pl.group(2).strip() if pl else ""), source="cdp"))
        # CDP brief (device id may sit on its own line)
        txt, pending = self.sec("show cdp neighbors", exact=True), ""
        for raw in txt.split("\n"):
            line = raw.rstrip()
            if not line.strip() or re.match(r"^\s*(Capability Codes|Device ID|Total|\s+[A-Z] -)", line):
                continue
            if len(line.split()) == 1:
                pending = line.strip()
                continue
            line = (pending + " " + line.strip()) if pending else line.strip()
            pending = ""
            m = re.match(rf"^(\S+)\s+({PORT_RX})\s+\d+\s+((?:[RTBSHIrPDCM]\s+)+)(\S+)\s+({PORT_RX})\s*$", line)
            if m:
                add(Neighbor(local_port=m.group(2), remote_name=m.group(1), remote_port=m.group(5),
                             platform=m.group(4), capabilities=m.group(3).strip(), source="cdp"))
        # LLDP detail
        for blk in re.split(r"^-{10,}\s*$", self.sec("show lldp neighbors detail"), flags=re.M):
            loc = re.search(r"Local Intf:\s*(\S+)", blk)
            if not loc:
                continue
            g = lambda rx: (re.search(rx, blk, re.M) or [None, ""])[1].strip()
            desc = re.search(r"System Description:\s*\n?(.*?)\n\s*Time remaining", blk, re.S)
            ip = re.search(rf"(?:IP|Management Addresses:\s*\n?\s*IP):\s*({IPV4})", blk)
            chassis = g(r"Chassis id:\s*(.+)")
            add(Neighbor(local_port=loc.group(1), remote_name=g(r"System Name:\s*(.+)"), remote_port=g(r"Port id:\s*(.+)"),
                         remote_ip=ip.group(1) if ip else "", remote_mac=mac_or_blank(chassis),
                         capabilities=g(r"System Capabilities:\s*(.+)"), description=" ".join((desc.group(1) if desc else "").split()),
                         source="lldp"))
        # LLDP brief
        for m in re.finditer(rf"^(\S+)\s+({PORT_RX})\s+\d+\s+([A-Z,\-]+)?\s+(\S+)\s*$", self.sec("show lldp neighbors", exact=True), re.M):
            if m.group(1).lower() in ("device", "total"):
                continue
            add(Neighbor(local_port=m.group(2), remote_name=m.group(1), remote_port=m.group(4),
                         capabilities=m.group(3) or "", source="lldp"))

    # ------------------------------------------------------------------ DHCP
    def parse_dhcp(self):
        sw = self.sw
        snoop = self.sec("show ip dhcp snooping binding")
        for m in re.finditer(rf"^({MAC_RX})\s+({IPV4})\s+\d+\s+\S+\s+(\d+)\s+(\S+)", snoop, re.M):
            sw.dhcp_bindings[m.group(2)] = {"mac": norm_mac(m.group(1)), "vlan": m.group(3), "port": canon_port(m.group(4))}
        for m in re.finditer(rf"^({IPV4})\s+([0-9a-fA-F.]{{11,}})\s+", self.sec("show ip dhcp binding"), re.M):
            sw.dhcp_bindings.setdefault(m.group(1), {"mac": "", "vlan": "", "port": ""})
