"""Topology diagram as a Microsoft Visio drawing (.vsdx).

Same layout, colours and port labels as the on-screen diagram (it uses the same layout and label code as diagram_export.py).
In Visio:
  * every switch / firewall is a group (box + badges + its port labels), so it moves as one piece;
  * every cable is a connector glued to the switch at both ends, so cables stay attached when a switch is moved;
  * every switch carries Shape Data (right-click > Data > Shape Data): hostname, IP, vendor, model, firmware, STP, root bridge.

A .vsdx file is a zip of XML parts (Open Packaging Conventions). Units inside are inches; 96 diagram pixels = 1 inch,
so text and line sizes match the SVG. Visio's y axis points up, the SVG's points down, so every y is flipped.
"""
from __future__ import annotations
import io
import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _esc

from .diagram_export import (layout, bounds, end_labels, side_counts, FIBER, UTP, BLOCKED, INK, ACCENT, MUTED,
                             FW_STROKE, FW_FILL)
from .layered import NODE_W, NODE_H

DPI = 96.0
NS = "http://schemas.microsoft.com/office/visio/2012/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FONT, MONO = "Segoe UI", "Consolas"
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def esc(v) -> str:
    return _esc(_BAD_XML.sub("", str(v if v is not None else "")), {'"': "&quot;"})


def _in(px: float) -> str:
    return f"{px / DPI:.4f}"


def _pt(pt: float) -> str:            # font sizes are stored in inches
    return f"{pt / 72:.4f}"


class _Page:
    def __init__(self, minx, maxy):
        self.minx, self.maxy = minx, maxy
        self.next_id = 1
        self.shapes, self.connects = [], []

    def X(self, x):                    # diagram px -> page inches
        return (x - self.minx) / DPI

    def Y(self, y):
        return (self.maxy - y) / DPI

    def nid(self):
        self.next_id += 1
        return self.next_id - 1


def _cells(**kv) -> str:
    out = []
    for k, v in kv.items():
        if v is None:
            continue
        if isinstance(v, tuple):       # (value, formula) or (value, formula, unit)
            val, f = v[0], v[1]
            unit = f' U="{v[2]}"' if len(v) > 2 else ""
            form = f' F="{esc(f)}"' if f else ""
            out.append(f'<Cell N="{k}" V="{esc(val)}"{form}{unit}/>')
        else:
            out.append(f'<Cell N="{k}" V="{esc(v)}"/>')
    return "".join(out)


def _char(rows) -> str:
    """rows: [(font, size_pt, colour, bold)]"""
    r = "".join(f'<Row IX="{i}"><Cell N="Font" V="{esc(f)}"/><Cell N="Color" V="{c}"/><Cell N="Style" V="{1 if b else 0}"/>'
                f'<Cell N="Size" V="{_pt(s)}" U="PT"/></Row>' for i, (f, s, c, b) in enumerate(rows))
    return f'<Section N="Character">{r}</Section>'


def _para(align: int) -> str:          # 0 left, 1 centre, 2 right
    return f'<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="{align}"/></Row></Section>'


def _rect_geom(fill=True, line=True) -> str:
    rows = [("RelMoveTo", 0, 0), ("RelLineTo", 1, 0), ("RelLineTo", 1, 1), ("RelLineTo", 0, 1), ("RelLineTo", 0, 0)]
    r = "".join(f'<Row T="{t}" IX="{i + 1}"><Cell N="X" V="{x}"/><Cell N="Y" V="{y}"/></Row>' for i, (t, x, y) in enumerate(rows))
    return (f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="{0 if fill else 1}"/><Cell N="NoLine" V="{0 if line else 1}"/>'
            f'<Cell N="NoShow" V="0"/><Cell N="NoSnap" V="0"/>{r}</Section>')


