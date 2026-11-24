"""Firewall recognition from neighbour info (CDP/LLDP name, platform, description), port descriptions and MAC prefixes.

Add your own vendor by appending a row to RULES: (vendor, regex that identifies it, regex that captures the model).
"""
from __future__ import annotations
import re

from .utils import norm_mac

# (vendor, identifying regex, model regex or "")   - matched case-insensitively against name + platform + description
RULES = [
    ("Palo Alto Networks", r"palo\s*alto|pan-?os|\bPA-\d{3,4}\b|\bPA-VM", r"\b(PA-\d{3,4}[A-Z]?|PA-VM)\b"),
    ("Fortinet", r"fortigate|fortiwifi|\bFGT[-_ ]?\w*|\bFG-?\d{2,4}[A-Z]?\b|\bFWF-?\d", r"(FortiGate[-\w]*|FortiWiFi[-\w]*|FG-?\d{2,4}[A-Z]?\b|FGT[-_]?\w+)"),
    ("Sophos", r"sophos|\bXGS?[ -]?\d{2,4}\b|\bSFOS\b|cyberoam|\bSG[ -]?\d{3}\b", r"(XGS?[ -]?\d{2,4}\w*|SG[ -]?\d{3}\w*|SFOS)"),
    ("Cisco", r"\bASA[ -]?\d|ASA5\d{3}|firepower|\bFPR[ -]?\d|secure firewall|\bFTD\b", r"(ASA ?5\d{3}\S*|ASA[ -]?\d{4}\S*|FPR-?\d{4}\S*|Firepower[\w -]*\d{4})"),
    ("Check Point", r"check ?point|\bgaia\b|quantum (?:spark|force|maestro)|\bCP-?\d{4}\b", r"(Quantum[\w ]*\d{3,5}|Gaia)"),
    ("SonicWall", r"sonic ?wall|sonicos|\bNSa[ -]?\d{3,4}|\bNSsp|\bTZ[ -]?\d{2,3}\b", r"(NSa ?\d{3,4}|NSsp ?\d{4,5}|TZ ?\d{2,3}\w*)"),
    ("WatchGuard", r"watchguard|firebox|fireware", r"(Firebox[\w -]*)"),
    ("Juniper", r"\bSRX[ -]?\d{3,4}", r"(SRX[ -]?\d{3,4}\w*)"),
    ("Netgate (pfSense)", r"pfsense|netgate", ""),
    ("OPNsense", r"opnsense", ""),
    ("Barracuda", r"barracuda|cloudgen", ""),
    ("Huawei", r"\bUSG[ -]?\d{3,5}|huawei.*firewall", r"(USG[ -]?\d{3,5}\w*)"),
    ("Hillstone", r"hillstone", ""),
    ("Sangfor", r"sangfor", ""),
    ("Stormshield", r"stormshield", ""),
    ("Ubiquiti", r"\bUXG|\bUDM[- ]?(?:pro|se)?\b|dream machine", r"(UXG[-\w]*|UDM[-\w]*)"),
]
GENERIC = r"\bfirewalls?\b|\bFW[-_ ]?\w*\d|\bFW\b|\bfw[-_]\w+"
NOT_FIREWALL = re.compile(r"fortiswitch|fortiap|fortilink|\bFS-?\d{3}|\bFAP-?\d", re.I)

# Registered MAC prefixes (IEEE OUI). Partial lists: vendors own dozens of blocks, so this is only a secondary hint.
FORTINET_OUI = {"00:09:0F", "00:0C:E6", "04:01:A1", "04:D5:90", "08:5B:0E", "1C:D1:1A", "38:C0:EA", "48:3A:02", "5C:63:B0",
                "68:CC:AE", "70:4C:A5", "74:78:A6", "78:18:EC", "80:5A:70", "80:80:2C", "84:39:8F", "90:6C:AC", "94:F3:92",
                "94:FF:3C", "AC:71:2E", "B4:B2:E9", "D4:76:A0", "E8:1C:BA"}
PALO_ALTO_OUI = {"00:1B:17", "00:86:9C", "00:DA:27", "04:47:2A", "08:03:42", "08:30:6B", "08:66:1F", "1C:CF:82", "24:0B:0A",
                 "34:E5:EC", "3C:FA:30", "58:49:3B", "58:76:9C", "5C:58:E6", "60:15:2B", "64:7C:E8", "78:6D:94", "7C:89:C1",
                 "7C:C0:25", "7C:C7:90", "84:D4:12", "A4:27:A5", "A0:F2:61", "8C:36:7A", "E8:98:6D", "C8:29:C8"}


def _tidy(text: str) -> str:
    t = re.sub(r"^cisco\s+", "", (text or "").strip(), flags=re.I)
    return t[:40]


def detect_firewall(name="", platform="", description="", mac="", use_oui=False):
    """Return {'vendor', 'model', 'source'} when the neighbour looks like a firewall, else None."""
    blob = " ".join(x for x in (name, platform, description) if x)
    if not blob and not mac:
        return None
    for vendor, rx, model_rx in RULES:
        if re.search(rx, blob, re.I):
            if vendor == "Fortinet" and NOT_FIREWALL.search(blob):
                continue
            m = re.search(model_rx, blob, re.I) if model_rx else None
            return {"vendor": vendor, "model": m.group(1).strip() if m else _tidy(platform), "source": "CDP/LLDP or description text"}
    if use_oui and mac:
        p = norm_mac(mac)[:8]
        if p in PALO_ALTO_OUI:
            return {"vendor": "Palo Alto Networks", "model": "", "source": "MAC prefix only (verify)"}
        if p in FORTINET_OUI and not NOT_FIREWALL.search(blob):
            return {"vendor": "Fortinet", "model": "", "source": "MAC prefix only (verify; could be a FortiSwitch/FortiAP)"}
    if re.search(GENERIC, blob, re.I):
        return {"vendor": "Unknown", "model": _tidy(platform), "source": "name/description keyword"}
    return None
