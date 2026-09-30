#!/usr/bin/env node
// Publish one built docs version into a static site tree, the way `mike` does for MkDocs:
//
//   <site>/[<product>/]<version>/...      the build (built with DOCS_BASE=<pages path>/<version>/)
//   <site>/[<product>/]<alias>/...        redirect stubs, one per page, to the aliased version
//   <site>/[<product>/]versions.json      [{version, title, aliases}]  (mike's format)
//   <site>/[<product>/]index.html         redirect to <default alias>/, or the newest version until it exists
//
// <site> is a checkout of the gh-pages branch. On GitHub Pages the repo name is already the
// path prefix (karthik446.github.io/gigai/), so --product is empty; set it only when several
// products share one site root. Old versions are never rebuilt: a release only adds its own
// folder and moves aliases.
//
// usage: publish-version.mjs --site DIR --version 0.1.10 --dist dist [--product NAME]
//                            [--alias latest] [--title "0.1.10"] [--default latest] [--delete]
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { parseArgs } from 'node:util';

const { values: a } = parseArgs({
  options: {
    site: { type: 'string' }, product: { type: 'string', default: '' },
    version: { type: 'string' }, dist: { type: 'string', default: 'dist' },
    alias: { type: 'string', multiple: true, default: [] }, title: { type: 'string' },
    default: { type: 'string', default: 'latest' }, delete: { type: 'boolean', default: false },
  },
});
if (!a.site || !a.version) throw new Error('--site and --version are required');
if (!/^[A-Za-z0-9._-]+$/.test(a.version)) throw new Error(`bad version ${a.version}`);

const root = join(a.site, a.product);
const manifestPath = join(root, 'versions.json');
let versions = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : [];

// newest first by numeric version parts (0.1.10 > 0.1.9.1 > 0.1.9)
// rolling builds (main, the active version branch) are not releases
const PRE = new Set(['dev', 'next']);
function cmp(x, y) {
  const key = (v) => (PRE.has(v.version) ? [Infinity] : v.version.split('.').map(Number));
  const [p, q] = [key(x), key(y)];
  for (let i = 0; i < Math.max(p.length, q.length); i++) if ((p[i] ?? 0) !== (q[i] ?? 0)) return (q[i] ?? 0) - (p[i] ?? 0);
  return 0;
}

const htmlFiles = (dir) =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? htmlFiles(p) : p.endsWith('.html') ? [p] : [];
  });

const writeRedirect = (file, target) => {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(
    file,
    `<!doctype html><meta charset="utf-8"><title>Redirecting</title>` +
      `<link rel="canonical" href="${target}"><meta http-equiv="refresh" content="0; url=${target}">` +
      `<script>location.replace(${JSON.stringify(target)} + location.hash)</script>`,
  );
};

if (a.delete) {
  rmSync(join(root, a.version), { recursive: true, force: true });
  versions = versions.filter((v) => v.version !== a.version);
} else {
  // 1. the version folder itself (replace in place: a re-publish of "dev" overwrites dev)
  rmSync(join(root, a.version), { recursive: true, force: true });
  mkdirSync(root, { recursive: true });
  cpSync(a.dist, join(root, a.version), { recursive: true });

  // 2. aliases move: drop them from any other version, attach to this one. "latest" never moves
  //    backwards: a hotfix to an older line (0.1.9.2 after 0.1.10) is filed but keeps no alias.
  const holder = versions.find((v) => v.aliases.includes('latest') && v.version !== a.version);
  if (a.alias.includes('latest') && holder && cmp(holder, { version: a.version }) < 0) {
    console.log(`latest stays on ${holder.version} (newer than ${a.version})`);
    a.alias = a.alias.filter((x) => x !== 'latest');
  }
  for (const v of versions) v.aliases = v.aliases.filter((x) => !a.alias.includes(x));
  versions = versions.filter((v) => v.version !== a.version);
  versions.push({ version: a.version, title: a.title ?? a.version, aliases: a.alias });

  // 3. alias folders = redirect stubs for every page, so deep links like /gigai/latest/install/ work
  for (const alias of a.alias) {
    const aliasDir = join(root, alias);
    rmSync(aliasDir, { recursive: true, force: true });
    for (const f of htmlFiles(join(root, a.version))) {
      const rel = relative(join(root, a.version), f);
      const depth = rel.split('/').length; // alias/<...>/index.html -> climb out of alias
      writeRedirect(join(aliasDir, rel), '../'.repeat(depth) + `${a.version}/` + rel.replace(/index\.html$/, ''));
    }
  }
}

// newest first; the rolling builds ("next", "dev") always last
versions.sort(cmp).sort((x, y) => PRE.has(x.version) - PRE.has(y.version));

writeFileSync(manifestPath, JSON.stringify(versions, null, 2) + '\n');
// the root goes to the default alias, or, before that alias exists (first publish, no release yet),
// to the newest version folder that does (a release before "next"/"dev"), so it never dangles
const rootTarget = existsSync(join(root, a.default)) ? a.default : versions[0]?.version;
if (rootTarget) writeRedirect(join(root, 'index.html'), `${rootTarget}/`);
else rmSync(join(root, 'index.html'), { force: true });
console.log(`${a.product || "versions"}: ${versions.map((v) => v.version + (v.aliases.length ? `[${v.aliases}]` : '')).join(', ')}`);
