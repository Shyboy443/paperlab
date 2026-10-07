/* PaperLab - the simplified front door, shared by the public inspection page and the private dashboard.

     HOME            the V6 FORWARD ARENA (v6.js): live Bybit data, paper fills, forward age, leaderboard, positions
     RESEARCH        answers in five seconds for the research history: how many bots, who is best, is anyone
                     profitable, is Jev helping, is anyone advancing, what is failing -- from ONE cached snapshot
                     (/public/inspection.json)
     TRADERS         every ranked bot of every program in one table; a trader opens with the tabs
                     Overview / Trades / Analyzer / Jev / Validation (/public/inspection/bot/{id}.json)
     V5 ARENA        the V5 hourly / daily futures arena (DEVELOPMENT and PSEUDO-HOLDOUT runs), frozen research
                     history under VALIDATION like V4 and older programs
     SYSTEM          execution and safety, stream / Jev / data health, research jobs, database, environment,
                     and the DEVELOPER details (fingerprints, run ids, schema / API version) folded away
     SAFETY STRIP    PAPER/LIVE, kill switch, engine, WS, DATA, JEV -- always visible in the header

   Every request is a plain GET to a public, read-only route: no credential, no cookie, nothing that mutates. */
'use strict';

const INSPECT = '/public/inspection.json';
const PUB = '/api/public/competition';

