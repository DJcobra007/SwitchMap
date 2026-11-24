// Right-hand details panel opened by clicking a switch in the diagram.
NT.panel = {
  open(id) {
    const el = document.getElementById("panel"), esc = NT.esc, sw = NT.sw(id);
    NT.diagram.select(id);
    el.hidden = false;
    if (!sw) {
      const n = NT.data.nodes.find(x => x.id === id);
      if (n.kind === "firewall") {
        el.innerHTML = `<button class="btn close" id="pclose">Close</button><h3>${esc(n.hostname)}</h3><p class="sub">Firewall · ${esc(n.vendor)} ${esc(n.model || "")}</p>
          <dl class="kv"><dt>IP address</dt><dd>${esc(n.ip || "N/A")}</dd><dt>MAC address</dt><dd>${esc(n.mac || "N/A")}</dd><dt>Detected from</dt><dd>${esc(n.source)}</dd></dl>
          <h4>Connected to</h4><div class="scroll"><table><tr><th>Switch</th><th>Switch port</th><th>Firewall port</th></tr>` +
          n.connections.map(c => `<tr><td>${esc(c.switch_hostname)}</td><td class="mono">${esc(c.switch_port)}</td><td class="mono">${esc(c.firewall_port)}</td></tr>`).join("") + `</table></div>
          <p class="hint">Only what the switch reported about this firewall is known; its own configuration is not read.</p>`;
        document.getElementById("pclose").onclick = () => this.close();
        return;
      }
      el.innerHTML = `<button class="btn close" id="pclose">Close</button><h3>${esc(n.hostname)}</h3><p class="sub">${esc(n.ip || "")}</p>
        <p class="hint">This device appears as a neighbour but its backup was not uploaded, so only what the neighbour table reported is known.</p>
        <dl class="kv"><dt>Platform</dt><dd>${esc(n.model || "unknown")}</dd></dl>`;
      document.getElementById("pclose").onclick = () => this.close();
      return;
    }
    const up = sw.interfaces.filter(i => i.status === "up").length;
    const members = sw.is_stack
      ? `<h4>Stack members</h4><div class="scroll"><table><tr><th>#</th><th>Role</th><th>Priority</th><th>Model</th><th>Serial</th><th>MAC</th></tr>` +
        sw.members.map(m => `<tr><td>${m.index}</td><td>${esc(m.role)}</td><td>${esc(m.priority || "-")}</td><td class="mono">${esc(m.model || "-")}</td><td class="mono">${esc(m.serial || "N/A")}</td><td class="mono">${esc(m.mac || "N/A")}</td></tr>`).join("") + `</table></div>`
      : `<dl class="kv"><dt>Serial number</dt><dd>${esc(sw.serial || "N/A")}</dd><dt>MAC address</dt><dd>${esc(sw.mac || "N/A")}</dd></dl>`;
    const node = NT.data.nodes.find(x => x.id === id) || {};
    const pcs = sw.port_channels.length
      ? `<details open><summary>Port-channels (${sw.port_channels.length})</summary><div class="scroll"><table><tr><th>Port-channel</th><th>Protocol</th><th>Member ports</th></tr>` +
        sw.port_channels.map(p => `<tr><td class="mono">${esc(this.short(p.name))}${p.description ? `<br><span class="hint">${esc(p.description)}</span>` : ""}</td><td>${esc(p.protocol || "-")}${p.flags ? ` <span class="hint">(${esc(p.flags)})</span>` : ""}</td>
          <td>${p.members.map(m => `<span class="chip" title="flag ${esc(m.flag || "n/a")}">${esc(this.short(m.port))}${m.flag ? `(${esc(m.flag)})` : ""}</span>`).join("")}</td></tr>`).join("") + `</table></div></details>` : "";
    const blocked = sw.blocked_ports.length ? `<dt>STP blocked</dt><dd>${sw.blocked_ports.map(esc).join(", ")}</dd>` : "";
    el.innerHTML = `<button class="btn close" id="pclose">Close</button>
      <h3>${esc(sw.hostname)} ${node.l3 ? '<span class="badge l3">L3 / core</span>' : ""}${node.is_root ? '<span class="badge" style="background:#1E9E5A">STP root</span>' : ""}</h3><p class="sub">${esc(sw.mgmt_ip || "no IP")} · ${esc(sw.vendor)} ${esc(sw.model || "")}</p>
      <dl class="kv"><dt>Gateway</dt><dd>${esc(sw.gateway || "N/A")}</dd><dt>Subnet</dt><dd>${esc(sw.subnet || "N/A")}</dd>
        <dt>Firmware</dt><dd>${esc(sw.firmware)}</dd><dt>STP</dt><dd>${esc(sw.stp_version)}</dd><dt>Root bridge</dt><dd>${esc(node.stp_root || "unknown")}</dd>${blocked}</dl>
      ${members}
      ${pcs}
      <details><summary>VLANs (${sw.vlans.length})</summary><div>${sw.vlans.length ? `<table><tr><th>ID</th><th>Name</th></tr>` +
        sw.vlans.map(v => `<tr class="${v.id === sw.mgmt_vlan ? "mgmt" : ""}"><td>${v.id}</td><td>${esc(v.name)}${v.id === sw.mgmt_vlan ? '<span class="badge">management</span>' : ""}</td></tr>`).join("") + `</table>` : '<span class="hint">None found.</span>'}</div></details>
      ${NT.panelL3(sw)}
      ${NT.compliance.forSwitch(id)}
      <details open><summary>Interface status (${up} up of ${sw.interfaces.length})</summary><div>
        <div class="key"><span><i style="background:var(--up)"></i>up</span><span><i style="background:var(--down)"></i>down</span><span><i style="background:var(--unknown)"></i>no status in backup</span></div>
        <div class="ports">${sw.interfaces.map((i, k) => `<span class="port ${i.status}" data-i="${k}" tabindex="0">${esc(this.short(i.name))}</span>`).join("")}</div></div></details>`;
    document.getElementById("pclose").onclick = () => this.close();
    this.bindPorts(el, sw);
  },
  // Hover (or keyboard focus) on a port chip shows that port's status details and its configuration from the backup.
  bindPorts(el, sw) {
    const tip = document.getElementById("tip"), esc = NT.esc;
    const place = (x, y, chip) => {
      const r = chip.getBoundingClientRect();
      x = x ?? r.left; y = y ?? r.bottom;
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let left = x - w - 14;                                   // the panel is on the right, so open the tip towards the left
      if (left < 8) left = Math.min(x + 14, window.innerWidth - w - 8);
      let top = y + 14;
      if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - h - 8);
      tip.style.left = left + "px"; tip.style.top = top + "px";
    };
    const show = (chip, ev) => {
      const i = sw.interfaces[+chip.dataset.i];
      const meta = [`status: ${i.status}${i.admin_down ? " (shut down)" : ""}`, i.speed && `speed ${i.speed}`, i.duplex && `duplex ${i.duplex}`,
        i.port_type && `type ${i.port_type}`, i.media && (i.media === "fiber" ? "fibre / SFP" : "copper"), i.mode && `mode ${i.mode}`,
        i.vlan && `VLAN ${i.vlan}`, i.channel_group && `in ${this.short(i.channel_group)}`].filter(Boolean);
      tip.className = "tip cfg";
      tip.innerHTML = `<b>${esc(i.name)}</b><div class="meta">${meta.map(esc).join(" · ")}</div>` +
        `<pre>${esc(i.config || "(no configuration lines for this port in the backup: it is at default settings)")}</pre>`;
      tip.hidden = false;
      place(ev ? ev.clientX : null, ev ? ev.clientY : null, chip);
    };
    const hide = () => { tip.hidden = true; tip.className = "tip"; };
    el.querySelectorAll(".port").forEach(chip => {
      chip.addEventListener("mouseenter", e => show(chip, e));
      chip.addEventListener("mousemove", e => place(e.clientX, e.clientY, chip));
      chip.addEventListener("mouseleave", hide);
      chip.addEventListener("focus", () => show(chip, null));
      chip.addEventListener("blur", hide);
    });
  },
  short(n) {
    return n.replace(/^TenGigabitEthernet/, "Te").replace(/^GigabitEthernet/, "Gi").replace(/^FastEthernet/, "Fa").replace(/^Port-channel/, "Po").replace(/^Ten-GigabitEthernet/, "XGE");
  },
  close() { document.getElementById("panel").hidden = true; NT.diagram.select(null); },
};
