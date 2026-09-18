// Browser regression for signals arriving between the scanner's one-second ticks.
// Run against an isolated frontend with a fixture scanner; never use a live URL.
// Requires @playwright/test (NODE_PATH may point to the browser-tool installation).
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require('@playwright/test');
const url = process.env.SIGNAL_TEST_URL;
const secret = process.env.SIGNAL_TEST_SECRET;
if (!url || !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname) || !secret) {
  throw new Error('An isolated loopback SIGNAL_TEST_URL and SIGNAL_TEST_SECRET are required');
}
const base = Date.parse('2026-09-11T15:59:35Z');
const boundary = Date.parse('2026-09-11T16:00:00Z');
const iso = n => new Date(n).toISOString();
function signals(now, includeForming = true) {
  return { data: {
    as_of: iso(now),
    confirmed: [{signal_id:'fixture-confirmed', instrument_id:'EUR_USD',mode:'MACRO',timeframe:'H1',direction:'BUY',
      source_provider:'IG_DEMO',source_bar_start:'2026-09-11T14:00:00Z',source_bar_end:'2026-09-11T15:00:00Z',
      confirmed_at:'2026-09-11T15:00:00Z',visible_until:iso(boundary)}],
    forming: includeForming ? [{instrument_id:'GBP_USD',instrument:'GBP_USD',authority:'IG_DEMO',mode:'MACRO',timeframe:'H1',direction:'SELL',
      source_bar_start:'2026-09-11T15:00:00Z',source_bar_end:iso(boundary),source_as_of:'2026-09-11T15:55:00Z',formed_at:'2026-09-11T15:55:00Z'}] : [],
    forming_candidates: [{instrument_id:'GBP_USD',authority:'IG_DEMO',mode:'MACRO',timeframe:'H1',state:'READY',
      source_as_of:'2026-09-11T15:55:00Z',source_bar_end:iso(boundary),max_age_seconds:600}],
  }};
}
(async () => {
 const browser = await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
 const checks=[];
 try {
  for (const scenario of ['refresh-and-expiry','failure','future-response','stale-response','stale-source','clock-behind','clock-ahead','missing-clock']) {
   const context=await browser.newContext();
   const skew=scenario==='clock-behind'?-3600000:scenario==='clock-ahead'?3600000:0;
   try {
    const payload=Buffer.from(JSON.stringify({sub:'single-client',exp:Math.floor(Date.now()/1000)+600})).toString('base64url');
    const token=payload+'.'+crypto.createHmac('sha256',secret).update(payload).digest('base64url');
    await context.addCookies([{name:'spect8_session',value:token,url}]);
    const page=await context.newPage();const pending=[],errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.clock.install({time:new Date(base-1000)});
    await page.clock.pauseAt(new Date(base));
    await page.route('**/api/signals/current',route=>{pending.push(route);});
    await page.goto(url+'/dashboard',{waitUntil:'domcontentloaded'});
    await page.getByText('Markets Monitored',{exact:true}).waitFor({state:'attached'});
    await page.clock.runFor(100);
    if(skew) await page.clock.setSystemTime(new Date(base+100+skew));
    async function nextRequest(advance=0) {
      if(advance) await page.clock.runFor(advance);
      for(let i=0;i<100 && !pending.length;i++) await new Promise(r=>setTimeout(r,20));
      assert.ok(pending.length,'Expected scheduled signal request');
      return pending.shift();
    }
    async function settle() { await new Promise(r=>setTimeout(r,150)); }
    async function state() {
      return page.evaluate(()=>({
        cards:[...document.querySelectorAll('.signal-card')].map(e=>e.classList.contains('forming')?'forming':'confirmed').sort(),
        confirmed:document.querySelector('[data-signal-summary="confirmed"] b').textContent,
        forming:document.querySelector('[data-signal-summary="forming"] b').textContent,
      }));
    }
    async function deliver(route, includeForming=true, offset=0) {
      // This response is newer than the previous tick but not newer than receipt.
      await page.clock.runFor(250);
      const now=(await page.evaluate(()=>Date.now()))-skew;
      const body=signals(now+offset,includeForming);
      if(scenario==='stale-source') body.data.forming[0].source_as_of='2026-09-11T15:40:00Z';
      await route.fulfill({status:200,contentType:'application/json',headers:scenario==='missing-clock'?{}:{'X-Spect8-Response-Time':iso(now)},body:JSON.stringify(body)});
      await settle();
      return now;
    }
    const first=await nextRequest();await deliver(first);
    const expected=scenario==='missing-clock'?{cards:[],confirmed:'—',forming:'0'}:scenario==='stale-source'?{cards:['confirmed'],confirmed:'1',forming:'0'}:{cards:['confirmed','forming'],confirmed:'1',forming:'1'};
    assert.deepEqual(await state(),expected,scenario+': initial response between ticks');
    if(['refresh-and-expiry','clock-behind','clock-ahead'].includes(scenario)) {
      for(const include of [true,false,true]) {
        const request=await nextRequest(5000);await deliver(request,include);
        const wanted=include?expected:{cards:['confirmed'],confirmed:'1',forming:'0'};
        assert.deepEqual(await state(),wanted,'Refresh must render immediately between ticks');
        await page.clock.runFor(1000);await settle();
        assert.deepEqual(await state(),wanted,'No drop after one-second clock tick');
      }
      const now=(await page.evaluate(()=>Date.now()))-skew;
      await page.clock.runFor(boundary-now+1000);await settle();
      assert.deepEqual(await state(),{cards:[],confirmed:'0',forming:'0'},'Real bar boundary expires both signals without a response');
    } else if(scenario==='failure') {
      const request=await nextRequest(5000);
      await request.fulfill({status:503,contentType:'application/json',body:'{}'});await settle();
      assert.deepEqual(await state(),{cards:[],confirmed:'—',forming:'0'},'Request failure must remain unavailable');
      const retry=await nextRequest(5000);await deliver(retry);
      assert.deepEqual(await state(),expected,'Successful retry recovers immediately');
    } else if(scenario==='future-response'||scenario==='stale-response') {
      const request=await nextRequest(5000);await deliver(request,true,scenario==='future-response'?60000:-31000);
      assert.deepEqual(await state(),{cards:[],confirmed:'—',forming:'0'},'Invalid response time remains unavailable');
    }
    assert.deepEqual(errors,[],'No browser exceptions');
    checks.push({scenario,passed:true});
   } finally {await context.close();}
  }
  console.log(JSON.stringify({checks,passed:checks.length}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
