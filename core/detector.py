"""Score-based vendor detection from the raw text of a backup."""
from __future__ import annotations
import re

from .utils import clean_terminal_log

SIGNATURES = {
    "cisco": [(r"^version \d+\.\d+", 2), (r"^interface (?:Gigabit|TenGigabit|FastEthernet|TwentyFiveGig|FortyGig|HundredGig)", 3),
              (r"^spanning-tree mode (?:rapid-pvst|pvst|mst)", 2), (r"Cisco IOS", 4), (r"switchport mode (?:access|trunk)", 2),
              (r"^service timestamps", 2), (r"^switch \d+ provision", 3), (r"Catalyst", 1), (r"^enable secret", 1)],
    "aruba_cx": [(r"ArubaOS-CX", 6), (r"^interface \d+/\d+/\d+", 3), (r"^interface lag \d+", 2),
                 (r"^\s+vlan access \d+", 2), (r"^vrf mgmt", 3), (r"^interface mgmt", 3)],
    "aruba_os": [(r"^;\s*J[A-Z]?\d{4}[A-Z]?\s+Configuration Editor", 6), (r"^trunk\s+\S+\s+trk\d+", 3), (r"ProCurve", 3),
                 (r"^module \d+ type", 3), (r"^\s+untagged\s", 2), (r"Running configuration:", 2),
                 (r"ArubaOS-Switch|HPE Aruba", 3), (r'^hostname "', 1)],
    "alcatel": [(r"OmniSwitch|Alcatel", 5), (r"^vlan \d+ (?:enable|disable)", 4), (r'^ip interface "', 4),
                (r'^system name\s+"', 3), (r"^! (?:VLAN|IP|Chassis|SYSTEM|LINKAGG|SPANNING TREE)", 3),
                (r"^ip static-route", 2), (r"^interfaces \d+/\d+", 2)],
    "tplink": [(r"TP-?LINK|JetStream|TL-SG|TL-SL", 4), (r"^interface gigabitEthernet \d+/\d+/\d+", 4),
               (r"^user name \S+ privilege", 2), (r"ip address-alloc", 3), (r"^interface ten-gigabitEthernet", 3)],
    "dlink": [(r"D-?Link|\bDGS-|\bDES-|\bDXS-", 5), (r"^config ipif", 5), (r"^create vlan", 3),
              (r"^config (?:vlan|ports|stp|lldp)", 3), (r"^enable stp", 1)],
    "comware": [(r"Comware|H3C|HPE\s?5\d{3}", 4), (r"^\s*sysname\s+\S+", 5),
                (r"^interface (?:Bridge-Aggregation|Vlan-interface|GigabitEthernet\d+/\d+/\d+)", 3),
                (r"^\s*port link-type", 3), (r"^ip route-static", 2), (r"^\s*irf member", 2)],
    # Quantum Networks (qntmnet.com): QN-SW (Sx350-style CLI) and QQ-SW firmware. Only the device's own identity counts high,
    # so a Cisco/Aruba backup that merely lists a Quantum neighbour is not taken for one.
    "quantum": [(r"^\s*Model Number\s*:\s*Q[NQ]-", 8), (r"SWITCH SYSTEM INFORMATION", 6), (r"QN_SW[-_]\S*\.qntm|QN_SW_QQ-", 8),
                (r"^\s*Device:\s*Q[NQ]-", 8), (r"^\s*System MAC Address:\s*58:61:63|^\s*CPU MAC\s+58-61-63", 5),
                (r"ntp\.qntmnet\.com", 4), (r"^config-file-header\s*$", 3), (r"^v\d+\.\d+\.\d+\.\d+ / RLINUX", 3),
                (r"^vlan database\s*$", 2), (r"^interface vlan \d+\s*$", 2), (r"^\s*management-vlan\s*$", 3)],
    # Cisco Business / Small Business (CBS, SG350/550): same Sx350-style CLI, told apart by its own identity lines
    "cisco_sb": [(r"^v\d+(?:\.\d+){3} / RCBS", 6), (r"^file SSD indicator", 3), (r"PID:\s*(?:CBS|SG|SF|SX|C1[23]00)\d{3}", 6),
                 (r"^\s*System Object ID:\s*1\.3\.6\.1\.4\.1\.9\.", 5), (r"image_cbs|Cisco (?:Business|Small Business)", 4),
                 (r"^config-file-header\s*$", 3), (r"^vlan database\s*$", 2), (r"^interface vlan \d+\s*$", 2)],
}

LABELS = {
    "cisco": "Cisco IOS / IOS-XE", "aruba_cx": "Aruba AOS-CX", "aruba_os": "HPE / Aruba ProCurve (ArubaOS-Switch)",
    "alcatel": "Alcatel-Lucent OmniSwitch", "tplink": "TP-Link JetStream", "dlink": "D-Link",
    "comware": "HPE / H3C Comware", "quantum": "Quantum Networks (QN-SW / QQ-SW)",
    "cisco_sb": "Cisco Business / Small Business (CBS, Sx350)", "generic": "Generic (unknown vendor)",
}


def detect_vendor(text: str) -> str:
    if "\x1b" in text or "\x08" in text:          # raw terminal log: score what was on screen, not the keystrokes
        text = clean_terminal_log(text)
    scores = {k: sum(w for rx, w in sigs if re.search(rx, text, re.M)) for k, sigs in SIGNATURES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "generic"
