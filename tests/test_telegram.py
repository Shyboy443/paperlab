"""Telegram trade updates (app/live/telegram.py): what each message says, that a TP hit and the close REPLY to the
trade's opening message (also after a restart), the filters, chat linking, and that the bot token never reaches an
error or log line. No network: the HTTP call is a fake."""
from __future__ import annotations

from app.live import telegram as tg

TOKEN = "123456:TEST-token-not-real"


class Keys:
    def __init__(self, token=TOKEN, chat=""):
        self.k = (token, chat)

    def keys(self, exchange, network):
        return self.k if (exchange, network) == tg.PROVIDER else None


class FakeHttp:
    def __init__(self):
        self.calls, self.next_id = [], 100

    def __call__(self, url, payload, timeout):
        self.calls.append((url, dict(payload)))
        self.next_id += 1
        return {"ok": True, "result": {"message_id": self.next_id}}


def _notifier(tmp_path, http=None, chat="777", env=None):
    n = tg.TelegramNotifier(Keys(chat=chat), str(tmp_path), names=lambda: {"v11|V11.3-SCAN": {"name": "Zap"}},
                            env=env or {}, http=http or FakeHttp(), sleep=lambda s: None)
    return n


OPEN = {"type": "open", "bot_key": "V11.3-SCAN", "role": "CONTROL", "strategy_id": "V11.3", "symbol": "SOLUSDT",
        "side": "long", "price": 120.0, "qty": 0.2, "ts": 1_790_000_060_000, "leverage": 3, "stop": 118.8,
        "tps": [120.6, 121.2, 121.8], "target": 120.6, "risk_usd": 0.24, "risk_pct": 0.012}
TP1 = {"type": "tp", "bot_key": "V11.3-SCAN", "role": "CONTROL", "symbol": "SOLUSDT", "side": "long",
       "entry_ts": 1_790_000_060_000, "entry_price": 120.0, "price": 120.6, "qty": 0.05, "fee": 0.001, "pnl": 0.03,
       "tp_index": 1, "remaining_qty": 0.15, "stop": 120.2, "ts": 1_790_000_660_000}
CLOSED = {"type": "closed", "bot_key": "V11.3-SCAN", "role": "CONTROL", "symbol": "SOLUSDT", "side": "long",
          "entry_ts": 1_790_000_060_000, "exit_ts": 1_790_003_000_000, "hold_s": 2940, "entry_price": 120.0,
          "exit_price": 121.2, "net": 0.11, "r_net": 0.46, "fees": 0.02, "exit_kind": "tp"}


def test_the_opening_message_is_the_signal_channel_layout():
    text = tg.fmt_open("v11", {**OPEN, "leverage": 7}, "Zap", (113, 61))
    assert text.splitlines() == ["#SOL/USDT - Long 🟢", "", "Entry: <code>120.00</code>",
                                 "Stop Loss: <code>118.80</code> (−1.0%)", "",
                                 "Target 1: <code>120.60</code> (+0.5%)", "Target 2: <code>121.20</code> (+1.0%)",
                                 "Target 3: <code>121.80</code> (+1.5%)", "",
                                 "Leverage: x7", "🤖 Zap · win rate <b>54%</b> (61 of 113)"]
    single = tg.fmt_open("v8", {**OPEN, "bot_key": "V8.3-ENA-5M", "side": "short", "stop": 121.0, "tps": [118.2]}, "Gamma", (0, 0))
    assert single.startswith("#SOL/USDT - Short 🔴") and "Target: <code>118.20</code> (−1.5%)" in single
    assert "🤖 Gamma · first trade" in single
    bizzy = tg.fmt_open("v12", {**OPEN, "bot_key": "V12.1-DAY", "tps": [], "target": None}, "Bizzy")
    assert "Target: the day close" in bizzy and "win rate" not in bizzy           # no stats source: just the name
    for t in (text, single, bizzy):                                            # none of the old clutter
        assert "size" not in t and "Risk" not in t and "paper trade" not in t and "V11.3-SCAN" not in t


