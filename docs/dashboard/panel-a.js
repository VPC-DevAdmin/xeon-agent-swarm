const $=id=>document.getElementById(id),C=RESULTS.capacity,S=RESULTS.serving;
const simulator=AgentSimulator.create(SIM_PROFILE,RESULTS),mix=AgentSimulator.mix(RESULTS,simulator.times);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');let motion=!reduced.matches;
const decimal=n=>n.toLocaleString('en-US',{maximumFractionDigits:1}),compact=n=>n>=1000?(n/1000).toFixed(1)+'K':decimal(n),fmt=n=>Math.round(n).toLocaleString('en-US');
const presets={Enterprise:[50,8,8,17,17],'Service desk':[75,15,5,3,2],'Document operations':[20,15,50,10,5],'Development team':[15,10,5,20,50]};
const colors=['var(--sandbox)','var(--check)','var(--cpu)','var(--support)','var(--idle)'],familyNames=['Sandbox jobs','Retrieval and embedding','Agent execution','Supporting services','Idle'];
const roles={'LLM':'var(--llm)','CPU sandbox':'var(--sandbox)','CPU service':'var(--cpu)','CPU check':'var(--check)'};
let preset='Enterprise',custom=mix.snapshot(),appliedMix=mix.snapshot(),simCounts=appliedMix.counts,simResult=simulator.calculate(simCounts);
let elapsed=0,last=performance.now(),transition=null,currentCounts=simCounts.slice(),hardwareKey='',activeTab='a',returnFocus=null;
let rowOrder=defs.map((_,i)=>i),customOrder=rowOrder.slice();
let theme;try{theme=localStorage.getItem('replay-demo-theme');}catch{}
if(!['light','dark'].includes(theme))theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
function setTheme(value){theme=value;document.documentElement?.setAttribute('data-theme',theme);$('theme-light').setAttribute('aria-pressed',theme==='light');$('theme-dark').setAttribute('aria-pressed',theme==='dark');try{localStorage.setItem('replay-demo-theme',theme);}catch{}}
$('theme-light').onclick=()=>setTheme('light');$('theme-dark').onclick=()=>setTheme('dark');setTheme(theme);
let tour=false,agent=0,flowClock=0,flowKey='',cpuDisplay=simResult.cpuPercent,memoryDisplay=simResult.memoryGB;
document.querySelectorAll('[data-asset]').forEach(el=>el.src=ASSETS[el.dataset.asset]);
$('mix').innerHTML=[...Object.keys(presets),'Custom'].map(n=>`<option>${n}</option>`).join('');
$('cpu-stack').innerHTML=colors.map((c,i)=>`<i id="cpu-family-${i}" style="background:${c}"></i>`).join('');
$('cpu-key').innerHTML=familyNames.map((n,i)=>`<span title="${n}"><i style="background:${colors[i]}"></i>${['Sandbox jobs','Retrieval','Agents','Services','Idle'][i]}</span>`).join('');
$('serving-reference').textContent=`${S.model} · ${fmt(S.outputTokensPerSecondPerGpu)} output tokens/s per GPU. Prefix caching with a ${S.cacheAllowancePercent}% allowance for cache misses.`;

