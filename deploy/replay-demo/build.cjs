/* Stage the built conference pages for Cloudflare. Source of truth stays in docs/dashboard;
   rebuild those first (node docs/dashboard/build-steady-state.cjs) when the demo changes. */
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const src = path.join(root, 'docs/dashboard');
const dist = path.join(__dirname, 'dist');
fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist);
const pages = { 'steady-state.html': ['index.html', 'steady-state.html'], 'agent-replay.html': ['agent-replay.html'] };
for (const [file, names] of Object.entries(pages)) {
  const html = fs.readFileSync(path.join(src, file), 'utf8');
  if (/\/\*__(DATA|RESULTS|SIM_)/.test(html)) throw new Error(`${file} still has unbuilt placeholders; run node docs/dashboard/build-steady-state.cjs first`);
  for (const name of names) fs.writeFileSync(path.join(dist, name), html);
}
// Preview site: keep it out of search indexes.
fs.writeFileSync(path.join(dist, '_headers'), '/*\n  X-Robots-Tag: noindex\n  Cache-Control: no-cache\n');
fs.writeFileSync(path.join(dist, 'robots.txt'), 'User-agent: *\nDisallow: /\n');
console.log('Staged', fs.readdirSync(dist).join(', '), 'in', path.relative(root, dist));
