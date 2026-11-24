"""Small helpers shared by parsers: MAC/IP/port normalisation and text sectioning."""
from __future__ import annotations
import re
import ipaddress

IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
MAC_ANY = re.compile(
    r"(?<![0-9A-Fa-f.:\-])((?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}"
    r"|(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}"
    r"|[0-9A-Fa-f]{6}-[0-9A-Fa-f]{6})(?![0-9A-Fa-f.:\-])"
)


# Non-capturing MAC pattern for embedding inside larger regexes (keeps group numbers stable)
MAC_RX = (r"(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}|(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}|[0-9A-Fa-f]{6}-[0-9A-Fa-f]{6}")


# ---------------------------------------------------------------- addresses
def norm_mac(s: str) -> str:
    h = re.sub(r"[^0-9A-Fa-f]", "", s or "")
    if len(h) != 12:
        return (s or "").strip()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).upper()


def mac_or_blank(s: str) -> str:
    """Normalised MAC if the whole string is a MAC address (any common notation), else ''."""
    h = re.sub(r"[ :.\-]", "", s or "")
    return norm_mac(s) if re.fullmatch(r"[0-9A-Fa-f]{12}", h) else ""


def to_netmask(v) -> str:
    v = str(v).strip().lstrip("/")
    if v.isdigit() and 0 <= int(v) <= 32:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{v}").netmask)
    return v


def split_ip_mask(text: str):
    """'10.1.1.2/24' or '10.1.1.2 255.255.255.0' -> ('10.1.1.2', '255.255.255.0')."""
    m = re.search(rf"({IPV4})\s*(?:/|\s)\s*({IPV4}|\d{{1,2}})\b", text or "")
    if not m:
        return None
    return m.group(1), to_netmask(m.group(2))


def network_of(ip: str, mask: str) -> str:
    try:
        n = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
        return f"{n.network_address}/{n.prefixlen}"
    except ValueError:
        return ""


def in_subnet(ip: str, ref_ip: str, mask: str) -> bool:
    try:
        return ipaddress.IPv4Address(ip) in ipaddress.IPv4Network(f"{ref_ip}/{mask}", strict=False)
    except ValueError:
        return False


# -------------------------------------------------------------------- ports
_PFX = {
    "gi": "GigabitEthernet", "gig": "GigabitEthernet", "gige": "GigabitEthernet", "gigabitethernet": "GigabitEthernet",
    "te": "TenGigabitEthernet", "ten": "TenGigabitEthernet", "tengig": "TenGigabitEthernet",
    "tengige": "TenGigabitEthernet", "tengigabitethernet": "TenGigabitEthernet",
    "fa": "FastEthernet", "fast": "FastEthernet", "fastethernet": "FastEthernet",
    "fo": "FortyGigabitEthernet", "fortygigabitethernet": "FortyGigabitEthernet",
    "hu": "HundredGigE", "hundredgige": "HundredGigE",
    "twe": "TwentyFiveGigE", "twentyfivegige": "TwentyFiveGigE", "tf": "TwentyFiveGigE",
    "po": "Port-channel", "port-channel": "Port-channel", "portchannel": "Port-channel",
    "eth": "Ethernet", "ethernet": "Ethernet",
    "ge": "GigabitEthernet", "xge": "Ten-GigabitEthernet", "ten-gigabitethernet": "Ten-GigabitEthernet",
    "bagg": "Bridge-Aggregation", "bridge-aggregation": "Bridge-Aggregation",
}
_SHORT = {
    "GigabitEthernet": "Gi", "TenGigabitEthernet": "Te", "FastEthernet": "Fa", "FortyGigabitEthernet": "Fo",
    "HundredGigE": "Hu", "TwentyFiveGigE": "Twe", "Port-channel": "Po", "Ethernet": "Eth",
    "Ten-GigabitEthernet": "XGE", "Bridge-Aggregation": "BAGG",
}


def canon_port(name: str) -> str:
    """Gi1/0/1 / Gig 1/0/1 / GigabitEthernet1/0/1 -> GigabitEthernet1/0/1 (others pass through)."""
    n = (name or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z\-]*)\s*([\d/.:]+)$", n)
    if m:
        full = _PFX.get(m.group(1).lower())
        if full:
            return full + m.group(2)
    return n


def short_port(name: str) -> str:
    n = canon_port(name)
    for full, short in _SHORT.items():
        if n.startswith(full):
            return short + n[len(full):]
    return n


