const fs = require('node:fs');
const path = require('node:path');
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
const assets = {r770:asset('Dell PowerEdge R770 rack server'), xe7740:asset('Dell PowerEdge XE7740 rack server'), xeon:asset('Intel Xeon processors'), dell:asset('Dell Technologies')};
const events = process.argv[2] || 'data/capacity/replay/enterprise-v23-12101.json';
const data = JSON.parse(fs.readFileSync(path.resolve(root, events), 'utf8'));
const results = JSON.parse(fs.readFileSync(path.join(root, 'docs/whitepaper/paper-results.json'), 'utf8'));
if (!data.plateaus.some(p=>p.keeps_up&&p.resident===results.capacity.resident)) throw new Error('Replay capacity does not match paper-results.json. Supply the matching event file.');
if (data.meta.sids.join(',')!=='task_ticket,deep_research,ingestion,analyst_large,code_agent') throw new Error('Replay archetype order changed. Update the conference lane mapping before building.');
// Select a continuous recorded interval. Never fill a gap with invented agents.
const plateauIndex=data.plateaus.findIndex(p=>p.keeps_up&&p.resident===results.capacity.resident);
const plateau=data.plateaus[plateauIndex], windowStart=plateau.t0+600, windowEnd=plateau.t1;
const units=data.units.filter(u=>u[0]===plateauIndex&&u[1]>=0);
const counts=data.meta.sids.map((_,i)=>units.filter(u=>u[1]===i&&u[2]<=windowStart&&(u[3]==null||u[3]>windowStart)).length);
const changes=new Map();
for(const u of units)for(const [time,delta] of [[u[2],1],[u[3],-1]]){
  if(time==null||time<=windowStart||time>=windowEnd)continue;
  if(!changes.has(time))changes.set(time,counts.map(()=>0));
  changes.get(time)[u[1]]+=delta;
}
let runStart=counts.every(n=>n>0)?windowStart:null,best=null;
function consider(end){if(runStart!=null&&(!best||end-runStart>best.end-best.start))best={start:runStart,end};}
for(const [time,deltas] of [...changes].sort((a,b)=>a[0]-b[0])){
  deltas.forEach((delta,i)=>counts[i]+=delta);
  if(counts.every(n=>n>0)){if(runStart==null)runStart=time;}
  else if(runStart!=null){consider(time);runStart=null;}
}
consider(windowEnd);
if(!best||best.end-best.start<60)throw new Error('No recorded interval of at least 60 seconds keeps all five archetypes resident. Select another capacity recording.');
data.conferenceWindow={...best,plateauIndex,selection:'Longest continuous post-warmup capacity interval with all five archetypes resident',defaultSpeed:1};
const json = o => JSON.stringify(o).replace(/</g, '\\u003c');
let html = fs.readFileSync(path.join(base, 'steady-state.src.html'), 'utf8');
html = html.replace('/*__DATA__*/null', json(data)).replace('/*__RESULTS__*/null', json(results)).replace('/*__ASSETS__*/null', json(assets)).replace('/*__FONTS__*/', [...paper.matchAll(/@font-face\s*\{[^}]+\}/g)].map(m => m[0]).join('\n'));
fs.writeFileSync(path.join(base, 'steady-state.html'), html);
console.log(`Built offline conference replay with ${data.units.length} workflow records and ${results.resultVersion} paper results.`);
console.log(`Perpetual steady-state segment: ${(best.start-plateau.t0).toFixed(1)}–${(best.end-plateau.t0).toFixed(1)} seconds into capacity hold, ${(best.end-best.start).toFixed(1)} seconds at 1×.`);
