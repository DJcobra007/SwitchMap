"""Writes realistic-looking sample backups into samples/ (used by tests and for a quick demo)."""
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "samples")
os.makedirs(OUT, exist_ok=True)


def w(name, text):
    with open(os.path.join(OUT, name), "w", newline="\n") as f:
        f.write(text.lstrip("\n"))


def status_table(rows):
    def r(*c):
        return c[0].ljust(10) + c[1][:18].ljust(19) + c[2].ljust(13) + c[3].ljust(11) + c[4].ljust(8) + c[5].ljust(7) + c[6]
    return "\n".join([r("Port", "Name", "Status", "Vlan", "Duplex", "Speed", "Type")] + [r(*x) for x in rows])


# ------------------------------------------------------------------ CORE-STK
core_rows = [("Gi1/0/1", "to ACC-SW1 Gi1/0/48", "connected", "trunk", "a-full", "a-1000", "10/100/1000BaseTX"),
             ("Gi1/0/2", "AP-LOBBY", "connected", "30", "a-full", "a-1000", "10/100/1000BaseTX")]
core_rows += [(f"Gi1/0/{i}", "", "notconnect", "1", "auto", "auto", "10/100/1000BaseTX") for i in range(3, 25)]
core_rows += [("Gi1/1/1", "", "notconnect", "1", "auto", "auto", "Not Present"),
              ("Te1/1/1", "uplink ACC-SW2", "connected", "trunk", "full", "10G", "SFP-10GBase-SR"),
              ("Gi2/0/1", "", "connected", "20", "a-full", "a-1000", "10/100/1000BaseTX"),
              ("Gi2/0/2", "", "notconnect", "1", "auto", "auto", "10/100/1000BaseTX")]

