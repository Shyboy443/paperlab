'use strict';
// Donchian Trend Rules desk (LuxAlgo defaults, paper only). GET-only: it never submits orders or changes rules.
const DonchianDesk = (() => {
  const root = () => document.getElementById('donchian-root');
  const q = id => document.getElementById('dt-' + id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v !== null && v !== '' && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null;
  const px = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: Number(v) < 1 ? 6 : Number(v) < 10 ? 4 : 2});
  const usd = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const P = (v, d = 1) => num(v) === null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  const stamp = t => num(t) === null ? '—' : new Date(Number(t)).toLocaleString([], {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  const theme = {bg: '#0a1926', text: '#96aab9', grid: '#203241', up: '#00d3cf', down: '#ff5088'};
  const state = {data: null, symbol: 'XRPUSDT', around: 0, inspect: null, bt: {}, chart: null, chartData: null, busy: false, seq: 0,
    timers: [], destroyed: true, built: false};
  async function get(url) { const c = new AbortController(), t = setTimeout(() => c.abort(), 25000); try { const r = await fetch(url, {signal: c.signal, cache: 'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); } }

  function build() {
    if (state.built || !root()) return; state.built = true;
    root().innerHTML = `
      <div class="nw-head"><div><h2>Donchian Desk</h2><p class="sub">LuxAlgo “Donchian Trend Rules”, defaults: long on a 4h close above the prior 20-bar high, short on a close below the prior 20-bar low, exit on a close beyond the prior 10-bar low / high, an opposite signal reverses · fills at the next 4h open · whole book as 1x notional · Binance fees, slippage and funding · paper only</p></div>
        <div class="nw-head-r"><span id="dt-health" class="pill">Connecting</span><span id="dt-health-detail" class="sub"></span></div></div>
      <section class="nw-card"><h3 class="dt-h3">Coins · two-year backtest and the forward paper book (1,000 USDT each)</h3><div id="dt-table"></div></section>
      <section class="nw-card ml-card" id="dt-live" hidden></section>
      <section class="nw-card nw-chart-card"><div class="nw-chart-head"><h3 id="dt-title">XRP / USDT · 4h</h3><span id="dt-caption" class="sub"></span></div>
        <div id="dt-chart" class="nw-chart"><p class="empty">Loading candles…</p></div>
        <p class="sub nw-legend">Shaded band: the 20-bar entry channel (prior bars) · dashed line: the 10-bar trailing exit of the open position · ▲ long / ▼ short entries at the next 4h open · each trade line is coloured by its result</p></section>
      <section class="nw-card"><div id="dt-trades"></div></section>`;
    root().addEventListener('click', e => {
      const c = e.target.closest('[data-dt-symbol]'); if (c) { state.symbol = c.dataset.dtSymbol; state.around = 0; state.inspect = null; renderTable(); loadChart(true); loadTrades(); return; }
      const t = e.target.closest('[data-dt-trade]'); if (t) inspect(t.dataset.dtTrade);
    });
  }

  function renderHealth() {
    const h = state.data?.health || {};
    q('health').textContent = (h.status || 'Connecting').replaceAll('_', ' ');
    q('health').className = 'pill ' + (h.status === 'RUNNING' ? 'up' : 'warn');
    q('health-detail').textContent = `${h.last_scan_ms ? 'Last scan ' + stamp(h.last_scan_ms) : 'Collecting two years of 4h candles…'}${h.error ? ' · ' + h.error : ''} · paper books since ${stamp(state.data?.started_ms)}`;
  }

  function renderTable() {
    const d = state.data || {}, bt = d.backtest || {}, books = d.books || {};
    const rows = (d.universe || []).map(s => {
      const b = bt[s] || {}, f = books[s] || {}, fs = f.summary || {}, o = f.open_trade;
      return `<tr data-dt-symbol="${esc(s)}" class="${s === state.symbol ? 'on' : ''}"><td><b>${esc(s.replace(/USDT$/, ''))}</b></td>
        <td class="num ${(b.return_pct || 0) >= 0 ? 'pos' : 'neg'}">${P(b.return_pct)}</td><td class="num ${(b.buy_hold_pct || 0) >= 0 ? 'pos' : 'neg'}">${P(b.buy_hold_pct)}</td><td class="num">${b.max_drawdown_pct == null ? '—' : '−' + Number(b.max_drawdown_pct).toFixed(1) + '%'}</td>
        <td class="num">${b.trades ?? '—'}</td><td class="num">${b.win_rate == null ? '—' : Math.round(100 * b.win_rate) + '%'}</td><td class="num">${b.profit_factor == null ? '—' : Number(b.profit_factor).toFixed(2)}</td>
        <td class="num">${fs.end_equity == null ? '—' : usd(fs.end_equity)}</td><td>${o ? `${esc(o.side)} @ ${esc(px(o.entry))} <span class="${(o.unrealized || 0) >= 0 ? 'pos' : 'neg'}">${P(100 * (o.unrealized || 0) / (o.notional || 1), 2)}</span>` : 'flat'}</td></tr>`;
    }).join('');
    q('table').innerHTML = rows ? `<div class="nw-scroll"><table class="nw-table"><thead><tr><th>Coin</th><th class="num">2-year return</th><th class="num">Buy &amp; hold</th><th class="num">Max drawdown</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">Profit factor</th><th class="num">Paper equity</th><th>Paper position</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">Waiting for the first scan.</p>';
    q('title').textContent = state.symbol.replace(/USDT$/, ' / USDT') + ' · 4h';
  }

  async function loadTrades() {
    const s = state.symbol;
    if (!state.bt[s]) { try { state.bt[s] = await get('/api/public/donchian/backtest?symbol=' + encodeURIComponent(s)); } catch (e) { q('trades').innerHTML = `<p class="empty">Backtest unavailable (${esc(e.message)})</p>`; return; } }
    const bt = state.bt[s], m = bt.summary || {};
    if (!bt.trades) { q('trades').innerHTML = '<p class="empty">Still collecting candles.</p>'; delete state.bt[s]; return; }
    const rows = [...bt.trades].reverse();
    q('trades').innerHTML = `<h3 class="dt-h3">${esc(s.replace(/USDT$/, ''))} backtest · ${esc(stamp(bt.start))} → ${esc(stamp(bt.end))}</h3>
      <p class="sub">${P(m.return_pct)} on 1,000 USDT · max drawdown −${Number(m.max_drawdown_pct || 0).toFixed(1)}% · ${m.trades} trades · ${m.win_rate == null ? '—' : Math.round(100 * m.win_rate) + '%'} winners · profit factor ${m.profit_factor == null ? '—' : Number(m.profit_factor).toFixed(2)} · fees ${usd(m.fees)} USDT · funding ${(m.funding || 0) >= 0 ? '+' : ''}${usd(m.funding)} USDT</p>
      <div class="nw-scroll"><table class="nw-table"><thead><tr><th>Entry</th><th>Side</th><th class="num">Entry price</th><th>Exit</th><th class="num">Exit price</th><th>Why</th><th class="num">Return</th><th class="num">P&amp;L (USDT)</th><th></th></tr></thead><tbody>${rows.map(t => `<tr data-dt-trade="${esc(t.id)}" class="${t.id === state.inspect?.id ? 'on' : ''}"><td>${esc(stamp(t.entry_t))}</td><td>${esc(t.side)}</td><td class="num">${esc(px(t.entry))}</td><td>${esc(stamp(t.exit_t))}</td><td class="num">${esc(px(t.exit))}</td><td>${esc(t.reason)}</td><td class="num ${(t.ret || 0) > 0 ? 'pos' : 'neg'}">${P(100 * t.ret, 2)}</td><td class="num ${(t.pnl || 0) > 0 ? 'pos' : 'neg'}">${esc(usd(t.pnl))}</td><td><button class="chip">Inspect</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  function inspect(id) {
    const t = (state.bt[state.symbol]?.trades || []).find(x => x.id === id); if (!t) return;
    state.inspect = t; state.around = Number(t.entry_t); loadTrades(); loadChart(true);
    q('chart')?.scrollIntoView({behavior: 'smooth', block: 'center'});
  }

  const toBars = rows => (rows || []).map(c => ({time: Number(c.t), open: +c.o, high: +c.h, low: +c.l, close: +c.c, volume: +c.v || 0}));
  function painter(d, bars) { return {boxes: [], markers: [], paint(ctx, args) {
    const {coords, scale, bounds} = args; if (!bars.length) return;
    const X = t => coords.timeToX(Number(t)), Y = p => coords.priceToY(Number(p), scale, bounds), ok = v => Number.isFinite(v), step = 4 * 3600000;
    // entry channel band
    const ch = (d.channels || []).filter(c => c.up != null);
    if (ch.length > 1) {
      ctx.beginPath(); ch.forEach((c, i) => { const x = X(c.time), y = Y(c.up); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      [...ch].reverse().forEach(c => ctx.lineTo(X(c.time), Y(c.dn))); ctx.closePath();
      const g = ctx.createLinearGradient(0, bounds.top, 0, bounds.top + bounds.height); g.addColorStop(0, 'rgba(0,211,207,.10)'); g.addColorStop(1, 'rgba(255,80,136,.10)');
      ctx.fillStyle = g; ctx.fill();
      for (const [key, col] of [['up', 'rgba(0,211,207,.55)'], ['dn', 'rgba(255,80,136,.55)']]) { ctx.beginPath(); ch.forEach((c, i) => { const x = X(c.time), y = Y(c[key]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.stroke(); }
    }
    const byT = new Map((d.channels || []).map(c => [c.time, c]));
    ctx.font = '600 10px sans-serif'; ctx.textBaseline = 'middle';
    for (const t of d.trades || []) {
      const long = t.side === 'long', x1 = X(t.entry_t), x2 = X(t.exit_t || bars.at(-1).time + step), y1 = Y(t.entry), y2 = Y(t.exit ?? bars.at(-1).close);
      if (![x1, x2, y1, y2].every(ok) || x2 < 0 || x1 > coords.width) continue;
      const res = t.ret ?? ((t.unrealized || 0) / (t.notional || 1)), col = res > 0 ? '#00d3cf' : '#ff5088', on = t.id === state.inspect?.id;
      // trailing exit level while the position is held (prior 10-bar low for a long, high for a short)
      ctx.beginPath(); let started = false;
      for (const b of bars) { if (b.time < t.entry_t || b.time > (t.exit_t || Infinity)) continue; const c = byT.get(b.time); const lv = c && (long ? c.xlo : c.xhi); if (lv == null) continue; const x = X(b.time), y = Y(lv); started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true; }
      ctx.setLineDash([4, 3]); ctx.strokeStyle = 'rgba(245,213,71,.8)'; ctx.lineWidth = 1.2; ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.strokeStyle = col; ctx.lineWidth = on ? 2.5 : 1.5; ctx.stroke();
      ctx.beginPath(); const s = 6, ty = long ? y1 + 12 : y1 - 12; ctx.moveTo(x1, long ? ty - s : ty + s); ctx.lineTo(x1 - s, long ? ty + s : ty - s); ctx.lineTo(x1 + s, long ? ty + s : ty - s); ctx.closePath(); ctx.fillStyle = long ? '#00d3cf' : '#ff5088'; ctx.fill();
      const text = (t.exit_t ? '' : 'open ') + P(100 * res, 1), w = ctx.measureText(text).width + 8;
      ctx.fillStyle = 'rgba(6,18,28,.92)'; ctx.fillRect(Math.min(coords.width - w - 2, x2 + 4), y2 - 7, w, 14); ctx.strokeStyle = col; ctx.strokeRect(Math.min(coords.width - w - 2, x2 + 4), y2 - 7, w, 14); ctx.fillStyle = col; ctx.fillText(text, Math.min(coords.width - w - 2, x2 + 4) + 4, y2);
    }
  }}; }

  async function loadChart(force = false) {
    if (state.destroyed || !q('chart') || state.busy) return; state.busy = true;
    const symbol = state.symbol, around = state.around, seq = ++state.seq;
    try {
      const d = await get(`/api/public/donchian/chart?symbol=${encodeURIComponent(symbol)}${around ? '&around=' + around : ''}`);
      if (state.destroyed || seq !== state.seq) return;
      const bars = toBars(d.candles); if (!bars.length) throw Error('no candles yet');
      state.chartData = d; const pb = painter(d, bars);
      if (force || !state.chart) {
        state.chart?.destroy(); state.chart = await DashboardVela.create(q('chart'), {symbol, minutes: 240, bars, theme, models: [], pb, volume: false});
        const dt = 4 * 3600000, t = state.inspect;
        const from = around && t ? Number(t.entry_t) - 60 * dt : bars.at(-Math.min(bars.length, 220)).time;
        const to = around && t ? Number(t.exit_t || t.entry_t) + 30 * dt : bars.at(-1).time;
        const range = {from: Math.max(bars[0].time, from), to: Math.min(bars.at(-1).time, to)}, inst = state.chart;
        const apply = () => { if (state.chart === inst && !inst.destroyed) inst.chart.setVisibleRange(range); };
        // the chart lays out after creation (and again once fonts and the page settle), which refits it: re-apply the range meanwhile
        apply(); let sized = false;
        const ro = new ResizeObserver(() => { if (inst.host.clientWidth < 100) return; apply(); if (!sized) { sized = true; [150, 600].forEach(ms => setTimeout(apply, ms)); setTimeout(() => ro.disconnect(), 700); } });
        ro.observe(inst.host);
      } else { state.chart.pb = pb; await DashboardVela.update(state.chart, bars); }
      q('caption').textContent = around ? `Inspecting the ${state.inspect?.side} trade from ${stamp(state.inspect?.entry_t)}` : `Last 4h candle ${stamp(bars.at(-1).time)} · Binance USDT perpetual · UTC`;
    } catch (e) { q('chart').innerHTML = `<p class="empty">Chart unavailable (${esc(e.message)}).</p>`; state.chart = null; }
    finally { state.busy = false; }
  }

  async function loadState() {
    if (state.destroyed) return;
    try { state.data = await get('/api/public/donchian'); renderHealth(); renderTable(); }
    catch (e) { if (q('health')) { q('health').textContent = 'Unavailable'; q('health-detail').textContent = e.message; } }
  }
  function load() {
    build(); state.destroyed = false; state.timers.forEach(clearInterval);
    state.timers = [setInterval(loadState, 30000), setInterval(() => { if (!state.around) loadChart(); }, 120000)];
    loadState().then(() => { loadChart(true); loadTrades(); });
    if (typeof DonchianLiveUI !== 'undefined') DonchianLiveUI.load(q('live'));   // private dashboard only
  }
  function deactivate() { if (typeof DonchianLiveUI !== 'undefined') DonchianLiveUI.deactivate(); state.destroyed = true; state.timers.forEach(clearInterval); state.timers = []; state.chart?.destroy(); state.chart = null; }
  return {state, load, deactivate, inspect};
})();
