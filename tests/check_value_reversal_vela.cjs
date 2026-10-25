const fs=require('fs');
const {openBrowser}=require('../../videos/paperlab-motion/node_modules/@remotion/renderer');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{const b=await openBrowser('chrome',{logLevel:'error'});try{const p=await b.newPage({context:()=>null,logLevel:'error',indent:false,pageIndex:0,onBrowserLog:null,onLog:()=>{}});const errors=[];p.on('pageerror',e=>errors.push(String(e)));const base=process.env.PREVIEW_URL||'http://127.0.0.1:8775/public/value-reversal';
async function wait(fn){for(let i=0;i<160;i++){if(await p.evaluate(fn))return;await delay(250)}throw Error('Timed out waiting for charts')}
for(const width of [1280,390]){
 await p.setViewport({width,height:1000,deviceScaleFactor:1});await p.goto({url:base+'?velaVerify='+width,timeout:60000});
 await wait(()=>document.querySelector('#crypto-replay .vela-chart[data-candles]')&&document.querySelector('#replay-chart .vela-chart[data-candles]'));
 const initial=await p.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,qqq:document.querySelector('#replay-chart .vela-chart').dataset.candles,crypto:document.querySelector('#crypto-replay .vela-chart').dataset.candles,attribution:[...document.querySelectorAll('.vela-heading a')].every(a=>a.href==='https://velacharts.dev/'),oldLibrary:typeof LightweightCharts}));
 if(initial.overflow||!initial.attribution||initial.oldLibrary!=='undefined')throw Error(JSON.stringify(initial));
 await p.evaluate(()=>document.querySelector('[data-supplied-key="ETHUSDT-30m-previous-10bars"]').click());
 await wait(()=>document.querySelector('#crypto-replay .vela-chart')?.dataset.symbol==='ETHUSDT'&&document.querySelector('#crypto-replay .vela-chart')?.dataset.minutes==='30');
 const checks=await p.evaluate(()=>{const i=PaperLabVela.instances.get('crypto-replay'),model=PaperLabVela.overlays(i,'verify'),marks=model.labels;return {candles:i.rows.length,markers:marks.length,expected:i.rows.filter(r=>r.signal).length,markerTimesMatch:marks.every(m=>i.rows.some(r=>r.bar[0]===m.x&&r.signal&&(r.signal>0?m.style==='triangleup':m.style==='triangledown'))),linesMatch:model.series.filter(s=>s.kind==='line').every(s=>s.points.every((point,j)=>point.value===(Number.isFinite(i.rows[j][s.title==='VAL'?'low':s.title==='VAH'?'high':'poc'])?i.rows[j][s.title==='VAL'?'low':s.title==='VAH'?'high':'poc']:null))),scene:i.chart.inspect().totals,timezone:i.chart.renderer.get('timezone'),range:i.chart.getVisibleRange()}});
 if(checks.candles!==48||checks.markers!==checks.expected||!checks.markerTimesMatch||!checks.linesMatch||checks.scene.series!==3||checks.scene.labels!==checks.expected||checks.timezone!=='America/New_York')throw Error(JSON.stringify(checks));
 await p.evaluate(()=>document.querySelector('#crypto-replay [data-act="zoom-in"]').click());await delay(800);
 const zoom=await p.evaluate(()=>PaperLabVela.instances.get('crypto-replay').chart.getVisibleRange());
 if(zoom.to-zoom.from>=checks.range.to-checks.range.from)throw Error('Zoom did not narrow range');
 await p.evaluate(()=>document.querySelector('#crypto-replay [data-act="next"]').click());await delay(500);
 const jump=await p.evaluate(()=>document.querySelector('#crypto-replay .vela-readout').textContent);
 if(!jump.includes('reclaim'))throw Error('Signal navigation failed');
 await p.evaluate(()=>{document.getElementById('supplied-development').click();document.querySelector('[data-supplied-key="QQQ-15m-previous-10bars"]').click()});
 await wait(()=>document.querySelector('#replay-chart .vela-chart[data-candles]')&&document.getElementById('replay-caption').textContent.includes('2023'));
 const live=await p.evaluate(async()=>{const q=PaperLabVela.instances.get('replay-chart'),opts={symbol:'QQQ',minutes:15,label:'Live update test'};await PaperLabVela.render('live-focus-chart',q.rows,opts);const before=PaperLabVela.instances.get('live-focus-chart');before.chart.setVisibleRange({from:q.rows[3].bar[0],to:q.rows[15].bar[0]});const range=before.chart.getVisibleRange();await PaperLabVela.render('live-focus-chart',q.rows,opts);const same=before===PaperLabVela.instances.get('live-focus-chart');const rows=q.rows.map(r=>({...r,bar:[...r.bar]}));rows.at(-1).bar[5]+=1;await PaperLabVela.render('live-focus-chart',rows,opts);const after=PaperLabVela.instances.get('live-focus-chart');return {same,noStack:document.querySelectorAll('#live-focus-chart .vela-chart').length===1,afterChangedSame:after===before,rangeBefore:range,rangeAfter:after.chart.getVisibleRange(),series:after.chart.inspect().totals.series}});
 if(!live.same||!live.noStack||!live.afterChangedSame||live.series!==3||JSON.stringify(live.rangeBefore)!==JSON.stringify(live.rangeAfter))throw Error(JSON.stringify(live));
 await p.evaluate(()=>document.getElementById('crypto-replay').scrollIntoView({block:'center'}));await delay(500);
 if(width===1280){
  const bounds=await p.evaluate(()=>{const r=PaperLabVela.instances.get('crypto-replay').surface.getBoundingClientRect();return {x:r.left+r.width*.4,y:r.top+r.height*.4}});
  const beforeWheel=await p.evaluate(()=>PaperLabVela.instances.get('crypto-replay').chart.getVisibleRange());
  await p._client().send('Input.dispatchMouseEvent',{type:'mouseWheel',x:bounds.x,y:bounds.y,deltaX:0,deltaY:-140});await delay(700);
  const afterWheel=await p.evaluate(()=>PaperLabVela.instances.get('crypto-replay').chart.getVisibleRange());
  if(afterWheel.to-afterWheel.from>=beforeWheel.to-beforeWheel.from)throw Error('Mouse wheel zoom failed');
  const button=await p.evaluate(()=>{const r=document.querySelector('#crypto-replay [data-act="fullscreen"]').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}});
  await p._client().send('Input.dispatchMouseEvent',{type:'mousePressed',...button,button:'left',clickCount:1});
  await p._client().send('Input.dispatchMouseEvent',{type:'mouseReleased',...button,button:'left',clickCount:1});await delay(700);
  const full=await p.evaluate(()=>({active:!!document.fullscreenElement,height:PaperLabVela.instances.get('crypto-replay').surface.getBoundingClientRect().height}));
  if(!full.active||full.height<600)throw Error('Fullscreen did not expand plot: '+JSON.stringify(full));
  await p.evaluate(()=>document.exitFullscreen());await delay(500);
 }
 const shot=await p._client().send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync('data/vela-preview-'+width+'.png',Buffer.from(shot.value.data,'base64'));
 console.log(JSON.stringify({width,initial,checks,jump,live}));
}
if(errors.length)throw Error(errors.join('\n'));console.log('Vela chart data, arrows, levels, zoom, navigation, live viewport and mobile checks passed');
}finally{await b.close({silent:true})}})().catch(e=>{console.error(e);process.exit(1)});
