"""Telegram notifications for the paper bots (operator, 2026-10-02): every trade a bot takes, its take-profits and its
result, posted to the operator's Telegram bot -- each update REPLIES to the trade's own opening message.

    open     a new message: bot, coin, side, entry, size, risk, the stop-loss and every take-profit (with how much of the
             position each one closes)
    tp       a reply to that message: TP1 / TP2 hit, what it booked, what is left and where the stop now sits
    closed   a reply to that message: the exit (take-profit, stop, break-even, time, ...), the result in USDT / R, the
             fees and how long it was held

Read-only and paper-only: it listens to the forward services' public events and never touches a trading path. The bot
token is a secret: it comes from the encrypted key vault (System -> Providers & Live -> "Telegram bot") or the Railway
variable TELEGRAM_BOT_TOKEN, and it is never logged -- every error is redacted before it is recorded. The chat is the
one in TELEGRAM_CHAT_ID / the vault's second field, or the first private chat that sends /start to the bot (stored, so
it survives restarts); any other chat is told the bot is private. Commands: /start (link), /settings (the default
take-profit and stop-loss rules of every program), /help.

Events from history replays (rederived) and counterfactual trades are never sent. Optional filters: TELEGRAM_FILTER,
per program, e.g. "v11,v12,v8:CONTROL" (every V11 and V12 bot, only V8's CONTROL bots; roles CONTROL / JEV / LADDER,
several as "v8:CONTROL|JEV"); or the coarser TELEGRAM_PROGRAMS (e.g. "v11,v12,v8") and TELEGRAM_ROLES (e.g. "CONTROL").
Default: every program, every role. Sending is
paced (Telegram allows about one message a second to one chat) and a 429 is retried after the delay Telegram asks for.
"""
from __future__ import annotations

import html
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