def test_tp_hits_and_the_close_reply_to_the_opening_message(tmp_path):
    http = FakeHttp()
    n = _notifier(tmp_path, http)
    n.stats_fn = lambda program, key: (12, 7)
    assert n.deliver("v11", OPEN) and n.deliver("v11", TP1)
    first_id = 101
    assert "reply_parameters" not in http.calls[0][1] and "win rate <b>58%</b> (7 of 12)" in http.calls[0][1]["text"]
    assert http.calls[1][1]["reply_parameters"]["message_id"] == first_id
    assert http.calls[1][1]["text"].splitlines() == ["✅ #SOL/USDT - Target 1 hit (+0.5%)",
                                                     "Stop moved to: <code>120.20</code> (profit locked)"]
    n2 = _notifier(tmp_path, http)                                # a restart: the thread is remembered
    n2.stats_fn = lambda program, key: (13, 8)
    assert n2.deliver("v11", CLOSED)
    last = http.calls[-1][1]
    assert last["reply_parameters"]["message_id"] == first_id
    assert last["text"].splitlines() == ["💰 #SOL/USDT - <b>WIN +0.11 USDT</b>", "Hit the target · held 49m",
                                         "🤖 Zap · win rate <b>62%</b> (8 of 13)"]
    assert all(p["chat_id"] == "777" and p["parse_mode"] == "HTML" for _, p in http.calls)


def test_a_loss_message_says_loss_and_flags_a_gap_past_the_stop():
    text = tg.fmt_closed("v11", {**CLOSED, "net": -0.31, "r_net": -1.8, "exit_kind": "stop"}, "Zap", (14, 8))
    assert text.startswith("🔻 #SOL/USDT - <b>LOSS −0.31 USDT</b>") and "Hit the stop" in text
    assert "Bigger loss than planned" in text and "57%" in text


def test_the_opening_message_goes_out_as_a_chart_photo_and_replies_still_thread(tmp_path):
    http, uploads = FakeHttp(), []

    def upload(url, fields, files, timeout):
        uploads.append((url, dict(fields), files))
        return {"ok": True, "result": {"message_id": 555}}
    n = tg.TelegramNotifier(Keys(chat="777"), str(tmp_path), names=lambda: {"v11|V11.3-SCAN": {"name": "Zap"}},
                            env={}, http=http, sleep=lambda s: None, upload=upload,
                            chart=lambda program, ev, name: b"\x89PNG fake" if name == "Zap" else None)
    assert n.deliver("v11", OPEN) and n.deliver("v11", TP1)
    url, fields, files = uploads[0]
    assert url.endswith("/sendPhoto") and files["photo"][1] == b"\x89PNG fake" and files["photo"][2] == "image/png"
    assert fields["caption"].startswith("#SOL/USDT - Long 🟢") and fields["parse_mode"] == "HTML"
    assert http.calls[-1][1]["reply_parameters"]["message_id"] == 555           # the TP replies to the photo
    assert TOKEN not in repr(uploads[0][1]) and n.stats["photos"] == 1


def test_no_chart_or_a_failed_chart_falls_back_to_text(tmp_path):
    http = FakeHttp()

    def boom(program, ev, name):
        raise RuntimeError("no bars")
    n = tg.TelegramNotifier(Keys(chat="777"), str(tmp_path), env={}, http=http, sleep=lambda s: None, chart=boom)
    assert n.deliver("v11", OPEN) and http.calls[0][0].endswith("/sendMessage")


