"""Run:  python tests/test_pipeline.py   (no pytest needed)"""
import os, sys, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.loader import load_uploads
from core.pipeline import run
from core.excel_export import build_workbook
from core.diagram_export import render_svg

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")


def load(names):
    items = [(n, open(os.path.join(SAMPLES, n), "rb").read()) for n in names]
    return load_uploads(items)[0]


def test_cisco_ring():
    an = run(load(["CORE-STK.txt", "ACC-SW1.txt", "ACC-SW2.txt"]))
    sw = {s.hostname: s for s in an.switches}
    core = sw["CORE-STK"]
    assert core.vendor == "Cisco" and core.is_stack and len(core.members) == 2
    assert [m.serial for m in core.members] == ["JAE24020B2D", "JAE24020B7K"], core.members
    assert core.members[0].role == "Active" and core.members[1].role == "Standby"
    assert core.members[0].mac == "0C:D0:F8:B5:11:00"
    assert core.mgmt_ip == "10.10.10.2" and core.mgmt_vlan == 10 and core.gateway == "10.10.10.1", (core.mgmt_ip, core.mgmt_vlan)
    assert core.firmware == "17.06.04" and "RSTP" in core.stp_version
    assert {v.id: v.name for v in core.vlans}[10] == "MGMT"
    kinds = sorted(c.kind for c in core.components)
    assert kinds.count("Stack Cable") == 2 and "Uplink / Network Module" in kinds and "DAC / AOC Cable" in kinds, kinds
    names = sorted(c.kind for c in core.components)
    assert "Power Supply" in names and "Fan / Fan Tray" in names and "Other" in names, names
    assert len(core.components) == 8, [(c.kind, c.port) for c in core.components]
    up = [i.name for i in core.interfaces if i.status == "up"]
    assert "GigabitEthernet1/0/1" in up and "TenGigabitEthernet1/1/1" in up
    a1, a2 = sw["ACC-SW1"], sw["ACC-SW2"]
    assert a1.primary.model == "WS-C2960X-48FPD-L" and a1.primary.serial == "FCW2133L0AB" and a1.firmware == "15.2(7)E3"
    assert a2.blocked_ports == ["GigabitEthernet1/0/47"], a2.blocked_ports
    # topology: 3 links, ring, one blocked, SFP link is fibre
    sw_links = [l for l in an.links if not l["b"].startswith("fw:")]
    assert len(sw_links) == 3 and len(an.links) == 6, an.links
    blocked = [l for l in sw_links if l["blocked"]]
    assert len(blocked) == 1 and set([blocked[0]["a"], blocked[0]["b"]]) == {a1.id, a2.id}
    fiber = [l for l in sw_links if l["media"] == "fiber"]
    assert len(fiber) == 1 and {fiber[0]["a"], fiber[0]["b"]} == {core.id, a2.id}
    # access points
    aps = {a["hostname"]: a for a in an.aps}
    assert set(aps) == {"AP-LOBBY", "AP-515-Floor1", "AP-Warehouse"}, set(aps)
    assert aps["AP-LOBBY"]["ip_display"] == "DHCP" and aps["AP-LOBBY"]["make"] == "Cisco" and aps["AP-LOBBY"]["subnet"] == "255.255.255.0"
    assert aps["AP-515-Floor1"]["make"] == "Aruba" and aps["AP-515-Floor1"]["ip_display"] == "10.10.20.60"
    assert aps["AP-Warehouse"]["ip_display"] == "10.10.10.70" and aps["AP-Warehouse"]["gateway"] == "10.10.10.1"
    # firewalls: Palo Alto via LLDP, Sophos + Fortinet via port descriptions; the Linux host is ignored
    fws = {r["make"]: r for r in an.firewalls}
    assert set(fws) == {"Palo Alto Networks", "Sophos", "Fortinet"}, set(fws)
    pa = fws["Palo Alto Networks"]
    assert pa["model"] == "PA-3220" and pa["ip"] == "10.10.10.250" and pa["switch_hostname"] == "CORE-STK" and pa["switch_port"] == "Gi2/0/10", pa
    assert fws["Sophos"]["model"] == "XGS2300" and fws["Fortinet"]["model"] == "FortiGate-100F"
    assert not any("LINUX" in n["hostname"] for n in an.nodes)
    assert sum(1 for n in an.nodes if n["kind"] == "firewall") == 3
    ys = {n["id"]: n["y"] for n in an.nodes}
    fwn = [n for n in an.nodes if n["kind"] == "firewall"]
    assert all(ys[n["id"]] < ys[sw["ACC-SW1"].id] or n["connections"][0]["switch_hostname"] == "CORE-STK" for n in fwn)
    # port-channel: two physical links CORE<->ACC-SW1 are drawn as ONE bundled link labelled Po1 / Po2
    lag = [l for l in sw_links if l["lag"]]
    assert len(lag) == 1 and lag[0]["count"] == 2 and {lag[0]["a_port"], lag[0]["b_port"]} == {"Po1", "Po2"}, lag
    assert core.port_channels[0]["name"] == "Port-channel1" and len(core.port_channels[0]["members"]) == 2
    # spanning-tree root bridge
    nodes = {n["hostname"]: n for n in an.nodes if n["kind"] == "switch"}
    assert nodes["CORE-STK"]["is_root"] and nodes["ACC-SW1"]["stp_root"].startswith("CORE-STK"), nodes["ACC-SW1"]["stp_root"]
    # standard configuration comparison: ACC-SW2 lacks NTP, has a different DNS and no AAA accounting
    comp = {c["key"]: c for c in an.compliance["categories"]}
    assert comp["ntp"]["missing"] == 1 and comp["dns"]["differs"] == 1 and comp["aaa"]["differs"] == 1 and comp["domain"]["match"] == 3
    assert "10.10.10.20" in comp["ntp"]["baseline"] and "S3cretRO" not in str(an.compliance)          # secrets never exposed
    # misconfigurations
    titles = {(f["hostname"], f["title"]) for f in an.findings["items"]}
    for want in [("ACC-SW1", "Native VLAN mismatch on trunk"), ("ACC-SW1", "Telnet allowed on VTY lines"), ("ACC-SW1", "HTTP management server is enabled"),
                 ("ACC-SW2", "Well-known SNMP community string in use"), ("ACC-SW2", "NTP is not configured"), ("ACC-SW2", "DNS differs from the standard"),
                 ("ACC-SW2", "Ports assigned to VLAN 99, which does not exist on this switch")]:
        assert want in titles, (want, sorted(titles))
    assert an.findings["counts"]["High"] >= 3 and "S3cretRO" not in str(an.findings)          # no secrets in findings
    assert an.compliance["score"] < 100 and comp["snmp"]["differs"] == 1 and comp["aaa"]["items"]
    # per-interface configuration (shown on hover): the port's own lines, secrets masked
    gi1 = next(i for i in core.interfaces if i.name == "GigabitEthernet1/0/1")
    assert "channel-group 1 mode active" in gi1.config and gi1.config.startswith("interface GigabitEthernet1/0/1"), gi1.config
    from core.utils import mask_secrets
    assert "S3cr3t" not in mask_secrets(" ip ospf message-digest-key 1 md5 7 S3cr3t\n snmp-server community S3cr3t RO") and "standby 1 ip 10.1.1.1" in mask_secrets(" standby 1 ip 10.1.1.1")
    # layer 3 view of the core
    l3 = core.l3
    assert l3["is_l3"] and {s["name"] for s in l3["svis"]} == {"Vlan10", "Vlan20", "Vlan30"}
    assert [p["protocol"] for p in l3["protocols"]] == ["Static", "OSPF"] and l3["protocols"][1]["router_id"] == "1.1.1.1"
    assert any(r["default"] for r in l3["routes"]) and l3["dhcp"]["helpers"][0]["address"] == "10.10.10.5"
    assert l3["acls"][0]["name"] == "USERS-IN" and l3["acls"][0]["applied_to"] == ["Vlan20 (in)"]
    assert l3["snmp"]["hosts"][0]["host"] == "10.10.10.50" and "config" in l3["snmp"]["traps_enabled"]
    assert l3["dhcp"]["pools"][0]["name"] == "WIFI" and l3["svis"][2]["virtual_ip"] == ["10.10.30.254"]
    return an


