// Thin wrapper around the Flask endpoints + shared helpers. Everything hangs off window.NT.
window.NT = {
  data: null,
  esc: s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  async analyze(files, vendor) {
    const fd = new FormData();
    files.forEach(f => fd.append("files", f));
    fd.append("vendor", vendor);
    const r = await fetch("/api/analyze", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({ error: "Unexpected server response." }));
    if (!r.ok) throw new Error(j.error || "Analysis failed.");
    return j;
  },
  save(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  },
  sw(id) { return (NT.data?.switches || []).find(s => s.id === id); },
};
