"""Build the inventory workbook: Switches, Access Points, Connected Devices, Firewalls, Summary."""
from __future__ import annotations
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"
HDR_FILL = PatternFill("solid", fgColor="0B5563")
SEC_FILL = PatternFill("solid", fgColor="DCEBEE")
THIN = Side(style="thin", color="B7C4CA")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _header(ws, headers):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = HDR_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"


_DATA_FONT = Font(name=FONT, size=10)
_LEFT = Alignment(vertical="center", horizontal="left")
_CENTER = Alignment(vertical="center", horizontal="center")


def _style(ws, first_row, last_row, ncols, merged_cols=()):
    """Shared style objects, no text wrapping and a fixed row height: Excel then never has to auto-fit rows on open."""
    for r in range(first_row, last_row + 1):
        ws.row_dimensions[r].height = 18
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = _DATA_FONT
            cell.border = BORDER
            cell.alignment = _CENTER if c in merged_cols else _LEFT


def _merge(ws, cols, r1, r2, enabled=True):
    """Merge each column in `cols` between rows r1..r2 (values already written in the top cell)."""
    if r2 <= r1 or not enabled:
        return
    for c in cols:
        ws.merge_cells(start_row=r1, start_column=c, end_row=r2, end_column=c)


def _widths(ws, ncols, minimum=12, maximum=48):
    longest = [0] * ncols
    for row in ws.iter_rows(values_only=True):
        for c, v in enumerate(row[:ncols]):
            if v is not None and len(str(v)) > longest[c]:
                longest[c] = len(str(v))
    for c in range(ncols):
        ws.column_dimensions[get_column_letter(c + 1)].width = max(minimum, min(maximum, longest[c] + 3))


def _group_runs(rows, key):
    """Yield (start_index, end_index) of consecutive rows sharing key(row)."""
    start = 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or key(rows[i]) != key(rows[start]):
            yield start, i - 1
            start = i


