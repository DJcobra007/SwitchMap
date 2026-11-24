"""Best-effort parser for any vendor we have no dedicated support for (hostname, IP, VLANs, serial/model/MAC hints)."""
from __future__ import annotations
import re

from ..utils import IPV4, MAC_ANY, norm_mac, split_ip_mask
from .base import BaseParser


class GenericParser(BaseParser):
    vendor = "Unknown"
    os_family = "Generic"
    steps = ("parse_all",)

    def parse_all(self):
        t, sw = self.text, self.sw
        for rx in (r'^\s*(?:hostname|sysname|system[ _-]?name|snmp-server name|set system name)\s+"?([\w.\-]+)"?', r"^\s*host-name\s+([\w.\-]+)"):
            if (m := re.search(rx, t, re.M | re.I)):
                sw.hostname = m.group(1)
                break
        if (m := re.search(rf"(?:default-gateway|default[- ]route|ip route 0\.0\.0\.0 0\.0\.0\.0|gateway)\s+({IPV4})", t, re.I)):
            sw.gateway = m.group(1)
        for m in re.finditer(rf"(?:interface\s+(?:vlan|Vlan-interface)\s*(\d+)|ipif\s+(\S+))[\s\S]{{0,200}}?(?:ip address|ipaddress)\s+({IPV4})\s*(?:/|\s)\s*({IPV4}|\d{{1,2}})", t, re.I):
            from ..utils import to_netmask
            sw.svis[m.group(1) or f"if:{m.group(2)}"] = {"ip": m.group(3), "mask": to_netmask(m.group(4)), "desc": ""}
        if not sw.svis and (m := re.search(rf"ip(?:v4)? address\s+({IPV4})\s+({IPV4})", t, re.I)):
            sw.svis["if:mgmt"] = {"ip": m.group(1), "mask": m.group(2), "desc": ""}
        for m in re.finditer(r'^\s*vlan\s+(\d+)\s+(?:name\s+)?"?([A-Za-z][\w\-. ]*?)"?\s*$', t, re.M):
            self.vlan_names[int(m.group(1))] = m.group(2)
        m1 = self.member(1)
        if (m := re.search(r"(?:serial(?: number)?|S/N|SN)\s*[:=#]\s*([A-Z0-9]{6,})", t, re.I)):
            m1.serial = m.group(1)
        if (m := re.search(r"(?:model(?: name| number)?|product name|device type)\s*[:=]\s*(\S+)", t, re.I)):
            m1.model = m.group(1)
        if (m := re.search(r"(?:base )?mac(?: address| addr)?\s*[:=]\s*(" + MAC_ANY.pattern + ")", t, re.I)):
            m1.mac = norm_mac(m.group(1))
        if (m := re.search(r"(?:firmware|software|system)\s+(?:version|revision)\s*[:=]?\s*(\S+)", t, re.I)):
            sw.firmware = m.group(1)
        self.warn("Vendor not recognised: only generic fields (hostname, IP, VLANs, serial/model/MAC hints) were extracted. "
                  "Pick the vendor manually on upload, or send me a sample so a parser can be added.")