async function getJSON(url) {
  const res = await fetch(url, { method: 'GET', cache: 'no-store' });
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok || (data && data.ok === false)) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}
const fR = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(3) + 'R' : DASH);
const fU = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(2) : DASH);
const fP = (v) => (isNum(v) ? (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%' : DASH);
const fPF = (v) => (isNum(v) ? (v >= 999 ? '∞' : v.toFixed(2)) : DASH);
const fDD = (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : DASH);
const cause = (s) => (s ? String(s).replace(/_/g, ' ') : DASH);
const botLabel = (r) => [r.strategy, r.coin, r.timeframe].filter(Boolean).join('-') || r.bot_id;
const stageLabel = (r) => [r.program, r.stage && r.stage.replace('DEVELOPMENT', 'DEV').replace('MULTI_YEAR', 'MULTI-YEAR')].filter(Boolean).join(' ');

/* ---- the one snapshot every view reads (server-cached 30 s; refreshed on a timer only while visible) ---- */
const Snapshot = {
  data: null, at: 0, pending: null, listeners: [],
  async get(force) {
    if (!force && this.data && Date.now() - this.at < 25_000) return this.data;
    if (this.pending) return this.pending;
    this.pending = getJSON(INSPECT).then((d) => { this.data = d; this.at = Date.now(); this.listeners.forEach((f) => f(d)); return d; })
      .finally(() => { this.pending = null; });
    return this.pending;
  },
  on(fn) { this.listeners.push(fn); },
};

/* ---- SAFETY STRIP: header pills, fed by the snapshot and by every public 'health' push ---- */
const Safety = {
  state: { exec: null, kill: null, engine: null, data: null, jev: null, ws: null },
  /** `keys`: which pills to draw (the private header already shows execution, kill switch and engine). */
  init(keys) {
    const host = $('#safety-strip');
    if (!host) return;
    this.pills = {};
    for (const [k, title] of [['exec', 'paper or live execution'], ['kill', 'kill switch'], ['engine', 'engine state / risk halt'],
      ['ws', 'server push connection'], ['data', 'live market data (the V6 forward feed: Bybit WebSocket klines + tickers)'], ['jev', 'Jev API']]) {
      if (keys && !keys.includes(k)) continue;
      this.pills[k] = h('span', { class: 'pill ghost', title, text: k.toUpperCase() + ' ● …' });
      host.append(this.pills[k]);
    }
    Snapshot.on((d) => this.fromSnapshot(d));
    Stream.on('health', (d) => this.fromHealth(d));
    Stream.onStatus((info) => this.set('ws', (info.transport === 'sse' ? 'SSE' : 'WS') + ' ● ' + info.status,
      info.status === 'LIVE' ? 'up' : info.status === 'OFF' ? 'down' : 'warn'));
  },
  set(k, text, kind) {
    const p = this.pills && this.pills[k];
    if (!p) return;
    setText(p, text);
    p.className = 'pill ' + (kind || 'ghost');
  },
  fromSnapshot(d) {
    const s = (d && d.system) || {};
    const exec = String(s.execution || '').startsWith('LIVE') ? 'LIVE' : String(s.execution || '').startsWith('PAPER') ? 'PAPER' : '?';
    this.set('exec', exec + ' ● ' + (exec === 'LIVE' ? 'REAL ORDERS' : 'SIMULATED'), exec === 'LIVE' ? 'down' : 'up');
    this.set('kill', 'KILL ● ' + (s.kill_switch || '?'), s.kill_switch === 'ENGAGED' ? 'down' : 'up');
    this.set('engine', 'ENGINE ● ' + (s.paused_reason ? 'HALT' : (s.engine_state || '?')), s.paused_reason ? 'down' : s.engine_state === 'running' ? 'up' : 'warn');
    this.set('jev', 'JEV ● ' + (s.jev_status || '?'), s.jev_status === 'OK' ? 'up' : s.jev_status ? 'warn' : 'ghost');
  },
  fromHealth(d) {
    const sf = d && d.safety;
    if (sf && sf.execution) {
      this.set('exec', sf.execution + ' ● ' + (sf.execution === 'LIVE' ? 'REAL ORDERS' : 'SIMULATED'), sf.execution === 'LIVE' ? 'down' : 'up');
      this.set('kill', 'KILL ● ' + sf.kill_switch, sf.kill_switch === 'ENGAGED' ? 'down' : 'up');
      this.set('engine', 'ENGINE ● ' + (sf.risk_halt ? 'HALT' : sf.engine_state), sf.risk_halt ? 'down' : sf.engine_state === 'running' ? 'up' : 'warn');
    }
    const v6 = (d && d.v6) || {};
    if (v6.enabled) {                                   // V6: DATA is the live Bybit feed the forward bots trade on
      const age = v6.klines_age_s;
      const ok = v6.ws === true && isNum(age) && age <= 150;
      this.set('data', 'DATA ● ' + (!isNum(age) ? (v6.status || '…') : ok ? 'LIVE' : v6.ws === false ? 'DOWN' : 'STALE'),
        !isNum(age) ? 'warn' : ok ? 'up' : 'down');
      if (v6.jev_state) this.set('jev', 'JEV ● ' + v6.jev_state, v6.jev_state === 'READY' ? 'up' : v6.jev_state === 'DISABLED' ? 'ghost' : 'warn');
      return;
    }
    const feed = ((d && d.shadow) || {}).feed || {};
    const mk = sf && sf.market_feed;
    if (feed.klines != null || mk) {
      const live = feed.klines === true || mk === 'LIVE';
      const age = isNum(feed.klines_age_s) ? feed.klines_age_s : sf && sf.market_age_s;
      this.set('data', 'DATA ● ' + (live ? (isNum(age) && age > 180 ? 'STALE' : 'LIVE') : 'DOWN'),
        live ? (isNum(age) && age > 180 ? 'warn' : 'up') : 'down');
    }
    if (d && d.jev) this.set('jev', 'JEV ● ' + (d.jev.status || '?'), d.jev.status === 'OK' ? 'up' : 'warn');
  },
};

/* ---- CURRENT ARENA ---- */
const Home = {
  timer: null,
  async load() {
    const grid = $('#research-grid');
    if (!grid) return;
    try { this.render(grid, await Snapshot.get()); } catch (e) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: 'snapshot unavailable: ' + e.message }))); }
    clearInterval(this.timer);
    this.timer = setInterval(() => { if (!document.hidden && $('#research-grid') && !$('#research-grid').closest('[hidden]')) this.load(); }, 60_000);
  },
  deactivate() { clearInterval(this.timer); this.timer = null; },
  render(grid, d) {
    const s = d.summary || {};
    const best = s.best_bot || {};
    const rc = Object.entries((d.root_causes || {}).counts || {}).filter(([k]) => k !== 'PASSED' && k !== 'NONE');
    const fw = d.forward_shadow || {};
    const answers = h('div', { class: 'card answers' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Research history (V1-V5, stopped)' }),
        h('span', { class: 'sub', text: 'snapshot ' + (d.generated_at || '').replace('T', ' ').replace('Z', ' UTC') + ' · all results are PAPER' })),
      h('div', { class: 'tiles six' },
        tile('bots', String(s.active_bots ?? DASH) + ' running', '', (s.evaluated_bots ?? DASH) + ' evaluated · forward shadow ' +
          (s.forward_shadow_running === false ? 'stopped' : (s.in_forward_shadow ?? 0)) + ' · bake-off ' +
          (s.live_paper_books && !s.live_paper_running ? 'stopped' : (s.live_paper_running ?? s.live_paper_books ?? 0))),
        tile('best bot', best.bot_id ? botLabel(best) : DASH, 'accent', best.bot_id ? stageLabel(best) + ' · ' + fR(best.expectancy_r) + ' · ' + (best.trades ?? 0) + ' trades · ' +
          (best.status === 'ADVANCE' ? 'passed its stage' : 'NOT qualified: ' + cause(best.failure_reason)) : 'nobody ranked yet'),
        tile('profitable', String(s.profitable_after_costs ?? DASH) + ' after costs', (s.profitable_after_costs_30_trades || 0) > 0 ? 'up' : '',
          (s.profitable_after_costs_30_trades ?? 0) + ' with ≥ 30 trades · ' + (s.gross_profitable ?? 0) + ' gross-profitable of ' + (s.bots_with_trades ?? 0)),
        tile('Jev helping', String(s.jev_helping || '').startsWith('YES') ? 'YES' : 'NO', String(s.jev_helping || '').startsWith('YES') ? 'up' : 'down',
          'judged only against the matched random action'),
        tile('advancing', (s.qualified ?? 0) + ' qualified', (s.qualified || 0) > 0 ? 'up' : '',
          'ADVANCED SET ' + (s.advanced_set || 'NONE') + ' · ' + (s.passed_discovery ?? 0) + ' passed dev · ' + (s.passed_holdout ?? 0) + ' holdout · ' + (s.passed_multi_year ?? 0) + ' multi-year'),
        tile('failing', rc.length ? cause(rc[0][0]) : DASH, 'down', rc.slice(0, 3).map(([k, v]) => cause(k) + ' ' + v).join(' · '))));
    const programs = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Research programs' }),
      h('span', { class: 'sub', text: (d.system || {}).competition_status || '' })),
      h('div', { class: 'tablewrap' }, (() => {
        const t = h('table', { class: 'tbl' });
        renderTable(t, [
          { h: 'program', cell: (r) => r.program + (r.stage ? ' ' + r.stage : '') },
          { h: 'window', cell: (r) => r.window || DASH },
          { h: 'status', cell: (r) => pill(r.status === 'running' ? 'RUNNING' + (r.stage_now ? ' · ' + r.stage_now : '') : r.status || '?', r.status === 'running' ? 'accent' : r.status === 'complete' ? 'muted' : 'warn') },
          { h: 'advanced set', cell: (r) => (Array.isArray(r.advanced_set) ? r.advanced_set.join(', ') : r.advanced_set) || DASH },
          { h: 'headline', cell: (r) => truncate(r.headline || '', 110) }],
        (d.experiments || []).filter((r) => ['V5', 'V4', 'V3.1', 'V3'].includes(r.program) || r.status === 'running').slice(0, 8));
        return t;
      })()),
      h('p', { class: 'sub' }, 'Older programs (V4 and earlier) are under ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (window.Nav) Nav.go('validation'); }, text: 'VALIDATION · research history' }), '.'));
    const top = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Top 10 bots' }),
      h('span', { class: 'sub', text: 'current result of each bot · ≥ 30 trades · ranked by net expectancy per trade · ranking is not qualification' })),
      h('div', { class: 'tablewrap' }, Traders.table(d.top_bots || [], false)));
    const topJ = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Top 10 Jev bots' }),
      h('span', { class: 'sub', text: 'selection alpha = Jev net minus the matched random action (same SKIP / TAKE / ATTACK rates)' })),
      h('div', { class: 'tablewrap' }, Traders.table(d.top_jev_bots || [], true)));
    const fwd = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Forward shadow (live data, paper fills)' }),
      h('span', { class: 'sub', text: fw.status || DASH })),
      h('div', { class: 'tiles' }, tile('bots', String(fw.bots ?? DASH)), tile('closed trades', String(fw.trades ?? fw.closed_trades ?? DASH)),
        tile('net', fU(fw.net), signClass(fw.net)), tile('open positions', String(fw.open_positions ?? DASH))));
    const links = h('div', { class: 'card' }, h('h2', { text: 'Machine-readable' }),
      h('p', { class: 'sub' }, h('a', { href: INSPECT, text: INSPECT }), ' — the whole snapshot as JSON (no login, GET only) · ',
        h('a', { href: '/public/competition/report', text: 'static report' })));
    clear(grid).append(answers, programs, top, topJ, fwd, links);
  },
};

