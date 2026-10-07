/* PaperLab - SCOUT (V10): news and CoinGecko trending attention per coin / stock (Reddit only if enabled). RESEARCH ONLY - no bot trades on it until a
   pre-registered study passes a signal (docs/V10_PROTOCOL.md).

   Public page: the numbers (mentions in the last hour vs that symbol's own normal, news counts, tone, 24 h sparkline).
   Private dashboard: click a symbol for the posts and headlines behind its numbers. Data: GET /api/public/scout,
   GET /api/scout/items (private), and the 'scout' push event after every 5-minute cycle. */
'use strict';

const Scout = {
  data: null, symbol: '', items: null, timer: null, clock: null,
  grid() { return document.getElementById('scout-grid'); },
  async load() {
    this.deactivate();
    if (typeof Stream !== 'undefined' && !this.wired) { Stream.on('scout', (d) => { this.data = d; this.render(); }); this.wired = true; }
    await this.refresh();
    this.timer = setInterval(() => this.refresh(), 60_000);
    this.clock = setInterval(() => this.tick(), 1000);
  },
  deactivate() { clearInterval(this.timer); clearInterval(this.clock); this.timer = this.clock = null; },
  async refresh() {
    try { this.data = await getJSON('/api/public/scout'); } catch (e) { this.data = { error: e.message }; }
    this.render();
  },
  tick() {
    const el = document.getElementById('scout-next'), nxt = ((this.data || {}).health || {}).next_cycle_ms;
    if (el && nxt) { const s = Math.max(0, Math.floor((nxt - Date.now()) / 1000)); setText(el, Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')); }
  },
  srcPill(name, s) {
    s = s || {};
    const kind = { OK: 'up', NEEDS_KEYS: 'warn', ERROR: 'down', WAITING: 'ghost' }[s.state] || 'ghost';
    const txt = { OK: 'OK', NEEDS_KEYS: 'NEEDS KEY', ERROR: 'ERROR', WAITING: 'WAITING' }[s.state] || (s.state || '?');
    return h('div', { class: 'tile' }, h('span', { class: 'lbl', text: name }), h('span', { class: 'val' }, pill(txt, kind)),
      h('span', { class: 'sub', text: s.state === 'OK' ? (name === 'reddit' ? (s.read ?? 0) + ' read · ' + (s.new ?? 0) + ' new'
        : name === 'CoinGecko' ? (s.coins ?? 0) + ' trending coins' : (s.new ?? 0) + ' new headlines') : (s.detail || '') }));
  },
  tone(v) { return isNum(v) ? h('span', { class: signClass(v), text: (v > 0 ? '+' : '') + v.toFixed(2) }) : DASH; },
  render() {
    const grid = this.grid();
    if (!grid || grid.closest('[hidden]')) return;
    const d = this.data || {};
    if (d.error || !d.health) { clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'sub', text: d.error ? 'scout unavailable: ' + d.error : 'loading…' }))); return; }
    const hl = d.health, day = d.last_24h || {}, src = hl.sources || {};
    const head = h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Scout · news & crowd attention' }),
        pill(hl.enabled ? (hl.state || '?') : 'OFF', hl.state === 'RUNNING' ? 'up' : hl.enabled ? 'warn' : 'ghost')),
      h('p', { class: 'sub', text: 'Research only: every 5 minutes the scout reads Benzinga news and CoinGecko\'s trending list (the coins people search for most). No bot trades on this until a study proves it predicts moves.' }),
      h('div', { class: 'tiles six' }, this.srcPill('news', src.news),
        (src.reddit || {}).state === 'OFF' ? this.srcPill('CoinGecko', src.trending) : this.srcPill('reddit', src.reddit),
        tile('read in 24 h', String(day.items_read ?? 0), '', (day.new_items ?? 0) + ' about our symbols'),
        tile('cycles in 24 h', String(day.cycles ?? 0), '', (day.errors ?? 0) + ' errors'),
        tile('Jev tone cost 24 h', '$' + (day.jev_cost_usd ?? 0).toFixed(4), '', hl.jev_tone ? 'headline tone by Jev' : 'off'),
        h('div', { class: 'tile' }, h('span', { class: 'lbl', text: 'next read' }), h('span', { class: 'val accent', id: 'scout-next', text: '…' }),
          h('span', { class: 'sub', text: 'every 5 minutes' }))));
    const rows = (d.symbols || []).slice().sort((a, b) => (b.spike || 0) - (a.spike || 0) || b.mentions_24h - a.mentions_24h);
    const trend = (d.trending || []);
    const trendCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Unusual attention right now' })),
      trend.length ? h('div', { class: 'chips' }, trend.map((s) => {
        const r = rows.find((x) => x.symbol === s) || {};
        return h('button', { type: 'button', class: 'chip on', text: s + ' ×' + (r.spike ?? '?'), onclick: () => this.pick(s) });
      })) : h('p', { class: 'sub', text: 'nothing unusual: no symbol has 1.5× its normal mentions (with at least 5) in the last hour' }));
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'symbol', cell: (r) => h('b', { class: 'mono', text: r.symbol }) },
      { h: 'market', cell: (r) => pill(r.market === 'crypto' ? 'CRYPTO' : 'STOCK', 'ghost') },
      { h: 'mentions 1 h', cell: (r) => String(r.mentions_1h), num: true },
      { h: 'normal / h', cell: (r) => (isNum(r.normal_per_h) ? r.normal_per_h.toFixed(1) : DASH), num: true },
      { h: 'spike', cell: (r) => (isNum(r.spike) ? h('b', { class: r.spike >= 1.5 ? 'up' : '', text: '×' + r.spike.toFixed(2) }) : DASH), num: true },
      { h: 'news 1 h / 24 h', cell: (r) => r.news_1h + ' / ' + r.news_24h, num: true },
      { h: 'Reddit tone 1 h', cell: (r) => this.tone(r.tone_reddit_1h), num: true },
      { h: 'news tone 24 h', cell: (r) => this.tone(r.tone_news_24h), num: true },
      { h: 'CoinGecko', cell: (r) => (isNum(r.cg_rank) ? h('b', { class: 'up', text: '#' + r.cg_rank }) : r.market === 'crypto' ? (r.cg_hours_24h ? r.cg_hours_24h.toFixed(1) + ' h / 24 h' : DASH) : ''), num: true },
      { h: 'mentions · 24 h', cell: (r) => sparkline((r.series || []).map((p) => [p[0], p[1]]), 120, 24) },
    ], rows, { onRow: window.PAPERLAB_PRIVATE ? (r) => this.pick(r.symbol) : null, empty: 'no data yet — the first read runs within 5 minutes' });
    const table = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Attention by symbol' }),
      h('span', { class: 'sub', text: window.PAPERLAB_PRIVATE ? 'click a row for the posts and headlines' : 'spike = mentions in the last hour ÷ that symbol\'s normal hour' })),
    h('div', { class: 'tablewrap' }, t));
    clear(grid).append(head, this.coingeckoCard(d.coingecko), trendCard, table, this.itemsCard());
    this.tick();
  },
  /** CoinGecko's trending list right now (public data, credited as CoinGecko asks). */
  coingeckoCard(cg) {
    cg = cg || {};
    const now = cg.now || [];
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: '#', cell: (c) => String(c.rank), num: true },
      { h: 'coin', cell: (c) => h('span', null, h('b', { class: 'mono', text: c.symbol }), ' ', h('span', { class: 'sub', text: c.name })) },
      { h: '', cell: (c) => (c.ours ? pill('IN OUR BOTS', 'up') : null) },
      { h: 'trending for', cell: (c) => aAge(Date.now() - c.since_ms), num: true },
      { h: 'hours in 24 h', cell: (c) => ((cg.hours_24h || {})[c.symbol] ?? 0).toFixed(1), num: true },
      { h: 'market cap rank', cell: (c) => (isNum(c.mcap_rank) ? '#' + c.mcap_rank : DASH), num: true },
    ], now, { empty: 'waiting for the first read (every 5 minutes)' });
    return h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Trending on CoinGecko now' }),
        h('span', { class: 'sub' }, 'the coins crypto users search for most · data by ',
          h('a', { href: 'https://www.coingecko.com', target: '_blank', rel: 'noopener noreferrer', text: 'CoinGecko' }))),
      h('div', { class: 'tablewrap' }, t));
  },
  async pick(sym) {
    if (!window.PAPERLAB_PRIVATE) return;
    this.symbol = sym; this.items = null; this.render();
    try { this.items = (await api('/api/scout/items?symbol=' + encodeURIComponent(sym) + '&limit=60')).items || []; }
    catch (e) { this.items = { error: e.message }; }
    this.render();
    const el = document.getElementById('scout-items');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },
  itemsCard() {
    if (!window.PAPERLAB_PRIVATE || !this.symbol) return null;
    const list = h('ul', { class: 'feed' });
    if (!this.items) list.append(h('li', { class: 'sub', text: 'loading…' }));
    else if (this.items.error) list.append(h('li', { class: 'err', text: this.items.error }));
    else if (!this.items.length) list.append(h('li', { class: 'sub', text: 'nothing stored for ' + this.symbol + ' yet' }));
    else for (const it of this.items) {
      const jt = (it.jev_tone || {})[this.symbol];
      list.append(h('li', { class: 'act' },
        h('span', { class: 'act-time', text: new Date(it.created_ms).toISOString().slice(5, 16).replace('T', ' ') }),
        h('span', { class: 'act-body' }, pill(it.source === 'news' ? 'NEWS' : it.channel, it.source === 'news' ? 'accent' : 'ghost'), ' ',
          it.url ? h('a', { href: it.url, target: '_blank', rel: 'noopener noreferrer', text: it.text.split('\n')[0].slice(0, 180) }) : it.text.slice(0, 180), ' ',
          h('span', { class: 'sub' }, 'tone ', this.tone(isNum(jt) ? jt : it.tone), isNum(jt) ? ' (Jev)' : ''))));
    }
    return h('div', { class: 'card', id: 'scout-items' }, h('div', { class: 'card-head' }, h('h2', { text: 'What people say about ' + this.symbol }),
      h('button', { type: 'button', class: 'chip', text: 'close', onclick: () => { this.symbol = ''; this.render(); } })), list);
  },
};
