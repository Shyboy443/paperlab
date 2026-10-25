/* Native Vela renderer for dashboard candles, trade overlays and equity curves.
   Uses only the existing PaperLab read-only data; no exchange provider or scripting engine. */
'use strict';
const DashboardVela = (() => {
  const instances = new Map();
  const TYPE = 'paperlab-dashboard-overlay', STUDY_TYPE = 'paperlab-dashboard-rsi';
  let sequence = 0;
  for (const type of [TYPE, STUDY_TYPE]) if (window.Vela?.registerRendererLayer) Vela.registerRendererLayer({
    id: type, placement: 'above-data', repaintOnCursor: true,
    create() {
      let canvas, ctx;
      return {
        mount(c) { canvas = c; ctx = c.getContext('2d'); },
        render(args) {
          if (!ctx) return;
          ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height);
          const pb = args.data;
          if (!pb) return;
          const dpr = args.coords.dpr;
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.save();
          ctx.beginPath(); ctx.rect(0, args.bounds.top, args.coords.width, args.bounds.height); ctx.clip();
          pb.paint(ctx, args); ctx.restore();
        },
        destroy() { canvas = ctx = null; },
      };
    },
  });
  for (const type of [TYPE, STUDY_TYPE]) if (window.Vela?.registerNativeIndicator) Vela.registerNativeIndicator({
    type, title: type === STUDY_TYPE ? 'RSI (14)' : 'PaperLab', overlay: type !== STUDY_TYPE, paneHint: type === STUDY_TYPE ? 'new' : 'price', legend: false,
    inputsSchema: () => [], defaultInputs: () => ({slot: '', revision: 0}),
    create() {
      let ctx, slot;
      const emit = () => {
        const owner = instances.get(slot), inst = type === STUDY_TYPE ? owner?.study : owner;
        if (!inst || !ctx) return;
        const series = (inst.models || []).map((s, i) => ({
          id: ctx.id + '-' + i, paneId: 'price', kind: 'line', title: s.title || '',
          points: s.points, style: {color: s.color, width: s.width || 1, lineStyle: s.dashed ? 'dashed' : 'solid'},
        }));
        // Transparent risk bounds contribute to native autoscaling without another visible line.
        if (inst.pb) for (const [i, b] of inst.pb.boxes.entries()) {
          for (const [j, value] of [b.entry, b.stop, ...PositionBoxes.tps(b)].entries()) if (Number.isFinite(value)) {
            series.push({id: ctx.id + '-risk-' + i + '-' + j, paneId: 'price', kind: 'line',
              points: [{time: b.entry_ts, value}, {time: b.open ? inst.bars.at(-1)?.time || b.entry_ts : b.exit_ts, value}],
              style: {color: 'rgba(0,0,0,0)', width: 1, lineStyle: 'solid'}});
          }
        }
        ctx.emit({series}); ctx.pushData(inst.pb || null);
      };
      return {start(c, inputs) { ctx = c; slot = inputs.slot; emit(); }, onBars: emit, onViewport() {},
        setInputs(inputs) { slot = inputs.slot; emit(); }, suspend() {}, resume: emit, stop() { ctx = null; }};
    },
  });
  function pointsToBars(points) {
    const timeline = new Map();
    for (const p of points) if (Number.isFinite(p.time) && Number.isFinite(p.value) && !timeline.has(p.time)) {
      timeline.set(p.time, {time:p.time, open:p.value, high:p.value, low:p.value, close:p.value, volume:0});
    }
    return [...timeline.values()].sort((a,b) => a.time - b.time);
  }
  async function create(host, opts) {
    if (!window.Vela?.Vela) throw Error('Vela did not load. Refresh to retry.');
    host.replaceChildren(); host.classList.add('dashboard-vela'); host.dataset.renderer = 'vela-native';
    const toolbar = h('div', {class: 'dashboard-vela-tools'},
      h('a', {href: 'https://velacharts.dev', target: '_blank', rel: 'noopener', text: 'Vela™', class: 'vela-brand'}));
    const surface = h('div', {class: 'dashboard-vela-surface', 'aria-label': opts.symbol || 'Portfolio equity chart'});
    const readout = h('div', {class: 'dashboard-vela-readout', text: opts.line ? 'Portfolio history' : opts.study ? 'Candles · RSI (14) · paper setups · UTC' : 'Candles · volume · paper trades · UTC'});
    host.append(toolbar, surface, readout);
    const slot = String(++sequence), th = opts.theme;
    const inst = {slot, host, surface, readout, bars: opts.bars || [], pb: opts.pb, models: opts.models || [], study: opts.study,
      chart: null, indicator: null, revision: 0, destroyed: false};
    instances.set(slot, inst); host.velaInstance = inst;
    const chart = inst.chart = new Vela.Vela(surface, {
      data: inst.bars, timeframe: String(opts.minutes || 1), height: Math.max(160, surface.clientHeight),
      live: false, volume: opts.volume ?? !opts.line, drawings: false, priceStyle: opts.line ? 'line' : 'candles',
      currentPriceLine: !opts.line, animations: matchMedia('(prefers-reduced-motion: reduce)').matches ? false : {intro: false, zoom: 100, pan: 80, autoscale: 0},
      theme: {background: th.bg, textColor: th.text, gridColor: th.grid, borderColor: th.grid,
        upColor: th.up, downColor: th.down, fontFamily: 'JetBrains Mono, monospace'},
      upColor: th.up, downColor: th.down,
    });
    inst.destroy = () => { if (inst.destroyed) return; inst.destroyed = true; instances.delete(slot); inst.resizeObserver?.disconnect(); chart.destroy(); };
    inst.refreshOverlay = () => { const inputs={slot, revision: ++inst.revision};inst.indicator?.setInputs(inputs);inst.study?.indicator?.setInputs(inputs); };
    for (const [action, label] of [['fit','Fit'], ['in','+'], ['out','−'], ['fullscreen','Fullscreen']]) {
      toolbar.append(h('button', {type: 'button', class: 'chip', text: label,
        'aria-label': ({fit:'Fit chart', in:'Zoom in', out:'Zoom out', fullscreen:'Chart fullscreen'})[action],
        onclick: () => {
          if (action === 'fit') chart.setVisibleRangePreset('ALL');
          else if (action === 'fullscreen') {
            const promise = document.fullscreenElement ? document.exitFullscreen() : host.requestFullscreen?.();
            promise?.catch(() => { readout.textContent = 'Fullscreen is unavailable. Pan and zoom remain available.'; });
          } else {
            const r = chart.getVisibleRange(); if (!r) return;
            const mid = (r.from + r.to) / 2, half = (r.to - r.from) / 2 * (action === 'in' ? .7 : 1.4);
            chart.setVisibleRange({from: mid - half, to: mid + half});
          }
        }}));
    }
    chart.renderer.set({timezone: opts.line ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC'});
    if (opts.line) chart.renderer.set('lineColor', 'rgba(0,0,0,0)');
    chart.renderer.onCrosshairMove(e => {
      if (!e.bar) return;
      const bar = e.bar;
      readout.textContent = new Date(bar.time).toLocaleString('en-GB', {timeZone: opts.line ? undefined : 'UTC'}) +
        (opts.line ? ' · ' + Number(bar.close).toFixed(2) : ' UTC · O ' + aPrice(bar.open) + ' H ' + aPrice(bar.high) +
          ' L ' + aPrice(bar.low) + ' C ' + aPrice(bar.close) + ' · Vol ' + Number(bar.volume || 0).toLocaleString());
    });
    await chart.ready();
    inst.resizeObserver = new ResizeObserver(() => {
      if (!inst.destroyed) { surface.firstElementChild?.style.setProperty("height", "100%"); chart.resize(); }
    });
    inst.resizeObserver.observe(surface);
    if (!host.isConnected || host.velaInstance !== inst) { inst.destroy(); return null; }
    inst.indicator = chart.addNativeIndicator(TYPE, {inputs: {slot, revision: 0}});
    if (inst.study) { inst.study.indicator=chart.addNativeIndicator(STUDY_TYPE,{inputs:{slot,revision:0}});inst.study.chart=chart;inst.study.destroy=()=>{}; }
    if (inst.pb) inst.pb.requestUpdate = inst.refreshOverlay;
    host.dataset.candles = String(inst.bars.length);
    return inst;
  }
  async function update(inst, bars) {
    if (!inst || inst.destroyed) return;
    const range = inst.chart.getVisibleRange();
    inst.bars = bars;
    await inst.chart.setMarket({data: bars});
    if (inst.destroyed) return;
    if (range) inst.chart.setVisibleRange(range);
    inst.refreshOverlay(); inst.host.dataset.candles = String(bars.length);
  }
  window.addEventListener('pagehide', () => { for (const inst of [...instances.values()]) inst.destroy(); });
  return {create, update, instances, pointsToBars};
})();
