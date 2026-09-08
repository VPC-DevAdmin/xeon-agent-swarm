const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
(async()=>{
  const output=fs.mkdtempSync(path.join(os.tmpdir(),'conference-demo-'));
 console.log('Screenshots: '+output);
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage();
  const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.context().setOffline(true);
  await page.goto('file://'+path.join(__dirname,'steady-state.html'));
  await page.evaluate(()=>window.__steady.pause());
  await page.evaluate(()=>document.fonts.ready);
  assert.deepEqual(errors,[],'Browser errors');
  assert.deepEqual(requests,[],'Page must work without network requests');
  assert.ok(await page.locator('img').evaluateAll(es=>es.every(e=>e.complete&&e.naturalWidth>0)),'Embedded images');
  assert.equal(await page.locator('.r770-fleet img[data-asset="r770"]').count(),3,'Three execution systems');
  assert.equal(await page.locator('img[data-asset="xe7740"]').count(),1,'One model-serving system');
  for(const [width,height] of [[1920,1080],[1366,768],[1200,800]]){
   await page.setViewportSize({width,height});
   for(let i=0;i<5;i++){
    await page.evaluate(i=>{window.__steady.seek(400);window.__steady.focus(i);},i);
    await page.screenshot({path:path.join(output,`demo-${width}-${i}.png`)});
    const overflow=await page.locator('.glass,.lane,.focus,.cycle-node,.worker-sequence,footer').evaluateAll(es=>es.filter(e=>e.scrollHeight>e.clientHeight+2||e.scrollWidth>e.clientWidth+2).map(e=>({class:e.className,h:e.clientHeight,sh:e.scrollHeight,w:e.clientWidth,sw:e.scrollWidth})));
    assert.deepEqual(overflow,[],'Panel overflow at '+width+', archetype '+i);
   }
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight),'No page scrolling');
  }
  const dataCheck=await page.evaluate(()=>{
   for(let t=0;t<900;t+=3){
    window.__steady.seek(t);const s=window.__steady.snapshot();
    const units=DATA.units.filter(u=>u[0]===s.plateauIndex&&u[1]>=0);
    const active=units.filter(u=>u[2]<=s.absoluteTime&&(u[3]==null||u[3]>s.absoluteTime));
    if(s.resident!==active.length||s.counts.reduce((a,b)=>a+b,0)!==active.length)return 'Resident count mismatch at '+t;
    if(s.completion&&!units.some(u=>JSON.stringify(u)===JSON.stringify(s.completion)&&u[3]!=null))return 'Unrecorded completion at '+t;
   }
   return 'Recorded counts and completion provenance pass at 300 timestamps';
  });
  assert.ok(dataCheck.startsWith('Recorded'),dataCheck);
  const steadyCheck=await page.evaluate(()=>{
   const s=window.__steady.snapshot();
   const times=new Set([0,s.end-s.start-0.000001]);
   DATA.units.filter(u=>u[0]===s.plateauIndex).forEach(u=>[u[2],u[3]].forEach(t=>{
    if(t!=null&&t>s.start&&t<s.end){times.add(t-s.start);times.add(t-s.start-0.000001);}
   }));
   for(let t=0;t<s.end-s.start;t+=.5)times.add(t);
   for(const t of times){
    window.__steady.seek(t);const r=window.__steady.snapshot();
    if(r.counts.some(n=>n<=0)||!(r.cpu>0)||!(r.memory>0))return 'Zero or missing reading at '+t;
   }
   return 'No zero readings at '+times.size+' event boundaries and steady-state samples';
  });
  assert.ok(steadyCheck.startsWith('No zero'),steadyCheck);
  console.log(steadyCheck);
  for(const i of [1.36,1.72,3.36,3.72,4.36,4.72]){
   await page.evaluate(i=>window.__steady.focus(i),i);
   assert.equal(await page.locator('.focus').evaluate(e=>e.scrollHeight>e.clientHeight+2||e.scrollWidth>e.clientWidth+2),false,'Worker sequence overflow at '+i);
  }
  await page.evaluate(()=>window.__steady.focus(3.72));
  assert.ok(!(await page.locator('#cycle').innerText()).includes('Retrieve definitions'),'Third analyst worker skips retrieval');
  await page.setViewportSize({width:1920,height:1080});
  await page.evaluate(()=>window.__steady.focus(4));
  const topics=await page.locator('[data-topic]').evaluateAll(es=>[...new Set(es.map(e=>e.dataset.topic))]);
  for(const topic of topics){
   await page.locator(`[data-topic="${topic}"]`).first().click();
   assert.ok(await page.locator('#topic-panel').isVisible(),'Context panel for '+topic);
   assert.ok((await page.locator('#topic-title').innerText()).length>3,'Context title for '+topic);
   assert.ok((await page.locator('#topic-body').innerText()).length>30,'Context explanation for '+topic);
   assert.equal((await page.evaluate(()=>window.__steady.snapshot())).playing,false,'Pause while reading');
   await page.keyboard.press('Escape');
  }
  await page.locator('[data-topic="agent:3"]').focus();
  await page.keyboard.press('Enter');
  assert.ok((await page.locator('#topic-title').innerText()).startsWith('Analyst'),'Keyboard access');
  await page.screenshot({path:path.join(output,'agent-details.png')});
  await page.locator('#show-reference').click();
  assert.ok(await page.locator('#reference-panel').isVisible(),'Source reference from topic');
  await page.keyboard.press('Escape');
  await page.evaluate(()=>window.__steady.play());
  await page.locator('[data-topic="memory"]').click();
  assert.equal((await page.evaluate(()=>window.__steady.snapshot())).playing,false);
  await page.keyboard.press('Escape');
  assert.equal((await page.evaluate(()=>window.__steady.snapshot())).playing,true,'Resume after closing a topic');
  await page.evaluate(()=>window.__steady.pause());
  await page.locator('#fullscreen').click();
  await page.waitForFunction(()=>!!document.fullscreenElement);
  await page.evaluate(()=>document.exitFullscreen());
  await page.locator('#details').click();
  assert.ok(await page.locator('#detail-dialog').evaluate(e=>e.open));
  await page.screenshot({path:path.join(output,'details.png')});
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#detail-dialog').evaluate(e=>e.open),false);
  await page.evaluate(()=>{const s=window.__steady.snapshot();window.__steady.seek(s.end-s.start-.1);});
  await page.locator('#play').click();
  await page.waitForTimeout(600);
  assert.ok((await page.evaluate(()=>window.__steady.snapshot())).loops>0,'Loop boundary');
  await page.evaluate(()=>window.__steady.pause());
  const t=await page.evaluate(()=>window.__steady.time());
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(()=>window.__steady.time()),t,'Pause freezes replay');
  await page.locator('#restart').click();
  assert.equal(await page.evaluate(()=>window.__steady.time()),0,'Restart');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>window.__steady.snapshot().motion===false);
  assert.equal((await page.evaluate(()=>window.__steady.snapshot())).motion,false,'Reduced motion');
  await page.reload();
  assert.equal((await page.evaluate(()=>window.__steady.snapshot())).playing,false,'Reduced-motion startup');
  assert.deepEqual(errors,[]);
  console.log(dataCheck);console.log('PASS: offline loading, images, controls, loop boundary, reduced motion. Screenshots: '+output);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
