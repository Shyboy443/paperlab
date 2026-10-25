'use strict';
// No-Wick Retest desk (operator's Pine script, paper only). GET-only: it never submits orders or changes rules.
const NoWickDesk = (() => {
  const root = () => document.getElementById('nowick-root');
  const q = id => document.getElementById('nw-' + id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v !== null && v !== '' && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null;
  const px = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: Number(v) < 1 ? 6 : 2});
  const R = v => num(v) === null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + 'R';
  const pct = v => num(v) === null ? '—' : Math.round(100 * v) + '%';
  const stamp = t => num(t) === null ? '—' : new Date(Number(t)).toLocaleString([], {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  const theme = {bg: '#0a1926', text: '#96aab9', grid: '#203241', up: '#00d3cf', down: '#ff5088'};
  const TF_MIN = {'5m': 5, '15m': 15};
  const state = {data: null, symbol: 'BTCUSDT', tf: '5m', around: 0, inspect: null, list: 'paper', backtest: {}, chart: null,
    chartData: null, chartKey: '', busy: false, timers: [], destroyed: true, built: false, seq: 0};
  async function get(url) { const c = new AbortController(), t = setTimeout(() => c.abort(), 25000); try { const r = await fetch(url, {signal: c.signal, cache: 'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); } }

  function build() {
    if (state.built || !root()) return;
    state.built = true;
    root().innerHTML = `
      <div class="nw-head"><div><h2>No-Wick Desk</h2><p class="sub">A candle with no wick on its open side leaves a level; the first retest of that level is traded when the 1-minute EMA20/EMA50 trend agrees · TP 2 ATR · SL 1 ATR · 10 Binance perps · 5m and 15m books · paper only</p></div>
        <div class="nw-head-r"><span id="nw-health" class="pill">Connecting</span><span id="nw-health-detail" class="sub"></span></div></div>
      <div id="nw-books" class="nw-books"></div>
      <section class="nw-card nw-chart-card">
        <div class="nw-controls"><div id="nw-markets" class="nw-markets"></div><div class="nw-tfs" role="group" aria-label="Chart timeframe"><button data-nw-tf="5m">5m</button><button data-nw-tf="15m">15m</button></div></div>
        <div class="nw-chart-head"><h3 id="nw-title">BTC / USDT</h3><span id="nw-caption" class="sub"></span></div>
        <div id="nw-chart" class="nw-chart"><p class="empty">Loading candles…</p></div>
        <p id="nw-legend" class="sub nw-legend">▲ long level (bullish candle, no lower wick) · ▼ short level (bearish candle, no upper wick) · yellow = the level that was traded · green box = take-profit (2 ATR), red box = stop (1 ATR) · dashed lines: 1-minute EMA20 (yellow) and EMA50 (blue) at each candle close</p>
      </section>
      <section class="nw-card"><div class="nw-tabs"><button data-nw-list="paper">Paper trades (forward)</button><button data-nw-list="backtest">Backtest (90 days)</button></div><div id="nw-list"></div></section>`;
    root().addEventListener('click', e => {
      const m = e.target.closest('[data-nw-symbol]'); if (m) { state.symbol = m.dataset.nwSymbol; state.around = 0; state.inspect = null; renderMarkets(); loadChart(true); renderList(); return; }
      const t = e.target.closest('[data-nw-tf]'); if (t) { state.tf = t.dataset.nwTf; state.around = 0; state.inspect = null; renderMarkets(); loadChart(true); renderList(); return; }
      const l = e.target.closest('[data-nw-list]'); if (l) { state.list = l.dataset.nwList; renderList(); return; }
      const r = e.target.closest('[data-nw-trade]'); if (r) inspect(r.dataset.nwTrade);
    });
  }

  function renderHealth() {
    const h = state.data?.health || {};
    q('health').textContent = (h.status || 'Connecting').replaceAll('_', ' ');
    q('health').className = 'pill ' + (h.status === 'RUNNING' ? 'up' : 'warn');
    q('health-detail').textContent = `${h.last_scan_ms ? 'Last scan ' + stamp(h.last_scan_ms) : 'Waiting for the first scan'}${h.error ? ' · ' + h.error : ''} · paper books since ${stamp(state.data?.started_ms)}`;
  }

  function renderBooks() {
    const d = state.data || {}, out = [];
    for (const tf of ['5m', '15m']) {
      const b = d.books?.[tf] || {}, bt = d.backtest?.[tf] || {}, s = bt.summary || {};
      out.push(`<article class="nw-card nw-book"><h3>${tf} book</h3><dl>
        <div><dt>Equity</dt><dd>${esc(px(b.equity))} USDT</dd></div><div><dt>Trades</dt><dd>${b.trades ?? 0}${b.open ? ' (' + b.open + ' open)' : ''}</dd></div>
        <div><dt>Win rate</dt><dd>${pct(b.win_rate)}</dd></div><div><dt>Total</dt><dd class="${(b.total_r || 0) >= 0 ? 'pos' : 'neg'}">${R(b.total_r)}</dd></div></dl>
        <p class="sub">Backtest 90 days, 10 coins: ${bt.status === 'READY' ? `${s.trades} trades · ${pct(s.win_rate)} wins · ${R(s.total_r)} · ${R(s.avg_r)} per trade` : 'computing (two months of 1-minute candles)…'}</p></article>`);
    }
    q('books').innerHTML = out.join('');
  }

  function renderMarkets() {
    const syms = state.data?.universe || ['BTCUSDT', 'ETHUSDT'];
    q('markets').innerHTML = syms.map(s => `<button data-nw-symbol="${esc(s)}" aria-pressed="${s === state.symbol}">${esc(s.replace(/USDT$/, ''))}</button>`).join('');
    root().querySelectorAll('[data-nw-tf]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.nwTf === state.tf)));
    root().querySelectorAll('[data-nw-list]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.nwList === state.list)));
    q('title').textContent = state.symbol.replace(/USDT$/, ' / USDT') + ' · ' + state.tf;
  }

  function tradeRows(rows) {
    if (!rows.length) return '<p class="empty">No trades yet.</p>';
    return `<div class="nw-scroll"><table class="nw-table"><thead><tr><th>Entry</th><th>Market</th><th>TF</th><th>Side</th><th>Level / entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Result</th><th></th></tr></thead><tbody>${rows.map(t => `<tr data-nw-trade="${esc(t.id)}" class="${t.id === state.inspect?.id ? 'on' : ''}"><td>${esc(stamp(t.entry_t))}</td><td>${esc(t.symbol.replace(/USDT$/, ''))}</td><td>${esc(t.tf)}</td><td>${esc(t.side)}</td><td class="num">${esc(px(t.entry))}</td><td class="num">${esc(px(t.stop))}</td><td class="num">${esc(px(t.target))}</td><td>${t.reason === 'open' ? 'open' : esc(px(t.exit)) + ' (' + esc(t.reason) + ')'}</td><td class="num ${(t.r || 0) > 0 ? 'pos' : t.r == null ? '' : 'neg'}">${R(t.r)}</td><td><button class="chip">Inspect</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  async function renderList() {
    if (!q('list')) return;
    renderMarkets();
    if (state.list === 'paper') {
      const rows = (state.data?.books?.[state.tf]?.recent || []).filter(t => t.symbol === state.symbol);
      q('list').innerHTML = `<p class="sub">Forward paper trades on ${esc(state.symbol)} ${esc(state.tf)} since the desk started (all markets count in the book above).</p>` + tradeRows(rows);
      return;
    }
    const key = state.tf + ':' + state.symbol;
    if (!state.backtest[key]) {
      q('list').innerHTML = '<p class="empty">Loading the backtest…</p>';
      try { state.backtest[key] = await get(`/api/public/nowick/backtest?tf=${encodeURIComponent(state.tf)}&symbol=${encodeURIComponent(state.symbol)}`); } catch (e) { q('list').innerHTML = `<p class="empty">Backtest unavailable (${esc(e.message)})</p>`; return; }
    }
    const bt = state.backtest[key], s = bt.summary_symbol || {};
    if (bt.status !== 'READY') { q('list').innerHTML = '<p class="empty">The server is still collecting 90 days of 1-minute candles and replaying them. Check back in a few minutes.</p>'; delete state.backtest[key]; return; }
    const rows = [...(bt.trades || [])].reverse();
    q('list').innerHTML = `<p class="sub">${esc(state.symbol)} ${esc(state.tf)}, ${esc(stamp(bt.start))} → ${esc(stamp(bt.end))}: ${s.trades ?? rows.length} trades · ${pct(s.win_rate)} wins · ${R(s.total_r)} total · ${R(s.avg_r)} per trade · profit factor ${s.profit_factor == null ? '—' : Number(s.profit_factor).toFixed(2)}. Costs: maker 0.02% entry and take-profit, taker 0.05% + 0.02% slippage on stops.</p>` + tradeRows(rows.slice(0, 400));
  }

  function inspect(id) {
    const all = [...(state.data?.books?.[state.tf]?.recent || []), ...((state.backtest[state.tf + ':' + state.symbol] || {}).trades || [])];
    const t = all.find(x => x.id === id); if (!t) return;
    state.inspect = t; state.around = Number(t.entry_t); renderList(); loadChart(true);
    q('chart')?.scrollIntoView({behavior: 'smooth', block: 'center'});
  }

  const toBars = rows => (rows || []).map(c => ({time: Number(c.t), open: +c.o, high: +c.h, low: +c.l, close: +c.c, volume: +c.v || 0}));
  function dense(points, bars) { const m = new Map(points.map(p => [p.time, p.value])); return bars.map(b => ({time: b.time, value: m.has(b.time) ? m.get(b.time) : null})); }

  function painter(d, bars) { return {boxes: [], markers: [], paint(ctx, args) {
    const {coords, scale, bounds} = args; if (!bars.length) return;
    const X = t => coords.timeToX(Number(t)), Y = p => coords.priceToY(Number(p), scale, bounds), ok = v => Number.isFinite(v);
    const lastT = bars.at(-1).time, tfMs = TF_MIN[d.tf] * 60000;
    // levels: a line from the candle that made it to where it was tapped / dropped / now, and a triangle at its origin
    for (const lv of d.levels || []) {
      const x1 = X(lv.origin_t), x2 = X(lv.end_t || lastT + tfMs), y = Y(lv.price);
      if (![x1, x2, y].every(ok) || x2 < 0 || x1 > coords.width) continue;
      const traded = lv.outcome === 'entry', col = traded ? '#f5d547' : lv.dir === 1 ? 'rgba(0,211,207,.75)' : 'rgba(255,80,136,.75)';
      ctx.beginPath(); ctx.setLineDash(lv.outcome === 'untapped' ? [] : [3, 3]); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.strokeStyle = col; ctx.lineWidth = traded ? 2 : 1; ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); const up = lv.dir === 1, s = 5, ty = up ? y + 9 : y - 9; ctx.moveTo(x1, up ? ty - s : ty + s); ctx.lineTo(x1 - s, up ? ty + s : ty - s); ctx.lineTo(x1 + s, up ? ty + s : ty - s); ctx.closePath(); ctx.fillStyle = '#f5d547'; ctx.fill();
    }
    // trades: take-profit and stop boxes from entry to exit, like the script
    ctx.font = '600 10px sans-serif'; ctx.textBaseline = 'middle';
    for (const t of d.trades || []) {
      const x1 = X(t.entry_t), x2 = X(t.exit_t || lastT + tfMs), yE = Y(t.entry), yT = Y(t.target), yS = Y(t.stop);
      if (![x1, x2, yE, yT, yS].every(ok) || x2 < 0 || x1 > coords.width) continue;
      const L = Math.max(0, x1), W = Math.max(6, Math.min(coords.width, x2) - L), on = t.id === state.inspect?.id;
      ctx.fillStyle = on ? 'rgba(0,211,207,.24)' : 'rgba(0,211,207,.13)'; ctx.fillRect(L, Math.min(yE, yT), W, Math.abs(yT - yE));
      ctx.fillStyle = on ? 'rgba(255,80,136,.24)' : 'rgba(255,80,136,.13)'; ctx.fillRect(L, Math.min(yE, yS), W, Math.abs(yS - yE));
      ctx.strokeStyle = on ? '#f5d547' : 'rgba(233,243,252,.35)'; ctx.lineWidth = on ? 1.5 : 1; ctx.strokeRect(L, Math.min(yT, yS), W, Math.abs(yT - yS));
      const tag = (x, y, text, col) => { const w = ctx.measureText(text).width + 8; ctx.fillStyle = 'rgba(6,18,28,.92)'; ctx.fillRect(x, y - 7, w, 14); ctx.strokeStyle = col; ctx.strokeRect(x, y - 7, w, 14); ctx.fillStyle = col; ctx.fillText(text, x + 4, y); };
      tag(Math.max(2, L - 16), yE, t.side === 'long' ? 'L' : 'S', t.side === 'long' ? '#00d3cf' : '#ff5088');
      if (t.reason !== 'open' && num(t.exit) !== null && ok(X(t.exit_t))) tag(Math.min(coords.width - 60, X(t.exit_t) + 4), Y(t.exit), R(t.r), (t.r || 0) > 0 ? '#00d3cf' : '#ff5088');
      if (on) { tag(L + W + 4 > coords.width - 120 ? L + 4 : L + W + 4, yT, `TP ${px(t.target)}`, '#00d3cf'); tag(L + W + 4 > coords.width - 120 ? L + 4 : L + W + 4, yS, `SL ${px(t.stop)}`, '#ff5088'); }
    }
  }}; }

  async function loadChart(force = false) {
    if (state.destroyed || !q('chart')) return;
    if (state.busy) return; state.busy = true;
    const symbol = state.symbol, tf = state.tf, around = state.around, key = `${symbol}:${tf}:${around}`, seq = ++state.seq;
    try {
      const d = await get(`/api/public/nowick/chart?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}${around ? '&around=' + around : ''}`);
      if (state.destroyed || seq !== state.seq) return;
      const bars = toBars(d.candles); if (!bars.length) throw Error('no candles yet');
      state.chartData = d;
      const models = [{title: '1m EMA20', color: '#f5d547', width: 1.2, points: dense((d.ema || []).map(e => ({time: e.time, value: e.fast})), bars)},
                      {title: '1m EMA50', color: '#62beff', width: 1.2, points: dense((d.ema || []).map(e => ({time: e.time, value: e.slow})), bars)}];
      const pb = painter(d, bars);
      if (force || state.chartKey !== key || !state.chart) {
        state.chart?.destroy(); state.chart = null; state.chartKey = key;
        state.chart = await DashboardVela.create(q('chart'), {symbol, minutes: TF_MIN[tf], bars, theme, models, pb, volume: false});
        if (around && state.inspect) { const dt = TF_MIN[tf] * 60000, t = state.inspect; state.chart.chart.setVisibleRange({from: Number(t.origin_t || t.entry_t) - 40 * dt, to: Number(t.exit_t || t.entry_t) + 30 * dt}); }
      } else { state.chart.models = models; state.chart.pb = pb; await DashboardVela.update(state.chart, bars); }
      q('caption').textContent = around ? `Inspecting ${state.inspect?.side || ''} trade at ${px(state.inspect?.entry)} · ${stamp(around)}` : `Last candle ${stamp(bars.at(-1).time)} · Binance USDT perpetual · UTC`;
    } catch (e) { q('chart').innerHTML = `<p class="empty">Chart unavailable (${esc(e.message)}). The desk may still be collecting candles.</p>`; state.chart = null; state.chartKey = ''; }
    finally { state.busy = false; }
  }

  async function loadState() {
    if (state.destroyed) return;
    try { state.data = await get('/api/public/nowick'); renderHealth(); renderBooks(); renderMarkets(); if (state.list === 'paper') renderList(); }
    catch (e) { if (q('health')) { q('health').textContent = 'Unavailable'; q('health-detail').textContent = e.message; } }
  }

  function load() {
    build(); state.destroyed = false;
    state.timers.forEach(clearInterval);
    state.timers = [setInterval(loadState, 15000), setInterval(() => { if (!state.around) loadChart(); }, 60000)];
    loadState().then(() => { renderList(); loadChart(true); });
  }
  function deactivate() { state.destroyed = true; state.timers.forEach(clearInterval); state.timers = []; state.chart?.destroy(); state.chart = null; state.chartKey = ''; }
  return {state, load, deactivate, inspect, loadChart};
})();
