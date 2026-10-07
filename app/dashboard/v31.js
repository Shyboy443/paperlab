/* PaperLab - V3.1 AGGRESSIVE EDGE (docs/V31_PROTOCOL.md).

   The Aggressive Arena for V3.1: the DEVELOPMENT run and, once it exists, the pre-registered TEST run,
   read from /api[/public]/competition/v31 (REST for history; the live connection only refreshes the
   header). Timeframe tabs (3m / 5m / 15m / 30m), SMALL-TIMEFRAME VIABILITY, the leaderboard, CONTROL vs
   +JEV3 against every baseline, JEV SELECTION ALPHA, ATTACK EFFECTIVENESS, the edge calibration, the
   PARTICIPATION FUNNEL, holding / exit diagnostics, maker vs taker and capacity. textContent only. */
'use strict';

const V31_LEVELS = ['SKIP', 'TAKE', 'ATTACK', 'STRONG_ATTACK'];
const V31_TFS = ['3m', '5m', '15m', '30m'];
const V31_STATE_KIND = { ADVANCE: 'up', FAIL: 'down', LOW_ACTIVITY_EDGE: 'warn', INSUFFICIENT_SAMPLE: 'ghost' };
const V31_FAIL_KIND = { ROBUST: 'up', ENTRY_HAS_NO_EDGE: 'down', EXIT_DESTROYS_EDGE: 'down', HOLD_TOO_SHORT: 'warn',
  COST_DESTROYED: 'down', TOO_LITTLE_ACTIVITY: 'warn', JEV_NO_SELECTION_ALPHA: 'warn', PROFIT_CONCENTRATED: 'warn',
  MIN_NOTIONAL_LIMITED: 'warn', DRAWDOWN_FAILURE: 'down', LIQUIDATION: 'down', MARGINAL_EDGE: 'warn',
  JEV_OVER_FILTERING: 'warn', INSUFFICIENT_SAMPLE: 'ghost', NO_TRADES: 'ghost' };
