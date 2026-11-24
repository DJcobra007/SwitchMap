"""Quantum Networks switches (qntmnet.com).

Two firmware families:
  * QN-SW / QN-IS series (QN-SW-225-12P, QN-SW-325-24FP, QN-IS-225-8FP ...): Sx350-style CLI with 'config-file-header',
    'vlan database', 'interface vlan 2', ports gi1/0/1 / te1/0/1 / tf1/0/1 (25G) and Quantum's own 'more system-info'.
  * QQ-SW series (QQ-SW-155-G48-6SFPP, firmware QN_SW_QQ-*): 'Interface Ethernet1/0/1', 'vlan 1;102;107',
    'show version' with Device / CPU MAC / Serial Num lines, 'show interface' detail blocks.

Backups are usually raw PuTTY logs: abbreviated commands (sh int status, sh runn), commands typed ahead, echoed late
or not at all, output glued to the prompt line, ANSI/backspace noise. The log is therefore replayed like a terminal,
cut at every prompt, and each table is found by its own header line (the typed command is only a hint).

Reads: running-config | more system-info | show system | show inventory | show stack | show version | show vlan |
       show interfaces status | show interfaces configuration | show interfaces description | show lldp neighbors |
       show spanning-tree | show ip interface | show ip route | show interface (QQ-SW)
"""
from __future__ import annotations
import re
from collections import Counter

from ..models import Neighbor, Component
from ..parts import classify_part
from ..utils import (IPV4, MAC_RX, canon_port, clean_terminal_log, iter_blocks, mac_or_blank, media_from_type,
                     network_of, norm_mac, to_netmask)
from .base import BaseParser

# ------------------------------------------------------------------ log splitting
PROMPT = re.compile(r"^(?P<host>[A-Za-z0-9][\w.\-]*?)(?P<mode>\([\w\-]+\))?[#>](?P<rest>.*)$")
HEADER = re.compile(r"^\s*[#=\-*]{4,}\s*((?:show|sh|sho|more|display|terminal|ter|write|copy|dir)\b[^#=*]*?)\s*[#=\-*]{4,}\s*$", re.I)
BARE = re.compile(r"^(?:do\s+)?(?:sh|sho|show|more)\s+[A-Za-z][\w\-./ ]*$", re.I)
VERBS = ("show", "more", "terminal", "write", "copy", "configure", "exit", "end", "do", "reload", "ping", "traceroute",
         "clear", "dir", "enable", "disable", "delete", "boot", "logout", "quit", "debug", "undebug", "no", "clock", "ssh",
         "telnet", "help", "history", "erase", "rename", "test", "disconnect", "login", "set", "stack", "lldp")

SIGNATURES = [   # (kind, regex) - first match wins; each kind is stored as shows["show <kind>"]
    ("system-info", re.compile(r"SWITCH SYSTEM INFORMATION|^\s*Model Number\s*:", re.M)),
    ("system", re.compile(r"^\s*System (?:Description|MAC Address):", re.M)),
    ("inventory", re.compile(r'^\s*NAME:\s*"', re.M)),
    ("stack", re.compile(r"^\s*Unit Id\s+MAC Address\s+Role\b", re.M | re.I)),
    ("version", re.compile(r"^\s*Active-image:|^\s*Device:\s*\S+.*sysLocation|^\s*SoftWare Version\s", re.M)),
    ("interfaces status", re.compile(r"^\s*Port\s+Type\s+Duplex\s+Speed\s+Neg\s+ctrl\s+State", re.M)),
    ("interfaces configuration", re.compile(r"^\s*Port\s+Type\s+Duplex\s+Speed\s+Neg\s+control\s+State", re.M)),
    ("interfaces description", re.compile(r"^\s*Port\s+Description\s*$", re.M)),
    ("lldp neighbors", re.compile(r"^\s*Port\s+Device ID\s+Port ID\s+System Name", re.M)),
    ("cdp neighbors", re.compile(r"^\s*Device ID\s+Local\s+Adv\s+Time To\s+Capability", re.M)),
    ("spanning-tree", re.compile(r"Spanning tree enabled mode|^\s*Root ID\s+Priority|Self Bridge Id\s*:", re.M)),
    ("vlan", re.compile(r"^\s*Vlan\s+Name\s+Tagged Ports|^\s*VLAN\s+Name\s+Type\s+Media\s+Ports", re.M | re.I)),
    ("ip interface", re.compile(r"^\s*IP Address\s+I/F\s+I/F Status|^\s*Index\s+Interface\s+IP-Address", re.M)),
    ("ip route", re.compile(r"Gateway of last resort|IP Forwarding:|^\s*(?:S\*?|C)\s+\d+(?:\.\d+){3}/\d+.*(?:via|directly connected)", re.M)),
    ("mac address-table", re.compile(r"^\s*Vlan\s+Mac Address\s+(?:Port\s+Type|Type\s+Ports)", re.M)),
    ("interfaces", re.compile(r"\bis (?:up|down|administratively down), line protocol is", re.M)),
]

