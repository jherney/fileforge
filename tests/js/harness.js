// Minimal DOM stub + loader for static/app.js. Only what app.js touches.
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

class El {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {};
    this._cls = new Set(); this._text = ""; this._value = "";
    this.disabled = false; this.selected = false; this.title = ""; this.label = "";
    this.className = "";
    const self = this;
    this.classList = {
      add: (...c) => c.forEach((x) => self._cls.add(x)),
      remove: (...c) => c.forEach((x) => self._cls.delete(x)),
      contains: (c) => self._cls.has(c),
    };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(_) { this.children = []; this._text = ""; if (this.tag === "select") this._value = ""; }
  get innerHTML() { return ""; }
  _options() {
    return this.children.flatMap((c) => (c.tag === "optgroup" ? c.children : [c])).filter((c) => c.tag === "option");
  }
  get value() { return this.tag === "select" ? this._value : this._v ?? ""; }
  set value(v) {
    if (this.tag === "select") this._value = this._options().some((o) => o.value === v) ? v : "";
    else this._v = v;
  }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  remove() {}
  click() { this.dispatch("click"); }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  dispatch(ev, e = {}) { (this.listeners[ev] || []).forEach((fn) => fn({ preventDefault() {}, stopPropagation() {}, ...e })); }
  querySelectorAll(sel) {
    if (sel === ":scope > option") return this.children.filter((c) => c.tag === "option");
    throw new Error("unsupported selector " + sel);
  }
}

const IDS = ["drop", "file", "chip", "go", "status", "fromSel", "toSel", "chipExt", "chipName",
  "chipSize", "chipX", "available", "dropHint", "tagline", "footer"];

const DEFAULT_PAIRS = [
  { from: "png", to: "jpg" }, { from: "png", to: "pdf" }, { from: "png", to: "webp" },
  { from: "jpg", to: "png" }, { from: "jpg", to: "pdf" },
  { from: "jpeg", to: "png" }, { from: "jpeg", to: "pdf" },
  { from: "tiff", to: "png" }, { from: "tiff", to: "pdf" },
  { from: "tif", to: "png" }, { from: "tif", to: "pdf" },
  { from: "heic", to: "jpg" }, { from: "heic", to: "png" }, { from: "heic", to: "pdf" },
  { from: "docx", to: "pdf" }, { from: "docx", to: "png", via: ["pdf"] },
  { from: "pdf", to: "docx" }, { from: "pdf", to: "png" }, { from: "pdf", to: "jpg", via: ["png"] },
  { from: "csv", to: "xlsx" },   // no pdf/png/jpg target: no default
];

async function load({ pairs = DEFAULT_PAIRS, fetchOk = true, extra = {} } = {}) {
  const els = Object.fromEntries(IDS.map((id) => [id, new El(id.endsWith("Sel") ? "select" : "div")]));
  els.go.disabled = true;
  const winListeners = {};
  const ctx = vm.createContext({
    document: {
      getElementById: (id) => els[id],
      createElement: (t) => new El(t),
      createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
      body: new El("body"),
    },
    window: { addEventListener: (ev, fn) => (winListeners[ev] ||= []).push(fn) },
    fetch: async () => ({ ok: fetchOk, statusText: "err", json: async () => ({ pairs, ...extra }) }),
    FormData: class { append() {} },
    URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
    setTimeout, console,
  });
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "app.js"), "utf8");
  vm.runInContext(src, ctx, { filename: "app.js" });
  await new Promise((r) => setImmediate(r));   // let loadFormats() finish

  const api = {
    els,
    eval: (code) => vm.runInContext(code, ctx),
    // pick a file as the browser would
    pick(name, size = 1000) {
      els.file.files = [{ name, size }];
      els.file.dispatch("change");
    },
    clear() { els.chipX.dispatch("click"); },
    setFrom(v) { els.fromSel.value = v; els.fromSel.dispatch("change"); },
    setTo(v) { els.toSel.value = v; els.toSel.dispatch("change"); },
    opts: (sel) => sel._options().map((o) => o.value),
    directTo: () => els.toSel.children.filter((c) => c.tag === "option").map((o) => o.value),
    chainedTo: () => els.toSel.children.filter((c) => c.tag === "optgroup").flatMap((g) => g.children.map((o) => o.value)),
    optText: (sel, v) => sel._options().find((o) => o.value === v)?.textContent,
    status: () => els.status.textContent,
    hasChipError: () => els.chip.classList.contains("error"),
  };
  return api;
}

module.exports = { load };
