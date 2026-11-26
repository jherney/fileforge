const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness");

test("initial state: placeholders and Convert disabled", async () => {
  const u = await load();
  assert.equal(u.els.fromSel.value, "");
  assert.equal(u.els.toSel.value, "");
  assert.equal(u.els.go.disabled, true);
  assert.equal(u.els.fromSel._options()[0].textContent, "Choose a format…");
  assert.equal(u.els.toSel._options()[0].textContent, "Choose a format…");
  assert.equal(u.status(), "");
});

test("format list load failure shows an error", async () => {
  const u = await load({ fetchOk: false });
  assert.match(u.status(), /Couldn’t load the format list/);
  assert.equal(u.els.go.disabled, true);
});

test("loading a PNG sets From=png, To defaults to pdf, Convert enabled", async () => {
  const u = await load();
  u.pick("a.png");
  assert.equal(u.els.fromSel.value, "png");
  assert.equal(u.els.toSel.value, "pdf");
  assert.equal(u.els.go.disabled, false);
  assert.equal(u.hasChipError(), false);
  assert.equal(u.els.chipExt.textContent, "png");
});

test("no default target when pdf/png/jpg are not offered directly", async () => {
  const u = await load();
  u.pick("a.csv");
  assert.equal(u.els.fromSel.value, "csv");
  assert.equal(u.els.toSel.value, "");
  assert.equal(u.els.go.disabled, true);
});

test("From mismatch keeps the file, warns, disables Convert", async () => {
  const u = await load();
  u.pick("a.png");
  u.setFrom("docx");
  assert.equal(u.eval("currentFile.name"), "a.png");
  assert.match(u.status(), /The file is PNG, not DOCX/);
  assert.match(u.els.status.className, /err/);
  assert.equal(u.els.go.disabled, true);
});

test("setting From back restores the default To and clears the warning", async () => {
  const u = await load();
  u.pick("a.png");
  u.setFrom("docx");
  assert.equal(u.els.go.disabled, true);
  u.setFrom("png");
  assert.equal(u.status(), "");
  assert.equal(u.els.toSel.value, "pdf");
  assert.equal(u.els.go.disabled, false);
});

test("rejected files reset From/To, flag the chip, and list supported types", async () => {
  for (const [name, size, re] of [
    ["x.xyz", 10, /Unsupported file type “xyz”/],
    ["noext", 10, /Unsupported file type “none”/],
    ["big.png", 50 * 1024 * 1024 + 1, /limit is 50 MB/],
  ]) {
    const u = await load();
    u.pick("ok.png");                       // a valid earlier file must not leak through
    assert.equal(u.els.go.disabled, false);
    u.pick(name, size);
    assert.equal(u.els.fromSel.value, "", name);
    assert.equal(u.els.toSel.value, "", name);
    assert.equal(u.hasChipError(), true, name);
    assert.equal(u.els.go.disabled, true, name);
    assert.match(u.status(), re, name);
    assert.match(u.els.status.className, /err/);
    if (!name.startsWith("big")) { assert.match(u.status(), /Supported: .*PDF.*PNG/); assert.doesNotMatch(u.status(), /JPEG|TIF,/); }
  }
});

test("exactly 50 MB is accepted; a valid file after a rejected one recovers", async () => {
  const u = await load();
  u.pick("big.png", 50 * 1024 * 1024);
  assert.equal(u.hasChipError(), false);
  assert.equal(u.els.go.disabled, false);
  u.pick("x.xyz");
  assert.equal(u.hasChipError(), true);
  u.pick("b.jpg");
  assert.equal(u.hasChipError(), false);
  assert.equal(u.els.fromSel.value, "jpg");
  assert.equal(u.els.toSel.value, "pdf");
  assert.equal(u.status(), "");
});

test("changing To clears the status", async () => {
  const u = await load();
  u.pick("a.png");
  u.setFrom("docx");
  assert.notEqual(u.status(), "");
  u.setTo("");
  assert.equal(u.status(), "");
  assert.equal(u.els.status.className.trim(), "status");
});

test("removing the file resets everything", async () => {
  const u = await load();
  u.pick("a.png");
  u.clear();
  assert.equal(u.els.fromSel.value, "");
  assert.equal(u.els.toSel.value, "");
  assert.equal(u.els.go.disabled, true);
  assert.equal(u.els.chip.classList.contains("show"), false);
});

test(".jpeg selects the single 'JPG / JPEG' entry", async () => {
  const u = await load();
  u.pick("a.jpeg");
  assert.equal(u.els.fromSel.value, "jpg");
  assert.equal(u.optText(u.els.fromSel, "jpg"), "JPG / JPEG");
  assert.equal(u.els.toSel.value, "pdf");
  assert.equal(u.els.go.disabled, false);
  u.pick("A.JPG");
  assert.equal(u.els.fromSel.value, "jpg");
});

test(".tif selects the 'TIFF / TIF' entry", async () => {
  const u = await load();
  u.pick("scan.TIF");
  assert.equal(u.els.fromSel.value, "tiff");
  assert.equal(u.optText(u.els.fromSel, "tiff"), "TIFF / TIF");
  assert.equal(u.els.go.disabled, false);
});

test("From list hides alias entries", async () => {
  const u = await load();
  const vals = u.opts(u.els.fromSel);
  assert.ok(vals.includes("jpg") && vals.includes("tiff"));
  assert.ok(!vals.includes("jpeg") && !vals.includes("tif"));
  assert.equal(vals.filter((v) => v === "jpg").length, 1);
});

test("a chained target is not carried over to a new file", async () => {
  const u = await load();
  u.pick("a.docx");
  assert.deepEqual(u.chainedTo(), ["png"]);
  assert.match(u.optText(u.els.toSel, "png"), /PNG \(via PDF\)/);
  u.setTo("png");
  assert.equal(u.els.toSel.value, "png");
  assert.equal(u.els.go.disabled, false);
  u.pick("b.docx");  // docx>png is chained, so it is not kept: the default applies
  assert.equal(u.els.toSel.value, "pdf");
  // a chained choice on pdf>jpg is dropped when switching to a file where it is chained too
  u.pick("c.pdf");
  u.setTo("jpg");
  assert.equal(u.els.toSel.value, "jpg");
  u.pick("d.pdf");
  assert.equal(u.els.toSel.value, "png");
});

test("a direct target is kept only when the new file's format also offers it directly", async () => {
  const u = await load();
  u.pick("a.png");
  u.setTo("webp");
  u.pick("b.jpg");   // jpg has no webp target here -> default applies
  assert.equal(u.els.toSel.value, "pdf");
});