SHORT_PORT = r"(?:gi|te|tf|fa|fo|hu|xg|tw)\d+(?:/\d+)*"          # gi1/0/1, or gi1 on 1-unit SG/CBS models
PORT_COL = rf"(?:{SHORT_PORT}|Po\d+)"
STATUS_ROW = re.compile(rf"^\s*(?P<port>{SHORT_PORT})\s+(?P<type>\S+)\s+(?P<duplex>\S+)\s+(?P<speed>\S+)\s+(?P<neg>\S+)\s+"
                        rf"(?P<fc>\S+)\s+(?P<state>\S.*?)\s+(?P<bp>\S+)\s+(?P<mdix>\S+)\s*$")
PO_ROW = re.compile(r"^\s*(?P<port>Po\d+)\s+(?:\S+\s+){4,5}(?P<state>\S.*?)\s*$")
DASHES = re.compile(r"^\s*-{2,}(?:\s+-{2,})+\s*$")
# 'show stack' rows: "   1     40:a6:e8:e6:9c:11    active     gi      te" (older firmware: master / backup / slave)
STACK_ROW = re.compile(rf"(?m)^\s*(\d+)\s+({MAC_RX})\s+([A-Za-z]+)\b")
STACK_ROLE = {"active": "Active", "master": "Active", "standby": "Standby", "backup": "Standby", "member": "Member", "slave": "Member"}


def _is_command(rest: str) -> bool:
    """Text after a prompt: a typed command, or the first line of output that the device glued to the prompt?"""
    s = rest.strip()
    if not s or re.match(r"^\s{3,}", rest):
        return False
    tok = s.split()[0].lower()
    return len(tok) >= 2 and any(v.startswith(tok) for v in VERBS)


def _command(rest: str) -> str:
    s = re.split(r"\s{3,}", rest.strip())[0]
    s = re.sub(r"^do\s+", "", s, flags=re.I)
    return " ".join(s.split())


def _is_config_cmd(cmd: str | None) -> bool:
    t = (cmd or "").lower().split()
    return (len(t) >= 2 and len(t[0]) >= 2 and ("show".startswith(t[0]) or t[0] == "more") and len(t[1]) >= 2
            and ("running-config".startswith(t[1]) or "startup-config".startswith(t[1])))


def split_log(text: str):
    """Cut a CLI capture into [{'cmd': typed command or None, 'lines': [...]}] at every prompt / section header."""
    chunks, cur = [], {"cmd": None, "lines": [], "bare": False}

    def start(cmd, bare=False):
        nonlocal cur
        chunks.append(cur)
        cur = {"cmd": cmd, "lines": [], "bare": bare}

    for line in text.split("\n"):
        h = HEADER.match(line)
        if h:
            start(_command(h.group(1)))
            continue
        p = PROMPT.match(line)
        if p and not re.match(r"^\d+(?:\.\d+)*$", p.group("host")):
            rest = p.group("rest")
            while rest.strip() and (q := PROMPT.match(rest.strip())):       # "SW#SW#" - repeated prompts
                rest = q.group("rest")
            if p.group("mode") or _is_command(rest):
                start(_command(rest))
            else:
                # a command echoed on its own line before the prompt ("sh runn" / "SW#config-file-header ...")
                inherit = cur["cmd"] if cur["bare"] and not any(l.strip() for l in cur["lines"]) else None
                start(inherit)
                if rest.strip():
                    cur["lines"].append(rest)
            continue
        if BARE.match(line) and len(line.split()) <= 6:
            start(_command(line), bare=True)
            continue
        cur["lines"].append(line)
    chunks.append(cur)
    return [c for c in chunks if c["cmd"] or any(l.strip() for l in c["lines"])]


def classify(chunk) -> str:
    text = "\n".join(chunk["lines"])
    if _is_config_cmd(chunk["cmd"]) or re.search(r"(?m)^config-file-header\s*$", text):
        return "config"
    for kind, rx in SIGNATURES:
        if rx.search(text):
            return kind
    return "other"


def expand_port_list(spec: str):
    """'gi1/0/1-2,gi1/0/10-12,te1/0/1-2' / 'Po1-2' -> ['gi1/0/1', 'gi1/0/2', ...]."""
    out = []
    for item in re.split(r"[,\s]+", spec or ""):
        m = re.fullmatch(r"([A-Za-z]+(?:\d+/)*)(\d+)(?:-(\d+))?", item.strip())
        if not m:
            continue
        a, b = int(m.group(2)), int(m.group(3) or m.group(2))
        out += [f"{m.group(1)}{n}" for n in range(a, min(b, a + 512) + 1)]
    return out


def _expand_vlans(spec: str):
    ids = []
    for part in re.split(r"[,;\s]+", spec or ""):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            ids += [v for v in range(a, min(b, 4094) + 1) if 1 <= v <= 4094]
    return ids


