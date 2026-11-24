// Wires tabs and download buttons; called after each analysis.
NT.showTab = function (name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.dataset.tab === name));
  document.getElementById("tab-dashboard").hidden = name !== "dashboard";
  document.getElementById("tab-topology").hidden = name !== "topology";
  if (name === "topology") { NT.diagram.show(); document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" }); }
};
NT.onResults = function () {
  document.getElementById("results").hidden = false;
  document.getElementById("downloads").hidden = false;
  document.getElementById("msg").textContent += ` Workbook rows: ${NT.data.summary.member_count} switches, ${NT.data.aps.length} APs, ${NT.data.components.length} devices, ${NT.data.firewalls.length} firewalls.`;
  const link = document.getElementById("dl-xlsx"), merge = document.getElementById("merge"), sm = NT.data.summary;
  const setHref = () => (link.href = `/download/inventory/${NT.data.session_id}.xlsx?merge=${merge.checked ? 1 : 0}`);
  merge.onchange = setHref;
  const rows = sm.member_count + NT.data.aps.length + NT.data.components.length + NT.data.firewalls.length;
  link.title = `${sm.member_count} switch rows, ${NT.data.aps.length} AP rows, ${NT.data.components.length} device rows, ${NT.data.firewalls.length} firewall rows`;
  merge.checked = rows < 1500;     // big sets: merged cells make Excel slow, so default them off
  setHref();
  document.getElementById("panel").hidden = true;
  NT.dashboard.render();
  NT.showTab("dashboard");
};
document.querySelectorAll(".tab").forEach(t => (t.onclick = () => NT.showTab(t.dataset.tab)));
const guard = fn => async () => { try { await fn(); } catch (e) { alert(e.message); } };
// "Diagram" opens a small menu of download formats
(function () {
  const btn = document.getElementById("dl-diagram"), list = document.getElementById("dl-formats");
  const close = () => { list.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = e => { e.stopPropagation(); list.hidden = !list.hidden; btn.setAttribute("aria-expanded", String(!list.hidden)); if (!list.hidden) list.querySelector("button").focus(); };
  const run = { vsdx: () => NT.diagram.downloadVSDX(), svg: () => NT.diagram.downloadSVG(), png: () => NT.diagram.downloadPNG() };
  list.querySelectorAll("button").forEach(b => (b.onclick = () => { close(); guard(run[b.dataset.fmt])(); }));
  document.addEventListener("click", e => { if (!list.hidden && !list.contains(e.target)) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !list.hidden) { close(); btn.focus(); } });
})();
document.getElementById("z-in").onclick = () => NT.diagram.zoomIn();
document.getElementById("z-out").onclick = () => NT.diagram.zoomOut();
document.getElementById("z-fit").onclick = () => NT.diagram.fit();
document.getElementById("z-pct").onclick = () => NT.diagram.actual();
document.getElementById("find").addEventListener("change", e => NT.diagram.find(e.target.value));
