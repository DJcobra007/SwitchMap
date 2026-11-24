"""TP-Link JetStream / Omada managed switches (TL-SG3xxx, T2600G, T1600G ...)."""
from __future__ import annotations
import re

from ..models import Neighbor
from ..utils import IPV4, mac_or_blank, norm_mac, canon_port, iter_blocks, blocked_from_text, to_netmask
from .base import BaseParser


class TPLinkParser(BaseParser):
    vendor = "TP-Link"
    os_family = "JetStream"
    steps = ("parse_config", "parse_system", "parse_status", "parse_stp", "parse_neighbors")

    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if (m := re.search(r'^hostname\s+"?([^"\n]+?)"?\s*$', cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(rf"^ip route\s+0\.0\.0\.0\s+0\.0\.0\.0\s+({IPV4})", cfg, re.M)) or \
           (m := re.search(rf"^ip default-gateway\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if (m := re.search(r"^spanning-tree mode\s+(\S+)", cfg, re.M)):
            sw.stp_mode = m.group(1)
        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):
            n = re.search(r'^\s*name\s+"?([^"\n]+?)"?\s*$', body, re.M)
            self.vlan_names[int(m.group(1))] = n.group(1) if n else f"VLAN{m.group(1)}"
        for m, body in iter_blocks(cfg, r"^interface vlan\s+(\d+)\s*$"):
            ip = (re.search(rf"ip address-alloc static ip-address\s+({IPV4})\s+mask\s+({IPV4})", body)
                  or re.search(rf"^\s*ip address\s+({IPV4})\s+({IPV4})", body, re.M))
            if ip:
                sw.svis[m.group(1)] = {"ip": ip.group(1), "mask": ip.group(2), "desc": self.vlan_names.get(int(m.group(1)), "")}
        for m, body in iter_blocks(cfg, r"^interface (?:range )?((?:ten-)?gigabitEthernet|fastEthernet)\s+(\S+)\s*$"):
            if "," in m.group(2) or "-" in m.group(2):
                continue
            i = self.iface(f"{m.group(1)}{m.group(2)}")
            i.config = self.block_text(f"interface {m.group(1)} {m.group(2)}", body)
            d = re.search(r'^\s*description\s+"?([^"\n]+?)"?\s*$', body, re.M)
            i.description = d.group(1) if d else ""
            i.admin_down = bool(re.search(r"^\s*shutdown\s*$", body, re.M))
            if (g := re.search(r"^\s*channel-group\s+(\d+)(?:\s+mode\s+(\S+))?", body, re.M)):
                self.lag(f"Port-channel{g.group(1)}", member=i.name, mode=g.group(2) or "")

    def parse_system(self):
        txt, sw = self.sec("show system-info"), self.sw
        m1 = self.member(1)
        if (m := re.search(r"Firmware Version\s*[-:]\s*(.+)", txt)):
            sw.firmware = m.group(1).strip()
        if (m := re.search(r"MAC Address\s*[-:]\s*(\S+)", txt)):
            m1.mac = norm_mac(m.group(1))
        if (m := re.search(r"Serial Number\s*[-:]\s*(\S+)", txt)):
            m1.serial = m.group(1)
        if (m := re.search(r"System Description\s*[-:]\s*(.+)", txt)):
            m1.model = re.sub(r"\s+", " ", m.group(1)).strip()
        if (m := re.search(r"^\s*#?\s*(T[LG]-\S+|T\d{4}\S*)", self.text, re.M)) and not m1.model:
            m1.model = m.group(1)

    def parse_status(self):
        found = False
        for m in re.finditer(r"^\s*(Gi|Te|Fa)?(\d+/\d+/\d+)\s+(?:\S+\s+)*?(LinkUp|LinkDown|Link Up|Link Down)\b", self.sec("show interface status"), re.M):
            found = True
            i = self.iface(f"{'gigabitEthernet' if m.group(1) != 'Te' else 'ten-gigabitEthernet'}{m.group(2)}")
            i.status = "up" if "up" in m.group(3).lower() else "down"
        if not found:
            self.warn("No 'show interface status' output: port up/down colours unavailable.")

    def parse_stp(self):
        sw = self.sw
        sw.stp_version = {"stp": "STP (802.1D)", "rstp": "RSTP (802.1w)", "mstp": "MSTP (802.1s)"}.get(sw.stp_mode.lower(), sw.stp_mode)
        sw.blocked_ports = sorted({p for p in blocked_from_text(self.sec("show spanning-tree"))})

    def parse_neighbors(self):
        for blk in re.split(r"(?=^\s*Local Interface\s*:)", self.sec("show lldp neighbor-information"), flags=re.M):
            lp = re.search(r"Local Interface\s*:\s*(\S+)", blk)
            if lp:
                g = lambda rx: (re.search(rx, blk, re.M | re.I) or [None, ""])[1].strip()
                self.sw.neighbors.append(Neighbor(local_port=lp.group(1), remote_name=g(r"System Name\s*:\s*(.+)"),
                                                  remote_port=g(r"Port ID\s*:\s*(.+)"), remote_mac=mac_or_blank(g(r"Chassis ID\s*:\s*(.+)")),
                                                  remote_ip=(re.search(IPV4, g(r"Management Address\s*:\s*(.+)")) or [""])[0], source="lldp"))