def _box(sid, name, cx, cy, w, h, *, fill="#FFFFFF", line="#000000", weight=1.0, rounding=0.0, dash=False,
         text="", chars=None, align=1, valign=1, no_fill=False, no_line=False, margin=2.0, extra="") -> str:
    """A 2-D shape. cx, cy, w, h in inches (centre, size); weight in px; rounding in px."""
    c = _cells(PinX=f"{cx:.4f}", PinY=f"{cy:.4f}", Width=f"{w:.4f}", Height=f"{h:.4f}",
               LocPinX=(f"{w / 2:.4f}", "Width*0.5"), LocPinY=(f"{h / 2:.4f}", "Height*0.5"), Angle=0, FlipX=0, FlipY=0, ResizeMode=0,
               LineWeight=_in(weight), LineColor=line, LinePattern=2 if dash else 1,
               Rounding=_in(rounding), FillForegnd=fill, FillBkgnd=fill, FillPattern=0 if no_fill else 1,
               LeftMargin=_in(margin), RightMargin=_in(margin), TopMargin=_in(margin), BottomMargin=_in(margin),
               VerticalAlign=valign)
    body = c + extra + (_char(chars) if chars else "") + _para(align) + _rect_geom(fill=not no_fill, line=not no_line)
    if text:
        body += f"<Text>{text}</Text>"
    return f'<Shape ID="{sid}" NameU="{esc(name)}" Name="{esc(name)}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">{body}</Shape>'


def _label(pg, sid, x_px, y_px, text, anchor, size=10.5 * 0.75, bold=False, colour=INK, font=MONO, origin=(0.0, 0.0), halo=False):
    """A text-only shape placed like an SVG <text>: x is the start / middle / end of the text, y its baseline (diagram px)."""
    px = size / 0.75                                   # font size in diagram px
    w_px = max(16.0, len(text) * px * 0.66 + 10)       # generous so Visio never wraps it; transparent, so it never hides a neighbour
    h_px = px + 4
    left = {"start": x_px - 2, "middle": x_px - w_px / 2, "end": x_px - w_px + 2}[anchor]
    cx, cy = pg.X(left + w_px / 2) - origin[0], pg.Y(y_px - size / 0.75 * 0.35) - origin[1]
    return _box(sid, f"Label.{sid}", cx, cy, w_px / DPI, h_px / DPI, fill="#FFFFFF", no_fill=not halo, no_line=True, margin=0.5,
                text=esc(text), chars=[(font, size, colour, bold)], align={"start": 0, "middle": 1, "end": 2}[anchor])


def _props(rows) -> str:
    r = "".join(f'<Row N="{esc(re.sub(r"[^A-Za-z0-9_]", "_", k))}"><Cell N="Value" V="{esc(v)}" U="STR"/><Cell N="Label" V="{esc(k)}"/>'
                f'<Cell N="Type" V="0"/></Row>' for k, v in rows if v not in (None, ""))
    return f'<Section N="Property">{r}</Section>' if r else ""


