const $=id=>document.getElementById(id),C=RESULTS.capacity,S=RESULTS.serving;
const simulator=AgentSimulator.create(SIM_PROFILE,RESULTS),mix=AgentSimulator.mix(RESULTS,simulator.times);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');let motion=!reduced.matches;
const decimal=n=>n.toLocaleString('en-US',{maximumFractionDigits:1}),compact=n=>n>=1000?(n/1000).toFixed(1)+'K':decimal(n),fmt=n=>Math.round(n).toLocaleString('en-US');
const presets={Enterprise:[50,8,8,17,17],'Service desk':[75,15,5,3,2],'Document operations':[20,15,50,10,5],'Development team':[15,10,5,20,50]};
const descriptions=['Retrieve ticket guidance and prepare a reply.','Gather evidence and prepare a brief.','Read documents, extract text, and index content.','Compare periods and explain the results.','Build software, run checks, and verify changes.'];
const colors=['#f0be68','#56dcbf','#53c3ff','#83a9c1','#0d2b41'],familyNames=['Sandbox jobs','Retrieval and embedding','Agent execution','Supporting services','Idle'];
const roles={'LLM':'#c39bff','CPU sandbox':'#f0be68','CPU service':'#53c3ff','CPU check':'#70d9bd'};
let preset='Enterprise',custom=mix.snapshot(),appliedMix=mix.snapshot(),simCounts=appliedMix.counts,simResult=simulator.calculate(simCounts);
let elapsed=0,last=performance.now(),transition=null,currentCounts=simCounts.slice(),hardwareKey='',activeTab='a',returnFocus=null;
let tour=motion,agent=0,flowClock=0,flowKey='',cpuDisplay=simResult.cpuPercent,memoryDisplay=simResult.memoryGB;
document.querySelectorAll('[data-asset]').forEach(el=>el.src=ASSETS[el.dataset.asset]);
$('mix').innerHTML=[...Object.keys(presets),'Custom'].map(n=>`<option>${n}</option>`).join('');
$('cpu-stack').innerHTML=colors.map((c,i)=>`<i id="cpu-family-${i}" style="background:${c}"></i>`).join('');
$('cpu-key').innerHTML=familyNames.map((n,i)=>`<span><i style="background:${colors[i]}"></i>${n}</span>`).join('');
$('serving-reference').textContent=`${S.model} · ${fmt(S.outputTokensPerSecondPerGpu)} output tokens/s per GPU. Prefix caching with a ${S.cacheAllowancePercent}% allowance for cache misses.`;