w("CORE-STK.txt", f"""
Building configuration...

Current configuration : 4120 bytes
!
version 17.6
service timestamps debug datetime msec
!
hostname CORE-STK
!
switch 1 provision c9200l-24p-4g
switch 2 provision c9200l-24p-4g
switch 1 priority 15
switch 2 priority 14
!
vlan 10
 name MGMT
vlan 20
 name USERS
vlan 30
 name WIFI
!
spanning-tree mode rapid-pvst
!
ip dhcp pool WIFI
 network 10.10.30.0 255.255.255.0
 default-router 10.10.30.1
!
interface Port-channel1
 description uplink bundle to ACC-SW1
 switchport mode trunk
!
interface GigabitEthernet1/0/1
 description to ACC-SW1 Gi1/0/48
 switchport mode trunk
 channel-group 1 mode active
!
interface GigabitEthernet2/0/3
 description to ACC-SW1 Gi1/0/46
 switchport mode trunk
 channel-group 1 mode active
!
interface GigabitEthernet1/0/2
 description AP-LOBBY
 switchport access vlan 30
!
interface GigabitEthernet2/0/1
 switchport access vlan 20
!
interface GigabitEthernet2/0/10
 switchport access vlan 10
!
interface TenGigabitEthernet1/1/1
 description uplink ACC-SW2
 switchport mode trunk
!
interface Vlan10
 description Management
 ip address 10.10.10.2 255.255.255.0
!
interface Vlan20
 ip address 10.10.20.1 255.255.255.0
 ip helper-address 10.10.10.5
 ip access-group USERS-IN in
!
interface Vlan30
 ip address 10.10.30.1 255.255.255.0
 standby 30 ip 10.10.30.254
!
ip routing
ip route 0.0.0.0 0.0.0.0 10.10.10.1
ip route 172.16.0.0 255.255.0.0 10.10.10.9 150 name BRANCH
router ospf 1
 router-id 1.1.1.1
 network 10.10.0.0 0.0.255.255 area 0
 passive-interface default
 redistribute static
!
ip access-list extended USERS-IN
 permit ip 10.10.20.0 0.0.0.255 any
 deny ip any any log
!
logging source-interface Vlan10
snmp-server trap-source Vlan10
snmp-server enable traps config
snmp-server enable traps snmp linkdown linkup
aaa new-model
aaa authentication login default group tacacs+ local
aaa authorization exec default group tacacs+ local
aaa accounting exec default start-stop group tacacs+
tacacs server T1
 address ipv4 10.10.10.10
ntp server 10.10.10.20
snmp-server community S3cretRO RO
snmp-server location HQ
snmp-server host 10.10.10.50 version 2c S3cretRO
ip name-server 10.10.10.30
ip domain name corp.local
!
end

CORE-STK#show version
Cisco IOS XE Software, Version 17.06.04
Cisco IOS Software [Bengaluru], Catalyst L3 Switch Software (CAT9K_LITE_IOSXE), Version 17.6.4, RELEASE SOFTWARE (fc1)

Switch Ports Model              SW Version        SW Image              Mode
------ ----- -----              ----------        ----------            ----
*    1 28    C9200L-24P-4G      17.06.04          CAT9K_LITE_IOSXE      INSTALL
     2 28    C9200L-24P-4G      17.06.04          CAT9K_LITE_IOSXE      INSTALL

Switch 01
---------
Base Ethernet MAC Address          : 0c:d0:f8:b5:11:00
Model Number                       : C9200L-24P-4G
System Serial Number               : JAE24020B2D

Switch 02
---------
Base Ethernet MAC Address          : 0c:d0:f8:b5:22:00
Model Number                       : C9200L-24P-4G
System Serial Number               : JAE24020B7K

CORE-STK#show switch
Switch/Stack Mac Address : 0cd0.f8b5.1100 - Local Mac Address
                                           H/W   Current
Switch#  Role   Mac Address     Priority Version  State
-------------------------------------------------------------
*1       Active 0cd0.f8b5.1100     15     V02     Ready
 2       Standby 0cd0.f8b5.2200    14     V02     Ready

CORE-STK#show inventory
NAME: "Switch 1", DESCR: "Cisco Catalyst 9200L 24-port PoE+, 4 x 1G Uplink Switch"
PID: C9200L-24P-4G     , VID: V01  , SN: JAE24020B2D

NAME: "Switch 1 - FRU Uplink Module 1", DESCR: "Cisco Catalyst 9200L 4x1G Uplink Module"
PID: C9200-NM-4G       , VID: V01  , SN: JAE240201XY

NAME: "Switch 1 - StackPort1", DESCR: "Stack Cable 50cm"
PID: STACK-T1-50CM     , VID: V01  , SN: MOC2338A1Z1

NAME: "Switch 2", DESCR: "Cisco Catalyst 9200L 24-port PoE+, 4 x 1G Uplink Switch"
PID: C9200L-24P-4G     , VID: V01  , SN: JAE24020B7K

NAME: "Switch 1 - Power Supply A", DESCR: "Cisco 125W AC Power Supply, with "quote" inside"
PID: PWR-C5-125WAC     , VID: V01  , SN: DCC2412ABCD

NAME: "Switch 1 - FAN 1", DESCR: "Switch 1 - FAN 1"
PID: FAN-T1            , VID:      , SN:

NAME: "Switch 2 - Some Unknown Part", DESCR: "Mystery accessory"
PID: XYZ-123           , VID: V01  , SN: ABC99999999

NAME: "Switch 2 - StackPort1", DESCR: "Stack Cable 50cm"
PID: STACK-T1-50CM     , VID: V01  , SN: MOC2338A1Z2

NAME: "TenGigabitEthernet1/1/1", DESCR: "SFP-10GBase-SR"
PID: SFP-10G-SR        , VID: V03  , SN: FNS24120ABC

NAME: "TenGigabitEthernet2/1/2", DESCR: "Cisco Direct Attach Twinax"
PID: SFP-H10GB-CU3M    , VID: V03  , SN: TED2411X0AB

CORE-STK#show vlan brief

VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi1/0/3, Gi1/0/4
10   MGMT                             active
20   USERS                            active    Gi2/0/1
30   WIFI                             active    Gi1/0/2
1002 fddi-default                     act/unsup

CORE-STK#show interfaces status

{status_table(core_rows)}

CORE-STK#show etherchannel summary
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi1/0/1(P)  Gi2/0/3(P)

CORE-STK#show spanning-tree

VLAN0001
  Spanning tree enabled protocol rstp
  Root ID    Priority    24577
             Address     0cd0.f8b5.1100
             This bridge is the root
  Bridge ID  Priority    24577
             Address     0cd0.f8b5.1100

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi1/0/1             Desg FWD 4         128.1    P2p
Te1/1/1             Desg FWD 2         128.50   P2p

CORE-STK#show cdp neighbors detail
-------------------------
Device ID: ACC-SW1.corp.local
Entry address(es):
  IP address: 10.10.10.11
Platform: cisco WS-C2960X-48FPD-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/48
Holdtime : 150 sec
-------------------------
Device ID: ACC-SW1.corp.local
Entry address(es):
  IP address: 10.10.10.11
Platform: cisco WS-C2960X-48FPD-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet2/0/3,  Port ID (outgoing port): GigabitEthernet1/0/46
Holdtime : 150 sec
-------------------------
Device ID: ACC-SW2.corp.local
Entry address(es):
  IP address: 10.10.10.12
Platform: cisco WS-C2960X-24PS-L,  Capabilities: Switch IGMP
Interface: TenGigabitEthernet1/1/1,  Port ID (outgoing port): TenGigabitEthernet1/1/1
Holdtime : 160 sec
-------------------------
Device ID: AP-LOBBY
Entry address(es):
  IP address: 10.10.30.50
Platform: cisco C9120AXI-E,  Capabilities: Trans-Bridge
Interface: GigabitEthernet1/0/2,  Port ID (outgoing port): GigabitEthernet0
Holdtime : 140 sec

CORE-STK#show lldp neighbors detail
------------------------------------------------
Local Intf: Gi2/0/10
Chassis id: 0830.6b12.3456
Port id: ethernet1/3
Port Description: ethernet1/3
System Name: PA-FW01
System Description:
Palo Alto Networks PA-3220 firewall
Time remaining: 99 seconds
System Capabilities: B,R
Enabled Capabilities: B,R
Management Addresses:
    IP: 10.10.10.250
------------------------------------------------
Local Intf: Gi2/0/20
Chassis id: 0001.0203.0405
Port id: eth9
System Name: LINUX-HOST-7
System Description:
Ubuntu server
Time remaining: 99 seconds
System Capabilities: S
Enabled Capabilities: S
""")

# ------------------------------------------------------------------ ACC-SW1
a1 = [("Gi1/0/1", "PC-reception", "connected", "20", "a-full", "a-100", "10/100/1000BaseTX"),
      ("Gi1/0/10", "AP-515-Floor1", "connected", "20", "a-full", "a-1000", "10/100/1000BaseTX")]