def port_key(name: str) -> str:
    return canon_port(name).lower().replace(" ", "")


_OPTICAL = (r"(sfp|xfp|qsfp|gbic|sx|lx|lr|sr|er|zr|bx|fx|ex|zx|lrm|cwdm|dwdm|fib(?:er|re)|optic|aoc|dac|twinax"
            r"|da\d|cu\d)")


def media_from_type(t: str) -> str:
    """Classify a port/transceiver type string as 'utp' (copper RJ45) or 'fiber' (SFP / optical / DAC)."""
    t = (t or "").lower()
    if not t or "not present" in t or t in ("--", "-", "none"):
        return ""
    if re.search(r"base-?t\b|basetx|base-tx|rj45|copper|10/100|(?<![a-z])\d+t\b|gbe-t|gbt", t):
        return "utp"
    if re.search(_OPTICAL, t):
        return "fiber"
    return ""


def media_from_name(name: str) -> str:
    """Last-resort guess when the backup carries no media information."""
    n = canon_port(name)
    return "fiber" if re.match(r"(TenGig|TwentyFive|Forty|Hundred|Ten-Gig)", n) else "utp"


# ----------------------------------------------------- terminal (PuTTY) logs
_TERM_ESC = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z@])|\x1b[()][A-Za-z0-9]|\x1b[=>78]")


def _replay_line(line: str) -> str:
    """What one terminal line looked like on screen: applies CR, backspace, cursor left/right and erase-to-end-of-line."""
    buf, col, i = [], 0, 0
    while i < len(line):
        ch = line[i]
        if ch == "\x1b":
            m = _TERM_ESC.match(line, i)
            if m and m.group(2):
                arg, op = m.group(1) or "", m.group(2)
                n = int(arg) if arg.isdigit() else 1
                if op == "D":
                    col = max(0, col - n)
                elif op == "C":
                    col += n
                elif op == "G":
                    col = max(0, n - 1)
                elif op == "K":
                    if arg in ("", "0"):
                        del buf[col:]
                    elif arg == "2":
                        buf = []
            i = m.end() if m else i + 1
            continue
        if ch == "\r":
            col = 0
        elif ch == "\x08":
            col = max(0, col - 1)
        elif ch == "\t" or ch >= " ":
            if col > len(buf):
                buf += [" "] * (col - len(buf))
            if col == len(buf):
                buf.append(ch)
            else:
                buf[col] = ch
            col += 1
        i += 1
    return "".join(buf).rstrip()


def clean_terminal_log(text: str) -> str:
    """Turn a raw terminal capture (PuTTY / SecureCRT log with echoes, backspaces and ANSI codes) into the text the user saw."""
    out = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if "\r" in line or "\x08" in line or "\x1b" in line or "\x07" in line:
            line = _replay_line(line)
        out.append(line.rstrip())
    return "\n".join(out)


# ----------------------------------------------------------- text sectioning
_CONFIG_CMDS =("show running", "show startup", "show run", "show configuration", "show config",
                "display current-configuration", "display saved-configuration",
                "more system:running-config")
_HDR = re.compile(
    r"^(?:\S+?[#>]\s*|->\s*|[\-=*#!~\s]+)?((?:show|display)\s+[A-Za-z][\w\-./ |]*?)[\s\-=*#!~]*$", re.I)