/* ---- TRADERS ---- */
const Traders = {
  botId: '', tab: 'overview', filter: { program: '', mode: '' },
  table(rows, jev) {
    const t = h('table', { class: 'tbl lb' });
    const cols = [
      { h: '#', cell: (r, i) => String(r.rank || i + 1), num: true },
      { h: 'bot', cell: (r) => h('span', { title: r.bot_id, text: botLabel(r) + (jev ? ' +JEV' : '') }) },
      { h: 'program', cell: (r) => stageLabel(r) },
      { h: 'trades', cell: (r) => String(r.trades ?? DASH), num: true },
      { h: '/day', cell: (r) => fmtNum(r.trades_per_day, 2), num: true },
      { h: 'gross', cell: (r) => num(r.gross_pnl, fU), num: true },
      { h: 'costs', cell: (r) => fU(-((r.fees || 0) + (r.slippage || 0))), num: true },
      { h: 'net', cell: (r) => num(r.net_pnl, fU), num: true },
      { h: 'return', cell: (r) => num(r.return_pct, fP), num: true },
      { h: 'exp R', cell: (r) => num(r.expectancy_r, fR), num: true },
      { h: 'PF', cell: (r) => fPF(r.profit_factor), num: true },
      { h: 'max DD', cell: (r) => fDD(r.max_drawdown), num: true }];
    if (jev) cols.push({ h: 'Jev S/T/A', cell: (r) => { const j = r.jev || {}; return [j.skip_rate, j.take_rate, j.attack_rate].map((x) => (isNum(x) ? Math.round(x * 100) + '%' : DASH)).join(' / '); } },
      { h: 'AUC', cell: (r) => fmtNum((r.jev || {}).auc, 3), num: true },
      { h: 'sel. alpha', cell: (r) => num((r.jev || {}).selection_alpha, fU), num: true });
    cols.push({ h: 'status', cell: (r) => (r.qualified ? pill('QUALIFIED', 'up') : pill(cause(r.failure_reason || r.status), r.failure_reason ? 'down' : 'muted')) });
    renderTable(t, cols, rows, { onRow: (r) => Traders.open(r.bot_id), empty: 'no ranked bot yet' });
    return t;
  },
  async load() {
    const grid = $('#traders-grid');
    if (!grid) return;
    if (this.botId) return this.detail(grid, this.botId);
    let d;
    try { d = await Snapshot.get(); } catch (e) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: e.message }))); return; }
    const all = (d.leaderboard || []).concat((d.top_jev_bots || []).map((r) => Object.assign({ mode: 'JEV' }, r)));
    const programs = [...new Set(all.map((r) => r.program).filter(Boolean))];
    const rows = all.filter((r) => (!this.filter.program || r.program === this.filter.program) &&
      (!this.filter.mode || (this.filter.mode === 'JEV' ? r.mode === 'JEV' || r.jev : r.mode !== 'JEV' && !r.jev)));
    const chips = h('div', { class: 'chips' },
      ['', ...programs].map((p) => h('button', { type: 'button', class: 'chip' + (this.filter.program === p ? ' on' : ''), text: p || 'all programs',
        onclick: () => { this.filter.program = p; this.load(); } })),
      ['', 'CONTROL', 'JEV'].map((m) => h('button', { type: 'button', class: 'chip' + (this.filter.mode === m ? ' on' : ''), text: m || 'all modes',
        onclick: () => { this.filter.mode = m; this.load(); } })));
    clear(grid).append(h('div', { class: 'card toolbar' }, chips, h('span', { class: 'sub', text: rows.length + ' traders · the top 50 ranked controls plus the top Jev bots of every program · click one for its detail' })),
      h('div', { class: 'card' }, h('div', { class: 'tablewrap' }, this.table(rows.filter((r) => !(r.mode === 'JEV' || r.jev)), false))),
      rows.some((r) => r.mode === 'JEV' || r.jev) ? h('div', { class: 'card' }, h('h2', { text: 'Jev bots' }), h('div', { class: 'tablewrap' }, this.table(rows.filter((r) => r.mode === 'JEV' || r.jev), true))) : null);
  },
  /** Open one trader (or the list, with ''): the page's Nav owns the URL, so a link survives a reload. */
  open(botId) {
    this.botId = botId || '';
    this.tab = 'overview';
    if (typeof Nav !== 'undefined' && Nav.openTrader) Nav.openTrader(this.botId);
    else this.load();
  },
  back() { this.open(''); },
  async detail(grid, botId) {
    clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'sub', text: 'loading ' + botId + '…' })));
    let d;
    try { d = await getJSON('/public/inspection/bot/' + encodeURIComponent(botId) + '.json'); } catch (e) {
      clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: botId + ': ' + e.message }), h('button', { class: 'btn ghost', type: 'button', text: '← all traders', onclick: () => this.back() })));
      return;
    }
    const tabs = ['overview', 'trades', 'analyzer', 'jev', 'validation'];
    const body = h('div');
    const bar = h('nav', { class: 'subtabs', role: 'tablist' }, tabs.map((t) => h('button', { role: 'tab', type: 'button', class: t === this.tab ? 'active' : '', text: t[0].toUpperCase() + t.slice(1),
      onclick: () => { this.tab = t; this.detail(grid, botId); } })));
    const head = h('div', { class: 'card' }, h('div', { class: 'card-head' },
      h('button', { class: 'btn ghost', type: 'button', text: '← all traders', onclick: () => this.back() }),
      h('h2', { text: botLabel(d) + (d.mode === 'JEV' ? ' +JEV' : '') }),
      h('span', { class: 'sub', text: stageLabel(d) + ' · ' + (d.window_from || '') + (d.window_to ? ' → ' + d.window_to : '') + ' · ' + d.bot_id })),
      h('div', { class: 'tiles six' }, tile('net', fU(d.net_pnl), signClass(d.net_pnl), fP(d.return_pct) + ' of ' + fmtNum(d.starting_equity, 0) + ' USDT'),
        tile('gross', fU(d.gross_pnl), signClass(d.gross_pnl), 'fees ' + fU(-(d.fees || 0)) + ' · slippage ' + fU(-(d.slippage || 0)) + ' · funding ' + fU(d.funding)),
        tile('expectancy', fR(d.expectancy_r), signClass(d.expectancy_r), (d.trades ?? 0) + ' trades · ' + fmtNum(d.trades_per_day, 2) + '/day'),
        tile('profit factor', fPF(d.profit_factor), '', 'max DD ' + fDD(d.max_drawdown)),
        tile('status', d.qualified ? 'QUALIFIED' : cause(d.status), d.qualified ? 'up' : '', d.failure_reason ? 'primary failure: ' + cause(d.failure_reason) : ''),
        tile('without best 3', fU(d.net_without_top3), signClass(d.net_without_top3), 'profit concentration check')));
    clear(grid).append(head, h('div', { class: 'card' }, bar, body));
    if (this.tab === 'overview') body.append(kvList({ bot_id: d.bot_id, program: d.program, stage: d.stage, strategy: d.strategy, family: d.family,
      coin: d.coin, timeframe: d.timeframe, mode: d.mode, venue: d.venue, leverage_ceiling: d.leverage, starting_equity: d.starting_equity,
      equity: d.equity, cost_to_edge: d.cost_to_edge, cost_class: d.cost_class, win_rate: d.win_rate, forward: d.forward }));
    if (this.tab === 'trades') this.trades(body, botId);
    if (this.tab === 'analyzer' && (d.analysis || {}).money) {
      const a = d.analysis;
      const m = a.money || {};
      const act = a.activity || {};
      const hz = Object.entries(a.horizons || {});
      const ht = h('table', { class: 'tbl' });
      renderTable(ht, [{ h: 'after entry', cell: (r) => r[0] }, { h: 'trades', cell: (r) => String(r[1].n), num: true },
        { h: 'MFE R', cell: (r) => fmtNum(r[1].mfe_r, 2), num: true }, { h: 'MAE R', cell: (r) => fmtNum(r[1].mae_r, 2), num: true },
        { h: 'drift R', cell: (r) => num(r[1].drift_r, fR), num: true }, { h: 'reached +1R', cell: (r) => fDD(r[1].reached_1r), num: true }], hz);
      body.append(h('div', { class: 'tiles six' }, tile('signal horizon', d.timeframe || DASH, '', (act.signals ?? 0) + ' setups · ' + fmtNum(act.signals_per_day, 2) + '/day'),
        tile('average hold', isNum(act.avg_hold_h) ? act.avg_hold_h.toFixed(1) + ' h' : DASH, '', 'median ' + fmtNum(act.median_hold_h, 1) + ' h'),
        tile('funding', fU(m.funding_net), signClass(m.funding_net), 'paid ' + fU(-(m.funding_paid || 0)) + ' · received ' + fU(m.funding_received)),
        tile('total fees', fU(-(m.fees || 0)), '', 'maker ' + fU(-(m.maker_fees || 0)) + ' · taker ' + fU(-(m.taker_fees || 0))),
        tile('label', cause(a.label), a.label === 'ACTIVE_POSITIVE_EDGE' ? 'up' : '', (a.flags || []).map(cause).join(' · ') || 'family ' + cause(a.family_verdict)),
        tile('MIN_NOTIONAL refused', String(act.min_notional_refused ?? 0), '', fDD(act.min_notional_share) + ' of setups · never enlarged')),
      h('h3', { text: 'gross → net' }), V5View.waterfall(m),
      h('h3', { text: 'MFE / MAE after entry (R of the trade’s own stop)' }), h('div', { class: 'tablewrap' }, ht),
      h('h3', { text: 'exit reasons' }), kvList(a.exit_mix || {}),
      h('h3', { text: 'gates' }), h('div', { class: 'tablewrap' }, (() => { const t = h('table', { class: 'tbl' });
        renderTable(t, [{ h: 'gate', cell: (g) => g.name }, { h: 'result', cell: (g) => pill(g.ok ? 'PASS' : 'FAIL', g.ok ? 'up' : 'down') },
          { h: 'actual', cell: (g) => fmtVal(g.actual) }, { h: 'threshold', cell: (g) => g.threshold || DASH }], a.gates || []); return t; })()));
    } else if (this.tab === 'analyzer') {
      const a = d.analysis || {};
      body.append(a.gates ? h('div', { class: 'tablewrap' }, (() => { const t = h('table', { class: 'tbl' });
        renderTable(t, [{ h: 'gate', cell: (g) => g.name }, { h: 'result', cell: (g) => pill(g.ok ? 'PASS' : 'FAIL', g.ok ? 'up' : 'down') },
          { h: 'actual', cell: (g) => fmtVal(g.actual) }, { h: 'threshold', cell: (g) => g.threshold || DASH }], a.gates); return t; })()) : h('p', { class: 'sub', text: 'no gate list stored for this program' }),
      a.diagnoses ? h('p', null, h('b', { text: 'diagnoses: ' }), (a.diagnoses || []).map(cause).join(' · ')) : null,
      kvList(Object.fromEntries(Object.entries(a).filter(([k]) => !['gates', 'diagnoses'].includes(k)))));
    }
    if (this.tab === 'jev') body.append(d.jev ? kvList(d.jev) : h('p', { class: 'sub', text: 'a CONTROL bot: no Jev in its pipeline. Its +JEV twin, if one ran, is a separate trader.' }));
    if (this.tab === 'validation') {
      const t = h('table', { class: 'tbl' });
      renderTable(t, [{ h: 'stage', cell: (e) => stageLabel(e) }, { h: 'window', cell: (e) => (e.window_from || '') + ' → ' + (e.window_to || '') },
        { h: 'trades', cell: (e) => String(e.trades ?? DASH), num: true }, { h: 'net', cell: (e) => num(e.net_pnl, fU), num: true },
        { h: 'exp R', cell: (e) => num(e.expectancy_r, fR), num: true }, { h: 'status', cell: (e) => cause(e.status) },
        { h: 'failure', cell: (e) => cause(e.failure_reason) }], d.evaluations || [], { empty: 'one evaluation only' });
      body.append(h('div', { class: 'tablewrap' }, t), h('p', { class: 'sub', text: 'pipeline: development → holdout test → multi-year → forward shadow → qualified → live candidate (operator only)' }));
    }
  },
  async trades(body, botId) {
    body.append(h('p', { class: 'sub', text: 'loading trades…' }));
    let d;
    try { d = await getJSON(PUB + '/trader/' + encodeURIComponent(botId) + '/trades?limit=300'); } catch (e) { clear(body).append(h('p', { class: 'err', text: e.message })); return; }
    clear(body);
    if (!d.stored) { body.append(h('p', { class: 'sub', text: d.note || 'no trade list stored' })); return; }
    const t = h('table', { class: 'tbl' });
    const v5 = (d.trades || []).some((x) => x.setup || x.positioning);
    const cols = [{ h: 'entry', cell: (x) => new Date(x.entry_ts).toISOString().slice(0, 16).replace('T', ' ') },
      { h: 'side', cell: (x) => pill(x.side, x.side) }, { h: 'hold', cell: (x) => fmtDur(x.hold_s || 0), num: true },
      { h: 'gross', cell: (x) => num(x.gross, fU), num: true }, { h: 'costs', cell: (x) => fU(-((x.fees || 0) + (x.slippage || 0))), num: true },
      { h: 'funding', cell: (x) => (v5 ? fU(-(x.funding_paid || 0)) + ' / ' + fU(x.funding_received || 0) : num(x.funding, fU)), num: true },
      { h: 'net', cell: (x) => num(x.net, fU), num: true }, { h: 'R', cell: (x) => num(x.r, fR), num: true },
      { h: 'exit', cell: (x) => x.exit_kind || DASH }, { h: 'size', cell: (x) => x.jev_level || x.tier || DASH }];
    if (v5) cols.push({ h: 'setup', cell: (x) => truncate(x.setup || DASH, 48) },
      { h: 'stop', cell: (x) => (isNum(x.stop_pct) ? (x.stop_pct * 100).toFixed(2) + '%' : DASH), num: true },
      { h: 'funding pct · OI 24h', cell: (x) => { const p = x.positioning || {}; return fmtNum(p.funding_pct_90d, 2) + ' · ' + fP(p.oi_chg_24h); } },
      { h: 'regime', cell: (x) => x.regime || DASH });
    renderTable(t, cols, d.trades);
    body.append(h('p', { class: 'sub', text: 'newest ' + d.shown + ' of ' + d.total + ' closed trades (paper) · run ' + d.run_id +
      (v5 ? ' · funding column: paid / received' : '') }), h('div', { class: 'tablewrap' }, t));
  },
};

