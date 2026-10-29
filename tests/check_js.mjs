// Syntax-checks every module in web/js (node --check cannot see some errors in ES modules).
//   node tests/check_js.mjs
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'web', 'js');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
let bad = 0;

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p);
    else if (name.endsWith('.js')) {
      // drop import lines (single or multi-line) and the export keyword, then let the engine parse the rest
      const src = readFileSync(p, 'utf8').replace(/^import[\s\S]*?from\s+['"][^'"]+['"];?$/gm, '').replace(/^export (default )?(async )?/gm, '$2');
      try { new AsyncFunction(src); } catch (e) { bad++; console.log(`FAIL ${p.slice(root.length + 1)}: ${e.message}`); }
    }
  }
}
walk(root);

// el.replaceChildren(...) prints null/false and arrays as text ("null", "[object Object]"): use fill() from kit.js when a child can be missing or a list.
function checkReplaceChildren(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { checkReplaceChildren(p); continue; }
    if (!name.endsWith('.js')) continue;
    const src = readFileSync(p, 'utf8');
    for (let at = src.indexOf('.replaceChildren('); at >= 0; at = src.indexOf('.replaceChildren(', at + 1)) {
      let depth = 0, i = at + '.replaceChildren'.length, end = -1;
      for (; i < src.length; i++) {            // find the matching close parenthesis
        const ch = src[i];
        if (ch === '(') depth++;
        else if (ch === ')' && --depth === 0) { end = i; break; }
      }
      const args = src.slice(at + '.replaceChildren('.length, end);
      // top-level only: strip nested (...) and {...} so that "? a : b" inside a child is ignored
      let flat = args, prev;
      do { prev = flat; flat = flat.replace(/\([^()]*\)|\{[^{}]*\}|\[[^\[\]]*\]|`[^`]*`|'[^']*'/g, '_'); } while (flat !== prev);
      if (/\bnull\b|\bfalse\b/.test(flat) && !/^\s*\.\.\./.test(flat)) {
        bad++;
        console.log(`FAIL ${p.slice(root.length + 1)}: replaceChildren() may receive null/false; use fill() from kit.js`);
      }
    }
  }
}
checkReplaceChildren(root);

// Imported names that are never used are dead code (and hide typos).
function checkUnusedImports(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { checkUnusedImports(p); continue; }
    if (!name.endsWith('.js')) continue;
    const src = readFileSync(p, 'utf8');
    for (const m of src.matchAll(/^import\s*\{([^}]*)\}\s*from\s*['"][^'"]+['"];?$/gm)) {
      for (const raw of m[1].split(',')) {
        const id = raw.trim().split(/\s+as\s+/).pop().trim();
        if (id && !new RegExp('\\b' + id + '\\b').test(src.replace(m[0], ''))) {
          bad++;
          console.log(`FAIL ${p.slice(root.length + 1)}: '${id}' is imported but never used`);
        }
      }
    }
  }
}
checkUnusedImports(root);
console.log(bad ? `${bad} file(s) with syntax errors` : 'all JavaScript modules parse');
process.exit(bad ? 1 : 0);
