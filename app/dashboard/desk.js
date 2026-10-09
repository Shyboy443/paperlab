/* PaperLab - TRADING DESK: six AI agents (Chief, Scout, News, Sentiment, Charts, Skeptic) in one group chat, a paper
   ledger, session decision logs. Paper money only.

   Public page: watch the chat live, the ledger and the decision logs (GET /api/public/desk...). Private dashboard:
   also message an agent directly, run a session now and change the settings (POST /api/desk/... with the dashboard
   login). Live messages arrive as the 'desk' push event. */
'use strict';

const Desk = {
  data: null, tab: 'chat', agent: null, dms: {}, timer: null, clock: null, wired: false, logs: {}, busy: {}, drafts: {},
  root() { return document.getElementById('desk-root'); },
  priv() { return !document.body.classList.contains('public'); },
  async load() {
    this.deactivate();
    if (typeof Stream !== 'undefined' && !this.wired) { Stream.on('desk', (d) => this.onEvent(d)); this.wired = true; }
    await this.refresh();
    this.timer = setInterval(() => this.refresh(), 60_000);
    this.clock = setInterval(() => this.tick(), 1000);
  },
  deactivate() { clearInterval(this.timer); clearInterval(this.clock); this.timer = this.clock = null; },
  async refresh() {
    const pending = ((this.data || {}).messages || []).filter((m) => m.pending);
    try { this.data = await api('/api/public/desk?limit=200'); this.data.messages = this.data.messages.concat(pending); }
    catch (e) { if (!this.data) this.data = { error: e.message }; }
    const typing = document.activeElement && document.activeElement.classList.contains('dk-input') && document.activeElement.value;
    if (!this.agent && this.tab !== 'settings' && !typing) this.render();   // never wipe a message or a form being typed
  },
  onEvent(d) {
    const m = d && d.message;
    if (!m || !this.data || !this.data.messages) return;
    if (m.channel === 'group') {
      if (this.data.messages.some((x) => x.id === m.id)) return;
      this.data.messages.push(m);
      if (m.kind === 'trade' || m.kind === 'decision') this.refresh();
      else if (this.tab === 'chat' && !this.agent) this.appendMsg(m);
    } else if (m.channel.startsWith('dm:')) {
      const a = m.channel.slice(3);
      (this.dms[a] = this.dms[a] || []).some((x) => x.id === m.id) || this.dms[a].push(m);
      if (this.agent === a) this.render();
    }
  },
  tick() {
    const el = document.getElementById('desk-next'), hl = (this.data || {}).health || {};
    if (!el) return;
    if (hl.running) { setText(el, 'session running…'); return; }
    const s = Math.max(0, Math.floor(((hl.next_session_ms || 0) - Date.now()) / 1000));
    if (s === 0) { setText(el, 'next session due now'); return; }
    setText(el, 'next session in ' + Math.floor(s / 3600) + 'h ' + String(Math.floor((s % 3600) / 60)).padStart(2, '0') + 'm');
  },
  who(id) { return ((this.data || {}).agents || {})[id] || { name: id === 'you' ? 'You' : id, color: '#8a94a6', initials: id === 'you' ? 'ME' : '?' }; },
  avatar(id) { const a = this.who(id); return h('span', { class: 'dk-av', style: `background:${a.color}`, text: a.initials }); },
  when(ts) {
    if (!ts) return '';
    const d = new Date(ts), today = new Date().toDateString() === d.toDateString();
    return today ? fmtTime(ts) : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) + ' ' + fmtTime(ts);
  },
  money(v) { return isNum(v) ? (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : DASH; },
  px(v) { return isNum(v) ? (Math.abs(v) >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : String(+v.toPrecision(6))) : DASH; },

  // -- layout -----------------------------------------------------------------------------------------------------------
  render() {
    const root = this.root();
    if (!root || root.closest('[hidden]')) return;
    const d = this.data || {};
    if (d.error) { root.replaceChildren(h('div', { class: 'card' }, h('h2', { text: 'Trading Desk' }), h('p', { class: 'sub', text: 'unavailable: ' + d.error }))); return; }
    const hl = d.health || {}, llm = hl.llm || {}, st = d.settings || {};
    const head = h('div', { class: 'dk-head' },
      h('div', {}, h('h2', { text: 'Trading Desk' }),
        h('p', { class: 'sub', text: 'Six AI agents debate the market every ' + (st.schedule_hours || 4) + 'h and paper-trade a ' + this.money((d.ledger || {}).start_balance) + ' book · ' + (st.mode || '') + ' mode · paper money only' })),
      h('div', { class: 'dk-head-r' },
        pill(llm.configured ? 'AI ' + (llm.model || '').split('/').pop() : 'AI KEY MISSING', llm.configured ? 'up' : 'down'),
        h('span', { class: 'sub', id: 'desk-next' }),
        h('span', { class: 'sub', text: (llm.calls_today ?? 0) + '/' + (llm.max_calls_per_day ?? '?') + ' AI calls today' + (isNum(llm.cost_today_usd) && llm.cost_today_usd > 0 ? ' · $' + llm.cost_today_usd.toFixed(2) : '') }),
        this.priv() ? h('button', { class: 'btn', text: hl.running ? 'Running…' : 'Run session now', disabled: !!hl.running, onclick: () => this.runNow() }) : null));
    const tabs = h('div', { class: 'dk-tabs' }, ...[['chat', 'Group chat'], ['ledger', 'Ledger'], ['decisions', 'Decisions'], ...(this.priv() ? [['settings', 'Settings']] : [])]
      .map(([k, label]) => h('button', { class: 'chip' + (this.tab === k && !this.agent ? ' on' : ''), text: label, onclick: () => { this.tab = k; this.agent = null; this.render(); } })));
    const body = this.agent ? this.dmView() : this.tab === 'ledger' ? this.ledgerView() : this.tab === 'decisions' ? this.decisionsView()
      : this.tab === 'settings' ? this.settingsView() : this.chatView();
    root.replaceChildren(head, h('div', { class: 'dk-grid' }, this.side(), h('div', { class: 'dk-main' }, tabs, body)));
    this.tick();
    const log = document.getElementById('dk-log');
    if (log) { log.scrollTop = log.scrollHeight; requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; }); }
  },
  side() {
    const ag = (this.data || {}).agents || {};
    return h('aside', { class: 'dk-side card' }, h('h3', { text: 'The desk' }),
      ...Object.entries(ag).map(([id, a]) => h('button', {
        class: 'dk-agent' + (this.agent === id ? ' on' : ''), title: this.priv() ? 'Message ' + a.name + ' directly' : a.role,
        onclick: () => this.openAgent(id) }, this.avatar(id), h('span', {}, h('b', { text: a.name + (this.busy[id] ? ' …' : '') }), h('small', { text: this.busy[id] ? 'working on your message' : a.role })))),
      h('p', { class: 'sub dk-note', text: this.priv() ? 'Write in the group chat (@name to pick an agent), or click an agent for a private chat. Agents fetch live data and can do tasks: run a session, open or close paper trades, change settings.' : 'Watching read-only. Use “Log in to chat” to talk to the agents and give them tasks.' }));
  },

  // -- chat ---------------------------------------------------------------------------------------------------------------
  chatView() {
    const msgs = (this.data || {}).messages || [];
    return h('div', { class: 'card dk-chat' }, h('div', { class: 'dk-log', id: 'dk-log' },
      ...(msgs.length ? msgs.map((m) => this.msg(m)) : [h('p', { class: 'sub', text: 'No session yet. The first one runs at the next scheduled slot.' })])),
      this.composer());
  },
  composer() {
    if (!this.priv()) {
      return h('div', { class: 'dk-compose dk-login' }, h('span', { class: 'sub', text: 'Want to talk to the desk or give it tasks?' }),
        h('a', { class: 'btn', href: '/#desk', text: 'Log in to chat' }));
    }
    const input = h('textarea', { class: 'dk-input', rows: 2, maxlength: 2000,
      placeholder: 'Message the desk… @Charts plan SOL · @Chief run a session · "open a long on ETH" · "close #2" · "switch to aggressive"' });
    input.value = this.drafts.group || '';
    input.addEventListener('input', () => { this.drafts.group = input.value; });
    const send = () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = ''; this.drafts.group = '';
      const pending = { id: 'p' + Date.now(), author: 'you', kind: 'chat', text, ts: Date.now(), channel: 'group', pending: true };
      this.data.messages.push(pending); this.busy.group = (this.busy.group || 0) + 1;
      this.render();
      post('/api/desk/chat', { text })
        .catch((e) => toast(e.message, 'err'))
        .finally(() => { this.busy.group -= 1; this.data.messages = this.data.messages.filter((m) => m !== pending); this.refresh(); });
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
    const note = this.busy.group ? h('span', { class: 'sub dk-typing', text: 'the desk is answering ' + this.busy.group + ' message' + (this.busy.group > 1 ? 's' : '') + '… you can keep typing or switch chats' }) : null;
    return h('div', {}, note ? h('div', { class: 'dk-note-row' }, note) : null, h('div', { class: 'dk-compose' }, input, h('button', { class: 'btn', text: 'Send', onclick: send })));
  },
  appendMsg(m) { const log = document.getElementById('dk-log'); if (!log) return; log.append(this.msg(m)); log.scrollTop = log.scrollHeight; },
  msg(m) {
    const a = this.who(m.author), data = m.data || {};
    const extras = [];
    if (data.table) extras.push(this.scanTable(data.table));
    if (data.checks && data.checks.problems && data.checks.problems.length === 0 && data.checks.facts) extras.push(h('div', { class: 'dk-ok', text: '✔ desk checks passed · R:R ' + data.checks.facts.rr + ' · stop ' + (data.checks.facts.stop_atr1h ?? '?') + '× 1h ATR' }));
    if (data.verdict) extras.push(pill('VERDICT ' + data.verdict, { BUY: 'up', WAIT: 'warn', SKIP: 'down' }[data.verdict] || 'ghost'));
    if (data.trade && (m.kind === 'trade')) extras.push(this.tradeCard(data.trade));
    if (data.tools && data.tools.length) extras.push(h('div', { class: 'dk-tools sub', text: '⚙ used ' + [...new Set(data.tools)].join(', ').replace(/_/g, ' ') }));
    const cls = 'dk-msg' + (m.author === 'chief' && (m.kind === 'decision' || m.kind === 'recap') ? ' dk-decision' : '') + (m.kind === 'error' ? ' dk-err' : '') + (m.author === 'you' ? ' dk-you' : '');
    return h('div', { class: cls, style: `--agent:${a.color}` }, this.avatar(m.author),
      h('div', { class: 'dk-bubble' }, h('div', { class: 'dk-meta' }, h('b', { text: a.name }), h('span', { class: 'sub', text: this.when(m.ts) + (m.kind === 'decision' && data.decision ? ' · ' + data.decision.split(' -- ')[0] : '') })),
        h('div', { class: 'dk-text', text: m.text }), ...extras));
  },
  scanTable(rows) {
    return h('table', { class: 'dk-table' }, h('thead', {}, h('tr', {}, ...['coin', 'price', '24h', 'rel vol 1h / 4h', 'RSI 1h / 4h', 'trend 1h / 4h', 'flags'].map((t) => h('th', { text: t })))),
      h('tbody', {}, ...rows.map((r) => h('tr', {}, h('td', { text: r.coin }), h('td', { class: 'num', text: this.px(r.price) }),
        h('td', { class: 'num ' + signClass(r.change_24h_pct), text: fmtPct(r.change_24h_pct) }),
        h('td', { class: 'num', text: fmtNum((r['1h'] || {}).rel_volume) + ' / ' + fmtNum((r['4h'] || {}).rel_volume) }),
        h('td', { class: 'num', text: fmtNum((r['1h'] || {}).rsi14, 0) + ' / ' + fmtNum((r['4h'] || {}).rsi14, 0) }),
        h('td', { text: ((r['1h'] || {}).trend || '') + ' / ' + ((r['4h'] || {}).trend || '') }),
        h('td', { class: 'sub', text: (r.flags || []).join(', ').replace(/_/g, ' ') })))));
  },
  tradeCard(t) {
    const entry = t.entry ?? t.entry_req;
    const card = h('div', { class: 'dk-trade ' + (t.side === 'long' ? 'long' : 'short') },
      h('div', { class: 'dk-trade-h' }, h('b', { text: '#' + t.id + ' ' + String(t.side).toUpperCase() + ' ' + t.coin }), pill(t.status, t.status === 'open' ? 'up' : t.status === 'closed' ? (t.pnl >= 0 ? 'up' : 'down') : 'ghost')),
      h('div', { class: 'dk-trade-n' }, ...[['entry', entry], ['stop', t.stop], ['target', t.target]].map(([k, v]) => h('span', {}, h('small', { text: k }), h('b', { text: this.px(v) }))),
        h('span', {}, h('small', { text: 'risk' }), h('b', { text: this.money(t.risk_usd) })),
        t.status === 'closed' ? h('span', {}, h('small', { text: 'result' }), h('b', { class: signClass(t.pnl), text: this.money(t.pnl) + ' (' + (t.r >= 0 ? '+' : '') + fmtNum(t.r) + 'R)' })) : null));
    const box = h('div', { class: 'dk-mini' });
    card.append(box);
    if (t.chart && t.chart.length) box.append(this.mini(t.chart, t));
    else api('/api/public/desk/candles?coin=' + encodeURIComponent(t.coin) + '&tf=1h&limit=60').then((r) => box.append(this.mini(r.candles, t))).catch(() => {});
    return card;
  },
  mini(candles, t) {
    const W = 400, H = 130, P = 4, LBL = 112;
    const lv = [t.entry ?? t.entry_req, t.stop, t.target].filter(isNum);
    const lo = Math.min(...candles.map((c) => c[3]), ...lv), hi = Math.max(...candles.map((c) => c[2]), ...lv);
    const y = (v) => P + (H - 2 * P) * (1 - (v - lo) / ((hi - lo) || 1));
    const bw = (W - LBL) / candles.length;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'dk-svg'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', t.coin + ' 1h candles with entry, stop and target');
    const el = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); svg.append(e); return e; };
    candles.forEach((c, i) => {
      const x = i * bw + bw / 2, up = c[4] >= c[1];
      el('line', { x1: x, x2: x, y1: y(c[2]), y2: y(c[3]), class: up ? 'dk-c-up' : 'dk-c-dn' });
      el('rect', { x: x - bw * 0.35, width: Math.max(1, bw * 0.7), y: y(Math.max(c[1], c[4])), height: Math.max(1, Math.abs(y(c[1]) - y(c[4]))), class: up ? 'dk-c-up' : 'dk-c-dn' });
    });
    for (const [k, v, cls] of [['entry', t.entry ?? t.entry_req, 'dk-l-entry'], ['stop', t.stop, 'dk-l-stop'], ['target', t.target, 'dk-l-target']]) {
      if (!isNum(v)) continue;
      el('line', { x1: 0, x2: W - LBL + 4, y1: y(v), y2: y(v), class: cls });
      const tx = el('text', { x: W - LBL + 8, y: y(v) + 3, class: cls + ' dk-l-txt' }); tx.textContent = k + ' ' + this.px(v);
    }
    return svg;
  },

  // -- direct messages -----------------------------------------------------------------------------------------------
  async openAgent(id) {
    if (!this.priv()) { const a = this.who(id); toast(a.name + ': ' + a.role + '. Log in (button under the chat) to message ' + a.name + '.'); return; }
    this.agent = id;
    this.render();                                            // show the cached thread at once
    try {
      const msgs = (await api('/api/desk/dm/' + encodeURIComponent(id))).messages;
      const local = (this.dms[id] || []).filter((m) => m.pending);
      this.dms[id] = msgs.concat(local);
      if (this.agent === id) this.render();
    } catch (e) { toast(e.message, 'err'); }
  },
  dmView() {
    const a = this.who(this.agent), msgs = this.dms[this.agent] || [];
    const input = h('textarea', { class: 'dk-input', rows: 2, maxlength: 2000, placeholder: 'Ask ' + a.name + ' something or give it a task… (Enter to send)' });
    const agent = this.agent;
    input.value = this.drafts[agent] || '';
    input.addEventListener('input', () => { this.drafts[agent] = input.value; });
    const send = () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = ''; this.drafts[agent] = '';
      const mine = { id: 'p' + Date.now(), author: 'you', text, ts: Date.now(), channel: 'dm:' + agent, pending: true };
      const typing = { id: 't' + Date.now(), author: agent, text: a.name + ' is working on it…', ts: Date.now(), kind: 'typing', pending: true };
      (this.dms[agent] = this.dms[agent] || []).push(mine, typing);
      this.busy[agent] = (this.busy[agent] || 0) + 1;
      this.render();
      post('/api/desk/dm', { agent, text })
        .catch((e) => toast(a.name + ': ' + e.message, 'err'))
        .finally(async () => {
          this.busy[agent] -= 1;
          try { this.dms[agent] = (await api('/api/desk/dm/' + encodeURIComponent(agent))).messages.concat((this.dms[agent] || []).filter((m) => m.pending && m !== mine && m !== typing)); }
          catch (e) { this.dms[agent] = (this.dms[agent] || []).filter((m) => m !== typing); }
          if (this.agent === agent) this.render(); else { this.render(); toast(a.name + ' replied'); }
        });
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
    setTimeout(() => input.focus(), 0);
    return h('div', { class: 'card dk-chat' }, h('div', { class: 'dk-dm-h' }, this.avatar(this.agent), h('div', {}, h('b', { text: 'Direct message · ' + a.name }), h('div', { class: 'sub', text: a.role }))),
      h('div', { class: 'dk-log', id: 'dk-log' }, ...(msgs.length ? msgs.map((m) => this.msg(m)) : [h('p', { class: 'sub', text: 'Ask about any coin in the universe, the ledger, or why the desk made a call.' })])),
      h('div', { class: 'dk-compose' }, input, h('button', { class: 'btn', text: 'Send', onclick: send })));
  },
  async runNow() {
    try { const r = await post('/api/desk/run', {}); toast(r.started ? 'Session started: watch the chat' : (r.error || 'not started')); this.tab = 'chat'; this.agent = null; setTimeout(() => this.refresh(), 1500); }
    catch (e) { toast(e.message, 'err'); }
  },

  // -- ledger -------------------------------------------------------------------------------------------------------
  ledgerView() {
    const L = (this.data || {}).ledger || {};
    const tile = (lbl, val, cls, sub) => h('div', { class: 'tile' }, h('span', { class: 'lbl', text: lbl }), h('span', { class: 'val ' + (cls || ''), text: val }), sub ? h('span', { class: 'sub', text: sub }) : null);
    const kpis = h('div', { class: 'tiles' }, tile('equity', this.money(L.equity), signClass((L.equity || 0) - (L.start_balance || 0)), 'start ' + this.money(L.start_balance)),
      tile('return', fmtPct(L.return_pct), signClass(L.return_pct)), tile('win rate', isNum(L.win_rate) ? (L.win_rate * 100).toFixed(0) + '%' : DASH, '', (L.closed_count || 0) + ' closed'),
      tile('avg R', isNum(L.avg_r) ? (L.avg_r >= 0 ? '+' : '') + L.avg_r.toFixed(2) + 'R' : DASH, signClass(L.avg_r)),
      tile('open', String((L.open || []).length) + ' / ' + ((L.rules || {}).max_open ?? 3), '', 'risk ' + ((L.rules || {}).risk_pct ?? '?') + '% · fee ' + ((L.rules || {}).fee_pct ?? '?') + '%/side'));
    const first = (L.equity_curve || [])[0];
    const curve = this.curve([[first ? first.ts - 3_600_000 : Date.now() - 3_600_000, L.start_balance], ...(L.equity_curve || []).map((p) => [p.ts, p.equity]), [Date.now(), L.equity]].filter((p) => isNum(p[1])));
    const row = (t, closed) => h('tr', {}, h('td', { text: '#' + t.id }), h('td', { text: t.coin }), h('td', { class: t.side === 'long' ? 'up' : 'down', text: t.side }),
      h('td', { class: 'num', text: this.px(t.entry ?? t.entry_req) }), h('td', { class: 'num', text: this.px(t.stop) }), h('td', { class: 'num', text: this.px(t.target) }),
      closed ? h('td', { class: 'num', text: this.px(t.exit) + ' (' + (t.exit_reason || '') + ')' }) : h('td', { class: 'num', text: this.px(t.mark) }),
      h('td', { class: 'num ' + signClass(closed ? t.pnl : t.upnl), text: closed ? this.money(t.pnl) + ' · ' + fmtNum(t.r) + 'R' : (t.status === 'pending' ? 'pending limit' : this.money(t.upnl)) }),
      h('td', { class: 'sub', text: fmtTime(closed ? t.closed_ts : t.created_ts) }));
    const table = (title, rows, closed) => h('div', { class: 'card' }, h('h3', { text: title }), rows.length ? h('table', { class: 'dk-table' },
      h('thead', {}, h('tr', {}, ...['#', 'coin', 'side', 'entry', 'stop', 'target', closed ? 'exit' : 'mark', closed ? 'P&L' : 'unrealized', 'time'].map((x) => h('th', { text: x })))),
      h('tbody', {}, ...rows.map((t) => row(t, closed)))) : h('p', { class: 'sub', text: closed ? 'No closed trades yet.' : 'No open trades.' }));
    return h('div', {}, kpis, h('div', { class: 'card' }, h('h3', { text: 'Equity' }), curve), table('Open positions', L.open || [], false), table('Closed trades', L.closed || [], true));
  },
  curve(pts) {
    const W = 640, H = 140, ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'dk-svg dk-curve');
    if (pts.length < 2) return svg;
    const vs = pts.map((p) => p[1]), lo = Math.min(...vs), hi = Math.max(...vs);
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const x = (t, i) => (t1 > t0 ? (t - t0) / (t1 - t0) : i / (pts.length - 1)) * W;   // by time, not by index
    const d = pts.map((p, i) => (i ? 'L' : 'M') + x(p[0], i).toFixed(1) + ' ' + (6 + (H - 12) * (1 - (p[1] - lo) / ((hi - lo) || 1))).toFixed(1)).join(' ');
    const path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); path.setAttribute('class', vs[vs.length - 1] >= vs[0] ? 'dk-eq-up' : 'dk-eq-dn');
    svg.append(path);
    return svg;
  },

  // -- decisions -----------------------------------------------------------------------------------------------------
  decisionsView() {
    const rows = (this.data || {}).sessions || [];
    const box = h('div', { class: 'card dk-logview', id: 'dk-logview' }, h('p', { class: 'sub', text: 'Pick a session to read its decision log.' }));
    const open = async (sid) => {
      try { const r = this.logs[sid] || (this.logs[sid] = (await api('/api/public/desk/session/' + encodeURIComponent(sid))).session); box.replaceChildren(...this.md(r.log_md || '')); }
      catch (e) { toast(e.message, 'err'); }
    };
    return h('div', { class: 'dk-dec' }, h('div', { class: 'card' }, h('h3', { text: 'Sessions' }), rows.length ? h('table', { class: 'dk-table' },
      h('thead', {}, h('tr', {}, ...['started', 'coin', 'Skeptic', 'decision', 'AI cost'].map((x) => h('th', { text: x })))),
      h('tbody', {}, ...rows.map((s) => h('tr', { class: 'dk-click', onclick: () => open(s.id) }, h('td', { text: new Date(s.started_ts).toLocaleString('en-GB', { hour12: false }) }),
        h('td', { text: s.coin || DASH }), h('td', {}, s.verdict ? pill(s.verdict, { BUY: 'up', WAIT: 'warn', SKIP: 'down' }[s.verdict]) : DASH),
        h('td', { class: (s.decision || '').startsWith('TRADE') ? 'up' : 'sub', text: (s.decision || s.status || '').slice(0, 90) }),
        h('td', { class: 'num', text: isNum(s.cost_usd) ? '$' + s.cost_usd.toFixed(3) : DASH }))))) : h('p', { class: 'sub', text: 'No sessions yet.' })), box);
  },
  md(text) {
    return String(text).split(/\n{2,}/).map((block) => {
      const b = block.trim();
      if (b.startsWith('# ')) return h('h3', { text: b.slice(2) });
      if (b.startsWith('## ')) return h('h4', { text: b.slice(3) });
      if (/^- /m.test(b) && b.split('\n').every((l) => l.startsWith('- '))) return h('ul', {}, ...b.split('\n').map((l) => h('li', { text: l.slice(2) })));
      return h('p', { class: 'dk-text', text: b });
    });
  },

  // -- settings (private) ------------------------------------------------------------------------------------------------
  settingsView() {
    const s = (this.data || {}).settings || {}, n = s.notify || {};
    const f = {};
    const field = (label, input, help) => h('label', { class: 'dk-field' }, h('span', { text: label }), input, help ? h('small', { class: 'sub', text: help }) : null);
    f.enabled = h('input', { type: 'checkbox' }); f.enabled.checked = !!s.enabled;
    f.schedule = h('select', {}, ...[1, 2, 4, 6, 8, 12, 24].map((x) => h('option', { value: x, text: 'every ' + x + 'h', selected: x === s.schedule_hours })));
    f.universe = h('input', { type: 'text', value: (s.universe || []).join(', ') });
    f.mode = h('select', {}, ...[['cautious', 'Cautious: trade only on a Skeptic BUY, min 2:1'], ['aggressive', 'Aggressive: trade unless the Skeptic names a deal-breaker, min 1.5:1']].map(([v, t]) => h('option', { value: v, text: t, selected: v === s.mode })));
    f.risk = h('input', { type: 'number', min: 0.25, max: 5, step: 0.25, value: s.risk_pct });
    f.plans = h('select', {}, ...[1, 2, 3].map((x) => h('option', { value: x, text: x + (x === 1 ? ' plan' : ' plans') + ' per session', selected: x === (s.max_plans || 3) })));
    f.maxOpen = h('input', { type: 'number', min: 1, max: 6, step: 1, value: s.max_open || 3 });
    f.balance = h('input', { type: 'number', min: 100, step: 100, value: s.start_balance });
    f.recap = h('input', { type: 'number', min: 0, max: 23, value: s.recap_hour });
    f.tz = h('input', { type: 'text', value: s.tz || '' });
    f.tg = h('input', { type: 'checkbox' }); f.tg.checked = !!n.telegram;
    f.app = h('input', { type: 'checkbox' }); f.app.checked = n.in_app !== false;
    const save = async () => {
      try {
        await post('/api/desk/settings', { enabled: f.enabled.checked, schedule_hours: +f.schedule.value, universe: f.universe.value, mode: f.mode.value,
          risk_pct: +f.risk.value, max_plans: +f.plans.value, max_open: +f.maxOpen.value, start_balance: +f.balance.value, recap_hour: +f.recap.value, tz: f.tz.value.trim(),
          notify: { in_app: f.app.checked, telegram: f.tg.checked, email: false } });
        toast('Desk settings saved'); this.refresh();
      } catch (e) { toast(e.message, 'err'); }
    };
    return h('div', { class: 'card dk-settings' }, h('h3', { text: 'Desk settings' }),
      field('Scheduled sessions on', f.enabled), field('Schedule', f.schedule, 'aligned to UTC, 2 minutes after the candle closes'),
      field('Coin universe', f.universe, 'USDT pairs, comma separated (max 20)'), field('Mode', f.mode),
      field('Risk per trade (% of equity, fees included)', f.risk), field('Trades Charts may propose each session', f.plans, 'each one is checked and gets its own Skeptic verdict'),
      field('Max open trades', f.maxOpen, 'each trade is sized to fit equity / this number (no leverage)'), field('Starting balance (USD)', f.balance, 'only before the first trade'),
      field('Daily recap hour (local)', f.recap), field('Time zone', f.tz, 'e.g. Asia/Colombo'),
      field('Telegram alerts (trade open/close + recap)', f.tg), field('In-app alerts', f.app, 'Email alerts are not available: no mail account is configured.'),
      h('button', { class: 'btn', text: 'Save', onclick: save }));
  },
};
