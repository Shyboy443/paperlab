/* PaperLab - V3 AGGRESSIVE INTRADAY JEV ARENA (docs/V3_PROTOCOL.md).

   The primary ARENA screen. Everything here is the finished DEVELOPMENT discovery, read from
   /api[/public]/competition/v3 (REST for history; nothing on a timer). It answers two questions --
   is there an aggressive small-timeframe bot with a positive after-cost edge, and does Jev make it
   better -- and shows WHY every bot passes or fails (the Bot Analyzer). Built with textContent only. */
'use strict';

const V3_LEVELS = ['SKIP', 'DEFENSIVE', 'NORMAL', 'ATTACK'];
const V3_STATE_KIND = { ADVANCE: 'up', FAIL: 'down', INSUFFICIENT_AGGRESSIVE_PARTICIPATION: 'warn', EXPERIMENTAL: 'ghost' };
const V3_FAILURE_KIND = { ROBUST: 'up', NO_GROSS_EDGE: 'down', FEE_DESTROYED: 'down', SLIPPAGE_DESTROYED: 'down',
  OVERTRADING: 'warn', TOO_LOW_ACTIVITY: 'warn', JEV_OVER_FILTERING: 'warn', JEV_BAD_DISCRIMINATION: 'warn',
  JEV_NO_VALUE: 'warn', DRAWDOWN_FAILURE: 'down', LIQUIDATION: 'down', MIN_NOTIONAL_CONSTRAINED: 'warn',
  REGIME_DEPENDENT: 'warn', PROFIT_CONCENTRATION: 'warn', MARGINAL_EDGE: 'warn', LATENCY_SENSITIVE: 'warn' };