a1 += [(f"Gi1/0/{i}", "", "notconnect", "20", "auto", "auto", "10/100/1000BaseTX") for i in (2, 3, 4)]
a1 += [("Gi1/0/47", "ring to ACC-SW2", "connected", "trunk", "a-full", "a-1000", "10/100/1000BaseTX"),
       ("Gi1/0/48", "to CORE-STK", "connected", "trunk", "a-full", "a-1000", "10/100/1000BaseTX"),
       ("Gi1/1/1", "", "notconnect", "1", "auto", "auto", "Not Present")]
w("ACC-SW1.txt", f"""
version 15.2
hostname ACC-SW1
!
vlan 10
 name MGMT
vlan 20
 name USERS
!
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/10
 description AP-515-Floor1
 switchport access vlan 20
!
interface Port-channel2
 description bundle to CORE-STK
 switchport mode trunk
!
interface GigabitEthernet1/0/46
 description to CORE-STK Gi2/0/3
 channel-group 2 mode active
!
interface GigabitEthernet1/0/20
 description SOPHOS-XGS2300 LAN
!
interface GigabitEthernet1/0/21
 description uplink to FortiGate-100F
!
interface GigabitEthernet1/0/47
 description ring to ACC-SW2
 switchport mode trunk
 switchport trunk native vlan 10
!
interface GigabitEthernet1/0/48
 description to CORE-STK
 switchport mode trunk
 channel-group 2 mode active
!
interface Vlan10
 ip address 10.10.10.11 255.255.255.0
!
ip default-gateway 10.10.10.1
snmp-server trap-source Vlan10
ip http server
line vty 0 4
 transport input telnet ssh
aaa new-model
aaa authentication login default group tacacs+ local
aaa authorization exec default group tacacs+ local
aaa accounting exec default start-stop group tacacs+
tacacs server T1
 address ipv4 10.10.10.10
ntp server 10.10.10.20
snmp-server community S3cretRO RO
snmp-server location HQ
snmp-server host 10.10.10.50 version 2c S3cretRO
ip name-server 10.10.10.30
ip domain name corp.local
end

ACC-SW1#show version
Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E3, RELEASE SOFTWARE (fc2)
cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision A0) with 524288K bytes of memory.
Processor board ID FCW2133L0AB
Base ethernet MAC Address       : 00:1E:F7:AA:10:00
Model number                    : WS-C2960X-48FPD-L
System serial number            : FCW2133L0AB

ACC-SW1#show interfaces status

{status_table(a1)}

ACC-SW1#show etherchannel summary
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
2      Po2(SU)         LACP      Gi1/0/46(P) Gi1/0/48(P)

ACC-SW1#show spanning-tree

VLAN0001
  Spanning tree enabled protocol rstp
  Root ID    Priority    24577
             Address     0cd0.f8b5.1100
             Cost        4
             Port        48 (GigabitEthernet1/0/48)
  Bridge ID  Priority    32769
             Address     001e.f7aa.1000
Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi1/0/47            Desg FWD 4         128.47   P2p
Gi1/0/48            Root FWD 4         128.48   P2p

ACC-SW1#show lldp neighbors detail
------------------------------------------------
Local Intf: Gi1/0/10
Chassis id: 9c1c.1234.5678
Port id: 9C:1C:12:34:56:78
Port Description: eth0
System Name: AP-515-Floor1
System Description:
ArubaOS (MODEL: 515), Version 8.10.0.2
Time remaining: 99 seconds
System Capabilities: B,W,R
Enabled Capabilities: B,W
Management Addresses:
    IP: 10.10.20.60
Auto Negotiation - supported, enabled
""")

# ------------------------------------------------------------------ ACC-SW2
a2 = [("Gi1/0/12", "AP-Warehouse", "connected", "10", "a-full", "a-1000", "10/100/1000BaseTX"),
      ("Gi1/0/47", "ring to ACC-SW1", "connected", "trunk", "a-full", "a-1000", "10/100/1000BaseTX"),
      ("Te1/1/1", "to CORE-STK", "connected", "trunk", "full", "10G", "SFP-10GBase-SR")]
w("ACC-SW2.txt", f"""
version 15.2
hostname ACC-SW2
!
vlan 10
 name MGMT
!
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/12
 switchport access vlan 99
!
interface GigabitEthernet1/0/47
 description ring to ACC-SW1
 switchport mode trunk
 switchport trunk native vlan 20
!
interface Vlan10
 ip address 10.10.10.12 255.255.255.0
!
ip default-gateway 10.10.10.1
aaa new-model
aaa authentication login default group tacacs+ local
tacacs server T1
 address ipv4 10.10.10.10
snmp-server community S3cretRO RO
snmp-server community public RO
snmp-server location HQ
snmp-server host 10.10.10.50 version 2c S3cretRO
ip name-server 8.8.8.8
ip domain name corp.local
end

ACC-SW2#show version
cisco WS-C2960X-24PS-L (APM86XXX) processor (revision A0) with 524288K bytes of memory.
Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E3, RELEASE SOFTWARE (fc2)
Base ethernet MAC Address       : 00:1E:F7:BB:20:00
Model number                    : WS-C2960X-24PS-L
System serial number            : FCW2201Q0CD

ACC-SW2#show inventory
NAME: "1", DESCR: "WS-C2960X-24PS-L"
PID: WS-C2960X-24PS-L  , VID: V01  , SN: FCW2201Q0CD

NAME: "TenGigabitEthernet1/1/1", DESCR: "SFP-10GBase-SR"
PID: SFP-10G-SR        , VID: V03  , SN: FNS24120XYZ

ACC-SW2#show interfaces status

{status_table(a2)}

ACC-SW2#show spanning-tree

VLAN0001
  Spanning tree enabled protocol rstp
  Root ID    Priority    24577
             Address     0cd0.f8b5.1100
             Cost        2
             Port        50 (TenGigabitEthernet1/1/1)
  Bridge ID  Priority    32769
             Address     001e.f7bb.2000
Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi1/0/47            Altn BLK 4         128.47   P2p
Te1/1/1             Root FWD 2         128.50   P2p

ACC-SW2#show cdp neighbors detail
-------------------------
Device ID: AP-Warehouse
Entry address(es):
  IP address: 10.10.10.70
Platform: cisco AIR-AP2802I-E-K9,  Capabilities: Trans-Bridge
Interface: GigabitEthernet1/0/12,  Port ID (outgoing port): GigabitEthernet0
Holdtime : 140 sec
""")

