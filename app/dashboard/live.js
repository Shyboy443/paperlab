/* PaperLab - live views: the arena's alive header, the live shadow (frozen v2 bots and their +JEV
   twins on live market data), the activity feed, the live market and system health.

   Shared by the private dashboard and the public inspection page. Everything live arrives by push
   (stream.js); REST is used for history and for the full payload when a view opens or an event says
   the numbers behind it changed. Built with textContent only, like the rest of the dashboard. */
'use strict';

const ACT_ICON = { candidate: '◆', decision: '◇', open: '▲', closed: '■', error: '✕', session: '●',
  fill: '▪', signal: '◆', note: '✎', competition: '⚑', evaluating: '…' };
const MODE_KIND = { ATTACK: 'up', NORMAL: 'accent', DEFENSIVE: 'warn', HALTED: 'down', WARMING_UP: 'ghost', ERROR: 'down' };
const fmtBps = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(1) + ' bps' : DASH);
const fmtMs = (v) => (isNum(v) ? Math.round(v) + ' ms' : DASH);
const pctOf = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + '%' : DASH);
const shortKey = (k) => String(k || '').replace('@20x-v2', '').replace('USDT', '');
/** Pushed number that flashes green/red when it moved since the previous render. */
const PREV = new Map();
function pushed(key, value, text, cls) {
  const el = h('span', { class: cls || 'num', text });
  const prev = PREV.get(key);
  if (isNum(value) && isNum(prev) && Math.abs(prev - value) > 1e-12) el.classList.add(value > prev ? 'flash-up' : 'flash-down');
  if (isNum(value)) PREV.set(key, value);
  return el;
}

// ---- the alive header ------------------------------------------------------------------------
const Alive = {
  health: null, stream: { status: 'OFF' },
  mount() { this.el = $('#alive'); },
  setHealth(h) { this.health = h; this.render(); },
  setStream(info) { this.stream = info; this.render(); },
  render() {
    const el = this.el || $('#alive');
    if (!el) return;
    clear(el);
    const sh = (this.health && this.health.shadow) || {};
    const jl = Shadow.data && Shadow.data.jev_live;
    const st = sh.status || 'DISABLED';
    const item = (value, label, kind, title) => h('span', { class: 'alive-item ' + (kind || ''), title: title || null },
      h('b', null, pushed('alive:' + label, Number(value), value, '')), h('span', { text: ' ' + label }));
    const dot = (ok) => h('i', { class: 'dot ' + (ok ? 'ok' : 'off') });
    if (st === 'DISABLED') el.append(item('LIVE SHADOW', 'off', 'ghost', 'LIVE_SHADOW_ENABLED is false on this server'));
    else {
      el.append(item(String(sh.active ?? 0), 'ACTIVE', 'up', (sh.controls ?? 0) + ' frozen v2 controls'),
        item(String(sh.positions_open ?? 0), 'positions open', sh.positions_open ? 'accent' : ''),
        item(String(sh.evaluating ?? 0), 'evaluating', sh.evaluating ? 'warn' : '', 'candidates waiting on Jev'),
        item(String(sh.attack ?? 0), 'ATTACK', sh.attack ? 'up' : '', 'bots whose open position was sized in ATTACK'));
      if (st !== 'LIVE') el.append(item(st.replace('_', ' '), '', st === 'WARMING_UP' ? 'warn' : 'down'));
    }
    const jevOk = jl && jl.decisions ? 1 - (jl.failure_rate || 0) : null;
    const jh = (this.health && this.health.jev) || {};
    const jevText = isNum(jevOk) ? (jevOk * 100).toFixed(1) + '%' : (jh.status || sh.jev_state || '—');
    el.append(h('span', { class: 'alive-item', title: 'live Jev decisions answered without error' },
      h('span', { text: 'JEV HEALTH ' }), dot(isNum(jevOk) ? jevOk > 0.95 : jh.status === 'OK'), h('b', { text: ' ' + jevText })));
    const live = this.stream.status === 'LIVE';
    const tr = this.stream.transport === 'sse' ? 'SSE' : 'WEBSOCKET';
    el.append(h('span', { class: 'alive-item', title: 'server push connection (' + tr.toLowerCase() + ')' },
      h('span', { text: tr + ' ' }), dot(live), h('b', { text: ' ' + (this.stream.status || 'OFF') })));
    if (typeof V3 !== 'undefined' && V3.data) V3.refreshHeader();
    if (typeof V31 !== 'undefined' && V31.data) V31.refreshHeader();
  },
};

