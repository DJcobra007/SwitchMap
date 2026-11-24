"""Vendor-neutral parts classification and inventory scanning.

`scan_inventory` reads *any* show/display output that looks like inventory (chassis, modules, transceivers, PSUs, fans ...)
either as "Key : value" blocks or as column tables with a Serial column, so parts are found for every OEM, not only Cisco.
"""
from __future__ import annotations
import re

from .utils import norm_mac, mac_or_blank

INVENTORY_CMD = re.compile(r"inventory|module|chassis|transceiver|manuinfo|hardware|\bfru\b|sfp|power|\bfan|stack|member|slot|unit|device|system|environment|optic", re.I)
KEY_SERIAL = re.compile(r"serial|^sn$|s/n", re.I)
MODEL_KEYS = ["part number", "part no", "part no.", "part #", "product number", "product no", "pid", "product id", "model", "model name",
              "model number", "device_name", "device name", "product name", "prod #", "type", "name"]
DESCR_KEYS = ["description", "descr", "device description", "module description"]
PORT_KEYS = ["port", "slot", "interface", "location", "member", "unit", "sfp", "module"]
HEAD_RX = re.compile(r"^\s*(Slot|Chassis|Member|Unit|Switch|Port|Interface|Module|NI|Transceiver|Power ?Supply|Fan)\s*[#]?\s*([\w/.:\-]+)\s*:?\s*$", re.I)
IFACE_HEAD = re.compile(r"^\s*(\S+?)\s+(?:transceiver|optical module|sfp)\b.*:\s*$", re.I)
MAX_SECTION_LINES = 800     # skip huge dumps (e.g. full tech-support): they are not inventory tables
MAX_PARTS = 400              # per switch; anything beyond is almost certainly a mis-parse
DASHES = re.compile(r"^\s*[-=+*# |]{4,}\s*$")


def classify_part(name: str, descr: str = "", pid: str = "") -> str:
    text = f"{name} {descr} {pid}"
    if re.search(r"STACK-T|CAB-STK|CAB-SS|stacking cable|stack\s?cable", text, re.I) or re.search(r"stack\s?port", name, re.I):
        return "Stack Cable"
    if re.search(r"STACK", pid, re.I) or re.search(r"stack (?:module|adapter)|stackwise", text, re.I):
        return "Stack Module"
    if re.search(r"power supply|\bPWR-|\bPSU\b|\bPS\d|\bpsu\b|power module", text, re.I):
        return "Power Supply"
    if re.search(r"\bfan\b|fan tray|\bFAN-", text, re.I):
        return "Fan / Fan Tray"
    if re.search(r"uplink module|network module|\bNM-|-NM-|\bNIM-|\bSM-|expansion module|\bmodule\b.*\bSFP\+?\b", text, re.I) and not re.search(r"transceiver", text, re.I):
        return "Uplink / Network Module"
    if re.search(r"-CU\d|CU\dM|AOC|direct attach|twinax|active optical|\bDAC\b|\bSFP\+?-?DA", text, re.I):
        return "DAC / AOC Cable"
    if re.search(r"^(?:SFP|GLC|QSFP|XFP|GBIC|FET|X2|XENPAK|CWDM|DWDM)|SFP|transceiver|optical|1000BASE|10GBASE|40GBASE|25GBASE|\bJ\d{4}[A-Z]\b", text, re.I):
        return "SFP / Transceiver"
    if re.search(r"supervisor|line ?card|module|\bslot\b|\bcard\b|\bWS-X", text, re.I):
        return "Module / Line Card"
    return "Other"


def _pick(d: dict, keys):
    for k in keys:
        for key, v in d.items():
            if key == k and v:
                return v
    return ""


