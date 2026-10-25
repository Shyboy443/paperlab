const fs=require('fs'),path=require('path');const {openBrowser}=require('../../videos/paperlab-motion/node_modules/@remotion/renderer');const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{const b=await openBrowser('chrome',{logLevel:'error'});try{
 const p=await b.newPage({context:()=>null,logLevel:'error',indent:false,pageIndex:0,onBrowserLog:null,onLog:()=>{}});
 await p.evaluateOnNewDocument(()=>{window.__errors=[];window.addEventListener('error',e=>__errors.push(e.message));window.addEventListener('unhandledrejection',e=>__errors.push(String(e.reason)));window.__requests=[];});
 if(process.env.LOCAL_ASSETS==='1'){
  p._client().on('Fetch.requestPaused',async e=>{const u=new URL(e.request.url);let file=u.pathname.startsWith('/public/competition')?'app/dashboard/public.html':u.pathname.startsWith('/static/')?'app/dashboard/'+u.pathname.slice(8):null;
   if(file&&fs.existsSync(file)){const mime=file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'application/octet-stream';await p._client().send('Fetch.fulfillRequest',{requestId:e.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:mime}],body:fs.readFileSync(file).toString('base64')})}else await p._client().send('Fetch.continueRequest',{requestId:e.requestId});});
  await p._client().send('Fetch.enable',{patterns:[{urlPattern:'https://paperlab-production-919c.up.railway.app/public/competition*'},{urlPattern:'https://paperlab-production-919c.up.railway.app/static/*'}]});
 }
 for(const width of [1440,390]){
  await p.setViewport({width,height:1000,deviceScaleFactor:1});await p.goto({url:'https://paperlab-production-919c.up.railway.app/public/competition/bots?p=v6',timeout:60000});await delay(1500);
  await p.evaluate(async()=>{Arena.prog='v6';Arena.view='bots';await Arena.openBot('V6.2-DOGE-1H');Arena.tf='1h';Arena.render()});
  for(let i=0;i<120;i++){if(await p.evaluate(()=>!!Arena.liveChartHost()?.chartRefs))break;await delay(150)}
  await delay(400);
  const r=await p.evaluate(()=>{const host=Arena.liveChartHost(),r=host?.chartRefs;return {native:r?.chart.renderer.name,bars:r?.inst.bars.length,canvases:host?.querySelectorAll('canvas').length,boxes:host?.positionBoxes.rects.length,marks:host?.positionBoxes.marks.length,errors:__errors,lwc:typeof LightweightCharts,requests:performance.getEntriesByType('resource').filter(r=>/lightweight|tradingview/i.test(r.name)).map(r=>r.name),text:host?.textContent,stream:Stream.status}});
  if(r.native!=='native'||r.bars!==300||!r.canvases||!r.boxes||!r.marks||r.errors.length||r.lwc!=='undefined'||r.requests.length)throw Error(JSON.stringify(r));
  await p.evaluate(()=>Arena.liveChartHost().scrollIntoView({block:'center'}));await delay(100);const shot=await p._client().send('Page.captureScreenshot',{format:'png'});fs.writeFileSync('data/arena-vela-'+width+'.png',Buffer.from(shot.value.data,'base64'));
  const kept=await p.evaluate(async()=>{const host=Arena.liveChartHost(),r=host.chartRefs,bar=r.inst.bars;r.chart.setVisibleRange({from:bar[160].time,to:bar[190].time});const before=r.chart.getVisibleRange();await Arena.updateCandles(host);return {same:r===host.chartRefs,before,after:r.chart.getVisibleRange()}});
  if(!kept.same||JSON.stringify(kept.before)!==JSON.stringify(kept.after))throw Error('Viewport '+JSON.stringify(kept));
  await p.evaluate(()=>Arena.showTrade(Arena.bot.trades[0]));for(let i=0;i<120;i++){if(await p.evaluate(()=>!!Arena.liveChartHost()?.positionBoxes?.pinned))break;await delay(150)}await delay(150);
  const focus=await p.evaluate(()=>{const h=Arena.liveChartHost();return {pinned:!!h?.positionBoxes?.pinned,drawn:h?.positionBoxes?.rects.length,range:h?.chartRefs.chart.getVisibleRange(),errors:__errors}});if(!focus.pinned||!focus.drawn||focus.errors.length)throw Error(JSON.stringify(focus));
  const hover=await p.evaluate(()=>{const host=Arena.liveChartHost(),pb=host.positionBoxes,r=pb.rects[0],bounds=host.velaInstance.surface.getBoundingClientRect();pb.pinned=null;return {x:bounds.left+(r.x0+r.x1)/2,y:bounds.top+(r.yE+r.yT)/2}});
  await p._client().send('Input.dispatchMouseEvent',{type:'mouseMoved',x:hover.x,y:hover.y});await delay(150);
  if(!await p.evaluate(()=>!!Arena.liveChartHost().positionBoxes.hover))throw Error('Native trade hover failed');
  // A browser-only fixture exercises all three targets; no saved trade or rule is edited.
  await p.evaluate(()=>{const host=Arena.liveChartHost(),pb=host.positionBoxes,b={...pb.boxes[0]};const sign=b.side==='long'?1:-1;
   b.tps=[1,2,3].map(n=>b.entry*(1+sign*n*.01));b.target=b.tps[2];b.shares=[.25,.5,.25];pb.boxes=[b];pb.pinned=b;pb.requestUpdate();});await delay(200);
  const ladder=await p.evaluate(()=>{const host=Arena.liveChartHost(),pb=host.positionBoxes;return {rungs:pb.rects[0].yTps.length,nativeSeries:host.chartRefs.chart.inspect().totals.series,labels:pb.placed.length,errors:__errors}});
  if(ladder.rungs!==3||ladder.nativeSeries!==5||ladder.labels<2||ladder.errors.length)throw Error('TP ladder '+JSON.stringify(ladder));
  const extras=await p.evaluate(async()=>{
   const d=Arena.data.v6, growth=Arena.growthCard(d);document.getElementById('home-grid').append(growth);
   const equity=h('div',{id:'test-equity',style:'height:300px'}),toggles=h('div',{id:'curve-toggles'});document.getElementById('home-grid').append(equity,toggles);
   Charts.init(equity);Charts.setCurves({TOTAL:[[1791200000000,20],[1791200600000,21],[1791201200000,22]],S01:[[1791200000000,10],[1791200600000,11],[1791201200000,12]]});
   return {growth:!!growth};
  });
  for(let i=0;i<100;i++){if(await p.evaluate(()=>!!document.querySelector('#arena-growth .arena-chart')?.velaInstance?.indicator&&!!document.getElementById('test-equity')?.velaInstance?.indicator))break;await delay(100)}
  const lines=await p.evaluate(async()=>{const growth=document.querySelector('#arena-growth .arena-chart').velaInstance,eq=document.getElementById('test-equity').velaInstance;
   const countBefore=eq.chart.inspect().totals.series;document.querySelector('#curve-toggles .tg').click();await new Promise(r=>setTimeout(r,150));
   return {growthNative:growth.chart.renderer.name,growthSeries:growth.chart.inspect().totals.series,equityNative:eq.chart.renderer.name,countBefore,countAfter:eq.chart.inspect().totals.series,errors:__errors};});
  if(lines.growthNative!=='native'||lines.equityNative!=='native'||lines.growthSeries<1||lines.countAfter===lines.countBefore||lines.errors.length)throw Error(JSON.stringify(lines));
  await p.evaluate(()=>{Arena.tf='5m';Arena.render()});for(let i=0;i<120;i++){if(await p.evaluate(()=>Arena.liveChartHost()?.chartRefs.tf==='5m'))break;await delay(100)}
  const five=await p.evaluate(()=>({native:Arena.liveChartHost()?.chartRefs.chart.renderer.name,bars:Arena.liveChartHost()?.chartRefs.inst.bars.length}));
  if(five.native!=='native'||five.bars!==300)throw Error('5m candles '+JSON.stringify(five));
  await p.evaluate(()=>{Arena.view='markets';Arena.focus=null;Arena.coin='ARB';Arena.tf='15m';Arena.render()});for(let i=0;i<120;i++){if(await p.evaluate(()=>Arena.liveChartHost()?.chartRefs.symbol==='ARBUSDT'))break;await delay(100)}
  const market=await p.evaluate(()=>({native:Arena.liveChartHost()?.chartRefs.chart.renderer.name,bars:Arena.liveChartHost()?.chartRefs.inst.bars.length,errors:__errors}));
  if(market.native!=='native'||market.bars!==300||market.errors.length)throw Error('Markets chart '+JSON.stringify(market));
  console.log(JSON.stringify({width,r,kept,focus,ladder,lines,five,market}));
 }
}finally{await b.close({silent:true})}})().catch(e=>{console.error(e);process.exit(1)});
