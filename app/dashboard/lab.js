/* PaperLab - pieces from the Lovable dashboard, folded into the competition pages (public and private):
   the sidebar icons, the Programs view and the AI cost analyzer.

   Programs: one card per research program with its verdict, bot count, average return and best bot. Data: the public
   roster (GET /api/public/competition/roster), which lists every bot.

   Cost analyzer: the operator uploads a backtest or trade history; the server sends it to OpenRouter with its own key
   (app/ai/analyzer.py) and streams the Markdown answer back. The route is private (Basic auth + X-PaperLab), so the
   view asks for the dashboard password unless the private dashboard already holds it (same sessionStorage key). The
   OpenRouter key never reaches the browser, and the answer is rendered as text nodes, never as HTML. */
'use strict';

const LAB_SVG = 'http://www.w3.org/2000/svg';
const LAB_ICONS = {      // lucide-style 24px strokes
  dashboard: 'M3 3h7v9H3z M14 3h7v5h-7z M14 12h7v9h-7z M3 16h7v5H3z',
  bots: 'M12 8V4H8 M4 8h16v12H4z M2 14h2 M20 14h2 M15 13v2 M9 13v2',
  markets: 'M3 3v18h18 M7 16l4-4 4 4 5-6',
  programs: 'M10 2v7.31 M14 9.3V2 M8.5 2h7 M14 9.3a6.5 6.5 0 1 1-4 0 M5.52 16h12.96',
  analyzer: 'M9.94 14.06 4 20 M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8z M20 3v4 M22 5h-4',
  scout: 'M11 3a8 8 0 1 0 0 16a8 8 0 1 0 0-16z M21 21l-4.3-4.3',
  system: 'M12 2l8 4v6c0 5-3.5 8.5-8 10c-4.5-1.5-8-5-8-10V6z M9 12l2 2 4-4',
};

