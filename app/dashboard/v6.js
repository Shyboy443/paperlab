/* PaperLab - V6 FORWARD ARENA: the HOME screen of the public page and of the private dashboard.

   Frozen V6 bots trade LIVE Bybit market data with simulated fills (DRY_RUN: the V6 worker holds no exchange client,
   key or order path). The view reads one allow-listed payload, GET /api/public/competition/v6, then follows the public
   push channel: 'v6_state' (bot books, prices, worker health; every ~2 s while anything moves) and 'v6' (candidates,
   gate / Jev decisions, opens, closes, system events). Forward age and the next-decision countdown tick in the browser.

   Nothing here starts, stops or changes anything, and there is no backtest control: research lives under
   VALIDATION (history) and SYSTEM -> RESEARCH. Every request is a plain GET. */
'use strict';

const V6_HOUR = 3_600_000;
const V6_MATURITY_KIND = { 'TOO EARLY': 'ghost', COLLECTING: 'muted', 'EARLY SIGNAL': 'accent', 'MATURE SAMPLE': 'up' };
const v6Utc = (ms) => (ms ? new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : DASH);
const v6Clock = (ms) => (ms ? new Date(ms).toISOString().slice(11, 19) : DASH);
const v6Money = (v) => (isNum(v) ? (v < 0 ? '-' : v > 0 ? '+' : '') + Math.abs(v).toFixed(2) : DASH);
const v6R = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + 'R' : DASH);
const v6Pct = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + '%' : DASH);
const v6Price = (v) => (isNum(v) ? (Math.abs(v) >= 100 ? v.toFixed(2) : Math.abs(v) >= 1 ? v.toFixed(4) : v.toPrecision(4)) : DASH);
const v6Side = (s) => pill(String(s || '?').toUpperCase(), s === 'long' ? 'up' : 'down');
const v6Cause = (s) => (s ? String(s).replace(/_/g, ' ') : DASH);

/** "4h 23m", "1d 8h", "17d" -- the same rule as the server's age_text. */
function v6Age(ms) {
  if (!isNum(ms) || ms <= 0) return '0m';
  const m = Math.floor(ms / 60_000), d = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60), mm = m % 60;
  if (d >= 3) return d + 'd';
  if (d) return d + 'd ' + hh + 'h';
  return hh ? hh + 'h ' + String(mm).padStart(2, '0') + 'm' : mm + 'm';
}
function v6Countdown(ms) {
  if (!isNum(ms)) return DASH;
  const s = Math.max(0, Math.floor(ms / 1000)), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return (hh ? hh + ':' + String(mm).padStart(2, '0') : String(mm)) + ':' + String(ss).padStart(2, '0');
}