// ---- live shadow ------------------------------------------------------------------------------
const Shadow = {
  base: '/api/competition', data: null, busy: false, timer: 0, lastLoad: 0, filter: { coin: '', tf: '', role: '' },
  visible() { const p = $('[data-panel="live"]'); return p && !p.hidden; },

  /** The full payload (forward stats, pairs, Jev live stats): when a view opens, and after events
      that change those numbers -- never on a timer. */
  async load() {
    if (this.busy) { this.flush(); return; }
    this.busy = true;
    try { this.data = await api(this.base + '/shadow'); this.lastLoad = Date.now(); this.render(); Alive.render(); }
    catch (e) { if (e.status !== 401) this.fail(e); }
    finally { this.busy = false; }
  },
  /** Coalesce a burst of events into one reload, at most every 4 s, and only while it is on screen. */
  flush() {
    if (this.timer) return;
    const wait = Math.max(250, 4000 - (Date.now() - this.lastLoad));
    this.timer = setTimeout(() => { this.timer = 0; if (this.visible() || Market.visible()) this.load(); }, wait);
  },

  fail(e) {
    const g = $('#live-grid');
    if (!g) return;
    clear(g).append(h('div', { class: 'card' }, h('h2', { text: 'could not load the live shadow' }),
      h('p', { class: 'msg err', text: (e && e.message) || String(e) })));
  },

  /** Pushed every couple of seconds while anything moves: header counts, bot rows, market. */
  onState(s) {
    Alive.setHealth(Object.assign({}, Alive.health || {}, { shadow: s.health }));
    if (!this.data) return;
    const byKey = new Map((s.bots || []).map((b) => [b.key, b]));
    for (const b of this.data.bots || []) {
      const u = byKey.get(b.key);
      if (!u) continue;
      Object.assign(b, { mode: u.mode, live: u.live, evaluating: u.evaluating, equity: u.equity, net: u.net,
        trades: u.trades, last_bar_ts: u.last_bar_ts, error: u.error });
      b.open_positions = (u.positions || []).map((p) => Object.assign({}, p));
    }
    this.data.status = s.health || this.data.status;
    this.data.prices = s.prices || this.data.prices;
    if (this.visible()) this.renderBots();
    if (Market.visible()) Market.render();
  },

  render() {
    const g = $('#live-grid');
    if (!g || !this.data) return;
    clear(g);
    const d = this.data, st = d.status || {}, cfg = d.config || {};
    g.append(this.statusCard(d, st, cfg));
    g.append(h('div', { class: 'card', id: 'live-bots-card' }));
    this.renderBots();
    g.append(this.pairsCard(d));
    g.append(this.jevLiveCard(d));
    g.append(this.historicalCard(d.jev_historical || {}));
    g.append(this.decisionsCard(d));
  },

  // One continuous book per bot across restarts (app/live/continuity.py), and the replay check
  // that proves a resumed session reproduced what the earlier ones recorded.
  continuityLine(fx) {
    const c = fx.continuity || {};
    if (!c.mode) return null;
    const resumed = (c.resumed_from_sessions || []).length;
    const bad = (c.diverged || 0) + (c.unreproduced || 0);
    return h('p', { class: 'sub' + (bad ? ' down' : '') },
      h('b', { text: 'CONTINUOUS BOOK' }),
      h('span', { text: ' since ' + fmtTs(c.forward_from_ms) + ' UTC — a restart re-derives every book from the closed 1m tape, so open positions, '
        + 'drawdown and halts carry over and a stop never invents an exit. '
        + (resumed ? 'This session resumed ' + resumed + ' earlier session' + (resumed > 1 ? 's' : '') + '; replay check against what they recorded: matched '
          + (c.matched ?? 0) + ', diverged ' + (c.diverged ?? 0) + ', unreproduced ' + (isNum(c.unreproduced) ? c.unreproduced : 'pending') + '. ' : '')
        + 'Recorded from the tape because no earlier session recorded them (downtime, or a pre-continuity session\'s reset book): '
        + (c.rederived ?? 0) + ' events · ' + (fx.rederived_trades || 0) + ' closed trades, flagged re-derived.' }));
  },

  statusCard(d, st, cfg) {
    const s = st.status || 'DISABLED';
    const kind = s === 'LIVE' ? 'up' : s === 'WARMING_UP' || s === 'STARTING' ? 'warn' : s === 'DISABLED' ? 'ghost' : 'down';
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'LIVE SHADOW — FORWARD TEST' }), pill(s.replace('_', ' '), kind),
      h('span', { class: 'spacer' }),
      h('span', { class: 'sub', text: (cfg.source_label || 'no source run') + (cfg.source_run_id ? ' · ' + cfg.source_run_id : '') })));
    if (s === 'DISABLED') {
      card.append(h('p', { class: 'sub', text: 'The live shadow is not running on this server (LIVE_SHADOW_ENABLED=false). Nothing below is live.' }));
    }
    card.append(h('p', { class: 'sub', text: 'Frozen v2 bots from the TEST arena trade simulated 20 USDT books on live Binance USD-M market data: '
      + 'same engine, fees, execution model, cost gate and RiskManager as the replay. Nothing here can place an order. '
      + 'This is FORWARD data — unseen by every strategy and by Jev.' }));
    const pairs = st.pairs || 0, elig = (d.eligible_controls || []).length, minPairs = cfg.min_pairs || 10;
    if (elig < minPairs) {
      card.append(h('div', { class: 'banner warn' }, h('b', { text: 'INSUFFICIENT JEV-ELIGIBLE CONTROLS' }),
        h('span', { text: ' — ' + elig + ' of the ' + (cfg.controls || []).length + ' frozen controls pass JEV_ELIGIBLE_CONTROL on TEST data; '
          + minPairs + ' are required for a live Jev verdict. ' + (pairs ? pairs + ' CONTROL vs +JEV pairs run and collect evidence.' : 'No pairs are running.') })));
    }
    const fx = d.forward_experiment;
    if (fx) {
      const id = fx.identity || {};
      card.append(h('div', { class: 'fx' },
        h('div', { class: 'card-head' }, h('h3', null, 'FORWARD EXPERIMENT ', h('span', { class: 'mono keep-case', text: fx.experiment_id })),
          pill(id.venue === 'BINANCE_USDM' ? 'BINANCE USD-M' : (id.venue || '?'), 'accent'),
          h('span', { class: 'sub', text: 'every compatible session combined — a deploy does not reset the evidence' })),
        h('div', { class: 'tiles small' },
          tile('forward started', fmtTs(fx.started_ts), '', 'UTC'),
          tile('elapsed', fmtDur((fx.elapsed_ms || 0) / 1000), '', 'observed ' + fmtDur((fx.observed_ms || 0) / 1000)
            + (isNum(fx.coverage) ? ' (' + Math.round(fx.coverage * 100) + '%)' : '')),
          tile('sessions', String(fx.sessions || 0), '', 'restarts stitch into this experiment'),
          tile('signals', String(fx.signals || 0), '', 'candidate entries emitted'),
          tile('closed trades', String(fx.closed_trades || 0), '', '+JEV twins ' + (fx.closed_trades_jev || 0)),
          tile('Jev decisions', String(fx.jev_decisions || 0))),
        this.continuityLine(fx),
        h('p', { class: 'sub mono', text: 'identity: strategy ' + Object.entries(id.strategy_fingerprints || {}).map(([k, v]) => k + ' ' + v).join(' · ')
          + ' · config ' + (id.arena_config_fingerprint || DASH) + ' · Jev ' + ((id.jev || {}).policy_version || DASH) }),
        (d.other_experiments || []).length ? h('p', { class: 'sub', text: 'Earlier incompatible experiments are kept separately, never merged: '
          + d.other_experiments.map((o) => o.experiment_id + ' (' + o.sessions + ' sessions)').join(', ') }) : null));
    }
    const ft = d.forward_total || {};
    card.append(h('div', { class: 'tiles' },
      tile('active controls', String(st.active ?? 0), st.active ? 'up' : '', 'of ' + (st.controls ?? 0) + ' · warming ' + (st.warming ?? 0)),
      tile('+JEV pairs', String(pairs), pairs ? 'accent' : '', 'eligible ' + elig + ' / min ' + minPairs),
      tile('positions open', String(st.positions_open ?? 0), st.positions_open ? 'accent' : ''),
      tile('forward trades', String(ft.trades ?? 0), '', 'all sessions, controls'),
      tile('forward net', fmtMoney(ft.net, true), signClass(ft.net), 'after fees, slippage, funding'),
      tile('session', d.session ? (d.session.status || DASH) : DASH, '', d.session ? 'since ' + fmtTs(d.session.created_ts) : 'never started'),
      tile('feed', st.feed && st.feed.klines ? 'klines ● book ' + (st.feed.book ? '●' : '○') : 'down', st.feed && st.feed.klines ? 'up' : 'down',
        st.feed ? (st.feed.bars_live ?? 0) + ' live bars · ' + (st.feed.gaps_repaired ?? 0) + ' gaps repaired' : '')));
    const fp = cfg.strategy_fingerprints || {};
    if (Object.keys(fp).length) {
      card.append(h('p', { class: 'sub mono', text: 'frozen v2 fingerprints: ' + Object.entries(fp).map(([k, v]) => k + ' ' + v).join(' · ')
        + (cfg.fees ? ' · fees maker ' + pctOf(cfg.fees.maker_rate, 3) + ' / taker ' + pctOf(cfg.fees.taker_rate, 3) : '')
        + (isNum(cfg.cost_gate_min_ratio) ? ' · cost gate ' + cfg.cost_gate_min_ratio + '×' : '') }));
    }
    return card;
  },

  renderBots() {
    const card = $('#live-bots-card');
    if (!card || !this.data) return;
    clear(card);
    const bots = this.data.bots || [];
    const f = this.filter;
    const coins = [...new Set(bots.map((b) => b.coin || String(b.symbol || '').replace('USDT', '')))].sort();
    const sel = (name, opts, label) => h('select', { 'aria-label': label, onchange: (e) => { f[name] = e.target.value; this.renderBots(); } },
      h('option', { value: '', text: label }), opts.map((o) => h('option', { value: o, text: o, selected: f[name] === o })));
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'traders' }),
      h('span', { class: 'sub', text: bots.length + ' books · equity marks move with the live mid; entries, stops and exits run on closed 1m bars' }),
      h('span', { class: 'spacer' }), sel('coin', coins, 'all coins'), sel('tf', ['5m', '15m', '30m'], 'all timeframes'),
      sel('role', ['CONTROL', 'JEV'], 'controls + Jev')));
    const rows = bots.filter((b) => (!f.coin || (b.coin || '') === f.coin) && (!f.tf || b.timeframe === f.tf) && (!f.role || b.role === f.role));
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'bot', cell: (b) => [h('span', { class: 'sid', text: shortKey(b.key) }), b.role === 'JEV' ? pill('+JEV', 'accent') : null] },
      { h: 'mode', cell: (b) => [pill(b.mode || '—', MODE_KIND[b.mode] || 'ghost'), b.evaluating ? pill('EVALUATING', 'warn') : null] },
      { h: 'equity', num: true, cell: (b) => pushed('eq:' + b.key, b.equity, fmtMoney(b.equity), 'num ' + signClass(b.net)) },
      { h: 'book net', num: true, title: 'continuous book since the forward experiment began: realised + open, after all costs', cell: (b) => pushed('net:' + b.key, b.net, fmtMoney(b.net, true), 'num ' + signClass(b.net)) },
      { h: 'position', cell: (b) => (b.open_positions || []).map((p, i) => h('span', { class: 'pos' },
        pill(p.side, p.side), ' ' + fmtQty(p.qty) + ' @ ' + fmtPrice(p.entry) + ' ',
        pushed('upnl:' + b.key + ':' + i, p.upnl, fmtMoney(p.upnl, true), 'num ' + signClass(p.upnl)),
        isNum(p.mark) ? h('span', { class: 'sub', text: ' mark ' + fmtPrice(p.mark) }) : null)) },
      { h: 'fwd trades', num: true, cell: (b) => String((b.forward || {}).trades || 0) },
      { h: 'fwd net', num: true, cell: (b) => num((b.forward || {}).net) },
      { h: 'fwd exp R', num: true, cell: (b) => fmtNum((b.forward || {}).expectancy_r, 2) },
      { h: 'last bar', cell: (b) => h('span', { class: 'sub', text: b.last_bar_ts ? ago(b.last_bar_ts) : DASH }) },
      { h: '', cell: (b) => (b.error ? h('span', { class: 'down', title: b.error, text: 'error' }) : '') },
    ], rows, { empty: this.data.status && this.data.status.status === 'DISABLED' ? 'live shadow is off on this server' : 'no bots yet',
      rowClass: (b) => (b.role === 'JEV' ? 'twin' : '') });
    card.append(h('div', { class: 'tablewrap' }, table));
  },

  pairsCard(d) {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'CONTROL vs +JEV (live)' }),
      h('span', { class: 'sub', text: 'identical live bars; Jev can only scale or skip what the RiskManager approved' })));
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'control', cell: (p) => h('span', { class: 'sid', text: shortKey(p.control_key) }) },
      { h: 'control net', num: true, cell: (p) => num(p.control.net) },
      { h: 'trades', num: true, cell: (p) => String(p.control.trades) },
      { h: '+JEV net', num: true, cell: (p) => num(p.jev.net) },
      { h: 'trades', num: true, cell: (p) => String(p.jev.trades) },
      { h: 'JEV EDGE DELTA', num: true, cell: (p) => num(p.edge_delta) },
      { h: 'decisions', num: true, cell: (p) => String(p.decisions) },
      { h: 'skipped / taken', num: true, cell: (p) => p.skipped + ' / ' + p.taken },
    ], d.pairs || [], { empty: 'no CONTROL vs +JEV pairs are running' });
    card.append(h('div', { class: 'tablewrap' }, table));
    return card;
  },

  jevLiveCard(d) {
    const s = d.jev_live || {}, v = s.verdict || {};
    const vk = v.verdict === 'EDGE (PROVISIONAL)' ? 'up' : v.verdict === 'NO EDGE' ? 'down' : 'warn';
    const card = h('div', { class: 'card' });
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'JEV V1 — LIVE SHADOW' }), pill(v.status || 'NO DATA', vk),
      h('span', { class: 'spacer' }), h('span', { class: 'sub', text: 'frozen JEV_PROMPT_V1 / JEV_POLICY_V1 · failures and timeouts are SKIP' })));
    card.append(h('p', { class: 'jev-answer ' + vk, text: 'LIVE VERDICT: ' + (v.verdict || 'NO VERDICT') + (v.why ? ' — ' + v.why : '') }));
    card.append(h('div', { class: 'tiles' },
      tile('decisions', String(s.decisions ?? 0), '', 'resolved ' + (s.resolved ?? 0) + ' / ' + (v.min_resolved ?? 100) + ' needed'),
      tile('skip rate', pctOf(s.skip_rate), '', Object.entries(s.actions || {}).map(([k, n]) => k + ' ' + n).join(' · ') || DASH),
      tile('failure rate', pctOf(s.failure_rate, 2), s.errors ? 'down' : '', 'timeouts ' + pctOf(s.timeout_rate, 2)),
      tile('latency p50 / p95 / p99', [s.latency_p50_ms, s.latency_p95_ms, s.latency_p99_ms].map((x) => (isNum(x) ? Math.round(x) : '—')).join(' / ') + ' ms'),
      tile('AI latency slippage', fmtBps(s.latency_slippage_bps_mean), signClass(-(s.latency_slippage_bps_mean || 0)),
        'p95 ' + fmtBps(s.latency_slippage_bps_p95) + ' · on taken ' + fmtMoney(-(s.latency_slippage_usdt_taken || 0), true)),
      tile('AUC take p → win', fmtNum(v.auc && v.auc.auc, 3), v.auc && isNum(v.auc.low) && v.auc.low > 0.5 ? 'up' : 'warn',
        v.auc && isNum(v.auc.low) ? '95% CI ' + fmtNum(v.auc.low, 3) + '–' + fmtNum(v.auc.high, 3) : 'needs wins and losses'),
      tile('+JEV vs control', fmtMoney(v.jev_minus_control, true), signClass(v.jev_minus_control), 'control ' + fmtMoney(v.control_net, true)),
      tile('+JEV vs always-skip', fmtMoney(v.jev_minus_always_skip, true), signClass(v.jev_minus_always_skip), 'trades taken ' + (v.trades_taken ?? 0)),
      tile('API cost', '$' + (s.cost_usd || 0).toFixed(4))));
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'take probability', cell: (r) => r.bucket },
      { h: 'n', num: true, cell: (r) => String(r.n) },
      { h: 'mean p', num: true, cell: (r) => fmtNum(r.mean_take_probability, 3) },
      { h: 'win rate', num: true, cell: (r) => pctOf(r.win_rate) },
      { h: 'mean R', num: true, cell: (r) => h('span', { class: 'num ' + signClass(r.mean_r), text: fmtNum(r.mean_r, 2) }) },
    ], s.calibration || [], { empty: 'no resolved decisions yet' });
    card.append(h('h3', { text: 'calibration (realized R of each candidate, taken or shadowed)' }), h('div', { class: 'tablewrap' }, table));
    return card;
  },

  historicalCard(hv) {
    return h('div', { class: 'card historical' },
      h('div', { class: 'card-head' }, h('h2', { text: 'JEV V1 HISTORICAL DISCOVERY VERDICT' }), pill(hv.verdict || 'NO EDGE', 'down'),
        h('span', { class: 'spacer' }), h('span', { class: 'sub', text: 'experiment ' + (hv.run_id || DASH) + ' · ' + (hv.period || '') })),
      h('div', { class: 'tiles small' },
        tile('verdict', hv.verdict || 'NO EDGE', 'down'),
        tile('skip rate', pctOf(hv.skip_rate), '', (hv.decisions || 0).toLocaleString() + ' decisions · ' + (hv.pairs || 0) + ' pairs'),
        tile('AUC', fmtNum(hv.auc, 3), 'warn', hv.auc_ci ? '95% CI ' + hv.auc_ci.join('–') : ''),
        tile('baseline', 'always-skip', 'warn', hv.null_note || '')),
      h('p', { class: 'sub', text: 'Kept for the record: V1 is judged again only on unseen live data above. No prompt or threshold was changed.' }));
  },

  decisionsCard(d) {
    const card = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'recent live Jev decisions' }),
      h('span', { class: 'sub', text: 'candidate → request → response → decision → hypothetical submission' })));
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'time', cell: (r) => fmtTime(r.candidate_wall_ms) },
      { h: 'bot', cell: (r) => shortKey(r.bot_key) },
      { h: 'side', cell: (r) => pill(r.side, r.side) },
      { h: 'action', cell: (r) => pill(r.final_action || '?', r.final_action === 'SKIP' ? 'ghost' : 'up', r.error_code || '') },
      { h: 'take p', num: true, cell: (r) => pctOf(r.take_probability, 0) },
      { h: 'latency', num: true, cell: (r) => fmtMs(r.latency_ms) },
      { h: 'slippage', num: true, cell: (r) => fmtBps(r.latency_slippage_bps) },
      { h: 'submit', cell: (r) => (r.submit_ms ? '+' + (r.submit_ms - r.candidate_wall_ms) + ' ms' : DASH) },
      { h: 'outcome', cell: (r) => (r.outcome_kind ? [pill(r.outcome_kind, r.outcome_kind === 'TAKEN' ? 'accent' : 'ghost'), ' ',
        h('span', { class: 'num ' + signClass(r.outcome_r), text: fmtNum(r.outcome_r, 2) + 'R' })] : h('span', { class: 'sub', text: 'open' })) },
      { h: 'error', cell: (r) => (r.error_code ? h('span', { class: 'down', text: r.error_code }) : '') },
    ], d.decisions_recent || [], { empty: 'no live decisions yet' });
    card.append(h('div', { class: 'tablewrap' }, table));
    return card;
  },
};