def test_excel(an):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(build_workbook(an)))
    assert wb.sheetnames == ["Switches", "Access Points", "Connected Devices", "Firewalls", "Summary"]
    ws = wb["Switches"]
    merged = {str(r) for r in ws.merged_cells.ranges}
    assert any(m.startswith("E") for m in merged) and any(m.startswith("H") for m in merged), merged
    assert wb["Firewalls"].max_row == 4 and wb["Access Points"].max_row == 4 and wb["Summary"]["D2"].value.startswith("=COUNTIF")
    return wb


def test_vendors():
    an = run(load(["ARUBA-2930.txt", "CX-6300.txt", "OS6450-A.txt", "TPLINK-SG3428.txt", "DLINK-DGS3120.txt", "COMWARE-5130.txt"]))
    got = {s.filename: s for s in an.switches}
    a = got["ARUBA-2930.txt"]
    assert a.detected_as.startswith("HPE / Aruba ProCurve") and a.is_stack and a.mgmt_ip == "10.10.10.21" and a.gateway == "10.10.10.1"
    assert a.members[0].mac == "94:57:A5:1D:2E:00" and a.firmware == "WC.16.10.0003"
    c = got["CX-6300.txt"]
    assert c.detected_as == "Aruba AOS-CX" and c.mgmt_ip == "10.10.99.5" or c.mgmt_ip == "10.10.99.2", c.mgmt_ip
    assert c.firmware == "FL.10.08.0001" and c.gateway == "10.10.99.1"
    o = got["OS6450-A.txt"]
    assert o.detected_as.startswith("Alcatel") and o.hostname == "OS6450-A" and o.mgmt_ip == "10.10.10.30" and o.mgmt_vlan == 10
    assert o.primary.model == "OS6450-P24" and o.primary.serial == "N1234567" and o.primary.mac == "E8:E7:32:11:22:33"
    t = got["TPLINK-SG3428.txt"]
    assert t.detected_as.startswith("TP-Link") and t.mgmt_ip == "10.10.10.40" and t.primary.serial == "2171234567890"
    assert t.primary.mac == "00:0A:EB:12:34:56"
    d = got["DLINK-DGS3120.txt"]
    assert d.detected_as == "D-Link" and d.primary.model == "DGS-3120-24TC" and d.mgmt_ip == "10.10.10.50" and d.gateway == "10.10.10.1"
    h = got["COMWARE-5130.txt"]
    assert h.detected_as.startswith("HPE / H3C") and h.hostname == "H3C-5130" and h.primary.serial == "CN98765432" and h.mgmt_ip == "10.10.10.60"


