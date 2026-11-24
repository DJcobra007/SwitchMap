// Drag-and-drop / file picker and the Analyse button.
(function () {
  const drop = document.getElementById("drop"), input = document.getElementById("files"),
        list = document.getElementById("filelist"), go = document.getElementById("go"),
        msg = document.getElementById("msg");
  let files = [];

  function setFiles(fl) {
    files = [...fl];
    list.innerHTML = files.map(f => `<li>${NT.esc(f.name)}</li>`).join("");
    go.disabled = !files.length;
  }
  input.addEventListener("change", () => setFiles(input.files));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); setFiles(e.dataTransfer.files); });
  drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") input.click(); });

  go.addEventListener("click", async () => {
    go.disabled = true;
    msg.className = "msg";
    msg.textContent = `Analysing ${files.length} file${files.length > 1 ? "s" : ""}...`;
    try {
      NT.data = await NT.analyze(files, document.getElementById("vendor").value);
      const s = NT.data.skipped || [];
      msg.textContent = `Analysed ${NT.data.switches.length} switch${NT.data.switches.length === 1 ? "" : "es"}.` + (s.length ? " Skipped: " + s.join("; ") : "");
      NT.onResults();
    } catch (e) {
      msg.className = "msg err";
      msg.textContent = e.message;
    }
    go.disabled = false;
  });
})();