/** A sidebar icon for a nav id (falls back to a dot). */
function navIcon(id) {
  const svg = document.createElementNS(LAB_SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor',
    'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'nav-ico' })) svg.setAttribute(k, v);
  const path = document.createElementNS(LAB_SVG, 'path');
  path.setAttribute('d', LAB_ICONS[id] || 'M12 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2z');
  svg.append(path);
  return svg;
}

/** A nav button: icon + label (sentence case, as in the Lovable sidebar). */
function navButton(id, label, onclick) {
  return h('button', { role: 'tab', type: 'button', 'data-nav': id, onclick }, navIcon(id), h('span', { text: label }));
}

// ---- Programs -------------------------------------------------------------------------------------------------
// Verdicts follow the studies in docs/ (2-year backtest, V9/V11/V12/V13 studies).
const LAB_PROGRAMS = [
  { id: 'v6', name: 'V6 Forward Arena', desc: 'Hourly strategies that read Bybit positioning: funding, open interest, premium. Only the two families that made money over the 2-year backtest remain: V6.6 trend follower and V6.2 momentum rider. They trade about once every 10-15 days per bot.', market: 'Bybit perps', tf: '1h', verdict: 'edge' },
  { id: 'v7', name: 'V7 Active Challenger', desc: '15-minute trend and positioning strategies.', market: 'Bybit perps', tf: '15m', verdict: 'no-edge' },
  { id: 'v8', name: 'V8.3 Scalpers', desc: '5-minute VWAP snap-back with control, Jev and ladder twins.', market: 'Bybit perps', tf: '5m', verdict: 'no-edge' },
  { id: 'v9', name: 'V9 Stocks', desc: 'V8 scalp families on US stocks and ETFs, regular sessions only.', market: 'Alpaca IEX', tf: '5m', verdict: 'no-edge' },
  { id: 'v11', name: 'V11 Scanners', desc: 'Four scanners over 30 coins with a 25/50/25 take-profit ladder.', market: 'Bybit perps', tf: '15m', verdict: 'no-edge' },
  { id: 'v12', name: 'V12 Bizzy', desc: 'Daily breakout ported from Bizzy Bee. The backtest loses after Bybit costs.', market: 'ETH, SOL, HYPE', tf: '1d', verdict: 'no-edge' },
  { id: 'v13', name: 'V13 Snapback', desc: 'Maker snap-back to the 24-hour VWAP. The study failed on its test window.', market: '29 coins', tf: '15m', verdict: 'no-edge' },
  { id: 'v14', name: 'V14 HTF', desc: 'Copies of the leading bots that only trade with the 4h and daily trend.', market: 'Mixed', tf: '5m / 15m', verdict: 'research' },
  { id: 'video', name: 'Video breakout', desc: 'BTC 4-hour breakout taken from a trading video.', market: 'BTC', tf: '4h', verdict: 'research' },
];
// Switched off on 2026-10-08 (app/core/programs.py): average return per bot over the 2-year backtest.
const LAB_RETIRED = [
  ['V6.1 / V6.3 / V6.4 / V6.5', '-1.8% / -20.7% / -7.7% / -0.5%', 'lost, or barely traded (V6.5: 3 trades in 2 years); still computed inside the frozen V6 experiment, hidden'],
  ['V7 15-minute trend', '-23.5% / -20.3%', 'lost to fees and spread'],
  ['V8.3 scalpers', '-28.8%', 'lost to fees and spread'],
  ['V9 stock scalpers', '-25.3% / -21.4% / -26.5%', 'no edge on stocks; replaced by day-trading research'],
  ['V11 scanners', '-25% to -32%', 'lost to fees and spread'],
  ['V12 Bizzy', '-63.5%', 'the day breakout loses after Bybit costs'],
  ['V13 Snapback', '-16.7%', 'maker reversion failed its test window'],
  ['V14 HTF copies', '-22% to -29%', 'the higher-timeframe filter did not rescue the copies'],
];
const LAB_VERDICT = { edge: ['Shows an edge', 'up'], 'no-edge': ['No edge after costs', 'down'], research: ['Still researching', 'warn'] };

function labBotHref(program, key) {
  const q = '?p=' + encodeURIComponent(program) + '&bot=' + encodeURIComponent(key);
  return document.body.classList.contains('public') ? '/public/competition/bots' + q : '/' + q + '#bots/bots';
}

function labPct(v, digits = 2) {
  if (!isFinite(v)) return '—';
  return (v > 0 ? '+' : '') + (v * 100).toFixed(digits) + '%';
}

const ProgramsView = {
  data: null, busy: false,
  async load() {
    const grid = $('#programs-grid');
    if (!grid || this.busy) return;
    this.busy = true;
    try {
      this.data = await (await fetch('/api/public/competition/roster', { cache: 'no-store' })).json();
      this.render();
    } catch (e) {
      clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'msg bad', text: 'Could not load the programs: ' + e.message })));
    } finally { this.busy = false; }
  },
  render() {
    const grid = clear($('#programs-grid'));
    const d = this.data || {};
    const rows = [...(d.roster || []), ...(d.ready || []), ...(d.scanners || []), ...(d.retired || [])];
    const seen = new Set();
    const bots = rows.filter((x) => { const k = x.program + '|' + x.key; if (seen.has(k)) return false; seen.add(k); return true; });
    grid.append(h('div', { class: 'card page-head' }, h('h1', { class: 'page-title', text: 'Programs' }),
      h('p', { class: 'sub', text: 'Each program is a frozen, pre-registered experiment. Verdicts come from the backtests and studies in the repository.' })));
    const list = h('div', { class: 'prog-grid' });
    for (const p of LAB_PROGRAMS) {
      const pb = bots.filter((x) => x.program === p.id);
      if (!pb.length) continue;
      const rets = pb.map((x) => Number(x.return) || 0);
      const avg = rets.reduce((a, b) => a + b, 0) / rets.length;
      const best = pb.slice().sort((a, b) => (Number(b.return) || 0) - (Number(a.return) || 0))[0];
      const [vText, vKind] = LAB_VERDICT[p.verdict];
      list.append(h('div', { class: 'card prog-card' },
        h('div', { class: 'prog-top' }, h('h2', { class: 'prog-name', text: p.name }), h('span', { class: 'pill ' + vKind, text: vText })),
        h('p', { class: 'sub prog-desc', text: p.desc }),
        h('div', { class: 'prog-facts' },
          h('div', {}, h('span', { class: 'lbl', text: 'Market' }), h('b', { text: p.market })),
          h('div', {}, h('span', { class: 'lbl', text: 'Timeframe' }), h('b', { text: p.tf })),
          h('div', {}, h('span', { class: 'lbl', text: 'Avg return' }), h('b', { class: 'num ' + (avg >= 0 ? 'up' : 'down'), text: labPct(avg) }))),
        h('div', { class: 'prog-best' }, h('span', { class: 'sub', text: pb.length + ' bots · best: ' }),
          h('a', { href: labBotHref(best.program, best.key), text: best.name || best.key, onclick: (e) => {
            if (typeof Roster === 'undefined') return;                      // no in-page router: follow the link
            e.preventDefault(); Roster.open(best); window.scrollTo(0, 0);
          } }), ' ',
          h('span', { class: 'num ' + ((Number(best.return) || 0) >= 0 ? 'up' : 'down'), text: labPct(Number(best.return) || 0) }))));
    }
    grid.append(list);
    grid.append(h('div', { class: 'card prog-retired' },
      h('h2', { text: 'Switched off on 2026-10-08' }),
      h('p', { class: 'sub', text: 'Every bot was replayed over two years with real fees. These families lost money, so they no longer trade; their history stays on the server.' }),
      h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' },
        h('thead', {}, h('tr', {}, h('th', { text: 'Family' }), h('th', { class: 'num', text: '2-year return per bot' }), h('th', { text: 'Why' }))),
        h('tbody', {}, LAB_RETIRED.map(([f, r, why]) => h('tr', {}, h('td', { text: f }), h('td', { class: 'num down', text: r }),
          h('td', { class: 'wrapall', text: why }))))))));
  },
};