def _switch_group(pg, n, x, y, labels):
    """One switch / firewall as a Visio group: box, stack shadow, badges, three text lines, plus its port labels."""
    gid = pg.nid()
    fw = n.get("kind") == "firewall"
    ext = n.get("external")
    stroke = FW_STROKE if fw else (MUTED if ext else ACCENT)
    fill = FW_FILL if fw else "#FFFFFF"
    gx0, gy0 = pg.X(x), pg.Y(y + NODE_H)                 # group's bottom-left corner on the page
    w, h = NODE_W / DPI, NODE_H / DPI
    org = (gx0, gy0)
    kids = []

    def local(px_x, px_y):                                # diagram px -> inches inside the group
        return pg.X(px_x) - gx0, pg.Y(px_y) - gy0

    if n.get("is_stack"):
        cx, cy = local(x + 6 + NODE_W / 2, y + 6 + NODE_H / 2)
        kids.append(_box(pg.nid(), "Stack", cx, cy, w, h, fill="#DCEBEE", line=stroke, weight=1.5, rounding=6))
    sub = ((n["vendor"] if str(n.get("model") or "~").lower() in str(n["hostname"]).lower() else f'{n["vendor"]} {n.get("model") or ""}'.strip())
           if fw else str(n.get("model") or "")) + (f' - stack of {n["members"]}' if n.get("is_stack") else "")
    text = f'<cp IX="0"/>{esc(str(n["hostname"])[:40])}\n<cp IX="1"/>{esc(n.get("ip") or "")}\n<cp IX="2"/>{esc(sub[:48])}'
    kids.append(_box(pg.nid(), "Box", w / 2, h / 2, w, h, fill=fill, line=stroke, weight=2, rounding=6, dash=bool(ext), text=text,
                     chars=[(FONT, 14 * 0.75, INK, True), (MONO, 12 * 0.75, INK, False), (FONT, 10 * 0.75, MUTED, False)], margin=3))
    if fw:
        kids.append(_label(pg, pg.nid(), x + 8, y + 13, "FIREWALL", "start", size=9 * 0.75, bold=True, colour=FW_STROKE, font=FONT, origin=org, halo=False))
    if n.get("is_root"):
        cx, cy = local(x + NODE_W - 27, y + 11)
        kids.append(_box(pg.nid(), "Root badge", cx, cy, 46 / DPI, 14 / DPI, fill="#1E9E5A", line="#1E9E5A", weight=0.5, rounding=7,
                         text="ROOT", chars=[(FONT, 9 * 0.75, "#FFFFFF", True)], margin=0))
    if n.get("l3") and not fw:
        cx, cy = local(x + 19, y + 11)
        kids.append(_box(pg.nid(), "L3 badge", cx, cy, 26 / DPI, 14 / DPI, fill="#7A3FF2", line="#7A3FF2", weight=0.5, rounding=7,
                         text="L3", chars=[(FONT, 9 * 0.75, "#FFFFFF", True)], margin=0))
    for it in labels:                                     # port labels travel with their switch
        if it["kind"] == "text":
            kids.append(_label(pg, pg.nid(), it["x"], it["y"], it["text"], it["anchor"], bold=it["bold"], origin=org))
        else:
            cx, cy = local(it["x"], it["y"])
            kids.append(_box(pg.nid(), "Blocked", cx, cy, 28 / DPI, 12 / DPI, fill=BLOCKED, line=BLOCKED, weight=0.5, rounding=6,
                             text="BLK", chars=[(FONT, 8.5 * 0.75, "#FFFFFF", True)], margin=0))
    data = [("Hostname", n.get("hostname")), ("IP address", n.get("ip")), ("Vendor", n.get("vendor")), ("Model", n.get("model")),
            ("Kind", "Firewall" if fw else ("Neighbour (no backup)" if ext else "Switch"))]
    if not fw and not ext:
        data += [("Firmware", n.get("firmware")), ("STP version", n.get("stp_version")), ("STP root bridge", n.get("stp_root")),
                 ("Stack members", n.get("members") if n.get("is_stack") else ""), ("Layer 3", "Yes" if n.get("l3") else "No")]
    cells = _cells(PinX=f"{gx0 + w / 2:.4f}", PinY=f"{gy0 + h / 2:.4f}", Width=f"{w:.4f}", Height=f"{h:.4f}",
                   LocPinX=(f"{w / 2:.4f}", "Width*0.5"), LocPinY=(f"{h / 2:.4f}", "Height*0.5"), Angle=0, FlipX=0, FlipY=0, ResizeMode=0)
    name = f'{"Firewall" if fw else "Switch"} {n["hostname"]}'
    xml = (f'<Shape ID="{gid}" NameU="{esc(name)}" Name="{esc(name)}" Type="Group" LineStyle="0" FillStyle="0" TextStyle="0">'
           f'{cells}{_props(data)}<Shapes>{"".join(kids)}</Shapes></Shape>')
    return gid, xml


