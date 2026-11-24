// Layer-3 sections of the details panel: SVIs, routing, ACLs, SNMP traps, DHCP pools / relay.
NT.panelL3 = function (sw) {
  const esc = NT.esc, l3 = sw.l3 || {};
  if (!l3.supported) return `<p class="hint">Layer 3 details are read for Cisco-style CLI (Cisco, Aruba, TP-Link). This vendor is not covered yet.</p>`;
  const open = l3.is_l3 ? "open" : "";
  const tbl = (head, rows) => rows.length ? `<div class="scroll"><table><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr>${rows.join("")}</table></div>` : `<span class="hint">None configured.</span>`;
  const chips = a => (a || []).map(x => `<span class="chip">${esc(x)}</span>`).join("") || "-";

  const svis = tbl(["Interface", "IP / mask", "Description", "DHCP relay", "ACL in / out", "Virtual IP"], l3.svis.map(s =>
    `<tr><td class="mono">${esc(s.name)}${s.shutdown ? ' <span class="hint">(shutdown)</span>' : ""}</td><td class="mono">${esc(s.ip)} / ${esc(s.mask)}${s.vrf ? `<br><span class="hint">vrf ${esc(s.vrf)}</span>` : ""}</td>
     <td>${esc(s.description || "")}</td><td class="mono">${chips(s.helpers)}</td><td class="mono">${esc(s.acl_in || "-")} / ${esc(s.acl_out || "-")}</td><td class="mono">${chips(s.virtual_ip)}</td></tr>`));

  const protos = tbl(["Protocol", "ID", "Router-ID", "Networks / neighbours", "Redistributes", "Notes"], l3.protocols.map(p =>
    `<tr><td><b>${esc(p.protocol)}</b></td><td class="mono">${esc(p.id || "-")}</td><td class="mono">${esc(p.router_id || "-")}</td>
     <td class="mono">${chips([...(p.networks || []), ...(p.neighbors || [])])}</td><td>${esc((p.redistribute || []).join(", ") || "-")}</td><td>${esc((p.notes || []).join("; "))}</td></tr>`));
  const routes = l3.routes.length ? `<h4>Static routes (${l3.routes.length})</h4>` + tbl(["Prefix", "Next hop", "AD", "VRF", "Name"], l3.routes.map(r =>
    `<tr class="${r.default ? "defroute" : ""}"><td class="mono">${esc(r.prefix)}${r.default ? " (default)" : ""}</td><td class="mono">${esc(r.next_hop)}</td><td>${esc(r.ad)}</td><td>${esc(r.vrf || "-")}</td><td>${esc(r.name || "")}</td></tr>`)) : "";

  const acls = l3.acls.length ? l3.acls.map(a => `<details><summary>${esc(a.name)} <span class="hint">${esc(a.type)} · ${a.count} entr${a.count === 1 ? "y" : "ies"} · applied: ${esc((a.applied_to || []).join(", ") || "not applied in this config")}</span></summary>
      <pre class="ace">${esc((a.entries || []).join("\n"))}</pre></details>`).join("") : `<span class="hint">None configured.</span>`;

  const sn = l3.snmp || {};
  const traps = `<h4>Trap receivers</h4>` + tbl(["Host", "Type", "Version"], (sn.hosts || []).map(h => `<tr><td class="mono">${esc(h.host)}</td><td>${esc(h.type)}</td><td>${esc(h.version || "-")}</td></tr>`)) +
    `<h4>Traps enabled</h4><div>${chips(sn.traps_enabled)}</div>` + (sn.trap_source ? `<p class="hint">Trap source interface: ${esc(sn.trap_source)}</p>` : "");

  const dh = l3.dhcp || {};
  const dhcp = `<h4>DHCP pools</h4>` + tbl(["Pool", "Network", "Gateway", "DNS", "Lease"], (dh.pools || []).map(p =>
      `<tr><td class="mono">${esc(p.name)}</td><td class="mono">${esc(p.network)}</td><td class="mono">${esc(p.gateway)}</td><td class="mono">${esc(p.dns)}</td><td>${esc(p.lease)}</td></tr>`)) +
    `<h4>IP helper-address (DHCP relay)</h4>` + tbl(["Interface", "Relays to"], (dh.helpers || []).map(h => `<tr><td class="mono">${esc(h.interface)}</td><td class="mono">${esc(h.address)}</td></tr>`)) +
    ((dh.excluded || []).length ? `<p class="hint">Excluded: ${esc(dh.excluded.join("; "))}</p>` : "") + `<p class="hint">DHCP snooping: ${dh.snooping ? "enabled" : "not configured"}</p>`;

  return `<h4>Layer 3 ${l3.is_l3 ? '<span class="badge l3">L3 / core</span>' : ""}</h4>
    <details ${open}><summary>SVIs and routed interfaces (${l3.svis.length})</summary><div>${svis}</div></details>
    <details ${open}><summary>Routing - IP routing ${l3.routing_enabled ? "enabled" : "not enabled"}, ${l3.protocols.filter(p => p.protocol !== "Static").map(p => p.protocol).join(", ") || "no dynamic protocol"}${l3.routes.length ? `, ${l3.routes.length} static` : ""}</summary><div>${protos}${routes}</div></details>
    <details ${open}><summary>Access lists (${l3.acls.length})</summary><div>${acls}</div></details>
    <details ${open}><summary>SNMP traps (${(sn.hosts || []).length} receiver${(sn.hosts || []).length === 1 ? "" : "s"})</summary><div>${traps}</div></details>
    <details ${open}><summary>DHCP pools (${(dh.pools || []).length}) and relay (${(dh.helpers || []).length})</summary><div>${dhcp}</div></details>`;
};
