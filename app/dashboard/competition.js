/* PaperLab - Competition tab.
   Uses the same helpers as the rest of the dashboard (h/renderTable/pill/tile/fmt* from charts.js)
   and the same auth'd api()/post() from app.js, so this is a new section of PaperLab rather than a
   second application. Loaded before app.js; app.js calls Competition.init() and routes the tab. */
'use strict';

const COMP_TABS = ['overview', 'arena', 'jev', 'leaderboard', 'bots', 'seasons', 'execution', 'validation', 'settings'];

/* Qualification states carry meaning, so they get deliberate colours. Green is reserved for states
   that actually passed the gates: a profitable bot that failed qualification must NOT look green. */
const STATE_KIND = {
  QUALIFIED: 'up', SHADOW_LIVE: 'up', LIVE_CANDIDATE: 'up',
  WATCHLIST: 'warn', INSUFFICIENT_SAMPLE: 'warn',
  FAILED: 'down', DISQUALIFIED: 'down',
  COMPETING: 'accent', ADVANCE: 'up', NOT_ENTERED: 'ghost', ERROR: 'down',
};
const STATE_HELP = {
  QUALIFIED: 'passed every hard gate on out-of-sample evidence',
  SHADOW_LIVE: 'running forward on live data with simulated execution',
  LIVE_CANDIDATE: 'forward edge held; awaiting operator approval — nothing is promoted automatically',
  WATCHLIST: 'passed the hard gates but a stage has not been run, or a soft gate failed',
  INSUFFICIENT_SAMPLE: 'too few closed trades to judge; it can still appear on the leaderboard',
  FAILED: 'judged and failed a hard gate',
  DISQUALIFIED: 'liquidated or crossed the catastrophic-equity floor',
  COMPETING: 'trading the season, not yet judged',
  ADVANCE: 'passed every discovery gate: earns multi-year validation. NOT qualified, NOT live.',
  NOT_ENTERED: 'failed preflight; does not count toward the field',
  ERROR: 'the replay itself failed; see the reason',
};
const COST_KIND = { HEALTHY: 'up', MARGINAL: 'warn', FEE_DESTROYED: 'warn', GROSS_NEGATIVE: 'down', NO_TRADES: 'ghost' };
const COST_HELP = { HEALTHY: 'net positive, costs well inside the gross edge', MARGINAL: 'net positive, but costs take more than half the gross edge',
  FEE_DESTROYED: 'positive at decision prices; fees and slippage ate the edge', GROSS_NEGATIVE: 'the trading logic loses before any cost', NO_TRADES: 'nothing to judge' };
const costPill = (c) => pill((c || 'NO_TRADES').replace('_', '-'), COST_KIND[c] || 'ghost', COST_HELP[c] || '');
const stateBadge = (s) => pill(s || 'COMPETING', STATE_KIND[s] || 'ghost', STATE_HELP[s] || '');
const pctText = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + '%' : DASH);
const pfCell = (v) => (v == null ? DASH : v >= 999 ? '∞' : v.toFixed(2));
/** Bot keys for a tile caption: short form, at most `max`, then a count -- a tile is not a table. */
const keyList = (keys, max = 4) => {
  const k = (keys || []).map((x) => String(x).split('@')[0]);
  return k.length ? k.slice(0, max).join(', ') + (k.length > max ? ' +' + (k.length - max) + ' more' : '') : 'none';
};
const fmtTs = (ms) => (ms ? new Date(ms).toISOString().slice(0, 16).replace('T', ' ') : DASH);

