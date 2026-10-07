-- The live PAPER bots' results by market regime at entry (research/pull_trades.py first).
-- Is a family losing everywhere, or only in some regimes?
SELECT t.program, split_part(t.bot_key, '-', 1) AS family, f.vol_regime, f.trend_regime, count(*) AS trades,
       round(avg(t.r), 3) AS r_per_trade, round(sum(t.net), 2) AS net_usdt, round(avg(CASE WHEN t.net > 0 THEN 1 ELSE 0 END), 2) AS win
FROM paper_trades t
JOIN features_5m f ON f.symbol = t.symbol AND f.ts = ((t.entry_ts - 60000) // 300000) * 300000
WHERE t.role = 'CONTROL' {where:}
GROUP BY ALL HAVING count(*) >= 20 ORDER BY 1, 2, 3, 4;
