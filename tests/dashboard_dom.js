// Führt das Skript einer erzeugten Dashboard-Datei in Node aus, mit einem kleinen Ersatz für das DOM, und bedient
// Umschalter und Pfade wie im Browser. Aufruf: node dashboard_dom.js <datei.html>; Ausgabe: JSON je Umschalter-Stellung.
"use strict";
const fs = require("fs"), vm = require("vm");

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.style = {}; this.on = {};
    this.className = ""; this.hidden = false; this.own = ""; this.innerHTML = ""; this.scrollTop = 0; this.value = "";
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
  }
  get options() { return this.kids; }
  append(...kids) { this.kids.push(...kids); }
  replaceChildren(...kids) { this.kids = kids; }
  get textContent() { return this.own + this.kids.map(k => (k instanceof El ? k.textContent : String(k))).join(""); }
  set textContent(v) { this.own = v; this.kids = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(type, f) { (this.on[type] = this.on[type] || []).push(f); }
  click() { for (const f of this.on.click || []) f({ target: this, stopPropagation() {}, preventDefault() {} }); }
  querySelector(sel) { return sel.startsWith(".") ? new El("div") : null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  contains() { return false; }
  focus() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }; }
  get parentElement() { return new El("div"); }
  getScreenCTM() { return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }; }
  find(test) {
    if (test(this)) return this;
    for (const k of this.kids) { const hit = k instanceof El && k.find(test); if (hit) return hit; }
    return null;
  }
}

const html = fs.readFileSync(process.argv[2], "utf-8");
const data = /<script type="application\/json" id="okf-data">(.*?)<\/script>/s.exec(html)[1];
const code = [...html.matchAll(/<script(?: nonce="[^"]*")?>(.*?)<\/script>/gs)].pop()[1];
const byId = new Map(), bySel = new Map();
const pick = (map, key) => { if (!map.has(key)) map.set(key, new El("div")); return map.get(key); };
pick(byId, "okf-data").textContent = data;
pick(byId, "pfade").hidden = true;

const store = new Map();
let edit = null;
const sandbox = {
  document: {
    documentElement: new El("html"), body: new El("body"),
    getElementById: id => pick(byId, id), querySelector: sel => pick(bySel, sel),
    createElement: tag => new El(tag), createElementNS: (ns, tag) => new El(tag), addEventListener() {},
  },
  window: { okfwEdit: { reader() {}, panel() {}, release() {}, ready(api) { edit = api; } }, addEventListener() {} },
  localStorage: { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) },
  ResizeObserver: class { observe() {} }, matchMedia: () => ({ matches: false }), innerWidth: 1200, innerHeight: 800, navigator: {}, URL, console,
};
vm.runInNewContext(code, sandbox);

const $ = id => pick(byId, id), byClass = (root, cls) => root.find(e => e.className === cls);
const buttons = () => $("scope").kids;

// Was „Lesen“ je Pfad in der gerade gewählten Stellung bekäme
const bekommen = [];
sandbox.window.okfwPfad = { lesen: p => bekommen.push(p), starten: p => bekommen.push(p) };
function pfade() {
  return $("pfade").find(e => e.tagName === "UL").kids.map(li => {
    bekommen.length = 0;
    const lesen = li.find(e => e.tagName === "BUTTON" && e.textContent === "Lesen");
    lesen.click();
    return { titel: li.find(e => e.tagName === "STRONG").textContent, zeile: li.find(e => e.tagName === "SMALL").textContent,
             gesperrt: !!lesen.disabled, befunde: bekommen[0].befunde.map(b => b.karte),
             stationen: bekommen[0].stationen.map(s => [s.titel, s.elemente.map(e => e.titel || e.art)]) };
  });
}
const ergebnis = { start: pfade(), stellungen: {} };
for (const b of buttons()) {
  b.click();
  ergebnis.stellungen[b.textContent] = pfade();
}
console.log(JSON.stringify(ergebnis));
