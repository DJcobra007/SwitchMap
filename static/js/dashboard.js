// Dashboard tab: summary cards, VLAN explorer (management VLAN highlighted), switch table, parse warnings.
NT.dashboard = {
  render() {
    const d = NT.data, s = d.summary, esc = NT.esc;
    const cards = [["Switches", s.switch_count], ["Stack members", s.member_count], ["Stacks", s.stack_count], ["Access points", s.ap_count], ["Firewalls", s.firewall_count], ["Port-channels", s.port_channel_count], ["Findings", NT.data.findings.items.length],
                   ["SFP / stack parts", s.component_count], ["Direct links", s.link_count], ["STP-blocked links", s.blocked_count]];
    document.getElementById("stats").innerHTML = cards.map(([l, v]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");

    const sel = document.getElementById("vlan-switch");
    sel.innerHTML = d.switches.map(x => `<option value="${x.id}">${esc(x.hostname)} (${esc(x.mgmt_ip || "no IP")})</option>`).join("");
    sel.onchange = () => this.vlans(sel.value);
    this.vlans(sel.value);

    document.getElementById("sw-table").innerHTML =
      `<table><tr><th>Hostname</th><th>Vendor</th><th>Model</th><th>IP</th><th>Mgmt VLAN</th><th>Firmware</th><th>STP</th><th>Detected as</th><th>MACs read</th><th>Serials read</th><th>Parts listed</th></tr>` +
      d.switches.map(x => `<tr><td><a href="#" data-id="${x.id}" class="open">${esc(x.hostname)}</a>${x.is_stack ? `<span class="badge">stack ×${x.members.length}</span>` : ""}</td>
        <td>${esc(x.vendor)}</td><td class="mono">${esc(x.model || "")}</td><td class="mono">${esc(x.mgmt_ip || "N/A")}</td>
        <td>${x.mgmt_vlan ?? "N/A"}</td><td class="mono">${esc(x.firmware)}</td><td>${esc(x.stp_version)}</td><td>${esc(x.detected_as)}</td>
        <td>${x.members.filter(m => m.mac).length}/${x.members.length}</td><td>${x.members.filter(m => m.serial).length}/${x.members.length}</td><td>${x.components.length}</td></tr>`).join("") + `</table>`;
    document.querySelectorAll("#sw-table a.open").forEach(a => a.onclick = e => { e.preventDefault(); NT.showTab("topology"); NT.panel.open(a.dataset.id); });

    NT.compliance.render();
    NT.findings.render();
    document.getElementById("warns").innerHTML = d.switches.filter(x => x.warnings.length || x.info.length).map(x =>
      `<div class="warn-sw"><h3>${esc(x.hostname)} <span class="hint">${esc(x.filename)}</span></h3><ul>${x.info.map(w => `<li>${esc(w)}</li>`).join("")}${x.warnings.map(w => `<li><b>Missing:</b> ${esc(w)}</li>`).join("")}</ul></div>`).join("")
      || `<p class="hint">Nothing missing: every expected field was found.</p>`;
  },
  vlans(id) {
    const x = NT.sw(id), esc = NT.esc;
    document.getElementById("vlan-sum").textContent = `${x.vlans.length} VLAN${x.vlans.length === 1 ? "" : "s"}` + (x.mgmt_vlan ? ` - management VLAN ${x.mgmt_vlan}` : "");
    document.getElementById("vlan-list").innerHTML = x.vlans.length
      ? `<table><tr><th>VLAN ID</th><th>Name</th></tr>` + x.vlans.map(v =>
          `<tr class="${v.id === x.mgmt_vlan ? "mgmt" : ""}"><td>${v.id}</td><td>${esc(v.name)}${v.id === x.mgmt_vlan ? '<span class="badge">management</span>' : ""}</td></tr>`).join("") + `</table>`
      : `<p class="hint">No VLANs found in this backup.</p>`;
  },
};
