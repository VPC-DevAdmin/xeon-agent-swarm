const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,process.argv[2]||'layout-studies.html'),'utf8');
const nodes=new Map(),listeners=new Map(),timers=[];let now=0;
class Element{
  constructor(id){this.id=id;this.style={setProperty(){}};this.dataset={};this.value='';this.hidden=false;this.open=false;this.classList={add(){},toggle(){}};this.listeners={};}
  set innerHTML(value){this.html=value;for(const match of value.matchAll(/\bid="([^"]+)"/g))if(!nodes.has(match[1]))nodes.set(match[1],new Element(match[1]));}
  get innerHTML(){return this.html||'';}
  setAttribute(name,value){this.attributes??={};this.attributes[name]=value;} querySelectorAll(){return [];} addEventListener(name,fn){this.listeners[name]=fn;} setCustomValidity(){} focus(){} replaceChildren(){this.innerHTML='';} showModal(){this.open=true;} close(){this.open=false;}
}
for(const m of html.matchAll(/\bid="([^"]+)"/g))nodes.set(m[1],new Element(m[1]));
const exchange=new Element('exchange');
const document={documentElement:new Element('html'),hidden:false,activeElement:null,getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>[],querySelector:s=>s==='.exchange'?exchange:null,addEventListener:(n,f)=>{const prior=listeners.get(n);listeners.set(n,prior?e=>{prior(e);f(e);}:f);}};
const context=vm.createContext({document,window:{},innerWidth:1440,innerHeight:900,addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}}),performance:{now:()=>now},setInterval:fn=>timers.push(fn),console});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const api=context.window.__panelA,advance=()=>{for(let i=0;i<120;i++){now+=100;timers.forEach(f=>f());}};
assert.equal(api.snapshot().result.total,720);assert.equal(api.snapshot().result.cpuSystems,3);
const initial=api.snapshot().result;
api.preset('Development team');assert(api.snapshot().transition);advance();assert.equal(api.snapshot().result.total,720);assert.notEqual(api.snapshot().result.counts[4],initial.counts[4]);
function step(i,delta){listeners.get('click')({target:{closest:s=>s==='[data-step]'?{dataset:{step:String(i),delta:String(delta)}}:null}});}
step(0,5);assert.equal(api.snapshot().draft.valid,false);advance();assert.equal(api.snapshot().result.total,720);
step(4,-5);advance();const custom=Array.from(api.snapshot().draft.weights);assert.equal(api.snapshot().preset,'Custom');
api.preset('Enterprise');advance();api.preset('Custom');advance();assert.deepEqual(Array.from(api.snapshot().draft.weights),custom);
const target=nodes.get('target');target.value='0';target.listeners.input({target});advance();assert.equal(api.snapshot().result.cpuSystems,0);assert.equal(api.snapshot().result.gpuSystems,0);assert.equal(api.snapshot().cpu,0);assert(nodes.get('cpu-photos').innerHTML.includes('No systems allocated'));
target.value='100000';target.listeners.input({target});advance();assert.equal(api.snapshot().result.total,100000);assert(Number.isFinite(api.snapshot().memory));
nodes.get('reset').onclick();advance();assert.equal(api.snapshot().result.total,720);
// The bottom box shows the selected agent's stages as chips and the current stage's steps, and keeps animating.
listeners.get('click')({target:{closest:s=>s==='[data-agent]'?{dataset:{agent:'1'}}:null}});
let chips=nodes.get('worker-sequence').innerHTML;assert(chips.includes('1 · Research')&&chips.includes('3 · Writing')&&chips.includes('4 · Finalize'));
advance();advance();assert.notEqual(nodes.get('worker-sequence').innerHTML,chips,'Stages advance with the tour stopped');
// Choosing a preset shows the workflow of its largest agent.
api.preset('Development team');assert(nodes.get('flow-title').textContent.startsWith('Code'),'Development team shows Code');
api.preset('Document operations');assert(nodes.get('flow-title').textContent.startsWith('Ingestion'));
api.preset('Service desk');assert(nodes.get('flow-title').textContent.startsWith('Task'));assert(!nodes.get('worker-sequence').innerHTML.includes('Finalize'));
// Appearance changes must not alter the workload or restart the tour.
const beforeTheme=Array.from(api.snapshot().draft.weights);
nodes.get('theme-dark').onclick();assert.equal(document.documentElement.attributes['data-theme'],'dark');
nodes.get('theme-light').onclick();assert.equal(document.documentElement.attributes['data-theme'],'light');
assert.deepEqual(Array.from(api.snapshot().draft.weights),beforeTheme);
assert(!html.includes('Before system rounding'));assert(!html.includes('Color palette'));
// Selecting an included archetype swaps its arrival share without duplicating archetypes.
nodes.get('reset').onclick();advance();
function choose(i,value){nodes.get('rows').listeners.change({target:{dataset:{workflow:String(i)},value:String(value),matches:s=>s==='[data-workflow]'}});}
choose(0,1);advance();assert.equal(api.snapshot().draft.weights[0],8);assert.equal(api.snapshot().draft.weights[1],50);assert.equal(api.snapshot().draft.valid,true);
assert(nodes.get('rows').innerHTML.indexOf('id="row-1"')<nodes.get('rows').innerHTML.indexOf('id="row-0"'));
// Custom retains the chosen row order; removing and replacing do not apply incomplete drafts.
const customRows=nodes.get('rows').innerHTML.match(/id="row-\d"/g);
api.preset('Enterprise');advance();api.preset('Custom');advance();assert.deepEqual(nodes.get('rows').innerHTML.match(/id="row-\d"/g),customRows);
nodes.get('reset').onclick();advance();choose(4,'remove');assert.equal(api.snapshot().draft.included[4],false);assert.equal(api.snapshot().draft.valid,false);
choose(0,4);assert.equal(api.snapshot().draft.included[0],false);assert.equal(api.snapshot().draft.included[4],true);assert.equal(api.snapshot().draft.weights[4],50);advance();assert.equal(api.snapshot().result.total,720);
nodes.get('reset').onclick();advance();
// The sub-head says the counts are extrapolated, and one server is singular.
target.value='100';target.listeners.input({target});advance();assert(/^100 working agents · 1 PowerEdge R770 server · 1 PowerEdge XE7740 \(Extrapolated from measurements\)$/.test(nodes.get('result-sentence').textContent),nodes.get('result-sentence').textContent);
nodes.get('reset').onclick();advance();assert(nodes.get('result-sentence').textContent.endsWith('R770 servers · 1 PowerEdge XE7740 (Extrapolated from measurements)'));
assert(!/\/\*__[A-Z_]+__\*\//.test(html));assert(!html.includes('of 286 measured'));assert(html.includes('workers per agent'));assert(html.includes('CPU sockets : GPUs'));
console.log('PASS: Panel A initializes; presets, saved Custom, incomplete drafts, transitions, zero and large fleets, reset, shared details, stage chips that advance, presets that show their largest agent, independent light/dark mode, workflow swaps/removal, and the extrapolation note with singular server counts. Source-level DOM harness; not a visual browser test.');
