'use strict';
/* PaperLab - STRATEGY CHECK: the operator's YouTube ranking PDF ("Tested 100 times", "Proven 100 trades") put to a
   pre-registered test on 20 crypto perpetuals and 14 MT5 markets (docs/YT_STRATEGIES_STUDY.md). Read-only:
   GET /api/public/yt-strategies. Shared by the public page and the private dashboard. Each row compares the video's
   claimed win rate with the measured one and the break-even line for its reward:risk, then the net result per year. */
const YtCheck = {
  data: null, market: 'all', sort: 'claim', open: null, loading: false,
  NAMES: {
    rsi_stoch_ema: 'RSI + Stochastic + 200 EMA', ichimoku: 'Ichimoku Cloud', ash: 'Absolute Strength Histogram',
    cmf_macd_atr: 'CMF + MACD + ATR', mfi_ema_stoch: 'MFI + 200 EMA + Stochastic', triple_supertrend: 'Triple Supertrend',
    ema_stochrsi_atr: 'EMA + Stoch RSI + ATR', macd_2stage: 'MACD, 2-stage take profit', macd_ema_1r4: '200 EMA + MACD, 1:4',
    macd_ema_1r3: '200 EMA + MACD, 1:3', macd_ema_1r15: 'MACD, 1:1.5', macd_ema_1r1: 'MACD, 1:1', wavetrend: 'WaveTrend',
    stc: 'STC', alligator: 'Williams Alligator', ttm_squeeze: 'TTM Squeeze', ha_psar_ema: 'Heikin Ashi + PSAR + 200 EMA',
    triple_ema: 'Triple EMA', rsi_simple: 'RSI', supertrend: 'Supertrend', engulfing: 'Engulfing candle', darvas: 'Darvas Box',
    stoch_1r6: 'Stochastic, 1:6', stochrsi: 'Stochastic RSI', stoch_1r05: 'Stochastic, 1:0.5', awesome: 'Awesome Oscillator',
    ema_cross: 'EMA 9/21 crossover',
  },
  MARKETS: { all: 'All markets', crypto: 'Crypto', mt5: 'Gold · FX · indices', forex: 'Forex only' },
  MARKET_TAG: { crypto: '20 crypto perps', mt5: '14 MT5 markets', forex: '7 forex majors' },

  root() { return document.getElementById('ytcheck-root'); },
  resized: false,
  async load() {
    const root = this.root();
    if (!root || this.loading) return;
    if (!this.resized) { this.resized = true; window.addEventListener('resize', () => this.fitDetail()); }
    if (this.data) { this.render(); return; }
    this.loading = true;
    clear(root).append(h('div', { class: 'card' }, h('p', { class: 'sub', text: 'loading the strategy check…' })));
    try {
      const r = await fetch('/api/public/yt-strategies', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status === 503 ? 'the study has not been run on this server yet' : 'HTTP ' + r.status);
      this.data = await r.json();
      this.render();
    } catch (e) {
      clear(root).append(h('div', { class: 'card' }, h('p', { class: 'err', text: 'Strategy check unavailable: ' + e.message })));
    } finally { this.loading = false; }
  },

  /* ---- helpers ---- */
  r100(v) { return v == null || !isFinite(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + 'R'; },
  pct(v, d = 0) { return v == null || !isFinite(v) ? '—' : Number(v).toFixed(d) + '%'; },
  rr(t) { return t.map((x) => (x[0] < 1 ? '½ at ' : '') + '1:' + (+x[1].toFixed(2))).join(' + '); },
  breakeven(t) { const r = t.reduce((a, x) => a + x[0] * x[1], 0); return 100 / (1 + r); },   // gross, no costs
  tf(m) { return m >= 60 ? m / 60 + 'h' : m + 'm'; },
  rows() {
    const all = (this.data.strategies || []).filter((r) => this.market === 'all' || r.market === this.market);
    const net = (r) => r.y2.net.goa_per_100 ?? -1e9;
    return this.sort === 'net' ? all.slice().sort((a, b) => net(b) - net(a)) : all;      // 'claim' = the PDF's order
  },

  /* ---- the page: three sources, one tab bar ---- */
  source: 'yt', scribd: null, gold: null, swing: null, r3: null, r4: null, s2: null, r6: null, r7: null, sfilter: 'all', ssort: 'list', sopen: null, wopen: null,
  SOURCES: { ibs: 'IBS live bot', yt: 'YouTube ranking', scribd: 'Scribd scalping', swing: 'Scribd swing & trend', r3: 'Scribd round 3', r4: 'Scribd round 4', s2: 'Scalping round 2', r6: 'Scribd round 6', r7: 'Scribd round 7', gold: 'Gold 1-min PDF' },
  tabs() {
    return h('div', { class: 'yc-sources', role: 'tablist' }, ...Object.entries(this.SOURCES).map(([k, label]) =>
      h('button', { type: 'button', role: 'tab', 'aria-selected': String(this.source === k), class: this.source === k ? 'active' : '', text: label,
        onclick: () => { this.source = k; this.render(); } })));
  },
  async fetchOnce(key, url) {
    if (this[key]) return this[key];
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status === 503 ? 'not run on this server yet' : 'HTTP ' + r.status);
    this[key] = await r.json();
    return this[key];
  },
  render() {
    const root = this.root();
    if (!root) return;
    if (this.source === 'yt') return this.renderYt();
    if (this.source === 'ibs') {                     // the winner's bot (app/dashboard/ibsbot.js), live from the server
      const host = h('div', { class: 'yc-wrap' });
      clear(root).append(h('div', { class: 'yc-wrap' }, this.tabs(), host));
      IbsPanel.mount(host);
      return;
    }
    const key = this.source;
    const url = { scribd: '/api/public/scribd-strategies', swing: '/api/public/scribd-swing', r3: '/api/public/scribd-round3', r4: '/api/public/scribd-round4', s2: '/api/public/scribd-scalp2', r6: '/api/public/scribd-round6', r7: '/api/public/scribd-round7', gold: '/api/public/gold-scalp' }[key];
    if (!this[key]) {
      clear(root).append(h('div', { class: 'yc-wrap' }, this.tabs(), h('div', { class: 'card' }, h('p', { class: 'sub', text: 'loading…' }))));
      this.fetchOnce(key, url).then(() => this.render()).catch((e) => {
        clear(root).append(h('div', { class: 'yc-wrap' }, this.tabs(), h('div', { class: 'card' }, h('p', { class: 'err', text: 'Unavailable: ' + e.message }))));
      });
      return;
    }
    const y = window.scrollY;
    clear(root).append(key === 'scribd' ? this.renderScribd() : ['swing', 'r3', 'r4', 's2', 'r6', 'r7'].includes(key) ? this.renderSwing(key) : this.renderGold());
    window.scrollTo(0, y);
    this.fitDetail();
  },
  renderYt() {
    const root = this.root(), d = this.data;
    if (!root || !d) return;
    const rows = d.strategies || [];
    const proven = rows.filter((r) => r.verdict.startsWith('PROVEN'));
    const trades = rows.reduce((a, r) => a + (r.y1.net.trades || 0) + (r.y2.net.trades || 0), 0);
    const gaps = rows.map((r) => r.claim.win_rate_pct - (r.y2.gross.win_rate_pct ?? 0)).sort((a, b) => a - b);
    const gap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
    const assets = new Set(rows.flatMap((r) => r.assets)).size;
    const keys = new Set(rows.map((r) => r.key)).size;
    const kpi = (lbl, val, sub) => h('div', { class: 'yc-kpi' }, h('span', { class: 'lbl', text: lbl }), h('b', { text: val }), h('span', { class: 'sub', text: sub }));
    const hero = h('div', { class: 'yc-hero' },
      h('div', { class: 'yc-hero-l' },
        h('span', { class: 'yc-kicker', text: 'Strategy check · the YouTube "tested 100 times" ranking, on real markets' }),
        h('h2', {}, h('span', { class: 'yc-big ' + (proven.length ? 'up' : 'down'), text: String(proven.length) }),
          h('span', { text: ' of ' + rows.length + ' results pass' })),
        h('p', { class: 'sub', text: proven.length
          ? proven.length + ' result(s) passed the pre-registered rule: candidates for a forward paper test, not live bots yet.'
          : 'Before costs every strategy lands where a coin flip would for its reward:risk; after costs every one loses. The videos\' win rates were a median '
            + gap.toFixed(0) + ' points higher than measured.' })),
      h('div', { class: 'yc-kpis' },
        kpi('strategies', String(keys), 'from the PDF, rules fixed before the test'),
        kpi('markets', String(assets), '20 crypto · gold, silver, 7 FX, 5 indices'),
        kpi('trades measured', trades.toLocaleString(), 'two separate years'),
        kpi('claim vs measured', '+' + gap.toFixed(0) + ' pts', 'median win-rate overstatement')));
    const chips = h('div', { class: 'yc-filters', role: 'tablist' }, ...Object.entries(this.MARKETS).map(([k, label]) =>
      h('button', { type: 'button', role: 'tab', class: 'chip' + (this.market === k ? ' active' : ''), text: label,
        onclick: () => { this.market = k; this.open = null; this.render(); } })),
      h('label', { class: 'yc-sort' }, h('span', { class: 'sub', text: 'order' }),
        h('select', { onchange: (e) => { this.sort = e.target.value; this.render(); } },
          h('option', { value: 'claim', text: 'as ranked in the PDF', selected: this.sort === 'claim' }),
          h('option', { value: 'net', text: 'by year-2 net result', selected: this.sort === 'net' }))));
    const legend = h('div', { class: 'yc-legend' },
      h('span', {}, h('i', { class: 'yc-dot claim' }), 'claimed win rate (video)'),
      h('span', {}, h('i', { class: 'yc-dot meas' }), 'measured win rate (year 2, before costs)'),
      h('span', {}, h('i', { class: 'yc-tick' }), 'break-even for its reward:risk'),
      h('span', { class: 'sub', text: 'R per 100 trades = the videos\' "GOA" at 1% risk per trade' }));
    const head = h('tr', {},
      h('th', { text: 'Strategy' }), h('th', { class: 'yc-wrcol' }, this.axis()),
      h('th', { class: 'num', text: 'Claimed', title: 'the video\'s net R per 100 trades ("GOA")' }),
      h('th', { class: 'num', text: 'Before costs', title: 'year 2, gross R per 100 trades' }),
      h('th', { class: 'num', text: 'Net yr 1' }), h('th', { class: 'num', text: 'Net yr 2' }),
      h('th', { class: 'num', text: 'Trades' }), h('th', { text: 'Verdict' }));
    const body = h('tbody');
    for (const r of this.rows()) {
      const id = r.key + '|' + r.market;
      const tr = h('tr', { class: 'yc-row' + (this.open === id ? ' on' : ''), tabindex: 0, 'aria-expanded': String(this.open === id),
        onclick: () => { this.open = this.open === id ? null : id; this.render(); },
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.open = this.open === id ? null : id; this.render(); } } },
        h('td', {}, h('b', { text: this.NAMES[r.key] || r.key }),
          h('div', { class: 'sub' }, this.tf(r.tf_min) + ' · ' + this.rr(r.targets) + ' · ', h('span', { class: 'yc-mkt ' + r.market, text: this.MARKET_TAG[r.market] || r.market }))),
        h('td', { class: 'yc-wrcol' }, this.dumbbell(r)),
        h('td', { class: 'num', text: this.r100(r.claim.goa_per_100) }),
        h('td', { class: 'num', text: this.r100(r.y2.gross.goa_per_100) }),
        h('td', { class: 'num ' + this.cls(r.y1.net.goa_per_100), text: this.r100(r.y1.net.goa_per_100) }),
        h('td', { class: 'num ' + this.cls(r.y2.net.goa_per_100), text: this.r100(r.y2.net.goa_per_100) }),
        h('td', { class: 'num', text: (r.y2.net.trades || 0).toLocaleString() }),
        h('td', {}, r.verdict.startsWith('PROVEN') ? pill('PASS', 'up') : pill('FAIL', 'down')));
      body.append(tr);
      if (this.open === id) body.append(h('tr', { class: 'yc-detail-row' }, h('td', { colspan: 8 }, this.detail(r))));
    }
    const table = h('div', { class: 'yc-scroll' }, h('table', { class: 'yc-table' }, h('thead', {}, head), body));
    const m = d.markets || {};
    const notes = h('div', { class: 'card yc-notes' },
      h('h3', { text: 'How this was tested' }),
      h('ul', {},
        h('li', { text: 'The PDF gives each video\'s title, reward:risk, win rate and "GOA" but not its rules. Each strategy is written down in its standard published form (both directions, 200 EMA trend filter, the swing of the last 10 candles as the stop, the video\'s R multiple as the target), fixed before any result was seen.' }),
        h('li', { text: 'Pass rule, fixed in advance: profitable after costs in year 1 (PF ≥ 1.10) and year 2 (PF ≥ 1.15, ≥ 100 trades), the year-2 average above zero with 95% confidence, and positive on at least half the markets.' }),
        h('li', { text: 'Crypto: ' + ((m.crypto || {}).source || 'Binance USD-M perpetuals') + ', ' + ((m.crypto || {}).costs || '') + '.' }),
        h('li', { text: 'Gold · FX · indices: ' + ((m.mt5 || {}).source || 'MT5') + ', ' + ((m.mt5 || {}).costs || '') + '. Overnight swaps ignored.' }),
        h('li', { text: 'Only two videos name an asset ("Forex"): CMF + MACD + ATR and TTM Squeeze, tested on the 7 forex majors only.' }),
        h('li', { text: 'Windows: year 1 ' + (d.windows?.y1 || []).join(' → ') + ', year 2 ' + (d.windows?.y2 || []).join(' → ') + '. A signal fills at the next candle\'s open; a candle touching both stop and target is settled with 15-minute candles.' })),
      h('p', { class: 'sub', text: 'Pre-registration and full write-up: docs/YT_STRATEGIES_STUDY.md · generated ' + String(d.generated_utc || '').replace('T', ' ').slice(0, 16) + ' UTC · historical research, not advice' }));
    const y = window.scrollY;                                     // a re-render (filter, row opened) keeps the place
    clear(this.root()).append(h('div', { class: 'yc-wrap' }, this.tabs(), hero, h('div', { class: 'card yc-card' }, chips, legend, table), notes));
    window.scrollTo(0, y);
    this.fitDetail();
  },
  /** The opened row's detail spans the VISIBLE width of the (horizontally scrollable) table, pinned to its left edge. */
  fitDetail() {
    const sc = document.querySelector('#ytcheck-root .yc-scroll'), d = document.querySelector('#ytcheck-root .yc-detail');
    if (sc && d) d.style.width = Math.max(280, sc.clientWidth) + 'px';
  },
  cls(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; },

  /* ---- the win-rate dumbbell: one shared 0-100 % scale for every row ---- */
  W: 260, H: 26, PAD: 6,
  x(v) { return this.PAD + (Math.max(0, Math.min(100, v)) / 100) * (this.W - 2 * this.PAD); },
  axis() {
    const s = rsSvg('svg', { viewBox: `0 0 ${this.W} 16`, class: 'yc-axis', 'aria-hidden': 'true' });
    for (const v of [0, 25, 50, 75, 100]) s.append(rsSvg('text', { x: this.x(v), y: 12, 'text-anchor': v === 0 ? 'start' : v === 100 ? 'end' : 'middle' }, v + '%'));
    return h('div', { class: 'yc-axis-wrap' }, h('span', { class: 'yc-axis-lbl', text: 'Win rate' }), s);
  },
  dumbbell(r) {
    const c = r.claim.win_rate_pct, m = r.y2.gross.win_rate_pct, be = this.breakeven(r.targets);
    const s = rsSvg('svg', { viewBox: `0 0 ${this.W} ${this.H}`, class: 'yc-db', role: 'img',
      'aria-label': `claimed ${c}% win rate, measured ${m == null ? 'none' : m.toFixed(1) + '%'}, break-even ${be.toFixed(0)}%` });
    const y = this.H / 2;
    for (const v of [25, 50, 75]) s.append(rsSvg('line', { x1: this.x(v), x2: this.x(v), y1: 3, y2: this.H - 3, class: 'yc-grid' }));
    s.append(rsSvg('line', { x1: this.x(be), x2: this.x(be), y1: 2, y2: this.H - 2, class: 'yc-be' }));
    if (m != null) {
      s.append(rsSvg('line', { x1: this.x(Math.min(c, m)), x2: this.x(Math.max(c, m)), y1: y, y2: y, class: 'yc-gap' }));
      s.append(rsSvg('circle', { cx: this.x(m), cy: y, r: 5, class: 'yc-meas' }));
    }
    s.append(rsSvg('circle', { cx: this.x(c), cy: y, r: 5, class: 'yc-claim' }));
    const tip = `Claimed ${c}% · measured ${m == null ? '—' : m.toFixed(1) + '%'} (year 2, ${(r.y2.gross.trades || 0).toLocaleString()} trades) · break-even ${be.toFixed(0)}%`;
    s.addEventListener('mousemove', (e) => this.tip(e, tip));
    s.addEventListener('mouseleave', () => this.tip(null));
    return s;
  },
  tip(e, text) {
    let t = document.getElementById('yc-tip');
    if (!t) { t = h('div', { id: 'yc-tip', class: 'yc-tip', role: 'tooltip' }); document.body.append(t); }
    if (!e) { t.hidden = true; return; }
    t.textContent = text; t.hidden = false;
    const x = Math.min(window.innerWidth - t.offsetWidth - 8, e.clientX + 14);
    t.style.left = Math.max(8, x) + 'px'; t.style.top = (e.clientY + 16) + 'px';
  },

  /* ---- one result opened: the rule, the four checks, every market ---- */
  detail(r) {
    const names = { y1_positive: 'Year 1 profitable after costs (PF ≥ 1.10)', y2_positive: 'Year 2 profitable after costs (PF ≥ 1.15, ≥ 100 trades)',
      y2_ci_above_zero: 'Year 2 average above zero with 95% confidence', y2_half_the_assets: 'Year 2 positive on at least half the markets' };
    const checks = h('ul', { class: 'yc-checks' }, ...Object.entries(r.checks).map(([k, ok]) =>
      h('li', { class: ok ? 'ok' : 'no' }, h('span', { class: 'yc-ck', 'aria-hidden': 'true', text: ok ? '✓' : '✗' }),
        h('span', { text: (names[k] || k) + (ok ? '' : ' — failed') }))));
    const by = Object.entries(r.y2.by_asset_net_r || {}).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
    const maxAbs = Math.max(1, ...by.map(([, v]) => Math.abs(v)));
    const bars = h('div', { class: 'yc-bars' }, ...by.map(([a, v]) => h('div', { class: 'yc-bar-row', title: `${a}: ${this.r100(v)} net in year 2` },
      h('span', { class: 'yc-bar-lbl', text: a }),
      h('span', { class: 'yc-bar-track' }, h('i', { class: 'yc-bar ' + (v >= 0 ? 'up' : 'down'),
        style: `width:${(Math.abs(v) / maxAbs) * 50}%;${v >= 0 ? 'left:50%' : `right:50%`}` })),
      h('span', { class: 'num ' + this.cls(v), text: this.r100(v) }))));
    const g = r.y2.gross, n = r.y2.net;
    return h('div', { class: 'yc-detail' },
      h('div', {},
        h('p', { class: 'yc-title', text: '“' + r.title + '”' }),
        h('p', {}, h('b', { text: 'Rule tested: ' }), r.rule),
        h('p', { class: 'sub', text: `Year 2: ${(n.trades || 0).toLocaleString()} trades · win rate ${this.pct(g.win_rate_pct, 1)} before costs, ${this.pct(n.win_rate_pct, 1)} after · profit factor ${n.profit_factor == null ? '—' : n.profit_factor.toFixed(2)} · average cost ${(n.mean_cost_r ?? 0).toFixed(2)}R per trade · median stop ${(n.median_risk_pct ?? 0).toFixed(2)}% away` }),
        checks),
      h('div', {}, h('p', { class: 'sub', text: 'Year 2 net result by market (total R)' }), bars));
  },

  /* ---- Scribd scalping: before costs vs after costs per rule (% of the price per trade) ---- */
  docTitle(doc) { return doc.replace(/^\d{6,}(\s*\/\s*\d{6,})*\s+/, '').replace(/\s*\(\+\d{6,}\)/, ''); },
  smarket(r) { return r.assets.includes('XAUUSD') ? 'gold' : (r.assets.includes('BTC') ? 'crypto' : 'forex'); },
  SMARKETS: { all: 'All', forex: 'Forex', gold: 'Gold', crypto: 'Crypto' },
  spct(v, d = 4) { return v == null || !isFinite(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) + '%'; },
  renderScribd() {
    const d = this.scribd, all = d.strategies || [];
    const passed = all.filter((r) => r.verdict.startsWith('PASS'));
    const prelim = all.filter((r) => r.window_set !== 'full').length;
    const trades = all.reduce((a, r) => a + (r.a.trades || 0) + (r.b.trades || 0), 0);
    const grossB = all.map((r) => r.gross?.b?.mean_ret_pct).filter((v) => v != null && isFinite(v) && Math.abs(v) < 0.5);
    const edge = grossB.length ? Math.max(...grossB.map(Math.abs)) : 0;
    const kpi = (lbl, val, sub) => h('div', { class: 'yc-kpi' }, h('span', { class: 'lbl', text: lbl }), h('b', { text: val }), h('span', { class: 'sub', text: sub }));
    const hero = h('div', { class: 'yc-hero' },
      h('div', { class: 'yc-hero-l' },
        h('span', { class: 'yc-kicker', text: 'Strategy check · scalping strategies from Scribd, on the markets they name' }),
        h('h2', {}, h('span', { class: 'yc-big ' + (passed.length ? 'up' : 'down'), text: String(passed.length) }), h('span', { text: ' of ' + all.length + ' rules pass' })),
        h('p', { class: 'sub', text: 'Before costs every rule sits at zero: within ±' + edge.toFixed(3) + '% of the price per trade. The spread, commission and slippage then make every one of them lose. '
          + (prelim ? prelim + ' of the verdicts are preliminary (1-minute and 5-minute history is capped in MT5).' : 'Every rule is judged on two full years.') })),
      h('div', { class: 'yc-kpis' },
        kpi('documents read', '≈54', 'Scribd "scalping strategy" + "gold scalping strategy"'),
        kpi('rules tested', String(all.length), 'mechanical rules, fixed before the test'),
        kpi('trades measured', trades.toLocaleString(), 'two windows per rule'),
        kpi('best before costs', '±' + edge.toFixed(3) + '%', 'per trade, second window')));
    const rows = all.filter((r) => this.sfilter === 'all' || this.smarket(r) === this.sfilter);
    const net = (r) => r.b.mean_ret_pct ?? -1e9;
    const list = this.ssort === 'net' ? rows.slice().sort((a, b) => net(b) - net(a)) : rows;
    // a shared axis around zero: the largest |value| among the shown rows, clipped to 0.06 % (crypto costs run off it)
    const vals = list.flatMap((r) => [r.gross?.b?.mean_ret_pct, r.b.mean_ret_pct]).filter((v) => v != null && isFinite(v));
    const lim = Math.min(0.06, Math.max(0.01, ...vals.map(Math.abs)));
    const chips = h('div', { class: 'yc-filters', role: 'tablist' }, ...Object.entries(this.SMARKETS).map(([k, label]) =>
      h('button', { type: 'button', role: 'tab', class: 'chip' + (this.sfilter === k ? ' active' : ''), text: label,
        onclick: () => { this.sfilter = k; this.sopen = null; this.render(); } })),
      h('label', { class: 'yc-sort' }, h('span', { class: 'sub', text: 'order' }),
        h('select', { onchange: (e) => { this.ssort = e.target.value; this.render(); } },
          h('option', { value: 'list', text: 'as read', selected: this.ssort === 'list' }),
          h('option', { value: 'net', text: 'by result after costs', selected: this.ssort === 'net' }))));
    const legend = h('div', { class: 'yc-legend' },
      h('span', {}, h('i', { class: 'yc-dot claim' }), 'before costs (price move only)'),
      h('span', {}, h('i', { class: 'yc-dot meas' }), 'after costs (spread, commission, slippage)'),
      h('span', {}, h('i', { class: 'yc-tick' }), 'zero'),
      h('span', { class: 'sub', text: 'average per trade in the second window, % of the price · axis ±' + lim.toFixed(3) + '%' }));
    const body = h('tbody');
    for (const r of list) {
      const id = r.key, open = this.sopen === id;
      const toggle = () => { this.sopen = open ? null : id; this.render(); };
      body.append(h('tr', { class: 'yc-row' + (open ? ' on' : ''), tabindex: 0, 'aria-expanded': String(open), onclick: toggle,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } } },
        h('td', {}, h('b', { text: this.docTitle(r.doc) }),
          h('div', { class: 'sub' }, this.tf(r.tf_min) + ' · ', h('span', { class: 'yc-mkt ' + (this.smarket(r) === 'gold' ? 'forex' : ''), text: r.assets.length > 3 ? r.assets.length + ' ' + this.smarket(r) + ' markets' : r.assets.join(', ') }))),
        h('td', { class: 'yc-wrcol' }, this.zeroPlot(r.gross?.b?.mean_ret_pct, r.b.mean_ret_pct, lim)),
        h('td', { class: 'num ' + this.cls(r.a.mean_ret_pct), text: this.spct(r.a.mean_ret_pct) }),
        h('td', { class: 'num ' + this.cls(r.b.mean_ret_pct), text: this.spct(r.b.mean_ret_pct) }),
        h('td', { class: 'num', text: (r.a.profit_factor ?? 0).toFixed(2) + ' / ' + (r.b.profit_factor ?? 0).toFixed(2) }),
        h('td', { class: 'num', text: (r.b.trades || 0).toLocaleString() }),
        h('td', {}, r.verdict.startsWith('PASS') ? pill('PASS', 'up') : pill('FAIL', 'down'), r.window_set !== 'full' ? h('span', { class: 'yc-pre', text: 'prelim' }) : null)));
      if (open) body.append(h('tr', { class: 'yc-detail-row' }, h('td', { colspan: 7 }, this.scribdDetail(r))));
    }
    const head = h('tr', {}, h('th', { text: 'Rule (Scribd document)' }),
      h('th', { class: 'yc-wrcol' }, h('div', { class: 'yc-axis-wrap' }, h('span', { class: 'yc-axis-lbl', text: 'Per trade' }), this.zeroAxis(lim))),
      h('th', { class: 'num', text: 'Net A' }), h('th', { class: 'num', text: 'Net B' }), h('th', { class: 'num', text: 'PF A / B' }),
      h('th', { class: 'num', text: 'Trades B' }), h('th', { text: 'Verdict' }));
    const table = h('div', { class: 'yc-scroll' }, h('table', { class: 'yc-table' }, h('thead', {}, head), body));
    const notes = h('div', { class: 'card yc-notes' }, h('h3', { text: 'How this was tested' }), h('ul', {},
      h('li', { text: 'About 54 documents from Scribd searches "scalping strategy" and "gold scalping strategy" were read; full books, image-only files, rules needing paid indicators or robots, pure advice and duplicates were left out. Rules judged by eye (hand-drawn levels, trendlines, order blocks) are not tested rather than guessed.' }),
      h('li', { text: 'Each rule is tested only on the market its document names: the forex majors (or the named pairs), gold, Bitcoin or crypto. Rules were written down before any test; a document without an exit uses its own opposite signal.' }),
      h('li', { text: 'Costs: ' + (d.costs?.mt5 || '') + ' (forex, gold); ' + (d.costs?.crypto || '') + ' (crypto). Inside a candle the price takes the standard neutral path (green: open-low-high-close, red: open-high-low-close).' }),
      h('li', { text: 'Pass rule, fixed in advance: profitable after costs in the first window (PF ≥ 1.10) and the second (PF ≥ 1.15, enough trades), the second-window average above zero with 95% confidence, and positive on at least half its markets.' }),
      h('li', { text: prelim ? '15-minute and hourly rules use two full years. ' + prelim + ' 1-minute / 5-minute rules are PRELIMINARY (MT5 history cap).' : 'Every rule: year 1 2024-10-20 → 2025-10-20 and year 2 2025-10-20 → 2026-10-20 (two full years of 1m/5m history from MT5).' })),
      h('p', { class: 'sub', text: 'Pre-registration and write-up: ' + d.pre_registration + ' · generated ' + String(d.generated_utc || '').replace('T', ' ').slice(0, 16) + ' UTC · historical research, not advice' }));
    return h('div', { class: 'yc-wrap' }, this.tabs(), hero, h('div', { class: 'card yc-card' }, chips, legend, table), notes);
  },
  zx(v, lim) { return this.PAD + ((Math.max(-lim, Math.min(lim, v)) + lim) / (2 * lim)) * (this.W - 2 * this.PAD); },
  zeroAxis(lim) {
    const s = rsSvg('svg', { viewBox: `0 0 ${this.W} 16`, class: 'yc-axis', 'aria-hidden': 'true' });
    for (const v of [-lim, -lim / 2, 0, lim / 2, lim]) s.append(rsSvg('text', { x: this.zx(v, lim), y: 12, 'text-anchor': v === -lim ? 'start' : v === lim ? 'end' : 'middle' },
      (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')));
    return s;
  },
  zeroPlot(g, n, lim) {
    const s = rsSvg('svg', { viewBox: `0 0 ${this.W} ${this.H}`, class: 'yc-db', role: 'img',
      'aria-label': `before costs ${this.spct(g)} per trade, after costs ${this.spct(n)}` });
    const y = this.H / 2;
    s.append(rsSvg('line', { x1: this.zx(0, lim), x2: this.zx(0, lim), y1: 2, y2: this.H - 2, class: 'yc-be' }));
    if (g != null && n != null && isFinite(g) && isFinite(n)) {
      s.append(rsSvg('line', { x1: this.zx(Math.min(g, n), lim), x2: this.zx(Math.max(g, n), lim), y1: y, y2: y, class: 'yc-gap' }));
      s.append(rsSvg('circle', { cx: this.zx(n, lim), cy: y, r: 5, class: 'yc-meas' }));
      if (Math.abs(n) > lim) s.append(rsSvg('text', { x: n < 0 ? this.PAD + 9 : this.W - this.PAD - 9, y: y + 4, 'text-anchor': n < 0 ? 'start' : 'end', class: 'yc-clip' }, this.spct(n, 2)));
    }
    if (g != null && isFinite(g)) s.append(rsSvg('circle', { cx: this.zx(g, lim), cy: y, r: 5, class: 'yc-claim' }));
    const tip = `Before costs ${this.spct(g)} · after costs ${this.spct(n)} per trade (second window)`;
    s.addEventListener('mousemove', (e) => this.tip(e, tip));
    s.addEventListener('mouseleave', () => this.tip(null));
    return s;
  },
  scribdDetail(r) {
    const names = { a_positive: 'First window profitable after costs (PF ≥ 1.10)', b_positive: 'Second window profitable after costs (PF ≥ 1.15, enough trades)',
      b_ci_above_zero: 'Second-window average above zero with 95% confidence', b_half_the_assets: 'Second window positive on at least half its markets' };
    const checks = h('ul', { class: 'yc-checks' }, ...Object.entries(r.checks).map(([k, ok]) =>
      h('li', { class: ok ? 'ok' : 'no' }, h('span', { class: 'yc-ck', 'aria-hidden': 'true', text: ok ? '✓' : '✗' }), h('span', { text: (names[k] || k) + (ok ? '' : ' — failed') }))));
    const by = Object.entries(r.b.by_asset_ret_pct || {}).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
    const maxAbs = Math.max(0.001, ...by.map(([, v]) => Math.abs(v)));
    const bars = h('div', { class: 'yc-bars' }, ...by.map(([a, v]) => h('div', { class: 'yc-bar-row', title: `${a}: ${v.toFixed(2)}% summed over the second window` },
      h('span', { class: 'yc-bar-lbl', text: a }),
      h('span', { class: 'yc-bar-track' }, h('i', { class: 'yc-bar ' + (v >= 0 ? 'up' : 'down'), style: `width:${(Math.abs(v) / maxAbs) * 50}%;${v >= 0 ? 'left:50%' : 'right:50%'}` })),
      h('span', { class: 'num ' + this.cls(v), text: (v > 0 ? '+' : '') + v.toFixed(2) + '%' }))));
    const id = (r.doc.match(/^(\d+)/) || r.doc.match(/\((\d{6,})\)/) || [])[1];
    const w = r.windows || {};
    return h('div', { class: 'yc-detail' },
      h('div', {},
        h('p', { class: 'yc-title', text: '“' + this.docTitle(r.doc) + '”' }),
        h('p', {}, h('b', { text: 'Rule tested: ' }), r.rule.replace(/\s+/g, ' ')),
        r.note ? h('p', { class: 'sub', text: 'Note: ' + r.note }) : null,
        h('p', { class: 'sub', text: `Window A ${(w.a || []).join(' → ')} · window B ${(w.b || []).join(' → ')} ${r.cost_per_side_pct != null ? '· cost ' + r.cost_per_side_pct + '% per side ' : ''}· second window: ${(r.b.trades || 0).toLocaleString()} trades, win rate ${this.pct(r.b.win_rate_pct, 1)}` }),
        checks,
        id ? h('p', { class: 'sub' }, 'Source: ', h('a', { href: 'https://www.scribd.com/document/' + id, target: '_blank', rel: 'noopener noreferrer', text: 'Scribd document ' + id })) : null),
      h('div', {}, h('p', { class: 'sub', text: 'Second window, net result summed per market (% of the price)' }), bars));
  },

  /* ---- Scribd round 2: swing, trend and London-session rules over up to 12 years ---- */
  RULE_TABS: {
    swing: {
      kicker: 'Strategy check · swing, trend and London-session strategies from Scribd, 2015 → 2026',
      docs: ['≈30', 'Turtle, RSI-2, Holy Grail, London breakouts…'], history: ['2015 → 2026', 'daily; London rules from 2020 (1-minute)'],
      notes: [
        'Scribd searches for the Turtle rules, RSI-2, the Holy Grail, London breakouts, mean reversion, moving-average and opening-range systems; about 30 documents opened. Rules judged by eye, rules without exits and self-declared incomplete ones are not tested.',
        'Each rule runs only on the market its document names: US500 for the S&P 500 rules, the GBP pairs for the London breakout, the 30 Dow stocks for the stock rules, every MT5 market for the futures-based Turtles.',
        'Daily rules: window A 2015 → 2020, window B 2021 → 2026 (MT5 daily history; stocks from Yahoo). London-session rules: your broker keeps 1-minute history only from late 2020, so they split 2020-11 → 2023-11 and 2023-11 → 2026-10.'],
      pass: 'PASS* = passed on a survivorship-biased stock list, so it does not count.' },
    r3: {
      kicker: 'Strategy check · NR7, Internal Bar Strength, an SPY intraday-momentum paper and a Bitcoin thesis from Scribd',
      docs: ['≈20', 'NR7, IBS, Zarattini et al., ETH Zurich thesis…'], history: ['2000 → 2026', 'ETFs daily; BTC 2019 →; US500 1-minute 2020 →'],
      notes: [
        'Scribd searches for Connors, Larry Williams, NR7 / narrow range, Bitcoin back-tests, Internal Bar Strength, gold swing and Ichimoku systems; about 20 documents opened. A paid 49-page manual, rules without parameters, order-flow judgement and scrambled formulas are not tested.',
        'Each rule runs only on the market its document names: the forex majors, the SPY/QQQ/DIA/IWM index ETFs, the Nifty 50, SPY via your broker\'s US500 CFD, Bitcoin, SPY and the Singapore ETF, and QQQ (identified from the article\'s own price example).',
        'Windows: ETFs and Nifty 2000 → 2012 vs 2013 → 2026; forex 2015 → 2020 vs 2021 → 2026; US500 1-minute 2020-11 → 2023-11 vs 2023-11 → 2026-10; Bitcoin 2019-06 → 2022 vs 2023 → 2026 (all after the thesis was written).'],
      pass: '' },
    r4: {
      kicker: 'Strategy check · the highest-return claims on Scribd: monthly ETF rotation and momentum systems',
      docs: ['≈15', 'Global Market Rotation, GEM, bond rotation, Alvarez…'], history: ['2007 → 2026', 'split at each article\'s publication'],
      notes: [
        'Scribd searches for dual momentum, ETF rotation, leveraged-ETF, Connors and crypto trend systems; about 15 documents opened. Discretionary rules, an instrument that no longer exists (ZIV), an unreadable file and books are not tested.',
        'Monthly rules: signals on month-end adjusted closes, trades at the next open, one trade = one month. Each rule\'s history is split at its publication date where the data allows, so window B is out-of-sample.',
        'Next to the verdict: compounded annual return (CAGR), maximum drawdown and buy-and-hold SPY over the same months.'],
      pass: '' },
    s2: {
      kicker: 'Strategy check · scalping round 2: new Scribd scalping documents on six years of your broker’s 1-minute data',
      docs: ['≈25', 'VWAP bands, 4H VWAP, GBPJPY 10 pips, SMB VWAP flip…'], history: ['2020 → 2026', '1-minute MT5 history'],
      notes: [
        'Scribd searches "1 minute scalping strategy", "NAS100 US30 scalping strategy", "crypto scalping strategy" and "VWAP scalping strategy"; only documents not read in earlier rounds. Private or paid indicators, order-flow judgement, books and markets without data are not tested.',
        'Signals on completed 1/3/5/15-minute candles; every fill, stop, target, partial and breakeven is walked on 1-minute candles. One position at a time per market; a document naming no market is tested on the 7 FX majors.',
        'Windows: A 2020-11 → 2023-11, B 2023-11 → 2026-10 (your broker keeps real 1-minute data only from late 2020).'],
      pass: '' },
    r6: {
      kicker: 'Strategy check · round 6: the index mean-reversion family (the only edge found so far), tested after publication',
      docs: ['≈20', 'Pagonidis IBS paper, Katsanos Stiffness, TSI, MR Swing…'], history: ['1996 → 2026', 'split at each paper’s date'],
      notes: [
        'Scribd searches for SPY mean reversion, Williams %R, ConnorsRSI, QQQ swing, VIX and buy-the-dip systems; about 20 documents opened. Papers without exact parameters (TSI), systems not reproducible from their own text (MR Swing), paid courses and image-only files are not tested.',
        'Pagonidis’ rules run on his own 31 surviving ETFs: window A is his sample (to 2012), window B is everything after his paper. Stiffness runs on the Dow 30 (survivorship-biased) split at its 2018 article.',
        'Next to the verdict: a comparison with random entry days and, for the RSI(3)+IBS rule, its QQQ-only result and its signals traded in TQQQ.'],
      pass: 'PASS* = passed on a survivorship-biased stock list, so it does not count.' },
    r7: {
      kicker: 'Strategy check · round 7: inverse-volatility shorts, calendar seasonality and the lunar effect',
      docs: ['≈15', 'Grossmann VXX, Fosback, Kaeppel, Yuan–Zheng–Zhu…'], history: ['1998 → 2026', 'split at each document’s date'],
      notes: [
        'Scribd searches for seasonality, gap trading, pairs trading and VXX volatility systems; about 15 documents opened. Rules needing VIX-futures term-structure history, option courses, discretionary gap and COT methods and image-only files are not tested.',
        'VXX rules run on VIXY (the same VIX short-term futures index; the short borrow fee is not counted). Seasonal rules run on SPY and DIA, the lunar rule on the 31 country and index ETFs.',
        'Next to the verdict: compounded return and drawdown against buy and hold, a comparison with random entry days, and the seasonal days traded in 3× ETFs (UPRO, UDOW).'],
      pass: '' },
  },
  renderSwing(key = 'swing') {
    const d = this[key], all = d.rules || [], cfg = this.RULE_TABS[key];
    const passed = all.filter((r) => r.verdict === 'PASS');
    const trades = all.reduce((a, r) => a + (r.a.trades || 0) + (r.b.trades || 0), 0);
    const kpi = (lbl, val, sub) => h('div', { class: 'yc-kpi' }, h('span', { class: 'lbl', text: lbl }), h('b', { text: val }), h('span', { class: 'sub', text: sub }));
    const hero = h('div', { class: 'yc-hero' },
      h('div', { class: 'yc-hero-l' },
        h('span', { class: 'yc-kicker', text: cfg.kicker }),
        h('h2', {}, h('span', { class: 'yc-big ' + (passed.length ? 'up' : 'down'), text: String(passed.length) }), h('span', { text: ' of ' + all.length + ' rules pass' })),
        h('p', { class: 'sub', text: d.headline || '' })),
      h('div', { class: 'yc-kpis' },
        kpi('documents read', cfg.docs[0], cfg.docs[1]),
        kpi('rules tested', String(all.length), 'mechanical rules, fixed before the test'),
        kpi('trades measured', trades.toLocaleString(), 'two windows per rule'),
        kpi('history', cfg.history[0], cfg.history[1])));
    const vals = all.flatMap((r) => [r.gross?.b?.mean_ret_pct, r.b.mean_ret_pct]).filter((v) => v != null && isFinite(v));
    const lim = Math.min(1.5, Math.max(0.05, ...vals.map(Math.abs)));
    const legend = h('div', { class: 'yc-legend' },
      h('span', {}, h('i', { class: 'yc-dot claim' }), 'before costs (price move only)'),
      h('span', {}, h('i', { class: 'yc-dot meas' }), 'after costs'),
      h('span', {}, h('i', { class: 'yc-tick' }), 'zero'),
      h('span', { class: 'sub', text: 'average per trade in the second window, % of the price · axis ±' + lim.toFixed(2) + '%' }));
    const body = h('tbody');
    for (const r of all) {
      const id = key + ':' + r.key, open = this.wopen === id;
      const toggle = () => { this.wopen = open ? null : id; this.render(); };
      const counted = r.verdict === 'PASS', soft = r.verdict.startsWith('PASS') && !counted;
      body.append(h('tr', { class: 'yc-row' + (open ? ' on' : ''), tabindex: 0, 'aria-expanded': String(open), onclick: toggle,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } } },
        h('td', {}, h('b', { text: this.docTitle(r.doc).replace(/\s*\(\d{6,}\)/, '') }),
          h('div', { class: 'sub' }, r.tf + ' · ', h('span', { class: 'yc-mkt', text: r.assets.length > 3 ? r.assets.length + ' markets' : r.assets.join(', ') }))),
        h('td', { class: 'yc-wrcol' }, this.zeroPlot(r.gross?.b?.mean_ret_pct, r.b.mean_ret_pct, lim)),
        h('td', { class: 'num ' + this.cls(r.a.mean_ret_pct), text: this.spct(r.a.mean_ret_pct, 3) }),
        h('td', { class: 'num ' + this.cls(r.b.mean_ret_pct), text: this.spct(r.b.mean_ret_pct, 3) }),
        h('td', { class: 'num', text: (r.a.profit_factor ?? 0).toFixed(2) + ' / ' + (r.b.profit_factor ?? 0).toFixed(2) }),
        h('td', { class: 'num', text: (r.b.trades || 0).toLocaleString() }),
        h('td', {}, counted ? pill('PASS', 'up') : soft ? pill('PASS*', 'warn') : pill('FAIL', 'down'))));
      if (open) body.append(h('tr', { class: 'yc-detail-row' }, h('td', { colspan: 7 }, this.swingDetail(r))));
    }
    const head = h('tr', {}, h('th', { text: 'Rule (Scribd document)' }),
      h('th', { class: 'yc-wrcol' }, h('div', { class: 'yc-axis-wrap' }, h('span', { class: 'yc-axis-lbl', text: 'Per trade' }), this.zeroAxis(lim))),
      h('th', { class: 'num', text: 'Net A' }), h('th', { class: 'num', text: 'Net B' }), h('th', { class: 'num', text: 'PF A / B' }),
      h('th', { class: 'num', text: 'Trades B' }), h('th', { text: 'Verdict' }));
    const table = h('div', { class: 'yc-scroll' }, h('table', { class: 'yc-table' }, h('thead', {}, head), body));
    const notes = h('div', { class: 'card yc-notes' }, h('h3', { text: 'How this was tested' }), h('ul', {},
      ...cfg.notes.map((t) => h('li', { text: t })),
      h('li', { text: 'Costs per side: ' + Object.entries(d.costs || {}).map(([k, v]) => k + ' ' + v).join('; ') + '.' }),
      h('li', { text: 'Pass rule, fixed in advance: profitable after costs in window A (PF ≥ 1.10) and window B (PF ≥ 1.15, ≥ 100 trades, ≥ 30 for single-market documents), the window-B average above zero with 95% confidence, and positive on at least half its markets. ' + cfg.pass })),
      h('p', { class: 'sub', text: 'Pre-registration and write-up: ' + d.pre_registration + ' · generated ' + String(d.generated_utc || '').replace('T', ' ').slice(0, 16) + ' UTC · historical research, not advice' }));
    const lev = key === 'r4' ? this.leverageCard(d.ibs_qqq_leverage_sensitivity) : null;
    return h('div', { class: 'yc-wrap' }, this.tabs(), hero, h('div', { class: 'card yc-card' }, legend, table), lev, notes);
  },
  leverageCard(s) {
    if (!s || !s.TQQQ) return null;
    const row = (lbl, x) => h('tr', {}, h('td', { text: lbl }), h('td', { class: 'num', text: (x.trades || 0).toLocaleString() }),
      h('td', { class: 'num ' + this.cls(x.cagr_pct), text: this.spct(x.cagr_pct, 1) }), h('td', { class: 'num', text: (x.max_dd_pct ?? 0).toFixed(1) + '%' }),
      h('td', { class: 'num down', text: this.spct(x.worst_trade_pct, 1) }), h('td', { class: 'num', text: this.spct(x.total_pct, 0) }));
    return h('div', { class: 'card yc-card' }, h('h3', { text: 'Highest return so far: round 3\'s IBS rule with leverage (not a verdict)' }),
      h('p', { class: 'sub', text: 'Signals from QQQ (IBS < 0.10 → buy the close; exit at the first close above the previous day\'s high), trades taken in QQQ or in TQQQ (3× QQQ), 2010-03 → 2026-10, full capital per trade. Drawdown measured at trade closes. QQQ buy and hold: ' + this.spct(s.buy_and_hold_QQQ_cagr_pct, 1) + ' a year; TQQQ buy and hold: about +41% a year with an 82% drawdown.' }),
      h('div', { class: 'yc-scroll' }, h('table', { class: 'yc-table' },
        h('thead', {}, h('tr', {}, ...['Traded in', 'Trades', 'CAGR', 'Max drawdown', 'Worst trade', 'Total'].map((t, i) => h('th', { class: i ? 'num' : '', text: t })))),
        h('tbody', {}, row('QQQ', s.QQQ), row('TQQQ (3×)', s.TQQQ)))));
  },
  swingDetail(r) {
    const base = this.scribdDetail({ ...r, cost_per_side_pct: null });
    const extra = [];
    const bh = r.buy_and_hold_pct;
    if (bh) extra.push(h('p', { class: 'sub', text: `${r.assets[0] === 'US500' || r.key.startsWith('rsi2') || r.key.startsWith('r2_') ? 'US500' : r.assets[0]} buy and hold over the same windows: A ${this.spct(bh.a, 1)}, B ${this.spct(bh.b, 1)}.` }));
    const rt = r.random_timing;
    if (rt) extra.push(h('p', { class: 'sub', text: 'Timing check (not part of the verdict): the rule vs random entry days with the same holding times — '
      + ['a', 'b'].filter((w) => rt[w]).map((w) => `window ${w.toUpperCase()}: ${this.spct(rt[w].rule_mean_pct, 2)} vs ${this.spct(rt[w].random_mean_pct, 2)} (edge ${this.spct(rt[w].edge_pct, 2)}, z ${rt[w].z.toFixed(1)})`).join(' · ') }));
    if (r.swap_sensitivity) extra.push(h('p', { class: 'sub', text: r.swap_sensitivity.text }));
    if (r.curve && r.curve.a?.rule) {                       // round 7: {rule, buy_and_hold, time_in_market_pct} per window
      const c = r.curve, f = (w) => {
        const x = c[w] || {}, bh = x.buy_and_hold;
        return `window ${w.toUpperCase()} ${this.spct(x.rule?.cagr_pct, 1)} a year (max drawdown ${(x.rule?.max_dd_pct ?? 0).toFixed(1)}%)`
          + (bh ? ` vs buy and hold ${this.spct(bh.cagr_pct, 1)} (drawdown ${(bh.max_dd_pct ?? 0).toFixed(1)}%), in the market ${(x.time_in_market_pct ?? 0).toFixed(0)}% of days` : '');
      };
      extra.push(h('p', {}, h('b', { text: 'Compounded: ' }), f('a') + ' · ' + f('b') + '.'));
      if (r.note) extra.push(h('p', { class: 'sub', text: r.note }));
    } else if (r.curve) {
      const c = r.curve, sp = r.spy_same_months || {};
      extra.push(h('p', {}, h('b', { text: 'Compounded: ' }), `window A ${this.spct(c.a?.cagr_pct, 1)} a year (max drawdown ${(c.a?.max_dd_pct ?? 0).toFixed(1)}%) vs SPY ${this.spct(sp.a?.cagr_pct, 1)} · window B ${this.spct(c.b?.cagr_pct, 1)} a year (max drawdown ${(c.b?.max_dd_pct ?? 0).toFixed(1)}%) vs SPY ${this.spct(sp.b?.cagr_pct, 1)} (drawdown ${(sp.b?.max_dd_pct ?? 0).toFixed(1)}%).`));
      if (r.claim) extra.push(h('p', { class: 'sub', text: 'The document claims: ' + r.claim }));
    }
    const rb = r.robustness;
    if (rb) {
      const f = (s) => s && s.trades ? `${s.trades} trades, ${this.spct(s.mean_ret_pct, 2)} per trade, PF ${(s.profit_factor ?? 0).toFixed(2)}` : '—';
      extra.push(h('p', { class: 'sub', text: 'Robustness (not part of the verdict): since the article (2016-02 →) ' + f(rb.qqq_since_article_2016_02)
        + ' · SPY A/B PF ' + (rb.SPY?.a?.profit_factor ?? 0).toFixed(2) + ' / ' + (rb.SPY?.b?.profit_factor ?? 0).toFixed(2)
        + ' · DIA ' + (rb.DIA?.a?.profit_factor ?? 0).toFixed(2) + ' / ' + (rb.DIA?.b?.profit_factor ?? 0).toFixed(2)
        + ' · IWM ' + (rb.IWM?.a?.profit_factor ?? 0).toFixed(2) + ' / ' + (rb.IWM?.b?.profit_factor ?? 0).toFixed(2)
        + ' · your NAS100 CFD 2015 → 2026 with swap: ' + f(rb.NAS100_cfd_2015_2026?.with_swap) + ' · US500 CFD with swap: ' + f(rb.US500_cfd_2015_2026?.with_swap) }));
    }
    if (r.sensitivity_tp) extra.push(h('p', { class: 'sub', text: 'Target sensitivity (window B): 30 pips ' + this.spct(r.sensitivity_tp['30']?.b?.mean_ret_pct, 4) + ', 50 pips ' + this.spct(r.sensitivity_tp['50']?.b?.mean_ret_pct, 4) + ' per trade.' }));
    const lv = r.leverage_2010_2026;
    if (lv && lv.etf) extra.push(h('p', { class: 'sub', text: `The same days traded in ${lv.etf} (3×), 2010 → 2026: ${this.spct(lv.cagr_pct, 1)} a year, drawdown ${(lv.max_dd_pct ?? 0).toFixed(0)}%, worst trade ${this.spct(lv.worst_trade_pct, 1)} · ${lv.etf} buy and hold ${this.spct(lv.buy_and_hold_cagr_pct, 1)} a year, drawdown ${(lv.buy_and_hold_max_dd_pct ?? 0).toFixed(0)}%. Not part of the verdict.` }));
    if (lv && !lv.etf) extra.push(h('p', { class: 'sub', text: `QQQ only, window B: ${r.qqq_only?.b?.trades ?? 0} trades, ${this.spct(r.qqq_only?.b?.mean_ret_pct, 2)} per trade, PF ${(r.qqq_only?.b?.profit_factor ?? 0).toFixed(2)} · its QQQ signals 2010 → 2026: in QQQ ${this.spct(lv.QQQ?.cagr_pct, 1)} a year (drawdown ${(lv.QQQ?.max_dd_pct ?? 0).toFixed(0)}%), in TQQQ ${this.spct(lv.TQQQ?.cagr_pct, 1)} a year (drawdown ${(lv.TQQQ?.max_dd_pct ?? 0).toFixed(0)}%). Not part of the verdict.` }));
    if (r.b?.trades_per_day_per_market != null) extra.push(h('p', { class: 'sub', text: `Window B: ${r.b.trades_per_day_per_market.toFixed(2)} trades per day per market · ${this.spct(r.b.sum_pct_per_year_1x, 2)} a year per market at 1× notional (sum of per-trade results).` }));
    if (r.missing_data?.length) extra.push(h('p', { class: 'sub', text: 'No broker data for: ' + r.missing_data.join(', ') }));
    base.firstChild.append(...extra);
    return base;
  },

  /* ---- the Gold 1-min PDF: its 9 take-profit / stop pairs ---- */
  renderGold() {
    const d = this.gold, pairs = d.pairs || [];
    const wkey = (p) => d.complete ? 'y2' : 'window';
    const kpi = (lbl, val, sub) => h('div', { class: 'yc-kpi' }, h('span', { class: 'lbl', text: lbl }), h('b', { text: val }), h('span', { class: 'sub', text: sub }));
    const nets = pairs.map((p) => p[wkey(p)].net.goa_per_100);
    const best = Math.max(...nets), worst = Math.min(...nets);
    const win = Object.values(d.windows || {})[0] || [];
    const hero = h('div', { class: 'yc-hero' },
      h('div', { class: 'yc-hero-l' },
        h('span', { class: 'yc-kicker', text: 'Strategy check · "Gold Scalping Strategy (1-Min TF)", XAUUSD only' }),
        h('h2', {}, h('span', { class: 'yc-big down', text: String(nets.filter((v) => v > 0).length) }), h('span', { text: ' of ' + pairs.length + ' TP/SL pairs make money' })),
        h('p', { class: 'sub', text: (d.complete ? '' : 'PRELIMINARY: ' + win.join(' → ') + ' only (MT5 keeps 100,000 one-minute bars). ')
          + '9/21 EMA cross, RSI 50, a break of the last 10 candles and a strong candle, London and New York openings only. Every pair loses even before costs: entering after a strong 1-minute burst tends to buy its top.' })),
      h('div', { class: 'yc-kpis' },
        kpi('best pair', this.r100(best), 'per 100 trades after costs'),
        kpi('worst pair', this.r100(worst), 'per 100 trades after costs'),
        kpi('costs', '$' + ((d.costs_usd?.spread || 0) + 2 * (d.costs_usd?.commission_per_side || 0) + 2 * (d.costs_usd?.slippage_per_side || 0)).toFixed(3), 'per ounce, round trip'),
        kpi('if 1 pip = $1', this.r100(d.sensitivity_pip_1usd_tp15_sl12?.goa_per_100), 'TP 15 / SL 12, about break-even')));
    const body = h('tbody', {}, ...pairs.map((p) => {
      const w = p[wkey(p)], n = w.net, g = w.gross;
      return h('tr', {},
        h('td', { text: p.tp_pips + ' / ' + p.sl_pips + ' pips' }), h('td', { class: 'sub', text: '$' + (p.tp_pips / 10).toFixed(2) + ' / $' + (p.sl_pips / 10).toFixed(2) }),
        h('td', { class: 'num', text: (n.trades || 0).toLocaleString() }), h('td', { class: 'num', text: this.pct(n.win_rate_pct, 1) }),
        h('td', { class: 'num ' + this.cls(g.goa_per_100), text: this.r100(g.goa_per_100) }),
        h('td', { class: 'num ' + this.cls(n.goa_per_100), text: this.r100(n.goa_per_100) }),
        h('td', { class: 'num', text: (n.profit_factor ?? 0).toFixed(2) }),
        h('td', {}, pill(d.complete ? (p.verdict.startsWith('PROVEN') ? 'PASS' : 'FAIL') : 'PRELIM', (n.goa_per_100 > 0) ? 'up' : 'down')));
    }));
    const table = h('div', { class: 'yc-scroll' }, h('table', { class: 'yc-table yc-gold' },
      h('thead', {}, h('tr', {}, ...['Take profit / stop', 'In USD', 'Trades', 'Win rate', 'Price move only', 'After costs', 'PF', 'Verdict'].map((t, i) => h('th', { class: i >= 2 && i <= 6 ? 'num' : '', text: t })))), body));
    const notes = h('div', { class: 'card yc-notes' }, h('h3', { text: 'How this was tested' }), h('ul', {},
      h('li', { text: 'Rules: sessions 07:00-10:00 and 13:00-16:00 UTC; buy when the 9 EMA crossed above the 21 EMA in the last 5 candles, RSI(14) above 50, the close above the last 10 candles\' high and a bullish candle with a body of at least 60% of its range (sell mirror); at most 5 trades a day; an opposite signal closes.' }),
      h('li', { text: 'Gold pip = 0.10 USD. Costs: your broker\'s recorded spread ($' + (d.costs_usd?.spread || 0).toFixed(3) + '), $' + (d.costs_usd?.commission_per_side || 0) + ' commission and $' + (d.costs_usd?.slippage_per_side || 0) + ' slippage per side, per ounce.' }),
      h('li', { text: 'Data: ' + d.source + (d.complete ? '. Year 1 2024-10-20 → 2025-10-20, year 2 2025-10-20 → 2026-10-20 (the pre-registered two years).' : '. The full pre-registered test (two years) needs MT5\'s "Max. bars in chart" raised.') })),
      h('p', { class: 'sub', text: 'Pre-registration and write-up: ' + d.pre_registration + ' · historical research, not advice' }));
    return h('div', { class: 'yc-wrap' }, this.tabs(), hero, h('div', { class: 'card yc-card' }, table), notes);
  },
};