log = logging.getLogger("paperlab.live.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
PROVIDER = ("telegram", "data")
SEND_EVERY_S = 1.1


# -- formatting (pure) --------------------------------------------------------------------------------------------------
def px(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    a = abs(v)
    return f"{v:,.2f}" if a >= 100 else f"{v:.4f}" if a >= 1 else f"{v:.4g}"






def ccy_of(program: str) -> str:
    return "USD" if program == "v9" else "USDT"


def coin_of(program: str, symbol: str) -> str:
    return symbol[:-4] if program != "v9" and symbol.endswith("USDT") else symbol


def hold_text(seconds: Any) -> str:
    s = int(seconds or 0)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"




def thread_key(program: str, data: Mapping[str, Any]) -> str:
    """The same key for a trade's open, take-profit and close events, stable across restarts (the fill time)."""
    entry = data.get("ts") if data.get("type") == "open" else data.get("entry_ts")
    return f"{program}|{data.get('bot_key')}|{data.get('symbol')}|{data.get('side')}|{int(entry or 0)}"


def who(program: str, data: Mapping[str, Any], name: str | None) -> str:
    return html.escape(name or str(data.get("bot_key") or ""))


EXIT_SIMPLE = {"tp": "hit the target", "stop": "hit the stop", "be": "safety stop after a target",
               "time": "time limit", "trail": "trailing stop", "liq": "liquidated", "manual": "closed",
               "reset": "day reset", "end_of_run": "closed"}


def px_as(v: Any, ref: Any) -> str:
    """A price shown with the same number of decimals as the entry, so 0.2350 lines up with 0.2344."""
    if v is None:
        return "—"
    r = px(ref)
    dec = len(r.split(".")[1]) if "." in r else 0
    return f"{float(v):,.{dec}f}"


def pc(v: float, ref: float) -> str:
    """A move in percent, one decimal (two under 0.1%), with a real minus sign."""
    if not ref:
        return ""
    x = (v / ref - 1) * 100
    return f"{'+' if x >= 0 else '−'}{abs(x):.{2 if abs(x) < 0.1 else 1}f}%"


def cash(v: Any, ccy: str) -> str:
    v = float(v or 0.0)
    return f"{'+' if v >= 0 else '−'}{abs(v):.{2 if abs(v) >= 0.1 else 3}f} {ccy}"




def pair_tag(program: str, symbol: str) -> str:
    """#SOL/USDT for a crypto perp (a tappable hashtag in Telegram), #AAPL for a stock."""
    coin = html.escape(coin_of(program, symbol))
    return f"#{coin}/USDT" if program != "v9" and symbol.endswith("USDT") else f"#{coin}"


def bot_line(name: str | None, stats: tuple[int, int] | None) -> str | None:
    if not name and not stats:
        return None
    nm = html.escape(name or "bot")
    if not stats:
        return f"🤖 {nm}"
    n, w = stats
    return f"🤖 {nm} · first trade" if n <= 0 else f"🤖 {nm} · win rate <b>{w / n * 100:.0f}%</b> ({w} of {n})"


def confidence_line(conf: tuple[int, int, str] | None) -> str:
    """Confidence: 37% ▰▰▰▰▱▱▱▱▱▱ (451 past trades) -- the setup's measured win chance (app/live/trade_stats.py)."""
    if not conf:
        return "Confidence: building up (too few past trades of this setup)"
    pct, n, scope = conf
    full = max(0, min(10, round(pct / 10)))
    return f"Confidence: <b>{pct}%</b> {'▰' * full}{'▱' * (10 - full)} ({n:,} past trades" + \
        (", last 30 days)" if scope == "last 30 days" else ")")


def fmt_open(program: str, d: Mapping[str, Any], name: str | None, stats: tuple[int, int] | None = None,
             conf: tuple[int, int, str] | None | bool = False) -> str:
    """The signal-channel layout (operator, 2026-10-03), sent as the caption of the opening chart:

        #SOL/USDT - Long 🟢
        (blank)
        Entry: 117.92
        Stop Loss: 116.50 (−1.2%)
        (blank)
        Target 1: 118.63 (+0.6%)  ...
        (blank)
        Leverage: x7
        🤖 Xyla · win rate 29% (2 of 7)"""
    long = str(d.get("side") or "") == "long"
    entry = float(d.get("price") or 0.0)
    lines = [f"{pair_tag(program, str(d.get('symbol') or ''))} - {'Long 🟢' if long else 'Short 🔴'}", "",
             f"Entry: <code>{px(entry)}</code>"]
    stop = d.get("stop")
    if stop:
        lines.append(f"Stop Loss: <code>{px_as(stop, entry)}</code> ({pc(float(stop), entry)})")
    lines.append("")
    tps = [float(x) for x in (d.get("tps") or ([d["target"]] if d.get("target") else []))]
    if len(tps) == 1:
        lines.append(f"Target: <code>{px_as(tps[0], entry)}</code> ({pc(tps[0], entry)})")
    for i, t in enumerate(tps if len(tps) > 1 else [], 1):
        lines.append(f"Target {i}: <code>{px_as(t, entry)}</code> ({pc(t, entry)})")
    if not tps:
        lines.append("Target: the day close (no fixed target)")
    lines.append("")
    if d.get("leverage"):
        lines.append(f"Leverage: x{int(float(d['leverage']))}")
    if conf is not False:                       # False: no confidence source configured (tests, other callers)
        lines.append(confidence_line(conf or None))
    bl = bot_line(name, stats)
    if bl:
        lines.append(bl)
    return "\n".join(lines).rstrip()


def fmt_tp(program: str, d: Mapping[str, Any], name: str | None, stats: tuple[int, int] | None = None) -> str:
    """✅ #SOL/USDT - Target 1 hit (+0.6%) / Stop moved to: 117.95 (profit locked)."""
    i = d.get("tp_index")
    entry = float(d.get("entry_price") or 0.0)
    gain = f" ({pc(float(d['price']), entry)})" if entry and d.get("price") else ""
    lines = [f"✅ {pair_tag(program, str(d.get('symbol') or ''))} - Target{' ' + str(i) if i else ''} hit{gain}"]
    stop = d.get("stop")
    if stop and entry:
        stop = float(stop)
        long = str(d.get("side") or "") == "long"
        locked = stop > entry if long else stop < entry
        lines.append(f"Stop moved to: <code>{px_as(stop, entry)}</code>" + (" (profit locked)" if locked else ""))
    return "\n".join(lines)


def fmt_closed(program: str, d: Mapping[str, Any], name: str | None, stats: tuple[int, int] | None = None) -> str:
    """💰 #SOL/USDT - WIN +0.12 USDT / Hit the target · held 42m / 🤖 name · win rate."""
    ccy = ccy_of(program)
    net = float(d.get("net") or 0.0)
    r = d.get("r_net") if d.get("r_net") is not None else d.get("r")
    kind = str(d.get("exit_kind") or "")
    tag = pair_tag(program, str(d.get("symbol") or ""))
    lines = [f"💰 {tag} - <b>WIN {cash(net, ccy)}</b>" if net > 0 else f"🔻 {tag} - <b>LOSS {cash(net, ccy)}</b>",
             f"{EXIT_SIMPLE.get(kind, kind or 'closed').capitalize()} · held {hold_text(d.get('hold_s'))}"]
    if r is not None and float(r) <= -1.5:
        lines.append("⚠️ Bigger loss than planned (price jumped past the stop)")
    bl = bot_line(name, stats)
    if bl:
        lines.append(bl)
    return "\n".join(lines)


def settings_text() -> str:
    """The default take-profit / stop-loss rules of every program, read from the running configuration."""
    out = ["⚙️ <b>Default TP / SL settings</b> (paper bots)", ""]
    try:
        from app.strategies.v8.arena import VwapSnapV8
        from app.strategies.v8.ladder import LADDER
        p3 = VwapSnapV8.Params()
        lad = " · ".join(f"TP{i} {r:g}R closes {f * 100:.0f}%" for i, (r, f) in enumerate(LADDER, 1))
        out += ["<b>V8 Scalp</b> (V8.3 VWAP snap-back, 5m, 20 USDT books; V8.1 / V8.2 retired 2026-10-04)",
                f"• SL: the setup's extreme + {p3.stop_buffer_atr:g} ATR, {p3.min_stop_pct * 100:g}% to "
                f"{p3.max_stop_pct * 100:g}%",
                f"• TP: {p3.target_r:g}R, the whole position, resting as a limit order",
                f"• Time: {p3.max_hold_min / 60:g} h limit",
                f"• +LADDER twins: {lad} (stop → entry + fees after TP1, → TP1 after TP2)",
                "• +JEV twins: Jev may skip a trade", ""]
    except Exception:
        pass
    try:
        from app.strategies.v9.stocks import PullbackV9
        p = PullbackV9.Params()
        out += ["<b>V9 Stocks</b> (US stocks, 1,000 USD books, regular sessions only)",
                f"• SL: the setup's extreme + {p.stop_buffer_atr:g} ATR, {p.min_stop_pct * 100:g}–{p.max_stop_pct * 100:g}%",
                f"• TP: {p.target_r:g}R · time limit {p.max_hold_min:g} min · always flat before the close", ""]
    except Exception:
        pass
    try:
        from app.strategies.v11.ladder import LOCK_AFTER_TP1, SHARES, SPACING
        from app.strategies.v11.scan import load_v11_scanners
        names = {"V11.1": "breakouts", "V11.2": "pullbacks", "V11.3": "crash catcher", "V11.4": "hourly reversal"}
        out.append("<b>V11 Scan</b> (30 coins, 20 USDT books, up to 8 coins at once)")
        for sid, cls in load_v11_scanners().items():
            k, sh, lock = SPACING[sid], SHARES[sid], LOCK_AFTER_TP1[sid]
            p = cls.Params()
            lock_txt = "entry + fees" if not lock else f"{lock:.0%} of the way to TP1"
            out.append(f"• {sid} {names.get(sid, '')}: SL ≥ {p.min_stop_pct * 100:g}% · TP1 {k:.2g}R / TP2 {2 * k:.2g}R / "
                       f"TP3 {3 * k:.2g}R closing {sh[0]:.0%} / {sh[1]:.0%} / {sh[2]:.0%} · after TP1 the SL → {lock_txt}, "
                       f"after TP2 → TP1 · time limit "
                       + (f"{p.max_hold_min:g} min" if p.max_hold_min < 60 else f"{p.max_hold_min / 60:g} h"))
        out += ["• AI twins: Jev may skip, and its confidence stretches the TPs", ""]
    except Exception:
        pass
    out += ["<b>V12 Bizzy</b> (beebots' Bizzy Bee, ETH / SOL / HYPE, 20 USDT books)",
            "• Entry: above today's UTC open + ½ yesterday's range · full size 2x · one trade a day",
            "• SL: today's open · no TP: out 1 minute before the UTC midnight",
            "• Bizzy AI: Jev says GO or WAIT", ""]
    try:
        from app.strategies.v13.snapback import SnapbackV13
        q = SnapbackV13.Params()
        out += ["<b>V13 Snapback · Bounce</b> (29 coins, 20 USDT book, up to 4 at once)",
                f"• Entry: a coin {q.threshold:g} daily-vol stretched from its 24 h VWAP → a LIMIT order "
                f"{q.offset_atr:g} ATR further out (maker fee), cancelled after {q.expiry_min:g} min",
                f"• SL: {q.stop_atr:g} ATR, at least {q.min_stop_pct * 100:g}% · TP: {q.target_r:g}R as a limit · "
                f"time limit {q.max_hold_min / 60:g} h", ""]
    except Exception:
        pass
    out += ["<b>V14 HTF</b> (copies of the best strategies, 20 USDT books)",
            "• The V8.3 snap-back (6 coins), the V11.1 / V11.2 scanners and V13 Snapback, each with ONE extra rule:",
            "• enter only in the direction of the 4-hour and daily trend (from 1h candles)",
            "• SL / TP / time limit: exactly the original's", ""]
    out.append("<i>All bots are paper: simulated fills on live market data.</i>")
    return "\n".join(out)


def parse_filter(spec: str) -> dict[str, set[str]] | None:
    """"v11,v12,v8:CONTROL" -> {"v11": set(), "v12": set(), "v8": {"CONTROL"}} (an empty set = every role); "" -> None."""
    out: dict[str, set[str]] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        prog, _, roles = part.partition(":")
        out[prog.strip().lower()] = {r.strip().upper() for r in roles.split("|") if r.strip()}
    return out or None


# -- the notifier ---------------------------------------------------------------------------------------------------------
class TelegramNotifier:
    def __init__(self, providers: Any, data_dir: str, names: Callable[[], Mapping[str, Any]] | None = None,
                 env: Mapping[str, str] | None = None, http: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 stats: Callable[[str, str], tuple[int, int] | None] | None = None,
                 chart: Callable[[str, Mapping[str, Any], str | None], bytes | None] | None = None,
                 upload: Callable[[str, dict[str, Any], dict[str, tuple[str, bytes, str]], float], dict[str, Any]] | None = None,
                 confidence: Callable[[str, Mapping[str, Any]], tuple[int, int, str] | None] | None = None):
        self.providers = providers
        self.confidence_fn = confidence          # (program, open event) -> (win chance %, trades, scope) | None
        self.chart_fn = chart                    # (program, open event, bot name) -> PNG of the opening chart, or None
        self.upload = upload or self._post_multipart
        self.stats_fn = stats                    # (program, bot_key) -> (closed trades, wins) in the current experiment
        self.env = os.environ if env is None else env
        self.names_fn = names
        self.http = http or self._post
        self.clock, self.sleep = clock, sleep
        self.db = sqlite3.connect(os.path.join(str(data_dir), "telegram.db"), check_same_thread=False)
        self.db_lock = threading.Lock()
        with self.db_lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS threads(key TEXT PRIMARY KEY, chat_id TEXT, message_id INTEGER, ts INTEGER)")
            self.db.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
        progs = (self.env.get("TELEGRAM_PROGRAMS") or "").strip().lower()
        roles = (self.env.get("TELEGRAM_ROLES") or "").strip().upper()
        self.programs = {p.strip() for p in progs.split(",") if p.strip()} or None
        self.roles = {r.strip() for r in roles.split(",") if r.strip()} or None
        self.rules = parse_filter(self.env.get("TELEGRAM_FILTER") or "")
        self.q: queue.Queue = queue.Queue(maxsize=20_000)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._names: tuple[float, Mapping[str, Any]] = (0.0, {})
        self.stats = {"sent": 0, "replies": 0, "dropped": 0, "errors": 0, "last_error": None, "queued": 0,
                      "last_sent_ts": None, "poll_failures": 0}

    # -- configuration -----------------------------------------------------------------------------------------------
    def _keys(self) -> tuple[str, str] | None:
        try:
            k = self.providers.keys(*PROVIDER) if self.providers is not None else None
        except Exception:
            k = None
        token = (k[0] if k else "") or (self.env.get("TELEGRAM_BOT_TOKEN") or "")
        chat = (k[1] if k else "") or (self.env.get("TELEGRAM_CHAT_ID") or "")
        return (token.strip(), chat.strip()) if token.strip() else None

    def _kv(self, k: str, v: str | None = None) -> str | None:
        with self.db_lock, self.db:
            if v is None:
                row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
                return row[0] if row else None
            self.db.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            return v

    def chat_id(self) -> str | None:
        """A configured chat id wins only if it IS one (digits, '-' for groups); anything else typed into the optional
        box is ignored, so /start can still link the operator's private chat."""
        keys = self._keys()
        if keys and re.fullmatch(r"-?\d{1,20}", keys[1]):
            return keys[1]
        return self._kv("chat_id")

    def health(self) -> dict[str, Any]:
        keys = self._keys()
        flt = ({p: sorted(r) or "all" for p, r in self.rules.items()} if self.rules is not None else
               {"programs": sorted(self.programs) if self.programs else "all", "roles": sorted(self.roles) if self.roles else "all"})
        return {"configured": bool(keys), "linked": bool(self.chat_id()), "filters": flt, **self.stats,
                "queued": self.q.qsize()}

    # -- events --------------------------------------------------------------------------------------------------------
    def on_event(self, program: str, data: Mapping[str, Any]) -> None:
        """Called from a forward service's pump thread for every published event: filter, queue, return at once."""
        kind = data.get("type")
        if kind not in ("open", "tp", "closed") or data.get("rederived") or data.get("counterfactual"):
            return
        role = str(data.get("role") or "CONTROL").upper()
        if self.rules is not None:
            if program not in self.rules or (self.rules[program] and role not in self.rules[program]):
                return
        elif (self.programs and program not in self.programs) or (self.roles and role not in self.roles):
            return
        try:
            self.q.put_nowait((program, dict(data)))
        except queue.Full:
            self.stats["dropped"] += 1

    def _name(self, program: str, key: str) -> str | None:
        if self.names_fn is None:
            return None
        at, names = self._names
        if self.clock() - at > 60:
            try:
                names = self.names_fn() or {}
            except Exception:
                names = {}
            self._names = (self.clock(), names)
        return ((names.get(f"{program}|{key}") or {}).get("name")) or None

    def alert(self, text: str) -> None:
        """A SYSTEM alert (live mirror, safe mode, outages, daily digest): never filtered by program, queued like a
        trade message. Text is HTML (callers escape their own values)."""
        try:
            self.q.put_nowait(("_alert", {"type": "alert", "text": str(text)[:3800]}))
        except queue.Full:
            self.stats["dropped"] += 1

    def wants(self, program: str, role: str) -> bool:
        """Would a trade event from this program / role be sent? (the watchdog uses it for bot-level alerts)."""
        role = (role or "CONTROL").upper()
        if self.rules is not None:
            return program in self.rules and (not self.rules[program] or role in self.rules[program])
        return (not self.programs or program in self.programs) and (not self.roles or role in self.roles)

    def kv(self, k: str, v: str | None = None) -> str | None:
        return self._kv(k, v)

    def _stats(self, program: str, key: str) -> tuple[int, int] | None:
        if self.stats_fn is None:
            return None
        try:
            return self.stats_fn(program, key)
        except Exception:
            return None

    def render(self, program: str, d: Mapping[str, Any]) -> str:
        key = str(d.get("bot_key") or "")
        name = self._name(program, key)
        kind = d.get("type")
        if kind == "open":
            conf: Any = False
            if self.confidence_fn is not None:
                try:
                    conf = self.confidence_fn(program, d)
                except Exception:
                    conf = None
            return fmt_open(program, d, name, self._stats(program, key), conf)
        if kind == "tp":
            return fmt_tp(program, d, name)
        return fmt_closed(program, d, name, self._stats(program, key))

    def deliver(self, program: str, d: Mapping[str, Any]) -> bool:
        """Send one event: an open starts the trade's thread, a TP or close replies to it."""
        keys, chat = self._keys(), self.chat_id()
        if not keys or not chat:
            self.stats["dropped"] += 1
            return False
        if program == "_alert":
            ok = self.send_text(chat, str(d.get("text") or ""))
            if ok:
                self.stats["sent"] += 1
                self.stats["last_sent_ts"] = int(self.clock() * 1000)
            return ok
        key = thread_key(program, d)
        reply_to = None
        if d.get("type") != "open":
            with self.db_lock:
                row = self.db.execute("SELECT message_id FROM threads WHERE key=? AND chat_id=?", (key, chat)).fetchone()
            reply_to = row[0] if row else None
        text = self.render(program, d)
        res = None
        if d.get("type") == "open" and self.chart_fn is not None:
            png = None
            try:
                png = self.chart_fn(program, d, self._name(program, str(d.get("bot_key") or "")))
            except Exception as exc:                       # a chart problem never blocks the message
                log.warning("telegram chart failed: %s: %s", type(exc).__name__, str(exc)[:120])
            if png:
                res = self._call("sendPhoto", {"chat_id": chat, "caption": text[:1024], "parse_mode": "HTML"},
                                 files={"photo": ("trade.png", png, "image/png")})
                if res:
                    self.stats["photos"] = int(self.stats.get("photos") or 0) + 1
        if res is None:
            payload: dict[str, Any] = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                       "disable_web_page_preview": True}
            if reply_to:
                payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
            res = self._call("sendMessage", payload)
        if not res:
            return False
        self.stats["sent"] += 1
        self.stats["replies"] += int(bool(reply_to))
        self.stats["last_sent_ts"] = int(self.clock() * 1000)
        if d.get("type") == "open":
            mid = (res.get("result") or {}).get("message_id")
            if mid:
                with self.db_lock, self.db:
                    self.db.execute("INSERT INTO threads(key, chat_id, message_id, ts) VALUES(?, ?, ?, ?) "
                                    "ON CONFLICT(key) DO UPDATE SET chat_id=excluded.chat_id, message_id=excluded.message_id",
                                    (key, chat, int(mid), int(self.clock() * 1000)))
        return True

    # -- Telegram API ----------------------------------------------------------------------------------------------------
    def _post(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}

    def _redact(self, text: Any) -> str:
        s = str(text)
        keys = self._keys()
        if keys and keys[0]:
            s = s.replace(keys[0], "***")
        return s[:200]

    def _post_multipart(self, url: str, fields: dict[str, Any], files: dict[str, tuple[str, bytes, str]],
                        timeout: float) -> dict[str, Any]:
        """A multipart/form-data POST (sendPhoto). Dict / list fields are sent as JSON, as the Bot API expects."""
        import uuid
        boundary = "pl" + uuid.uuid4().hex
        parts: list[bytes] = []
        for k, v in fields.items():
            val = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{val}\r\n'.encode())
        for k, (fname, data, mime) in files.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}

    def _call(self, method: str, payload: dict[str, Any], timeout: float = 15.0, attempts: int = 3,
              files: dict[str, tuple[str, bytes, str]] | None = None) -> dict[str, Any] | None:
        keys = self._keys()
        if not keys:
            return None
        url = API.format(token=keys[0], method=method)
        for _ in range(attempts):
            try:
                res = self.upload(url, payload, files, 30.0) if files else self.http(url, payload, timeout)
            except Exception as exc:                       # never let the token reach a log line
                res = {"ok": False, "description": self._redact(f"{type(exc).__name__}: {exc}")}
            if res.get("ok"):
                return res
            retry = (res.get("parameters") or {}).get("retry_after")
            if res.get("error_code") == 429 and retry:
                self.sleep(min(60.0, float(retry)) + 0.5)
                continue
            if res.get("error_code") == 400 and "reply" in str(res.get("description", "")).lower():
                payload = {k: v for k, v in payload.items() if k != "reply_parameters"}      # the original is gone
                continue
            self.stats["errors"] += 1
            self.stats["last_error"] = self._redact(res.get("description") or res.get("error_code") or "error")
            if method != "getUpdates":                   # the poller logs its own failures (throttled)
                log.warning("telegram %s failed (%s): %s", method, res.get("error_code") or "-", self.stats["last_error"])
            return None
        self.stats["errors"] += 1                        # every attempt rate-limited or retried: the message is lost
        self.stats["last_error"] = f"{method}: gave up after {attempts} attempts"
        if method != "getUpdates":
            log.warning("telegram %s gave up after %d attempts", method, attempts)
        return None

    def send_text(self, chat: str, text: str) -> bool:
        return bool(self._call("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                               "disable_web_page_preview": True}))

    def send_test(self) -> dict[str, Any]:
        if not self._keys():
            return {"ok": False, "error": "no bot token saved"}
        chat = self.chat_id()
        if not chat:
            return {"ok": False, "error": "no chat linked yet: open your bot in Telegram and send /start"}
        ok = self.send_text(chat, "🔔 <b>PaperLab test message.</b> Trade updates arrive in this chat.")
        return {"ok": True} if ok else {"ok": False, "error": self.stats.get("last_error") or "Telegram refused the message"}

    def handle_update(self, upd: Mapping[str, Any]) -> None:
        """/start links the first private chat (unless a chat is configured); /settings and /help answer the linked one."""
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id") or "")
        text = str(msg.get("text") or "").strip().split()
        if not chat or not text:
            return
        cmd = text[0].split("@")[0].lower()
        linked = self.chat_id()
        if linked and chat != linked:
            self.send_text(chat, "This is a private PaperLab bot.")
            return
        if not linked:                                   # the first private message links, whatever it says
            if (msg.get("chat") or {}).get("type") != "private":
                self.send_text(chat, "Send /start from a private chat with the bot to link it.")
                return
            self._kv("chat_id", chat)
            log.info("telegram: a private chat is linked; trade updates go there now")
            cmd = "/start"
        if cmd == "/start":
            self.send_text(chat, "✅ <b>Linked to PaperLab.</b>\nEvery paper trade the bots take is posted here: the "
                                 "opening message carries the SL and TPs, and its TP hits and close REPLY to it.\n"
                                 "Commands: /settings (default TP / SL rules) · /help")
            self.send_text(chat, settings_text())
        elif cmd == "/settings":
            self.send_text(chat, settings_text())
        elif cmd == "/help":
            self.send_text(chat, "PaperLab paper-trading updates.\n/settings — the default TP / SL of every bot program\n"
                                 "Each trade: a new message when it opens; TP hits and the close reply to it.")

    # -- threads ---------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        for fn, name in ((self._sender, "telegram-send"), (self._poller, "telegram-poll")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def _sender(self) -> None:
        last = 0.0
        while not self._stop.is_set():
            try:
                program, d = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            wait = SEND_EVERY_S - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
            try:
                self.deliver(program, d)
            except Exception as exc:
                self.stats["errors"] += 1
                self.stats["last_error"] = self._redact(f"{type(exc).__name__}: {exc}")
                log.warning("telegram send failed: %s", self.stats["last_error"])
            last = self.clock()

    def _poller(self) -> None:
        offset = int(self._kv("offset") or 0)
        while not self._stop.is_set():
            if not self._keys():
                self.sleep(15.0)
                continue
            res = self._call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
                             timeout=30.0, attempts=1)
            if not res:
                self.stats["poll_failures"] += 1
                if self.stats["poll_failures"] in (1, 10) or self.stats["poll_failures"] % 100 == 0:
                    log.warning("telegram: reading the bot's messages failed (%d times): %s",
                                self.stats["poll_failures"], self.stats.get("last_error"))
                self.sleep(10.0)
                continue
            for upd in res.get("result") or []:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                try:
                    self.handle_update(upd)
                except Exception as exc:
                    self.stats["last_error"] = self._redact(f"{type(exc).__name__}: {exc}")
            self._kv("offset", str(offset))