# ------------------------------------------------------------- other vendors
w("ARUBA-2930.txt", """
Running configuration:

; JL253A Configuration Editor; Created on release #WC.16.10.0003
hostname "ARUBA-2930"
stacking
   member 1 type "JL253A" mac-address 9457a5-1d2e00
   member 2 type "JL253A" mac-address 9457a5-1d3f00
   exit
vlan 1
   name "DEFAULT_VLAN"
   no untagged 1
   exit
vlan 10
   name "MGMT"
   tagged 1/25
   ip address 10.10.10.21 255.255.255.0
   exit
ip default-gateway 10.10.10.1
spanning-tree

ARUBA-2930# show system
 System Name        : ARUBA-2930
 Software revision  : WC.16.10.0003
 Base MAC Addr      : 9457a5-1d2e00
 Serial Number      : SG9BKP1234

ARUBA-2930# show lldp info remote-device

  LLDP Remote Devices Information

  LocalPort | ChassisId                 PortId PortDescr SysName
  --------- + ------------------------- ------ --------- ----------------------
  1/25      | 0c d0 f8 b5 11 00         Gi1/0/5  Gi1/0/5 CORE-STK
""")

w("CX-6300.txt", """
!Version ArubaOS-CX FL.10.08.0001
hostname CX-6300
vrf mgmt
vlan 1
vlan 20
    name USERS
vlan 99
    name MGMT
spanning-tree mode mstp
interface mgmt
    no shutdown
    ip static 10.10.99.5/24
    default-gateway 10.10.99.1
interface 1/1/1
    no shutdown
    description to CORE
    vlan trunk allowed all
interface vlan 99
    ip address 10.10.99.2/24
ip route 0.0.0.0/0 10.10.99.1
""")

w("OS6450-A.txt", """
! Chassis:
system name "OS6450-A"
! VLAN:
vlan 1 enable name "VLAN 1"
vlan 10 enable name "Mgmt"
! IP:
ip interface "mgmt" address 10.10.10.30 mask 255.255.255.0 vlan 10 ifindex 1
ip static-route 0.0.0.0 mask 0.0.0.0 gateway 10.10.10.1 metric 1
-> show chassis
Chassis 1
  Model Name:                    OS6450-P24,
  Serial Number:                 N1234567,
  MAC Address:                   e8:e7:32:11:22:33,
""")

w("TPLINK-SG3428.txt", """
#
hostname "SG3428"
vlan 10
 name "MGMT"
interface vlan 10
 ip address-alloc static ip-address 10.10.10.40 mask 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.10.10.1
spanning-tree mode rstp
SG3428#show system-info
 System Description - JetStream 24-Port Gigabit L2 Managed Switch with 4 SFP Slots
 Firmware Version   - 3.0.0 Build 20200923 Rel.57812
 MAC Address        - 00-0A-EB-12-34-56
 Serial Number      - 2171234567890
""")

w("DLINK-DGS3120.txt", """
# DGS-3120-24TC Gigabit Ethernet Switch
# Configuration
# Firmware: Build 2.00.B011
config ipif System ipaddress 10.10.10.50/24 vlan default state enable
create iproute default 10.10.10.1 1
create vlan Mgmt tag 10
config snmp system_name DGS3120
enable stp
config stp version rstp
""")

w("COMWARE-5130.txt", """
#
 version 7.1.070, Release 3208P05
#
 sysname H3C-5130
#
vlan 10
 name MGMT
#
interface Vlan-interface10
 ip address 10.10.10.60 255.255.255.0
#
ip route-static 0.0.0.0 0 10.10.10.1
 stp mode rstp
<H3C-5130>display device manuinfo
Slot 1:
DEVICE_NAME          : HPE 5130-24G-4SFP+ EI
DEVICE_SERIAL_NUMBER : CN98765432
MAC_ADDRESS          : 3C-8A-B0-11-22-33
""")
# ------------------------------------------------------- Quantum Networks (qntmnet.com)
# A small ring: QN-CORE (QN-SW-325-24 stack) - QN-ACC1 (QN-SW-225-12P) - QQ-ACC2 (QQ-SW-155) - back to the core, plus
# QN-ACC3 on the core. Most files are raw PuTTY logs, as they are usually collected: CR/LF, ANSI + backspace edits,
# abbreviated commands, a command typed ahead, a lost echo with the output glued to the prompt, wrapped LLDP/VLAN names.
LLDP_LEGEND = """
System capability legend:
B - Bridge; R - Router; W - Wlan Access Point; T - telephone;
D - DOCSIS Cable Device; H - Host; r - Repeater;
TP - Two Ports MAC Relay; S - S-VLAN; C - C-VLAN; O - Other
"""


