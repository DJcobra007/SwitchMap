"""HPE / Aruba ProCurve family (ArubaOS-Switch: 2530, 2540, 2920, 2930F/M, 3810, 5400R ...)."""
from __future__ import annotations
import re

from ..models import Neighbor, Component
from ..utils import expand_ports, IPV4, MAC_RX, mac_or_blank, norm_mac, canon_port, media_from_type, iter_blocks, fixed_columns, split_ip_mask, to_netmask
from .base import BaseParser


class ArubaOSSwitchParser(BaseParser):
    vendor = "HPE / Aruba"
    os_family = "ArubaOS-Switch (ProCurve)"
    steps = ("parse_config", "parse_system", "parse_stack", "parse_vlans", "parse_status", "parse_stp",
             "parse_neighbors", "parse_transceivers")

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r'^hostname\s+"?([^"\n]+?)"?\s*$', cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(r"^;\s*(J[A-Z]?\d{4}[A-Z]?|JL\d{3}[A-Z]?)\s+Configuration Editor; Created on release #(\S+)", cfg, re.M)):
            self.product_no, sw.firmware = m.group(1), m.group(2)
            self.member(1).model = self.member(1).model or m.group(1)
        if (m := re.search(rf"^ip default-gateway\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        elif (m := re.search(rf"^ip route\s+0\.0\.0\.0\s+0\.0\.0\.0\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if re.search(r"^spanning-tree\s*$", cfg, re.M) or re.search(r"^spanning-tree\b", cfg, re.M):
            mm = re.search(r"^spanning-tree\s+(?:mode\s+)?(mstp|rstp|rpvst|pvst)", cfg, re.M)
            force = re.search(r"^spanning-tree force-version\s+(\S+)", cfg, re.M)
            sw.stp_mode = mm.group(1) if mm else ("rstp" if force and "rstp" in force.group(1) else "mstp")
        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):
            vid = int(m.group(1))
            n = re.search(r'^\s*name\s+"?([^"\n]+?)"?\s*$', body, re.M)
            self.vlan_names[vid] = n.group(1) if n else f"VLAN{vid}"
            ip = re.search(rf"^\s*ip address\s+({IPV4})\s*(?:/|\s)\s*({IPV4}|\d{{1,2}})", body, re.M)
            if ip:
                self.sw.svis[str(vid)] = {"ip": ip.group(1), "mask": to_netmask(ip.group(2)), "desc": self.vlan_names[vid]}
        for m in re.finditer(r"^trunk\s+(\S+)\s+(trk\d+)\s*(lacp|dt-lacp|trunk)?", cfg, re.M | re.I):
            for port in expand_ports(m.group(1)):
                self.lag(m.group(2).capitalize(), member=port, protocol={"lacp": "LACP", "dt-lacp": "LACP", "trunk": "static"}.get((m.group(3) or "trunk").lower(), ""))
        for m, body in iter_blocks(cfg, r"^stacking\s*$"):
            for mm in re.finditer(r'member\s+(\d+)\s+type\s+"?(\w+)"?\s+mac-address\s+(\S+)', body):
                mem = self.member(int(mm.group(1)))
                mem.model, mem.mac = mm.group(2), norm_mac(mm.group(3))
            for mm in re.finditer(r"member\s+(\d+)\s+priority\s+(\d+)", body):
                self.member(int(mm.group(1))).priority = mm.group(2)
        for m, body in iter_blocks(cfg, r"^interface\s+(\S+)\s*$"):
            i = self.iface(m.group(1))
            i.config = self.block_text(f"interface {m.group(1)}", body)
            n = re.search(r'^\s*name\s+"?([^"\n]+?)"?\s*$', body, re.M)
            i.description = n.group(1) if n else ""
            i.admin_down = bool(re.search(r"^\s*disable\s*$", body, re.M))

    def parse_system(self):
        txt, sw = self.sec("show system"), self.sw
        m1 = self.member(1)
        if (m := re.search(r"Software revision\s*:\s*(\S+)", txt)):
            sw.firmware = m.group(1)
        if (m := re.search(r"Base MAC Addr\s*:\s*(\S+)", txt)):
            m1.mac = m1.mac or norm_mac(m.group(1))
        if (m := re.search(r"Serial Number\s*:\s*(\S+)", txt)):
            m1.serial = m1.serial or m.group(1)
        if (m := re.search(r"System Name\s*:\s*(.+)", txt)) and not sw.hostname:
            sw.hostname = m.group(1).strip()
        mods = self.sec("show modules")
        for i, m in enumerate(re.finditer(r"Chassis:\s*(\S+)\s+(J\w+|JL\w+)\s+Serial Number:\s*(\S+)", mods), 1):
            mem = self.member(i)
            mem.model, mem.serial = f"{m.group(1)} ({m.group(2)})", mem.serial or m.group(3)

    def parse_stack(self):
        txt = self.sec("show stacking")
        for m in re.finditer(rf"^\s*(\d+)\s*\|\s*({MAC_RX})\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(Commander|Standby|Member|Not Joined)",
                             txt, re.M):
            mem = self.member(int(m.group(1)))
            mem.mac, mem.model, mem.priority = norm_mac(m.group(2)), m.group(3).strip(), m.group(4)
            mem.role = {"Commander": "Active"}.get(m.group(5), m.group(5))

    def parse_vlans(self):
        for m in re.finditer(r"^\s*(\d+)\s+(.+?)\s+\|\s+(?:Port-based|Protocol)", self.sec("show vlans"), re.M):
            self.vlan_names.setdefault(int(m.group(1)), m.group(2).strip())

    def parse_status(self):
        txt = self.sec("show interfaces brief")
        found = False
        for m in re.finditer(r"^\s*(\S+)\s+(\S+)\s+\|\s+\S+\s+(Yes|No)\s+(Up|Down)\s+(\S*)", txt, re.M):
            found = True
            i = self.iface(m.group(1))
            i.port_type, i.status, i.admin_down, i.speed = m.group(2), m.group(4).lower(), m.group(3) == "No", m.group(5)
            i.media = media_from_type(m.group(2)) or i.media
        if not found:
            self.warn("No 'show interfaces brief' output: port up/down colours unavailable.")

    def parse_stp(self):
        txt, sw = self.sec("show spanning-tree"), self.sw
        mine = re.search(rf"Switch MAC Address\s*:\s*({MAC_RX})", txt)
        root = re.search(rf"CST Root MAC Address\s*:\s*({MAC_RX})", txt)
        pri = re.search(r"CST Root Priority\s*:\s*(\d+)", txt)
        if root:
            sw.stp_roots = {"CST": {"mac": norm_mac(root.group(1)), "priority": pri.group(1) if pri else "", "cost": "", "port": "",
                                    "is_root": bool(mine and norm_mac(mine.group(1)) == norm_mac(root.group(1)))}}
        mode = (re.search(r"Protocol Version\s*:\s*(\S+)", txt) or [None, sw.stp_mode])[1] or ""
        sw.stp_version = {"mstp": "MSTP (802.1s)", "rstp": "RSTP (802.1w)", "rpvst": "Rapid PVST+", "rapid-pvst": "Rapid PVST+"}.get(
            mode.lower(), mode)
        blocked = set()
        for m in re.finditer(r"^\s*(\S+)\s+\S+\s+\d+\s+\d+\s+(Alternate|Backup|Designated|Root|Disabled|Master)\s+(Blocking|Forwarding|Discarding|Learning|Disabled)",
                             txt, re.M):
            if m.group(3) in ("Blocking", "Discarding") or m.group(2) in ("Alternate", "Backup"):
                blocked.add(canon_port(m.group(1)))
        sw.blocked_ports = sorted(blocked)

    def parse_neighbors(self):
        sw = self.sw
        detail = self.sec("show lldp info remote-device", exact=False)
        for blk in re.split(r"(?=^\s*Local Port\s*:)", detail, flags=re.M):
            lp = re.search(r"Local Port\s*:\s*(\S+)", blk)
            if not lp:
                continue
            g = lambda rx: (re.search(rx, blk, re.M) or [None, ""])[1].strip()
            chassis = g(r"ChassisId\s*:\s*(.+)")
            sw.neighbors.append(Neighbor(
                local_port=lp.group(1), remote_name=g(r"SysName\s*:\s*(.+)"), remote_port=g(r"PortId\s*:\s*(.+)"),
                remote_ip=(re.search(rf"Address\s*:\s*({IPV4})", blk) or [None, ""])[1],
                remote_mac=mac_or_blank(chassis),
                description=g(r"System Descr\s*:\s*(.+)"), capabilities=g(r"System Capabilities Supported\s*:\s*(.+)"), source="lldp"))
        if sw.neighbors:
            return
        for r in fixed_columns(detail, "LocalPort"):
            if not r.get("localport") or not r.get("sysname"):
                continue
            chassis = r.get("chassisid", "")
            sw.neighbors.append(Neighbor(local_port=r["localport"], remote_name=r["sysname"], remote_port=r.get("portid", ""),
                                         remote_mac=mac_or_blank(chassis),
                                         source="lldp"))

    def parse_transceivers(self):
        txt = self.sec("show tech transceivers") + "\n" + self.sec("show interfaces transceiver")
        for m in re.finditer(r"^\s*([A-Za-z]?\d+(?:/\d+)?)\s+(\S+)\s+([A-Z][A-Z0-9]{3,8})\s+(\S{6,})\s+(\S+)?\s*$", txt, re.M):
            kind = "DAC / AOC Cable" if re.search(r"DA\d|DAC|AOC|CU", m.group(2), re.I) else "SFP / Transceiver"
            self.sw.components.append(Component(kind=kind, model=m.group(3), serial=m.group(4), port=m.group(1), make="HPE / Aruba"))
            self.iface(m.group(1)).media = "fiber"