const pc = (v, d = 1, signed = false) => (isNum(v) ? (signed && v > 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : DASH);
const nfix = (v, d = 2, signed = false) => (isNum(v) ? (signed && v > 0 ? '+' : '') + v.toFixed(d) : DASH);
const v3pf = (v) => (!isNum(v) ? DASH : v >= 999 ? '∞' : v.toFixed(2));
const shortV3 = (k) => String(k || '').replace('@20x', '');

const V3 = {
  base: '/api/competition', data: null, bot: null, botKey: '', tab: 'overview', filter: { tf: '', family: '' },
  loading: false, pathBase: '',

  async load() {
    if (this.loading) return;
    this.loading = true;
    try { this.data = await api(this.base + '/v3'); this.render(); if (this.botKey) await this.openBot(this.botKey, true); }
    catch (e) { if (e.status !== 401) this.fail(e); }
    finally { this.loading = false; }
  },
  fail(e) {
    const g = $('#v3-grid');
    if (g) clear(g).append(h('div', { class: 'card' }, h('h2', { text: 'could not load the V3 arena' }),
      h('p', { class: 'msg err', text: (e && e.message) || String(e) })));
  },

  // ---- the arena ---------------------------------------------------------------------------------------
  render() {
    const g = $('#v3-grid');
    if (!g || !this.data) return;
    clear(g);
    const d = this.data;
    g.append(this.headerCard(d));
    if (!d.run) { g.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: d.note || 'no V3 run yet' }))); return; }
    g.append(h('div', { class: 'card', id: 'v3-bot-card', hidden: true }));
    const s = d.summary;
    if (!s) { g.append(this.progressCard(d)); return; }
    g.append(this.answersCard(d, s), this.leaderboardCard(s), this.failureCard(s), this.pairsCard(s),
      this.jevCard(s), this.matrixCard('Strategy × timeframe', s.matrix_strategy_tf, 'strategy family'),
      this.matrixCard('Coin × timeframe', s.matrix_coin_tf, 'coin'), this.timeframeCard(s), this.scanCard(s),
      this.protocolCard(d));
  },

  headerCard(d) {
    const s = d.summary || {};
    const c = s.counts || {};
    const byTf = c.field_by_tf || {};
    const al = (typeof Alive !== 'undefined' && Alive) || {};
    const stream = al.stream || {};
    const jevHealth = ((al.health || {}).jev || {}).status || ((al.health || {}).shadow || {}).jev_state;
    const run = d.run || {};
    const card = h('div', { class: 'card v3-head' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'AGGRESSIVE ARENA' }),
      pill('V3 · DEVELOPMENT DISCOVERY', 'accent'),
      run.status ? pill(String(run.status).toUpperCase(), run.status === 'complete' ? 'up' : 'warn') : null,
      h('span', { class: 'spacer' }), h('span', { class: 'sub mono keep-case', text: run.run_id || '' })));
    card.append(h('p', { class: 'sub', text: (d.venue_label || '') + (s.window ? ' · ' + s.window.from + ' → ' + s.window.to
      + ' (' + s.window.days + ' days, DEVELOPMENT data only)' : '') }));
    card.append(h('div', { class: 'v3-counters' },
      this.counter(c.active_jev_bots ?? DASH, 'ACTIVE JEV BOTS', 'accent'),
      this.counter(c.matched_controls ?? DASH, 'CONTROLS'),
      ...['3m', '5m', '15m', '30m'].map((tf) => this.counter(byTf[tf] ?? 0, tf + (tf === '30m' ? ' benchmark' : ''))),
      this.counter(c.controls_scanned ?? DASH, 'SCANNED', '', (c.experimental_1m || 0) + ' experimental 1m'),
      h('div', { class: 'v3-counter' }, h('b', null, h('i', { class: 'dot ' + (jevHealth === 'OK' || jevHealth === 'READY' ? 'ok' : 'off') }),
        ' ' + (jevHealth || DASH)), h('span', { text: 'JEV HEALTH' })),
      h('div', { class: 'v3-counter' }, h('b', null, h('i', { class: 'dot ' + (stream.status === 'LIVE' ? 'ok' : 'off') }),
        ' ' + (stream.status || 'OFF')), h('span', { text: (stream.transport === 'sse' ? 'SSE' : 'WS') }))));
    const v2 = d.v2 || {};
    if (isNum(v2.passed)) {
      card.append(h('div', { class: 'banner ' + (v2.passed ? 'up' : 'down') },
        h('b', { text: 'V2 MULTI-YEAR ' + v2.passed + ' / ' + v2.candidates + ' PASSED' }),
        h('span', { text: ' — the frozen V2 survivors failed ' + (v2.window || '') + ' after realistic costs (run ' + (v2.run_id || '') + '). '
          + 'Failing is evidence the pipeline filters weak bots; V3 is a new research program, not a repair.' })));
    }
    return card;
  },
  /** Jev health and the push connection change while the page is open: re-render only the header. */
  refreshHeader() {
    const old = document.querySelector('#v3-grid .v3-head');
    if (!old || !this.data) return;
    const al = (typeof Alive !== 'undefined' && Alive) || {};
    const sig = [(al.stream || {}).status, (al.stream || {}).transport, ((al.health || {}).jev || {}).status,
      ((al.health || {}).shadow || {}).jev_state].join('|');
    if (sig === this.headerSig) return;            // nothing the header shows has changed
    this.headerSig = sig;
    const card = this.headerCard(this.data);
    card.classList.add('static');                  // an in-place update must not replay the fade-in
    old.replaceWith(card);
  },
  counter(value, label, cls, sub) {
    return h('div', { class: 'v3-counter ' + (cls || '') }, h('b', { text: String(value) }), h('span', { text: label }),
      sub ? h('small', { text: sub }) : null);
  },

  progressCard(d) {
    const p = d.progress || {};
    const card = h('div', { class: 'card' }, h('h3', { text: 'DISCOVERY IN PROGRESS' }),
      h('p', { class: 'sub', text: 'stage ' + ((d.run || {}).stage || DASH) + ' · ' + (p.bots_done || 0) + ' bots finished'
        + (p.jobs ? ' · current phase ' + p.phase + ' (' + p.jobs + ' jobs)' : '') + '. Results appear when the analysis has run.' }));
    const rows = (d.partial || []).slice(0, 400);
    if (rows.length) {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [
        { h: 'bot', cell: (r) => h('span', { class: 'mono', text: shortV3(r.key) }) },
        { h: 'role', cell: (r) => pill(r.role, r.role === 'JEV' ? 'accent' : 'ghost') },
        { h: 'signals', num: true, cell: (r) => String(r.signals ?? DASH) },
        { h: 'trades', num: true, cell: (r) => String(r.trades ?? DASH) },
        { h: 'net', num: true, cell: (r) => num(r.net_pnl) }], rows);
      card.append(h('div', { class: 'tablewrap' }, t));
    }
    return card;
  },

  answersCard(d, s) {
    const a = s.answers || {};
    const adv = s.advanced_set;
    const none = !Array.isArray(adv) || !adv.length;
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'THE TWO QUESTIONS' }),
      pill(none ? 'ADVANCED SET = NONE' : 'ADVANCED: ' + adv.length, none ? 'down' : 'up')),
      h('dl', { class: 'v3-answers' },
        h('dt', { text: 'Is there an aggressive small-timeframe bot with a positive after-cost edge?' }),
        h('dd', { class: String(a.aggressive_edge || '').startsWith('YES') ? 'up' : 'down', text: a.aggressive_edge || DASH }),
        h('dt', { text: 'Does Jev make that bot better?' }),
        h('dd', { class: String(a.jev_makes_it_better || '').startsWith('YES') ? 'up' : 'down', text: a.jev_makes_it_better || DASH }),
        h('dt', { text: 'Advanced to the HOLDOUT TEST (2026-05 → 2026-08, never loaded)' }),
        h('dd', { text: none ? 'NONE — that is a correct result, not a failure of the arena' : adv.join(', ') })),
      this.fieldNote(d),
      (s.best_aggressive || []).length ? h('p', { class: 'sub', text: 'Profitable with enough trades (not necessarily passing every gate): '
        + s.best_aggressive.slice(0, 6).map((r) => shortV3(r.key) + ' ' + nfix(r.net_pnl, 2, true)).join(' · ') }) : null);
  },

  fieldNote(d) {
    const f = d.field || {};
    if (!f.preregistered && !f.amendment) return null;
    const pre = f.preregistered || {};
    const am = f.amendment || {};
    return h('div', { class: 'banner warn' }, h('b', { text: 'FIELD: ' }),
      h('span', { text: 'the pre-registered activity rule qualified ' + (pre.pairs ?? DASH) + ' Jev pairs → ' + (pre.status || DASH)
        + (am.id ? '. Protocol amendment ' + am.id + ' (decided from activity counts only, before any Jev answer was read) added '
          + am.added + ' EXTENDED pairs so the Jev question has enough pairs; every gate, including participation, still applies to them.' : '') }));
  },
  extended(pairId) {
    const f = (this.data || {}).field || {};
    return (f.pairs || []).some((p) => p.extended && 'v3pair:' + p.strategy_id + '-' + p.coin + '-' + p.timeframe === pairId);
  },

  leaderboardCard(s) {
    const card = h('div', { class: 'card', id: 'v3-lb' });
    const f = this.filter;
    const rows = (s.leaderboard_field || []).filter((r) => (!f.tf || r.tf === f.tf) && (!f.family || r.strategy_id === f.family));
    const sel = (name, opts, label) => h('select', { 'aria-label': label, onchange: (e) => { f[name] = e.target.value; this.rerender('#v3-lb', () => this.leaderboardCard(s)); } },
      h('option', { value: '', text: label }), opts.map((o) => h('option', { value: o, text: o, selected: f[name] === o })));
    card.append(h('div', { class: 'card-head' }, h('h3', { text: 'LEADERBOARD — Jev field and matched controls' }),
      h('span', { class: 'sub', text: 'ranked by the score (separate from the gates); STATUS is the gate verdict' }),
      h('span', { class: 'spacer' }), sel('tf', ['3m', '5m', '15m', '30m'], 'all timeframes'),
      sel('family', ['S31', 'S32', 'S33', 'S34', 'S35', 'S36'], 'all families')));
    card.append(this.lbTable(rows, true));
    return card;
  },
  lbTable(rows, withJev) {
    const t = h('table', { class: 'tbl' });
    const cols = [
      { h: 'bot', cell: (r) => [h('a', { class: 'mono v3-link', href: '#', text: shortV3(r.key), onclick: (e) => { e.preventDefault(); this.openBot(r.key); } }),
        withJev && this.extended(r.pair_id) ? [' ', pill('EXT', 'ghost', 'added by protocol amendment 1')] : null] },
      { h: 'coin', cell: (r) => r.coin },
      { h: 'tf', cell: (r) => r.tf },
      { h: 'mode', cell: (r) => pill(r.role === 'JEV' ? 'JEV2' : r.role, r.role === 'JEV' ? 'accent' : 'ghost') },
      { h: 'equity', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.net_pnl), text: fmtMoney(20 + (r.net_pnl || 0)) }) },
      { h: 'return', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.net_return_pct), text: pc(r.net_return_pct, 1, true) }) },
      { h: 'trades/day', num: true, cell: (r) => nfix(r.trades_per_day, 2) },
      { h: 'ExpR', num: true, title: 'net expectancy per trade, in R', cell: (r) => h('span', { class: 'num ' + signClass(r.exp_r), text: nfix(r.exp_r, 3, true) }) },
      { h: 'PF', num: true, cell: (r) => v3pf(r.pf) },
      { h: 'DD', num: true, cell: (r) => pc(r.max_dd, 1) },
      { h: 'cost bps', num: true, title: 'average round trip (fees + slippage) in bps of notional', cell: (r) => nfix(r.rt_cost_bps, 1) },
    ];
    if (withJev) cols.push({ h: 'Jev take', num: true, cell: (r) => (r.role === 'JEV' ? pc(r.jev_take, 0) : DASH) },
      { h: 'vs ctrl', num: true, title: 'Jev net minus its matched control', cell: (r) => (r.role === 'JEV' ? num(r.jev_contribution) : DASH) });
    cols.push({ h: 'score', num: true, cell: (r) => nfix(withJev ? r.field_score : r.score, 2) },
      { h: 'status', cell: (r) => [pill(r.state === 'ADVANCE' ? 'ADVANCE' : r.state === 'INSUFFICIENT_AGGRESSIVE_PARTICIPATION' ? 'LOW PARTICIPATION' : r.state || DASH, V3_STATE_KIND[r.state] || 'ghost'),
        ' ', h('span', { class: 'sub', text: ((this.data.summary || {}).failure_modes || {}).labels ? ((this.data.summary.failure_modes.labels || {})[r.failure_mode] || r.failure_mode) : r.failure_mode })] });
    renderTable(t, cols, rows, { empty: 'no bots' });
    return h('div', { class: 'tablewrap' }, t);
  },
  rerender(sel, build) { const old = $(sel); if (old) old.replaceWith(build()); },

  failureCard(s) {
    const fm = s.failure_modes || {};
    const labels = fm.labels || {};
    const block = (title, counts) => {
      const total = Object.values(counts || {}).reduce((a, b) => a + b, 0) || 1;
      return h('div', { class: 'v3-fail' }, h('h4', { text: title }),
        ...Object.entries(counts || {}).map(([k, n]) => h('div', { class: 'v3-bar' },
          h('span', { class: 'v3-bar-label', text: labels[k] || k }),
          h('span', { class: 'v3-bar-track' }, h('i', { class: V3_FAILURE_KIND[k] || 'ghost', style: 'width:' + Math.round(100 * n / total) + '%' })),
          h('b', { class: 'num', text: String(n) }))));
    };
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'FAILURE REASONS — the root cause per bot' })),
      h('p', { class: 'sub', text: 'One primary reason each, root cause first: a bot that loses before costs and is then halted failed for lack of edge, not for its drawdown.' }),
      h('div', { class: 'v3-fails' }, block('+JEV2 bots', fm.jev_bots), block('matched controls', fm.field_controls),
        block('all ' + (((s.counts || {}).controls_scanned) || '') + ' scanned controls', fm.all_controls)));
  },

  pairsCard(s) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'pair', cell: (p) => [h('a', { class: 'mono v3-link', href: '#', text: p.strategy_id + '-' + p.coin + '-' + p.tf, onclick: (e) => { e.preventDefault(); this.openBot(p.jev_key); } }),
        this.extended(p.pair_id) ? [' ', pill('EXT', 'ghost', 'added by protocol amendment 1')] : null] },
      { h: 'control', num: true, cell: (p) => num((p.control || {}).net) },
      { h: '+JEV2', num: true, cell: (p) => num((p.jev || {}).net) },
      { h: 'Δ vs ctrl', num: true, cell: (p) => num(p.delta_vs_control) },
      { h: 'always-take', num: true, cell: (p) => num((p.always_take || {}).net) },
      { h: 'always-skip', num: true, cell: () => h('span', { class: 'num', text: '0.00' }) },
      { h: 'random p50 / p90', num: true, cell: (p) => { const r = p.random || {}; return nfix(r.p50, 2, true) + ' / ' + nfix(r.p90, 2, true); } },
      { h: 'rand p', num: true, title: 'share of the 20 random-filter seeds that did at least as well as Jev', cell: (p) => nfix((p.random || {}).p_value, 2) },
      { h: 'accepted W/L', num: true, cell: (p) => { const a = p.jev_accepted || {}; return (a.winners ?? 0) + ' / ' + (a.losers ?? 0); } },
      { h: 'Jev skipped W/L', num: true, cell: (p) => { const a = p.jev_skipped || {}; return (a.winners ?? 0) + ' / ' + (a.losers ?? 0); } },
      { h: 'refused after resize', num: true, title: 'Jev chose a smaller size (DEFENSIVE 0.5×) and the RiskManager refused it — usually below the exchange minimum at 20 USDT',
        cell: (p) => String((p.jev_refused || {}).n ?? 0) },
      { h: 'AUC [95%]', num: true, cell: (p) => { const a = p.jev_auc || {}; return isNum(a.auc) ? a.auc.toFixed(2) + ' [' + nfix(a.low, 2) + ', ' + nfix(a.high, 2) + ']' : DASH; } },
      { h: 'status', cell: (p) => pill(p.state === 'ADVANCE' ? 'ADVANCE' : p.state === 'INSUFFICIENT_AGGRESSIVE_PARTICIPATION' ? 'LOW PARTICIPATION' : p.state, V3_STATE_KIND[p.state] || 'ghost') },
    ], s.pairs || [], { empty: 'no Jev pairs ran' });
    const b = s.baselines || {};
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'CONTROL vs +JEV2 — against every baseline' })),
      h('p', { class: 'sub', text: 'Same data, signal, wallet, risk rules, fees and execution; only Jev differs. Jev adds value only if it beats its control, '
        + 'always-skip (never trading = 0), always-take and a random filter that accepts the same share of candidates with the same size mix.' }),
      h('div', { class: 'tiles small' },
        tile('pairs', String(b.pairs ?? 0)), tile('Jev total', fmtMoney(b.jev_total, true), signClass(b.jev_total)),
        tile('controls total', fmtMoney(b.control_total, true), signClass(b.control_total)),
        tile('always-take total', fmtMoney(b.always_take_total, true), signClass(b.always_take_total)),
        tile('random median total', fmtMoney(b.random_median_total, true), signClass(b.random_median_total)),
        tile('Jev beats control', (b.jev_beats_control ?? 0) + ' / ' + (b.pairs ?? 0)),
        tile('beats always-skip', (b.jev_beats_skip ?? 0) + ' / ' + (b.pairs ?? 0)),
        tile('beats random p90', (b.jev_beats_random_p90 ?? 0) + ' / ' + (b.pairs ?? 0))),
      h('div', { class: 'tablewrap' }, t));
  },

  jevCard(s) {
    const j = s.jev_pooled || {};
    if (!j.decisions) return h('div', { class: 'card' }, h('h3', { text: 'JEV V2' }), h('p', { class: 'sub', text: 'no Jev V2 decisions in this run' }));
    const fin = j.final || {};
    const auc = j.auc || {};
    const lat = j.latency_ms || {};
    const cal = h('table', { class: 'tbl' });
    renderTable(cal, [{ h: 'P(win) band', cell: (r) => r.bucket }, { h: 'candidates', num: true, cell: (r) => String(r.n) },
      { h: 'predicted', num: true, cell: (r) => pc(r.predicted, 0) }, { h: 'realized win rate', num: true, cell: (r) => pc(r.realized_win_rate, 0) },
      { h: 'mean R', num: true, cell: (r) => nfix(r.mean_r, 2, true) }], j.calibration || []);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'JEV V2 — what it decided and whether it could tell' }),
      pill('JEV_POLICY_V2', 'accent'), pill('JEV_PROMPT_V2', 'ghost')),
      h('div', { class: 'v3-levels' }, ...V3_LEVELS.map((l) => h('div', { class: 'v3-level ' + l.toLowerCase() },
        h('b', { text: String(fin[l] || 0) }), h('span', { text: l }), h('small', { text: pc((fin[l] || 0) / j.decisions, 0) })))),
      h('div', { class: 'tiles small' },
        tile('decisions', String(j.decisions)), tile('skip rate', pc(j.skip_rate, 1), (j.skip_rate || 0) > 0.8 ? 'down' : ''),
        tile('ATTACK rate', pc(j.attack_rate, 1)),
        tile('AUC P(win)', isNum(auc.auc) ? auc.auc.toFixed(3) : DASH, isNum(auc.low) && auc.low > 0.5 ? 'up' : '',
          isNum(auc.low) ? '95% [' + auc.low.toFixed(3) + ', ' + auc.high.toFixed(3) + '] · n ' + ((auc.wins || 0) + (auc.losses || 0)) : ''),
        tile('accepted', ((j.accepted || {}).winners ?? 0) + ' W / ' + ((j.accepted || {}).losers ?? 0) + ' L', '', 'mean ' + nfix((j.accepted || {}).mean_r, 2, true) + 'R'),
        tile('Jev SKIP (counterfactual)', ((j.skipped || {}).winners ?? 0) + ' W / ' + ((j.skipped || {}).losers ?? 0) + ' L', '', 'mean ' + nfix((j.skipped || {}).mean_r, 2, true) + 'R'),
        tile('refused after Jev resize', String((j.refused_after_resize || {}).n ?? 0), (j.refused_after_resize || {}).n ? 'warn' : '',
          'half size below the exchange minimum · mean ' + nfix((j.refused_after_resize || {}).mean_r, 2, true) + 'R'),
        tile('not traded (all causes)', pc(j.not_traded_rate, 1)),
        tile('latency p50 / p95 / p99', (lat.p50 ?? DASH) + ' / ' + (lat.p95 ?? DASH) + ' / ' + (lat.p99 ?? DASH) + ' ms'),
        tile('API errors', String(j.errors ?? 0), (j.error_rate || 0) > 0.02 ? 'down' : '', pc(j.error_rate, 2)),
        tile('cost', isNum(j.cost_usd) ? '$' + j.cost_usd.toFixed(3) : DASH),
        tile('move during latency', (j.latency_model || {}).n ? nfix(j.latency_model.move_bps_p50, 2) + ' / ' + nfix(j.latency_model.move_bps_p95, 2)
          + ' / ' + nfix(j.latency_model.move_bps_p99, 2) + ' bps' : DASH, '', 'MODELLED p50 / p95 / p99 (1σ)')),
      h('p', { class: 'sub', text: 'Latency cannot move a historical fill (orders fill at the next 1m bar open either way). The move during latency is MODELLED '
        + 'from each decision’s own 1m realized volatility × √(request latency / 60 s); the real latency slippage is measured on the live forward shadow.' }),
      h('div', { class: 'tablewrap' }, cal));
  },

  matrixCard(title, m, rowLabel) {
    const tfs = ['1m', '3m', '5m', '15m', '30m'];
    const t = h('table', { class: 'tbl v3-matrix' });
    const head = h('tr', null, h('th', { text: rowLabel }), ...tfs.map((tf) => h('th', { class: 'num', text: tf + (tf === '1m' ? ' (exp.)' : tf === '30m' ? ' (bench)' : '') })));
    const body = h('tbody');
    for (const [rk, cells] of Object.entries(m || {})) {
      body.append(h('tr', null, h('td', { text: rk }), ...tfs.map((tf) => {
        const c = (cells || {})[tf];
        if (!c) return h('td', { class: 'num sub', text: DASH });
        return h('td', { class: 'num v3-cell ' + signClass(c.net_return_mean), title: c.bots + ' bots · ' + c.trades + ' trades · PF ' + v3pf(c.pf)
          + ' · mean DD ' + pc(c.max_dd_mean, 0) + ' · cost/edge ' + nfix(c.cost_ratio, 2) + ' · gross-positive ' + c.gross_positive + '/' + c.bots },
        h('b', { text: pc(c.net_return_mean, 1, true) }), h('small', { text: ' ' + nfix(c.expectancy_r, 2, true) + 'R · ' + c.profitable + '/' + c.bots }));
      })));
    }
    t.append(h('thead', null, head), body);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: title.toUpperCase() })),
      h('p', { class: 'sub', text: 'Every scanned control (no Jev). Cell: mean net return · mean expectancy · profitable bots. Hover for PF, trades, drawdown and cost ratio.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  timeframeCard(s) {
    const tv = s.timeframe_verdict || {};
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'family', cell: ([sid]) => sid },
      ...['3m', '5m', '15m', '30m'].map((tf) => ({ h: tf, num: true, cell: ([, v]) => { const c = (v.by_tf || {})[tf]; return c ? pc(c.mean_net_return, 1, true) + ' · ' + c.profitable_active + ' ok' : DASH; } })),
      { h: 'best timeframe', cell: ([, v]) => v.best_tf || DASH }], Object.entries(tv));
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'WHICH TIMEFRAME WORKS?' })),
      h('p', { class: 'sub', text: 'Smaller is not assumed better. "ok" = profitable after costs with enough trades.' }), h('div', { class: 'tablewrap' }, t));
  },

  scanCard(s) {
    const d = h('details', { class: 'v3-more' }, h('summary', { text: 'All scanned controls (' + (s.leaderboard_scan || []).length + ') and the experimental 1m bots' }));
    d.addEventListener('toggle', () => {
      if (!d.open || d.dataset.built) return;
      d.dataset.built = '1';
      d.append(this.lbTable(s.leaderboard_scan || [], false));
      if ((s.experimental_1m || []).length) d.append(h('h4', { text: 'EXPERIMENTAL 1m signal bots (execution: next-1m-open proxy, never advanced)' }),
        this.lbTable(s.experimental_1m, false));
      const f = s.field || {};
      d.append(h('h4', { text: 'Field selection (activity only)' }), h('p', { class: 'sub', text: f.rule || '' }),
        h('p', { class: 'sub', text: 'empty slots: ' + ((f.empty_slots || []).map((e) => e.strategy_id + ' ' + e.timeframe
          + (e.best ? ' (best ' + e.best.coin + ' ' + e.best.entries + ' < ' + e.need + ')' : '')).join(', ') || 'none') }));
    });
    return h('div', { class: 'card' }, d);
  },

  protocolCard(d) {
    const c = d.config || {};
    const g = c.gates || {};
    const part = c.participation || {};
    return h('div', { class: 'card' }, h('details', { class: 'v3-more' }, h('summary', { text: 'Protocol, gates, Jev V2 policy and fingerprints (pre-registered)' }),
      h('dl', { class: 'kv' },
        h('dt', { text: 'venue' }), h('dd', { text: d.venue_label || DASH }),
        h('dt', { text: 'window' }), h('dd', { text: (c.trade_from || '') + ' → ' + (c.trade_to || '') + ' · ' + (c.dataset_role || '') }),
        h('dt', { text: 'coins' }), h('dd', { text: (c.coins || []).join(' ') }),
        h('dt', { text: 'risk profile' }), h('dd', { text: 'AGGRESSIVE_V3: normal 1.0% · strong 1.5% · ATTACK up to 2.0% (hard cap) · 20x ceiling, used only as needed · RiskManager floor 75%, halt at 30% drawdown' }),
        h('dt', { text: 'cost gate' }), h('dd', { text: 'EDGE_COST_RATIO ≥ ' + (c.cost_gate_min_ratio ?? 2) + ' to consider; ≥ ' + (c.attack_min_edge_to_cost ?? 3) + ' for STRONG/ATTACK sizing and Jev ATTACK' }),
        h('dt', { text: 'Jev V2 policy' }), h('dd', { text: 'Jev picks SKIP 0× · DEFENSIVE 0.5× · NORMAL 1.0× · ATTACK 1.5× (2.0× when P(ATTACK) ≥ 0.6); ATTACK needs edge/cost ≥ 3; an error is SKIP; never bypasses the RiskManager' }),
        h('dt', { text: 'participation' }), h('dd', { text: Object.entries(part).map(([tf, n]) => tf + ' ≥ ' + n).join(' · ') + ' closed trades; +JEV also skip ≤ ' + pc(g.max_skip_rate, 0) }),
        h('dt', { text: 'gates' }), h('dd', { text: 'net > 0 · ExpR > 0 · PF ≥ ' + (g.min_profit_factor ?? 1.1) + ' · DD ≤ ' + pc(g.max_drawdown_pct, 0) + ', no halt · no liquidation · positive without the best 3 trades; '
          + '+JEV: beats control, always-skip and the random-filter ' + Math.round((g.random_percentile || 0.9) * 100) + 'th percentile, AUC 95% CI above 0.50, errors ≤ ' + pc(g.max_error_rate, 0) + ', p95 latency ≤ ' + (g.max_latency_p95_ms ?? 2000) + ' ms' }),
        h('dt', { text: 'fingerprints' }), h('dd', { class: 'mono keep-case', text: Object.entries(c.strategy_fingerprints || {}).map(([k, v]) => k + ' ' + v).join(' · ')
          + ' · ' + Object.entries(c.jev_fingerprints || {}).map(([k, v]) => k + ' ' + v).join(' · ') }),
        h('dt', { text: 'dataset' }), h('dd', { class: 'mono keep-case', text: (c.dataset_fingerprint || DASH) + ' · rules ' + (c.rules_source || '') }))));
  },

  // ---- one bot -----------------------------------------------------------------------------------------
  async openBot(key, silent) {
    this.botKey = key;
    const card = $('#v3-bot-card');
    if (!card) return;
    card.hidden = false;
    clear(card).append(h('p', { class: 'sub', text: 'loading ' + key + '…' }));
    try { this.bot = await api(this.base + '/v3/bots/' + encodeURIComponent(key)); }
    catch (e) { clear(card).append(h('p', { class: 'msg err', text: (e && e.message) || String(e) })); return; }
    if (!silent && this.pathBase) history.replaceState(null, '', this.pathBase + '/bot/' + encodeURIComponent(key));
    this.renderBot();
    card.scrollIntoView({ block: 'start' });
  },
  closeBot() {
    this.botKey = ''; this.bot = null;
    const card = $('#v3-bot-card'); if (card) { clear(card); card.hidden = true; }
    if (this.pathBase) history.replaceState(null, '', this.pathBase);
  },
  renderBot() {
    const card = $('#v3-bot-card');
    const b = this.bot;
    if (!card || !b || !b.ok) return;
    clear(card);
    const bot = b.bot || {};
    const idn = bot.identity || {};
    const a = b.analysis || {};
    const sum = a.summary || {};
    card.append(h('div', { class: 'card-head' },
      h('h2', { class: 'keep-case', text: idn.strategy_id + ' · ' + idn.coin + ' · ' + idn.timeframe + ' · ' + (bot.role === 'JEV' ? 'JEV2' : bot.role) }),
      pill(sum.result || bot.state || DASH, V3_STATE_KIND[a.state] || 'ghost'),
      h('span', { class: 'spacer' }), h('button', { type: 'button', class: 'btn ghost', text: 'close', onclick: () => this.closeBot() })));
    card.append(h('p', { class: 'sub mono keep-case', text: bot.key + ' · fingerprint ' + (bot.fingerprint || DASH) + ' · context '
      + (idn.context_timeframes || []).join(' + ') + ' · execution ' + (idn.execution_timeframe || '1m') + ' · ' + (idn.profile || '') + ' · ' + (b.venue_label || '') }));
    const tabs = ['overview', 'trades', 'analyzer', 'jev', 'validation'];
    card.append(h('div', { class: 'subtabs' }, ...tabs.map((t) => h('button', { type: 'button', class: t === this.tab ? 'active' : '',
      text: t[0].toUpperCase() + t.slice(1), onclick: () => { this.tab = t; this.renderBot(); } }))));
    const body = h('div', { class: 'v3-tab' });
    card.append(body);
    if (this.tab === 'overview') this.overview(body, b, a);
    else if (this.tab === 'trades') this.trades(body, b);
    else if (this.tab === 'analyzer') this.analyzer(body, b, a);
    else if (this.tab === 'jev') this.jev(body, b, a);
    else this.validation(body, b, a);
  },

  overview(body, b, a) {
    const m = (b.bot || {}).metrics || {};
    const e = a.edge || {}, c = a.cost || {}, act = a.activity || {}, j = a.jev || {}, sum = a.summary || {};
    body.append(h('div', { class: 'v3-diagnosis' },
      h('div', null, h('span', { text: 'EDGE' }), h('b', { class: signClass(e.net_expectancy_r), text: nfix(e.net_expectancy_r, 2, true) + 'R' }),
        h('small', { text: 'gross ' + nfix(e.gross_expectancy_r, 2, true) + 'R' })),
      h('div', null, h('span', { text: 'COST' }), h('b', { text: nfix(c.round_trip_cost_bps, 1) + ' bps' }), h('small', { text: 'edge/cost ' + nfix(c.realized_edge_to_cost, 2) })),
      h('div', null, h('span', { text: 'ACTIVITY' }), h('b', { text: nfix(act.trades_per_day, 2) + ' trades/day' }), h('small', { text: (act.trades ?? 0) + ' trades' })),
      h('div', null, h('span', { text: 'JEV TAKE' }), h('b', { text: j.decisions ? pc(j.acceptance_rate, 0) : DASH }), h('small', { text: j.decisions ? 'AUC ' + nfix((j.auc || {}).auc, 2) : 'no Jev' })),
      h('div', null, h('span', { text: 'DRAWDOWN' }), h('b', { text: pc(e.max_drawdown_pct, 1) }), h('small', { text: 'PF ' + v3pf(e.net_pf) })),
      h('div', { class: 'diag ' + (a.failure_mode === 'ROBUST' ? 'up' : 'down') }, h('span', { text: 'DIAGNOSIS' }),
        h('b', { text: sum.primary_issue || a.failure_mode || DASH }))));
    const eq = b.equity || [];
    if (eq.length > 1 && typeof Candidates !== 'undefined' && Candidates.spark) body.append(Candidates.spark(eq, 20));
    body.append(h('div', { class: 'tiles small' },
      tile('net', fmtMoney(m.net_profit, true), signClass(m.net_profit), pc(m.net_return_pct, 1, true)),
      tile('gross (decision prices)', fmtMoney(m.gross_pnl, true), signClass(m.gross_pnl)),
      tile('fees', fmtMoney(-(m.fees_paid || 0), true), 'down'), tile('slippage', fmtMoney(-(m.slippage_cost || 0), true), 'down'),
      tile('funding', fmtMoney(m.funding_paid, true), signClass(m.funding_paid)),
      tile('win rate', pc(m.win_rate, 0)), tile('trades', String(m.trades ?? 0))));
    if ((sum.hypothesis || []).length) body.append(h('div', { class: 'banner accent' }, h('b', { text: 'NEXT VERSION HYPOTHESIS ' }),
      h('span', { text: sum.hypothesis.join(' · ') + ' — proposed only; it would be V3.1, developed on DEVELOPMENT data and judged on unseen data.' })));
    if (b.pair) body.append(this.pairCompare(b.pair));
  },

  pairCompare(p) {
    const side = (label, x) => (x ? h('tr', null, h('td', { text: label }), h('td', { class: 'num ' + signClass(x.gross), text: fmtMoney(x.gross, true) }),
      h('td', { class: 'num ' + signClass(x.net), text: fmtMoney(x.net, true) }), h('td', { class: 'num', text: String(x.trades ?? DASH) }),
      h('td', { class: 'num', text: fmtMoney(-(x.fees || 0), true) }), h('td', { class: 'num', text: pc(x.max_dd, 1) }),
      h('td', { class: 'num', text: nfix(x.exp_r, 3, true) })) : null);
    const t = h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ...['', 'gross edge', 'net edge', 'trades', 'fees', 'DD', 'ExpR'].map((x) => h('th', { class: x ? 'num' : '', text: x })))),
      h('tbody', null, side('CONTROL', p.control), side('+JEV2', p.jev), side('ALWAYS-TAKE', p.always_take),
        h('tr', null, h('td', { text: 'ALWAYS-SKIP' }), h('td', { class: 'num', text: '0.00' }), h('td', { class: 'num', text: '0.00' }), h('td', { class: 'num', text: '0' }),
          h('td', { class: 'num', text: '0.00' }), h('td', { class: 'num', text: '0.0%' }), h('td', { class: 'num', text: DASH }))));
    const acc = p.jev_accepted || {}, sk = p.jev_skipped || {}, rnd = p.random || {};
    return h('div', { class: 'v3-pair' }, h('h4', { text: 'CONTROL vs JEV — ' + p.strategy_id + ' ' + p.coin + ' ' + p.tf }), h('div', { class: 'tablewrap' }, t),
      h('div', { class: 'tiles small' },
        tile('Jev accepted winners', String(acc.winners ?? 0), 'up'), tile('Jev accepted losers', String(acc.losers ?? 0), 'down'),
        tile('Jev skipped winners', String(sk.winners ?? 0), sk.winners ? 'down' : ''), tile('Jev skipped losers', String(sk.losers ?? 0), sk.losers ? 'up' : ''),
        tile('refused after resize', String((p.jev_refused || {}).n ?? 0), (p.jev_refused || {}).n ? 'warn' : '', 'DEFENSIVE half size not a legal order'),
        tile('random filter p50 / p90', nfix(rnd.p50, 2, true) + ' / ' + nfix(rnd.p90, 2, true), '', 'Jev percentile ' + pc(rnd.jev_percentile, 0)),
        tile('permutation p', nfix((p.permutation || {}).p, 3), '', 'trade-level selection test')));
  },

  trades(body, b) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'entry (UTC)', cell: (r) => fmtTs(r.entry_ts) }, { h: 'side', cell: (r) => pill(r.side, r.side) },
      { h: 'hold', num: true, cell: (r) => fmtDur(r.hold_s) }, { h: 'gross', num: true, cell: (r) => num(r.gross) },
      { h: 'fees', num: true, cell: (r) => fmtMoney(-(r.fees || 0), true) }, { h: 'slip', num: true, cell: (r) => fmtMoney(-(r.slippage || 0), true) },
      { h: 'net', num: true, cell: (r) => num(r.net) }, { h: 'R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.r), text: nfix(r.r, 2, true) }) },
      { h: 'exit', cell: (r) => r.exit_kind }, { h: 'quality', num: true, cell: (r) => nfix(r.quality, 2) },
      { h: 'edge/cost', num: true, cell: (r) => nfix(r.e2c, 1) }, { h: 'sizing', cell: (r) => (r.jev_level ? 'JEV ' + r.jev_level + ' ×' + nfix(r.jev_mult, 1) : (r.state || '') + ' ' + (r.tier || '')) },
      { h: 'lev', num: true, cell: (r) => (r.leverage ? r.leverage + 'x' : DASH) }], (b.trades || []).slice().reverse(), { empty: 'no trades' });
    body.append(h('p', { class: 'sub', text: (b.trades || []).length + ' closed trades, newest first. Gross is at decision prices; net = gross − fees − slippage + funding.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  analyzer(body, b, a) {
    const kv = (obj, keys) => h('dl', { class: 'kv' }, ...keys.flatMap(([k, label, f]) => [h('dt', { text: label }), h('dd', { text: f ? f(obj[k]) : String(obj[k] ?? DASH) })]));
    const act = a.activity || {}, e = a.edge || {}, c = a.cost || {};
    body.append(h('div', { class: 'v3-an' },
      section('ACTIVITY', kv(act, [['candidates_per_day', 'candidates/day', (v) => nfix(v, 2)], ['jev_calls_per_day', 'Jev calls/day', (v) => nfix(v, 2)],
        ['accepted_per_day', 'accepted/day', (v) => nfix(v, 2)], ['trades_per_day', 'trades/day', (v) => nfix(v, 2)],
        ['trades_per_30d', 'trades per 30 days', (v) => nfix(v, 1)], ['avg_hold_min', 'average hold (min)', (v) => nfix(v, 0)],
        ['median_gap_min', 'median time between trades (min)', (v) => nfix(v, 0)], ['below_exchange_minimum', 'below exchange minimum'],
        ['cost_gate_rejected', 'cost-gate rejected']])),
      section('EDGE', kv(e, [['gross_expectancy_r', 'gross expectancy', (v) => nfix(v, 3, true) + 'R'], ['net_expectancy_r', 'net expectancy', (v) => nfix(v, 3, true) + 'R'],
        ['gross_expectancy_usdt', 'gross per trade', (v) => fmtMoney(v, true)], ['net_expectancy_usdt', 'net per trade', (v) => fmtMoney(v, true)],
        ['gross_pf', 'gross PF', v3pf], ['net_pf', 'net PF', v3pf], ['win_rate', 'win rate', (v) => pc(v, 0)],
        ['gross_bps_per_trade', 'gross bps per trade', (v) => nfix(v, 1)], ['net_without_top3', 'net without best 3', (v) => fmtMoney(v, true)],
        ['largest_winner_share', 'largest winner share of profit', (v) => pc(v, 0)]])),
      section('COST', kv(c, [['fees', 'fees', (v) => fmtMoney(-(v || 0), true)], ['slippage', 'spread + slippage', (v) => fmtMoney(-(v || 0), true)],
        ['funding', 'funding', (v) => fmtMoney(v, true)], ['avg_cost_per_trade', 'average cost per trade', (v) => fmtMoney(v)],
        ['round_trip_cost_bps', 'round trip (bps of notional)', (v) => nfix(v, 1)], ['cost_share_of_gross_winners', 'cost as share of gross winners', (v) => pc(v, 0)],
        ['cost_to_edge', 'cost-to-edge ratio', (v) => nfix(v, 2)], ['realized_edge_to_cost', 'realized edge/cost', (v) => nfix(v, 2)],
        ['planned_edge_to_cost_median', 'planned edge/cost (median at entry)', (v) => nfix(v, 1)]]))));
    const bucketTable = (rows, label) => {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [{ h: label, cell: (r) => r.bucket || r[0] }, { h: 'trades', num: true, cell: (r) => String(r.n ?? (r[1] || {}).n) },
        { h: 'win rate', num: true, cell: (r) => pc(r.win_rate ?? (r[1] || {}).win_rate, 0) },
        { h: 'mean R', num: true, cell: (r) => nfix(r.mean_r ?? (r[1] || {}).mean_r, 2, true) },
        { h: 'net', num: true, cell: (r) => num(r.net ?? (r[1] || {}).net) }], rows);
      return h('div', { class: 'tablewrap' }, t);
    };
    body.append(h('div', { class: 'v3-an' },
      section('SIGNAL QUALITY', bucketTable(a.quality || [], 'quality band')),
      section('REGIME — trend', bucketTable(Object.entries((a.regime || {}).trend || {}), 'regime')),
      section('REGIME — volatility', bucketTable(Object.entries((a.regime || {}).vol || {}), 'regime')),
      section('TIME OF DAY (UTC session)', bucketTable(Object.entries(a.sessions || {}), 'session'))));
    const gt = h('table', { class: 'tbl' });
    renderTable(gt, [{ h: 'gate', cell: (g) => g.name }, { h: 'result', cell: (g) => pill(g.ok ? 'PASS' : 'FAIL', g.ok ? 'up' : 'down') },
      { h: 'actual', cell: (g) => String(g.actual ?? DASH) }, { h: 'needs', cell: (g) => g.threshold }], a.gates || []);
    body.append(section('GATES', h('div', { class: 'tablewrap' }, gt)),
      h('div', { class: 'banner ' + (a.failure_mode === 'ROBUST' ? 'up' : 'down') }, h('b', { text: 'PRIMARY FAILURE: ' + ((a.summary || {}).primary_issue || a.failure_mode || DASH) }),
        h('span', { text: ' · ' + ((a.summary || {}).hypothesis || []).join(' · ') })));
  },

  jev(body, b, a) {
    const j = a.jev;
    if (!j) {
      const twin = (b.siblings || []).find((k) => k.includes('JEV2'));
      body.append(h('p', { class: 'sub', text: 'This bot runs without Jev.' + (twin ? ' Its +JEV2 twin: ' : '') }),
        twin ? h('a', { class: 'mono v3-link', href: '#', text: shortV3(twin), onclick: (e) => { e.preventDefault(); this.openBot(twin); } }) : null);
      return;
    }
    const fin = j.final || {};
    body.append(h('div', { class: 'v3-levels' }, ...V3_LEVELS.map((l) => h('div', { class: 'v3-level ' + l.toLowerCase() },
      h('b', { text: String(fin[l] || 0) }), h('span', { text: l }), h('small', { text: pc((fin[l] || 0) / (j.decisions || 1), 0) })))));
    const auc = j.auc || {};
    body.append(h('div', { class: 'tiles small' },
      tile('decisions', String(j.decisions)), tile('skip rate', pc(j.skip_rate, 1)), tile('ATTACK rate', pc(j.attack_rate, 1)),
      tile('AUC P(win)', nfix(auc.auc, 3), isNum(auc.low) && auc.low > 0.5 ? 'up' : '', isNum(auc.low) ? '[' + auc.low.toFixed(2) + ', ' + auc.high.toFixed(2) + '] n ' + ((auc.wins || 0) + (auc.losses || 0)) : ''),
      tile('AUC by action', nfix((j.auc_level || {}).auc, 3)), tile('P(win) median', nfix((j.p_win_quartiles || [])[2], 2)),
      tile('latency p50/p95/p99', ((j.latency_ms || {}).p50 ?? DASH) + '/' + ((j.latency_ms || {}).p95 ?? DASH) + '/' + ((j.latency_ms || {}).p99 ?? DASH) + ' ms'),
      tile('errors', String(j.errors ?? 0), '', pc(j.error_rate, 2)), tile('permutation p', nfix((j.permutation || {}).p, 3))));
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'signal (UTC)', cell: (d) => fmtTs(d.ts) }, { h: 'side', cell: (d) => pill(d.side, d.side) },
      { h: 'P(win)', num: true, cell: (d) => pc(d.p_win, 0) }, { h: 'Jev chose', cell: (d) => d.chosen || (d.error ? 'ERROR ' + d.error : DASH) },
      { h: 'final', cell: (d) => pill(d.level || DASH, d.level === 'SKIP' ? 'down' : d.level === 'ATTACK' ? 'up' : 'accent') },
      { h: '×', num: true, cell: (d) => nfix(d.mult, 1) }, { h: 'edge/cost', num: true, cell: (d) => nfix(d.e2c, 1) },
      { h: 'latency', num: true, cell: (d) => (isNum(d.latency_ms) ? d.latency_ms + ' ms' : d.source) },
      { h: 'outcome', cell: (d) => (d.outcome ? (d.outcome === 'SHADOW' ? (String(d.result || '').startsWith('RISK_REJECTED') ? 'refused at the smaller size → ' : 'skipped → ') : 'taken → ')
        + nfix(d.r, 2, true) + 'R' : DASH) }],
      (b.decisions || []).slice().reverse(), { empty: 'no decisions' });
    body.append(h('p', { class: 'sub', text: (b.decisions || []).length + ' Jev V2 decisions, newest first; a skipped candidate\'s outcome is the shadow trade the engine simulated at the control\'s size.' }),
      h('div', { class: 'tablewrap' }, t));
    if (b.pair) body.append(this.pairCompare(b.pair));
  },

  validation(body, b, a) {
    const adv = a.state === 'ADVANCE';
    const stages = [['DISCOVERY', adv ? 'PASS' : a.state === 'EXPERIMENTAL' ? 'EXPERIMENTAL' : 'FAIL'],
      ['HOLDOUT TEST', adv ? 'NEXT (2026-05 → 2026-08, not yet run)' : 'NOT ELIGIBLE'], ['MULTI-YEAR', 'PENDING'], ['MONTE CARLO', 'PENDING'],
      ['STRESS', 'PENDING'], ['FORWARD SHADOW', 'PENDING'], ['QUALIFIED', 'PENDING'], ['LIVE_CANDIDATE', 'PENDING'], ['MANUAL GO LIVE', 'operator only']];
    body.append(h('ol', { class: 'v3-pipeline' }, ...stages.map(([s, st]) => h('li', { class: st.startsWith('PASS') ? 'up' : st.startsWith('FAIL') || st === 'NOT ELIGIBLE' ? 'down' : '' },
      h('b', { text: s }), h('span', { text: st })))),
      h('p', { class: 'sub', text: 'Discovery on DEVELOPMENT data can only earn a bot the right to be tested on data it has never seen. Nothing here trades real money; live capital always needs an operator.' }));
  },
};
