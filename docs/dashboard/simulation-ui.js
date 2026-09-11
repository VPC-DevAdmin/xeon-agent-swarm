  const simulator=AgentSimulator.create(SIM_PROFILE,RESULTS);
  const mixControl=AgentSimulator.mix(RESULTS,simulator.times);
  let tourRunning=!reduced.matches;
  const tourButton=document.createElement('button');tourButton.className='control';tourButton.id='demo-tour';
  $('play').replaceWith(tourButton);$('restart').remove();
  function updateTour(){tourButton.textContent=tourRunning?'Pause Tour':'Start Tour';tourButton.setAttribute('aria-label',tourRunning?'Pause the guided workflow tour':'Start the guided workflow tour');}
  function toggleTour(){tourRunning=!tourRunning;if(tourRunning){playing=true;motion=true;last=performance.now();}updateTour();syncControls();render();}
  tourButton.onclick=toggleTour;updateTour();
  const presets={default:[50,8,8,17,17],service:[75,15,5,3,2],documents:[20,15,50,10,5],development:[15,10,5,20,50]};
  const mixTitles={default:'Enterprise agent mix',service:'Service desk agent mix',documents:'Document operations agent mix',development:'Development team agent mix',custom:'Custom agent mix'};
  let selectedMix='default',savedCustom=mixControl.snapshot();
  const mixHeading=document.querySelector('.fleet .panel-title h2');mixHeading.classList.add('mix-heading');
  mixHeading.innerHTML='<span id="mix-heading-text"></span><span class="mix-heading-chevron" aria-hidden="true">⌄</span><select id="mix-selector" aria-label="Choose agent mix">'+Object.entries(mixTitles).map(([key,label])=>'<option value="'+key+'">'+label+'</option>').join('')+'</select>';
  const mixSelector=$('mix-selector');
  mixSelector.addEventListener('click',e=>e.stopPropagation());mixSelector.addEventListener('keydown',e=>e.stopPropagation());
  mixSelector.onchange=e=>{
    selectedMix=e.target.value;
    const choice=selectedMix==='custom'?savedCustom:{weights:presets[selectedMix],included:[true,true,true,true,true]};
    choice.weights.forEach((n,i)=>{const present=mixControl.snapshot().included[i];if(choice.included[i]){if(!present)mixControl.add(i);mixControl.setPercent(i,n);}else if(present)mixControl.remove(i);});
    applyMix(undefined,true);
  };
  document.addEventListener('pointerdown',e=>{if(e.target.closest('#screen')&&!e.target.closest('.controls')){tourRunning=false;updateTour();}},true);
  document.addEventListener('keydown',e=>{if(e.target.closest('#screen')&&!e.target.closest('.controls')&&['Enter',' ','ArrowUp','ArrowDown'].includes(e.key)){tourRunning=false;updateTour();}},true);
  const defaultWeights=mixControl.snapshot().weights;
  function comparisonText(r){
    if(appliedMix.weights.every((n,i)=>n===defaultWeights[i]))return 'Mix: 100% · Shorter tasks finish sooner.';
    const baseline=AgentSimulator.mix(RESULTS,simulator.times);baseline.setTarget(appliedMix.target);
    const b=simulator.calculate(baseline.snapshot().counts);
    const delta=(n,ref)=>ref?((n/ref-1)*100):0;
    const signed=n=>(n>0?'+':'')+Math.round(n)+'%';
    return r.total?'At the same agent count vs default: CPU demand '+signed(delta(r.cpuEquivalent,b.cpuEquivalent))+' · GPU demand '+signed(delta(r.gpuEquivalent,b.gpuEquivalent)):'No active agents';
  }
  let previousSizing=null,noticeTimer;
  const sizingNotice=document.createElement('div');sizingNotice.className='sizing-notice';sizingNotice.hidden=true;sizingNotice.setAttribute('role','status');document.querySelector('.systems').appendChild(sizingNotice);
  function explainSizing(r){
    if(previousSizing&&(r.cpuSystems!==previousSizing.cpu||r.gpuSystems!==previousSizing.gpu)){
      const changedCPU=r.cpuSystems!==previousSizing.cpu;
      sizingNotice.textContent=changedCPU?(r.cpuSystems>previousSizing.cpu?'CPU capacity added. Utilization is averaged across '+r.cpuSystems+' R770 servers.':r.cpuSystems+' R770 servers now carry the workload.'):r.gpuSystems+' XE7740 servers now provide model-serving capacity.';
      sizingNotice.hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{sizingNotice.hidden=true;},7000);
    }
    previousSizing={cpu:r.cpuSystems,gpu:r.gpuSystems};
  }
  function highlightResources(node){
    const role=node?.querySelector('b')?.textContent||'',label=node?.querySelector('span')?.textContent||'';
    const family=role==='CPU sandbox'?0:role==='CPU service'?(/retriev|lookup|embed/i.test(label)?1:/record/i.test(label)?3:2):role==='CPU check'?2:-1;
    for(let i=0;i<4;i++)$('family-'+i).classList.toggle('activity-highlight',motion&&family===i);
    document.querySelector('.hardware .exchange')?.classList.toggle('activity-highlight',motion&&role==='LLM');
  }
  function showThroughput(rates){
    rates.forEach((rate,i)=>{
      const el=$('throughput-'+i);
      el.querySelector('b').textContent=Math.round(rate).toLocaleString('en-US');
      el.title='Modeled steady-state completions per hour across the target deployment.';
    });
  }
  let simulation=true,pendingMix=false,appliedMix=mixControl.snapshot(),simCounts=appliedMix.counts,simResult=null;
  let transitionClock=0,workloadTransition=null,displayCounts=simCounts.slice();
  function transitioningCounts(){
    if(!workloadTransition)return simCounts.slice();
    const t=Math.min(1,(transitionClock-workloadTransition.started)/8),ease=t*t*(3-2*t);
    const exact=simCounts.map((n,i)=>workloadTransition.from[i]+(n-workloadTransition.from[i])*ease);
    const counts=exact.map(Math.floor),total=Math.round(exact.reduce((a,b)=>a+b,0));
    const order=exact.map((n,i)=>({i,f:n-counts[i]})).sort((a,b)=>b.f-a.f);
    for(let k=0,remaining=total-counts.reduce((a,b)=>a+b,0);k<remaining;k++)counts[order[k].i]++;
    if(t===1)workloadTransition=null;
    return counts;
  }
  const resetMix=document.createElement('button');resetMix.className='control';resetMix.id='simulation-reset';resetMix.textContent='Reset to Default';document.querySelector('.controls').prepend(resetMix);
  $('screen').classList.add('simulating');
  const targetInput=document.createElement('input');targetInput.type='number';targetInput.min='0';targetInput.max='100000';targetInput.step='1';targetInput.id='agent-target';targetInput.className='agent-target';targetInput.value=mixControl.snapshot().target;targetInput.setAttribute('aria-label','Target total working agents across deployment');
  document.querySelector('.finding').prepend(targetInput);
  const stopControl=e=>e.stopPropagation();
  targetInput.addEventListener('click',stopControl);targetInput.addEventListener('keydown',stopControl);
  targetInput.addEventListener('input',()=>{const n=Number(targetInput.value);if(targetInput.value===''||!Number.isInteger(n)||n<0||n>100000){targetInput.setCustomValidity('Enter a whole target from 0 to 100,000');return;}targetInput.setCustomValidity('');mixControl.setTarget(n);applyMix();});
  targetInput.addEventListener('change',()=>{targetInput.value=mixControl.snapshot().target;targetInput.setCustomValidity('');});
  const addAgent=document.createElement('button');addAgent.type='button';addAgent.id='add-agent';addAgent.className='shadow-agent';addAgent.innerHTML='<span aria-hidden="true">+</span> Add agent';addAgent.setAttribute('aria-haspopup','dialog');addAgent.hidden=true;$('lanes').appendChild(addAgent);
  const picker=document.createElement('dialog');picker.className='agent-picker';picker.setAttribute('aria-labelledby','agent-picker-title');
  picker.innerHTML='<button class="control picker-close" type="button" aria-label="Close agent selector">×</button><h2 id="agent-picker-title">Add agent</h2><p>Choose a missing archetype.</p><div class="agent-options"></div>';
  document.body.appendChild(picker);
  picker.querySelector('.picker-close').onclick=()=>picker.close();
  picker.addEventListener('keydown',stopControl);
  picker.addEventListener('click',e=>{if(e.target===picker){const r=picker.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)picker.close();}});
  addAgent.onclick=()=>{
    const options=picker.querySelector('.agent-options');options.replaceChildren();
    mixControl.snapshot().included.forEach((present,i)=>{if(present)return;const option=document.createElement('button');option.type='button';option.textContent=defs[i].name;option.onclick=()=>{mixControl.add(i);picker.close();applyMix();$('sim-percent-'+i).focus();};options.appendChild(option);});
    picker.showModal();
  };
  function mixNotice(text){document.querySelector('.fleet .panel-title p').textContent=text;}
  function editPercent(i,n,input){
    try{mixControl.setPercent(i,n);input.setCustomValidity('');applyMix(input);}
    catch(error){input.setCustomValidity(error.message);mixNotice(error.message);}
  }
  const capacityStrip=document.createElement('div');capacityStrip.className='sim-capacity';capacityStrip.setAttribute('data-topic','architecture');capacityStrip.setAttribute('role','button');capacityStrip.tabIndex=0;
  document.querySelector('.system-note').after(capacityStrip);
  document.querySelectorAll('.lane').forEach((el,i)=>{
    el.querySelector('h3+small').textContent='';
    const throughput=document.createElement('div');throughput.className='lane-throughput';throughput.id='throughput-'+i;
    throughput.innerHTML='<b>—</b><span>completed/hour</span>';
    el.querySelector('.workline').after(throughput);
    const control=document.createElement('div');control.className='mix-edit';
    control.innerHTML='<input class="sim-percent" id="sim-percent-'+i+'" type="number" min="0" max="100" step="1" aria-label="'+defs[i].name+' incoming workflow percentage"><span class="mix-caption">% incoming</span><div class="mix-ticks"><button type="button" class="mix-step mix-increase" aria-label="Increase '+defs[i].name+' percentage"><svg viewBox="0 0 12 8" aria-hidden="true"><path d="M2 6 6 2 10 6"/></svg></button><button type="button" class="mix-step mix-decrease" aria-label="Decrease '+defs[i].name+' percentage"><svg viewBox="0 0 12 8" aria-hidden="true"><path d="M2 6 6 2 10 6"/></svg></button></div>';
    control.querySelectorAll('.mix-step').forEach(button=>{
      button.addEventListener('keydown',stopControl);
      button.addEventListener('click',e=>{e.stopPropagation();const delta=button.classList.contains('mix-increase')?1:-1;editPercent(i,Math.max(0,Math.min(100,mixControl.snapshot().weights[i]+delta)),input);input.value=mixControl.snapshot().weights[i];});
    });
    const input=control.querySelector('input');input.addEventListener('click',stopControl);input.addEventListener('keydown',stopControl);
    input.addEventListener('input',()=>{const n=Number(input.value);if(input.value===''||!Number.isInteger(n)||n<0||n>100){input.setCustomValidity('Enter a whole percentage from 0 to 100');return;}editPercent(i,n,input);});
    input.addEventListener('change',()=>{input.setCustomValidity('');syncMixInputs();});
    const remove=document.createElement('button');remove.className='remove-archetype';remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove '+defs[i].name+' archetype');remove.title='Remove '+defs[i].name;remove.addEventListener('click',e=>{e.stopPropagation();mixControl.remove(i);applyMix();addAgent.focus();});remove.addEventListener('keydown',stopControl);
    el.querySelector('h3').parentElement.appendChild(control);el.appendChild(remove);
  });
  function syncMixInputs(editing){
    const m=mixControl.snapshot();
    document.querySelectorAll('.lane').forEach((el,i)=>{el.hidden=!m.included[i];const input=$('sim-percent-'+i);if(input!==editing){input.value=m.weights[i];input.setCustomValidity('');}el.querySelector('.mix-decrease').disabled=m.weights[i]===0;el.querySelector('.mix-increase').disabled=m.weights[i]===100;});
    const visible=m.included.filter(Boolean).length;addAgent.hidden=visible===5;
    document.querySelector('.finding .label').innerHTML='Target total agents<small>Deployment total</small>';
    targetInput.value=m.target;
    $('mix-heading-text').textContent=mixTitles[selectedMix];mixSelector.value=selectedMix;
    const hint=document.querySelector('.fleet .panel-title p');hint.setAttribute('role','status');hint.textContent='Mix: '+m.total+'%'+(m.valid?' · Shorter tasks finish sooner.':m.total<100?' · add '+(100-m.total)+'% · showing previous results':' · reduce '+(m.total-100)+'% · showing previous results');
    document.querySelectorAll('.lane-count small').forEach(el=>el.textContent='working now');
  }
  function applyMix(editing,fromSelector=false){
    const draft=mixControl.snapshot();
    if(!fromSelector&&(selectedMix==='custom'||!draft.included.every(Boolean)||draft.weights.some((n,i)=>n!==presets[selectedMix][i]))){selectedMix='custom';savedCustom=draft;}
    pendingMix=!draft.valid;
    if(pendingMix){syncMixInputs(editing);$('screen').classList.add('editing-mix');return;}
    appliedMix=draft;simCounts=draft.counts;setSimulation(true,editing);
  }
  function setSimulation(on,editing){
    pendingMix=false;$('screen').classList.remove('editing-mix');
    if(!on){selectedMix='default';appliedMix=mixControl.reset();simCounts=appliedMix.counts;}
    workloadTransition=reduced.matches?null:{from:displayCounts.slice(),started:transitionClock};
    simulation=true;lastFocus='';
    syncMixInputs(editing);render();
  }
  resetMix.onclick=()=>{tourRunning=true;updateTour();setSimulation(false);};
  syncMixInputs();
  const decimal=n=>n.toLocaleString('en-US',{maximumFractionDigits:1});
  const compact=n=>n>=1000?(n/1000).toFixed(1)+'K':decimal(n);
  let hardwareKey='';
  // Reuse measured process-family variation; costs set the amplitude for this mix.
  // These are aggregate traces, not per-archetype event telemetry.
  const activityTrace=samples.filter(s=>s[1]>=start&&s[1]<end&&s[3]>0&&s[4]>0).map(s=>{
    const f=s[5];return {t:s[1]-start,values:[s[3],s[4],f[0],f[1],f[2]+f[3]+f[4],f[5]+f[6]+f[7]]};
  });
  const traceMeans=Array.from({length:6},(_,i)=>activityTrace.reduce((sum,s)=>sum+s.values[i],0)/Math.max(1,activityTrace.length));
  function recordedFactors(t){
    if(!activityTrace.length)return [1,1,1,1,1,1];
    let right=activityTrace.findIndex(s=>s.t>t);if(right<0)right=0;
    const b=activityTrace[right],a=activityTrace[(right+activityTrace.length-1)%activityTrace.length];
    const at=right===0?a.t-length:a.t,bt=b.t,x=t>activityTrace.at(-1).t?t-length:t;
    const blend=Math.max(0,Math.min(1,(x-at)/(bt-at||1)));
    return a.values.map((v,i)=>traceMeans[i]>0?(v+(b.values[i]-v)*blend)/traceMeans[i]:1);
  }
  let smoothedActivity=null,activityClock=0;
  function activityReadings(r){
    const factors=motion?recordedFactors(tau):[1,1,1,1,1,1];
    const families=r.families.map((n,i)=>n*factors[i+2]);
    // The model already divides this mix's CPU work by allocated physical cores.
    // Whole-host variation moves that baseline; family traces only shape the split.
    const target={cpu:r.total?Math.min(100,r.cpuPercent*factors[0]):0,
      memoryGB:r.memoryGB*(1+(factors[1]-1)*(r.memoryGB?Math.max(0,r.memoryGB-r.sockets*C.memoryGB*(1-SIM_PROFILE.analystMemoryShare))/r.memoryGB:0))};
    const dt=Math.max(0,transitionClock-activityClock);activityClock=transitionClock;
    const alpha=reduced.matches?1:1-Math.exp(-dt/1.2);
    if(!smoothedActivity||(!r.total&&!workloadTransition))smoothedActivity=target;
    else for(const key of ['cpu','memoryGB'])smoothedActivity[key]+=(target[key]-smoothedActivity[key])*alpha;
    return {...smoothedActivity,families};
  }
  function renderSimulation(){
    displayCounts=transitioningCounts();
    const r=simResult=simulator.calculate(displayCounts);
    const activity=activityReadings(r);
    if(!pendingMix)mixNotice(workloadTransition?'Applying workload mix…':comparisonText(r));explainSizing(r);
    showThroughput(r.rates.map(rate=>rate*3600),'simulation');
    $('finding-resident').textContent=r.total.toLocaleString();$('finding-cpu').textContent=Math.round(activity.cpu)+'%';$('finding-ratio').textContent=r.total?'1 : '+r.ratio.toFixed(2):'—';
    const labels=document.querySelectorAll('.finding .label');
    labels[0].innerHTML='Target total agents<small>Deployment total</small>';
    labels[1].innerHTML='Average CPU utilization<small>Across '+r.cpuSystems+' PowerEdge R770 Server'+(r.cpuSystems===1?'':'s')+'</small>';
    labels[2].innerHTML='CPU-to-GPU ratio<small>Per CPU socket · before system rounding</small>';
    $('resident-now').textContent=r.total.toLocaleString();document.querySelector('.census small').textContent='working across fleet';
    document.querySelector('.census').setAttribute('aria-label','Working agents across required R770 systems');
    r.counts.forEach((n,i)=>{$('count-'+i).textContent=n;[...$('population-'+i).children].forEach((e,k)=>e.classList.toggle('empty',k>=Math.ceil(n/Math.max(4,Math.max(...r.counts)/32))));});
    document.querySelectorAll('.lane-count small').forEach(e=>e.textContent='working now');
    const gpuCount=Math.ceil(r.gpuEquivalent-1e-9),lastGPUCount=gpuCount?r.gpuSystems>1?gpuCount-8*(r.gpuSystems-1):gpuCount:0;
    const hardwareLayoutKey=[r.cpuSystems,r.gpuSystems,gpuCount].join(':');
    if(hardwareKey!==hardwareLayoutKey){
      hardwareKey=hardwareLayoutKey;
      document.querySelector('.hardware').innerHTML=`<div class="machine" data-topic="r770" role="button" tabindex="0" aria-label="R770 systems for this workload"><h3>${r.cpuSystems} × R770</h3><small>${r.sockets} CPU sockets</small><div class="server-plinth r770-fleet">${Array.from({length:Math.min(4,r.cpuSystems)},()=>'<img class="server-photo" src="'+ASSETS.r770+'" alt="Dell PowerEdge R770">').join('')}</div></div><div class="exchange" data-topic="llm" role="button" tabindex="0"><span>LLM calls</span><svg viewBox="0 0 71 28" aria-hidden="true"><path d="M4 14H67M10 9L4 14L10 19M61 9L67 14L61 19" stroke="#a0d4ec" stroke-width="1.5" fill="none"/><circle class="signal" cx="13" cy="14" r="3" fill="#c39bff"/></svg><img class="xeon" src="${ASSETS.xeon}" alt="Intel Xeon processors"></div><div class="machine" data-topic="xe7740" role="button" tabindex="0"><h3>${r.gpuSystems} × XE7740</h3><small>${gpuCount} GPUs allocated</small><div class="server-plinth">${r.gpuSystems?'<img class="server-photo gpu-photo" src="'+ASSETS.xe7740+'" alt="Dell PowerEdge XE7740">':''}</div></div>`;
    }
    document.querySelector('.system-note').hidden=true;
    capacityStrip.innerHTML=`<div><b>${r.cpuSystems} R770${r.cpuSystems===1?'':'s'} required</b><small>${r.extraCPU?'+'+r.extraCPU+' beyond initial 3':r.sockets+' CPU sockets'}</small></div><div><b>${r.gpuSystems} XE7740${r.gpuSystems===1?'':'s'} required</b><small>${gpuCount} GPUs allocated${r.extraGPU?' · +'+r.extraGPU+' systems':''}</small></div>`;
    $('model-calls').textContent=decimal(r.calls)+' model calls/s · fleet total';$('output-demand').textContent=compact(r.output)+' tokens/s';$('serving-model').textContent=S.model+' · '+S.outputTokensPerSecondPerGpu.toLocaleString()+'/s/GPU';
    $('cpu-now').textContent=Math.round(activity.cpu)+'%';$('memory-now').textContent=decimal(r.cpuSystems?activity.memoryGB/r.cpuSystems:0)+' GB';
    document.querySelector('.memory .recorded-tag').textContent='Per R770 · '+compact(activity.memoryGB)+' GB deployment';
    const total=activity.families.reduce((a,b)=>a+b,0);activity.families.forEach((n,i)=>{$('family-'+i).style.width=(total?n/total*100:0)+'%';$('family-'+i).title=categories[i][0]+': '+decimal(total?n/total*100:0)+'% modeled CPU work';});
    $('sample-clock').textContent='';$('clock').textContent=playing?'Simulation · working pool':'Simulation paused';
    $('progress').style.width='100%';
    state={mode:'simulation',...r,target:appliedMix.target,incomingShares:appliedMix.weights,counts:r.counts,resident:r.total,cpu:activity.cpu,memory:r.cpuSystems?activity.memoryGB/r.cpuSystems:0,shares:r.families,playing,motion,time:tau};
    focus();
  }
  function showSimulationTopic(key){renderDetailView(key);}
  window.__simulator={enable:()=>applyMix(),disable:()=>setSimulation(false),setTarget:n=>{mixControl.setTarget(n);applyMix();},setPercent:(i,n)=>{mixControl.setPercent(i,n);applyMix();},remove:i=>{mixControl.remove(i);applyMix();},add:i=>{mixControl.add(i);applyMix();},mix:()=>mixControl.snapshot(),snapshot:()=>simulation?simResult:null,defaults:()=>simulator.defaultCounts.slice()};
