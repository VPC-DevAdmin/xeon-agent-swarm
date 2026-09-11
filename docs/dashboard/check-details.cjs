const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'steady-state.html'),'utf8');
const value=name=>JSON.parse(html.match(new RegExp('const '+name+' = (.*);'))[1]);
const RESULTS=value('RESULTS'),SIM_PROFILE=value('SIM_PROFILE');
const simulator=require('./simulation-model.cjs').create(SIM_PROFILE,RESULTS);
const mix=require('./simulation-model.cjs').mix(RESULTS,simulator.times).snapshot();
const source=fs.readFileSync(path.join(__dirname,'steady-state.src.html'),'utf8');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{classList:{add(){},toggle(){}},innerHTML:'',textContent:'',hidden:false});return nodes.get(id);};
const context=vm.createContext({RESULTS,SIM_PROFILE,simulator,simResult:simulator.calculate(mix.counts),simCounts:mix.counts,appliedMix:mix,C:RESULTS.capacity,S:RESULTS.serving,ASSETS:{dell:'dell.png',xeon:'xeon.png',r770:'r770.png',xe7740:'xe7740.png'},motion:true,reduced:{matches:false},$:node,decimal:n=>n.toFixed(1),compact:n=>n.toFixed(0)});
vm.runInContext(source.match(/const defs=\[[\s\S]*?\n  \];/)[0],context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'detail-views.js'),'utf8'),context);
for(const key of ['agent:0','agent:1','agent:2','agent:3','agent:4','worker:3:1','stage:4:0:2','r770','xe7740','cpu','cpu-share','cpu-reference','serving','llm','memory','capacity','residency','architecture','methodology']){
  vm.runInContext(`renderDetailView(${JSON.stringify(key)})`,context);
  const content=node('topic-body').innerHTML;
  assert.ok(content.length>150,key);assert.ok(!/undefined(?!-behavior)|NaN|Infinity/.test(content),key);
  if(key.startsWith('agent:'))assert.ok(content.includes('dv-flow'));
  if(['r770','xe7740'].includes(key)){assert.ok(content.includes('dv-product'));assert.ok(content.includes('Intel Xeon'));}
  if(key==='cpu')assert.ok(content.includes('dv-sweep')&&content.includes('Reserved core pools'));
  if(key==='methodology')assert.ok(content.includes('Model boundaries and provenance'));
}
context.simResult=simulator.calculate([0,0,0,0,0]);
for(const key of ['cpu','memory','serving','capacity','r770','xe7740']){vm.runInContext(`renderDetailView('${key}')`,context);assert.ok(!/NaN|Infinity/.test(node('topic-body').innerHTML));}
assert.ok(!html.includes('/*__DETAIL_'));assert.ok(html.includes('data-topic="r770"'));assert.ok(html.includes('data-topic="xe7740"'));
console.log('PASS: all agent, server, CPU, memory, model-serving, capacity, and methodology views; zero-load handling; embedded resources and server routing.');