def _media(port_type: str) -> str:
    t = (port_type or "").lower()
    if re.search(r"combo-?f|fiber|fibre|sfp|-fx\b|\bfx\b", t):
        return "fiber"
    if re.search(r"combo-?c|copper|-tx\b|\btx\b|base-?t\b", t):
        return "utp"
    return media_from_type(port_type)


def span_table(lines, header_rx):
    """Column table whose dashes line gives the column widths and whose long values wrap onto continuation lines
    (Quantum 'show lldp neighbors' / 'show vlan'). Yields one list of cell strings per logical row."""
    i = 0
    while i < len(lines):
        if not header_rx.search(lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not DASHES.match(lines[j]) and j - i < 4:
            j += 1
        if j >= len(lines) or not DASHES.match(lines[j]):
            i += 1
            continue
        runs = [(m.start(), len(m.group())) for m in re.finditer(r"-+", lines[j])]
        starts = [a for a, _ in runs]
        rows, last, k = [], [], j + 1
        while k < len(lines) and lines[k].strip() and not DASHES.match(lines[k]):
            line = lines[k]
            cells = [line[s:(starts[n + 1] if n + 1 < len(starts) else len(line))].strip() for n, s in enumerate(starts)]
            if cells[0] or not rows:
                rows.append(cells)
                last = [len(c) for c in cells]
            else:       # continuation line: a piece that filled its column was cut mid-word, a shorter one at a space
                rows[-1] = [a if not b else (a + ("" if last[n] >= runs[n][1] else " ") + b if a else b)
                            for n, (a, b) in enumerate(zip(rows[-1], cells))]
                last = [len(b) if b else last[n] for n, b in enumerate(cells)]
            k += 1
        yield from rows
        i = k


class QuantumParser(BaseParser):
    vendor = "Quantum Networks"
    os_family = "QN-SW"
    scan_skip = ("show",)              # every section is parsed explicitly below; the generic scan would only add noise
    steps = ("parse_config", "parse_inventory", "parse_stack", "parse_system_info", "parse_system", "parse_version", "parse_vlan_table",
             "parse_status", "parse_port_config", "parse_descriptions", "parse_qq_interfaces", "parse_ip_interface",
             "parse_route", "parse_stp", "parse_lldp", "parse_prompt_name")

    def __init__(self, text: str, filename: str, sid: str):
        clean = clean_terminal_log(text)
        super().__init__(clean, filename, sid)
        chunks = split_log(clean)
        configs, shows = [], {}
        for c in chunks:
            kind = classify(c)
            body = "\n".join(c["lines"])
            if kind == "config":
                m = re.search(r"(?m)^config-file-header\s*$", body)
                configs.append(body[m.start():] if m else body)
                continue
            if kind != "other":
                shows[f"show {kind}"] = shows.get(f"show {kind}", "") + "\n" + body
        self.cfg = max(configs, key=len) if configs else ""
        self.shows = shows
        self.prompts = Counter(p.group("host") for l in clean.split("\n") if (p := PROMPT.match(l)) and not p.group("mode")
                               and not re.match(r"^\d+(?:\.\d+)*$", p.group("host")))
        self.qq = bool(re.search(r"^\s*Device:\s*QQ-|QN_SW_QQ|^Interface Ethernet\d", clean, re.M))
        self.sw.os_family = "QQ-SW" if self.qq else self.os_family
        self.sysinfo: dict = {}
        self.oob = None
        self.stack_read = False          # True once 'show stack' gave the real unit roles

    def lines_of(self, kind: str):
        return self.shows.get(f"show {kind}", "").split("\n")

    # ----------------------------------------------------------- running-config
    def parse_config(self):
        cfg, sw = self.cfg, self.sw
        if not cfg.strip():
            self.warn("No running-config in the backup: port modes, VLAN names and the gateway come from show output only.")
            return
        if (m := re.search(r'^hostname\s+"?([^"\s]+)"?', cfg, re.M)):
            sw.hostname = m.group(1)
        if (m := re.search(rf"^ip default-gateway\s+({IPV4})", cfg, re.M)) or \
           (m := re.search(rf"^ip route\s+0\.0\.0\.0(?:/0|\s+0\.0\.0\.0)\s+({IPV4})", cfg, re.M)):
            sw.gateway = m.group(1)
        if (m := re.search(r"^spanning-tree mode\s+(\S+)", cfg, re.M | re.I)):
            sw.stp_mode = m.group(1).lower()
        for m in re.finditer(r"^vlan\s+([\d,;\-]+)\s*$", cfg, re.M):
            for vid in _expand_vlans(m.group(1)):
                self.vlan_names.setdefault(vid, "")
        for m, body in iter_blocks(cfg, r"^vlan\s+(\d+)\s*$"):                              # QQ-SW: vlan N / name X
            if (n := re.search(r'^\s*name\s+"?([^"\n]+?)"?\s*$', body, re.M)):
                self.vlan_names[int(m.group(1))] = n.group(1)
        for vid in re.findall(r"(?i)\bsource(?:-interface)?\s+vlan\s*(\d+)", cfg):
            self.mgmt_hints[vid] += 1

        for m, body in iter_blocks(cfg, r"(?i)^interface\s+vlan\s*(\d+)\s*$"):
            vid = m.group(1)
            if (n := re.search(r'^\s*name\s+"?([^"\n]+?)"?\s*$', body, re.M)):
                self.vlan_names[int(vid)] = n.group(1)
            else:
                self.vlan_names.setdefault(int(vid), "")
            if re.search(r"^\s*management-vlan\s*$", body, re.M):
                self.mgmt_hints[vid] += 5
            ip = re.search(rf"^\s*ip address\s+({IPV4})\s*(?:/|\s)\s*({IPV4}|\d{{1,2}})\b", body, re.M)
            if ip:
                sw.svis[vid] = {"ip": ip.group(1), "mask": to_netmask(ip.group(2)), "desc": self.vlan_names.get(int(vid), "")}
            elif re.search(r"^\s*ip address dhcp\s*$", body, re.M):
                self.sw.info.append(f"VLAN {vid} gets its IP address by DHCP (taken from 'show ip interface' if present).")
        for m, body in iter_blocks(cfg, r"(?i)^interface\s+oob\s*$"):
            if (ip := re.search(rf"^\s*ip address\s+({IPV4})\s+({IPV4})", body, re.M)):
                self.oob = (ip.group(1), ip.group(2))
        for m, body in iter_blocks(cfg, r"(?i)^interface\s+([A-Za-z][A-Za-z\-]*\s*\d+(?:/\d+)*)\s*$"):
            name = m.group(1)
            if re.match(r"(?i)(vlan|oob|loopback|tunnel|null)", name):
                continue
            self._interface(name.replace(" ", ""), body)
        self._dhcp_pools(cfg)

    def _interface(self, name: str, body: str):
        i = self.iface(name)
        i.config = self.block_text(f"interface {name}", body)
        desc = re.search(r'^\s*description\s+"?(.+?)"?\s*$', body, re.M)
        i.description = desc.group(1) if desc else ""
        i.admin_down = bool(re.search(r"^\s*shutdown\s*$", body, re.M))
        mode = re.search(r"^\s*switchport mode\s+(\S+)", body, re.M)
        acc = re.search(r"^\s*switchport access vlan\s+(\d+)", body, re.M)
        nat = re.search(r"^\s*switchport (?:trunk native vlan|general pvid)\s+(\d+)", body, re.M)
        i.mode = mode.group(1).lower() if mode else ("routed" if re.search(r"^\s*no switchport\s*$", body, re.M) else "access")
        if i.mode in ("trunk", "general"):
            i.native_vlan = nat.group(1) if nat else "1"
            i.vlan = f"trunk (native {nat.group(1)})" if nat else "trunk"
            i.mode = "trunk"
        elif i.mode != "routed":
            i.vlan = acc.group(1) if acc else "1"
        if (g := re.search(r"^\s*channel-group\s+(\d+)(?:\s+mode\s+(\S+))?", body, re.M)):        # QN-SW: on | auto (LACP)
            md = (g.group(2) or "on").lower()
            self.lag(f"Port-channel{g.group(1)}", member=i.name, protocol="LACP" if md == "auto" else "static")
        if (g := re.search(r"^\s*port-group\s+(\d+)(?:\s+mode\s+(\S+))?", body, re.M)):           # QQ-SW: active | passive | on
            self.lag(f"Port-channel{g.group(1)}", member=i.name, mode=g.group(2) or "on")
        if i.name.lower().startswith("port-channel"):
            self.lag(i.name, description=i.description)

    def _dhcp_pools(self, cfg: str):
        """QN-SW pools are flat ('ip dhcp pool network X' ... 'exit'); QQ-SW pools are indented blocks."""
        for m in re.finditer(rf"(?m)^ip dhcp pool network\s+\S+\s*\n((?:(?!exit\s*$|ip dhcp pool).*\n)*)", cfg):
            a = re.search(rf"^\s*address\s+(?:low\s+)?({IPV4})(?:\s+high\s+{IPV4})?\s+({IPV4}|/\d+)", m.group(1), re.M)
            if a:
                mask = to_netmask(a.group(2))
                self.sw.dhcp_pools.append((network_of(a.group(1), mask).split("/")[0] or a.group(1), mask))
        for m, body in iter_blocks(cfg, r"^ip dhcp pool\s+\S+\s*$"):
            if (n := re.search(rf"^\s*network(?:-address)?\s+({IPV4})\s*(?:/|\s)\s*({IPV4}|\d{{1,2}})", body, re.M)):
                self.sw.dhcp_pools.append((n.group(1), to_netmask(n.group(2))))

    # ------------------------------------------------------- identity / hardware
    def parse_inventory(self):
        txt = self.shows.get("show inventory", "")
        if not txt.strip():
            return
        seen = set()
        for m in re.finditer(r'NAME:\s*"(?P<name>[^"]*)"\s*,?\s*DESCR:\s*"(?P<descr>[^"]*)"\s*\n\s*PID:\s*(?P<pid>.*?)\s*,?\s+'
                             r'VID:[ \t]*(?P<vid>.*?)[ \t]*,?[ \t]+SN:[ \t]*(?P<sn>\S*)', txt):
            name, descr, pid, vid, sn = (m.group(k).strip() for k in ("name", "descr", "pid", "vid", "sn"))
            if (name, sn) in seen:
                continue
            seen.add((name, sn))
            if re.fullmatch(r"\d+", name):              # chassis / stack unit
                mem = self.member(int(name))
                mem.serial = mem.serial or sn
                mem.model = mem.model or pid
                continue
            make = "Quantum Networks" if re.search(r"quantum", vid, re.I) else (vid if vid and vid != "0" else "")
            port = canon_port(name)
            self.sw.components.append(Component(kind=classify_part(name, descr, pid), model=pid or "Unknown", serial=sn, port=port,
                                                make=make, descr=descr))
            if re.search(r"SFP|BASE-?[LSEZ]|BASE-?BX|optic", f"{descr} {pid}", re.I) and not re.search(r"BASE-?T\b", descr, re.I):
                self.iface(port).media = "fiber"
        units = [k for k in self.members]
        self.sw.info.append(f"show inventory: {len(units)} unit(s), {len(self.sw.components)} other part(s)")

    def parse_stack(self):
        """'show stack' (QN-SW / Sx350 'Unit Id  MAC Address  Role' table): every unit's MAC and its real role."""
        n = 0
        for m in STACK_ROW.finditer(self.shows.get("show stack", "")):
            role = STACK_ROLE.get(m.group(3).lower())
            if role is None:
                continue
            mem = self.member(int(m.group(1)))
            mem.role, mem.mac = role, mem.mac or norm_mac(m.group(2))
            n += 1
        if n:
            self.stack_read = True
            self.sw.info.append(f"show stack: {n} unit(s), roles read from the backup.")

    def _unit_for_serial(self, serial: str) -> int:
        for idx, mem in self.members.items():
            if serial and mem.serial == serial:
                return idx
        return min(self.members) if self.members else 1

    def parse_system_info(self):
        """Quantum's own 'more system-info': marketing model (QN-SW-325-24FP), serial, base MAC, firmware."""
        txt = self.shows.get("show system-info", "")
        g = lambda k: (re.search(rf"^\s*{k}\s*:\s*(.+?)\s*$", txt, re.M | re.I) or [None, ""])[1]
        info = {"model": g("Model Number"), "serial": g("Serial Number"), "mac": g("MAC Address"), "firmware": g("Switch Firmware Version")}
        if not any(info.values()):
            return
        self.sysinfo = info
        mem = self.member(self._unit_for_serial(info["serial"]))
        hw_pid = mem.model
        mem.model = info["model"] or mem.model
        mem.serial = mem.serial or info["serial"]
        mem.mac = mem.mac or mac_or_blank(info["mac"])
        if info["firmware"]:
            self.sw.firmware = info["firmware"]
        # stack units of the same hardware get the same marketing model
        for other in self.members.values():
            if other is not mem and info["model"] and other.model == hw_pid and hw_pid:
                other.model = info["model"]
        if len(self.members) > 1 and not self.stack_read:     # no 'show stack': 'more system-info' describes the master
            mem.role = "Active"
            if len(self.members) == 2:       # a 2-unit stack is always master + backup
                next(m for m in self.members.values() if m is not mem).role = "Standby"
            self.sw.info.append("Stack roles are inferred, not read from the backup: master = the unit 'more system-info' "
                                "describes" + (", the other unit = backup." if len(self.members) == 2 else "."))

    def parse_system(self):
        txt = self.shows.get("show system", "")
        if not txt.strip():
            return
        g = lambda k: (re.search(rf"^\s*{k}:\s*(.*?)\s*$", txt, re.M) or [None, ""])[1]
        if not self.sw.hostname and g("System Name"):
            self.sw.hostname = g("System Name")
        mac = mac_or_blank(g("System MAC Address"))
        lines = txt.split("\n")
        for i, l in enumerate(lines):                       # "Unit  Type" table -> one row per stack unit
            if re.match(r"^\s*Unit\s+Type\s*$", l):
                for row in lines[i + 2:]:
                    r = re.match(r"^\s*(\d+)\s+(\S.*?)\s*$", row)
                    if not r:
                        break
                    mem = self.member(int(r.group(1)))
                    mem.model = mem.model or r.group(2)
                break
        if mac:
            mem = self.member(self._unit_for_serial(self.sysinfo.get("serial", "")))
            mem.mac = mem.mac or mac
        if not self.members:
            self.member(1)
        if (d := g("System Description")):
            self.sw.info.append(f"System description: {d}")

    def parse_version(self):
        txt, sw = self.shows.get("show version", ""), self.sw
        if not txt.strip():
            if not sw.firmware:
                self.warn("No 'show version' / 'more system-info' output: firmware unknown.")
            return
        if (m := re.search(r"Active-image:.*?\n\s*Version:\s*(\S+)", txt)):                    # QN-SW
            sw.firmware = m.group(1)
        if (m := re.search(r"^\s*SoftWare Version\s+(\S+)", txt, re.M)):                         # QQ-SW
            sw.firmware = m.group(1)
            mem = self.member(1)
            if (d := re.search(r"^\s*Device:\s*([^,\s]+)", txt, re.M)):
                mem.model = mem.model or d.group(1)
            if (s := re.search(r"^\s*Serial Num(?:ber)?\s*:\s*(\S+)", txt, re.M)):
                mem.serial = mem.serial or s.group(1)
            if (c := re.search(rf"^\s*CPU MAC\s+({MAC_RX}|[0-9A-Fa-f]{{2}}(?:-[0-9A-Fa-f]{{2}}){{5}})", txt, re.M)):
                mem.mac = mem.mac or norm_mac(c.group(1))

    # ----------------------------------------------------------------- VLANs
    def parse_vlan_table(self):
        lines = self.lines_of("vlan")
        tagged: dict[str, set] = {}
        untagged: dict[str, set] = {}
        for cells in span_table(lines, re.compile(r"^\s*Vlan\s+Name\s+Tagged Ports")):          # QN-SW
            if len(cells) < 4 or not cells[0].isdigit():
                continue
            vid = int(cells[0])
            name = cells[1]
            if name and not self.vlan_names.get(vid) and name != str(vid):
                self.vlan_names[vid] = name
            self.vlan_names.setdefault(vid, "")
            for p in expand_port_list(cells[2]):
                tagged.setdefault(canon_port(p), set()).add(vid)
            for p in expand_port_list(cells[3]):
                untagged.setdefault(canon_port(p), set()).add(vid)
        cur = None
        for l in lines:                                                                           # QQ-SW
            m = re.match(r"^(\d+)\s+(\S+)\s+(?:Static|Dynamic|Default)\s+\S+\s*(.*)$", l)
            if m:
                cur = int(m.group(1))
                if m.group(2) != "default" and not re.fullmatch(r"VLAN\d{4}", m.group(2)) and not self.vlan_names.get(cur):
                    self.vlan_names[cur] = m.group(2)
                self.vlan_names.setdefault(cur, "")
                ports = m.group(3)
            elif cur is not None and re.match(r"^\s{20,}\S", l):
                ports = l
            else:
                cur = None
                continue
            for p, t in re.findall(r"((?:Ethernet|Port-Channel)\d+(?:/\d+)*)(\(T\))?", ports):
                (tagged if t else untagged).setdefault(canon_port(p), set()).add(cur)
        if not self.cfg.strip() and (tagged or untagged):
            for port in set(tagged) | set(untagged):
                i = self.iface(port)
                if tagged.get(port):
                    nat = sorted(untagged.get(port, set()))
                    i.mode, i.native_vlan = "trunk", str(nat[0]) if nat else ""
                    i.vlan = f"trunk (native {nat[0]})" if nat and nat[0] != 1 else "trunk"
                else:
                    i.mode, i.vlan = "access", str(sorted(untagged[port])[0])
        for vid, name in list(self.vlan_names.items()):
            if not name:
                self.vlan_names[vid] = "default" if vid == 1 else f"VLAN{vid:04d}"

    # --------------------------------------------------------------- ports
    def parse_status(self):
        found = False
        for l in self.lines_of("interfaces status"):
            if (m := STATUS_ROW.match(l)):
                found = True
                i = self.iface(m.group("port"))
                state = m.group("state").strip()
                i.status = "up" if state.lower().startswith("up") else "down"
                i.state_text = state.lower()
                i.duplex = "" if m.group("duplex") == "--" else m.group("duplex")
                i.speed = "" if m.group("speed") == "--" else m.group("speed")
                i.port_type = m.group("type")
                i.media = _media(i.port_type) or i.media
            elif (m := PO_ROW.match(l)) and canon_port(m.group("port")) in self.lags:
                i = self.iface(m.group("port"))
                st = m.group("state").lower()
                i.status, i.state_text = ("up" if st.startswith("up") else "down"), st
        if not found and not self.qq:
            self.warn("No 'show interfaces status' output: port up/down colours need it (config-only shows shutdown ports).")

    def parse_port_config(self):
        """'show interfaces configuration': the Admin State column tells administratively shut ports."""
        for l in self.lines_of("interfaces configuration"):
            if (m := STATUS_ROW.match(l)):
                i = self.iface(m.group("port"))
                if m.group("state").strip().lower().startswith("down"):
                    i.admin_down = True
                    if i.status != "up":
                        i.state_text = i.state_text or "disabled"

    def parse_descriptions(self):
        on = False
        for l in self.lines_of("interfaces description"):
            if re.match(r"^\s*(?:Port|Ch|Oob)\s+Description\s*$", l):
                on = True
                continue
            m = re.match(rf"^\s*({PORT_COL})\s+(\S.*?)\s*$", l) if on else None
            if m and not re.fullmatch(r"-+", m.group(2)):
                i = self.iface(m.group(1)) if not m.group(1).startswith("Po") or canon_port(m.group(1)) in self.lags else None
                if i is not None and not i.description:
                    i.description = m.group(2)

    def parse_qq_interfaces(self):
        """QQ-SW 'show interface': one 'EthernetX is up, line protocol is up' block per port."""
        txt = self.shows.get("show interfaces", "")
        for blk in re.split(r"(?m)^\s*Interface brief:\s*$", txt):
            h = re.search(r"^\s*((?:Ethernet|Port-Channel)\d+(?:/\d+)*) is (up|down|administratively down), line protocol is (\w+)", blk, re.M)
            if not h:
                continue
            i = self.iface(h.group(1))
            i.status = "up" if h.group(2) == "up" and h.group(3) == "up" else "down"
            i.admin_down = i.admin_down or h.group(2).startswith("admin")
            i.state_text = h.group(2)
            if (a := re.search(r"alias name is (.*?), index is", blk)) and not i.description and a.group(1) != "(null)":
                i.description = a.group(1)
            if (hw := re.search(r"Hardware is ([\w\-+]+)", blk)):
                i.port_type = hw.group(1)
                i.media = _media(i.port_type) or i.media
            if (d := re.search(r"Negotiation (full|half)-duplex", blk, re.I)) or (d := re.search(r"\b(Full|Half)-duplex", blk)):
                i.duplex = d.group(1).capitalize()
            if (s := re.search(r"Negotiation (\d+[GM]) bits", blk)) or (s := re.search(r"\b(\d+[GM])\b(?: bits)?(?:,|\s)", blk)):
                i.speed = s.group(1)
            if (mac := re.search(r"Hardware is [\w\-+]+, address is (\S+)", blk)) and not self.member(1).mac:
                self.member(1).mac = mac_or_blank(mac.group(1))

    # --------------------------------------------------------------- layer 3
    def parse_ip_interface(self):
        """'show ip interface': fills in addresses learned by DHCP or when the running-config is missing."""
        for l in self.lines_of("ip interface"):
            m = re.match(rf"^\s*({IPV4})/(\d+)\s+(vlan\s*(\d+)|oob|\S+)\s+(\S+)", l, re.I)
            if not m:
                continue
            ip, mask = m.group(1), to_netmask(m.group(2))
            if ip == "0.0.0.0":                      # DHCP client that got no lease ("Not received")
                continue
            if m.group(4) and m.group(4) not in self.sw.svis:
                self.sw.svis[m.group(4)] = {"ip": ip, "mask": mask, "desc": self.vlan_names.get(int(m.group(4)), "")}
            elif m.group(3).lower() == "oob" and not self.oob:
                self.oob = (ip, mask)
        if not self.sw.svis and self.oob and self.oob[0] != "192.168.254.254":
            self.sw.svis["if:oob"] = {"ip": self.oob[0], "mask": self.oob[1], "desc": "out-of-band management port"}

    def parse_route(self):
        txt = self.shows.get("show ip route", "")
        if self.sw.gateway or not txt:
            return
        m = (re.search(rf"^\s*S\*?\s+0\.0\.0\.0/0\s+\[\S+\]\s+via\s+({IPV4})", txt, re.M)
             or re.search(rf"Gateway of last resort is ({IPV4})", txt))
        if m:
            self.sw.gateway = m.group(1)

    # ------------------------------------------------------------- spanning tree
    def parse_stp(self):
        txt, sw = self.shows.get("show spanning-tree", ""), self.sw
        if (m := re.search(r"Spanning tree enabled mode:?\s*(\w+)", txt)):
            sw.stp_mode = m.group(1).lower()
        elif (m := re.search(r"--\s*(STP|RSTP|MSTP)\s+Bridge Config Info", txt)):
            sw.stp_mode = m.group(1).lower()
        sw.stp_version = {"stp": "STP (802.1D)", "rstp": "RSTP (802.1w)", "mstp": "MSTP (802.1s)", "mst": "MSTP (802.1s)"}.get(
            sw.stp_mode, sw.stp_mode.upper() if sw.stp_mode else "")
        blocked = set()
        # QN-SW: Root ID block + "Name State Prio.Nbr Cost Sts Role PortFast Type" table
        inst = "CST"
        lines = txt.split("\n")
        for n, l in enumerate(lines):
            if (h := re.match(r"^\s*#*\s*MST\s*(\d+)\b", l)):
                inst = f"MST{int(h.group(1))}"
            if (r := re.match(r"^\s*Root ID\s+Priority:?\s*(\d+)", l)):
                rec = {"priority": r.group(1), "mac": "", "is_root": False, "cost": "", "port": ""}
                for nxt in lines[n + 1:n + 8]:
                    if re.match(r"^\s*Bridge ID", nxt):
                        break
                    if (a := re.match(rf"^\s+Address:?\s*({MAC_RX})", nxt)):
                        rec["mac"] = norm_mac(a.group(1))
                    if (c := re.match(r"^\s+Cost:?\s*(\d+)", nxt)):
                        rec["cost"] = c.group(1)
                    if (p := re.match(r"^\s+Port:?\s*(\S+)", nxt)):
                        rec["port"] = canon_port(p.group(1))
                    if re.search(r"this switch is the root", nxt, re.I):
                        rec["is_root"] = True
                sw.stp_roots[inst] = rec
            if (p := re.match(rf"^\s*({PORT_COL})\s+(?:enabled|disabled)\s+\d+\.\d+\s+\d+\s+(\w+)\s+(\w+)\s", l)):
                sts, role = p.group(2).lower(), p.group(3).lower()
                if sts in ("blk", "dsc", "bkn") or role in ("altn", "back"):
                    blocked.add(canon_port(p.group(1)))
        # QQ-SW: "Self Bridge Id / Root Id" + "PortName ID PathCost ExtRPC State Role ..." table
        if (s := re.search(r"Self Bridge Id\s*:\s*(\d+)\.(\S+)", txt)):
            root = re.search(r"Root Id\s*:\s*(this switch|(\d+)\.(\S+))", txt)
            me = bool(root and root.group(1) == "this switch")
            sw.stp_roots["CST"] = {"priority": (s.group(1) if me else (root.group(2) if root else "")),
                                   "mac": norm_mac(s.group(2) if me else (root.group(3) if root else "")) if root else "",
                                   "is_root": me, "cost": (re.search(r"Ext\.RootPathCost\s*:\s*(\d+)", txt) or [None, ""])[1], "port": ""}
        for p in re.finditer(r"(?m)^\s*((?:Ethernet|Port-Channel)\d+(?:/\d+)*)\s+\d+\.\d+\s+\d+\s+\d+\s+(\w+)\s+(\w+)\s", txt):
            st, role = p.group(2).upper(), p.group(3).upper()
            if st in ("BLK", "DSC", "DISCARD", "BLOCK") or role in ("ALTR", "BKUP", "ALTN", "BACK"):
                blocked.add(canon_port(p.group(1)))
        sw.blocked_ports = sorted(set(sw.blocked_ports) | blocked)
        for b in blocked:
            sw.blocked_detail.setdefault(b, [])

    # ------------------------------------------------------------------ LLDP
    def parse_lldp(self):
        seen = set()
        for cells in span_table(self.lines_of("lldp neighbors"), re.compile(r"^\s*Port\s+Device ID\s+Port ID\s+System Name")):
            if len(cells) < 5 or not cells[0]:
                continue
            local, dev, rport, name, caps = cells[:5]
            n = Neighbor(local_port=canon_port(local), remote_name=name or dev, remote_port=canon_port(rport),
                         remote_mac=mac_or_blank(dev), capabilities=caps, source="lldp")
            key = (n.local_port.lower(), n.remote_name.lower(), n.remote_port.lower())
            if key not in seen:
                seen.add(key)
                self.sw.neighbors.append(n)
        # CDP (Cisco SG/CBS only; Quantum answers "Unrecognized command"): adds the platform, or a neighbour LLDP missed
        by_port = {n.local_port: n for n in self.sw.neighbors}
        for cells in span_table(self.lines_of("cdp neighbors"), re.compile(r"^\s*Device ID\s+Local\s+Adv")):
            if len(cells) < 7 or not cells[0] or not cells[1]:
                continue
            dev, local, _ver, _ttl, caps, platform, rport = cells[:7]
            local = canon_port(local)
            if local in by_port:
                by_port[local].platform = by_port[local].platform or platform
                continue
            n = Neighbor(local_port=local, remote_name=dev, remote_port=canon_port(rport), remote_mac=mac_or_blank(dev),
                         platform=platform, capabilities=caps, source="cdp")
            by_port[local] = n
            self.sw.neighbors.append(n)

    # ----------------------------------------------------------------- naming
    def parse_prompt_name(self):
        if self.sw.hostname or not self.prompts:
            return
        name = self.prompts.most_common(1)[0][0]
        self.sw.hostname = name.rstrip(".")
        self.warn(f"Hostname not in the running-config or 'show system': used the CLI prompt '{name}'"
                  + (" (truncated by the switch)." if name.endswith("...") else "."))


class CiscoSBParser(QuantumParser):
    """Cisco Business / Small Business switches (CBS250/350, SG350/550X): the same Sx350-style CLI as Quantum QN-SW,
    so they are read the same way; only the brand differs."""
    vendor = "Cisco"
    os_family = "Business / Small Business (CBS, Sx350)"
