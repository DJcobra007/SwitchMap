"""SwitchMap - upload switch backups, get an inventory workbook and a topology diagram.

Run:  python app.py        then open http://127.0.0.1:5000
"""
from __future__ import annotations
import io
import json
import os

from flask import Flask, render_template, request, jsonify, send_file, abort

from core import sessions
from core.detector import LABELS
from core.diagram_export import render_svg, render_diagram
from core.excel_export import build_workbook
from core.visio_export import build_vsdx
from core.loader import load_uploads
from core.pipeline import run

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html", vendors=LABELS)


@app.post("/api/analyze")
def api_analyze():
    uploads = [(f.filename or "backup.txt", f.read()) for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        return jsonify(error="Choose at least one backup file."), 400
    files, skipped = load_uploads(uploads)
    if not files:
        return jsonify(error="No readable text files found. " + " ".join(skipped)), 400
    analysis = run(files, request.form.get("vendor", "auto"), skipped)
    sessions.put(analysis)
    return jsonify(analysis.to_dict())


def _layout_args():
    """Switch positions sent by the browser after the user dragged switches (empty = automatic layout)."""
    body = request.get_json(silent=True) if request.method == "POST" else None
    body = body if isinstance(body, dict) else {}
    pos = body.get("positions")
    moved = body.get("moved")
    return {"positions": pos if isinstance(pos, dict) else None, "free": body.get("free") is True,
            "moved": moved if isinstance(moved, str) else None}


def _session_or_404(sid):
    a = sessions.get(sid)
    if a is None:
        abort(404, "Analysis expired - upload the backups again.")
    return a


@app.post("/api/diagram/<sid>")
def api_diagram(sid):
    """On-screen diagram: same renderer as the download, so screen and file always match."""
    a = _session_or_404(sid)
    return jsonify(render_diagram(a.nodes, a.links, export=False, **_layout_args()))


@app.get("/download/inventory/<sid>.xlsx")
def download_inventory(sid):
    a = _session_or_404(sid)
    return send_file(io.BytesIO(build_workbook(a, merge=request.args.get('merge', '1') != '0')), as_attachment=True, download_name="switch_inventory.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/download/diagram/<sid>.svg", methods=["GET", "POST"])
def download_diagram(sid):
    a = _session_or_404(sid)
    svg = render_svg(a.nodes, a.links, **_layout_args())
    return send_file(io.BytesIO(svg.encode("utf-8")), as_attachment=request.args.get("inline") != "1",
                     download_name="switch_topology.svg", mimetype="image/svg+xml")



@app.route("/download/diagram/<sid>.vsdx", methods=["GET", "POST"])
def download_visio(sid):
    a = _session_or_404(sid)
    return send_file(io.BytesIO(build_vsdx(a.nodes, a.links, **_layout_args())), as_attachment=True, download_name="switch_topology.vsdx",
                     mimetype="application/vnd.ms-visio.drawing")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)), debug=False)