/* ---- V4 ARENA ---- */
const V4View = {
  async load() {
    const grid = $('#v4-grid');
    if (!grid) return;
    let d;
    try { d = await getJSON(PUB + '/v4'); } catch (e) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: e.message }))); return; }
    clear(grid);
    grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'V4 intraday specialists' }),
      h('span', { class: 'sub', text: '1h trend → 15m / 30m structure → 3m / 5m / 15m / 30m trigger · 20 USDT · ' + d.venue_label })),
      h('p', { class: 'sub', text: 'Seven families, one exit, a causal family × timeframe expected-edge gate, Jev V4 (CONTRADICT / SUPPORT / STRONGLY SUPPORT → SKIP / TAKE / ATTACK). ADVANCED = passes every gate in DEVELOPMENT and in the pre-registered holdout.' })));
    if (d.note) { grid.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: d.note }))); return; }
    for (const [label, b] of [['DEVELOPMENT', d.dev], ['HOLDOUT TEST', d.test]]) {
      if (!b) { grid.append(h('div', { class: 'card' }, h('h2', { text: label }), h('p', { class: 'sub', text: label === 'HOLDOUT TEST' ? 'not run yet: runs only after V4 is frozen and the holdout is pre-registered' : 'not run yet' }))); continue; }
      const run = b.run || {};
      if (!b.counts) {
        grid.append(h('div', { class: 'card' }, h('h2', { text: label + ' · ' + run.run_id }), h('p', null, pill('RUNNING · ' + (run.stage || ''), 'accent'), ' ',
          h('span', { class: 'sub', text: (b.bots_done ?? 0) + ' bots replayed so far · ' + (run.label || '') }))));
        continue;
      }
      const a = b.answers || {};
      grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: label + ' · ' + (b.window || {}).from + ' → ' + (b.window || {}).to }),
        h('span', { class: 'sub', text: run.run_id + ' · ' + (b.counts.controls || 0) + ' controls · ' + (b.counts.jev_bots || 0) + ' Jev bots' })),
      h('div', { class: 'tiles' }, tile('advanced set', Array.isArray(b.advanced_set) ? b.advanced_set.length + ' bots' : String(b.advanced_set), Array.isArray(b.advanced_set) ? 'up' : ''),
        tile('passed every gate', String(((b.passed || {}).controls || []).length + ((b.passed || {}).jev_bots || []).length)),
        tile('Jev selection alpha', fU((b.selection_alpha || {}).total_usdt), signClass((b.selection_alpha || {}).total_usdt), ((b.selection_alpha || {}).positive_pairs ?? 0) + '/' + ((b.selection_alpha || {}).pairs ?? 0) + ' pairs positive'),
        tile('ATTACK added', fU(((b.attack || {}).jev || {}).attack_added_net), signClass(((b.attack || {}).jev || {}).attack_added_net), 'vs the same trades at normal size')),
      h('p', null, h('b', { text: 'edge: ' }), a.intraday_edge || DASH), h('p', null, h('b', { text: 'Jev: ' }), a.jev_selection || DASH),
      h('p', { class: 'sub' }, h('b', { text: 'failure modes (controls): ' }), Object.entries((b.failure_modes || {}).controls || {}).map(([k, v]) => cause(k) + ' ' + v).join(' · '))));
      const raw = ((b.raw_edge || {}).by_family_tf || []);
      if (raw.length) {
        const tr = h('table', { class: 'tbl' });
        renderTable(tr, [{ h: 'family × timeframe', cell: (r) => r.group }, { h: 'setups', cell: (r) => String(r.setups), num: true },
          { h: 'gross R', cell: (r) => num(r.gross_r, fR), num: true }, { h: 't', cell: (r) => fmtNum(r.t, 2), num: true },
          { h: 'cost R', cell: (r) => fmtNum(r.cost_r, 3), num: true }, { h: 'net R', cell: (r) => num(r.net_r, fR), num: true },
          { h: 'win rate', cell: (r) => fDD(r.win_rate), num: true },
          { h: 'diagnosis', cell: (r) => pill(r.diagnosis, r.diagnosis === 'EDGE SURVIVES COSTS' ? 'up' : r.diagnosis === 'NO RAW EDGE' ? 'down' : 'warn') }], raw);
        grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Raw edge before any gate · family × timeframe (' + label + ')' }),
          h('span', { class: 'sub', text: 'every legal setup simulated at TAKE size, sequenced like an ungated bot — the evidence the expected-edge gate reads' })),
        h('div', { class: 'tablewrap' }, tr)));
      }
      const eq = ((b.edge_quality || {}).by_family_tf || []);
      const t = h('table', { class: 'tbl' });
      renderTable(t, [{ h: 'family × timeframe', cell: (r) => r.group }, { h: 'bots', cell: (r) => String(r.bots), num: true },
        { h: 'trades', cell: (r) => String(r.trades), num: true }, { h: 'gross', cell: (r) => num(r.gross, fU), num: true },
        { h: 'fees', cell: (r) => fU(-(r.fees || 0)), num: true }, { h: 'slippage', cell: (r) => fU(-(r.slippage || 0)), num: true },
        { h: 'net', cell: (r) => num(r.net, fU), num: true }, { h: 'gross bps/turnover', cell: (r) => fmtNum(r.gross_bps_of_turnover, 1), num: true },
        { h: 'gross per fee $', cell: (r) => fmtNum(r.gross_per_fee_dollar, 2), num: true }, { h: 'diagnosis', cell: (r) => pill(r.diagnosis, r.diagnosis === 'EDGE SURVIVES COSTS' ? 'up' : r.diagnosis === 'NO RAW EDGE' ? 'down' : 'warn') }], eq);
      grid.append(h('div', { class: 'card' }, h('h2', { text: 'Edge decomposition · family × timeframe (' + label + ')' }), h('div', { class: 'tablewrap' }, t)));
      const ex = (b.exits || {}).by_tf || {};
      const te = h('table', { class: 'tbl' });
      renderTable(te, [{ h: 'timeframe', cell: (r) => r[0] }, { h: 'trades', cell: (r) => String(r[1].trades), num: true },
        { h: 'MFE R', cell: (r) => fmtNum(r[1].mfe_r_mean, 2), num: true }, { h: 'MAE R', cell: (r) => fmtNum(r[1].mae_r_mean, 2), num: true },
        { h: 'stopped → +2R', cell: (r) => fDD(r[1].stopped_then_2r_share), num: true }, { h: 'random entries', cell: (r) => fDD(r[1].random_stopped_then_2r_share), num: true },
        { h: 'after a win (4h)', cell: (r) => fR(r[1].winners_post_exit_4h_r_mean), num: true },
        { h: 'hold win / loss', cell: (r) => fmtNum(r[1].winner_hold_min_p50, 0) + ' / ' + fmtNum(r[1].loser_hold_min_p50, 0) + ' min' },
        { h: 'exit verdict', cell: (r) => (r[1].flags || []).join(' · ') }], Object.entries(ex));
      grid.append(h('div', { class: 'card' }, h('h2', { text: 'Exit analyzer (' + label + ')' }), h('div', { class: 'tablewrap' }, te)));
      const lb = h('table', { class: 'tbl lb' });
      renderTable(lb, [{ h: '#', cell: (r) => String(r.rank), num: true }, { h: 'bot', cell: (r) => r.key }, { h: 'trades', cell: (r) => String(r.trades), num: true },
        { h: 'net', cell: (r) => num(r.net_pnl, fU), num: true }, { h: 'gross R', cell: (r) => num(r.gross_exp_r, fR), num: true },
        { h: 'net R', cell: (r) => num(r.exp_r, fR), num: true }, { h: 'PF', cell: (r) => fPF(r.pf), num: true },
        { h: 'P(≤0)', cell: (r) => fmtNum(r.p_mean_le_0, 2), num: true }, { h: 'state', cell: (r) => pill(cause(r.state), r.state === 'ADVANCE' ? 'up' : 'muted') },
        { h: 'failure', cell: (r) => cause(r.failure_mode) }], (b.leaderboard_controls || []).slice(0, 25),
      { onRow: (r) => Traders.open((label === 'DEVELOPMENT' ? 'v4d:' : 'v4t:') + r.key) });
      grid.append(h('div', { class: 'card' }, h('h2', { text: 'Controls (' + label + ', top 25 by score; ranking is not qualification)' }), h('div', { class: 'tablewrap' }, lb)));
      if ((b.pairs || []).length) {
        const tp = h('table', { class: 'tbl' });
        renderTable(tp, [{ h: 'pair', cell: (p) => p.strategy_id + '-' + p.coin + '-' + p.tf }, { h: 'Jev net', cell: (p) => num((p.jev || {}).net, fU), num: true },
          { h: 'control', cell: (p) => num((p.control || {}).net, fU), num: true }, { h: 'always take', cell: (p) => num((p.always_take || {}).net, fU), num: true },
          { h: 'random p50 / p90', cell: (p) => fU((p.random || {}).random_median) + ' / ' + fU((p.random || {}).random_p90) },
          { h: 'alpha', cell: (p) => num((p.random || {}).alpha_usdt, fU), num: true },
          { h: 'S/T/A', cell: (p) => { const a2 = p.jev_actions || {}; const n2 = Object.values(a2).reduce((x, y) => x + y, 0) || 1; return ['SKIP', 'TAKE', 'ATTACK'].map((k) => Math.round(100 * (a2[k] || 0) / n2) + '%').join(' / '); } },
          { h: 'ATTACK +', cell: (p) => num((p.attack_jev || {}).attack_added_net, fU), num: true }], b.pairs);
        grid.append(h('div', { class: 'card' }, h('h2', { text: 'Jev V4 vs its baselines (' + label + ')' }), h('div', { class: 'tablewrap' }, tp)));
      }
      const cap = b.capacity || {};
      if ((cap.rows || []).length) grid.append(h('div', { class: 'card' }, h('h2', { text: 'Account size (diagnostic only)' }),
        h('p', { class: 'sub', text: cap.note || '' }), h('p', null, Object.entries(cap.verdicts || {}).map(([k, v]) => pill(cause(k) + ' ' + v, k === 'BAD' ? 'down' : k === 'OK_AT_20' ? 'up' : 'warn')))));
    }
  },
};

