/* PaperLab - VALIDATION / V2 candidates: the three frozen v2 TEST survivors.

   Each card shows the pipeline (DEVELOPMENT -> TEST -> MULTI-YEAR -> MONTE CARLO -> STRESS ->
   FORWARD -> QUALIFICATION) and two SEPARATE bodies of evidence side by side:

     HISTORICAL VALIDATION   the continuous 2021-01..2025-10 master ledger (Binance USD-M data)
     LIVE FORWARD SHADOW     the frozen bot on live market data since the forward experiment began

   Their PnLs are never added together. Every block names its venue. The forward numbers move by
   push (shadow_state); the historical ones are fixed once the run completes. */
'use strict';

const STAGE_KIND = { COMPLETED: 'up', PASS: 'up', FAIL: 'down', RUNNING: 'accent', PENDING: 'ghost', COLLECTING: 'accent',
  'NOT ELIGIBLE': 'down', 'NO LIVE SHADOW DATA': 'ghost' };
const pctS = (v, d = 1) => (isNum(v) ? (v > 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : DASH);
const num2 = (v, d = 2) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(d) : DASH);
const pfS = (v) => (!isNum(v) ? DASH : v >= 999 ? '∞' : v.toFixed(2));

const Candidates = {
  base: '/api/competition', data: null, busy: false, timer: 0,
  visible() { const p = $('[data-panel="candidates"]'); return p && !p.hidden; },

  async load() {
    if (this.busy) return;
    this.busy = true;
    try { this.data = await api(this.base + '/candidates'); this.render(); }
    catch (e) { if (e.status !== 401) this.fail(e); }
    finally { this.busy = false; }
  },
  /** A candidate run moved (progress / completion): refetch at most every 3 s while visible. */
  onCompetition(ev) {
    if (!ev || ev.kind !== 'candidates' || !this.visible() || this.timer) return;
    this.timer = setTimeout(() => { this.timer = 0; this.load(); }, 3000);
  },
  /** Live forward numbers, pushed every couple of seconds by the live shadow. */
  onShadowState(s) {
    if (!this.data || !this.visible()) return;
    const byKey = new Map((s.bots || []).map((b) => [b.key, b]));
    for (const c of this.data.candidates || []) {
      const b = byKey.get(c.key);
      if (!b) continue;
      Object.assign(c.forward, { live: b.live, mode: b.mode, session_equity: b.equity, session_net: b.net });
      c.forward.open_positions = b.positions || [];
      const slot = $('#fwd-live-' + CSS.escape(c.key));
      if (slot) slot.replaceWith(this.forwardLive(c));
    }
  },

  fail(e) {
    const g = $('#candidates-grid');
    if (g) clear(g).append(h('div', { class: 'card' }, h('h2', { text: 'could not load the v2 candidates' }),
      h('p', { class: 'msg err', text: (e && e.message) || String(e) })));
  },

  render() {
    const g = $('#candidates-grid');
    if (!g || !this.data) return;
    clear(g);
    const d = this.data, run = d.run || {};
    const head = h('div', { class: 'card' });
    head.append(h('div', { class: 'card-head' }, h('h2', { text: 'FROZEN V2 CANDIDATES — MULTI-YEAR VALIDATION' }),
      pill((run.status || 'NOT RUN').toUpperCase(), run.status === 'complete' ? 'up' : run.status === 'running' ? 'accent' : 'ghost'),
      h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: run.run_id ? 'run ' + run.run_id + ' · protocol ' + ((d.protocol || {}).version || DASH)
        + ' ' + (run.protocol_fingerprint || '') + ' · ' + (run.first_month || '?') + ' → ' + (run.last_month || '?') : 'no run yet' })));
    const prog = run.progress || {};
    if (run.status === 'running') {
      head.append(h('div', { class: 'progress' }, h('i', { style: 'width:' + Math.round(100 * (prog.done || 0) / Math.max(1, prog.total || 1)) + '%' })),
        h('p', { class: 'sub', text: (prog.done || 0) + ' / ' + (prog.total || '?') + ' scenario replays · last ' + (prog.last || DASH) }));
    }
    head.append(h('p', { class: 'sub', text: 'Three bots survived TEST. They have earned multi-year validation only — not qualification, never live. '
      + 'HISTORICAL and LIVE FORWARD evidence are shown separately and never added. Protocol: docs/V2_MULTIYEAR_PROTOCOL.md.' }));
    const tv = d.target_live_venue || {};
    head.append(h('div', { class: 'chips' }, pill('HISTORICAL: BINANCE USD-M', 'accent'), pill('FORWARD: BINANCE USD-M LIVE SHADOW', 'accent'),
      pill('TARGET LIVE VENUE: ' + (tv.label || 'BYBIT LINEAR'), 'warn', 'configured real-money venue; Bybit execution validation required before promotion')));
    g.append(head);
    for (const c of d.candidates || []) g.append(this.card(c));
  },

  card(c) {
    const h0 = c.historical, verdict = h0 ? h0.verdict : null;
    const card = h('div', { class: 'card cand' });
    card.append(h('div', { class: 'card-head' }, h('h2', { class: 'cand-title', text: c.label }),
      verdict ? pill('MULTI-YEAR ' + verdict, verdict === 'PASS' ? 'up' : 'down') : pill('MULTI-YEAR PENDING', 'ghost'),
      h('span', { class: 'spacer' }),
      h('span', { class: 'sub mono', text: 'manifest ' + ((c.manifest || {}).manifest_fingerprint || DASH) + ' · source '
        + Object.entries((c.manifest || {}).source_fingerprint || {}).map(([k, v]) => k + ' ' + v).join(' · ') })));
    const pipe = h('div', { class: 'pipeline' });
    (c.pipeline || []).forEach((s, i) => {
      if (i) pipe.append(h('span', { class: 'sub', text: '→' }));
      pipe.append(h('span', { class: 'chip ' + (STAGE_KIND[s.status] || 'ghost'), title: s.note || '', text: s.stage + ' · ' + s.status }));
    });
    card.append(pipe);
    const cols = h('div', { class: 'cand-cols' });
    cols.append(this.historical(c), this.forward(c));
    card.append(cols);
    return card;
  },

  // ---- HISTORICAL -------------------------------------------------------------------------------
  historical(c) {
    const box = h('div', { class: 'cand-col hist' });
    box.append(h('h3', { text: 'HISTORICAL VALIDATION' }), pill((c.venues.historical || {}).label || 'BINANCE USD-M', 'accent'));
    const r = c.historical;
    if (!r) { box.append(h('p', { class: 'sub', text: 'not run yet' })); return box; }
    const m = r.metrics || {}, w = r.windows || {}, rb = r.robustness || {}, hd = r.headroom || {}, mc = r.monte_carlo || {};
    box.append(h('p', { class: 'sub', text: (r.window || {}).first + ' → ' + (r.window || {}).last + ' · one continuous ledger from '
      + fmtMoney(m.starting_equity) + ', never reset' }));
    const halt = r.halt || {};
    if (halt.kind) box.append(h('p', { class: 'down', text: 'HALTED (' + halt.kind + ') ' + fmtTs(halt.ts) + ' — ' + (halt.note || '')
      + (halt.refused_after ? ' · ' + halt.refused_after + ' later signals refused' : '') }));
    const cap = r.capacity || {};
    if (isNum(cap.below_exchange_minimum_share)) box.append(h('p', { class: 'sub', text: 'capacity: ' + cap.trades + ' trades from ' + cap.signals
      + ' signals; ' + Math.round(cap.below_exchange_minimum_share * 100) + '% of signals were below the exchange minimum for a 20 USDT book' }));
    box.append(this.spark(r.equity_preview || r.daily_equity || [], m.starting_equity));
    box.append(h('div', { class: 'tiles small' },
      tile('equity', fmtMoney(m.starting_equity) + ' → ' + fmtMoney(m.ending_equity), signClass(m.net_profit), pctS(m.net_return_pct)),
      tile('net after costs', fmtMoney(m.net_profit, true), signClass(m.net_profit), 'gross ' + fmtMoney(m.gross_pnl, true)),
      tile('trades', String(m.trades ?? DASH), '', (isNum(r.trades_per_month) ? r.trades_per_month.toFixed(1) : DASH) + ' / month'),
      tile('win rate', pctOf(m.win_rate), '', 'avg win ' + fmtMoney(m.average_win) + ' · loss ' + fmtMoney(m.average_loss)),
      tile('expectancy', num2(m.expectancy_r, 3) + ' R', signClass(m.expectancy_r)),
      tile('profit factor', pfS(m.profit_factor), isNum(m.profit_factor) && m.profit_factor >= 1.15 ? 'up' : 'down'),
      tile('max drawdown', pctOf(m.max_drawdown_pct), (m.max_drawdown_pct || 0) >= 0.3 ? 'down' : '', 'longest losing streak ' + (m.longest_loss_streak ?? DASH)),
      tile('costs', fmtMoney(-(m.fees_paid || 0), true) + ' fees', 'down', 'slippage ' + fmtMoney(-(m.slippage_cost || 0), true) + ' · funding ' + fmtMoney(m.funding_paid, true)),
      tile('leverage', (isNum(m.avg_effective_leverage) ? m.avg_effective_leverage.toFixed(2) : DASH) + 'x avg', '', 'max ' + (isNum(m.max_effective_leverage) ? m.max_effective_leverage.toFixed(2) : DASH) + 'x · liquidations ' + (m.liquidation_count ?? 0)),
      tile('profitable quarters', (w.profitable ?? DASH) + ' / ' + (w.active ?? DASH), (w.profitable_ratio || 0) > 0.5 ? 'up' : 'down', 'median quarter ' + pctS(w.median_return)),
      tile('cost headroom', num2(hd.binance_headroom_bps, 1) + ' bps', signClass(hd.binance_headroom_bps),
        'break-even ' + num2(hd.breakeven_cost_bps, 1) + ' bps vs Binance ' + (isNum(hd.binance_cost_bps) ? hd.binance_cost_bps.toFixed(1) : DASH) + ' / Bybit ' + (isNum(hd.bybit_cost_bps) ? hd.bybit_cost_bps.toFixed(1) : DASH)),
      tile('Monte Carlo ruin', pctOf(mc.ruin_probability, 1), (mc.ruin_probability || 0) <= 0.05 ? 'up' : 'down', 'p5 end ' + fmtMoney(mc.p5_ending_equity) + ' · p95 DD ' + pctOf(mc.p95_max_dd))));
    box.append(this.gates(r.gates || []));
    box.append(this.details('year by year', this.yearTable(r.years || [])));
    box.append(this.details('quarters (' + (w.active || 0) + ' active of ' + (w.total || 0) + ')', this.windowTable(w.rows || [])));
    box.append(this.details('regimes' + ((r.regimes || {}).flag ? ' — FLAG: profit from one narrow regime' : ''), this.regimeTables(r.regimes || {})));
    box.append(this.details('stress tests', this.stressTable(r.stress || [])));
    box.append(this.details('Monte Carlo', kvList({ paths: mc.paths, method: 'block bootstrap, block ' + mc.block, ruin: mc.ruin_definition,
      median_ending_equity: mc.median_ending_equity, p5_ending_equity: mc.p5_ending_equity, median_max_dd: mc.median_max_dd,
      p95_max_dd: mc.p95_max_dd, p99_max_dd: mc.p99_max_dd, 'P(dd > 20%)': mc.p_dd_over_20, 'P(dd > 30%)': mc.p_dd_over_30,
      'P(dd > 50%)': mc.p_dd_over_50, median_longest_losing_streak: mc.median_longest_losing_streak,
      p95_longest_losing_streak: mc.p95_longest_losing_streak, probability_of_ruin: mc.ruin_probability })));
    box.append(this.details('robustness', kvList({ largest_winner_share_of_net: rb.largest_winner_share_of_net, top3_share_of_net: rb.top3_share_of_net,
      top10pct_winners_share_of_gross_profit: rb.top10pct_winners_share_of_gross_profit, net_without_best_trade: rb.net_without_best,
      net_without_best_3: rb.net_without_best3, best_year: rb.best_year, net_without_best_year: rb.net_without_best_year,
      concentration_risk: rb.concentration_risk })));
    box.append(this.details('cost headroom', kvList(hd)));
    return box;
  },

  spark(points, start) {
    const pts = (points || []).filter((p) => isNum(p[1]));
    if (pts.length < 2) return h('div', { class: 'sub', text: 'no equity curve yet' });
    const W = 560, H = 90, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys, start || ys[0]), y1 = Math.max(...ys, start || ys[0]);
    const X = (x) => ((x - x0) / Math.max(1, x1 - x0)) * W, Y = (y) => H - ((y - y0) / Math.max(1e-9, y1 - y0)) * H;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('class', 'spark'); svg.setAttribute('preserveAspectRatio', 'none');
    const base = document.createElementNS(ns, 'line');
    base.setAttribute('x1', 0); base.setAttribute('x2', W); base.setAttribute('y1', Y(start)); base.setAttribute('y2', Y(start));
    base.setAttribute('class', 'spark-base');
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('points', pts.map((p) => X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(' '));
    line.setAttribute('class', 'spark-line ' + (ys[ys.length - 1] >= start ? 'up' : 'down'));
    svg.append(base, line);
    return h('div', { class: 'spark-wrap', title: 'master ledger equity (daily), ' + fmtTs(x0) + ' → ' + fmtTs(x1) }, svg);
  },

  gates(gs) {
    const ul = h('ul', { class: 'gates' });
    for (const g of gs) ul.append(h('li', { class: g.ok ? 'up' : 'down' }, h('b', { text: (g.ok ? 'PASS ' : 'FAIL ') }),
      h('span', { text: g.name + ': ' + String(g.actual) + ' (' + g.threshold + ')' })));
    return ul;
  },
  details(title, body) { return h('details', { class: 'cand-more' }, h('summary', { text: title }), body); },

  yearTable(rows) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'year', cell: (r) => [r.year + (r.months < 12 ? ' (' + r.months + ' mo)' : ''),
        r.halted ? [' ', pill('HALTED', 'down', 'the book was halted before this year began: no trading')]
          : r.halted_during ? [' ', pill('HALTED IN YEAR', 'down')] : null] },
      { h: 'trades', num: true, cell: (r) => String(r.trades) },
      { h: 'return', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.return), text: pctS(r.return) }) },
      { h: 'net', num: true, cell: (r) => num(r.net) },
      { h: 'exp R', num: true, cell: (r) => num2(r.expectancy_r, 3) },
      { h: 'PF', num: true, cell: (r) => pfS(r.profit_factor) },
      { h: 'max DD', num: true, cell: (r) => pctOf(r.max_dd) },
    ], rows, { empty: 'no years' });
    return h('div', { class: 'tablewrap' }, t);
  },
  windowTable(rows) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'quarter', cell: (r) => r.window },
      { h: 'trades', num: true, cell: (r) => String(r.trades) },
      { h: 'return', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.return), text: pctS(r.return) }) },
      { h: 'exp R', num: true, cell: (r) => num2(r.expectancy_r, 3) },
      { h: 'PF', num: true, cell: (r) => pfS(r.profit_factor) },
      { h: 'max DD', num: true, cell: (r) => pctOf(r.max_dd) },
      { h: '', cell: (r) => (r.active ? (r.profitable ? pill('PROFITABLE', 'up') : pill('LOSING', 'down')) : pill('INACTIVE', 'ghost')) },
    ], rows, { empty: 'no windows' });
    return h('div', { class: 'tablewrap' }, t);
  },
  regimeTables(rg) {
    const wrap = h('div');
    for (const dim of ['trend', 'vol']) {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [
        { h: dim === 'trend' ? 'trend regime' : 'volatility regime', cell: (r) => r.regime },
        { h: 'time share', num: true, cell: (r) => pctOf(r.time_share, 0) },
        { h: 'trades', num: true, cell: (r) => String(r.trades) },
        { h: 'net', num: true, cell: (r) => num(r.net) },
        { h: 'exp R', num: true, cell: (r) => num2(r.expectancy_r, 3) },
        { h: 'PF', num: true, cell: (r) => pfS(r.profit_factor) },
      ], ((rg[dim] || {}).rows) || [], { empty: 'no trades' });
      wrap.append(h('div', { class: 'tablewrap' }, t));
      if ((rg[dim] || {}).narrow) wrap.append(h('p', { class: 'down', text: 'all net profit comes from ' + rg[dim].narrow_regime }));
    }
    return wrap;
  },
  stressTable(rows) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'scenario', cell: (r) => [r.label, r.gated ? '' : h('span', { class: 'sub', text: ' (reported, not gated)' })] },
      { h: 'trades', num: true, cell: (r) => String(r.trades ?? DASH) },
      { h: 'net', num: true, cell: (r) => num(r.net) },
      { h: 'exp R', num: true, cell: (r) => num2(r.expectancy_r, 3) },
      { h: 'PF', num: true, cell: (r) => pfS(r.profit_factor) },
      { h: 'max DD', num: true, cell: (r) => pctOf(r.max_dd) },
      { h: '', cell: (r) => (r.survives ? pill('SURVIVES', 'up') : pill('FAILS', 'down')) },
    ], rows, { empty: 'no stress runs' });
    return h('div', { class: 'tablewrap' }, t);
  },

  // ---- LIVE FORWARD ----------------------------------------------------------------------------
  forward(c) {
    const f = c.forward || {};
    const box = h('div', { class: 'cand-col fwd' });
    box.append(h('h3', { text: 'LIVE FORWARD SHADOW' }), pill((c.venues.forward || {}).label || 'BINANCE USD-M (live shadow)', 'accent'));
    if (f.status === 'NO LIVE SHADOW DATA') { box.append(h('p', { class: 'sub', text: 'the live shadow is not running on this server' })); return box; }
    box.append(h('p', { class: 'sub', text: 'Forward experiment ' + (f.experiment_id || DASH) + ' · started ' + fmtTs(f.started_ts)
      + ' · ' + fmtDur((f.elapsed_ms || 0) / 1000) + ' · ' + (f.sessions || 0) + ' session' + (f.sessions === 1 ? '' : 's') }));
    box.append(this.forwardLive(c));
    box.append(h('div', { class: 'tiles small' },
      tile('forward trades', String(f.trades || 0), '', 'closed, all compatible sessions'),
      tile('forward net', fmtMoney(f.net, true), signClass(f.net), 'after fees, slippage, funding'),
      tile('expectancy', num2(f.expectancy_r, 3) + ' R', signClass(f.expectancy_r)),
      tile('profit factor', pfS(f.profit_factor))));
    box.append(h('p', { class: 'sub', text: 'Forward evidence is judged on its own. It is never added to the historical ledger.' }));
    return box;
  },
  forwardLive(c) {
    const f = c.forward || {};
    const pos = (f.open_positions || [])[0];
    return h('div', { class: 'fwd-live', id: 'fwd-live-' + c.key },
      pill(f.live ? (f.mode || 'LIVE') : 'WARMING UP', f.live ? (MODE_KIND[f.mode] || 'accent') : 'ghost'),
      h('span', { class: 'sub', text: ' book equity ' }), pushed('cand-eq:' + c.key, f.session_equity, fmtMoney(f.session_equity), 'num'),
      h('span', { class: 'sub', text: ' net ' }), pushed('cand-net:' + c.key, f.session_net, fmtMoney(f.session_net, true), 'num ' + signClass(f.session_net)),
      pos ? h('span', null, ' · ', pill(pos.side, pos.side), ' ' + fmtQty(pos.qty) + ' @ ' + fmtPrice(pos.entry) + ' ',
        pushed('cand-upnl:' + c.key, pos.upnl, fmtMoney(pos.upnl, true), 'num ' + signClass(pos.upnl))) : h('span', { class: 'sub', text: ' · flat' }));
  },
};