const V6Home = {
  base: '/api/public/competition',
  data: null, challenger: null, feed: [], seen: new Set(), feedFilter: 'all', boardFilter: 'all', boardAll: false, pairsAll: false, botKey: '', bot: null,
  timer: null, clock: null, reloadTimer: null, drawPending: false, lastDraw: 0,
  loading: false, latestState: null, latestV7State: null,

  visible() { const g = $('#home-grid'); return !!g && !g.closest('[hidden]'); },

  async load() {
    const grid = $('#home-grid');
    if (!grid || this.loading) return;
    this.loading = true;
    const beforeV6 = this.latestState, beforeV7 = this.latestV7State;
    try {
      const d = await getJSON(this.base + '/v6');
      this.data = d;
      try { this.challenger = await getJSON(this.base + '/v7'); }
      catch (_) { this.challenger = { status: { status: 'UNAVAILABLE' } }; }
      if (beforeV6 !== this.latestState) this.applyState(this.data, this.latestState);
      if (beforeV7 !== this.latestV7State) this.applyState(this.challenger, this.latestV7State);
      this.addEvents((d.activity || []).slice().reverse(), true);
      this.render();
    } catch (e) {
      if (!this.data) clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: 'V6 forward arena unavailable: ' + e.message })));
    }
    this.loading = false;
    clearInterval(this.timer);
    this.timer = setInterval(() => { if (!document.hidden && this.visible()) this.load(); }, 30_000);
    clearInterval(this.clock);
    this.clock = setInterval(() => this.tick(), 1000);
  },
  deactivate() { clearInterval(this.timer); clearInterval(this.clock); this.timer = this.clock = null; },

  /* ---- live pushes ---- */
  onState(d) {
    this.latestState = d;
    const x = this.data;
    if (!x || !x.experiment) { this.reloadSoon(1500); return; }
    this.applyState(x, d);
    this.draw();
    LivePrices.schedule();
  },
  onV7State(d) {
    this.latestV7State = d;
    if (!this.challenger?.experiment) { this.reloadSoon(1500); return; }
    this.applyState(this.challenger, d);
    this.draw();
    LivePrices.schedule();
  },
  applyState(x, d) {
    if (!x?.experiment || !d) return;
    if (d.health?.experiment_id && d.health.experiment_id !== x.experiment.experiment_id) {
      this.reloadSoon(1000); return;
    }
    if (d.health) x.status = Object.assign({}, x.status || {}, d.health);
    if (d.prices) x.prices = d.prices;
    const rows = new Map((x.leaderboard || []).map((r) => [r.key, r]));
    for (const b of d.bots || []) {
      const r = rows.get(b.key);
      if (!r) { this.reloadSoon(3000); continue; }
      for (const k of ['live', 'evaluating', 'equity', 'equity_live', 'trades', 'risk_state', 'error']) if (k in b) r[k] = b[k];
      const eq = isNum(b.equity_live) ? b.equity_live : b.equity;
      if (isNum(eq)) { r.equity_now = eq; r.net_now = eq - (r.start_equity || 20); }
      const old = r.open_positions || [];
      r.open_positions = (b.positions || []).map((p) => Object.assign({}, old.find((o) => o.side === p.side && o.entry === p.entry) || {}, p));
      if ((b.positions || []).length !== old.length) this.reloadSoon(2500);
    }
    this.recompute(x);
  },
  onEvent(d) {
    this.addEvents([d], false);
    if (['open', 'closed', 'system'].includes(d.type)) this.reloadSoon(2500);
    if (this.visible()) this.renderFeed();
  },
  reloadSoon(ms) {
    if (this.reloadTimer) return;
    this.reloadTimer = setTimeout(() => { this.reloadTimer = null; if (this.visible()) this.load(); }, ms);
  },
  /** Hero numbers follow the pushed books between full reloads. */
  recompute(x = this.data) {
    const hero = x.hero || {};
    const rows = x.leaderboard || [];
    hero.active_bots = rows.filter((r) => r.live).length;
    hero.total_virtual_equity = rows.reduce((a, r) => a + (isNum(r.equity_now) ? r.equity_now : 0), 0);
    hero.net_pnl = hero.total_virtual_equity - (hero.start_equity_total || 0);
    const now = Date.now();
    x.positions = [];
    for (const r of rows) for (const p of r.open_positions || []) {
      x.positions.push(Object.assign({ bot_key: r.key, coin: r.coin, horizon: r.horizon, role: r.role }, p,
        { hold_s: p.entry_ts ? Math.floor((now - p.entry_ts) / 1000) : null }));
    }
    x.positions.sort((a, b) => (a.entry_ts || 0) - (b.entry_ts || 0));
    hero.positions_open = x.positions.length;
    hero.status = x.status?.status || hero.status;
    hero.live = hero.status === 'LIVE';
    if (hero.forward_start_ms) hero.forward_age = v6Age(now - hero.forward_start_ms);
    rows.sort((a, b) => (isNum(b.net_now) ? b.net_now : -9e9) - (isNum(a.net_now) ? a.net_now : -9e9) || String(a.key).localeCompare(b.key));
    rows.forEach((r, i) => { r.rank = i + 1; });
    const ctl = new Map(rows.filter((r) => r.role === 'CONTROL').map((r) => [r.key, r]));
    for (const p of x.pairs || []) {
      const j = rows.find((r) => r.key === p.jev_key), c = ctl.get(p.control_key);
      if (j) p.jev_net = j.net_now;
      if (c) p.control_net = c.net_now;
      p.delta = (p.jev_net || 0) - (p.control_net || 0);
    }
    if ((x.pairs || []).length) hero.jev_edge = x.pairs.reduce((a, p) => a + (p.delta || 0), 0);
  },
  /** A live push re-draws only the cards that move (hero, leaderboard, positions, pairs), in place, at most every
      4 s: the page stays still and light, and the feed, costs and analytics wait for the next full load. */
  draw() {
    if (this.drawPending || !this.visible()) return;
    this.drawPending = true;
    const wait = Math.max(0, 4000 - (Date.now() - (this.lastDraw || 0)));
    setTimeout(() => requestAnimationFrame(() => {
      this.drawPending = false;
      this.lastDraw = Date.now();
      if (!this.renderLive()) this.render();
    }), wait);
  },
  renderLive() {
    const d = this.data;
    if (!d || !d.experiment || !$('#v6-hero-card')) return false;
    for (const [id, build] of [['v6-hero-card', () => this.hero(d)], ['v6-equity-card', () => this.equityCard()], ['v6-board-card', () => this.board(d)],
      ['v6-pos-card', () => this.positionsCard(d)], ['v6-pairs-card', () => this.pairsCard(d)],
      ['v7-challenger', () => this.challengerCard()]]) {
      const el = document.getElementById(id);
      if (el) el.replaceWith(build());
    }
    this.tick();
    LivePrices.schedule();
    return true;
  },

  /* ---- activity ---- */
  /** The same event arrives twice -- pushed live and again in a reload's history -- with different wall stamps, so
      the identity uses the event's own fields: signal / entry / exit instants, the fill instant, the session. */
  eventKey(e) {
    const t = e.type || e.kind;
    return [t, e.event, e.session_id, e.bot_key, e.side, e.signal_ts, e.entry_ts, e.exit_ts, t === 'open' ? e.ts : '',
      t === 'system' && !e.session_id ? e.ts : '', e.counterfactual ? 'cf' : ''].join('|');
  },
  addEvents(list, history) {
    for (const raw of list) {
      const e = Object.assign({}, raw, { type: raw.type || raw.kind });
      if (e.type === 'evaluating' || e.type === 'bot_state') continue;
      if (e.type === 'closed' && e.counterfactual) continue;               // shadow closes stay in the pair analytics
      if (e.type === 'decision' && !(e.role === 'JEV' && e.source === 'api') && !/^(LATE|DATA_STALE|MISSED)/.test(String(e.reason || ''))) continue;
      const k = this.eventKey(e);
      if (this.seen.has(k)) continue;
      this.seen.add(k);
      e.at = e.at || e.ts || Date.now();
      if (history) this.feed.push(e); else this.feed.unshift(e);
    }
    this.feed.sort((a, b) => (b.at || 0) - (a.at || 0));
    if (this.feed.length > 400) this.feed.length = 400;
  },
  group(t) { return t === 'candidate' ? 'candidates' : t === 'decision' ? 'jev' : t === 'open' || t === 'closed' ? 'trades' : 'system'; },
  line(e) {
    const who = h('b', { class: 'v6-bot', text: e.bot_key || '' });
    const sub = (s) => h('span', { class: 'sub', text: s });
    switch (e.type) {
      case 'candidate': {
        const oc = String(e.outcome || '');
        const kind = oc === 'ORDERED' ? 'up' : oc.startsWith('SKIPPED') ? 'warn' : 'ghost';
        return [who, ' candidate ', v6Side(e.side), ' ', sub('@ ' + v6Price(e.price) + ' · stop ' + v6Pct(e.stop_pct, 2) +
          (isNum(e.quality) ? ' · quality ' + e.quality.toFixed(2) : '') + (e.regime ? ' · market ' + e.regime : '') + ' → '),
        pill(oc ? v6Cause(oc.replace(':', ': ')) : 'PENDING', kind), e.tier ? [' ', pill(v6Cause(e.tier), 'muted')] : null];
      }
      case 'decision': {
        if (e.source !== 'api') return [who, ' ', pill(v6Cause(e.final_level || 'SKIP'), 'warn'), ' ', sub(v6Cause(e.reason) + ' — the CONTROL twin is blocked identically')];
        const lvl = e.final_level || '?';
        return [who, ' Jev ', pill(lvl, lvl === 'SKIP' ? 'down' : lvl === 'ATTACK' ? 'accent' : 'up'), ' ',
          sub((isNum(e.p_support) ? Math.round(e.p_support * 100) + '% support · ' : '') + (isNum(e.latency_ms) ? Math.round(e.latency_ms) + ' ms' : '') +
            (isNum(e.move_bps) ? ' · move ' + e.move_bps.toFixed(1) + ' bps' : '') + (e.error_code ? ' · ' + e.error_code : ''))];
      }
      case 'open':
        return [v6Side(e.side), h('b', { text: ' OPENED ' }), who, ' ', sub('@ ' + v6Price(e.price) + ' · risk ' + v6Pct(e.risk_pct, 2) +
          (isNum(e.risk_usd) ? ' (' + e.risk_usd.toFixed(2) + ' USDT)' : '') + ' · stop ' + v6Price(e.stop) + ' · target ' + v6Price(e.target) +
          ' · ' + v6Cause(e.tier || '') + (e.jev_level && e.role === 'JEV' ? ' · Jev ' + e.jev_level : ''))];
      case 'closed':
        return [h('b', { text: 'CLOSED ' }), who, ' ', v6Side(e.side), ' ', h('b', { class: signClass(e.r_net), text: v6R(e.r_net) }), ' ',
          sub('net ' + v6Money(e.net) + ' USDT (gross ' + v6Money(e.gross) + ', fees ' + v6Money(-(e.fees || 0)) + ', slippage ' +
            v6Money(-(e.slippage || 0)) + ', funding ' + v6Money(e.funding) + ') · ' + v6Cause(e.exit_kind) + (isNum(e.hold_s) ? ' · held ' + fmtDur(e.hold_s) : ''))];
      case 'system': {
        const ev = e.event || '';
        const text = ev === 'FORWARD_START' ? 'V6 FORWARD START ' + v6Utc(e.forward_start_ms) + ' · warm-up ' + (e.warmup_s ?? '?') + ' s — only decisions after this count'
          : ev === 'RESUMED' ? 'experiment RESUMED after a restart (session ' + (e.sessions ?? '?') + ') · books re-derived from the forward start, live from ' + v6Clock(e.live_from_ms)
            : ev === 'LIVE' ? 'all ' + (e.bots ?? '?') + ' bots LIVE' + (e.continuity_checks ? ' · continuity ' + e.continuity_ok + '/' + e.continuity_checks + ' books identical to before the restart' : '')
              : ev === 'SESSION_START' ? 'worker session started (' + (e.resumed ? 'resuming' : 'new experiment') + ') · warming up on recent history'
                : ev === 'WORKER_RESTARTING' ? 'worker exited (code ' + e.exit_code + '); restarting' : v6Cause(ev);
        return [pill('SYSTEM', ev === 'WORKER_RESTARTING' ? 'warn' : 'accent'), ' ', h('span', { text: text })];
      }
      case 'error': return [pill('ERROR', 'down'), ' ', who, ' ', sub(String(e.error || ''))];
      default: return [pill(v6Cause(e.type), 'muted'), ' ', who];
    }
  },
  renderFeed() {
    const box = $('#v6-feed');
    if (!box) return;
    const items = this.feed.filter((e) => this.feedFilter === 'all' || this.group(e.type) === this.feedFilter).slice(0, 150);
    const ul = h('ul', { class: 'feed v6-feed' });
    if (!items.length) ul.append(h('li', { class: 'act' }, h('span'), h('span'), h('span', { class: 'sub', text: 'nothing yet: hourly bots decide at each 1h close (+2 min), swing bots at each 4h close' })));
    for (const e of items) {
      ul.append(h('li', { class: 'act act-' + e.type + (e.type === 'decision' ? ' jev' : '') },
        h('span', { class: 'act-time', text: v6Clock(e.at), title: v6Utc(e.at) }),
        h('span', { class: 'act-icon', text: { candidate: '◆', decision: '◇', open: '▲', closed: '■', system: '●' }[e.type] || '·' }),
        h('span', { class: 'act-body' }, this.line(e))));
    }
    clear(box).append(ul);
    $$('#v6-feed-filters button').forEach((b) => b.classList.toggle('on', b.dataset.f === this.feedFilter));
  },

  /* ---- the page ---- */
  statusOf(d) {
    const st = d.status || {}, s = st.status || 'DISABLED';
    const age = st.klines_age_s;
    if (s === 'LIVE' && isNum(age) && age > 150) return { text: 'PAUSED · DATA STALE', cls: 'warn' };
    return ({ LIVE: { text: 'LIVE', cls: 'up' }, DEGRADED: { text: 'DEGRADED · signals pause', cls: 'warn' },
      WARMING_UP: { text: 'WARMING UP', cls: 'accent' }, STARTING: { text: 'STARTING', cls: 'accent' }, STARTING_BOTS: { text: 'STARTING BOTS', cls: 'accent' },
      RESTARTING: { text: 'RESTARTING', cls: 'warn' }, FROZEN_MISMATCH: { text: 'NOT FROZEN · NOT TRADING', cls: 'down' },
      ERROR: { text: 'ERROR', cls: 'down' }, DISABLED: { text: 'OFF', cls: 'ghost' } })[s] || { text: s, cls: 'warn' };
  },
  healthPills(d) {
    const st = d.status || {}, info = typeof Stream !== 'undefined' ? Stream.info() : { status: '?' };
    const age = st.klines_age_s, jev = (d.jev || {}), js = st.jev_state || jev.state;
    const b = st.barrier_last || (d.stream || {}).barrier_last;
    const pills = [
      pill('WS ● ' + (info.status || '?'), info.status === 'LIVE' ? 'up' : 'warn', 'this page\'s live push connection'),
      pill('BYBIT ● ' + (st.ws === true ? 'CONNECTED' : st.ws === false ? 'DISCONNECTED' : '—'), st.ws === true ? 'up' : st.ws === false ? 'down' : 'ghost',
        'Bybit public WebSocket (kline.1 + tickers)' + (isNum(st.rest_errors) ? ' · REST errors ' + st.rest_errors : '')),
      pill('DATA ● ' + (!isNum(age) ? '—' : age <= 90 ? 'LIVE ' + Math.round(age) + 's' : age <= 150 ? 'LAGGING ' + Math.round(age) + 's' : 'STALE — signals paused'),
        !isNum(age) ? 'ghost' : age <= 90 ? 'up' : age <= 150 ? 'warn' : 'down', 'age of the newest closed 1m bar'),
      pill('JEV ● ' + (js || '—'), js === 'READY' ? 'up' : js === 'DISABLED' ? 'ghost' : 'warn', 'Jev V6 API state' + (isNum(jev.error_rate) ? ' · error rate ' + v6Pct(jev.error_rate) : '')),
    ];
    if (b && b.H) pills.push(pill('HOUR ' + v6Clock(b.H).slice(0, 5) + ' ● ' + (b.complete ? 'INPUTS COMPLETE' : 'INCOMPLETE'), b.complete ? 'up' : 'warn',
      'positioning inputs for the last hourly decision' + (isNum(b.waited_s) ? ' · waited ' + b.waited_s + ' s' : '') + (b.missing && b.missing.length ? ' · missing ' + b.missing.join(', ') : '')));
    return pills;
  },
  hero(d) {
    const x = d.hero || {}, exp = d.experiment || {}, st = this.statusOf(d);
    const t0 = x.forward_start_ms;
    const pos = d.positions || [];
    const longs = pos.filter((p) => p.side === 'long').length;
    return h('div', { class: 'card v6-hero cc-hero', id: 'v6-hero-card' },
      h('div', { class: 'v6-hero-top' },
        h('div', { class: 'v6-title' }, h('span', { class: 'v6-name', text: 'V6 FORWARD ARENA' }),
          h('span', { class: 'v6-status ' + st.cls, id: 'v6-status' }, h('i', { class: 'dot' }), st.text)),
        h('span', { class: 'sub', text: 'LIVE Bybit market data · simulated fills · DRY_RUN — no real orders, no real money' })),
      h('div', { class: 'v6-age-row' },
        h('div', { class: 'v6-age' }, h('span', { class: 'lbl', text: 'forward age' }), h('b', { id: 'v6-age', text: v6Age(Date.now() - t0) }),
          h('span', { class: 'sub', text: 'since ' + v6Utc(t0) + ' · ' + (exp.experiment_id || '') + ' · session ' + (exp.sessions || 1) })),
        h('div', { class: 'v6-maturity' }, h('span', { class: 'lbl', text: 'evidence' }), pill(x.maturity || 'TOO EARLY', V6_MATURITY_KIND[x.maturity] || 'ghost'),
          h('span', { class: 'sub', text: 'serious evaluation needs ≥ 30 days and a meaningful trade count; there is no WINNER' })),
        h('div', { class: 'v6-next' }, h('span', { class: 'lbl', text: 'next 1h decision' }), h('b', { id: 'v6-next1h', text: '…' }),
          h('span', { class: 'sub', id: 'v6-next4h', text: '' }))),
      h('div', { class: 'tiles six v6-tiles' },
        tile('active bots', (x.active_bots ?? 0) + ' / ' + (x.bots ?? 0), x.active_bots && x.active_bots === x.bots ? 'up' : 'warn', (x.controls ?? 0) + ' controls · ' + (x.jev_bots ?? 0) + ' +JEV twins'),
        tile('positions open', String(x.positions_open ?? 0), '', longs + ' long · ' + (pos.length - longs) + ' short'),
        tile('trades / 24h', String(x.trades_24h ?? 0), '', (x.candidates_24h ?? 0) + ' candidates / 24h'),
        LivePrices.tile('total virtual equity · USDT', 'v6', 'equity', x.total_virtual_equity, 'of ' + (x.start_equity_total ?? DASH) + ' · 20 USDT per bot, isolated', 'usd'),
        LivePrices.tile('net PnL · USDT', 'v6', 'net', x.net_pnl, 'after booked costs · open positions estimated at the live mid'),
        LivePrices.tile('Jev edge · USDT', 'v6', 'jev', x.jev_edge, 'Σ(+JEV − CONTROL) over ' + ((d.pairs || []).length) + ' matched pairs')),
      h('div', { class: 'v6-health' }, this.healthPills(d)));
  },
  board(d) {
    const all = (d.leaderboard || []).filter((r) => {
      const f = this.boardFilter;
      return f === 'all' || (f === 'controls' && r.role === 'CONTROL') || (f === 'jev' && r.role === 'JEV') ||
        (f === '1h' && r.horizon === 'HOURLY') || (f === '4h' && r.horizon === 'SWING') || (f === 'open' && (r.open_positions || []).length);
    });
    const rows = this.boardAll ? all : all.slice(0, 12);
    const t = h('table', { class: 'tbl lb' });
    renderTable(t, [
      { h: '#', cell: (r) => String(r.rank), num: true, cls: 'rank' },
      { h: 'bot', cell: (r) => [h('span', { class: 'sid', text: r.key }), r.evaluating ? [' ', pill('DECIDING', 'accent')] : null, r.error ? [' ', pill('ERROR', 'down', r.error)] : null] },
      { h: 'equity', cell: (r) => LivePrices.node('v6', r.key, 'equity', r.equity_now, 'usd'), num: true },
      { h: 'return', cell: (r) => LivePrices.node('v6', r.key, 'return', r.net_now / (r.start_equity || 20), 'pct'), num: true },
      { h: 'net', cell: (r) => LivePrices.node('v6', r.key, 'net', r.net_now), num: true },
      { h: 'open', cell: (r) => (r.open_positions || []).map((p) => [v6Side(p.side), ' ', LivePrices.node('v6', r.key, 'upnl', p.upnl)]) },
      { h: 'trades', cell: (r) => String(r.trades ?? 0), num: true },
      { h: '24h', cell: (r) => String(r.trades_24h ?? 0), num: true },
      { h: 'exp R', cell: (r) => v6R(r.expectancy_r), num: true },
      { h: 'PF', cell: (r) => (isNum(r.profit_factor) ? (r.profit_factor >= 999 ? '∞' : r.profit_factor.toFixed(2)) : DASH), num: true },
      { h: 'max DD', cell: (r) => v6Pct(r.max_dd), num: true },
      { h: 'fees', cell: (r) => (isNum(r.fees) ? r.fees.toFixed(3) : DASH), num: true },
      { h: 'funding', cell: (r) => v6Money(r.funding_net), num: true },
      { h: 'risk state', cell: (r) => pill(v6Cause(r.risk_state || '?'), r.risk_state === 'OK' ? 'muted' : /HALT/.test(r.risk_state || '') ? 'down' : 'warn') },
      { h: 'evidence', cell: (r) => pill(r.maturity || 'TOO EARLY', V6_MATURITY_KIND[r.maturity] || 'ghost') },
    ], rows, { onRow: (r) => this.openBot(r.key), rowClass: (r) => (r.role === 'JEV' ? 'twin' : ''), empty: 'no bot matches this filter' });
    const chips = h('div', { class: 'chips', id: 'v6-board-filters' }, [['all', 'All'], ['controls', 'Controls'], ['jev', '+JEV'], ['1h', 'Hourly'], ['4h', 'Swing 4h'], ['open', 'Open positions']]
      .map(([f, label]) => h('button', { type: 'button', class: 'chip' + (f === this.boardFilter ? ' on' : ''), text: label, onclick: () => { this.boardFilter = f; this.renderLive() || this.render(); } })));
    const more = all.length > 12 ? h('button', { type: 'button', class: 'chip', text: this.boardAll ? 'show the top 12' : 'show all ' + all.length + ' bots',
      onclick: () => { this.boardAll = !this.boardAll; this.renderLive() || this.render(); } }) : null;
    return h('div', { class: 'card', id: 'v6-board-card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Leaderboard' }),
      h('span', { class: 'sub', text: 'ranked by net PnL (paper, after every cost, open positions marked at the live mid) · ranking is not qualification · click a bot' })),
    chips, h('div', { class: 'tablewrap' }, t), more ? h('div', { class: 'v6-more' }, more) : null);
  },
  positionsCard(d) {
    const v7 = ((this.challenger || {}).positions || []).map((p) => Object.assign({ program: 'V7' }, p));
    const all = (d.positions || []).map((p) => Object.assign({ program: 'V6' }, p)).concat(v7);
    const list = h('div', { class: 'pos-list' });
    if (!all.length) list.append(h('div', { class: 'pos-empty', text: 'no open paper positions — V6 decides at each 1h / 4h close, V7 every 15 min' }));
    for (const p of all) {
      const kind = p.program === 'V7' ? 'v7' : 'v6';
      list.append(h('div', { class: 'pos-item ' + (p.side === 'short' ? 'short' : 'long'), title: 'open the bot',
        onclick: () => (p.program === 'V6' ? this.openBot(p.bot_key) : window.open(this.base + '/v7', '_blank', 'noopener')) },
      h('span', { class: 'who' }, v6Side(p.side), ' ', p.bot_key, ' ', pill(p.program, p.program === 'V7' ? 'accent' : 'muted')),
      h('span', { class: 'pnl' }, LivePrices.node(kind, p.bot_key, 'upnl', p.upnl)),
      h('span', { class: 'meta' }, (p.coin || '') + ' · entry ' + v6Price(p.entry) + ' → ', LivePrices.node(kind, p.bot_key, 'mark', p.mark, 'price'),
        ' · risk ' + v6Pct(p.risk_pct, 2) + (isNum(p.risk_usd) ? ' (' + p.risk_usd.toFixed(2) + ')' : '') +
        ' · stop ' + v6Price(p.stop) + ' · target ' + v6Price(p.target) + (isNum(p.hold_s) ? ' · held ' + fmtDur(p.hold_s) : ''))));
    }
    return h('div', { class: 'card', id: 'v6-pos-card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Open positions · ' + all.length }),
      h('span', { class: 'sub', text: 'simulated fills · live mark' })), list);
  },
  /** Equity since each forward start as % return on the group's starting capital, so V6 (560 + 560 USDT) and V7
      (160 USDT) share one axis. Realized steps at each close, net of every cost; the last point is live (booked costs
      plus open positions at the mid). */
  equitySeries() {
    const out = [];
    const add = (name, color, curve, rows) => {
      if (!curve || !curve.length || !(curve[0][1] > 0)) return;
      const start = curve[0][1];
      const pts = curve.length > 1 ? curve.slice(0, -1) : curve.slice();
      const live = rows.length ? rows.reduce((a, r) => a + (isNum(r.equity_now) ? r.equity_now : 0), 0) : curve[curve.length - 1][1];
      pts.push([Date.now(), live]);
      out.push({ name, color, start, now: live, pts: pts.map(([t, v]) => [t, v / start - 1]) });
    };
    const d = this.data || {}, c = this.challenger || {};
    const lb = d.leaderboard || [], cl = c.leaderboard || [];
    add('V6 controls', 'var(--up)', (d.equity_curve || {}).controls, lb.filter((r) => r.role === 'CONTROL'));
    add('V6 +JEV', 'var(--purple)', (d.equity_curve || {}).jev, lb.filter((r) => r.role === 'JEV'));
    add('V7 challenger', 'var(--accent)', (c.equity_curve || {}).controls, cl);
    return out;
  },
  equityCard() {
    const series = this.equitySeries();
    const W = 1000, H = 260, NS = 'http://www.w3.org/2000/svg';
    const svg = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
    const pctText = (v) => (v >= 0 ? '+' : '') + (100 * v).toFixed(2) + '%';
    const plot = h('div', { class: 'eq-plot' });
    const legend = h('div', { class: 'eq-legend' }, series.map((s) => {
      const last = s.pts[s.pts.length - 1][1];
      return h('span', null, h('i', { style: 'background:' + s.color }), s.name + ' ',
        h('b', { class: signClass(s.now - s.start), text: v6Money(s.now - s.start) + ' USDT' }),
        h('span', { class: signClass(last), text: pctText(last) }));
    }));
    if (!series.length) plot.append(h('div', { class: 'eq-empty', text: 'the equity curve starts with the first forward session' }));
    else {
      const t0 = Math.min(...series.map((s) => s.pts[0][0])), t1 = Math.max(Date.now(), t0 + 60_000);
      const vals = series.flatMap((s) => s.pts.map((p) => p[1])).concat([0]);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      const pad = Math.max((hi - lo) * 0.15, 0.002);
      lo -= pad; hi += pad;
      const X = (t) => ((t - t0) / (t1 - t0)) * W, Y = (v) => (1 - (v - lo) / (hi - lo)) * H;
      const root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', role: 'img',
        'aria-label': 'equity since the forward start, percent return per group' });
      const defs = svg('defs', {}), grad = svg('linearGradient', { id: 'eq-fill', x1: 0, y1: 0, x2: 0, y2: 1 });
      grad.append(svg('stop', { offset: '0%', 'stop-color': '#1ff29a', 'stop-opacity': '.28' }),
        svg('stop', { offset: '100%', 'stop-color': '#1ff29a', 'stop-opacity': '0' }));
      defs.append(grad);
      root.append(defs);
      for (let i = 0; i <= 4; i++) {
        const v = lo + ((hi - lo) * i) / 4;
        root.append(svg('line', { class: 'eq-grid', x1: 0, x2: W, y1: Y(v), y2: Y(v) }));
        plot.append(h('span', { class: 'eq-y', style: 'top:' + ((Y(v) / H) * 100).toFixed(2) + '%', text: pctText(v) }));
      }
      root.append(svg('line', { class: 'eq-zero', x1: 0, x2: W, y1: Y(0), y2: Y(0) }));
      series.forEach((s, i) => {
        let path = 'M' + X(s.pts[0][0]).toFixed(1) + ' ' + Y(s.pts[0][1]).toFixed(1);
        for (const [t, v] of s.pts.slice(1)) path += ' H' + X(t).toFixed(1) + ' V' + Y(v).toFixed(1);
        if (i === 0) root.append(svg('path', { d: path + ' V' + H + ' H' + X(s.pts[0][0]).toFixed(1) + ' Z', fill: 'url(#eq-fill)', stroke: 'none' }));
        root.append(svg('path', { class: 'eq-line', d: path, style: 'stroke:' + s.color }));
      });
      plot.append(root);
      const tl = (t) => new Date(t).toISOString().slice(5, 16).replace('T', ' ');
      plot.append(h('span', { class: 'eq-x first', style: 'left:0', text: tl(t0) + ' UTC' }),
        h('span', { class: 'eq-x', style: 'left:50%', text: tl((t0 + t1) / 2) }),
        h('span', { class: 'eq-x last', style: 'left:100%', text: 'now' }));
    }
    return h('div', { class: 'card eq-card', id: 'v6-equity-card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Equity since forward start' }),
        h('span', { class: 'sub', text: "% return on each group's starting capital · steps at each close, net of fees, spread and funding · last point live" })),
      legend, plot);
  },
  feedCard() {
    const chips = h('div', { class: 'chips', id: 'v6-feed-filters' }, [['all', 'All'], ['candidates', 'Candidates'], ['jev', 'Jev'], ['trades', 'Trades'], ['system', 'System']]
      .map(([f, label]) => h('button', { type: 'button', class: 'chip', 'data-f': f, text: label, onclick: () => { this.feedFilter = f; this.renderFeed(); } })));
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Activity' }),
      h('span', { class: 'sub', text: 'candidate → Jev → order → close, as it happens' })), chips, h('div', { id: 'v6-feed' }));
  },
  pairsCard(d) {
    const t = h('table', { class: 'tbl' });
    const pairs = (d.pairs || []).slice().sort((a, b) => (b.delta || 0) - (a.delta || 0) || (b.decisions || 0) - (a.decisions || 0)
      || String(a.control_key).localeCompare(String(b.control_key)));
    renderTable(t, [
      { h: 'pair', cell: (p) => h('span', { class: 'sid', text: p.control_key || p.pair_id }) },
      { h: 'CONTROL net', cell: (p) => h('span', { class: signClass(p.control_net), text: v6Money(p.control_net) }), num: true },
      { h: '+JEV net', cell: (p) => h('span', { class: signClass(p.jev_net), text: v6Money(p.jev_net) }), num: true },
      { h: 'Δ (Jev − control)', cell: (p) => h('b', { class: signClass(p.delta), text: v6Money(p.delta) }), num: true },
      { h: 'trades C / J', cell: (p) => (p.control_trades ?? 0) + ' / ' + (p.jev_trades ?? 0), num: true },
      { h: 'Jev decisions', cell: (p) => String(p.decisions ?? 0), num: true },
      { h: 'SKIP / TAKE / ATTACK', cell: (p) => ['SKIP', 'TAKE', 'ATTACK'].map((k) => (p.actions || {})[k] || 0).join(' / '), num: true },
      { h: 'selection α', cell: (p) => v6R(p.selection_alpha_r), num: true },
      { h: 'ATTACK +', cell: (p) => v6Money(p.attack_increment_usdt), num: true },
      { h: 'p50 latency', cell: (p) => (isNum(p.latency_p50_ms) ? Math.round(p.latency_p50_ms) + ' ms' : DASH), num: true },
      { h: 'evidence', cell: (p) => pill(p.maturity || 'TOO EARLY', V6_MATURITY_KIND[p.maturity] || 'ghost') },
    ], this.pairsAll ? pairs : pairs.slice(0, 12), { onRow: (p) => this.openBot(p.jev_key), empty: 'no +JEV twins in this experiment' });
    const more = pairs.length > 12 ? h('div', { class: 'v6-more' }, h('button', { type: 'button', class: 'chip',
      text: this.pairsAll ? 'show the top 12' : 'show all ' + pairs.length + ' pairs',
      onclick: () => { this.pairsAll = !this.pairsAll; this.renderLive() || this.render(); } })) : null;
    return h('div', { class: 'card', id: 'v6-pairs-card' }, h('div', { class: 'card-head' }, h('h2', { text: 'CONTROL vs +JEV (matched pairs)' }),
      h('span', { class: 'sub', text: 'identical market data, candidates, starting equity, execution and risk rules; Jev can only SKIP, TAKE or ATTACK a CONTROL candidate · sorted by Δ, most active first' })),
    h('div', { class: 'tablewrap' }, t), more);
  },
  costsCard(d) {
    const tot = d.totals || {};
    const t = h('table', { class: 'tbl' });
    const rows = [['CONTROL', tot.controls || {}], ['+JEV', tot.jev || {}]];
    renderTable(t, [
      { h: 'closed trades', cell: (r) => r[0] }, { h: 'n', cell: (r) => String(r[1].trades ?? 0), num: true },
      { h: 'gross', cell: (r) => v6Money(r[1].gross), num: true }, { h: 'fees', cell: (r) => v6Money(-(r[1].fees || 0)), num: true },
      { h: 'slippage', cell: (r) => v6Money(-(r[1].slippage || 0)), num: true }, { h: 'funding', cell: (r) => v6Money(r[1].funding), num: true },
      { h: 'net', cell: (r) => h('b', { class: signClass(r[1].net), text: v6Money(r[1].net) }), num: true },
      { h: 'exp R', cell: (r) => v6R(r[1].expectancy_r), num: true }, { h: 'win', cell: (r) => v6Pct(r[1].win_rate, 0), num: true },
    ], rows);
    return h('div', { class: 'card c6' }, h('div', { class: 'card-head' }, h('h2', { text: 'Gross → net' }),
      h('span', { class: 'sub', text: 'Bybit taker fees on every fill, the observed live spread plus modelled slippage, funding at each real settlement' })),
    h('div', { class: 'tablewrap' }, t),
    h('p', { class: 'sub', text: 'All fills, including open entries: fees ' + fmtNum(tot.fees, 4) +
      ' · slippage ' + fmtNum(tot.slippage, 4) + ' USDT. The table above covers closed trades only.' }),
    h('p', { class: 'sub', text: 'funding on all books so far: paid ' + (isNum(tot.funding_paid) ? tot.funding_paid.toFixed(4) : DASH) + ' · received ' +
      (isNum(tot.funding_received) ? tot.funding_received.toFixed(4) : DASH) + ' USDT · maker fees 0 (every V6 order is a market order)' }));
  },
  jevCard(d) {
    const j = d.jev || {}, lat = j.latency_ms || {}, mv = j.move_during_latency_bps || {};
    return h('div', { class: 'card c6' }, h('div', { class: 'card-head' }, h('h2', { text: 'Jev V6 live' }),
      h('span', { class: 'sub', text: 'SKIP / TAKE / ATTACK on each CONTROL candidate; no DEFENSIVE; Jev never creates a trade' })),
    kvList({ state: j.state || DASH, decisions: j.decisions ?? 0,
      'SKIP / TAKE / ATTACK': ['SKIP', 'TAKE', 'ATTACK'].map((k) => (j.actions || {})[k] || 0).join(' / '),
      accepted: j.accepted ?? 0, 'latency p50 / p95 / p99': [lat.p50, lat.p95, lat.p99].map((v) => (isNum(v) ? Math.round(v) + ' ms' : DASH)).join(' / '),
      'market move during latency': isNum(mv.mean) ? mv.mean.toFixed(2) + ' bps (p95 ' + (isNum(mv.p95) ? mv.p95.toFixed(2) : DASH) + ')' : DASH,
      'selection alpha': isNum(j.selection_alpha_r) ? v6R(j.selection_alpha_r) + ' per candidate (' + (j.resolved ?? 0) + ' resolved)' : 'not enough resolved candidates',
      'ATTACK trades / increment': (j.attack_trades ?? 0) + ' / ' + v6Money(j.attack_increment_usdt) + ' USDT',
      errors: (j.errors ?? 0) + (isNum(j.error_rate) ? ' (' + v6Pct(j.error_rate) + ')' : ''), 'API cost': isNum(j.cost_usd) ? '$' + j.cost_usd.toFixed(4) : DASH }));
  },
  familiesCard(d) {
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'family', cell: (f) => f.family }, { h: 'bots', cell: (f) => String(f.bots), num: true },
      { h: 'trades', cell: (f) => String(f.trades), num: true }, { h: 'net', cell: (f) => h('span', { class: signClass(f.net), text: v6Money(f.net) }), num: true },
      { h: 'evidence', cell: (f) => pill(f.maturity || 'TOO EARLY', V6_MATURITY_KIND[f.maturity] || 'ghost') }], d.families || []);
    return h('div', { class: 'card c6' }, h('div', { class: 'card-head' }, h('h2', { text: 'Families (CONTROL books)' }),
      h('span', { class: 'sub', text: 'V6.1 positioning pullback · V6.2 momentum + OI · V6.3 funding crowding · V6.4 OI breakout · V6.5 deleveraging · V6.6 market-aligned alt trend' })),
    h('div', { class: 'tablewrap' }, t));
  },
  riskCard(d) {
    const r = d.risk_distribution || {};
    const kv = {};
    for (const [k, v] of Object.entries(r.trades_by_tier || {})) kv['trades · ' + v6Cause(k)] = v;
    for (const [k, v] of Object.entries(r.min_notional_skips || {})) kv['skipped · ' + v6Cause(k)] = v;
    for (const [k, v] of Object.entries(r.risk_states || {})) kv['bots · ' + v6Cause(k)] = v;
    return h('div', { class: 'card c6' }, h('div', { class: 'card-head' }, h('h2', { text: 'Risk distribution' }),
      h('span', { class: 'sub', text: 'base 1% · up to 1.5% for a strong setup the exchange minimum needs · 1.5–2% only for very high conviction or Jev ATTACK · never above 2%: SKIP' })),
    kvList(kv));
  },
  rulesCard(d) {
    const r = d.rules || {}, exp = d.experiment || {};
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Protocol' }), h('span', { class: 'sub', text: 'docs/V6_PROTOCOL.md' })),
      h('p', { class: 'sub', text: (r.frozen || '') + '. ' + (r.paper || '') + '. Maturity: ' + (r.maturity || '') + '.' }),
      h('p', { class: 'sub', text: 'Only decisions after the forward start count and nothing is ever back-filled. A redeploy resumes the same experiment ' +
        '(same frozen code, parameters, universe, risk, execution, fees, venue and Jev policy): every book is re-derived from the forward start on the ' +
        'stored live inputs with each recorded decision replayed, so equity, open positions, stops and targets carry on. Candidates that fell into a ' +
        'downtime are MISSED, never traded late. Stale data pauses decisions for CONTROL and +JEV alike.' }),
      h('p', { class: 'sub', text: 'experiment ' + (exp.experiment_id || DASH) + ' · manifest ' + (exp.manifest || DASH) + ' · coins ' + ((exp.coins || []).join(', ') || DASH) }));
  },
  async openBot(key) {
    this.botKey = key;
    try { this.bot = await getJSON(this.base + '/v6/bot/' + encodeURIComponent(key)); } catch (e) { this.bot = { ok: false, error: e.message }; }
    this.render();
    const el = $('#v6-bot');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },
  botCard() {
    const b = this.bot || {};
    const close = h('button', { type: 'button', class: 'chip', text: 'close', onclick: () => { this.botKey = ''; this.bot = null; this.render(); } });
    if (!b.ok) return h('div', { class: 'card', id: 'v6-bot' }, h('div', { class: 'card-head' }, h('h2', { text: this.botKey }), close), h('p', { class: 'err', text: b.error || 'loading…' }));
    const bot = b.bot || {};
    const tt = h('table', { class: 'tbl' });
    renderTable(tt, [{ h: 'entry', cell: (t) => v6Utc(t.entry_ts) }, { h: 'side', cell: (t) => v6Side(t.side) },
      { h: 'entry → exit', cell: (t) => v6Price(t.entry_price) + ' → ' + v6Price(t.exit_price) },
      { h: 'gross', cell: (t) => v6Money(t.gross), num: true }, { h: 'fees', cell: (t) => v6Money(-(t.fees || 0)), num: true },
      { h: 'slippage', cell: (t) => v6Money(-(t.slippage || 0)), num: true }, { h: 'funding', cell: (t) => v6Money(t.funding), num: true },
      { h: 'net', cell: (t) => h('b', { class: signClass(t.net), text: v6Money(t.net) }), num: true }, { h: 'R', cell: (t) => v6R(t.r), num: true },
      { h: 'exit', cell: (t) => v6Cause(t.exit_kind) }, { h: 'risk', cell: (t) => v6Pct(t.risk_pct, 2) + ' ' + v6Cause(t.tier || '') },
      { h: '', cell: (t) => (t.counterfactual ? pill('SHADOW', 'ghost', 'what the skipped candidate would have done') : null) }], b.trades || [], { empty: 'no closed trades yet' });
    const dt = h('table', { class: 'tbl' });
    renderTable(dt, [{ h: 'signal', cell: (x) => v6Utc(x.signal_ts) }, { h: 'side', cell: (x) => v6Side(x.side) },
      { h: 'decision', cell: (x) => pill(x.final_level || '?', x.final_level === 'SKIP' ? 'down' : x.final_level === 'ATTACK' ? 'accent' : 'up') },
      { h: 'reason', cell: (x) => v6Cause(x.reason) }, { h: 'support', cell: (x) => (isNum(x.p_support) ? Math.round(x.p_support * 100) + '%' : DASH), num: true },
      { h: 'latency', cell: (x) => (isNum(x.latency_ms) ? Math.round(x.latency_ms) + ' ms' : DASH), num: true },
      { h: 'tier', cell: (x) => v6Cause(x.tier) }, { h: 'outcome', cell: (x) => (x.outcome_kind ? v6Cause(x.outcome_kind) + ' ' + v6R(x.outcome_r) : 'open / pending') }],
    b.decisions || [], { empty: 'no gate decisions yet' });
    return h('div', { class: 'card', id: 'v6-bot' },
      h('div', { class: 'card-head' }, h('h2', { text: bot.key + (bot.family ? ' · ' + v6Cause(bot.family) : '') }), close),
      h('div', { class: 'tiles six' },
        tile('equity', isNum(bot.equity_live ?? bot.equity) ? (bot.equity_live ?? bot.equity).toFixed(4) + ' USDT' : DASH, signClass(bot.net), 'start ' + (bot.start_equity ?? 20)),
        tile('trades', String(bot.trades ?? 0), '', (bot.trades_24h ?? 0) + ' in 24h · ' + (bot.candidates ?? 0) + ' candidates'),
        tile('expectancy', v6R(bot.expectancy_r), signClass(bot.expectancy_r), 'PF ' + (isNum(bot.profit_factor) ? bot.profit_factor.toFixed(2) : DASH) + ' · win ' + v6Pct(bot.win_rate, 0)),
        tile('drawdown', v6Pct(bot.max_dd), '', 'now ' + v6Pct(bot.dd_now)),
        tile('costs', isNum(bot.fees) ? (bot.fees + (bot.slippage || 0)).toFixed(4) + ' USDT' : DASH, '', 'fees ' + fmtNum(bot.fees, 4) + ' · slippage ' + fmtNum(bot.slippage, 4) + ' · funding ' + fmtNum(bot.funding_net, 4)),
        tile('risk state', v6Cause(bot.risk_state || '?'), '', (bot.min_notional_skips ?? 0) + ' min-notional skips')),
      bot.continuity && bot.continuity.check ? h('p', { class: 'sub', text: 'restart continuity: ' + (bot.continuity.check.ok ? 'the re-derived book equals the book before the restart' : 'DIFFERS — ' + JSON.stringify(bot.continuity.check)) }) : null,
      h('h3', { text: 'closed trades' }), h('div', { class: 'tablewrap' }, tt), h('h3', { text: 'gate / Jev decisions' }), h('div', { class: 'tablewrap' }, dt));
  },
  challengerCard() {
    const d = this.challenger || {}, x = d.hero || {}, rows = d.leaderboard || [];
    const table = h('table', { class: 'tbl' });
    renderTable(table, [
      { h: 'bot', cell: (r) => r.key },
      { h: 'net USDT', cell: (r) => LivePrices.node('v7', r.key, 'net', r.net_now), num: true },
      { h: 'live mid', cell: (r) => LivePrices.node('quote', r.symbol, 'mid', null, 'price'), num: true },
      { h: 'unrealized', cell: (r) => LivePrices.node('v7', r.key, 'upnl', 0), num: true },
      { h: 'closed', cell: (r) => String(r.trades || 0), num: true },
      { h: 'open', cell: (r) => String((r.open_positions || []).length), num: true },
      { h: 'max DD', cell: (r) => v6Pct(r.max_dd, 2), num: true },
      { h: 'risk', cell: (r) => r.risk_state || DASH },
    ], rows);
    return h('div', { class: 'card c12', id: 'v7-challenger' },
      h('div', { class: 'card-head' }, h('h2', { text: 'V7 · Active paper challenger' }),
        pill((d.status || {}).status || 'STARTING', x.live ? 'up' : 'muted')),
      h('p', { class: 'sub', text: '15-minute trend pullbacks and volume breakouts · 1% base risk, 2% maximum · 1.8R target · 6-hour maximum hold. Separate paper books; profitability unproven.' }),
      h('p', null, 'Live net ', LivePrices.node('v7', '', 'net', x.net_pnl), ' USDT · equity ',
        LivePrices.node('v7', '', 'equity', x.total_virtual_equity, 'usd'), ' USDT'),
      h('p', { text: x.bots ? x.active_bots + '/' + x.bots + ' bots live · ' + x.positions_open + ' open · ' +
        x.trades_24h + ' closed / 24h · ' + x.candidates_24h + ' candidates / 24h · starting capital ' +
        x.start_equity_total + ' USDT · age ' + x.forward_age : (d.note || 'Waiting for the challenger worker') }),
      h('div', { class: 'tablewrap' }, table),
      h('a', { href: this.base + '/v7', target: '_blank', rel: 'noopener', text: 'Inspect challenger positions, costs and activity' }));
  },
  render() {
    const grid = $('#home-grid'), d = this.data;
    if (!grid || !d) return;
    if (!d.experiment) {
      const st = this.statusOf(d);
      clear(grid).append(h('div', { class: 'card v6-hero' },
        h('div', { class: 'v6-hero-top' }, h('div', { class: 'v6-title' }, h('span', { class: 'v6-name', text: 'V6 FORWARD ARENA' }),
          h('span', { class: 'v6-status ' + st.cls }, h('i', { class: 'dot' }), st.text))),
        h('p', { class: 'sub', text: d.note || '' }),
        (d.status || {}).detail ? h('p', { class: 'err', text: d.status.detail }) : null,
        h('div', { class: 'v6-health' }, this.healthPills(d))));
      return;
    }
    const y = window.scrollY;
    clear(grid).append(this.hero(d),
      h('div', { class: 'cc-left' }, this.equityCard(), this.board(d)),
      h('div', { class: 'cc-right' }, LivePrices.strip(), this.positionsCard(d), this.feedCard()),
      this.botKey ? this.botCard() : null, this.challengerCard(), this.pairsCard(d),
      this.costsCard(d), this.jevCard(d), this.familiesCard(d), this.riskCard(d), this.rulesCard(d));
    this.renderFeed();
    this.tick();
    LivePrices.schedule();
    if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
  },
  visualHero() {
    return h('section', { class: 'market-hero', 'aria-label': 'PaperLab paper-trading research dashboard' },
      h('div', { class: 'market-hero-copy' },
        h('h1', { text: 'Paper trading, measured in real time.' }),
        h('p', { text: 'Live market context. Forward results. Clear evidence.' })));
  },
  tick() {
    const d = this.data;
    if (!d || !d.hero || !this.visible()) return;
    const now = Date.now(), t0 = d.hero.forward_start_ms;
    const age = $('#v6-age');
    if (age && t0) setText(age, v6Age(now - t0));
    const n1 = (Math.floor(now / V6_HOUR) + 1) * V6_HOUR, n4 = (Math.floor(now / (4 * V6_HOUR)) + 1) * 4 * V6_HOUR;
    const a = $('#v6-next1h'), b = $('#v6-next4h');
    if (a) setText(a, v6Countdown(n1 - now));
    if (b) setText(b, 'candle closes ' + v6Clock(n1).slice(0, 5) + ' UTC; orders fill ~2 min later · next 4h close in ' + v6Countdown(n4 - now));
  },
};