// ---- activity -----------------------------------------------------------------------------------
const Activity = {
  base: '/api/competition', items: [], seen: new Set(), filter: 'all', loaded: false, oldest: 0, private: false,
  visible() { const p = $('[data-panel="activity"]'); return p && !p.hidden; },

  async load() {
    try {
      const r = await api(this.base + '/shadow/activity?limit=150');
      for (const e of (r.events || []).reverse()) this.add(e.kind, e, 'history:' + e.id, e.ts);
      const ids = (r.events || []).map((e) => e.id);
      this.oldest = ids.length ? Math.min(...ids) : 0;
      this.loaded = true;
    } catch (e) { if (e.status !== 401) console.warn('[activity] history failed', e); }
    this.render();
  },
  async older() {
    if (!this.oldest) return;
    try {
      const r = await api(this.base + '/shadow/activity?limit=150&before=' + this.oldest);
      const evs = r.events || [];
      for (const e of evs) this.add(e.kind, e, 'history:' + e.id, e.ts, true);
      this.oldest = evs.length ? Math.min(...evs.map((e) => e.id)) : 0;
      this.render();
    } catch (e) { toast(e.message, 'bad'); }
  },

  /** Every event the stream brings that belongs in the feed. */
  onEvent(channel, data) {
    if (channel === 'shadow') this.add(data.type, data, null, data.ts);
    else if (channel === 'jev') this.add('decision', data, null, data.ts);
    else if (channel === 'fill' || channel === 'signal' || channel === 'note') this.add(channel, data, channel + ':' + (data.id || ''), data.ts);
    else if (channel === 'competition') this.add('competition', data, null, data.ts);
    else return;
    if (this.visible()) this.render();
  },

  add(kind, d, key, ts, append) {
    if (kind === 'evaluating') return;
    if (key && this.seen.has(key)) return;
    if (key) this.seen.add(key);
    const item = { kind, d, ts: ts || Date.now() };
    if (append) this.items.push(item); else this.items.unshift(item);
    if (this.items.length > 600) this.items.length = 600;
  },

  group(kind) {
    if (kind === 'candidate') return 'candidates';
    if (kind === 'decision') return 'jev';
    if (kind === 'open' || kind === 'closed') return 'trades';
    if (kind === 'fill' || kind === 'signal' || kind === 'note') return 'paper';
    return 'system';
  },

  line(it) {
    const d = it.d, k = it.kind;
    const who = [shortKey(d.bot_key || ''), d.role === 'JEV' ? '+JEV' : ''].filter(Boolean).join(' ');
    const tag = [d.strategy_id || (d.bot_key || '').split('-')[0], String(d.symbol || '').replace('USDT', ''), d.timeframe].filter(Boolean).join(' · ');
    if (k === 'candidate') {
      return [h('b', { text: tag + ' CANDIDATE ' }), pill(d.side, d.side), ' ',
        h('span', { class: 'sub', text: (isNum(d.signal_quality) ? 'q ' + d.signal_quality.toFixed(2) + ' · ' : '') + (isNum(d.edge_to_cost) ? 'edge/cost ' + d.edge_to_cost.toFixed(1) + '× · ' : '') }),
        pill(String(d.outcome || '').split(':')[0], d.outcome === 'ORDERED' ? 'up' : d.outcome === 'COST_REJECTED' ? 'warn' : 'ghost', d.outcome), who.includes('+JEV') ? pill('+JEV', 'accent') : null];
    }
    if (k === 'decision') {
      return [h('b', { text: '◇ JEV ' + (d.final_level || d.final_action || '?') }), ' ', h('span', { text: pctOf(d.take_probability, 0) + ' ' + fmtMs(d.latency_ms) }),
        h('span', { class: 'sub', text: ' · ' + tag + ' ' + (d.side || '') + (isNum(d.latency_slippage_bps) ? ' · slip ' + fmtBps(d.latency_slippage_bps) : '') }),
        d.error_code ? pill(d.error_code, 'down') : null];
    }
    if (k === 'open') {
      return [h('b', { text: String(d.side || '').toUpperCase() + ' OPENED ' }), h('span', { text: tag + ' ' }), ' ' + fmtQty(d.qty) + ' @ ' + fmtPrice(d.price),
        h('span', { class: 'sub', text: ' · ' + (d.attack_state || 'NORMAL') + (isNum(d.risk_pct) ? ' · risk ' + pctOf(d.risk_pct, 2) : '') + (d.leverage ? ' · ' + d.leverage + 'x' : '') }),
        d.role === 'JEV' ? pill('+JEV', 'accent') : null];
    }
    if (k === 'closed') {
      return [h('span', { class: 'num ' + signClass(d.r), text: (isNum(d.r) ? (d.r > 0 ? '+' : '') + d.r.toFixed(2) + 'R ' : '') }),
        h('b', { text: (d.counterfactual ? '◌ SHADOW ' : '') + 'CLOSED ' }),
        num(d.net), h('span', { class: 'sub', text: ' · ' + tag + ' ' + (d.side || '') + ' · ' + (d.exit_kind || '') + (d.counterfactual ? ' · not traded (counterfactual)' : '') }),
        d.role === 'JEV' ? pill('+JEV', 'accent') : null];
    }
    if (k === 'fill') {
      return [h('b', { text: 'PAPER ' + (d.kind || '').toUpperCase() + ' ' + (d.strategy_id || '') + ' ' + String(d.symbol || '').replace('USDT', '') + ' ' }),
        pill(d.side, d.side === 'buy' ? 'long' : 'short'), ' ' + fmtQty(d.qty) + ' @ ' + fmtPrice(d.price),
        isNum(d.r) ? h('span', { class: 'num ' + signClass(d.r), text: ' ' + (d.r > 0 ? '+' : '') + d.r.toFixed(2) + 'R' }) : null,
        !d.is_open && isNum(d.pnl) ? [' ', num(d.pnl)] : null];
    }
    if (k === 'signal') {
      return [h('b', { text: 'PAPER SIGNAL ' + (d.strategy_id || '') + ' ' + String(d.symbol || '').replace('USDT', '') + ' ' + (d.tf || '') + ' ' }),
        pill(d.side || d.kind, d.side), ' ', pill(d.status, d.status, d.reason || '')];
    }
    if (k === 'note') return [h('b', { text: '✎ ' }), h('span', { text: d.text || '' })];
    if (k === 'competition') {
      return [h('b', { text: '⚑ ' + String(d.kind || '').toUpperCase() + ' ' }),
        h('span', { text: [d.label || d.run_id || '', d.status || '', d.stage || '', isNum(d.pct) ? Math.round(d.pct * 100) + '%' : '',
          isNum(d.done) && isNum(d.total) ? d.done + '/' + d.total : ''].filter(Boolean).join(' · ') })];
    }
    if (k === 'session') return [h('b', { text: '● LIVE SHADOW ' + (d.status || '') })];
    if (k === 'error') return [h('b', { class: 'down', text: '✕ ' + who + ' ' }), h('span', { text: d.error || '' })];
    return [h('span', { text: k })];
  },

  render() {
    const box = $('#activity-feed');
    if (!box) return;
    const bar = $('#activity-filters');
    if (bar && !bar.dataset.built) {
      bar.dataset.built = '1';
      const groups = [['all', 'All'], ['candidates', 'Candidates'], ['jev', 'Jev'], ['trades', 'Trades'], ['system', 'System']];
      if (this.private) groups.splice(4, 0, ['paper', 'Paper engine']);
      for (const [id, label] of groups) {
        bar.append(h('button', { type: 'button', class: 'chip' + (id === this.filter ? ' active' : ''), 'data-f': id, text: label,
          onclick: () => { this.filter = id; $$('#activity-filters [data-f]').forEach((b) => b.classList.toggle('active', b.dataset.f === id)); this.render(); } }));
      }
    }
    const rows = this.items.filter((it) => this.filter === 'all' || this.group(it.kind) === this.filter).slice(0, 300);
    clear(box);
    if (!rows.length) { box.append(h('li', { class: 'muted', text: this.loaded ? 'nothing yet — events appear here the moment they happen' : 'loading…' })); return; }
    for (const it of rows) {
      box.append(h('li', { class: 'act act-' + it.kind + (it.d && it.d.role === 'JEV' ? ' jev' : '') },
        h('span', { class: 'act-time', text: fmtTime(it.ts) }), h('span', { class: 'act-icon', text: ACT_ICON[it.kind] || '·' }),
        h('span', { class: 'act-body' }, this.line(it))));
    }
    if (this.oldest) box.append(h('li', { class: 'more' }, h('button', { type: 'button', class: 'btn ghost', text: 'load older', onclick: () => this.older() })));
  },
};