def test_stack_without_show_switch():
    txt = """hostname STK3
SW#show version
Switch Ports Model              SW Version        SW Image              Mode
------ ----- -----              ----------        ----------            ----
*    1 28    C9200L-24P-4G      17.06.04          CAT9K_LITE_IOSXE      INSTALL
     2 28    C9200L-24P-4G      17.06.04          CAT9K_LITE_IOSXE      INSTALL
Switch 01
---------
Base Ethernet MAC Address . . . . : 0c:d0:f8:00:00:01
Model Number                       : C9200L-24P-4G
System Serial Number               : AAA111
Switch 02
---------
Base Ethernet MAC Address          : 0c:d0:f8:00:00:02
Model Number                       : C9200L-24P-4G
System Serial Number               : BBB222
"""
    from core.parsers.cisco import CiscoParser
    sw = CiscoParser(txt, "stk3.txt", "x").parse()
    assert [m.mac for m in sw.members] == ["0C:D0:F8:00:00:01", "0C:D0:F8:00:00:02"], sw.members
    one = CiscoParser("hostname A\nSW#show interfaces\nVlan1 is up, line protocol is up\n  Hardware is EtherSVI, address is 0cd0.f8aa.bb01 (bia 0cd0.f8aa.bb01)\n", "a.txt", "y").parse()
    assert one.members[0].mac == "0C:D0:F8:AA:BB:01", one.members


