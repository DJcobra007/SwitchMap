"""D-Link managed switches with the DGS/DES/DXS 'config ... / create ...' CLI."""
from __future__ import annotations
import re

from ..models import Neighbor
from ..utils import IPV4, norm_mac, mac_or_blank, to_netmask, split_ip_mask, blocked_from_text
from .base import BaseParser


class DLinkParser(BaseParser):
    vendor = "D-Link"
    os_family = "D-Link CLI"
    steps = ("parse_config", "parse_system", "parse_status", "parse_stp", "parse_neighbors")

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        m1 = self.member(1)
        if (m := re.search(r"^#\s*(D[GESX]S-\S+)", cfg, re.M | re.I)):
            m1.model = m.group(1)
        if (m := re.search(r"^#\s*Firmware:\s*(.+)", cfg, re.M)):
            sw.firmware = m.group(1).strip()
        if (m := re.search(r'^config snmp system_name\s+"?([^"\n]+?)"?\s*$', cfg, re.M)):
            sw.hostname = m.group(1)
        for m in re.finditer(r"^config ipif\s+(\S+)\s+ipaddress\s+(\S+)(?:\s+(\S+))?(?:\s+vlan\s+(\S+))?", cfg, re.M):
            pm = split_ip_mask(m.group(2) + (" " + m.group(3) if m.group(3) and re.match(IPV4, m.group(3)) else ""))
            if pm:
                key = m.group(4) or f"if:{m.group(1)}"
                sw.svis[key if key.isdigit() else f"if:{m.group(1)}"] = {"ip": pm[0], "mask": pm[1], "desc": m.group(1)}
                if m.group(1).lower() == "system":
                    self.mgmt_hints[key if key.isdigit() else f"if:{m.group(1)}"] += 1
        if (m := re.search(rf"^create iproute default\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        for m in re.finditer(r"^create vlan\s+(\S+)\s+tag\s+(\d+)", cfg, re.M):
            self.vlan_names[int(m.group(2))] = m.group(1)
        self.vlan_names.setdefault(1, "default")
        if (m := re.search(r"^config stp version\s+(\S+)", cfg, re.M)):
            sw.stp_mode = m.group(1)
        for m in re.finditer(r'^config ports\s+(\d+(?:[/:]\d+)?)\s+.*?description\s+"?([^"\n]+?)"?\s*$', cfg, re.M):
            self.iface(m.group(1)).description = m.group(2)

    def parse_system(self):
        txt, sw = self.sec("show switch"), self.sw
        m1 = self.member(1)
        for rx, attr in ((r"Device Type\s*:\s*(\S+)", "model"), (r"MAC Address\s*:\s*(\S+)", "mac"), (r"Serial Number\s*:\s*(\S+)", "serial")):
            if (m := re.search(rx, txt)):
                setattr(m1, attr, norm_mac(m.group(1)) if attr == "mac" else m.group(1))
        if (m := re.search(r"Firmware Version\s*:\s*(.+)", txt)):
            sw.firmware = m.group(1).strip()
        if (m := re.search(r"System Name\s*:\s*(\S+)", txt)) and not sw.hostname:
            sw.hostname = m.group(1)

    def parse_status(self):
        found = False
        for m in re.finditer(r"^\s*(\d+(?:[/:]\d+)?)\s+(?:Enabled|Disabled)\s+\S+\s+(Link Down|\d+\S*)", self.sec("show ports"), re.M):
            found = True
            self.iface(m.group(1)).status = "down" if m.group(2) == "Link Down" else "up"
        if not found:
            self.warn("No 'show ports' output: port up/down colours unavailable.")

    def parse_stp(self):
        sw = self.sw
        sw.stp_version = {"rstp": "RSTP (802.1w)", "stp_compatible": "STP (802.1D)", "mstp": "MSTP (802.1s)"}.get(sw.stp_mode.lower(), sw.stp_mode)
        sw.blocked_ports = sorted(set(blocked_from_text(self.sec("show stp ports"))))

    def parse_neighbors(self):
        for blk in re.split(r"(?=^\s*Port ID\s*:\s*\d)", self.sec("show lldp remote_ports"), flags=re.M):
            lp = re.match(r"\s*Port ID\s*:\s*(\S+)", blk)
            if lp:
                g = lambda rx: (re.search(rx, blk, re.M | re.I) or [None, ""])[1].strip()
                self.sw.neighbors.append(Neighbor(local_port=lp.group(1), remote_name=g(r"System Name\s*:\s*(.+)"),
                                                  remote_port=g(r"Port ID\s*:\s*(?!\d+\s*$)(.+)"), remote_mac=mac_or_blank(g(r"Chassis ID\s*:\s*(.+)")),
                                                  remote_ip=(re.search(IPV4, blk) or [""])[0], source="lldp"))