def test_the_chart_renders_a_png_from_stored_bars(tmp_path):
    import sqlite3

    from app.live import trade_chart
    db = tmp_path / "v11.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fwd6_bars(symbol TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL, "
                "volume REAL, turnover REAL, bid REAL, ask REAL, half_spread_bps REAL, source TEXT)")
    t0 = OPEN["ts"] - 8 * 3600_000
    for i in range(8 * 60 + 1):
        p = 120 + 2 * __import__("math").sin(i / 40)
        con.execute("INSERT INTO fwd6_bars VALUES ('SOLUSDT', ?, ?, ?, ?, ?, 10, 0, 0, 0, 0, 'live')",
                    (t0 + i * 60_000, p, p + 0.2, p - 0.2, p + 0.05))
    con.commit()
    con.close()
    png = trade_chart.chart_for_open(str(db), "v11", {**OPEN, "strategy_id": "V11.3"}, "Zap")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and 20_000 < len(png) < 400_000
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(png)).size == (trade_chart.W, trade_chart.H)
    assert trade_chart.chart_for_open(str(db), "v11", {**OPEN, "symbol": "XRPUSDT"}, "Zap") is None   # no bars: no chart
    short = trade_chart.chart_for_open(str(db), "v11", {**OPEN, "side": "short", "stop": 121.5, "tps": [118.0]}, None)
    assert short[:4] == b"\x89PNG"


def test_replays_counterfactuals_and_filtered_programs_are_not_sent(tmp_path):
    n = _notifier(tmp_path, env={"TELEGRAM_PROGRAMS": "v11,v12"})
    n.on_event("v11", {**OPEN, "rederived": True})
    n.on_event("v11", {**CLOSED, "counterfactual": True})
    n.on_event("v8", OPEN)
    n.on_event("v11", {"type": "candidate", "bot_key": "x"})
    assert n.q.qsize() == 0
    n.on_event("v11", OPEN)
    n.on_event("v12", OPEN)
    assert n.q.qsize() == 2


def test_start_links_the_first_private_chat_only(tmp_path):
    http = FakeHttp()
    n = _notifier(tmp_path, http, chat="")
    assert n.chat_id() is None
    n.handle_update({"message": {"chat": {"id": 42, "type": "private"}, "text": "/start"}})
    assert n.chat_id() == "42" and any("Linked to PaperLab" in p["text"] for _, p in http.calls)
    assert any("Default TP / SL settings" in p["text"] for _, p in http.calls)
    n.handle_update({"message": {"chat": {"id": 99, "type": "private"}, "text": "/start"}})
    assert n.chat_id() == "42" and http.calls[-1][1] == {**http.calls[-1][1], "chat_id": "99"}
    assert "private PaperLab bot" in http.calls[-1][1]["text"]


def test_the_token_never_reaches_an_error_or_log(tmp_path):
    def boom(url, payload, timeout):
        raise OSError(f"could not reach {url}")
    n = _notifier(tmp_path, http=boom)
    assert not n.deliver("v11", OPEN)
    assert TOKEN not in str(n.stats["last_error"]) and "***" in str(n.stats["last_error"])
    assert TOKEN not in str(n.health())


def test_rate_limit_is_retried_after_the_delay_telegram_asks_for(tmp_path):
    seen = []

    def limited(url, payload, timeout):
        seen.append(1)
        if len(seen) == 1:
            return {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}
        return {"ok": True, "result": {"message_id": 5}}
    slept = []
    n = tg.TelegramNotifier(Keys(chat="1"), str(tmp_path), env={}, http=limited, sleep=slept.append)
    assert n.deliver("v11", OPEN) and slept == [3.5]


def test_settings_message_lists_every_program():
    text = tg.settings_text()
    for part in ("V8 Scalp", "V9 Stocks", "V11 Scan", "V12 Bizzy", "after TP1 the SL", "today's open"):
        assert part in text


def test_the_vault_provider_takes_a_token_without_a_chat_id():
    from app.live.providers import Providers
    p = Providers(env={"TELEGRAM_BOT_TOKEN": TOKEN})
    assert p.keys("telegram", "data") == (TOKEN, "") and p.source("telegram", "data") == "railway"
    assert Providers(env={}).keys("telegram", "data") is None


