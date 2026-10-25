'use strict';
// MACD desk (LuxAlgo MACD 12/26/9 crosses on Binance SPOT ZEC, the audited settings, paper only). GET-only: it never submits orders or changes rules.
const MacdDesk = (() => {
  const root = () => document.getElementById('macd-root');
  const q = id => document.getElementById('mc-' + id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v !== null && v !== '' && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null;
  const px = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: Number(v) < 1 ? 6 : Number(v) < 10 ? 4 : 2});
  const usd = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const P = (v, d = 1) => num(v) === null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  const stamp = t => num(t) === null ? '—' : new Date(Number(t)).toLocaleString([], {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  const theme = {bg: '#0a1926', text: '#96aab9', grid: '#203241', up: '#00d3cf', down: '#ff5088'};
  const state = {data: null, symbol: 'ZECUSDT', around: 0, inspect: null, bt: {}, chart: null, chartData: null, busy: false, seq: 0,
    timers: [], destroyed: true, built: false};
  async function get(url) { const c = new AbortController(), t = setTimeout(() => c.abort(), 25000); try { const r = await fetch(url, {signal: c.signal, cache: 'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); } }

  function build() {
    if (state.built || !root()) return; state.built = true;
    root().innerHTML = `
      <div class="nw-head"><div><h2>MACD Desk</h2><p class="sub">LuxAlgo “MACD”, defaults 12 / 26 / 9 on the close, on <b>Binance spot ZEC/USDT</b> 4h: buy with all cash when the MACD line crosses above the signal line, sell everything when it crosses below · long or cash, 1x · fills at the next 4h open · 0.10% commission + 0.02% slippage per side · 10,000 USDT · the backtest is the audited window 2024-10-11 → 2026-10-11 (docs/MACD_ZEC_AUDIT.md) · paper only</p></div>
        <div class="nw-head-r"><span id="mc-health" class="pill">Connecting</span><span id="mc-health-detail" class="sub"></span></div></div>
      <section class="nw-card"><h3 class="dt-h3">ZEC spot · audited backtest and the forward paper book (10,000 USDT)</h3><div id="mc-table"></div></section>
      <section class="nw-card ml-card" id="mc-live" hidden></section>
      <section class="nw-card ml-card" id="mc-live-stx" hidden></section>
      <section class="nw-card ml-card" id="mc-live-dash" hidden></section>
      <section class="nw-card ml-card" id="mc-live-xlm" hidden></section>
      <section class="nw-card"><h3 class="dt-h3">ZEC perpetual futures · 2026 by month · one $10 account</h3>
        <p class="sub mc-perp-note">Same MACD rule on the Binance USD-M ZECUSDT perpetual, long or cash at 1x, starting flat on 1 Jan 2026 00:00 UTC. Fills at the next 4h open; 0.05% commission + 0.02% slippage per side; funding at Binance's recorded settlement mark price. Drawdown on hourly closing equity, so intrabar extremes are not included. Recomputed every 15 min; the current month is month-to-date. Matches the operator's January–September table within 0.08 points per month (docs/zec_perp_2026/README.md). One coin chosen with hindsight; paper only.</p>
        <div id="mc-perp"><p class="empty">Loading…</p></div></section>
      <section class="nw-card nw-chart-card"><div class="nw-chart-head"><h3 id="mc-title">ZEC / USDT · 4h</h3><span id="mc-caption" class="sub"></span></div>
        <div id="mc-chart" class="nw-chart"><p class="empty">Loading candles…</p></div>
        <p class="sub nw-legend">Lower pane: MACD line (blue), signal line (orange), histogram (MACD − signal) · ▲ buy at the next 4h open after a bullish cross · each trade line runs to its exit after the bearish cross, coloured by its result</p></section>
      <section class="nw-card"><div id="mc-trades"></div></section>`;
    root().addEventListener('click', e => {
      const c = e.target.closest('[data-mc-symbol]'); if (c) { state.symbol = c.dataset.mcSymbol; state.around = 0; state.inspect = null; renderTable(); loadChart(true); loadTrades(); return; }
      const t = e.target.closest('[data-mc-trade]'); if (t) inspect(t.dataset.mcTrade);
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
      return `<tr data-mc-symbol="${esc(s)}" class="${s === state.symbol ? 'on' : ''}"><td><b>${esc(s.replace(/USDT$/, ''))}</b></td>
        <td class="num ${(b.return_pct || 0) >= 0 ? 'pos' : 'neg'}">${P(b.return_pct)}</td><td class="num ${(b.buy_hold_pct || 0) >= 0 ? 'pos' : 'neg'}">${P(b.buy_hold_pct)}</td><td class="num">${b.max_drawdown_pct == null ? '—' : '−' + Number(b.max_drawdown_pct).toFixed(1) + '%'}</td>
        <td class="num">${b.trades ?? '—'}</td><td class="num">${b.win_rate == null ? '—' : Math.round(100 * b.win_rate) + '%'}</td><td class="num">${b.profit_factor == null ? '—' : Number(b.profit_factor).toFixed(2)}</td>
        <td class="num">${fs.end_equity == null ? '—' : usd(fs.end_equity)}</td><td>${o ? `${esc(o.side)} @ ${esc(px(o.entry))} <span class="${(o.unrealized || 0) >= 0 ? 'pos' : 'neg'}">${P(100 * (o.unrealized || 0) / (o.notional || 1), 2)}</span>` : 'flat'}</td></tr>`;
    }).join('');
    q('table').innerHTML = rows ? `<div class="nw-scroll"><table class="nw-table"><thead><tr><th>Coin</th><th class="num">Backtest return</th><th class="num">Buy &amp; hold</th><th class="num">Max drawdown</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">Profit factor</th><th class="num">Paper equity</th><th>Paper position</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">Waiting for the first scan.</p>';
    q('title').textContent = state.symbol.replace(/USDT$/, ' / USDT') + ' · 4h';
  }

  async function loadTrades() {
    const s = state.symbol;
    if (!state.bt[s]) { try { state.bt[s] = await get('/api/public/macd/backtest?symbol=' + encodeURIComponent(s)); } catch (e) { q('trades').innerHTML = `<p class="empty">Backtest unavailable (${esc(e.message)})</p>`; return; } }
    const bt = state.bt[s], m = bt.summary || {};
    if (!bt.trades) { q('trades').innerHTML = '<p class="empty">Still collecting candles.</p>'; delete state.bt[s]; return; }
    const rows = [...bt.trades].reverse();
    q('trades').innerHTML = `<h3 class="dt-h3">${esc(s.replace(/USDT$/, ''))} backtest · ${esc(stamp(bt.start))} → ${esc(stamp(bt.end))}</h3>
      <p class="sub">${P(m.return_pct)} on 10,000 USDT · max drawdown −${Number(m.max_drawdown_pct || 0).toFixed(1)}% · ${m.trades} trades · ${m.win_rate == null ? '—' : Math.round(100 * m.win_rate) + '%'} winners · profit factor ${m.profit_factor == null ? '—' : Number(m.profit_factor).toFixed(2)} · fees ${usd(m.fees)} USDT</p>
      <div class="nw-scroll"><table class="nw-table"><thead><tr><th>Entry</th><th>Side</th><th class="num">Entry price</th><th>Exit</th><th class="num">Exit price</th><th>Why</th><th class="num">Return</th><th class="num">P&amp;L (USDT)</th><th></th></tr></thead><tbody>${rows.map(t => `<tr data-mc-trade="${esc(t.id)}" class="${t.id === state.inspect?.id ? 'on' : ''}"><td>${esc(stamp(t.entry_t))}</td><td>${esc(t.side)}</td><td class="num">${esc(px(t.entry))}</td><td>${esc(stamp(t.exit_t))}</td><td class="num">${esc(px(t.exit))}</td><td>${esc(t.reason)}</td><td class="num ${(t.ret || 0) > 0 ? 'pos' : 'neg'}">${P(100 * t.ret, 2)}</td><td class="num ${(t.pnl || 0) > 0 ? 'pos' : 'neg'}">${esc(usd(t.pnl))}</td><td><button class="chip">Inspect</button></td></tr>`).join('')}</tbody></table></div>`;
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
    ctx.font = '600 10px sans-serif'; ctx.textBaseline = 'middle';
    for (const t of d.trades || []) {
      const long = t.side === 'long', x1 = X(t.entry_t), x2 = X(t.exit_t || bars.at(-1).time + step), y1 = Y(t.entry), y2 = Y(t.exit ?? bars.at(-1).close);
      if (![x1, x2, y1, y2].every(ok) || x2 < 0 || x1 > coords.width) continue;
      const res = t.ret ?? ((t.unrealized || 0) / (t.notional || 1)), col = res > 0 ? '#00d3cf' : '#ff5088', on = t.id === state.inspect?.id;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.strokeStyle = col; ctx.lineWidth = on ? 2.5 : 1.5; ctx.stroke();
      ctx.beginPath(); const s = 6, ty = long ? y1 + 12 : y1 - 12; ctx.moveTo(x1, long ? ty - s : ty + s); ctx.lineTo(x1 - s, long ? ty + s : ty - s); ctx.lineTo(x1 + s, long ? ty + s : ty - s); ctx.closePath(); ctx.fillStyle = long ? '#00d3cf' : '#ff5088'; ctx.fill();
      const text = (t.exit_t ? '' : 'open ') + P(100 * res, 1), w = ctx.measureText(text).width + 8;
      ctx.fillStyle = 'rgba(6,18,28,.92)'; ctx.fillRect(Math.min(coords.width - w - 2, x2 + 4), y2 - 7, w, 14); ctx.strokeStyle = col; ctx.strokeRect(Math.min(coords.width - w - 2, x2 + 4), y2 - 7, w, 14); ctx.fillStyle = col; ctx.fillText(text, Math.min(coords.width - w - 2, x2 + 4) + 4, y2);
    }
  }}; }

  function studyModels(d, bars) {
    const by = new Map((d.channels || []).map(c => [c.time, c])), pts = k => bars.map(b => ({time: b.time, value: num(by.get(b.time)?.[k])}));
    return [{title: 'MACD', color: '#62beff', width: 1.5, points: pts('macd')}, {title: 'Signal', color: '#ff9f43', width: 1.2, points: pts('signal')},
      {title: 'Histogram', color: 'rgba(0,0,0,0)', points: pts('hist')}, {title: 'Zero', color: 'rgba(150,170,185,.35)', dashed: true, points: bars.map(b => ({time: b.time, value: 0}))}];
  }
  function histPainter(d, bars) { return {boxes: [], markers: [], paint(ctx, args) {
    const {coords, scale, bounds} = args, step = 4 * 3600000;
    const X = t => coords.timeToX(Number(t)), Y = v => coords.priceToY(Number(v), scale, bounds), y0 = Y(0);
    const w = Math.max(1, (X(bars.at(-1).time) - X(bars.at(-1).time - step)) * 0.7);
    let prev = null;
    for (const c of d.channels || []) {
      if (c.hist == null) { prev = null; continue; }
      const x = X(c.time); if (!Number.isFinite(x) || x < -w || x > coords.width + w) { prev = c.hist; continue; }
      const rising = prev == null || c.hist >= prev;
      ctx.fillStyle = c.hist >= 0 ? (rising ? 'rgba(0,211,207,.75)' : 'rgba(0,211,207,.35)') : (rising ? 'rgba(255,80,136,.35)' : 'rgba(255,80,136,.75)');
      const y = Y(c.hist); ctx.fillRect(x - w / 2, Math.min(y, y0), w, Math.max(1, Math.abs(y - y0))); prev = c.hist;
    }
  }}; }

  async function loadChart(force = false) {
    if (state.destroyed || !q('chart') || state.busy) return; state.busy = true;
    const symbol = state.symbol, around = state.around, seq = ++state.seq;
    try {
      const d = await get(`/api/public/macd/chart?symbol=${encodeURIComponent(symbol)}${around ? '&around=' + around : ''}`);
      if (state.destroyed || seq !== state.seq) return;
      const bars = toBars(d.candles); if (!bars.length) throw Error('no candles yet');
      state.chartData = d; const pb = painter(d, bars);
      if (force || !state.chart) {
        state.chart?.destroy(); state.chart = await DashboardVela.create(q('chart'), {symbol, minutes: 240, bars, theme, models: [], pb, volume: false, study: {models: studyModels(d, bars), pb: histPainter(d, bars)}});
        if (state.chart?.readout) state.chart.readout.textContent = 'Candles · MACD (12, 26, 9) · paper trades · UTC';
        const dt = 4 * 3600000, t = state.inspect;
        const from = around && t ? Number(t.entry_t) - 60 * dt : bars.at(-Math.min(bars.length, 220)).time;
        const to = around && t ? Number(t.exit_t || t.entry_t) + 30 * dt : bars.at(-1).time;
        const range = {from: Math.max(bars[0].time, from), to: Math.min(bars.at(-1).time, to)}, inst = state.chart;
        const apply = () => { if (state.chart === inst && !inst.destroyed) inst.chart.setVisibleRange(range); };
        // the chart lays out after creation (and again once fonts and the page settle), which refits it: re-apply the range meanwhile
        apply(); let sized = false;
        const ro = new ResizeObserver(() => { if (inst.host.clientWidth < 100) return; apply(); if (!sized) { sized = true; [150, 600].forEach(ms => setTimeout(apply, ms)); setTimeout(() => ro.disconnect(), 700); } });
        ro.observe(inst.host);
      } else { state.chart.pb = pb; if (state.chart.study) { state.chart.study.models = studyModels(d, bars); state.chart.study.pb = histPainter(d, bars); } await DashboardVela.update(state.chart, bars); }
      q('caption').textContent = around ? `Inspecting the ${state.inspect?.side} trade from ${stamp(state.inspect?.entry_t)}` : `Last 4h candle ${stamp(bars.at(-1).time)} · Binance spot · UTC`;
    } catch (e) { q('chart').innerHTML = `<p class="empty">Chart unavailable (${esc(e.message)}).</p>`; state.chart = null; }
    finally { state.busy = false; }
  }

  async function loadPerp() {
    if (state.destroyed || !q('perp')) return;
    let d;
    try { d = await get('/api/public/macd/perp-monthly'); } catch (e) { q('perp').innerHTML = `<p class="empty">Unavailable (${esc(e.message)}).</p>`; return; }
    if (!d.months?.length) { q('perp').innerHTML = '<p class="empty">Computing: fetching ZEC perpetual candles and funding…</p>'; return; }
    const name = m => new Date(m + '-01T00:00:00Z').toLocaleString('en-US', {month: 'long', timeZone: 'UTC'});
    const money = v => '$' + Number(v).toFixed(2);
    const rows = d.months.map(m => `<tr><td>${esc(name(m.month))}${m.partial ? ' <span class="sub">(to date)</span>' : ''}</td>
      <td class="num ${m.return_pct >= 0 ? 'pos' : 'neg'}">${P(m.return_pct, 2)}</td><td class="num">${(m.max_drawdown_pct >= 0.005 ? '−' : '') + Number(m.max_drawdown_pct).toFixed(2)}%</td>
      <td class="num">${money(m.start_balance)}</td><td class="num"><b>${money(m.end_balance)}</b></td><td class="num">${m.closed_trades}</td></tr>`).join('');
    const t = d.total, o = d.open_position, asOf = new Date(d.as_of).toISOString().slice(0, 16).replace('T', ' ');
    q('perp').innerHTML = `<div class="nw-scroll"><table class="nw-table"><thead><tr><th>Month (UTC)</th><th class="num">Net return</th><th class="num">Max drawdown</th><th class="num">Start balance</th><th class="num">End balance</th><th class="num">Closed trades</th></tr></thead>
      <tbody>${rows}</tbody><tfoot><tr><td><b>Since 1 Jan</b></td><td class="num ${t.return_pct >= 0 ? 'pos' : 'neg'}"><b>${P(t.return_pct, 2)}</b></td><td class="num"><b>−${Number(t.max_drawdown_pct).toFixed(2)}%</b></td><td class="num">$10.00</td><td class="num"><b>${money(t.end_balance)}</b></td><td class="num"><b>${t.closed_trades}</b></td></tr></tfoot></table></div>
      <p class="sub">As of ${esc(asOf)} UTC · net funding ${t.funding_net >= 0 ? '+' : '−'}$${Math.abs(t.funding_net).toFixed(2)} · ${o ? `open long since ${esc(new Date(o.entry_t).toISOString().slice(0, 16).replace('T', ' '))} UTC at ${esc(px(o.entry))}, marked at ${esc(px(o.mark))} (worth ${money(o.value_if_sold)} if sold now, after exit costs)` : 'flat (no open position)'}</p>`;
  }

  async function loadState() {
    if (state.destroyed) return;
    try { state.data = await get('/api/public/macd'); renderHealth(); renderTable(); }
    catch (e) { if (q('health')) { q('health').textContent = 'Unavailable'; q('health-detail').textContent = e.message; } }
  }
  function load() {
    build(); state.destroyed = false; state.timers.forEach(clearInterval);
    state.timers = [setInterval(loadState, 30000), setInterval(() => { if (!state.around) loadChart(); }, 120000), setInterval(loadPerp, 300000)];
    loadState().then(() => { loadChart(true); loadTrades(); loadPerp(); });
    if (typeof MacdLiveUI !== 'undefined') MacdLiveUI.load(q('live'));   // private dashboard only
    if (typeof MacdCoinUIs !== 'undefined') for (const [coin, ui] of Object.entries(MacdCoinUIs)) ui.load(q('live-' + coin));
  }
  function deactivate() { if (typeof MacdLiveUI !== 'undefined') MacdLiveUI.deactivate(); if (typeof MacdCoinUIs !== 'undefined') Object.values(MacdCoinUIs).forEach((u) => u.deactivate()); state.destroyed = true; state.timers.forEach(clearInterval); state.timers = []; state.chart?.destroy(); state.chart = null; }
  return {state, load, deactivate, inspect};
})();