def putty(text):
    return "=~=~=~=~=~=~=~=~=~=~=~= PuTTY log 2026.01.01 10:00:00 =~=~=~=~=~=~=~=~=~=~=~=\r\n" + text.lstrip("\n").replace("\n", "\r\n")


def qn_status(rows):
    out = ["                                              Flow Link          Back   Mdix",
           "Port      Type         Duplex  Speed  Neg      ctrl State       Pressure Mode",
           "--------- ------------ ------  ------ -------- ---- ----------- -------- -------"]
    for p, t, up, sp in rows:
        out.append(f"{p:<10}{t:<13}{'Full':<8}{sp:<7}{'Enabled':<9}{'Off':<5}{'Up':<12}{'Disabled':<9}Off" if up else
                   f"{p:<10}{t:<13}{'  --':<8}{'  --':<7}{'  --':<9}{' --':<5}{'Down':<12}{'   --':<9}  --")
    return "\n".join(out + ["", "                                          Flow    Link        ",
                            "Ch       Type    Duplex  Speed  Neg      control  State       ",
                            "-------- ------- ------  -----  -------- -------  ----------- ",
                            "Po1         --     --      --      --       --    Not Present "])


def qn_admin(rows):
    out = ["                                               Flow    Admin     Back   Mdix",
           "Port      Type         Duplex  Speed  Neg      control  State   Pressure Mode",
           "--------- ------------ ------  -----  -------- -------  -----   -------- ----"]
    out += [f"{p:<10}{t:<13}{'Full':<8}{'1000':<7}{'Enabled':<9}{'Off':<9}{st:<8}{'Disabled':<9}Auto" for p, t, st in rows]
    return "\n".join(out)


def qn_lldp(rows):
    """rows: (local port, device id, port id, system name, capabilities). Long names wrap at 17 characters."""
    out = [LLDP_LEGEND.strip("\n"), "", "  Port        Device ID         Port ID          System Name    Capabilities  TTL  ",
           "--------- ----------------- ----------------- ----------------- ------------ ----- "]
    for lp, dev, pid, name, caps in rows:
        chunks = [name[i:i + 17] for i in range(0, len(name), 17)] or [""]
        out.append(f"{lp:<10}{dev:<18}{pid:^17} {chunks[0]:<18}{caps:^12} 110  ")
        out += [" " * 46 + c for c in chunks[1:]]
    return "\n".join(out)


def qn_vlan(rows):
    """rows: (vid, name, [tagged items], [untagged items]). Names wrap at 17 characters, one port item per line."""
    out = ["Created by: D-Default, S-Static, G-GVRP, R-Radius Assigned VLAN, V-Voice VLAN", "",
           "Vlan       Name          Tagged Ports      UnTagged Ports      Created by     ",
           "---- ----------------- ------------------ ------------------ ---------------- "]
    for vid, name, tag, untag in rows:
        names = [name[i:i + 17] for i in range(0, len(name), 17)]
        for n in range(max(len(names), len(tag), len(untag), 1)):
            c0 = str(vid).rjust(3).ljust(5) if n == 0 else " " * 5
            out.append(c0 + (names[n] if n < len(names) else "").ljust(18) + (tag[n] if n < len(tag) else "").ljust(19)
                       + (untag[n] if n < len(untag) else "").ljust(19) + ("D" if vid == 1 else "S") * (n == 0))
    return "\n".join(out)