def _connector(pg, sid, l, r, a_id, b_id):
    pts = [(pg.X(x), pg.Y(y)) for x, y in r["pts"]]
    (bx, by), (ex, ey) = pts[0], pts[-1]
    blocked = bool(l.get("blocked"))
    colour = BLOCKED if blocked else (FIBER if l.get("media") == "fiber" else UTP)
    weight = 4 if blocked else (6 if l.get("lag") else 3)
    w, h = ex - bx, ey - by
    cells = _cells(PinX=(f"{(bx + ex) / 2:.4f}", "GUARD((BeginX+EndX)/2)"), PinY=(f"{(by + ey) / 2:.4f}", "GUARD((BeginY+EndY)/2)"),
                   Width=(f"{w:.4f}", "GUARD(EndX-BeginX)"), Height=(f"{h:.4f}", "GUARD(EndY-BeginY)"),
                   LocPinX=(f"{w / 2:.4f}", "GUARD(Width*0.5)"), LocPinY=(f"{h / 2:.4f}", "GUARD(Height*0.5)"),
                   BeginX=f"{bx:.4f}", BeginY=f"{by:.4f}", EndX=f"{ex:.4f}", EndY=f"{ey:.4f}",
                   BegTrigger=("2", f"_XFTRIGGER(Sheet.{a_id}!EventXFMod)"), EndTrigger=("2", f"_XFTRIGGER(Sheet.{b_id}!EventXFMod)"),
                   ObjType=2, ShapeRouteStyle=1, ConLineRouteExt=1,
                   LineWeight=_in(weight), LineColor=colour, LinePattern=3 if blocked else 1, LineCap=1 if blocked else 0,
                   Rounding=_in(4), BeginArrow=0, EndArrow=0, FillPattern=0)
    rows = "".join(f'<Row T="{"MoveTo" if i == 0 else "LineTo"}" IX="{i + 1}"><Cell N="X" V="{x - bx:.4f}"/><Cell N="Y" V="{y - by:.4f}"/></Row>'
                   for i, (x, y) in enumerate(pts))
    geom = (f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="1"/><Cell N="NoLine" V="0"/><Cell N="NoShow" V="0"/>'
            f'<Cell N="NoSnap" V="0"/>{rows}</Section>')
    kind = "fibre / SFP" if l.get("media") == "fiber" else "UTP"
    members = ", ".join(f'{m["a_port"] or "?"} <-> {m["b_port"] or "?"}' for m in l.get("members", []))
    data = [("Cable", kind), ("A port", l.get("a_port")), ("B port", l.get("b_port")), ("Port-channel", "Yes" if l.get("lag") else "No"),
            ("Members", members if l.get("lag") else ""), ("STP", "Blocked" if blocked else "Forwarding")]
    name = f'Cable {l.get("a_port") or "?"} - {l.get("b_port") or "?"}'
    xml = (f'<Shape ID="{sid}" NameU="{esc(name)}" Name="{esc(name)}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
           f'{cells}{_props(data)}{geom}</Shape>')
    pg.connects.append(f'<Connect FromSheet="{sid}" FromCell="BeginX" FromPart="9" ToSheet="{a_id}" ToCell="PinX" ToPart="3"/>')
    pg.connects.append(f'<Connect FromSheet="{sid}" FromCell="EndX" FromPart="12" ToSheet="{b_id}" ToCell="PinX" ToPart="3"/>')
    return xml


def _legend(pg, minx, maxy_px):
    lx, ly = minx + 30, maxy_px - 120
    out = [_label(pg, pg.nid(), lx, ly, "Legend", "start", size=12 * 0.75, bold=True, font=FONT, halo=False)]

    def line(y, colour, weight, pattern, text, x0=lx, x1=lx + 40):
        sid = pg.nid()
        bx, by, ex = pg.X(x0), pg.Y(y), pg.X(x1)
        c = _cells(PinX=f"{(bx + ex) / 2:.4f}", PinY=f"{by:.4f}", Width=f"{ex - bx:.4f}", Height="0",
                   LocPinX=(f"{(ex - bx) / 2:.4f}", "Width*0.5"), LocPinY=("0", "Height*0.5"), BeginX=f"{bx:.4f}", BeginY=f"{by:.4f}",
                   EndX=f"{ex:.4f}", EndY=f"{by:.4f}", LineWeight=_in(weight), LineColor=colour, LinePattern=pattern, LineCap=1 if pattern == 3 else 0)
        g = (f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="1"/><Cell N="NoLine" V="0"/><Cell N="NoShow" V="0"/><Cell N="NoSnap" V="0"/>'
             f'<Row T="MoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row><Row T="LineTo" IX="2"><Cell N="X" V="{ex - bx:.4f}"/><Cell N="Y" V="0"/></Row></Section>')
        out.append(f'<Shape ID="{sid}" NameU="Legend line.{sid}" Name="Legend line.{sid}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">{c}{g}</Shape>')
        out.append(_label(pg, pg.nid(), x1 + 10, y + 4, text, "start", size=12 * 0.75, font=FONT, halo=False))

    line(ly + 18, FIBER, 3, 1, "Fibre / SFP")
    line(ly + 38, UTP, 3, 1, "UTP copper")
    line(ly + 58, BLOCKED, 4, 3, "STP blocked port")
    line(ly + 18, UTP, 6, 1, "Port-channel (name + member ports)", lx + 190, lx + 224)
    out.append(_box(pg.nid(), "Legend firewall", pg.X(lx + 207), pg.Y(ly + 41), 34 / DPI, 18 / DPI, fill=FW_FILL, line=FW_STROKE, weight=2, rounding=3))
    out.append(_label(pg, pg.nid(), lx + 232, ly + 46, "Firewall", "start", size=12 * 0.75, font=FONT, halo=False))
    out.append(_box(pg.nid(), "Legend root", pg.X(lx + 207), pg.Y(ly + 65), 34 / DPI, 14 / DPI, fill="#1E9E5A", line="#1E9E5A", weight=0.5, rounding=7))
    out.append(_label(pg, pg.nid(), lx + 232, ly + 69, "STP root bridge", "start", size=12 * 0.75, font=FONT, halo=False))
    return out