// ---- live market ------------------------------------------------------------------------------------
const Market = {
  visible() { const p = $('[data-panel="market"]'); return p && !p.hidden; },
  render() {
    if (typeof V6Market !== 'undefined') { V6Market.render(); return; }   // V6: the market is the Bybit feed the forward bots trade
    const box = $('#market-live');
    if (!box) return;
    clear(box);
    const prices = (Shadow.data && Shadow.data.prices) || {};
    const rows = Object.entries(prices).map(([sym, p]) => Object.assign({ symbol: sym }, p));
    box.append(h('div', { class: 'card-head' }, h('h2', { text: 'live market — Binance USD-M' }),
      h('span', { class: 'sub', text: 'public market data the live shadow trades on: best bid/ask mid, spread, funding' })));
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'coin', cell: (r) => h('span', { class: 'sid', text: r.symbol.replace('USDT', '') }) },
      { h: 'mid', num: true, cell: (r) => pushed('mid:' + r.symbol, r.mid, fmtPrice(r.mid)) },
      { h: 'half spread', num: true, cell: (r) => (isNum(r.half_spread_bps) ? r.half_spread_bps.toFixed(r.half_spread_bps < 1 ? 3 : 2) + ' bps' : DASH) },
      { h: 'funding', num: true, cell: (r) => (isNum(r.funding_rate) ? (r.funding_rate * 100).toFixed(4) + '%' : DASH) },
      { h: 'next funding', cell: (r) => (r.next_funding_ts ? 'in ' + fmtDur((r.next_funding_ts - Date.now()) / 1000) : DASH) },
      { h: 'last 1m bar', cell: (r) => (r.last_bar_open ? fmtTime(r.last_bar_open) : DASH) },
    ], rows, { empty: 'the live shadow is not running on this server' });
    box.append(h('div', { class: 'tablewrap' }, table));
  },
};

