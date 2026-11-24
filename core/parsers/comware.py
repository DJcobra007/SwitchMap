"""HPE / H3C Comware (5120/5130/5500/5900, OfficeConnect 1950 ...)."""
from __future__ import annotations
import re

from ..models import Neighbor, Component
from ..utils import IPV4, MAC_RX, mac_or_blank, norm_mac, canon_port, iter_blocks, media_from_type
from .base import BaseParser


class ComwareParser(BaseParser):
    vendor = "HPE / H3C"
    os_family = "Comware"
    steps = ("parse_config", "parse_version", "parse_devinfo", "parse_irf", "parse_status", "parse_stp", "parse_neighbors")

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r"^\s*sysname\s+(\S+)", cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(rf"^ip route-static\s+0\.0\.0\.0\s+(?:0\.0\.0\.0|0)\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if (m := re.search(r"^\s*stp mode\s+(\S+)", cfg, re.M)):
            sw.stp_mode = m.group(1)
        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):
            n = re.search(r"^\s*name\s+(.+?)\s*$", body, re.M)
            self.vlan_names[int(m.group(1))] = n.group(1) if n else f"VLAN {int(m.group(1)):04d}"
        for m, body in iter_blocks(cfg, r"^interface\s+Vlan-interface(\d+)\s*$"):
            ip = re.search(rf"^\s*ip address\s+({IPV4})\s+({IPV4}|\d+)", body, re.M)
            if ip:
                from ..utils import to_netmask
                sw.svis[m.group(1)] = {"ip": ip.group(1), "mask": to_netmask(ip.group(2)), "desc": ""}
        for m, body in iter_blocks(cfg, r"^interface\s+((?:Ten-)?GigabitEthernet\d+/\d+/\d+|Bridge-Aggregation\d+)\s*$"):
            i = self.iface(m.group(1))
            i.config = self.block_text(f"interface {m.group(1)}", body)
            d = re.search(r"^\s*description\s+(.+?)\s*$", body, re.M)
            i.description = d.group(1) if d else ""
            i.admin_down = bool(re.search(r"^\s*shutdown\s*$", body, re.M))
            if m.group(1).startswith("Bridge-Aggregation"):
                self.lag(m.group(1), protocol="LACP" if re.search(r"link-aggregation mode dynamic", body) else "static", description=i.description)
            elif (g := re.search(r"^\s*port link-aggregation group\s+(\d+)", body, re.M)):
                self.lag(f"Bridge-Aggregation{g.group(1)}", member=m.group(1))
        for m in re.finditer(r"^\s*irf member\s+(\d+)\s+priority\s+(\d+)", cfg, re.M):
            self.member(int(m.group(1))).priority = m.group(2)

    def parse_version(self):
        txt = self.sec("display version")
        if (m := re.search(r"Software,?\s+Version\s+([\w.\-]+)(?:,\s*(?:Release|ESS)\s*(\w+))?", txt)):
            self.sw.firmware = m.group(1) + (f" ({m.group(2)})" if m.group(2) else "")

    def parse_devinfo(self):
        txt = self.sec("display device manuinfo")
        for blk in re.split(r"(?=^\s*(?:Slot|Chassis)\s+\d+)", txt, flags=re.M):
            sm = re.match(r"\s*(?:Slot|Chassis)\s+(\d+)", blk)
            if not sm:
                continue
            mem = self.member(int(sm.group(1)))
            g = lambda rx: (re.search(rx, blk, re.M) or [None, ""])[1].strip()
            mem.model = g(r"DEVICE_NAME\s*:\s*(.+)") or mem.model
            mem.serial = g(r"DEVICE_SERIAL_NUMBER\s*:\s*(\S+)") or mem.serial
            mem.mac = mac_or_blank(g(r"MAC_ADDRESS\s*:\s*(\S+)")) or mem.mac

    def parse_irf(self):
        for m in re.finditer(rf"^\s*\*?\+?\s*(\d+)\s+(Master|Standby)\s+(\d+)\s+({MAC_RX})", self.sec("display irf"), re.M):
            mem = self.member(int(m.group(1)))
            mem.role, mem.priority = {"Master": "Active"}.get(m.group(2), m.group(2)), m.group(3)

    def parse_status(self):
        found = False
        for m in re.finditer(r"^(XGE|GE|BAGG|FGE)(\d+(?:/\d+)*)\s+(UP|DOWN|ADM|Stby)\b", self.sec("display interface brief"), re.M):
            found = True
            name = {"GE": "GigabitEthernet", "XGE": "Ten-GigabitEthernet", "BAGG": "Bridge-Aggregation"}.get(m.group(1), m.group(1)) + m.group(2)
            i = self.iface(name)
            i.status, i.admin_down = ("up" if m.group(3) == "UP" else "down"), m.group(3) == "ADM"
            i.media = "fiber" if m.group(1) in ("XGE", "FGE") else i.media
        if not found:
            self.warn("No 'display interface brief' output: port up/down colours unavailable.")

    def parse_stp(self):
        sw = self.sw
        sw.stp_version = {"stp": "STP (802.1D)", "rstp": "RSTP (802.1w)", "mstp": "MSTP (802.1s)", "pvst": "PVST"}.get(sw.stp_mode.lower(), sw.stp_mode)
        full = self.sec("display stp")
        root, me = re.search(r"CIST Root/ERPC\s*:\s*(\d+)\.(\S+?)\s*,", full), re.search(r"CIST Bridge\s*:\s*(\d+)\.(\S+)", full)
        if root:
            sw.stp_roots = {"CIST": {"mac": norm_mac(root.group(2)), "priority": root.group(1), "cost": "", "port": "",
                                     "is_root": bool(me and norm_mac(me.group(2)) == norm_mac(root.group(2)))}}
        blocked = set()
        for m in re.finditer(r"^\s*\d+\s+(XGE|GE|BAGG)(\d+(?:/\d+)*)\s+ALTE\s+DISCARDING", self.sec("display stp brief"), re.M):
            blocked.add(canon_port(m.group(1).lower() + m.group(2)))
        sw.blocked_ports = sorted(blocked)

    def parse_neighbors(self):
        for blk in re.split(r"(?=^\s*LLDP neighbor-information of port)", self.sec("display lldp neighbor-information"), flags=re.M):
            lp = re.search(r"LLDP neighbor-information of port\s+\d+\[(\S+?)\]", blk)
            if lp:
                g = lambda rx: (re.search(rx, blk, re.M) or [None, ""])[1].strip()
                self.sw.neighbors.append(Neighbor(local_port=lp.group(1), remote_name=g(r"System name\s*:\s*(.+)"),
                                                  remote_port=g(r"Port ID\s*:\s*(.+)"), remote_mac=mac_or_blank(g(r"Chassis ID\s*:\s*(.+)")),
                                                  remote_ip=(re.search(IPV4, g(r"Management address\s*:\s*(.+)")) or [""])[0], source="lldp"))
