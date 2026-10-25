'use strict';
// T3-CCI and CHI desks (TradingView research strategies). Read-only: GET only, paper only. One factory per desk.
function makeTvDeskUI(desk, rootId) {
  const st = {data: null, hist: null, mode: 'execution', histBook: null, timer: null, built: false, destroyed: true};
  const root = () => document.getElementById(rootId);
  const P = `tv-${desk}-`;
  const q = id => document.getElementById(P + id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v != null && Number.isFinite(Number(v)) ? Number(v) : null;
  const n = (v, d = 2) => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
  const pct = (v, d = 2) => num(v) === null ? '—' : (v > 0 ? '+' : '') + n(v, d) + '%';
  const px = v => num(v) === null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: Number(v) < 1 ? 8 : Number(v) < 10 ? 5 : 3});
  const utc = t => num(t) === null ? '—' : new Date(Number(t)).toISOString().slice(0, 16).replace('T', ' ');
  const cls = v => (num(v) || 0) > 0 ? 'pos' : (num(v) || 0) < 0 ? 'neg' : '';
  const csv = (params) => `/api/public/tv/${desk}/csv?` + new URLSearchParams(params).toString();
  async function get(url) { const c = new AbortController(), t = setTimeout(() => c.abort(), 30000); try { const r = await fetch(url, {signal: c.signal, cache: 'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); } }

  function build() {
    if (st.built || !root()) return; st.built = true;
    root().innerHTML = `<div id="${P}head"></div><section class="nw-card" id="${P}rules"></section>
      <section class="nw-card" id="${P}fwd"><p class="empty">Loading…</p></section><section class="nw-card tv-hist" id="${P}hist"><p class="empty">Loading historical results…</p></section>`;
    root().addEventListener('click', e => {
      const m = e.target.closest('[data-tv-mode]'); if (m) { st.mode = m.dataset.tvMode; renderForward(); return; }
      const b = e.target.closest('[data-tv-hbook]'); if (b) { st.histBook = b.dataset.tvHbook; renderHistory(); }
    });
  }

  function renderHead() {
    const d = st.data, h = d.health || {};
    const bad = ['STALLED', 'DEGRADED'].includes(h.status);
    q('head').innerHTML = `<div class="nw-head"><div><h2>${esc(d.name)}</h2>
      <p class="sub"><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.strategy)}</a> by ${esc(d.author)} · long / cash spot adaptation from the operator's research package · <b>paper only</b>, no orders reach an exchange</p></div>
      <div class="nw-head-r"><span class="pill ${h.status === 'RUNNING' ? 'up' : 'warn'}">${esc((h.status || '—').replaceAll('_', ' '))}</span>
      <span class="sub">${bad ? '⚠️ ' : ''}worker heartbeat ${h.heartbeat_age_s == null ? '—' : n(h.heartbeat_age_s, 0) + ' s ago'}${h.error ? ' · ' + esc(h.error) : ''}</span></div></div>`;
    const params = Object.entries(d.params || {}).map(([k, v]) => `<span><b>${esc(k.replaceAll('_', ' '))}</b> ${esc(v)}</span>`).join('');
    const venues = {};
    (d.books || []).forEach(b => { (venues[b.venue] ||= []).push(b.asset); });
    q('rules').innerHTML = `<h3 class="dt-h3">Rules · ${esc(d.rule_version)}</h3><ul class="tv-rules">${(d.rules || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      <div class="tv-params">${params}</div>
      <p class="sub">Market: spot, long or cash, no leverage, no shorts, no funding · ${d.books_count} independent book${d.books_count === 1 ? '' : 's'} × ${n(d.capital_per_book, 0)} USDT = <b>${n(d.allocated_capital, 0)} USDT allocated</b> (no book spends another's cash) ·
      costs per side: commission ${n(100 * d.costs.commission_per_side, 2)}%, adverse slippage ${n(100 * d.costs.slippage_per_side, 2)}% ·
      venues: ${Object.entries(venues).map(([v, a]) => `${esc(v)} (${a.length > 6 ? a.length + ' coins' : a.map(esc).join(', ')})`).join(' · ')}</p>
      ${d.islast_note ? `<p class="sub tv-note"><b>barstate.islast:</b> ${esc(d.islast_note)}</p>` : ''}`;
  }

  function renderForward() {
    const d = st.data, T = d.totals[st.mode], hist = d.forward_monthly || [];
    const books = [...(d.books || [])].sort((a, b) => (b[st.mode].equity - a[st.mode].equity));
    const tile = (k, v, c = '') => `<div><small>${k}</small><b class="${c}">${v}</b></div>`;
    const rows = books.map(b => { const m = b[st.mode], e = m.entry, p = m.pending;
      return `<tr><td><b>${esc(b.asset)}</b><div class="sub">${esc(b.venue)} ${esc(b.pair)}</div></td>
        <td>${m.units ? `${n(m.units, 4)} @ ${px(e && e.entry_price)}<div class="sub">since ${utc(e && e.entry_time)} UTC</div>` : '<span class="sub">cash</span>'}</td>
        <td>${p ? `<b>${esc(p.kind)}</b> at the ${utc(p.fill_t)} open` : '—'}</td>
        <td class="num ${cls(m.realized)}">${n(m.realized)}</td><td class="num ${cls(m.unrealized)}">${n(m.unrealized)}</td>
        <td class="num">${n(m.equity)}</td><td class="num ${cls(m.return_pct)}">${pct(m.return_pct)}</td><td class="num">${n(m.fees)}</td>
        <td class="num">${m.trades}</td><td class="num">${m.skips_total || 0}</td><td class="sub">${b.last_candle ? utc(b.last_candle) : '—'}${b.error ? ` <span class="neg" title="${esc(b.error.error)}">⚠</span>` : ''}</td></tr>`; }).join('');
    const skips = books.flatMap(b => (b[st.mode].skips || []).map(s => ({asset: b.asset, ...s}))).sort((a, b) => b.time - a.time).slice(0, 12);
    const div = desk === 'chi' ? books.reduce((s, b) => s + (b.divergences || 0), 0) : null;
    q('fwd').innerHTML = `<div class="tv-sec-head"><h3 class="dt-h3">Forward paper trading · since ${utc(d.forward_start_ms)} UTC</h3>
        <div class="tv-modes"><button data-tv-mode="execution" aria-pressed="${st.mode === 'execution'}">Execution simulation</button><button data-tv-mode="research" aria-pressed="${st.mode === 'research'}">Research reference (fractional)</button></div></div>
      <p class="sub">${st.mode === 'execution' ? 'The desk\'s paper P&L: exchange quantity step and minimum notional enforced; impossible orders are skipped with their reason.' : 'Reference only: fractional units, exactly the research accounting. Not a second account.'}
      Signals use completed UTC 4h candles; a request fills at the next 4h open. Warm-up candles (${d.warmup_days} days) never trade; every book started flat. Historical results are not included here.</p>
      ${realtimeNote(d.realtime_study)}
      <div class="bt-tiles tv-tiles">${tile('Equity', n(T.equity) + ' USDT')}${tile('Return', pct(T.return_pct), cls(T.return_pct))}${tile('Realized', n(T.realized), cls(T.realized))}${tile('Unrealized', n(T.unrealized), cls(T.unrealized))}${tile('Fees', n(T.fees))}${tile('Max drawdown', '−' + n(T.max_dd_pct) + '%')}${tile('Trades', T.trades)}${tile('Open / pending', T.open + ' / ' + T.pending)}${tile('Skipped orders', T.skips)}</div>
      <div class="nw-scroll"><table class="nw-table"><thead><tr><th>Book</th><th>Position</th><th>Pending order</th><th class="num">Realized</th><th class="num">Unrealized</th><th class="num">Equity</th><th class="num">Return</th><th class="num">Fees</th><th class="num">Trades</th><th class="num">Skipped</th><th>Last candle</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${skips.length ? `<h4 class="ml-h4">Skipped orders (execution simulation)</h4><ul class="tv-list">${skips.map(s => `<li><b>${esc(s.asset)}</b> ${esc(s.side)} at ${utc(s.time)} UTC: ${esc(s.reason)}</li>`).join('')}</ul>` : ''}
      ${desk === 'chi' ? `<p class="sub">islast divergences recorded: <b>${div}</b> (candles where a later re-computation without the substitution would request differently; decisions are never changed after the fact).</p>` : ''}
      ${hist.length ? `<h4 class="ml-h4">Forward months (execution simulation)</h4><table class="nw-table"><thead><tr><th>Month</th><th class="num">Opening</th><th class="num">Ending</th><th class="num">Return</th></tr></thead><tbody>${hist.map(m => `<tr><td>${esc(m.month)}</td><td class="num">${n(m.opening_equity)}</td><td class="num">${n(m.ending_equity)}</td><td class="num ${cls(m.net_return_pct)}">${pct(m.net_return_pct)}</td></tr>`).join('')}</tbody></table>` : ''}
      <p class="tv-dl">Download (CSV): <a href="${csv({section: 'forward', kind: 'trades', mode: st.mode})}">trades</a> · <a href="${csv({section: 'forward', kind: 'equity', mode: st.mode})}">equity</a> · <a href="${csv({section: 'forward', kind: 'monthly', mode: st.mode})}">monthly</a> · <a href="${csv({section: 'forward', kind: 'skips', mode: 'execution'})}">skipped orders</a>${desk === 'chi' ? ` · <a href="${csv({section: 'forward', kind: 'divergences'})}">islast divergences</a>` : ''}</p>`;
  }

  function renderHistory() {
    const h = st.hist;
    if (!h || h.status === 'MISSING') { q('hist').innerHTML = '<p class="empty">Historical results are not available.</p>'; return; }
    const S = h.summary, P1 = S.portfolio['1'], P2 = S.portfolio['2'];
    const books = [...h.books].sort((a, b) => b.metrics['1'].net_return_pct - a.metrics['1'].net_return_pct);
    const rows = books.map(b => { const m1 = b.metrics['1'], m2 = b.metrics['2'], ex = b.execution_simulation || {};
      return `<tr data-tv-hbook="${esc(b.asset)}" class="${st.histBook === b.asset ? 'on' : ''}"><td><b>${esc(b.asset)}</b></td><td class="num ${cls(m1.net_return_pct)}">${pct(m1.net_return_pct)}</td>
        <td class="num">−${n(m1.max_drawdown_pct)}%</td><td class="num">−${n(m1.ohlc_drawdown_bound_pct)}%</td><td class="num ${cls(m2.net_return_pct)}">${pct(m2.net_return_pct)}</td>
        <td class="num">${m1.trades}</td><td class="num">${n(m1.ending_capital)}</td><td class="num ${cls(b.splits.first_18_months_pct)}">${pct(b.splits.first_18_months_pct)}</td>
        <td class="num ${cls(b.splits.last_6_months_pct)}">${pct(b.splits.last_6_months_pct)}</td><td class="num">${ex.metrics ? pct(ex.metrics.net_return_pct) : '—'}</td></tr>`; }).join('');
    const book = st.histBook && desk === 't3cci' ? st.histBook : null;
    const months = book ? (h.book_monthly[book] || []) : h.portfolio_monthly['1'];
    const cmp = S.comparisons;
    const headline = desk === 'chi'
      ? `<div class="bt-tiles tv-tiles"><div><small>84-book return</small><b class="${cls(P1.net_return_pct)}">${pct(P1.net_return_pct)}</b></div><div><small>Doubled costs</small><b>${pct(P2.net_return_pct)}</b></div>
         <div><small>Close drawdown</small><b>−${n(P1.max_drawdown_pct)}%</b></div><div><small>OHLC bound</small><b>−${n(P1.ohlc_drawdown_bound_pct)}%</b></div><div><small>Trades</small><b>${P1.trades}</b></div>
         <div><small>Ending capital</small><b>${n(P1.ending_capital)}</b></div><div><small>Without ZEC</small><b>${pct(S.without_zec_return_pct)}</b></div><div><small>Profitable coins</small><b>${S.positive_books} / ${S.books}</b></div>
         <div><small>Median coin</small><b class="${cls(S.median_book_return_pct)}">${pct(S.median_book_return_pct)}</b></div><div><small>First 18 months / last 6</small><b>${pct(S.portfolio_splits.first_18_months_pct)} / ${pct(S.portfolio_splits.last_6_months_pct)}</b></div></div>`
      : '';
    const caveat = desk === 'chi'
      ? `CHI lost money on <b>${S.losing_books} of ${S.books}</b> coins; the median coin returned ${pct(S.median_book_return_pct)}. Equal-weight buy-and-hold of the same 84 assets returned ${pct(cmp.equal_weight_buy_and_hold_84_pct)} with a −75.90% close drawdown (the package's buy-and-hold control). This backtest is not reachable in real time: decided at each candle's close, CHI enters far less often (see the forward section).`
      : `T3-CCI / ZEC (${pct(books.find(b => b.asset === 'ZEC').metrics['1'].net_return_pct)}) <b>underperformed</b> the earlier audited MACD ZEC result (${pct(cmp.macd_zec_spot_audited_pct)}) and simply holding ZEC (${pct(cmp.zec_buy_and_hold_pct)}, same costs). ZEC and CAKE are separate books; there is no combined headline.`;
    q('hist').innerHTML = `<div class="tv-sec-head"><h3 class="dt-h3">Historical research reproduction · ${esc(S.window_utc[0])} → ${esc(S.window_utc[1])} UTC</h3><span class="pill">HISTORICAL</span></div>
      <p class="sub">The research package's recorded snapshot, replayed by this desk's own code and reconciled trade by trade and candle by candle (docs/TVDESKS_AUDIT.md). Fractional units, ${n(S.capital_per_book, 0)} USDT per book, all-in, no rebalancing; an open position is sold at the final close. Past results; they say nothing certain about the future, and no official TradingView Strategy Tester parity is claimed.</p>
      ${headline}<p class="tv-caveat">${caveat}</p>
      <div class="nw-scroll tv-books"><table class="nw-table"><thead><tr><th>Book</th><th class="num">Return</th><th class="num">Close DD</th><th class="num">OHLC bound</th><th class="num">Doubled costs</th><th class="num">Trades</th><th class="num">Ending</th><th class="num">First 18 m</th><th class="num">Last 6 m</th><th class="num">Exec. sim*</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="sub">* Execution simulation over the same history with today's exchange quantity steps and minimum notionals (not recorded historically): a separate experiment. ${desk === 't3cci' ? 'Click a book for its months.' : ''}</p>
      <h4 class="ml-h4">${book ? esc(book) + ' months' : (desk === 'chi' ? '84-book portfolio months' : 'Months (both books combined — click a book above)')}</h4>
      <div class="nw-scroll"><table class="nw-table"><thead><tr><th>Month</th><th class="num">Opening</th><th class="num">Ending</th><th class="num">Return</th><th class="num">Closed trades</th></tr></thead><tbody>${months.map(m => `<tr><td>${esc(m.month)}${m.partial_month ? ' <span class="sub">(partial)</span>' : ''}</td><td class="num">${n(m.opening_equity)}</td><td class="num">${n(m.ending_equity)}</td><td class="num ${cls(m.net_return_pct)}">${pct(m.net_return_pct)}</td><td class="num">${m.closed_trades ?? '—'}</td></tr>`).join('')}</tbody></table></div>
      <p class="tv-dl">Download (CSV): <a href="${csv({section: 'history', kind: 'books'})}">book summary</a> · <a href="${csv({section: 'history', kind: 'trades', book: book || 'portfolio'})}">trades${book ? ' ' + esc(book) : ''}</a> · <a href="${csv({section: 'history', kind: 'equity', book: book || 'portfolio'})}">equity${book ? ' ' + esc(book) : ''}</a> · <a href="${csv({section: 'history', kind: 'monthly', book: book || 'portfolio'})}">monthly</a> · <a href="${csv({section: 'history', kind: 'monthly', book: 'portfolio', cost: 2})}">monthly at doubled costs</a></p>`;
  }

  // CHI: the same rule decided at each candle's own close (as this desk and any live trading must) vs the backtest
  function realtimeNote(r) {
    if (!r) return '';
    const B = r.portfolio.backtest, R = r.portfolio.realtime, T = r.totals;
    return `<p class="tv-caveat"><b>Expect far fewer trades than the backtest.</b> The source swaps the newest close for the previous one (<code>barstate.islast</code>). In a backtest only the final candle is "last", but in real time every candle is decided as the last one, and CHI's entry trigger (a jump in Bollinger width) mostly comes from that candle's own move. Replayed over the same ${esc(r.window_utc[0].slice(0, 7))} → ${esc(r.window_utc[1].slice(0, 7))} snapshot with decisions at each close: <b>${T.buy_requests_realtime}</b> entry signals instead of ${T.buy_requests_backtest}, <b>${R.trades}</b> trades instead of ${B.trades}, ${pct(R.net_return_pct)} instead of ${pct(B.net_return_pct)} (${T.realtime_books_without_trades} of ${r.books} coins never trade). Historical; ${esc(r.script)}.</p>`;
  }

  async function refresh() {
    if (st.destroyed) return;
    try { st.data = await get(`/api/public/tv/${desk}`); renderHead(); renderForward(); }
    catch (e) { if (q('fwd')) q('fwd').innerHTML = `<p class="empty">Unavailable (${esc(e.message)}).</p>`; }
  }
  async function loadHistory() {
    if (st.hist) return renderHistory();
    try { st.hist = await get(`/api/public/tv/${desk}/history`); if (desk === 't3cci' && !st.histBook) st.histBook = 'ZEC'; renderHistory(); }
    catch (e) { if (q('hist')) q('hist').innerHTML = `<p class="empty">Historical results unavailable (${esc(e.message)}).</p>`; }
  }
  function load() { build(); st.destroyed = false; clearInterval(st.timer); st.timer = setInterval(refresh, 60000); refresh(); loadHistory(); }
  function deactivate() { st.destroyed = true; clearInterval(st.timer); st.timer = null; }
  return {load, deactivate, state: st};
}
const T3CciDesk = makeTvDeskUI('t3cci', 't3cci-root');
const ChiDesk = makeTvDeskUI('chi', 'chi-root');
