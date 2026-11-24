# SwitchMap

Upload switch backups (text or .zip) -> get a 5-sheet Excel inventory (Switches, Access Points, Connected Devices, Firewalls, Summary) and an interactive topology diagram.

## Run
```
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
python tests/generate_samples.py && python tests/test_pipeline.py   # sample backups + self-test
```
Use one worker if you deploy with gunicorn (analyses are kept in memory): `gunicorn -w 1 app:app`.

## Layout (one file per feature)
| File | Job |
|---|---|
| `app.py` | Flask routes: analyse, download Excel, download diagram (Visio, SVG; PNG is drawn in the browser) |
| `core/loader.py` | read uploads, unzip |
| `core/detector.py` | vendor auto-detection |
| `core/utils.py` | MAC/IP/port normalising, splitting `show` sections from a backup, replaying raw PuTTY / terminal logs |
| `core/models.py` | Switch, StackMember, Interface, Neighbor, Component |
| `core/parsers/*.py` | one parser per vendor: `cisco`, `aruba_os` (ProCurve), `aruba_cx`, `alcatel`, `tplink`, `dlink`, `comware` (HPE/H3C), `quantum` (Quantum Networks, also used for Cisco CBS / Sx350), `generic` fallback |
| `core/firewalls.py` | firewall vendor rules (Palo Alto, Fortinet, Sophos, Cisco ASA/FTD, Check Point, SonicWall, WatchGuard, Juniper SRX, pfSense, ...). Add a row to `RULES` for another OEM |
| `core/parts.py` | vendor-neutral parts classifier + inventory scanner (SFPs, modules, PSUs, fans, member MACs for any OEM) |
| `core/stdconfig.py` | AAA / NTP / SNMP / DNS / domain extraction and the fleet-wide baseline comparison |
| `core/l3.py` | Layer-3 view: SVIs, static routes, OSPF/EIGRP/BGP/RIP/IS-IS, ACLs, SNMP traps, DHCP pools and relay |
| `core/audit.py` | per-switch checks: Telnet/HTTP, default SNMP community, plaintext passwords, undefined VLANs, err-disabled ports, ACL and route problems |
| `core/findings.py` | the Misconfigurations box: audit results + link/port-channel/STP/VLAN/firmware checks + standard-config deviations |
| `core/analysis.py` | links between switches, STP-blocked ports, access-point inventory, summary |
| `core/layered.py` | layered layout and collision-free cable routing |
| `core/excel_export.py` | Switches / Access Points / Connected Devices / Summary sheets (merged cells, live COUNTIF formulas) |
| `core/diagram_export.py` | the single SVG renderer: the on-screen diagram and the SVG/PNG downloads are identical |
| `core/freeroute.py` | cable routing once a switch has been dragged anywhere: cables go around the switches, never through them or on top of each other |
| `core/visio_export.py` | the diagram as a Visio drawing (.vsdx): same layout, switches as groups with Shape Data, cables glued as connectors |
| `static/js/*.js` | `upload`, `dashboard`, `compliance` (standard config), `diagram`, `panel`, `panel_l3` (core switch details) |

## What a backup must contain
A running-config alone has **no** serial numbers, MACs, port up/down state, STP blocking state or neighbours.
Append the output of these commands to each backup (Cisco names shown; other vendors use their equivalents):
`show version`, `show switch`, `show inventory`, `show vlan brief`, `show interfaces status`,
`show spanning-tree`, `show cdp neighbors detail`, `show lldp neighbors detail`, `show ip dhcp snooping binding`.
The Dashboard lists exactly what was missing per switch.

## Quantum Networks (qntmnet.com)
Two firmware families are read by `core/parsers/quantum.py`:
- **QN-SW / QN-IS** (QN-SW-225-12P, QN-SW-325-24FP, QN-SW-325-48 ...): Sx350-style CLI, ports `gi1/0/1`, `te1/0/1`, `tf1/0/1` (25G).
- **QQ-SW** (QQ-SW-155-G24P-4SFPP ...): `Interface Ethernet1/0/1`, `show version` with Device / CPU MAC / Serial Num.

