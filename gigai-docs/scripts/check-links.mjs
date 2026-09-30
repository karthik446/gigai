#!/usr/bin/env node
// Offline broken-link check over a BUILT static site (any generator).
// Resolves every same-site href/src in every .html file against the file's URL and
// checks the target exists on disk (dir/ -> dir/index.html). External links are skipped
// (a scheduled lychee job can cover those). Exits 1 on any broken link.
//
// usage: check-links.mjs <site-dir> [--base /gigai/0.1.10/]
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const [dir, ...rest] = process.argv.slice(2);
const base = rest[0] === '--base' ? rest[1] : '/';
if (!dir) throw new Error('usage: check-links.mjs <site-dir> [--base /prefix/]');

const walk = (d) => readdirSync(d).flatMap((n) => {
  const p = join(d, n);
  return statSync(p).isDirectory() ? walk(p) : p.endsWith('.html') ? [p] : [];
});

const exists = (urlPath) => {
  if (!urlPath.startsWith(base)) return false;
  const local = join(dir, decodeURIComponent(urlPath.slice(base.length)));
  if (urlPath.endsWith('/')) return existsSync(join(local, 'index.html'));
  return existsSync(local) || existsSync(join(local, 'index.html'));
};

let broken = 0;
let checked = 0;
for (const file of walk(dir)) {
  const html = readFileSync(file, 'utf8');
  const pageUrl = new URL(base + relative(dir, file).replace(/index\.html$/, ''), 'http://site.invalid');
  for (const [, attr] of html.matchAll(/\s(?:href|src)="([^"#?][^"]*)"/g)) {
    if (/^(?:[a-z]+:|\/\/)/i.test(attr) || attr.includes('${')) continue; // external / templated
    const target = new URL(attr, pageUrl);
    checked++;
    if (!exists(target.pathname)) {
      broken++;
      console.error(`${relative(dir, file)}: broken link ${attr} -> ${target.pathname}`);
    }
  }
}
console.log(`${checked} internal links checked, ${broken} broken`);
process.exit(broken ? 1 : 0);