def split_sections(text: str):
    """Split a backup into (config_text, {normalised 'show ...' command: output})."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pre, cfg_parts, shows = [], [], {}
    key, buf = None, []
    for line in text.split("\n"):
        m = _HDR.match(line)
        if m:
            if key is not None:
                _store(key, buf, cfg_parts, shows)
            key, buf = " ".join(m.group(1).lower().split()), []
        elif key is None:
            pre.append(line)
        else:
            buf.append(line)
    if key is not None:
        _store(key, buf, cfg_parts, shows)
    return "\n".join(pre + cfg_parts), shows


def _store(key, buf, cfg_parts, shows):
    body = "\n".join(buf)
    if key.startswith(_CONFIG_CMDS):
        cfg_parts.append(body)
    else:
        shows[key] = shows.get(key, "") + "\n" + body


def _cmd_match(key: str, want: str, exact: bool) -> bool:
    kt, wt = key.split(), want.split()
    if len(kt) < len(wt) or (exact and len(kt) != len(wt)):
        return False
    return all(w.startswith(k) for k, w in zip(kt, wt))


def get_section(shows: dict, want: str, exact: bool = False) -> str:
    return "\n".join(v for k, v in shows.items() if _cmd_match(k, want.lower(), exact))


def iter_blocks(text: str, header_regex: str):
    """Yield (match, body) for config blocks: a header line plus its indented lines."""
    lines, hdr, i = text.split("\n"), re.compile(header_regex), 0
    while i < len(lines):
        m = hdr.match(lines[i])
        if not m:
            i += 1
            continue
        j, body = i + 1, []
        while j < len(lines) and lines[j][:1] in (" ", "\t"):
            body.append(lines[j])
            j += 1
        yield m, "\n".join(body)
        i = j


def fixed_columns(text: str, header_starts: str):
    """Parse a column-aligned table using the header line's word positions."""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.strip().lower().startswith(header_starts.lower()):
            hdr, start = l, i
            break
    else:
        return []
    cols = [(m.start(), m.group().lower()) for m in re.finditer(r"\S+", hdr)]
    rows = []
    for l in lines[start + 1:]:
        if not l.strip() or re.match(r"^\s*[-=+ |]{5,}\s*$", l):
            continue
        row = {}
        for k, (pos, name) in enumerate(cols):
            end = cols[k + 1][0] if k + 1 < len(cols) else len(l)
            row[name] = l[pos:end].strip()
        rows.append(row)
    return rows


def blocked_from_text(text: str):
    """Vendor-neutral: lines that start with a port id and say Blocking/Discarding/BLK."""
    return [m.group(1) for m in re.finditer(
        r"^\s*([A-Za-z]*\d+(?:[/.:]\d+)*)\s+.*\b(?:BLK|BKN|Blocking|BLOCKING|Discarding|DISCARDING)\b", text, re.M)]


def expand_ports(spec: str):
    """'49-50' / '1/49-1/50' / '1,3,5-6' -> list of port ids."""
    out = []
    for part in re.split(r"[,\s]+", (spec or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(.*?)(\d+)-(?:\1)?(\d+)", part)
        if m:
            out += [f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out.append(part)
    return out


def stp_root_blocks(text: str):
    """Cisco/AOS-CX style 'show spanning-tree': {vlan_or_instance: {mac, priority, is_root, cost, port}}."""
    roots, vlan, cur, sect = {}, "", None, False
    for line in text.split("\n"):
        h = re.match(r"^\s*(VLAN|MST)(\d+)\s*$", line)
        if h:
            vlan = str(int(h.group(2))) if h.group(1) == "VLAN" else f"MST{int(h.group(2))}"
            sect = False
            continue
        r = re.match(r"^\s*Root ID\s+Priority\s*:?\s*(\d+)", line)
        if r:
            cur = {"priority": r.group(1), "mac": "", "is_root": False, "cost": "", "port": ""}
            roots[vlan or "0"] = cur
            sect = True
            continue
        if re.match(r"^\s*Bridge ID", line):
            sect = False
        if sect and cur is not None:
            a = re.match(rf"^\s+(?:MAC-)?Address\s*:?\s*({MAC_RX})", line)
            if a:
                cur["mac"] = norm_mac(a.group(1))
            if "this bridge is the root" in line.lower():
                cur["is_root"] = True
            c = re.match(r"^\s+Cost\s*:?\s*(\d+)", line)
            if c:
                cur["cost"] = c.group(1)
            pt = re.match(r"^\s+Port\s*:?\s*\d+\s*\((\S+?)\)", line)
            if pt:
                cur["port"] = pt.group(1)
    return roots


_SECRET_PATTERNS = [
    re.compile(r"(?i)(\b(?:password|secret)\s+(?:\d\s+)?)(\S+)"),
    re.compile(r"(?i)(\bkey-string\s+(?:\d\s+)?)(\S+)"),
    re.compile(r"(?i)(\bauthentication-key\s+(?:\d\s+)?)(\S+)"),
    re.compile(r"(?i)(\bmd5\s+(?:\d\s+)?)(?!key-)(\S+)"),
    re.compile(r"(?i)(\b(?:community|pre-shared-key|auth-md5|auth-sha|priv-aes|priv-des|simple|cipher)\s+(?:\d\s+)?)(\S+)"),
]


def mask_secrets(text: str) -> str:
    """Hide passwords, keys and community strings before configuration lines are shown in the browser."""
    for rx in _SECRET_PATTERNS:
        text = rx.sub(lambda m: m.group(1) + "********", text)
    return text
