"""Plain data containers shared by every parser and exporter."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class StackMember:
    index: int
    role: str = "Member"          # Active | Standby | Member | Standalone
    priority: str = ""
    model: str = ""
    serial: str = ""
    mac: str = ""
    state: str = ""
    version: str = ""


@dataclass
class Vlan:
    id: int
    name: str = ""


@dataclass
class Interface:
    name: str
    status: str = "unknown"       # up | down | unknown
    admin_down: bool = False
    description: str = ""
    vlan: str = ""
    mode: str = ""                # access | trunk | routed | ""
    speed: str = ""
    media: str = ""               # fiber | utp | ""   (fiber = SFP / optical / DAC)
    port_type: str = ""
    channel_group: str = ""
    native_vlan: str = ""
    duplex: str = ""
    state_text: str = ""           # raw status word from the switch, e.g. connected / notconnect / err-disabled
    config: str = ""               # this port's configuration lines from the backup (secrets masked); shown on hover


@dataclass
class Neighbor:
    local_port: str
    remote_name: str = ""
    remote_port: str = ""
    remote_ip: str = ""
    remote_mac: str = ""
    platform: str = ""
    capabilities: str = ""
    description: str = ""
    source: str = ""              # cdp | lldp | description


@dataclass
class Component:
    kind: str                     # SFP / Transceiver | DAC / AOC Cable | Stack Cable | Stack Module | Uplink / Network Module
    model: str = ""
    serial: str = ""
    port: str = ""
    make: str = ""
    descr: str = ""


@dataclass
class Switch:
    id: str
    filename: str
    vendor: str = "Unknown"
    os_family: str = ""
    detected_as: str = ""
    hostname: str = ""
    mgmt_ip: str = ""
    subnet: str = ""              # dotted mask of the management network
    prefix: str = ""              # e.g. 10.1.10.0/24
    gateway: str = ""
    mgmt_vlan: int | None = None
    firmware: str = ""
    stp_mode: str = ""
    stp_version: str = ""
    members: list = field(default_factory=list)
    vlans: list = field(default_factory=list)
    interfaces: list = field(default_factory=list)
    neighbors: list = field(default_factory=list)
    components: list = field(default_factory=list)
    blocked_ports: list = field(default_factory=list)
    blocked_detail: dict = field(default_factory=dict)
    svis: dict = field(default_factory=dict)          # vlan id (or "if:<name>") -> {ip, mask, desc}
    dhcp_bindings: dict = field(default_factory=dict)  # ip -> {mac, vlan, port}
    dhcp_pools: list = field(default_factory=list)     # [(network, mask)]
    port_channels: list = field(default_factory=list)   # [{name, protocol, flags, members:[{port, flag}], description}]
    stp_roots: dict = field(default_factory=dict)       # vlan -> {mac, priority, is_root, cost, port}
    l3: dict = field(default_factory=dict)              # SVIs, routes, protocols, ACLs, SNMP traps, DHCP (see l3.py)
    std: dict = field(default_factory=dict)             # aaa / ntp / snmp / dns / domain facts (see stdconfig.py)
    warnings: list = field(default_factory=list)
    info: list = field(default_factory=list)
    audit: list = field(default_factory=list)            # configuration findings found while parsing (see audit.py)

    @property
    def primary(self) -> StackMember:
        for m in self.members:
            if m.role == "Active":
                return m
        return self.members[0]

    @property
    def is_stack(self) -> bool:
        return len(self.members) > 1

    def to_dict(self) -> dict:
        d = asdict(self)
        p = self.primary
        d.update(model=p.model, serial=p.serial, mac=p.mac, is_stack=self.is_stack)
        d.pop("dhcp_bindings", None)
        d.pop("dhcp_pools", None)
        return d