def test_generic_parts_other_oems():
    from core.parsers.comware import ComwareParser
    from core.parsers.generic import GenericParser
    h3c = """sysname H3C-STK
<H3C-STK>display device manuinfo
Slot 1:
DEVICE_NAME          : HPE 5130-24G-4SFP+ EI
DEVICE_SERIAL_NUMBER : CN98765432
MAC_ADDRESS          : 3C-8A-B0-11-22-33

Slot 2:
DEVICE_NAME          : HPE 5130-24G-4SFP+ EI
DEVICE_SERIAL_NUMBER : CN11112222
MAC_ADDRESS          : 3C-8A-B0-44-55-66

Fan 1:
DEVICE_NAME          : FAN-ASSY-1
DEVICE_SERIAL_NUMBER : FANSN00001
<H3C-STK>display transceiver manuinfo interface
GigabitEthernet1/0/25 transceiver information:
  Part Number      : JD118B
  Serial Number    : CN55556666
"""
    sw = ComwareParser(h3c, "h3c.txt", "x").parse()
    assert [(m.serial, m.mac) for m in sw.members] == [("CN98765432", "3C:8A:B0:11:22:33"), ("CN11112222", "3C:8A:B0:44:55:66")]
    assert sorted(c.kind for c in sw.components) == ["Fan / Fan Tray", "SFP / Transceiver"], sw.components
    gen = """hostname UNKNOWN-SW
mystery# show modules
 Slot   Module Description         Part Number    Serial Number
 -----  -------------------------  -------------  -------------
 1      24-port Gigabit switch     JL253A         SG11223344
 2      4-port expansion module    JL083A         SG55667788
"""
    sw2 = GenericParser(gen, "u.txt", "y").parse()
    assert sw2.members[0].serial == "SG11223344" and [c.serial for c in sw2.components] == ["SG55667788"], (sw2.members, sw2.components)