Collect (abbreviations are fine): `terminal datadump`, `show running-config`, `more system-info`, `show system`, `show inventory`,
`show stack` (stacks), `show version`, `show vlan`, `show interfaces status`, `show interfaces configuration`, `show interfaces description`,
`show lldp neighbors`, `show spanning-tree`, `show ip interface`, `show ip route`. On QQ-SW add `show interface` (port up/down);
QQ-SW does not answer `show lldp neighbors` without an interface, so its links come from the neighbouring switches' LLDP.

Raw PuTTY logs work as they are: the log is replayed like a terminal (backspaces, ANSI codes, CR), cut at every prompt and each
table is recognised by its header, so typed-ahead commands, lost echoes and output glued to the prompt do not matter.
Wrapped LLDP / VLAN names are joined back together. The model comes from `more system-info` (e.g. QN-SW-325-24FP), the
hardware PID (3824GS-6Y) is used when that is missing. Stack roles come from `show stack` (active / standby / member, or
master / backup / slave on older firmware). Without it, for a 2-unit stack the unit `more system-info` describes is shown as
master and the other as backup, and the switch details say these roles are inferred.
Cisco Business / Small Business switches (CBS250/350, SG250/350/550) use the same CLI and are read by the same parser, reported as Cisco.

## How things are decided
- **Links**: CDP/LLDP neighbours matched to uploaded switches by hostname, IP or chassis MAC; if a port has no neighbour entry, its description (e.g. "to ACC-SW1") is used.
- **Fibre vs UTP**: from the port type in `show interfaces status` / SFP in inventory (SFP, optical and DAC = dark yellow; RJ45 = blue).
- **Blocked port**: STP role Altn/Back or state BLK/Blocking/Discarding -> orange dotted.
- **Management IP**: the interface used as SNMP/syslog/NTP source, else one named mgmt, else the one containing the default gateway, else lowest VLAN.
- **AP IP "DHCP"**: shown when the AP's IP is in a DHCP snooping/lease table or inside a DHCP pool of an uploaded switch; otherwise "Static". This is a heuristic.
- **AP subnet/gateway**: taken from a switch SVI that contains the AP's IP; otherwise N/A.
- APs are detected from CDP/LLDP (wireless capability / AP platform) or port descriptions containing "AP".

## Firewalls
- Detected from CDP/LLDP (name, platform, system description) and from switch port descriptions such as "to FortiGate-100F" / "SOPHOS-XGS2300 LAN".
- A MAC-prefix match (Palo Alto / Fortinet OUIs) is used only as a last hint and is marked "verify". The prefix lists are partial.
- Drawn in the diagram as red-outlined "FIREWALL" boxes above the switch they connect to; listed on the Firewalls sheet.
- Hosts (PCs, servers, phones, printers) are never listed. Firewall config backups themselves are not parsed.
- Two switches describing the same firewall model on a port are treated as one firewall (typical HA pair). Give the ports distinct names if they are separate boxes.

## Parts and MACs for non-Cisco vendors
After the vendor parser runs, a generic scan reads any inventory-like `show` / `display` output (key: value blocks and column tables with a Serial column).
It is best-effort: it can only find what the backup actually contains. The Dashboard shows MACs read / Serials read / Parts listed for every switch.

## Port-channels
- Read from `channel-group` / `show etherchannel summary` (Cisco, TP-Link), `channel-group` (Quantum QN-SW, Cisco CBS) and `port-group` (Quantum QQ-SW), `trunk ... trkN` (Aruba ProCurve), `lag N` (AOS-CX) and Bridge-Aggregation (H3C). Alcatel linkagg and D-Link link aggregation are not parsed yet.
- Member links between the same two switches are drawn as ONE double-rail link labelled `Po1` / `Po2` with `x2`; hover lists the member ports. The switch panel has a Port-channels table with LACP/PAgP and member flags.

## Standard configuration (Dashboard)
Baseline = the most common value across the uploaded switches for AAA, NTP, SNMP, DNS and domain name. Each switch is marked match / differs / missing.
SNMP community strings are never displayed, only their access level and a 6-character identifier so equal strings can be recognised.

