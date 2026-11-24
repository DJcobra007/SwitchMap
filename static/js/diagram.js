// Interactive topology diagram. The SVG itself is drawn by Python (core/diagram_export.py) so the screen and the downloads are identical.
// This file adds a map-style view: wheel / pinch zoom around the pointer (up to 1200%), drag to pan, overview map, hover, click, drag-a-switch, search.
NT.diagram = (function () {
  const W = 210, H = 72, MAX_ZOOM = 12;
  let pos = {}, pinned = false, free = false, moved = null, selected = null, svgEl = null, size = { w: 1, h: 1 }, base = { x: 0, y: 0, w: 1, h: 1 };
  let view = { x: 0, y: 0, w: 1, h: 1 }, busy = false, again = false;
  const esc = NT.esc, box = () => document.getElementById("canvas");

  const pxPerUnit = () => box().clientWidth / view.w;

  function setView(x, y, w) {
    const c = box(), aspect = c.clientHeight / c.clientWidth;
    w = Math.min(Math.max(w, c.clientWidth / MAX_ZOOM), Math.max(base.w, base.h / aspect) * 4);
    view = { x, y, w, h: w * aspect };
    if (svgEl) svgEl.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    const z = pxPerUnit(), unit0 = 40 * z;
    let unit = unit0;
    while (unit < 16) unit *= 5;                                    // keep the background grid readable at any zoom
    c.style.backgroundSize = `${unit}px ${unit}px`;
    c.style.backgroundPosition = `${-view.x * z}px ${-view.y * z}px`;
    const lab = document.getElementById("z-pct");
    if (lab) lab.textContent = Math.round(z * 100) + "%";
    drawMini();
  }

  function toSvg(clientX, clientY) {                                // pointer position -> diagram coordinates
    const r = box().getBoundingClientRect(), z = pxPerUnit();
    return { x: view.x + (clientX - r.left) / z, y: view.y + (clientY - r.top) / z };
  }

  function zoomAt(factor, clientX, clientY) {
    const c = box(), r = c.getBoundingClientRect();
    const cx = clientX ?? r.left + r.width / 2, cy = clientY ?? r.top + r.height / 2;
    const p = toSvg(cx, cy), w = view.w / factor;
    const nw = Math.min(Math.max(w, c.clientWidth / MAX_ZOOM), Math.max(base.w, base.h / (c.clientHeight / c.clientWidth)) * 4);
    const k = nw / view.w;                                          // keep the point under the pointer fixed
    setView(p.x - (p.x - view.x) * k, p.y - (p.y - view.y) * k, nw);
  }

  // switch positions for the server: none = automatic tiers; pinned = the user moved switches sideways in their tier;
  // free = the user moved a switch anywhere, so every switch stays where it is and the cables are routed around them
  const layoutBody = () => ({ positions: pinned ? pos : {}, free, moved });

  async function fetchSvg() {
    const r = await fetch(`/api/diagram/${NT.data.session_id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(layoutBody()) });
    if (!r.ok) throw new Error("Could not draw the diagram.");
    return r.json();
  }

  async function refresh() {
    if (busy) { again = true; return; }
    busy = true;
    try {
      const j = await fetchSvg(), c = box();
      pos = j.positions;                                              // the server decides tiers and x order; this keeps the browser in step
      c.innerHTML = j.svg;
      svgEl = c.firstChild;
      const vb = svgEl.viewBox.baseVal;
      base = { x: vb.x, y: vb.y, w: vb.width, h: vb.height };
      size = { w: j.width, h: j.height };
      svgEl.removeAttribute("width"); svgEl.removeAttribute("height");
      svgEl.setAttribute("preserveAspectRatio", "xMinYMin meet");
      svgEl.style.width = "100%"; svgEl.style.height = "100%";
      setView(view.x, view.y, view.w);
      bind();
      setFocus(selected);
    } finally {
      busy = false;
      if (again) { again = false; refresh(); }
    }
  }

  // ------------------------------------------------- highlight one switch's cables and dim the rest (the way to follow a cable in a busy diagram)
  function setFocus(id) {
    if (!svgEl) return;
    const lit = new Set(), nodes = new Set();
    if (id) {
      nodes.add(id);
      NT.data.links.forEach(l => { if (l.a === id || l.b === id) { lit.add(l.id); nodes.add(l.a); nodes.add(l.b); } });
    }
    svgEl.classList.toggle("dimmed", !!id);
    svgEl.querySelectorAll(".link, .lbl").forEach(g => g.classList.toggle("hl", lit.has(g.dataset.id)));
    svgEl.querySelectorAll(".node").forEach(g => g.classList.toggle("hl", nodes.has(g.dataset.id)));
  }

  // ---------------------------------------------------------------- hover / click / drag a switch
  function bind() {
    const tip = document.getElementById("tip");
    svgEl.querySelectorAll(".node").forEach(g => {
      const n = NT.data.nodes.find(x => x.id === g.dataset.id);
      if (g.dataset.id === selected) g.classList.add("sel");
      g.addEventListener("mouseenter", () => {
        setFocus(n.id);
        tip.innerHTML = n.kind === "firewall"
          ? `<b>${esc(n.hostname)}</b>Firewall: ${esc(n.vendor)} ${esc(n.model || "")}<br>IP: ${esc(n.ip || "n/a")}`
          : `<b>${esc(n.hostname)}</b>STP version: ${esc(n.stp_version)}<br>Root bridge: ${esc(n.stp_root || "unknown")}<br>Firmware: ${esc(n.firmware)}`;
        tip.hidden = false;
      });
      g.addEventListener("mousemove", e => { tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; });
      g.addEventListener("mouseleave", () => { tip.hidden = true; setFocus(selected); });
      g.addEventListener("pointerdown", e => { e.stopPropagation(); dragNode(e, g, n); });
    });
    svgEl.querySelectorAll(".link").forEach(g => {
      const t = g.querySelector("title");
      g.addEventListener("mouseenter", () => { tip.innerHTML = esc(t.textContent); tip.hidden = false; });
      g.addEventListener("mousemove", e => { tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; });
      g.addEventListener("mouseleave", () => (tip.hidden = true));
    });
  }

  function dragNode(e, g, n) {
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY }, orig = [...pos[n.id]], p0 = toSvg(e.clientX, e.clientY);
    let dragged = false, dx = 0, dy = 0;
    const mine = [...svgEl.querySelectorAll(".link, .lbl")].filter(el => NT.data.links.some(l => l.id === el.dataset.id && (l.a === n.id || l.b === n.id)));
    const move = ev => {
      if (!dragged && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return;
      if (!dragged) { mine.forEach(el => el.classList.add("moving")); g.classList.add("dragging"); svgEl.appendChild(g); }
      dragged = true;
      const p = toSvg(ev.clientX, ev.clientY);
      dx = p.x - p0.x; dy = p.y - p0.y;
      g.setAttribute("transform", `translate(${dx} ${dy})`);          // the switch follows the pointer in any direction; its cables are re-routed on release
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (!dragged) { NT.panel.open(n.id); return; }
      if (!free && Math.abs(dy) < H / 2) pos[n.id] = [orig[0] + dx, orig[1]];   // stayed in its row: keep the tidy tiered layout
      else { free = true; moved = n.id; pos[n.id] = [orig[0] + dx, orig[1] + dy]; }
      pinned = true;
      refresh();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---------------------------------------------------------------- pan (drag the background), wheel zoom, pinch, keyboard
  const pointers = new Map();
  let pinch = null;
  function initCanvas() {
    const c = box();
    if (c.dataset.ready) return;
    c.dataset.ready = "1";
    c.addEventListener("wheel", e => { e.preventDefault(); zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), e.clientX, e.clientY); }, { passive: false });
    c.addEventListener("pointerdown", e => {
      c.focus({ preventScroll: true });
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      c.setPointerCapture(e.pointerId);
      c.classList.add("panning");
      if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) }; }
    });
    c.addEventListener("pointermove", e => {
      const prev = pointers.get(e.pointerId);
      if (!prev) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 2 && pinch) {
        const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
        zoomAt(d / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
        pinch.d = d;
      } else if (pointers.size === 1) {
        const z = pxPerUnit();
        setView(view.x - (e.clientX - prev.x) / z, view.y - (e.clientY - prev.y) / z, view.w);
      }
    });
    const end = e => { pointers.delete(e.pointerId); pinch = null; if (!pointers.size) c.classList.remove("panning"); };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("keydown", e => {
      const step = view.w * 0.15;
      const acts = { "+": () => zoomAt(1.4), "=": () => zoomAt(1.4), "-": () => zoomAt(1 / 1.4), "0": fit, "1": () => zoomTo(1),
                     ArrowLeft: () => setView(view.x - step, view.y, view.w), ArrowRight: () => setView(view.x + step, view.y, view.w),
                     ArrowUp: () => setView(view.x, view.y - step, view.w), ArrowDown: () => setView(view.x, view.y + step, view.w) };
      if (acts[e.key]) { e.preventDefault(); acts[e.key](); }
    });
    new ResizeObserver(() => { if (svgEl) setView(view.x, view.y, view.w); }).observe(c);
    initMini();
  }

  function zoomTo(z, cx, cy) {                                      // absolute zoom (1 = 100%) around a diagram point (default: centre of the view)
    const c = box(), w = c.clientWidth / z;
    const px = cx ?? view.x + view.w / 2, py = cy ?? view.y + view.h / 2;
    const aspect = c.clientHeight / c.clientWidth;
    setView(px - w / 2, py - (w * aspect) / 2, w);
  }

  function fit() {
    const c = box(), aspect = c.clientHeight / c.clientWidth;
    const w = Math.max(base.w, base.h / aspect);                    // whole diagram visible
    setView(base.x - (w - base.w) / 2, base.y - (w * aspect - base.h) / 2, w);
  }

  // ---------------------------------------------------------------- overview map
  function initMini() {
    const m = document.getElementById("minimap");
    const go = e => {
      const r = m.getBoundingClientRect(), k = base.w / r.width;
      const cx = base.x + (e.clientX - r.left) * k, cy = base.y + (e.clientY - r.top) * k;
      setView(cx - view.w / 2, cy - view.h / 2, view.w);
    };
    m.addEventListener("pointerdown", e => { e.stopPropagation(); m.setPointerCapture(e.pointerId); go(e); m.onpointermove = go; });
    m.addEventListener("pointerup", () => { m.onpointermove = null; });
  }
  function drawMini() {
    const m = document.getElementById("minimap");
    if (!m || !NT.data) return;
    if (NT.data.nodes.length < 8) { m.hidden = true; return; }
    m.hidden = false;
    const ratio = base.h / base.w, wpx = 200, hpx = Math.max(60, Math.min(150, wpx * ratio));
    m.style.width = wpx + "px"; m.style.height = hpx + "px";
    const dots = NT.data.nodes.map(n => { const p = pos[n.id] || [n.x, n.y];
      return `<rect x="${p[0]}" y="${p[1]}" width="${W}" height="${H}" fill="${n.kind === "firewall" ? "#B3261E" : (n.id === selected ? "#E5484D" : "#0B5563")}"/>`; }).join("");
    m.innerHTML = `<svg viewBox="${base.x} ${base.y} ${base.w} ${base.h}" width="100%" height="100%" preserveAspectRatio="none">${dots}
      <rect x="${view.x}" y="${view.y}" width="${view.w}" height="${view.h}" fill="rgba(31,111,235,.12)" stroke="#1F6FEB" stroke-width="${base.w / 150}"/></svg>`;
  }

  async function svgText() {
    const r = await fetch(`/download/diagram/${NT.data.session_id}.svg`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(layoutBody()) });
    if (!r.ok) throw new Error("Diagram export failed.");
    return r.text();
  }

  return {
    async show() {
      initCanvas();
      pos = {};
      pinned = false; free = false; moved = null;
      selected = null;
      view = { x: 0, y: 0, w: 1000, h: 600 };
      await refresh();
      fit();
      document.getElementById("hostlist").innerHTML = NT.data.nodes.map(n => `<option value="${esc(n.hostname)}">`).join("");
    },
    select(id) {
      selected = id;
      if (svgEl) svgEl.querySelectorAll(".node").forEach(g => g.classList.toggle("sel", g.dataset.id === id));
      setFocus(id);
      drawMini();
    },
    fit, zoomIn() { zoomAt(1.5); }, zoomOut() { zoomAt(1 / 1.5); }, actual() { zoomTo(1); },
    find(name) {
      const n = NT.data.nodes.find(x => x.id === name || String(x.hostname).toLowerCase() === name.trim().toLowerCase());
      if (!n) return;
      zoomTo(Math.max(pxPerUnit(), 1), pos[n.id][0] + W / 2, pos[n.id][1] + H / 2);   // centre on it, at least 100% so the text is readable
      NT.panel.open(n.id);
    },
    async downloadVSDX() {
      const r = await fetch(`/download/diagram/${NT.data.session_id}.vsdx`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(layoutBody()) });
      if (!r.ok) throw new Error("Visio export failed.");
      NT.save(await r.blob(), "switch_topology.vsdx");
    },
    async downloadSVG() { NT.save(new Blob([await svgText()], { type: "image/svg+xml" }), "switch_topology.svg"); },
    async downloadPNG() {
      const text = await svgText(), url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" })), img = new Image();
      const m = text.match(/width="([\d.]+)" height="([\d.]+)"/);
      const scale = Math.min(2, 12000 / Math.max(m[1], m[2]));
      img.onload = () => {
        const c = document.createElement("canvas");
        c.width = Math.round(m[1] * scale); c.height = Math.round(m[2] * scale);
        const ctx = c.getContext("2d");
        ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(b => NT.save(b, "switch_topology.png"), "image/png");
        URL.revokeObjectURL(url);
      };
      img.src = url;
    },
  };
})();