/* ---- V5 FUTURES ARENA (frozen research history) ---- */
const V5View = {
  verdictPill(v) { return pill(cause(v || 'NOT_EVALUATED'), v === 'PASSED' ? 'up' : v === 'NO_RAW_EDGE' ? 'down' : v ? 'warn' : 'muted'); },
  waterfall(c) {
    if (!c) return h('p', { class: 'sub', text: 'no trades' });
    const rows = [['gross (price move only)', c.gross], ['maker fees', -(c.maker_fees || 0)], ['taker fees', -(c.taker_fees || 0)],
      ['slippage & spread', -(c.slippage || 0)], ['funding paid', -(c.funding_paid || 0)], ['funding received', c.funding_received], ['NET', c.net]];
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'step', cell: (r) => r[0] }, { h: 'USDT', cell: (r) => num(r[1], fU), num: true }], rows);
    return h('div', { class: 'tablewrap' }, t);
  },
  families(b, label) {
    const fams = b.families || [];
    if (!fams.length) return null;
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'family', cell: (f) => f.strategy_id + ' ' + cause(f.family) }, { h: 'class', cell: (f) => f.horizon },
      { h: 'trades (100 USDT)', cell: (f) => String((f.raw_edge || {}).trades ?? DASH), num: true },
      { h: 'gross R', cell: (f) => num((f.raw_edge || {}).mean_r, fR), num: true },
      { h: 'P(≤0)', cell: (f) => fmtNum((f.raw_edge || {}).p_mean_le_0, 3), num: true },
      { h: '90% CI', cell: (f) => { const c = (f.raw_edge || {}).ci90; return c ? fR(c[0]) + ' … ' + fR(c[1]) : DASH; } },
      { h: 'sub-periods', cell: (f) => ((f.raw_edge || {}).subperiod_mean_r || []).map((x) => (isNum(x) ? x.toFixed(2) : '–')).join(' / ') },
      { h: 'coins +', cell: (f) => { const cs = Object.values((f.raw_edge || {}).coins || {}).filter((c) => c.n >= 10); return cs.filter((c) => c.mean_r > 0).length + '/' + cs.length; } },
      { h: 'net R @20 (n)', cell: (f) => { const e = f.economic_20 || f.economic_20_baseline || {}; return fR(e.mean_r) + ' (' + (e.trades ?? 0) + ')'; } },
      { h: 'cost R', cell: (f) => fmtNum((f.costs_100 || {}).cost_r, 3), num: true },
      { h: 'funding R', cell: (f) => num((f.costs_100 || {}).funding_r, fR), num: true },
      { h: 'refused @20', cell: (f) => fDD((f.costs_20 || {}).refused_share), num: true },
      { h: 'exit', cell: (f) => (f.exit || {}).plan || DASH },
      { h: 'verdict', cell: (f) => this.verdictPill(f.verdict || ((f.raw_edge || {}).passed ? null : 'NO_RAW_EDGE')) }], fams);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Stage 1 raw edge · family × class (' + label + ')' }),
      h('span', { class: 'sub', text: 'ungated controls, no Jev; the raw-edge gate reads the 100 USDT twins (every setup legal); economics read the official 20 USDT books' })),
    h('div', { class: 'tablewrap' }, t));
  },
  horizons(b, label) {
    const fams = (b.families || []).filter((f) => f.horizons && Object.keys(f.horizons).length);
    if (!fams.length) return null;
    const hs = ['1h', '2h', '4h', '8h', '12h', '24h', '48h', '72h'];
    const t = h('table', { class: 'tbl' });
    renderTable(t, [{ h: 'family × class', cell: (f) => f.strategy_id + ' ' + f.horizon }]
      .concat(hs.map((k) => ({ h: 'drift ' + k, cell: (f) => { const x = f.horizons[k]; return x ? num(x.drift_r, fR) : DASH; }, num: true })))
      .concat([{ h: 'MFE / MAE 24h', cell: (f) => { const x = f.horizons['24h']; return x ? fmtNum(x.mfe_r, 2) + ' / ' + fmtNum(x.mae_r, 2) : DASH; } }]), fams);
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Where the entries point · mean close-vs-entry drift after entry, in R (' + label + ')' }),
      h('span', { class: 'sub', text: 'whatever the actual exit was; DEVELOPMENT evidence for the time stop (Amendment 2)' })), h('div', { class: 'tablewrap' }, t));
  },
  async load() {
    const grid = $('#v5-grid');
    if (!grid) return;
    let d;
    try { d = await getJSON(PUB + '/v5'); } catch (e) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: e.message }))); return; }
    clear(grid);
    grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'V5 hourly / daily futures arena' }),
      h('span', { class: 'sub', text: 'HOURLY: 1h signal, 4h + 1D context, 4-24 h holds · SWING: 4h signal, 1D + 1W context, 12-72 h holds · 20 USDT · ' + d.venue_label })),
    h('p', { class: 'sub', text: 'Eight positioning families (funding, open interest, basis, long/short ratio). Raw edge first: a family advances only if its gross edge is real and survives fees, slippage and funding; Jev V5 (CONTRADICT / SUPPORT / STRONGLY SUPPORT → SKIP / TAKE / ATTACK) runs only for survivors. No untouched historical window remains: the holdout is a PSEUDO-HOLDOUT, and QUALIFIED needs forward validation.' })));
    if (d.note) { grid.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: d.note }))); return; }
    for (const [label, b, pre] of [['DEVELOPMENT', d.dev, 'v5d:'], ['PSEUDO-HOLDOUT', d.test, 'v5t:']]) {
      if (!b) { grid.append(h('div', { class: 'card' }, h('h2', { text: label }), h('p', { class: 'sub', text: label === 'PSEUDO-HOLDOUT' ? 'not run yet: runs only after V5 is frozen and the holdout is pre-registered' : 'not run yet' }))); continue; }
      const run = b.run || {};
      if (!b.counts) {
        grid.append(h('div', { class: 'card' }, h('h2', { text: label + ' · ' + run.run_id }), h('p', null, pill('RUNNING · ' + (run.stage || ''), 'accent'), ' ',
          h('span', { class: 'sub', text: (b.bots_done ?? 0) + ' bots replayed so far · ' + (run.label || '') }))));
        const f = this.families(b, label);
        if (f) grid.append(f);
        continue;
      }
      const a = b.answers || {};
      const c = (b.costs || {}).controls_20 || {};
      const sa = b.selection_alpha || {};
      grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: label + ' · ' + (b.window || {}).from + ' → ' + (b.window || {}).to }),
        h('span', { class: 'sub', text: run.run_id + ' · ' + (b.counts.controls || 0) + ' official controls · ' + (b.counts.all_books || 0) + ' books replayed' })),
      h('div', { class: 'tiles six' },
        tile('advanced set', Array.isArray(b.advanced_set) ? b.advanced_set.length + ' bots' : String(b.advanced_set), Array.isArray(b.advanced_set) ? 'up' : ''),
        tile('raw-edge families', String((b.families || []).filter((f) => (f.raw_edge || {}).passed).length) + ' / ' + (b.families || []).length, '', 'Stage 1 gate on the 100 USDT twins'),
        tile('after costs', String((b.economic || []).filter((e) => e.verdict === 'PASSED').length), '', 'families passing the economic gate'),
        tile('profitable controls', String(b.profitable_controls ?? DASH), '', 'net > 0 after fees, slippage and funding'),
        tile('net (controls)', fU(c.net), signClass(c.net), 'gross ' + fU(c.gross) + ' · ' + (c.trades ?? 0) + ' trades'),
        tile('Jev selection alpha', (b.pairs || []).length ? fU(sa.total_usdt) : 'NOT RUN', signClass(sa.total_usdt), (b.pairs || []).length ? (sa.positive_pairs ?? 0) + '/' + (sa.pairs ?? 0) + ' pairs positive' : 'no family survived before Jev')),
      h('p', null, h('b', { text: 'raw edge: ' }), a.raw_edge || DASH), h('p', null, h('b', { text: 'after costs: ' }), a.economics || DASH),
      h('p', null, h('b', { text: 'verdict: ' }), a.verdict || DASH), h('p', null, h('b', { text: 'Jev: ' }), a.jev || DASH),
      h('p', { class: 'sub' }, h('b', { text: 'primary failures (controls): ' }), Object.entries((b.failure_modes || {}).controls || {}).map(([k, v]) => cause(k) + ' ' + v).join(' · ')),
      h('p', { class: 'sub' }, h('b', { text: 'activity labels: ' }), Object.entries(b.labels || {}).map(([k, v]) => cause(k) + ' ' + v).join(' · '))));
      const f = this.families(b, label);
      if (f) grid.append(f);
      const vt = h('table', { class: 'tbl' });
      renderTable(vt, [{ h: 'horizon', cell: (r) => r.horizon + ' (' + r.signal + ')' }, { h: 'bots', cell: (r) => String(r.bots), num: true },
        { h: 'trades', cell: (r) => String(r.trades), num: true }, { h: 'trades/day/bot', cell: (r) => fmtNum(r.trades_per_day_per_bot, 3), num: true },
        { h: 'avg hold', cell: (r) => fmtNum(r.avg_hold_h, 1) + ' h', num: true }, { h: 'gross exp', cell: (r) => num(r.gross_exp_r, fR), num: true },
        { h: 'fees', cell: (r) => fU(-(r.fees || 0)), num: true }, { h: 'slippage', cell: (r) => fU(-(r.slippage || 0)), num: true },
        { h: 'funding', cell: (r) => num(r.funding, fU), num: true }, { h: 'net exp', cell: (r) => num(r.net_exp_r, fR), num: true },
        { h: 'net', cell: (r) => num(r.net, fU), num: true }, { h: 'PF', cell: (r) => fPF(r.pf), num: true },
        { h: 'profitable', cell: (r) => fDD(r.profitable_pct), num: true }, { h: 'qualified', cell: (r) => String(r.qualified), num: true }], b.viability || []);
      grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'HOURLY / DAILY viability (' + label + ', official 20 USDT controls)' }),
        h('span', { class: 'sub', text: 'every order is a MARKET (taker) order, so maker fees are 0 by construction' })), h('div', { class: 'tablewrap' }, vt)));
      grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Gross → net (' + label + ', official controls)' }),
        h('span', { class: 'sub', text: 'net edge per trade ' + fU(c.net_per_trade) + ' · per bot-day ' + fmtNum(c.net_per_bot_day, 4) + ' · ' + (c.refused_min_notional ?? 0) + ' setups refused by the exchange minimum (MIN_NOTIONAL_LIMITED, never enlarged)' })),
      this.waterfall(c)));
      const hz = this.horizons(b, label);
      if (hz) grid.append(hz);
      const lb = h('table', { class: 'tbl lb' });
      renderTable(lb, [{ h: '#', cell: (r) => String(r.rank), num: true }, { h: 'bot', cell: (r) => r.strategy_id + '-' + r.coin },
        { h: 'horizon', cell: (r) => r.horizon }, { h: 'trades/day', cell: (r) => fmtNum(r.trades_per_day, 3), num: true },
        { h: 'hold', cell: (r) => (isNum(r.avg_hold_h) ? r.avg_hold_h.toFixed(1) + ' h' : DASH), num: true },
        { h: 'gross', cell: (r) => num(r.gross, fU), num: true }, { h: 'fees', cell: (r) => fU(-(r.fees || 0)), num: true },
        { h: 'funding', cell: (r) => num(r.funding, fU), num: true }, { h: 'net', cell: (r) => num(r.net, fU), num: true },
        { h: 'net/day', cell: (r) => fmtNum(r.net_per_day, 4), num: true }, { h: 'exp R', cell: (r) => num(r.exp_r, fR), num: true },
        { h: 'PF', cell: (r) => fPF(r.pf), num: true }, { h: 'DD', cell: (r) => fDD(r.max_dd), num: true },
        { h: 'Jev', cell: (r) => (r.jev ? ['SKIP', 'TAKE', 'ATTACK'].map((k) => Math.round(100 * (r.jev[k] || 0)) + '%').join('/') : '—') },
        { h: 'status', cell: (r) => (r.state === 'ADVANCE' ? pill('ADVANCE', 'up') : pill(cause(r.failure_mode || r.label), 'muted')) }],
      (b.leaderboard_controls || []).slice(0, 40), { onRow: (r) => Traders.open(pre + r.key) });
      grid.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'After-cost leaderboard (' + label + ', top 40 official controls)' }),
        h('span', { class: 'sub', text: 'ranked by net edge per day, then net expectancy; ranking is not qualification · click a bot for its detail' })), h('div', { class: 'tablewrap' }, lb)));
      const surv = (b.families || []).filter((x) => x.exit_grid);
      for (const s of surv) {
        const gt = h('table', { class: 'tbl' });
        renderTable(gt, [{ h: 'exit plan', cell: (r) => r[0] + ((s.exit || {}).plan === r[0] ? ' ✓' : '') }, { h: 'trades', cell: (r) => String(r[1].n), num: true },
          { h: 'net R', cell: (r) => num(r[1].net_r, fR), num: true }, { h: 'P(≤0)', cell: (r) => fmtNum(r[1].p_mean_le_0, 3), num: true }], Object.entries(s.exit_grid));
        grid.append(h('div', { class: 'card' }, h('h2', { text: 'Exit grid · ' + s.strategy_id + ' ' + s.horizon + ' (DEVELOPMENT only; the selection is frozen)' }), h('div', { class: 'tablewrap' }, gt)));
      }
      if ((b.pairs || []).length) {
        const tp = h('table', { class: 'tbl' });
        renderTable(tp, [{ h: 'pair', cell: (p) => p.strategy_id + '-' + p.coin + '-' + p.horizon }, { h: 'Jev net', cell: (p) => num((p.jev || {}).net, fU), num: true },
          { h: 'control', cell: (p) => num((p.control || {}).net, fU), num: true }, { h: 'always take', cell: (p) => num((p.always_take || {}).net, fU), num: true },
          { h: 'always skip', cell: () => fU(0), num: true },
          { h: 'random p50 / p90', cell: (p) => fU((p.random || {}).random_median) + ' / ' + fU((p.random || {}).random_p90) },
          { h: 'alpha', cell: (p) => num((p.random || {}).alpha_usdt, fU), num: true },
          { h: 'ATTACK +', cell: (p) => num((p.attack || {}).attack_added_net, fU), num: true }], b.pairs);
        grid.append(h('div', { class: 'card' }, h('h2', { text: 'Jev V5 vs its baselines (' + label + ')' }), h('div', { class: 'tablewrap' }, tp)));
      }
      const cap = b.capacity || {};
      if (Object.keys(cap.verdicts || {}).length) grid.append(h('div', { class: 'card' }, h('h2', { text: 'Account size (diagnostic only)' }),
        h('p', { class: 'sub', text: cap.note || '' }), h('p', null, Object.entries(cap.verdicts || {}).map(([k, v]) => pill(cause(k) + ' ' + v, k === 'BAD' ? 'down' : k === 'OK_AT_20' ? 'up' : 'warn')))));
    }
  },
};