## STP root bridge
Read from `show spanning-tree` (Cisco, AOS-CX), CST root lines (ArubaOS-Switch) or `display stp` (H3C). The root MAC is matched to an uploaded switch; the hover shows who the root is and the diagram marks it with a ROOT badge.
If different VLANs have different roots, the hover lists them per VLAN.

## Core switch details (click a switch)
SVIs / routed ports / loopbacks (relay, ACLs, HSRP/VRRP virtual IP), routing protocols and static routes (default route highlighted), ACLs with where they are applied, SNMP trap receivers and enabled traps, DHCP pools and `ip helper-address`.
Parsed for Cisco-style CLI (Cisco, Aruba, TP-Link, Quantum). Other vendors show a note. Layer-3 sections open by default on switches that route.

## Diagram
- Tiers: firewalls on top, then core / L3 / root switches, then distribution and access. Lines are drawn as elbows; every link has its own port label at both ends.
- Port-channels show the bundle name AND every member port at each end (for example `Po1` over `Gi1/0/1`, `Gi2/0/3`), drawn as a double line.
- A blocked port has an orange dotted line and a BLK tag on the blocked end. Fibre/SFP is yellow, UTP is blue.
- Drag a switch to move it; its lines are re-routed when you release it. Use Fit, + and - or Find switch for large networks.

## Misconfigurations (Dashboard)
Severity High / Medium / Low / Info, filterable by category and text. Examples: native VLAN or duplex/speed mismatch between two uploaded switches,
trunk vs access, port-channel protocol mismatch or one-sided bundles, inactive bundle members, Telnet on VTY, HTTP server, default SNMP community,
`enable password`, undefined VLANs, error-disabled ports, ACLs applied but not defined, unreachable static-route next hops,
stack members not Ready, mixed STP modes, root bridge not on the core, VLAN names or firmware that differ between switches,
and every deviation from the standard configuration. Checks that need a command missing from the backup simply do not run.

## Zooming the diagram
Map-style view: mouse wheel (or trackpad pinch) zooms around the pointer from the whole network up to 1200%; drag the background to pan;
`+` / `-` / `Fit` / the percentage button (click for 100%) in the toolbar; keyboard when the diagram is focused: `+` `-` `0` (fit) `1` (100%) and the arrow keys.
A small overview map (bottom-left, shown for 8+ switches) shows where you are; click or drag it to jump. "Find switch" centres on a switch at 100% or more.
The text is vector, so it stays sharp at any zoom. Busy switches stagger their port labels over several rows so they never overlap.

## Interface configuration on hover
In a switch's detail panel, hover (or tab to) any port chip to see its status details (speed, duplex, type, mode, VLAN, port-channel) and the port's own configuration lines from the backup.
Read for Cisco, Aruba (ProCurve and AOS-CX), H3C Comware, TP-Link and Quantum interface blocks, and for Alcatel by collecting the lines that mention the port.
Passwords, secrets, key-strings, OSPF/HSRP keys and SNMP community strings are masked (`********`) before they reach the browser.
A port with no configuration lines in the backup is shown as being at default settings.

## Diagram layout (rings and large networks)
`core/layered.py` places switches in tiers and routes every cable itself, so a ring or a 100-switch network stays readable:
- Tiers by hop distance from the core (firewalls above it). A redundant core shares the core's tier when both configs show them as L3 switches.
- A cable that skips a tier gets its own lane, so it never runs through a switch.
- Tier order is chosen by barycentre sweeps plus adjacent swaps, keeping the ordering with the fewest crossings.
- Every cable end gets its own anchor point on the switch edge, and every horizontal run its own track, so **no two cables share a line and no cable crosses a switch box**.
- A ring closes either as a short link between two neighbouring switches or as a clean U under the tier.
- Dragging a switch moves it anywhere. A small sideways move keeps the tidy tiered layout; any other move keeps every switch where it is and re-routes all cables around them. Switching tabs and back resets the layout; hovering a switch highlights its cables and dims the rest.

`tests/test_scale.py` checks this geometrically on rings, ring-of-rings, a dual-homed campus, random meshes and a 150-switch tree:
it fails if any two cables overlap, any cable passes through a switch, or any two switches overlap.

## Credits
SwitchMap is designed and developed by **Pratham Chokshi**.
