  /* Full workflow strip: every worker's steps in order, then the final step. Shared by both demo pages.
     The active step advances on the caller's clock; tick() reports when one full pass completes. */
  const WorkflowStrip=(()=>{
    const roleColor={'LLM':'#c39bff','CPU service':'#53c3ff','CPU sandbox':'#f0be68','CPU check':'#56dcbf'};
    const STEP_SECONDS=1.2;
    const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    function cycleFor(defs,i,worker){return defs[i].cycle.filter(c=>!(i===3&&worker===2&&c[1]==='Fetch definitions')).flatMap(c=>c[1]==='Record and draft'?[['CPU service','Record outcome'],['LLM','Draft result']]:[c]);}
    function stages(defs,i){
      const d=defs[i],many=d.workers.length>1;
      const list=d.workers.map((w,k)=>({title:(many?(k+1)+' · ':'')+w,job:d.jobs[k],steps:cycleFor(defs,i,k)}));
      list.push(i===0?{title:'Answer',job:'Reply is the result',steps:[['CPU check','Return the checked answer']]}:{title:'Final',job:'Synthesis and review',steps:[['LLM','Synthesize results'],['CPU check','Check final structure'],['LLM','Review final answer']]});
      return list;
    }
    function legend(){return Object.entries(roleColor).map(([r,c])=>`<span><i style="background:${c}"></i>${r}</span>`).join('');}
    function create(container,{defs,onStep,rows=3}={}){
      let agent=-1,clock=0,flat=[],els=[],stageEls=[],shown=-1;
      container.classList.add('wf-strip');container.style.setProperty('--wf-rows',rows);
      function paint(idx){
        if(idx===shown)return;shown=idx;
        els.forEach((e,k)=>{e.classList.toggle('active',k===idx);e.classList.toggle('done',k<idx);});
        stageEls.forEach((s,k)=>s.classList.toggle('current',!!flat[idx]&&flat[idx][2]===k));
        if(onStep)onStep(flat[idx]?{role:flat[idx][0],label:flat[idx][1]}:null);
      }
      function select(i){
        agent=i;clock=0;shown=-1;flat=[];
        if(i<0){container.replaceChildren();els=[];stageEls=[];if(onStep)onStep(null);return;}
        const list=stages(defs,i);
        container.innerHTML=list.map((s,si)=>`<section class="wf-stage" style="flex:${Math.max(1.6,Math.ceil(s.steps.length/rows))} 1 0"><header title="${esc(s.title+' · '+s.job)}"><b>${esc(s.title)}</b><span>${esc(s.job)}</span></header><ol class="wf-steps">${s.steps.map(([role,label])=>{flat.push([role,label,si]);return `<li class="wf-step" style="--role:${roleColor[role]}" title="${esc(role+' · '+label)}"><i></i><span>${esc(label)}</span></li>`;}).join('')}</ol></section>`).join('<span class="wf-arrow" aria-hidden="true">›</span>');
        els=[...container.querySelectorAll('.wf-step')];stageEls=[...container.querySelectorAll('.wf-stage')];
        paint(0);
      }
      // Advance by dt seconds; returns true once a full pass (plus a held final beat) completes.
      function tick(dt){
        if(agent<0||!flat.length)return false;
        clock+=dt;
        const idx=Math.floor(clock/STEP_SECONDS);
        if(idx>flat.length){clock=0;shown=-1;paint(0);return true;}
        paint(Math.min(idx,flat.length-1));return false;
      }
      return {select,tick,agent:()=>agent,stepCount:()=>flat.length,stages:i=>stages(defs,i)};
    }
    return {create,legend,stages,roleColor};
  })();