// ---- Cost analyzer --------------------------------------------------------------------------------------------
const LAB_MAX_CHARS = 250000;
const LAB_PW_KEY = 'paperlab.pw';          // the private dashboard's key: signing in on either page works for both
const LabCred = {
  get() {
    if (typeof Auth !== 'undefined' && Auth.get) return Auth.get();
    try { return sessionStorage.getItem(LAB_PW_KEY); } catch (e) { return null; }
  },
  set(pw) { try { sessionStorage.setItem(LAB_PW_KEY, pw); } catch (e) { /* storage blocked: kept in memory only */ } this.mem = pw; },
  clear() { try { sessionStorage.removeItem(LAB_PW_KEY); } catch (e) { /* nothing stored */ } this.mem = null; },
  header(pw) { return 'Basic ' + btoa(unescape(encodeURIComponent('admin:' + pw))); },
};

/** Markdown (the analyzer's fixed format: ## headings, bullets, numbered lists, tables, **bold**, `code`) as DOM.
    Every piece of text becomes a text node, so nothing in the answer can inject markup. */
function labInline(text) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    out.push(t.startsWith('**') ? h('strong', { text: t.slice(2, -2) }) : h('code', { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
function labMarkdown(md) {
  const root = h('div', { class: 'md' });
  const lines = String(md).replace(/\r/g, '').split('\n');
  let i = 0;
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const hd = /^(#{1,4})\s+(.*)$/.exec(line);
    if (hd) { root.append(h(hd[1].length <= 2 ? 'h3' : 'h4', {}, labInline(hd[2]))); i++; continue; }
    if (line.trim().startsWith('|')) {
      const block = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) block.push(lines[i++]);
      const body = block.filter((l) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(l));        // drop the |---|---| separator
      if (!body.length) continue;
      const [head, ...rest] = body;
      const t = h('table', { class: 'tbl md-table' },
        h('thead', {}, h('tr', {}, cells(head).map((c) => h('th', {}, labInline(c))))),
        h('tbody', {}, rest.map((r) => h('tr', {}, cells(r).map((c) => h('td', {}, labInline(c)))))));
      root.append(h('div', { class: 'tablewrap' }, t));
      continue;
    }
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const list = h(ordered ? 'ol' : 'ul', {});
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/).test(lines[i])) {
        list.append(h('li', {}, labInline(lines[i].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/, ''))));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {   // continuation
          list.lastChild.append(' ', ...labInline(lines[i].trim())); i++;
        }
      }
      root.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*[-*]\s|\s*\d+[.)]\s)/.test(lines[i])) para.push(lines[i++].trim());
    root.append(h('p', {}, labInline(para.join(' '))));
  }
  return root;
}

