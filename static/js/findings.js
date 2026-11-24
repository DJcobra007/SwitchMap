// Dashboard: misconfigurations found in the backups, filterable by severity, category and switch.
NT.findings = {
  sev: "all", cat: "all", q: "", limit: 300,
  render() {
    const f = NT.data.findings, esc = NT.esc, root = document.getElementById("findings");
    if (!f) { root.innerHTML = ""; return; }
    const chips = ["High", "Medium", "Low", "Info"].map(s => `<button type="button" class="sevchip ${s} ${this.sev === s ? "on" : ""}" data-sev="${s}"><b>${f.counts[s]}</b> ${s}</button>`).join("") +
      `<button type="button" class="sevchip all ${this.sev === "all" ? "on" : ""}" data-sev="all">All (${f.items.length})</button>`;
    root.innerHTML = `<div class="fbar">${chips}<select id="f-cat" aria-label="Category"><option value="all">All categories</option>${f.categories.map(c => `<option ${c === this.cat ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <input id="f-q" placeholder="Filter by switch or text..." value="${esc(this.q)}" aria-label="Filter findings"></div><div id="f-table"></div>
      <p class="hint">Each finding is based only on what is in the backups. Where a check needs a command that is not in the backup (for example <code>show interfaces status</code> or <code>show etherchannel summary</code>), it cannot run.</p>`;
    root.querySelectorAll(".sevchip").forEach(b => b.onclick = () => { this.sev = b.dataset.sev; this.limit = 300; this.render(); });
    root.querySelector("#f-cat").onchange = e => { this.cat = e.target.value; this.limit = 300; this.table(); };
    root.querySelector("#f-q").oninput = e => { this.q = e.target.value; this.limit = 300; this.table(); };
    this.table();
  },
  table() {
    const f = NT.data.findings, esc = NT.esc, q = this.q.trim().toLowerCase();
    const rows = f.items.filter(x => (this.sev === "all" || x.severity === this.sev) && (this.cat === "all" || x.category === this.cat) &&
      (!q || `${x.hostname} ${x.title} ${x.detail} ${x.category} ${x.where}`.toLowerCase().includes(q)));
    const shown = rows.slice(0, this.limit);
    document.getElementById("f-table").innerHTML = rows.length
      ? `<div class="scroll" style="max-height:480px"><table><tr><th>Severity</th><th>Switch</th><th>Category</th><th>Issue</th><th>Detail</th></tr>` + shown.map(x =>
          `<tr><td><span class="sev ${x.severity}">${x.severity}</span></td><td>${x.switch_id ? `<a href="#" class="open" data-id="${x.switch_id}">${esc(x.hostname)}</a>` : esc(x.hostname)}</td>
           <td>${esc(x.category)}</td><td><b>${esc(x.title)}</b></td><td class="hint">${esc(x.detail)}</td></tr>`).join("") + `</table></div>` +
        (rows.length > shown.length ? `<p><button class="btn" id="f-more" type="button">Show all ${rows.length}</button></p>` : "")
      : `<p class="ok">${f.items.length ? "No findings match this filter." : "No misconfigurations were found in the uploaded backups."}</p>`;
    document.querySelectorAll("#f-table a.open").forEach(a => a.onclick = e => { e.preventDefault(); NT.showTab("topology"); NT.panel.open(a.dataset.id); });
    const more = document.getElementById("f-more");
    if (more) more.onclick = () => { this.limit = 1e9; this.table(); };
  },
};
