const $ = (id) => document.getElementById(id);
const drop = $("drop"), fileInput = $("file");
const chip = $("chip"), go = $("go"), statusEl = $("status");
const fromSel = $("fromSel"), toSel = $("toSel");

let PAIRS = [];          // [{from, to}, ...]
let SOURCES = {};        // ext -> [targets]
let currentFile = null;

const MAX_BYTES = 50 * 1024 * 1024;

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i + 1).toLowerCase();
}

function setStatus(msg, cls = "") {
  statusEl.textContent = "";
  if (msg) {
    if (cls === "loading") {
      const s = document.createElement("span");
      s.className = "spinner";
      statusEl.appendChild(s);
      statusEl.appendChild(document.createTextNode(msg));
    } else {
      statusEl.textContent = msg;
    }
  }
  statusEl.className = "status " + (cls === "loading" ? "" : cls);
}

function fillFrom() {
  fromSel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = ""; ph.textContent = "Choose a format…"; ph.disabled = true; ph.selected = true;
  fromSel.appendChild(ph);
  for (const ext of Object.keys(SOURCES).sort()) {
    const o = document.createElement("option");
    o.value = ext; o.textContent = ext.toUpperCase();
    fromSel.appendChild(o);
  }
  fillTo();
}

function fillTo() {
  const prev = toSel.value;
  toSel.innerHTML = "";
  for (const t of (SOURCES[fromSel.value] || [])) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t.toUpperCase();
    toSel.appendChild(o);
  }
  if ([...toSel.options].some((o) => o.value === prev)) toSel.value = prev;
  syncUi();
}

function syncUi() {
  const ok = !!currentFile && currentFile.size <= MAX_BYTES &&
    !!SOURCES[extOf(currentFile.name)] &&
    SOURCES[extOf(currentFile.name)].includes(toSel.value) &&
    fromSel.value === extOf(currentFile.name);
  go.disabled = !ok;
}

function setFile(f) {
  currentFile = f;
  if (!f) {
    chip.classList.remove("show");
    setStatus("");
    fromSel.value = "";
    fillTo();
    return;
  }
  const ext = extOf(f.name);
  chip.classList.add("show");
  $("chipExt").textContent = ext || "?";
  $("chipName").textContent = f.name;
  $("chipSize").textContent = fmtSize(f.size);

  if (!SOURCES[ext]) {
    setStatus(`Unsupported file type “${ext || "?"}”. Try one of the formats below.`, "err");
  } else if (f.size > MAX_BYTES) {
    setStatus(`File is ${fmtSize(f.size)} — the limit is 50 MB.`, "err");
  } else {
    fromSel.value = ext;
    fillTo();
    setStatus("");
  }
  syncUi();
}

function renderAvailable() {
  const el = $("available");
  el.innerHTML = "<h2>Supported conversions</h2>";
  const rows = document.createElement("div");
  rows.className = "rows";
  for (const src of Object.keys(SOURCES).sort()) {
    if (src === "jpeg") continue; // shown as JPG
    const row = document.createElement("div");
    row.className = "row";
    const targets = [...new Set(SOURCES[src])];
    const b = document.createElement("b"); b.textContent = src.toUpperCase();
    const t = document.createElement("span"); t.textContent = targets.map((x) => x.toUpperCase()).join(", ");
    row.append(b, t);
    rows.appendChild(row);
  }
  el.appendChild(rows);
  $("dropHint").textContent = "Up to 50 MB. Supported formats are listed below.";
}

async function loadFormats() {
  let data;
  try {
    const res = await fetch("/api/formats");
    if (!res.ok) throw new Error(res.statusText);
    data = await res.json();
  } catch (err) {
    setStatus("Couldn’t load the format list — is the FileForge server running?", "err");
    return;
  }
  PAIRS = data.pairs;
  SOURCES = {};
  for (const p of PAIRS) (SOURCES[p.from] ||= []).push(p.to);
  fillFrom();
  renderAvailable();
  syncUi();
}

async function convert() {
  if (!currentFile) return;
  const from = fromSel.value, to = toSel.value;
  go.disabled = true;
  setStatus(`Converting ${from.toUpperCase()} to ${to.toUpperCase()}…`, "loading");

  const fd = new FormData();
  fd.append("file", currentFile);
  try {
    const res = await fetch(
      `/api/convert?fmt_from=${encodeURIComponent(from)}&fmt_to=${encodeURIComponent(to)}`,
      { method: "POST", body: fd }
    );
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const blob = await res.blob();
    const name = res.headers.get("X-Download-Filename") ||
      currentFile.name.replace(/\.[^.]+$/, "") + "." + to;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    setStatus(`Done — saved ${name}`, "ok");
  } catch (err) {
    setStatus(err.message || "Conversion failed", "err");
  } finally {
    syncUi();
  }
}

// events
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
// a file dropped outside the zone would otherwise make the browser navigate to it
for (const ev of ["dragover", "drop"]) window.addEventListener(ev, (e) => e.preventDefault());
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
$("chipX").addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.value = "";
  setFile(null);
});
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) {
    fileInput.files = e.dataTransfer.files; setFile(f);
    if (e.dataTransfer.files.length > 1 && !statusEl.textContent) setStatus("Using the first file only — one file at a time.");
  }
});
fromSel.addEventListener("change", () => {
  // switching the source must match the chosen file
  if (currentFile && extOf(currentFile.name) !== fromSel.value) {
    const chosen = fromSel.value;
    setFile(null); fileInput.value = "";
    fromSel.value = chosen;  // keep the format the user just picked
    setStatus("Pick a file that matches the selected format.", "");
  }
  fillTo();
});
toSel.addEventListener("change", syncUi);
go.addEventListener("click", convert);

loadFormats();
