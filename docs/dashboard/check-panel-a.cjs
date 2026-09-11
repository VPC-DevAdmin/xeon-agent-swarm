const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'layout-studies.html'),'utf8');
const nodes=new Map(),listeners=new Map(),timers=[];let now=0;
class Element{
  constructor(id){this.id=id;this.style={};this.dataset={};this.value='';this.hidden=false;this.open=false;this.classList={add(){},toggle(){}};this.listeners={};}
  set innerHTML(value){this.html=value;for(const match of value.matchAll(/\bid="([^"]+)"/g))if(!nodes.has(match[1]))nodes.set(match[1],new Element(match[1]));}
  get innerHTML(){return this.html||'';}
  setAttribute(){} addEventListener(name,fn){this.listeners[name]=fn;} setCustomValidity(){} focus(){} replaceChildren(){this.innerHTML='';} showModal(){this.open=true;} close(){this.open=false;}
}
for(const m of html.matchAll(/\bid="([^"]+)"/g))nodes.set(m[1],new Element(m[1]));
const exchange=new Element('exchange');
const document={hidden:false,activeElement:null,getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>[],querySelector:s=>s==='.exchange'?exchange:null,addEventListener:(n,f)=>listeners.set(n,f)};
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
assert(!/\/\*__[A-Z_]+__\*\//.test(html));assert(!html.includes('of 286 measured'));assert(html.includes('workers per agent'));assert(html.includes('CPU sockets : GPUs'));
console.log('PASS: Panel A initializes; presets, saved Custom, incomplete drafts, transitions, zero and large fleets, reset, and shared details. Source-level DOM harness; not a visual browser test.');