const v31pc = (v, d = 1, signed = false) => (isNum(v) ? (signed && v > 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : DASH);
const v31n = (v, d = 2, signed = false) => (isNum(v) ? (signed && v > 0 ? '+' : '') + v.toFixed(d) : DASH);
const v31pf = (v) => (!isNum(v) ? DASH : v >= 999 ? '∞' : v.toFixed(2));
const v31key = (k) => String(k || '').replace('@20x', '');
const v31mix = (m) => {
  const e = Object.entries(m || {});
  if (!e.length) return DASH;
  return e.map(([k, v]) => (k === 'STRONG_ATTACK' ? 'S' : k[0]) + ' ' + v31pc(v, 0)).join(' · ');
};

const V31 = {
  base: '/api/competition', data: null, bot: null, botKey: '', role: '', tf: '', tab: 'overview', loading: false,
  pathBase: '', headerSig: '',

  async load() {
    if (this.loading) return;
    this.loading = true;
    try {
      this.data = await api(this.base + '/v31');
      if (!this.role) this.role = (this.data.test && this.data.test.summary) ? 'TEST' : 'DEVELOPMENT';
      this.render();
      if (this.botKey) await this.openBot(this.botKey, true);
    } catch (e) { if (e.status !== 401) this.fail(e); }
    finally { this.loading = false; }
  },
  fail(e) {
    const g = $('#v31-grid');
    if (g) clear(g).append(h('div', { class: 'card' }, h('h2', { text: 'could not load the V3.1 arena' }),
      h('p', { class: 'msg err', text: (e && e.message) || String(e) })));
  },
  block() { const d = this.data || {}; return this.role === 'TEST' ? d.test : d.dev; },
  extended(pairId) {
    const f = (this.block() || {}).field || {};
    return (f.pairs || []).some((p) => p.extended && 'v31pair:' + p.strategy_id + '-' + p.coin + '-' + p.timeframe === pairId);
  },
  fieldNote() {
    const f = (this.block() || {}).field || {};
    const pre = f.preregistered, am = f.amendment;
    if (!pre && !am) return null;
    return h('div', { class: 'banner warn' }, h('b', { text: 'FIELD: ' }),
      h('span', { text: 'the pre-registered rule qualified ' + ((pre || {}).pairs ?? DASH) + ' Jev pairs → ' + ((pre || {}).status || DASH)
        + (am ? '. Protocol amendment ' + am.id + ' (' + (am.basis || 'activity counts only') + '): ' + (am.rule || '') + ' → '
          + (f.n ?? (f.pairs || []).length) + ' pairs, ' + am.added + ' EXTENDED. Every gate still applies to them.' : '') }));
  },
  labels() { return (((this.block() || {}).summary || {}).failure_modes || {}).labels || {}; },
  byTf(rows, key = 'tf') { return this.tf ? (rows || []).filter((r) => r[key] === this.tf) : (rows || []); },

  // ---- the arena ---------------------------------------------------------------------------------------
  render() {
    const g = $('#v31-grid');
    if (!g || !this.data) return;
    clear(g);
    g.append(this.headerCard());
    const b = this.block();
    if (!b) { g.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: this.data.note || 'no V3.1 ' + this.role + ' run yet' }))); return; }
    g.append(this.switchCard(), h('div', { class: 'card', id: 'v31-bot-card', hidden: true }));
    const s = b.summary;
    if (!s) { g.append(this.progressCard(b)); return; }
    g.append(this.answersCard(b, s), this.viabilityCard(s), this.leaderboardCard(s), this.pairsCard(s), this.alphaCard(s),
      this.jevCard(s), this.calibrationCard(s), this.funnelCard(s), this.holdingCard(s), this.makerCard(s),
      this.capacityCard(s), this.diagnosesCard(s), this.controlsCard(s), this.protocolCard(b));
  },
  rerender() { const y = window.scrollY; this.render(); window.scrollTo(0, y); if (this.botKey && this.bot) this.renderBot(); },

  headerCard() {
    const d = this.data || {};
    const b = this.block() || {};
    const s = b.summary || {};
    const c = s.counts || {};
    const byTf = c.field_by_tf || {};
    const al = (typeof Alive !== 'undefined' && Alive) || {};
    const stream = al.stream || {};
    const jevHealth = ((al.health || {}).jev || {}).status || ((al.health || {}).shadow || {}).jev_state;
    const run = b.run || {};
    const adv = s.advanced_set;
    const card = h('div', { class: 'card v3-head' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'AGGRESSIVE ARENA' }),
      pill('V3.1 · AGGRESSIVE EDGE', 'accent'), pill(this.role || 'DEVELOPMENT', this.role === 'TEST' ? 'up' : 'ghost'),
      run.status ? pill(String(run.status).toUpperCase(), run.status === 'complete' ? 'up' : 'warn') : null,
      h('span', { class: 'spacer' }), h('span', { class: 'sub mono keep-case', text: run.run_id || '' })));
    card.append(h('p', { class: 'sub', text: (d.venue_label || '') + (s.window ? ' · ' + s.window.from + ' → ' + s.window.to
      + ' (' + s.window.days + ' days, ' + (s.dataset_role || this.role) + ')' : '') }));
    card.append(h('div', { class: 'v3-counters' },
      this.counter(c.jev_bots ?? DASH, 'JEV V3 BOTS', 'accent'), this.counter(c.controls ?? DASH, 'CONTROLS'),
      ...V31_TFS.map((tf) => this.counter(byTf[tf] ?? 0, tf + (tf === '30m' ? ' benchmark' : ''))),
      this.counter(c.random_twins ?? DASH, 'RANDOM TWINS'),
      this.counter(Array.isArray(adv) ? adv.length : (adv || DASH), 'ADVANCED', Array.isArray(adv) && adv.length ? 'accent' : ''),
      h('div', { class: 'v3-counter' }, h('b', null, h('i', { class: 'dot ' + (jevHealth === 'OK' || jevHealth === 'READY' ? 'ok' : 'off') }),
        ' ' + (jevHealth || DASH)), h('span', { text: 'JEV HEALTH' })),
      h('div', { class: 'v3-counter' }, h('b', null, h('i', { class: 'dot ' + (stream.status === 'LIVE' ? 'ok' : 'off') }),
        ' ' + (stream.status || 'OFF')), h('span', { text: (stream.transport === 'sse' ? 'SSE' : 'WS') }))));
    const v3 = d.v3 || {};
    card.append(h('div', { class: 'banner down' }, h('b', { text: 'V3 FROZEN · ADVANCED SET = ' + (v3.advanced_set || 'NONE') }),
      h('span', { text: ' — run ' + (v3.run_id || '') + ' is kept exactly as it finished (' + (v3.doc || '') + '). V3.1 is a new program '
        + 'built from its root-cause analysis, not a re-run of it.' })));
    return card;
  },
  refreshHeader() {
    const old = document.querySelector('#v31-grid .v3-head');
    if (!old || !this.data) return;
    const al = (typeof Alive !== 'undefined' && Alive) || {};
    const sig = [(al.stream || {}).status, (al.stream || {}).transport, ((al.health || {}).jev || {}).status,
      ((al.health || {}).shadow || {}).jev_state, this.role].join('|');
    if (sig === this.headerSig) return;
    this.headerSig = sig;
    const card = this.headerCard();
    card.classList.add('static');
    old.replaceWith(card);
  },
  counter(value, label, cls, sub) {
    return h('div', { class: 'v3-counter ' + (cls || '') }, h('b', { text: String(value) }), h('span', { text: label }),
      sub ? h('small', { text: sub }) : null);
  },

  switchCard() {
    const d = this.data || {};
    const roles = [['DEVELOPMENT', d.dev], ['TEST', d.test]];
    return h('div', { class: 'card v31-switch' },
      h('div', { class: 'subtabs' }, ...roles.map(([r, x]) => h('button', { type: 'button', class: r === this.role ? 'active' : '',
        disabled: !x, text: r + (x ? '' : ' (not run)'), onclick: () => { this.role = r; this.rerender(); } })),
      h('span', { class: 'spacer' }),
      ...[''].concat(V31_TFS).map((tf) => h('button', { type: 'button', class: tf === this.tf ? 'active' : '',
        text: tf ? tf + (tf === '30m' ? ' (bench)' : '') : 'ALL TIMEFRAMES', onclick: () => { this.tf = tf; this.rerender(); } }))));
  },

  progressCard(b) {
    const p = b.progress || {};
    const card = h('div', { class: 'card' }, h('h3', { text: 'RUN IN PROGRESS' }),
      h('p', { class: 'sub', text: 'stage ' + ((b.run || {}).stage || DASH) + ' · ' + (p.bots_done || 0) + ' bots finished'
        + (p.jobs ? ' · current phase ' + p.phase + ' (' + p.jobs + ' jobs)' : '') + '. Results appear when the analysis has run.' }));
    const rows = this.byTf(b.partial || []).slice(0, 400);
    if (rows.length) {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [
        { h: 'bot', cell: (r) => h('span', { class: 'mono', text: v31key(r.key) }) },
        { h: 'role', cell: (r) => pill(r.role, r.role === 'JEV' ? 'accent' : 'ghost') },
        { h: 'signals', num: true, cell: (r) => String(r.signals ?? DASH) },
        { h: 'executed', num: true, cell: (r) => String(r.executed ?? DASH) },
        { h: 'net', num: true, cell: (r) => num(r.net_pnl) }], rows);
      card.append(h('div', { class: 'tablewrap' }, t));
    }
    return card;
  },

  answersCard(b, s) {
    const a = s.answers || {};
    const adv = s.advanced_set;
    const none = !Array.isArray(adv) || !adv.length;
    const test = this.role === 'TEST';
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: test ? 'TEST — THE PRE-REGISTERED HOLDOUT' : 'DEVELOPMENT — WHAT V3.1 FOUND' }),
      pill(test ? (none ? 'ADVANCED SET = NONE' : 'ADVANCED: ' + adv.length) : (adv === 'NONE' ? 'NO DEV SURVIVOR' : String(adv)), none ? 'down' : 'up')),
      h('dl', { class: 'v3-answers' },
        h('dt', { text: 'Is there an aggressive bot with a positive after-cost edge?' }),
        h('dd', { class: String(a.aggressive_edge || '').startsWith('YES') ? 'up' : 'down', text: a.aggressive_edge || DASH }),
        h('dt', { text: 'Does Jev V3 select better than a random action with the same rates?' }), h('dd', { text: a.jev_selection || DASH }),
        h('dt', { text: 'Small timeframes after costs' }), h('dd', { text: a.small_timeframes || DASH }),
        h('dt', { text: test ? 'ADVANCED SET (passed DEVELOPMENT and TEST)' : 'Goes to the pre-registered TEST (2026-05 → 2026-08)' }),
        h('dd', { text: test ? (none ? 'NONE — a correct result, not a failure of the arena' : adv.join(', ')) : ((s.passed_all || []).map(v31key).join(', ') || 'nothing passed every DEVELOPMENT gate') })),
      this.fieldNote());
  },

  viabilityCard(s) {
    const v = s.viability || {};
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'tf', cell: ([tf]) => h('b', { class: tf === this.tf ? 'accent' : '', text: tf + (tf === '30m' ? ' (bench)' : '') }) },
      { h: 'raw candidates', num: true, title: 'every legal candidate the RAW observers simulated (no gate); TEST never observes RAW (no TEST outcome becomes evidence)',
        cell: ([, c]) => (this.role === 'TEST' ? 'n/a' : String((c.raw || {}).candidates ?? DASH)) },
      { h: 'raw net R', num: true, title: 'mean net R of the sequenced RAW candidates: the edge BEFORE the edge gate',
        cell: ([, c]) => (this.role === 'TEST' ? 'n/a' : h('span', { class: 'num ' + signClass((c.raw || {}).net_r), text: v31n((c.raw || {}).net_r, 3, true) })) },
      { h: 'gated signals', num: true, cell: ([, c]) => String(c.signals ?? DASH) },
      { h: 'edge passed', num: true, cell: ([, c]) => String(c.edge_passed ?? DASH) },
      { h: 'trades', num: true, cell: ([, c]) => String(c.trades ?? DASH) },
      { h: 'trades/bot-day', num: true, cell: ([, c]) => v31n(c.trades_per_bot_day, 2) },
      { h: 'gross ExpR', num: true, cell: ([, c]) => h('span', { class: 'num ' + signClass(c.gross_expectancy_r), text: v31n(c.gross_expectancy_r, 3, true) }) },
      { h: 'costs', num: true, cell: ([, c]) => v31n(c.cost_r, 3) + 'R · ' + v31n(c.cost_bps, 1) + ' bps' },
      { h: 'net ExpR', num: true, cell: ([, c]) => h('span', { class: 'num ' + signClass(c.net_expectancy_r), text: v31n(c.net_expectancy_r, 3, true) }) },
      { h: 'PF', num: true, cell: ([, c]) => v31pf(c.pf) },
      { h: 'mean DD', num: true, cell: ([, c]) => v31pc(c.max_dd_mean, 1) },
      { h: 'profitable bots', num: true, cell: ([, c]) => (c.profitable_bots ?? 0) + ' / ' + (c.bots ?? 0) + ' (' + v31pc(c.profitable_pct, 0) + ')' }],
    V31_TFS.filter((tf) => v[tf]).map((tf) => [tf, v[tf]]));
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'SMALL-TIMEFRAME VIABILITY' })),
      h('p', { class: 'sub', text: 'Per timeframe: the RAW edge of every candidate (ungated, simulated at TAKE size) and the edge-gated CONTROL bots. 3m/5m are not required to win; 30m is the benchmark.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  leaderboardCard(s) {
    const rows = this.byTf(s.leaderboard_field || []);
    return h('div', { class: 'card', id: 'v31-lb' }, h('div', { class: 'card-head' }, h('h3', { text: 'LEADERBOARD — Jev field, matched controls, always-take' }),
      h('span', { class: 'sub', text: 'ranked by the score (never a gate); STATUS is the gate verdict' })), this.lbTable(rows));
  },
  lbTable(rows) {
    const t = h('table', { class: 'tbl' });
    const labels = this.labels();
    renderTable(t, [
      { h: 'rank', num: true, cell: (r) => String(r.rank ?? DASH) },
      { h: 'bot', cell: (r) => [h('a', { class: 'mono v3-link', href: '#', text: v31key(r.key), onclick: (e) => { e.preventDefault(); this.openBot(r.key); } }),
        this.extended(r.pair_id) ? [' ', pill('EXT', 'ghost', 'added by protocol amendment 1 (activity only)')] : null] },
      { h: 'coin', cell: (r) => r.coin }, { h: 'tf', cell: (r) => r.tf },
      { h: 'mode', cell: (r) => pill(r.mode || r.role, r.role === 'JEV' ? 'accent' : 'ghost') },
      { h: 'equity', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.net_pnl), text: fmtMoney(r.equity) }) },
      { h: 'return', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.net_return_pct), text: v31pc(r.net_return_pct, 1, true) }) },
      { h: 'trades/day', num: true, cell: (r) => h('span', { class: 'num ' + (r.participation_ok ? '' : 'warn'), text: v31n(r.trades_per_day, 2) }) },
      { h: 'ExpR', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.exp_r), text: v31n(r.exp_r, 3, true) }) },
      { h: 'PF', num: true, cell: (r) => v31pf(r.pf) },
      { h: 'DD', num: true, cell: (r) => v31pc(r.max_dd, 1) },
      { h: 'Jev action', cell: (r) => (r.role === 'JEV' ? v31mix(r.jev_actions) : r.role === 'TAKE' ? 'all TAKE' : 'deterministic') },
      { h: 'selection alpha', num: true, title: 'Jev net minus the median of its 20 matched random-action twins', cell: (r) => (r.role === 'JEV' ? num(r.selection_alpha) : DASH) },
      { h: 'status', cell: (r) => [pill(r.state || DASH, V31_STATE_KIND[r.state] || 'ghost'), ' ', h('span', { class: 'sub', text: labels[r.failure_mode] || r.failure_mode || '' })] }],
    rows, { empty: 'no bots' });
    return h('div', { class: 'tablewrap' }, t);
  },

  pairsCard(s) {
    const t = h('table', { class: 'tbl' });
    const pairs = this.byTf(s.pairs || []);
    renderTable(t, [
      { h: 'pair', cell: (p) => [h('a', { class: 'mono v3-link', href: '#', text: p.strategy_id + '-' + p.coin + '-' + p.tf, onclick: (e) => { e.preventDefault(); this.openBot(p.jev_key); } }),
        this.extended(p.pair_id) ? [' ', pill('EXT', 'ghost', 'added by protocol amendment 1 (activity only)')] : null] },
      { h: 'CONTROL', num: true, cell: (p) => num((p.control || {}).net) },
      { h: '+JEV3', num: true, cell: (p) => num((p.jev || {}).net) },
      { h: 'Δ vs ctrl', num: true, cell: (p) => num(p.delta_vs_control) },
      { h: 'ALWAYS TAKE', num: true, cell: (p) => num((p.always_take || {}).net) },
      { h: 'ALWAYS SKIP', num: true, cell: () => h('span', { class: 'num', text: '0.00' }) },
      { h: 'random p50 / p90', num: true, cell: (p) => { const r = p.random || {}; return v31n(r.random_median, 2, true) + ' / ' + v31n(r.random_p90, 2, true); } },
      { h: 'selection alpha', num: true, cell: (p) => num((p.random || {}).alpha_usdt) },
      { h: 'rand p', num: true, cell: (p) => v31n((p.random || {}).p_value, 2) },
      { h: 'Jev actions', cell: (p) => { const a = p.jev_actions || {}; const n = Object.values(a).reduce((x, y) => x + y, 0) || 1;
        return v31mix(Object.fromEntries(Object.entries(a).map(([k, v]) => [k, v / n]))); } },
      { h: 'AUC P(support)', num: true, cell: (p) => { const a = p.jev_auc || {}; return isNum(a.auc) ? a.auc.toFixed(2) + ' [' + v31n(a.low, 2) + ', ' + v31n(a.high, 2) + ']' : DASH; } },
      { h: 'status', cell: (p) => pill(p.state || DASH, V31_STATE_KIND[p.state] || 'ghost') }], pairs, { empty: 'no Jev pairs ran' });
    const b = s.baselines || {};
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'CONTROL vs +JEV3 — against every baseline' })),
      h('p', { class: 'sub', text: 'Same candidates, edge gate, wallet, risk rules, fees and execution; only the action differs. ALWAYS SKIP never trades (0). The RANDOM twins take SKIP / TAKE / ATTACK at Jev\'s own rates through the same ATTACK rule.' }),
      h('div', { class: 'tiles small' },
        tile('pairs', String(b.pairs ?? 0)), tile('Jev total', fmtMoney(b.jev_total, true), signClass(b.jev_total)),
        tile('controls total', fmtMoney(b.control_total, true), signClass(b.control_total)),
        tile('always-take total', fmtMoney(b.always_take_total, true), signClass(b.always_take_total)),
        tile('random median total', fmtMoney(b.random_median_total, true), signClass(b.random_median_total)),
        tile('Jev beats control', (b.jev_beats_control ?? 0) + ' / ' + (b.pairs ?? 0), '', 'meaningfully (≥ +0.50): ' + (b.jev_beats_control_meaningfully ?? 0)),
        tile('beats always-skip', (b.jev_beats_skip ?? 0) + ' / ' + (b.pairs ?? 0)),
        tile('beats random p90', (b.jev_beats_random_p90 ?? 0) + ' / ' + (b.pairs ?? 0))),
      h('div', { class: 'tablewrap' }, t));
  },

  alphaCard(s) {
    const sa = s.selection_alpha || {};
    const att = s.attack || {};
    const a = (x, label) => {
      x = x || {};
      return h('div', { class: 'v31-attack' }, h('h4', { text: label }), h('div', { class: 'tiles small' },
        tile('ATTACK trades', String(x.attack_trades ?? 0), '', (x.strong_attack_trades ?? 0) + ' STRONG · ' + v31pc(x.attack_share, 0) + ' of trades'),
        tile('ATTACK ExpR', v31n(x.attack_expectancy_r, 3, true), signClass(x.attack_expectancy_r), 'TAKE trades ' + v31n(x.take_expectancy_r, 3, true)),
        tile('ATTACK net', fmtMoney(x.attack_net, true), signClass(x.attack_net)),
        tile('at TAKE size', fmtMoney(x.counterfactual_normal_net, true), signClass(x.counterfactual_normal_net), 'counterfactual NORMAL'),
        tile('added by ATTACK', fmtMoney(x.attack_added_net, true), signClass(x.attack_added_net))));
    };
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'JEV SELECTION ALPHA · ATTACK EFFECTIVENESS' })),
      h('div', { class: 'tiles small' },
        tile('selection alpha (total)', fmtMoney(sa.total_usdt, true), signClass(sa.total_usdt), 'Jev − matched random action, summed over pairs'),
        tile('mean per pair', fmtMoney(sa.mean_usdt, true), signClass(sa.mean_usdt)),
        tile('pairs with positive alpha', (sa.positive_pairs ?? 0) + ' / ' + (sa.pairs ?? 0))),
      a(att.jev, '+JEV3 — Jev ATTACK'), a(att.controls_field, 'CONTROL (field) — deterministic ATTACK from the edge model'));
  },

  jevCard(s) {
    const j = s.jev_pooled || {};
    if (!j.decisions) return h('div', { class: 'card' }, h('h3', { text: 'JEV V3' }), h('p', { class: 'sub', text: 'no Jev V3 decisions in this run' }));
    const fin = j.final || {};
    const auc = j.auc_support || {};
    const auc2 = j.auc_not_skip || {};
    const lat = j.latency_ms || {};
    const by = j.by_action || {};
    const sup = h('table', { class: 'tbl' });
    renderTable(sup, [{ h: 'P(support) band', cell: (r) => r.bucket }, { h: 'candidates', num: true, cell: (r) => String(r.n) },
      { h: 'realized mean R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.mean_r), text: v31n(r.mean_r, 2, true) }) },
      { h: 'win rate', num: true, cell: (r) => v31pc(r.win_rate, 0) }], j.support_buckets || []);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'JEV V3 — support or contradict, and whether it can tell' }),
      pill('JEV_POLICY_V3', 'accent'), pill('JEV_PROMPT_V3', 'ghost')),
      h('div', { class: 'v3-levels' }, ...V31_LEVELS.map((l) => h('div', { class: 'v3-level ' + (l === 'SKIP' ? 'skip' : l === 'TAKE' ? 'normal' : 'attack') },
        h('b', { text: String(fin[l] || 0) }), h('span', { text: l.replace('_', ' ') }),
        h('small', { text: v31pc((fin[l] || 0) / j.decisions, 0) + ' · realized ' + v31n((by[l] || {}).mean_r, 2, true) + 'R' + (l === 'SKIP' ? ' (shadow)' : '') })))),
      h('div', { class: 'tiles small' },
        tile('decisions', String(j.decisions)), tile('skip rate', v31pc(j.skip_rate, 1), (j.skip_rate || 0) > 0.8 ? 'down' : ''),
        tile('ATTACK rate', v31pc(j.attack_rate, 1)),
        tile('AUC P(support)', v31n(auc.auc, 3), isNum(auc.low) && auc.low > 0.5 ? 'up' : '', isNum(auc.low) ? '95% [' + auc.low.toFixed(3) + ', ' + auc.high.toFixed(3) + ']' : ''),
        tile('AUC 1 − P(SKIP)', v31n(auc2.auc, 3), isNum(auc2.low) && auc2.low > 0.5 ? 'up' : '', isNum(auc2.low) ? '95% [' + auc2.low.toFixed(3) + ', ' + auc2.high.toFixed(3) + ']' : ''),
        tile('ATTACK downgraded', String(Object.values(j.downgrades || {}).reduce((a, b) => a + b, 0)), '', Object.entries(j.downgrades || {}).map(([k, v]) => k + ' ' + v).join(' · ').slice(0, 90)),
        tile('MIN_NOTIONAL_AFTER_JEV', String(j.min_notional_after_jev ?? 0), j.min_notional_after_jev ? 'down' : 'up', 'Jev never shrinks an order'),
        tile('latency p50 / p95 / p99', (lat.p50 ?? DASH) + ' / ' + (lat.p95 ?? DASH) + ' / ' + (lat.p99 ?? DASH) + ' ms'),
        tile('API errors', String(j.errors ?? 0), (j.error_rate || 0) > 0.02 ? 'down' : '', v31pc(j.error_rate, 2)),
        tile('cost', isNum(j.cost_usd) ? '$' + j.cost_usd.toFixed(3) : DASH)),
      h('p', { class: 'sub', text: 'Confidence → realized expectancy: every candidate Jev answered, with its real trade or (for a SKIP) the shadow trade the engine simulated.' }),
      h('div', { class: 'tablewrap' }, sup));
  },

  calibrationCard(s) {
    const c = (this.tf ? (s.calibration_by_tf || {})[this.tf] : s.calibration) || {};
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'predicted net R', cell: (r) => r.bucket }, { h: 'candidates', num: true, cell: (r) => String(r.n) },
      { h: 'mean predicted', num: true, cell: (r) => v31n(r.predicted, 3, true) },
      { h: 'mean realized', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.realized), text: v31n(r.realized, 3, true) }) },
      { h: 'win rate', num: true, cell: (r) => v31pc(r.win_rate, 0) }], c.buckets || []);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'EDGE CALIBRATION — does the expected net edge rank outcomes?' + (this.tf ? ' · ' + this.tf : '') })),
      h('div', { class: 'tiles small' }, tile('candidates', String(c.n ?? 0)), tile('slope', v31n(c.slope, 3), (c.slope || 0) > 0 ? 'up' : 'down', 'realized on predicted'),
        tile('Spearman', v31n(c.spearman, 3)), tile('passed: realized', v31n(c.passed_realized, 3, true) + 'R', signClass(c.passed_realized), 'n ' + (c.passed_n ?? 0)),
        tile('refused: realized', v31n(c.refused_realized, 3, true) + 'R', signClass(c.refused_realized), 'n ' + (c.refused_n ?? 0) + ' (shadow)')),
      h('p', { class: 'sub', text: 'The model is fitted only on DEVELOPMENT RAW candidates that had already closed (causal); TEST uses the frozen DEVELOPMENT ledger.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  funnelCard(s) {
    const f = s.funnels || {};
    const rows = [];
    for (const who of ['controls', 'jev']) for (const tf of V31_TFS) {
      if (this.tf && tf !== this.tf) continue;
      const x = (f[who] || {})[tf];
      if (x && x.raw_setups) rows.push([who, tf, x]);
    }
    const t = h('table', { class: 'tbl' });
    const bar = (v, of) => h('span', { class: 'v31-funnel' }, h('i', { style: 'width:' + Math.max(2, Math.round(100 * (v || 0) / (of || 1))) + '%' }), h('b', { text: String(v ?? DASH) }));
    renderTable(t, [{ h: 'bots', cell: ([w]) => (w === 'jev' ? '+JEV3' : 'CONTROL') }, { h: 'tf', cell: ([, tf]) => tf },
      { h: 'raw setups', cell: ([, , x]) => bar(x.raw_setups, x.raw_setups) }, { h: 'legal', cell: ([, , x]) => bar(x.legal, x.raw_setups) },
      { h: 'positive edge', cell: ([, , x]) => bar(x.positive_edge, x.raw_setups) },
      { h: 'Jev TAKE / ATTACK', cell: ([w, , x]) => (w === 'jev' ? bar(x.jev_accepted, x.raw_setups) : DASH) },
      { h: 'executed', cell: ([, , x]) => bar(x.executed, x.raw_setups) }], rows, { empty: 'no funnel data' });
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'PARTICIPATION FUNNEL' })),
      h('p', { class: 'sub', text: 'raw setups → legal (RiskManager at TAKE size) → positive expected edge → Jev TAKE/ATTACK → executed. Participation gate: 3m ≥ 2, 5m ≥ 1.5, 15m ≥ 0.5, 30m ≥ 0.25 trades per day.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  holdingCard(s) {
    const ho = s.holding || {};
    const buckets = ['<5m', '5-15m', '15-60m', '1-4h', '>4h'];
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'tf', cell: ([tf]) => tf }, { h: 'trades', num: true, cell: ([, x]) => String(x.trades) },
      { h: 'median hold', num: true, cell: ([, x]) => v31n(x.median_hold_min, 0) + ' min' },
      ...buckets.map((bk) => ({ h: bk, num: true, cell: ([, x]) => { const c = (x.buckets || {})[bk] || {};
        return h('span', { class: 'num ' + signClass(c.gross), title: c.n + ' trades · net ' + v31n(c.net, 2, true), text: (c.n || 0) + ' · ' + v31n(c.gross, 2, true) + ' · ' + v31n(c.mean_r, 2, true) + 'R' }); } }))],
    V31_TFS.filter((tf) => ho[tf] && (!this.tf || tf === this.tf)).map((tf) => [tf, ho[tf]]));
    const ed = s.exit_diagnostics || {};
    const rows = [];
    for (const [sid, tfs] of Object.entries(ed)) for (const tf of V31_TFS) if ((tfs || {})[tf] && (!this.tf || tf === this.tf)) rows.push([sid, tf, tfs[tf]]);
    const e = h('table', { class: 'tbl' });
    renderTable(e, [{ h: 'family', cell: ([sid]) => sid }, { h: 'tf', cell: ([, tf]) => tf }, { h: 'trades', num: true, cell: ([, , x]) => String(x.n) },
      { h: 'verdict', cell: ([, , x]) => pill(x.verdict || DASH, x.verdict === 'EDGE SURVIVES' ? 'up' : 'down') },
      { h: 'gross / cost bps', num: true, cell: ([, , x]) => v31n(x.gross_bps, 1) + ' / ' + v31n(x.cost_bps, 1) },
      { h: 'drift after entry 15m / 1h / 4h / 12h', num: true, cell: ([, , x]) => ['15m', '60m', '240m', '720m'].map((k) => v31n((x.drift_bps || {})[k], 1, true)).join(' / ') },
      { h: 'gave back +1R', num: true, cell: ([, , x]) => v31pc(x.gave_back_share, 0) }], rows, { empty: 'no exit diagnostics' });
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'HOLDING PERIOD & EXIT DIAGNOSTICS' })),
      h('p', { class: 'sub', text: 'Cell: trades · gross USDT · mean R per holding bucket (CONTROL trades). Verdicts: where the edge is lost — entry, exit, holding or cost.' }),
      h('div', { class: 'tablewrap' }, t), h('div', { class: 'tablewrap' }, e));
  },

  makerCard(s) {
    const m = s.maker || {};
    const g = this.tf && m[this.tf] ? this.tf : 'ALL';
    const block = m[g] || {};
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'wait', cell: ([w]) => w }, { h: 'taker only', num: true, cell: ([, p]) => num((p.taker || {}).net) },
      { h: 'conservative maker', num: true, cell: ([, p]) => [num((p.conservative || {}).net), h('small', { class: 'sub', text: ' ' + ((p.conservative || {}).status ? Object.entries(p.conservative.status).map(([k, v]) => k.toLowerCase() + ' ' + v).join(' · ') : '') })] },
      { h: 'optimistic maker', num: true, cell: ([, p]) => num((p.optimistic || {}).net) },
      { h: 'missed winners (cons.)', num: true, cell: ([, p]) => String((p.conservative || {}).missed_winners ?? 0) },
      { h: 'verdict', cell: ([, p]) => h('span', { class: String(p.verdict || '').startsWith('FAIL') ? 'down' : 'up', text: p.verdict || DASH }) }], Object.entries(block));
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'MAKER-FIRST vs TAKER' + (g !== 'ALL' ? ' · ' + g : '') })),
      h('p', { class: 'sub', text: 'Post hoc on the 1m tape: a limit at t0, filled only if the tape reached it (optimistic: a touch; conservative: one half-spread better and traded through by max(1 tick, 1 bp)); unfilled after the wait → taker only if price is within 0.25 × stop of the limit, else missed. Profitable only with the optimistic maker = FAIL.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  capacityCard(s) {
    const cap = s.capacity || {};
    const tot = cap.totals || {};
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'CAPACITY DIAGNOSTIC — 20 / 50 / 100 USDT' }), pill('ANALYSIS ONLY', 'ghost')),
      h('p', { class: 'sub', text: cap.note || '' }),
      h('div', { class: 'tiles small' }, ...Object.entries(tot).map(([k, v]) => tile(k + ' USDT', fmtMoney(v.net, true), signClass(v.net),
        v31pc(v.return_pct, 1, true) + ' · ' + (v.trades ?? 0) + ' trades · ' + (v.below_min ?? 0) + ' below min'))));
  },

  diagnosesCard(s) {
    const fm = s.failure_modes || {};
    const labels = fm.labels || {};
    const blockOf = (title, counts) => {
      const total = Object.values(counts || {}).reduce((a, b) => a + b, 0) || 1;
      return h('div', { class: 'v3-fail' }, h('h4', { text: title }), ...Object.entries(counts || {}).map(([k, n]) => h('div', { class: 'v3-bar' },
        h('span', { class: 'v3-bar-label', text: labels[k] || k }),
        h('span', { class: 'v3-bar-track' }, h('i', { class: V31_FAIL_KIND[k] || 'ghost', style: 'width:' + Math.round(100 * n / total) + '%' })),
        h('b', { class: 'num', text: String(n) }))));
    };
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', { text: 'BOT ANALYZER V3.1 — the root cause per bot' })),
      h('div', { class: 'v3-fails' }, blockOf('+JEV3 bots', fm.jev_bots), blockOf('all CONTROL bots', fm.controls)),
      (s.low_activity_edge || []).length ? h('p', { class: 'sub', text: 'LOW_ACTIVITY_EDGE (positive expectancy, too little activity): ' + s.low_activity_edge.map(v31key).join(', ') }) : null);
  },

  controlsCard(s) {
    const d = h('details', { class: 'v3-more' }, h('summary', { text: 'All CONTROL bots (' + (s.leaderboard_controls || []).length + ') and the field selection' }));
    d.addEventListener('toggle', () => {
      if (!d.open || d.dataset.built) return;
      d.dataset.built = '1';
      d.append(this.lbTable(this.byTf(s.leaderboard_controls || [])));
      const f = s.field || ((this.block() || {}).field) || {};
      d.append(h('h4', { text: 'Field selection (activity only)' }), h('p', { class: 'sub', text: f.rule || '' }),
        h('p', { class: 'sub', text: 'thin slots: ' + ((f.thin_slots || []).map((e) => e.strategy_id + ' ' + e.timeframe + ' (' + e.eligible + ' eligible)').join(', ') || 'none') }));
    });
    return h('div', { class: 'card' }, d);
  },

  protocolCard(b) {
    const c = b.config || {};
    const g = c.gates || {};
    const e = c.edge || {};
    const ev = b.evidence || {};
    const part = c.participation_per_day || {};
    return h('div', { class: 'card' }, h('details', { class: 'v3-more' }, h('summary', { text: 'Protocol, edge model, Jev V3 policy, gates and fingerprints (pre-registered)' }),
      h('dl', { class: 'kv' },
        h('dt', { text: 'window' }), h('dd', { text: (c.trade_from || '') + ' → ' + (c.trade_to || '') + ' · ' + (c.dataset_role || '') + (c.observe_from ? ' · RAW evidence from ' + c.observe_from : '') }),
        h('dt', { text: 'strategies' }), h('dd', { text: 'S33.1 pullback regime continuation · S35.1 flow persistence momentum · S37 impulse continuation — long-hold runners (BE 1.5-2R, partial at 2-2.5R, 1h-ATR trail, 12 h max)' }),
        h('dt', { text: 'edge gate' }), h('dd', { text: 'hierarchical shrinkage (k ' + (e.shrinkage_k ?? 30) + '): exact bucket → strategy×tf×coin → strategy×tf → family → prior 0; family needs ≥ ' + (e.min_family_n ?? 30) + ' observations; TAKE if predicted net ≥ ' + (e.take_margin_r ?? 0.05) + 'R; ATTACK if net − se ≥ ' + (e.attack_lower_r ?? 0.1) + 'R, gross ≥ ' + (e.attack_headroom_ratio ?? 2) + '× cost, quality ≥ ' + (e.attack_min_quality ?? 0.6) }),
        h('dt', { text: 'evidence' }), h('dd', { class: 'mono keep-case', text: (ev.observations ?? DASH) + ' observations · ' + (ev.fingerprint || DASH) }),
        h('dt', { text: 'risk profile' }), h('dd', { text: 'AGGRESSIVE_V31: TAKE 1.0% · ATTACK 1.5% · STRONG ATTACK 2.0% (hard cap) · 20x ceiling · no DEFENSIVE size · no ATTACK in a drawdown ≥ 18% · halt at 30%' }),
        h('dt', { text: 'Jev V3 policy' }), h('dd', { text: 'SKIP (evidence contradicts) · TAKE (valid, the normal action) · ATTACK (independent conditions reinforce) — ATTACK through the same deterministic rule as CONTROL; never shrinks an order; an error is SKIP' }),
        h('dt', { text: 'participation' }), h('dd', { text: Object.entries(part).map(([tf, n]) => tf + ' ≥ ' + n + '/day').join(' · ') + ' · adequate sample ≥ 30 trades' }),
        h('dt', { text: 'gates' }), h('dd', { text: 'net > 0 · ExpR > 0 · PF ≥ ' + (g.min_profit_factor ?? 1.1) + ' · DD ≤ ' + v31pc(g.max_drawdown_pct, 0) + ', no halt · no liquidation · positive without the best 3 and top 3 ≤ ' + v31pc(g.max_top3_share, 0) + ' of profit; +JEV3: beats the matched random action (p90), beats CONTROL by ≥ ' + (g.min_delta_vs_control ?? 0.5) + ' USDT, AUC 95% CI above 0.50, skip ≤ ' + v31pc(g.max_skip_rate, 0) + ', errors ≤ ' + v31pc(g.max_error_rate, 0) }),
        h('dt', { text: 'fingerprints' }), h('dd', { class: 'mono keep-case', text: Object.entries(c.strategy_fingerprints || {}).map(([k, v]) => k + ' ' + v).join(' · ') + ' · ' + Object.entries(c.jev_fingerprints || {}).map(([k, v]) => k + ' ' + v).join(' · ') }),
        h('dt', { text: 'dataset' }), h('dd', { class: 'mono keep-case', text: (c.dataset_fingerprint || DASH) + ' · rules ' + (c.rules_source || '') }))));
  },

  // ---- one bot -----------------------------------------------------------------------------------------
  async openBot(key, silent) {
    this.botKey = key;
    const card = $('#v31-bot-card');
    if (!card) return;
    card.hidden = false;
    clear(card).append(h('p', { class: 'sub', text: 'loading ' + key + '…' }));
    const run = ((this.block() || {}).run || {}).run_id;
    try { this.bot = await api(this.base + '/v31/bots/' + encodeURIComponent(key) + (run ? '?run_id=' + encodeURIComponent(run) : '')); }
    catch (e) { clear(card).append(h('p', { class: 'msg err', text: (e && e.message) || String(e) })); return; }
    if (!silent && this.pathBase) history.replaceState(null, '', this.pathBase + '/bot/' + encodeURIComponent(key));
    this.renderBot();
    card.scrollIntoView({ block: 'start' });
  },
  closeBot() {
    this.botKey = ''; this.bot = null;
    const card = $('#v31-bot-card'); if (card) { clear(card); card.hidden = true; }
    if (this.pathBase) history.replaceState(null, '', this.pathBase);
  },
  renderBot() {
    const card = $('#v31-bot-card');
    const b = this.bot;
    if (!card || !b || !b.ok) return;
    clear(card);
    card.hidden = false;
    const bot = b.bot || {};
    const idn = bot.identity || {};
    const a = b.analysis || {};
    const sum = a.summary || {};
    card.append(h('div', { class: 'card-head' },
      h('h2', { class: 'keep-case', text: idn.strategy_id + ' · ' + idn.coin + ' · ' + idn.timeframe + ' · ' + (bot.role === 'JEV' ? 'JEV3' : bot.role) }),
      pill(sum.result || bot.state || DASH, V31_STATE_KIND[a.state] || 'ghost'), pill(b.dataset_role || '', 'ghost'),
      h('span', { class: 'spacer' }), h('button', { type: 'button', class: 'btn ghost', text: 'close', onclick: () => this.closeBot() })));
    card.append(h('p', { class: 'sub mono keep-case', text: bot.key + ' · fingerprint ' + (bot.fingerprint || DASH) + ' · context '
      + (idn.context_timeframes || []).join(' + ') + ' · execution 1m · ' + (idn.profile || '') + ' · ' + (b.venue_label || '') }));
    const tabs = ['overview', 'funnel', 'trades', 'analyzer', 'jev', 'validation'];
    card.append(h('div', { class: 'subtabs' }, ...tabs.map((t) => h('button', { type: 'button', class: t === this.tab ? 'active' : '',
      text: t === 'funnel' ? 'Participation funnel' : t[0].toUpperCase() + t.slice(1), onclick: () => { this.tab = t; this.renderBot(); } }))));
    const body = h('div', { class: 'v3-tab' });
    card.append(body);
    if (this.tab === 'overview') this.overview(body, b, a);
    else if (this.tab === 'funnel') this.funnelTab(body, b, a);
    else if (this.tab === 'trades') this.trades(body, b);
    else if (this.tab === 'analyzer') this.analyzer(body, b, a);
    else if (this.tab === 'jev') this.jev(body, b, a);
    else this.validation(body, b, a);
  },

  overview(body, b, a) {
    const m = (b.bot || {}).metrics || {};
    const e = a.edge || {}, act = a.activity || {}, j = a.jev || {}, sum = a.summary || {}, conc = a.concentration || {};
    body.append(h('div', { class: 'v3-diagnosis' },
      h('div', null, h('span', { text: 'EDGE' }), h('b', { class: signClass(e.net_expectancy_r), text: v31n(e.net_expectancy_r, 2, true) + 'R' }),
        h('small', { text: 'gross ' + v31n(e.gross_expectancy_r, 2, true) + 'R' })),
      h('div', null, h('span', { text: 'ACTIVITY' }), h('b', { text: v31n(act.trades_per_day, 2) + ' / day' }), h('small', { text: 'needs ' + (act.required_per_day ?? DASH) + ' · ' + (act.trades ?? 0) + ' trades' })),
      h('div', null, h('span', { text: 'CONCENTRATION' }), h('b', { text: v31pc(conc.top3_share_of_profit, 0) }), h('small', { text: 'ex-top3 ' + v31n(conc.net_without_top3, 2, true) })),
      h('div', null, h('span', { text: 'JEV' }), h('b', { text: j.decisions ? v31pc(j.acceptance_rate, 0) + ' taken' : DASH }), h('small', { text: j.decisions ? 'alpha ' + v31n(sum.selection_alpha, 2, true) : 'no Jev' })),
      h('div', null, h('span', { text: 'DRAWDOWN' }), h('b', { text: v31pc(e.max_drawdown_pct, 1) }), h('small', { text: 'PF ' + v31pf(e.net_pf) })),
      h('div', { class: 'diag ' + (a.failure_mode === 'ROBUST' ? 'up' : 'down') }, h('span', { text: 'DIAGNOSIS' }),
        h('b', { text: sum.primary_issue || a.failure_mode || DASH }), h('small', { text: (sum.diagnoses || []).slice(1).join(' · ') }))));
    const eq = b.equity || [];
    if (eq.length > 1 && typeof Candidates !== 'undefined' && Candidates.spark) body.append(Candidates.spark(eq, (b.bot || {}).balance || 20));
    body.append(h('div', { class: 'tiles small' },
      tile('net', fmtMoney(m.net_profit, true), signClass(m.net_profit), v31pc(m.net_return_pct, 1, true)),
      tile('gross (decision prices)', fmtMoney(m.gross_pnl, true), signClass(m.gross_pnl)),
      tile('fees', fmtMoney(-(m.fees_paid || 0), true), 'down'), tile('slippage', fmtMoney(-(m.slippage_cost || 0), true), 'down'),
      tile('funding', fmtMoney(m.funding_paid, true), signClass(m.funding_paid)),
      tile('win rate', v31pc(m.win_rate, 0)), tile('trades', String(m.trades ?? 0)),
      tile('without best trade', fmtMoney(conc.net_without_best, true), signClass(conc.net_without_best)),
      tile('without best 3', fmtMoney(conc.net_without_top3, true), signClass(conc.net_without_top3))));
    if (b.pair) body.append(this.pairCompare(b.pair));
  },

  pairCompare(p) {
    const side = (label, x) => (x ? h('tr', null, h('td', { text: label }), h('td', { class: 'num ' + signClass(x.gross), text: fmtMoney(x.gross, true) }),
      h('td', { class: 'num ' + signClass(x.net), text: fmtMoney(x.net, true) }), h('td', { class: 'num', text: String(x.trades ?? DASH) }),
      h('td', { class: 'num', text: v31n(x.trades_per_day, 2) }), h('td', { class: 'num', text: v31pc(x.max_dd, 1) }),
      h('td', { class: 'num', text: v31n(x.exp_r, 3, true) })) : null);
    const r = p.random || {};
    const t = h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ...['', 'gross', 'net', 'trades', 'trades/day', 'DD', 'ExpR'].map((x) => h('th', { class: x ? 'num' : '', text: x })))),
      h('tbody', null, side('CONTROL', p.control), side('+JEV3', p.jev), side('ALWAYS TAKE', p.always_take),
        h('tr', null, h('td', { text: 'ALWAYS SKIP' }), ...['0.00', '0.00', '0', '0.00', '0.0%', DASH].map((x) => h('td', { class: 'num', text: x }))),
        h('tr', null, h('td', { text: 'RANDOM (median of ' + (r.n || 0) + ')' }), h('td', { class: 'num', text: DASH }),
          h('td', { class: 'num ' + signClass(r.random_median), text: fmtMoney(r.random_median, true) }), ...[DASH, DASH, DASH].map((x) => h('td', { class: 'num', text: x })),
          h('td', { class: 'num', text: v31n(r.random_expectancy_r, 3, true) }))));
    const aj = p.attack_jev || {};
    return h('div', { class: 'v3-pair' }, h('h4', { text: 'CONTROL vs JEV — ' + p.strategy_id + ' ' + p.coin + ' ' + p.tf }), h('div', { class: 'tablewrap' }, t),
      h('div', { class: 'tiles small' },
        tile('selection alpha', fmtMoney(r.alpha_usdt, true), signClass(r.alpha_usdt), 'vs random p90 ' + v31n(r.random_p90, 2, true) + ' · p ' + v31n(r.p_value, 2)),
        tile('Jev ATTACK', String(aj.attack_trades ?? 0), '', 'ExpR ' + v31n(aj.attack_expectancy_r, 2, true) + ' · added ' + v31n(aj.attack_added_net, 2, true)),
        tile('Δ vs control', fmtMoney(p.delta_vs_control, true), signClass(p.delta_vs_control))));
  },

  funnelTab(body, b, a) {
    const f = a.funnel || (b.bot || {}).funnel || {};
    const steps = [['RAW SETUPS', f.raw_setups], ['LEGAL', f.legal], ['POSITIVE EDGE', f.positive_edge],
      ['JEV TAKE / ATTACK', f.jev_accepted], ['EXECUTED', f.executed]];
    const top = f.raw_setups || 1;
    body.append(h('div', { class: 'v31-funnel-big' }, ...steps.map(([label, v]) => h('div', { class: 'v31-step' + (v === null || v === undefined ? ' off' : '') },
      h('span', { text: label }), h('i', { style: 'width:' + Math.max(2, Math.round(100 * (v || 0) / top)) + '%' }),
      h('b', { text: v === null || v === undefined ? 'n/a' : String(v) + ' · ' + v31pc((v || 0) / top, 0) })))));
    const kv = (obj) => h('dl', { class: 'kv' }, ...Object.entries(obj || {}).flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: String(v) })]));
    body.append(h('div', { class: 'v3-an' }, section('EDGE-GATE REFUSALS', kv(f.edge_reasons)), section('NOT LEGAL AT TAKE SIZE', kv(f.illegal_reasons)),
      section('AFTER JEV', kv({ 'MIN_NOTIONAL_AFTER_JEV': f.min_notional_after_jev ?? 0, 'ATTACK not legal → TAKE': f.attack_not_legal ?? 0, 'gate decisions': f.gate_decisions ?? DASH }))),
      h('p', { class: 'sub', text: 'Legal = the RiskManager would accept the candidate at TAKE size at that moment (a bot already in a position is not legal). Participation gate for ' + ((b.bot || {}).identity || {}).timeframe + ': ' + ((a.activity || {}).required_per_day ?? DASH) + ' trades/day.' }));
  },

  trades(body, b) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'entry (UTC)', cell: (r) => fmtTs(r.entry_ts) }, { h: 'side', cell: (r) => pill(r.side, r.side) },
      { h: 'hold', num: true, cell: (r) => fmtDur(r.hold_s) }, { h: 'gross', num: true, cell: (r) => num(r.gross) },
      { h: 'fees', num: true, cell: (r) => fmtMoney(-(r.fees || 0), true) }, { h: 'slip', num: true, cell: (r) => fmtMoney(-(r.slippage || 0), true) },
      { h: 'net', num: true, cell: (r) => num(r.net) }, { h: 'R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.r), text: v31n(r.r, 2, true) }) },
      { h: 'exit', cell: (r) => r.exit_kind + (r.tp_hit ? ' · tp' : '') }, { h: 'quality', num: true, cell: (r) => v31n(r.quality, 2) },
      { h: 'pred. net R', num: true, cell: (r) => v31n(r.edge_net_r, 2, true) },
      { h: 'size', cell: (r) => (r.jev_level ? 'JEV ' + r.jev_level + ' ×' + v31n(r.jev_mult, 1) : (r.tier || '')) + (r.attack_downgrade ? ' ↓' : '') },
      { h: 'regime · vol', cell: (r) => (r.regime || DASH) + ' · ' + (r.vol_band || DASH) }], (b.trades || []).slice().reverse(), { empty: 'no trades' });
    body.append(h('p', { class: 'sub', text: (b.trades || []).length + ' closed trades, newest first. Gross is at decision prices; net = gross − fees − slippage + funding.' }),
      h('div', { class: 'tablewrap' }, t));
  },

  analyzer(body, b, a) {
    const kv = (obj, keys) => h('dl', { class: 'kv' }, ...keys.flatMap(([k, label, f]) => [h('dt', { text: label }), h('dd', { text: f ? f((obj || {})[k]) : String((obj || {})[k] ?? DASH) })]));
    const act = a.activity || {}, e = a.edge || {}, c = a.cost || {}, en = a.entry || {}, ex = a.exit || {}, ho = a.holding || {}, cc = a.concentration || {};
    body.append(h('div', { class: 'v3-an' },
      section('ENTRY QUALITY', kv(en, [['drift_bps', 'drift after entry 15m / 1h / 4h / 12h (bps)', (v) => ['15m', '60m', '240m', '720m'].map((k) => v31n((v || {})[k], 1, true)).join(' / ')],
        ['mfe_r_mean', 'mean MFE (R)', (v) => v31n(v, 2)], ['mae_r_mean', 'mean MAE (R)', (v) => v31n(v, 2)], ['reached_1r_share', 'reached +1R', (v) => v31pc(v, 0)],
        ['win_rate', 'win rate', (v) => v31pc(v, 0)]])),
      section('EXIT QUALITY', kv(ex, [['verdict', 'verdict'], ['tp_hit_share', 'reached the first target', (v) => v31pc(v, 0)],
        ['gave_back_share', 'gave back after +1R', (v) => v31pc(v, 0)], ['avg_win_r', 'average winner', (v) => v31n(v, 2, true) + 'R'],
        ['avg_loss_r', 'average loser', (v) => v31n(v, 2, true) + 'R'], ['post_exit_drift_r_winners', 'after a winning exit (4h)', (v) => v31n(v, 2, true) + 'R'],
        ['post_exit_drift_r_losers', 'after a losing exit (4h)', (v) => v31n(v, 2, true) + 'R']])),
      section('HOLDING PERIOD', kv(ho, [['median_hold_min', 'median hold (min)', (v) => v31n(v, 0)], ['buckets', 'gross by bucket',
        (v) => Object.entries(v || {}).map(([k, x]) => k + ' ' + (x.n || 0) + '×' + v31n(x.gross, 2, true)).join(' · ')]])),
      section('COST STRUCTURE', kv(c, [['fees', 'fees', (v) => fmtMoney(-(v || 0), true)], ['slippage', 'spread + slippage', (v) => fmtMoney(-(v || 0), true)],
        ['funding', 'funding', (v) => fmtMoney(v, true)], ['round_trip_cost_bps', 'round trip (bps)', (v) => v31n(v, 1)], ['cost_r_per_trade', 'cost per trade (R)', (v) => v31n(v, 3)],
        ['gross_r_per_trade', 'gross per trade (R)', (v) => v31n(v, 3, true)], ['realized_edge_to_cost', 'realized gross/cost', (v) => v31n(v, 2)]])),
      section('EDGE / CONCENTRATION', kv({ ...e, ...cc }, [['gross_expectancy_r', 'gross expectancy', (v) => v31n(v, 3, true) + 'R'], ['net_expectancy_r', 'net expectancy', (v) => v31n(v, 3, true) + 'R'],
        ['net_pf', 'net PF', v31pf], ['net_without_best', 'net without the best trade', (v) => fmtMoney(v, true)], ['net_without_top3', 'net without the best 3', (v) => fmtMoney(v, true)],
        ['top3_share_of_profit', 'top 3 share of profit', (v) => v31pc(v, 0)]])),
      section('ACTIVITY', kv(act, [['raw_setups_per_day', 'raw setups/day', (v) => v31n(v, 2)], ['trades_per_day', 'trades/day', (v) => v31n(v, 2)],
        ['required_per_day', 'participation gate/day'], ['avg_hold_min', 'average hold (min)', (v) => v31n(v, 0)], ['median_gap_min', 'median gap (min)', (v) => v31n(v, 0)],
        ['edge_rejected', 'edge-gate refusals'], ['below_exchange_minimum', 'below exchange minimum']]))));
    if (a.calibration && a.calibration.n) {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [{ h: 'predicted net R', cell: (r) => r.bucket }, { h: 'n', num: true, cell: (r) => String(r.n) },
        { h: 'predicted', num: true, cell: (r) => v31n(r.predicted, 3, true) }, { h: 'realized', num: true, cell: (r) => v31n(r.realized, 3, true) }], a.calibration.buckets || []);
      body.append(section('EDGE CALIBRATION (this bot)', h('div', { class: 'tablewrap' }, t)));
    }
    const gt = h('table', { class: 'tbl' });
    renderTable(gt, [{ h: 'gate', cell: (g) => g.name }, { h: 'result', cell: (g) => pill(g.ok ? 'PASS' : 'FAIL', g.ok ? 'up' : 'down') },
      { h: 'actual', cell: (g) => String(g.actual ?? DASH) }, { h: 'needs', cell: (g) => g.threshold }], a.gates || []);
    body.append(section('QUALIFICATION V3.1', h('div', { class: 'tablewrap' }, gt)),
      h('div', { class: 'banner ' + (a.failure_mode === 'ROBUST' ? 'up' : 'down') }, h('b', { text: 'DIAGNOSIS: ' + ((a.summary || {}).diagnoses || []).join(' · ') })));
  },

  jev(body, b, a) {
    const j = a.jev;
    if (!j) {
      const twin = (b.siblings || []).find((k) => k.includes('JEV3'));
      body.append(h('p', { class: 'sub', text: 'This bot runs without Jev.' + (twin ? ' Its +JEV3 twin: ' : '') }),
        twin ? h('a', { class: 'mono v3-link', href: '#', text: v31key(twin), onclick: (e) => { e.preventDefault(); this.openBot(twin); } }) : null);
      const at = a.attack || {};
      body.append(section('DETERMINISTIC ATTACK (edge model)', h('div', { class: 'tiles small' }, tile('ATTACK trades', String(at.attack_trades ?? 0)),
        tile('ATTACK ExpR', v31n(at.attack_expectancy_r, 3, true)), tile('added by ATTACK', fmtMoney(at.attack_added_net, true), signClass(at.attack_added_net)))));
      return;
    }
    const fin = j.final || {};
    const by = j.by_action || {};
    body.append(h('div', { class: 'v3-levels' }, ...V31_LEVELS.map((l) => h('div', { class: 'v3-level ' + (l === 'SKIP' ? 'skip' : l === 'TAKE' ? 'normal' : 'attack') },
      h('b', { text: String(fin[l] || 0) }), h('span', { text: l.replace('_', ' ') }),
      h('small', { text: v31pc((fin[l] || 0) / (j.decisions || 1), 0) + ' · ' + v31n((by[l] || {}).mean_r, 2, true) + 'R' })))));
    const auc = j.auc_support || {};
    const sel = ((a.baselines || {}).selection) || {};
    const at = a.attack || {};
    body.append(h('div', { class: 'tiles small' },
      tile('decisions', String(j.decisions)), tile('skip rate', v31pc(j.skip_rate, 1)), tile('ATTACK rate', v31pc(j.attack_rate, 1)),
      tile('AUC P(support)', v31n(auc.auc, 3), isNum(auc.low) && auc.low > 0.5 ? 'up' : '', isNum(auc.low) ? '[' + auc.low.toFixed(2) + ', ' + auc.high.toFixed(2) + ']' : ''),
      tile('SELECTION ALPHA', fmtMoney(sel.alpha_usdt, true), signClass(sel.alpha_usdt), 'vs random median · p ' + v31n(sel.p_value, 2)),
      tile('ATTACK added', fmtMoney(at.attack_added_net, true), signClass(at.attack_added_net), (at.attack_trades ?? 0) + ' ATTACK trades'),
      tile('latency p50/p95', ((j.latency_ms || {}).p50 ?? DASH) + '/' + ((j.latency_ms || {}).p95 ?? DASH) + ' ms'),
      tile('errors', String(j.errors ?? 0), '', v31pc(j.error_rate, 2))));
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'signal (UTC)', cell: (d) => fmtTs(d.ts) }, { h: 'side', cell: (d) => pill(d.side, d.side) },
      { h: 'P(support)', num: true, cell: (d) => v31pc(d.p_support, 0) },
      { h: 'P(S/T/A)', num: true, cell: (d) => [d.p_skip, d.p_take, d.p_attack].map((x) => v31pc(x, 0)).join(' / ') },
      { h: 'Jev chose', cell: (d) => d.chosen || (d.error ? 'ERROR ' + d.error : DASH) },
      { h: 'final', cell: (d) => pill(d.level || DASH, d.level === 'SKIP' ? 'down' : d.level === 'TAKE' ? 'accent' : 'up') },
      { h: '×', num: true, cell: (d) => v31n(d.mult, 1) }, { h: 'pred. net R', num: true, cell: (d) => v31n(d.edge_net_r, 2, true) },
      { h: 'downgrade', cell: (d) => d.downgrade || '' },
      { h: 'outcome', cell: (d) => (d.outcome ? (d.outcome === 'SHADOW' ? 'skipped → ' : 'taken → ') + v31n(d.r, 2, true) + 'R' : DASH) }],
    (b.decisions || []).slice().reverse(), { empty: 'no decisions' });
    body.append(h('p', { class: 'sub', text: (b.decisions || []).length + ' Jev V3 decisions, newest first; a skipped candidate\'s outcome is the shadow trade the engine simulated at TAKE size.' }),
      h('div', { class: 'tablewrap' }, t));
    if (b.pair) body.append(this.pairCompare(b.pair));
  },

  validation(body, b, a) {
    const dev = b.dataset_role !== 'TEST';
    const adv = a.state === 'ADVANCE';
    const stages = dev
      ? [['DEVELOPMENT', adv ? 'PASS' : 'FAIL'], ['TEST 2026-05 → 08', adv ? 'NEXT (pre-registered)' : 'runs for evidence; cannot advance'],
        ['ADVANCED SET', 'needs DEVELOPMENT + TEST'], ['FORWARD SHADOW', 'only after advancement'], ['LIVE_CANDIDATE', 'PENDING'], ['MANUAL GO LIVE', 'operator only']]
      : [['DEVELOPMENT', 'see the DEVELOPMENT run'], ['TEST', adv ? 'PASS' : 'FAIL'], ['ADVANCED SET', 'needs both'], ['FORWARD SHADOW', 'only after advancement'],
        ['LIVE_CANDIDATE', 'PENDING'], ['MANUAL GO LIVE', 'operator only']];
    body.append(h('ol', { class: 'v3-pipeline' }, ...stages.map(([s, st]) => h('li', { class: st.startsWith('PASS') ? 'up' : st.startsWith('FAIL') ? 'down' : '' },
      h('b', { text: s }), h('span', { text: st })))),
      h('p', { class: 'sub', text: 'DEVELOPMENT can only earn a bot the right to be judged on the pre-registered TEST. Nothing here trades real money; live capital always needs an operator.' }));
  },
};
