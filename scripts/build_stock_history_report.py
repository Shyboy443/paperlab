"""Build a local, reproducible report from public paper records and archived replays."""
import csv
import datetime as dt
import html
import json
import statistics
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'stock-bot-audit'
TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = 'https://paperlab-production-919c.up.railway.app/api/public/competition/'


def when(ts):
    return dt.datetime.fromtimestamp(ts / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=40) as response:
        return json.load(response)


def group_stats(trades):
    return {'trades': len(trades), 'net': round(sum(t['net'] for t in trades), 6),
            'average_r': round(statistics.mean(t['r'] for t in trades), 6) if trades else None,
            'win_rate': sum(t['net'] > 0 for t in trades) / len(trades) if trades else None}


def graph(bot, color):
    replay = bot['historical']['replay']
    peak = bot['historical']['peak_marked_equity']
    end = replay['trade_list'][-1]['exit'] + 86400000
    start = dt.datetime(2024, 10, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
    points = [(start, 1000)] + [p for p in replay['equity_daily'] if p[0] <= end]
    points.append((end, replay['final_equity']))
    lo = min(y for _, y in points) - 25
    hi = max(peak['equity'], max(y for _, y in points)) + 25
    x = lambda t: 52 + (t - start) / (end - start) * 666
    y = lambda value: 24 + (hi - value) / (hi - lo) * 170
    path = ' '.join(f'{x(t):.2f},{y(value):.2f}' for t, value in points)
    lines = []
    for value in [750, 850, 1000, 1100]:
        if lo <= value <= hi:
            lines.append(f'<line x1="52" y1="{y(value):.2f}" x2="718" y2="{y(value):.2f}" stroke="#293541" stroke-dasharray="3 5"/><text x="4" y="{y(value)+4:.2f}">${value}</text>')
    lines.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>')
    lines.append(f'<circle cx="{x(peak["ts"]):.2f}" cy="{y(peak["equity"]):.2f}" r="5" fill="#ffd185"><title>Highest minute-sampled equity: ${peak["equity"]:.2f} at {when(peak["ts"])} Sri Lanka</title></circle>')
    for t, value in points[1:-1]:
        lines.append(f'<circle cx="{x(t):.2f}" cy="{y(value):.2f}" r="4" fill="transparent"><title>Daily equity ${value:.2f} · {when(t)} Sri Lanka</title></circle>')
    lines.append(f'<text x="52" y="222">2024-10-01</text><text x="718" y="222" text-anchor="end">Stopped {when(replay["trade_list"][-1]["exit"])[:10]}</text>')
    return '<svg viewBox="0 0 750 238" role="img" aria-label="Daily historical paper equity until trading stopped">' + ''.join(lines) + '</svg>'


def main():
    audit = json.loads((OUT / 'minute-equity-audit.json').read_text(encoding='utf-8-sig'))
    assert all(b['matches_existing_replay'] for b in audit['bots']), 'Replay drift: do not publish a matching claim'
    live = get('v9')
    forward = {}
    bots = []
    for historical in audit['bots']:
        key = historical['key']
        detail = get('v9/bot/' + key)
        forward[key] = detail
        current = next(b for b in live['leaderboard'] if b['key'] == key)
        trades = historical['replay']['trade_list']
        balance = historical['replay']['start_equity']
        settled_peak = {'equity': balance, 'ts': None}
        for trade in trades:
            balance += trade['net']
            if balance > settled_peak['equity']:
                settled_peak = {'equity': round(balance, 6), 'ts': trade['exit']}
        assert abs(balance - historical['replay']['final_equity']) < 0.002
        grouped = {kind: group_stats([t for t in trades if t['kind'] == kind]) for kind in sorted({t['kind'] for t in trades})}
        bots.append({'name': historical['name'], 'key': key, 'historical': historical,
                     'peak_settled_balance': settled_peak, 'historical_by_exit': grouped,
                     'historical_fast_exits_under_10min': group_stats([t for t in trades if t['exit'] - t['entry'] <= 600000]),
                     'historical_by_side': {side: group_stats([t for t in trades if t['side'] == side]) for side in ('long', 'short')},
                     'worst_historical_trades': sorted(trades, key=lambda t: t['net'])[:5],
                     'recent': {'equity_marked': current['equity_now'], 'recorded_peak': current['peak'],
                                'settled_balance': 1000 + current['net_closed'], 'trades': current['trades'],
                                'wins': current['wins'], 'profit_factor': current['profit_factor'],
                                'losing_trades': [t for t in detail['trades'] if t['net'] < 0]}})
        with (OUT / f'{historical["name"]}-historical-trades.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['entry_local', 'exit_local', *trades[0].keys()])
            writer.writeheader()
            for trade in trades:
                writer.writerow({'entry_local': when(trade['entry']), 'exit_local': when(trade['exit']), **trade})
    summary = json.loads((ROOT / 'docs' / 'BACKTEST_2Y_SUMMARY.json').read_text())
    all_stocks = [r for r in summary['bots'] if r['program'] == 'v9']
    for bot in bots:
        stored_summary = next(r for r in all_stocks if r['key'] == bot['key'])
        bot['historical_without_peak_stop'] = stored_summary.get('without_stop')
    report = {'generated_local': dt.datetime.now(TZ).isoformat(), 'timezone': 'Asia/Colombo',
              'recent_snapshot_ms': live['experiment']['sessions_detail'][-1]['heartbeat_ts'],
              'recent_forward_start_ms': live['hero']['forward_start_ms'],
              'window': audit['window'], 'historical_stock_bots': len(all_stocks),
              'historical_losing_stock_bots': sum(b['net'] < 0 for b in all_stocks),
              'historical_halted_stock_bots': sum(b['halted'] for b in all_stocks),
              'recent_all_stock_books': {'equity': live['hero']['total_virtual_equity'], 'start': live['hero']['start_equity_total'], 'net': live['hero']['net_pnl']},
              'scope': 'The three CONTROL stock bots in the screenshot. Gamma is a crypto bot and is excluded.',
              'bots': bots,
              'limitations': ['Simulated USD books, not broker account balances.',
                  'Historical marked peak includes unrealized PnL and is sampled at each processed 1m close; not tick-level.',
                  'Settled peak is reconstructed after completed trades, including their modeled fees and slippage.',
                  'The replay preserves the 30% peak-drawdown sizing stop; daily and strategy engine halts are disabled. The forward arena has additional eligibility gates.',
                  'Alpaca IEX regular-session archive; missing session minutes are filled flat by the existing session tape.',
                  'Current instrument rules and strategy settings; parts of this historical period informed development. This is not an independent unseen validation.',
                  'The daily-reset variant study in V9_STOCK_STUDY.json is a different experiment and is not mixed with these continuous wallet balances.',
                  'Forward prices continue changing; recent amounts are a saved snapshot.']}
    (OUT / 'stock-bot-performance-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (OUT / 'forward-evidence.json').write_text(json.dumps({'arena': live, 'selected_details': forward}, indent=2), encoding='utf-8')
    lines = ['# Stock bot performance audit', '', f'Generated {report["generated_local"]}. All displayed timestamps use Asia/Colombo.', '',
             'The three stock bots have short recent winning records, but each lost in the continuous historical replay from 2024-10-01 to 2026-10-01. Fresh replays exactly matched every saved trade and ending balance.', '',
             '| Bot | Highest marked equity | Peak date | Highest settled balance | Ending balance | Return | Trades | Profit factor | Stop date |',
             '|---|---:|---|---:|---:|---:|---:|---:|---|']
    for b in bots:
        h, r = b['historical'], b['historical']['replay']
        lines.append(f'| {b["name"]} / {r["symbol"]} | ${h["peak_marked_equity"]["equity"]:,.2f} | {when(h["peak_marked_equity"]["ts"])} | ${b["peak_settled_balance"]["equity"]:,.2f} | ${r["final_equity"]:,.2f} | {r["return_pct"]:+.2f}% | {r["trades"]} | {r["profit_factor"]:.3f} | {when(r["trade_list"][-1]["exit"])} |')
    lines += ['', 'Peak marked equity includes open-position gains. The continuous book stopped taking trades after its peak-drawdown risk limit; the remaining archive does not create later trades.', '',
              '**When the decline started:** Halo reached its high in February 2025, then lost $64.22 in March and $106.23 in June, before stopping in August. Iris peaked on October 2, 2024 and lost $203.21 that October. Oscar peaked on October 10, 2024; every trading month from October through May finished negative.', '',
              '**What the trades show:** Long and short trades both lost overall in each bot. Stop exits averaged approximately -1.17R, so the realized loss regularly exceeded intended risk. These observations support an entry/exit and execution problem; they do not isolate one proven causal fix.', '',
              f'Across the existing replay, all {len(all_stocks)} CONTROL stock bots ended below $1,000; {report["historical_halted_stock_bots"]} triggered the peak risk stop. Selecting only the current leaders hides this wider result.', '', '## Recent forward paper history', '',
              f'Forward experiment began {when(report["recent_forward_start_ms"])}. Snapshot near {when(report["recent_snapshot_ms"])}.', '',
              '| Bot | Marked equity now | Recorded marked peak | Settled balance | Closed trades / wins |', '|---|---:|---:|---:|---|']
    for b in bots:
        r = b['recent']
        lines.append(f'| {b["name"]} | ${r["equity_marked"]:,.2f} | ${r["recorded_peak"]:,.2f} | ${r["settled_balance"]:,.2f} | {r["trades"]} / {r["wins"]} |')
        lines += []
    lines += ['', 'Iris has no closed losing forward trades yet; its displayed infinite profit factor results from that tiny sample, not evidence of unlimited or reliable performance.', '', '## Recent losses', '']
    for b in bots:
        lines += [f'### {b["name"]}', '']
        if not b['recent']['losing_trades']:
            lines += ['No closed losing trades in this snapshot.', '']
        for t in sorted(b['recent']['losing_trades'], key=lambda x: x['exit_ts']):
            lines.append(f'- {when(t["exit_ts"])}: {t["side"]}, {t["exit_kind"]} exit, ${t["net"]:+.2f}.')
        lines += ['', 'Historical monthly net: ' + '; '.join(f'{m}: ${n:+.2f}' for m, n in b['historical']['replay']['monthly'].items()), '']
    lines += ['## Existing replay with the peak risk stop removed', '',
              'This separate saved experiment keeps each bot trading to the end of September 2026. Removing the stop made all three ending balances worse; it does not repair the strategy.', '',
              '| Bot | Ending balance without peak stop | Return | Closed trades |', '|---|---:|---:|---:|']
    for b in bots:
        unhalted = b['historical_without_peak_stop']
        if unhalted:
            lines.append(f'| {b["name"]} | ${unhalted["final_equity"]:,.2f} | {unhalted["return_pct"]:+.2f}% | {unhalted["trades"]} |')
    lines += ['## Limits and definitions', '', *['- ' + s for s in report['limitations']], '',
              'Alpaca documents the feed distinction: [Historical Stock Data](https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1). IEX is not the consolidated SIP feed.', '',
              'No trading configuration, live orders, or Railway book data were changed by this audit.']
    (OUT / 'Stock-Bot-Performance-Audit.md').write_text('\n'.join(lines), encoding='utf-8')
    cards = []
    for b, color in zip(bots, ['#61dfc4', '#c890ff', '#ffb959']):
        h, r = b['historical'], b['historical']['replay']
        losses = ''.join(f'<li>{when(t["exit_ts"])} · {html.escape(t["side"])} / {html.escape(t["exit_kind"])} · <b>${t["net"]:+.2f}</b></li>' for t in sorted(b['recent']['losing_trades'], key=lambda x: x['exit_ts'])) or '<li>No closed losing forward trades yet.</li>'
        cards.append(f'<article><h2 style="color:{color}">{b["name"]} <span>{r["symbol"]} · {r["strategy_id"]}</span></h2><div class="metrics"><div><small>Historical marked peak</small><strong>${h["peak_marked_equity"]["equity"]:,.2f}</strong><small>{when(h["peak_marked_equity"]["ts"])}</small></div><div><small>Historical final</small><strong>${r["final_equity"]:,.2f}</strong><small>{r["return_pct"]:+.2f}% after {r["trades"]} trades</small></div><div><small>Peak to trough</small><strong>{h["minute_sample_max_drawdown"]["fraction"]*100:.2f}%</strong><small>Profit factor {r["profit_factor"]:.3f}</small></div></div>{graph(b,color)}<p class="caption">Line: daily account equity until the last trade. Gold dot: highest observed minute-close equity, including open PnL. Hover for values.</p><details><summary>Monthly losses and execution evidence</summary><p>{html.escape(str(r["monthly"]))}</p><p>Stop exits: {b["historical_by_exit"]["stop"]["trades"]}; average {b["historical_by_exit"]["stop"]["average_r"]:.3f}R. Settled wallet peak: ${b["peak_settled_balance"]["equity"]:,.2f}.</p></details><h3>Recent paper run</h3><p>Marked equity <b>${b["recent"]["equity_marked"]:,.2f}</b> · recorded peak <b>${b["recent"]["recorded_peak"]:,.2f}</b> · {b["recent"]["trades"]} closed trades, {b["recent"]["wins"]} wins.</p><ul>{losses}</ul></article>')
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PaperLab · Stock bot history audit</title><style>body{margin:0;background:#090f14;color:#e5edf3;font:16px/1.6 system-ui,sans-serif}main{max-width:1050px;margin:auto;padding:40px 24px}h1{font-size:clamp(30px,5vw,50px);line-height:1.15;letter-spacing:-1.5px}p{color:#acb9c5}small,.caption{font-size:13px;color:#a0afbd}article{padding:26px;border:1px solid #293640;border-radius:18px;background:#101920;margin:24px 0}h2{font-size:28px;margin:0 0 18px}h2 span{font-size:14px;color:#9eafbd;margin-left:10px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.metrics small{display:block}.metrics strong{display:block;font-size:28px}svg{width:100%;margin-top:18px}svg text{fill:#a0afbd;font:12px system-ui}summary{cursor:pointer;color:#c2d4e2}li{margin:5px 0}footer{border-top:1px solid #293640;padding-top:20px;color:#9eafbd;font-size:13px}a{color:#61dfc4}@media(max-width:550px){main{padding:24px 14px}article{padding:18px}.metrics{grid-template-columns:1fr}.metrics strong{font-size:24px}}</style><main><p>PAPERLAB / READ-ONLY HISTORICAL AUDIT</p><h1>Recent wins. A weaker long-term record.</h1><p>The three stock bots from your screenshot were replayed from October 2024 through September 2026. Each started with $1,000. All three ended in loss, and all 30 CONTROL stock bots in the existing wider replay ended below their starting balance.</p><p><b>Halo:</b> peaked in February 2025; sustained losses began in March. <b>Iris:</b> declined in the first month. <b>Oscar:</b> peaked in October 2024, then every trading month finished negative.</p>' + ''.join(cards) + '<footer>All timestamps use Sri Lanka time (Asia/Colombo). Historical peaks include floating PnL at processed minute closes, not every market tick. Historical books preserve the 30% peak-drawdown sizing stop; other daily/strategy halts are disabled, and forward eligibility is different. Replay trades and final balances match the saved records exactly. IEX regular-session data, modeled execution and current instrument rules; parts of the period informed strategy development. These are simulated balances, not brokerage wallets or proof of future returns. No trading settings or orders were changed.<p>Generated ' + html.escape(report['generated_local']) + ' · <a href="https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1">Alpaca feed documentation</a></p></footer></main></html>'
    (OUT / 'Stock-Bot-Performance-Audit.html').write_text(page, encoding='utf-8')
    print(json.dumps({'report': str(OUT / 'Stock-Bot-Performance-Audit.html'), 'recent_snapshot_local': when(report['recent_snapshot_ms']), 'bots': [{'name': b['name'], 'historical_peak': b['historical']['peak_marked_equity'], 'historical_final': b['historical']['replay']['final_equity'], 'recent': b['recent']} for b in bots]}, indent=2))


if __name__ == '__main__':
    main()
