// Static check for English mode: every Arabic text fragment in app.js / index.html, run through the app's own
// trString() with i18n-en.js. Prints JSON [[line, fragment, result]] for fragments that would stay Arabic.
// usage: node tests/i18n_gaps.js <repo>
const fs = require("fs"), path = require("path"), vm = require("vm");
const repo = process.argv[2];
const js = fs.readFileSync(path.join(repo, "app/web/app.js"), "utf8");
const html = fs.readFileSync(path.join(repo, "app/web/index.html"), "utf8");
const ctx = { window: {} }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(repo, "app/web/i18n-en.js"), "utf8"), ctx);
const dict = ctx.window.I18N_EN;
// the app's own translation code, cut from app.js
const cut = (a, b) => js.slice(js.indexOf(a), js.indexOf(b));
const code = cut("const AR = /", "const SKIP = new Set") ;
const setup = js.slice(js.indexOf("    const dict = window.I18N_EN || {};"), js.indexOf("  I18N.on = true; h.lang"));
const prog = `const I18N = { on: true, map: null, re: null };\n${code}\nfunction init(window){\n${setup.replace(/^\s*}\s*$/m, "")}\n}\ninit(window);\nglobalThis.trString = trString;`;
const c2 = { window: { I18N_EN: dict } }; vm.createContext(c2);
try { vm.runInContext(prog, c2); } catch (e) { console.error(prog.slice(-600)); throw e; }
const tr = c2.trString;
const AR = /[؀-ۿ]/;
// tokenizer: collect string / template literal texts with ${} replaced by "0"
const frags = [];
function lineOf(i, src) { return src.slice(0, i).split("\n").length; }
function scan(src) {
  let i = 0; const n = src.length;
  function readTemplate() {           // at char after `
    let out = "", start = i;
    while (i < n) {
      const ch = src[i];
      if (ch === "\\") { out += src[i + 1]; i += 2; continue; }
      if (ch === "`") { i++; frags.push([lineOf(start, src), out]); return; }
      if (ch === "$" && src[i + 1] === "{") { i += 2; out += "\u0001"; readCode(1); continue; }
      out += ch; i++;
    }
  }
  function readQuoted(q) {
    let out = "", start = i;
    while (i < n && src[i] !== q) { if (src[i] === "\\") { out += src[i + 1]; i += 2; } else out += src[i++]; }
    i++; frags.push([lineOf(start, src), out]);
  }
  function readCode(depth) {          // until matching } at depth 0 (depth counts braces)
    while (i < n) {
      const ch = src[i];
      if (ch === "/" && src[i + 1] === "/") { while (i < n && src[i] !== "\n") i++; continue; }
      if (ch === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i) + 2; continue; }
      if (ch === "`") { i++; readTemplate(); continue; }
      if (ch === '"' || ch === "'") { i++; readQuoted(ch); continue; }
      if (ch === "/" ) {               // regex literal heuristic: previous non-space is ( , = : [ ! & | ? ; { } or start
        let j = i - 1; while (j >= 0 && /\s/.test(src[j])) j--;
        if (j < 0 || "(,=:[!&|?;{}".includes(src[j])) { i++; while (i < n && src[i] !== "/") { if (src[i] === "\\") i++; else if (src[i] === "[") { while (src[i] !== "]") { if (src[i] === "\\") i++; i++; } } i++; } i++; continue; }
      }
      if (ch === "{") depth++;
      if (ch === "}") { depth--; if (depth === 0) { i++; return; } }
      i++;
    }
  }
  readCode(Infinity);
}
scan(js);
const out = [];
const lineAt = (needle) => js.slice(0, js.indexOf(needle)).split("\n").length;
const tStart = lineAt("const I18N = { on: false"), tEnd = lineAt("const SKIP = new Set");
const check = (line, text) => {
  // split into DOM-ish pieces: tags end text nodes; attribute values are their own texts
  const attrs = [...text.matchAll(/(?:placeholder|title|aria-label|alt|label)="([^"]*)"/g)].map((m) => m[1]);
  const pieces = text.replace(/<[^>]*>/g, "\u0002").split(/[\u0002]/).concat(attrs);
  for (let p of pieces) {
    p = p.replace(/\u0001/g, "0");
    if (!AR.test(p)) continue;
    const t = p.replace(/\s+/g, " ").trim();
    const r = tr(t);
    const res = r == null ? t : r;
    if (AR.test(res)) out.push([line, t, res]);
  }
};
for (const [line, text] of frags) {
  if (line >= tStart && line <= tEnd) continue;      // the translation code itself
  check(line, text);
}
for (const m of html.matchAll(/>([^<]*[؀-ۿ][^<]*)</g)) check("html", m[1]);
for (const m of html.matchAll(/(?:title|aria-label|placeholder)="([^"]*)"/g)) check("html", m[1]);
console.log(JSON.stringify(out));
