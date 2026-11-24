"""Alcatel-Lucent Enterprise OmniSwitch (AOS 6.x / 8.x: OS6350/6450/6860/6900 ...)."""
from __future__ import annotations
import re

from ..models import Neighbor
from ..utils import IPV4, mac_or_blank, norm_mac, canon_port, to_netmask, blocked_from_text, MAC_RX
from .base import BaseParser


class AlcatelParser(BaseParser):
    vendor = "Alcatel-Lucent"
    os_family = "OmniSwitch AOS"
    steps = ("parse_config", "parse_chassis", "parse_status", "parse_stp", "parse_neighbors")
    flat_ports = True

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r'^system name\s+"?([^"\n]+?)"?\s*$', cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(rf"^ip static-route\s+0\.0\.0\.0\s+mask\s+0\.0\.0\.0\s+gateway\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        elif (m := re.search(rf"^ip static-route\s+0\.0\.0\.0/0\s+gateway\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        for m in re.finditer(r'^vlan\s+(\d+)\s+(?:enable|disable)\s+(?:name\s+"?([^"\n]*?)"?\s*)?$', cfg, re.M):
            self.vlan_names[int(m.group(1))] = (m.group(2) or f"VLAN {m.group(1)}").strip()
        for m in re.finditer(rf'^ip interface\s+"?([^"\n]+?)"?\s+address\s+({IPV4})\s+mask\s+({IPV4})(?:\s+vlan\s+(\d+))?', cfg, re.M):
            key = m.group(4) or f"if:{m.group(1)}"
            self.sw.svis[key] = {"ip": m.group(2), "mask": m.group(3), "desc": m.group(1)}
            if re.search(r"mgmt|management|manage", m.group(1), re.I):
                self.mgmt_hints[key] += 1
        for m in re.finditer(r'^interfaces\s+(\d+/\d+(?:/\d+)?)\s+alias\s+"?([^"\n]+?)"?\s*$', cfg, re.M):
            self.iface(m.group(1)).description = m.group(2)
        if (m := re.search(r"^(?:bridge|spantree)\s+.*?protocol\s+(\S+)", cfg, re.M)) or (m := re.search(r"^bridge mode\s+(\S+)", cfg, re.M)):
            self.sw.stp_mode = m.group(1)

    def parse_chassis(self):
        sw = self.sw
        txt = self.sec("show chassis") + "\n" + self.sec("show system")
        if (m := re.search(r"(?:Description|Software Version)\s*:\s*.*?(\d+\.\d+\.\d+\.\d+\.R\d+)", txt, re.I)):
            sw.firmware = m.group(1)
        for blk in re.split(r"(?=^\s*Chassis\s+\d+)", self.sec("show chassis"), flags=re.M):
            cm = re.match(r"\s*Chassis\s+(\d+)", blk)
            if not cm:
                continue
            mem = self.member(int(cm.group(1)))
            g = lambda rx: (re.search(rx, blk, re.I) or [None, ""])[1].strip().rstrip(",")
            mem.model = g(r"Model Name:\s*(\S+)") or mem.model
            mem.serial = g(r"Serial Number:\s*(\S+)") or mem.serial
            mem.mac = norm_mac(g(r"MAC Address:\s*(\S+)")) if g(r"MAC Address:\s*(\S+)") else mem.mac
        if not self.members:
            m1 = self.member(1)
            m1.model = (re.search(r"Model Name:\s*(\S+?),?\s", txt) or [None, ""])[1]
            m1.serial = (re.search(r"Serial Number:\s*(\S+?),?\s", txt) or [None, ""])[1]
            if (m := re.search(rf"MAC Address:\s*({MAC_RX})", txt)):
                m1.mac = norm_mac(m.group(1))
        for m in re.finditer(r"^\s*(\d+)\s+(MASTER|SLAVE|IDLE|PRIMARY|SECONDARY)\s+\S+\s+\S+\s+(\d+)", self.sec("show stack topology"), re.M | re.I):
            mem = self.member(int(m.group(1)))
            mem.role = {"master": "Active", "primary": "Active", "slave": "Member", "secondary": "Standby"}.get(m.group(2).lower(), "Member")

    def parse_status(self):
        txt, found = self.sec("show interfaces status"), False
        for m in re.finditer(r"^\s*(\d+/\d+(?:/\d+)?)\s+(?:\S+\s+)?(?:Enable|Disable|en|dis)\w*\s+(?:\S+\s+)*?(Up|Down)\b", txt, re.M | re.I):
            found = True
            i = self.iface(m.group(1))
            i.status = m.group(2).lower()
        if not found:
            self.warn("No 'show interfaces status' output: port up/down colours unavailable.")

    def parse_stp(self):
        sw = self.sw
        sw.stp_version = {"rstp": "RSTP (802.1w)", "mstp": "MSTP (802.1s)", "1x1": "Per-VLAN STP (1x1)", "flat": "Flat STP/RSTP"}.get(
            sw.stp_mode.lower(), sw.stp_mode)
        sw.blocked_ports = sorted({canon_port(p) for p in blocked_from_text(self.sec("show spantree"))})

    def parse_neighbors(self):
        txt = self.sec("show lldp remote-system")
        for blk in re.split(r"(?=^Remote LLDP nearest-bridge Agents on Local Port)", txt, flags=re.M):
            lp = re.search(r"Local Port\s+(\d+/\d+(?:/\d+)?)", blk)
            if not lp:
                continue
            g = lambda rx: (re.search(rx, blk, re.M) or [None, ""])[1].strip().rstrip(",")
            chassis = g(r"Chassis (?:ID|Id)\s+(?:\d+\s*=\s*)?(.+)")
            self.sw.neighbors.append(Neighbor(
                local_port=lp.group(1), remote_name=g(r"System Name\s*=\s*(.+)"), remote_port=g(r"Port ID\s+(?:\d+\s*=\s*)?(.+)"),
                remote_ip=(re.search(rf"Management IP Address\s*=\s*({IPV4})", blk) or [None, ""])[1],
                remote_mac=mac_or_blank(chassis), description=g(r"System Description\s*=\s*(.+)"),
                capabilities=g(r"Capabilities Supported\s*=\s*(.+)"), source="lldp"))