def _kv_blocks(text: str):
    blocks, cur, head = [], {}, ""

    def flush():
        nonlocal cur
        if cur:
            blocks.append((head, cur))
        cur = {}
    for line in text.split("\n"):
        if not line.strip() or DASHES.match(line):
            flush()
            continue
        h = HEAD_RX.match(line)
        ih = IFACE_HEAD.match(line)
        if h or ih:
            flush()
            head = (f"{h.group(1)} {h.group(2)}" if h else ih.group(1)).rstrip(":")
            continue
        kv = re.match(r"^\s*([A-Za-z][\w /#.\-]{1,40}?)\s*(?:[:=]|\s-\s)\s*(.+?)\s*$", line)
        if kv:
            cur[kv.group(1).strip().lower()] = kv.group(2)
    flush()
    return blocks


def _tables(text: str):
    lines, out = text.split("\n"), []
    for i, l in enumerate(lines):
        toks = [(m.start(), m.group().strip()) for m in re.finditer(r"\S+(?: \S+)*", l) if m.group().strip() not in ("|", "+")]
        low = [t.lower() for _, t in toks]
        if len(toks) >= 3 and any(KEY_SERIAL.search(t) for t in low) and any(re.search(r"part|model|product|pid|type|descr|name", t) for t in low):
            j = i + 1
            while j < len(lines) and DASHES.match(lines[j]):
                j += 1
            while j < len(lines) and lines[j].strip() and not DASHES.match(lines[j]):
                row = {}
                for k, (pos, name) in enumerate(toks):
                    end = toks[k + 1][0] if k + 1 < len(toks) else len(lines[j])
                    row[name.lower()] = lines[j][pos:end].strip(" |+")
                out.append(row)
                j += 1
    return out


def _serial(d: dict) -> str:
    for k, v in d.items():
        if KEY_SERIAL.search(k) and re.fullmatch(r"[A-Za-z0-9]{6,}", (v or "").split()[0] if v else ""):
            return v.split()[0]
    return ""


def scan_inventory(shows: dict, known_serials: set, make: str = ""):
    """Return (components, {member_index: mac}) discovered in inventory-like sections."""
    from .models import Component
    comps, macs, seen = [], {}, set(known_serials)

    def add(kind, model, serial, port, descr):
        key = serial or f"{model}|{port}"
        if key in seen:
            return
        seen.add(key)
        comps.append(Component(kind=kind, model=model or "Unknown", serial=serial, port=port, make=make, descr=descr))

    for cmd, text in shows.items():
        if not INVENTORY_CMD.search(cmd) or text.count("\n") > MAX_SECTION_LINES or len(comps) >= MAX_PARTS:
            continue
        hint = " transceiver" if re.search(r"transceiver|sfp|optic", cmd) else ""
        for head, d in _kv_blocks(text):
            serial = _serial(d)
            mac = mac_or_blank(_pick(d, ["mac address", "mac_address", "base mac address", "mac"]))
            idx = re.search(r"(?:slot|chassis|member|unit|switch|ni)\s+(\d+)$", head, re.I)
            if mac and idx:
                macs.setdefault(int(idx.group(1)), mac)
            if not serial or serial in known_serials:
                continue
            model = _pick(d, MODEL_KEYS)
            if not model:
                continue
            descr = _pick(d, DESCR_KEYS)
            port = head or _pick(d, PORT_KEYS)
            add(classify_part(f"{head} {model}", descr + hint, model), model, serial, port, descr)
        for row in _tables(text):
            serial = _serial(row)
            if not serial or serial in known_serials:
                continue
            model = _pick(row, MODEL_KEYS + ["part number", "prod #", "product number", "part no."]) or _pick(row, [k for k in row if re.search(r"part|model|product|type", k)])
            if not model:
                continue
            port = _pick(row, PORT_KEYS) or next((v for k, v in row.items() if re.search(r"port|slot|member|name", k) and v), "")
            descr = _pick(row, DESCR_KEYS)
            mac = mac_or_blank(next((v for k, v in row.items() if "mac" in k), ""))
            if mac and re.fullmatch(r"\d+", port or ""):
                macs.setdefault(int(port), mac)
            add(classify_part(f"{port} {model}", descr + hint, model), model, serial, port, descr)
    return comps, macs