QN_CORE_MAC, QN_ACC1_MAC, QQ_ACC2_MAC, QN_ACC3_MAC = "58:61:63:a0:00:01", "58:61:63:a0:00:11", "58:61:63:a0:00:12", "58:61:63:a0:00:03"
P = "QN-CORE-SERVER-R...#"            # Quantum truncates long hostnames in the prompt
# 'sh int confi' was typed ahead, its echo got lost and the first output line is glued to a later prompt
CORE_ADMIN = qn_admin([("gi1/0/1", "1G-Copper", "Up"), ("gi1/0/2", "1G-Copper", "Up"), ("gi1/0/3", "1G-Copper", "Down")])
w("QN-CORE.txt", putty(f"""
QN-a00001#ter datadump
sh runn
\r{P}config-file-header
QN-CORE-SERVER-ROOM-STK
v7.2.7.14 / RLINUX_900_100
CLI v1.0
@
spanning-tree priority 0
vlan database
vlan 10,20,30
exit
ip dhcp server
ip dhcp pool network USERS
address low 10.20.20.10 high 10.20.20.250 255.255.255.0
default-router 10.20.20.1
dns-server 10.20.10.5
exit
hostname QN-CORE-SERVER-ROOM-STK
username netadmin password encrypted 0123456789abcdef0123456789abcdef01234567 privilege 15
ip ssh server
snmp-server community s4mpleComm rw view all
sntp server ntp.qntmnet.com poll
ip domain name example.local
ip name-server  10.20.10.5
ip access-list extended GUEST-BLOCK
deny ip 10.20.30.0 0.0.0.255 10.20.10.0 0.0.0.255 ace-priority 20
permit ip any any ace-priority 40
exit
!
interface vlan 10
 name MGMT
 ip address 10.20.10.1 255.255.255.0
 management-vlan
!
interface vlan 20
 name USERS
 ip address 10.20.20.1 255.255.255.0
!
interface vlan 30
 name GUEST
 ip address 10.20.30.1 255.255.255.0
!
interface GigabitEthernet1/0/1
 description to FortiGate-100F
 switchport access vlan 10
!
interface GigabitEthernet1/0/2
 description AP-RECEPTION
 switchport mode trunk
 switchport trunk native vlan 20
 service-acl input GUEST-BLOCK
!
interface GigabitEthernet1/0/3
 shutdown
!
interface TwentyFiveGigE1/0/1
 description to-QN-ACC1
 switchport mode trunk
!
interface TwentyFiveGigE2/0/1
 description to-QQ-ACC2
 switchport mode trunk
!
interface GigabitEthernet2/0/24
 description to-QN-ACC3
 switchport mode trunk
!
interface oob
 ip address 192.168.254.254 255.255.255.0
 no ip address dhcp
!
exit
ip default-gateway 10.20.10.254
{P}more system-info
*************************************************
*********   SWITCH SYSTEM INFORMATION   *********
*************************************************

Model Number : QN-SW-325-24
Serial Number : QNTEST0A0001
MAC Address : {QN_CORE_MAC}
Switch Firmware Version : 2.3.22.00
*************************************************
{P}sh system
System Description:                       24 GE 6x25G ports Managed Switch with , MacSec
System Name:                              QN-CORE-SERVER-ROOM-STK
System MAC Address:                       {QN_CORE_MAC}
System Object ID:                         1.3.6.1.4.1

Unit Type
---- ----------------------
 1        3824GS-6Y
 2        3824GS-6Y

{P}sh inv\x08 \x1b[D\x08 \x1b[Dnventory

NAME: "1"  DESCR: "24 GE 6x25G ports Managed Switch with , MacSec"
PID: 3824GS-6Y  VID:  0  SN: QNTEST0A0001
NAME: "TwentyFiveGigE1/0/1"  DESCR: "SFP 10G BASE-LR"
PID: QN-UTSPP-LR    VID: QUANTUM NETWORK    SN: QNSFPTEST001
NAME: "2"  DESCR: "24 GE 6x25G ports Managed Switch with , MacSec"
PID: 3824GS-6Y  VID:  0  SN: QNTEST0A0002
NAME: "TwentyFiveGigE2/0/1"  DESCR: "SFP 10G BASE-LR"
PID: QN-UTSPP-LR    VID: Quantum Networks    SN: QNSFPTEST002
sh int confi
{P}show cdp neighbors detail
% Unrecognized command
{P}{CORE_ADMIN}
{P}sh int status
{qn_status([("gi1/0/1", "1G-Copper", True, "1000"), ("gi1/0/2", "1G-Copper", True, "1000"), ("gi1/0/3", "1G-Copper", False, ""),
            ("gi2/0/24", "1G-Copper", True, "1000"), ("tf1/0/1", "25G-Fiber", True, "10000"), ("tf2/0/1", "25G-Fiber", True, "10000")])}
{P}sh lldp neigh
{qn_lldp([("gi1/0/2", "80:03:84:00:00:01", "80:03:84:00:00:01", "AP-RECEPTION", "B, W"),
          ("gi2/0/24", QN_ACC3_MAC, "gi1/0/12", "QN-ACC3", "B, R"),
          ("tf1/0/1", QN_ACC1_MAC, "te1/0/1", "QN-ACC1-STORE-BUILDING-A", "B, R"),
          ("tf2/0/1", QQ_ACC2_MAC, "Ethernet1/0/25", "QQ-ACC2", "B, R")])}

{P}sh spanning-tree

Spanning tree enabled mode: RSTP
Default port cost method:   long

  Root ID    Priority:   0
             Address:    {QN_CORE_MAC}
             This switch is the root
             Hello Time: 2 sec Max Age: 20 sec Forward Delay: 15 sec

Interfaces
  Name     State    Prio.Nbr   Cost    Sts   Role PortFast Type
--------- -------- --------- -------- ------ ---- -------- -----------------
 gi1/0/1  enabled    128.1    20000    Frw   Desg   No       P2P (RSTP)
 tf1/0/1  enabled   128.25     2000    Frw   Desg   No       P2P (RSTP)
 tf2/0/1  enabled   128.85     2000    Frw   Desg   No       P2P (RSTP)

{P}show version
show ip interface
Active-image: flash://system/images/QN_SW-AG-2.3.22.00.qntm
  Version: 2.3.22.00
  MD5 Digest: d41d8cd98f00b204e9800998ecf8427e
{P}

IP Address         I/F       I/F Status Type    Directed  Prec Redirect Status
                             admin/oper         Broadcast
------------------ --------- ---------- ------- --------- ---- -------- ------
10.20.10.1/24      vlan 10   UP/UP      Static  disable   No   enable   Valid
10.20.20.1/24      vlan 20   UP/UP      Static  disable   No   enable   Valid
10.20.30.1/24      vlan 30   UP/UP      Static  disable   No   enable   Valid
192.168.254.254/24 oob       UP/DOWN    Static  disable   No   enable   Valid

{P}sh ip route
Maximum Parallel Paths: 8 (8 after reset)
IP Forwarding: enabled
S   0.0.0.0/0 [1/4] via 10.20.10.254, 10:00:00, vlan 10
C   10.20.10.0/24 is directly connected, vlan 10
\x1b[K{P}exit
"""))

