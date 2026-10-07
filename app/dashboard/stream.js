/* PaperLab - realtime transport: WebSocket first, server-sent events as fallback.

   The browser never polls for live state. The server pushes every change over one connection:

     WebSocket  /api/ws (private) or /api/public/ws (public)   primary
     SSE        /api/stream or /api/public/stream              automatic fallback when a network
                                                               blocks WebSockets (3 failed opens)

   Both carry the same events with the same ids. On (re)connect the client sends its last event id,
   is replayed what it missed, and receives a fresh greeting (a full state snapshot on the private
   dashboard). The private socket authenticates in its FIRST MESSAGE -- a browser cannot put an
   Authorization header on a WebSocket, and a credential must never travel in a URL.

   Stream.on(channel, fn)          'state' 'fill' 'signal' 'note' 'equity' 'competition' 'health'
                                   'shadow' 'shadow_state' 'jev' 'hello' 'unauthorized' '*'
   Stream.onStatus(fn)             {status: CONNECTING|LIVE|RETRYING|OFF, transport, ...}
   Stream.start({ws, sse}, authFn) begin; authFn() returns the Authorization value or null
   Stream.stop() / Stream.resync() */
'use strict';

const Stream = {
  paths: null, authFn: null, ws: null, ctrl: null, lastId: null, running: false, status: 'OFF',
  transport: 'websocket', wsFailures: 0, connectedAt: 0, lastEventAt: 0, retries: 0, received: 0,
  handlers: {}, statusHandlers: [], watchdog: 0,

  on(channel, fn) { (this.handlers[channel] = this.handlers[channel] || []).push(fn); return this; },
  onStatus(fn) { this.statusHandlers.push(fn); fn(this.info()); return this; },
  info() {
    return { status: this.status, transport: this.transport, connectedAt: this.connectedAt, lastEventAt: this.lastEventAt,
      retries: this.retries, received: this.received };
  },
  setStatus(s) {
    if (this.status === s) return;
    this.status = s;
    this.statusHandlers.forEach((fn) => { try { fn(this.info()); } catch (e) { console.error(e); } });
  },

  start(paths, authFn) {
    this.stop();
    this.paths = paths; this.authFn = authFn || null; this.running = true; this.retries = 0;
    this.transport = window.WebSocket ? 'websocket' : 'sse';
    this.wsFailures = 0;
    clearInterval(this.watchdog);
    // A connection that says nothing for 45 s (the server pings every 15 s) is dead: reconnect.
    this.watchdog = setInterval(() => {
      if (this.status === 'LIVE' && this.lastEventAt && Date.now() - this.lastEventAt > 45000) this.resync();
    }, 5000);
    this.loop();
  },
  stop() {
    this.running = false;
    clearInterval(this.watchdog);
    this.closeCurrent();
    this.lastId = null;
    this.setStatus('OFF');
  },
  closeCurrent() {
    if (this.ws) { try { this.ws.close(); } catch (e) { /* already closed */ } }
    if (this.ctrl) { try { this.ctrl.abort(); } catch (e) { /* already closed */ } }
    this.ws = null; this.ctrl = null;
  },
  /** Drop the connection and reconnect now (a state delta arrived out of sequence, or silence). */
  resync() { this.closeCurrent(); },

  dispatch(channel, data, id) {
    this.lastEventAt = Date.now(); this.received++;
    for (const fn of (this.handlers[channel] || [])) { try { fn(data, id); } catch (e) { console.error('[stream] ' + channel + ' handler failed', e); } }
    for (const fn of (this.handlers['*'] || [])) { try { fn(channel, data, id); } catch (e) { console.error(e); } }
  },

  async loop() {
    while (this.running) {
      this.setStatus(this.retries ? 'RETRYING' : 'CONNECTING');
      let opened = false;
      try {
        opened = this.transport === 'websocket' ? await this.runWS() : await this.runSSE();
      } catch (e) {
        if (e && e.name !== 'AbortError') console.warn('[stream] ' + this.transport + ' dropped:', e.message || e);
      }
      if (!this.running) return;
      if (this.transport === 'websocket') {
        this.wsFailures = opened ? 0 : this.wsFailures + 1;
        if (this.wsFailures >= 3 && this.paths.sse) {
          console.warn('[stream] WebSocket unavailable here; falling back to server-sent events');
          this.transport = 'sse';
        }
      }
      this.retries = opened ? 1 : this.retries + 1;
      this.setStatus('RETRYING');
      const wait = Math.min(15000, 500 * 2 ** Math.min(this.retries, 5)) * (0.8 + Math.random() * 0.4);
      await new Promise((r) => setTimeout(r, wait));
    }
  },

  /** One WebSocket session. Resolves when it closes; true if it ever got to LIVE. */
  runWS() {
    return new Promise((resolve) => {
      let live = false, settled = false;
      const done = () => { if (!settled) { settled = true; resolve(live); } };
      const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + this.paths.ws;
      let ws;
      try { ws = new WebSocket(url); } catch (e) { done(); return; }
      this.ws = ws;
      ws.onopen = () => {
        const hello = { type: 'hello', last_id: this.lastId };
        const auth = this.authFn ? this.authFn() : null;
        if (auth) hello.authorization = auth;
        ws.send(JSON.stringify(hello));
      };
      ws.onmessage = (m) => {
        let msg;
        try { msg = JSON.parse(m.data); } catch (e) { return; }
        if (msg.event === 'unauthorized') { this.running = false; this.setStatus('OFF'); this.dispatch('unauthorized', {}); return; }
        if (msg.id != null) this.lastId = msg.id;
        if (msg.event === 'hello') {
          live = true; this.retries = 0; this.connectedAt = Date.now();
          this.setStatus('LIVE');
          this.statusHandlers.forEach((fn) => { try { fn(this.info()); } catch (e) { /* ignore */ } });
        }
        this.dispatch(msg.event, msg.data || {}, msg.id);
      };
      ws.onerror = () => { /* onclose follows */ };
      ws.onclose = (ev) => {
        if (ev.code === 4401) { this.running = false; this.setStatus('OFF'); this.dispatch('unauthorized', {}); }
        if (this.ws === ws) this.ws = null;
        done();
      };
    });
  },

  /** One SSE session over fetch (so the private stream can carry Basic auth). */
  async runSSE() {
    this.ctrl = new AbortController();
    const headers = { Accept: 'text/event-stream' };
    const auth = this.authFn ? this.authFn() : null;
    if (auth) headers.Authorization = auth;
    if (this.lastId != null) headers['Last-Event-ID'] = String(this.lastId);
    const res = await fetch(this.paths.sse, { headers, cache: 'no-store', signal: this.ctrl.signal });
    if (res.status === 401) { this.running = false; this.setStatus('OFF'); this.dispatch('unauthorized', {}); return false; }
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
    this.retries = 0; this.connectedAt = Date.now(); this.lastEventAt = Date.now();
    this.setStatus('LIVE');
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return true;
      this.lastEventAt = Date.now();
      buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, '\n');
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        this.sseFrame(frame);
      }
    }
  },
  sseFrame(text) {
    let event = 'message', id = null; const data = [];
    for (const line of text.split('\n')) {
      if (!line || line[0] === ':') continue;               // comment = heartbeat
      const c = line.indexOf(':');
      const k = c < 0 ? line : line.slice(0, c), v = c < 0 ? '' : line.slice(c + 1).replace(/^ /, '');
      if (k === 'event') event = v; else if (k === 'data') data.push(v); else if (k === 'id') id = v;
    }
    if (id != null && id !== '') { const n = parseInt(id, 10); if (Number.isFinite(n)) this.lastId = n; }
    if (!data.length) return;
    let parsed = null;
    try { parsed = JSON.parse(data.join('\n')); } catch (e) { return; }
    this.dispatch(event, parsed, id);
  },
};

/** Briefly highlight a value that just changed (live trading-UI cue). `dir` > 0 up, < 0 down. */
function flash(el, dir) {
  if (!el) return;
  el.classList.remove('flash-up', 'flash-down', 'flash');
  void el.offsetWidth;                      // restart the animation
  el.classList.add(dir > 0 ? 'flash-up' : dir < 0 ? 'flash-down' : 'flash');
}