function updateTour(){ $('tour').textContent=tour?'Pause Tour':'Start Tour'; }
function stopTour(){tour=false;updateTour();}
function syncEditor(){
  const m=mix.snapshot();$('mix').value=preset;
  if(document.activeElement!==$('target'))$('target').value=m.target;
  m.weights.forEach((n,i)=>{const input=$('percent-'+i);if(input&&document.activeElement!==input)input.value=n;});
  $('mix-status').classList.toggle('pending',!m.valid);
  $('mix-status').hidden=m.valid&&!transition;
  $('mix-status').textContent=m.valid?(transition?'Applying workload mix…':'Incoming shares total 100%.'):`Shares total ${m.total}%. ${m.total<100?'Add '+(100-m.total):'Reduce '+(m.total-100)}% to apply. Previous workload continues.`;
}
function applyDraft(saveCustom=true){
  const m=mix.snapshot();if(saveCustom){preset='Custom';custom=m;customOrder=rowOrder.slice();}
  if(m.valid){appliedMix=m;simCounts=m.counts;transition=reduced.matches?null:{from:currentCounts.slice(),at:elapsed};if(!transition)currentCounts=simCounts.slice();}
  syncEditor();render(0);
}
function loadMix(name){
  preset=name;rowOrder=name==='Custom'?customOrder.slice():defs.map((_,i)=>i);const selected=name==='Custom'?custom:{weights:presets[name],included:defs.map(()=>true)};
  selected.weights.forEach((n,i)=>{const included=mix.snapshot().included[i];if(selected.included[i]){if(!included)mix.add(i);mix.setPercent(i,n);}else if(included)mix.remove(i);});
  buildRows();applyDraft(false);stopTour();
  if(name!=='Custom'){agent=largestShare();flowClock=0;flowKey='';renderFlow(0);}
}
function icon(i){return `<span class="icon" aria-hidden="true"><svg viewBox="0 0 28 28"><path d="${defs[i].icon}"/></svg></span>`;}
function buildRows(){
  const m=mix.snapshot();$('rows').innerHTML=rowOrder.map(i=>{const d=defs[i];return !m.included[i]?'':`<div class="agent-row" style="--agent:${RESULTS.archetypes[i].color}" id="row-${i}"><div class="agent-line"><button class="agent-select" data-agent="${i}" aria-label="Show ${d.name} workflow">${icon(i)}</button><select class="workflow-select" data-workflow="${i}" aria-label="${d.name} workflow">${defs.map((other,k)=>`<option value="${k}" ${k===i?'selected':''}>${other.name}</option>`).join('')}<optgroup label="Actions"><option value="remove">Remove workflow</option></optgroup></select><div class="edit"><input id="percent-${i}" data-percent="${i}" type="number" min="0" max="100" step="1" value="${m.weights[i]}" aria-label="${d.name} incoming percentage"><span>%</span></div></div><div class="agent-meta"><span><b id="now-${i}">0</b> working now</span><span><b id="rate-${i}">0</b> completed/h</span></div></div>`;}).join('')+(m.included.every(Boolean)?'':'<button class="add-agent" id="add-agent">＋ Add workflow</button>');

  if(!m.included[agent]){agent=m.included.findIndex(Boolean);flowClock=0;flowKey='';}
}
function apportionTransition(from,to,p){
  const exact=to.map((n,i)=>from[i]+(n-from[i])*p),counts=exact.map(Math.floor),total=Math.round(exact.reduce((a,b)=>a+b,0));
  const order=exact.map((n,i)=>({i,f:n-counts[i]})).sort((a,b)=>b.f-a.f);
  for(let k=0,n=total-counts.reduce((a,b)=>a+b,0);k<n;k++)counts[order[k].i]++;
  return counts;
}
const tracePeriod=TRACE.at(-1)[0]+(TRACE.at(-1)[0]-TRACE.at(-2)[0]);
function traceAt(t){
  const x=((t%tracePeriod)+tracePeriod)%tracePeriod;
  let i=TRACE.findIndex(s=>s[0]>x),a,b;
  if(i<0){a=TRACE.at(-1);b=[tracePeriod,...TRACE[0].slice(1)];}else{a=TRACE[Math.max(0,i-1)];b=TRACE[i];}
  const p=(x-a[0])/(b[0]-a[0]||1);return [1,2].map(k=>a[k]+(b[k]-a[k])*p);
}
function photos(id,count,gpu){
  const el=$(id);if(!count){el.innerHTML='<span class="empty-hardware">No systems allocated</span>';return;}
  const shown=gpu?1:count>3?2:count;el.innerHTML=Array.from({length:shown},()=>`<img src="${gpu?ASSETS.xe7740:ASSETS.r770}" alt="Dell PowerEdge ${gpu?'XE7740':'R770'}">`).join('')+(!gpu&&count>shown?`<span class="more">+${count-shown} more systems</span>`:'');
}
function render(dt){
  if(transition){const t=Math.min(1,(elapsed-transition.at)/8);currentCounts=apportionTransition(transition.from,simCounts,t*t*(3-2*t));if(t===1){transition=null;syncEditor();}}
  else currentCounts=simCounts.slice();
  const r=simResult=simulator.calculate(currentCounts),[cpuFactor,memFactor]=motion?traceAt(elapsed):[1,1];
  const targetCPU=r.total?Math.min(100,r.cpuPercent*cpuFactor):0,base=r.sockets*C.memoryGB*(1-SIM_PROFILE.analystMemoryShare),targetMem=base+(r.memoryGB-base)*memFactor;
  const smoothing=reduced.matches?1:1-Math.exp(-dt/1.2);cpuDisplay+=(targetCPU-cpuDisplay)*smoothing;memoryDisplay+=(targetMem-memoryDisplay)*smoothing;
  if(!r.total&&!transition){cpuDisplay=0;memoryDisplay=0;}
  $('ratio').textContent=r.total?'1 : '+r.ratio.toFixed(2):'—';
  $('result-sentence').textContent=`${fmt(r.total)} working agents · ${r.cpuSystems} PowerEdge R770 server${r.cpuSystems===1?'':'s'} · ${r.gpuSystems} PowerEdge XE7740${r.gpuSystems===1?'':'s'} (Extrapolated from measurements)`;
  $('installed-capacity').textContent=`Installed: ${r.sockets} sockets / ${r.gpuSystems*8} GPUs`;
  if(!r.total)$('result-sentence').textContent='Set an agent population to see the execution and model-serving capacity.';
  r.counts.forEach((n,i)=>{if($('now-'+i)){$('now-'+i).textContent=fmt(n);$('rate-'+i).textContent=fmt(r.rates[i]*3600);}});
  $('cpu-count').textContent=r.cpuSystems;$('cpu-sockets').textContent=`${r.sockets} CPU sockets · ${r.sockets*RESULTS.platform.cores} cores`;
  $('gpu-count').textContent=r.gpuSystems;$('gpu-allocation').textContent=`${Math.ceil(r.gpuEquivalent-1e-9)} GPUs allocated · ${r.gpuSystems*8} positions`;
  const hw=[r.cpuSystems,r.gpuSystems].join(':');if(hw!==hardwareKey){photos('cpu-photos',r.cpuSystems,false);photos('gpu-photos',r.gpuSystems,true);hardwareKey=hw;}
  $('cpu').innerHTML=Math.round(cpuDisplay)+'<span class="metric-unit">%</span>';$('memory').innerHTML=fmt(r.cpuSystems?memoryDisplay/r.cpuSystems:0)+'<span class="metric-unit">GB</span>';$('per-server').textContent=fmt(r.cpuSystems?r.total/r.cpuSystems:0);
  const total=r.families.reduce((a,b)=>a+b,0);colors.forEach((_,i)=>{$('cpu-family-'+i).style.width=(i===4?100-cpuDisplay:total?r.families[i]/total*cpuDisplay:0)+'%';});
  $('gpu-demand').innerHTML=Math.round(r.gpuUsage)+'<span class="metric-unit">%</span>';$('gpu-bar').style.width=Math.min(100,r.gpuUsage)+'%';$('tokens').innerHTML=compact(r.output)+'<span class="metric-unit">/s</span>';$('calls').textContent=decimal(r.calls);
  renderFlow(dt);
}
// Bottom box: the selected agent's stages as chips (1 · Research, 2 · Analysis, 3 · Writing, 4 · Finalize),
// with the current stage's steps below. Steps advance continuously; the tour only moves to the next agent.
const WORKER_SECONDS=12,FINAL_SECONDS=6;
function cycleFor(i,worker){return defs[i].cycle.filter(c=>!(i===3&&worker===2&&c[1]==='Retrieve definitions')).flatMap(c=>c[1]==='Record and draft'?[['CPU service','Record outcome'],['LLM','Draft result']]:[c]);}
function stagesFor(i){
  const d=defs[i],many=d.workers.length>1;
  const list=d.workers.map((w,k)=>({chip:(many?(k+1)+' · ':'')+w,job:d.jobs[k],steps:cycleFor(i,k),seconds:WORKER_SECONDS}));
  if(i!==0)list.push({chip:(many?(d.workers.length+1)+' · ':'')+'Finalize',job:'Synthesis and review',steps:[['LLM','Synthesize results'],['CPU check','Check final structure'],['LLM','Review final answer']],seconds:FINAL_SECONDS});
  return list;
}
function largestShare(){const m=mix.snapshot();let best=-1;m.weights.forEach((w,i)=>{if(m.included[i]&&(best<0||w>m.weights[best]))best=i;});return best;}
function renderFlow(dt){
  const included=mix.snapshot().included.flatMap((yes,i)=>yes?[i]:[]);
  if(agent<0||!included.length){$('flow-title').textContent='Add an agent to explore its workflow';$('worker-sequence').replaceChildren();$('flow').replaceChildren();$('agent-details').disabled=true;flowKey='';return;}
  if(!included.includes(agent)){agent=included[0];flowClock=0;}
  $('agent-details').disabled=false;
  if(motion)flowClock+=dt;
  const stages=stagesFor(agent),total=stages.reduce((a,s)=>a+s.seconds,0);
  if(flowClock>=total){flowClock%=total;if(tour){agent=included[(included.indexOf(agent)+1)%included.length];flowClock=0;flowKey='';renderFlow(0);return;}}
  let t=flowClock,stage=0;while(stage<stages.length-1&&t>=stages[stage].seconds){t-=stages[stage].seconds;stage++;}
  const current=stages[stage],key=agent+':'+stage;
  if(key!==flowKey){
    flowKey=key;const d=defs[agent];
    $('flow-title').textContent=d.name+' · '+current.job;
    $('worker-sequence').innerHTML=stages.map((s,k)=>`${k?'<span class="seq-link" aria-hidden="true">›</span>':''}<button type="button" data-flow-stage="${k}" class="${k===stage?'current':k<stage?'done':''}" aria-pressed="${k===stage}">${s.chip}</button>`).join('');
    $('flow').innerHTML=current.steps.map(([role,label],i)=>`${i?'<span class="flow-link" aria-hidden="true">›</span>':''}<button class="flow-node" data-topic="agent:${agent}" style="--role:${roles[role]}"><small>${role}</small><b>${label}</b></button>`).join('');
  }
  defs.forEach((_,i)=>$('row-'+i)?.classList.toggle('selected',i===agent));
  const index=Math.min(current.steps.length-1,Math.floor(t/current.seconds*current.steps.length));
  document.querySelectorAll('.flow-node').forEach((n,i)=>n.classList.toggle('active',i===index));
  const [role,label]=current.steps[index],family=role==='CPU sandbox'?0:role==='CPU check'?2:role==='CPU service'?(/retriev|lookup|embed/i.test(label)?1:/record/i.test(label)?3:2):-1;
  colors.forEach((_,i)=>$('cpu-family-'+i).classList.toggle('active',motion&&i===family));document.querySelector('.exchange').classList.toggle('active',motion&&role==='LLM');
}
function openTopic(key){
  returnFocus=document.activeElement?.closest('#utility-menu')?$('utility-menu').querySelector('summary'):document.activeElement;if($('utility-menu'))$('utility-menu').open=false;stopTour();renderDetailView(key==='ratio'?'serving':key);
  if(!$('detail-dialog').open)$('detail-dialog').showModal();$('detail-dialog').scrollTop=0;
}
function closeDetails(){ $('detail-dialog').close();returnFocus?.focus(); }
$('close-details').onclick=closeDetails;$('detail-dialog').addEventListener('cancel',e=>{e.preventDefault();closeDetails();});
$('show-reference').onclick=()=>openTopic('methodology');$('agent-details').onclick=()=>openTopic('agent:'+agent);
$('mix').onchange=e=>loadMix(e.target.value);
$('target').addEventListener('input',e=>{const value=Number(e.target.value);if(e.target.value===''||!Number.isInteger(value)||value<0||value>100000){e.target.setCustomValidity('Enter a whole agent count from 0 to 100,000.');return;}e.target.setCustomValidity('');mix.setTarget(value);applyDraft(false);stopTour();});
$('target').addEventListener('change',()=>{$('target').value=mix.snapshot().target;$('target').setCustomValidity('');});
$('rows').addEventListener('input',e=>{if(!e.target.matches('[data-percent]'))return;const n=Number(e.target.value);if(e.target.value===''||!Number.isInteger(n)||n<0||n>100){e.target.setCustomValidity('Enter a whole percentage from 0 to 100.');return;}e.target.setCustomValidity('');mix.setPercent(Number(e.target.dataset.percent),n);applyDraft();stopTour();});
$('rows').addEventListener('focusin',e=>{if(e.target.matches('[data-workflow]')){agent=Number(e.target.dataset.workflow);flowClock=0;flowKey='';stopTour();renderFlow(0);}});
$('rows').addEventListener('change',e=>{if(e.target.matches('[data-workflow]')){const i=Number(e.target.dataset.workflow),value=e.target.value,m=mix.snapshot();if(value==='remove'){mix.remove(i);}else{const next=Number(value);if(next!==i){const oldPosition=rowOrder.indexOf(i),newPosition=rowOrder.indexOf(next);[rowOrder[oldPosition],rowOrder[newPosition]]=[rowOrder[newPosition],rowOrder[oldPosition]];if(m.included[next]){mix.setPercent(i,m.weights[next]);mix.setPercent(next,m.weights[i]);}else{mix.remove(i);mix.add(next);mix.setPercent(next,m.weights[i]);}agent=next;}}flowClock=0;flowKey='';buildRows();applyDraft();stopTour();return;}if(e.target.matches('[data-percent]')){e.target.value=mix.snapshot().weights[Number(e.target.dataset.percent)];e.target.setCustomValidity('');}});
document.addEventListener('click',e=>{
  const topic=e.target.closest('[data-topic]');if(topic){openTopic(topic.dataset.topic);return;}
  const stageChoice=e.target.closest('[data-flow-stage]');if(stageChoice){const stage=Number(stageChoice.dataset.flowStage);flowClock=stagesFor(agent).slice(0,stage).reduce((sum,s)=>sum+s.seconds,0);flowKey='';stopTour();renderFlow(0);return;}
  const chosen=e.target.closest('[data-agent]');if(chosen){agent=Number(chosen.dataset.agent);flowClock=0;flowKey='';stopTour();renderFlow(0);return;}
  const step=e.target.closest('[data-step]');if(step){const i=Number(step.dataset.step);mix.setPercent(i,Math.max(0,Math.min(100,mix.snapshot().weights[i]+Number(step.dataset.delta))));applyDraft();stopTour();return;}
  const removed=e.target.closest('[data-remove]');if(removed){mix.remove(Number(removed.dataset.remove));buildRows();applyDraft();stopTour();$('add-agent')?.focus();return;}
  if(e.target.closest('#add-agent')){$('agent-options').innerHTML=defs.map((d,i)=>mix.snapshot().included[i]?'':`<button data-add="${i}">${d.name}</button>`).join('');$('agent-picker').showModal();}
  const added=e.target.closest('[data-add]');if(added){const i=Number(added.dataset.add);mix.add(i);$('agent-picker').close();buildRows();applyDraft();$('percent-'+i).focus();}
});
$('close-picker').onclick=()=>$('agent-picker').close();
document.addEventListener('click',e=>{const menu=$('utility-menu');if(menu?.open&&!menu.contains(e.target))menu.open=false;},true);
document.addEventListener('keydown',e=>{const menu=$('utility-menu');if(e.key==='Escape'&&menu?.open){menu.open=false;menu.querySelector('summary').focus();}});
$('tour').onclick=()=>{if($('utility-menu'))$('utility-menu').open=false;tour=!tour;if(tour&&flowClock===0)flowKey='';updateTour();};
$('reset').onclick=()=>{if($('utility-menu'))$('utility-menu').open=false;rowOrder=defs.map((_,i)=>i);preset='Enterprise';appliedMix=mix.reset();buildRows();applyDraft(false);agent=0;flowClock=0;flowKey='';tour=false;updateTour();};
$('fullscreen').onclick=async()=>{if($('utility-menu'))$('utility-menu').open=false;try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('fullscreen').textContent='Use browser full screen';}};
function showTab(name){activeTab=name;$('panel-a').hidden=name!=='a';$('current').hidden=name!=='current';$('tab-a').setAttribute('aria-pressed',name==='a');$('tab-current').setAttribute('aria-pressed',name==='current');last=performance.now();}
if($('tab-a')){$('tab-a').onclick=()=>showTab('a');$('tab-current').onclick=()=>showTab('current');}
reduced.addEventListener('change',e=>{motion=!e.matches;if(!motion){tour=false;transition=null;currentCounts=simCounts.slice();}updateTour();render(0);});
document.addEventListener('visibilitychange',()=>last=performance.now());
buildRows();syncEditor();updateTour();render(0);
// Timer-driven, not gated on document visibility: embedded viewers can report the page hidden while showing it.
setInterval(()=>{const now=performance.now(),dt=Math.min(.25,(now-last)/1000);last=now;if(activeTab!=='a')return;elapsed+=dt;render(dt);},100);
window.__panelA={snapshot:()=>({result:simResult,preset,draft:mix.snapshot(),transition:!!transition,cpu:cpuDisplay,memory:memoryDisplay}),preset:loadMix};