const Competition = {
  ctab: 'overview',
  data: null, lb: null, seasons: null, exec: null,
  bot: null, botId: '', trades: null, tradeFilter: {}, tradePage: 0,
  arena: null, arenaBot: '', arenaRun: '', arenaFilter: { coin: '', tf: '', strategy: '', state: '' },
  jevData: null, jevPair: '', jevActionFilter: '',
  timer: 0, active: false, busy: false, sort: 'score', runId: '', lbTried: false,
  // Set by public.js for the unauthenticated inspection page. Same components, different base
  // and no mutating controls -- the UI is parameterised, not forked.
  base: '/api/competition', readOnly: false,

  /** Season selector as a query string. Empty means "the most recent season". */
  q(sep) { return this.runId ? (sep || '?') + 'run_id=' + encodeURIComponent(this.runId) : ''; },

  init() {
    $$('#comp-tabs [data-ctab]').forEach((b) => b.addEventListener('click', () => this.showTab(b.dataset.ctab)));
    const sel = $('#comp-sort');
    if (sel) sel.addEventListener('change', () => { this.sort = sel.value; this.loadLeaderboard(); });
  },

  showTab(name) {
    if (!COMP_TABS.includes(name)) name = 'overview';
    this.ctab = name;
    $$('#comp-tabs [data-ctab]').forEach((b) => b.classList.toggle('active', b.dataset.ctab === name));
    $$('#main [data-cpanel]').forEach((p) => { p.hidden = p.dataset.cpanel !== name; });
    this.refresh();
  },

  activate() { this.active = true; },
  deactivate() { this.active = false; clearTimeout(this.timer); this.timer = 0; },

  /* No polling. Competition data changes when a season, arena, validation or Jev run moves; the
     server watches those and pushes a `competition` event (app/core/realtime.py). This refetches
     the open view at most every 2 s while events keep arriving -- a running season still follows
     closely, and an idle page makes no requests at all. */
  onEvent(ev) {
    if (!this.active) return;
    if (ev && ev.kind === 'season' && this.data) {
      this.data.progress = Object.assign({}, this.data.progress || {}, ev);
      this.renderProgress();
    }
    if (this.timer) return;
    this.timer = setTimeout(() => { this.timer = 0; this.refresh(true); }, 2000);
  },

  async refresh(fromPoll) {
    if (this.busy || !this.active || Auth.get() == null) return;
    this.busy = true;
    const quietArena = !!fromPoll;
    try {
      this.data = await api(this.base + this.q());
      this.renderProgress();
      if (this.ctab === 'overview') this.renderOverview();
      else if (this.ctab === 'arena') { if (!this.arena || !quietArena) await this.renderArena(); }
      else if (this.ctab === 'jev') { if (!this.jevData || !quietArena) await this.renderJev(); }
      else if (this.ctab === 'leaderboard') await this.loadLeaderboard();
      else if (this.ctab === 'bots') await this.renderBots();
      else if (this.ctab === 'seasons') await this.loadSeasons();
      else if (this.ctab === 'execution') await this.loadExecution();
      else if (this.ctab === 'validation') await this.renderValidation();
      else this.renderSettings();
    } catch (e) {
      if (e.status !== 401) this.fail($('#comp-overview'), 'competition overview', e);
    } finally { this.busy = false; }
  },

  render() { if (this.active) this.refresh(); },

  /** A failed fetch must never render as an empty card: say what broke and why. */
  fail(container, what, err) {
    const msg = (err && err.message) || String(err || 'unknown error');
    const status = err && err.status ? ' (HTTP ' + err.status + ')' : '';
    console.error('[competition] ' + what + ' failed' + status, err);
    const card = h('div', { class: 'card' },
      h('h2', { text: 'could not load ' + what }),
      h('p', { class: 'msg err', text: msg + status }),
      h('p', { class: 'sub', text: 'Real data only: nothing is substituted when a request fails.' }));
    if (container) container.append(card);
    return card;
  },

  // ---- progress strip -------------------------------------------------------------------------
  renderProgress() {
    const strip = $('#comp-progress');
    const p = (this.data && this.data.progress) || {};
    const running = p.status === 'running';
    strip.hidden = !running && p.status !== 'error';
    if (strip.hidden) return;
    clear(strip);
    if (p.status === 'error') {
      strip.append(h('div', { class: 'msg err', text: 'competition failed: ' + (p.error || 'unknown') }));
      return;
    }
    const pct = Math.round((p.pct || 0) * 100);
    strip.append(
      h('div', { class: 'run-head' },
        pill('RUNNING', 'accent'),
        h('span', { class: 'sub', text: `${p.bots_done || 0}/${p.total_bots || 0} bots · ${p.trades || 0} trades · ${fmtDur(p.elapsed_s || 0)} elapsed` }),
        h('span', { class: 'spacer' }),
        h('span', { class: 'sub', text: 'replaying ' + fmtTs(p.replay_ts) }),
        this.readOnly ? pill('READ-ONLY INSPECTION', 'ghost')
          : h('button', { class: 'btn ghost', type: 'button', text: 'cancel', onclick: () => this.cancel() })),
      h('div', { class: 'progress' }, h('i', { style: 'width:' + pct + '%' })),
      h('div', { class: 'sub', text: p.current_bot ? 'current: ' + p.current_bot : '' }));
    if ((p.partial || []).length) {
      const t = h('table', { class: 'tbl lb' });
      strip.append(h('div', { class: 'sub', text: 'provisional leaderboard' }), h('div', { class: 'tablewrap' }, t));
      renderTable(t, [
        { h: '#', cls: 'rank', cell: (r, i) => String(i + 1) },
        { h: 'bot', cell: (r) => r.strategy_id },
        { h: 'net', num: true, cell: (r) => num(r.net_pnl) },
        { h: 'trades', num: true, cell: (r) => String(r.trades ?? DASH) },
        { h: 'state', cell: (r) => stateBadge(r.state) },
      ], p.partial, { empty: 'no bot has finished yet' });
    }
  },

  async cancel() { try { await post(this.base + '/cancel'); toast('cancelling after the current bot'); } catch (e) { toast(e.message, 'err'); } },

  // ---- overview ---------------------------------------------------------------------------------
  renderOverview() {
    const g = clear($('#comp-overview'));
    const d = this.data || {};
    if (!d.ran) { g.append(this.emptyCard()); return; }
    const s = d.season || {}, st = d.states || {}, t = d.totals || {};

    const head = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'season ' + (s.label || s.season_id || DASH) }),
      h('span', { class: 'sub', text: 'fingerprint ' + (s.fingerprint || DASH) })));
    head.append(kvList({
      'competition': s.competition_id, 'season': s.season_id, 'status': s.status,
      'starting balance': fmtMoney(s.starting_balance) + ' USDT', 'venue': s.venue,
      'symbols': (s.symbols || []).join(', '), 'timeframe': s.timeframe,
      'max leverage': (s.max_leverage || DASH) + 'x',
      'execution': s.execution_profile_label || s.execution_profile,
      'bots': t.bots, 'bars replayed': s.bars,
      'window': fmtTs(s.start_ms) + '  →  ' + fmtTs(s.end_ms),
      'runtime': fmtDur(((s.finished_ts || 0) - (s.created_ts || 0)) / 1000),
    }));
    g.append(head);

    const cards = h('div', { class: 'card c8' }, h('h2', { text: 'qualification states' }));
    const tiles = h('div', { class: 'tiles' });
    for (const k of ['COMPETING', 'QUALIFIED', 'WATCHLIST', 'INSUFFICIENT_SAMPLE', 'FAILED', 'DISQUALIFIED', 'SHADOW_LIVE', 'LIVE_CANDIDATE']) {
      tiles.append(tile(k.replace(/_/g, ' ').toLowerCase(), String(st[k] ?? 0),
        st[k] ? (STATE_KIND[k] || '') : 'muted', STATE_HELP[k]));
    }
    cards.append(tiles);
    g.append(cards);

    const costs = h('div', { class: 'card c4' }, h('h2', { text: 'competition totals' }));
    costs.append(kvList({
      'trades': t.trades, 'gross PnL': fmtMoney(t.gross_pnl, true), 'net PnL': fmtMoney(t.net_profit, true),
      'fees paid': fmtMoney(-Math.abs(t.fees_paid || 0)), 'slippage': fmtMoney(-Math.abs(t.slippage_cost || 0)),
      'funding': fmtMoney(t.funding_paid, true), 'liquidations': t.liquidation_count,
    }));
    g.append(costs);
    g.append(this.latestCard());
  },

  /* "Latest competition" — the real top rows of the stored season, never hard-coded values. */
  latestCard() {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'latest competition' }),
      h('span', { class: 'sub', text: 'click a bot for its full account' })));
    const t = h('table', { class: 'tbl lb' });
    card.append(h('div', { class: 'tablewrap' }, t));
    const rows = (this.lb && this.lb.rows) || [];
    // Fetch the rows once if the overview was opened first; without the guard an empty leaderboard
    // would re-enter renderOverview on every response and spin.
    if (!rows.length && !this.lbTried) { this.lbTried = true; this.loadLeaderboard(true); }
    renderTable(t, this.lbCols(true), rows.slice(0, 8), {
      empty: 'open the Leaderboard tab', onRow: (r) => this.openBot(r.strategy_id),
    });
    return card;
  },

  emptyCard() {
    const d = this.data || {}, data = d.data || {};
    const card = h('div', { class: 'card' }, h('h2', { text: 'no competition has been run yet' }));
    card.append(h('p', { class: 'sub', text: data.ready
      ? 'Stored candles cover ' + fmtTs(data.overlap_start_ms) + ' → ' + fmtTs(data.overlap_end_ms) + '. Start a season from Settings.'
      : 'No overlapping candle window is stored yet. The live feed backfills candles into the database; once every symbol has history a season can be replayed.' }));
    const rows = Object.entries(data.symbols || {}).map(([sym, v]) => ({ sym, ...v }));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'symbol', cell: (r) => r.sym },
      { h: 'bars', num: true, cell: (r) => String(r.bars ?? 0) },
      { h: 'first', cell: (r) => fmtTs(r.first_ms) },
      { h: 'last', cell: (r) => fmtTs(r.last_ms) },
    ], rows, { empty: 'no candles stored' });
    return card;
  },

  // ---- leaderboard -------------------------------------------------------------------------------
  lbCols(compact) {
    const cols = [
      { h: '#', cls: 'rank', cell: (r) => String(r.rank) },
      { h: 'bot', cell: (r) => h('span', null, h('b', { text: r.strategy_id }),
          h('span', { class: 'sub sname', text: ' ' + truncate(r.name || '', compact ? 18 : 30) })) },
      // Equity is a balance, not a delta: it is always positive, so colouring it by its own
      // sign painted every losing bot green. Colour by the net result instead, and drop the
      // leading '+' that made a balance look like a gain.
      { h: 'equity', num: true, cell: (r) => h('span', {
          class: 'num ' + signClass(r.metrics.net_profit),
          text: fmtMoney(r.metrics.ending_equity) }) },
      { h: 'gross', num: true, cls: 'gross', cell: (r) => num(r.metrics.gross_pnl) },
      { h: 'net', num: true, cell: (r) => num(r.metrics.net_profit) },
      { h: 'ret%', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.metrics.net_return_pct), text: pctText(r.metrics.net_return_pct) }) },
      { h: 'trades', num: true, cell: (r) => String(r.metrics.trades ?? DASH) },
      { h: 'maxDD', num: true, cell: (r) => h('span', { class: 'num down', text: pctText(r.metrics.max_drawdown_pct) }) },
      { h: 'qualification', cell: (r) => h('span', null, stateBadge(r.state),
          r.reasons && r.reasons.length ? h('span', { class: 'sub why', text: ' ' + truncate(r.reasons[0], 34) }) : null) },
    ];
    if (compact) return cols;
    return [
      cols[0], cols[1],
      { h: 'start', num: true, cell: (r) => fmtMoney(r.metrics.starting_equity) },
      cols[2], cols[3],
      { h: 'fees', num: true, cell: (r) => h('span', { class: 'num down', text: fmtMoney(-Math.abs(r.metrics.fees_paid || 0)) }) },
      { h: 'slip', num: true, cell: (r) => h('span', { class: 'num down', text: fmtMoney(-Math.abs(r.metrics.slippage_cost || 0)) }) },
      { h: 'funding', num: true, cell: (r) => num(r.metrics.funding_paid) },
      cols[4], cols[5], cols[6],
      { h: 'win%', num: true, cell: (r) => pctText(r.metrics.win_rate, 0) },
      { h: 'expR', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.metrics.expectancy_r), text: fmtNum(r.metrics.expectancy_r, 2) }) },
      { h: 'PF', num: true, cell: (r) => pfCell(r.metrics.profit_factor) },
      cols[7],
      { h: 'maxLev', num: true, cell: (r) => (r.metrics.max_leverage_used || DASH) + 'x' },
      { h: 'score', num: true, cell: (r) => h('span', { class: 'num link-ish', title: 'click the row, then open Score', text: fmtNum(r.score, 3) }) },
      cols[8],
    ];
  },

  async loadLeaderboard(quiet) {
    this.lbTried = true;
    try { this.lb = await api(this.base + '/leaderboard?sort=' + encodeURIComponent(this.sort) + this.q('&')); }
    catch (e) { if (e.status !== 401) this.fail($('#comp-lb-table').parentElement, 'leaderboard', e); return; }
    if (quiet) { if (this.ctab === 'overview') this.renderOverview(); return; }
    const rows = this.lb.rows || [];
    setText($('#comp-lb-sub'), rows.length ? rows.length + ' bots · rank is not qualification' : '');
    renderTable($('#comp-lb-table'), this.lbCols(false), rows, {
      empty: 'no competition has been run yet',
      rowClass: (r) => (r.state === 'DISQUALIFIED' || r.state === 'FAILED' ? 'halted' : ''),
      onRow: (r) => this.openBot(r.strategy_id),
    });
    const sk = this.lb.skipped || [];
    $('#comp-skipped-card').hidden = !sk.length;
    if (sk.length) {
      const box = clear($('#comp-skipped'));
      sk.forEach((s) => box.append(h('div', { class: 'sub' },
        h('b', { text: s.strategy_id + ' ' }), h('span', { text: (s.name || '') + ' — needs ' + s.skipped }))));
    }
  },

  // ---- bots (detail) ------------------------------------------------------------------------------
  async openBot(sid) {
    this.botId = sid; this.tradePage = 0; this.tradeFilter = {};
    this.showTab('bots');
  },

  async renderBots() {
    const g = clear($('#comp-bots'));
    if (!this.botId) {
      if (!this.lb) await this.loadLeaderboard(true);
      const rows = (this.lb && this.lb.rows) || [];
      const card = h('div', { class: 'card' }, h('h2', { text: 'pick a bot' }));
      const t = h('table', { class: 'tbl lb' });
      card.append(h('div', { class: 'tablewrap' }, t));
      renderTable(t, this.lbCols(true), rows, { empty: 'no competition has been run yet', onRow: (r) => this.openBot(r.strategy_id) });
      g.append(card);
      return;
    }
    let bot, qual;
    try {
      [bot, qual] = await Promise.all([
        api(this.base + '/bots/' + encodeURIComponent(this.botId) + this.q()),
        api(this.base + '/bots/' + encodeURIComponent(this.botId) + '/qualification' + this.q()),
      ]);
    } catch (e) {
      g.append(h('div', { class: 'card' }, h('h2', { text: this.botId }),
        h('p', { class: 'msg err', text: e.message })));
      return;
    }
    const b = bot.bot, m = b.metrics || {};
    g.append(this.botHeader(b, m));
    g.append(this.accountCard(b, m));
    g.append(this.qualCard(qual));
    g.append(this.scoreCard(qual.score_breakdown));
    g.append(this.perfCard(m));
    g.append(this.riskCard(m));
    g.append(this.execCard(m, b.execution_levels));
    g.append(this.symbolCard(m));
    g.append(await this.ledgerCard());
  },

  botHeader(b, m) {
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' },
      h('h2', null, h('b', { text: b.strategy_id }), h('span', { text: ' ' + (b.name || '') })),
      h('span', { class: 'sub', text: 'version ' + (b.version || DASH) }),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn ghost', type: 'button', text: '← all bots', onclick: () => { this.botId = ''; this.renderBots(); } })));
    const tiles = h('div', { class: 'tiles' });
    tiles.append(tile('rank', b.rank == null ? DASH : '#' + b.rank));
    tiles.append(tile('equity', fmtMoney(m.ending_equity), signClass(m.net_profit)));
    tiles.append(tile('return', pctText(m.net_return_pct), signClass(m.net_return_pct)));
    tiles.append(tile('max drawdown', pctText(m.max_drawdown_pct), 'down'));
    tiles.append(tile('trades', String(m.trades ?? 0), m.trades >= 100 ? '' : 'warn'));
    card.append(tiles, h('div', { class: 'badges' }, stateBadge(b.state)));
    return card;
  },

  /* The 20 USDT account, top to bottom. This is the card that answers "where did the money go". */
  accountCard(b, m) {
    const card = h('div', { class: 'card c6' }, h('div', { class: 'card-head' },
      h('h2', { text: 'the ' + fmtMoney(b.starting_balance) + ' USDT account' }),
      h('span', { class: 'sub', text: 'gross → net' })));
    const line = (label, v, cls) => h('div', { class: 'ledger-line ' + (cls || '') },
      h('span', { text: label }), h('span', { class: 'num ' + (cls || signClass(v)), text: fmtMoney(v, true) }));
    const box = h('div', { class: 'ledger' });
    box.append(h('div', { class: 'ledger-line head' },
      h('span', { text: 'starting balance' }), h('span', { class: 'num', text: fmtMoney(b.starting_balance) })));
    box.append(line('gross trading PnL (at decision prices)', m.gross_pnl));
    box.append(line('commission', -Math.abs(m.fees_paid || 0), 'down'));
    box.append(line('spread crossed', -Math.abs(m.spread_cost || 0), 'down'));
    box.append(line('latency in flight', -Math.abs(m.latency_cost || 0), 'down'));
    box.append(line('market impact', -Math.abs(m.impact_cost || 0), 'down'));
    box.append(line('funding', m.funding_paid));
    box.append(h('div', { class: 'ledger-line total' },
      h('span', { text: 'current equity' }),
      h('span', { class: 'num ' + signClass(m.net_profit), text: fmtMoney(m.ending_equity) })));
    card.append(box);
    card.append(h('p', { class: 'sub', text:
      'Spread, latency and impact are a decomposition of total slippage (' + fmtMoney(-Math.abs(m.slippage_cost || 0)) + '), not extra charges on top of it.' }));
    return card;
  },

  qualCard(q) {
    const card = h('div', { class: 'card c6' }, h('div', { class: 'card-head' },
      h('h2', { text: 'why did this bot pass or fail?' }), stateBadge(q.state)));
    const list = h('div', { class: 'gates' });
    (q.gates || []).forEach((g) => {
      const kind = g.ok ? 'up' : (g.soft ? 'warn' : 'down');
      list.append(h('div', { class: 'gate' },
        pill(g.ok ? 'PASS' : (g.soft ? 'SOFT' : 'FAIL'), kind),
        h('span', { class: 'gate-name', text: g.name.replace(/_/g, ' ') }),
        h('span', { class: 'num sub', text: fmtVal(g.actual) + ' ' + g.comparison + ' ' + fmtVal(g.threshold) })));
    });
    card.append(list);
    card.append(h('h3', { text: 'validation stages' }));
    const stages = h('div', { class: 'gates' });
    (q.stages || []).forEach((s) => {
      const ran = s.status === 'RAN';
      stages.append(h('div', { class: 'gate' },
        pill(ran ? 'RAN' : 'NOT RUN', ran ? 'accent' : 'ghost'),
        h('span', { class: 'gate-name', text: s.name.replace(/_/g, ' ') })));
    });
    card.append(stages);
    if ((q.reasons || []).length) {
      card.append(h('p', { class: 'msg warn', text: 'not qualified: ' + q.reasons.join('; ') }));
    }
    return card;
  },

  scoreCard(b) {
    const card = h('div', { class: 'card c6' }, h('div', { class: 'card-head' },
      h('h2', { text: 'competition score' }),
      h('span', { class: 'num', text: b ? fmtNum(b.total, 3) : DASH })));
    if (!b) { card.append(h('p', { class: 'sub', text: 'no score' })); return card; }
    const bars = h('div', { class: 'bars' });
    const add = (k, v, cls) => bars.append(h('div', { class: 'bar-row' },
      h('span', { class: 'bar-label', text: k.replace(/_/g, ' ') }),
      h('span', { class: 'bar' }, h('i', { class: cls, style: 'width:' + Math.min(100, Math.abs(v) * 200) + '%' })),
      h('span', { class: 'num ' + cls, text: (v >= 0 ? '+' : '') + v.toFixed(3) })));
    Object.entries(b.components || {}).forEach(([k, v]) => add(k, v, 'up'));
    Object.entries(b.penalties || {}).forEach(([k, v]) => { if (v) add(k + ' penalty', -v, 'down'); });
    card.append(bars);
    card.append(h('p', { class: 'sub', text: 'The score orders the leaderboard. It is never a gate — qualification is decided above.' }));
    return card;
  },

  perfCard(m) {
    const card = h('div', { class: 'card c6' }, h('h2', { text: 'performance' }));
    card.append(kvList({
      'total trades': m.trades, wins: m.wins, losses: m.losses, 'win rate': pctText(m.win_rate, 0),
      'avg winner': fmtMoney(m.average_win, true), 'avg loser': fmtMoney(m.average_loss, true),
      'expectancy': fmtMoney(m.expectancy_usdt, true), 'expectancy R': fmtNum(m.expectancy_r, 3),
      'profit factor': pfCell(m.profit_factor), 'median R': fmtNum(m.median_r, 3),
      'best trade': fmtMoney(m.best_trade, true), 'worst trade': fmtMoney(m.worst_trade, true),
      'longest losing streak': m.longest_loss_streak, 'longest winning streak': m.longest_win_streak,
      'avg hold': fmtDur((m.average_holding_ms || 0) / 1000),
    }));
    return card;
  },

  riskCard(m) {
    const card = h('div', { class: 'card c6' }, h('h2', { text: 'risk' }));
    card.append(kvList({
      'max drawdown': pctText(m.max_drawdown_pct) + ' (' + fmtMoney(m.max_drawdown_usdt) + ')',
      'drawdown duration': fmtDur((m.drawdown_duration_ms || 0) / 1000),
      'recovery factor': fmtNum(m.recovery_factor, 2),
      'max leverage used': (m.max_leverage_used || DASH) + 'x',
      'turnover': fmtNum(m.turnover, 1) + 'x',
      'total notional traded': fmtMoney(m.total_notional_traded),
      'liquidations': m.liquidation_count,
      'sharpe-like': fmtNum(m.sharpe_like, 3), 'sortino-like': fmtNum(m.sortino_like, 3),
    }));
    if (m.liquidation_count) card.append(h('p', { class: 'msg err', text: 'liquidated — disqualified for this season' }));
    return card;
  },

  execCard(m, levels) {
    const card = h('div', { class: 'card c6' }, h('h2', { text: 'execution' }));
    card.append(kvList({
      'taker fills': pctText(m.taker_percentage, 0), 'maker fills': pctText(m.maker_percentage, 0),
      'commission': fmtMoney(-Math.abs(m.fees_paid || 0)),
      'spread cost': fmtMoney(-Math.abs(m.spread_cost || 0)),
      'latency cost': fmtMoney(-Math.abs(m.latency_cost || 0)),
      'market impact': fmtMoney(-Math.abs(m.impact_cost || 0)),
      'fee / gross profit': pctText(m.fee_to_gross_profit_ratio, 0),
      'slippage / gross profit': pctText(m.slippage_to_gross_profit_ratio, 0),
    }));
    const lv = levels || {};
    const tiles = h('div', { class: 'tiles small' });
    tiles.append(tile('L1 order book', String(lv['1'] || 0), lv['1'] ? 'accent' : 'muted', 'real book walk'));
    tiles.append(tile('L2 bid/ask', String(lv['2'] || 0), lv['2'] ? 'accent' : 'muted', 'quote available'));
    tiles.append(tile('L3 OHLCV model', String(lv['3'] || 0), lv['3'] ? 'warn' : 'muted', 'modelled from candles'));
    card.append(h('h3', { text: 'fills by execution level' }), tiles);
    if (lv['3'] && !lv['1']) {
      card.append(h('p', { class: 'sub', text:
        'Historical seasons run at L3: the archive publishes no historical order books, so these fills are modelled from candles — not order-book fills.' }));
    }
    return card;
  },

  symbolCard(m) {
    const card = h('div', { class: 'card c6' }, h('h2', { text: 'by symbol' }));
    const rows = Object.entries(m.by_symbol || {}).map(([sym, v]) => ({ sym, ...v }));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'symbol', cell: (r) => r.sym },
      { h: 'trades', num: true, cell: (r) => String(r.trades) },
      { h: 'net', num: true, cell: (r) => num(r.net) },
      { h: 'total R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.r), text: fmtNum(r.r, 2) }) },
    ], rows, { empty: 'no trades' });
    return card;
  },

  // ---- trade ledger (server-side pagination) ---------------------------------------------------
  async ledgerCard() {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'trade ledger' }), h('span', { class: 'sub', id: 'comp-ledger-sub' })));
    const bar = h('div', { class: 'toolbar inline' });
    const mk = (key, label, opts) => {
      const sel = h('select', { onchange: (e) => { this.tradeFilter[key] = e.target.value; this.tradePage = 0; this.renderBots(); } },
        ...opts.map((o) => h('option', { value: o[0], text: o[1], selected: this.tradeFilter[key] === o[0] })));
      return h('label', { class: 'field inline' }, h('span', { text: label }), sel);
    };
    bar.append(
      mk('symbol', 'symbol', [['', 'all'], ...(((this.data || {}).season || {}).symbols || []).map((s) => [s, s])]),
      mk('side', 'side', [['', 'all'], ['BUY', 'buy'], ['SELL', 'sell']]),
      mk('outcome', 'result', [['', 'all'], ['win', 'profitable'], ['loss', 'losing']]),
      mk('role', 'liquidity', [['', 'all'], ['MAKER', 'maker'], ['TAKER', 'taker']]),
      mk('level', 'exec level', [['', 'all'], ['1', 'L1 book'], ['2', 'L2 quote'], ['3', 'L3 model']]));
    card.append(bar);

    const q = new URLSearchParams({ offset: String(this.tradePage * 100), limit: '100' });
    Object.entries(this.tradeFilter).forEach(([k, v]) => { if (v) q.set(k, v); });
    if (this.runId) q.set('run_id', this.runId);
    let data = { rows: [], total: 0 };
    try { data = await api(this.base + '/bots/' + encodeURIComponent(this.botId) + '/trades?' + q); }
    catch (e) { card.append(h('p', { class: 'msg err', text: e.message })); return card; }
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'time', cell: (r) => fmtTs(r.ts) },
      { h: 'symbol', cell: (r) => r.symbol },
      { h: 'kind', cell: (r) => pill(r.kind, r.kind === 'entry' ? 'accent' : (r.kind === 'liq' ? 'down' : 'ghost')) },
      { h: 'side', cell: (r) => r.side },
      { h: 'decision', num: true, cell: (r) => fmtPrice(r.decision_price) },
      { h: 'fill', num: true, cell: (r) => fmtPrice(r.price) },
      { h: 'qty', num: true, cell: (r) => fmtQty(r.qty) },
      { h: 'lev', num: true, cell: (r) => (r.leverage || DASH) + 'x' },
      { h: 'role', cell: (r) => (r.liquidity_role ? pill(r.liquidity_role, r.liquidity_role === 'MAKER' ? 'up' : 'ghost') : DASH) },
      { h: 'exec', num: true, cell: (r) => (r.execution_level ? 'L' + r.execution_level : DASH) },
      { h: 'fee', num: true, cell: (r) => h('span', { class: 'num down', text: fmtMoney(-Math.abs(r.fee || 0)) }) },
      { h: 'spread', num: true, cell: (r) => fmtNum(-Math.abs(r.spread_cost || 0), 4) },
      { h: 'latency', num: true, cell: (r) => fmtNum(-Math.abs(r.latency_cost || 0), 4) },
      { h: 'impact', num: true, cell: (r) => fmtNum(-Math.abs(r.impact_cost || 0), 4) },
      { h: 'realised', num: true, cell: (r) => num(r.realized_pnl) },
    ], data.rows || [], { empty: 'no fills match' });

    const pages = Math.max(1, Math.ceil((data.total || 0) / 100));
    const sub = $('#comp-ledger-sub', card);
    if (sub) setText(sub, (data.total || 0) + ' fills · server-side paging');
    card.append(h('div', { class: 'btn-row' },
      h('button', { class: 'btn ghost', type: 'button', text: '← prev', disabled: this.tradePage <= 0,
        onclick: () => { this.tradePage--; this.renderBots(); } }),
      h('span', { class: 'sub', text: `page ${this.tradePage + 1} / ${pages} · ${data.total} fills` }),
      h('button', { class: 'btn ghost', type: 'button', text: 'next →', disabled: this.tradePage + 1 >= pages,
        onclick: () => { this.tradePage++; this.renderBots(); } })));
    return card;
  },

  // ---- seasons ------------------------------------------------------------------------------------
  async loadSeasons() {
    try { this.seasons = await api(this.base + '/seasons'); }
    catch (e) { this.fail($('#comp-seasons-table').parentElement, 'seasons', e); return; }
    const rows = this.seasons.seasons || [];
    setText($('#comp-seasons-sub'), rows.length + ' stored');
    renderTable($('#comp-seasons-table'), [
      { h: 'season', cell: (r) => r.label || r.season_id },
      { h: 'window', cell: (r) => fmtTs(r.start_ms) + ' → ' + fmtTs(r.end_ms) },
      { h: 'symbols', cell: (r) => (r.symbols || []).join(' ') },
      { h: 'balance', num: true, cell: (r) => fmtMoney(r.starting_balance) },
      { h: 'maxLev', num: true, cell: (r) => (r.max_leverage || DASH) + 'x' },
      { h: 'execution', cell: (r) => pill(r.execution_profile || DASH, r.execution_profile === 'legacy' ? 'warn' : 'accent', r.profile_label) },
      { h: 'bots', num: true, cell: (r) => String(r.bots) },
      { h: 'qualified', num: true, cell: (r) => h('span', { class: r.qualified ? 'num up' : 'num muted', text: String(r.qualified) }) },
      { h: 'rank leader', cell: (r) => (r.leader ? h('span', null, h('b', { text: r.leader.strategy_id }), ' ', stateBadge(r.leader.state)) : DASH) },
      { h: 'status', cell: (r) => pill(r.status || DASH, r.status === 'done' ? 'up' : (r.status === 'error' ? 'down' : 'ghost')) },
    ], rows, { empty: 'no seasons stored yet', onRow: (r) => { this.runId = r.run_id; this.botId = ''; this.lb = null; this.showTab('leaderboard'); } });
  },

  // ---- execution comparison -------------------------------------------------------------------------
  async loadExecution() {
    const g = clear($('#comp-exec'));
    let d;
    try { d = await api(this.base + '/execution-comparison'); }
    catch (e) { this.fail(g, 'execution comparison', e); return; }
    if (!d.available) {
      g.append(h('div', { class: 'card' }, h('h2', { text: 'execution comparison' }),
        h('p', { class: 'sub', text: d.reason }),
        h('p', { class: 'sub', text: 'Run the same window twice from Settings — once on the realistic profile, once on legacy — to see what unrealistic execution does to the ranking.' })));
      return;
    }
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'old vs new execution' }),
      h('span', { class: 'sub', text: 'legacy (flat 2bps, no latency, flat 2.5% margin)  →  realistic (tick spread, 400ms latency, bracket margin)' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    const pair = (o, n, fmt, invert) => h('span', null,
      h('span', { class: 'num was', text: fmt(o) }), h('span', { class: 'arrow', text: ' → ' }),
      h('span', { class: 'num ' + signClass(invert ? -(n || 0) : n), text: fmt(n) }));
    renderTable(t, [
      { h: 'bot', cell: (r) => h('span', null, h('b', { text: r.strategy_id }), h('span', { class: 'sub', text: ' ' + truncate(r.name || '', 20) })) },
      { h: 'rank', cell: (r) => h('span', null, h('span', { class: 'was', text: '#' + (r.old_rank ?? DASH) }), ' → ',
          h('b', { class: r.rank_delta < 0 ? 'down' : (r.rank_delta > 0 ? 'up' : ''), text: '#' + (r.new_rank ?? DASH) })) },
      { h: 'return %', num: true, cell: (r) => pair(r.old_net_return_pct, r.new_net_return_pct, (v) => pctText(v)) },
      { h: 'net', num: true, cell: (r) => pair(r.old_net_profit, r.new_net_profit, (v) => fmtMoney(v, true)) },
      { h: 'trades', num: true, cell: (r) => pair(r.old_trades, r.new_trades, (v) => String(v ?? DASH)) },
      { h: 'fees', num: true, cell: (r) => pair(-Math.abs(r.old_fees_paid || 0), -Math.abs(r.new_fees_paid || 0), (v) => fmtMoney(v)) },
      { h: 'slippage', num: true, cell: (r) => pair(-Math.abs(r.old_slippage_cost || 0), -Math.abs(r.new_slippage_cost || 0), (v) => fmtMoney(v)) },
      { h: 'maxDD', num: true, cell: (r) => pair(r.old_max_drawdown_pct, r.new_max_drawdown_pct, (v) => pctText(v), true) },
      { h: 'liquidations', num: true, cell: (r) => pair(r.old_liquidation_count, r.new_liquidation_count, (v) => String(v ?? DASH), true) },
      { h: 'state', cell: (r) => h('span', null, stateBadge(r.old_state), h('span', { class: 'arrow', text: ' → ' }), stateBadge(r.new_state)) },
    ], d.rows || [], { empty: 'nothing to compare', rowClass: (r) => (r.flipped ? 'flipped' : '') });
    g.append(card);
    const flipped = (d.rows || []).filter((r) => r.flipped);
    if (flipped.length) {
      g.append(h('div', { class: 'card' }, h('h2', { text: 'fake winners' }),
        h('p', { class: 'sub', text: 'These bots changed sign when execution became realistic — they were profitable only because the old model under-charged for getting in and out.' }),
        ...flipped.map((r) => h('div', { class: 'msg warn' },
          `${r.strategy_id} ${r.name || ''}: ${pctText(r.old_net_return_pct)} → ${pctText(r.new_net_return_pct)}  (rank #${r.old_rank} → #${r.new_rank})`))));
    }
  },

  // ---- validation: multi-year walk-forward / Monte Carlo / stress ------------------------------
  vsort: 'score', vkey: '',

  async renderValidation() {
    const g = clear($('#comp-validation'));
    let v;
    try { v = await api(this.base + '/validation'); }
    catch (e) { this.fail(g, 'validation', e); return; }
    if (!v.ran) { g.append(this.validationEmpty(v)); return; }
    g.append(this.validationHeader(v));
    if (this.vkey) { await this.validationDetail(g); return; }
    await this.validationLeaderboard(g);
    await this.leverageCard(g);
    g.append(this.promotionCard());
  },

  validationEmpty(v) {
    const card = h('div', { class: 'card' }, h('h2', { text: 'multi-year validation' }));
    card.append(h('p', { class: 'sub', text:
      'No validation run yet. Walk-forward, Monte Carlo and stress have NOT been run, so no bot can reach QUALIFIED — judge() treats missing evidence as missing, not as a pass.' }));
    const box = h('div', { class: 'gates' });
    (v.stages || []).forEach((s) => box.append(h('div', { class: 'gate' },
      pill('NOT RUN', 'ghost'), h('span', { class: 'gate-name', text: s.replace(/_/g, ' ').toLowerCase() }))));
    card.append(box);
    card.append(h('p', { class: 'sub', text: 'Start one with: python scripts/run_validation.py --from 2021-01 --to 2026-08' }));
    return card;
  },

  validationHeader(v) {
    const card = h('div', { class: 'card' });
    const running = v.status === 'running';
    card.append(h('div', { class: 'card-head' },
      h('h2', { text: 'multi-year validation' }),
      pill(v.stage || v.status || '—', running ? 'accent' : (v.status === 'done' ? 'up' : 'warn')),
      h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: 'dataset ' + (v.dataset_fingerprint || '—') + ' · config ' + (v.config_fingerprint || '—') })));
    card.append(kvList({
      window: (v.first_month || '?') + ' → ' + (v.last_month || '?'),
      symbols: (v.symbols || []).join(', '),
      'walk-forward windows': v.windows,
      'leverage identities': (v.leverages || []).map((x) => x + 'x').join(' / '),
      'starting balance': fmtMoney(v.starting_balance) + ' USDT per window',
      competitors: (v.completed || 0) + ' / ' + (v.total_competitors || 0),
    }));
    const pct = Math.round((v.pct || 0) * 100);
    card.append(h('div', { class: 'progress' }, h('i', { style: 'width:' + pct + '%' })));
    card.append(h('div', { class: 'sub', text:
      `${v.completed || 0} / ${v.total_competitors || 0} competitors (${pct}%)`
      + (v.current ? ' · last: ' + v.current : '')
      + (v.elapsed_s ? ' · ' + fmtDur(v.elapsed_s) + ' elapsed' : '') }));
    const tiles = h('div', { class: 'tiles' });
    Object.entries(v.states || {}).forEach(([k, n]) =>
      tiles.append(tile(k.replace(/_/g, ' ').toLowerCase(), String(n), STATE_KIND[k] || 'muted', STATE_HELP[k])));
    if (Object.keys(v.states || {}).length) card.append(tiles);
    return card;
  },

  async validationLeaderboard(g) {
    let d;
    try { d = await api(this.base + '/validation/leaderboard?sort=' + encodeURIComponent(this.vsort)); }
    catch (e) { this.fail(g, 'out-of-sample leaderboard', e); return; }
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'out-of-sample leaderboard' }),
      h('span', { class: 'sub', text: 'every number from the master OOS ledger — training PnL is excluded' })));
    const t = h('table', { class: 'tbl lb' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: '#', cls: 'rank', cell: (r) => String(r.rank) },
      { h: 'competitor', cell: (r) => h('span', null, h('b', { text: r.key }),
          h('span', { class: 'sub sname', text: ' ' + truncate(r.name || '', 20) }),
          r.ran === false ? pill('not entered', 'ghost', r.skipped ? 'needs ' + r.skipped : '') : null) },
      { h: 'OOS net', num: true, cell: (r) => num(r.oos_net) },
      { h: 'OOS ret%', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.oos_return_pct), text: pctText(r.oos_return_pct) }) },
      { h: 'OOS trades', num: true, cell: (r) => h('span', { class: 'num ' + (r.oos_trades >= 100 ? '' : 'warn'), text: String(r.oos_trades ?? DASH) }) },
      { h: 'expR', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.oos_expectancy_r), text: fmtNum(r.oos_expectancy_r, 2) }) },
      { h: 'PF', num: true, cell: (r) => pfCell(r.oos_profit_factor) },
      { h: 'maxDD', num: true, cell: (r) => h('span', { class: 'num down', text: pctText(r.oos_max_dd) }) },
      { h: 'win+ windows', num: true, cell: (r) => `${r.profitable_windows ?? 0}/${r.active_windows ?? 0}` },
      { h: 'active', num: true, cell: (r) => pctText(r.active_ratio, 0) },
      { h: 'effLev', num: true, cell: (r) => fmtNum(r.avg_effective_leverage, 2) + 'x' },
      { h: 'MC ruin', num: true, cell: (r) => (r.mc_ran ? h('span', { class: 'num ' + (r.mc_ruin > 0.05 ? 'down' : 'up'), text: pctText(r.mc_ruin, 1) }) : pill('NOT RUN', 'ghost')) },
      { h: 'stress', cell: (r) => (r.stress_ran ? pill(r.stress_survives ? 'PASS' : 'FAIL', r.stress_survives ? 'up' : 'down', r.stress_worst || '') : pill('NOT RUN', 'ghost')) },
      { h: 'qualification', cell: (r) => h('span', null, stateBadge(r.state),
          (r.reasons || []).length ? h('span', { class: 'sub why', text: ' ' + truncate(r.reasons[0], 30) }) : null) },
    ], d.rows || [], {
      empty: 'no competitors finished yet',
      rowClass: (r) => (r.ran === false ? 'off' : (r.state === 'FAILED' || r.state === 'DISQUALIFIED' ? 'halted' : '')),
      onRow: (r) => { if (r.ran !== false) { this.vkey = r.key; this.renderValidation(); } },
    });
    g.append(card);

    const q = d.qualified || [];
    const qual = h('div', { class: 'card' }, h('h2', { text: 'qualified set' }));
    qual.append(q.length
      ? h('div', { class: 'badges' }, ...q.map((k) => pill(k, 'up')))
      : h('p', { class: 'msg warn', text: 'QUALIFIED = []  — no current PaperLab strategy has earned live trading privileges.' }));
    g.append(qual);

    const fr = d.failure_reasons || {};
    if (Object.keys(fr).length) {
      const card2 = h('div', { class: 'card' }, h('h2', { text: 'why competitors failed' }));
      const bars = h('div', { class: 'bars' });
      const max = Math.max(...Object.values(fr));
      Object.entries(fr).sort((a, b) => b[1] - a[1]).forEach(([k, n]) =>
        bars.append(h('div', { class: 'bar-row' },
          h('span', { class: 'bar-label', text: k.replace(/_/g, ' ') }),
          h('span', { class: 'bar' }, h('i', { class: 'down', style: 'width:' + (100 * n / max) + '%' })),
          h('span', { class: 'num', text: String(n) }))));
      card2.append(bars);
      g.append(card2);
    }
  },

  async leverageCard(g) {
    let d;
    try { d = await api(this.base + '/validation/leverage-comparison'); }
    catch (e) { this.fail(g, 'leverage diagnostic', e); return; }
    if (!d.ran || !(d.rows || []).length) return;
    const levs = (d.leverages || []).map(String);
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'leverage diagnostic' }),
      h('span', { class: 'sub', text: 'does leverage amplify a real edge, or just change which trades clear the minimum notional?' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    const cols = [{ h: 'strategy', cell: (r) => h('span', null, h('b', { text: r.strategy_id }),
      h('span', { class: 'sub', text: ' ' + truncate(r.name || '', 18) })) }];
    levs.forEach((L) => {
      cols.push({ h: L + 'x ret%', num: true, cell: (r) => { const v = r.variants[L];
        return v ? h('span', { class: 'num ' + signClass(v.oos_return_pct), text: pctText(v.oos_return_pct) }) : DASH; } });
      cols.push({ h: L + 'x trades', num: true, cell: (r) => { const v = r.variants[L]; return v ? String(v.trades ?? DASH) : DASH; } });
      cols.push({ h: L + 'x effLev', num: true, cell: (r) => { const v = r.variants[L]; return v ? fmtNum(v.avg_effective_leverage, 2) + 'x' : DASH; } });
      cols.push({ h: L + 'x minNot', num: true, cell: (r) => { const v = r.variants[L]; return v ? String(v.below_min_notional ?? 0) : DASH; } });
    });
    renderTable(t, cols, d.rows, { empty: 'no leverage variants yet' });
    card.append(h('p', { class: 'sub', text:
      'Diagnostic only. The highest ceiling is never treated as best — the competition score penalises leverage, and a variant that only looks better because more trades became eligible is an artefact, not an edge.' }));
    g.append(card);
  },

  async validationDetail(g) {
    let d;
    try { d = await api(this.base + '/validation/bots/' + encodeURIComponent(this.vkey)); }
    catch (e) { g.append(h('div', { class: 'card' }, h('p', { class: 'msg err', text: e.message }))); return; }
    const b = d.bot, m = b.oos_metrics || {}, w = b.walk_forward || {}, mc = b.monte_carlo || {}, s = b.stress || {}, q = b.qualification || {};
    const head = h('div', { class: 'card' });
    head.append(h('div', { class: 'card-head' },
      h('h2', null, h('b', { text: b.key })), h('span', { class: 'sub', text: b.name || '' }),
      h('span', { class: 'spacer' }), stateBadge(b.state),
      h('button', { class: 'btn ghost', type: 'button', text: '← all competitors',
        onclick: () => { this.vkey = ''; this.renderValidation(); } })));
    const tiles = h('div', { class: 'tiles' });
    tiles.append(tile('OOS net', fmtMoney(m.net_profit, true), signClass(m.net_profit)));
    tiles.append(tile('OOS trades', String(m.trades ?? 0), (m.trades || 0) >= 100 ? '' : 'warn'));
    tiles.append(tile('expectancy R', fmtNum(m.expectancy_r, 3), signClass(m.expectancy_r)));
    tiles.append(tile('profit factor', pfCell(m.profit_factor)));
    tiles.append(tile('OOS max DD', pctText(m.max_drawdown_pct), 'down'));
    tiles.append(tile('positive windows', `${w.profitable_windows ?? 0}/${w.active_windows ?? 0}`));
    head.append(tiles);
    g.append(head);

    // pipeline
    const pipe = h('div', { class: 'card c6' }, h('h2', { text: 'validation pipeline' }));
    const stages = [
      ['season replay', true, 'walk-forward replay over the full history'],
      ['walk-forward', (w.total_windows || 0) > 0, `${w.total_windows || 0} windows, ${w.active_windows || 0} active`],
      ['monte carlo', !!mc.ran, mc.ran ? `${mc.simulations} paths` : (mc.reason || 'not run')],
      ['stress', !!s.ran, s.ran ? `${(s.scenarios || []).length} scenarios` : 'not run'],
      ['shadow live', false, 'not run'],
    ];
    const box = h('div', { class: 'gates' });
    stages.forEach(([label, ran, note]) => box.append(h('div', { class: 'gate' },
      pill(ran ? 'RAN' : 'NOT RUN', ran ? 'accent' : 'ghost'),
      h('span', { class: 'gate-name', text: label }), h('span', { class: 'sub', text: note }))));
    pipe.append(box);
    g.append(pipe);

    // gates
    const gates = h('div', { class: 'card c6' }, h('div', { class: 'card-head' },
      h('h2', { text: 'qualification gates' }), stateBadge(b.state)));
    const gl = h('div', { class: 'gates' });
    (q.gates || []).forEach((x) => gl.append(h('div', { class: 'gate' },
      pill(x.ok ? 'PASS' : (x.soft ? 'SOFT' : 'FAIL'), x.ok ? 'up' : (x.soft ? 'warn' : 'down')),
      h('span', { class: 'gate-name', text: x.name.replace(/_/g, ' ') }),
      h('span', { class: 'num sub', text: fmtVal(x.actual) + ' ' + x.comparison + ' ' + fmtVal(x.threshold) }))));
    gates.append(gl);
    if ((q.reasons || []).length) gates.append(h('p', { class: 'msg warn', text: q.reasons.join('; ') }));
    g.append(gates);

    // windows
    const wins = h('div', { class: 'card' }, h('h2', { text: 'walk-forward windows' }));
    const wt = h('table', { class: 'tbl' });
    wins.append(h('div', { class: 'tablewrap' }, wt));
    renderTable(wt, [
      { h: '#', cls: 'rank', cell: (r) => String(r.index) },
      { h: 'OOS period', cell: (r) => fmtTs(r.test_start) + ' → ' + fmtTs(r.test_end) },
      { h: 'trades', num: true, cell: (r) => String(r.trades) },
      { h: 'net', num: true, cell: (r) => num(r.net) },
      { h: 'fees', num: true, cell: (r) => h('span', { class: 'num down', text: fmtMoney(-Math.abs(r.fees || 0)) }) },
      { h: 'slippage', num: true, cell: (r) => h('span', { class: 'num down', text: fmtMoney(-Math.abs(r.slippage || 0)) }) },
      { h: 'funding', num: true, cell: (r) => num(r.funding) },
      { h: 'result', cell: (r) => (!r.active ? pill('no trades', 'ghost') : pill(r.profitable ? 'profit' : 'loss', r.profitable ? 'up' : 'down')) },
    ], w.windows || [], { empty: 'no windows' });
    g.append(wins);

    // monte carlo
    const mcc = h('div', { class: 'card c6' }, h('h2', { text: 'monte carlo' }));
    if (!mc.ran) {
      mcc.append(h('p', { class: 'msg', text: 'NOT RUN — ' + (mc.reason || 'skipped: the competitor cannot qualify regardless of the outcome') }));
    } else {
      mcc.append(kvList({
        simulations: mc.simulations, mode: mc.mode,
        'median ending equity': fmtMoney(mc.median_ending_equity),
        'median max DD': pctText(mc.median_max_drawdown),
        '95th pct max DD': pctText(mc.p95_max_drawdown),
        '99th pct max DD': pctText(mc.p99_max_drawdown),
        'median losing streak': mc.median_longest_losing_streak,
        '95th pct losing streak': mc.p95_longest_losing_streak,
        'P(equity < 75%)': pctText((mc.prob_below || {})['0.75'], 1),
        'P(equity < 50%)': pctText((mc.prob_below || {})['0.5'], 1),
        'P(equity < 25%)': pctText((mc.prob_below || {})['0.25'], 1),
        [`probability of ruin (<= ${fmtMoney(mc.ruin_equity)})`]: pctText(mc.ruin_probability, 2),
      }));
    }
    g.append(mcc);

    // stress
    const sc = h('div', { class: 'card c6' }, h('h2', { text: 'stress scenarios' }));
    if (!s.ran) {
      sc.append(h('p', { class: 'msg', text: 'NOT RUN — skipped: the competitor cannot qualify regardless of the outcome' }));
    } else {
      const stt = h('table', { class: 'tbl' });
      sc.append(h('div', { class: 'tablewrap' }, stt));
      renderTable(stt, [
        { h: 'scenario', cell: (r) => r.name },
        { h: 'net', num: true, cell: (r) => num(r.net_profit) },
        { h: 'ret%', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.net_return_pct), text: pctText(r.net_return_pct) }) },
        { h: 'expR', num: true, cell: (r) => fmtNum(r.expectancy_r, 2) },
        { h: 'PF', num: true, cell: (r) => pfCell(r.profit_factor) },
        { h: 'maxDD', num: true, cell: (r) => pctText(r.max_drawdown_pct) },
        { h: 'equity', num: true, cell: (r) => fmtMoney(r.ending_equity) },
      ], s.scenarios || [], { empty: 'none', rowClass: (r) => (r.ran && r.net_profit <= 0 ? 'halted' : '') });
      sc.append(h('p', { class: 'sub', text: 'worst: ' + (s.worst_scenario || '—') + ' · survives all: ' + (s.survives ? 'yes' : 'no') }));
    }
    g.append(sc);

    // symbols
    const sym = h('div', { class: 'card c6' }, h('h2', { text: 'cross-symbol contribution' }));
    const rows = Object.entries(w.by_symbol || {}).map(([k, v]) => ({ sym: k, ...v }));
    const symt = h('table', { class: 'tbl' });
    sym.append(h('div', { class: 'tablewrap' }, symt));
    renderTable(symt, [
      { h: 'symbol', cell: (r) => r.sym },
      { h: 'trades', num: true, cell: (r) => String(r.trades) },
      { h: 'net', num: true, cell: (r) => num(r.net) },
      { h: 'total R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.r), text: fmtNum(r.r, 2) }) },
    ], rows, { empty: 'no OOS trades' });
    sym.append(h('p', { class: 'sub', text: 'profit concentration in one symbol: ' + pctText(w.symbol_concentration) }));
    g.append(sym);

    // leverage diagnostics
    const lev = h('div', { class: 'card c6' }, h('h2', { text: 'leverage actually used' }));
    lev.append(kvList({
      'configured ceiling': (b.leverage || DASH) + 'x',
      'average effective': fmtNum(m.avg_effective_leverage, 2) + 'x',
      'maximum effective': fmtNum(m.max_effective_leverage, 2) + 'x',
      'time-weighted': fmtNum(m.time_weighted_leverage, 2) + 'x',
      'margin utilisation avg': pctText(m.margin_utilization_avg, 1),
      'margin utilisation max': pctText(m.margin_utilization_max, 1),
      'time in market': pctText(m.time_in_market_pct, 1),
    }));
    lev.append(h('p', { class: 'sub', text: 'The ceiling is a limit, not a target: RiskManager sizes from risk-per-trade and stop distance, so a 20x book routinely runs far below it.' }));
    g.append(lev);
  },

  promotionCard() {
    const promo = h('div', { class: 'card' }, h('h2', { text: 'promotion to real money' }));
    promo.append(h('p', { class: 'sub', text:
      'QUALIFIED is not permission to trade. The next state is SHADOW_LIVE — forward testing on live market data with simulated execution — and only sustained forward evidence makes a bot a LIVE_CANDIDATE. Even then nothing is automatic: real capital requires the operator path on Controls (strategy selection, pre-arm checks, the GO LIVE phrase).' }));
    if (this.readOnly) {
      promo.append(h('div', { class: 'badges' }, pill('READ-ONLY INSPECTION', 'ghost',
        'operator controls are not reachable from the public page')));
    } else {
      promo.append(h('div', { class: 'btn-row' },
        h('button', { class: 'btn ghost', type: 'button', text: 'open Controls', onclick: () => Tabs.show('controls') })));
    }
    return promo;
  },

  // ---- settings / new competition -----------------------------------------------------------------
  renderSettings() {
    const g = clear($('#comp-settings'));
    if (this.readOnly) {
      g.append(h('div', { class: 'card' }, h('h2', { text: 'read-only inspection' }),
        h('p', { class: 'sub', text:
          'This page can start nothing. Creating or cancelling a competition, promoting a strategy '
          + 'and arming live trading all require the authenticated dashboard; the server rejects those '
          + 'requests here regardless of what the page shows.' })));
      return;
    }
    const d = this.data || {}, data = d.data || {}, def = d.defaults || {};
    const card = h('div', { class: 'card c6' }, h('h2', { text: 'new competition' }));
    const inp = (id, label, value, type) => h('label', { class: 'field' }, h('span', { text: label }),
      h('input', { id, type: type || 'text', value: value == null ? '' : String(value) }));
    const profiles = d.profiles || [];
    card.append(
      inp('comp-f-label', 'label', ''),
      inp('comp-f-balance', 'starting balance (USDT)', def.starting_balance ?? 20, 'number'),
      inp('comp-f-lev', 'max leverage', def.max_leverage ?? 20, 'number'),
      inp('comp-f-minlev', 'min closed trades to qualify', 100, 'number'),
      h('label', { class: 'field' }, h('span', { text: 'execution profile' }),
        h('select', { id: 'comp-f-profile' }, ...profiles.map((p) => h('option', { value: p.id, text: p.label })))),
      h('label', { class: 'field' }, h('span', { text: 'symbols' }),
        h('input', { id: 'comp-f-symbols', value: (def.symbols || []).join(', ') })),
      inp('comp-f-start', 'start (UTC)', fmtTs(data.overlap_start_ms)),
      inp('comp-f-end', 'end (UTC)', fmtTs(data.overlap_end_ms)),
      h('label', { class: 'field' }, h('span', { text: 'strategies (blank = all)' }),
        h('input', { id: 'comp-f-strats', placeholder: 'S02, S04, S24' })));
    card.append(h('div', { class: 'btn-row' },
      h('button', { class: 'btn primary', type: 'button', text: 'Run competition',
        disabled: !data.ready || (d.progress && d.progress.status === 'running'),
        onclick: () => this.runCompetition() })));
    card.append(h('div', { class: 'msg', id: 'comp-run-msg' }));
    if (!data.ready) card.append(h('p', { class: 'msg warn', text: 'No overlapping stored candle window yet — the live feed backfills candles into the database first.' }));
    g.append(card);

    const info = h('div', { class: 'card c6' }, h('h2', { text: 'venue & data' }));
    info.append(kvList({
      venue: 'Binance USD-M (simulated — the competition never sends an order)',
      timeframe: data.timeframe || '1m',
      'candles stored': Object.entries(data.symbols || {}).map(([s, v]) => s + ' ' + (v.bars || 0)).join('  ·  ') || DASH,
      'window available': data.ready ? fmtTs(data.overlap_start_ms) + ' → ' + fmtTs(data.overlap_end_ms) : 'none',
    }));
    info.append(h('p', { class: 'sub', text:
      'Seasons replay candles already stored in the database, so a run never downloads an archive at request time.' }));
    g.append(info);
  },

  // ---- specialist bot arena (discovery) ---------------------------------------------------------
  /* One strategy, one coin, one timeframe, one 20 USDT wallet per bot. Discovery can only ADVANCE a
     bot to multi-year validation; nothing here can promote, qualify or trade anything. */
  async renderArena() {
    const g = clear($('#comp-arena'));
    let a;
    try { a = await api(this.base + '/arena' + (this.arenaRun ? '?run_id=' + encodeURIComponent(this.arenaRun) : '')); }
    catch (e) { this.fail(g, 'bot arena', e); return; }
    this.arena = a;
    if (!a.ran) {
      g.append(h('div', { class: 'card' }, h('h2', { text: 'bot arena' }),
        h('p', { class: 'sub', text: 'No specialist arena run has been published yet. Runs are produced offline with scripts/run_arena.py (where the market archive lives) and shipped with the deploy.' })));
      return;
    }
    if (this.arenaBot) { await this.arenaDetail(g); return; }
    g.append(this.arenaHeader(a));
    g.append(this.arenaLeaderboard(a));
    g.append(this.arenaMatrix(a));
    g.append(this.arenaFeasibility(a));
    g.append(this.arenaNotEntered(a));
    g.append(this.arenaTimeframes(a));
    g.append(this.arenaConfig(a));
  },

  arenaHeader(a) {
    const s = a.summary || {}, run = a.run || {}, cfg = a.config || {};
    const active = s.active_bots || 0, need = s.min_active_bots || 10;
    const done = (run.status || '') === 'complete';
    const role = (cfg.dataset_role || '').toUpperCase();
    const ROLE_KIND = { TEST: 'up', DEVELOPMENT: 'warn' };
    const ROLE_HELP = { TEST: 'the ONE evaluation of frozen v2 strategies on data they were never tuned on',
      DEVELOPMENT: 'design data: used to build v2 and pick the TEST field; never reported as performance' };
    const runs = a.runs || [];
    const sel = h('select', { 'aria-label': 'arena run', onchange: (e) => { this.arenaRun = e.target.value; this.arenaBot = ''; this.renderArena(); } },
      runs.map((r) => h('option', { value: r.run_id, selected: r.run_id === run.run_id,
        text: (r.dataset_role ? r.dataset_role + ' · ' : r.params_version === 'v1' ? 'v1 · ' : '') + (r.label || r.run_id) + ' (' + r.run_id + ')' })));
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' },
      h('h2', { text: 'BOT ARENA — DISCOVERY' }),
      pill(done ? 'COMPLETED' : (run.status || '—').toUpperCase(), done ? 'up' : 'accent'),
      role ? pill(role + ' DATA', ROLE_KIND[role] || 'ghost', ROLE_HELP[role] || '') : null,
      cfg.params_version ? pill(String(cfg.params_version).toUpperCase(), 'accent') : null,
      h('span', { class: 'spacer' }),
      runs.length > 1 ? sel : null,
      h('span', { class: 'sub', text: (run.label ? run.label + ' · ' : '') + 'competition ' + (run.run_id || DASH) + ' · ' + (run.first_month || '?') + ' → ' + (run.last_month || '?') })));
    card.append(h('p', { class: 'hist-note', text: 'HISTORICAL — a completed replay; these values are fixed. Live forward results of the frozen v2 bots are in Arena → Live shadow.' }));
    if (role === 'DEVELOPMENT') card.append(h('div', { class: 'banner warn' }, h('b', { text: 'DEVELOPMENT DATA' }),
      h('span', { text: ' — these numbers designed v2 and chose the TEST field. They are not performance: judge v2 on its TEST run.' })));
    if ((cfg.only_keys || []).length) card.append(h('p', { class: 'sub', text: 'Pre-registered field: ' + cfg.only_keys.length
      + ' bots fixed before this run (docs/V2_FREEZE.md).' + (cfg.strategy_fingerprints ? ' Frozen source: ' + Object.entries(cfg.strategy_fingerprints).map(([k, v]) => k + ' ' + v).join(' · ') : '') }));
    card.append(h('div', { class: 'tiles' },
      tile('active bots', String(active), active >= need ? 'up' : 'down', active >= need ? 'field OK' : 'INSUFFICIENT_COMPETITORS'),
      tile('minimum required', String(need), '', 'not-entered bots never count'),
      tile('coins represented', String((s.coins || []).length), '', (s.coins || []).join(' ') || DASH),
      tile('timeframes', (s.timeframes || []).join(' / ') || DASH, '', 'native signal timeframes only'),
      tile('status', done ? 'completed' : (run.status || DASH), done ? 'up' : 'warn', fmtTs(run.finished_ts || run.created_ts)),
      tile('advanced', String(s.advanced || 0), s.advanced ? 'up' : '', keyList(s.advanced_keys))));
    const cd = a.cost_diagnostics || {}, cls = cd.classes || {};
    card.append(h('div', { class: 'tiles small', style: 'margin-top:8px' },
      tile('gross edge', fmtMoney(cd.total_gross, true), signClass(cd.total_gross), 'before any cost'),
      tile('costs', fmtMoney(-(cd.total_costs || 0), true), 'down'),
      tile('fee-destroyed', String(cls.FEE_DESTROYED || 0), cls.FEE_DESTROYED ? 'warn' : '', keyList((cd.fee_destroyed || []).map((r) => r.key))),
      tile('gross-negative', String(cls.GROSS_NEGATIVE || 0), cls.GROSS_NEGATIVE ? 'down' : ''),
      tile('healthy / marginal', (cls.HEALTHY || 0) + ' / ' + (cls.MARGINAL || 0), cls.HEALTHY ? 'up' : ''),
      tile('Jev-eligible controls', String((cd.jev_eligible || []).length), (cd.jev_eligible || []).length ? 'up' : 'warn', keyList(cd.jev_eligible))));
    card.append(h('div', { class: 'tiles small', style: 'margin-top:8px' },
      tile('traded', String(s.bots_that_traded ?? DASH), '', 'of ' + active),
      tile('profitable after costs', String(s.profitable_after_costs ?? DASH), s.profitable_after_costs ? 'up' : 'down'),
      tile('liquidated', String(s.liquidated ?? DASH), s.liquidated ? 'down' : ''),
      tile('trades', String(s.total_trades ?? DASH)),
      tile('gross', fmtMoney(s.total_gross_pnl, true), signClass(s.total_gross_pnl)),
      tile('fees', fmtMoney(-(s.total_fees || 0), true), 'down'),
      tile('slippage', fmtMoney(-(s.total_slippage || 0), true), 'down'),
      tile('funding', fmtMoney(s.total_funding, true), signClass(s.total_funding)),
      tile('net', fmtMoney(s.total_net_pnl, true), signClass(s.total_net_pnl))));
    const pipe = h('div', { class: 'pipeline' });
    (a.pipeline || []).forEach((step, i) => {
      if (i) pipe.append(h('span', { class: 'sub', text: '→' }));
      pipe.append(h('span', { class: 'chip ' + (step === 'DISCOVERY' ? 'accent' : step === 'ADVANCE' ? 'up' : 'ghost'), text: step }));
    });
    card.append(pipe);
    card.append(h('p', { class: 'sub', text: 'ADVANCE is not QUALIFIED: an advanced bot has only earned multi-year validation. Going live always needs a manual operator decision.' }));
    return card;
  },

  costCard(b) {
    const ce = b.cost_efficiency || {};
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'cost efficiency' }),
      costPill(ce.class), h('span', { class: 'spacer' }),
      pill(b.jev_eligible ? 'JEV-ELIGIBLE' : 'NOT JEV-ELIGIBLE', b.jev_eligible ? 'up' : 'ghost', (b.jev_eligibility_reasons || []).join('; '))));
    card.append(h('div', { class: 'tiles small' },
      tile('gross edge', fmtMoney(ce.gross_edge, true), signClass(ce.gross_edge), 'decision prices'),
      tile('costs', fmtMoney(-(ce.total_costs || 0), true), 'down', 'fees + slippage + funding'),
      tile('cost / edge', isNum(ce.cost_to_edge) ? ce.cost_to_edge.toFixed(2) + 'x' : '∞', isNum(ce.cost_to_edge) && ce.cost_to_edge < 0.5 ? 'up' : 'down'),
      tile('gross / trade', fmtNum(ce.avg_gross_edge_bps, 1) + ' bps', signClass(ce.avg_gross_edge_bps)),
      tile('cost / trade', fmtNum(ce.avg_round_trip_cost_bps, 1) + ' bps', 'down'),
      tile('fees / gross wins', pctText(ce.fees_to_gross_winning)),
      tile('break-even win', pctText(ce.break_even_win_rate), '', 'actual ' + pctText(ce.win_rate)),
      tile('trades / day', fmtNum(ce.trades_per_day, 2), '', 'hold ' + fmtNum(ce.avg_holding_minutes, 0) + ' min')));
    return card;
  },

  arenaRows(a) {
    const f = this.arenaFilter;
    return (a.bots || []).filter((b) => (!f.coin || b.coin === f.coin) && (!f.tf || b.timeframe === f.tf)
      && (!f.strategy || b.strategy_id === f.strategy) && (!f.state || b.state === f.state));
  },

  arenaLeaderboard(a) {
    const card = h('div', { class: 'card' });
    const f = this.arenaFilter;
    const sel = (label, key, values) => {
      const s = h('select', { 'aria-label': label, onchange: (e) => { f[key] = e.target.value; this.renderArena(); } },
        h('option', { value: '', text: 'all ' + label }), values.map((v) => h('option', { value: v, text: v })));
      s.value = f[key] || '';
      return field(label, s);
    };
    const fl = a.filters || {};
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'arena leaderboard' }),
      h('span', { class: 'sub', text: 'ADVANCE first, then net PnL after fees, slippage and funding; bots that never traded last' })));
    card.append(h('div', { class: 'arena-filters' },
      sel('coin', 'coin', fl.coins || []), sel('timeframe', 'tf', fl.timeframes || []),
      sel('strategy', 'strategy', fl.strategies || []), sel('status', 'state', fl.states || [])));
    const t = h('table', { class: 'tbl lb' });
    card.append(h('div', { class: 'tablewrap' }, t));
    const m = (b) => b.metrics || {};
    renderTable(t, [
      { h: '#', cls: 'rank', cell: (b) => String(b.rank || DASH) },
      { h: 'bot', cell: (b) => h('span', { class: 'sname', text: b.key }) },
      { h: 'strategy', cell: (b) => b.strategy_id + ' ' + (b.name || '') },
      { h: 'coin', cell: (b) => b.coin },
      { h: 'tf', cell: (b) => b.timeframe },
      { h: 'max lev', num: true, cell: (b) => (b.max_leverage || DASH) + 'x' },
      { h: 'avg eff lev', num: true, cell: (b) => fmtNum(m(b).avg_effective_leverage, 2) + 'x' },
      { h: 'pos lev', num: true, cell: (b) => fmtNum(b.avg_position_leverage, 1) + 'x' },
      { h: 'trades', num: true, cell: (b) => String(m(b).trades ?? DASH) },
      { h: 'gross', num: true, cell: (b) => num(m(b).gross_pnl) },
      { h: 'fees', num: true, cell: (b) => num(-(m(b).fees_paid || 0)) },
      { h: 'slip', num: true, cell: (b) => num(-(m(b).slippage_cost || 0)) },
      { h: 'fund', num: true, cell: (b) => num(m(b).funding_paid) },
      { h: 'net', num: true, cell: (b) => num(m(b).net_profit) },
      { h: 'return', num: true, cell: (b) => h('span', { class: 'num ' + signClass(m(b).net_return_pct), text: pctText(m(b).net_return_pct) }) },
      { h: 'exp R', num: true, cell: (b) => fmtNum(m(b).expectancy_r, 2) },
      { h: 'PF', num: true, cell: (b) => pfCell(m(b).profit_factor) },
      { h: 'max DD', num: true, cell: (b) => pctText(m(b).max_drawdown_pct) },
      { h: 'liq', num: true, cell: (b) => String(m(b).liquidation_count ?? DASH) },
      { h: 'states', cell: (b) => Object.entries(b.entry_states || {}).map(([k, v]) => k[0] + v).join(' ') || DASH },
      { h: 'cost', cell: (b) => costPill((b.cost_efficiency || {}).class) },
      { h: 'result', cell: (b) => stateBadge(b.state) },
    ], this.arenaRows(a), { empty: 'no bot matches these filters', onRow: (b) => this.openArenaBot(b.key) });
    return card;
  },

  arenaMatrix(a) {
    const mx = a.matrix || {};
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'strategy × coin' }),
      h('span', { class: 'sub', text: 'return · PF · expectancy · max DD · trades · result. An empty cell was not selected — it says nothing about that pair.' })));
    const t = h('table', { class: 'tbl mx' });
    const syms = mx.symbols || [];
    t.append(h('thead', null, h('tr', null, h('th', { text: 'strategy' }),
      syms.map((s) => h('th', { text: s.replace(/USDT$/, '') })))));
    const body = h('tbody');
    (mx.strategies || []).forEach((sid) => {
      const tr = h('tr', null, h('td', null, h('b', { text: sid + ' ' }), h('span', { class: 'sub', text: (mx.names || {})[sid] || '' })));
      syms.forEach((sym) => {
        const cell = (((mx.cells || {})[sid]) || {})[sym] || [];
        if (!cell.length) { tr.append(h('td', { class: 'muted', text: '·' })); return; }
        tr.append(h('td', null, cell.map((c) => h('div', {
          class: 'mx-cell click ' + (STATE_KIND[c.state] || (c.state === 'ADVANCE' ? 'up' : 'ghost')),
          title: c.key, onclick: () => this.openArenaBot(c.key) },
          h('span', { class: 'num ' + signClass(c.net_return_pct), text: pctText(c.net_return_pct) }),
          h('span', { text: ' PF ' + pfCell(c.profit_factor) + ' · ' + fmtNum(c.expectancy_r, 2) + 'R' }),
          h('span', { class: 'sub', text: ' DD ' + pctText(c.max_drawdown_pct) + ' · n=' + (c.trades ?? DASH) + ' · ' + c.timeframe }),
          h('div', null, stateBadge(c.state))))));
      });
      body.append(tr);
    });
    t.append(body);
    card.append(h('div', { class: 'tablewrap' }, t));
    return card;
  },

  arenaFeasibility(a) {
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'coin feasibility' }),
      h('span', { class: 'sub', text: 'Binance checks MIN_NOTIONAL on the submitted order and exempts reduce-only exits; the old 2 × minNotional rule is gone' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'symbol', cell: (r) => r.symbol },
      { h: 'minNotional', num: true, cell: (r) => fmtNum(r.min_notional, 0) },
      { h: 'minQty', num: true, cell: (r) => String(r.min_qty ?? DASH) },
      { h: 'ref price', num: true, cell: (r) => fmtNum(r.reference_price, 4) },
      { h: 'exchange min', num: true, cell: (r) => fmtNum(r.exchange_min, 2) },
      { h: 'PaperLab min', num: true, cell: (r) => fmtNum(r.floor, 2) },
      { h: 'fee-gate max', num: true, cell: (r) => fmtNum(r.ceiling, 2) },
      { h: 'stop band', cell: (r) => (isNum(r.min_stop_pct) && isNum(r.max_stop_pct)
        ? (r.max_stop_pct < r.min_stop_pct ? 'empty' : pctText(r.min_stop_pct, 2) + ' – ' + pctText(r.max_stop_pct, 2)) : DASH) },
      { h: 'risk needed', num: true, cell: (r) => pctText(r.min_risk_pct_needed, 2) },
      { h: 'tradeable', cell: (r) => pill(r.tradeable ? 'YES' : 'NO', r.tradeable ? 'up' : 'down') },
      { h: 'reason', cell: (r) => r.reason || DASH },
    ], a.symbols || [], { empty: 'no symbols checked' });
    return card;
  },

  arenaNotEntered(a) {
    const card = h('div', { class: 'card' });
    const ne = a.not_entered || [];
    const total = ne.reduce((n, g) => n + (g.count || 0), 0);
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'not entered (' + total + ' candidates)' }),
      h('span', { class: 'sub', text: (a.eligible_not_selected || []).length + ' more passed preflight but were not selected (field cap; strategy × coin rotation, results never consulted)' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'bots', num: true, cell: (g) => String(g.count) },
      { h: 'reason', cell: (g) => g.reason },
      { h: 'which', cell: (g) => h('details', null, h('summary', { text: 'show' }), h('div', { class: 'sub wrapall', text: (g.keys || []).join(', ') })) },
    ], ne, { empty: 'every candidate passed preflight' });
    return card;
  },

  arenaTimeframes(a) {
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'strategy timeframes' }),
      h('span', { class: 'sub', text: 'v1 strategies trade their native timeframe only; a retimed strategy would be a new version (v2)' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'strategy', cell: (r) => r.strategy_id + ' ' + (r.name || '') },
      { h: 'native', cell: (r) => r.native_timeframe || 'unknown' },
      { h: 'arena-supported', cell: (r) => (r.supported_timeframes || []).join(', ') || pill('NONE', 'ghost') },
      { h: 'context (read, never traded)', cell: (r) => (r.context_timeframes || []).join(', ') || DASH },
    ], a.strategies || []);
    return card;
  },

  arenaConfig(a) {
    const c = a.config || {}, r = c.risk_profile || {}, fees = c.fees || {};
    const card = h('div', { class: 'card' }, h('h2', { text: 'exact configuration' }));
    card.append(kvList({
      'arena version': c.arena_version, months: (c.months || []).join(', '),
      'starting balance (USDT, isolated)': c.starting_balance, profile: c.profile,
      'risk ordinary / strong / exceptional': pctText(r.ordinary_risk_pct, 2) + ' / ' + pctText(r.strong_risk_pct, 2) + ' / ' + pctText(r.exceptional_risk_pct, 2),
      'hard max risk per trade': pctText(r.max_risk_pct, 2),
      'state multipliers': 'ATTACK ' + r.attack_multiplier + 'x · NORMAL 1x · DEFENSIVE ' + r.defensive_multiplier + 'x · HALTED 0',
      'ATTACK requires': '>= ' + r.attack_min_trades + ' closed trades, expectancy >= ' + r.attack_min_expectancy_r + 'R, DD <= ' + pctText(r.attack_max_drawdown) + ', graded signal (none in v1)',
      'leverage': 'ceiling ' + c.leverage_ceiling + 'x, per-position ' + c.leverage_policy,
      'fees charged': 'maker ' + pctText(fees.maker_rate, 3) + ' / taker ' + pctText(fees.taker_rate, 3) + ' (' + c.fee_source + ')',
      'fee gate': 'round trip <= ' + pctText(c.max_fee_share_of_r, 0) + ' of risk',
      'min-notional safety multiplier': c.min_notional_safety_multiplier,
      'gates': 'trades ' + JSON.stringify(c.min_trades || {}) + ' · net > ' + c.min_net_profit + ' · expR > ' + c.min_expectancy_r + ' · PF >= ' + c.min_profit_factor + ' · DD <= ' + pctText(c.max_drawdown_pct, 0) + ' · liq <= ' + c.max_liquidations,
      'field': 'min ' + c.min_active_bots + ' active, cap ' + c.max_bots, seed: c.seed,
    }));
    return card;
  },

  openArenaBot(key) {
    this.arenaBot = key;
    if (this.readOnly && window.history && history.pushState) history.pushState(null, '', '/public/competition/arena/bot/' + encodeURIComponent(key));
    this.renderArena();
  },

  closeArenaBot() {
    this.arenaBot = '';
    if (this.readOnly && window.history && history.pushState) history.pushState(null, '', '/public/competition/arena');
    this.renderArena();
  },

  async arenaDetail(g) {
    let d;
    try { d = await api(this.base + '/arena/bots/' + encodeURIComponent(this.arenaBot) + (this.arenaRun ? '?run_id=' + encodeURIComponent(this.arenaRun) : '')); }
    catch (e) { this.fail(g, 'arena bot ' + this.arenaBot, e); return; }
    const b = d.bot || {}, m = b.metrics || {};
    const head = h('div', { class: 'card' });
    head.append(h('div', { class: 'card-head' },
      h('button', { class: 'btn ghost', type: 'button', text: '← arena', onclick: () => this.closeArenaBot() }),
      h('h2', { text: b.key }), stateBadge(b.state), h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: 'rank ' + (b.rank || DASH) + ' · run ' + (b.run_id || DASH) })));
    head.append(h('div', { class: 'tiles small' },
      tile('strategy', b.strategy_id, '', b.name || ''), tile('coin', b.symbol || DASH), tile('timeframe', b.timeframe || DASH),
      tile('leverage ceiling', (b.max_leverage || DASH) + 'x', '', 'pos avg ' + fmtNum(b.avg_position_leverage, 1) + 'x · max ' + (b.max_position_leverage ?? DASH) + 'x'),
      tile('profile', b.profile || DASH, '', 'params ' + (b.params_version || DASH)),
      tile('version', (b.version || '').split(':').pop() || DASH)));
    head.append(h('div', { class: 'tiles small' },
      tile('gross', fmtMoney(m.gross_pnl, true), signClass(m.gross_pnl)),
      tile('fees', fmtMoney(-(m.fees_paid || 0), true), 'down'),
      tile('slippage', fmtMoney(-(m.slippage_cost || 0), true), 'down'),
      tile('funding', fmtMoney(m.funding_paid, true), signClass(m.funding_paid)),
      tile('net', fmtMoney(m.net_profit, true), signClass(m.net_profit), pctText(m.net_return_pct)),
      tile('trades', String(m.trades ?? DASH), '', (b.signals ?? DASH) + ' signals'),
      tile('expectancy', fmtNum(m.expectancy_r, 3) + 'R', signClass(m.expectancy_r)),
      tile('profit factor', pfCell(m.profit_factor)),
      tile('max DD', pctText(m.max_drawdown_pct), 'down'),
      tile('liquidations', String(m.liquidation_count ?? DASH), m.liquidation_count ? 'down' : '')));
    g.append(head);
    const gates = h('div', { class: 'card' }, h('h2', { text: 'advancement gates' }));
    const box = h('div', { class: 'gates' });
    (b.gates || []).forEach((x) => box.append(h('div', { class: 'gate' }, pill(x.ok ? 'PASS' : 'FAIL', x.ok ? 'up' : 'down'),
      h('span', { class: 'gate-name', text: x.name.replace(/_/g, ' ') }),
      h('span', { class: 'sub', text: fmtNum(x.actual, 4) + ' ' + x.comparison + ' ' + fmtNum(x.threshold, 4) }))));
    gates.append(box, h('ul', null, (b.reasons || []).map((r) => h('li', { class: 'sub', text: r }))));
    g.append(gates);
    g.append(this.costCard(b));
    const beh = h('div', { class: 'card' }, h('h2', { text: 'behaviour' }));
    beh.append(kvList({
      'entry states': Object.entries(b.entry_states || {}).map(([k, v]) => k + ' ' + v).join(', ') || DASH,
      'signal grades': Object.entries(b.entry_quality || {}).map(([k, v]) => k + ' ' + v).join(', ') || DASH,
      'avg / max risk per trade': pctText(b.avg_risk_pct, 2) + ' / ' + pctText(b.max_risk_pct, 2),
      'avg effective leverage': fmtNum(m.avg_effective_leverage, 2) + 'x',
      'win rate': pctText(m.win_rate), 'partially filled orders': b.partial_fills,
      'halted by the 25% floor': !!b.halted, 'fees charged from': b.fee_source,
    }));
    const rj = h('table', { class: 'tbl' });
    renderTable(rj, [{ h: 'refused signals', cell: (r) => r[0] }, { h: 'count', num: true, cell: (r) => String(r[1]) }],
      Object.entries(b.rejects || {}).sort((x, y) => y[1] - x[1]), { empty: 'none' });
    beh.append(h('div', { class: 'tablewrap' }, rj));
    g.append(beh);
    const eq = b.equity || [];
    if (eq.length > 1) {
      const vals = eq.map((p) => p[1]); const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
      const pts = eq.map((p, i) => (i / (eq.length - 1) * 600).toFixed(1) + ',' + (110 - (p[1] - lo) / span * 100).toFixed(1)).join(' ');
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 600 120'); svg.setAttribute('class', 'arena-eq'); svg.setAttribute('preserveAspectRatio', 'none');
      const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      pl.setAttribute('points', pts); pl.setAttribute('fill', 'none'); pl.setAttribute('stroke-width', '1.5');
      pl.setAttribute('stroke', vals[vals.length - 1] >= vals[0] ? 'var(--up)' : 'var(--down)');
      svg.append(pl);
      g.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'equity' }),
        h('span', { class: 'sub', text: fmtMoney(vals[0]) + ' → ' + fmtMoney(vals[vals.length - 1]) + ' USDT (low ' + fmtMoney(lo) + ')' })), svg));
    }
    const tl = h('table', { class: 'tbl' });
    renderTable(tl, [
      { h: 'entry', cell: (t) => fmtTs(t.entry_ts) }, { h: 'exit', cell: (t) => fmtTs(t.exit_ts) },
      { h: 'side', cell: (t) => pill(t.side, t.side) }, { h: 'qty', num: true, cell: (t) => fmtQty(t.qty) },
      { h: 'entry px', num: true, cell: (t) => fmtPrice(t.entry) }, { h: 'exit px', num: true, cell: (t) => fmtPrice(t.exit) },
      { h: 'pnl', num: true, cell: (t) => num(t.pnl) }, { h: 'fees', num: true, cell: (t) => fmtMoney(t.fees) },
      { h: 'net', num: true, cell: (t) => num(t.net) }, { h: 'R', num: true, cell: (t) => fmtNum(t.r, 2) },
      { h: 'exit', cell: (t) => t.exit_kind },
    ], (b.trades || []).slice().reverse(), { empty: 'no closed trades' });
    g.append(h('div', { class: 'card' }, h('h2', { text: 'trade ledger (' + (b.trades || []).length + ', simulated)' }), h('div', { class: 'tablewrap' }, tl)));
  },

  // ---- CONTROL vs +JEV ------------------------------------------------------------------------------
  /* Same bot twice: once as it is, once with Jev deciding TAKE / REDUCE / SKIP on its signals. The
     number that matters is the JEV EDGE DELTA (+JEV net minus CONTROL net), not whether +JEV made money. */
  async renderJev() {
    const g = clear($('#comp-jev'));
    let j;
    try { j = await api(this.base + '/jev'); }
    catch (e) { this.fail(g, 'Jev experiment', e); return; }
    this.jevData = j;
    g.append(this.jevLiveStrip());
    g.append(this.jevHealthCard(j.health));
    this.loadJevLive();
    if (!j.ran) {
      g.append(h('div', { class: 'card' }, h('h2', { text: 'CONTROL vs +JEV' }),
        h('p', { class: 'sub', text: 'No paired experiment has completed yet. Experiments run on the server, where the OpenRouter key lives: scripts/run_jev_experiment.py.' })));
      return;
    }
    if (this.jevPair) { await this.jevPairView(g); return; }
    g.append(this.jevHeadline(j));
    g.append(this.jevPairsTable(j));
    g.append(this.calibrationCard((j.summary || {}).calibration || [], 'calibration (all +JEV bots)'));
  },

  /* ---- live strip: the running Jev V1 twins, pushed ------------------------------------------
     The experiment on this page is a COMPLETED replay (its numbers never change). What is live is
     Jev V1 deciding on the live shadow's candidates; this strip shows that, and updates on every
     pushed decision and health beat without a reload. */
  jevLive: null,
  jevLiveStrip() {
    const el = h('div', { class: 'card live-strip', id: 'jev-live-strip' });
    this.renderJevLive(el);
    return el;
  },
  async loadJevLive() {
    try {
      const d = await api(this.base + '/shadow');
      this.jevLive = { pairs: (d.status || {}).pairs, eligible: (d.eligible_controls || []).length,
        verdict: (d.jev_live || {}).verdict || {}, stats: d.jev_live || {}, last: (d.decisions_recent || [])[0] || null };
      this.renderJevLive();
    } catch (e) { /* the strip says "no live data" */ }
  },
  onJevEvent(dec) {
    if (!this.jevLive) return;
    const st = this.jevLive.stats;
    st.decisions = (st.decisions || 0) + 1;
    st.actions = st.actions || {};
    st.actions[dec.final_action] = (st.actions[dec.final_action] || 0) + 1;
    if (dec.error_code) st.errors = (st.errors || 0) + 1;
    st.failure_rate = st.decisions ? (st.errors || 0) / st.decisions : null;
    this.jevLive.last = dec;
    this.renderJevLive(null, true);
  },
  onHealth(hh) {
    if (this.ctab !== 'jev' || !this.jevData || !hh) return;
    if (hh.jev) {
      this.jevData.health = Object.assign({}, this.jevData.health || {}, hh.jev);
      const old = $('#jev-health-card');
      if (old) old.replaceWith(this.jevHealthCard(this.jevData.health));
    }
    if (hh.shadow && this.jevLive) {
      this.jevLive.pairs = hh.shadow.pairs;
      if (isNum(hh.shadow.eligible_controls)) this.jevLive.eligible = hh.shadow.eligible_controls;
      this.renderJevLive();
    }
  },
  renderJevLive(el, changed) {
    el = el || $('#jev-live-strip');
    if (!el) return;
    clear(el);
    const L = this.jevLive;
    el.append(h('i', { class: 'live-dot' }), h('b', { text: 'LIVE NOW' }),
      h('span', { class: 'sub', text: 'Jev V1 (frozen) on the live shadow, pushed as it decides' }));
    if (!L) { el.append(h('span', { class: 'sub', text: '· no live shadow data on this server' })); return; }
    const st = L.stats || {}, acts = st.actions || {}, v = L.verdict || {};
    const add = (label, value, key) => el.append(h('span', null, h('span', { class: 'sub', text: label + ' ' }),
      h('b', { class: changed && key === 'decisions' ? 'flash' : '', text: value })));
    add('pairs running', String(L.pairs ?? 0));
    add('eligible controls', String(L.eligible ?? 0) + ' of ' + (v.min_pairs || 10) + ' needed');
    add('decisions', String(st.decisions || 0), 'decisions');
    add('skip / reduce / take', (acts.SKIP || 0) + ' / ' + (acts.REDUCE || 0) + ' / ' + (acts.TAKE || 0));
    add('failures', isNum(st.failure_rate) ? (st.failure_rate * 100).toFixed(1) + '%' : DASH);
    add('latency p50', isNum(st.latency_p50_ms) ? Math.round(st.latency_p50_ms) + ' ms' : DASH);
    add('last', L.last ? (L.last.final_action || '?') + ' ' + (isNum(L.last.take_probability) ? Math.round(L.last.take_probability * 100) + '%' : '')
      + ' · ' + String(L.last.bot_key || '').split('@')[0] + ' · ' + ago(L.last.candidate_wall_ms || L.last.ts) : 'none yet');
    add('verdict', v.verdict ? v.verdict + (v.status ? ' — ' + v.status : '') : 'NO VERDICT');
  },

  jevHealthCard(hh) {
    const s = (hh && hh.status) || 'NOT_STARTED';
    const kind = s === 'OK' ? 'up' : s === 'DEGRADED' || s === 'UNTESTED' ? 'warn' : s === 'DISABLED' ? 'ghost' : 'down';
    const c = (hh && hh.last_check) || {};
    return h('div', { class: 'card', id: 'jev-health-card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'JEV API HEALTH' }), pill(s, kind), pill('LIVE', 'up', 'updated by push every 10 s'), h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: hh ? ('model ' + (hh.model_requested || DASH) + (hh.model_resolved ? ' → ' + hh.model_resolved : '')
        + ' · ' + (hh.prompt_version || DASH) + ' / ' + (hh.policy_version || DASH)) : DASH })),
      h('div', { class: 'tiles small' },
        tile('configured', hh && hh.configured ? 'yes' : 'no', hh && hh.configured ? 'up' : 'down'),
        tile('enabled', hh && hh.enabled ? 'yes' : 'no', hh && hh.enabled ? 'up' : 'warn', 'JEV_ENABLED kill switch'),
        tile('last check', c.status || 'never', c.ok ? 'up' : 'warn', c.checked_at ? fmtTs(c.checked_at) : ''),
        tile('check latency', isNum(c.latency_ms) ? c.latency_ms + ' ms' : DASH),
        tile('calls since boot', String(hh ? hh.calls_since_boot : DASH), '', 'errors ' + (hh ? hh.errors_since_boot : DASH)),
        tile('p95 latency', isNum(hh && hh.latency_p95_ms) ? hh.latency_p95_ms + ' ms' : DASH)));
  },

  jevAnswer(s) {
    if (s.verdict) return [s.verdict + ': ' + (s.verdict_detail || ''), s.verdict === 'YES' ? 'up' : s.verdict === 'NO' ? 'down' : 'warn'];
    const imp = s.improved || 0, wor = s.worsened || 0, p = s.sign_test_p;
    if (!(imp + wor)) return ['NO ANSWER: no pair differed', 'ghost'];
    if (isNum(p) && p < 0.05) {
      if (imp > wor && (s.mean_edge_delta_usdt || 0) > 0) return ['YES: Jev improved significantly more pairs than it worsened', 'up'];
      if (wor > imp && (s.mean_edge_delta_usdt || 0) < 0) return ['NO: Jev made significantly more pairs worse', 'down'];
      return ['MIXED: the pair count and the average disagree', 'warn'];
    }
    return ['NOT DEMONSTRATED: ' + imp + ' improved vs ' + wor + ' worsened (sign test p=' + (isNum(p) ? p.toFixed(2) : DASH) + ')', 'warn'];
  },

  jevHeadline(j) {
    const s = j.summary || {}, run = j.run || {};
    const [answer, kind] = this.jevAnswer(s);
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'CONTROL vs +JEV' }),
      pill((run.status || '').toUpperCase() || DASH, run.status === 'complete' ? 'up' : 'accent'),
      h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: 'experiment ' + (run.run_id || DASH) + ' · arena ' + (run.arena_run_id || DASH) + ' · ' + (run.first_month || '?') + ' → ' + (run.last_month || '?') })));
    card.append(h('p', { class: 'hist-note', text: 'HISTORICAL — a completed replay experiment on July–August 2026 data (contaminated window). '
      + 'Its numbers are fixed and will not move; Jev V1 is judged again only on live forward data (strip above, and Arena → Live shadow).' }));
    card.append(h('p', { class: 'jev-answer ' + kind, text: 'DOES JEV ADD A MEASURABLE AFTER-COST EDGE?  ' + answer }));
    card.append(h('div', { class: 'tiles' },
      tile('JEV EDGE DELTA (mean)', fmtMoney(s.mean_edge_delta_usdt, true) + ' USDT', signClass(s.mean_edge_delta_usdt), fmtNum(s.mean_edge_delta_return_pp, 2) + ' pp'),
      tile('JEV EDGE DELTA (median)', fmtMoney(s.median_edge_delta_usdt, true) + ' USDT', signClass(s.median_edge_delta_usdt), fmtNum(s.median_edge_delta_return_pp, 2) + ' pp'),
      tile('improved', String(s.improved ?? DASH), 'up', 'of ' + (s.pairs ?? DASH) + ' pairs'),
      tile('worsened', String(s.worsened ?? DASH), 'down', 'unchanged ' + (s.unchanged ?? DASH)),
      tile('sign test p', fmtNum(s.sign_test_p, 3), isNum(s.sign_test_p) && s.sign_test_p < 0.05 ? 'up' : 'warn', 'two-sided'),
      tile('CONTROL net / +JEV net', fmtMoney(s.total_control_net, true) + ' / ' + fmtMoney(s.total_jev_net, true))));
    const auc = s.auc || {}, ao = s.allowed_outcomes || {}, so = s.skipped_outcomes || {};
    card.append(h('div', { class: 'tiles small', style: 'margin-top:8px' },
      tile('never-trade null', fmtMoney(s.null_mean_edge_delta_usdt, true) + ' USDT', signClass(s.null_mean_edge_delta_usdt), 'mean delta vs control'),
      tile('+JEV bots net + / −', (s.pairs_jev_profitable ?? DASH) + ' / ' + (s.pairs_jev_losing ?? DASH)),
      tile('allowed trades', String(ao.n ?? DASH), signClass(ao.net), 'net ' + fmtMoney(ao.net, true) + ' · ' + fmtNum(ao.mean_r, 2) + 'R'),
      tile('skipped (shadow)', String(so.n ?? DASH), '', 'mean ' + fmtNum(so.mean_r, 2) + 'R · win ' + pctText(so.win_rate)),
      tile('AUC take p → win', fmtNum(auc.auc, 3), isNum(auc.low) && auc.low > 0.5 ? 'up' : 'warn', '95% CI ' + fmtNum(auc.low, 3) + '–' + fmtNum(auc.high, 3)),
      tile('Spearman p vs R', fmtNum(s.spearman_take_p_vs_r, 3))));
    card.append(h('div', { class: 'tiles small', style: 'margin-top:8px' },
      tile('Jev bots / controls', (s.jev_bots ?? DASH) + ' / ' + (s.control_bots ?? DASH)),
      tile('signals reviewed', String(s.candidate_signals ?? DASH)),
      tile('API calls / cache hits', (s.jev_calls ?? DASH) + ' / ' + (s.cache_hits ?? DASH)),
      tile('API errors', String(s.api_errors ?? DASH), s.api_errors ? 'down' : '', pctText(s.error_rate, 2)),
      tile('accept / reduce / skip', (s.accepted ?? DASH) + ' / ' + (s.reduced ?? DASH) + ' / ' + (s.skipped ?? DASH), '', 'acceptance ' + pctText(s.acceptance_rate)),
      tile('skips that would lose / win', (s.skipped_losing ?? DASH) + ' / ' + (s.skipped_winning ?? DASH)),
      tile('latency avg / p95', fmtNum(s.latency_avg_ms, 0) + ' / ' + fmtNum(s.latency_p95_ms, 0) + ' ms'),
      tile('input tokens', String(s.total_input_tokens ?? DASH)),
      tile('AI cost', isNum(s.total_cost_usd) ? '$' + s.total_cost_usd.toFixed(5) : DASH, '', 'per call $' + (isNum(s.cost_per_decision_usd) ? s.cost_per_decision_usd.toFixed(7) : DASH))));
    return card;
  },

  jevPairsTable(j) {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'pairs' }),
      h('span', { class: 'sub', text: 'sorted by JEV EDGE DELTA; a +JEV bot that made money but less than its control is WORSENED' })));
    const t = h('table', { class: 'tbl lb' });
    card.append(h('div', { class: 'tablewrap' }, t));
    const rows = (j.pairs || []).slice().sort((a, b) => (b.edge_delta_usdt || 0) - (a.edge_delta_usdt || 0));
    renderTable(t, [
      { h: 'pair', cell: (r) => h('span', null, h('span', { class: 'sname', text: r.control_key + ' ' }), pill('JEV', 'accent')) },
      { h: 'coin', cell: (r) => r.coin },
      { h: 'tf', cell: (r) => r.timeframe },
      { h: 'CONTROL net', num: true, cell: (r) => num((r.control || {}).net_profit) },
      { h: '+JEV net', num: true, cell: (r) => num((r.jev || {}).net_profit) },
      { h: 'EDGE DELTA', num: true, cell: (r) => num(r.edge_delta_usdt) },
      { h: 'Δ return', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.edge_delta_return_pp), text: fmtNum(r.edge_delta_return_pp, 2) + ' pp' }) },
      { h: 'trades C/J', num: true, cell: (r) => ((r.control || {}).trades ?? DASH) + ' / ' + ((r.jev || {}).trades ?? DASH) },
      { h: 'PF C/J', num: true, cell: (r) => pfCell((r.control || {}).profit_factor) + ' / ' + pfCell((r.jev || {}).profit_factor) },
      { h: 'DD C/J', num: true, cell: (r) => pctText((r.control || {}).max_drawdown_pct) + ' / ' + pctText((r.jev || {}).max_drawdown_pct) },
      { h: 'reviewed', num: true, cell: (r) => String((r.jev_stats || {}).reviewed ?? DASH) },
      { h: 'accept', num: true, cell: (r) => pctText((r.jev_stats || {}).acceptance_rate) },
      { h: 'AI $', num: true, cell: (r) => isNum((r.jev_stats || {}).cost_usd) ? r.jev_stats.cost_usd.toFixed(5) : DASH },
      { h: 'verdict', cell: (r) => pill(r.verdict, r.verdict === 'IMPROVED' ? 'up' : r.verdict === 'WORSENED' ? 'down' : 'ghost') },
    ], rows, { empty: 'no pairs', onRow: (r) => this.openJevPair(r.pair_id) });
    return card;
  },

  calibrationCard(rows, title) {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: title }),
      h('span', { class: 'sub', text: 'take probability → how the candidate really did (its trade, or its shadow trade when skipped)' })));
    const t = h('table', { class: 'tbl' });
    card.append(h('div', { class: 'tablewrap' }, t));
    renderTable(t, [
      { h: 'take p', cell: (r) => r.bucket },
      { h: 'candidates', num: true, cell: (r) => String(r.candidates) },
      { h: 'taken', num: true, cell: (r) => String(r.taken) },
      { h: 'shadow', num: true, cell: (r) => String(r.shadow) },
      { h: 'mean R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.mean_r), text: fmtNum(r.mean_r, 3) }) },
      { h: 'win rate', num: true, cell: (r) => pctText(r.win_rate) },
      { h: 'sum R', num: true, cell: (r) => fmtNum(r.sum_r, 2) },
    ], rows, { empty: 'no decisions with outcomes' });
    return card;
  },

  openJevPair(pid) {
    this.jevPair = pid;
    if (this.readOnly && window.history && history.pushState) history.pushState(null, '', '/public/competition/jev/pair/' + encodeURIComponent(pid));
    this.renderJev();
  },

  closeJevPair() {
    this.jevPair = '';
    if (this.readOnly && window.history && history.pushState) history.pushState(null, '', '/public/competition/jev');
    this.renderJev();
  },

  async jevPairView(g) {
    let d;
    try { d = await api(this.base + '/jev/pairs/' + encodeURIComponent(this.jevPair)); }
    catch (e) { this.fail(g, 'pair ' + this.jevPair, e); return; }
    const pr = d.pair || {}, c = pr.control || {}, x = pr.jev || {}, st = pr.jev_stats || {};
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' },
      h('button', { class: 'btn ghost', type: 'button', text: '← pairs', onclick: () => this.closeJevPair() }),
      h('h2', { text: 'CONTROL vs +JEV' }), pill(pr.verdict || DASH, pr.verdict === 'IMPROVED' ? 'up' : pr.verdict === 'WORSENED' ? 'down' : 'ghost')));
    card.append(h('p', { class: 'sub', text: pr.control_key + '   vs   ' + pr.jev_key }));
    const rows = [
      ['Return', pctText(c.net_return_pct), pctText(x.net_return_pct)],
      ['Net PnL', fmtMoney(c.net_profit, true), fmtMoney(x.net_profit, true)],
      ['Trades', String(c.trades ?? DASH), String(x.trades ?? DASH)],
      ['Expectancy', fmtNum(c.expectancy_r, 3) + 'R', fmtNum(x.expectancy_r, 3) + 'R'],
      ['PF', pfCell(c.profit_factor), pfCell(x.profit_factor)],
      ['Max DD', pctText(c.max_drawdown_pct), pctText(x.max_drawdown_pct)],
      ['Fees', fmtMoney(c.fees_paid), fmtMoney(x.fees_paid)],
      ['Slippage', fmtMoney(c.slippage_cost), fmtMoney(x.slippage_cost)],
      ['Funding', fmtMoney(c.funding_paid, true), fmtMoney(x.funding_paid, true)],
      ['Liquidations', String(c.liquidation_count ?? DASH), String(x.liquidation_count ?? DASH)],
    ];
    const t = h('table', { class: 'tbl cmp' });
    renderTable(t, [{ h: '', cell: (r) => r[0] }, { h: 'CONTROL', num: true, cell: (r) => r[1] }, { h: '+JEV', num: true, cell: (r) => r[2] }], rows);
    card.append(h('div', { class: 'tablewrap' }, t));
    card.append(h('p', { class: 'jev-delta ' + signClass(pr.edge_delta_usdt), text: 'JEV EDGE DELTA: ' + fmtMoney(pr.edge_delta_usdt, true) + ' USDT  (' + fmtNum(pr.edge_delta_return_pp, 2) + ' percentage points)' }));
    card.append(kvList({
      'decisions': st.reviewed, 'accepted / reduced / skipped': (st.accepted ?? DASH) + ' / ' + (st.reduced ?? DASH) + ' / ' + (st.skipped ?? DASH),
      'acceptance rate': pctText(st.acceptance_rate), 'skipped that would have lost / won': (st.skipped_losing ?? DASH) + ' / ' + (st.skipped_winning ?? DASH),
      'shadow net of skipped candidates': fmtMoney(st.skipped_shadow_net, true), 'API errors': st.errors,
      'latency avg / p95 (ms)': fmtNum(st.latency_avg_ms, 0) + ' / ' + fmtNum(st.latency_p95_ms, 0),
      'cache hit rate': pctText(st.cache_hit_rate), 'input tokens': st.input_tokens,
      'AI cost (USD)': isNum(st.cost_usd) ? st.cost_usd.toFixed(6) : DASH,
      'economic net after AI cost': fmtMoney(pr.economic_net_after_ai, true),
      'models resolved': (st.models_resolved || []).join(', ') || DASH,
    }));
    g.append(card);
    const ce = d.control_equity || [], je = d.jev_equity || [];
    if (ce.length > 1 && je.length > 1) {
      const all = ce.concat(je).map((p) => p[1]); const lo = Math.min(...all), hi = Math.max(...all), span = (hi - lo) || 1;
      const t0 = Math.min(ce[0][0], je[0][0]), t1 = Math.max(ce[ce.length - 1][0], je[je.length - 1][0]), tspan = (t1 - t0) || 1;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 600 140'); svg.setAttribute('class', 'arena-eq'); svg.setAttribute('preserveAspectRatio', 'none');
      [[ce, 'var(--muted)'], [je, 'var(--accent)']].forEach(([series, color]) => {
        const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        pl.setAttribute('points', series.map((p) => ((p[0] - t0) / tspan * 600).toFixed(1) + ',' + (130 - (p[1] - lo) / span * 120).toFixed(1)).join(' '));
        pl.setAttribute('fill', 'none'); pl.setAttribute('stroke', color); pl.setAttribute('stroke-width', '1.5');
        svg.append(pl);
      });
      g.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'equity' }),
        h('span', { class: 'sub', text: 'grey CONTROL · blue +JEV' })), svg));
    }
    g.append(this.calibrationCard(d.calibration || [], 'calibration (this pair)'));
    const insp = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('h2', { text: 'decision inspector (' + (d.decisions || []).length + ' of ' + d.decisions_total + ')' })));
    const f = this.jevActionFilter || '';
    const sel = h('select', { onchange: (e) => { this.jevActionFilter = e.target.value; this.renderJev(); } },
      ['', 'TAKE', 'REDUCE', 'SKIP'].map((v) => h('option', { value: v, text: v || 'all actions' })));
    sel.value = f;
    insp.append(h('div', { class: 'arena-filters' }, field('action', sel)));
    const dt = h('table', { class: 'tbl' });
    renderTable(dt, [
      { h: 'signal (UTC)', cell: (r) => fmtTs(r.signal_ts) },
      { h: 'side', cell: (r) => pill(r.side, r.side) },
      { h: 'take p', num: true, cell: (r) => fmtNum(r.take_probability, 3) },
      { h: 'quality', num: true, cell: (r) => fmtNum(r.setup_quality, 2) },
      { h: 'risk state', cell: (r) => r.risk_state || DASH },
      { h: 'regime', cell: (r) => r.regime || DASH },
      { h: 'action', cell: (r) => pill(r.final_action, r.final_action === 'SKIP' ? 'down' : r.final_action === 'REDUCE' ? 'warn' : 'up') },
      { h: 'x', num: true, cell: (r) => fmtNum(r.risk_multiplier, 2) },
      { h: 'outcome', cell: (r) => (r.outcome_kind || DASH) + (r.outcome_exit ? ' ' + r.outcome_exit : '') },
      { h: 'net', num: true, cell: (r) => num(r.outcome_net) },
      { h: 'R', num: true, cell: (r) => fmtNum(r.outcome_r, 2) },
      { h: 'market (5-bar · ATR% · RSI)', cell: (r) => { const m = (r.state || {}).market || {}; return pctText(m.ret_5, 2) + ' · ' + pctText(m.atr_pct, 2) + ' · ' + fmtNum(m.rsi_14, 0); } },
      { h: 'error', cell: (r) => r.error_code || '' },
    ], (d.decisions || []).filter((r) => !f || r.final_action === f), { empty: 'no decisions' });
    insp.append(h('div', { class: 'tablewrap' }, dt));
    g.append(insp);
  },

  async runCompetition() {
    const v = (id) => ($(id) ? $(id).value.trim() : '');
    const toMs = (s) => { const t = Date.parse(s.replace(' ', 'T') + 'Z'); return Number.isFinite(t) ? t : null; };
    const body = {
      label: v('#comp-f-label'),
      starting_balance: Number(v('#comp-f-balance')) || 20,
      max_leverage: Number(v('#comp-f-lev')) || 20,
      min_closed_trades: Number(v('#comp-f-minlev')) || 100,
      profile: v('#comp-f-profile') || 'realistic',
      symbols: v('#comp-f-symbols').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean),
      strategies: v('#comp-f-strats').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean),
      start_ms: toMs(v('#comp-f-start')), end_ms: toMs(v('#comp-f-end')),
    };
    try {
      const r = await post(this.base + '/run', body);
      toast('competition started (' + r.run_id + ')');
      if (typeof Nav !== 'undefined' && Nav.go) Nav.go('validation', 'overview'); else this.showTab('overview');
    } catch (e) { setText($('#comp-run-msg'), e.message); toast(e.message, 'err'); }
  },
};