P = "QN-ACC1-STORE-BU...#"
w("QN-ACC1.txt", putty(f"""
QN-a00011#hos\x08 \x1b[Dt\x08 \x1b[D\x08 \x1b[D\x08 \x1b[Dconf t
QN-a00011(config)#hostname QN-ACC1-STORE-BUILDING-A
{P}terminal datadump
sh runn
\r{P}config-file-header
QN-ACC1-STORE-BUILDING-A
v7.2.7.14 / RLINUX_900_100
CLI v1.0
@
vlan database
vlan 10,20,30
exit
hostname QN-ACC1-STORE-BUILDING-A
username netadmin password encrypted 0123456789abcdef0123456789abcdef01234567 privilege 15
snmp-server community s4mpleComm rw view all
sntp server ntp.qntmnet.com poll
ip domain name example.local
ip name-server  10.20.10.5
!
interface vlan 10
 name MGMT
 ip address 10.20.10.11 255.255.255.0
 management-vlan
!
interface vlan 20
 name USERS
!
interface vlan 30
 name GUEST
!
interface GigabitEthernet1/0/2
 description AP-STORE-01
 switchport mode trunk
 switchport trunk native vlan 20
!
interface GigabitEthernet1/0/4
 description PC-STORE
 switchport access vlan 20
!
interface GigabitEthernet1/0/9
 description spare
 shutdown
 switchport access vlan 30
!
interface GigabitEthernet1/0/12
 description to-QQ-ACC2
 switchport mode trunk
!
interface TenGigabitEthernet1/0/1
 description to-core
 switchport mode trunk
!
exit
ip default-gateway 10.20.10.1
{P}more system-info
*************************************************
*********   SWITCH SYSTEM INFORMATION   *********
*************************************************

Model Number : QN-SW-225-12P
Serial Number : QNTEST0A0011
MAC Address : {QN_ACC1_MAC}
Switch Firmware Version : 2.3.20.00
*************************************************
{P}sh inventory

NAME: "1"  DESCR: "12 GE 2x10G ports Managed Switch with POE"
PID: 2612GP-2XGF  VID:  0  SN: QNTEST0A0011
NAME: "TenGigabitEthernet1/0/1"  DESCR: "SFP 10G BASE-LR"
PID: QN-UTSPP-LR    VID: QUANTUM NETWORK    SN: QNSFPTEST011
{P}sh int status
{qn_status([("gi1/0/2", "1G-Copper", True, "1000"), ("gi1/0/4", "1G-Copper", True, "100"), ("gi1/0/9", "1G-Copper", False, ""),
            ("gi1/0/12", "1G-Copper", True, "1000"), ("te1/0/1", "10G-Fiber", True, "10000")])}
{P}sh int confi
{qn_admin([("gi1/0/2", "1G-Copper", "Up"), ("gi1/0/9", "1G-Copper", "Down"), ("gi1/0/12", "1G-Copper", "Up")])}
{P}sh lldp neigh
{qn_lldp([("gi1/0/2", "80:03:84:00:00:02", "80:03:84:00:00:02", "AP-STORE-01", "B, W"),
          ("gi1/0/12", QQ_ACC2_MAC, "Ethernet1/0/26", "QQ-ACC2", "B, R"),
          ("te1/0/1", QN_CORE_MAC, "tf1/0/1", "QN-CORE-SERVER-ROOM-STK", "B, R")])}

{P}sh spanning-tree

Spanning tree enabled mode: RSTP

  Root ID    Priority:   0
             Address:    {QN_CORE_MAC}
             Cost:       2000
             Port:       te1/0/1
  Bridge ID  Priority:   32768
             Address:    {QN_ACC1_MAC}

Interfaces
  Name     State    Prio.Nbr   Cost    Sts   Role PortFast Type
--------- -------- --------- -------- ------ ---- -------- -----------------
 gi1/0/2  enabled    128.2    20000    Frw   Desg   Yes      P2P (RSTP)
gi1/0/12  enabled   128.12    20000    Blk   Altn    No      P2P (RSTP)
 te1/0/1  enabled   128.49     2000    Frw   Root    No      P2P (RSTP)

{P}sh ver
Active-image: flash://system/images/QN_SW-AG-2.3.20.00.qntm
  Version: 2.3.20.00
{P}exit
"""))

