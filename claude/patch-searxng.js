// Cut SearXNG search-result noise.
//
// We enable many search engines for redundancy (anti-blocking), so a single
// query returns ~100+ results — and mcp-searxng dumps ALL of them to the LLM,
// which then has to wade through a long tail of near-zero-relevance junk.
// mcp-searxng 1.0.5 has no result cap or score filter, so we patch the copy we
// install ourselves. SearXNG already attaches a relevance `score` to every
// result (and mcp-searxng already carries it through), so we just filter on it:
//
//   SEARXNG_MIN_SCORE   drop results below this relevance score (default 0.3)
//   SEARXNG_MAX_RESULTS keep only the top-N by score          (default 8)
//
// A fallback keeps the top 5 if filtering would return nothing, so a search
// never comes back empty. Idempotent; asserts the target exists so a future
// mcp-searxng bump fails loudly instead of silently skipping the filter.
const fs = require("fs");
const p = "/usr/lib/node_modules/mcp-searxng/dist/search.js";
let s = fs.readFileSync(p, "utf8");
if (s.includes("__noise_filter__")) { console.log("[patch-searxng] already applied"); process.exit(0); }
const re = /return results\s*\n(\s*)\.map\(/;
if (!re.test(s)) { console.error("[patch-searxng] FAILED: target not found — mcp-searxng changed, review this patch"); process.exit(1); }
s = s.replace(re, (m, ind) =>
  "/* __noise_filter__ */" +
  'const _min=parseFloat(process.env.SEARXNG_MIN_SCORE||"0");' +
  'const _max=parseInt(process.env.SEARXNG_MAX_RESULTS||"0",10);' +
  "let _f=results.slice().sort((a,b)=>(b.score||0)-(a.score||0));" +
  "if(_min>0)_f=_f.filter(r=>(r.score||0)>=_min);" +
  "if(_max>0)_f=_f.slice(0,_max);" +
  "if(_f.length===0&&results.length)_f=results.slice(0,5);" +
  "return _f\n" + ind + ".map(");
fs.writeFileSync(p, s);
console.log("[patch-searxng] applied — SEARXNG_MIN_SCORE / SEARXNG_MAX_RESULTS now honoured");