def test_per_program_filter_v11_bizzy_and_v8_controls_only(tmp_path):
    assert tg.parse_filter("v11, v12 ,v8:CONTROL") == {"v11": set(), "v12": set(), "v8": {"CONTROL"}}
    assert tg.parse_filter("") is None
    n = _notifier(tmp_path, env={"TELEGRAM_FILTER": "v11,v12,v8:CONTROL"})
    sent = [("v11", "CONTROL"), ("v11", "JEV"), ("v12", "CONTROL"), ("v12", "JEV"), ("v8", "CONTROL")]
    dropped = [("v8", "JEV"), ("v8", "LADDER"), ("v9", "CONTROL"), ("v7", "CONTROL"), ("v6", "CONTROL")]
    for prog, role in sent + dropped:
        n.on_event(prog, {**OPEN, "role": role})
    assert n.q.qsize() == len(sent)
    assert n.health()["filters"] == {"v11": "all", "v12": "all", "v8": ["CONTROL"]}


def test_the_dashboard_saves_a_token_alone_and_rejects_a_non_numeric_chat_id(tmp_path):
    import asyncio
    from app.live.key_vault import KeyVault
    from app.live.providers import Providers

    class Ok:
        async def fetch_balance(self):
            return {"wallet": 0.0, "available": 0.0, "currency": "-"}

        async def close(self):
            pass
    v = KeyVault(tmp_path / "k.vault", "pw")
    p = Providers({}, client_factory=lambda ex, net: Ok(), vault=v)
    assert asyncio.run(p.save_keys("telegram", "data", TOKEN, ""))["ok"]               # no 500, no chat id needed
    assert p.keys("telegram", "data") == (TOKEN, "") and p.source("telegram", "data") == "dashboard"
    assert KeyVault(tmp_path / "k.vault", "pw").get("telegram:data") == (TOKEN, "")
    bad = asyncio.run(p.save_keys("telegram", "data", TOKEN, "my chat"))
    assert not bad["ok"] and "number" in bad["error"]
    assert asyncio.run(p.save_keys("telegram", "data", TOKEN, "123456789"))["ok"]
    assert p.keys("telegram", "data") == (TOKEN, "123456789")
    v.set("alpaca:testnet", "k", "s")
    import pytest
    from app.live.key_vault import VaultError
    with pytest.raises(VaultError):
        v.set("alpaca:testnet", "k", "")                                                # exchanges still need both


def test_a_non_numeric_configured_chat_id_is_ignored_so_start_still_links(tmp_path):
    http = FakeHttp()
    n = _notifier(tmp_path, http, chat="not-a-chat")
    assert n.chat_id() is None
    n.handle_update({"message": {"chat": {"id": 42, "type": "private"}, "text": "/start"}})
    assert n.chat_id() == "42" and any("Linked to PaperLab" in p["text"] for _, p in http.calls)
    assert _notifier(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, chat="-100123").chat_id() == "-100123"


def test_any_private_message_links_and_the_test_button_reaches_the_chat(tmp_path):
    http = FakeHttp()
    n = _notifier(tmp_path, http, chat="")
    assert not n.send_test()["ok"] and "send /start" in n.send_test()["error"]
    n.handle_update({"message": {"chat": {"id": 55, "type": "private"}, "text": "hello"}})
    assert n.chat_id() == "55" and any("Linked to PaperLab" in p["text"] for _, p in http.calls)
    assert n.send_test() == {"ok": True} and "test message" in http.calls[-1][1]["text"]
    assert n.deliver("v11", OPEN) and n.health()["last_sent_ts"] and n.health()["linked"]
    assert TOKEN not in str(n.health())