P = "QQ-ACC2#"
w("QQ-ACC2.txt", putty(f"""
{P}ter length 0
{P}sh running-config
!
service password-encryption
!
hostname QQ-ACC2
sysLocation Store
sysContact Default
!
enable password level 15 7 00112233445566778899aabbccddeeff
username admin privilege 15 password 7 00112233445566778899aabbccddeeff
!
spanning-tree mode RSTP
!
vlan 1;20;30
!
vlan 10
 name MGMT
!
Interface Ethernet1/0/1
 description PC-01
 switchport access vlan 20
!
Interface Ethernet1/0/25
 description to-QN-CORE
 switchport mode trunk
!
Interface Ethernet1/0/26
 description to-QN-ACC1
 switchport mode trunk
!
interface Vlan10
 ip address 10.20.10.12 255.255.255.0
!
ip route 0.0.0.0/0 10.20.10.1
!
sntp server 10.20.10.5 source vlan 10
!
{P}sh version
  Device: QQ-SW-155-G24P-4SFPP, sysLocation: Store.
  CPU MAC          58-61-63-A0-00-12
  VLAN MAC         58-61-63-A0-00-13
  SoftWare Version QN_SW_QQ-2.6.03.24
  BootRom Version  V2.00
  Serial Num:QNTEST0A0012
{P}sh lldp nei
% Incomplete command.
{P}sh vlan
VLAN Name         Type       Media     Ports
---- ------------ ---------- --------- ----------------------------------------
1    default      Static     ENET      Ethernet1/0/25(T)   Ethernet1/0/26(T)
10   MGMT         Static     ENET      Ethernet1/0/25(T)   Ethernet1/0/26(T)
20   VLAN0020     Static     ENET      Ethernet1/0/1       Ethernet1/0/25(T)
                                       Ethernet1/0/26(T)
{P}sh spanning-tree
                 -- RSTP Bridge Config Info --
Standard     :  IEEE 802.1w
Bridge MAC   :  {QQ_ACC2_MAC}
#############################################################
Self Bridge Id   : 32768.{QQ_ACC2_MAC}
Root Id          : 0.{QN_CORE_MAC}
Ext.RootPathCost : 2000
Root Port ID     : 128.025

   PortName       ID     Port Path Cost    ExtRPC  State Role     DsgBridge      DsgPort
-------------- -------- ---------------- --------- ----- ---- ------------------ -------
 Ethernet1/0/1 128.001            20000       2000   FWD DSGN 32768.586163a00012 128.001
Ethernet1/0/25 128.025             2000          0   FWD ROOT     0.586163a00001 128.085
Ethernet1/0/26 128.026            20000       2000   FWD DSGN 32768.586163a00012 128.026
{P}sh interface
Interface brief:
  Ethernet1/0/1 is up, line protocol is up
  Ethernet1/0/1 is layer 2 port, alias name is PC-01, index is 1
  Hardware is Gigabit-TX, address is 58-61-63-A0-00-12
  Auto-duplex: Negotiation full-duplex, Auto-speed: Negotiation 1G bits
Interface brief:
  Ethernet1/0/2 is down, line protocol is down
  Ethernet1/0/2 is layer 2 port, alias name is (null), index is 2
  Hardware is Gigabit-TX, address is 58-61-63-A0-00-12
Interface brief:
  Ethernet1/0/25 is up, line protocol is up
  Ethernet1/0/25 is layer 2 port, alias name is to-QN-CORE, index is 25
  Hardware is SFP+, address is 58-61-63-A0-00-12
  Auto-duplex: Negotiation full-duplex, Auto-speed: Negotiation 10G bits
Interface brief:
  Ethernet1/0/26 is up, line protocol is up
  Ethernet1/0/26 is layer 2 port, alias name is to-QN-ACC1, index is 26
  Hardware is Gigabit-TX, address is 58-61-63-A0-00-12
{P}exit
"""))

# collector-style headers and no running-config: VLANs, port modes and the IP come from show output only
w("QN-ACC3.txt", f"""
########## show system ##########
System Description:                       12 GE 2x10G ports Managed Switch with POE
System Name:                              QN-ACC3
System MAC Address:                       {QN_ACC3_MAC}

########## show inventory ##########
NAME: "1"  DESCR: "12 GE 2x10G ports Managed Switch with POE"
PID: 2612GP-2XGF  VID:  0  SN: QNTEST0A0003

########## show vlan ##########
{qn_vlan([(1, "1", [], ["gi1/0/1-2,", "gi1/0/12"]), (10, "***Network-MGMT***", ["gi1/0/12"], []),
          (20, "***Barcode_Scanner***", ["gi1/0/12"], ["gi1/0/3-5"])])}

########## show ip interface ##########
IP Address         I/F       I/F Status Type    Directed  Prec Redirect Status
------------------ --------- ---------- ------- --------- ---- -------- ------
10.20.10.13/24     vlan 10   UP/UP      Static  disable   No   enable   Valid

########## show interface status ##########
{qn_status([("gi1/0/3", "1G-Copper", True, "1000"), ("gi1/0/12", "1G-Copper", True, "1000")])}

########## show lldp neighbors ##########
{qn_lldp([("gi1/0/12", QN_CORE_MAC, "gi2/0/24", "QN-CORE-SERVER-ROOM-STK", "B, R")])}
""")

# Cisco Business 350: the same Sx350-style CLI, so it is read by the same parser but reported as Cisco
w("CBS350-24P.txt", """
config-file-header
CBS-LAB
v3.5.0.24 / RCBS3.5_900_100
CLI v1.0
file SSD indicator encrypted
@
vlan database
vlan 10
exit
hostname CBS-LAB
interface vlan 10
 name MGMT
 ip address 10.20.10.40 255.255.255.0
!
interface GigabitEthernet1
 description uplink
 switchport mode trunk
!
exit
ip default-gateway 10.20.10.1
CBS-LAB#show inventory
NAME: "1"  DESCR: "CBS350-24P-4G 24-Port Gigabit PoE Managed Switch"
PID: CBS350-24P-4G       VID: V02      SN: FOC0000X0AB
CBS-LAB#show system
System Description:                       CBS350-24P-4G 24-Port Gigabit PoE Managed Switch with 4-Port 1G SFP
System MAC Address:                       e8:0a:b9:00:00:40
CBS-LAB#show interfaces status
                                             Flow Link          Back   Mdix
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi2      1G-Copper      --      --     --     --  Down           --     --
""")
print("samples written to", os.path.abspath(OUT))
