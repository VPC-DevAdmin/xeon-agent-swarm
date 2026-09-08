/* Generate static result bindings and a portable evidence companion.
 * Usage: node update-paper.cjs [--check]
 * Editing prose outside result markers remains safe. No browser JS is required.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = __dirname;
const result = JSON.parse(fs.readFileSync(path.join(root, 'paper-results.json'), 'utf8'));
const c = result.capacity, serving = result.serving, points = result.responsePoints;
const esc = value => String(value).replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const fmt = value => Number(value).toLocaleString('en-US', {maximumFractionDigits:1});
function requireNumber(value, name, min=0) {
  if (!Number.isFinite(value) || value < min) throw new Error(`Invalid ${name}`);
}
for (const key of ['resident','cpuPercent','memoryGB','callsPerSecond','outputTokensPerSecond','coreMsPerSecond']) requireNumber(c[key],key,0.01);
if(c.cpuPercent>100 || c.memoryGB>result.platform.memoryGB) throw new Error('Capacity exceeds the stated platform');
requireNumber(serving.outputTokensPerSecondPerGpu,'GPU throughput',1);
if(Math.abs(Object.values(c.cpuShares).reduce((a,b)=>a+b,0)-100)>0.01) throw new Error('CPU shares must total 100');
for(const value of Object.values(c.cpuShares)) requireNumber(value,'CPU share');
if(!points.some(p=>p.resident===c.resident && p.sustainable)) throw new Error('Capacity must be a sustainable response point');
points.forEach((p,i)=>{
  requireNumber(p.resident,'response population',1);
  if(i && p.resident<=points[i-1].resident) throw new Error('Response points must be in increasing resident order');
  for(const a of result.archetypes) if(p.p95[a.name]!==null) requireNumber(p.p95[a.name],`${a.name} p95`);
});
for(const a of result.archetypes) for(const key of ['coreMs','modelCalls','outputTokens']) requireNumber(a[key],`${a.name} ${key}`,1);
const gpu = c.outputTokensPerSecond/serving.outputTokensPerSecondPerGpu;
const values = {
  resident:fmt(c.resident), cpu:fmt(c.cpuPercent), memory:fmt(c.memoryGB), calls:fmt(c.callsPerSecond),
  output:fmt(c.outputTokensPerSecond), outputRounded:fmt(Math.round(c.outputTokensPerSecond/100)*100),
  gpu:gpu.toFixed(1), gpuExact:gpu.toFixed(2), gpuRate:fmt(serving.outputTokensPerSecondPerGpu),
  coreRate:fmt(c.coreMsPerSecond/1000)+'K', weight:(c.coreMsPerSecond/c.outputTokensPerSecond).toFixed(1),
  cores:fmt(result.platform.cores),
  versionNote:result.resultVersion===result.definitionVersion
    ? `The capacity figures and agent flows use ${esc(result.resultVersion)}.`
    : `The capacity figures are the completed ${esc(result.resultVersion)} results. The task flow below shows the revised ${esc(result.definitionVersion)} handoff. Its mixed-workload benchmark is pending.`,
  publication:`Revised ${esc(result.revisionDate)} · Results ${esc(result.resultVersion)} · Agent definitions ${esc(result.definitionVersion)}. <a href="evidence.html">Packaged methodology and evidence</a>.`,
};
const metric = (label, max, current, level) => `<div class="mix-meter" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="${max}" aria-valuenow="${current}"><i style="--level:${level}%"></i></div>`;
values.memoryMeter=metric(`${fmt(c.memoryGB)} GB of ${fmt(result.platform.memoryGB)} GB memory used`,result.platform.memoryGB,c.memoryGB,100*c.memoryGB/result.platform.memoryGB);
const callScale=Math.max(20,Math.ceil(c.callsPerSecond/10)*10);
values.callsMeter=metric(`LLM calls per second, scale 0 to ${callScale}`,callScale,c.callsPerSecond,100*c.callsPerSecond/callScale);
values.outputMeter=metric(`Model-serving demand of ${fmt(c.outputTokensPerSecond)} output tokens per second`,c.outputTokensPerSecond,c.outputTokensPerSecond,100);
values.cpuStack=`<div class="cpu-stack" role="img" aria-label="Share of CPU work: ${c.cpuShares.sandbox} percent sandboxed jobs, ${c.cpuShares.retrieval} percent retrieval and embedding, ${c.cpuShares.execution} percent agent execution, and ${c.cpuShares.support} percent database and other services">${Object.entries(c.cpuShares).map(([key,n])=>`<i class="${key}" style="--share:${n}%"></i>`).join('')}</div>`;
const maxWeight=Math.max(...result.archetypes.map(a=>a.coreMs/a.outputTokens));
values.archetypeRows=result.archetypes.map(a=>{
  const weight=a.coreMs/a.outputTokens;
  const ratio=weight<10 ? weight.toFixed(1) : String(Math.round(weight));
  return `<div class="agent-weight-row" role="listitem" style="--tone:${esc(a.color)}"><div class="weight-label"><b>${esc(a.name)}</b><span>${esc(a.label)}</span></div><div class="weight-stat"><span>CPU work</span><b>${fmt(a.coreMs/1000)}K core-ms</b></div><div class="weight-stat"><span>Model calls</span><b>${fmt(a.modelCalls)}</b></div><div class="weight-stat"><span>Total output tokens</span><b>${fmt(a.outputTokens)}</b></div><div class="weight-bar-cell"><div class="weight-track"><i style="--level:${(100*weight/maxWeight).toFixed(2)}%"></i></div><strong>${ratio}</strong></div></div>`;
}).join('\n');
function chart(mobile) {
  const suffix=mobile?'mobile':'desktop', width=mobile?380:900, height=mobile?480:450;
  const left=mobile?38:55, right=mobile?260:690, top=65, bottom=mobile?370:365;
  const maxSeconds=Math.max(...points.flatMap(p=>Object.values(p.p95).filter(n=>n!==null)));
  const step=Math.max(60,Math.ceil(maxSeconds/4/60)*60), ceiling=step*4;
  const x=n=>+(left+(n-points[0].resident)/(points.at(-1).resident-points[0].resident)*(right-left)).toFixed(2);
  const y=n=>+(bottom-n/ceiling*(bottom-top)).toFixed(2);
  const capacityX=x(c.resident), overload=points.filter(p=>!p.sustainable);
  const description=points.map(p=>`${p.resident} resident agents per CPU: ${result.archetypes.map(a=>`${a.name} ${p.p95[a.name]===null?'no completed result in the evaluation group':p.p95[a.name]+' seconds'}`).join(', ')}.${p.sustainable?'':' Unfinished work accumulating.'}`).join(' ');
  let out=`<svg class="s4-response-chart s4-chart-${suffix}" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="s4-chart-title-${suffix} s4-chart-desc-${suffix}"><title id="s4-chart-title-${suffix}">95th-percentile completion time by resident agents per CPU</title><desc id="s4-chart-desc-${suffix}">${esc(description)}</desc>`;
  if(overload.length) out+=`<rect x="${x(overload[0].resident)-8}" y="53" width="${right-x(overload[0].resident)+18}" height="${bottom-53}" rx="8" fill="#e4ba73" opacity=".06"/>`;
  for(let n=0;n<=ceiling;n+=step) out+=`<line x1="${left}" x2="${right+10}" y1="${y(n)}" y2="${y(n)}" class="s4-gridline"/><text x="${left-13}" y="${y(n)+4}" text-anchor="end">${n/60}</text>`;
  out+=`<text x="${left}" y="22" class="s4-axis-title">95th-percentile completion time · minutes</text><line x1="${capacityX}" x2="${capacityX}" y1="53" y2="${bottom}" class="s4-capacity-line"/><text x="${capacityX}" y="44" text-anchor="middle" class="s4-marker-label">${c.resident}${mobile?' · capacity':' resident agents per CPU · capacity'}</text>`;
  if(!mobile&&overload.length)out+=`<text x="${right}" y="44" text-anchor="end" class="s4-overload-label">Work accumulating</text>`;
  const colors={Analyst:'#c3cde8',Code:'#b798e3',Research:'#64d0c8',Ingestion:'#e4ba73',Task:'#5bb8f0'};
  for(const [name,color] of Object.entries(colors)) {
    let previous=null,last=null;
    for(const p of points) {
      const seconds=p.p95[name];
      if(seconds===null){previous=null;continue;}
      const cx=x(p.resident),cy=y(seconds);
      if(previous)out+=`<path d="M${previous.x},${previous.y} L${cx},${cy}" fill="none" stroke="${color}" stroke-width="2.7"${p.sustainable?'':' stroke-dasharray="5 5"'}/>`;
      out+=`<circle cx="${cx}" cy="${cy}" r="${p.resident===c.resident?5:3.5}" fill="${p.resident===c.resident?color:'#10324b'}" stroke="${color}" stroke-width="1.8"/>`;
      previous=last={x:cx,y:cy};
    }
    if(last)out+=`<text x="${last.x+14}" y="${last.y+4}" class="s4-series-label" style="fill:${color}">${name}</text>`;
  }
  for(const p of points)out+=`<text x="${x(p.resident)}" y="${bottom+24}" text-anchor="middle"${p.resident===c.resident?' class="s4-current-label"':''}>${p.resident}</text>`;
  out+=`<text x="${(left+right)/2}" y="${bottom+57}" text-anchor="middle" class="s4-axis-title">Resident agents per CPU</text>`;
  if(mobile&&overload.length)out+=`<text x="${left}" y="455" class="s4-overload-label">Dashed lines: work accumulating.</text>`;
  return out+'</svg>';
}
values.responseCharts=chart(false)+chart(true);
values.referenceData=`<script type="application/json" id="s4-reference-data">${JSON.stringify({version:result.resultVersion,resident:c.resident,cpuUtilization:c.cpuPercent,memoryGB:c.memoryGB,output:c.outputTokensPerSecond,gpuRate:serving.outputTokensPerSecondPerGpu,points:points.map(p=>({resident:p.resident,p95:Object.fromEntries(Object.entries(p.p95).map(([k,v])=>[k.toLowerCase(),v]))}))})}</script>`;
const paperPath=path.join(root,'agent-capacity-whitepaper.html');
let html=fs.readFileSync(paperPath,'utf8'), count=0;
html=html.replace(/<!--result:([\w]+)-->[\s\S]*?<!--\/result-->/g,(whole,key)=>{
  if(!(key in values))throw new Error(`Unknown result binding: ${key}`);
  count++;
  return `<!--result:${key}-->${values[key]}<!--/result-->`;
});
if(count<30)throw new Error('Missing result bindings. Refusing partial generation.');
html=html.replace(/(class="mix-vessel" aria-label=")[^"]*(")/,`$1Twelve representative incoming workflows. The separate capacity label reports ${c.resident} resident agents per CPU.$2`);
html=html.replace(/(class="s4-sizing-band" role="img" aria-label=")[^"]*(")/,`$1${fmt(c.outputTokensPerSecond)} required output tokens per second divided by ${fmt(serving.outputTokensPerSecondPerGpu)} output tokens per second per GPU equals ${gpu.toFixed(2)} GPU equivalents.$2`);
const snapshots=result.sources.map(s=>{
  const source=fs.readFileSync(path.resolve(root,s.path),'utf8');
  const hash=crypto.createHash('sha256').update(source).digest('hex');
  return { ...s, source, hash };
});
const evidence=`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent capacity: methodology and evidence</title><style>body{max-width:1000px;margin:40px auto;padding:0 22px;font:16px/1.6 system-ui;color:#203040;background:#f6f8fa}a{color:#0672cb}nav{display:flex;gap:18px;flex-wrap:wrap}section{margin:48px 0;scroll-margin-top:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace;background:white;border:1px solid #d5dfe6;border-radius:12px;padding:24px}.source{font-size:12px;overflow-wrap:anywhere}@media print{section{break-before:page}pre{border:0;padding:0}}</style><a href="agent-capacity-whitepaper.html#summary">Return to the whitepaper</a><h1>Methodology and evidence</h1><p>Source snapshots packaged with the ${esc(result.revisionDate)} paper revision. Results: ${esc(result.resultVersion)}. Agent definitions: ${esc(result.definitionVersion)}. These records preserve the technical detail behind the paper and may describe work still in progress.</p><nav>${snapshots.map(s=>`<a href="#${s.id}">${esc(s.title)}</a>`).join('')}</nav>${snapshots.map(s=>`<section id="${s.id}"><h2>${esc(s.title)}</h2><p class="source">Repository path: ${esc(s.path)}<br>SHA-256: ${s.hash}</p><pre>${esc(s.source)}</pre></section>`).join('')}</html>`;
const outputs=[[paperPath,html],[path.join(root,'evidence.html'),evidence]];
if(process.argv.includes('--check')){
  for(const [file,content] of outputs)if(!fs.existsSync(file)||fs.readFileSync(file,'utf8')!==content)throw new Error(`Stale output: ${path.basename(file)}. Run node update-paper.cjs.`);
  console.log(`Checked ${count} static bindings, chart geometry, bar lengths, and evidence snapshots.`);
}else{
  for(const [file,content] of outputs)fs.writeFileSync(file,content);
  console.log(`Updated ${count} static bindings and portable evidence. Results remain ${result.resultVersion}.`);
}
