const $ = (id) => document.getElementById(id);
const drop = $("drop"), fileInput = $("file");
const chip = $("chip"), go = $("go"), statusEl = $("status");
const fromSel = $("fromSel"), toSel = $("toSel");

let PAIRS = [];          // [{from, to}, ...]
let SOURCES = {};        // ext -> [targets]
let VIA = {};            // "src>dst" -> middle formats, for pairs converted in more than one step
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

// .jpeg files are converted as JPG
const srcKey = (ext) => (ext === "jpeg" ? "jpg" : ext);
const DEFAULT_TARGETS = ["pdf", "png", "jpg"];

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
  for (const ext of Object.keys(SOURCES).filter((e) => e !== "jpeg").sort()) {
    const o = document.createElement("option");
    o.value = ext; o.textContent = ext === "jpg" ? "JPG / JPEG" : ext.toUpperCase();
    fromSel.appendChild(o);
  }
  fillTo();
}

function fillTo() {
  const prev = toSel.value;
  toSel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = ""; ph.textContent = "Choose a format…"; ph.disabled = true; ph.selected = true;
  toSel.appendChild(ph);
  const chained = document.createElement("optgroup");
  chained.label = "Converted in steps";
  for (const t of (SOURCES[fromSel.value] || [])) {
    const via = VIA[fromSel.value + ">" + t];
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t.toUpperCase() + (via ? ` (via ${via.map((v) => v.toUpperCase()).join(", ")})` : "");
    (via ? chained : toSel).appendChild(o);
  }
  if (chained.children.length) toSel.appendChild(chained);
  // keep the earlier choice only if it is a direct target of this format
  const direct = [...toSel.querySelectorAll(":scope > option")].map((o) => o.value).filter(Boolean);
  if (direct.includes(prev)) toSel.value = prev;
  syncUi();
}

function syncUi() {
  const key = currentFile ? srcKey(extOf(currentFile.name)) : "";
  const ok = !!currentFile && currentFile.size <= MAX_BYTES &&
    !!SOURCES[key] && SOURCES[key].includes(toSel.value) && fromSel.value === key;
  go.disabled = !ok;
}

function setFile(f) {
  currentFile = f;
  if (!f) {
    chip.classList.remove("show", "error");
    setStatus("");
    fromSel.value = "";
    fillTo();
    return;
  }
  const ext = extOf(f.name);
  chip.classList.add("show");
  $("chipExt").textContent = ext || "?";
  $("chipName").textContent = f.name;
  $("chipName").title = f.name;
  $("chipSize").textContent = fmtSize(f.size);

  const key = srcKey(ext);
  chip.classList.remove("error");
  if (!SOURCES[key] || f.size > MAX_BYTES) {
    chip.classList.add("error");
    fromSel.value = "";  // don't leave the previous file's formats selected
    fillTo();
    setStatus(!SOURCES[key]
      ? `Unsupported file type “${ext || "none"}”. Supported: ${Object.keys(SOURCES).filter((e) => e !== "jpeg").sort().map((e) => e.toUpperCase()).join(", ")}.`
      : `File is ${fmtSize(f.size)} — the limit is 50 MB.`, "err");
  } else {
    fromSel.value = key;
    fillTo();
    if (!toSel.value) {
      const direct = [...toSel.querySelectorAll(":scope > option")].map((o) => o.value);
      toSel.value = DEFAULT_TARGETS.find((t) => direct.includes(t)) || "";
    }
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
    const targets = [...new Set(SOURCES[src])].filter((t) => !VIA[src + ">" + t]);
    const b = document.createElement("b"); b.textContent = src.toUpperCase();
    const t = document.createElement("span"); t.textContent = targets.map((x) => x.toUpperCase()).join(", ");
    row.append(b, t);
    rows.appendChild(row);
  }
  el.appendChild(rows);
  const note = document.createElement("p");
  note.textContent = "Where there is no direct converter, FileForge converts in steps, for example Word to PNG by way of PDF. Those targets are listed under “Converted in steps” once you pick a file.";
  el.appendChild(note);
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
  VIA = {};
  for (const p of PAIRS) {
    (SOURCES[p.from] ||= []).push(p.to);
    if (p.via) VIA[p.from + ">" + p.to] = p.via;
  }
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
  // the source must match the chosen file: keep the file, say what to do
  const key = currentFile ? srcKey(extOf(currentFile.name)) : "";
  if (currentFile && key !== fromSel.value) {
    setStatus(`The file is ${key.toUpperCase() || "of unknown type"}, not ${fromSel.value.toUpperCase()}. Set “From” back to ${key.toUpperCase()}, or choose another file.`, "err");
  } else {
    setStatus("");
  }
  fillTo();
});
toSel.addEventListener("change", () => { setStatus(""); syncUi(); });
go.addEventListener("click", convert);

loadFormats();