/* ---- SYSTEM ---- */
const SystemView = {
  async load() {
    const grid = $('#system-grid');
    if (!grid) return;
    let d;
    try { d = await getJSON(PUB + '/system'); } catch (e) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: e.message }))); return; }
    const sf = d.safety || {};
    const card = (title, node, sub) => h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: title }), sub ? h('span', { class: 'sub', text: sub }) : null), node);
    const runs = (d.research || {}).runs || [];
    const rt = h('table', { class: 'tbl' });
    renderTable(rt, [{ h: 'program', cell: (r) => r.program + ' ' + (r.role || '') }, { h: 'run', cell: (r) => r.run_id },
      { h: 'status', cell: (r) => pill(r.status || '?', r.status === 'running' ? 'accent' : 'muted') }, { h: 'stage', cell: (r) => r.stage || DASH },
      { h: 'window', cell: (r) => r.window }], runs);
    const dev = h('details', { class: 'card dev' }, h('summary', { text: 'Developer details — fingerprints, run ids, schema / API version, data hashes' }),
      kvList(d.developer || {}), h('h3', { text: 'run fingerprints' }),
      runs.map((r) => h('div', null, h('b', { text: r.program + ' ' + (r.role || '') + ' ' + r.run_id }),
        kvList({ config: r.config_fingerprint, dataset: r.dataset_fingerprint, universe: r.universe_fingerprint, strategies: r.strategy_fingerprints, jev: r.jev_fingerprints }))));
    clear(grid).append(
      card('Execution & safety', kvList({ execution: sf.execution, real_orders_placed: sf.real_orders_placed, kill_switch: sf.kill_switch,
        engine_state: sf.engine_state, risk_halt: sf.risk_halt || 'none', market_feed: sf.engine_market_feed, liquidations: sf.liquidations_live }),
      'live capital needs QUALIFIED + an operator; this page cannot change anything'),
      card('Stream', kvList(d.stream || {}), 'WebSocket first, server-sent events as fallback'),
      card('Jev API', kvList(d.jev || {})),
      card('Forward shadow & live data', kvList(Object.assign({}, d.forward_shadow || {}, d.data || {}))),
      card('Research jobs', h('div', { class: 'tablewrap' }, rt), ((d.research || {}).running || []).length ? 'running now' : 'none running'),
      card('Database & environment', kvList({ database: d.database, environment: d.environment })),
      dev);
  },
};