def build_vsdx(nodes, links, positions=None, free=False, moved=None) -> bytes:
    pos, routes = layout(nodes, links, positions, free, moved) if nodes else ({}, {})
    if not pos:
        pos = {"_": (0, 0)}
    minx, miny, maxx, maxy = bounds(pos, routes, True)
    pos.pop("_", None)
    page_w, page_h = (maxx - minx) / DPI, (maxy - miny) / DPI
    pg = _Page(minx, maxy)
    by_id = {n["id"]: n for n in nodes}
    sc = side_counts(routes)
    per_node = {n: [] for n in pos}
    for l in links:
        r = routes.get(l["id"])
        if r:
            for it in end_labels(l, r, sc, by_id):
                per_node[it["node"]].append(it)
    shapes, gid = [], {}
    cable_ids = {l["id"]: pg.nid() for l in links if l["id"] in routes}   # cables get the low ids so they sit under the switches
    for n in nodes:
        if n["id"] in pos:
            gid[n["id"]], xml = _switch_group(pg, n, *pos[n["id"]], per_node[n["id"]])
            shapes.append(xml)
    cables = [_connector(pg, cable_ids[l["id"]], l, routes[l["id"]], gid[l["a"]], gid[l["b"]]) for l in links if l["id"] in routes]
    legend = _legend(pg, minx, maxy)
    contents = (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<PageContents xmlns="{NS}" xmlns:r="{NS_R}" xml:space="preserve">'
                f'<Shapes>{"".join(cables + shapes + legend)}</Shapes>'
                + (f'<Connects>{"".join(pg.connects)}</Connects>' if pg.connects else "") + "</PageContents>")
    return _package(contents, page_w, page_h)