def test_quantum():
    """Quantum Networks QN-SW (Sx350-style CLI) + QQ-SW, mostly raw PuTTY logs, plus a Cisco CBS350 (same CLI family)."""
    an = run(load(["QN-CORE.txt", "QN-ACC1.txt", "QQ-ACC2.txt", "QN-ACC3.txt", "CBS350-24P.txt"]))
    sw = {s.filename: s for s in an.switches}
    core, a1, a2, a3, cbs = (sw[n] for n in ("QN-CORE.txt", "QN-ACC1.txt", "QQ-ACC2.txt", "QN-ACC3.txt", "CBS350-24P.txt"))
    assert all(s.vendor == "Quantum Networks" and s.detected_as.startswith("Quantum") for s in (core, a1, a2, a3)), [s.detected_as for s in an.switches]
    assert cbs.vendor == "Cisco" and cbs.detected_as.startswith("Cisco Business") and cbs.primary.model == "CBS350-24P-4G"
    # identity: 'more system-info' model, inventory serials, stack of two (master + inferred backup), firmware
    assert core.hostname == "QN-CORE-SERVER-ROOM-STK" and core.is_stack and core.firmware == "2.3.22.00", core.hostname
    assert [(m.model, m.serial, m.role) for m in core.members] == [("QN-SW-325-24", "QNTEST0A0001", "Active"), ("QN-SW-325-24", "QNTEST0A0002", "Standby")]
    assert core.members[0].mac == "58:61:63:A0:00:01" and core.mgmt_ip == "10.20.10.1" and core.mgmt_vlan == 10 and core.gateway == "10.20.10.254"
    assert a1.primary.model == "QN-SW-225-12P" and a1.primary.serial == "QNTEST0A0011" and a1.mgmt_ip == "10.20.10.11" and a1.firmware == "2.3.20.00"
    assert (a2.os_family, a2.primary.model, a2.primary.serial, a2.primary.mac) == ("QQ-SW", "QQ-SW-155-G24P-4SFPP", "QNTEST0A0012", "58:61:63:A0:00:12")
    assert a2.firmware == "QN_SW_QQ-2.6.03.24" and a2.mgmt_ip == "10.20.10.12" and a2.gateway == "10.20.10.1"
    # ports: config + 'show interfaces status' + typed-ahead 'sh int confi' whose output was glued to a later prompt
    p = {i.name: i for i in core.interfaces}
    assert p["GigabitEthernet1/0/3"].admin_down and p["GigabitEthernet1/0/1"].status == "up" and p["TwentyFiveGigE1/0/1"].media == "fiber"
    assert p["GigabitEthernet1/0/2"].vlan == "trunk (native 20)" and p["GigabitEthernet1/0/1"].vlan == "10"
    assert {i.name for i in a1.interfaces if i.admin_down} == {"GigabitEthernet1/0/9"} and {v.id: v.name for v in a1.vlans}[20] == "USERS"
    assert [i.name for i in a2.interfaces if i.status == "up"] == ["Ethernet1/0/1", "Ethernet1/0/25", "Ethernet1/0/26"]
    assert {i.name: i.media for i in a2.interfaces}["Ethernet1/0/25"] == "fiber" and {v.id: v.name for v in a2.vlans}[10] == "MGMT"
    # no running-config: VLAN names (wrapped in 'show vlan'), port VLANs and the IP come from show output
    assert a3.hostname == "QN-ACC3" and a3.mgmt_ip == "10.20.10.13" and {v.id: v.name for v in a3.vlans}[20] == "***Barcode_Scanner***"
    p3 = {i.name: i for i in a3.interfaces}
    assert p3["GigabitEthernet1/0/4"].vlan == "20" and p3["GigabitEthernet1/0/12"].mode == "trunk" and any("No running-config" in w for w in a3.warnings)
    # LLDP: wrapped system names are rebuilt, chassis MACs match the uploaded switches
    n1 = {n.local_port: n for n in core.neighbors}
    assert n1["TwentyFiveGigE1/0/1"].remote_name == "QN-ACC1-STORE-BUILDING-A" and n1["TwentyFiveGigE1/0/1"].remote_port == "TenGigabitEthernet1/0/1"
    assert n1["TwentyFiveGigE2/0/1"].remote_mac == "58:61:63:A0:00:12"
    # topology: core to all three, ring closed ACC1-ACC2 and blocked on ACC1 (RSTP Altn/Blk)
    names = {s.id: s.hostname for s in an.switches}
    links = {tuple(sorted((names.get(l["a"], l["a"]), names.get(l["b"], l["b"])))): l for l in an.links}
    want = {("QN-ACC3", "QN-CORE-SERVER-ROOM-STK"), ("QN-ACC1-STORE-BUILDING-A", "QN-CORE-SERVER-ROOM-STK"),
            ("QN-CORE-SERVER-ROOM-STK", "QQ-ACC2"), ("QN-ACC1-STORE-BUILDING-A", "QQ-ACC2")}
    assert want <= set(links), sorted(links)
    assert links[("QN-ACC1-STORE-BUILDING-A", "QQ-ACC2")]["blocked"] and not links[("QN-ACC3", "QN-CORE-SERVER-ROOM-STK")]["blocked"]
    assert a1.blocked_ports == ["GigabitEthernet1/0/12"] and links[("QN-ACC1-STORE-BUILDING-A", "QN-CORE-SERVER-ROOM-STK")]["media"] == "fiber"
    nodes = {n["hostname"]: n for n in an.nodes if n["kind"] == "switch"}
    assert nodes["QN-CORE-SERVER-ROOM-STK"]["is_root"] and nodes["QQ-ACC2"]["stp_root"].startswith("QN-CORE-SERVER-ROOM-STK")
    assert {a["hostname"] for a in an.aps} == {"AP-RECEPTION", "AP-STORE-01"} and an.firewalls[0]["make"] == "Fortinet"
    assert {c.model for c in core.components} == {"QN-UTSPP-LR"} and core.components[0].make == "Quantum Networks"
    # layer 3 on the core: SVIs, flat ACL applied with service-acl, flat DHCP pool, default gateway = default route
    l3 = core.l3
    assert l3["is_l3"] and [s["name"] for s in l3["svis"]] == ["Vlan10", "Vlan20", "Vlan30"] and l3["dhcp"]["pools"][0]["network"] == "10.20.20.0/24"
    assert l3["acls"][0]["name"] == "GUEST-BLOCK" and l3["acls"][0]["count"] == 2 and l3["acls"][0]["applied_to"] == ["GigabitEthernet1/0/2 (in)"]
    # no false alarms: hashed passwords, the QN-SW default route, a backup without running-config
    titles = {(f["hostname"], f["title"]) for f in an.findings["items"]}
    assert not any(t for h, t in titles if "password" in t.lower() or "default route" in t or "Stack" in t), sorted(titles)
    assert not any(h == "QN-ACC3" for h, t in titles) and ("QN-ACC1-STORE-BUILDING-A", "SNMP read-write community configured") in titles
    assert "s4mpleComm" not in str(an.to_dict())                                  # community strings never reach the browser
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(build_workbook(an)))
    assert wb["Switches"].max_row >= 7 and "<svg" in render_svg(an.nodes, an.links)