def test_confidence_is_the_setups_measured_win_chance_shrunk_toward_half(tmp_path):
    import sqlite3

    from app.live.trade_stats import bot_record, setup_confidence
    db = tmp_path / "v8.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fwd6_trades(experiment_id TEXT, bot_key TEXT, strategy_id TEXT, role TEXT, net REAL, "
                "exit_ts INTEGER, counterfactual INTEGER)")
    now = 1_790_000_000_000
    rows = [("x1", f"V8.3-{c}-5M", "V8.3", "CONTROL", 1.0 if i % 3 == 0 else -1.0, now - 3_600_000, 0)
            for i, c in enumerate(["ENA", "SOL", "XRP"] * 10)]                          # 30 trades, 10 wins, 3 coins
    rows += [("x1", "V8.3-ENA-5M+JEV", "V8.3", "JEV", 1.0, now, 0)] * 3                    # another role
    rows += [("x1", "V8.3-ENA-5M", "V8.3", "CONTROL", 5.0, now, 1)] * 9                    # counterfactuals: never
    rows += [("x0", "V8.1-ENA-5M", "V8.1", "CONTROL", 1.0, now - 86_400_000, 0)] * 25      # an earlier run, V8.1
    con.executemany("INSERT INTO fwd6_trades VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    assert setup_confidence(str(db), "x1", "V8.3", "CONTROL", now) == (round(15 / 40 * 100), 30, "this test run")
    assert setup_confidence(str(db), "x1", "V8.3", "JEV", now) is None                     # 3 trades: no number
    assert setup_confidence(str(db), "x1", "V8.1", "CONTROL", now) == (round(30 / 35 * 100), 25, "last 30 days")
    assert setup_confidence(str(db), "x1", "V8.1", "CONTROL", now + 40 * 86_400_000) is None
    assert bot_record(str(db), "x1", "V8.3-ENA-5M") == (10, 10)                         # every 3rd row: all ENA rows win


def test_the_opening_message_shows_the_confidence_line(tmp_path):
    http = FakeHttp()
    n = tg.TelegramNotifier(Keys(chat="777"), str(tmp_path), env={}, http=http, sleep=lambda s: None,
                            confidence=lambda program, ev: (37, 451, "this test run") if ev.get("bot_key") else None)
    assert n.deliver("v11", OPEN)
    assert "Confidence: <b>37%</b> ▰▰▰▰▱▱▱▱▱▱ (451 past trades)" in http.calls[0][1]["text"]
    n2 = tg.TelegramNotifier(Keys(chat="777"), str(tmp_path / "b") if (tmp_path / "b").mkdir() is None else "", env={},
                             http=http, sleep=lambda s: None, confidence=lambda program, ev: None)
    assert n2.deliver("v11", OPEN) and "Confidence: building up" in http.calls[-1][1]["text"]
    assert "Confidence" not in tg.fmt_open("v11", OPEN, "Zap")                              # no source: no line


def test_a_failed_send_is_logged_with_its_reason_and_never_the_token(tmp_path, caplog):
    import logging

    def refuse(url, payload, timeout):
        return {"ok": False, "error_code": 400, "description": f"Bad Request: can't parse entities ({url})"}
    n = _notifier(tmp_path, http=refuse)
    with caplog.at_level(logging.WARNING, logger="paperlab.live.telegram"):
        assert not n.deliver("v11", OPEN)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("telegram sendMessage failed (400)" in m and "can't parse entities" in m for m in msgs)
    assert all(TOKEN not in m for m in msgs)


def test_giving_up_after_rate_limits_is_counted_and_logged(tmp_path, caplog):
    import logging

    def limited(url, payload, timeout):
        return {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}
    n = _notifier(tmp_path, http=limited)
    with caplog.at_level(logging.WARNING, logger="paperlab.live.telegram"):
        assert not n.deliver("v11", OPEN)
    assert n.stats["errors"] == 1 and "gave up after 3 attempts" in n.stats["last_error"]
    assert any("gave up after 3 attempts" in r.getMessage() for r in caplog.records)