# --------------------------------------------------------------------------- the fixed parts of the package
def _package(page_xml: str, page_w: float, page_h: float) -> bytes:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    head = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
    parts = {
        "[Content_Types].xml": head + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
            '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
            '<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>'
            '<Override PartName="/visio/windows.xml" ContentType="application/vnd.ms-visio.windows+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>',
        "_rels/.rels": head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>'
            '</Relationships>',
        "docProps/core.xml": head + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>Switch topology</dc:title><dc:creator>SwitchMap</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            '</cp:coreProperties>',
        "docProps/app.xml": head + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            '<Application>Microsoft Visio</Application><Template></Template></Properties>',
        "visio/_rels/document.xml.rels": head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.microsoft.com/visio/2010/relationships/windows" Target="windows.xml"/>'
            '</Relationships>',
        "visio/document.xml": head + f'<VisioDocument xmlns="{NS}" xmlns:r="{NS_R}" xml:space="preserve">'
            '<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" DefaultFillStyle="0" DefaultGuideStyle="0">'
            '<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings><SnapExtensions>34</SnapExtensions><SnapAngles/>'
            '<DynamicGridEnabled>1</DynamicGridEnabled><ProtectStyles>0</ProtectStyles><ProtectShapes>0</ProtectShapes>'
            '<ProtectMasters>0</ProtectMasters><ProtectBkgnds>0</ProtectBkgnds></DocumentSettings>'
            '<Colors><ColorEntry IX="0" RGB="#000000"/><ColorEntry IX="1" RGB="#FFFFFF"/></Colors>'
            f'<FaceNames><FaceName NameU="{FONT}"/><FaceName NameU="{MONO}"/></FaceNames>'
            '<StyleSheets><StyleSheet ID="0" NameU="No Style" Name="No Style">'
            + _cells(EnableLineProps=1, EnableFillProps=1, EnableTextProps=1, HideForApply=0, LineWeight="0.01041666666666667", LineColor="#000000",
                     LinePattern=1, Rounding=0, EndArrowSize=2, BeginArrow=0, EndArrow=0, LineCap=0, BeginArrowSize=2, LineColorTrans=0,
                     FillForegnd="#FFFFFF", FillBkgnd="#000000", FillPattern=1, ShdwForegnd="#000000", ShdwPattern=0, FillForegndTrans=0,
                     FillBkgndTrans=0, ShdwForegndTrans=0, LeftMargin=0, RightMargin=0, TopMargin=0, BottomMargin=0, VerticalAlign=1,
                     TextDirection=0)
            + f'<Section N="Character"><Row IX="0"><Cell N="Font" V="{FONT}"/><Cell N="Color" V="#000000"/><Cell N="Style" V="0"/>'
              '<Cell N="Size" V="0.1666666666666667" U="PT"/></Row></Section>'
              '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="1"/></Row></Section>'
            '</StyleSheet></StyleSheets></VisioDocument>',
        "visio/windows.xml": head + f'<Windows xmlns="{NS}" xmlns:r="{NS_R}" ClientWidth="1600" ClientHeight="900" xml:space="preserve">'
            f'<Window ID="0" WindowType="Drawing" WindowState="1073741824" ContainerType="Page" Page="0" ViewScale="-1" '
            f'ViewCenterX="{page_w / 2:.4f}" ViewCenterY="{page_h / 2:.4f}"><ShowRulers>1</ShowRulers><ShowGrid>0</ShowGrid>'
            '<ShowPageBreaks>0</ShowPageBreaks><ShowGuides>1</ShowGuides><ShowConnectionPoints>1</ShowConnectionPoints>'
            '<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings><SnapExtensions>34</SnapExtensions><SnapAngles/>'
            '<DynamicGridEnabled>1</DynamicGridEnabled><TabSplitterPos>0.5</TabSplitterPos></Window></Windows>',
        "visio/pages/_rels/pages.xml.rels": head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/></Relationships>',
        "visio/pages/pages.xml": head + f'<Pages xmlns="{NS}" xmlns:r="{NS_R}" xml:space="preserve">'
            f'<Page ID="0" NameU="Topology" Name="Topology" ViewScale="-1" ViewCenterX="{page_w / 2:.4f}" ViewCenterY="{page_h / 2:.4f}">'
            '<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">'
            + _cells(PageWidth=f"{page_w:.4f}", PageHeight=f"{page_h:.4f}", ShdwOffsetX="0.125", ShdwOffsetY="-0.125",
                     PageScale=("1", "", "IN"), DrawingScale=("1", "", "IN"), DrawingSizeType=0, DrawingScaleType=0,
                     InhibitSnap=0, PageLockReplace=0, PageLockDuplicate=0, UIVisibility=0, ShdwType=0, ShdwObliqueAngle=0,
                     ShdwScaleFactor=1, DrawingResizeType=1, PageShapeSplit=1, PlaceStyle=0, RouteStyle=1, LineRouteExt=1,
                     PrintPageOrientation=2 if page_w >= page_h else 1)
            + '</PageSheet><Rel r:id="rId1"/></Page></Pages>',
        "visio/pages/page1.xml": page_xml,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("[Content_Types].xml", "_rels/.rels", "docProps/core.xml", "docProps/app.xml", "visio/document.xml",
                     "visio/_rels/document.xml.rels", "visio/windows.xml", "visio/pages/pages.xml", "visio/pages/_rels/pages.xml.rels",
                     "visio/pages/page1.xml"):
            z.writestr(name, parts[name])
    return buf.getvalue()
