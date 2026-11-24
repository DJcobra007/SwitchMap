// Dashboard: the STANDARD CONFIGURATION (AAA / NTP / SNMP / DNS / domain name) - what every switch should have - and who complies.
NT.compliance = {
  glyph: { match: "✓ match", differs: "≠ differs", missing: "✗ missing", unknown: "? n/a" },
  render() {
    const c = NT.data.compliance, esc = NT.esc, root = document.getElementById("std");
    if (!c || !c.categories.length || !c.categories[0].total) { root.innerHTML = `<p class="hint">No switch with a readable configuration for this check.</p>`; return; }
    const n = c.categories[0].total;
    const boxes = c.categories.map(k => {
      const pct = k.total ? Math.round(100 * k.match / k.total) : 0;
      const bad = k.rows.filter(r => r.status !== "match");
      const items = k.items.length ? `<dl class="std-items">${k.items.map(i => `<dt>${esc(i.label)}</dt><dd class="mono">${esc(i.value)}</dd>`).join("")}</dl>`
                                   : `<p class="hint">Not configured on any switch.</p>`;
      const list = bad.length ? `<details><summary>${bad.length} switch${bad.length === 1 ? "" : "es"} not matching</summary><ul class="std-bad">${bad.slice(0, 40).map(r =>
        `<li><a href="#" class="open" data-id="${r.switch_id}">${esc(r.hostname)}</a> <span class="st ${r.status}">${this.glyph[r.status]}</span><br><span class="hint mono">${esc(r.value)}</span></li>`).join("")}${bad.length > 40 ? `<li class="hint">+${bad.length - 40} more (see the matrix below)</li>` : ""}</ul></details>`
        : `<p class="ok">All ${k.total} switches match.</p>`;
      return `<div class="std-box ${bad.length ? "has-bad" : ""}"><h3>${esc(k.label)}</h3>
        <div class="bar" title="${pct}% match"><i style="width:${pct}%"></i></div>
        <small><b>${k.match}/${k.total}</b> match · ${k.differs} differ · ${k.missing} missing</small>${items}${list}</div>`;
    }).join("");
    const matrix = `<details><summary>Per-switch matrix (${c.matrix.length} switches)</summary><div class="scroll" style="max-height:380px"><table><tr><th>Switch</th>` +
      c.categories.map(k => `<th>${esc(k.label)}</th>`).join("") + `</tr>` + c.matrix.map(m => `<tr><td><a href="#" class="open" data-id="${m.switch_id}">${esc(m.hostname)}</a></td>` +
        c.categories.map(k => `<td class="st ${m.status[k.key]}">${this.glyph[m.status[k.key]]}</td>`).join("") + `</tr>`).join("") + `</table></div></details>`;
    const note = (c.unsupported.length ? `<p class="hint">Not checked (vendor not supported, or no running-config in the backup): ${esc(c.unsupported.join(", "))}.</p>` : "") +
      `<p class="hint">SNMP community strings are never shown, only an identifier.</p>`;
    root.innerHTML = `<div class="std-head"><div class="score"><b>${c.score}%</b><span>overall match</span></div>
      <p>This is the standard configuration every switch should have, taken from the most common setup across the <b>${n}</b> uploaded switch${n === 1 ? "" : "es"}.
      Anything that differs or is missing is listed under the setting.</p></div><div class="std-grid">${boxes}</div>${matrix}${note}`;
    root.querySelectorAll("a.open").forEach(a => a.onclick = e => { e.preventDefault(); NT.showTab("topology"); NT.panel.open(a.dataset.id); });
  },
  forSwitch(id) {
    const c = NT.data.compliance, esc = NT.esc;
    const row = c && c.matrix.find(m => m.switch_id === id);
    if (!row) return "";
    return `<details><summary>Standard configuration</summary><div><div class="scroll"><table><tr><th>Setting</th><th>Status</th><th>Configured</th></tr>` +
      c.categories.map(k => { const r = k.rows.find(x => x.switch_id === id); return `<tr><td>${esc(k.label)}</td><td class="st ${row.status[k.key]}">${this.glyph[row.status[k.key]]}</td><td class="mono">${esc(r ? r.value : "n/a")}</td></tr>`; }).join("") + `</table></div></div></details>`;
  },
};