// ---- system health --------------------------------------------------------------------------------
const Health = {
  data: null,
  set(d) { this.data = d; if (this.visible()) this.render(); },
  visible() { const p = $('[data-panel="health"]'); return p && !p.hidden; },
  render() {
    const box = $('#health-body');
    if (!box) return;
    const d = this.data || {}, s = Stream.info();
    clear(box).append(
      h('div', { class: 'card' }, h('h2', { text: 'stream' }), kvList({ status: s.status, connected_since: s.connectedAt || null,
        last_event: s.lastEventAt || null, events_received: s.received, reconnects: s.retries, server: d.stream || {} })),
      h('div', { class: 'card' }, h('h2', { text: 'live shadow' }), kvList(d.shadow || { status: 'DISABLED' })),
      h('div', { class: 'card' }, h('h2', { text: 'Jev API' }), kvList(d.jev || { status: 'unknown' })),
      d.engine ? h('div', { class: 'card' }, h('h2', { text: 'paper engine' }), kvList({ engine: d.engine, feed: d.feed })) : null);
  },
};

/** Wire a stream to the live views. Used by both entry points. */
function wireLiveViews() {
  Alive.mount();
  Stream.onStatus((info) => { Alive.setStream(info); if (Health.visible()) Health.render(); });
  Stream.on('health', (d) => { Alive.setHealth(d); Health.set(d); if (typeof Competition !== 'undefined') Competition.onHealth(d); });
  Stream.on('shadow_state', (d) => { Shadow.onState(d); if (typeof Candidates !== 'undefined') Candidates.onShadowState(d); });
  Stream.on('shadow', (d) => { Activity.onEvent('shadow', d); if (d.type === 'closed' || d.type === 'open' || d.type === 'session') Shadow.flush(); });
  Stream.on('jev', (d) => { Activity.onEvent('jev', d); Shadow.flush(); if (typeof Competition !== 'undefined') Competition.onJevEvent(d); });
  Stream.on('competition', (d) => { Activity.onEvent('competition', d); if (typeof Candidates !== 'undefined') Candidates.onCompetition(d); });
}
