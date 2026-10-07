"""Read-only replay audit of the three stock bots identified in the user's screenshot.

Run on Railway against /data/bt2y. Reads the existing archive; writes no server
files and never accesses account credentials or a running trading book.
"""
import json
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from scripts import backtest_2y as bt


def main():
    root = Path('/data/bt2y')
    output = []
    for key, name in [('V9.1-TSLA-5M', 'Halo'), ('V9.3-AAPL-5M', 'Iris'), ('V9.1-AMZN-5M', 'Oscar')]:
        stored = json.loads((root / 'results' / f'v9__{key}.json').read_text())
        job = {k: stored[k] for k in ('program', 'key', 'strategy_id', 'role', 'coin', 'symbol', 'timeframe', 'horizon', 'multi')}
        engine, strategy, bars, _, leverage, _ = bt.build(job, root)
        engine.portfolio.closed_trades = deque(maxlen=None)
        start = float(engine.settings.strategy_starting_balance)
        peak = {'ts': bt.START, 'equity': start}
        drawdown = {'fraction': 0.0, 'ts': bt.START, 'equity': start, 'peak_ts': bt.START, 'peak_equity': start}
        sample = engine._sample_leverage

        def observe(res, sid, equity):
            sample(res, sid, equity)
            ts = res.equity[-1][0]
            if ts < bt.START:
                return
            if equity > peak['equity']:
                peak.update(ts=ts, equity=equity)
            fraction = (peak['equity'] - equity) / peak['equity']
            if fraction > drawdown['fraction']:
                drawdown.update(fraction=fraction, ts=ts, equity=equity,
                                peak_ts=peak['ts'], peak_equity=peak['equity'])

        engine._sample_leverage = observe
        started = time.time()
        res = engine.run(strategy, bars, since_ms=bt.START, leverage=leverage,
                         signal_tf=job['timeframe'], only_symbol=job['symbol'])
        replay = bt.summarize(job, res, start, time.time() - started)
        matches = (replay['trades'] == stored['trades'] and
                   abs(replay['final_equity'] - stored['final_equity']) < 0.001 and
                   replay['trade_list'] == stored['trade_list'])
        output.append({'name': name, 'key': key, 'peak_marked_equity': peak,
                       'minute_sample_max_drawdown': drawdown,
                       'matches_existing_replay': matches, 'replay': replay})
    print(json.dumps({'window': {'from': '2024-10-01', 'to_exclusive': '2026-10-01'},
                      'equity_resolution': 'every processed 1m bar, including unrealized PnL',
                      'server_write_operations': 0, 'bots': output}, separators=(',', ':')))


if __name__ == '__main__':
    main()