const AnalyzerView = {
  status: null, file: null, output: '', error: null, busy: false, abort: null, authError: null, mounted: false,
  load() {
    if (!$('#analyzer-grid')) return;
    if (!this.mounted) { this.mounted = true; this.render(); }
    const pw = LabCred.get();
    if (pw != null && !this.status) this.checkStatus(pw);
  },
  async checkStatus(pw) {
    try {
      const r = await fetch('/api/analyzer/status', { headers: { Authorization: LabCred.header(pw) }, cache: 'no-store' });
      if (r.status === 401) { LabCred.clear(); this.authError = 'Wrong password.'; this.status = null; }
      else if (r.ok) { this.status = await r.json(); this.authError = null; }
    } catch (e) { this.authError = 'Could not reach PaperLab.'; }
    this.render();
  },
  async botOptions() {
    try {
      const d = await (await fetch('/api/public/competition/roster', { cache: 'no-store' })).json();
      const all = [...(d.roster || []), ...(d.ready || []), ...(d.scanners || []), ...(d.retired || [])];
      const seen = new Set();
      return all.filter((x) => { const k = x.program + '|' + x.key; if (seen.has(k)) return false; seen.add(k); return true; })
        .sort((a, b) => String(a.name).localeCompare(String(b.name)));
    } catch (e) { return []; }
  },
  render() {
    const grid = clear($('#analyzer-grid'));
    const pw = LabCred.get();
    grid.append(h('div', { class: 'card page-head' }, h('h1', { class: 'page-title', text: 'Cost analyzer' }),
      h('p', { class: 'sub', text: 'Upload a backtest or trade history. AI finds where fees and spread eat the edge and proposes changes you can test.' })));
    if (pw == null) { grid.append(this.signIn()); return; }
    grid.append(this.form(), this.answer());
  },
  signIn() {
    const input = h('input', { type: 'password', autocomplete: 'current-password', 'aria-label': 'dashboard password' });
    const form = h('form', { class: 'card an-signin' },
      h('h2', { text: 'Operator only' }),
      h('p', { class: 'sub', text: "The analyzer runs on PaperLab's own OpenRouter key, so it needs the dashboard password. Everything else here is read-only." }),
      h('label', { class: 'field' }, h('span', { text: 'Password' }), input),
      this.authError ? h('p', { class: 'msg bad', role: 'alert', text: this.authError }) : null,
      h('button', { class: 'btn primary', type: 'submit', text: 'Sign in' }));
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!input.value) return;
      LabCred.set(input.value); this.status = null; this.checkStatus(input.value);
    });
    return form;
  },
  form() {
    const s = this.status;
    const fileLabel = h('span', { text: this.file ? this.file.name : 'Choose a CSV, JSON or text file' });
    const fileInput = h('input', { type: 'file', accept: '.csv,.json,.txt,.tsv,.log', hidden: true });
    const drop = h('label', { class: 'an-drop' }, navIcon('analyzer'), fileLabel,
      this.file && this.file.truncated ? h('span', { class: 'sub warn', text: 'Large file: kept the header and most recent rows.' }) : null, fileInput);
    fileInput.addEventListener('change', async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      const text = await f.text();
      const truncated = text.length > LAB_MAX_CHARS;
      this.file = { name: f.name, truncated,
        content: truncated ? text.slice(0, 4000) + '\n...[middle rows omitted]...\n' + text.slice(-(LAB_MAX_CHARS - 4100)) : text };
      this.render();
    });
    const select = h('select', { class: 'an-bot' }, h('option', { value: '', text: 'None' }));
    this.botOptions().then((bots) => { for (const b of bots) select.append(h('option', { value: b.program + '|' + b.key, text: (b.name || b.key) + ' · ' + b.key })); select.value = this.botId || ''; select._bots = bots; });
    select.addEventListener('change', () => { this.botId = select.value; });
    const notes = h('textarea', { rows: 3, placeholder: 'e.g. taker entries, 0.055% fee, 5m timeframe' });
    notes.value = this.notes || '';
    notes.addEventListener('input', () => { this.notes = notes.value; });
    const run = this.busy
      ? h('button', { class: 'btn', type: 'button', text: 'Stop', onclick: () => this.abort && this.abort.abort() })
      : h('button', { class: 'btn primary', type: 'button', text: 'Analyze', disabled: !this.file || (s && s.configured === false),
        onclick: () => this.run(select._bots || []) });
    const foot = s && s.configured === false ? 'Not available: the server has no OPENROUTER_API_KEY.'
      : "Runs on PaperLab's OpenRouter credits" + (s ? ' (' + s.model + ')' : '') + '. Files are not stored. Suggestions are research ideas, not financial advice.';
    return h('div', { class: 'card an-form' },
      drop,
      h('label', { class: 'field' }, h('span', { text: 'Link to a bot (optional)' }), select),
      h('label', { class: 'field' }, h('span', { text: 'Notes (optional)' }), notes),
      h('div', { class: 'btn-row' }, run, h('button', { class: 'btn ghost', type: 'button', text: 'Sign out',
        onclick: () => { LabCred.clear(); this.status = null; this.render(); } })),
      h('p', { class: 'sub', text: foot }));
  },
  answer() {
    const box = h('div', { class: 'card an-out', id: 'an-out' });
    this.paint(box);
    return box;
  },
  paint(box = $('#an-out')) {
    if (!box) return;
    clear(box);
    if (this.error) box.append(h('div', { class: 'banner down', role: 'alert', text: this.error }));
    if (this.output) box.append(labMarkdown(this.output));
    else if (this.busy) box.append(h('p', { class: 'sub pulse', text: 'Reading your trades…' }));
    else if (!this.error) box.append(h('p', { class: 'sub', text: 'Your analysis will appear here.' }));
  },
  async run(bots) {
    const pw = LabCred.get();
    if (!this.file || pw == null) return;
    const [prog, key] = (this.botId || '').split('|');
    const bot = bots.find((b) => b.program === prog && b.key === key);
    const botContext = bot ? `name=${bot.name} key=${bot.key} program=${bot.program} net=${labPct(Number(bot.return) || 0)} trades=${bot.trades} winRate=${bot.win_rate == null ? '?' : (bot.win_rate * 100).toFixed(1) + '%'} maxDD=${bot.max_dd == null ? '?' : (bot.max_dd * 100).toFixed(2) + '%'} fees=${bot.fees}` : undefined;
    this.output = ''; this.error = null; this.busy = true; this.abort = new AbortController();
    this.render();
    try {
      const res = await fetch('/api/analyzer', {
        method: 'POST', signal: this.abort.signal,
        headers: { 'Content-Type': 'application/json', Authorization: LabCred.header(pw), 'X-PaperLab': '1' },
        body: JSON.stringify({ fileName: this.file.name, content: this.file.content, botContext, notes: this.notes || undefined }),
      });
      if (res.status === 401) { LabCred.clear(); this.status = null; this.authError = 'Your session expired. Sign in again.'; return; }
      if (!res.ok || !res.body) { const j = await res.json().catch(() => ({})); throw new Error(j.error || 'Request failed (' + res.status + ')'); }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let acc = '', painted = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        acc += dec.decode(value, { stream: true });
        const i = acc.indexOf('[[ERROR]]');
        if (i >= 0) { this.output = acc.slice(0, i).trim(); this.error = acc.slice(i + 9).trim(); } else this.output = acc;
        if (Date.now() - painted > 120) { this.paint(); painted = Date.now(); }
      }
    } catch (e) {
      if (e.name !== 'AbortError') this.error = e.message;
    } finally {
      this.busy = false; this.abort = null; this.render();
    }
  },
};