def test_quantum_stack_and_lag():
    """'show stack' gives the real roles and beats the 'more system-info' guess (unit 2 is active here), with both the
    current (active/standby) and the older (master/backup) wording; 'channel-group N mode auto' is an LACP bundle."""
    from core.parsers.quantum import QuantumParser
    head = ("QN-STK#sh runn\nconfig-file-header\nQN-STK\nv7.2.7.14 / RLINUX_900_100\n@\nhostname QN-STK\n"
            "interface gi1/0/1\n channel-group 1 mode auto\nexit\ninterface gi2/0/1\n channel-group 1 mode auto\nexit\n"
            "QN-STK#more system-info\n*********   SWITCH SYSTEM INFORMATION   *********\nModel Number : QN-SW-325-24\n"
            "Serial Number : QNTEST0B0001\nMAC Address : 58:61:63:b0:00:01\nSwitch Firmware Version : 2.3.22.00\n"
            'QN-STK#sh inventory\nNAME: "1"  DESCR: "24 GE Managed Switch"\nPID: 3824GS-6Y  VID:  0  SN: QNTEST0B0001\n'
            'NAME: "2"  DESCR: "24 GE Managed Switch"\nPID: 3824GS-6Y  VID:  0  SN: QNTEST0B0002\n')
    for low, high in (("standby", "active"), ("backup", "master")):
        stack = ("QN-STK#sh stack\n\nTopology is Chain\nUnits stack mode: Native\n\n"
                 "Unit Id     MAC Address        Role    Network Uplink \n"
                 "                                       Port    Port   \n"
                 "------- ------------------- ---------- ------- ------ \n"
                 f"   1     58:61:63:b0:00:01  {low:^10}   gi      te   \n"
                 f"   2     58:61:63:b0:00:02  {high:^10}   gi      te   \nQN-STK#")
        sw = QuantumParser(head + stack, "QN-STK.txt", "s").parse()
        got = [(m.index, m.model, m.serial, m.role) for m in sw.members]
        assert got == [(1, "QN-SW-325-24", "QNTEST0B0001", "Standby"), (2, "QN-SW-325-24", "QNTEST0B0002", "Active")], got
        assert sw.members[1].mac == "58:61:63:B0:00:02" and not any("inferred" in i for i in sw.info), sw.info
    assert [(p["name"], p["protocol"], [m["port"] for m in p["members"]]) for p in sw.port_channels] == \
        [("Port-channel1", "LACP", ["GigabitEthernet1/0/1", "GigabitEthernet2/0/1"])], sw.port_channels


def test_terminal_log_cleanup():
    from core.utils import clean_terminal_log
    from core.parsers.quantum import split_log, classify
    assert clean_terminal_log("SW#sh inv\x08 \x1b[D\x08 \x1b[Dnventory\r\n") == "SW#sh inventory\n"
    assert clean_terminal_log("host\r\x1b[KQN-1#conf t\r\n\rQN-1(config)#") == "QN-1#conf t\nQN-1(config)#"
    chunks = split_log("SW#terminal datadump\nsh runn\nSW#config-file-header\nhostname SW\nSW#show cdp neighbors\n% Unrecognized command\n"
                       "SW#              Flow Link\nPort      Type         Duplex  Speed  Neg      ctrl State\n")
    kinds = [classify(c) for c in chunks]
    assert "config" in kinds and kinds[-1] == "interfaces status", [(c["cmd"], classify(c)) for c in chunks]


if __name__ == "__main__":
    an = test_cisco_ring()
    test_excel(an)
    svg = render_svg(an.nodes, an.links)
    assert "<svg" in svg and "stroke-dasharray" in svg
    test_vendors()
    test_stack_without_show_switch()
    test_generic_parts_other_oems()
    test_quantum()
    test_quantum_stack_and_lag()
    test_terminal_log_cleanup()
    print("all tests passed")