/* The MARKET tab: live Bybit prices the V6 bots trade on. */
const V6Market = {
  async render() {
    const box = $('#market-live');
    if (!box) return;
    let d = V6Home.data;
    if (!d) { try { d = await getJSON(V6Home.base + '/v6'); V6Home.data = d; } catch (e) { d = {}; } }
    const prices = d.prices || {};
    const rows = Object.entries(prices).map(([sym, p]) => Object.assign({ symbol: sym }, p));
    clear(box).append(h('div', { class: 'card-head' }, h('h2', { text: 'live market — Bybit USDT perpetuals' }),
      h('span', { class: 'sub', text: 'what the V6 bots trade on: best bid/ask mid, observed half spread, mark / index, funding, open interest' })));
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'coin', cell: (r) => h('span', { class: 'sid', text: r.symbol.replace('USDT', '') }) },
      { h: 'mid', cell: (r) => LivePrices.node('quote', r.symbol, 'mid', r.mid, 'price'), num: true },
      { h: 'half spread', cell: (r) => LivePrices.node('quote', r.symbol, 'half_spread_bps', r.half_spread_bps, 'bps'), num: true },
      { h: 'mark', cell: (r) => LivePrices.node('quote', r.symbol, 'mark', r.mark, 'price'), num: true },
      { h: 'index', cell: (r) => LivePrices.node('quote', r.symbol, 'index', r.index, 'price'), num: true },
      { h: 'funding', cell: (r) => (isNum(r.funding_rate) ? (r.funding_rate * 100).toFixed(4) + '%' : DASH), num: true },
      { h: 'next funding', cell: (r) => (r.next_funding_ts ? 'in ' + fmtDur((r.next_funding_ts - Date.now()) / 1000) : DASH) },
      { h: 'open interest', cell: (r) => (isNum(r.oi) ? NF.qty.format(Math.round(r.oi)) : DASH), num: true },
      { h: 'last 1m bar', cell: (r) => (r.last_bar_open ? v6Clock(r.last_bar_open) : DASH) },
    ], rows, { empty: 'the V6 forward worker is not running on this server' });
    box.append(h('div', { class: 'tablewrap' }, t));
    LivePrices.schedule();
  },
};

function wireV6() {
  if (typeof Stream === 'undefined') return;
  LivePrices.wire();
  Stream.on('v6_state', (d) => { V6Home.onState(d); if (V6Home.visible() === false && $('[data-panel="market"]') && !$('[data-panel="market"]').hidden) V6Market.render(); });
  Stream.on('v6', (d) => V6Home.onEvent(d));
  Stream.on('v7_state', (d) => V6Home.onV7State(d));
  Stream.on('v7', (d) => { if (['open', 'closed', 'system'].includes(d.type)) V6Home.reloadSoon(1000); });
  Stream.onStatus(() => { if (V6Home.visible()) V6Home.draw(); });
}
