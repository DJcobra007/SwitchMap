"""Glue: detect vendor -> parse each file -> analyse the whole set."""
from __future__ import annotations

from .detector import detect_vendor, LABELS
from .parsers import get_parser
from .analysis import analyze, Analysis
from .l3 import extract_l3
from .stdconfig import extract_std
from .audit import audit_config


def run(files, vendor: str = "auto", skipped=None) -> Analysis:
    switches = []
    for n, (name, text) in enumerate(files, 1):
        key = detect_vendor(text) if vendor in ("", "auto", None) else vendor
        parser = get_parser(key)(text, name, f"sw{n}")
        sw = parser.parse()
        sw.detected_as = LABELS.get(key, key)
        sw.std = extract_std(parser.cfg, key)
        sw.l3 = extract_l3(parser.cfg, key, parser.shows)
        sw.audit = audit_config(parser.cfg, key, sw)
        switches.append(sw)
    seen = {}
    for sw in switches:
        if sw.hostname.lower() in seen:
            sw.warnings.append(f"Same hostname as '{seen[sw.hostname.lower()]}': is this the same device uploaded twice?")
        seen.setdefault(sw.hostname.lower(), sw.filename)
    return analyze(switches, skipped)
