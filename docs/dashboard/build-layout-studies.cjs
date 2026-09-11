/* Build the layout studies page: alternative compositions of the conference demo,
   rendered from the same paper results, simulation profile and model as steady-state.html.
   The current page is untouched; the studies embed it in a tab for comparison. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = __dirname;
const root = path.resolve(base, '../..');
const paper = fs.readFileSync(path.join(root, 'docs/whitepaper/agent-capacity-whitepaper.html'), 'utf8');
const imageTags = [...paper.matchAll(/<img\b[^>]+>/g)].map(m => m[0]);
function asset(alt) {
  const tag = imageTags.find(t => t.includes(`alt="${alt}"`));
  if (!tag) throw new Error(`Whitepaper image missing: ${alt}`);
  const src = tag.match(/src="([^"]+)"/)[1];
  if (!src.startsWith('data:')) throw new Error(`Expected embedded image: ${alt}`);
  return src;
}
const assets = {r770: asset('Dell PowerEdge R770 rack server'), xe7740: asset('Dell PowerEdge XE7740 rack server'), xeon: asset('Intel Xeon processors'), dell: asset('Dell Technologies')};
const events = process.argv[2] || 'data/capacity/replay/enterprise-v23-12101.json';
const data = JSON.parse(fs.readFileSync(path.resolve(root, events), 'utf8'));
const results = JSON.parse(fs.readFileSync(path.join(root, 'docs/whitepaper/paper-results.json'), 'utf8'));
const profile = JSON.parse(fs.readFileSync(path.join(base, 'simulation-profile.json'), 'utf8'));
if (profile.version !== results.definitionVersion) throw new Error('Simulation profile does not match the current definitions.');
// Same timing evidence as the conference page: successful workflows submitted at least
// 300 seconds into the capacity plateau and completed before its end.
const capacityPlateau = data.plateaus.findIndex(p => p.keeps_up && p.resident === results.capacity.resident);
if (capacityPlateau < 0) throw new Error('Replay capacity does not match paper-results.json.');
profile.timings = results.archetypes.map((a, i) => {
  const p = data.plateaus[capacityPlateau];
  const rows = data.units.filter(u => u[0] === capacityPlateau && u[1] === i && u[4] && u[3] != null && u[2] >= p.t0 + 300 && u[3] <= p.t1);
  if (rows.length < 20) throw new Error('Insufficient timing evidence for ' + a.name);
  const mean = fn => rows.reduce((s, u) => s + fn(u), 0) / rows.length;
  return {name: a.name, samples: rows.length, meanSeconds: mean(u => u[3] - u[2])};
});
profile.sourceHashes = Object.fromEntries(['config/capacity_scenarios.yaml', 'docs/benchmark-methodology.md', events].map(p => [p, crypto.createHash('sha256').update(fs.readFileSync(path.resolve(root, p))).digest('hex')]));
// Recorded host activity in the capacity plateau's steady window, normalized to its means:
// [seconds, CPU factor, memory factor]. The studies breathe with it; they never invent events.
const plateau = data.plateaus[capacityPlateau];
const window = data.samples.filter(s => s[0] === capacityPlateau && s[1] >= plateau.t0 + 600 && s[1] <= plateau.t1 && s[3] > 0 && s[4] > 0);
const meanOf = i => window.reduce((a, s) => a + s[i], 0) / window.length;
const meanCpu = meanOf(3), meanMem = meanOf(4);
const trace = window.map(s => [Number((s[1] - window[0][1]).toFixed(1)), Number((s[3] / meanCpu).toFixed(3)), Number((s[4] / meanMem).toFixed(3))]);
if (trace.length < 60) throw new Error('Too few activity samples in the capacity window');
const json = o => JSON.stringify(o).replace(/</g, '\\u003c');
const currentSource = fs.readFileSync(path.join(base, 'steady-state.src.html'), 'utf8');
const defsMatch = currentSource.match(/const defs=(\[[\s\S]*?\n  \]);/);
if (!defsMatch) throw new Error('Archetype step definitions not found in steady-state.src.html');
let html = fs.readFileSync(path.join(base, 'layout-studies.src.html'), 'utf8');
html = html.replace('/*__DEFS__*/null', () => defsMatch[1]);
html = html.replace('/*__TRACE__*/null', json(trace)).replace('/*__RESULTS__*/null', json(results)).replace('/*__ASSETS__*/null', json(assets)).replace('/*__SIM_PROFILE__*/null', json(profile));
html = html.replace('/*__FONTS__*/', [...paper.matchAll(/@font-face\s*\{[^}]+\}/g)].map(m => m[0]).join('\n'));
html = html.replace('/*__SIM_MODEL__*/', () => fs.readFileSync(path.join(base, 'simulation-model.cjs'), 'utf8'));
if (/\/\*__[A-Z_]+__\*\//.test(html)) throw new Error('Unfilled placeholder in layout studies page');
fs.writeFileSync(path.join(base, 'layout-studies.html'), html);
console.log(`Built layout studies on ${results.resultVersion} results (${(html.length / 1024).toFixed(0)} KB).`);
