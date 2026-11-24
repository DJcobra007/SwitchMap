"""Aruba AOS-CX (6000/6100/6200/6300/6400/8xxx series)."""
from __future__ import annotations
import re

from ..models import Neighbor, Component
from ..utils import stp_root_blocks, IPV4, MAC_RX, norm_mac, canon_port, media_from_type, iter_blocks, split_ip_mask, blocked_from_text
from .base import BaseParser


class ArubaCXParser(BaseParser):
    vendor = "HPE / Aruba"
    os_family = "AOS-CX"
    steps = ("parse_config", "parse_system", "parse_vsf", "parse_status", "parse_stp", "parse_neighbors", "parse_transceivers")

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r"^hostname\s+(\S+)", cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(r"^!Version\s+ArubaOS-CX\s+(\S+)", cfg, re.M)):
            sw.firmware = m.group(1)
        if (m := re.search(rf"^ip route\s+0\.0\.0\.0/0\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if (m := re.search(r"^spanning-tree mode\s+(\S+)", cfg, re.M)):
            sw.stp_mode = m.group(1)
        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):
            n = re.search(r"^\s*name\s+(.+?)\s*$", body, re.M)
            self.vlan_names[int(m.group(1))] = n.group(1) if n else f"VLAN{m.group(1)}"
        for m, body in iter_blocks(cfg, r"^interface\s+vlan\s+(\d+)\s*$"):
            ip = re.search(r"^\s*ip address\s+(\S+)", body, re.M)
            if ip and (pm := split_ip_mask(ip.group(1))):
                sw.svis[m.group(1)] = {"ip": pm[0], "mask": pm[1], "desc": ""}
        for m, body in iter_blocks(cfg, r"^interface\s+mgmt\s*$"):
            ip = re.search(r"^\s*ip static\s+(\S+)", body, re.M)
            gw = re.search(rf"^\s*default-gateway\s+({IPV4})", body, re.M)
            if ip and (pm := split_ip_mask(ip.group(1))):
                sw.svis["if:mgmt"] = {"ip": pm[0], "mask": pm[1], "desc": "management port"}
                self.mgmt_hints["if:mgmt"] += 1
            if gw and not sw.gateway:
                sw.gateway = gw.group(1)
        for m, body in iter_blocks(cfg, r"^interface\s+(\d+/\d+/\d+|lag\s+\d+)\s*$"):
            i = self.iface(m.group(1).replace(" ", ""))
            i.config = self.block_text(f"interface {m.group(1)}", body)
            d = re.search(r"^\s*description\s+(.+?)\s*$", body, re.M)
            i.description = d.group(1) if d else ""
            i.admin_down = not re.search(r"^\s*no shutdown\s*$", body, re.M)
            if i.name.startswith("lag"):
                self.lag(i.name, protocol="LACP" if re.search(r"lacp mode", body) else "static", description=i.description)
            if (v := re.search(r"^\s*vlan access\s+(\d+)", body, re.M)):
                i.mode, i.vlan = "access", v.group(1)
            elif re.search(r"^\s*vlan trunk", body, re.M):
                i.mode, i.vlan = "trunk", "trunk"
            elif (lg := re.search(r"^\s*lag\s+(\d+)", body, re.M)):
                self.lag(f"lag{lg.group(1)}", member=i.name)

    def parse_system(self):
        txt, sw = self.sec("show system"), self.sw
        m1 = self.member(1)
        for rx, attr in ((r"Chassis Serial Nbr\s*:\s*(\S+)", "serial"), (r"Product Name\s*:\s*(.+)", "model"),
                         (r"Base MAC Address\s*:\s*(\S+)", "mac")):
            if (m := re.search(rx, txt)):
                setattr(m1, attr, norm_mac(m.group(1)) if attr == "mac" else m.group(1).strip())
        if (m := re.search(r"ArubaOS-CX Version\s*:\s*(\S+)", txt)) or (m := re.search(r"^Version\s*:\s*(\S+)", self.sec("show version"), re.M)):
            sw.firmware = m.group(1)

    def parse_vsf(self):
        for m in re.finditer(rf"^\s*(\d+)\s+({MAC_RX})\s+(\S+)\s+(Master|Standby|Member|Commander)", self.sec("show vsf"), re.M):
            mem = self.member(int(m.group(1)))
            mem.mac, mem.model = norm_mac(m.group(2)), mem.model or m.group(3)
            mem.role = {"Master": "Active", "Commander": "Active"}.get(m.group(4), m.group(4))

    def parse_status(self):
        txt, found = self.sec("show interface brief"), False
        for m in re.finditer(r"^(\d+/\d+/\d+|lag\d+)\s+(\S+)\s+(?:(access|trunk|routed|native-\S+)\s+)?(\S+)\s+(yes|no)\s+(up|down)\b", txt, re.M):
            found = True
            i = self.iface(m.group(1))
            i.status, i.admin_down, i.port_type = m.group(6), m.group(5) == "no", m.group(4)
            i.media = media_from_type(m.group(4)) or i.media
        if not found:
            self.warn("No 'show interface brief' output: port up/down colours need it.")

    def parse_stp(self):
        sw = self.sw
        txt = self.sec("show spanning-tree")
        sw.stp_roots = stp_root_blocks(txt)
        sw.stp_version = {"mstp": "MSTP (802.1s)", "rpvst": "Rapid PVST+", "rstp": "RSTP (802.1w)"}.get(sw.stp_mode.lower(), sw.stp_mode)
        sw.blocked_ports = sorted({canon_port(p) for p in re.findall(r"^(\d+/\d+/\d+|lag\d+)\s+\S*Alternate\S*\s+Blocking", txt, re.M)}
                                  | {canon_port(p) for p in blocked_from_text(txt) if re.match(r"\d+/\d+/\d+", p)})

    def parse_neighbors(self):
        txt = self.sec("show lldp neighbor-info")
        for m in re.finditer(rf"^(\d+/\d+/\d+)\s+({MAC_RX})\s+(\S+)\s+(.*?)\s+(\d+)\s+(\S+)\s*$", txt, re.M):
            self.sw.neighbors.append(Neighbor(local_port=m.group(1), remote_mac=norm_mac(m.group(2)), remote_port=m.group(3),
                                              remote_name=m.group(6), source="lldp"))

    def parse_transceivers(self):
        txt = self.sec("show interface transceiver")
        for m in re.finditer(r"^(\d+/\d+/\d+)\s+(\S+)\s+(\S+)\s+(\S+)", txt, re.M):
            if m.group(1) and not m.group(2).lower().startswith("port"):
                kind = "DAC / AOC Cable" if re.search(r"DA\d|DAC|AOC|CU", m.group(2) + m.group(3), re.I) else "SFP / Transceiver"
                self.sw.components.append(Component(kind=kind, model=m.group(3), serial=m.group(4), port=m.group(1), make="HPE / Aruba"))
                self.iface(m.group(1)).media = "fiber"
