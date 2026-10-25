'use strict';
// Trading Desk ranking: highest net return among books whose max drawdown is within the limit. Win rate is shown, never
// ranked. Every row states its window, coins, market and sizing, because the desks are not configured alike. GET-only.
const DeskRanking = (() => {
  const root = () => document.getElementById('deskrank-root');
  const q = id => document.getElementById('dr-' + id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v !== null && v !== '' && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null;
  const usd = v => num(v) === null ? '—' : (v > 0 ? '+' : '') + Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const P = (v, d = 1) => num(v) === null ? '—' : (v > 0 ? '+' : '') + Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d}) + '%';
  const utc = t => num(t) === null ? '—' : new Date(Number(t)).toISOString().slice(0, 16).replace('T', ' ');
  const ago = t => { if (num(t) === null) return '—'; const h = (Date.now() - t) / 3600000; return h < 48 ? Math.max(1, Math.round(h)) + ' h' : Math.round(h / 24) + ' days'; };
  const LIMITS = [20, 30, 40, 50, 100];
  const state = {data: null, limit: 40, timer: null, built: false, destroyed: true};
  try { const v = Number(localStorage.getItem('deskrank.limit')); if (LIMITS.includes(v)) state.limit = v; } catch (e) { /* storage unavailable */ }
  async function get(url) { const c = new AbortController(), t = setTimeout(() => c.abort(), 120000); try { const r = await fetch(url, {signal: c.signal, cache: 'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); } }

  // eligible (traded, drawdown within the limit) by net return; then over the limit; then books without trades
  function ordered(rows) {
    const lim = state.limit >= 100 ? Infinity : state.limit;
    const traded = rows.filter(r => r.trades > 0), idle = rows.filter(r => !(r.trades > 0));
    // eligibility uses the actual equity drawdown (open positions marked); rows without one are marked provisional
    const ok = traded.filter(r => num(r.max_dd_pct) !== null && r.max_dd_pct <= lim), over = traded.filter(r => !ok.includes(r));
    const byRoi = (a, b) => (num(b.roi_pct) ?? -Infinity) - (num(a.roi_pct) ?? -Infinity);
    return [...ok.sort(byRoi).map(r => ({...r, status: 'ok'})), ...over.sort(byRoi).map(r => ({...r, status: 'over'})),
      ...idle.map(r => ({...r, status: 'idle'}))];
  }
  const medal = i => ['🥇', '🥈', '🥉'][i] || '#' + (i + 1);
  const cls = v => (num(v) || 0) > 0 ? 'pos' : (num(v) || 0) < 0 ? 'neg' : '';
  const ddCls = v => num(v) === null ? '' : v <= state.limit * 0.5 ? 'dd-ok' : v <= state.limit ? 'dd-mid' : 'dd-bad';

  function table(kind) {
    const all = state.data?.[kind] || [], rows = ordered(all.filter(r => !r.error)), errors = all.filter(r => r.error);
    if (!rows.length) return '<p class="empty">No results yet.</p>';
    let n = 0;
    const when = r => kind === 'live' ? (r.since ? `${ago(r.since)} (since ${utc(r.since)})` : '—')
      : (r.window ? `${utc(r.window[0])} → ${utc(r.window[1])}` : '—');
    return `<div class="nw-scroll"><table class="nw-table dr-table"><thead><tr><th>Rank</th><th>Desk</th><th class="num">Net return</th><th class="num">Max drawdown</th>
        <th class="num">Net P&amp;L (USDT)</th><th class="num">Capital</th><th class="num dr-muted">Win rate</th><th class="num">Trades</th><th>Market · coins · sizing</th><th>${kind === 'live' ? 'Running' : 'Window (UTC)'}</th></tr></thead>
      <tbody>${rows.map(r => `<tr data-dr-tab="${esc(r.tab)}" class="dr-${r.status}" title="Drawdown measured on ${esc(r.dd_basis || '—')}">
        <td class="dr-rank">${r.status === 'ok' ? medal(n++) + (r.dd_provisional ? '<div class="dr-prov" title="Drawdown is from closed trades only; open positions are not marked for this book">provisional</div>' : '') : r.status === 'over' ? '<span class="dr-flag">DD over limit</span>' : '—'}</td>
        <td><b>${esc(r.desk)}</b><div class="sub">${esc(r.book)}${r.open ? ` · ${r.open} open` : ''}</div></td>
        <td class="num dr-big ${cls(r.roi_pct)}">${P(r.roi_pct, 2)}</td>
        <td class="num dr-big ${ddCls(r.max_dd_pct)}">${num(r.max_dd_pct) === null ? '—' : (r.max_dd_pct >= 0.05 ? '−' : '') + Number(r.max_dd_pct).toFixed(1) + '%'}${num(r.dd_stress_pct) !== null ? `<div class="sub dr-bound" title="Stop-loss stress scenario: every trade also dips to its stop before closing. Not an upper bound: open gains can lift the peak first, and stops can fill worse than the trigger. Not used for ranking.">stop-loss stress −${Number(r.dd_stress_pct).toFixed(1)}%</div>` : ''}</td>
        <td class="num ${cls(r.pnl)}">${usd(r.pnl)}</td><td class="num">${num(r.capital) === null ? '—' : Number(r.capital).toLocaleString('en-US')}</td>
        <td class="num dr-muted">${r.trades ? Math.round(100 * r.win_rate) + '%' : '—'}</td><td class="num">${r.trades ?? 0}</td>
        <td class="sub dr-cfg">${esc(r.market)} · ${esc(r.coins ?? '—')} ${typeof r.coins === 'number' ? (r.coins === 1 ? 'coin' : 'coins') : ''} · ${esc(r.sizing)}</td>
        <td class="sub dr-cfg">${esc(when(r))}</td></tr>`).join('')}</tbody></table></div>
      ${errors.length ? `<p class="sub">Unavailable: ${errors.map(e => esc(e.error)).join(', ')}</p>` : ''}`;
  }

  function render() {
    if (!root()) return;
    q('live').innerHTML = table('live');
    q('common').innerHTML = table('common');
    q('full').innerHTML = table('full');
    const w = state.data?.common_window;
    q('common-window').textContent = w ? `${utc(w[0])} → ${utc(w[1])} UTC` : '';
    q('updated').textContent = state.data ? 'Updated ' + new Date(state.data.generated_ms).toLocaleTimeString() : '';
    root().querySelectorAll('[data-dr-limit]').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.drLimit) === state.limit)));
  }

  function build() {
    if (state.built || !root()) return; state.built = true;
    root().innerHTML = `
      <div class="nw-head"><div><h2>Desk ranking</h2><p class="sub">Ranked by <b>net return</b> among books whose <b>max drawdown</b> stays within your limit. Books over the limit are listed below the ranked ones; win rate is shown for information and is <b>not</b> used to rank. Click a row to open that desk. Paper only.</p></div>
        <div class="nw-head-r"><div class="dr-limits" role="group" aria-label="Maximum acceptable drawdown"><span class="sub">Max drawdown limit</span>${LIMITS.map(v => `<button type="button" data-dr-limit="${v}">${v >= 100 ? 'none' : v + '%'}</button>`).join('')}</div><span id="dr-updated" class="sub"></span></div></div>
      <section class="nw-card"><h3 class="dt-h3">Live paper books · since each desk started</h3>
        <p class="sub dr-note">Real forward results. Most desks started in the last few days: small samples, not evidence of an edge.</p><div id="dr-live"><p class="empty">Loading…</p></div></section>
      <section class="nw-card"><h3 class="dt-h3">Backtests on one common window · <span id="dr-common-window"></span></h3>
        <p class="sub dr-note">Every rule desk replayed on the same window with 1,000 USDT per coin. The window is the No-Wick backtest's, the shortest history any desk keeps. <b>Still not like-for-like:</b> the coin sets, markets (spot vs perps) and sizing rules differ, and are listed on every row. MACD is one coin (ZEC) chosen with hindsight; the operator's package reports the same rule across 84 coins at +11.5% with a 57.2% drawdown, and −24.3% on average without ZEC. This orders books; it does not prove which strategy is best. The window covers completed 4h candles only; it starts at the first 4h boundary at least 6 h after No-Wick's data begins (indicator warm-up) where no book holds a trade, so every book starts flat. Max drawdown is the actual equity curve including open positions: on 4h closes for Donchian and MACD, 1m closes for No-Wick and 15m closes for Divergence. The small “stop-loss stress” figure is a scenario, not a bound, and is never used to rank.</p><div id="dr-common"><p class="empty">Loading…</p></div></section>
      <section class="nw-card"><h3 class="dt-h3">Backtests on each desk's own full window · reference only</h3>
        <p class="sub dr-note">Different lengths and configurations, so these are not comparable with each other. MACD is the audited ZEC spot test (docs/MACD_ZEC_AUDIT.md), scaled to 1,000 USDT.</p><div id="dr-full"><p class="empty">Loading…</p></div></section>`;
    root().addEventListener('click', e => {
      const l = e.target.closest('[data-dr-limit]');
      if (l) { state.limit = Number(l.dataset.drLimit); try { localStorage.setItem('deskrank.limit', String(state.limit)); } catch (err) { /* ignore */ } render(); return; }
      const row = e.target.closest('[data-dr-tab]');
      if (row) { const tab = row.dataset.drTab; if (Array.isArray(NAV)) Nav.go(tab); else Nav.go('desk', tab); }
    });
  }

  async function refresh() {
    if (state.destroyed) return;
    try { state.data = await get('/api/public/desks/ranking'); render(); }
    catch (e) { if (q('live')) q('live').innerHTML = `<p class="empty">Ranking unavailable (${esc(e.message)}).</p>`; }
  }
  function load() { build(); state.destroyed = false; clearInterval(state.timer); state.timer = setInterval(refresh, 60000); refresh(); }
  function deactivate() { state.destroyed = true; clearInterval(state.timer); state.timer = null; }
  return {state, load, deactivate};
})();