def build_workbook(analysis, merge: bool = True) -> bytes:
    wb = Workbook()

    # ------------------------------------------------------------ 1. Switches
    ws = wb.active
    ws.title = "Switches"
    heads = ["Make (Vendor)", "Model", "Serial Number", "MAC Address", "IP Address", "Subnet", "Gateway", "Hostname", "Stack Role"]
    _header(ws, heads)
    row = 2
    for sw in sorted(analysis.switches, key=lambda s: s.hostname.lower()):
        start = row
        for m in sw.members:
            role = "Standalone" if not sw.is_stack else f"Member {m.index} - {m.role}" + (f" (priority {m.priority})" if m.priority else "")
            ws.append([sw.vendor, m.model or "Unknown", m.serial or "N/A", m.mac or "N/A", None, None, None, None, role])
            row += 1
        shared = (sw.mgmt_ip or "N/A", sw.subnet or "N/A", sw.gateway or "N/A", sw.hostname)
        for r_ in range(start, row if not merge else start + 1):       # without merging, repeat the values on every member row
            for off, val in enumerate(shared):
                ws.cell(row=r_, column=5 + off, value=val)
        _style(ws, start, row - 1, len(heads), merged_cols=(5, 6, 7, 8) if merge else ())
        _merge(ws, (5, 6, 7, 8), start, row - 1, merge)  # a stack shares one IP / subnet / gateway / hostname
    _widths(ws, len(heads))
    sw_last = max(ws.max_row, 2)

    # --------------------------------------------------------- 2. Access Points
    wa = wb.create_sheet("Access Points")
    heads = ["Make (Vendor)", "Model", "Hostname", "IP Address", "Subnet", "Gateway", "Switch Port", "Switch IP", "Switch Hostname"]
    _header(wa, heads)
    aps = analysis.aps
    for a in aps:
        wa.append([a["make"], a["model"], a["hostname"], a["ip_display"], a["subnet"], a["gateway"], a["port"], a["switch_ip"], a["switch_hostname"]])
    if aps:
        _style(wa, 2, len(aps) + 1, len(heads), merged_cols=(8, 9) if merge else ())
        for s, e in _group_runs(aps, lambda a: a["switch_id"]):
            _merge(wa, (8, 9), s + 2, e + 2, merge)
    _widths(wa, len(heads))
    ap_last = max(wa.max_row, 2)

    # --------------------------------------------------- 3. Connected devices
    wd = wb.create_sheet("Connected Devices")
    heads = ["Type", "Make (Vendor)", "Model", "Serial Number", "Switch Port / Slot", "Description", "Switch IP", "Switch Hostname"]
    _header(wd, heads)
    comps = sorted(analysis.component_rows(), key=lambda r: (r["switch_hostname"].lower(), r["port"]))
    for r in comps:
        wd.append([r["kind"], r["make"], r["model"], r["serial"], r["port"], r["description"], r["switch_ip"], r["switch_hostname"]])
    if comps:
        _style(wd, 2, len(comps) + 1, len(heads), merged_cols=(7, 8) if merge else ())
        for s, e in _group_runs(comps, lambda r: r["switch_id"]):
            _merge(wd, (7, 8), s + 2, e + 2, merge)
    _widths(wd, len(heads))
    dev_last = max(wd.max_row, 2)

    # ------------------------------------------------------------ 4. Firewalls
    wf = wb.create_sheet("Firewalls")
    heads = ["Device ID", "Make (Vendor)", "Model", "Hostname", "IP Address", "MAC Address", "Switch Port", "Firewall Port",
             "Switch IP", "Switch Hostname", "Detected From"]
    _header(wf, heads)
    fws = analysis.firewalls
    for r in fws:
        wf.append([r["fw_id"], r["make"], r["model"] or "Unknown", r["hostname"], r["ip"], r["mac"], r["switch_port"], r["firewall_port"],
                   r["switch_ip"], r["switch_hostname"], r["detected_from"]])
    if fws:
        _style(wf, 2, len(fws) + 1, len(heads), merged_cols=(9, 10) if merge else ())
        for s_, e_ in _group_runs(fws, lambda r: r["switch_id"]):
            _merge(wf, (9, 10), s_ + 2, e_ + 2, merge)
    _widths(wf, len(heads))
    fw_last = max(wf.max_row, 2)

    # ------------------------------------------------------------ 5. Summary
    wsu = wb.create_sheet("Summary")
    heads = ["Category", "Make / Type", "Model", "Count"]
    _header(wsu, heads)
    sm = analysis.summary()
    r = 2
    totals = []

    def section(title, entries, formula):
        nonlocal r
        if not entries:
            return
        first = r
        for make, model in entries:
            wsu.cell(row=r, column=1, value=title)
            wsu.cell(row=r, column=2, value=make)
            wsu.cell(row=r, column=3, value=model)
            wsu.cell(row=r, column=4, value=formula(r))
            r += 1
        _style(wsu, first, r - 1, 4)
        wsu.cell(row=r, column=1, value=f"Total {title.lower()}")
        wsu.cell(row=r, column=4, value=f"=SUM(D{first}:D{r - 1})")
        for c in range(1, 5):
            cell = wsu.cell(row=r, column=c)
            cell.font, cell.fill, cell.border = Font(name=FONT, bold=True, size=10), SEC_FILL, BORDER
        totals.append(r)
        r += 2

    section("Switch", [(x["make"], x["model"]) for x in sm["switch_models"]],
            lambda i: f"=COUNTIF(Switches!$B$2:$B${sw_last},C{i})")
    section("Access point", [(x["make"], x["model"]) for x in sm["ap_models"]],
            lambda i: f"=COUNTIF('Access Points'!$B$2:$B${ap_last},C{i})")
    section("Connected device", [(x["kind"], x["model"]) for x in sm["component_models"]],
            lambda i: f"=COUNTIFS('Connected Devices'!$A$2:$A${dev_last},B{i},'Connected Devices'!$C$2:$C${dev_last},C{i})")
    section("Firewall", [(x["make"], x["model"]) for x in sm["firewall_models"]],
            lambda i: f"=SUMPRODUCT((Firewalls!$C$2:$C${fw_last}=C{i})/COUNTIF(Firewalls!$A$2:$A${fw_last},Firewalls!$A$2:$A${fw_last}))")
    if totals:
        wsu.cell(row=r, column=1, value="Grand total")
        wsu.cell(row=r, column=4, value="=" + "+".join(f"D{t}" for t in totals))
        for c in range(1, 5):
            cell = wsu.cell(row=r, column=c)
            cell.font, cell.border = Font(name=FONT, bold=True, size=10), BORDER
    wsu.cell(row=r + 2, column=1, value="Counts are live formulas over the other sheets; every stack member counts as one switch.").font = Font(
        name=FONT, italic=True, size=9, color="555555")
    _widths(wsu, 4)

    buf = io.BytesIO()
    wb.calculation.fullCalcOnLoad = True
    wb.save(buf)
    return buf.getvalue()
