"""Parser registry: vendor key -> parser class."""
from __future__ import annotations

from .cisco import CiscoParser
from .aruba_os import ArubaOSSwitchParser
from .aruba_cx import ArubaCXParser
from .alcatel import AlcatelParser
from .tplink import TPLinkParser
from .dlink import DLinkParser
from .comware import ComwareParser
from .quantum import QuantumParser, CiscoSBParser
from .generic import GenericParser

REGISTRY = {
    "cisco": CiscoParser,
    "aruba_os": ArubaOSSwitchParser,
    "aruba_cx": ArubaCXParser,
    "alcatel": AlcatelParser,
    "tplink": TPLinkParser,
    "dlink": DLinkParser,
    "comware": ComwareParser,
    "quantum": QuantumParser,
    "cisco_sb": CiscoSBParser,
    "generic": GenericParser,
}


def get_parser(key: str):
    return REGISTRY.get(key, GenericParser)
