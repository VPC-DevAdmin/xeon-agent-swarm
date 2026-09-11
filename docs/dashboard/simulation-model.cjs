/* Current-definition resource model, shared by the offline demo and checks. */
(function(root){
  'use strict';
  const sum=xs=>xs.reduce((a,b)=>a+b,0);
  const cpuSystemCount=cpuEquivalent=>cpuEquivalent>0?Math.max(1,Math.ceil(cpuEquivalent/2/1.1-1e-9)):0;
  function create(profile,results){
    if(profile.version!==results.definitionVersion)throw Error('Simulation definition version mismatch');
    const names=results.archetypes.map(a=>a.name), ref=profile.reference;
    const shares=names.map(n=>results.definitions.tile[n]/sum(Object.values(results.definitions.tile)));
    const outputMean=sum(results.archetypes.map((a,i)=>a.outputTokens*shares[i]));
    const refRate=results.capacity.outputTokensPerSecond/outputMean;
    const rawTimes=profile.timings.map(t=>t.meanSeconds);
    const lifetimeScale=results.capacity.resident/(refRate*sum(rawTimes.map((t,i)=>t*shares[i])));
    const times=rawTimes.map(t=>t*lifetimeScale);
    const referenceCounts=times.map((t,i)=>t*refRate*shares[i]);
    const defaultCounts=referenceCounts.map(n=>Math.round(n*6));
    defaultCounts[defaultCounts.indexOf(Math.max(...defaultCounts))]+=results.capacity.resident*6-sum(defaultCounts);
    times.forEach((_,i)=>{times[i]=defaultCounts[i]/(6*refRate*shares[i]);});
    const costs=profile.costs;
    const sandboxMean=sum(costs.map((c,i)=>c.sandboxCoreSeconds*shares[i]));
    const retrievalMean=sum(costs.map((c,i)=>(c.pairs*.02+c.queryCount*.2+c.ingestCoreSeconds)*shares[i]));
    const executionMean=sum(results.archetypes.map((a,i)=>a.modelCalls*shares[i]));
    const busy=results.capacity.coreMsPerSecond/1000;
    const measured=results.capacity.cpuShares;
    const analystMemory=results.capacity.memoryGB*profile.analystMemoryShare;
    const memoryBase=results.capacity.memoryGB-analystMemory;
    const analystFraction=analystMemory/(defaultCounts[3]/6*profile.analystJobGB);
    function demand(rates){
      return {
        sandbox:sum(rates.map((r,i)=>r*costs[i].sandboxCoreSeconds)),
        retrieval:sum(rates.map((r,i)=>r*(costs[i].pairs*.02+costs[i].queryCount*.2+costs[i].ingestCoreSeconds))),
        calls:sum(rates.map((r,i)=>r*results.archetypes[i].modelCalls)),
        pairs:sum(rates.map((r,i)=>r*costs[i].pairs)),
        queries:sum(rates.map((r,i)=>r*costs[i].queryCount)),
        ingest:sum(rates.map((r,i)=>r*costs[i].ingestCoreSeconds)),
        output:sum(rates.map((r,i)=>r*results.archetypes[i].outputTokens))
      };
    }
    const base=demand(shares.map(s=>s*refRate));
    function calculate(input){
      if(!Array.isArray(input)||input.length!==5||input.some(n=>!Number.isFinite(n)||n<0||n>100000||!Number.isInteger(n)))throw Error('Use five whole agent counts between 0 and 100,000');
      const counts=input.slice(), total=sum(counts),rates=counts.map((n,i)=>n/times[i]),d=demand(rates);
      const activity=total>0;
      // Per-CPU service pools retain the allocation of record. Scale-out, not rebinding.
      const appUnits=.85*d.sandbox/base.sandbox+.15*d.calls/base.calls;
      const limits={
        'Agent execution':appUnits,
        'Reranking':d.pairs*.02/(results.platform.allocation.reranking*profile.serviceTarget),
        'Query embedding':d.queries*.2/(results.platform.allocation.queryEmbedding*profile.serviceTarget),
        'Document embedding':d.ingest/(results.platform.allocation.ingestEmbedding*profile.serviceTarget)
      };
      // Fixed baseline plus active analyst working sets. Other per-agent memory is not isolated.
      const activeAnalysts=counts[3]*analystFraction;
      limits.Memory=activeAnalysts*profile.analystJobGB/(results.platform.memoryGB*profile.memoryTarget-memoryBase);
      let [bottleneck,cpuEquivalent]=Object.entries(limits).sort((a,b)=>b[1]-a[1])[0];
      if(activity)cpuEquivalent=Math.max(cpuEquivalent,.001);
      const cpuSystems=activity?cpuSystemCount(cpuEquivalent):0;
      const sockets=cpuSystems*2;
      const gpuEquivalent=d.output/results.serving.outputTokensPerSecondPerGpu;
      const gpuSystems=activity?Math.max(1,Math.ceil((gpuEquivalent-1e-9)/8)):0;
      const families=[
        d.sandbox/(refRate*sandboxMean)*busy*measured.sandbox/100,
        d.retrieval/(refRate*retrievalMean)*busy*measured.retrieval/100,
        activity?(sockets*.6+d.calls/(refRate*executionMean)*.4)*busy*measured.execution/100:0,
        sockets*busy*measured.support/100
      ];
      const cpuPercent=sockets?Math.min(100,sum(families)/(sockets*results.platform.cores)*100):0;
      const memoryGB=sockets*memoryBase+activeAnalysts*profile.analystJobGB;
      const gpuUsage=gpuSystems?gpuEquivalent/(gpuSystems*8)*100:0;
      const lastGpuUsage=gpuSystems?gpuEquivalent-8*(gpuSystems-1):0;
      const ratio=cpuEquivalent?gpuEquivalent/cpuEquivalent:0;
      const ratioR770=gpuEquivalent?cpuEquivalent/2/gpuEquivalent*8:0;
      const initialLoad=cpuEquivalent/6;
      return {counts,total,rates,times,output:d.output,calls:d.calls,cpuEquivalent,cpuSystems,sockets,gpuEquivalent,gpuSystems,gpuUsage,lastGpuUsage,ratio,ratioR770,cpuPercent,memoryGB,memoryPerCPU:sockets?memoryGB/sockets:0,families,bottleneck:activity?bottleneck:'No workload',limits,initialLoad,extraCPU:Math.max(0,cpuSystems-3),extraGPU:Math.max(0,gpuSystems-1),referenceCounts,defaultCounts};
    }
    return {calculate,defaultCounts,times,referenceCounts};
  }
  function mix(results,times){
    const defaults=results.archetypes.map(a=>results.definitions.tile[a.name]);
    let weights=defaults.slice(),included=weights.map(()=>true),target=720;
    function apportion(values,total){
      const denominator=sum(values);if(!denominator)return values.map(()=>0);
      const exact=values.map(w=>w/denominator*total),rounded=exact.map(w=>Math.floor(w+1e-10));
      const order=exact.map((w,i)=>({i,remainder:w-rounded[i]})).sort((a,b)=>b.remainder-a.remainder);
      for(let n=0,remaining=total-sum(rounded);n<remaining;n++)rounded[order[n].i]++;
      return rounded;
    }
    const normalize=()=>{weights=apportion(weights,100);};
    normalize();
    function index(i){if(!Number.isInteger(i)||i<0||i>=5)throw Error('Unknown archetype');}
    function snapshot(){
      const residencyWeights=weights.map((w,i)=>w*times[i]),denominator=sum(residencyWeights);
      const exact=residencyWeights.map(w=>denominator?target*w/denominator:0),counts=exact.map(Math.floor);
      const order=exact.map((n,i)=>({i,remainder:n-counts[i]})).sort((a,b)=>b.remainder-a.remainder);
      const remaining=denominator?target-sum(counts):0;
      for(let n=0;n<remaining;n++)counts[order[n].i]++;
      const total=sum(weights),valid=total===100;
      return {target,weights:weights.slice(),included:included.slice(),total,valid,counts:valid?counts:null};
    }
    return {
      snapshot,
      setTarget(n){if(!Number.isInteger(n)||n<0||n>100000)throw Error('Use a whole target from 0 to 100,000');target=n;return snapshot();},
      setPercent(i,n){
        index(i);if(!included[i]||!Number.isInteger(n)||n<0||n>100)throw Error('Use a whole percentage from 0 to 100');
        weights[i]=n;return snapshot();
      },
      remove(i){index(i);included[i]=false;weights[i]=0;return snapshot();},
      add(i){index(i);if(included[i])throw Error('Archetype already included');weights[i]=0;included[i]=true;return snapshot();},
      reset(){weights=defaults.slice();included=weights.map(()=>true);target=720;normalize();return snapshot();}
    };
  }
  const api={create,mix,cpuSystemCount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AgentSimulator=api;
})(typeof globalThis!=='undefined'?globalThis:this);
