/* Vela 0.8.3, Apache-2.0. Only renders PaperLab's verified, closed-bar output.
 * No market provider, Pine runtime, strategy execution or broker calls here. */
(() => {
  const instances = new Map();
  const TYPE = 'paperlab-verified-reclaims';
  const colors = {line:'#7db7e8',poc:'#a9b9cc',long:'#82cab6',short:'#e6b180'};
  const time = ts => new Date(ts).toLocaleString('en-US', {
    timeZone:'America/New_York',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'
  });
  function overlays(inst, id) {
    const series = [];
    for (const [field,title,color] of [['low','VAL',colors.line],['high','VAH',colors.line],['poc','POC',colors.poc]]) {
      const hasRows = inst.rows.some(r => Number.isFinite(r[field]));
      const fixed = inst.levels?.[field] ?? inst.levels?.[field==='low'?'value_low':field==='high'?'value_high':'poc'];
      const points = inst.rows.map(r => ({time:r.bar[0], value:hasRows ? (Number.isFinite(r[field])?r[field]:null) : (Number.isFinite(fixed)?fixed:null)}));
      if (points.some(p => p.value!==null)) series.push({id:id+'-'+field,title,paneId:'price',kind:'line',points,
        style:{color,width:1,lineStyle:field==='poc'?'dotted':'dashed'}});
    }
    // Vela's native backend paints plotshape-style drawing labels. Its neutral
    // MarkerSeries type is not painted by the 0.8.3 native geometry backend.
    const labels=inst.rows.filter(r=>r.signal).map(r=>({id:id+'-arrow-'+r.bar[0],paneId:'price',
      xloc:'bar_time',x:r.bar[0],y:r.signal>0?r.bar[3]:r.bar[2],yloc:r.signal>0?'belowbar':'abovebar',
      style:r.signal>0?'triangleup':'triangledown',color:r.signal>0?colors.long:colors.short,
      text:inst.profile==='developing'?'CVA':'PVA',textColor:'#e8edf3',
      size:'small',textAlign:'center',fontFamily:'default',
      tooltip:(r.signal>0?'Bullish':'Bearish')+' reclaim · '+time(r.bar[0])+' New York · indicator signal'}));
    return {series,labels};
  }
  if (window.Vela?.registerNativeIndicator) window.Vela.registerNativeIndicator({
    type:TYPE,title:'Verified value-area reclaim',overlay:true,paneHint:'price',legend:false,
    inputsSchema:()=>[],defaultInputs:()=>({slot:''}),
    create:() => {
      let ctx,slot;
      const emit=()=>{const inst=instances.get(slot);if(ctx&&inst)ctx.emit(overlays(inst,ctx.id));};
      return {start(c,inputs){ctx=c;slot=inputs.slot;emit();},onBars:emit,onViewport(){},
        setInputs(inputs){slot=inputs.slot;emit();},suspend(){},resume:emit,stop(){ctx=null;}};
    }
  });
  function destroy(id) {
    const inst=instances.get(id);
    if (!inst) return;
    instances.delete(id);
    inst.chart?.destroy();
  }
  function readout(inst,bar) {
    if (!bar) return;
    const n=x=>Number(x).toLocaleString('en-US',{minimumFractionDigits:inst.digits,maximumFractionDigits:inst.digits});
    const row=inst.rows.find(r=>r.bar[0]===bar.time);
    inst.readout.textContent=time(bar.time)+' NY · O '+n(bar.open)+' H '+n(bar.high)+' L '+n(bar.low)+' C '+n(bar.close)+
      ' · Vol '+Number(bar.volume||0).toLocaleString('en-US')+(row?.signal?' · '+(row.signal>0?'Bullish':'Bearish')+' reclaim':'');
  }
  function fit(inst) {inst.chart.renderer.set('autoScale',true);inst.chart.setVisibleRangePreset('ALL');}
  function action(inst,act) {
    const chart=inst.chart,range=chart.getVisibleRange();
    if (act==='fit') fit(inst);
    if (act==='zoom-in'||act==='zoom-out') {
      if (!range) return;
      const center=(range.from+range.to)/2,half=(range.to-range.from)/2*(act==='zoom-in'?.7:1.4);
      chart.setVisibleRange({from:center-half,to:center+half});
    }
    if (act==='previous'||act==='next') {
      const arrows=inst.rows.filter(r=>r.signal);
      if (!arrows.length) return;
      inst.arrowIndex=inst.arrowIndex<0?(act==='next'?0:arrows.length-1):
        (inst.arrowIndex+(act==='next'?1:-1)+arrows.length)%arrows.length;
      const row=arrows[inst.arrowIndex],step=inst.minutes*60000;
      chart.setVisibleRange({from:row.bar[0]-step*8,to:row.bar[0]+step*8});
      chart.renderer.setExternalCrosshair(row.bar[0],row.bar[4]);
      readout(inst,{time:row.bar[0],open:row.bar[1],high:row.bar[2],low:row.bar[3],close:row.bar[4],volume:row.bar[5]});
    }
    if (act==='fullscreen') {
      const promise=document.fullscreenElement?document.exitFullscreen():inst.wrap.requestFullscreen?.();
      promise?.catch(()=>{inst.readout.textContent='Fullscreen is unavailable in this browser. You can still pan and zoom.';});
    }
  }
  async function render(id, rows, opts={}) {
    const el=document.getElementById(id);
    if (!el) return null;
    if (!window.Vela?.Vela) throw Error('Vela did not load. Refresh the page to retry.');
    if (!rows?.length) {destroy(id);el.textContent='No completed candles for this session.';return null;}
    const signature=JSON.stringify([rows,opts.levels,opts.symbol,opts.minutes,opts.label]);
    let inst=instances.get(id);
    if (inst && inst.signature===signature && el.contains(inst.wrap)) return inst;
    // The host replaces its loading text before a replay request. Recreate only when detached;
    // live polling keeps the mounted chart and the user's viewport, even as bars change.
    if (inst && !el.contains(inst.wrap)) {destroy(id);inst=null;}
    const bars=rows.map(r=>({time:r.bar[0],open:r.bar[1],high:r.bar[2],low:r.bar[3],close:r.bar[4],volume:r.bar[5]}));
    if (!inst) {
      el.classList.remove('empty');el.replaceChildren();
      const wrap=document.createElement('div');wrap.className='vela-chart';
      const head=document.createElement('div');head.className='vela-heading';
      const label=document.createElement('strong');label.textContent=opts.label||opts.symbol||'Closed-bar replay';
      const attribution=document.createElement('a');attribution.href='https://velacharts.dev';attribution.target='_blank';attribution.rel='noopener';attribution.textContent='Vela™';
      head.append(label,attribution);
      const tools=document.createElement('div');tools.className='vela-controls';
      for (const [act,title,aria] of [['fit','Fit session','Fit all loaded candles'],['zoom-in','+','Zoom in'],['zoom-out','−','Zoom out'],
        ['previous','Previous signal','Jump to previous reclaim arrow'],['next','Next signal','Jump to next reclaim arrow'],['fullscreen','Fullscreen','Open chart fullscreen']]) {
        const button=document.createElement('button');button.type='button';button.dataset.act=act;button.textContent=title;button.setAttribute('aria-label',aria);tools.append(button);
      }
      const surface=document.createElement('div');surface.className='vela-surface';surface.setAttribute('aria-label',(opts.symbol||'Market')+' candlesticks, volume, verified reclaim arrows and value-area levels');
      const info=document.createElement('div');info.className='vela-readout';
      const key=document.createElement('p');key.className='vela-key';key.textContent='VAL / VAH · blue dashed   POC · gray dotted   ▲ bullish   ▼ bearish · New York time';
      wrap.append(head,tools,surface,info,key);el.append(wrap);
      inst={wrap,surface,tools,readout:info,rows,levels:opts.levels,digits:opts.digits??2,minutes:opts.minutes??15,profile:opts.profile,
        arrowIndex:-1,signature,label,chart:null};instances.set(id,inst);
      const chart=new window.Vela.Vela(surface,{data:bars,timeframe:String(inst.minutes),symbol:opts.symbol,
        height:420,live:false,volume:true,drawings:false,visibleRange:'ALL',upColor:colors.long,downColor:colors.short,
        animations:window.matchMedia('(prefers-reduced-motion: reduce)').matches?false:{intro:false,zoom:110,pan:90},
        theme:{background:'#172437',textColor:'#a9b9cc',gridColor:'#26384d',borderColor:'#334458',
          upColor:colors.long,downColor:colors.short,fontFamily:'JetBrains Mono, monospace'}});
      inst.chart=chart;
      chart.renderer.set({timezone:'America/New_York',currentPriceLine:false});
      chart.renderer.onCrosshairMove(event=>readout(inst,event.bar));
      tools.addEventListener('click',e=>{const button=e.target.closest('button');if(button)action(inst,button.dataset.act);});
      await chart.ready();
      if (instances.get(id)!==inst) return null;
      chart.addNativeIndicator(TYPE,{inputs:{slot:id}});
      fit(inst);
    } else {
      const range=inst.chart.getVisibleRange();
      inst.rows=rows;inst.levels=opts.levels;inst.profile=opts.profile;inst.signature=signature;inst.label.textContent=opts.label||opts.symbol;
      inst.minutes=opts.minutes??inst.minutes;
      await inst.chart.setMarket({data:bars,timeframe:String(inst.minutes),symbol:opts.symbol});
      if (instances.get(id)!==inst) return null;
      if (range) inst.chart.setVisibleRange(range);
    }
    inst.wrap.dataset.candles=String(rows.length);
    inst.wrap.dataset.signals=String(rows.filter(r=>r.signal).length);
    inst.wrap.dataset.symbol=opts.symbol||'';
    inst.wrap.dataset.minutes=String(inst.minutes);
    for(const button of inst.tools.querySelectorAll('[data-act="previous"],[data-act="next"]'))button.disabled=!rows.some(r=>r.signal);
    readout(inst,bars.at(-1));
    return inst;
  }
  window.PaperLabVela={render,destroy,instances,overlays};
  window.addEventListener('pagehide',()=>{for(const id of [...instances.keys()])destroy(id);});
})();