function updateTour(){ $('tour').textContent=tour?'Pause Tour':'Start Tour'; }
function stopTour(){tour=false;updateTour();}
function syncEditor(){
  const m=mix.snapshot();$('mix-name').textContent=preset+' mix';$('mix').value=preset;
  if(document.activeElement!==$('target'))$('target').value=m.target;
  m.weights.forEach((n,i)=>{const input=$('percent-'+i);if(input&&document.activeElement!==input)input.value=n;});
  $('mix-status').classList.toggle('pending',!m.valid);
  $('mix-status').hidden=m.valid&&!transition;
  $('mix-status').textContent=m.valid?(transition?'Applying workload mix…':'Incoming shares total 100%.'):`Shares total ${m.total}%. ${m.total<100?'Add '+(100-m.total):'Reduce '+(m.total-100)}% to apply. Previous workload continues.`;
}
function applyDraft(saveCustom=true){
  const m=mix.snapshot();if(saveCustom){preset='Custom';custom=m;}
  if(m.valid){appliedMix=m;simCounts=m.counts;transition=reduced.matches?null:{from:currentCounts.slice(),at:elapsed};if(!transition)currentCounts=simCounts.slice();}
  syncEditor();render(0);
}
function loadMix(name){
  preset=name;const selected=name==='Custom'?custom:{weights:presets[name],included:defs.map(()=>true)};
  selected.weights.forEach((n,i)=>{const included=mix.snapshot().included[i];if(selected.included[i]){if(!included)mix.add(i);mix.setPercent(i,n);}else if(included)mix.remove(i);});
  buildRows();applyDraft(false);stopTour();
}
function icon(i){return `<span class="icon" aria-hidden="true"><svg viewBox="0 0 28 28"><path d="${defs[i].icon}"/></svg></span>`;}
function buildRows(){
  const m=mix.snapshot();$('rows').innerHTML=defs.map((d,i)=>!m.included[i]?'':`<div class="agent-row" style="--agent:${RESULTS.archetypes[i].color}" id="row-${i}"><button class="agent-select" data-agent="${i}" aria-label="Show ${d.name} workflow">${icon(i)}<span><b>${d.name}</b><small>${descriptions[i]}</small></span></button><div class="edit"><input id="percent-${i}" data-percent="${i}" type="number" min="0" max="100" step="1" value="${m.weights[i]}" aria-label="${d.name} incoming percentage"><span>%</span><div class="stepper"><button data-step="${i}" data-delta="1" aria-label="Increase ${d.name} share"><svg viewBox="0 0 12 8"><path d="M2 6 6 2 10 6"/></svg></button><button data-step="${i}" data-delta="-1" aria-label="Decrease ${d.name} share"><svg viewBox="0 0 12 8"><path d="M2 6 6 2 10 6"/></svg></button></div></div><button class="remove" data-remove="${i}" aria-label="Remove ${d.name}">×</button><div class="agent-meta"><span><b id="now-${i}">0</b> working now</span><span><b id="rate-${i}">0</b> completed/hour</span></div></div>`).join('')+(m.included.every(Boolean)?'':'<button class="add-agent" id="add-agent">＋ Add agent</button>');
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
  const shown=gpu?1:Math.min(3,count);el.innerHTML=Array.from({length:shown},()=>`<img src="${gpu?ASSETS.xe7740:ASSETS.r770}" alt="Dell PowerEdge ${gpu?'XE7740':'R770'}">`).join('')+(!gpu&&count>shown?`<span class="more">+${count-shown} more systems</span>`:'');
}
function render(dt){
  if(transition){const t=Math.min(1,(elapsed-transition.at)/8);currentCounts=apportionTransition(transition.from,simCounts,t*t*(3-2*t));if(t===1){transition=null;syncEditor();}}
  else currentCounts=simCounts.slice();
  const r=simResult=simulator.calculate(currentCounts),[cpuFactor,memFactor]=motion?traceAt(elapsed):[1,1];
  const targetCPU=r.total?Math.min(100,r.cpuPercent*cpuFactor):0,base=r.sockets*C.memoryGB*(1-SIM_PROFILE.analystMemoryShare),targetMem=base+(r.memoryGB-base)*memFactor;
  const smoothing=reduced.matches?1:1-Math.exp(-dt/1.2);cpuDisplay+=(targetCPU-cpuDisplay)*smoothing;memoryDisplay+=(targetMem-memoryDisplay)*smoothing;
  if(!r.total&&!transition){cpuDisplay=0;memoryDisplay=0;}
  $('ratio').textContent=r.total?'1 : '+r.ratio.toFixed(2):'—';
  $('result-sentence').textContent=`This mix places ${fmt(r.total)} working agents across ${r.cpuSystems} PowerEdge R770 servers, supported by ${r.gpuSystems} XE7740${r.gpuSystems===1?'':'s'}.`;
  if(!r.total)$('result-sentence').textContent='Set an agent population to see the execution and model-serving capacity.';
  r.counts.forEach((n,i)=>{if($('now-'+i)){$('now-'+i).textContent=fmt(n);$('rate-'+i).textContent=fmt(r.rates[i]*3600);}});
  $('cpu-count').textContent=r.cpuSystems;$('cpu-sockets').textContent=`${r.sockets} CPU sockets · ${r.sockets*RESULTS.platform.cores} cores`;
  $('gpu-count').textContent=r.gpuSystems;$('gpu-allocation').textContent=`${Math.ceil(r.gpuEquivalent-1e-9)} GPUs allocated · ${r.gpuSystems*8} positions`;
  const hw=[r.cpuSystems,r.gpuSystems].join(':');if(hw!==hardwareKey){photos('cpu-photos',r.cpuSystems,false);photos('gpu-photos',r.gpuSystems,true);hardwareKey=hw;}
  $('cpu').textContent=Math.round(cpuDisplay)+'%';$('memory').textContent=fmt(r.cpuSystems?memoryDisplay/r.cpuSystems:0)+' GB';$('per-server').textContent=fmt(r.cpuSystems?r.total/r.cpuSystems:0);
  const total=r.families.reduce((a,b)=>a+b,0);colors.forEach((_,i)=>{$('cpu-family-'+i).style.width=(i===4?100-cpuDisplay:total?r.families[i]/total*cpuDisplay:0)+'%';});
  $('gpu-demand').textContent=Math.round(r.gpuUsage)+'%';$('gpu-bar').style.width=Math.min(100,r.gpuUsage)+'%';$('tokens').textContent=compact(r.output)+'/s';$('calls').textContent=decimal(r.calls);
  renderFlow(dt);
}
function cycleFor(i,worker){return defs[i].cycle.filter(c=>!(i===3&&worker===2&&c[1]==='Retrieve definitions')).flatMap(c=>c[1]==='Record and draft'?[['CPU service','Record outcome'],['LLM','Draft result']]:[c]);}
function renderFlow(dt){
  const included=mix.snapshot().included.flatMap((yes,i)=>yes?[i]:[]);
  if(agent<0||!included.length){$('flow-title').textContent='Add an agent to explore its workflow';$('flow-sub').textContent='';$('flow').replaceChildren();$('worker-sequence').replaceChildren();$('agent-details').disabled=true;return;}
  $('agent-details').disabled=false;if(tour&&motion)flowClock+=dt;
  const d=defs[agent],workers=d.workers.length,worker=Math.min(workers-1,Math.floor(flowClock/12));
  if(flowClock>=workers*12+6&&tour){agent=included[(included.indexOf(agent)+1)%included.length];flowClock=0;flowKey='';renderFlow(0);return;}
  const final=flowClock>=workers*12,key=agent+':'+worker+':'+final;
  const cycle=final?(agent===0?[['CPU check','Return the checked answer']]:[['LLM','Synthesize reviewed results'],['CPU check','Check final structure'],['LLM','Review final answer']]):cycleFor(agent,worker);
  if(key!==flowKey){
    flowKey=key;$('flow-title').textContent=d.name+' agent';
    $('flow-sub').textContent=final?(agent===0?'Return the checked answer':'Final synthesis and review'):`Worker ${worker+1} of ${workers} · ${d.workers[worker]}`;
    $('worker-sequence').innerHTML=d.workers.map((w,i)=>`<span class="${!final&&worker===i?'current':''}">${i+1}. ${w}</span>`).join('')+`<span class="${final?'current':''}">${agent===0?'Checked answer':'Final synthesis and review'}</span>`;
    $('flow').innerHTML=cycle.map(([role,label],i)=>`${i?'<span class="flow-link" aria-hidden="true">›</span>':''}<button class="flow-node" data-topic="agent:${agent}" style="--role:${roles[role]}"><small>${role}</small><b>${label}</b></button>`).join('');
    defs.forEach((_,i)=>$('row-'+i)?.classList.toggle('selected',i===agent));
  }
  const index=Math.min(cycle.length-1,Math.floor(((final?flowClock-workers*12:flowClock%12)/(final?6:12))*cycle.length));
  document.querySelectorAll('.flow-node').forEach((n,i)=>n.classList.toggle('active',i===index));
  const [role,label]=cycle[index],family=role==='CPU sandbox'?0:role==='CPU check'?2:role==='CPU service'?(/retriev|lookup|embed/i.test(label)?1:/record/i.test(label)?3:2):-1;
  colors.forEach((_,i)=>$('cpu-family-'+i).classList.toggle('active',motion&&i===family));document.querySelector('.exchange').classList.toggle('active',motion&&role==='LLM');
}
function openTopic(key){
  returnFocus=document.activeElement;stopTour();renderDetailView(key==='ratio'?'serving':key);
  if(!$('detail-dialog').open)$('detail-dialog').showModal();$('detail-dialog').scrollTop=0;
}
function closeDetails(){ $('detail-dialog').close();returnFocus?.focus(); }
$('close-details').onclick=closeDetails;$('detail-dialog').addEventListener('cancel',e=>{e.preventDefault();closeDetails();});
$('show-reference').onclick=()=>openTopic('methodology');$('agent-details').onclick=()=>openTopic('agent:'+agent);
$('mix').onchange=e=>loadMix(e.target.value);
$('target').addEventListener('input',e=>{const value=Number(e.target.value);if(e.target.value===''||!Number.isInteger(value)||value<0||value>100000){e.target.setCustomValidity('Enter a whole agent count from 0 to 100,000.');return;}e.target.setCustomValidity('');mix.setTarget(value);applyDraft(false);stopTour();});
$('target').addEventListener('change',()=>{$('target').value=mix.snapshot().target;$('target').setCustomValidity('');});
$('rows').addEventListener('input',e=>{if(!e.target.matches('[data-percent]'))return;const n=Number(e.target.value);if(e.target.value===''||!Number.isInteger(n)||n<0||n>100){e.target.setCustomValidity('Enter a whole percentage from 0 to 100.');return;}e.target.setCustomValidity('');mix.setPercent(Number(e.target.dataset.percent),n);applyDraft();stopTour();});
$('rows').addEventListener('change',e=>{if(e.target.matches('[data-percent]')){e.target.value=mix.snapshot().weights[Number(e.target.dataset.percent)];e.target.setCustomValidity('');}});
document.addEventListener('click',e=>{
  const topic=e.target.closest('[data-topic]');if(topic){openTopic(topic.dataset.topic);return;}
  const chosen=e.target.closest('[data-agent]');if(chosen){agent=Number(chosen.dataset.agent);flowClock=0;flowKey='';stopTour();renderFlow(0);return;}
  const step=e.target.closest('[data-step]');if(step){const i=Number(step.dataset.step);mix.setPercent(i,Math.max(0,Math.min(100,mix.snapshot().weights[i]+Number(step.dataset.delta))));applyDraft();stopTour();return;}
  const removed=e.target.closest('[data-remove]');if(removed){mix.remove(Number(removed.dataset.remove));buildRows();applyDraft();stopTour();$('add-agent')?.focus();return;}
  if(e.target.closest('#add-agent')){$('agent-options').innerHTML=defs.map((d,i)=>mix.snapshot().included[i]?'':`<button data-add="${i}">${d.name}</button>`).join('');$('agent-picker').showModal();}
  const added=e.target.closest('[data-add]');if(added){const i=Number(added.dataset.add);mix.add(i);$('agent-picker').close();buildRows();applyDraft();$('percent-'+i).focus();}
});
$('close-picker').onclick=()=>$('agent-picker').close();
$('tour').onclick=()=>{tour=!tour;if(tour&&flowClock===0)flowKey='';updateTour();};
$('reset').onclick=()=>{preset='Enterprise';appliedMix=mix.reset();buildRows();applyDraft(false);agent=0;flowClock=0;flowKey='';tour=motion;updateTour();};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('fullscreen').textContent='Use browser full screen';}};
function showTab(name){activeTab=name;$('panel-a').hidden=name!=='a';$('current').hidden=name!=='current';$('tab-a').setAttribute('aria-pressed',name==='a');$('tab-current').setAttribute('aria-pressed',name==='current');last=performance.now();}
$('tab-a').onclick=()=>showTab('a');$('tab-current').onclick=()=>showTab('current');
reduced.addEventListener('change',e=>{motion=!e.matches;if(!motion){tour=false;transition=null;currentCounts=simCounts.slice();}updateTour();render(0);});
document.addEventListener('visibilitychange',()=>last=performance.now());
function fitPanel(){
  const panel=$('panel-a');
  if(innerWidth<900){panel.style.transform='none';panel.style.marginLeft='0';panel.style.marginTop='0';return;}
  const available=innerHeight-44,scale=Math.min(innerWidth/1440,available/820);
  panel.style.transform=`scale(${scale})`;panel.style.marginLeft=`${Math.max(0,(innerWidth-1440*scale)/2)}px`;panel.style.marginTop=`${Math.max(0,(available-820*scale)/2)}px`;
}
addEventListener('resize',fitPanel);fitPanel();
buildRows();syncEditor();updateTour();render(0);
setInterval(()=>{const now=performance.now(),dt=Math.min(.25,(now-last)/1000);last=now;if(document.hidden||activeTab!=='a')return;elapsed+=dt;render(dt);},100);
window.__panelA={snapshot:()=>({result:simResult,preset,draft:mix.snapshot(),transition:!!transition,cpu:cpuDisplay,memory:memoryDisplay}),preset:loadMix};
